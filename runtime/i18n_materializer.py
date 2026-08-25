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
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping

import config as _C
from i18n_registry import LocalizationRegistry, normalize_language
from manifest_inventory import (
    ManifestOrigin,
    ManifestRef,
    ManifestSource,
    default_manifest_sources,
    inventory_manifests,
)

if TYPE_CHECKING:
    from contract_store import ContractRevision, VerifiedManifest


_HUMAN_REVIEW_DETECTION_KINDS = frozenset({"regex"})
_GENERATED_ALTERNATE_LINK = re.compile(
    r"<link\b(?=[^>]*\brel\s*=\s*[\"']alternate[\"'])[^>]*>\s*",
    flags=re.IGNORECASE,
)
_CONTRACT_STATE_VERSION = 1
_CONTRACT_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SCHEMA_MAP_CHILDREN = frozenset({
    "$defs", "definitions", "dependentSchemas", "patternProperties",
    "properties",
})
_SCHEMA_SINGLE_CHILDREN = frozenset({
    "additionalProperties", "contains", "contentSchema", "else", "if",
    "items", "not", "propertyNames", "then", "unevaluatedItems",
    "unevaluatedProperties",
})
_SCHEMA_LIST_CHILDREN = frozenset({
    "allOf", "anyOf", "oneOf", "prefixItems",
})


