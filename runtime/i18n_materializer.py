"""Deterministic inventory and materialization for an instance locale.

F3 never calls an LLM.  It first enumerates every eligible resource, records
its exact source hash in :mod:`i18n_registry`, creates target placeholders,
and leaves translation to bounded workers.  Paths and databases are injected
so the same mechanism serves production, installers, and isolated fixtures.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import config as _C
from i18n_registry import LocalizationRegistry, normalize_language


_LOCALIZABLE_FIELDS = frozenset({
    "description", "summary", "title", "label", "help", "message",
})
_HUMAN_REVIEW_DETECTION_KINDS = frozenset({"regex"})
_GENERATED_ALTERNATE_LINK = re.compile(
    r"<link\b(?=[^>]*\brel\s*=\s*[\"']alternate[\"'])[^>]*>\s*",
    flags=re.IGNORECASE,
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class InventoryItem:
    resource_id: str
    layer: str
    source_lang: str
    source_hash: str
    source_text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    manual_review: bool = False


@dataclass(frozen=True, slots=True)
class LocalizationPaths:
    prompts: Path = _C.PATH_RUNTIME / "prompts"
    manifest_roots: tuple[Path, ...] = (
        _C.PATH_EXECUTORS,
        _C.PATH_RUNTIME / "builtin_executor_contracts",
    )
    messages_db: Path = _C.DB_I18N
    detection_db: Path = _C.DB_DETECTION
    public_messages_db: Path = _C.PATH_ROOT / "install" / "data" / "i18n_seed.sqlite"
    device_catalog: Path = _C.PATH_RUNTIME / "device_shim" / "messages_i18n.json"
    docs: Path = _C.PATH_DOCS
    include_runtime_catalogs: bool = True


@dataclass(frozen=True, slots=True)
class MaterializationReport:
    source_lang: str
    target_lang: str
    resources: int
    by_layer: Mapping[str, int]
    message_placeholders: int
    detection_placeholders: int
    prompt_state_path: str


def _relative_id(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _iter_prompt_items(paths: LocalizationPaths, source_lang: str) -> Iterator[InventoryItem]:
    source_root = paths.prompts / source_lang
    if not source_root.is_dir():
        return
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or path.suffix not in {".j2", ".yaml"}:
            continue
        if "_pending" in path.parts:
            continue
        relative = _relative_id(path, source_root)
        source = path.read_text(encoding="utf-8")
        yield InventoryItem(
            resource_id=f"prompt:{relative}", layer="prompt",
            source_lang=source_lang, source_hash=sha256_text(source),
            source_text=source,
            metadata={"relative_path": relative, "format": path.suffix.lstrip(".")},
        )


def iter_localized_text_tables(
    node: Mapping[str, Any], source_lang: str, prefix: tuple[str, ...] = (),
) -> Iterator[tuple[str, str]]:
    """Yield prose tables without treating schemas or capabilities as text.

    Any nested field whose semantic name is localizable and whose value is a
    language table is admitted.  Thus new ``output.description`` or
    ``hint.description`` fields need no new executor-specific code.
    """
    for key, value in node.items():
        path = prefix + (str(key),)
        if not isinstance(value, Mapping):
            continue
        source = value.get(source_lang)
        if path[-1] in _LOCALIZABLE_FIELDS and isinstance(source, str):
            yield ".".join(path), source
            continue
        yield from iter_localized_text_tables(value, source_lang, path)


def _iter_manifest_paths(roots: Iterable[Path]) -> Iterator[tuple[Path, Path]]:
    seen: set[Path] = set()
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("manifest.toml")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield root, path


def _iter_contract_items(paths: LocalizationPaths, source_lang: str) -> Iterator[InventoryItem]:
    for root, path in _iter_manifest_paths(paths.manifest_roots):
        raw = path.read_bytes()
        manifest = tomllib.loads(raw.decode("utf-8"))
        manifest_name = str(manifest.get("name") or path.parent.name)
        relative = _relative_id(path, root)
        for selector, source in iter_localized_text_tables(manifest, source_lang):
            yield InventoryItem(
                resource_id=f"contract:{manifest_name}:{selector}",
                layer="contract", source_lang=source_lang,
                source_hash=sha256_text(source), source_text=source,
                metadata={
                    "manifest_path": str(path), "manifest_relative": relative,
                    "manifest_hash": sha256_bytes(raw), "selector": selector,
                    "executor": manifest_name,
                },
            )


def _query_rows(path: Path, query: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
    if not path.is_file():
        return []
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query, params).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()


def _iter_message_items(paths: LocalizationPaths, source_lang: str) -> Iterator[InventoryItem]:
    public_keys = {
        str(row["key"])
        for row in _query_rows(
            paths.public_messages_db,
            "SELECT key FROM i18n WHERE lang=? AND text IS NOT NULL ORDER BY key",
            (source_lang,),
        )
    }
    rows = _query_rows(
        paths.messages_db,
        "SELECT key,text FROM i18n WHERE lang=? AND text IS NOT NULL AND trim(text)<>'' ORDER BY key",
        (source_lang,),
    )
    for row in rows:
        source = str(row["text"])
        yield InventoryItem(
            resource_id=f"message:{row['key']}", layer="message",
            source_lang=source_lang, source_hash=sha256_text(source),
            source_text=source, metadata={
                "key": str(row["key"]),
                "public": str(row["key"]) in public_keys,
            },
        )


def _iter_runtime_catalog_items(source_lang: str) -> Iterator[InventoryItem]:
    """Enumerate typed runtime prose catalogs through one open protocol."""

    from services_registry import localization_inventory as service_inventory
    from ui_surfaces import localization_inventory as surface_inventory

    providers = (
        ("services", service_inventory),
        ("settings", surface_inventory),
    )
    for catalog_name, provider in providers:
        for key, source in sorted(provider(source_lang)):
            yield InventoryItem(
                resource_id=f"message:{key}", layer="message",
                source_lang=source_lang, source_hash=sha256_text(source),
                source_text=source,
                metadata={
                    "key": key, "public": False,
                    "catalog": catalog_name,
                },
            )


def _iter_detection_items(paths: LocalizationPaths, source_lang: str) -> Iterator[InventoryItem]:
    columns = {
        str(row["name"])
        for row in _query_rows(paths.detection_db, "PRAGMA table_info(detection_lexicon)", ())
    }
    review_expr = "review_policy" if "review_policy" in columns else "'automatic' AS review_policy"
    rows = _query_rows(
        paths.detection_db,
        f"""SELECT concept,kind,match_mode,payload,{review_expr} FROM detection_lexicon
           WHERE lang=? AND payload IS NOT NULL ORDER BY concept""",
        (source_lang,),
    )
    for row in rows:
        source = str(row["payload"])
        kind = str(row["kind"])
        yield InventoryItem(
            resource_id=f"input:{row['concept']}", layer="input",
            source_lang=source_lang, source_hash=sha256_text(source),
            source_text=source,
            metadata={
                "concept": str(row["concept"]), "kind": kind,
                "match_mode": str(row["match_mode"]),
                "review_policy": str(row["review_policy"]),
            },
            manual_review=(
                kind in _HUMAN_REVIEW_DETECTION_KINDS
                or str(row["review_policy"]) == "manual"
            ),
        )


def _iter_knowledge_items(paths: LocalizationPaths, source_lang: str) -> Iterator[InventoryItem]:
    source_root = paths.docs / source_lang
    if not source_root.is_dir():
        return
    for path in sorted(source_root.rglob("*.html")):
        source = path.read_text(encoding="utf-8")
        relative = _relative_id(path, source_root)
        # Reciprocal hreflang links are derived publication metadata. Adding a
        # newly admitted locale must not invalidate the semantic source text
        # and recursively stale every already-reviewed translation.
        semantic_source = _GENERATED_ALTERNATE_LINK.sub("", source)
        yield InventoryItem(
            resource_id=f"knowledge:{relative}", layer="knowledge",
            source_lang=source_lang, source_hash=sha256_text(semantic_source),
            source_text=source,
            metadata={"relative_path": relative, "format": "html", "public": True},
        )


def _device_catalog_item(paths: LocalizationPaths, source_lang: str) -> InventoryItem | None:
    rows = _query_rows(
        paths.public_messages_db,
        """SELECT key,text FROM i18n WHERE lang=? AND text IS NOT NULL
           AND (key LIKE 'ERR\\_%' ESCAPE '\\' OR key LIKE 'WARN\\_%' ESCAPE '\\'
                OR key LIKE 'MSG\\_%' ESCAPE '\\') ORDER BY key""",
        (source_lang,),
    )
    if not rows:
        return None
    catalog = {str(row["key"]): str(row["text"]) for row in rows}
    encoded = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return InventoryItem(
        resource_id="device:public-message-catalog", layer="device",
        source_lang=source_lang, source_hash=sha256_text(encoded),
        source_text=encoded,
        metadata={"keys": sorted(catalog), "derived_from": str(paths.public_messages_db)},
    )


def inventory(
    paths: LocalizationPaths,
    *,
    source_lang: str = _C.BOOTSTRAP_LANGUAGE,
) -> tuple[InventoryItem, ...]:
    source = normalize_language(source_lang)
    items = [
        *_iter_prompt_items(paths, source),
        *_iter_contract_items(paths, source),
        *_iter_message_items(paths, source),
        *_iter_detection_items(paths, source),
        *_iter_knowledge_items(paths, source),
    ]
    if paths.include_runtime_catalogs:
        by_id = {item.resource_id: item for item in items}
        for item in _iter_runtime_catalog_items(source):
            existing = by_id.get(item.resource_id)
            if existing is not None:
                if existing.source_hash != item.source_hash:
                    raise ValueError(
                        f"conflicting catalog source: {item.resource_id}"
                    )
                continue
            items.append(item)
            by_id[item.resource_id] = item
    device = _device_catalog_item(paths, source)
    if device is not None:
        items.append(device)
    # Tutor compilation is a derived resource whose source version is the
    # complete public knowledge inventory, never a private runtime index.
    knowledge_hashes = sorted(item.source_hash for item in items if item.layer == "knowledge")
    if knowledge_hashes:
        joined = "\n".join(knowledge_hashes)
        items.append(InventoryItem(
            resource_id="tutor:public-catalog", layer="tutor",
            source_lang=source, source_hash=sha256_text(joined),
            source_text=joined, metadata={"derived": True},
        ))
    ids = [item.resource_id for item in items]
    if len(ids) != len(set(ids)):
        duplicates = sorted(key for key in set(ids) if ids.count(key) > 1)
        raise ValueError(f"duplicate localization resource ids: {duplicates}")
    return tuple(sorted(items, key=lambda item: (item.layer, item.resource_id)))


def _atomic_json(path: Path, payload: Mapping[str, Any], *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _materialize_message_placeholder(db: Path, item: InventoryItem, target: str) -> bool:
    if not db.is_file():
        return False
    conn = sqlite3.connect(str(db))
    try:
        key = str(item.metadata["key"])
        before = conn.total_changes
        conn.execute(
            """INSERT INTO i18n(key,lang,text,needs_translation,source_lang,updated_at)
               VALUES (?,?,NULL,1,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ON CONFLICT(key,lang) DO UPDATE SET
                 needs_translation=CASE
                   WHEN i18n.source_text_hash IS NULL OR i18n.source_text_hash<>? THEN 1
                   ELSE i18n.needs_translation END,
                 source_lang=excluded.source_lang,updated_at=excluded.updated_at""",
            (key, target, item.source_lang, "sha256:" + item.source_hash),
        )
        conn.commit()
        return conn.total_changes > before
    finally:
        conn.close()


def _materialize_detection_placeholder(db: Path, item: InventoryItem, target: str) -> bool:
    if not db.is_file():
        return False
    conn = sqlite3.connect(str(db))
    try:
        columns = {
            str(row[1]) for row in conn.execute(
                "PRAGMA table_info(detection_lexicon)"
            )
        }
        if "review_policy" not in columns:
            conn.execute(
                "ALTER TABLE detection_lexicon ADD COLUMN "
                "review_policy TEXT NOT NULL DEFAULT 'automatic'"
            )
        before = conn.total_changes
        conn.execute(
            """INSERT INTO detection_lexicon
               (concept,lang,kind,match_mode,payload,needs_translation,source_lang,review_policy,updated_at)
               VALUES (?,?,?,?,NULL,1,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ON CONFLICT(concept,lang) DO UPDATE SET
                 needs_translation=CASE
                   WHEN detection_lexicon.source_text_hash IS NULL
                     OR detection_lexicon.source_text_hash<>? THEN 1
                   ELSE detection_lexicon.needs_translation END,
                 source_lang=excluded.source_lang,
                 review_policy=excluded.review_policy,
                 updated_at=excluded.updated_at""",
            (
                item.metadata["concept"], target, item.metadata["kind"],
                item.metadata["match_mode"], item.source_lang,
                str(item.metadata.get("review_policy") or (
                    "manual" if item.manual_review else "automatic"
                )), "sha256:" + item.source_hash,
            ),
        )
        conn.commit()
        return conn.total_changes > before
    finally:
        conn.close()


def materialize(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    paths: LocalizationPaths | None = None,
    source_lang: str = _C.BOOTSTRAP_LANGUAGE,
) -> MaterializationReport:
    """Register the corpus and create idempotent target placeholders."""
    paths = paths or LocalizationPaths()
    source = normalize_language(source_lang)
    target = normalize_language(target_lang)
    if source == target:
        raise ValueError("source and target language must differ")
    items = inventory(paths, source_lang=source)
    target_root = paths.prompts / target
    (target_root / "_pending").mkdir(parents=True, exist_ok=True)
    prompt_state_path = target_root / ".lang_state.json"
    try:
        prompt_state = json.loads(prompt_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        prompt_state = {}
    if not isinstance(prompt_state, dict):
        prompt_state = {}
    by_layer: dict[str, int] = {}
    message_placeholders = 0
    detection_placeholders = 0
    manifest_states: dict[Path, dict[str, Any]] = {}
    for item in items:
        registered = registry.register(
            item.resource_id, item.layer, source, target, item.source_hash,
            metadata=item.metadata, manual_review=item.manual_review,
        )
        by_layer[item.layer] = by_layer.get(item.layer, 0) + 1
        if item.layer == "prompt":
            relative = str(item.metadata["relative_path"])
            prompt_state.setdefault(relative, {}).update({
                "status": registered.status, "source_lang": source,
                "source_hash": "sha256:" + item.source_hash,
                "version_hash": (
                    "sha256:" + registered.translation_hash
                    if registered.translation_hash else ""
                ),
            })
        elif item.layer == "contract":
            state_path = Path(str(item.metadata["manifest_path"])).with_name(
                "manifest.lang_state.json"
            )
            if state_path not in manifest_states:
                try:
                    loaded = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, TypeError):
                    loaded = {}
                manifest_states[state_path] = loaded if isinstance(loaded, dict) else {}
            selector = str(item.metadata["selector"])
            target_state = manifest_states[state_path].setdefault(selector, {}).setdefault(target, {})
            target_state.update({
                "status": registered.status, "source_lang": source,
                "source_hash": "sha256:" + item.source_hash,
                "version_hash": (
                    "sha256:" + registered.translation_hash
                    if registered.translation_hash else ""
                ),
            })
        elif item.layer == "message":
            message_placeholders += int(_materialize_message_placeholder(paths.messages_db, item, target))
        elif item.layer == "input":
            detection_placeholders += int(_materialize_detection_placeholder(paths.detection_db, item, target))
    _atomic_json(prompt_state_path, prompt_state)
    for state_path, state in manifest_states.items():
        _atomic_json(state_path, state)
    return MaterializationReport(
        source_lang=source, target_lang=target, resources=len(items),
        by_layer=dict(sorted(by_layer.items())),
        message_placeholders=message_placeholders,
        detection_placeholders=detection_placeholders,
        prompt_state_path=str(prompt_state_path),
    )


def materialize_requested(
    *,
    registry: LocalizationRegistry | None = None,
    paths: LocalizationPaths | None = None,
    request_path: Path | None = None,
) -> MaterializationReport | None:
    """Read the signed request and materialize only its requested locale."""
    request, error = _C.read_localization_request(request_path)
    if request is None:
        if error == "missing":
            return None
        raise ValueError(f"localization request is not valid: {error}")
    if request.state != "bootstrap_english" or not request.requested_lang:
        return None
    return materialize(
        request.requested_lang,
        registry=registry or LocalizationRegistry(), paths=paths,
        source_lang=request.instance_lang,
    )
