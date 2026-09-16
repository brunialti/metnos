#!/usr/bin/env python3
"""Resumable image-index phases; only publication changes the active index.

Public requests are expanded by LRE. Discovery seals bounded groups of source
records; analysis consumes private snapshots and preclassified folder context;
merge carries only receipts; publication activates one complete generation.
There are no subprocesses, internal worker pools, folder LLM calls or partial
index checkpoints in an analysis unit.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import sys

_RUNTIME = os.environ.get("METNOS_RUNTIME") or next(
    str(parent / "runtime") for parent in Path(__file__).resolve().parents
    if (parent / "runtime" / "config.py").is_file())
sys.path.insert(0, _RUNTIME)

from executor_helpers import run_stdio  # noqa: E402
from image_index_build import (  # noqa: E402
    DESCRIPTION_SCHEMA, IMAGE_EXTENSIONS, MAX_SOURCE_BYTES, MAX_SOURCE_DEPTH,
    ImageIndexBuild, ImageIndexBuildError,
    analysis_identity, classify_folder_context, folder_label,
)
from index_schema import INDEX_SCHEMA_VERSION, image_corpus_dir  # noqa: E402
from image_index_outcomes import DECODE_FAILURE_CODES, failure_description  # noqa: E402
from messages import get as _msg  # noqa: E402
from parallel_walk import parallel_walk  # noqa: E402

log = logging.getLogger(__name__)
_INDEX_VERSION = INDEX_SCHEMA_VERSION


def _index_dir(base_path: Path) -> Path:
    return image_corpus_dir(base_path) / "unified"


def _scan_image_corpus(base: Path, recursive: bool):
    """Preserve the deterministic, read-only public dry-run operation."""
    walk = parallel_walk(base, accept=lambda _path, kind, _depth: kind == "file",
                         recursive=recursive)
    images = [path for path in walk.items if path.suffix.lower() in IMAGE_EXTENSIONS]
    return {"n_image": len(images), "n_other": len(walk.items) - len(images),
            "errors": walk.errors, "visited_dirs": walk.visited_dirs,
            "walk_workers": walk.workers}


def _vlm_prompt(lang: str, filename: str, parent_dir: str) -> str:
    import prompt_loader
    return prompt_loader.get("image_index_describe", lang,
                             filename=filename, parent_dir=parent_dir)


def _call_vlm(image_path: Path, *, original_path: Path) -> dict:
    import i18n
    from vlm_client import describe_image, model_binding_facts

    facts = model_binding_facts()
    return describe_image(
        image_path,
        prompt=_vlm_prompt(i18n.current_lang(), original_path.name, original_path.parent.name),
        max_tokens=facts["max_tokens"], allow_lazy_start=False,
        response_schema=DESCRIPTION_SCHEMA,
    )


def folder_path_context(parent_dir: str, lang: str) -> str:
    """Compatibility helper for the explicit offline path-context maintenance job.

    Durable analysis never calls this helper: its context is an accepted
    dependency result from the separate registered classification workload.
    """
    return classify_folder_context(folder_label(parent_dir), lang)


def _open_image_with_exif(path: Path):
    from PIL import Image, UnidentifiedImageError
    from PIL.ExifTags import GPSTAGS, TAGS
    from pillow_heif import register_heif_opener

    # Snapshots are digest-named: detect by bytes, never by extension. The
    # registered decoder is also used by local face/image models and VLM.
    register_heif_opener(thumbnails=False)
    try:
        image = Image.open(path)
    except UnidentifiedImageError as error:
        raise ImageIndexBuildError("image_format_unreadable") from error
    try:
        try:
            image.load()
        except OSError as error:
            raise ImageIndexBuildError("image_decode_failed") from error
        raw = image.getexif() or {}
        named = {TAGS.get(key, key): value for key, value in raw.items()}
        gps = raw.get_ifd(34853) if hasattr(raw, "get_ifd") and 34853 in raw else named.get("GPSInfo")
        if isinstance(gps, dict):
            named["GPSInfo"] = {GPSTAGS.get(key, key): value for key, value in gps.items()}
        return image, named
    except BaseException:
        image.close()
        raise


def _exif_gps(exif: dict) -> dict | None:
    gps = exif.get("GPSInfo")
    if not isinstance(gps, dict):
        return None

    def decimal(coordinates, reference):
        degrees, minutes, seconds = (float(value) for value in coordinates)
        value = degrees + minutes / 60.0 + seconds / 3600.0
        return -value if reference in ("S", "W") else value

    try:
        return {"lat": decimal(gps["GPSLatitude"], gps.get("GPSLatitudeRef", "N")),
                "lon": decimal(gps["GPSLongitude"], gps.get("GPSLongitudeRef", "E"))}
    except (KeyError, TypeError, ValueError):
        return None


# Camera-generated names carry no human labels. This is the existing lexical
# policy, shared by every source instead of depending on a snapshot basename.
_AUTO_FILENAME_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"^DSC[NF_-]?\d+$", r"^IMG[_-]?\d[\dA-Z_-]*$",
    r"^IMG-?\d{8}-?WA\d+$", r"^PI?C[T_]?\d+$", r"^P\d+$",
    r"^(CIMG|CAM|SDC)\d+$", r"^\d{4}[-_]?\d{2}[-_]?\d{2}(?:\d|[-_]\d)*$",
    r"^\d{14}$", r"^\d+$", r"^[0-9a-fA-F]{16,}$", r"^Thumbs$",
))


def _meaningful_filename_tokens(stem: str) -> list[str]:
    if not stem or any(pattern.match(stem) for pattern in _AUTO_FILENAME_PATTERNS):
        return []
    return [token for token in re.split(r"[\s_\-]+", stem.lower())
            if len(token) >= 2 and not token.isdigit()
            and token not in {"wa", "vid", "img", "dsc", "pic", "p"}]


def _meaningful_dir_tokens(name: str) -> list[str]:
    if not name or name in (".", "/", "Immagini", "Photos", "Foto"):
        return []
    return [token for token in re.split(r"[\s_\-]+", name.lower())
            if len(token) >= 2 and not (token.isdigit() and len(token) < 4)]


def _path_tokens(original: Path) -> list[str]:
    tokens = (_meaningful_filename_tokens(original.stem)
              + _meaningful_dir_tokens(original.parent.name)
              + _meaningful_dir_tokens(original.parent.parent.name))
    return list(dict.fromkeys(tokens))


def _source_entry(original: Path, source: dict) -> dict:
    return {
        "path": str(original), "name": original.name,
        "sha256": source["content_digest"][7:],
        "mtime": source["mtime_ns"] / 1e9, "mtime_ns": source["mtime_ns"],
        "size": source["size_bytes"], "path_tokens": _path_tokens(original),
    }


def _build_entry(snapshot: Path, original: Path, source: dict, *, face_engine, image, exif) -> dict:
    """Analyze exactly one sealed image, retaining authoritative original metadata."""
    from index_schema import _exif_taken_at

    width, height = image.size
    faces_out = []
    for face in face_engine.detect_faces(snapshot):
        bbox, landmarks = face.get("bbox"), face.get("landmarks")
        entry = {"bbox": [int(value) for value in bbox] if bbox is not None else [],
                 "detect_score": float(face.get("score", 0)),
                 "_embedding_face": face.get("embedding")}
        if landmarks is not None:
            entry["landmarks"] = [[float(value) for value in point] for point in landmarks]
        faces_out.append(entry)
    vlm = _call_vlm(snapshot, original_path=original)
    if vlm.get("_vlm_error") or not isinstance(vlm.get("description"), str) or not vlm["description"].strip():
        raise ImageIndexBuildError("image_description_unavailable")
    return {
        **_source_entry(original, source), "indexing_status": "indexed",
        "image_w": int(width), "image_h": int(height),
        "taken_at_iso": _exif_taken_at(exif=exif), "exif_gps": _exif_gps(exif),
        "description": vlm["description"], "keywords": list(vlm.get("keywords", [])),
        "location_hint": vlm.get("location_hint", ""),
        "activity_hint": vlm.get("activity_hint", ""), "faces": faces_out,
    }


def _analyze_one(store, record, *, context, identity, force):
    import numpy as np
    from face_embedding import get_face_engine
    from virt import get_embedder
    from virt.local_models import local_embedding_spec
    from vlm_client import model_binding_facts

    snapshot, original, source = store.snapshot(record)
    checkpoint = store.analysis_checkpoint(original, source, identity=identity, folder_context=context)
    if checkpoint is not None:
        return checkpoint
    reused = None if force else store.reusable(original, source, identity=identity,
                                               folder_context=context)
    if reused is not None:
        entry, vectors, models = reused
        entry["path_tokens"] = _path_tokens(original)
        return store.analysis(entry, vectors, models, source, identity=identity, reused=True)

    # Only the decoder's two explicit file outcomes are recoverable here.
    # Authority, source changes, model/usage failures and resource limits are
    # still fatal. Never manufacture semantic vectors from an error message.
    try:
        image, exif = _open_image_with_exif(snapshot)
    except ImageIndexBuildError as error:
        code = str(error)
        if code not in DECODE_FAILURE_CODES:
            raise
        entry = {
            **_source_entry(original, source), "indexing_status": "not_indexed",
            "indexing_error_code": code, "description": failure_description(code),
            "keywords": [], "faces": [], "path_context": context,
        }
        return store.analysis(entry, {axis: [] for axis in ("text", "face", "image")},
                              {}, source, identity=identity)
    try:
        face_engine = get_face_engine()
        if not face_engine.available:
            raise ImageIndexBuildError("face_model_unavailable")
        image_engine = get_embedder("image")
        if not image_engine.available:
            raise ImageIndexBuildError("image_model_unavailable")
        text_engine = get_embedder("text")
        entry = _build_entry(snapshot, original, source, face_engine=face_engine, image=image, exif=exif)
    finally:
        image.close()
    entry["path_context"] = context
    vectors = {
        "text": text_engine.embed_texts([(context + " " + entry["description"]).strip()]),
        "image": image_engine.embed_images([str(snapshot)], batch_size=1),
        "face": [],
    }
    for index, face in enumerate(entry["faces"]):
        embedding = face.pop("_embedding_face")
        if embedding is None:
            raise ImageIndexBuildError("face_embedding_unavailable")
        vectors["face"].append(embedding)
        face["embedding_face_idx"] = index
    for axis in ("text", "image"):
        entry[f"embedding_{axis}_idx"] = 0
    provider = local_embedding_spec("text")["provider"]
    models = {
        "model_text": getattr(text_engine, "name", "bge-m3" if provider == "bge" else provider),
        "model_face": face_engine.name, "model_image": image_engine.name,
        "model_vlm": model_binding_facts()["model"],
        "dim_text": int(np.asarray(vectors["text"]).shape[1]),
        "dim_image": int(np.asarray(vectors["image"]).shape[1]),
    }
    return store.analysis(entry, vectors, models, source, identity=identity)


def _error(code, *, key="ERR_DURABLE_EXECUTION_FAILED", **parameters):
    return {"ok": False, "entries": [], "error_code": code,
            "error_class": "invalid_input" if code.endswith("invalid") else "execution_failed",
            "error": _msg(key, **parameters)}


def invoke(args):
    if not isinstance(args, dict):
        return _error("args_not_object", key="ERR_ARGS_NOT_OBJECT")
    raw_base = args.get("base_path")
    if not isinstance(raw_base, str) or not raw_base.strip():
        return _error("base_path_missing", key="ERR_ARG_MISSING", arg="base_path")
    base = Path(os.path.abspath(os.path.expanduser(raw_base)))
    max_files = args.get("max_files", 50000)
    if isinstance(max_files, bool) or not isinstance(max_files, int) or not 1 <= max_files <= 1_000_000:
        return _error("max_files_invalid", key="ERR_ARG_NOT_POSITIVE_INT", arg="max_files")
    phase = args.get("phase")
    dry_run = bool(args.get("dry_run")) or os.environ.get("METNOS_DRY_RUN") == "1"
    if phase in (None, "discover") or dry_run:
        if not base.exists():
            return _error("base_path_missing", key="ERR_PATH_NOT_FOUND", path=str(base))
        if not base.is_dir():
            return _error("base_path_invalid", key="ERR_PATH_WRONG_TYPE", expected="dir", actual="file", path=str(base))
    if dry_run:
        scan = _scan_image_corpus(base, bool(args.get("recursive", True)))
        if scan["errors"]:
            return _error("source_unreadable", key="ERR_FILE_READ_FAILED", path=str(scan["errors"][0].path))
        return {"ok": True, "entries": [], "dry_run": True,
                "schema_version": INDEX_SCHEMA_VERSION, "base_path": str(base),
                "would_index_count": scan["n_image"], "n_other_files": scan["n_other"],
                "visited_dirs": scan["visited_dirs"], "walk_workers": scan["walk_workers"]}
    if phase is None:
        return _error("requires_lre", key="ERR_LRE_REQUEST_INVALID")
    if not isinstance(phase, str) or phase not in {"discover", "analyze", "merge", "publish"}:
        return _error("phase_invalid")
    try:
        store = ImageIndexBuild(base, args.get("generation"))
        if phase == "discover":
            result = store.discover(
                device_id=args.get("device_id", "server"), max_files=max_files,
                max_total_bytes=args.get("max_total_bytes", MAX_SOURCE_BYTES),
                max_depth=args.get("max_depth", MAX_SOURCE_DEPTH), recursive=args.get("recursive", True),
            )
            return result if result["source_count"] else _error(
                "image_corpus_empty", key="ERR_DURABLE_SOURCE_MISSING")
        entries = args.get("entries", [])
        if phase == "analyze":
            import i18n
            records, contexts = store.discovery_group(entries)
            identity = analysis_identity(i18n.current_lang())
            receipts = [_analyze_one(
                store, record, context=contexts[folder_label(Path(record["original_path"]).parent.name)],
                identity=identity, force=bool(args.get("force", False)),
            ) for record in records]
            error_counts = {}
            for item in receipts:
                entry = store._part(item)["entry"]
                if entry.get("indexing_status") == "not_indexed":
                    code = entry["indexing_error_code"]
                    error_counts[code] = error_counts.get(code, 0) + 1
            failed = sum(error_counts.values())
            return {"ok": True, "entries": [store.merge(receipts)],
                    "ok_count": len(receipts) - failed, "fail_count": failed,
                    # Originating units report each handled outcome once.
                    # Reducers/publication must not recount the same failures.
                    "domain_outcome": {"version": 1, "error_counts": error_counts}}
        if phase == "merge":
            return {"ok": True, "entries": [store.merge(entries)]}
        return store.publish(entries, expected_count=args.get("expected_count"))
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        log.warning("Image-index phase %s failed (%s)", phase, type(error).__name__)
        return _error(str(error) if isinstance(error, ImageIndexBuildError) else "image_index_phase_failed")


def reverse(plan, results):
    """Historical reverse entry point, deliberately non-destructive."""
    return {"ok": False, "ok_count": 0, "fail_count": 0,
            "error": _msg("MSG_UNDO_NOT_REVERSIBLE_GENERIC"), "error_code": "not_reversible"}


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
