"""Resumable image-index artifacts and atomic, bounded-memory publication.

Each analysis produces one content-addressed JSON leaf with local vectors.
Merge units only record child receipts. Publication validates the accepted tree
and streams it into a new generation; no partial index replaces the active one.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import time

from index_schema import INDEX_SCHEMA_VERSION, image_corpus_dir
from image_index_outcomes import DECODE_FAILURE_CODES, FAILURE_MARKER, is_not_indexed

MAX_PART_BYTES = 8 * 1024 * 1024
MAX_CHILDREN = 256
MAX_ENTRIES = 1_000_000
MAX_DEPTH = 32
MAX_DIMENSION = 16384
GROUP_SIZE = 32
MAX_SOURCE_BYTES = 1_099_511_627_776
MAX_SOURCE_DEPTH = 64
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".heic", ".webp", ".tiff", ".bmp"})
# Constrain the model's generation, not just its prompt. The existing token
# ceiling remains authoritative: incomplete responses still fail explicitly.
DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "minLength": 1, "maxLength": 400},
        "keywords": {"type": "array", "items": {"type": "string", "maxLength": 40}, "maxItems": 15},
        "location_hint": {"type": "string", "maxLength": 100},
        "activity_hint": {"type": "string", "maxLength": 100},
    },
    "required": ["description", "keywords", "location_hint", "activity_hint"],
    "additionalProperties": False,
}
_AXES = ("text", "face", "image")
_GENERATION_FILES = ("entries.jsonl", "lookup.sqlite", *(
    f"embeddings_{axis}.npy" for axis in _AXES))
_HASH = re.compile(r"[0-9a-f]{64}")
_GENERATION = re.compile(r"[A-Za-z0-9_-]{1,128}")


class ImageIndexBuildError(ValueError):
    """A source, artifact or publication does not satisfy the build contract."""


def _json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _safe_directory(path: Path, *, create=False) -> Path:
    for parent in reversed((path, *path.parents)):
        if parent.is_symlink():
            raise ImageIndexBuildError("symlink_directory")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise ImageIndexBuildError("directory_unavailable")
    return path


def _read_bytes(path: Path, *, limit=MAX_PART_BYTES) -> bytes:
    _safe_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ImageIndexBuildError("artifact_size_or_type")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ImageIndexBuildError("artifact_too_large")
        return data
    finally:
        os.close(fd)


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _generation_file_fact(path: Path) -> dict:
    """Fingerprint one bounded regular output without trusting its pathname."""
    _safe_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= MAX_SOURCE_BYTES:
            raise ImageIndexBuildError("generation_incomplete")
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                raise ImageIndexBuildError("generation_incomplete")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise ImageIndexBuildError("generation_incomplete")
        after, named = os.fstat(fd), path.lstat()
        identity = lambda info: (
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
        )
        if (not stat.S_ISREG(named.st_mode) or identity(before) != identity(after)
                or identity(after) != identity(named)):
            raise ImageIndexBuildError("generation_incomplete")
        return {"size_bytes": before.st_size, "sha256": digest.hexdigest()}
    finally:
        os.close(fd)


def _generation_files(directory: Path, *, expected=None) -> dict:
    """Seal or verify the exact output-file set, including retry after rename."""
    if expected is not None:
        if not isinstance(expected, dict) or set(expected) != set(_GENERATION_FILES):
            raise ImageIndexBuildError("generation_incomplete")
        for fact in expected.values():
            if (not isinstance(fact, dict) or set(fact) != {"size_bytes", "sha256"}
                    or type(fact["size_bytes"]) is not int
                    or not 0 <= fact["size_bytes"] <= MAX_SOURCE_BYTES
                    or not isinstance(fact["sha256"], str)
                    or _HASH.fullmatch(fact["sha256"]) is None):
                raise ImageIndexBuildError("generation_incomplete")
    try:
        facts = {name: _generation_file_fact(directory / name) for name in _GENERATION_FILES}
    except OSError as error:
        raise ImageIndexBuildError("generation_incomplete") from error
    if expected is not None and facts != expected:
        raise ImageIndexBuildError("generation_incomplete")
    return facts


def _write_bytes(path: Path, data: bytes, *, immutable: bool) -> None:
    _safe_directory(path.parent, create=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            try:
                os.link(temporary_path, path, follow_symlinks=False)
            except FileExistsError:
                if _read_bytes(path) != data:
                    raise ImageIndexBuildError("immutable_artifact_conflict")
        else:
            if path.is_symlink():
                raise ImageIndexBuildError("symlink_reference")
            os.replace(temporary_path, path)
        _sync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def _source_record(original, base_path, source) -> tuple[Path, dict]:
    original_path, base = Path(original), Path(base_path)
    if (not original_path.is_absolute()
            or not base.is_absolute() or ".." in original_path.parts
            or not original_path.is_relative_to(base) or original_path == base):
        raise ImageIndexBuildError("source_path_invalid")
    if not isinstance(source, dict):
        raise ImageIndexBuildError("source_record_invalid")
    digest = source.get("content_digest")
    if (not isinstance(digest, str) or not digest.startswith("sha256:")
            or not _HASH.fullmatch(digest[7:])):
        raise ImageIndexBuildError("source_digest_invalid")
    for field in ("size_bytes", "mtime_ns"):
        value = source.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ImageIndexBuildError("source_metadata_invalid")
    record = {key: source[key] for key in (
        "source_id", "ordinal", "device_id", "locator_redacted", "kind",
        "content_digest", "size_bytes", "mtime_ns", "state", "accounted",
    ) if key in source}
    return original_path, record


def validate_source(snapshot, original, base_path, source) -> tuple[Path, Path, dict]:
    """Verify full snapshot bytes against the authority's frozen source record."""
    original_path, record = _source_record(original, base_path, source)
    path = Path(snapshot)
    if not path.is_absolute():
        raise ImageIndexBuildError("snapshot_path_invalid")
    _safe_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != source["size_bytes"]:
            raise ImageIndexBuildError("source_size_mismatch")
        hasher = hashlib.sha256()
        remaining = source["size_bytes"]
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while chunk := stream.read(min(1024 * 1024, remaining + 1)):
                if len(chunk) > remaining:
                    raise ImageIndexBuildError("source_changed")
                hasher.update(chunk)
                remaining -= len(chunk)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                after.st_size, after.st_mtime_ns, after.st_ino):
            raise ImageIndexBuildError("source_changed")
        if hasher.hexdigest() != record["content_digest"][7:]:
            raise ImageIndexBuildError("source_digest_mismatch")
    finally:
        os.close(fd)
    return path, original_path, record


