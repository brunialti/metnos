"""Immutable, generation-addressed executor contract publication.

The store deliberately contains only the signed manifest, its existing
signature and canonical language provenance.  Code remains at the inventoried
authoring source and is verified there; no database, journal, second signature
or code copy is introduced.

On local Linux filesystems file and directory barriers protect the pointer
across a process crash and, subject to the filesystem, sudden power loss.  On
Windows/NTFS ``os.replace`` protects readers from process crashes, but Python
does not expose an equivalent directory durability barrier: v1 does not claim
that the newest pointer survives sudden power loss on Windows.
"""
from __future__ import annotations

import contextlib
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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

import config as _C
from i18n_materializer import (
    LanguageStateError,
    decode_language_state,
)
from manifest_inventory import ContractId, ManifestOrigin, ManifestRef, ManifestStatus
from sign import (
    ManifestSignatureError,
    TrustedPublic,
    verify_manifest_bytes,
)


STORE_RELATIVE = Path("contract-publications") / "v1"
SHADOW_RELATIVE = Path("contract-publications-shadow")
ACTIVE_RELATIVE = Path("contract-publications.ACTIVE")
BINDING_FILE = "binding.json"
BINDING_VERSION = 1
GENERATION_FILES = (
    "manifest.toml",
    "manifest.toml.sig",
    "manifest.lang_state.json",
)
DEFAULT_LOCK_TIMEOUT = 5.0
DEFAULT_REPLACE_TIMEOUT = 2.0
WINDOWS_POWER_LOSS_LIMIT = (
    "NTFS process-crash atomicity is supported; sudden-power-loss durability "
    "of the newest directory entry is not claimed"
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PHYSICAL_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.Lock] = {}


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
class PublicationResult:
    contract_id: ContractId
    previous_generation_id: str | None
    current_generation_id: str
    operation: str
    repeated: bool


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


def _m2_shadow_root(store_root: Path | str | None) -> Path:
    """Require an explicitly injected non-production root during M2."""
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


