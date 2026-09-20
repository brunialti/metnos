"""Public-only custody boundary for the optional F5 certification authority.

Provisioning belongs to the administrative installer. Loading this key does
not certify evidence, activate F5 or participate in ordinary F4 startup.
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


PURPOSE_V1 = "f5_certification_v1"
DIRECTORY_BASENAME_V1 = "certification-authority-v1"
DEFAULT_DIRECTORY_V1 = DEFAULT_OWNERSHIP_ROOT_V1 / DIRECTORY_BASENAME_V1
PRIVATE_BASENAME_V1 = "private.bin"
REGISTRY_BASENAME_V1 = "registry.json"
MAX_REGISTRY_BYTES_V1 = 4096
_FIELDS = {"schema_version", "purpose", "key_id", "public_key", "status"}


@dataclass(frozen=True, slots=True)
class CertificationPublicKeyV1:
    """Decoded verification material, not a certification or an activation."""

    key_id: str
    public_key: Ed25519PublicKey
    status: str


def encode_certification_registry_v1(
    public_key: Ed25519PublicKey, *, status: str = "active",
) -> bytes:
    if not isinstance(public_key, Ed25519PublicKey) or status not in ("active", "revoked"):
        raise OwnershipAuthorityError("birth_certification_authority_invalid", "key or status")
    return encode_canonical_ascii_v1({
        "schema_version": 1,
        "purpose": PURPOSE_V1,
        "key_id": birth_key_id(public_key),
        "public_key": base64.b64encode(public_key.public_bytes_raw()).decode("ascii"),
        "status": status,
    })


def decode_certification_registry_v1(encoded: bytes) -> CertificationPublicKeyV1:
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
        raise OwnershipAuthorityError("birth_certification_authority_invalid") from exc
    return CertificationPublicKeyV1(value["key_id"], public, value["status"])


def _load_certification_public_at_v1(
    directory: Path, *, root_owned: bool,
) -> CertificationPublicKeyV1:
    """Shared filesystem reader; only the fixed entry is productive."""
    _directory_metadata(directory, root_owned=root_owned)
    return decode_certification_registry_v1(_read_regular(
        directory / REGISTRY_BASENAME_V1,
        maximum=MAX_REGISTRY_BYTES_V1, mode=0o644, root_owned=root_owned,
    ))


def load_certification_public_key_v1() -> CertificationPublicKeyV1:
    """Read current, active public trust without opening any private key.

    The managed authority surface follows the existing Linux deployment
    boundary. No caller path, environment override or implicit provisioning
    is accepted. Absence or revocation affects this F5 entry only.
    """
    if not _managed_authority_platform_supported_v1():
        raise OwnershipAuthorityError("birth_certification_authority_platform_unsupported")
    _root_owned_chain(DEFAULT_DIRECTORY_V1)
    public = _load_certification_public_at_v1(DEFAULT_DIRECTORY_V1, root_owned=True)
    if public.status != "active":
        raise OwnershipAuthorityError("birth_certification_authority_revoked")
    return public


__all__ = [
    "CertificationPublicKeyV1", "decode_certification_registry_v1",
    "encode_certification_registry_v1", "load_certification_public_key_v1",
]