def folder_label(parent_dir: str) -> str:
    """Normalize a folder label once, independently from snapshot filenames."""
    label = re.sub(r"\b(19|20)\d\d\b", "", parent_dir or "").replace("-", " ")
    return re.sub(r"\s+", " ", label).strip()


def classify_folder_context(label: str, lang: str) -> str:
    """Perform exactly one registered folder-classification call per unit.

    LRE, rather than an in-process cache, owns deduplication and retries.
    Invalid output is a failed unit, never a cached empty classification.
    """
    if not isinstance(label, str) or len(label) > 4096:
        raise ImageIndexBuildError("folder_label_invalid")
    if not label.strip():
        return ""
    from llm_router import LLMRouter
    from llm_workloads import tier_for
    import prompt_loader

    system = prompt_loader.get("image_index_folder", lang)
    result = LLMRouter().chat(system, json.dumps(label, ensure_ascii=False),
                             tier=tier_for("images.folder_classify"),
                             max_tokens=512, request_timeout_s=60)
    category, separator, place = result.text.strip().partition("|")
    if not separator or category not in {"VIAGGIO", "EVENTO", "PERSONE", "DOCUMENTI", "ALTRO"}:
        raise ImageIndexBuildError("folder_classification_invalid")
    if len(place) > 4096 or "\n" in place:
        raise ImageIndexBuildError("folder_classification_invalid")
    return prompt_loader.get("image_index_context", lang, category=category,
                             place=place.strip(), label=label).strip()


def analysis_identity(lang: str) -> str:
    """Identify the actual local models and frozen-capable description prompt."""
    import prompt_loader
    import vlm_client
    from virt.local_models import model_artifacts, model_spec, projected_embedding_spec

    local = {}
    for role in ("text", "image", "face"):
        spec = (projected_embedding_spec(role) if role != "face" else None) or model_spec(role)
        files = []
        for path, relative in model_artifacts(spec):
            info = path.stat()
            files.append([str(relative), info.st_size, info.st_mtime_ns])
        local[role] = {"provider": spec["provider"], "files": files}
    facts = {"prompt": prompt_loader.prompt_identity("image_index_describe", lang).digest,
             "vlm": vlm_client.model_binding_facts(), "local": local,
             "description_schema": DESCRIPTION_SCHEMA}
    return hashlib.sha256(_json_bytes(facts)).hexdigest()