def _binding_json_without_duplicates(data: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ContractStoreError("binding_invalid", f"duplicate key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except UnicodeDecodeError as exc:
        raise ContractStoreError("binding_invalid", f"UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractStoreError("binding_invalid", f"JSON: {exc}") from exc


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


def generation_id(files: Mapping[str, bytes]) -> str:
    if set(files) != set(GENERATION_FILES):
        raise ContractStoreError("generation_payload", "exactly three files are required")
    digest = hashlib.sha256()
    for name in GENERATION_FILES:
        payload = files[name]
        if not isinstance(payload, bytes):
            raise TypeError(f"{name} payload must be bytes")
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return "sha256:" + digest.hexdigest()


def generation_directory_name(identifier: str) -> str:
    if not isinstance(identifier, str) or not _DIGEST_RE.fullmatch(identifier):
        raise ContractStoreError("generation_id_invalid", str(identifier))
    physical = identifier.removeprefix("sha256:")
    if not _PHYSICAL_ID_RE.fullmatch(physical):
        raise ContractStoreError("generation_id_invalid", identifier)
    return physical


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
    if parsed.get("executor_standard") is not None:
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
    payloads = _generation_payloads(generations / physical)
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
    payloads = _generation_payloads(generations / physical)
    _authenticate_payloads(
        ref,
        payloads,
        trusted_publics=trusted_publics,
        identifier=identifier,
        require_inventory_hash=False,
    )
    return payloads


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


def current_manifest(
    ref: ManifestRef,
    *,
    trusted_publics: Iterable[TrustedPublic],
    store_root: Path | str | None = None,
) -> VerifiedManifest:
    trusted = _trusted_public_tuple(trusted_publics)
    contract_dir = _existing_contract_directory(ref.contract_id, store_root=store_root)
    identifier = _read_current_optional(contract_dir)
    if identifier is None:
        raise ContractStoreError("current_missing", str(ref.contract_id))
    return _load_generation(
        ref,
        identifier,
        trusted_publics=trusted,
        store_root=store_root,
    )


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
def _writer_lock(
    contract_id: ContractId,
    *,
    store_root: Path | str,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> Iterator[None]:
    """Serialize every cooperating writer with one finite, portable lock."""
    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    contract_dir, _generations = _ensure_store_directories(
        contract_id,
        store_root=store_root,
    )
    lock_path = contract_dir / "writer.lock"
    process_lock = _process_lock_for(lock_path)
    deadline = time.monotonic() + timeout
    remaining = max(0.0, deadline - time.monotonic())
    if not process_lock.acquire(timeout=remaining):
        raise ContractStoreError("lock_timeout", str(contract_id))
    handle = None
    system_locked = False
    try:
        if _is_link_like(lock_path):
            raise ContractStoreError("lock_file_invalid", str(lock_path))
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        file_status = os.fstat(handle.fileno())
        if not stat.S_ISREG(file_status.st_mode):
            raise ContractStoreError("lock_file_invalid", str(lock_path))
        size = file_status.st_size
        if size == 0:
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        elif size != 1:
            raise ContractStoreError("lock_file_invalid", str(lock_path))
        while not _try_system_lock(handle):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ContractStoreError("lock_timeout", str(contract_id))
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


def publish_signed_source(
    ref: ManifestRef,
    *,
    expected_generation_id: str | None,
    trusted_publics: Iterable[TrustedPublic],
    store_root: Path | str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    replace_timeout: float = DEFAULT_REPLACE_TIMEOUT,
) -> PublicationResult:
    shadow_root = _m2_shadow_root(store_root)
    _validate_manifest_ref(ref)
    if ref.status is not ManifestStatus.ADMITTED:
        raise ContractStoreError("source_not_admitted", str(ref.contract_id))
    trusted = _trusted_public_tuple(trusted_publics)
    if expected_generation_id is not None:
        generation_directory_name(expected_generation_id)
    previous: str | None = None
    repeated = False
    with _writer_lock(ref.contract_id, store_root=shadow_root, timeout=lock_timeout):
        contract_dir, generations = _ensure_store_directories(
            ref.contract_id,
            store_root=shadow_root,
        )
        _ensure_binding_locked(contract_dir, ref.contract_id)
        previous = _read_current_optional(contract_dir)
        current_payloads: dict[str, bytes] | None = None
        if previous is not None:
            current_payloads = _load_generation_for_commit(
                ref,
                previous,
                trusted_publics=trusted,
                store_root=shadow_root,
            )
        elif expected_generation_id is not None:
            raise ContractStoreError(
                "commit_conflict",
                f"expected={expected_generation_id} current=None",
            )
        candidate = verify_manifest_source(ref, trusted_publics=trusted)
        payloads = _snapshot_payloads(candidate)
        desired = generation_id(payloads)
        if previous is None:
            _validate_initial_history(generations, desired_identifier=desired)
        if previous != expected_generation_id:
            if (
                current_payloads is not None
                and previous == desired
                and current_payloads == payloads
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
            and current_payloads == payloads
        ):
            repeated = True
        if not repeated:
            _install_generation(
                ref,
                payloads,
                identifier=desired,
                trusted_publics=trusted,
                store_root=shadow_root,
            )
            _write_current(
                contract_dir,
                desired,
                replace_timeout=replace_timeout,
            )
    fresh = current_manifest(ref, trusted_publics=trusted, store_root=shadow_root)
    if (
        fresh.generation_id != desired
        or _snapshot_payloads(fresh) != payloads
    ):
        raise ContractStoreError(
            "publication_superseded",
            f"desired={desired} current={fresh.generation_id}",
        )
    return PublicationResult(
        contract_id=ref.contract_id,
        previous_generation_id=previous,
        current_generation_id=str(fresh.generation_id),
        operation="publish_signed_source",
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
            if current_id is None:
                diagnostics.append(StoreDiagnostic(
                    "current_missing", ref.contract_id, str(contract_dir / "current"),
                ))
            else:
                _load_generation(
                    ref,
                    current_id,
                    trusted_publics=trusted,
                    store_root=root,
                )
            generations = contract_dir / "generations"
            _require_plain_directory(generations, code="generations_directory_invalid")
            current_physical = (
                generation_directory_name(current_id) if current_id is not None else None
            )
            for child in generations.iterdir():
                if child.name.startswith(".generation-"):
                    diagnostics.append(StoreDiagnostic(
                        "staging_orphan", ref.contract_id, str(child),
                    ))
                    orphan_count += 1
                elif _PHYSICAL_ID_RE.fullmatch(child.name) and child.name != current_physical:
                    orphan_count += 1
                    try:
                        _load_generation(
                            ref,
                            "sha256:" + child.name,
                            trusted_publics=trusted,
                            store_root=root,
                        )
                    except ContractStoreError as exc:
                        diagnostics.append(StoreDiagnostic(
                            exc.code, ref.contract_id, str(child), exc.detail,
                        ))
                    else:
                        diagnostics.append(StoreDiagnostic(
                            "generation_orphan", ref.contract_id, str(child),
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
    "BINDING_FILE",
    "BINDING_VERSION",
    "ContractBinding",
    "ContractStoreError",
    "GENERATION_FILES",
    "PublicationResult",
    "SHADOW_RELATIVE",
    "StoreDiagnostic",
    "VerifiedManifest",
    "WINDOWS_POWER_LOSS_LIMIT",
    "contract_storage_key",
    "current_manifest",
    "decode_binding",
    "diagnose_store",
    "encode_binding",
    "generation_directory_name",
    "generation_id",
    "publish_signed_source",
    "read_binding",
    "verify_manifest_source",
]