class LanguageStateError(ValueError):
    """A manifest language-state document is invalid or non-canonical."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class LanguageStateMigration:
    """Canonical bytes and deterministic evidence from a legacy migration."""

    state_bytes: bytes
    added_entries: tuple[str, ...]
    dropped_entries: tuple[str, ...]
    normalized_language_tags: tuple[str, ...]
    cleared_provenance: tuple[str, ...]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _state_hash(value: str) -> str:
    return "sha256:" + sha256_text(value)


def _freeze_state(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_state(item) for key, item in value.items()
        })
    if isinstance(value, list):
        return tuple(_freeze_state(item) for item in value)
    return value


def _state_json_without_duplicates(data: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise LanguageStateError("language_state_duplicate_key", key)
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except UnicodeDecodeError as exc:
        raise LanguageStateError("language_state_utf8", str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise LanguageStateError("language_state_json", str(exc)) from exc


def manifest_language_selectors(
    manifest: Mapping[str, Any],
) -> Mapping[str, Mapping[str, str]]:
    """Enumerate every localized contract surface in canonical path form.

    The executor arguments are a JSON-Schema tree, so descriptions may occur
    below nested ``properties`` and ``items`` nodes.  Traversing the tree is
    both simpler and more future-proof than special-casing top-level argument
    names.  Dots in a path segment are rejected because v1 selectors use dots
    as their unambiguous structural separator.
    """
    selectors: dict[str, Mapping[str, str]] = {}

    def add(path: tuple[str, ...], languages: Mapping[str, Any]) -> None:
        if any(not part or "." in part for part in path):
            raise LanguageStateError(
                "language_selector_invalid", ".".join(path),
            )
        selector = ".".join(path)
        if selector in selectors:
            raise LanguageStateError("language_selector_duplicate", selector)
        selectors[selector] = languages

    def visit_schema(node: Mapping[str, Any], path: tuple[str, ...]) -> None:
        description = node.get("description")
        if isinstance(description, Mapping):
            add((*path, "description"), description)

        for keyword in sorted(_SCHEMA_MAP_CHILDREN):
            children = node.get(keyword)
            if not isinstance(children, Mapping):
                continue
            for raw_name, child in children.items():
                if isinstance(child, Mapping):
                    visit_schema(child, (*path, keyword, str(raw_name)))

        for keyword in sorted(_SCHEMA_SINGLE_CHILDREN):
            child = node.get(keyword)
            if isinstance(child, Mapping):
                visit_schema(child, (*path, keyword))
            elif isinstance(child, (list, tuple)):
                for index, item in enumerate(child):
                    if isinstance(item, Mapping):
                        visit_schema(item, (*path, keyword, str(index)))

        for keyword in sorted(_SCHEMA_LIST_CHILDREN):
            children = node.get(keyword)
            if not isinstance(children, (list, tuple)):
                continue
            for index, child in enumerate(children):
                if isinstance(child, Mapping):
                    visit_schema(child, (*path, keyword, str(index)))

    description = manifest.get("description")
    if isinstance(description, Mapping):
        add(("description",), description)
    args = manifest.get("args")
    if isinstance(args, Mapping):
        visit_schema(args, ("args",))
    return MappingProxyType(selectors)


def iter_localized_text_tables(
    node: Mapping[str, Any],
    source_lang: str,
    prefix: tuple[str, ...] = (),
) -> Iterator[tuple[str, str]]:
    """Yield contract prose through the canonical selector enumerator."""
    for selector, languages in manifest_language_selectors(node).items():
        source = languages.get(source_lang)
        if isinstance(source, str):
            yield ".".join((*prefix, selector)), source


def _normalize_contract_language_state(
    state: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if set(state) != {"schema_version", "selectors"}:
        raise LanguageStateError(
            "language_state_schema",
            "expected only schema_version and selectors",
        )
    if (
        type(state.get("schema_version")) is not int
        or state.get("schema_version") != _CONTRACT_STATE_VERSION
    ):
        raise LanguageStateError(
            "language_state_version", str(state.get("schema_version"))
        )
    raw_selectors = state.get("selectors")
    if not isinstance(raw_selectors, Mapping):
        raise LanguageStateError(
            "language_state_selectors", "selectors must be an object"
        )
    expected = manifest_language_selectors(manifest)
    if set(raw_selectors) != set(expected):
        raise LanguageStateError(
            "language_state_coverage",
            f"expected={sorted(expected)} "
            f"actual={sorted(str(key) for key in raw_selectors)}",
        )
    normalized_selectors: dict[str, Any] = {}
    for selector, raw_languages in raw_selectors.items():
        if not isinstance(selector, str):
            raise LanguageStateError("language_selector_invalid", repr(selector))
        if selector != "description":
            parts = selector.split(".")
            if (
                len(parts) < 2
                or parts[0] != "args"
                or parts[-1] != "description"
                or any(not part for part in parts)
            ):
                raise LanguageStateError("language_selector_invalid", selector)
        if not isinstance(raw_languages, Mapping) or not raw_languages:
            raise LanguageStateError("language_state_languages", selector)
        manifest_languages = expected.get(selector)
        if manifest_languages is not None and set(raw_languages) != set(manifest_languages):
            raise LanguageStateError("language_state_language_coverage", selector)
        normalized_languages: dict[str, Any] = {}
        for language, raw_entry in raw_languages.items():
            if not isinstance(language, str):
                raise LanguageStateError("language_tag_invalid", repr(language))
            try:
                normalized_language = normalize_language(language)
            except (TypeError, ValueError) as exc:
                raise LanguageStateError("language_tag_invalid", language) from exc
            if normalized_language != language:
                raise LanguageStateError("language_tag_noncanonical", language)
            if not isinstance(raw_entry, Mapping) or set(raw_entry) != {
                "version_hash", "source_lang", "source_hash",
            }:
                raise LanguageStateError(
                    "language_state_entry", f"{selector}:{language}"
                )
            version_hash = raw_entry.get("version_hash")
            source_language = raw_entry.get("source_lang")
            source_hash = raw_entry.get("source_hash")
            if (
                not isinstance(version_hash, str)
                or not _CONTRACT_DIGEST_RE.fullmatch(version_hash)
            ):
                raise LanguageStateError(
                    "language_version_hash", f"{selector}:{language}"
                )
            if source_language is None:
                if source_hash is not None:
                    raise LanguageStateError(
                        "language_source_pair", f"{selector}:{language}"
                    )
            else:
                if not isinstance(source_language, str):
                    raise LanguageStateError(
                        "language_source_tag", f"{selector}:{language}"
                    )
                try:
                    canonical_source = normalize_language(source_language)
                except (TypeError, ValueError) as exc:
                    raise LanguageStateError(
                        "language_source_tag", source_language
                    ) from exc
                if canonical_source != source_language:
                    raise LanguageStateError("language_source_tag", source_language)
                if (
                    not isinstance(source_hash, str)
                    or not _CONTRACT_DIGEST_RE.fullmatch(source_hash)
                ):
                    raise LanguageStateError(
                        "language_source_hash", f"{selector}:{language}"
                    )
                if source_language not in manifest_languages:
                    raise LanguageStateError(
                        "language_source_missing", source_language
                    )
                source_text = manifest_languages.get(source_language)
                if (
                    not isinstance(source_text, str)
                    or _state_hash(source_text) != source_hash
                ):
                    raise LanguageStateError(
                        "language_source_mismatch",
                        f"{selector}:{language}",
                    )
            text = manifest_languages.get(language)
            if not isinstance(text, str):
                raise LanguageStateError(
                    "language_text_invalid", f"{selector}:{language}"
                )
            if _state_hash(text) != version_hash:
                raise LanguageStateError(
                    "language_version_mismatch", f"{selector}:{language}"
                )
            normalized_languages[language] = {
                "source_hash": source_hash,
                "source_lang": source_language,
                "version_hash": version_hash,
            }
        normalized_selectors[selector] = normalized_languages
    return {
        "schema_version": _CONTRACT_STATE_VERSION,
        "selectors": normalized_selectors,
    }


def encode_language_state(
    state: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
) -> bytes:
    """Validate and encode the sole canonical v1 contract-state format."""
    normalized = _normalize_contract_language_state(state, manifest=manifest)
    return (
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
    ).encode("utf-8")


def decode_language_state(
    state_bytes: bytes,
    *,
    manifest: Mapping[str, Any],
) -> Mapping[str, object]:
    """Decode strictly; legacy selectors are deliberately not aliases."""
    parsed = _state_json_without_duplicates(state_bytes)
    if not isinstance(parsed, Mapping):
        raise LanguageStateError("language_state_schema", "root must be an object")
    normalized = _normalize_contract_language_state(parsed, manifest=manifest)
    if encode_language_state(normalized, manifest=manifest) != state_bytes:
        raise LanguageStateError("language_state_noncanonical")
    return _freeze_state(normalized)


def migrate_language_state_bytes(
    legacy_bytes: bytes,
    *,
    manifest: Mapping[str, Any],
) -> LanguageStateMigration:
    """Rebuild canonical state and report every normalization or discard.

    M4 records this evidence before cutover.  Nothing is silently retained or
    silently discarded merely because a legacy companion was permissive.
    """
    legacy = _state_json_without_duplicates(legacy_bytes)
    if not isinstance(legacy, Mapping):
        raise LanguageStateError("language_state_migration_root")
    legacy_selectors: Mapping[str, Any]
    if set(legacy) == {"schema_version", "selectors"}:
        raw_selectors = legacy.get("selectors")
        if not isinstance(raw_selectors, Mapping):
            raise LanguageStateError("language_state_migration_root")
        legacy_selectors = raw_selectors
    else:
        legacy_selectors = legacy
    expected = manifest_language_selectors(manifest)
    added_entries: set[str] = set()
    dropped_entries: set[str] = set()
    normalized_tags: set[str] = set()
    cleared_provenance: set[str] = set()
    mapped: dict[str, Mapping[str, Any]] = {}
    for raw_selector, raw_languages in legacy_selectors.items():
        if not isinstance(raw_selector, str):
            dropped_entries.add(f"{raw_selector!r}:*")
            continue
        if not isinstance(raw_languages, Mapping):
            dropped_entries.add(f"{raw_selector}:*")
            continue
        canonical = raw_selector
        parts = raw_selector.split(".")
        if (
            len(parts) == 3
            and parts[0] == "args"
            and parts[1]
            and parts[2] == "description"
        ):
            canonical = f"args.properties.{parts[1]}.description"
        if canonical not in expected:
            dropped_entries.add(f"{raw_selector}:*")
            continue
        if canonical in mapped:
            raise LanguageStateError("language_state_migration_ambiguous", canonical)
        mapped[canonical] = raw_languages
    selectors: dict[str, Any] = {}
    for selector, language_table in expected.items():
        legacy_languages = mapped.get(selector, {})
        normalized_legacy: dict[str, tuple[str, Mapping[str, Any]]] = {}
        for raw_language, raw_entry in legacy_languages.items():
            entry_id = f"{selector}:{raw_language}"
            if not isinstance(raw_language, str) or not isinstance(raw_entry, Mapping):
                dropped_entries.add(entry_id)
                continue
            try:
                language = normalize_language(raw_language)
            except (TypeError, ValueError):
                dropped_entries.add(entry_id)
                continue
            if language in normalized_legacy:
                raise LanguageStateError(
                    "language_state_migration_ambiguous",
                    f"{selector}:{language}",
                )
            if language != raw_language:
                normalized_tags.add(f"{selector}:{raw_language}->{language}")
            normalized_legacy[language] = (raw_language, raw_entry)
        for language, (raw_language, _raw_entry) in normalized_legacy.items():
            if language not in language_table:
                dropped_entries.add(f"{selector}:{raw_language}")
        rebuilt_languages: dict[str, Any] = {}
        for language, text in language_table.items():
            if not isinstance(language, str):
                raise LanguageStateError("language_tag_invalid", str(language))
            try:
                canonical_language = normalize_language(language)
            except (TypeError, ValueError) as exc:
                raise LanguageStateError("language_tag_invalid", language) from exc
            if canonical_language != language:
                raise LanguageStateError("language_tag_noncanonical", language)
            if not isinstance(text, str):
                raise LanguageStateError(
                    "language_text_invalid", f"{selector}:{language}"
                )
            current_hash = _state_hash(text)
            old = normalized_legacy.get(language)
            old_entry = old[1] if old is not None else {}
            if old is None:
                added_entries.add(f"{selector}:{language}")
            source_language: str | None = None
            source_hash: str | None = None
            if old_entry.get("version_hash") == current_hash:
                raw_source_language = old_entry.get("source_lang")
                raw_source_hash = old_entry.get("source_hash")
                if isinstance(raw_source_language, str):
                    try:
                        candidate_source = normalize_language(raw_source_language)
                    except (TypeError, ValueError):
                        candidate_source = ""
                    if candidate_source and candidate_source != raw_source_language:
                        normalized_tags.add(
                            f"{selector}:{language}:source:"
                            f"{raw_source_language}->{candidate_source}"
                        )
                    source_text = language_table.get(candidate_source)
                    if (
                        candidate_source == raw_source_language
                        and isinstance(source_text, str)
                        and isinstance(raw_source_hash, str)
                        and _CONTRACT_DIGEST_RE.fullmatch(raw_source_hash)
                        and raw_source_hash == _state_hash(source_text)
                    ):
                        source_language = candidate_source
                        source_hash = raw_source_hash
            if old is not None and source_language is None and (
                old_entry.get("source_lang") is not None
                or old_entry.get("source_hash") is not None
            ):
                cleared_provenance.add(f"{selector}:{language}")
            rebuilt_languages[language] = {
                "version_hash": current_hash,
                "source_lang": source_language,
                "source_hash": source_hash,
            }
        selectors[selector] = rebuilt_languages
    state_bytes = encode_language_state(
        {"schema_version": _CONTRACT_STATE_VERSION, "selectors": selectors},
        manifest=manifest,
    )
    return LanguageStateMigration(
        state_bytes=state_bytes,
        added_entries=tuple(sorted(added_entries)),
        dropped_entries=tuple(sorted(dropped_entries)),
        normalized_language_tags=tuple(sorted(normalized_tags)),
        cleared_provenance=tuple(sorted(cleared_provenance)),
    )


@dataclass(frozen=True, slots=True)
class InventoryItem:
    resource_id: str
    layer: str
    source_lang: str
    source_hash: str
    source_text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    manual_review: bool = False
    basis_id: str | None = None
    contract_ref: ManifestRef | None = None
    # Ephemeral verified object used by the activation gate.  It is never
    # serialized into registry metadata, so paths cannot become authority.
    contract_snapshot: "VerifiedManifest | None" = None


ContractSnapshotProvider = Callable[[ManifestRef], "ContractRevision"]


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


def _manifest_sources(roots: tuple[Path, ...]) -> tuple[ManifestSource, ...]:
    """Map configured localization roots to the neutral shared inventory.

    Known roots keep their stable origin and topology.  An injected fixture
    remains explicit and receives no authority beyond its own directory.
    """
    known = {
        Path(source.root).resolve(strict=False): source
        for source in default_manifest_sources()
    }
    selected: list[ManifestSource] = []
    for root in roots:
        path = Path(root)
        source = known.get(path.resolve(strict=False))
        if source is None:
            source = ManifestSource(
                ManifestOrigin.EXPLICIT,
                path,
                min_depth=0,
                max_depth=None,
                allowed_code_roots=(path,),
            )
        selected.append(source)
    return tuple(selected)


def _iter_contract_items(
    paths: LocalizationPaths,
    source_lang: str,
    contract_snapshot_provider: ContractSnapshotProvider | None = None,
) -> Iterator[InventoryItem]:
    if contract_snapshot_provider is None:
        from manifest_inventory import ManifestLayout, resolve_manifest_layout

        if resolve_manifest_layout() is ManifestLayout.STORE_ONLY:
            raise ValueError(
                "contract_snapshot_provider is required in store-only mode",
            )
    manifest_inventory = inventory_manifests(
        _manifest_sources(paths.manifest_roots),
    )
    if manifest_inventory.problems:
        summary = "; ".join(
            f"{problem.code}:{problem.path}"
            for problem in manifest_inventory.problems[:8]
        )
        raise ValueError(f"manifest inventory is not clean: {summary}")
    refs = (
        manifest_inventory.manifests
        if contract_snapshot_provider is None
        else manifest_inventory.admitted()
    )
    for ref in refs:
        path = ref.manifest_path
        basis_id: str | None = None
        verified_snapshot = None
        if contract_snapshot_provider is None:
            raw = path.read_bytes()
            manifest = tomllib.loads(raw.decode("utf-8"))
            manifest_hash = sha256_bytes(raw)
        else:
            revision = contract_snapshot_provider(ref)
            from contract_store import ContractRetirement, VerifiedManifest

            if isinstance(revision, ContractRetirement):
                # The tombstone is authenticated by the provider and is the
                # live assertion that this contract contributes no surfaces.
                continue
            if not isinstance(revision, VerifiedManifest):
                raise ValueError(
                    f"verified contract revision unavailable: {ref.contract_id}",
                )
            snapshot = revision
            verified_snapshot = snapshot
            if snapshot.contract_id != ref.contract_id:
                raise ValueError(
                    "contract snapshot identity does not match inventory: "
                    f"{ref.contract_id}"
                )
            basis_id = snapshot.generation_id
            if (
                not isinstance(basis_id, str)
                or _CONTRACT_DIGEST_RE.fullmatch(basis_id) is None
            ):
                raise ValueError(
                    f"verified generation unavailable: {ref.contract_id}"
                )
            if not isinstance(snapshot.parsed, Mapping):
                raise ValueError(
                    f"verified manifest unavailable: {ref.contract_id}"
                )
            manifest = snapshot.parsed
            if (
                not isinstance(snapshot.manifest_hash, str)
                or _CONTRACT_DIGEST_RE.fullmatch(snapshot.manifest_hash) is None
            ):
                raise ValueError(
                    f"verified manifest hash unavailable: {ref.contract_id}"
                )
            manifest_hash = snapshot.manifest_hash.removeprefix("sha256:")
        name = manifest.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"manifest name unavailable: {ref.contract_id}")
        for selector, languages in manifest_language_selectors(manifest).items():
            source = languages.get(source_lang)
            if not isinstance(source, str):
                if contract_snapshot_provider is not None:
                    raise ValueError(
                        "verified contract surface has no source language: "
                        f"{ref.contract_id}:{selector}:{source_lang}"
                    )
                continue
            language_hashes = {
                str(language): "sha256:" + sha256_text(text)
                for language, text in languages.items()
                if isinstance(language, str) and isinstance(text, str)
            }
            metadata: dict[str, Any] = {
                "manifest_relative": ref.manifest_relative,
                "manifest_hash": manifest_hash,
                "selector": selector,
                "executor": name,
                "contract_id": str(ref.contract_id),
                "origin": ref.origin.value,
                "status": ref.status.value,
            }
            # The legacy publisher still needs its authoring destination until
            # M4.  A versioned candidate deliberately carries only structural
            # identity; registry metadata cannot grant a destination path.
            if basis_id is None:
                metadata["manifest_path"] = str(path)
            else:
                metadata["language_hashes"] = language_hashes
            yield InventoryItem(
                resource_id=f"contract:{name}:{selector}",
                layer="contract", source_lang=source_lang,
                source_hash=sha256_text(source), source_text=source,
                metadata=metadata,
                basis_id=basis_id,
                contract_ref=ref,
                contract_snapshot=verified_snapshot,
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
    contract_snapshot_provider: ContractSnapshotProvider | None = None,
) -> tuple[InventoryItem, ...]:
    source = normalize_language(source_lang)
    items = [
        *_iter_prompt_items(paths, source),
        *_iter_contract_items(paths, source, contract_snapshot_provider),
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
    contract_snapshot_provider: ContractSnapshotProvider | None = None,
) -> MaterializationReport:
    """Register the corpus and create idempotent target placeholders."""
    paths = paths or LocalizationPaths()
    source = normalize_language(source_lang)
    target = normalize_language(target_lang)
    if source == target:
        raise ValueError("source and target language must differ")
    items = inventory(
        paths,
        source_lang=source,
        contract_snapshot_provider=contract_snapshot_provider,
    )
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
            basis_id=item.basis_id,
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
            if item.basis_id is not None:
                # M3 versioned publication is deliberately dormant and never
                # mirrors the authoring companion before the M4 cutover.
                continue
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
    contract_snapshot_provider: ContractSnapshotProvider | None = None,
) -> MaterializationReport | None:
    """Read the signed request and materialize only its requested locale."""
    request, error = _C.read_localization_request(request_path)
    if request is None:
        if error == "missing":
            return None
        raise ValueError(f"localization request is not valid: {error}")
    if request.state != "bootstrap_english" or not request.requested_lang:
        return None
    selected_registry = registry or LocalizationRegistry()
    snapshot_provider = contract_snapshot_provider
    if snapshot_provider is None:
        from i18n_pipeline import live_contract_context

        snapshot_provider = live_contract_context(
            selected_registry,
        ).snapshot_provider
    return materialize(
        request.requested_lang,
        registry=selected_registry, paths=paths,
        source_lang=request.instance_lang,
        contract_snapshot_provider=snapshot_provider,
    )