def _vectors(value) -> dict:
    import numpy as np

    if not isinstance(value, dict) or set(value) != set(_AXES):
        raise ImageIndexBuildError("vectors_invalid")
    result = {}
    for axis in _AXES:
        matrix = np.asarray(value[axis], dtype="float32")
        if matrix.size == 0:
            matrix = np.empty((0, 0), dtype="float32")
        if (matrix.ndim != 2 or matrix.shape[1] > MAX_DIMENSION
                or matrix.shape[0] > (1024 if axis == "face" else 1)
                or not np.isfinite(matrix).all()):
            raise ImageIndexBuildError("vectors_invalid")
        result[axis] = matrix
    return result


def _validate_analysis(leaf: dict, base_path: str) -> dict:
    """Check the closed metadata/vector relationship before any publication."""
    entry, models = leaf["entry"], leaf["models"]
    original, source = _source_record(entry["path"], base_path, leaf["source"])
    if (entry.get("name") != original.name or entry.get("sha256") != source["content_digest"][7:]
            or entry.get("size") != source["size_bytes"]
            or entry.get("mtime") != source["mtime_ns"] / 1e9
            or not isinstance(entry.get("faces"), list)
            or not isinstance(entry.get("description"), str) or not entry["description"].strip()
            or "_vlm_error" in entry):
        raise ImageIndexBuildError("entry_source_mismatch")
    if entry.get("indexing_status") == "not_indexed":
        code = entry.get("indexing_error_code")
        matrices = _vectors(leaf["vectors"])
        if (not isinstance(code, str) or code not in DECODE_FAILURE_CODES
                or not entry["description"].startswith(f"{FAILURE_MARKER}:{code} ")
                or not entry["description"].split(" ", 1)[1].strip()
                or models != {} or any(len(matrix) for matrix in matrices.values())
                or entry["faces"] or entry.get("keywords") != []
                or any(entry.get(f"embedding_{axis}_idx") is not None for axis in ("text", "image"))):
            raise ImageIndexBuildError("invalid_indexing_failure")
        return matrices
    if entry.get("indexing_status") not in (None, "indexed") or "indexing_error_code" in entry:
        raise ImageIndexBuildError("invalid_indexing_failure")
    strings = {"model_text", "model_face", "model_image", "model_vlm"}
    if (not isinstance(models, dict) or set(models) != strings | {"dim_text", "dim_image"}
            or any(not isinstance(models[key], str) or not models[key] or len(models[key]) > 1024 for key in strings)):
        raise ImageIndexBuildError("model_metadata_invalid")
    matrices = _vectors(leaf["vectors"])
    if (len(matrices["text"]) != 1 or len(matrices["image"]) != 1
            or len(matrices["face"]) != len(entry["faces"])):
        raise ImageIndexBuildError("incomplete_analysis")
    for axis in ("text", "image"):
        dimension = models[f"dim_{axis}"]
        if type(dimension) is not int or dimension != matrices[axis].shape[1] or dimension < 1:
            raise ImageIndexBuildError("model_dimension_mismatch")
    return matrices


