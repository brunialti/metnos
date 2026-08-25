"""Immutable, revision-addressed executor contract publication.

A live revision contains only the signed manifest, its signature and canonical
language provenance.  A retired contract instead points at a small signed
tombstone whose immutable predecessor remains verifiable without reopening
deleted code.  Code remains at the inventoried authoring source while live;
no database, journal or code copy is introduced.

On local Linux filesystems file and directory barriers protect the pointer
across a process crash and, subject to the filesystem, sudden power loss.  On
Windows/NTFS ``os.replace`` protects readers from process crashes, but Python
does not expose an equivalent directory durability barrier: v1 does not claim
that the newest pointer survives sudden power loss on Windows.
"""
from __future__ import annotations

import contextlib
import copy
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import tomllib
import tomlkit
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Callable, Iterable, Iterator, Mapping, TypeAlias

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

import config as _C
from contract_bootstrap import (
    ACTIVE_BYTES,
    ACTIVE_RELATIVE,
    STORE_RELATIVE,
    BootstrapStateError,
    ProductionStoreMode,
    classify_production_store,
)
from i18n_materializer import (
    LanguageStateError,
    decode_language_state,
    encode_language_state,
    manifest_language_selectors,
)
from i18n_registry import normalize_language
from manifest_inventory import ContractId, ManifestOrigin, ManifestRef, ManifestStatus
from sign import (
    ManifestSignatureError,
    TrustedPublic,
    sign_manifest_bytes,
    verify_manifest_bytes,
)


SHADOW_RELATIVE = Path("contract-publications-shadow")
BINDING_FILE = "binding.json"
BINDING_VERSION = 1
GENERATION_FILES = (
    "manifest.toml",
    "manifest.toml.sig",
    "manifest.lang_state.json",
)
RETIREMENT_FILES = (
    "retirement.json",
    "retirement.json.sig",
)
RETIREMENT_SCHEMA = "metnos.contract-retirement"
RETIREMENT_VERSION = 1
RETIREMENT_SIGNATURE_DOMAIN = b"metnos.contract-retirement/v1\x00"
DEFAULT_LOCK_TIMEOUT = 5.0
DEFAULT_REPLACE_TIMEOUT = 2.0
WINDOWS_POWER_LOSS_LIMIT = (
    "NTFS process-crash atomicity is supported; sudden-power-loss durability "
    "of the newest directory entry is not claimed"
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PHYSICAL_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_DIRECT_STAGING_RE = re.compile(
    r"\.(binding\.json|current)\.(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.tmp\Z"
)
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_CATALOG_LOCK_LOCAL = threading.local()


def _reset_process_lock_state_after_fork() -> None:
    """Discard thread-owned lock bookkeeping inherited by a forked child.

    A child must contend on the real file lock.  Reusing the parent's
    thread-local reentrancy depth would bypass it, while reusing a
    ``threading.Lock`` held by a vanished parent thread could block forever.
    """
    global _PROCESS_LOCKS_GUARD, _PROCESS_LOCKS, _CATALOG_LOCK_LOCAL
    _PROCESS_LOCKS_GUARD = threading.Lock()
    _PROCESS_LOCKS = {}
    _CATALOG_LOCK_LOCAL = threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_process_lock_state_after_fork)


