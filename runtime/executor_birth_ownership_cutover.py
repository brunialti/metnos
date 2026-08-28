"""Canonical, authenticated F4 ownership-cutover certificates.

This module deliberately does not deploy a closed build or decide whether a
legacy API is callable.  It owns only the portable certificate codec, exact
binding to ``CurrentReceiptProof`` and the no-replace durable file pair used by
the later root-owned deployment coordinator.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import ctypes
import errno
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

from executor_birth_cutover import CurrentReceiptProof


SIGNATURE_DOMAIN = b"metnos.executor-birth.ownership-cutover/v1\0"
CUTOVER_ID_DOMAIN = b"metnos.executor-birth.ownership-cutover-id/v1\0"
CATALOG_ID_DOMAIN = b"metnos.executor-birth.current-catalog/v1\0"
PURPOSE = "ownership_cutover_v1"
PAYLOAD_BASENAME = "ownership-cutover-v1.json"
SIGNATURE_BASENAME = "ownership-cutover-v1.sig"
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_KEY_ID_RE = re.compile(r"birth-ed25519-v1-sha256-[0-9a-f]{64}\Z")
_PAYLOAD_KEYS = frozenset({
    "schema_version", "cutover_id", "previous_cutover_id", "request_id",
    "signing_key_id", "catalog_id", "current_count", "current_receipts",
    "maintenance_evidence_hash", "boundary_inventory_hash",
    "boundary_guard_version", "closed_build_id",
})
_RECEIPT_KEYS = frozenset({"contract_id", "generation_id", "receipt_hash"})
_OWNERSHIP_PURPOSES = frozenset({PURPOSE, "ownership_head_v1"})


class OwnershipCutoverError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def ownership_key_id(public_key: Ed25519PublicKey) -> str:
    """Derive the only admitted ownership key identifier from raw Ed25519."""
    if not isinstance(public_key, Ed25519PublicKey):
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "key registry")
    raw = public_key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    return "birth-ed25519-v1-sha256-" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class OwnershipCutoverKey:
    key_id: str
    public_key: Ed25519PublicKey
    purposes: frozenset[str]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.key_id, str) or _KEY_ID_RE.fullmatch(self.key_id) is None
            or not isinstance(self.public_key, Ed25519PublicKey)
            or self.key_id != ownership_key_id(self.public_key)
            or not isinstance(self.purposes, frozenset)
            or len(self.purposes) != 1
            or not self.purposes.issubset(_OWNERSHIP_PURPOSES)
        ):
            raise OwnershipCutoverError("birth_ownership_proof_invalid", "key registry")


@dataclass(frozen=True, slots=True)
class OwnershipCutoverRegistry:
    keys: Mapping[str, OwnershipCutoverKey]

    def __post_init__(self) -> None:
        values = dict(self.keys)
        if not values or any(
            key != entry.key_id or not isinstance(entry, OwnershipCutoverKey)
            for key, entry in values.items()
        ):
            raise OwnershipCutoverError("birth_ownership_proof_invalid", "key registry")
        object.__setattr__(self, "keys", MappingProxyType(values))


@dataclass(frozen=True, slots=True)
class OwnershipReceiptBinding:
    contract_id: str
    generation_id: str
    receipt_hash: str


@dataclass(frozen=True, slots=True)
class OwnershipCutoverCertificate:
    cutover_id: str
    previous_cutover_id: str | None
    request_id: str
    signing_key_id: str
    catalog_id: str
    current_receipts: tuple[OwnershipReceiptBinding, ...]
    maintenance_evidence_hash: str
    boundary_inventory_hash: str
    boundary_guard_version: str
    closed_build_id: str

    @property
    def current_count(self) -> int:
        return len(self.current_receipts)

    def as_proof(self) -> CurrentReceiptProof:
        return CurrentReceiptProof(
            tuple((item.contract_id, item.generation_id) for item in self.current_receipts),
            {(item.contract_id, item.generation_id): item.receipt_hash
             for item in self.current_receipts},
        )


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "json") from exc


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise OwnershipCutoverError("birth_ownership_proof_invalid", "duplicate key")
        result[key] = value
    return result


def _require_digest(value: object, field: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise OwnershipCutoverError("birth_ownership_proof_invalid", field)
    return value


def _bindings_from_proof(proof: CurrentReceiptProof) -> tuple[OwnershipReceiptBinding, ...]:
    if not isinstance(proof, CurrentReceiptProof):
        raise OwnershipCutoverError("birth_ownership_binding_invalid", "proof type")
    try:
        rebound = CurrentReceiptProof(proof.identities, proof.receipt_hashes)
    except Exception as exc:
        raise OwnershipCutoverError("birth_ownership_binding_invalid", "proof") from exc
    return tuple(
        OwnershipReceiptBinding(contract_id, generation_id, rebound.receipt_hashes[identity])
        for identity in rebound.identities
        for contract_id, generation_id in (identity,)
    )


def _binding_values(bindings: Iterable[OwnershipReceiptBinding]) -> list[dict[str, object]]:
    return [{
        "contract_id": item.contract_id,
        "generation_id": item.generation_id,
        "receipt_hash": item.receipt_hash,
    } for item in bindings]


def _catalog_id(values: list[dict[str, object]]) -> str:
    framed = bytearray(CATALOG_ID_DOMAIN)
    for value in values:
        encoded = _canonical(value)
        framed.extend(len(encoded).to_bytes(8, "big"))
        framed.extend(encoded)
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _cutover_id(unsigned: Mapping[str, object]) -> str:
    material = {key: value for key, value in unsigned.items() if key != "cutover_id"}
    return "sha256:" + hashlib.sha256(CUTOVER_ID_DOMAIN + _canonical(material)).hexdigest()


def issue_ownership_cutover_certificate(
    *, proof: CurrentReceiptProof, previous_cutover_id: str | None,
    request_id: str, signing_key_id: str,
    maintenance_evidence_hash: str, boundary_inventory_hash: str,
    boundary_guard_version: str, closed_build_id: str,
    private_key: Ed25519PrivateKey,
) -> tuple[bytes, bytes]:
    """Create the exact certificate bytes; persistence remains a separate step."""
    bindings = _bindings_from_proof(proof)
    receipts = _binding_values(bindings)
    for field, value in {
        "request_id": request_id,
        "maintenance_evidence_hash": maintenance_evidence_hash,
        "boundary_inventory_hash": boundary_inventory_hash,
        "closed_build_id": closed_build_id,
    }.items():
        _require_digest(value, field)
    _require_digest(previous_cutover_id, "previous_cutover_id", nullable=True)
    if _KEY_ID_RE.fullmatch(signing_key_id or "") is None:
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "signing_key_id")
    if (
        not isinstance(boundary_guard_version, str) or not boundary_guard_version
        or "\x00" in boundary_guard_version or len(boundary_guard_version.encode("utf-8")) > 128
        or not isinstance(private_key, Ed25519PrivateKey)
    ):
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "issuer")
    value: dict[str, object] = {
        "schema_version": 1, "cutover_id": None,
        "previous_cutover_id": previous_cutover_id, "request_id": request_id,
        "signing_key_id": signing_key_id, "catalog_id": _catalog_id(receipts),
        "current_count": len(receipts), "current_receipts": receipts,
        "maintenance_evidence_hash": maintenance_evidence_hash,
        "boundary_inventory_hash": boundary_inventory_hash,
        "boundary_guard_version": boundary_guard_version,
        "closed_build_id": closed_build_id,
    }
    value["cutover_id"] = _cutover_id(value)
    encoded = _canonical(value)
    return encoded, private_key.sign(SIGNATURE_DOMAIN + encoded)


def verify_ownership_cutover_certificate(
    encoded: bytes, signature: bytes, *, registry: OwnershipCutoverRegistry,
    expected_proof: CurrentReceiptProof | None = None,
    expected_previous_cutover_id: str | None | object = ...,
) -> OwnershipCutoverCertificate:
    """Authenticate canonical bytes, key purpose, chain and current proof."""
    if not isinstance(encoded, bytes) or len(encoded) > MAX_PAYLOAD_BYTES:
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "payload size")
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "signature size")
    try:
        value = json.loads(encoded.decode("ascii"), object_pairs_hook=_pairs)
    except OwnershipCutoverError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "json") from exc
    if not isinstance(value, dict) or set(value) != _PAYLOAD_KEYS or _canonical(value) != encoded:
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "schema")
    if value.get("schema_version") != 1 or isinstance(value.get("current_count"), bool):
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "schema version")
    key_id = value.get("signing_key_id")
    if not isinstance(registry, OwnershipCutoverRegistry) or not isinstance(key_id, str):
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "registry")
    entry = registry.keys.get(key_id)
    if entry is None or PURPOSE not in entry.purposes:
        raise OwnershipCutoverError("birth_ownership_key_unauthorized", str(key_id))
    try:
        entry.public_key.verify(signature, SIGNATURE_DOMAIN + encoded)
    except InvalidSignature as exc:
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "signature") from exc

    raw_receipts = value.get("current_receipts")
    if not isinstance(raw_receipts, list) or value.get("current_count") != len(raw_receipts):
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "current_count")
    bindings: list[OwnershipReceiptBinding] = []
    identities: list[tuple[str, str]] = []
    for raw in raw_receipts:
        if not isinstance(raw, dict) or set(raw) != _RECEIPT_KEYS:
            raise OwnershipCutoverError("birth_ownership_proof_invalid", "receipt schema")
        contract_id = raw.get("contract_id")
        if not isinstance(contract_id, str) or not contract_id or "\x00" in contract_id:
            raise OwnershipCutoverError("birth_ownership_proof_invalid", "contract_id")
        generation_id = _require_digest(raw.get("generation_id"), "generation_id")
        receipt_hash = _require_digest(raw.get("receipt_hash"), "receipt_hash")
        assert isinstance(generation_id, str) and isinstance(receipt_hash, str)
        identities.append((contract_id, generation_id))
        bindings.append(OwnershipReceiptBinding(contract_id, generation_id, receipt_hash))
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "receipt order")
    receipt_values = _binding_values(bindings)
    if value.get("catalog_id") != _catalog_id(receipt_values):
        raise OwnershipCutoverError("birth_ownership_binding_invalid", "catalog_id")
    if value.get("cutover_id") != _cutover_id(value):
        raise OwnershipCutoverError("birth_ownership_binding_invalid", "cutover_id")
    for field in (
        "cutover_id", "request_id", "catalog_id", "maintenance_evidence_hash",
        "boundary_inventory_hash", "closed_build_id",
    ):
        _require_digest(value.get(field), field)
    previous = _require_digest(value.get("previous_cutover_id"), "previous_cutover_id", nullable=True)
    guard_version = value.get("boundary_guard_version")
    if (not isinstance(guard_version, str) or not guard_version or "\x00" in guard_version
            or len(guard_version.encode("utf-8")) > 128):
        raise OwnershipCutoverError("birth_ownership_proof_invalid", "boundary_guard_version")
    certificate = OwnershipCutoverCertificate(
        str(value["cutover_id"]), previous, str(value["request_id"]), key_id,
        str(value["catalog_id"]), tuple(bindings),
        str(value["maintenance_evidence_hash"]), str(value["boundary_inventory_hash"]),
        guard_version, str(value["closed_build_id"]),
    )
    if expected_previous_cutover_id is not ... and previous != expected_previous_cutover_id:
        raise OwnershipCutoverError("birth_ownership_binding_invalid", "previous_cutover_id")
    if expected_proof is not None:
        expected = _bindings_from_proof(expected_proof)
        if certificate.current_receipts != expected:
            raise OwnershipCutoverError("birth_ownership_binding_invalid", "current proof")
    return certificate


def _safe_directory(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    for component in reversed((absolute, *absolute.parents)):
        try:
            component_info = component.lstat()
        except OSError as exc:
            raise OwnershipCutoverError(
                "birth_ownership_recovery_required", str(component),
            ) from exc
        if (
            stat.S_ISLNK(component_info.st_mode)
            or bool(getattr(component_info, "st_file_attributes", 0) & 0x400)
            or (hasattr(component, "is_junction") and component.is_junction())
        ):
            raise OwnershipCutoverError(
                "birth_ownership_recovery_required", str(component),
            )
    try:
        info = absolute.lstat()
    except OSError as exc:
        raise OwnershipCutoverError("birth_ownership_recovery_required", str(absolute)) from exc
    reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or reparse:
        raise OwnershipCutoverError("birth_ownership_recovery_required", str(absolute))
    if os.name != "nt" and info.st_mode & 0o022:
        raise OwnershipCutoverError("birth_ownership_recovery_required", "unsafe directory mode")


def _safe_read(path: Path, maximum: int) -> bytes:
    # Reuse the already certified POSIX/Win32 handle reader.  Translate its
    # subsystem-specific exception at this ownership boundary.
    from executor_birth_semantic_authority import _secure_file_bytes
    deadline = time.monotonic() + (2.0 if os.name == "nt" else 0.0)
    while True:
        try:
            return _secure_file_bytes(path, maximum=maximum, error="unsafe")
        except Exception as exc:
            # NTFS metadata and filter drivers can briefly report a freshly
            # renamed file as unavailable or with transitional metadata.  We
            # never accept that observation: retry the complete handle-based
            # verification for a finite interval and fail closed if no single
            # stable, regular, one-link observation succeeds.  A real hard
            # link, reparse point or tamper therefore remains rejected.
            if os.name != "nt" or time.monotonic() >= deadline:
                raise OwnershipCutoverError(
                    "birth_ownership_recovery_required", path.name,
                ) from exc
            time.sleep(0.01)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_temporary(path: Path, payload: bytes) -> None:
    # Ed25519 signatures are arbitrary bytes.  Without O_BINARY the Windows
    # CRT expands any 0x0a byte to CRLF, changing both length and signature.
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    )
    fd = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
        if os.name != "nt":
            os.fchmod(fd, 0o644)
            os.fsync(fd)
    finally:
        os.close(fd)


def _prepare_recoverable_temporary(
    temporary: Path, destination: Path, expected: bytes,
) -> bool:
    """Prepare a pre-publication file; discard only an exact write prefix."""
    if destination.exists():
        if _safe_read(destination, len(expected)) != expected:
            raise OwnershipCutoverError(
                "birth_ownership_cutover_conflict", destination.name,
            )
        if temporary.exists():
            observed = _safe_read(temporary, len(expected))
            if observed != expected and not (
                len(observed) < len(expected) and expected.startswith(observed)
            ):
                raise OwnershipCutoverError(
                    "birth_ownership_cutover_conflict", temporary.name,
                )
            temporary.unlink()
            _sync_directory(temporary.parent)
        return False
    if temporary.exists():
        observed = _safe_read(temporary, len(expected))
        if observed == expected:
            return True
        if len(observed) >= len(expected) or not expected.startswith(observed):
            raise OwnershipCutoverError(
                "birth_ownership_cutover_conflict", temporary.name,
            )
        temporary.unlink()
        _sync_directory(temporary.parent)
    _write_temporary(temporary, expected)
    return True


def _publish_no_replace(temporary: Path, destination: Path, expected: bytes) -> None:
    try:
        if os.name == "nt":
            # Python maps this to MoveFileEx without REPLACE_EXISTING.
            os.rename(temporary, destination)
        elif sys.platform.startswith("linux"):
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = getattr(libc, "renameat2", None)
            if renameat2 is None:
                raise OSError(errno.ENOSYS, "renameat2 unavailable")
            renameat2.argtypes = (
                ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                ctypes.c_uint,
            )
            renameat2.restype = ctypes.c_int
            result = renameat2(
                -100, os.fsencode(temporary), -100, os.fsencode(destination), 1,
            )
            if result != 0:
                number = ctypes.get_errno()
                if number == errno.EEXIST:
                    raise FileExistsError(number, os.strerror(number), destination)
                raise OSError(number, os.strerror(number), destination)
        else:
            raise OSError(errno.ENOSYS, "no-replace rename unavailable")
    except FileExistsError:
        if _safe_read(destination, len(expected)) != expected:
            raise OwnershipCutoverError("birth_ownership_cutover_conflict", destination.name)
    except OSError as exc:
        raise OwnershipCutoverError("birth_ownership_recovery_required", destination.name) from exc
    else:
        _sync_directory(destination.parent)


def install_ownership_cutover_certificate(
    directory: Path, encoded: bytes, signature: bytes, *,
    registry: OwnershipCutoverRegistry, expected_proof: CurrentReceiptProof,
    _crash_seam=None,
) -> OwnershipCutoverCertificate:
    """Install signature then payload without replacement; exact retries succeed."""
    directory = Path(directory)
    _safe_directory(directory)
    certificate = verify_ownership_cutover_certificate(
        encoded, signature, registry=registry, expected_proof=expected_proof,
    )
    payload_path = directory / PAYLOAD_BASENAME
    signature_path = directory / SIGNATURE_BASENAME
    if payload_path.exists() and not signature_path.exists():
        raise OwnershipCutoverError("birth_ownership_recovery_required", "payload without signature")
    suffix = certificate.request_id.removeprefix("sha256:")
    signature_tmp = directory / f".{SIGNATURE_BASENAME}.{suffix}.tmp"
    payload_tmp = directory / f".{PAYLOAD_BASENAME}.{suffix}.tmp"
    try:
        if _prepare_recoverable_temporary(
            signature_tmp, signature_path, signature,
        ):
            _publish_no_replace(signature_tmp, signature_path, signature)
        if _crash_seam:
            _crash_seam("certificate_signature")
        if _prepare_recoverable_temporary(
            payload_tmp, payload_path, encoded,
        ):
            _publish_no_replace(payload_tmp, payload_path, encoded)
        if _crash_seam:
            _crash_seam("certificate_payload")
        return read_ownership_cutover_certificate(
            directory, registry=registry, expected_proof=expected_proof,
        )
    finally:
        for temporary in (signature_tmp, payload_tmp):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def read_ownership_cutover_certificate(
    directory: Path, *, registry: OwnershipCutoverRegistry,
    expected_proof: CurrentReceiptProof | None = None,
) -> OwnershipCutoverCertificate:
    directory = Path(directory)
    _safe_directory(directory)
    payload_path = directory / PAYLOAD_BASENAME
    signature_path = directory / SIGNATURE_BASENAME
    if not payload_path.exists() and not signature_path.exists():
        raise OwnershipCutoverError("birth_ownership_proof_missing")
    if not payload_path.exists() or not signature_path.exists():
        raise OwnershipCutoverError("birth_ownership_recovery_required", "incomplete pair")
    encoded = _safe_read(payload_path, MAX_PAYLOAD_BYTES)
    signature = _safe_read(signature_path, 64)
    return verify_ownership_cutover_certificate(
        encoded, signature, registry=registry, expected_proof=expected_proof,
    )


__all__ = [
    "OwnershipCutoverCertificate", "OwnershipCutoverError", "OwnershipCutoverKey",
    "OwnershipCutoverRegistry", "OwnershipReceiptBinding", "PURPOSE",
    "install_ownership_cutover_certificate", "issue_ownership_cutover_certificate",
    "ownership_key_id",
    "read_ownership_cutover_certificate", "verify_ownership_cutover_certificate",
]