class ImageIndexBuild:
    """One opaque build generation, bound to the previously active reference."""

    def __init__(self, base_path, generation, *, create=True):
        if not isinstance(generation, str) or not _GENERATION.fullmatch(generation):
            raise ImageIndexBuildError("generation_invalid")
        base = Path(base_path)
        if not base.is_absolute() or ".." in base.parts:
            raise ImageIndexBuildError("base_path_invalid")
        self.base_path, self.generation = str(base), generation
        self.root = image_corpus_dir(base) / "unified"
        self.work = self.root / ".builds" / generation
        self.parts = _safe_directory(self.work / "parts", create=create)
        context_path = self.work / "context.json"
        if create and not context_path.exists():
            previous = self._active_bytes()
            context = {"base_path": self.base_path, "generation": generation,
                       "previous_digest": hashlib.sha256(previous).hexdigest(),
                       "previous_meta": json.loads(previous) if previous else {}}
            try:
                _write_bytes(context_path, _json_bytes(context), immutable=True)
            except ImageIndexBuildError:
                if not context_path.exists():
                    raise
        self.context = json.loads(_read_bytes(context_path))
        if (self.context.get("base_path") != self.base_path
                or self.context.get("generation") != generation):
            raise ImageIndexBuildError("generation_context_mismatch")

    def _active_bytes(self) -> bytes:
        path = self.root / "meta.json"
        return _read_bytes(path, limit=262_144) if path.exists() else b""

    def _store_part(self, payload: dict) -> dict:
        payload = {"format": 1, "base_path": self.base_path,
                   "generation": self.generation, **payload}
        data = _json_bytes(payload)
        if len(data) > MAX_PART_BYTES:
            raise ImageIndexBuildError("artifact_too_large")
        digest = hashlib.sha256(data).hexdigest()
        _write_bytes(self.parts / f"{digest}.json", data, immutable=True)
        return {"part": digest}

    def _part(self, receipt) -> dict:
        digest = receipt.get("part") if isinstance(receipt, dict) else None
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ImageIndexBuildError("part_invalid")
        data = _read_bytes(self.parts / f"{digest}.json")
        if hashlib.sha256(data).hexdigest() != digest:
            raise ImageIndexBuildError("part_digest_mismatch")
        value = json.loads(data)
        if (value.get("format") != 1 or value.get("generation") != self.generation
                or value.get("base_path") != self.base_path):
            raise ImageIndexBuildError("part_context_mismatch")
        count = value.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= MAX_ENTRIES:
            raise ImageIndexBuildError("part_count_invalid")
        return value

    def analysis(self, entry, vectors, models, source, *, identity, reused=False) -> dict:
        matrices = _validate_analysis({"entry": entry, "vectors": vectors,
                                       "models": models, "source": source}, self.base_path)
        entry = copy.deepcopy(entry)
        entry["_analysis_identity"] = identity
        entry["_vector_digests"] = {
            axis: hashlib.sha256(matrix.tobytes()).hexdigest()
            for axis, matrix in matrices.items()
        }
        receipt = self._store_part({
            "kind": "analysis", "count": 1, "entry": entry, "models": models,
            "source": source, "reused": bool(reused),
            "vectors": {axis: matrix.tolist() for axis, matrix in matrices.items()},
        })
        # Each completed photo survives an interruption of its enclosing group.
        # This is a private resume hint, never an accepted LRE result or an
        # active index. The receipt and full source relationship are rechecked.
        checkpoint = self._analysis_checkpoint_path(
            entry["path"], source, identity=identity,
            folder_context=entry.get("path_context", ""),
        )
        _write_bytes(checkpoint, _json_bytes(receipt), immutable=False)
        return receipt

    def _analysis_checkpoint_path(self, original, source, *, identity, folder_context):
        key = hashlib.sha256(_json_bytes({
            "path": str(original), "source": source, "identity": identity,
            "folder_context": folder_context,
        })).hexdigest()
        return self.work / "checkpoints" / (key + ".json")

    def analysis_write_targets(self, entries, *, identity):
        """Read and verify a group's exact mutable checkpoint destinations.

        Immutable parts and source snapshots already use no-replace creation;
        the optional previous-generation lookup has its own filesystem lock.
        The per-source checkpoint is the remaining mutable analysis target.
        A batch ID or receipt alone is not an exclusion proof: groups can
        overlap, so return every target and let the common scheduler compare
        their sets. This method neither writes nor opens model/source bytes.
        """
        records, contexts = self.discovery_group(entries)
        targets = []
        for record in records:
            original, source = _source_record(
                record["original_path"], self.base_path, record["source"])
            target = self._analysis_checkpoint_path(
                original, source, identity=identity,
                folder_context=contexts[folder_label(original.parent.name)])
            targets.append(os.path.abspath(target))
        if len(set(targets)) != len(targets):
            raise ImageIndexBuildError("duplicate_source_path")
        return tuple(sorted(targets))

    def analysis_checkpoint(self, original, source, *, identity, folder_context):
        """Resume only fully validated leaves from this exact build generation."""
        path = self._analysis_checkpoint_path(
            original, source, identity=identity, folder_context=folder_context,
        )
        if not path.exists():
            return None
        receipt = json.loads(_read_bytes(path, limit=1024))
        leaf = self._part(receipt)
        if (leaf.get("kind") != "analysis" or leaf.get("count") != 1
                or leaf.get("source") != source
                or leaf.get("entry", {}).get("path") != str(original)
                or leaf["entry"].get("_analysis_identity") != identity
                or leaf["entry"].get("path_context", "") != folder_context):
            raise ImageIndexBuildError("analysis_checkpoint_invalid")
        _validate_analysis(leaf, self.base_path)
        return receipt

    def merge(self, entries) -> dict:
        if not isinstance(entries, list) or len(entries) > MAX_CHILDREN:
            raise ImageIndexBuildError("reduction_fanout_invalid")
        children, count = [], 0
        for receipt in entries:
            child = self._part(receipt)
            digest = receipt["part"]
            if digest in children:
                raise ImageIndexBuildError("duplicate_part")
            children.append(digest)
            count += child["count"]
        if count > MAX_ENTRIES:
            raise ImageIndexBuildError("entry_limit")
        return self._store_part({"kind": "merge", "count": count,
                                 "children": sorted(children)})

    def discover(self, *, device_id, max_files, max_total_bytes, max_depth, recursive):
        from durable_workloads.inventory import InventoryLimits, seal_local_inventory
        from durable_workloads.schema import MAX_RESULT_JSON_BYTES

        if (type(max_total_bytes) is not int or not 0 <= max_total_bytes <= MAX_SOURCE_BYTES
                or type(max_depth) is not int or not 0 <= max_depth <= MAX_SOURCE_DEPTH):
            raise ImageIndexBuildError("inventory_limits_invalid")
        group, receipts = [], []
        receipt_bytes = 0

        def flush():
            nonlocal receipt_bytes
            if not group:
                return
            labels = sorted({folder_label(Path(record["original_path"]).parent.name)
                             for record in group})
            receipt = self._store_part({"kind": "discovery", "count": len(group),
                                        "records": list(group), "folder_labels": labels})
            public = {**receipt, "folder_labels": labels}
            receipt_bytes += len(_json_bytes(public)) + 1
            if receipt_bytes > MAX_RESULT_JSON_BYTES - 65_536:
                raise ImageIndexBuildError("discovery_output_too_large")
            receipts.append(public)
            group.clear()

        def on_source(source, path):
            group.append({"source": dict(source), "original_path": str(path)})
            if len(group) == GROUP_SIZE:
                flush()

        inventory = None
        try:
            inventory = seal_local_inventory(
                [self.base_path], device_id=device_id,
                limits=InventoryLimits(max_sources=max_files,
                                       max_total_bytes=max_total_bytes, max_depth=max_depth),
                on_source=on_source, recursive=recursive,
                accept=lambda path: path.suffix.lower() in IMAGE_EXTENSIONS,
            )
            flush()
            result = {"ok": True, "entries": receipts,
                      "source_count": len(inventory["sources"]),
                      "inventory_digest": inventory["digest"]}
            if len(_json_bytes(result)) > MAX_RESULT_JSON_BYTES - 65_536:
                raise ImageIndexBuildError("discovery_output_too_large")
            return result
        finally:
            if inventory is not None and callable(getattr(inventory, "close", None)):
                inventory.close()

    def discovery_group(self, entries):
        if not isinstance(entries, list) or len(entries) != 1:
            raise ImageIndexBuildError("analysis_group_invalid")
        group = self._part(entries[0])
        records = group.get("records")
        if (group.get("kind") != "discovery" or not isinstance(records, list)
                or not 1 <= len(records) <= GROUP_SIZE or group["count"] != len(records)):
            raise ImageIndexBuildError("analysis_group_invalid")
        contexts = entries[0].get("folder_contexts")
        if not isinstance(contexts, dict) or set(contexts) != set(group["folder_labels"]):
            raise ImageIndexBuildError("folder_contexts_invalid")
        if any(not isinstance(value, str) or len(value) > 8192 for value in contexts.values()):
            raise ImageIndexBuildError("folder_contexts_invalid")
        return records, contexts

    def snapshot(self, record):
        """Copy stable original bytes once; later retries revalidate the sealed copy."""
        from durable_workloads.inventory import _stable_file_digest

        original, source = _source_record(record["original_path"], self.base_path, record["source"])
        digest = source.get("content_digest", "")
        if not isinstance(digest, str) or not digest.startswith("sha256:") or not _HASH.fullmatch(digest[7:]):
            raise ImageIndexBuildError("source_digest_invalid")
        snapshots = _safe_directory(self.work / "snapshots", create=True)
        target = snapshots / digest[7:]
        if not target.exists():
            _safe_directory(original.parent)
            fd, temporary_name = tempfile.mkstemp(prefix=".sealing-", dir=snapshots)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as output:
                    observed, metadata = _stable_file_digest(
                        original, chunk_bytes=1024 * 1024, max_bytes=source["size_bytes"],
                        before_final_stat=None, on_chunk=output.write,
                    )
                    if (observed != digest or metadata.st_size != source["size_bytes"]
                            or metadata.st_mtime_ns != source["mtime_ns"]):
                        raise ImageIndexBuildError("source_changed_since_discovery")
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    os.link(temporary, target, follow_symlinks=False)
                except FileExistsError:
                    pass
                _sync_directory(snapshots)
            finally:
                temporary.unlink(missing_ok=True)
        return validate_source(target, original, self.base_path, source)

    def _leaves(self, receipt, database):
        pending = [(receipt, 0)]
        count = 0
        while pending:
            selected, depth = pending.pop()
            if depth > MAX_DEPTH:
                raise ImageIndexBuildError("reduction_depth_invalid")
            try:
                database.execute("INSERT INTO parts VALUES (?)", (selected["part"],))
            except sqlite3.IntegrityError as exc:
                raise ImageIndexBuildError("duplicate_part") from exc
            node = self._part(selected)
            if node.get("kind") == "analysis" and node["count"] == 1:
                count += 1
                if count > MAX_ENTRIES:
                    raise ImageIndexBuildError("entry_limit")
                yield selected["part"], node
            elif node.get("kind") == "merge":
                children = node.get("children")
                if not isinstance(children, list) or len(children) > MAX_CHILDREN:
                    raise ImageIndexBuildError("reduction_fanout_invalid")
                child_receipts = [{"part": child} for child in children]
                if sum(self._part(child)["count"] for child in child_receipts) != node["count"]:
                    raise ImageIndexBuildError("part_count_mismatch")
                pending.extend((child, depth + 1) for child in reversed(child_receipts))
            else:
                raise ImageIndexBuildError("part_kind_invalid")

    def _previous_lookup(self) -> Path | None:
        meta = self.context["previous_meta"]
        if not meta:
            return None
        generation = meta.get("active_generation")
        if generation is not None and (not isinstance(generation, str) or not _GENERATION.fullmatch(generation)):
            raise ImageIndexBuildError("previous_generation_invalid")
        previous = self.root / ".generations" / generation if generation else self.root
        _safe_directory(previous)
        lookup = previous / "lookup.sqlite"
        if lookup.is_file():
            return lookup
        # Legacy indexes have no lookup. Build one once per generation under a
        # lock, rather than scanning all entries for every resumed image.
        lookup = self.work / "previous.sqlite"
        lock_fd = os.open(self.work / "previous.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if not lookup.exists():
                temporary = Path(tempfile.mkdtemp(prefix=".lookup-", dir=self.work))
                try:
                    with sqlite3.connect(temporary / "lookup.sqlite") as database:
                        database.execute("CREATE TABLE entries(path TEXT PRIMARY KEY, data TEXT NOT NULL)")
                        for entry in _read_entries(previous / "entries.jsonl"):
                            database.execute("INSERT INTO entries VALUES (?,?)",
                                             (entry["path"], _json_bytes(entry).decode()))
                    os.replace(temporary / "lookup.sqlite", lookup)
                finally:
                    shutil.rmtree(temporary)
            return lookup
        finally:
            os.close(lock_fd)

    def reusable(self, original: Path, source: dict, *, identity: str, folder_context: str):
        import numpy as np

        lookup = self._previous_lookup()
        if lookup is None:
            return None
        if lookup.is_symlink():
            raise ImageIndexBuildError("symlink_lookup")
        with sqlite3.connect(lookup.as_uri() + "?mode=ro", uri=True) as database:
            found = database.execute("SELECT data FROM entries WHERE path=?", (str(original),)).fetchone()
            if not found:
                previous_base = self.context["previous_meta"].get("base_path")
                if isinstance(previous_base, str) and Path(previous_base).is_absolute():
                    legacy_path = Path(previous_base) / original.relative_to(self.base_path)
                    found = database.execute("SELECT data FROM entries WHERE path=?", (str(legacy_path),)).fetchone()
        if not found:
            return None
        entry = json.loads(found[0])
        if (entry.get("indexing_status") == "not_indexed"
                or entry.get("sha256") != source["content_digest"][7:]
                or entry.get("size") != source["size_bytes"]
                or not entry.get("description") or "_vlm_error" in entry
                or entry.get("path_context", "") != folder_context):
            return None
        if entry.get("_analysis_identity") not in (None, identity):
            return None
        meta = self.context["previous_meta"]
        generation = meta.get("active_generation")
        previous = self.root / ".generations" / generation if generation else self.root
        _safe_directory(previous)
        vectors = {}
        try:
            for axis in _AXES:
                path = previous / f"embeddings_{axis}.npy"
                if path.is_symlink():
                    raise ImageIndexBuildError("symlink_vectors")
                matrix = np.load(path, mmap_mode="r", allow_pickle=False)
                indexes = ([face["embedding_face_idx"] for face in entry.get("faces", [])]
                           if axis == "face" else [entry[f"embedding_{axis}_idx"]])
                if any(isinstance(index, bool) or not isinstance(index, int)
                       or not 0 <= index < len(matrix) for index in indexes):
                    return None
                vectors[axis] = matrix[indexes]
            vectors = _vectors(vectors)
            for axis, matrix in vectors.items():
                expected = entry.get("_vector_digests", {}).get(axis)
                if expected and hashlib.sha256(matrix.tobytes()).hexdigest() != expected:
                    return None
        except (OSError, KeyError, ValueError, IndexError):
            return None
        entry = copy.deepcopy(entry)
        entry.update(path=str(original), name=original.name,
                     mtime=source["mtime_ns"] / 1e9, mtime_ns=source["mtime_ns"])
        for axis in ("text", "image"):
            entry[f"embedding_{axis}_idx"] = 0
        for index, face in enumerate(entry.get("faces", [])):
            face["embedding_face_idx"] = index
        model_keys = ("model_text", "model_face", "model_image", "model_vlm", "dim_text", "dim_image")
        models = {key: meta[key] for key in model_keys if key in meta}
        if len(models) != len(model_keys):
            return None
        return entry, vectors, models

    def _activate(self, receipt, target, *, metadata=None, temporary=None):
        """Activate a complete generation once, including a post-rename retry."""
        lock_fd = os.open(self.root / ".publish.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if target.exists():
                _safe_directory(target)
                stored = json.loads(_read_bytes(target / "meta.json"))
                if (stored.get("root_part") != receipt["part"]
                        or stored.get("active_generation") != self.generation
                        or stored.get("base_path") != self.base_path
                        or stored.get("n_entries") != self._part(receipt)["count"]):
                    raise ImageIndexBuildError("generation_receipt_conflict")
                # Existence is not proof of a completed generation: a crash or
                # later corruption may leave every filename but damaged bytes.
                # Pre-seal generations remain readable by normal consumers;
                # they cannot certify a publication retry without this proof.
                if not isinstance(stored.get("generation_files"), dict):
                    raise ImageIndexBuildError("generation_incomplete")
                failures = stored.get("indexing_error_counts")
                if (not isinstance(failures, dict) or set(failures) - DECODE_FAILURE_CODES
                        or any(type(count) is not int or count < 1 for count in failures.values())
                        or type(stored.get("n_indexed")) is not int
                        or type(stored.get("n_not_indexed")) is not int
                        or stored["n_indexed"] < 0
                        or stored["n_not_indexed"] != sum(failures.values())
                        or stored["n_entries"] != stored["n_indexed"] + stored["n_not_indexed"]):
                    raise ImageIndexBuildError("generation_incomplete")
                _generation_files(target, expected=stored["generation_files"])
                metadata = stored
            active = self._active_bytes()
            current = json.loads(active) if active else {}
            if current.get("active_generation") == self.generation:
                if current.get("root_part") != receipt["part"]:
                    raise ImageIndexBuildError("generation_receipt_conflict")
                if not target.exists():
                    raise ImageIndexBuildError("generation_incomplete")
                if current != metadata:
                    raise ImageIndexBuildError("generation_receipt_conflict")
                return current
            if hashlib.sha256(active).hexdigest() != self.context["previous_digest"]:
                raise ImageIndexBuildError("active_generation_changed")
            if not target.exists():
                if temporary is None:
                    return None
                os.rename(temporary, target)
                _sync_directory(target.parent)
            _write_bytes(self.root / "meta.json", _json_bytes(metadata), immutable=False)
            return metadata
        finally:
            os.close(lock_fd)

    def _published_result(self, receipt, target, metadata):
        return {"ok": True, "entries": [receipt], "schema_version": INDEX_SCHEMA_VERSION,
                "base_path": self.base_path, "index_path": str(target),
                "ok_count": metadata["n_indexed"], "fail_count": metadata["n_not_indexed"],
                "n_entries_total": metadata["n_entries"],
                **metadata}

    def publish(self, entries, *, expected_count=None) -> dict:
        import numpy as np

        if (expected_count is not None and (isinstance(expected_count, bool)
                or not isinstance(expected_count, int) or not 0 <= expected_count <= MAX_ENTRIES)):
            raise ImageIndexBuildError("expected_count_invalid")
        receipt = self.merge(entries)
        root_part = self._part(receipt)
        if expected_count is not None and root_part["count"] != expected_count:
            raise ImageIndexBuildError("coverage_mismatch")
        generations = _safe_directory(self.root / ".generations", create=True)
        target = generations / self.generation
        completed = self._activate(receipt, target)
        if completed is not None:
            return self._published_result(receipt, target, completed)
        temporary = Path(tempfile.mkdtemp(prefix=".publishing-", dir=generations))
        try:
            with sqlite3.connect(temporary / "lookup.sqlite") as database:
                database.execute("CREATE TABLE parts(part TEXT PRIMARY KEY)")
                database.execute("CREATE TABLE entries(path TEXT PRIMARY KEY, data TEXT NOT NULL, part TEXT UNIQUE)")
                counts, dimensions, models, reused = dict.fromkeys(_AXES, 0), {}, None, 0
                error_counts = {}
                for part, leaf in self._leaves(receipt, database):
                    entry = leaf["entry"]
                    original = Path(entry["path"])
                    if (not original.is_absolute() or ".." in original.parts
                            or not original.is_relative_to(self.base_path) or str(original) == self.base_path):
                        raise ImageIndexBuildError("entry_path_invalid")
                    vectors = _validate_analysis(leaf, self.base_path)
                    if is_not_indexed(entry):
                        code = entry["indexing_error_code"]
                        error_counts[code] = error_counts.get(code, 0) + 1
                    else:
                        if models is not None and models != leaf["models"]:
                            raise ImageIndexBuildError("mixed_model_generations")
                        models = leaf["models"]
                    for axis, matrix in vectors.items():
                        if len(matrix):
                            if axis in dimensions and dimensions[axis] != matrix.shape[1]:
                                raise ImageIndexBuildError("mixed_vector_dimensions")
                            dimensions[axis] = matrix.shape[1]
                        counts[axis] += len(matrix)
                    try:
                        database.execute("INSERT INTO entries VALUES (?,?,?)",
                                         (str(original), "", part))
                    except sqlite3.IntegrityError as exc:
                        raise ImageIndexBuildError("duplicate_source_path") from exc
                    reused += int(leaf.get("reused", False))
                total = database.execute("SELECT count(*) FROM entries").fetchone()[0]
                if total != root_part["count"]:
                    raise ImageIndexBuildError("coverage_mismatch")
                matrices = {}
                for axis in _AXES:
                    path = temporary / f"embeddings_{axis}.npy"
                    shape = (counts[axis], dimensions.get(axis, 0))
                    if counts[axis]:
                        matrices[axis] = np.lib.format.open_memmap(path, mode="w+", dtype="float32", shape=shape)
                    else:
                        np.save(path, np.empty(shape, dtype="float32"), allow_pickle=False)
                offsets = dict.fromkeys(_AXES, 0)
                with (temporary / "entries.jsonl").open("wb") as output:
                    for original, part in database.execute("SELECT path,part FROM entries ORDER BY path"):
                        leaf = self._part({"part": part})
                        entry = copy.deepcopy(leaf["entry"])
                        vectors = _vectors(leaf["vectors"])
                        for axis, matrix in vectors.items():
                            start, stop = offsets[axis], offsets[axis] + len(matrix)
                            if len(matrix):
                                matrices[axis][start:stop] = matrix
                            if axis == "face":
                                for index, face in enumerate(entry.get("faces", [])):
                                    face["embedding_face_idx"] = start + index
                            elif len(matrix):
                                entry[f"embedding_{axis}_idx"] = start
                            offsets[axis] = stop
                        data = _json_bytes(entry)
                        database.execute("UPDATE entries SET data=? WHERE path=?", (data.decode(), original))
                        output.write(data + b"\n")
                    output.flush()
                    os.fsync(output.fileno())
                for matrix in matrices.values():
                    matrix.flush()
                matrices.clear()
                database.execute("DROP TABLE parts")
            metadata = {
                "schema_version": INDEX_SCHEMA_VERSION, "version": INDEX_SCHEMA_VERSION,
                "active_generation": self.generation, "root_part": receipt["part"],
                "base_path": self.base_path, "n_entries": total,
                "n_indexed": total - sum(error_counts.values()),
                "n_not_indexed": sum(error_counts.values()),
                "indexing_error_counts": error_counts,
                "n_faces": counts["face"], "n_images_with_visual_emb": counts["image"],
                "last_refresh_at": time.time(), "refreshed_count": total - reused - sum(error_counts.values()),
                "index_created": not bool(self.context["previous_meta"]),
                "generation_files": _generation_files(temporary),
                **(models or {}),
            }
            _write_bytes(temporary / "meta.json", _json_bytes(metadata), immutable=True)
            for path in temporary.iterdir():
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            _sync_directory(temporary)
            metadata = self._activate(receipt, target, metadata=metadata, temporary=temporary)
            return self._published_result(receipt, target, metadata)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)


def _read_entries(path: Path):
    _safe_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ImageIndexBuildError("entries_type_invalid")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            count = 0
            while line := stream.readline(MAX_PART_BYTES + 1):
                if len(line) > MAX_PART_BYTES:
                    raise ImageIndexBuildError("entry_too_large")
                if not line.strip():
                    continue
                count += 1
                if count > MAX_ENTRIES:
                    raise ImageIndexBuildError("entry_limit")
                yield json.loads(line)
    finally:
        os.close(fd)