class ContractStoreError(RuntimeError):
    """Stable fail-closed error raised at the contract publication boundary."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class VerifiedManifest:
    contract_id: ContractId
    generation_id: str | None
    source_manifest_dir: Path
    allowed_code_roots: tuple[Path, ...]
    manifest_bytes: bytes
    manifest_hash: str
    parsed: Mapping[str, object]
    signature_bytes: bytes
    signature_hash: str
    language_state_bytes: bytes
    language_state: Mapping[str, object]
    signed_by: str
    declared_code_digest: str
    verified_code_digest: str


@dataclass(frozen=True, slots=True)
class ContractRetirement:
    """Authenticated immutable evidence that a contract is not live."""

    contract_id: ContractId
    retirement_id: str
    previous_generation_id: str
    actor: str
    reason: str
    payload_bytes: bytes
    signature_bytes: bytes
    signature_hash: str
    signed_by: str


# Productive callers own these two system-specific boundaries.  The store
# stays independent from service managers and from the RM-0005 database.
QuiescenceProof = Callable[[], bool]
ContractRevision: TypeAlias = VerifiedManifest | ContractRetirement
RegistryReconciler = Callable[[ContractRevision], None]
BirthReceiptIssuer = Callable[[str, Mapping[str, str], str, str], bytes]
BirthReceiptVerifier = Callable[[bytes], object]


@dataclass(frozen=True, slots=True)
class PublicationResult:
    contract_id: ContractId
    previous_generation_id: str | None
    current_generation_id: str
    operation: str
    repeated: bool


@dataclass(frozen=True, slots=True)
class BirthCommitAuthorization:
    """Sealed Birth authority used at the exact RM-0007 precommit point.

    The store deliberately does not own Birth's private signing key.  The
    issuer receives the generation selected by RM-0007 and hashes of the
    canonical payloads; the verifier must authenticate the returned wire
    receipt.  All identity bindings are checked again by this boundary.
    """

    candidate_id: str
    semantic_core_id: str
    admission_context_id: str
    predecessor_id: str | None
    issuer: BirthReceiptIssuer
    verifier: BirthReceiptVerifier
    predecessor_snapshot_id: str | None = None
    revision_facts_id: str | None = None
    context_epoch: str | None = None
    context_epoch_resolver: Callable[[], str] | None = None


@dataclass(frozen=True, slots=True)
class LocalizationPatch:
    """One reviewed prose replacement bound to an immutable generation."""

    selector: str
    source_hash: str
    previous_target_hash: str | None
    candidate_text: str
    candidate_hash: str


@dataclass(frozen=True, slots=True)
class TechnicalDraft:
    """Immutable authoring observation prepared before the writer lock.

    Code is deliberately represented only by its observed digest.  The
    publisher reads and hashes the allowed source files again under its lock;
    no copied code can become a second deployment authority.
    """

    manifest_bytes: bytes
    language_state_bytes: bytes
    authoring_manifest_hash: str
    authoring_signature_hash: str | None
    authoring_language_state_hash: str
    authoring_code_digest: str


@dataclass(frozen=True, slots=True)
class SurfaceRemoval:
    """Explicit evidence for an intentional schema-surface removal."""

    selectors: tuple[str, ...]
    actor: str
    reason: str


@dataclass(frozen=True, slots=True)
class ContractBinding:
    contract_id: ContractId
    storage_key: str


@dataclass(frozen=True, slots=True)
class StoreDiagnostic:
    code: str
    contract_id: ContractId | None
    path: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class _AuthenticatedPayloads:
    parsed: Mapping[str, Any]
    language_state: Mapping[str, object]
    signed_by: str
    declared_code_digest: str
    generation_id: str


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _auditable_event(fields: Mapping[str, object]) -> dict[str, object]:
    """Attach a stable ID so a precommit audit retry can be deduplicated.

    Audit sinks at this boundary must durably accept an event before returning
    and treat a repeated ``event_id`` as success without appending a duplicate.
    This is required because a process may stop after the sink returns but
    before ``current`` is replaced; the safe retry emits the same event.
    """
    if "event_id" in fields:
        raise ContractStoreError("audit_event_invalid", "event_id is reserved")
    try:
        canonical = json.dumps(
            dict(fields),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractStoreError("audit_event_invalid", str(exc)) from exc
    event = dict(fields)
    event["event_id"] = _sha256(b"metnos.contract-audit/v1\x00" + canonical)
    return event


def _trusted_public_tuple(
    trusted_publics: Iterable[TrustedPublic],
) -> tuple[TrustedPublic, ...]:
    try:
        trusted = tuple(trusted_publics)
    except TypeError as exc:
        raise ContractStoreError("trusted_keys_invalid", str(exc)) from exc
    if not trusted:
        raise ContractStoreError("trusted_keys_missing")
    names: set[str] = set()
    fingerprints: set[bytes] = set()
    for item in trusted:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ContractStoreError("trusted_keys_invalid", repr(item))
        name, public_key = item
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ContractStoreError("trusted_keys_invalid", str(name))
        if not isinstance(public_key, Ed25519PublicKey):
            raise ContractStoreError("trusted_keys_invalid", name)
        fingerprint = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if fingerprint in fingerprints:
            raise ContractStoreError("trusted_keys_invalid", f"duplicate key: {name}")
        names.add(name)
        fingerprints.add(fingerprint)
    return trusted


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _store_root(store_root: Path | str | None) -> Path:
    return (
        Path(store_root)
        if store_root is not None
        else _C.PATH_USER_STATE / STORE_RELATIVE
    )


def _production_paths() -> tuple[Path, Path, Path]:
    user_state = Path(os.path.abspath(_C.PATH_USER_STATE))
    container = user_state / STORE_RELATIVE.parent
    return container, container / STORE_RELATIVE.name, user_state / ACTIVE_RELATIVE


def _m2_shadow_root(store_root: Path | str | None) -> Path:
    """Require an explicitly injected root outside the production boundary."""
    if store_root is None:
        raise ContractStoreError("publication_not_active", "shadow root required")
    candidate = Path(os.path.abspath(store_root))
    production_container = Path(os.path.abspath(
        _C.PATH_USER_STATE / STORE_RELATIVE.parent,
    ))
    active_marker = Path(os.path.abspath(_C.PATH_USER_STATE / ACTIVE_RELATIVE))

    def overlaps(first: Path, second: Path) -> bool:
        return (
            first == second
            or first in second.parents
            or second in first.parents
        )

    if overlaps(candidate, production_container) or overlaps(candidate, active_marker):
        raise ContractStoreError("publication_not_active", str(candidate))
    return candidate


def _is_link_like(path: Path) -> bool:
    try:
        status = path.lstat()
        reparse = bool(
            getattr(status, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        return reparse or path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _require_no_link_components(path: Path, *, code: str) -> None:
    absolute = Path(os.path.abspath(path))
    for component in reversed((absolute, *absolute.parents)):
        if _is_link_like(component):
            raise ContractStoreError(code, str(component))


def _require_plain_directory(path: Path, *, code: str) -> None:
    if _is_link_like(path) or not path.is_dir():
        raise ContractStoreError(code, str(path))


def production_store_mode() -> ProductionStoreMode:
    """Return the fail-closed marker/root state without creating anything."""
    _container, root, marker = _production_paths()
    try:
        return classify_production_store(
            version_root=root,
            active_marker=marker,
            active_bytes=ACTIVE_BYTES,
        ).mode
    except BootstrapStateError as exc:
        raise ContractStoreError(exc.code, exc.detail) from exc


def _publication_root(store_root: Path | str | None) -> tuple[Path, bool]:
    """Resolve an isolated fixture or an activated productive store."""
    if store_root is not None:
        return _m2_shadow_root(store_root), False
    mode = production_store_mode()
    if mode is not ProductionStoreMode.ACTIVE:
        raise ContractStoreError("publication_not_active", mode.value)
    _container, root, _marker = _production_paths()
    _require_plain_directory(root, code="production_store_invalid")
    _require_no_link_components(root, code="production_store_invalid")
    return root, True


def _ensure_directory_chain(path: Path, *, code: str) -> None:
    """Create and durably link each missing directory without following links."""
    _require_no_link_components(path, code=code)
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        if _is_link_like(cursor):
            raise ContractStoreError(code, str(cursor))
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    if cursor.exists():
        _require_plain_directory(cursor, code=code)
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise ContractStoreError(code, f"{directory}: {exc}") from exc
        _require_plain_directory(directory, code=code)
        _require_no_link_components(directory, code=code)
        _sync_directory(directory.parent)


def _ensure_store_directories(
    contract_id: ContractId,
    *,
    store_root: Path | str | None,
) -> tuple[Path, Path]:
    root = _store_root(store_root)
    _ensure_directory_chain(root, code="store_root_invalid")
    contract_dir = root / contract_storage_key(contract_id)
    if _is_link_like(contract_dir):
        raise ContractStoreError("contract_directory_invalid", str(contract_dir))
    contract_created = not contract_dir.exists()
    contract_dir.mkdir(mode=0o700, exist_ok=True)
    _require_plain_directory(contract_dir, code="contract_directory_invalid")
    _require_no_link_components(contract_dir, code="contract_directory_invalid")
    if contract_created:
        _sync_directory(root)
    generations = contract_dir / "generations"
    if _is_link_like(generations):
        raise ContractStoreError("generations_directory_invalid", str(generations))
    generations_created = not generations.exists()
    generations.mkdir(mode=0o700, exist_ok=True)
    _require_plain_directory(generations, code="generations_directory_invalid")
    _require_no_link_components(generations, code="generations_directory_invalid")
    if generations_created:
        _sync_directory(contract_dir)
    return contract_dir, generations


def _existing_contract_directory(
    contract_id: ContractId,
    *,
    store_root: Path | str | None,
) -> Path:
    root = _store_root(store_root)
    _require_plain_directory(root, code="store_root_missing")
    _require_no_link_components(root, code="store_root_invalid")
    contract_dir = root / contract_storage_key(contract_id)
    _require_plain_directory(contract_dir, code="contract_directory_missing")
    binding = read_binding(contract_dir)
    if binding.contract_id != contract_id:
        raise ContractStoreError("binding_invalid", str(contract_id))
    return contract_dir


def _require_regular_file(path: Path, *, code: str) -> None:
    if _is_link_like(path) or not path.is_file():
        raise ContractStoreError(code, str(path))


def _read_regular_file(path: Path, *, code: str) -> bytes:
    _require_regular_file(path, code=code)
    try:
        with path.open("rb") as handle:
            return handle.read()
    except OSError as exc:
        raise ContractStoreError(code, f"{path}: {exc}") from exc


def read_binding(contract_dir: Path | str) -> ContractBinding:
    """Read and validate the immutable structural locator for a contract."""
    directory = Path(contract_dir)
    _require_plain_directory(directory, code="contract_directory_invalid")
    _require_no_link_components(directory, code="contract_directory_invalid")
    binding_bytes = _read_regular_file(
        directory / BINDING_FILE,
        code="binding_invalid",
    )
    return decode_binding(binding_bytes, storage_key=directory.name)


def _ensure_binding_locked(contract_dir: Path, contract_id: ContractId) -> ContractBinding:
    """Create the immutable binding once, or verify an interrupted retry."""
    desired = encode_binding(contract_id)
    destination = contract_dir / BINDING_FILE
    if _is_link_like(destination):
        raise ContractStoreError("binding_invalid", str(destination))
    if destination.exists():
        binding = read_binding(contract_dir)
        if binding.contract_id != contract_id:
            raise ContractStoreError("binding_invalid", str(contract_id))
        return binding
    current = contract_dir / "current"
    generations = contract_dir / "generations"
    try:
        has_history = (
            current.exists() or _is_link_like(current) or any(generations.iterdir())
        )
    except OSError as exc:
        raise ContractStoreError("binding_invalid", str(exc)) from exc
    if has_history:
        raise ContractStoreError(
            "binding_invalid", "binding missing from an existing publication"
        )
    temporary = destination.with_name(
        f".{BINDING_FILE}.{os.getpid()}.{threading.get_ident()}."
        f"{time.monotonic_ns()}.tmp"
    )
    try:
        _write_new_file(temporary, desired)
        # All cooperating creators hold writer.lock.  The destination is
        # checked again so an interrupted retry is verified, never replaced.
        if destination.exists() or _is_link_like(destination):
            if _is_link_like(destination):
                raise ContractStoreError("binding_invalid", str(destination))
            binding = read_binding(contract_dir)
            if binding.contract_id != contract_id:
                raise ContractStoreError("binding_invalid", str(contract_id))
            return binding
        try:
            _rename_no_replace(temporary, destination)
        except FileExistsError:
            binding = read_binding(contract_dir)
            if binding.contract_id != contract_id:
                raise ContractStoreError("binding_invalid", str(contract_id))
            return binding
        _sync_directory(contract_dir)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return read_binding(contract_dir)


def _json_without_duplicates(data: bytes, *, code: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ContractStoreError(code, f"duplicate key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except UnicodeDecodeError as exc:
        raise ContractStoreError(code, f"UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractStoreError(code, f"JSON: {exc}") from exc


def _binding_json_without_duplicates(data: bytes) -> Any:
    return _json_without_duplicates(data, code="binding_invalid")


def contract_storage_key(contract_id: ContractId) -> str:
    """Return the portable directory key for one structural contract ID."""
    value = hashlib.sha256(contract_id.value.encode("utf-8")).hexdigest()
    if value != contract_id.storage_key:
        raise ContractStoreError("storage_key_invalid", contract_id.value)
    return value


def encode_binding(contract_id: ContractId) -> bytes:
    """Encode the immutable, path-free binding in its sole canonical form."""
    return (
        json.dumps(
            {"contract_id": contract_id.value, "schema_version": BINDING_VERSION},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
    ).encode("utf-8")


def decode_binding(binding_bytes: bytes, *, storage_key: str) -> ContractBinding:
    """Validate a canonical binding and its containing directory name."""
    if not isinstance(storage_key, str) or not _PHYSICAL_ID_RE.fullmatch(storage_key):
        raise ContractStoreError("binding_invalid", f"storage key: {storage_key}")
    parsed = _binding_json_without_duplicates(binding_bytes)
    if not isinstance(parsed, Mapping) or set(parsed) != {
        "contract_id", "schema_version",
    }:
        raise ContractStoreError("binding_invalid", "unexpected schema")
    if (
        type(parsed.get("schema_version")) is not int
        or parsed.get("schema_version") != BINDING_VERSION
    ):
        raise ContractStoreError("binding_invalid", "unsupported schema version")
    raw_contract_id = parsed.get("contract_id")
    if not isinstance(raw_contract_id, str) or ":" not in raw_contract_id:
        raise ContractStoreError("binding_invalid", "invalid contract ID")
    raw_origin, relative_manifest = raw_contract_id.split(":", 1)
    posix_relative = PurePosixPath(relative_manifest)
    windows_relative = PureWindowsPath(relative_manifest)
    if (
        posix_relative.is_absolute()
        or windows_relative.is_absolute()
        or bool(windows_relative.drive)
        or "\\" in relative_manifest
    ):
        raise ContractStoreError("binding_invalid", "absolute or non-portable path")
    try:
        contract_id = ContractId(ManifestOrigin(raw_origin), relative_manifest)
    except (TypeError, ValueError) as exc:
        raise ContractStoreError("binding_invalid", str(exc)) from exc
    expected = contract_storage_key(contract_id)
    if storage_key != expected:
        raise ContractStoreError(
            "binding_invalid",
            f"storage key does not match {contract_id.value}",
        )
    if encode_binding(contract_id) != binding_bytes:
        raise ContractStoreError("binding_invalid", "non-canonical bytes")
    return ContractBinding(contract_id=contract_id, storage_key=storage_key)


def encode_retirement(
    contract_id: ContractId,
    *,
    previous_generation_id: str,
    actor: str,
    reason: str,
) -> bytes:
    """Encode one deterministic, path-free retirement authorization."""
    generation_directory_name(previous_generation_id)
    if not isinstance(actor, str) or not actor.strip():
        raise ContractStoreError("retirement_input_invalid", "actor is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ContractStoreError("retirement_input_invalid", "reason is required")
    return (
        json.dumps(
            {
                "actor": actor.strip(),
                "contract_id": contract_id.value,
                "previous_generation_id": previous_generation_id,
                "reason": reason.strip(),
                "schema": RETIREMENT_SCHEMA,
                "schema_version": RETIREMENT_VERSION,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
    ).encode("utf-8")


def _validate_manifest_ref(ref: ManifestRef) -> None:
    if ref.origin is not ref.contract_id.origin:
        raise ContractStoreError("contract_reference_invalid", str(ref.contract_id))
    if ref.manifest_relative != ref.contract_id.relative_manifest:
        raise ContractStoreError("contract_reference_invalid", str(ref.contract_id))
    expected = Path(ref.source_root) / ref.manifest_relative
    if Path(os.path.abspath(expected)) != Path(os.path.abspath(ref.manifest_path)):
        raise ContractStoreError("contract_reference_invalid", str(ref.manifest_path))
    if ref.manifest_path.name != "manifest.toml":
        raise ContractStoreError("contract_reference_invalid", str(ref.manifest_path))


def _require_publishable_manifest(ref: ManifestRef) -> None:
    """Keep publication eligibility separate from runtime visibility policy.

    ``DISABLED`` means that policy currently hides the executor; it does not
    erase an installed contract or make its immutable history unpublishable.
    A source explicitly classified as ``RETIRED`` is different: bringing it
    back requires the dedicated, audited reactivation boundary.
    """
    if ref.status is ManifestStatus.RETIRED:
        raise ContractStoreError("source_retired", str(ref.contract_id))


def _revision_id(
    files: Mapping[str, bytes],
    *,
    expected_files: tuple[str, ...],
    code: str,
) -> str:
    if set(files) != set(expected_files):
        raise ContractStoreError(code, "unexpected revision payload")
    digest = hashlib.sha256()
    for name in expected_files:
        payload = files[name]
        if not isinstance(payload, bytes):
            raise TypeError(f"{name} payload must be bytes")
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return "sha256:" + digest.hexdigest()


def generation_id(files: Mapping[str, bytes]) -> str:
    return _revision_id(
        files,
        expected_files=GENERATION_FILES,
        code="generation_payload",
    )


def retirement_id(files: Mapping[str, bytes]) -> str:
    return _revision_id(
        files,
        expected_files=RETIREMENT_FILES,
        code="retirement_payload",
    )


def generation_directory_name(identifier: str) -> str:
    if not isinstance(identifier, str) or not _DIGEST_RE.fullmatch(identifier):
        raise ContractStoreError("generation_id_invalid", str(identifier))
    physical = identifier.removeprefix("sha256:")
    if not _PHYSICAL_ID_RE.fullmatch(physical):
        raise ContractStoreError("generation_id_invalid", identifier)
    return physical


def _canonical_sha256(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ContractStoreError("publication_input_invalid", field)
    return value


def _canonical_optional_sha256(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    return _canonical_sha256(value, field=field)


def _canonical_language(value: str, *, field: str) -> str:
    try:
        return normalize_language(value)
    except (TypeError, ValueError) as exc:
        raise ContractStoreError("language_tag_invalid", field) from exc


def _validated_removal(removal: SurfaceRemoval | None) -> tuple[str, ...]:
    if removal is None:
        return ()
    if not isinstance(removal, SurfaceRemoval):
        raise ContractStoreError("surface_removal_invalid", "wrong type")
    selectors = removal.selectors
    if (
        not isinstance(selectors, tuple)
        or not selectors
        or any(not isinstance(item, str) or not item for item in selectors)
        or selectors != tuple(sorted(set(selectors)))
    ):
        raise ContractStoreError(
            "surface_removal_invalid", "selectors must be non-empty, unique and sorted",
        )
    if not isinstance(removal.actor, str) or not removal.actor.strip():
        raise ContractStoreError("surface_removal_invalid", "actor is required")
    if not isinstance(removal.reason, str) or not removal.reason.strip():
        raise ContractStoreError("surface_removal_invalid", "reason is required")
    return selectors


def _editable_manifest(manifest_bytes: bytes) -> Any:
    """Parse a manifest only when a no-op round trip is byte-identical."""
    try:
        text = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractStoreError("manifest_utf8", str(exc)) from exc
    try:
        document = tomlkit.parse(text)
        rendered = tomlkit.dumps(document).encode("utf-8")
    except Exception as exc:
        raise ContractStoreError("manifest_toml", str(exc)) from exc
    if rendered != manifest_bytes:
        raise ContractStoreError("manifest_roundtrip_changed")
    return document


def _selector_table(document: Mapping[str, Any], selector: str) -> Mapping[str, Any]:
    if not isinstance(selector, str) or not selector:
        raise ContractStoreError("localization_selector_invalid", str(selector))
    parts = selector.split(".")
    if any(not part for part in parts):
        raise ContractStoreError("localization_selector_invalid", selector)
    node: Any = document
    for part in parts:
        if isinstance(node, Mapping) and part in node:
            node = node[part]
            continue
        if (
            isinstance(node, list)
            and part.isdecimal()
            and str(int(part)) == part
            and int(part) < len(node)
        ):
            node = node[int(part)]
            continue
        else:
            raise ContractStoreError("localization_selector_missing", selector)
    if not isinstance(node, Mapping):
        raise ContractStoreError("localization_selector_invalid", selector)
    return node


def _selector_owner_exists(document: Mapping[str, Any], selector: str) -> bool:
    """Return whether the schema node owning a localized surface still exists.

    Selectors are structural paths emitted by ``manifest_language_selectors``.
    The final segment names the localized table; its parent is the schema node
    whose removal can justify deleting that surface.  A root-level surface has
    the manifest itself as owner and therefore cannot be removed while the
    contract still exists.
    """
    parts = selector.split(".")
    if any(not part for part in parts):
        raise ContractStoreError("localization_selector_invalid", selector)
    node: Any = document
    for part in parts[:-1]:
        if isinstance(node, Mapping) and part in node:
            node = node[part]
            continue
        if (
            isinstance(node, (list, tuple))
            and part.isdecimal()
            and str(int(part)) == part
            and int(part) < len(node)
        ):
            node = node[int(part)]
            continue
        return False
    return isinstance(node, Mapping)


def _manifest_from_document(document: Any) -> tuple[bytes, dict[str, Any]]:
    try:
        manifest_bytes = tomlkit.dumps(document).encode("utf-8")
        parsed = tomllib.loads(manifest_bytes.decode("utf-8"))
    except Exception as exc:
        raise ContractStoreError("manifest_toml", str(exc)) from exc
    return manifest_bytes, parsed


def _linguistic_tables(
    manifest: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    try:
        raw = manifest_language_selectors(manifest)
    except LanguageStateError as exc:
        raise ContractStoreError(exc.code, exc.detail) from exc
    tables: dict[str, dict[str, str]] = {}
    for selector, languages in raw.items():
        normalized: dict[str, str] = {}
        for language, text in languages.items():
            if not isinstance(language, str) or not isinstance(text, str) or not text.strip():
                raise ContractStoreError(
                    "language_text_invalid", f"{selector}:{language}",
                )
            canonical = _canonical_language(language, field=language)
            if canonical != language or canonical in normalized:
                raise ContractStoreError("language_tag_noncanonical", language)
            normalized[canonical] = text
        if not normalized:
            raise ContractStoreError("language_state_languages", selector)
        tables[selector] = normalized
    if not tables:
        raise ContractStoreError("language_surfaces_missing")
    return tables


def _validate_linguistic_candidate(manifest: Mapping[str, Any]) -> None:
    """Run RM-0002 local and pairwise checks over every declared language."""
    tables = _linguistic_tables(manifest)
    languages = set().union(*(set(table) for table in tables.values()))
    incomplete = {
        selector: sorted(languages - set(table))
        for selector, table in tables.items()
        if set(table) != languages
    }
    if incomplete:
        raise ContractStoreError(
            "language_coverage_incomplete",
            json.dumps(incomplete, ensure_ascii=False, sort_keys=True),
        )

    from manifest_lint import lint_contract_translation, lint_manifest

    errors: list[str] = []
    for language in sorted(languages):
        for finding in lint_manifest(manifest, language=language):
            if finding.severity == "error":
                errors.append(
                    f"{finding.check}:{finding.resource}:{','.join(finding.languages)}"
                )
    for selector, table in sorted(tables.items()):
        ordered = sorted(table.items())
        for source_index, (source_language, source_text) in enumerate(ordered):
            for target_language, target_text in ordered[source_index + 1:]:
                for finding in lint_contract_translation(
                    source_text,
                    target_text,
                    resource=selector,
                    source_language=source_language,
                    target_language=target_language,
                ):
                    if finding.severity == "error":
                        errors.append(
                            f"{finding.check}:{selector}:"
                            f"{source_language},{target_language}"
                        )
    if errors:
        raise ContractStoreError(
            "contract_language_invalid", ";".join(sorted(set(errors))[:24]),
        )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _code_digest(
    ref: ManifestRef,
    parsed: Mapping[str, Any],
) -> str:
    code = parsed.get("code")
    files = code.get("files") if isinstance(code, Mapping) else None
    if (
        not isinstance(files, list)
        or not files
        or any(not isinstance(item, str) or not item for item in files)
    ):
        raise ContractStoreError("code_files_invalid")
    base = Path(os.path.abspath(ref.manifest_dir))
    lexical_roots = tuple(Path(os.path.abspath(root)) for root in ref.allowed_code_roots)
    resolved_roots: tuple[Path, ...]
    try:
        resolved_roots = tuple(root.resolve(strict=True) for root in ref.allowed_code_roots)
    except OSError as exc:
        raise ContractStoreError("code_root_invalid", str(exc)) from exc
    if not lexical_roots:
        raise ContractStoreError("code_roots_missing", str(ref.contract_id))
    digest = hashlib.sha256()
    for declared_path in files:
        candidate = Path(declared_path)
        if candidate.is_absolute():
            raise ContractStoreError("code_path_absolute", declared_path)
        lexical = Path(os.path.abspath(base / candidate))
        if not any(_inside(lexical, root) for root in lexical_roots):
            raise ContractStoreError("code_path_escape", declared_path)
        try:
            resolved = lexical.resolve(strict=True)
        except OSError as exc:
            raise ContractStoreError("code_file_missing", f"{declared_path}: {exc}") from exc
        if not any(_inside(resolved, root) for root in resolved_roots):
            raise ContractStoreError("code_target_escape", declared_path)
        if not resolved.is_file():
            raise ContractStoreError("code_file_invalid", declared_path)
        try:
            with resolved.open("rb") as handle:
                for chunk in iter(lambda: handle.read(8192), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise ContractStoreError("code_file_unreadable", f"{declared_path}: {exc}") from exc
    return "sha256:" + digest.hexdigest()


def _authenticate_payloads(
    ref: ManifestRef,
    payloads: Mapping[str, bytes],
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    identifier: str | None,
    require_inventory_hash: bool,
) -> _AuthenticatedPayloads:
    if set(payloads) != set(GENERATION_FILES):
        raise ContractStoreError("generation_payload")
    manifest_bytes = payloads["manifest.toml"]
    signature_bytes = payloads["manifest.toml.sig"]
    state_bytes = payloads["manifest.lang_state.json"]
    if require_inventory_hash:
        if ref.manifest_hash is None or ref.name is None:
            raise ContractStoreError(
                "contract_reference_incomplete", str(ref.contract_id),
            )
        if _sha256(manifest_bytes) != ref.manifest_hash:
            raise ContractStoreError(
                "source_changed_since_inventory", str(ref.manifest_path),
            )
    try:
        parsed = tomllib.loads(manifest_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ContractStoreError("manifest_utf8", str(exc)) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ContractStoreError("manifest_toml", str(exc)) from exc
    parsed_name = parsed.get("name")
    if (
        not isinstance(parsed_name, str)
        or not parsed_name.strip()
        or (require_inventory_hash and parsed_name != ref.name)
    ):
        raise ContractStoreError("contract_identity_mismatch", str(ref.contract_id))
    try:
        signer = verify_manifest_bytes(
            manifest_bytes,
            signature_bytes,
            trusted_publics=trusted_publics,
        )
    except ManifestSignatureError as exc:
        raise ContractStoreError("signature_invalid", str(exc)) from exc
    declared = (
        parsed.get("code", {}).get("digest")
        if isinstance(parsed.get("code"), dict)
        else None
    )
    if not isinstance(declared, str) or not _DIGEST_RE.fullmatch(declared):
        raise ContractStoreError("declared_code_digest_invalid")
    # Publication is an admission boundary, not a compatibility reader.  The
    # declaration must therefore be checked unconditionally: making the
    # validator conditional on the field itself would let an update bypass the
    # standard simply by deleting that field.
    from executor_standard import validate_for_lifecycle

    findings = validate_for_lifecycle(parsed, require_declaration=True)
    if findings:
        detail = "; ".join(
            f"{finding.code}:{finding.message}" for finding in findings[:8]
        )
        raise ContractStoreError("executor_standard_invalid", detail)
    try:
        language_state = decode_language_state(state_bytes, manifest=parsed)
    except LanguageStateError as exc:
        raise ContractStoreError(exc.code, exc.detail) from exc
    computed_identifier = generation_id(payloads)
    if identifier is not None and computed_identifier != identifier:
        raise ContractStoreError(
            "generation_digest_mismatch",
            f"expected={identifier} actual={computed_identifier}",
        )
    return _AuthenticatedPayloads(
        parsed=parsed,
        language_state=language_state,
        signed_by=signer.name,
        declared_code_digest=declared,
        generation_id=computed_identifier,
    )


def _verify_payloads(
    ref: ManifestRef,
    payloads: Mapping[str, bytes],
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    identifier: str | None,
    require_inventory_hash: bool,
) -> VerifiedManifest:
    authenticated = _authenticate_payloads(
        ref,
        payloads,
        trusted_publics=trusted_publics,
        identifier=identifier,
        require_inventory_hash=require_inventory_hash,
    )
    actual = _code_digest(ref, authenticated.parsed)
    if authenticated.declared_code_digest != actual:
        raise ContractStoreError(
            "code_digest_mismatch",
            f"declared={authenticated.declared_code_digest} actual={actual}",
        )
    manifest_bytes = payloads["manifest.toml"]
    signature_bytes = payloads["manifest.toml.sig"]
    state_bytes = payloads["manifest.lang_state.json"]
    return VerifiedManifest(
        contract_id=ref.contract_id,
        generation_id=identifier,
        source_manifest_dir=ref.manifest_dir,
        allowed_code_roots=tuple(ref.allowed_code_roots),
        manifest_bytes=manifest_bytes,
        manifest_hash=_sha256(manifest_bytes),
        parsed=_freeze(authenticated.parsed),
        signature_bytes=signature_bytes,
        signature_hash=_sha256(signature_bytes),
        language_state_bytes=state_bytes,
        language_state=authenticated.language_state,
        signed_by=authenticated.signed_by,
        declared_code_digest=authenticated.declared_code_digest,
        verified_code_digest=actual,
    )


def verify_manifest_source(
    ref: ManifestRef,
    *,
    trusted_publics: Iterable[TrustedPublic],
) -> VerifiedManifest:
    trusted = _trusted_public_tuple(trusted_publics)
    _validate_manifest_ref(ref)
    _require_plain_directory(ref.manifest_dir, code="source_directory_invalid")
    _require_no_link_components(ref.manifest_dir, code="source_directory_invalid")
    payloads = {
        name: _read_regular_file(
            ref.manifest_dir / name,
            code="source_file_invalid",
        )
        for name in GENERATION_FILES
    }
    return _verify_payloads(
        ref,
        payloads,
        trusted_publics=trusted,
        identifier=None,
        require_inventory_hash=True,
    )


def prepare_technical_draft(ref: ManifestRef) -> TechnicalDraft:
    """Snapshot a proposed authoring change without signing or locking it."""
    _validate_manifest_ref(ref)
    _require_publishable_manifest(ref)
    _require_plain_directory(ref.manifest_dir, code="source_directory_invalid")
    _require_no_link_components(ref.manifest_dir, code="source_directory_invalid")
    manifest_bytes = _read_regular_file(
        ref.manifest_dir / "manifest.toml", code="source_file_invalid",
    )
    state_bytes = _read_regular_file(
        ref.manifest_dir / "manifest.lang_state.json", code="source_file_invalid",
    )
    signature_path = ref.manifest_dir / "manifest.toml.sig"
    if _is_link_like(signature_path):
        raise ContractStoreError("source_file_invalid", str(signature_path))
    signature_bytes = (
        _read_regular_file(signature_path, code="source_file_invalid")
        if signature_path.exists()
        else None
    )
    document = _editable_manifest(manifest_bytes)
    _rendered, parsed = _manifest_from_document(document)
    if ref.name is None or parsed.get("name") != ref.name:
        raise ContractStoreError("contract_identity_mismatch", str(ref.contract_id))
    try:
        decode_language_state(
            state_bytes, manifest=parsed,
        )
    except LanguageStateError as exc:
        raise ContractStoreError(exc.code, exc.detail) from exc
    _validate_linguistic_candidate(parsed)
    return TechnicalDraft(
        manifest_bytes=manifest_bytes,
        language_state_bytes=state_bytes,
        authoring_manifest_hash=_sha256(manifest_bytes),
        authoring_signature_hash=(
            None if signature_bytes is None else _sha256(signature_bytes)
        ),
        authoring_language_state_hash=_sha256(state_bytes),
        authoring_code_digest=_code_digest(ref, parsed),
    )


def _validate_technical_policy(
    base_payloads: Mapping[str, bytes] | None,
    candidate_manifest: Mapping[str, Any],
    candidate_state: Mapping[str, Any],
    *,
    removal: SurfaceRemoval | None,
) -> None:
    requested_removals = set(_validated_removal(removal))
    candidate_tables = _linguistic_tables(candidate_manifest)
    _validate_linguistic_candidate(candidate_manifest)
    if base_payloads is None:
        if requested_removals:
            raise ContractStoreError(
                "surface_removal_invalid", "new contract has no surfaces to remove",
            )
        return

    try:
        base_manifest = tomllib.loads(
            base_payloads["manifest.toml"].decode("utf-8"),
        )
        base_state = decode_language_state(
            base_payloads["manifest.lang_state.json"], manifest=base_manifest,
        )
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ContractStoreError("generation_invalid", str(exc)) from exc
    except LanguageStateError as exc:
        raise ContractStoreError(exc.code, exc.detail) from exc

    if candidate_manifest.get("name") != base_manifest.get("name"):
        raise ContractStoreError("contract_identity_changed", "name")
    base_tables = _linguistic_tables(base_manifest)
    removed = set(base_tables) - set(candidate_tables)
    if removed != requested_removals:
        code = "surface_removal_required" if removed and not removal else "surface_removal_invalid"
        raise ContractStoreError(
            code,
            f"declared={sorted(requested_removals)} actual={sorted(removed)}",
        )
    still_applicable = sorted(
        selector
        for selector in removed
        if _selector_owner_exists(candidate_manifest, selector)
    )
    if still_applicable:
        raise ContractStoreError(
            "surface_removal_still_applicable", ",".join(still_applicable),
        )

    candidate_state_selectors = candidate_state.get("selectors")
    base_state_selectors = base_state.get("selectors")
    if not isinstance(candidate_state_selectors, Mapping) or not isinstance(
        base_state_selectors, Mapping,
    ):
        raise ContractStoreError("language_state_selectors")
    for selector, old_languages in base_tables.items():
        if selector in removed:
            continue
        new_languages = candidate_tables.get(selector)
        if new_languages is None:
            raise ContractStoreError("surface_removal_invalid", selector)
        for language, old_text in old_languages.items():
            if new_languages.get(language) != old_text:
                raise ContractStoreError(
                    "existing_localization_changed", f"{selector}:{language}",
                )
            old_entry = base_state_selectors[selector][language]
            new_entry = candidate_state_selectors[selector][language]
            if new_entry != old_entry:
                raise ContractStoreError(
                    "existing_localization_state_changed", f"{selector}:{language}",
                )


def _generation_payloads(path: Path) -> dict[str, bytes]:
    _require_plain_directory(path, code="generation_invalid")
    try:
        children = tuple(path.iterdir())
    except OSError as exc:
        raise ContractStoreError("generation_invalid", str(exc)) from exc
    if {child.name for child in children} != set(GENERATION_FILES):
        raise ContractStoreError("generation_structure", str(path))
    payloads: dict[str, bytes] = {}
    for child in children:
        payloads[child.name] = _read_regular_file(child, code="generation_file_invalid")
    return payloads


def _retirement_payloads(path: Path) -> dict[str, bytes]:
    _require_plain_directory(path, code="retirement_invalid")
    try:
        children = tuple(path.iterdir())
    except OSError as exc:
        raise ContractStoreError("retirement_invalid", str(exc)) from exc
    if {child.name for child in children} != set(RETIREMENT_FILES):
        raise ContractStoreError("retirement_structure", str(path))
    return {
        child.name: _read_regular_file(
            child, code="retirement_file_invalid",
        )
        for child in children
    }


def _revision_kind(path: Path) -> str:
    _require_plain_directory(path, code="revision_invalid")
    try:
        names = {child.name for child in path.iterdir()}
    except OSError as exc:
        raise ContractStoreError("revision_invalid", str(exc)) from exc
    if names == set(GENERATION_FILES):
        return "generation"
    if names == set(RETIREMENT_FILES):
        return "retirement"
    raise ContractStoreError("revision_structure", str(path))


def _authenticate_retirement_payloads(
    ref: ManifestRef,
    payloads: Mapping[str, bytes],
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    identifier: str | None,
) -> ContractRetirement:
    if set(payloads) != set(RETIREMENT_FILES):
        raise ContractStoreError("retirement_payload")
    retirement_bytes = payloads["retirement.json"]
    signature_bytes = payloads["retirement.json.sig"]
    parsed = _json_without_duplicates(
        retirement_bytes, code="retirement_invalid",
    )
    expected_keys = {
        "actor",
        "contract_id",
        "previous_generation_id",
        "reason",
        "schema",
        "schema_version",
    }
    if not isinstance(parsed, Mapping) or set(parsed) != expected_keys:
        raise ContractStoreError("retirement_invalid", "unexpected schema")
    if (
        parsed.get("schema") != RETIREMENT_SCHEMA
        or type(parsed.get("schema_version")) is not int
        or parsed.get("schema_version") != RETIREMENT_VERSION
    ):
        raise ContractStoreError("retirement_invalid", "unsupported schema")
    if parsed.get("contract_id") != ref.contract_id.value:
        raise ContractStoreError(
            "retirement_contract_mismatch", str(ref.contract_id),
        )
    previous = parsed.get("previous_generation_id")
    actor = parsed.get("actor")
    reason = parsed.get("reason")
    if not isinstance(previous, str):
        raise ContractStoreError(
            "retirement_invalid", "previous generation is required",
        )
    generation_directory_name(previous)
    if not isinstance(actor, str) or not actor.strip() or actor != actor.strip():
        raise ContractStoreError("retirement_invalid", "actor is not canonical")
    if not isinstance(reason, str) or not reason.strip() or reason != reason.strip():
        raise ContractStoreError("retirement_invalid", "reason is not canonical")
    if encode_retirement(
        ref.contract_id,
        previous_generation_id=previous,
        actor=actor,
        reason=reason,
    ) != retirement_bytes:
        raise ContractStoreError("retirement_invalid", "non-canonical bytes")
    try:
        signer = verify_manifest_bytes(
            RETIREMENT_SIGNATURE_DOMAIN + retirement_bytes,
            signature_bytes,
            trusted_publics=trusted_publics,
        )
    except ManifestSignatureError as exc:
        raise ContractStoreError("retirement_signature_invalid", str(exc)) from exc
    computed = retirement_id(payloads)
    if identifier is not None and computed != identifier:
        raise ContractStoreError(
            "retirement_digest_mismatch",
            f"expected={identifier} actual={computed}",
        )
    return ContractRetirement(
        contract_id=ref.contract_id,
        retirement_id=computed,
        previous_generation_id=previous,
        actor=actor,
        reason=reason,
        payload_bytes=retirement_bytes,
        signature_bytes=signature_bytes,
        signature_hash=_sha256(signature_bytes),
        signed_by=signer.name,
    )


def _load_generation(
    ref: ManifestRef,
    identifier: str,
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path | str | None,
) -> VerifiedManifest:
    _validate_manifest_ref(ref)
    physical = generation_directory_name(identifier)
    contract_dir = _existing_contract_directory(ref.contract_id, store_root=store_root)
    generations = contract_dir / "generations"
    _require_plain_directory(generations, code="generations_directory_invalid")
    revision = generations / physical
    try:
        kind = _revision_kind(revision)
    except ContractStoreError as exc:
        if exc.code != "revision_structure":
            raise
        kind = "generation"
    if kind == "retirement":
        retired = _load_retirement(
            ref,
            identifier,
            trusted_publics=trusted_publics,
            store_root=store_root,
        )
        raise ContractStoreError("contract_retired", retired.retirement_id)
    payloads = _generation_payloads(revision)
    return _verify_payloads(
        ref,
        payloads,
        trusted_publics=trusted_publics,
        identifier=identifier,
        require_inventory_hash=False,
    )


def _load_generation_for_commit(
    ref: ManifestRef,
    identifier: str,
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path | str,
) -> dict[str, bytes]:
    """Authenticate the immutable CAS base without binding it to new code.

    A technical publisher necessarily observes the old signed generation
    after authoring code has changed.  Its CAS base must still have a valid
    signature, structure, language state and generation digest, while the new
    candidate alone must match the current code.  Live readers and localization
    publication continue to use :func:`current_manifest`, which verifies code.
    """
    _validate_manifest_ref(ref)
    physical = generation_directory_name(identifier)
    contract_dir = _existing_contract_directory(
        ref.contract_id, store_root=store_root,
    )
    generations = contract_dir / "generations"
    _require_plain_directory(generations, code="generations_directory_invalid")
    revision = generations / physical
    try:
        kind = _revision_kind(revision)
    except ContractStoreError as exc:
        if exc.code != "revision_structure":
            raise
        kind = "generation"
    if kind != "generation":
        raise ContractStoreError("contract_retired", identifier)
    payloads = _generation_payloads(revision)
    _authenticate_payloads(
        ref,
        payloads,
        trusted_publics=trusted_publics,
        identifier=identifier,
        require_inventory_hash=False,
    )
    return payloads


def _load_retirement(
    ref: ManifestRef,
    identifier: str,
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path | str | None,
) -> ContractRetirement:
    """Authenticate a tombstone and the immutable generation it retires."""
    _validate_manifest_ref(ref)
    physical = generation_directory_name(identifier)
    contract_dir = _existing_contract_directory(
        ref.contract_id, store_root=store_root,
    )
    generations = contract_dir / "generations"
    _require_plain_directory(generations, code="generations_directory_invalid")
    payloads = _retirement_payloads(generations / physical)
    retirement = _authenticate_retirement_payloads(
        ref,
        payloads,
        trusted_publics=trusted_publics,
        identifier=identifier,
    )
    # The referenced active revision remains part of the evidence chain.  It
    # is authenticated without touching code, which callers may delete only
    # after this tombstone has become current.
    _load_generation_for_commit(
        ref,
        retirement.previous_generation_id,
        trusted_publics=trusted_publics,
        store_root=_store_root(store_root),
    )
    return retirement


def _load_revision(
    ref: ManifestRef,
    identifier: str,
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path | str | None,
) -> VerifiedManifest | ContractRetirement:
    contract_dir = _existing_contract_directory(
        ref.contract_id, store_root=store_root,
    )
    physical = generation_directory_name(identifier)
    revision = contract_dir / "generations" / physical
    kind = _revision_kind(revision)
    if kind == "generation":
        return _load_generation(
            ref,
            identifier,
            trusted_publics=trusted_publics,
            store_root=store_root,
        )
    return _load_retirement(
        ref,
        identifier,
        trusted_publics=trusted_publics,
        store_root=store_root,
    )


def _authenticate_revision_for_commit(
    ref: ManifestRef,
    identifier: str,
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path | str | None,
) -> Mapping[str, bytes] | ContractRetirement:
    """Authenticate a CAS revision without binding it to authoring code.

    Technical rollback is requested after the caller has restored the target
    source bytes.  The currently selected generation can therefore describe
    the code being replaced, not the code now present in authoring.  Its
    structure, signature and revision digest remain mandatory; only the
    rollback target is verified against the restored code before the pointer
    can move.
    """
    contract_dir = _existing_contract_directory(
        ref.contract_id, store_root=store_root,
    )
    revision = (
        contract_dir / "generations" / generation_directory_name(identifier)
    )
    if _revision_kind(revision) == "retirement":
        return _load_retirement(
            ref,
            identifier,
            trusted_publics=trusted_publics,
            store_root=store_root,
        )
    return _load_generation_for_commit(
        ref,
        identifier,
        trusted_publics=trusted_publics,
        store_root=_store_root(store_root),
    )


def _read_current_optional(contract_dir: Path) -> str | None:
    current = contract_dir / "current"
    if _is_link_like(current):
        raise ContractStoreError("current_invalid", str(current))
    if not current.exists():
        return None
    value = _read_regular_file(current, code="current_invalid")
    try:
        text = value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ContractStoreError("current_invalid", str(exc)) from exc
    if not text.endswith("\n") or text.count("\n") != 1:
        raise ContractStoreError("current_invalid", repr(text))
    identifier = text[:-1]
    generation_directory_name(identifier)
    return identifier


def current_revision_id(
    ref: ManifestRef,
    *,
    store_root: Path | str | None = None,
) -> str:
    """Read only the canonical pointer after validating binding and layout.

    This lightweight cache token deliberately does not authenticate revision
    payloads or code.  A consumer must still use :func:`current_contract`
    before treating the referenced content as authoritative.
    """
    _validate_manifest_ref(ref)
    contract_dir = _existing_contract_directory(
        ref.contract_id, store_root=store_root,
    )
    identifier = _read_current_optional(contract_dir)
    if identifier is None:
        raise ContractStoreError("current_missing", str(ref.contract_id))
    return identifier


def current_contract(
    ref: ManifestRef,
    *,
    trusted_publics: Iterable[TrustedPublic],
    store_root: Path | str | None = None,
) -> VerifiedManifest | ContractRetirement:
    """Return the authenticated active generation or retirement tombstone."""
    trusted = _trusted_public_tuple(trusted_publics)
    identifier = current_revision_id(ref, store_root=store_root)
    return _load_revision(
        ref,
        identifier,
        trusted_publics=trusted,
        store_root=store_root,
    )


def current_manifest(
    ref: ManifestRef,
    *,
    trusted_publics: Iterable[TrustedPublic],
    store_root: Path | str | None = None,
) -> VerifiedManifest:
    """Return the live manifest, failing closed for a retired contract."""
    current = current_contract(
        ref,
        trusted_publics=trusted_publics,
        store_root=store_root,
    )
    if isinstance(current, ContractRetirement):
        raise ContractStoreError("contract_retired", current.retirement_id)
    return current


def _process_lock_for(path: Path) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(path))
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.Lock())


def _lock_conflict(exc: OSError) -> bool:
    return (
        isinstance(exc, BlockingIOError)
        or exc.errno in {errno.EACCES, errno.EAGAIN}
        or getattr(exc, "winerror", None) in {5, 32, 33, 36}
    )


def _windows_delete_share_conflict(path: Path) -> bool:
    """Probe one Windows path for a conflicting delete-sharing mode.

    ``MoveFileExW`` can report ``ERROR_ACCESS_DENIED`` (5) when an existing
    destination handle was opened without ``FILE_SHARE_DELETE``.  Retrying
    every error 5 would also retry real ACL and attribute failures.  A
    ``CreateFileW`` probe that requests only ``DELETE`` access separates the
    cases: an incompatible live handle is reported as
    ``ERROR_SHARING_VIOLATION`` (32), while a denied access check remains 5.

    This is a classifier only.  It never changes the path and fails closed
    for every result other than a documented sharing/lock violation.
    """
    if os.name != "nt":
        return False

    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateFileW(
        str(path),
        0x00010000,  # DELETE: also grants rename access.
        0x00000001 | 0x00000002 | 0x00000004,  # Share read/write/delete.
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in {None, invalid}:
        return ctypes.get_last_error() in {32, 33}
    kernel32.CloseHandle(handle)
    return False


def _pointer_replace_conflict(
    exc: OSError,
    *,
    source: Path,
    destination: Path,
) -> bool:
    """Return only proven Windows sharing/lock violations."""
    error = getattr(exc, "winerror", None)
    if error in {32, 33}:
        return True
    if error != 5:
        return False
    return _windows_delete_share_conflict(destination) or (
        _windows_delete_share_conflict(source)
    )


def _try_system_lock(handle: Any) -> bool:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if _lock_conflict(exc):
                return False
            raise
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if _lock_conflict(exc):
            return False
        raise


def _release_system_lock(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _exclusive_file_lock(
    lock_path: Path,
    *,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
    timeout_code: str,
    invalid_code: str,
    detail: str,
) -> Iterator[None]:
    """Hold one finite cross-process lock without following redirected paths."""
    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    _require_plain_directory(lock_path.parent, code=invalid_code)
    _require_no_link_components(lock_path.parent, code=invalid_code)
    process_lock = _process_lock_for(lock_path)
    deadline = time.monotonic() + timeout
    remaining = max(0.0, deadline - time.monotonic())
    if not process_lock.acquire(timeout=remaining):
        raise ContractStoreError(timeout_code, detail)
    handle = None
    system_locked = False
    try:
        before: os.stat_result | None
        try:
            before = lock_path.lstat()
        except FileNotFoundError:
            before = None
        except OSError as exc:
            raise ContractStoreError(invalid_code, f"{lock_path}: {exc}") from exc
        if before is not None and (
            _is_link_like(lock_path)
            or not stat.S_ISREG(before.st_mode)
        ):
            raise ContractStoreError(invalid_code, str(lock_path))
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise ContractStoreError(invalid_code, f"{lock_path}: {exc}") from exc
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        file_status = os.fstat(handle.fileno())
        if not stat.S_ISREG(file_status.st_mode):
            raise ContractStoreError(invalid_code, str(lock_path))
        if (
            hasattr(os, "geteuid")
            and hasattr(file_status, "st_uid")
            and file_status.st_uid != os.geteuid()
        ):
            raise ContractStoreError(invalid_code, f"foreign owner: {lock_path}")
        try:
            after = lock_path.lstat()
        except OSError as exc:
            raise ContractStoreError(invalid_code, f"{lock_path}: {exc}") from exc
        if (
            _is_link_like(lock_path)
            or not stat.S_ISREG(after.st_mode)
            or not os.path.samestat(after, file_status)
            or (before is not None and not os.path.samestat(before, file_status))
        ):
            raise ContractStoreError(invalid_code, str(lock_path))
        size = file_status.st_size
        if size == 0:
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        elif size != 1:
            raise ContractStoreError(invalid_code, str(lock_path))
        while not _try_system_lock(handle):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ContractStoreError(timeout_code, detail)
            time.sleep(min(0.02, remaining))
        system_locked = True
        yield
    finally:
        try:
            if handle is not None:
                try:
                    if system_locked:
                        _release_system_lock(handle)
                finally:
                    handle.close()
        finally:
            process_lock.release()


@contextlib.contextmanager
def _writer_lock(
    contract_id: ContractId,
    *,
    store_root: Path | str,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> Iterator[None]:
    """Serialize every cooperating contract writer with a portable lock."""
    contract_dir, _generations = _ensure_store_directories(
        contract_id,
        store_root=store_root,
    )
    with _exclusive_file_lock(
        contract_dir / "writer.lock",
        timeout=timeout,
        timeout_code="lock_timeout",
        invalid_code="lock_file_invalid",
        detail=str(contract_id),
    ):
        yield


def _catalog_lock_path(root: Path) -> Path:
    """Place the global lock outside the immutable version-directory shape."""
    absolute = Path(os.path.abspath(root))
    if absolute.name == STORE_RELATIVE.name:
        return absolute.parent.parent / (
            f".{absolute.parent.name}-{absolute.name}.catalog-admission.lock"
        )
    return absolute.parent / f".{absolute.name}.catalog-admission.lock"


@contextlib.contextmanager
def catalog_admission_lock(
    *,
    store_root: Path | str | None = None,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> Iterator[None]:
    """Serialize every transition that can add or remove a visible name.

    The fixed acquisition order is this global lock first, followed by the
    contract writer lock or the external visibility-policy lock.  Keeping the
    sidecar outside ``v1`` preserves the immutable store grammar.
    """
    root = _store_root(store_root)
    _require_no_link_components(root, code="store_root_invalid")
    lock_path = _catalog_lock_path(root)
    _ensure_directory_chain(lock_path.parent, code="catalog_lock_invalid")
    key = os.path.normcase(os.path.abspath(lock_path))
    held = getattr(_CATALOG_LOCK_LOCAL, "held", None)
    if held is None:
        held = {}
        _CATALOG_LOCK_LOCAL.held = held
    if key in held:
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return

    lock = _exclusive_file_lock(
        lock_path,
        timeout=timeout,
        timeout_code="catalog_lock_timeout",
        invalid_code="catalog_lock_invalid",
        detail=str(lock_path),
    )
    lock.__enter__()
    held[key] = 1
    try:
        yield
    finally:
        del held[key]
        lock.__exit__(None, None, None)


def _catalog_sources_for_candidate(ref: ManifestRef):
    """Return the canonical origin map, extending it only for test fixtures."""
    from manifest_inventory import ManifestSource, default_manifest_sources

    sources = tuple(default_manifest_sources())
    if any(source.origin is ref.origin for source in sources):
        return sources
    # ``EXPLICIT`` is intentionally absent from the productive topology but
    # is useful for isolated store certification.  Its structural root comes
    # from the already validated reference, never from the executor name.
    return sources + (ManifestSource(
        ref.origin,
        Path(ref.source_root),
        min_depth=0,
        max_depth=None,
        default_status=ref.status,
        skill_scoped=ref.skill_name is not None,
        allowed_code_roots=tuple(ref.allowed_code_roots),
    ),)


def _require_catalog_name_candidate(
    ref: ManifestRef,
    candidate_name: object,
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path,
) -> None:
    """Authenticate the complete visible candidate before a pointer commit.

    The caller holds :func:`catalog_admission_lock`.  The candidate contract
    is substituted in memory, so a rejected first publication cannot leave a
    new binding that makes the next boot incomplete.
    """
    if not isinstance(candidate_name, str) or not candidate_name.strip():
        raise ContractStoreError("published_name_invalid", ref.contract_id.value)
    if not store_root.exists():
        return

    from manifest_inventory import (
        inventory_store_manifests,
        manifest_name_collisions,
    )

    candidate_dir = store_root / contract_storage_key(ref.contract_id)
    candidate_binding = candidate_dir / BINDING_FILE
    substitute_unbound_candidate = (
        candidate_dir.exists()
        and not _is_link_like(candidate_dir)
        and candidate_dir.is_dir()
        and not candidate_binding.exists()
        and not _is_link_like(candidate_binding)
    )

    def binding_reader(directory: Path) -> ContractBinding:
        # A hard stop can leave the candidate's directory before the binding
        # rename.  Inventory still substitutes that one structural identity
        # in memory; the writer-locked recovery below must validate its exact
        # layout and staging bytes before publication can continue.
        if substitute_unbound_candidate and directory == candidate_dir:
            return ContractBinding(
                contract_id=ref.contract_id,
                storage_key=contract_storage_key(ref.contract_id),
            )
        return read_binding(directory)

    inventory = inventory_store_manifests(
        _catalog_sources_for_candidate(ref),
        store_root=store_root,
        binding_reader=binding_reader,
    )
    if inventory.problems:
        problem = inventory.problems[0]
        raise ContractStoreError(
            "catalog_candidate_invalid",
            f"{problem.code}:{problem.path}:{problem.detail}",
        )
    installed_names: list[tuple[ContractId, str]] = []
    for current_ref in inventory.installed():
        if current_ref.contract_id == ref.contract_id:
            continue
        revision = current_contract(
            current_ref,
            trusted_publics=trusted_publics,
            store_root=store_root,
        )
        if isinstance(revision, ContractRetirement):
            # Retirement removes executable authority, not the stable public
            # identity used by the i18n registry.  Authenticate the immutable
            # predecessor and keep its name reserved; otherwise a different
            # ContractId can commit successfully and fail deterministically
            # during registry reconciliation against the retained rows.
            predecessor = _load_generation_for_commit(
                current_ref,
                revision.previous_generation_id,
                trusted_publics=trusted_publics,
                store_root=store_root,
            )
            try:
                parsed = tomllib.loads(
                    predecessor["manifest.toml"].decode("utf-8")
                )
            except (KeyError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                raise ContractStoreError(
                    "catalog_candidate_invalid",
                    f"retired_predecessor:{current_ref.contract_id.value}:{exc}",
                ) from exc
            current_name = parsed.get("name")
        else:
            current_name = revision.parsed.get("name")
        if not isinstance(current_name, str) or not current_name.strip():
            raise ContractStoreError(
                "published_name_invalid", current_ref.contract_id.value,
            )
        installed_names.append((current_ref.contract_id, current_name))
    # Names are reserved by every installed contract, including a skill hidden
    # by policy and an authenticated tombstone.  The i18n resource identity is
    # name-based too; reuse under another ContractId would otherwise make the
    # generation commit succeed and registry reconciliation fail afterwards.
    installed_names.append((ref.contract_id, candidate_name))
    collisions = manifest_name_collisions(installed_names)
    if collisions:
        detail = "; ".join(
            f"{name}=[{','.join(contract_ids)}]"
            for name, contract_ids in collisions[:8]
        )
        raise ContractStoreError("published_name_collision", detail)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new_file(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically install one sibling path and never replace its destination.

    Windows ``os.rename`` already has no-replace semantics.  Certified Linux
    filesystems use ``renameat2(RENAME_NOREPLACE)``; if that primitive is not
    available we fail closed instead of weakening the immutability contract.
    """
    if os.name == "nt":
        os.rename(source, destination)
        return
    if not sys.platform.startswith("linux"):
        raise ContractStoreError("no_replace_unavailable", sys.platform)
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ContractStoreError("no_replace_unavailable", "renameat2")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,  # AT_FDCWD
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,  # RENAME_NOREPLACE
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, os.strerror(error_number), destination)
    if error_number in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
        raise ContractStoreError(
            "no_replace_unavailable", os.strerror(error_number),
        )
    raise OSError(error_number, os.strerror(error_number), destination)


