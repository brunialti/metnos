"""Provider-neutral translation, validation, and admission pipeline.

The module implements RM-0005 F4-F7 as data-driven operations over the F2
registry.  Translation providers receive prose plus BCP-47 tags; structural
validators, not provider promises, decide whether an artifact may advance.
Canonical IDs, TOML schemas, YAML keys, JSON keys, placeholders, code spans,
and HTML markup are never translated.
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
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol

import config as _C

from i18n_materializer import (
    ContractSnapshotProvider,
    InventoryItem,
    LocalizationPaths,
    inventory,
    iter_localized_text_tables,
    manifest_language_selectors,
    sha256_text,
)
from i18n_registry import (
    LocalizationRegistry,
    PublishedTranslation,
    RegistryError,
    ResourceRecord,
    TranslationLease,
    normalize_language,
)
from manifest_inventory import ManifestRef

if TYPE_CHECKING:
    from contract_store import LocalizationPatch, PublicationResult
    from manifest_lint import Finding


Translator = Callable[[str, str, str, str], str]
EquivalenceJudge = Callable[[str, str, str], bool]


class VersionedContractPublisher(Protocol):
    def __call__(
        self,
        ref: ManifestRef,
        *,
        expected_generation_id: str,
        source_language: str,
        target_language: str,
        patches: tuple["LocalizationPatch", ...],
    ) -> "PublicationResult": ...


@dataclass(frozen=True, slots=True)
class LiveContractContext:
    """One layout-selected verified boundary shared by every i18n entry."""

    store_only: bool
    snapshot_provider: ContractSnapshotProvider | None
    publisher: VersionedContractPublisher | None


def live_contract_context(
    registry: LocalizationRegistry,
    *,
    publication: bool = False,
) -> LiveContractContext:
    """Resolve the active contract authority once for an i18n operation."""
    from manifest_inventory import ManifestLayout, resolve_manifest_layout

    if resolve_manifest_layout() is ManifestLayout.AUTHORING:
        return LiveContractContext(False, None, None)
    from functools import partial
    from contract_store import current_contract
    from sign import list_trusted_publics

    trusted = tuple(list_trusted_publics())
    if not trusted:
        raise ValueError("no trusted contract signing keys")
    provider = partial(current_contract, trusted_publics=trusted)
    publisher = None
    if publication:
        from contract_store import publish_localization
        from sign import load_private

        publisher = partial(
            publish_localization,
            private_key=load_private("author"),
            trusted_publics=trusted,
            registry_reconciler=partial(
                reconcile_published_contract_registry,
                registry=registry,
            ),
        )
    return LiveContractContext(True, provider, publisher)

_PROMPT_PROSE_FIELDS = frozenset({
    "body", "description", "error", "header", "help", "instruction",
    "label", "message", "must", "must_not", "ok", "preamble", "query",
    "summary", "title", "user", "when",
})
_SENTINEL_RE = re.compile(r"__METNOS_(?:INV|HTML)_[A-Za-z0-9_]+__")
_JINJA_RE = re.compile(r"\{\{[-+]?.*?[-+]?\}\}|\{%[-+]?.*?[-+]?%\}", re.DOTALL)
_FORMAT_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_.]*)(?:![rsa])?(?::[^{}]*)?\}(?!\})")
_CODE_RE = re.compile(r"```[A-Za-z0-9_+\-]*\n.*?\n```|`[^`\n]+`", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_LOGICAL_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_BARE_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CONTRACT_ARTIFACT_V2_FIELDS = frozenset({
    "schema",
    "resource_id",
    "layer",
    "contract_id",
    "selector",
    "expected_generation",
    "source_lang",
    "target_lang",
    "source_hash",
    "previous_target_hash",
    "candidate_hash",
    "translation",
})


class CandidateValidationError(ValueError):
    """A candidate failed deterministic validation.

    ``findings`` keeps rule results machine-readable for callers that need
    diagnostics.  The optional field preserves compatibility with the older
    validation sites, which still raise this exception with a message only.
    """

    def __init__(self, message: str, *, findings: tuple[Any, ...] = ()) -> None:
        super().__init__(message)
        self.findings = findings


@dataclass(frozen=True, slots=True)
class TranslationReport:
    target_lang: str
    translated: int
    failed: int
    skipped: int
    errors: Mapping[str, str]
    warnings: Mapping[str, tuple["Finding", ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PromotionReport:
    target_lang: str
    admitted: int
    skipped: int
    errors: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class VersionedContractPublicationReport:
    """Outcome of the isolated M3 publisher; it never means activation."""

    target_lang: str
    published_contracts: int
    published_resources: int
    skipped: int
    errors: Mapping[str, str]


def published_contract_registry_identity(
    snapshot,
) -> tuple[str, tuple[str, ...]]:
    """Derive the exact registry ownership claimed by one verified revision."""

    from contract_store import VerifiedManifest

    if not isinstance(snapshot, VerifiedManifest):
        raise TypeError("snapshot must be a verified manifest revision")
    generation = snapshot.generation_id
    if (
        not isinstance(generation, str)
        or _LOGICAL_SHA256_RE.fullmatch(generation) is None
    ):
        raise ValueError("published snapshot has no canonical generation")
    name = snapshot.parsed.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("published snapshot has no executor name")
    selectors = manifest_language_selectors(snapshot.parsed)
    state_selectors = snapshot.language_state.get("selectors")
    if not isinstance(state_selectors, Mapping):
        raise ValueError("published snapshot has no canonical language state")
    resource_ids = tuple(
        f"contract:{name}:{selector}" for selector in selectors
    )
    if not resource_ids:
        raise ValueError("published contract has no localized surfaces")
    return str(snapshot.contract_id), resource_ids


def preflight_published_contract_registry(
    snapshots: tuple[object, ...],
    *,
    registry: LocalizationRegistry | None = None,
) -> None:
    """Read-only ownership check for an authenticated catalog snapshot."""

    if not isinstance(snapshots, tuple) or not snapshots:
        raise ValueError("snapshots must contain verified manifests")
    assignments = tuple(
        published_contract_registry_identity(snapshot)
        for snapshot in snapshots
    )
    if registry is None:
        LocalizationRegistry.preflight_published_contract_path(assignments)
    else:
        registry.preflight_published_contracts(assignments)


def reconcile_published_contract_registry(
    snapshot,
    *,
    registry: LocalizationRegistry | None = None,
) -> tuple[ResourceRecord, ...]:
    """Reconcile RM-0005 from a freshly re-read current contract revision.

    A manifest admits only translations authenticated by its language state.
    A retirement atomically stales the exact ContractId indexed during the
    preceding publication; no executor-name inference is permitted.
    """

    from contract_store import ContractRetirement, VerifiedManifest

    selected_registry = registry or LocalizationRegistry()
    if isinstance(snapshot, ContractRetirement):
        selected_registry.retire_published_contract(str(snapshot.contract_id))
        return ()
    contract_identity, expected_resource_ids = (
        published_contract_registry_identity(snapshot)
    )
    assert isinstance(snapshot, VerifiedManifest)
    generation = snapshot.generation_id
    assert isinstance(generation, str)
    manifest = snapshot.parsed
    name = manifest.get("name")
    assert isinstance(name, str)
    tables = manifest_language_selectors(manifest)
    state_selectors = snapshot.language_state.get("selectors")
    assert isinstance(state_selectors, Mapping)

    contract_id = snapshot.contract_id
    language_hashes_by_selector = {
        selector: {
            language: "sha256:" + sha256_text(text)
            for language, text in languages.items()
        }
        for selector, languages in tables.items()
    }
    publications: list[PublishedTranslation] = []
    resource_ids: list[str] = []
    for selector, languages in tables.items():
        resource_id = f"contract:{name}:{selector}"
        resource_ids.append(resource_id)
        entries = state_selectors.get(selector)
        if not isinstance(entries, Mapping):
            raise ValueError(f"published language state missing selector: {selector}")
        metadata = {
            "manifest_relative": contract_id.relative_manifest,
            "manifest_hash": snapshot.manifest_hash.removeprefix("sha256:"),
            "selector": selector,
            "executor": name,
            "contract_id": str(contract_id),
            "origin": contract_id.origin.value,
            "status": "admitted",
            "language_hashes": language_hashes_by_selector[selector],
        }
        for target, entry in entries.items():
            if not isinstance(entry, Mapping):
                raise ValueError(f"published language state invalid: {selector}:{target}")
            source = entry.get("source_lang")
            source_hash = entry.get("source_hash")
            if source is None:
                continue
            if not isinstance(source, str) or source == target:
                raise ValueError(f"published translation provenance invalid: {selector}:{target}")
            source_text = languages.get(source)
            target_text = languages.get(target)
            if not isinstance(source_text, str) or not isinstance(target_text, str):
                raise ValueError(f"published translation text missing: {selector}:{target}")
            expected_source_hash = "sha256:" + sha256_text(source_text)
            target_hash = "sha256:" + sha256_text(target_text)
            if source_hash != expected_source_hash or entry.get("version_hash") != target_hash:
                raise ValueError(f"published translation hash mismatch: {selector}:{target}")
            publications.append(PublishedTranslation(
                resource_id=resource_id,
                source_lang=source,
                target_lang=str(target),
                source_hash=expected_source_hash,
                translation_hash=target_hash,
                basis_id=generation,
                metadata=metadata,
            ))
    if tuple(resource_ids) != expected_resource_ids:
        raise AssertionError("published registry identity derivation drifted")
    return selected_registry.reconcile_published_contract(
        contract_identity, tuple(resource_ids), tuple(publications),
    )


def _atomic_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _atomic_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _default_translator(source: str, source_lang: str, target_lang: str, context: str) -> str:
    del context
    from i18n_translator import translate_document_text
    translated, errors = translate_document_text(
        source, source_lang=source_lang, target_lang=target_lang,
    )
    if translated is None or errors:
        raise RuntimeError("; ".join(errors or ["translation provider returned no text"]))
    return translated


def _candidate_path(
    item: InventoryItem,
    target: str,
    registry: LocalizationRegistry,
    paths: LocalizationPaths,
    *,
    lease_token: str | None = None,
) -> Path:
    if item.layer == "prompt":
        relative = str(item.metadata["relative_path"])
        return paths.prompts / target / "_pending" / f"{relative}.candidate"
    digest = hashlib.sha256(item.resource_id.encode("utf-8")).hexdigest()
    if item.layer == "contract" and item.basis_id is not None:
        if not lease_token:
            raise CandidateValidationError("versioned candidate lease is unavailable")
        generation = _canonical_logical_hash(
            item.basis_id,
            field="expected_generation",
        ).removeprefix("sha256:")
        lease = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        return (
            registry.path.parent
            / "i18n_candidates"
            / target
            / item.layer
            / generation
            / f"{digest}.{lease}.json"
        )
    return registry.path.parent / "i18n_candidates" / target / item.layer / f"{digest}.json"


def _canonical_logical_hash(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _LOGICAL_SHA256_RE.fullmatch(value) is None:
        raise CandidateValidationError(f"{field} must be a canonical SHA-256 identifier")
    return value


def _contract_artifact_payload_v2(
    item: InventoryItem,
    target: str,
    translation: Any,
) -> str:
    if not isinstance(translation, str) or not translation.strip():
        raise CandidateValidationError("contract translation must be non-empty text")
    contract_id = (
        str(item.contract_ref.contract_id)
        if item.contract_ref is not None
        else None
    )
    selector = item.metadata.get("selector")
    if not isinstance(contract_id, str) or not contract_id:
        raise CandidateValidationError("contract identity is unavailable")
    if not isinstance(selector, str) or not selector:
        raise CandidateValidationError("contract selector is unavailable")
    expected_generation = _canonical_logical_hash(
        item.basis_id,
        field="expected_generation",
    )
    if _BARE_SHA256_RE.fullmatch(item.source_hash) is None:
        raise CandidateValidationError("source_hash must be a SHA-256 digest")
    source_hash = "sha256:" + item.source_hash
    language_hashes = item.metadata.get("language_hashes")
    if not isinstance(language_hashes, Mapping):
        raise CandidateValidationError("contract language hashes are unavailable")
    if language_hashes.get(item.source_lang) != source_hash:
        raise CandidateValidationError("contract source hash does not match its snapshot")
    previous_target_hash = language_hashes.get(target)
    if previous_target_hash is not None:
        _canonical_logical_hash(
            previous_target_hash,
            field="previous_target_hash",
        )
    candidate_hash = "sha256:" + sha256_text(translation)
    return json.dumps({
        "schema": "metnos.localization-candidate/2",
        "resource_id": item.resource_id,
        "layer": item.layer,
        "contract_id": contract_id,
        "selector": selector,
        "expected_generation": expected_generation,
        "source_lang": item.source_lang,
        "target_lang": target,
        "source_hash": source_hash,
        "previous_target_hash": previous_target_hash,
        "candidate_hash": candidate_hash,
        "translation": translation,
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _artifact_payload(item: InventoryItem, target: str, translation: Any) -> str:
    if item.layer == "contract" and item.basis_id is not None:
        return _contract_artifact_payload_v2(item, target, translation)
    return json.dumps({
        "schema": "metnos.localization-candidate/1",
        "resource_id": item.resource_id,
        "layer": item.layer,
        "source_lang": item.source_lang,
        "target_lang": target,
        "source_hash": item.source_hash,
        "translation": translation,
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _read_json_object(path: Path) -> dict[str, Any]:
    def no_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise CandidateValidationError(f"candidate contains duplicate key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=no_duplicates,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateValidationError(f"candidate artifact is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise CandidateValidationError("candidate artifact must be a JSON object")
    return payload


def _read_contract_artifact_v2(
    record: ResourceRecord,
    authoritative_item: InventoryItem,
    *,
    require_current_basis: bool = True,
) -> Mapping[str, Any]:
    if record.layer != "contract" or record.basis_id is None:
        raise CandidateValidationError("versioned contract candidate is unavailable")
    if not record.artifact_path:
        raise RegistryError(f"candidate artifact unavailable: {record.resource_id}")
    if require_current_basis:
        _require_versioned_contract_item(record, authoritative_item)
    else:
        _require_versioned_contract_identity(record, authoritative_item)
    payload = _read_json_object(Path(record.artifact_path))
    if set(payload) != _CONTRACT_ARTIFACT_V2_FIELDS:
        raise CandidateValidationError("versioned candidate schema fields do not match")
    contract_id = (
        str(authoritative_item.contract_ref.contract_id)
        if authoritative_item.contract_ref is not None
        else None
    )
    selector = authoritative_item.metadata.get("selector")
    language_hashes = (
        authoritative_item.metadata.get("language_hashes")
        if require_current_basis
        else record.metadata.get("language_hashes")
    )
    if (
        not isinstance(contract_id, str)
        or not contract_id
        or not isinstance(selector, str)
        or not selector
        or not isinstance(language_hashes, Mapping)
    ):
        raise CandidateValidationError("versioned contract metadata is incomplete")
    expected_generation = _canonical_logical_hash(
        authoritative_item.basis_id if require_current_basis else record.basis_id,
        field="expected_generation",
    )
    source_digest = (
        authoritative_item.source_hash
        if require_current_basis
        else record.source_hash
    )
    source_language = (
        authoritative_item.source_lang
        if require_current_basis
        else record.source_lang
    )
    if _BARE_SHA256_RE.fullmatch(source_digest) is None:
        raise CandidateValidationError("registry source hash is invalid")
    source_hash = "sha256:" + source_digest
    if language_hashes.get(source_language) != source_hash:
        raise CandidateValidationError("registry source hash does not match its snapshot")
    previous_target_hash = language_hashes.get(record.target_lang)
    if previous_target_hash is not None:
        _canonical_logical_hash(
            previous_target_hash,
            field="previous_target_hash",
        )
    expected_identity = {
        "schema": "metnos.localization-candidate/2",
        "resource_id": authoritative_item.resource_id,
        "layer": "contract",
        "contract_id": contract_id,
        "selector": selector,
        "expected_generation": expected_generation,
        "source_lang": source_language,
        "target_lang": record.target_lang,
        "source_hash": source_hash,
        "previous_target_hash": previous_target_hash,
    }
    if any(payload.get(key) != value for key, value in expected_identity.items()):
        raise CandidateValidationError("versioned candidate identity does not match the registry")
    translation = payload.get("translation")
    if not isinstance(translation, str) or not translation.strip():
        raise CandidateValidationError("contract translation must be non-empty text")
    candidate_hash = _canonical_logical_hash(
        payload.get("candidate_hash"),
        field="candidate_hash",
    )
    if candidate_hash != "sha256:" + sha256_text(translation):
        raise CandidateValidationError("candidate text hash does not match")
    encoded = json.dumps(
        translation,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if sha256_text(encoded) != record.translation_hash:
        raise CandidateValidationError("candidate hash does not match the registry")
    return payload


def _read_artifact(
    record: ResourceRecord,
    *,
    authoritative_item: InventoryItem | None = None,
) -> Any:
    if not record.artifact_path:
        raise RegistryError(f"candidate artifact unavailable: {record.resource_id}")
    path = Path(record.artifact_path)
    if record.layer == "prompt":
        text = path.read_text(encoding="utf-8")
        if sha256_text(text) != record.translation_hash:
            raise CandidateValidationError("prompt candidate hash does not match the registry")
        return text
    if record.layer == "contract" and record.basis_id is not None:
        if authoritative_item is None:
            raise CandidateValidationError(
                "verified contract inventory item is required"
            )
        return _read_contract_artifact_v2(record, authoritative_item)["translation"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "metnos.localization-candidate/1"
        or payload.get("resource_id") != record.resource_id
        or payload.get("source_hash") != record.source_hash
    ):
        raise CandidateValidationError("candidate identity does not match the registry")
    translation = payload.get("translation")
    encoded = json.dumps(
        translation, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    if sha256_text(encoded) != record.translation_hash:
        raise CandidateValidationError("candidate hash does not match the registry")
    return translation


def _update_state(path: Path, selector: str, target: str, record: ResourceRecord) -> None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    if path.name == ".lang_state.json":
        entry = state.setdefault(selector, {})
    else:
        entry = state.setdefault(selector, {}).setdefault(target, {})
    entry.update({
        "status": "admitted", "source_lang": record.source_lang,
        "source_hash": "sha256:" + record.source_hash,
        "version_hash": "sha256:" + str(record.translation_hash or ""),
    })
    _atomic_text(path, json.dumps(
        state, ensure_ascii=False, indent=2, sort_keys=True,
    ) + "\n", mode=0o644)


def _tokens(pattern: re.Pattern[str], value: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in pattern.finditer(value):
        found.append(match.group(0) if match.lastindex is None else match.group(1))
    return tuple(found)


def _jinja_tokens(value: str) -> tuple[str, ...]:
    """Return Jinja tokens with insignificant placeholder padding removed.

    Only whitespace immediately inside ``{{`` and ``}}`` is insignificant.
    Whitespace inside the expression, control-block syntax, order, and
    multiplicity remain exact invariants.
    """
    return tuple(
        "{{" + token[2:-2].strip() + "}}"
        if token.startswith("{{") and token.endswith("}}")
        else token
        for token in _tokens(_JINJA_RE, value)
    )


def _validate_common(source: str, translated: str) -> None:
    if not isinstance(translated, str) or not translated.strip():
        raise CandidateValidationError("translation is empty")
    if _jinja_tokens(source) != _jinja_tokens(translated):
        raise CandidateValidationError("jinja invariants changed")
    for name, pattern in (("format", _FORMAT_RE), ("code", _CODE_RE)):
        if _tokens(pattern, source) != _tokens(pattern, translated):
            raise CandidateValidationError(f"{name} invariants changed")
    if _SENTINEL_RE.search(translated):
        raise CandidateValidationError("translation contains an unresolved sentinel")
    ratio = len(translated) / max(1, len(source))
    if ratio < 0.35 or ratio > 3.5:
        raise CandidateValidationError(f"length ratio out of bounds: {ratio:.3f}")


def _set_prompt_lang(text: str, target: str) -> str:
    return re.sub(
        r"^(\s*lang\s*:\s*)[^\s#]+", lambda match: match.group(1) + target,
        text, count=1, flags=re.MULTILINE,
    )


def _validate_jinja(source: str, translated: str) -> None:
    _validate_common(source, translated)
    try:
        import minijinja
        env = minijinja.Environment()
        env.undeclared_variables_in_str(translated)
    except Exception as exc:
        raise CandidateValidationError(f"MiniJinja parse failed: {exc}") from exc


def _translate_yaml_node(
    node: Any,
    *,
    source_lang: str,
    target_lang: str,
    context: str,
    translator: Translator,
    field_name: str = "",
) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "lang" and isinstance(value, str):
                out[key] = target_lang
            else:
                out[key] = _translate_yaml_node(
                    value, source_lang=source_lang, target_lang=target_lang,
                    context=f"{context}.{key}", translator=translator,
                    field_name=str(key),
                )
        return out
    if isinstance(node, list):
        return [
            _translate_yaml_node(
                value, source_lang=source_lang, target_lang=target_lang,
                context=f"{context}[{index}]", translator=translator,
                field_name=field_name,
            )
            for index, value in enumerate(node)
        ]
    if isinstance(node, str) and field_name in _PROMPT_PROSE_FIELDS:
        translated = translator(node, source_lang, target_lang, context)
        _validate_common(node, translated)
        return translated
    return node


def _yaml_shape(node: Any, *, field_name: str = "") -> Any:
    if isinstance(node, dict):
        return {
            key: (
                "<lang>" if key == "lang" and isinstance(value, str)
                else _yaml_shape(value, field_name=str(key))
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_yaml_shape(value, field_name=field_name) for value in node]
    if isinstance(node, str) and field_name in _PROMPT_PROSE_FIELDS:
        return "<prose>"
    return node


def _translate_yaml(item: InventoryItem, target: str, translator: Translator) -> str:
    import yaml

    source_obj = yaml.safe_load(item.source_text)
    translated_obj = _translate_yaml_node(
        source_obj, source_lang=item.source_lang, target_lang=target,
        context=item.resource_id, translator=translator,
    )
    if _yaml_shape(source_obj) != _yaml_shape(translated_obj):
        raise CandidateValidationError("YAML structure or invariant scalar changed")
    return yaml.safe_dump(translated_obj, allow_unicode=True, sort_keys=False)


def _mask_html(source: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}
    counter = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal counter
        token = f"__METNOS_HTML_{counter:08d}__"
        counter += 1
        mapping[token] = match.group(0)
        return token

    protected = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", replace, source, flags=re.I | re.S)
    return _HTML_TAG_RE.sub(replace, protected), mapping


def _translate_html(item: InventoryItem, target: str, translator: Translator) -> str:
    masked, mapping = _mask_html(item.source_text)
    translated = translator(masked, item.source_lang, target, item.resource_id)
    for token, original in mapping.items():
        translated = translated.replace(token, original)
    _validate_common(item.source_text, translated)
    if _HTML_TAG_RE.findall(item.source_text) != _HTML_TAG_RE.findall(translated):
        raise CandidateValidationError("HTML markup changed")
    return _localize_html_metadata(
        translated, source_lang=item.source_lang, target_lang=target,
    )


def _localize_html_metadata(text: str, *, source_lang: str, target_lang: str) -> str:
    """Project locale metadata without translating arbitrary HTML markup."""
    localized = re.sub(
        r'(<html\b[^>]*\blang\s*=\s*["\'])[^"\']+(["\'])',
        lambda match: match.group(1) + target_lang + match.group(2),
        text, count=1, flags=re.I,
    )
    localized = re.sub(
        r'(<link\b[^>]*\brel\s*=\s*["\']canonical["\'][^>]*\bhref\s*=\s*["\'][^"\']*)'
        + re.escape(f"/{source_lang}/"),
        lambda match: match.group(1) + f"/{target_lang}/",
        localized, count=1, flags=re.I,
    )
    canonical = re.search(
        r'<link\b[^>]*\brel\s*=\s*["\']canonical["\'][^>]*\bhref\s*=\s*["\']([^"\']+)',
        localized, flags=re.I,
    )
    if canonical and not re.search(
        r'<link\b[^>]*\brel\s*=\s*["\']alternate["\'][^>]*\bhreflang\s*=\s*["\']'
        + re.escape(target_lang) + r'["\']',
        localized, flags=re.I,
    ):
        link = (
            f'  <link rel="alternate" hreflang="{target_lang}" '
            f'href="{canonical.group(1)}">\n'
        )
        localized = re.sub(r"</head>", link + "</head>", localized, count=1, flags=re.I)
    return localized


def _translate_input(item: InventoryItem, target: str, translator: Translator) -> Any:
    source = json.loads(item.source_text)
    kind = str(item.metadata.get("kind") or "")
    if kind == "regex":
        raise CandidateValidationError("regular expressions require explicit review")

    def translate_forms(node: Any, context: str) -> Any:
        if isinstance(node, list):
            out = []
            seen: set[str] = set()
            for index, value in enumerate(node):
                if not isinstance(value, str):
                    raise CandidateValidationError("lexicon form must be a string")
                translated = translator(
                    value, item.source_lang, target, f"{context}[{index}]",
                ).strip()
                _validate_common(value, translated)
                folded = translated.casefold()
                if translated and folded not in seen:
                    out.append(translated)
                    seen.add(folded)
            if not out:
                raise CandidateValidationError("lexicon form list is empty")
            return out
        if isinstance(node, dict):
            # Mapping keys are canonical concepts and therefore invariant.
            return {key: translate_forms(value, f"{context}.{key}") for key, value in node.items()}
        raise CandidateValidationError("unsupported lexicon payload")

    translated = translate_forms(source, item.resource_id)
    if isinstance(source, dict) and tuple(source) != tuple(translated):
        raise CandidateValidationError("canonical mapping keys changed")
    return translated


def _contract_candidate_warnings(
    item: InventoryItem,
    *,
    selector: str,
    target_language: str,
    candidate_text: str,
) -> tuple["Finding", ...]:
    """Lint one translated surface in its authenticated contract context.

    Translation happens one resource at a time, while the canonical local
    rules operate on a complete manifest.  Project the candidate into the
    verified snapshot bytes and keep only warnings owned by that resource.
    Other not-yet-translated surfaces may legitimately be missing the target
    language and therefore must not leak diagnostics into this item report.
    """
    snapshot = item.contract_snapshot
    if snapshot is None:
        # Legacy authoring has no authenticated snapshot.  Its complete lint
        # remains the responsibility of the authoring audit; productive M3
        # candidates always carry a verified revision.
        if item.basis_id is not None:
            raise CandidateValidationError(
                "verified contract snapshot is unavailable",
            )
        return ()
    try:
        manifest = tomllib.loads(snapshot.manifest_bytes.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CandidateValidationError(
            "verified contract snapshot is unavailable",
        ) from exc
    table = manifest_language_selectors(manifest).get(selector)
    if not isinstance(table, dict):
        raise CandidateValidationError(
            f"contract selector is unavailable: {selector}",
        )
    table[target_language] = candidate_text

    from manifest_lint import lint_manifest

    return tuple(
        finding
        for finding in lint_manifest(manifest, language=target_language)
        if finding.severity == "warn" and finding.resource == selector
    )


def _translate_item(
    item: InventoryItem,
    target: str,
    translator: Translator,
    *,
    finding_sink: list["Finding"] | None = None,
) -> Any:
    """Translate and validate one inventory item.

    ``finding_sink`` is an optional structured diagnostic sink.  It preserves
    the exact objects returned by the canonical manifest linter so callers
    can report non-blocking warnings without parsing messages or
    reimplementing any rule.
    """
    if item.layer == "prompt":
        if item.metadata.get("format") == "yaml":
            return _translate_yaml(item, target, translator)
        translated = translator(item.source_text, item.source_lang, target, item.resource_id)
        translated = _set_prompt_lang(translated, target)
        source_for_validation = _set_prompt_lang(item.source_text, target)
        _validate_jinja(source_for_validation, translated)
        return translated
    if item.layer == "contract":
        translated = translator(item.source_text, item.source_lang, target, item.resource_id)
        _validate_common(item.source_text, translated)
        selector = item.metadata.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            raise CandidateValidationError("contract selector is unavailable")
        from manifest_lint import lint_contract_translation
        contract_findings = lint_contract_translation(
            item.source_text,
            translated,
            resource=selector,
            source_language=item.source_lang,
            target_language=target,
        )
        if finding_sink is not None:
            finding_sink.extend(contract_findings)
        errors = tuple(
            finding for finding in contract_findings
            if finding.severity == "error"
        )
        if errors:
            checks = ", ".join(sorted({finding.check for finding in errors}))
            raise CandidateValidationError(
                f"contract translation invariants changed: {checks}",
                findings=errors,
            )
        candidate_text = translated.strip()
        if finding_sink is not None:
            finding_sink.extend(_contract_candidate_warnings(
                item,
                selector=selector,
                target_language=target,
                candidate_text=candidate_text,
            ))
        return candidate_text
    if item.layer == "message":
        translated = translator(item.source_text, item.source_lang, target, item.resource_id)
        _validate_common(item.source_text, translated)
        return translated.strip()
    if item.layer == "input":
        return _translate_input(item, target, translator)
    if item.layer == "knowledge":
        return _translate_html(item, target, translator)
    raise CandidateValidationError(f"derived layer {item.layer!r} has no translation payload")


def _require_versioned_contract_identity(
    record: ResourceRecord,
    item: InventoryItem | None,
) -> None:
    """Resolve contract identity from fresh inventory, never registry paths."""
    if (
        record.layer != "contract"
        or item is None
        or item.layer != "contract"
        or item.contract_ref is None
        or item.resource_id != record.resource_id
        or record.metadata.get("contract_id") != str(item.contract_ref.contract_id)
        or record.metadata.get("selector") != item.metadata.get("selector")
    ):
        raise CandidateValidationError(
            f"verified contract inventory item unavailable: {record.resource_id}"
        )


def _require_versioned_contract_item(
    record: ResourceRecord,
    item: InventoryItem | None,
) -> None:
    """Bind a workflow row to the same verified generation inventory item."""
    if record.layer != "contract":
        return
    if record.basis_id is None:
        if item is not None and item.basis_id is not None:
            raise CandidateValidationError(
                f"versioned contract has not been materialized: {record.resource_id}"
            )
        return
    _require_versioned_contract_identity(record, item)
    assert item is not None
    if (
        item.basis_id != record.basis_id
        or item.source_hash != record.source_hash
        or item.source_lang != record.source_lang
        or item.metadata.get("language_hashes") != record.metadata.get("language_hashes")
    ):
        raise CandidateValidationError(
            f"verified contract basis changed: {record.resource_id}"
        )


def _require_versioned_contract_lease(
    lease: TranslationLease,
    record: ResourceRecord,
    item: InventoryItem,
) -> None:
    """Close the inventory-to-claim race for generation-bound work."""
    if item.layer != "contract" or item.basis_id is None:
        return
    if (
        lease.resource_id != item.resource_id
        or lease.layer != item.layer
        or lease.source_lang != item.source_lang
        or lease.target_lang != record.target_lang
        or lease.source_hash != item.source_hash
        or lease.basis_id != item.basis_id
        or lease.metadata.get("contract_id")
        != str(item.contract_ref.contract_id if item.contract_ref else "")
        or lease.metadata.get("selector") != item.metadata.get("selector")
        or lease.metadata.get("language_hashes")
        != item.metadata.get("language_hashes")
    ):
        raise CandidateValidationError(
            f"verified contract lease changed: {record.resource_id}"
        )


def translate_pending(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    paths: LocalizationPaths | None = None,
    source_lang: str = _C.BOOTSTRAP_LANGUAGE,
    translator: Translator | None = None,
    limit: int = 0,
    contract_snapshot_provider: ContractSnapshotProvider | None = None,
) -> TranslationReport:
    paths = paths or LocalizationPaths()
    target = normalize_language(target_lang)
    records = registry.resources(target)
    if (
        contract_snapshot_provider is None
        and any(record.layer == "contract" and record.basis_id is not None for record in records)
    ):
        raise CandidateValidationError(
            "verified contract snapshot provider is required for versioned candidates"
        )
    items = {
        item.resource_id: item
        for item in inventory(
            paths,
            source_lang=source_lang,
            contract_snapshot_provider=contract_snapshot_provider,
        )
    }
    for record in records:
        _require_versioned_contract_item(record, items.get(record.resource_id))
    provider = translator or _default_translator
    translated_count = failed = skipped = 0
    errors: dict[str, str] = {}
    warnings: dict[str, tuple["Finding", ...]] = {}
    processed = 0
    for record in records:
        if limit > 0 and processed >= limit:
            break
        item = items.get(record.resource_id)
        if item is None or record.status in {"translated", "admitted", "manual_review"}:
            skipped += 1
            continue
        if record.layer in {"device", "tutor"}:
            skipped += 1
            continue
        lease = registry.claim(record.resource_id, target)
        if lease is None:
            skipped += 1
            continue
        processed += 1
        item_findings: list["Finding"] = []
        try:
            _require_versioned_contract_lease(lease, record, item)
            translation = _translate_item(
                item,
                target,
                provider,
                finding_sink=item_findings,
            )
            candidate = _candidate_path(
                item,
                target,
                registry,
                paths,
                lease_token=lease.lease_token,
            )
            if item.layer == "prompt":
                _atomic_text(candidate, str(translation), mode=0o644)
                digest_source = str(translation)
            else:
                payload = _artifact_payload(item, target, translation)
                _atomic_text(candidate, payload)
                digest_source = json.dumps(translation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            quality = "candidate" if item.layer == "knowledge" else "structural"
            registry.complete(
                item.resource_id, target, sha256_text(digest_source), quality,
                lease_token=lease.lease_token, artifact_path=str(candidate),
            )
            translated_count += 1
        except Exception as exc:  # validation/provider failures are retryable and bounded
            error_class = type(exc).__name__
            try:
                registry.fail(
                    record.resource_id, target, error_class,
                    lease_token=lease.lease_token,
                )
            except Exception:
                pass
            failed += 1
            errors[record.resource_id] = f"{error_class}: {exc}"
        finally:
            item_warnings = tuple(
                finding for finding in item_findings
                if finding.severity == "warn"
            )
            if item_warnings:
                warnings[record.resource_id] = item_warnings
    return TranslationReport(
        target_lang=target, translated=translated_count, failed=failed,
        skipped=skipped, errors=dict(sorted(errors.items())),
        warnings=dict(sorted(warnings.items())),
    )


def review_semantics(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    paths: LocalizationPaths | None = None,
    source_lang: str = _C.BOOTSTRAP_LANGUAGE,
    judge: EquivalenceJudge,
    limit: int = 0,
    contract_snapshot_provider: ContractSnapshotProvider | None = None,
) -> dict[str, bool]:
    """Review every translated prose surface with one equivalence contract."""
    paths = paths or LocalizationPaths()
    target = normalize_language(target_lang)
    records = registry.resources(target)
    if (
        contract_snapshot_provider is None
        and any(record.layer == "contract" and record.basis_id is not None for record in records)
    ):
        raise CandidateValidationError(
            "verified contract snapshot provider is required for versioned candidates"
        )
    items = {
        item.resource_id: item
        for item in inventory(
            paths,
            source_lang=source_lang,
            contract_snapshot_provider=contract_snapshot_provider,
        )
    }
    for record in records:
        _require_versioned_contract_item(record, items.get(record.resource_id))
    results: dict[str, bool] = {}
    prompt_evidence: list[str] = []
    knowledge_evidence: list[str] = []
    contract_evidence: list[str] = []
    input_evidence: list[str] = []
    judged = 0
    for record in records:
        if record.status != "translated" or record.layer not in {
            "prompt", "knowledge", "contract", "message", "input",
        }:
            continue
        item = items.get(record.resource_id)
        if item is None:
            continue
        translated = _read_artifact(record, authoritative_item=item)
        if record.quality == "reviewed":
            accepted = True
        elif limit > 0 and judged >= limit:
            continue
        else:
            accepted = bool(
                judge(item.source_text, str(translated), item.resource_id)
            )
            judged += 1
        results[record.resource_id] = accepted
        if accepted and record.quality != "reviewed":
            if record.layer == "contract" and record.basis_id is not None:
                registry.review_candidate(record, quality="reviewed")
            else:
                registry.review(record.resource_id, target, quality="reviewed")
        if accepted:
            evidence = sha256_text(item.source_hash + str(record.translation_hash))
            if record.layer == "prompt":
                prompt_evidence.append(evidence)
            elif record.layer == "knowledge":
                knowledge_evidence.append(evidence)
            elif record.layer == "input":
                input_evidence.append(evidence)
            else:
                contract_evidence.append(evidence)
    records = registry.resources(target)
    prompt_records = [row for row in records if row.layer == "prompt"]
    prompt_ok = bool(prompt_records) and all(
        row.status in {"translated", "admitted"} and row.quality == "reviewed"
        for row in records if row.layer == "prompt"
    )
    registry.record_check(
        "planner_proposer_equivalence", target,
        "passed" if prompt_ok else "failed",
        evidence_hash=sha256_text("\n".join(sorted(prompt_evidence))),
        details={"resources": len(prompt_records)},
    )
    knowledge_records = [row for row in records if row.layer == "knowledge"]
    knowledge_ok = bool(knowledge_records) and all(
        row.status in {"translated", "admitted"} and row.quality == "reviewed"
        for row in records if row.layer == "knowledge"
    )
    registry.record_check(
        "public_knowledge_review", target,
        "passed" if knowledge_ok else "failed",
        evidence_hash=sha256_text("\n".join(sorted(knowledge_evidence))),
        details={"resources": len(knowledge_records)},
    )
    contract_records = [
        row for row in records
        if row.layer in {"contract", "message"}
    ]
    contract_ok = bool(contract_records) and all(
        row.status in {"translated", "admitted"} and row.quality == "reviewed"
        for row in contract_records
    )
    registry.record_check(
        "contract_message_equivalence", target,
        "passed" if contract_ok else "failed",
        evidence_hash=sha256_text("\n".join(sorted(contract_evidence))),
        details={"resources": len(contract_records)},
    )
    input_records = [
        row for row in records
        if row.layer == "input" and row.status != "manual_review"
    ]
    input_ok = bool(input_records) and all(
        row.status in {"translated", "admitted"} and row.quality == "reviewed"
        for row in input_records
    )
    registry.record_check(
        "input_lexicon_equivalence", target,
        "passed" if input_ok else "failed",
        evidence_hash=sha256_text("\n".join(sorted(input_evidence))),
        details={"resources": len(input_records)},
    )
    return results


def default_equivalence_judge(source: str, target: str, resource_id: str) -> bool:
    """Use the configured fidelity workload as a strict, provider-neutral judge."""
    from llm_helpers import call_llm
    from llm_workloads import tier_for
    payload = json.dumps({
        "resource_id": resource_id, "source": source, "candidate": target,
    }, ensure_ascii=False)
    prompt = (
        "Assess whether candidate preserves every instruction, prohibition, "
        "fact, placeholder, identifier, and deontic strength of source. "
        "Different languages are expected. Reply only as JSON "
        f'{{"equivalent": true|false}}. Input: {payload}'
    )
    raw, _meta = call_llm(
        prompt, "You are a strict localization equivalence verifier.",
        tier=tier_for("translation.i18n"), max_tokens=120,
    )
    try:
        parsed = json.loads(str(raw or "").strip().removeprefix("```json").removesuffix("```").strip())
    except (TypeError, json.JSONDecodeError):
        return False
    return parsed.get("equivalent") is True


def publish_versioned_contract_candidates(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    paths: LocalizationPaths,
    contract_snapshot_provider: ContractSnapshotProvider,
    publisher: VersionedContractPublisher,
    source_lang: str = _C.BOOTSTRAP_LANGUAGE,
) -> VersionedContractPublicationReport:
    """Publish reviewed M3 candidates to an explicitly injected store.

    ``publisher`` is normally a partial application of
    :func:`contract_store.publish_localization`; it owns keys, trust and the
    isolated store root.  Destination identity always comes from the fresh
    manifest inventory carried by ``InventoryItem.contract_ref``.  Registry
    metadata and candidate artifacts are evidence to verify, never path
    authority.  Successful publication remains dormant and therefore does
    not admit or activate any registry row.
    """
    if not callable(contract_snapshot_provider) or not callable(publisher):
        raise ValueError("snapshot provider and publisher must be callable")
    target = normalize_language(target_lang)
    items = tuple(
        item
        for item in inventory(
            paths,
            source_lang=source_lang,
            contract_snapshot_provider=contract_snapshot_provider,
        )
        if item.layer == "contract" and item.basis_id is not None
    )
    records = {
        record.resource_id: record
        for record in registry.resources(target)
        if record.layer == "contract"
    }
    authoritative = {item.resource_id: item for item in items}
    errors: dict[str, str] = {}
    skipped = 0
    grouped: dict[str, tuple[ManifestRef, list[InventoryItem]]] = {}

    for item in items:
        if item.contract_ref is None:
            errors[item.resource_id] = "CandidateValidationError: contract reference is unavailable"
            continue
        key = str(item.contract_ref.contract_id)
        if key not in grouped:
            grouped[key] = (item.contract_ref, [])
        grouped[key][1].append(item)

    for record in records.values():
        if record.basis_id is not None and record.resource_id not in authoritative:
            errors[record.resource_id] = (
                "CandidateValidationError: verified contract inventory item unavailable"
            )

    published_contracts = 0
    published_resources = 0
    from contract_store import LocalizationPatch, PublicationResult

    for _contract_id, (ref, contract_items) in sorted(grouped.items()):
        prepared: list[tuple[ResourceRecord, Mapping[str, Any]]] = []
        observed: list[ResourceRecord] = []
        group_failed = False
        for item in sorted(
            contract_items,
            key=lambda candidate: str(candidate.metadata.get("selector") or ""),
        ):
            record = records.get(item.resource_id)
            if record is None:
                errors[item.resource_id] = (
                    "CandidateValidationError: versioned workflow row is unavailable"
                )
                group_failed = True
                continue
            try:
                _require_versioned_contract_identity(record, item)
                if record.basis_id is None:
                    raise CandidateValidationError(
                        "versioned workflow row has no expected generation"
                    )
                observed.append(record)
                if record.status != "translated":
                    skipped += 1
                    continue
                if record.quality != "reviewed":
                    raise CandidateValidationError("candidate has not passed semantic review")
                payload = _read_contract_artifact_v2(
                    record,
                    item,
                    require_current_basis=False,
                )
                prepared.append((record, payload))
            except Exception as exc:
                errors[item.resource_id] = f"{type(exc).__name__}: {exc}"
                group_failed = True
        if group_failed or not prepared:
            continue

        bases = {record.basis_id for record in observed}
        sources = {record.source_lang for record, _payload in prepared}
        selectors = [str(payload["selector"]) for _record, payload in prepared]
        if len(bases) != 1 or None in bases or len(sources) != 1:
            message = "CandidateValidationError: contract candidate group is inconsistent"
            for record, _payload in prepared:
                errors[record.resource_id] = message
            continue
        if len(selectors) != len(set(selectors)):
            message = "CandidateValidationError: contract candidate selector is duplicated"
            for record, _payload in prepared:
                errors[record.resource_id] = message
            continue

        patches = tuple(
            LocalizationPatch(
                selector=str(payload["selector"]),
                source_hash=str(payload["source_hash"]),
                previous_target_hash=payload["previous_target_hash"],
                candidate_text=str(payload["translation"]),
                candidate_hash=str(payload["candidate_hash"]),
            )
            for _record, payload in prepared
        )
        try:
            result = publisher(
                ref,
                expected_generation_id=next(iter(bases)),
                source_language=next(iter(sources)),
                target_language=target,
                patches=patches,
            )
            if (
                not isinstance(result, PublicationResult)
                or result.contract_id != ref.contract_id
                or result.operation != "publish_localization"
                or _LOGICAL_SHA256_RE.fullmatch(result.current_generation_id) is None
            ):
                raise CandidateValidationError(
                    "publisher returned an invalid publication result"
                )
            published_contracts += 1
            published_resources += len(prepared)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            for record, _payload in prepared:
                errors[record.resource_id] = message

    return VersionedContractPublicationReport(
        target_lang=target,
        published_contracts=published_contracts,
        published_resources=published_resources,
        skipped=skipped,
        errors=dict(sorted(errors.items())),
    )


def _promote_message(db: Path, record: ResourceRecord, target: str, translation: str) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """UPDATE i18n SET text=?,needs_translation=0,version_hash=?,
               source_text_hash=?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE key=? AND lang=?""",
            (
                translation, "sha256:" + sha256_text(translation),
                "sha256:" + record.source_hash, record.metadata["key"], target,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _promote_input(db: Path, record: ResourceRecord, target: str, translation: Any) -> None:
    encoded = json.dumps(translation, ensure_ascii=False, sort_keys=True)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """UPDATE detection_lexicon SET payload=?,needs_translation=0,
               version_hash=?,source_text_hash=?,
               updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE concept=? AND lang=?""",
            (
                encoded, "sha256:" + sha256_text(encoded),
                "sha256:" + record.source_hash, record.metadata["concept"], target,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def promote_candidates(
    target_lang: str,
    *,
    registry: LocalizationRegistry,
    paths: LocalizationPaths | None = None,
    versioned_contracts_published: bool = False,
) -> PromotionReport:
    """Promote validated artifacts; instance configuration is untouched."""
    paths = paths or LocalizationPaths()
    target = normalize_language(target_lang)
    records = list(registry.resources(target))
    has_versioned_contracts = any(
        record.layer == "contract" and record.basis_id is not None
        for record in records
    )
    if has_versioned_contracts and not versioned_contracts_published:
        raise CandidateValidationError(
            "versioned contract candidates require the explicit M3 publisher"
        )
    legacy_contract_records = [
        record for record in records
        if record.layer == "contract"
        and record.status == "translated"
        and record.basis_id is None
    ]
    if legacy_contract_records:
        raise CandidateValidationError(
            "legacy contract publication is retired; activate the verified "
            "contract store and use the versioned publisher"
        )
    admitted = 0
    errors: dict[str, str] = {}
    skipped = 0
    for record in records:
        if record.layer == "contract":
            continue
        if record.status != "translated":
            skipped += 1
            continue
        try:
            translation = _read_artifact(record)
            if record.layer == "prompt":
                candidate = Path(str(record.artifact_path))
                relative = str(record.metadata["relative_path"])
                _atomic_text(paths.prompts / target / relative, str(translation), mode=0o644)
                _update_state(
                    paths.prompts / target / ".lang_state.json",
                    relative, target, record,
                )
            elif record.layer == "message":
                _promote_message(paths.messages_db, record, target, str(translation))
            elif record.layer == "input":
                _promote_input(paths.detection_db, record, target, translation)
            elif record.layer == "knowledge":
                if record.quality != "reviewed":
                    raise CandidateValidationError("public knowledge has not passed review")
                relative = str(record.metadata["relative_path"])
                _atomic_text(paths.docs / target / relative, str(translation), mode=0o644)
            else:
                skipped += 1
                continue
            registry.admit(record.resource_id, target)
            admitted += 1
        except Exception as exc:
            errors[record.resource_id] = f"{type(exc).__name__}: {exc}"
    return PromotionReport(
        target_lang=target, admitted=admitted, skipped=skipped,
        errors=dict(sorted(errors.items())),
    )
