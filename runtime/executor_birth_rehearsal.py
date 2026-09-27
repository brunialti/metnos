"""Short-lived F5 rehearsal authorization, distinct from qualification.

The existing certification owner signs this purpose only in a native isolated
installation. Every use rereads confinement, the required release and expiry.
No environment switch, alternate path, key or background worker is involved.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import os
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from executor_birth_authority_files import _directory_metadata, _read_regular, _root_owned_chain
from executor_birth_canonical import decode_canonical_ascii_v1, encode_canonical_ascii_v1
from executor_birth_certification_authority import CertificationPublicKeyV1
from executor_birth_lifecycle import (
    ACTIVATION_DIRECTORY, ACTIVATION_MAX_BYTES, LifecycleError, _digest,
    _installation_id_v1,
)
from executor_birth_rehearsal_isolation import observe_local_rehearsal_v1


REHEARSAL_DOMAIN_V1 = b"metnos.executor-birth.f5-rehearsal/v1\0"
REHEARSAL_POLICY_V1 = "rm0008-f5-rehearsal/1"
REHEARSAL_BASENAME_V1 = "rehearsal.json"
REHEARSAL_SECONDS_V1 = 3600
_REHEARSAL_SEAL = object()
_BINDINGS = ("installation_id", "head_id", "closed_build_id", "migration_id",
             "profile_id", "source_id", "isolation_id")
_CLOCKS = ("issued_at", "expires_at", "issued_boot_ns", "expires_boot_ns")


@dataclass(frozen=True, slots=True)
class F5RehearsalPermitV1:
    permit_id: str
    installation_id: str
    head_id: str
    closed_build_id: str
    migration_id: str
    profile_id: str
    source_id: str
    isolation_id: str
    issued_at: int
    expires_at: int
    issued_boot_ns: int
    expires_boot_ns: int
    key_id: str


class F5Rehearsal:
    """Authenticated rehearsal only; never an F5Activation or qualification."""
    __slots__ = ("permit",)

    def __init__(self, permit: F5RehearsalPermitV1, *, _seal: object):
        if _seal is not _REHEARSAL_SEAL:
            raise LifecycleError("f5_rehearsal_forbidden")
        self.permit = permit


def _clock_v1() -> tuple[int, int]:
    # BOOTTIME includes suspension. The isolation identity also binds the boot;
    # a wall-clock rollback cannot extend this permit or revive it on reboot.
    return int(time.time()), time.clock_gettime_ns(time.CLOCK_BOOTTIME)


def _decode_rehearsal_v1(
    encoded: bytes, *, authority: CertificationPublicKeyV1,
    installation_id: str, head_id: str, closed_build_id: str,
    migration_id: str, isolation_id: str, now: tuple[int, int],
) -> F5Rehearsal:
    """Private codec seam; installed readers supply all trust and native facts."""
    try:
        value = decode_canonical_ascii_v1(encoded, maximum=ACTIVATION_MAX_BYTES)
        fields = {*_BINDINGS, *_CLOCKS, "schema_version", "purpose", "policy_id", "key_id", "signature"}
        if (type(value) is not dict or set(value) != fields
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["purpose"] != "f5_rehearsal_v1"
                or value["policy_id"] != REHEARSAL_POLICY_V1):
            raise ValueError("schema or purpose")
        for name in _BINDINGS:
            _digest(value[name], name)
        if any(value[name] != expected for name, expected in (
            ("installation_id", installation_id), ("head_id", head_id),
            ("closed_build_id", closed_build_id), ("migration_id", migration_id),
            ("isolation_id", isolation_id),
        )):
            raise ValueError("binding")
        if (any(type(value[name]) is not int or value[name] < 0 for name in _CLOCKS)
                or value["expires_at"] - value["issued_at"] != REHEARSAL_SECONDS_V1
                or value["expires_boot_ns"] - value["issued_boot_ns"] != REHEARSAL_SECONDS_V1 * 10**9
                or not value["issued_at"] <= now[0] < value["expires_at"]
                or not value["issued_boot_ns"] <= now[1] < value["expires_boot_ns"]):
            raise ValueError("expired or invalid lifetime")
        if (type(authority) is not CertificationPublicKeyV1 or authority.status != "active"
                or not isinstance(authority.public_key, Ed25519PublicKey)
                or value["key_id"] != authority.key_id or type(value["signature"]) is not str):
            raise ValueError("authority")
        signature = base64.b64decode(value["signature"], validate=True)
        if len(signature) != 64 or base64.b64encode(signature).decode("ascii") != value["signature"]:
            raise ValueError("signature")
        payload = REHEARSAL_DOMAIN_V1 + encode_canonical_ascii_v1({
            key: item for key, item in value.items() if key != "signature"})
        authority.public_key.verify(signature, payload)
    except (ValueError, TypeError, InvalidSignature, LifecycleError, RecursionError) as exc:
        raise LifecycleError("f5_rehearsal_invalid", "document, binding, authority or expiry") from exc
    permit = F5RehearsalPermitV1(
        permit_id="sha256:" + hashlib.sha256(payload).hexdigest(),
        **{name: value[name] for name in (*_BINDINGS, *_CLOCKS, "key_id")},
    )
    return F5Rehearsal(permit, _seal=_REHEARSAL_SEAL)


def load_f5_rehearsal() -> F5Rehearsal:
    """Authenticate the fixed local permit; re-observe isolation on every use."""
    from executor_birth_activation_mode import _marker_bytes, decode_migration_marker_v1
    from executor_birth_certification_authority import load_certification_public_key_v1
    from executor_birth_ownership_authorities import load_ownership_public_registries_v1
    from executor_birth_ownership_chain import inspect_required_ownership_v1, VerifiedOwnershipWindowV1

    isolation = observe_local_rehearsal_v1()
    public = load_certification_public_key_v1()
    installation = _installation_id_v1(load_ownership_public_registries_v1())
    marker = _marker_bytes()
    if marker is None:
        raise LifecycleError("f5_epoch_migration_required")
    marker_installation, migration = decode_migration_marker_v1(marker)
    if marker_installation != installation:
        raise LifecycleError("f5_rehearsal_invalid", "migration installation")
    window = inspect_required_ownership_v1()
    if type(window) is not VerifiedOwnershipWindowV1:
        raise LifecycleError("f5_rehearsal_invalid", "required ownership window")
    _root_owned_chain(ACTIVATION_DIRECTORY)
    _directory_metadata(ACTIVATION_DIRECTORY, root_owned=True)
    path = ACTIVATION_DIRECTORY / REHEARSAL_BASENAME_V1
    encoded = _read_regular(path, maximum=ACTIVATION_MAX_BYTES, mode=0o644, root_owned=True)
    reread_public = load_certification_public_key_v1()
    reread_window = inspect_required_ownership_v1()
    if (observe_local_rehearsal_v1() != isolation or _marker_bytes() != marker
            or _read_regular(path, maximum=ACTIVATION_MAX_BYTES, mode=0o644, root_owned=True) != encoded
            or (reread_public.key_id, reread_public.status, reread_public.public_key.public_bytes_raw())
            != (public.key_id, public.status, public.public_key.public_bytes_raw())
            or _installation_id_v1(load_ownership_public_registries_v1()) != installation
            or type(reread_window) is not VerifiedOwnershipWindowV1
            or reread_window.required_head != window.required_head
            or reread_window.required_distribution.identity != window.required_distribution.identity):
        raise LifecycleError("f5_rehearsal_invalid", "changed authorization frontier")
    return _decode_rehearsal_v1(
        encoded, authority=public, installation_id=installation,
        head_id=window.required_head.head_id,
        closed_build_id=window.required_distribution.identity.closed_build_id,
        migration_id=migration, isolation_id=isolation.observation_id, now=_clock_v1(),
    )


def require_f5_lifecycle_authorization():
    """Select an explicit lab permit, or the unchanged productive certificate.

    A present but invalid/expired permit refuses. No failed productive
    certification silently switches the installation into rehearsal mode.
    """
    from executor_birth_activation_mode import require_f5_certificate

    try:
        os.lstat(ACTIVATION_DIRECTORY / REHEARSAL_BASENAME_V1)
    except FileNotFoundError:
        return require_f5_certificate()
    return load_f5_rehearsal()