def _replace_retry(source: Path, destination: Path, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            if not _windows_platform() or not _pointer_replace_conflict(
                exc,
                source=source,
                destination=destination,
            ):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ContractStoreError("pointer_replace_timeout", str(destination)) from exc
            time.sleep(min(0.02, remaining))


def _windows_platform() -> bool:
    return os.name == "nt"


def _atomic_replace_file(
    destination: Path,
    payload: bytes,
    *,
    replace_timeout: float,
    mode: int = 0o600,
) -> None:
    if _is_link_like(destination):
        raise ContractStoreError("destination_link", str(destination))
    _require_no_link_components(destination.parent, code="destination_directory_invalid")
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{threading.get_ident()}.{time.monotonic_ns()}.tmp"
    )
    try:
        _write_new_file(temporary, payload, mode=mode)
        _replace_retry(temporary, destination, timeout=replace_timeout)
        _sync_directory(destination.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_current(
    contract_dir: Path,
    identifier: str,
    *,
    replace_timeout: float,
) -> None:
    generation_directory_name(identifier)
    _atomic_replace_file(
        contract_dir / "current",
        (identifier + "\n").encode("ascii"),
        replace_timeout=replace_timeout,
    )


def _install_generation(
    ref: ManifestRef,
    payloads: Mapping[str, bytes],
    *,
    identifier: str,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path | str | None,
) -> None:
    _contract_dir, generations = _ensure_store_directories(
        ref.contract_id,
        store_root=store_root,
    )
    physical = generation_directory_name(identifier)
    final = generations / physical
    if _is_link_like(final):
        raise ContractStoreError("generation_corrupt", str(final))
    if final.exists():
        try:
            existing = _generation_payloads(final)
            if existing != dict(payloads):
                raise ContractStoreError("generation_corrupt", str(final))
            _verify_payloads(
                ref,
                existing,
                trusted_publics=trusted_publics,
                identifier=identifier,
                require_inventory_hash=False,
            )
        except ContractStoreError as exc:
            if exc.code == "generation_corrupt":
                raise
            raise ContractStoreError("generation_corrupt", f"{final}: {exc}") from exc
        _sync_directory(generations)
        return
    temporary = Path(tempfile.mkdtemp(prefix=".generation-", dir=generations))
    try:
        for name in GENERATION_FILES:
            _write_new_file(temporary / name, payloads[name])
        _sync_directory(temporary)
        prepared = _generation_payloads(temporary)
        _verify_payloads(
            ref,
            prepared,
            trusted_publics=trusted_publics,
            identifier=identifier,
            require_inventory_hash=False,
        )
        try:
            _rename_no_replace(temporary, final)
        except FileExistsError:
            try:
                existing = _generation_payloads(final)
                if existing != prepared:
                    raise ContractStoreError("generation_corrupt", str(final))
                _verify_payloads(
                    ref,
                    existing,
                    trusted_publics=trusted_publics,
                    identifier=identifier,
                    require_inventory_hash=False,
                )
            except ContractStoreError as exc:
                if exc.code == "generation_corrupt":
                    raise
                raise ContractStoreError(
                    "generation_corrupt", f"{final}: {exc}",
                ) from exc
        _sync_directory(generations)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _install_retirement(
    ref: ManifestRef,
    payloads: Mapping[str, bytes],
    *,
    identifier: str,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path | str | None,
) -> None:
    """Install one immutable tombstone with the generation durability rules."""
    _contract_dir, generations = _ensure_store_directories(
        ref.contract_id,
        store_root=store_root,
    )
    physical = generation_directory_name(identifier)
    final = generations / physical
    if _is_link_like(final):
        raise ContractStoreError("retirement_corrupt", str(final))
    if final.exists():
        try:
            if _revision_kind(final) != "retirement":
                raise ContractStoreError("retirement_corrupt", str(final))
            existing = _retirement_payloads(final)
            if existing != dict(payloads):
                raise ContractStoreError("retirement_corrupt", str(final))
            _authenticate_retirement_payloads(
                ref,
                existing,
                trusted_publics=trusted_publics,
                identifier=identifier,
            )
        except ContractStoreError as exc:
            if exc.code == "retirement_corrupt":
                raise
            raise ContractStoreError(
                "retirement_corrupt", f"{final}: {exc}",
            ) from exc
        _sync_directory(generations)
        return
    temporary = Path(tempfile.mkdtemp(prefix=".generation-", dir=generations))
    try:
        for name in RETIREMENT_FILES:
            _write_new_file(temporary / name, payloads[name])
        _sync_directory(temporary)
        prepared = _retirement_payloads(temporary)
        _authenticate_retirement_payloads(
            ref,
            prepared,
            trusted_publics=trusted_publics,
            identifier=identifier,
        )
        try:
            _rename_no_replace(temporary, final)
        except FileExistsError:
            try:
                if _revision_kind(final) != "retirement":
                    raise ContractStoreError("retirement_corrupt", str(final))
                existing = _retirement_payloads(final)
                if existing != prepared:
                    raise ContractStoreError("retirement_corrupt", str(final))
                _authenticate_retirement_payloads(
                    ref,
                    existing,
                    trusted_publics=trusted_publics,
                    identifier=identifier,
                )
            except ContractStoreError as exc:
                if exc.code == "retirement_corrupt":
                    raise
                raise ContractStoreError(
                    "retirement_corrupt", f"{final}: {exc}",
                ) from exc
        _sync_directory(generations)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _staging_directory_recovery_plan(
    generations: Path,
) -> tuple[tuple[Path, tuple[Path, ...]], ...]:
    """Validate generation staging and return a cleanup plan without writing.

    A hard process stop can bypass the publisher's ``finally`` block at any
    point after ``mkdtemp``.  These directories have no revision identifier
    and can never be selected by ``current``.  Recovery accepts only a plain
    directory with a non-empty staging suffix whose regular files are a
    partial generation *or* retirement payload.  Links, nested directories,
    mixed payload kinds and unknown names are debris and fail closed.
    """
    _require_plain_directory(generations, code="generations_directory_invalid")
    prefix = ".generation-"
    expected_sets = (set(GENERATION_FILES), set(RETIREMENT_FILES))
    recoverable: list[tuple[Path, tuple[Path, ...]]] = []
    try:
        entries = tuple(generations.iterdir())
    except OSError as exc:
        raise ContractStoreError("generations_directory_invalid", str(exc)) from exc
    for entry in entries:
        if not entry.name.startswith(prefix):
            continue
        if entry.name == prefix or _is_link_like(entry) or not entry.is_dir():
            raise ContractStoreError("staging_invalid", str(entry))
        try:
            children = tuple(entry.iterdir())
        except OSError as exc:
            raise ContractStoreError("staging_invalid", str(exc)) from exc
        names = {child.name for child in children}
        if not any(names <= expected for expected in expected_sets):
            raise ContractStoreError("staging_invalid", str(entry))
        if any(_is_link_like(child) or not child.is_file() for child in children):
            raise ContractStoreError("staging_invalid", str(entry))
        recoverable.append((entry, children))

    return tuple(recoverable)


def _direct_staging_recovery_plan(
    ref: ManifestRef,
    contract_dir: Path,
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path,
) -> tuple[Path, ...]:
    """Validate direct-file crash staging without treating it as authority.

    ``_ensure_binding_locked`` and ``_atomic_replace_file`` use one closed,
    process-generated filename grammar.  A staged binding is recoverable only
    when it is the exact canonical binding for this directory.  A staged
    pointer is recoverable only when its canonical revision already exists and
    authenticates from the immutable store.  Thus recovery never promotes
    uncommitted bytes and never guesses what a writer intended.
    """
    _require_plain_directory(contract_dir, code="contract_directory_invalid")
    try:
        entries = tuple(contract_dir.iterdir())
    except OSError as exc:
        raise ContractStoreError("contract_directory_invalid", str(exc)) from exc

    recoverable: list[Path] = []
    for entry in entries:
        reserved = (
            entry.name.startswith(f".{BINDING_FILE}.")
            or entry.name.startswith(".current.")
        )
        if not reserved:
            if entry.name not in {
                BINDING_FILE, "current", "writer.lock", "generations",
                "admission-receipts",
            }:
                raise ContractStoreError("staging_invalid", str(entry))
            continue
        match = _DIRECT_STAGING_RE.fullmatch(entry.name)
        if match is None or _is_link_like(entry) or not entry.is_file():
            raise ContractStoreError("staging_invalid", str(entry))
        payload = _read_regular_file(entry, code="staging_invalid")
        kind = match.group(1)
        if kind == BINDING_FILE:
            if payload != encode_binding(ref.contract_id):
                raise ContractStoreError("staging_invalid", str(entry))
        else:
            try:
                text = payload.decode("ascii")
                if not text.endswith("\n") or text.count("\n") != 1:
                    raise ContractStoreError("current_invalid", repr(text))
                identifier = text[:-1]
                generation_directory_name(identifier)
                _authenticate_revision_for_commit(
                    ref,
                    identifier,
                    trusted_publics=trusted_publics,
                    store_root=store_root,
                )
            except (ContractStoreError, UnicodeDecodeError) as exc:
                raise ContractStoreError(
                    "staging_invalid", f"{entry}: unauthenticated current"
                ) from exc
        recoverable.append(entry)
    return tuple(recoverable)


def _remove_staging_recovery_plan(
    contract_dir: Path,
    generations: Path,
    direct_files: tuple[Path, ...],
    staging_directories: tuple[tuple[Path, tuple[Path, ...]], ...],
) -> None:
    """Delete a fully validated recovery plan and persist directory entries."""
    try:
        for path in direct_files:
            path.unlink()
        for entry, children in staging_directories:
            for child in children:
                child.unlink()
            entry.rmdir()
    except OSError as exc:
        raise ContractStoreError("staging_recovery_failed", str(exc)) from exc
    if direct_files:
        _sync_directory(contract_dir)
    if staging_directories:
        _sync_directory(generations)


def _recover_contract_staging_locked(
    ref: ManifestRef,
    contract_dir: Path,
    generations: Path,
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path,
) -> None:
    """Recover all recognized crash staging while the writer lock is held.

    Both namespaces are validated before the first deletion.  A malformed
    sibling therefore fails closed without partially cleaning the contract.
    """
    direct_files = _direct_staging_recovery_plan(
        ref,
        contract_dir,
        trusted_publics=trusted_publics,
        store_root=store_root,
    )
    staging_directories = _staging_directory_recovery_plan(generations)
    _remove_staging_recovery_plan(
        contract_dir,
        generations,
        direct_files,
        staging_directories,
    )


def _recover_activation_staging(
    root: Path,
    expected_catalog: Mapping[ContractId, str],
    *,
    trusted_publics: tuple[TrustedPublic, ...],
) -> None:
    """Recover reserved staging for contracts in a quiescent cutover set."""
    plans: list[
        tuple[
            Path,
            Path,
            tuple[Path, ...],
            tuple[tuple[Path, tuple[Path, ...]], ...],
        ]
    ] = []
    for contract_id in sorted(expected_catalog, key=lambda item: item.value):
        ref = _activation_manifest_ref(contract_id)
        contract_dir = root / contract_storage_key(contract_id)
        _require_plain_directory(
            contract_dir, code="activation_contract_invalid",
        )
        generations = contract_dir / "generations"
        direct_files = _direct_staging_recovery_plan(
            ref,
            contract_dir,
            trusted_publics=trusted_publics,
            store_root=root,
        )
        staging_directories = _staging_directory_recovery_plan(generations)
        plans.append((
            contract_dir,
            generations,
            direct_files,
            staging_directories,
        ))

    # The complete cutover set is validated before any deletion.  Activation
    # cannot half-clean earlier contracts and then discover hostile debris in
    # a later one.
    for contract_dir, generations, direct_files, staging_directories in plans:
        _remove_staging_recovery_plan(
            contract_dir,
            generations,
            direct_files,
            staging_directories,
        )


@contextlib.contextmanager
def _activation_writer_locks(
    root: Path,
    expected_catalog: Mapping[ContractId, str],
) -> Iterator[None]:
    """Hold every cutover contract lock in canonical order.

    Activation has an external quiescence proof, but still uses the same
    filesystem exclusion primitive as ordinary writers.  Existing contract
    directories are required before acquiring a lock, so a malformed shadow
    cannot be repaired accidentally by lock setup.
    """
    contract_entries = tuple(
        (contract_id, root / contract_storage_key(contract_id))
        for contract_id in sorted(expected_catalog, key=lambda item: item.value)
    )
    for contract_id, contract_dir in contract_entries:
        _require_plain_directory(
            contract_dir, code="activation_contract_invalid",
        )
        _require_no_link_components(
            contract_dir, code="activation_contract_invalid",
        )
        binding = read_binding(contract_dir)
        if binding.contract_id != contract_id:
            raise ContractStoreError("binding_invalid", contract_id.value)
        lock_path = contract_dir / "writer.lock"
        _require_regular_file(lock_path, code="lock_file_invalid")
        try:
            if lock_path.stat().st_size != 1:
                raise ContractStoreError("lock_file_invalid", str(lock_path))
        except OSError as exc:
            raise ContractStoreError("lock_file_invalid", str(exc)) from exc
        _require_plain_directory(
            contract_dir / "generations",
            code="generations_directory_invalid",
        )
    with contextlib.ExitStack() as locks:
        for contract_id in sorted(expected_catalog, key=lambda item: item.value):
            locks.enter_context(_writer_lock(
                contract_id,
                store_root=root,
                timeout=DEFAULT_LOCK_TIMEOUT,
            ))
        yield


def _recover_and_verify_activation_catalog(
    root: Path,
    expected_catalog: Mapping[ContractId, str],
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    require_expected_current: bool = True,
) -> None:
    """Recover and authenticate one quiescent catalog under writer locks."""
    with _activation_writer_locks(root, expected_catalog):
        _recover_activation_staging(
            root,
            expected_catalog,
            trusted_publics=trusted_publics,
        )
        _verify_activation_catalog(
            root,
            expected_catalog,
            trusted_publics=trusted_publics,
            require_expected_current=require_expected_current,
        )


def _activation_manifest_ref(contract_id: ContractId) -> ManifestRef:
    """Reconstruct one structural reference from the versioned origin map."""
    from manifest_inventory import default_manifest_sources

    sources = tuple(
        source for source in default_manifest_sources()
        if source.origin is contract_id.origin
        and source.default_status is ManifestStatus.ADMITTED
    )
    if len(sources) != 1:
        raise ContractStoreError(
            "activation_origin_invalid", contract_id.origin.value,
        )
    source = sources[0]
    relative = Path(contract_id.relative_manifest)
    depth = len(relative.parts) - 1
    if depth < source.min_depth or (
        source.max_depth is not None and depth > source.max_depth
    ):
        raise ContractStoreError(
            "activation_contract_invalid", contract_id.value,
        )
    source_root = Path(source.root)
    manifest_path = source_root / relative
    allowed_code_roots = tuple(
        Path(item).resolve(strict=False) for item in source.allowed_code_roots
    )
    if source.skill_scoped:
        allowed_code_roots = (
            (source_root / relative.parts[0]).resolve(strict=False),
        )
    if not allowed_code_roots:
        allowed_code_roots = (manifest_path.parent.resolve(strict=False),)
    return ManifestRef(
        contract_id=contract_id,
        origin=contract_id.origin,
        status=ManifestStatus.ADMITTED,
        source_root=source_root,
        manifest_path=manifest_path,
        manifest_relative=contract_id.relative_manifest,
        allowed_code_roots=allowed_code_roots,
    )


def _canonical_activation_catalog(
    expected_catalog: Mapping[ContractId, str],
) -> dict[ContractId, str]:
    if not isinstance(expected_catalog, Mapping) or not expected_catalog:
        raise ContractStoreError("activation_catalog_invalid", "catalog is empty")
    canonical: dict[ContractId, str] = {}
    for contract_id, identifier in expected_catalog.items():
        if not isinstance(contract_id, ContractId):
            raise ContractStoreError(
                "activation_catalog_invalid", "contract ID has wrong type",
            )
        generation_directory_name(identifier)
        canonical[contract_id] = identifier
    return canonical


def _verify_pre_cutover_inventory(
    expected_catalog: Mapping[ContractId, str],
) -> None:
    """Require the caller's catalog to equal a freshly read legacy inventory."""
    from manifest_inventory import inventory_authoring_manifests

    inventory = inventory_authoring_manifests()
    if inventory.problems:
        problem = inventory.problems[0]
        raise ContractStoreError(
            "activation_inventory_invalid",
            f"{problem.code}:{problem.path}:{problem.detail}",
        )
    installed = {ref.contract_id for ref in inventory.installed()}
    supplied = set(expected_catalog)
    if installed != supplied:
        raise ContractStoreError(
            "activation_catalog_mismatch",
            f"inventory={sorted(item.value for item in installed)} "
            f"expected={sorted(item.value for item in supplied)}",
        )


def _verify_activation_container_shape(container: Path) -> Path:
    _require_plain_directory(container, code="activation_container_invalid")
    _require_no_link_components(container, code="activation_container_invalid")
    try:
        children = tuple(container.iterdir())
    except OSError as exc:
        raise ContractStoreError("activation_container_invalid", str(exc)) from exc
    if {child.name for child in children} != {STORE_RELATIVE.name}:
        raise ContractStoreError(
            "activation_container_invalid",
            f"expected only {STORE_RELATIVE.name}",
        )
    root = container / STORE_RELATIVE.name
    _require_plain_directory(root, code="activation_store_invalid")
    return root


def _verify_activation_catalog(
    root: Path,
    expected_catalog: Mapping[ContractId, str],
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    require_expected_current: bool = True,
) -> None:
    """Verify every binding and immutable revision before or after swap.

    Before the global move the prepared pointer must equal the migration
    report.  Once the production container exists, legitimate publications
    may have advanced it; marker recovery therefore verifies the current
    revision cryptographically without requiring the stale cutover ID.
    """
    _require_plain_directory(root, code="activation_store_invalid")
    _require_no_link_components(root, code="activation_store_invalid")
    expected_entries = {
        contract_storage_key(contract_id) for contract_id in expected_catalog
    }
    try:
        entries = tuple(root.iterdir())
    except OSError as exc:
        raise ContractStoreError("activation_store_invalid", str(exc)) from exc
    if {entry.name for entry in entries} != expected_entries:
        raise ContractStoreError(
            "activation_catalog_mismatch",
            f"expected={sorted(expected_entries)} "
            f"actual={sorted(entry.name for entry in entries)}",
        )

    for contract_id, expected_identifier in sorted(
        expected_catalog.items(), key=lambda item: item[0].value,
    ):
        ref = _activation_manifest_ref(contract_id)
        contract_dir = root / contract_storage_key(contract_id)
        _require_plain_directory(
            contract_dir, code="activation_contract_invalid",
        )
        try:
            contract_entries = tuple(contract_dir.iterdir())
        except OSError as exc:
            raise ContractStoreError(
                "activation_contract_invalid", str(exc),
            ) from exc
        contract_names = {entry.name for entry in contract_entries}
        required_contract_names = {
            BINDING_FILE, "writer.lock", "current", "generations",
        }
        if (
            not required_contract_names.issubset(contract_names)
            or contract_names - required_contract_names != (
                {"admission-receipts"}
                if "admission-receipts" in contract_names
                else set()
            )
        ):
            raise ContractStoreError(
                "activation_contract_invalid", str(contract_dir),
            )
        binding = read_binding(contract_dir)
        if binding.contract_id != contract_id:
            raise ContractStoreError("binding_invalid", contract_id.value)
        lock_path = contract_dir / "writer.lock"
        _require_regular_file(lock_path, code="lock_file_invalid")
        try:
            if lock_path.stat().st_size != 1:
                raise ContractStoreError("lock_file_invalid", str(lock_path))
        except OSError as exc:
            raise ContractStoreError("lock_file_invalid", str(exc)) from exc
        current = _read_current_optional(contract_dir)
        if current is None:
            raise ContractStoreError(
                "current_missing", contract_id.value,
            )
        if require_expected_current and current != expected_identifier:
            raise ContractStoreError(
                "activation_catalog_mismatch",
                f"{contract_id.value}: expected={expected_identifier} current={current}",
            )
        generations = contract_dir / "generations"
        _require_plain_directory(
            generations, code="generations_directory_invalid",
        )
        try:
            generation_entries = tuple(generations.iterdir())
        except OSError as exc:
            raise ContractStoreError(
                "generations_directory_invalid", str(exc),
            ) from exc
        if not generation_entries:
            raise ContractStoreError(
                "activation_generation_missing", contract_id.value,
            )
        _load_revision(
            ref,
            current,
            trusted_publics=trusted_publics,
            store_root=root,
        )
        for revision in generation_entries:
            if (
                _is_link_like(revision)
                or not revision.is_dir()
                or _PHYSICAL_ID_RE.fullmatch(revision.name) is None
            ):
                raise ContractStoreError(
                    "activation_generation_invalid", str(revision),
                )
            identifier = "sha256:" + revision.name
            if _revision_kind(revision) == "retirement":
                _load_retirement(
                    ref,
                    identifier,
                    trusted_publics=trusted_publics,
                    store_root=root,
                )
            else:
                _load_generation_for_commit(
                    ref,
                    identifier,
                    trusted_publics=trusted_publics,
                    store_root=root,
                )


def _canonical_activation_shadow(shadow_root: Path | str) -> tuple[Path, Path]:
    root = Path(os.path.abspath(shadow_root))
    _container, _production_root, _marker = _production_paths()
    expected_parent = Path(os.path.abspath(_C.PATH_USER_STATE / SHADOW_RELATIVE))
    if root.name != STORE_RELATIVE.name or root.parent.parent != expected_parent:
        raise ContractStoreError("activation_shadow_invalid", str(root))
    return root, root.parent


def _write_activation_marker(marker: Path) -> None:
    if marker.exists() or _is_link_like(marker):
        if _is_link_like(marker) or _read_regular_file(
            marker, code="active_marker_invalid",
        ) != ACTIVE_BYTES:
            raise ContractStoreError("active_marker_invalid", str(marker))
        return
    _ensure_directory_chain(marker.parent, code="active_marker_directory_invalid")
    temporary = marker.with_name(
        f".{marker.name}.{os.getpid()}.{threading.get_ident()}."
        f"{time.monotonic_ns()}.tmp"
    )
    try:
        _write_new_file(temporary, ACTIVE_BYTES)
        try:
            _rename_no_replace(temporary, marker)
        except FileExistsError:
            if _read_regular_file(
                marker, code="active_marker_invalid",
            ) != ACTIVE_BYTES:
                raise ContractStoreError("active_marker_invalid", str(marker))
        _sync_directory(marker.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _move_activation_container(source: Path, destination: Path) -> None:
    if destination.exists() or _is_link_like(destination):
        raise ContractStoreError("production_store_exists", str(destination))
    _rename_no_replace(source, destination)
    _sync_directory(destination.parent)


def _activate_store_locked(
    expected: Mapping[ContractId, str],
    *,
    shadow_v1: Path,
    shadow_container: Path,
    trusted: tuple[TrustedPublic, ...],
    production_container: Path,
    production_root: Path,
    marker: Path,
    mode: ProductionStoreMode,
) -> None:
    """Perform activation while the required catalog locks are held."""
    if mode is ProductionStoreMode.LEGACY:
        _verify_pre_cutover_inventory(expected)

    if mode in {ProductionStoreMode.ACTIVE, ProductionStoreMode.STORE_ONLY}:
        root = _verify_activation_container_shape(production_container)
        _recover_and_verify_activation_catalog(
            root,
            expected,
            trusted_publics=trusted,
            require_expected_current=False,
        )
        if mode is ProductionStoreMode.STORE_ONLY:
            _write_activation_marker(marker)
        return

    _require_plain_directory(shadow_container, code="activation_shadow_invalid")
    _require_no_link_components(shadow_container, code="activation_shadow_invalid")
    shadow_root_verified = _verify_activation_container_shape(shadow_container)
    if shadow_root_verified != shadow_v1:
        raise ContractStoreError("activation_shadow_invalid", str(shadow_v1))
    _recover_and_verify_activation_catalog(
        shadow_v1,
        expected,
        trusted_publics=trusted,
    )
    try:
        if shadow_container.stat().st_dev != marker.parent.stat().st_dev:
            raise ContractStoreError("activation_cross_device", str(shadow_container))
    except OSError as exc:
        raise ContractStoreError("activation_shadow_invalid", str(exc)) from exc

    if mode is ProductionStoreMode.LEGACY:
        _write_activation_marker(marker)
    # RECOVERY_REQUIRED already has the durable marker.  In both cases it must
    # precede the single global directory move.
    _move_activation_container(shadow_container, production_container)
    if production_store_mode() is not ProductionStoreMode.ACTIVE:
        raise ContractStoreError("activation_incomplete")
    root = _verify_activation_container_shape(production_container)
    if root != production_root:
        raise ContractStoreError("activation_store_invalid", str(root))
    _recover_and_verify_activation_catalog(
        root,
        expected,
        trusted_publics=trusted,
    )


def activate_store(
    expected_catalog: Mapping[ContractId, str],
    *,
    shadow_root: Path,
    trusted_publics: Iterable[TrustedPublic],
    quiescence_guard: QuiescenceProof | None = None,
) -> None:
    """Cross the irreversible shadow-to-production boundary.

    ``quiescence_guard`` is the injected maintenance authority.  Returning
    exactly ``True`` attests that ingress, scheduler, publishers, reload and
    watchers are blocked.  The managed caller retains catalog and lifecycle
    exclusion through its first verified store-only load.  Services remain
    stopped at least through that proof.  Their later controlled start and
    readiness checks use normal systemd semantics; this filesystem boundary
    neither spans that operation nor claims a target-wide maintenance gate
    while units are starting.  Service-specific probing deliberately stays
    outside this filesystem core.

    This boundary also owns its filesystem exclusion: production is always
    locked first and, while a shadow can still be consumed, shadow second.
    The locks are reentrant so the managed cutover guard can retain the same
    production exclusion through the first cold store-only load.
    """
    if sys.platform != "linux":
        raise ContractStoreError(
            "cutover_platform_unsupported",
            "the managed Metnos server and its cutover require Linux/systemd",
        )
    if not callable(quiescence_guard):
        raise ContractStoreError("activation_not_quiescent", "proof is required")

    expected = _canonical_activation_catalog(expected_catalog)
    trusted = _trusted_public_tuple(trusted_publics)
    shadow_v1, shadow_container = _canonical_activation_shadow(shadow_root)
    production_container, production_root, marker = _production_paths()

    with catalog_admission_lock(store_root=production_root):
        try:
            quiescent = quiescence_guard()
        except Exception as exc:
            raise ContractStoreError(
                "activation_not_quiescent", str(exc),
            ) from exc
        if quiescent is not True:
            raise ContractStoreError(
                "activation_not_quiescent", "proof was false",
            )
        mode = production_store_mode()
        if mode in {
            ProductionStoreMode.LEGACY,
            ProductionStoreMode.RECOVERY_REQUIRED,
        }:
            with catalog_admission_lock(store_root=shadow_v1):
                _activate_store_locked(
                    expected,
                    shadow_v1=shadow_v1,
                    shadow_container=shadow_container,
                    trusted=trusted,
                    production_container=production_container,
                    production_root=production_root,
                    marker=marker,
                    mode=mode,
                )
            return
        _activate_store_locked(
            expected,
            shadow_v1=shadow_v1,
            shadow_container=shadow_container,
            trusted=trusted,
            production_container=production_container,
            production_root=production_root,
            marker=marker,
            mode=mode,
        )


def _validate_initial_history(generations: Path, *, desired_identifier: str) -> None:
    """Reject a missing pointer unless it is the one recoverable rename crash.

    The sole admissible pre-pointer history is the exact desired final
    directory.  It is verified by ``_install_generation`` immediately after
    this check.  Every other generation, staging directory or unknown entry
    means the store is not virgin and must be repaired explicitly.
    """
    try:
        entries = tuple(generations.iterdir())
    except OSError as exc:
        raise ContractStoreError("generations_directory_invalid", str(exc)) from exc
    authoritative = tuple(
        entry for entry in entries if not entry.name.startswith(".generation-")
    )
    if not authoritative:
        return
    desired_name = generation_directory_name(desired_identifier)
    if len(authoritative) == 1 and authoritative[0].name == desired_name:
        return
    raise ContractStoreError(
        "current_missing_with_history",
        ",".join(sorted(entry.name for entry in entries)),
    )


def _snapshot_payloads(snapshot: VerifiedManifest) -> dict[str, bytes]:
    return {
        "manifest.toml": snapshot.manifest_bytes,
        "manifest.toml.sig": snapshot.signature_bytes,
        "manifest.lang_state.json": snapshot.language_state_bytes,
    }


def _require_registry_reconciler(
    productive: bool,
    reconciler: RegistryReconciler | None,
) -> None:
    """Require the RM-0005 integration at every productive publication.

    The callback receives one authenticated manifest or retirement revision.
    For a manifest it preserves rows only when the source and target evidence
    still match; for a retirement it invalidates every workflow row for the
    ContractId.  The store calls it outside the writer lock and follows any
    concurrent pointer change until the reconciled revision is stable.
    Exceptions propagate so an idempotent retry can repair a committed update.
    """
    if productive and not callable(reconciler):
        raise ContractStoreError("registry_reconciler_required")


def _reconcile_authoring_locked(
    ref: ManifestRef,
    payloads: Mapping[str, bytes],
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    replace_timeout: float,
) -> None:
    """Repair the three authoring files from authoritative current payloads."""
    _validate_manifest_ref(ref)
    _require_plain_directory(ref.manifest_dir, code="source_directory_invalid")
    _require_no_link_components(ref.manifest_dir, code="source_directory_invalid")
    for name in GENERATION_FILES:
        destination = ref.manifest_dir / name
        if _is_link_like(destination):
            raise ContractStoreError("source_file_invalid", str(destination))
        existing: bytes | None = None
        mode = 0o644
        if destination.exists():
            _require_regular_file(destination, code="source_file_invalid")
            existing = _read_regular_file(destination, code="source_file_invalid")
            try:
                mode = stat.S_IMODE(destination.stat().st_mode)
            except OSError as exc:
                raise ContractStoreError("source_file_invalid", str(exc)) from exc
        if existing != payloads[name]:
            _atomic_replace_file(
                destination,
                payloads[name],
                replace_timeout=replace_timeout,
                mode=mode,
            )
    mirrored = {
        name: _read_regular_file(
            ref.manifest_dir / name, code="source_file_invalid",
        )
        for name in GENERATION_FILES
    }
    if mirrored != dict(payloads):
        raise ContractStoreError("authoring_reconciliation_failed")
    _verify_payloads(
        ref,
        mirrored,
        trusted_publics=trusted_publics,
        identifier=None,
        require_inventory_hash=False,
    )


def _publication_base_locked(
    ref: ManifestRef,
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path,
    technical_base: bool,
) -> tuple[Path, Path, str | None, dict[str, bytes] | None]:
    """Load one authenticated CAS base while the sole writer lock is held."""
    contract_dir, generations = _ensure_store_directories(
        ref.contract_id,
        store_root=store_root,
    )
    _recover_contract_staging_locked(
        ref,
        contract_dir,
        generations,
        trusted_publics=trusted_publics,
        store_root=store_root,
    )
    _ensure_binding_locked(contract_dir, ref.contract_id)
    previous = _read_current_optional(contract_dir)
    current_payloads: dict[str, bytes] | None = None
    if previous is not None:
        if technical_base:
            current_payloads = _load_generation_for_commit(
                ref,
                previous,
                trusted_publics=trusted_publics,
                store_root=store_root,
            )
        else:
            current_payloads = _snapshot_payloads(_load_generation(
                ref,
                previous,
                trusted_publics=trusted_publics,
                store_root=store_root,
            ))
    return contract_dir, generations, previous, current_payloads


def _technical_expected_base_locked(
    ref: ManifestRef,
    *,
    expected_generation_id: str | None,
    previous_generation_id: str | None,
    current_payloads: Mapping[str, bytes] | None,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path,
) -> Mapping[str, bytes] | None:
    """Authenticate the CAS generation against which a draft was prepared."""
    if expected_generation_id is None:
        return None
    if previous_generation_id == expected_generation_id:
        if current_payloads is None:
            raise ContractStoreError("current_missing")
        return current_payloads
    return _load_generation_for_commit(
        ref,
        expected_generation_id,
        trusted_publics=trusted_publics,
        store_root=store_root,
    )


def _technical_authoring_base_locked(
    ref: ManifestRef,
    draft: TechnicalDraft,
    *,
    expected_generation_id: str | None,
    current_payloads: Mapping[str, bytes] | None,
    generations: Path,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path,
) -> Mapping[str, bytes] | None:
    """Resolve the signed generation from which the authoring draft diverged.

    M3 deliberately does not mirror a linguistic publication back into the
    authoring directory.  Its signature therefore remains a compact,
    authenticated lineage pointer to the last authoring generation while the
    live generation can contain newer translations.  The lookup is structural
    and content-verified; directory timestamps and authoring paths never choose
    a generation.
    """
    signature_hash = _canonical_optional_sha256(
        draft.authoring_signature_hash,
        field="authoring_signature_hash",
    )
    if current_payloads is None or (
        expected_generation_id is None and signature_hash is None
    ):
        if signature_hash is not None:
            raise ContractStoreError(
                "technical_ancestor_invalid",
                "new contract unexpectedly has an authoring signature",
            )
        return None
    if signature_hash is None:
        raise ContractStoreError(
            "technical_ancestor_missing", "existing contract has no signature",
        )
    if _sha256(current_payloads["manifest.toml.sig"]) == signature_hash:
        return current_payloads

    matches: list[Mapping[str, bytes]] = []
    try:
        entries = tuple(generations.iterdir())
    except OSError as exc:
        raise ContractStoreError("generations_directory_invalid", str(exc)) from exc
    for entry in entries:
        if entry.name.startswith(".generation-"):
            continue
        if not entry.is_dir() or _PHYSICAL_ID_RE.fullmatch(entry.name) is None:
            continue
        signature_path = entry / "manifest.toml.sig"
        try:
            signature_bytes = _read_regular_file(
                signature_path, code="generation_file_invalid",
            )
        except ContractStoreError:
            continue
        if _sha256(signature_bytes) != signature_hash:
            continue
        identifier = "sha256:" + entry.name
        payloads = _load_generation_for_commit(
            ref,
            identifier,
            trusted_publics=trusted_publics,
            store_root=store_root,
        )
        if _sha256(payloads["manifest.toml.sig"]) == signature_hash:
            matches.append(payloads)
    if not matches:
        raise ContractStoreError(
            "technical_ancestor_missing", str(ref.contract_id),
        )
    distinct = {
        generation_id(payloads): payloads
        for payloads in matches
    }
    if len(distinct) != 1:
        raise ContractStoreError(
            "technical_ancestor_ambiguous", str(ref.contract_id),
        )
    return next(iter(distinct.values()))


def _commit_payloads_locked(
    ref: ManifestRef,
    payloads: Mapping[str, bytes],
    *,
    contract_dir: Path,
    generations: Path,
    previous: str | None,
    current_payloads: Mapping[str, bytes] | None,
    expected_generation_id: str | None,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path,
    replace_timeout: float,
    precommit: Callable[[str], None] | None = None,
    birth_authorization: BirthCommitAuthorization | None = None,
) -> tuple[str, bool]:
    """Verify and commit one complete postcondition under the writer lock."""
    candidate = _verify_payloads(
        ref,
        payloads,
        trusted_publics=trusted_publics,
        identifier=None,
        require_inventory_hash=False,
    )
    canonical_payloads = _snapshot_payloads(candidate)
    desired = generation_id(canonical_payloads)
    if previous is None:
        if expected_generation_id is not None:
            raise ContractStoreError(
                "commit_conflict",
                f"expected={expected_generation_id} current=None",
            )
        _validate_initial_history(generations, desired_identifier=desired)

    repeated = False
    if previous != expected_generation_id:
        if (
            current_payloads is not None
            and previous == desired
            and dict(current_payloads) == canonical_payloads
        ):
            repeated = True
        else:
            raise ContractStoreError(
                "commit_conflict",
                f"expected={expected_generation_id} current={previous}",
            )
    elif (
        current_payloads is not None
        and previous == desired
        and dict(current_payloads) == canonical_payloads
    ):
        repeated = True

    if birth_authorization is not None:
        _persist_birth_receipt_locked(
            ref,
            desired,
            canonical_payloads,
            previous=(expected_generation_id if repeated else previous),
            contract_dir=contract_dir,
            authorization=birth_authorization,
            replace_timeout=replace_timeout,
        )
    if not repeated:
        if precommit is not None:
            precommit(desired)
            # The audit hook is application code.  Re-authenticate the exact
            # postcondition, including its live code digest, after it returns
            # so a faulty sink cannot make an already-invalid generation
            # current.  The event is an authorization record, not a claim that
            # the later filesystem commit succeeded.
            _verify_payloads(
                ref,
                canonical_payloads,
                trusted_publics=trusted_publics,
                identifier=desired,
                require_inventory_hash=False,
            )
        _install_generation(
            ref,
            canonical_payloads,
            identifier=desired,
            trusted_publics=trusted_publics,
            store_root=store_root,
        )
        _write_current(
            contract_dir,
            desired,
            replace_timeout=replace_timeout,
        )
    return desired, repeated


def _birth_receipt_path(contract_dir: Path, generation_identifier: str) -> Path:
    return contract_dir / "admission-receipts" / (
        generation_directory_name(generation_identifier) + ".json"
    )


def _validate_birth_receipt_binding(
    receipt: object,
    *,
    ref: ManifestRef,
    generation_identifier: str,
    previous: str | None,
    authorization: BirthCommitAuthorization,
    request_id: str | None = None,
    journal_hash: str | None = None,
) -> None:
    expected = {
        "contract_id": ref.contract_id.value,
        "generation_id": generation_identifier,
        "candidate_id": authorization.candidate_id,
        "semantic_core_id": authorization.semantic_core_id,
        "admission_context_id": authorization.admission_context_id,
        "predecessor_id": previous,
    }
    if authorization.predecessor_id != previous:
        raise ContractStoreError(
            "commit_conflict",
            f"authorized predecessor={authorization.predecessor_id} current={previous}",
        )
    for field, wanted in expected.items():
        if getattr(receipt, field, object()) != wanted:
            raise ContractStoreError("birth_receipt_binding_invalid", field)
    if request_id is not None or journal_hash is not None:
        _canonical_sha256(request_id, field="request_id")
        _canonical_sha256(journal_hash, field="journal_hash")
        checks = getattr(receipt, "check_results", None)
        check = checks.get("authoring_install_journal_v1") if isinstance(checks, Mapping) else None
        if (
            getattr(receipt, "birth_request_id", None) != request_id
            or getattr(receipt, "authoring_journal_hash", None) != journal_hash
            or
            check is None
            or getattr(check, "rule_version", None) != "1"
            or getattr(getattr(check, "status", None), "value", None) != "passed"
            or getattr(check, "evidence_hash", None) != journal_hash
        ):
            raise ContractStoreError(
                "birth_receipt_binding_invalid", "authoring_install_journal_v1",
            )


def _persist_birth_receipt_locked(
    ref: ManifestRef,
    generation_identifier: str,
    payloads: Mapping[str, bytes],
    *,
    previous: str | None,
    contract_dir: Path,
    authorization: BirthCommitAuthorization,
    replace_timeout: float,
    request_id: str | None = None,
    journal_hash: str | None = None,
) -> bytes:
    """Authenticate, durably store and exactly reread AdmissionReceipt.

    This runs while both catalog and per-contract writer locks are held and
    before generation installation or pointer replacement.  A crash can leave
    an orphan receipt, which is harmless: an exact retry validates and reuses
    it, while any byte or binding mismatch fails closed.
    """
    if not isinstance(authorization, BirthCommitAuthorization):
        raise ContractStoreError("birth_authorization_invalid")
    if not callable(authorization.issuer) or not callable(authorization.verifier):
        raise ContractStoreError("birth_authorization_invalid")
    for field in (
        "candidate_id", "semantic_core_id", "admission_context_id",
    ):
        _canonical_sha256(getattr(authorization, field), field=field)
    if authorization.predecessor_id is not None:
        generation_directory_name(authorization.predecessor_id)

    digests = MappingProxyType({
        name: _sha256(payloads[name]) for name in GENERATION_FILES
    })
    receipt_path = _birth_receipt_path(contract_dir, generation_identifier)
    receipt_dir = receipt_path.parent
    if receipt_dir.exists():
        _require_plain_directory(receipt_dir, code="birth_receipt_store_invalid")
        _require_no_link_components(receipt_dir, code="birth_receipt_store_invalid")
    else:
        try:
            receipt_dir.mkdir(mode=0o700)
            _sync_directory(receipt_dir.parent)
        except OSError as exc:
            raise ContractStoreError("birth_receipt_store_invalid", str(exc)) from exc

    if receipt_path.exists():
        encoded = _read_regular_file(receipt_path, code="birth_receipt_invalid")
    else:
        try:
            if request_id is None or journal_hash is None:
                raise ContractStoreError("birth_authorization_invalid", "journal binding")
            encoded = authorization.issuer(
                generation_identifier, digests, request_id, journal_hash,
            )
        except ContractStoreError:
            raise
        except Exception as exc:
            raise ContractStoreError("birth_receipt_issue_failed", str(exc)) from exc
        if not isinstance(encoded, bytes) or not encoded:
            raise ContractStoreError("birth_receipt_invalid", "empty wire receipt")
        try:
            receipt = authorization.verifier(encoded)
        except Exception as exc:
            raise ContractStoreError("birth_receipt_invalid", str(exc)) from exc
        _validate_birth_receipt_binding(
            receipt, ref=ref, generation_identifier=generation_identifier,
            previous=previous, authorization=authorization,
            request_id=request_id, journal_hash=journal_hash,
        )
        _atomic_replace_file(
            receipt_path, encoded, replace_timeout=replace_timeout, mode=0o600,
        )

    reread = _read_regular_file(receipt_path, code="birth_receipt_invalid")
    if reread != encoded:
        raise ContractStoreError("birth_receipt_reread_mismatch")
    try:
        receipt = authorization.verifier(reread)
    except Exception as exc:
        raise ContractStoreError("birth_receipt_invalid", str(exc)) from exc
    _validate_birth_receipt_binding(
        receipt, ref=ref, generation_identifier=generation_identifier,
        previous=previous, authorization=authorization,
        request_id=request_id, journal_hash=journal_hash,
    )
    return reread


def _verify_published_postcondition(
    ref: ManifestRef,
    payloads: Mapping[str, bytes],
    *,
    desired: str,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path,
    registry_reconciler: RegistryReconciler | None = None,
) -> VerifiedManifest:
    observed = current_contract(
        ref, trusted_publics=trusted_publics, store_root=store_root,
    )
    fresh = _reconcile_stable_current(
        ref,
        observed,
        trusted_publics=trusted_publics,
        store_root=store_root,
        registry_reconciler=registry_reconciler,
    )
    if (
        not isinstance(fresh, VerifiedManifest)
        or fresh.generation_id != desired
        or _snapshot_payloads(fresh) != dict(payloads)
    ):
        current_id = contract_revision_id(fresh)
        raise ContractStoreError(
            "publication_superseded",
            f"desired={desired} current={current_id}",
        )
    return fresh


def contract_revision_id(revision: ContractRevision) -> str:
    """Return the logical ID shared by the two immutable revision types."""
    if isinstance(revision, VerifiedManifest):
        if revision.generation_id is None:
            raise ContractStoreError("revision_id_missing")
        return revision.generation_id
    if isinstance(revision, ContractRetirement):
        return revision.retirement_id
    raise ContractStoreError("revision_type_invalid", type(revision).__name__)


def _reconcile_stable_current(
    ref: ManifestRef,
    initial: ContractRevision,
    *,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path,
    registry_reconciler: RegistryReconciler | None,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> ContractRevision:
    """Reconcile outside the writer lock until the authenticated pointer settles.

    A callback for revision A may finish after a concurrent publisher has
    already reconciled revision B.  Re-reading after every callback and then
    applying the same type-complete callback to B repairs that inverted
    completion order.  Continuous churn is bounded by a monotonic deadline;
    the committed filesystem state remains authoritative and a retry resumes
    reconciliation.
    """
    if registry_reconciler is None:
        return initial
    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    deadline = time.monotonic() + timeout
    candidate = initial
    while True:
        registry_reconciler(candidate)
        fresh = current_contract(
            ref,
            trusted_publics=trusted_publics,
            store_root=store_root,
        )
        if contract_revision_id(fresh) == contract_revision_id(candidate):
            return fresh
        if time.monotonic() >= deadline:
            raise ContractStoreError(
                "registry_reconciliation_unstable",
                f"observed={contract_revision_id(candidate)} "
                f"current={contract_revision_id(fresh)}",
            )
        candidate = fresh


def _verify_retirement_postcondition(
    ref: ManifestRef,
    payloads: Mapping[str, bytes],
    *,
    desired: str,
    trusted_publics: tuple[TrustedPublic, ...],
    store_root: Path,
    registry_reconciler: RegistryReconciler | None = None,
) -> ContractRetirement:
    observed = current_contract(
        ref, trusted_publics=trusted_publics, store_root=store_root,
    )
    fresh = _reconcile_stable_current(
        ref,
        observed,
        trusted_publics=trusted_publics,
        store_root=store_root,
        registry_reconciler=registry_reconciler,
    )
    if not isinstance(fresh, ContractRetirement):
        raise ContractStoreError(
            "retirement_superseded",
            f"desired={desired} current={fresh.generation_id}",
        )
    if (
        fresh.retirement_id != desired
        or fresh.payload_bytes != payloads["retirement.json"]
        or fresh.signature_bytes != payloads["retirement.json.sig"]
    ):
        raise ContractStoreError(
            "retirement_superseded",
            f"desired={desired} current={fresh.retirement_id}",
        )
    return fresh


def _record_surface_removal_locked(
    removal: SurfaceRemoval | None,
    audit_sink: Callable[[Mapping[str, object]], None] | None,
    *,
    ref: ManifestRef,
    operation: str,
    expected_generation_id: str | None,
    candidate_generation_id: str,
) -> None:
    if removal is None:
        return
    _validated_removal(removal)
    if not callable(audit_sink):
        raise ContractStoreError("surface_removal_audit_required")
    audit_sink(_auditable_event({
        "event": "contract_surface_removal_authorized",
        "operation": operation,
        "contract_id": ref.contract_id.value,
        "expected_generation_id": expected_generation_id,
        "candidate_generation_id": candidate_generation_id,
        "selectors": removal.selectors,
        "diff": {"removed_selectors": removal.selectors},
        "actor": removal.actor.strip(),
        "reason": removal.reason.strip(),
    }))


def _signed_candidate_payloads(
    ref: ManifestRef,
    *,
    manifest_bytes: bytes,
    language_state_bytes: bytes,
    private_key: Ed25519PrivateKey,
    trusted_publics: tuple[TrustedPublic, ...],
) -> dict[str, bytes]:
    signature_bytes = sign_manifest_bytes(
        manifest_bytes,
        private_key=private_key,
    )
    payloads = {
        "manifest.toml": manifest_bytes,
        "manifest.toml.sig": signature_bytes,
        "manifest.lang_state.json": language_state_bytes,
    }
    # Immediate verification makes signing-key/trust misconfiguration a
    # pre-commit error and applies the real standard to the prepared bytes.
    _verify_payloads(
        ref,
        payloads,
        trusted_publics=trusted_publics,
        identifier=None,
        require_inventory_hash=False,
    )
    return payloads


def _prepare_localization_payloads_locked(
    ref: ManifestRef,
    base: VerifiedManifest,
    *,
    source_language: str,
    target_language: str,
    patches: tuple[LocalizationPatch, ...],
    private_key: Ed25519PrivateKey,
    trusted_publics: tuple[TrustedPublic, ...],
) -> dict[str, bytes]:
    if not isinstance(patches, tuple) or not patches:
        raise ContractStoreError("localization_patches_invalid", "non-empty tuple required")
    source = _canonical_language(source_language, field="source_language")
    target = _canonical_language(target_language, field="target_language")
    if source == target:
        raise ContractStoreError("localization_languages_invalid", source)

    document = _editable_manifest(base.manifest_bytes)
    try:
        base_parsed = tomllib.loads(base.manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ContractStoreError("manifest_toml", str(exc)) from exc
    expected = copy.deepcopy(base_parsed)
    base_tables = _linguistic_tables(base_parsed)
    seen: set[str] = set()

    if any(not isinstance(patch, LocalizationPatch) for patch in patches):
        raise ContractStoreError("localization_patches_invalid", "wrong patch type")
    for patch in sorted(patches, key=lambda item: item.selector):
        selector = patch.selector
        if selector in seen:
            raise ContractStoreError("localization_patch_duplicate", selector)
        seen.add(selector)
        table = base_tables.get(selector)
        if table is None:
            raise ContractStoreError("localization_selector_missing", selector)
        source_text = table.get(source)
        if source_text is None:
            raise ContractStoreError(
                "localization_source_missing", f"{selector}:{source}",
            )
        _canonical_sha256(patch.source_hash, field=f"{selector}.source_hash")
        if patch.source_hash != _sha256(source_text.encode("utf-8")):
            raise ContractStoreError("localization_source_changed", selector)

        previous_text = table.get(target)
        expected_previous_hash = (
            None if previous_text is None else _sha256(previous_text.encode("utf-8"))
        )
        _canonical_optional_sha256(
            patch.previous_target_hash,
            field=f"{selector}.previous_target_hash",
        )
        if patch.previous_target_hash != expected_previous_hash:
            raise ContractStoreError("localization_target_changed", selector)
        if not isinstance(patch.candidate_text, str) or not patch.candidate_text.strip():
            raise ContractStoreError("localization_candidate_invalid", selector)
        _canonical_sha256(patch.candidate_hash, field=f"{selector}.candidate_hash")
        if patch.candidate_hash != _sha256(patch.candidate_text.encode("utf-8")):
            raise ContractStoreError("localization_candidate_hash", selector)

        editable_table = _selector_table(document, selector)
        expected_table = _selector_table(expected, selector)
        editable_table[target] = patch.candidate_text  # type: ignore[index]
        expected_table[target] = patch.candidate_text  # type: ignore[index]

    manifest_bytes, candidate_parsed = _manifest_from_document(document)
    if candidate_parsed != expected:
        raise ContractStoreError("localization_technical_change")
    if _code_digest(ref, candidate_parsed) != base.verified_code_digest:
        raise ContractStoreError("localization_code_changed")
    _validate_linguistic_candidate(candidate_parsed)

    try:
        state = json.loads(base.language_state_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractStoreError("language_state_json", str(exc)) from exc
    state_selectors = state.get("selectors") if isinstance(state, dict) else None
    if not isinstance(state_selectors, dict):
        raise ContractStoreError("language_state_selectors")
    for patch in patches:
        state_selectors[patch.selector][target] = {
            "version_hash": patch.candidate_hash,
            "source_lang": source,
            "source_hash": patch.source_hash,
        }
    try:
        state_bytes = encode_language_state(state, manifest=candidate_parsed)
    except LanguageStateError as exc:
        raise ContractStoreError(exc.code, exc.detail) from exc
    return _signed_candidate_payloads(
        ref,
        manifest_bytes=manifest_bytes,
        language_state_bytes=state_bytes,
        private_key=private_key,
        trusted_publics=trusted_publics,
    )


def _decoded_generation_language_state(
    payloads: Mapping[str, bytes],
) -> tuple[dict[str, Any], Mapping[str, object]]:
    try:
        manifest = tomllib.loads(payloads["manifest.toml"].decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ContractStoreError("generation_invalid", str(exc)) from exc
    try:
        state = decode_language_state(
            payloads["manifest.lang_state.json"], manifest=manifest,
        )
    except LanguageStateError as exc:
        raise ContractStoreError(exc.code, exc.detail) from exc
    return manifest, state


def _rebase_current_localizations(
    document: Any,
    proposed: Mapping[str, Any],
    proposed_state: dict[str, Any],
    *,
    authoring_base_payloads: Mapping[str, bytes] | None,
    current_base_payloads: Mapping[str, bytes] | None,
) -> None:
    """Carry live translations across a technical authoring update.

    The technical diff is authored from the last signed authoring generation,
    which can legitimately lag the live generation in M3.  Existing prose may
    therefore equal either that ancestor or the live value, but no third value
    is accepted.  The live text and provenance are then overlaid before the
    ordinary policy validates the final candidate against current.
    """
    if current_base_payloads is None:
        if authoring_base_payloads is not None:
            raise ContractStoreError("technical_ancestor_invalid")
        return
    if authoring_base_payloads is None:
        raise ContractStoreError("technical_ancestor_missing")

    ancestor, ancestor_state = _decoded_generation_language_state(
        authoring_base_payloads,
    )
    current, current_state = _decoded_generation_language_state(
        current_base_payloads,
    )
    ancestor_tables = _linguistic_tables(ancestor)
    current_tables = _linguistic_tables(current)
    proposed_tables = _linguistic_tables(proposed)
    proposed_selectors = proposed_state.get("selectors")
    ancestor_selectors = ancestor_state.get("selectors")
    current_selectors = current_state.get("selectors")
    if not all(isinstance(value, Mapping) for value in (
        proposed_selectors, ancestor_selectors, current_selectors,
    )):
        raise ContractStoreError("language_state_selectors")

    for selector, current_languages in current_tables.items():
        proposed_languages = proposed_tables.get(selector)
        if proposed_languages is None:
            # A real schema removal is checked, authorized and audited by the
            # final technical policy; there is nothing to rebase into it.
            continue
        editable_languages = _selector_table(document, selector)
        proposed_state_languages = proposed_selectors.get(selector)  # type: ignore[union-attr]
        current_state_languages = current_selectors.get(selector)  # type: ignore[union-attr]
        ancestor_languages = ancestor_tables.get(selector, {})
        ancestor_state_languages = ancestor_selectors.get(selector, {})  # type: ignore[union-attr]
        if not isinstance(proposed_state_languages, dict) or not isinstance(
            current_state_languages, Mapping,
        ) or not isinstance(ancestor_state_languages, Mapping):
            raise ContractStoreError("language_state_selectors", selector)

        for language, current_text in current_languages.items():
            proposed_text = proposed_languages.get(language)
            ancestor_text = ancestor_languages.get(language)
            current_entry = current_state_languages.get(language)
            proposed_entry = proposed_state_languages.get(language)
            ancestor_entry = ancestor_state_languages.get(language)
            if not isinstance(current_entry, Mapping):
                raise ContractStoreError(
                    "language_state_entry", f"{selector}:{language}",
                )
            if proposed_text is not None:
                matches_current = (
                    proposed_text == current_text
                    and proposed_entry == current_entry
                )
                matches_ancestor = (
                    ancestor_text is not None
                    and proposed_text == ancestor_text
                    and proposed_entry == ancestor_entry
                )
                if not matches_current and not matches_ancestor:
                    raise ContractStoreError(
                        "existing_localization_changed",
                        f"{selector}:{language}",
                    )
            editable_languages[language] = current_text  # type: ignore[index]
            proposed_state_languages[language] = copy.deepcopy(dict(current_entry))


def _prepare_technical_payloads_locked(
    ref: ManifestRef,
    base_payloads: Mapping[str, bytes] | None,
    authoring_base_payloads: Mapping[str, bytes] | None,
    visible_payloads: Mapping[str, bytes] | None,
    *,
    draft: TechnicalDraft,
    private_key: Ed25519PrivateKey,
    trusted_publics: tuple[TrustedPublic, ...],
    removal: SurfaceRemoval | None,
    require_authoring_signature: bool = True,
) -> dict[str, bytes]:
    if not isinstance(draft, TechnicalDraft):
        raise ContractStoreError("technical_draft_invalid", "wrong type")
    for field_name in (
        "authoring_manifest_hash",
        "authoring_language_state_hash",
        "authoring_code_digest",
    ):
        _canonical_sha256(getattr(draft, field_name), field=field_name)
    _canonical_optional_sha256(
        draft.authoring_signature_hash,
        field="authoring_signature_hash",
    )
    if (
        not isinstance(draft.manifest_bytes, bytes)
        or not isinstance(draft.language_state_bytes, bytes)
    ):
        raise ContractStoreError("technical_draft_invalid", "payloads must be bytes")
    if _sha256(draft.manifest_bytes) != draft.authoring_manifest_hash:
        raise ContractStoreError("technical_draft_invalid", "manifest hash")
    if _sha256(draft.language_state_bytes) != draft.authoring_language_state_hash:
        raise ContractStoreError("technical_draft_invalid", "language-state hash")

    authoring_manifest = _read_regular_file(
        ref.manifest_dir / "manifest.toml", code="source_file_invalid",
    )
    authoring_state = _read_regular_file(
        ref.manifest_dir / "manifest.lang_state.json", code="source_file_invalid",
    )
    signature_path = ref.manifest_dir / "manifest.toml.sig"
    if _is_link_like(signature_path):
        raise ContractStoreError("source_file_invalid", str(signature_path))
    authoring_signature = (
        _read_regular_file(signature_path, code="source_file_invalid")
        if signature_path.exists()
        else None
    )
    visible_manifest = (
        None if visible_payloads is None else visible_payloads["manifest.toml"]
    )
    visible_state = (
        None
        if visible_payloads is None
        else visible_payloads["manifest.lang_state.json"]
    )
    visible_signature = (
        None
        if visible_payloads is None
        else visible_payloads["manifest.toml.sig"]
    )
    signature_hash = (
        None if authoring_signature is None else _sha256(authoring_signature)
    )
    if (
        authoring_manifest not in {draft.manifest_bytes, visible_manifest}
        or authoring_state not in {draft.language_state_bytes, visible_state}
        or not (
            signature_hash == draft.authoring_signature_hash
            or (
                visible_signature is not None
                and authoring_signature == visible_signature
            )
        )
    ):
        raise ContractStoreError("technical_draft_stale", str(ref.contract_id))
    if (
        require_authoring_signature
        and base_payloads is not None
        and authoring_signature is None
    ):
        raise ContractStoreError(
            "technical_draft_invalid", "existing contract signature is missing",
        )

    document = _editable_manifest(draft.manifest_bytes)
    _unmodified_bytes, proposed = _manifest_from_document(document)
    if ref.name is None or proposed.get("name") != ref.name:
        raise ContractStoreError("contract_identity_mismatch", str(ref.contract_id))
    try:
        raw_state = json.loads(draft.language_state_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractStoreError("language_state_json", str(exc)) from exc
    if not isinstance(raw_state, dict):
        raise ContractStoreError("language_state_schema")
    try:
        decode_language_state(draft.language_state_bytes, manifest=proposed)
    except LanguageStateError as exc:
        raise ContractStoreError(exc.code, exc.detail) from exc
    _rebase_current_localizations(
        document,
        proposed,
        raw_state,
        authoring_base_payloads=authoring_base_payloads,
        current_base_payloads=base_payloads,
    )
    _rebased_bytes, rebased = _manifest_from_document(document)
    actual_code_digest = _code_digest(ref, rebased)
    if actual_code_digest != draft.authoring_code_digest:
        raise ContractStoreError("technical_draft_stale", "code changed")
    code_table = document.get("code")
    if not isinstance(code_table, Mapping):
        raise ContractStoreError("code_files_invalid")
    code_table["digest"] = actual_code_digest  # type: ignore[index]
    manifest_bytes, candidate_parsed = _manifest_from_document(document)
    expected = copy.deepcopy(rebased)
    expected["code"]["digest"] = actual_code_digest
    if candidate_parsed != expected:
        raise ContractStoreError("technical_draft_changed")
    try:
        state_bytes = encode_language_state(raw_state, manifest=candidate_parsed)
        candidate_state = decode_language_state(
            state_bytes,
            manifest=candidate_parsed,
        )
    except LanguageStateError as exc:
        raise ContractStoreError(exc.code, exc.detail) from exc
    _validate_technical_policy(
        base_payloads,
        candidate_parsed,
        candidate_state,
        removal=removal,
    )
    return _signed_candidate_payloads(
        ref,
        manifest_bytes=manifest_bytes,
        language_state_bytes=state_bytes,
        private_key=private_key,
        trusted_publics=trusted_publics,
    )


def publish_localization(
    ref: ManifestRef,
    *,
    expected_generation_id: str,
    source_language: str,
    target_language: str,
    patches: tuple[LocalizationPatch, ...],
    private_key: Ed25519PrivateKey,
    trusted_publics: Iterable[TrustedPublic],
    registry_reconciler: RegistryReconciler | None = None,
    store_root: Path | str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    replace_timeout: float = DEFAULT_REPLACE_TIMEOUT,
) -> PublicationResult:
    """Publish reviewed prose to an isolated or activated store."""
    root, productive = _publication_root(store_root)
    _require_registry_reconciler(productive, registry_reconciler)
    _validate_manifest_ref(ref)
    _require_publishable_manifest(ref)
    generation_directory_name(expected_generation_id)
    trusted = _trusted_public_tuple(trusted_publics)
    source = _canonical_language(source_language, field="source_language")
    target = _canonical_language(target_language, field="target_language")

    with contextlib.ExitStack() as locks:
        # Localization cannot change the executor name, but in ACTIVE mode it
        # mirrors the committed manifest/state/signature back to authoring.
        # Share the same global -> contract order as signing, migration and
        # technical publication so those files never have disjoint writers.
        locks.enter_context(catalog_admission_lock(
            store_root=root, timeout=lock_timeout,
        ))
        locks.enter_context(_writer_lock(
            ref.contract_id, store_root=root, timeout=lock_timeout,
        ))
        contract_dir, generations, previous, current_payloads = _publication_base_locked(
            ref,
            trusted_publics=trusted,
            store_root=root,
            technical_base=False,
        )
        if previous is None or current_payloads is None:
            raise ContractStoreError("commit_conflict", "localization requires a current base")
        if previous == expected_generation_id:
            base_payloads = current_payloads
        else:
            base_payloads = _snapshot_payloads(_load_generation(
                ref,
                expected_generation_id,
                trusted_publics=trusted,
                store_root=root,
            ))
        base = _verify_payloads(
            ref,
            base_payloads,
            trusted_publics=trusted,
            identifier=expected_generation_id,
            require_inventory_hash=False,
        )
        payloads = _prepare_localization_payloads_locked(
            ref,
            base,
            source_language=source,
            target_language=target,
            patches=patches,
            private_key=private_key,
            trusted_publics=trusted,
        )
        desired, repeated = _commit_payloads_locked(
            ref,
            payloads,
            contract_dir=contract_dir,
            generations=generations,
            previous=previous,
            current_payloads=current_payloads,
            expected_generation_id=expected_generation_id,
            trusted_publics=trusted,
            store_root=root,
            replace_timeout=replace_timeout,
        )
        if productive:
            _reconcile_authoring_locked(
                ref,
                payloads,
                trusted_publics=trusted,
                replace_timeout=replace_timeout,
            )
    fresh = _verify_published_postcondition(
        ref,
        payloads,
        desired=desired,
        trusted_publics=trusted,
        store_root=root,
        registry_reconciler=(registry_reconciler if productive else None),
    )
    return PublicationResult(
        contract_id=ref.contract_id,
        previous_generation_id=previous,
        current_generation_id=str(fresh.generation_id),
        operation="publish_localization",
        repeated=repeated,
    )


def publish_technical_update(
    ref: ManifestRef,
    *,
    expected_generation_id: str | None,
    draft: TechnicalDraft,
    private_key: Ed25519PrivateKey,
    trusted_publics: Iterable[TrustedPublic],
    removal: SurfaceRemoval | None = None,
    removal_audit: Callable[[Mapping[str, object]], None] | None = None,
    registry_reconciler: RegistryReconciler | None = None,
    birth_authorization: BirthCommitAuthorization | None = None,
    store_root: Path | str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    replace_timeout: float = DEFAULT_REPLACE_TIMEOUT,
) -> PublicationResult:
    """Sign and publish one technical draft under a single writer lock."""
    if birth_authorization is not None:
        raise ContractStoreError("birth_commit_boundary_required")
    root, productive = _publication_root(store_root)
    _require_registry_reconciler(productive, registry_reconciler)
    _validate_manifest_ref(ref)
    _require_publishable_manifest(ref)
    if expected_generation_id is not None:
        generation_directory_name(expected_generation_id)
    trusted = _trusted_public_tuple(trusted_publics)

    with contextlib.ExitStack() as locks:
        locks.enter_context(catalog_admission_lock(
            store_root=root, timeout=lock_timeout,
        ))
        _require_catalog_name_candidate(
            ref,
            ref.name,
            trusted_publics=trusted,
            store_root=root,
        )
        locks.enter_context(_writer_lock(
            ref.contract_id, store_root=root, timeout=lock_timeout,
        ))
        contract_dir, generations, previous, current_payloads = _publication_base_locked(
            ref,
            trusted_publics=trusted,
            store_root=root,
            technical_base=True,
        )
        policy_base = _technical_expected_base_locked(
            ref,
            expected_generation_id=expected_generation_id,
            previous_generation_id=previous,
            current_payloads=current_payloads,
            trusted_publics=trusted,
            store_root=root,
        )
        authoring_base = _technical_authoring_base_locked(
            ref,
            draft,
            expected_generation_id=expected_generation_id,
            current_payloads=current_payloads,
            generations=generations,
            trusted_publics=trusted,
            store_root=root,
        )
        payloads = _prepare_technical_payloads_locked(
            ref,
            policy_base,
            authoring_base,
            current_payloads,
            draft=draft,
            private_key=private_key,
            trusted_publics=trusted,
            removal=removal,
        )
        desired, repeated = _commit_payloads_locked(
            ref,
            payloads,
            contract_dir=contract_dir,
            generations=generations,
            previous=previous,
            current_payloads=current_payloads,
            expected_generation_id=expected_generation_id,
            trusted_publics=trusted,
            store_root=root,
            replace_timeout=replace_timeout,
            birth_authorization=birth_authorization,
            precommit=(
                None
                if removal is None
                else lambda candidate_id: _record_surface_removal_locked(
                    removal,
                    removal_audit,
                    ref=ref,
                    operation="publish_technical_update",
                    expected_generation_id=expected_generation_id,
                    candidate_generation_id=candidate_id,
                )
            ),
        )
        if productive:
            _reconcile_authoring_locked(
                ref,
                payloads,
                trusted_publics=trusted,
                replace_timeout=replace_timeout,
            )
    fresh = _verify_published_postcondition(
        ref,
        payloads,
        desired=desired,
        trusted_publics=trusted,
        store_root=root,
        registry_reconciler=(registry_reconciler if productive else None),
    )
    return PublicationResult(
        contract_id=ref.contract_id,
        previous_generation_id=previous,
        current_generation_id=str(fresh.generation_id),
        operation="publish_technical_update",
        repeated=repeated,
    )


def authenticate_birth_predecessor(
    ref: ManifestRef, *, trusted_publics: Iterable[TrustedPublic],
    store_root: Path | str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> tuple[object, Mapping[str, bytes] | None]:
    """Observe the verified immutable base and return a detached snapshot.

    Publication reconstructs this snapshot under its writer lock and compares
    its canonical identifier, closing the observation-to-commit interval.
    """
    from executor_birth_predecessor import predecessor_snapshot

    root, _productive = _publication_root(store_root)
    _validate_manifest_ref(ref)
    trusted = _trusted_public_tuple(trusted_publics)
    with catalog_admission_lock(store_root=root, timeout=lock_timeout):
        with _writer_lock(ref.contract_id, store_root=root, timeout=lock_timeout):
            _contract_dir, _generations, revision_id, payloads = _publication_base_locked(
                ref, trusted_publics=trusted, store_root=root, technical_base=True,
            )
            detached = None if payloads is None else {
                name: bytes(value) for name, value in payloads.items()
            }
            return predecessor_snapshot(
                revision_id, "absent" if revision_id is None else "generation", detached,
            ), detached


def commit_birth_snapshot(
    ref: ManifestRef,
    *,
    expected_generation_id: str | None,
    snapshot: object,
    request_id: str,
    private_key: Ed25519PrivateKey,
    trusted_publics: Iterable[TrustedPublic],
    birth_authorization: BirthCommitAuthorization,
    registry_reconciler: RegistryReconciler | None = None,
    store_root: Path | str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    replace_timeout: float = DEFAULT_REPLACE_TIMEOUT,
) -> PublicationResult:
    """Install one admitted private snapshot and its RM-0007 generation."""
    from executor_birth_authoring import (
        AuthoringInstallError, AuthoringInstallJournalV1, advance_version,
        authoring_paths, authoring_token, authoring_tree_id,
        cleanup_transaction, load_prepared_journal, materialize_staging,
        observe_tree, persist_prepared_journal, replace_with_staging,
        rollback_prepared,
    )
    from executor_birth_snapshot import CandidateSnapshot

    if not isinstance(birth_authorization, BirthCommitAuthorization):
        raise ContractStoreError("birth_authorization_required")
    if not isinstance(snapshot, CandidateSnapshot):
        raise ContractStoreError("candidate_snapshot_required")
    _canonical_sha256(request_id, field="request_id")
    root, productive = _publication_root(store_root)
    _require_registry_reconciler(productive, registry_reconciler)
    _validate_manifest_ref(ref)
    _require_publishable_manifest(ref)
    trusted = _trusted_public_tuple(trusted_publics)
    if expected_generation_id is not None:
        generation_directory_name(expected_generation_id)

    control = authoring_paths(ref.manifest_dir, ref.contract_id.value)
    previous: str | None = None
    repeated = False
    payloads: dict[str, bytes]
    desired: str
    try:
        with contextlib.ExitStack() as locks:
            locks.enter_context(catalog_admission_lock(
                store_root=root, timeout=lock_timeout,
            ))
            locks.enter_context(authoring_token(
                control.lock, exclusive=True, timeout=lock_timeout,
            ))
            locks.enter_context(_writer_lock(
                ref.contract_id, store_root=root, timeout=lock_timeout,
            ))
            contract_dir, generations, previous, current_payloads = _publication_base_locked(
                ref, trusted_publics=trusted, store_root=root, technical_base=True,
            )

            # Admission was performed outside the writer lock.  Reconstruct
            # the authenticated predecessor from the selected immutable
            # revision and re-confirm every pin before accepting its receipt.
            from executor_birth_predecessor import (
                derive_revision_facts, predecessor_snapshot,
                revision_facts_id as canonical_revision_facts_id,
            )
            pinned_predecessor = predecessor_snapshot(
                previous,
                "absent" if previous is None else "generation",
                current_payloads,
            )
            if (
                birth_authorization.predecessor_snapshot_id is not None
                and birth_authorization.predecessor_snapshot_id
                != pinned_predecessor.snapshot_id
            ):
                raise ContractStoreError("birth_predecessor_changed")
            if birth_authorization.revision_facts_id is not None:
                locked_facts = derive_revision_facts(
                    pinned_predecessor, current_payloads, snapshot,
                )
                if (
                    canonical_revision_facts_id(locked_facts)
                    != birth_authorization.revision_facts_id
                ):
                    raise ContractStoreError("birth_revision_facts_changed")
            if birth_authorization.context_epoch is not None:
                resolver = birth_authorization.context_epoch_resolver
                if resolver is None:
                    raise ContractStoreError("birth_context_resolver_required")
                try:
                    current_context_epoch = resolver()
                except Exception as exc:
                    raise ContractStoreError("birth_context_unavailable", str(exc)) from exc
                if current_context_epoch != birth_authorization.context_epoch:
                    raise ContractStoreError("birth_context_changed")

            pending = load_prepared_journal(control)
            if pending is not None:
                if pending.contract_id != ref.contract_id.value:
                    raise ContractStoreError("authoring_recovery_ambiguous", "contract_id")
                receipt_path = _birth_receipt_path(contract_dir, pending.new_generation_id)
                encoded = _read_regular_file(receipt_path, code="birth_receipt_invalid")
                try:
                    receipt = birth_authorization.verifier(encoded)
                except Exception as exc:
                    raise ContractStoreError("birth_receipt_invalid", str(exc)) from exc
                _validate_birth_receipt_binding(
                    receipt, ref=ref,
                    generation_identifier=pending.new_generation_id,
                    previous=pending.predecessor_generation_id,
                    authorization=birth_authorization,
                    request_id=pending.request_id,
                    journal_hash=pending.journal_hash,
                )
                if previous == pending.new_generation_id:
                    observed = observe_tree(control.canonical)
                    if authoring_tree_id(observed) != pending.new_tree_id:
                        raise ContractStoreError("authoring_recovery_ambiguous", "canonical")
                    advance_version(control, pending.contract_id, pending.new_tree_id)
                    cleanup_transaction(control, pending)
                    if pending.request_id != request_id:
                        raise ContractStoreError("commit_conflict", "recovered another request")
                    return PublicationResult(
                        ref.contract_id, pending.predecessor_generation_id,
                        pending.new_generation_id, "commit_birth_snapshot", True,
                    )
                if previous != pending.predecessor_generation_id:
                    raise ContractStoreError("authoring_recovery_ambiguous", "pointer")
                rollback_prepared(control, pending)

            if previous != expected_generation_id:
                replay_signature = sign_manifest_bytes(
                    snapshot.manifest_bytes, private_key=private_key,
                )
                replay_payloads = {
                    "manifest.toml": snapshot.manifest_bytes,
                    "manifest.toml.sig": replay_signature,
                    "manifest.lang_state.json": snapshot.language_state_bytes,
                }
                replay_desired = generation_id(replay_payloads)
                if (
                    previous == replay_desired
                    and current_payloads == replay_payloads
                ):
                    receipt_path = _birth_receipt_path(contract_dir, replay_desired)
                    encoded = _read_regular_file(receipt_path, code="birth_receipt_invalid")
                    try:
                        receipt = birth_authorization.verifier(encoded)
                    except Exception as exc:
                        raise ContractStoreError("birth_receipt_invalid", str(exc)) from exc
                    receipt_journal_hash = getattr(
                        receipt, "authoring_journal_hash", None,
                    )
                    _validate_birth_receipt_binding(
                        receipt, ref=ref, generation_identifier=replay_desired,
                        previous=expected_generation_id,
                        authorization=birth_authorization,
                        request_id=request_id,
                        journal_hash=receipt_journal_hash,
                    )
                    replay_files = dict(snapshot.code_files)
                    replay_files.update(replay_payloads)
                    if (
                        authoring_tree_id(replay_files)
                        != authoring_tree_id(observe_tree(control.canonical))
                    ):
                        raise ContractStoreError(
                            "authoring_recovery_ambiguous", "replay tree",
                        )
                    return PublicationResult(
                        ref.contract_id, expected_generation_id,
                        replay_desired, "commit_birth_snapshot", True,
                    )
                raise ContractStoreError(
                    "commit_conflict", f"expected={expected_generation_id} current={previous}",
                )
            signature = sign_manifest_bytes(snapshot.manifest_bytes, private_key=private_key)
            payloads = {
                "manifest.toml": snapshot.manifest_bytes,
                "manifest.toml.sig": signature,
                "manifest.lang_state.json": snapshot.language_state_bytes,
            }
            desired = generation_id(payloads)
            final_files = dict(snapshot.code_files)
            final_files.update(payloads)
            new_tree_id = authoring_tree_id(final_files)
            old_tree_id = (
                None if not control.canonical.exists()
                else authoring_tree_id(observe_tree(control.canonical))
            )
            suffix = request_id.removeprefix("sha256:")
            journal = AuthoringInstallJournalV1(
                request_id=request_id,
                contract_id=ref.contract_id.value,
                source_origin=ref.origin.value,
                canonical_tree_id=old_tree_id or new_tree_id,
                old_tree_id=old_tree_id,
                new_tree_id=new_tree_id,
                candidate_id=birth_authorization.candidate_id,
                semantic_core_id=birth_authorization.semantic_core_id,
                admission_context_id=birth_authorization.admission_context_id,
                predecessor_generation_id=previous,
                new_generation_id=desired,
                staging_basename=f".birth-stage-{suffix}",
                backup_basename=f".birth-backup-{suffix}",
            )
            staging, backup = control.transaction_paths(journal)
            if staging.exists() or backup.exists():
                # The receipt is deliberately made durable before the
                # prepared journal.  A hard stop in that interval leaves the
                # already verified, request-derived staging tree behind.  It
                # is resumable only for the exact request and exact closed
                # tree; a backup without a journal is never self-authorizing.
                if backup.exists() or not staging.is_dir() or staging.is_symlink():
                    raise ContractStoreError("authoring_recovery_ambiguous", "unjournaled staging")
                staged_files = observe_tree(staging)
                if (
                    dict(staged_files) != final_files
                    or authoring_tree_id(staged_files) != new_tree_id
                ):
                    raise ContractStoreError("authoring_recovery_ambiguous", "unjournaled staging")
            else:
                staging = materialize_staging(control, journal, final_files)
            staged_ref = replace(
                ref, source_root=staging, manifest_path=staging / "manifest.toml",
                allowed_code_roots=(staging,), manifest_hash=_sha256(snapshot.manifest_bytes),
            )
            _verify_payloads(
                staged_ref, payloads, trusted_publics=trusted, identifier=None,
                require_inventory_hash=False,
            )
            _persist_birth_receipt_locked(
                ref, desired, payloads, previous=previous,
                contract_dir=contract_dir, authorization=birth_authorization,
                replace_timeout=replace_timeout, request_id=request_id,
                journal_hash=journal.journal_hash,
            )
            persist_prepared_journal(control, journal)
            replace_with_staging(control, journal)
            installed_ref = ref
            installed = _verify_payloads(
                installed_ref, payloads, trusted_publics=trusted,
                identifier=None, require_inventory_hash=False,
            )
            if (
                installed.declared_code_digest != installed.verified_code_digest
                or authoring_tree_id(observe_tree(control.canonical)) != new_tree_id
            ):
                raise ContractStoreError("authoring_tree_reread_mismatch")
            desired, repeated = _commit_payloads_locked(
                installed_ref, payloads, contract_dir=contract_dir,
                generations=generations, previous=previous,
                current_payloads=current_payloads,
                expected_generation_id=expected_generation_id,
                trusted_publics=trusted, store_root=root,
                replace_timeout=replace_timeout,
            )
            _verify_published_postcondition(
                installed_ref, payloads, desired=desired,
                trusted_publics=trusted, store_root=root,
            )
            advance_version(control, ref.contract_id.value, new_tree_id)
            cleanup_transaction(control, journal)
    except AuthoringInstallError as exc:
        raise ContractStoreError(exc.code, exc.detail) from exc

    fresh_ref = ref
    fresh = _verify_published_postcondition(
        fresh_ref, payloads, desired=desired, trusted_publics=trusted,
        store_root=root,
        registry_reconciler=(registry_reconciler if productive else None),
    )
    return PublicationResult(
        contract_id=ref.contract_id,
        previous_generation_id=previous,
        current_generation_id=str(fresh.generation_id),
        operation="commit_birth_snapshot",
        repeated=repeated,
    )


def publish_signed_source(
    ref: ManifestRef,
    *,
    expected_generation_id: str | None,
    trusted_publics: Iterable[TrustedPublic],
    removal: SurfaceRemoval | None = None,
    removal_audit: Callable[[Mapping[str, object]], None] | None = None,
    registry_reconciler: RegistryReconciler | None = None,
    store_root: Path | str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    replace_timeout: float = DEFAULT_REPLACE_TIMEOUT,
) -> PublicationResult:
    root, productive = _publication_root(store_root)
    _require_registry_reconciler(productive, registry_reconciler)
    _validate_manifest_ref(ref)
    _require_publishable_manifest(ref)
    trusted = _trusted_public_tuple(trusted_publics)
    if expected_generation_id is not None:
        generation_directory_name(expected_generation_id)
    previous: str | None = None
    repeated = False
    with contextlib.ExitStack() as locks:
        locks.enter_context(catalog_admission_lock(
            store_root=root, timeout=lock_timeout,
        ))
        _require_catalog_name_candidate(
            ref,
            ref.name,
            trusted_publics=trusted,
            store_root=root,
        )
        locks.enter_context(_writer_lock(
            ref.contract_id, store_root=root, timeout=lock_timeout,
        ))
        contract_dir, generations, previous, current_payloads = _publication_base_locked(
            ref,
            trusted_publics=trusted,
            store_root=root,
            technical_base=True,
        )
        policy_base = _technical_expected_base_locked(
            ref,
            expected_generation_id=expected_generation_id,
            previous_generation_id=previous,
            current_payloads=current_payloads,
            trusted_publics=trusted,
            store_root=root,
        )
        candidate = verify_manifest_source(ref, trusted_publics=trusted)
        payloads = _snapshot_payloads(candidate)
        _validate_technical_policy(
            policy_base,
            candidate.parsed,
            candidate.language_state,
            removal=removal,
        )
        desired, repeated = _commit_payloads_locked(
            ref,
            payloads,
            contract_dir=contract_dir,
            generations=generations,
            previous=previous,
            current_payloads=current_payloads,
            expected_generation_id=expected_generation_id,
            trusted_publics=trusted,
            store_root=root,
            replace_timeout=replace_timeout,
            precommit=(
                None
                if removal is None
                else lambda candidate_id: _record_surface_removal_locked(
                    removal,
                    removal_audit,
                    ref=ref,
                    operation="publish_signed_source",
                    expected_generation_id=expected_generation_id,
                    candidate_generation_id=candidate_id,
                )
            ),
        )
        if productive:
            _reconcile_authoring_locked(
                ref,
                payloads,
                trusted_publics=trusted,
                replace_timeout=replace_timeout,
            )
    fresh = _verify_published_postcondition(
        ref,
        payloads,
        desired=desired,
        trusted_publics=trusted,
        store_root=root,
        registry_reconciler=(registry_reconciler if productive else None),
    )
    return PublicationResult(
        contract_id=ref.contract_id,
        previous_generation_id=previous,
        current_generation_id=str(fresh.generation_id),
        operation="publish_signed_source",
        repeated=repeated,
    )


def retire(
    ref: ManifestRef,
    *,
    expected_generation_id: str,
    actor: str,
    reason: str,
    private_key: Ed25519PrivateKey,
    trusted_publics: Iterable[TrustedPublic],
    audit_sink: Callable[[Mapping[str, object]], None] | None = None,
    registry_reconciler: RegistryReconciler | None = None,
    store_root: Path | str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    replace_timeout: float = DEFAULT_REPLACE_TIMEOUT,
) -> PublicationResult:
    """Atomically make a signed tombstone current for one contract.

    The source and code must remain intact until this call succeeds.  Once the
    tombstone is authoritative, live reads no longer consult code and the
    caller may archive or remove its own source tree.  An exact retry uses the
    original expected generation, actor and reason and emits no second audit
    event after commit.  If a crash occurs before the pointer, it re-emits the
    same stable ``event_id``; ``audit_sink`` must deduplicate that ID.
    """
    root, productive = _publication_root(store_root)
    _require_registry_reconciler(productive, registry_reconciler)
    _validate_manifest_ref(ref)
    generation_directory_name(expected_generation_id)
    if not callable(audit_sink):
        raise ContractStoreError("retirement_audit_required")
    trusted = _trusted_public_tuple(trusted_publics)
    retirement_bytes = encode_retirement(
        ref.contract_id,
        previous_generation_id=expected_generation_id,
        actor=actor,
        reason=reason,
    )
    signature_bytes = sign_manifest_bytes(
        RETIREMENT_SIGNATURE_DOMAIN + retirement_bytes,
        private_key=private_key,
    )
    payloads = {
        "retirement.json": retirement_bytes,
        "retirement.json.sig": signature_bytes,
    }
    desired = retirement_id(payloads)
    _authenticate_retirement_payloads(
        ref,
        payloads,
        trusted_publics=trusted,
        identifier=desired,
    )

    previous: str | None = None
    repeated = False
    with contextlib.ExitStack() as locks:
        locks.enter_context(catalog_admission_lock(
            store_root=root, timeout=lock_timeout,
        ))
        locks.enter_context(_writer_lock(
            ref.contract_id, store_root=root, timeout=lock_timeout,
        ))
        contract_dir = _existing_contract_directory(
            ref.contract_id, store_root=root,
        )
        generations = contract_dir / "generations"
        _recover_contract_staging_locked(
            ref,
            contract_dir,
            generations,
            trusted_publics=trusted,
            store_root=root,
        )
        previous = _read_current_optional(contract_dir)
        if previous is None:
            raise ContractStoreError("current_missing", ref.contract_id.value)
        current = _load_revision(
            ref,
            previous,
            trusted_publics=trusted,
            store_root=root,
        )
        if isinstance(current, ContractRetirement):
            repeated = (
                current.retirement_id == desired
                and current.previous_generation_id == expected_generation_id
                and current.payload_bytes == retirement_bytes
                and current.signature_bytes == signature_bytes
            )
            if not repeated:
                raise ContractStoreError(
                    "commit_conflict",
                    f"expected={expected_generation_id} current={previous}",
                )
        elif previous != expected_generation_id:
            raise ContractStoreError(
                "commit_conflict",
                f"expected={expected_generation_id} current={previous}",
            )

        if not repeated:
            audit_sink(_auditable_event({
                "event": "contract_retirement_authorized",
                "contract_id": ref.contract_id.value,
                "expected_generation_id": expected_generation_id,
                "retirement_id": desired,
                "actor": actor.strip(),
                "reason": reason.strip(),
            }))
            # The sink is application code.  Recheck the exact active state
            # before committing its authorization with one pointer replace.
            live_previous = _read_current_optional(contract_dir)
            if live_previous != previous:
                raise ContractStoreError(
                    "commit_conflict",
                    f"expected={previous} current={live_previous}",
                )
            live = _load_revision(
                ref,
                previous,
                trusted_publics=trusted,
                store_root=root,
            )
            if isinstance(live, ContractRetirement):
                raise ContractStoreError(
                    "commit_conflict",
                    f"expected={expected_generation_id} current={previous}",
                )
            _install_retirement(
                ref,
                payloads,
                identifier=desired,
                trusted_publics=trusted,
                store_root=root,
            )
            _write_current(
                contract_dir,
                desired,
                replace_timeout=replace_timeout,
            )

    fresh = _verify_retirement_postcondition(
        ref,
        payloads,
        desired=desired,
        trusted_publics=trusted,
        store_root=root,
        registry_reconciler=(
            registry_reconciler if productive else None
        ),
    )
    return PublicationResult(
        contract_id=ref.contract_id,
        previous_generation_id=previous,
        current_generation_id=fresh.retirement_id,
        operation="retire",
        repeated=repeated,
    )


def reactivate_technical_update(
    ref: ManifestRef,
    *,
    expected_retirement_id: str,
    draft: TechnicalDraft,
    actor: str,
    reason: str,
    private_key: Ed25519PrivateKey,
    trusted_publics: Iterable[TrustedPublic],
    audit_sink: Callable[[Mapping[str, object]], None] | None = None,
    removal: SurfaceRemoval | None = None,
    removal_audit: Callable[[Mapping[str, object]], None] | None = None,
    registry_reconciler: RegistryReconciler | None = None,
    store_root: Path | str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    replace_timeout: float = DEFAULT_REPLACE_TIMEOUT,
) -> PublicationResult:
    """Explicitly replace one authenticated tombstone with a new generation.

    This is intentionally separate from ordinary publication: reinstalling a
    source directory cannot make a retired contract live by accident.  The
    tombstone is the CAS base, while its referenced manifest generation is the
    linguistic and schema-policy base for the new signed draft.
    """
    root, productive = _publication_root(store_root)
    _require_registry_reconciler(productive, registry_reconciler)
    _validate_manifest_ref(ref)
    _require_publishable_manifest(ref)
    generation_directory_name(expected_retirement_id)
    if not isinstance(actor, str) or not actor.strip():
        raise ContractStoreError("reactivation_input_invalid", "actor is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ContractStoreError("reactivation_input_invalid", "reason is required")
    if not callable(audit_sink):
        raise ContractStoreError("reactivation_audit_required")
    _validated_removal(removal)
    if removal is not None and not callable(removal_audit):
        raise ContractStoreError("surface_removal_audit_required")
    trusted = _trusted_public_tuple(trusted_publics)

    previous: str | None = None
    repeated = False
    payloads: dict[str, bytes]
    with contextlib.ExitStack() as locks:
        locks.enter_context(catalog_admission_lock(
            store_root=root, timeout=lock_timeout,
        ))
        _require_catalog_name_candidate(
            ref,
            ref.name,
            trusted_publics=trusted,
            store_root=root,
        )
        locks.enter_context(_writer_lock(
            ref.contract_id, store_root=root, timeout=lock_timeout,
        ))
        contract_dir = _existing_contract_directory(
            ref.contract_id, store_root=root,
        )
        generations = contract_dir / "generations"
        _require_plain_directory(
            generations, code="generations_directory_invalid",
        )
        _recover_contract_staging_locked(
            ref,
            contract_dir,
            generations,
            trusted_publics=trusted,
            store_root=root,
        )
        previous = _read_current_optional(contract_dir)
        if previous is None:
            raise ContractStoreError("current_missing", ref.contract_id.value)
        current = _load_revision(
            ref,
            previous,
            trusted_publics=trusted,
            store_root=root,
        )
        retirement = (
            current
            if isinstance(current, ContractRetirement)
            and current.retirement_id == expected_retirement_id
            else _load_retirement(
                ref,
                expected_retirement_id,
                trusted_publics=trusted,
                store_root=root,
            )
        )
        if (
            isinstance(current, ContractRetirement)
            and current.retirement_id != expected_retirement_id
        ):
            raise ContractStoreError(
                "commit_conflict",
                f"expected={expected_retirement_id} current={previous}",
            )
        policy_base = _load_generation_for_commit(
            ref,
            retirement.previous_generation_id,
            trusted_publics=trusted,
            store_root=root,
        )
        current_payloads = (
            None
            if isinstance(current, ContractRetirement)
            else _snapshot_payloads(current)
        )
        payloads = _prepare_technical_payloads_locked(
            ref,
            policy_base,
            policy_base,
            policy_base if current_payloads is None else current_payloads,
            draft=draft,
            private_key=private_key,
            trusted_publics=trusted,
            removal=removal,
            require_authoring_signature=False,
        )

        def authorize(candidate_id: str) -> None:
            audit_sink(_auditable_event({
                "event": "contract_reactivation_authorized",
                "contract_id": ref.contract_id.value,
                "expected_retirement_id": expected_retirement_id,
                "target_generation_id": candidate_id,
                "actor": actor.strip(),
                "reason": reason.strip(),
            }))
            _record_surface_removal_locked(
                removal,
                removal_audit,
                ref=ref,
                operation="reactivate_technical_update",
                expected_generation_id=expected_retirement_id,
                candidate_generation_id=candidate_id,
            )

        desired, repeated = _commit_payloads_locked(
            ref,
            payloads,
            contract_dir=contract_dir,
            generations=generations,
            previous=previous,
            current_payloads=current_payloads,
            expected_generation_id=expected_retirement_id,
            trusted_publics=trusted,
            store_root=root,
            replace_timeout=replace_timeout,
            precommit=authorize,
        )
        if productive:
            _reconcile_authoring_locked(
                ref,
                payloads,
                trusted_publics=trusted,
                replace_timeout=replace_timeout,
            )
    fresh = _verify_published_postcondition(
        ref,
        payloads,
        desired=desired,
        trusted_publics=trusted,
        store_root=root,
        registry_reconciler=(registry_reconciler if productive else None),
    )
    return PublicationResult(
        contract_id=ref.contract_id,
        previous_generation_id=previous,
        current_generation_id=str(fresh.generation_id),
        operation="reactivate_technical_update",
        repeated=repeated,
    )


def rollback(
    ref: ManifestRef,
    *,
    expected_generation_id: str,
    target_generation_id: str,
    actor: str,
    reason: str,
    trusted_publics: Iterable[TrustedPublic],
    audit_sink: Callable[[Mapping[str, object]], None] | None = None,
    registry_reconciler: RegistryReconciler | None = None,
    store_root: Path | str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    replace_timeout: float = DEFAULT_REPLACE_TIMEOUT,
) -> PublicationResult:
    """Move only the pointer to one verified generation, never copy payloads.

    ``audit_sink`` must be idempotent by the stable ``event_id`` supplied in
    every callback because an authorization can precede an interrupted commit.
    """
    root, productive = _publication_root(store_root)
    _require_registry_reconciler(productive, registry_reconciler)
    _validate_manifest_ref(ref)
    _require_publishable_manifest(ref)
    generation_directory_name(expected_generation_id)
    generation_directory_name(target_generation_id)
    if not isinstance(actor, str) or not actor.strip():
        raise ContractStoreError("rollback_input_invalid", "actor is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ContractStoreError("rollback_input_invalid", "reason is required")
    if not callable(audit_sink):
        raise ContractStoreError("rollback_audit_required")
    trusted = _trusted_public_tuple(trusted_publics)

    with contextlib.ExitStack() as locks:
        locks.enter_context(catalog_admission_lock(
            store_root=root, timeout=lock_timeout,
        ))
        target_for_admission = _load_generation(
            ref,
            target_generation_id,
            trusted_publics=trusted,
            store_root=root,
        )
        _require_catalog_name_candidate(
            ref,
            target_for_admission.parsed.get("name"),
            trusted_publics=trusted,
            store_root=root,
        )
        locks.enter_context(_writer_lock(
            ref.contract_id, store_root=root, timeout=lock_timeout,
        ))
        contract_dir = _existing_contract_directory(
            ref.contract_id, store_root=root,
        )
        generations = contract_dir / "generations"
        _recover_contract_staging_locked(
            ref,
            contract_dir,
            generations,
            trusted_publics=trusted,
            store_root=root,
        )
        previous = _read_current_optional(contract_dir)
        if previous is None:
            raise ContractStoreError("current_missing", ref.contract_id.value)
        # Authenticate current before any conflict/idempotence decision.
        _authenticate_revision_for_commit(
            ref,
            previous,
            trusted_publics=trusted,
            store_root=root,
        )
        repeated = previous == target_generation_id
        if previous != expected_generation_id and not repeated:
            raise ContractStoreError(
                "commit_conflict",
                f"expected={expected_generation_id} current={previous}",
            )
        target = _load_generation(
            ref,
            target_generation_id,
            trusted_publics=trusted,
            store_root=root,
        )
        payloads = _snapshot_payloads(target)
        if not repeated:
            audit_sink(_auditable_event({
                "event": "contract_generation_rollback",
                "contract_id": ref.contract_id.value,
                "expected_generation_id": expected_generation_id,
                "target_generation_id": target_generation_id,
                "actor": actor.strip(),
                "reason": reason.strip(),
            }))
            # The audit callback is application code; repeat all target checks
            # and the CAS precondition before making its pointer authoritative.
            live_previous = _read_current_optional(contract_dir)
            if live_previous != previous:
                raise ContractStoreError(
                    "commit_conflict",
                    f"expected={previous} current={live_previous}",
                )
            _authenticate_revision_for_commit(
                ref,
                previous,
                trusted_publics=trusted,
                store_root=root,
            )
            target = _load_generation(
                ref,
                target_generation_id,
                trusted_publics=trusted,
                store_root=root,
            )
            payloads = _snapshot_payloads(target)
            _write_current(
                contract_dir,
                target_generation_id,
                replace_timeout=replace_timeout,
            )
        if productive:
            _reconcile_authoring_locked(
                ref,
                payloads,
                trusted_publics=trusted,
                replace_timeout=replace_timeout,
            )
    fresh = _verify_published_postcondition(
        ref,
        payloads,
        desired=target_generation_id,
        trusted_publics=trusted,
        store_root=root,
        registry_reconciler=(registry_reconciler if productive else None),
    )
    return PublicationResult(
        contract_id=ref.contract_id,
        previous_generation_id=previous,
        current_generation_id=str(fresh.generation_id),
        operation="rollback",
        repeated=repeated,
    )


def diagnose_store(
    refs: Iterable[ManifestRef],
    *,
    trusted_publics: Iterable[TrustedPublic],
    store_root: Path | str | None = None,
    orphan_warning_threshold: int = 20,
) -> tuple[StoreDiagnostic, ...]:
    """Inspect store integrity without creating, locking, repairing or deleting."""
    if orphan_warning_threshold < 0:
        raise ValueError("orphan_warning_threshold must be non-negative")
    root = _store_root(store_root)
    if not root.exists():
        return ()
    diagnostics: list[StoreDiagnostic] = []
    try:
        _require_plain_directory(root, code="store_root_invalid")
        _require_no_link_components(root, code="store_root_invalid")
    except ContractStoreError as exc:
        diagnostics.append(StoreDiagnostic(exc.code, None, str(root), exc.detail))
        return tuple(diagnostics)
    trusted = _trusted_public_tuple(trusted_publics)
    refs_tuple = tuple(refs)
    identities = tuple(ref.contract_id for ref in refs_tuple)
    if len(set(identities)) != len(identities):
        raise ContractStoreError("diagnostic_inventory_duplicate")
    known_root_entries = {
        contract_storage_key(ref.contract_id) for ref in refs_tuple
    }
    try:
        for entry in root.iterdir():
            if entry.name not in known_root_entries:
                diagnostics.append(StoreDiagnostic(
                    "contract_entry_unknown", None, str(entry),
                ))
    except OSError as exc:
        diagnostics.append(StoreDiagnostic(
            "diagnostic_io_error", None, str(root), str(exc),
        ))
        return tuple(diagnostics)
    orphan_count = 0
    for ref in sorted(refs_tuple, key=lambda item: str(item.contract_id)):
        contract_dir = root / contract_storage_key(ref.contract_id)
        if not contract_dir.exists():
            diagnostics.append(StoreDiagnostic(
                "contract_missing", ref.contract_id, str(contract_dir),
            ))
            continue
        try:
            _require_plain_directory(contract_dir, code="contract_directory_invalid")
            binding = read_binding(contract_dir)
            if binding.contract_id != ref.contract_id:
                raise ContractStoreError("binding_invalid", str(ref.contract_id))
            allowed_contract_entries = {
                BINDING_FILE, "writer.lock", "current", "generations",
                "admission-receipts",
            }
            for child in contract_dir.iterdir():
                if child.name not in allowed_contract_entries:
                    diagnostics.append(StoreDiagnostic(
                        "contract_entry_unknown", ref.contract_id, str(child),
                    ))
            lock_file = contract_dir / "writer.lock"
            if not lock_file.exists():
                diagnostics.append(StoreDiagnostic(
                    "lock_file_missing", ref.contract_id, str(lock_file),
                ))
            elif (
                _is_link_like(lock_file)
                or not lock_file.is_file()
                or lock_file.stat().st_size != 1
            ):
                diagnostics.append(StoreDiagnostic(
                    "lock_file_invalid", ref.contract_id, str(lock_file),
                ))
            current_id = _read_current_optional(contract_dir)
            reachable_ids: set[str] = set()
            if current_id is None:
                diagnostics.append(StoreDiagnostic(
                    "current_missing", ref.contract_id, str(contract_dir / "current"),
                ))
            else:
                current_revision = _load_revision(
                    ref,
                    current_id,
                    trusted_publics=trusted,
                    store_root=root,
                )
                reachable_ids.add(current_id)
                if isinstance(current_revision, ContractRetirement):
                    # The predecessor authenticated by the current tombstone
                    # is retained evidence and its only rollback target.  It
                    # is reachable history, not garbage.
                    reachable_ids.add(current_revision.previous_generation_id)
            generations = contract_dir / "generations"
            _require_plain_directory(generations, code="generations_directory_invalid")
            reachable_physical = {
                generation_directory_name(identifier)
                for identifier in reachable_ids
            }
            for child in generations.iterdir():
                if child.name.startswith(".generation-"):
                    diagnostics.append(StoreDiagnostic(
                        "staging_orphan", ref.contract_id, str(child),
                    ))
                    orphan_count += 1
                elif (
                    _PHYSICAL_ID_RE.fullmatch(child.name)
                    and child.name not in reachable_physical
                ):
                    orphan_count += 1
                    try:
                        orphan_identifier = "sha256:" + child.name
                        try:
                            orphan_kind = _revision_kind(child)
                        except ContractStoreError as exc:
                            if exc.code != "revision_structure":
                                raise
                            # Preserve the pre-tombstone diagnostic contract
                            # for an unrecognizable historical directory.
                            orphan_kind = "generation"
                        if orphan_kind == "retirement":
                            orphan = _load_retirement(
                                ref,
                                orphan_identifier,
                                trusted_publics=trusted,
                                store_root=root,
                            )
                        else:
                            _load_generation_for_commit(
                                ref,
                                orphan_identifier,
                                trusted_publics=trusted,
                                store_root=root,
                            )
                            orphan = None
                    except ContractStoreError as exc:
                        diagnostics.append(StoreDiagnostic(
                            exc.code, ref.contract_id, str(child), exc.detail,
                        ))
                    else:
                        diagnostics.append(StoreDiagnostic(
                            (
                                "retirement_orphan"
                                if isinstance(orphan, ContractRetirement)
                                else "generation_orphan"
                            ),
                            ref.contract_id,
                            str(child),
                        ))
                elif not _PHYSICAL_ID_RE.fullmatch(child.name):
                    diagnostics.append(StoreDiagnostic(
                        "generation_entry_unknown", ref.contract_id, str(child),
                    ))
        except ContractStoreError as exc:
            diagnostics.append(StoreDiagnostic(
                exc.code, ref.contract_id, str(contract_dir), exc.detail,
            ))
        except OSError as exc:
            diagnostics.append(StoreDiagnostic(
                "diagnostic_io_error", ref.contract_id, str(contract_dir), str(exc),
            ))
    if orphan_count > orphan_warning_threshold:
        diagnostics.append(StoreDiagnostic(
            "orphan_threshold_exceeded",
            None,
            str(root),
            f"orphans={orphan_count} threshold={orphan_warning_threshold}",
        ))
    return tuple(diagnostics)


__all__ = [
    "ACTIVE_BYTES",
    "ACTIVE_RELATIVE",
    "BINDING_FILE",
    "BINDING_VERSION",
    "BirthCommitAuthorization",
    "ContractBinding",
    "ContractRetirement",
    "ContractRevision",
    "ContractStoreError",
    "GENERATION_FILES",
    "LocalizationPatch",
    "ProductionStoreMode",
    "PublicationResult",
    "QuiescenceProof",
    "RegistryReconciler",
    "RETIREMENT_FILES",
    "RETIREMENT_SCHEMA",
    "RETIREMENT_VERSION",
    "SHADOW_RELATIVE",
    "StoreDiagnostic",
    "SurfaceRemoval",
    "TechnicalDraft",
    "VerifiedManifest",
    "WINDOWS_POWER_LOSS_LIMIT",
    "activate_store",
    "authenticate_birth_predecessor",
    "catalog_admission_lock",
    "commit_birth_snapshot",
    "contract_storage_key",
    "contract_revision_id",
    "current_contract",
    "current_manifest",
    "current_revision_id",
    "decode_binding",
    "diagnose_store",
    "encode_binding",
    "encode_retirement",
    "generation_directory_name",
    "generation_id",
    "prepare_technical_draft",
    "production_store_mode",
    "publish_localization",
    "publish_signed_source",
    "publish_technical_update",
    "reactivate_technical_update",
    "read_binding",
    "retire",
    "retirement_id",
    "rollback",
    "verify_manifest_source",
]
