"""Public-only custody boundary for the optional F6 retention authority.

Provisioning belongs to the administrative installer. Loading this key does
not sign receipts, activate F6 or participate in ordinary F4 startup.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from executor_birth_canonical import (
    CanonicalDocumentError, decode_canonical_ascii_v1, encode_canonical_ascii_v1,
)
from executor_birth_authority_files import (
    DEFAULT_OWNERSHIP_ROOT_V1, OwnershipAuthorityError,
    _directory_metadata, _managed_authority_platform_supported_v1,
    _read_regular, _root_owned_chain,
)
from executor_birth_keystore import birth_key_id


PURPOSE_V1 = "f6_retention_receipt_v1"
DIRECTORY_BASENAME_V1 = "retention-authority-v1"
DEFAULT_DIRECTORY_V1 = DEFAULT_OWNERSHIP_ROOT_V1 / DIRECTORY_BASENAME_V1
PRIVATE_BASENAME_V1 = "private.bin"
REGISTRY_BASENAME_V1 = "registry.json"
HISTORY_BASENAME_V1 = "public-history"
ROTATION_BASENAME_V1 = ".rotation"
MAX_REGISTRY_BYTES_V1 = 4096
_FIELDS = {"schema_version", "purpose", "key_id", "public_key", "status"}


@dataclass(frozen=True, slots=True)
class RetentionPublicKeyV1:
    """Decoded verification material, not a deletion permission or an activation."""

    key_id: str
    public_key: Ed25519PublicKey
    status: str


def encode_retention_registry_v1(
    public_key: Ed25519PublicKey, *, status: str = "active",
) -> bytes:
    if not isinstance(public_key, Ed25519PublicKey) or status not in ("active", "revoked"):
        raise OwnershipAuthorityError("birth_retention_authority_invalid", "key or status")
    return encode_canonical_ascii_v1({
        "schema_version": 1,
        "purpose": PURPOSE_V1,
        "key_id": birth_key_id(public_key),
        "public_key": base64.b64encode(public_key.public_bytes_raw()).decode("ascii"),
        "status": status,
    })


def decode_retention_registry_v1(encoded: bytes) -> RetentionPublicKeyV1:
    """Reject any other authority, ambiguous encoding or extra capability."""
    try:
        value = decode_canonical_ascii_v1(encoded, maximum=MAX_REGISTRY_BYTES_V1)
        if (type(value) is not dict or set(value) != _FIELDS
                or type(value["schema_version"]) is not int
                or value["schema_version"] != 1 or value["purpose"] != PURPOSE_V1
                or value["status"] not in ("active", "revoked")
                or type(value["public_key"]) is not str):
            raise ValueError("registry schema")
        raw = base64.b64decode(value["public_key"], validate=True)
        if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != value["public_key"]:
            raise ValueError("public key")
        public = Ed25519PublicKey.from_public_bytes(raw)
        if value["key_id"] != birth_key_id(public):
            raise ValueError("key id")
    except (CanonicalDocumentError, ValueError, TypeError, RecursionError) as exc:
        raise OwnershipAuthorityError("birth_retention_authority_invalid") from exc
    return RetentionPublicKeyV1(value["key_id"], public, value["status"])


def _load_retention_public_at_v1(
    directory: Path, *, root_owned: bool,
) -> RetentionPublicKeyV1:
    """Shared filesystem reader; only the fixed entry is productive."""
    _directory_metadata(directory, root_owned=root_owned)
    if (directory / ROTATION_BASENAME_V1).exists() or (directory / ROTATION_BASENAME_V1).is_symlink():
        raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "rotation")
    return decode_retention_registry_v1(_read_regular(
        directory / REGISTRY_BASENAME_V1,
        maximum=MAX_REGISTRY_BYTES_V1, mode=0o644, root_owned=root_owned,
    ))


def _load_retention_archive_at_v1(directory: Path, *, root_owned: bool) -> tuple[RetentionPublicKeyV1, ...]:
    history = directory / HISTORY_BASENAME_V1
    keys = {}
    if history.exists() or history.is_symlink():
        _directory_metadata(history, root_owned=root_owned)
        for index, path in enumerate(history.iterdir()):
            if index >= 1024:
                raise OwnershipAuthorityError("birth_retention_authority_invalid", "key history limit")
            public = decode_retention_registry_v1(_read_regular(
                path, maximum=MAX_REGISTRY_BYTES_V1, mode=0o644, root_owned=root_owned,
            ))
            if path.name != public.key_id + ".json":
                raise OwnershipAuthorityError("birth_retention_authority_invalid", "key history identity")
            keys[public.key_id] = public
    return tuple(keys[key] for key in sorted(keys))


def _load_retention_history_at_v1(directory: Path, *, root_owned: bool) -> tuple[RetentionPublicKeyV1, ...]:
    """Historical keys verify receipts; the current registry chooses signing.

    Current revocation cannot be hidden by a historical active registry.
    """
    current = _load_retention_public_at_v1(directory, root_owned=root_owned)
    keys = {key.key_id: key for key in _load_retention_archive_at_v1(directory, root_owned=root_owned)}
    keys[current.key_id] = current
    return tuple(keys[key] for key in sorted(keys))


def load_retention_public_key_v1() -> RetentionPublicKeyV1:
    """Read current, active public trust without opening any private key.

    The managed authority surface follows the existing Linux deployment
    boundary. No caller path, environment override or implicit provisioning
    is accepted. Absence or revocation affects this F6 entry only.
    """
    if not _managed_authority_platform_supported_v1():
        raise OwnershipAuthorityError("birth_retention_authority_platform_unsupported")
    _root_owned_chain(DEFAULT_DIRECTORY_V1)
    public = _load_retention_public_at_v1(DEFAULT_DIRECTORY_V1, root_owned=True)
    if public.status != "active":
        raise OwnershipAuthorityError("birth_retention_authority_revoked")
    return public


def load_retention_verification_keys_v1() -> tuple[RetentionPublicKeyV1, ...]:
    if not _managed_authority_platform_supported_v1():
        raise OwnershipAuthorityError("birth_retention_authority_platform_unsupported")
    _root_owned_chain(DEFAULT_DIRECTORY_V1)
    return _load_retention_history_at_v1(DEFAULT_DIRECTORY_V1, root_owned=True)


__all__ = [
    "RetentionPublicKeyV1", "decode_retention_registry_v1",
    "encode_retention_registry_v1", "load_retention_public_key_v1",
    "load_retention_verification_keys_v1",
]
