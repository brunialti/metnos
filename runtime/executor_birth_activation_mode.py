"""One durable answer to which lifecycle state this installation owns.

An ordinary F4 installation has no migration marker, so the reader returns the
legacy owner after a single ``lstat`` and never opens a certificate, a private
key or a database.  The administrative migration leaves a root-owned marker
once it has retired the name-based writers; from then on the epoch store is
authoritative and an absent or revoked certificate refuses only the F5
operations that require a derived qualification, never the whole service and
never a silent return to the retired legacy state.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import re
import stat

from executor_birth_authority_files import (
    OwnershipAuthorityError, _directory_metadata, _read_regular, _root_owned_chain,
    _managed_authority_platform_supported_v1,
)
from executor_birth_canonical import (
    CanonicalDocumentError, decode_canonical_ascii_v1, encode_canonical_ascii_v1,
)
from executor_birth_lifecycle import (
    ACTIVATION_DIRECTORY, F5Certification, LifecycleError, load_f5_activation,
)


MIGRATION_BASENAME_V1 = "migration.json"
MIGRATION_PURPOSE_V1 = "f5_migration_v1"
MIGRATION_MAX_BYTES_V1 = 4096
_MIGRATION_FIELDS = {
    "schema_version", "purpose", "installation_id", "migration_id", "completed_at",
}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


class BirthStateOwner(str, Enum):
    """Which store answers "what is this generation's lifecycle?"."""

    LEGACY = "legacy"
    EPOCH = "epoch"


@dataclass(frozen=True, slots=True)
class BirthActivationState:
    """Observed installation state; never a qualification or an authority."""

    owner: BirthStateOwner
    migration_id: str | None
    certificate: F5Certification | None
    certificate_refusal: str | None


def encode_migration_marker_v1(*, installation_id: str, migration_id: str,
                               completed_at: str) -> bytes:
    """Encode the marker the administrative migration installs at cutover."""
    for field, value, pattern in (
        ("installation_id", installation_id, _DIGEST),
        ("migration_id", migration_id, _DIGEST),
        ("completed_at", completed_at, _UTC),
    ):
        if type(value) is not str or pattern.fullmatch(value) is None:
            raise LifecycleError("f5_migration_marker_invalid", field)
    return encode_canonical_ascii_v1({
        "schema_version": 1,
        "purpose": MIGRATION_PURPOSE_V1,
        "installation_id": installation_id,
        "migration_id": migration_id,
        "completed_at": completed_at,
    })


def decode_migration_marker_v1(encoded: bytes) -> tuple[str, str]:
    """Return the marker's exact installation and migration identities."""
    try:
        value = decode_canonical_ascii_v1(encoded, maximum=MIGRATION_MAX_BYTES_V1)
        if (type(value) is not dict or set(value) != _MIGRATION_FIELDS
                or type(value["schema_version"]) is not int
                or value["schema_version"] != 1
                or value["purpose"] != MIGRATION_PURPOSE_V1
                or type(value["completed_at"]) is not str
                or _UTC.fullmatch(value["completed_at"]) is None):
            raise ValueError("schema")
        for field in ("installation_id", "migration_id"):
            if (type(value[field]) is not str
                    or _DIGEST.fullmatch(value[field]) is None):
                raise ValueError(field)
    except (CanonicalDocumentError, TypeError, ValueError, RecursionError) as exc:
        raise LifecycleError("f5_migration_marker_invalid", "document") from exc
    return value["installation_id"], value["migration_id"]


def _marker_bytes() -> bytes | None:
    """Read the root-owned marker, or report its ordinary absence as None."""
    if not _managed_authority_platform_supported_v1():
        return None
    path = ACTIVATION_DIRECTORY / MIGRATION_BASENAME_V1
    try:
        present = stat.S_ISREG(os.lstat(path).st_mode)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LifecycleError("f5_migration_marker_invalid", "probe") from exc
    if not present:
        # Something other than the marker occupies its exact name.  That is an
        # installation fault, not the absence this reader reports as legacy.
        raise LifecycleError("f5_migration_marker_invalid", "not a regular file")
    try:
        _root_owned_chain(ACTIVATION_DIRECTORY)
        _directory_metadata(ACTIVATION_DIRECTORY, root_owned=True)
        return _read_regular(path, maximum=MIGRATION_MAX_BYTES_V1,
                             mode=0o644, root_owned=True)
    except OwnershipAuthorityError as exc:
        raise LifecycleError("f5_migration_marker_invalid", exc.code) from exc


def read_birth_activation_state() -> BirthActivationState:
    """Resolve the owning store, then the optional derived qualification.

    An unreadable or inconsistent marker refuses the selection instead of
    guessing: the caller cannot know which store is authoritative, and both
    answers would be wrong.
    """
    encoded = _marker_bytes()
    if encoded is None:
        return BirthActivationState(BirthStateOwner.LEGACY, None, None, None)
    installation_id, migration_id = decode_migration_marker_v1(encoded)
    try:
        certificate = load_f5_activation().certificate
    except (LifecycleError, OwnershipAuthorityError, OSError) as exc:
        return BirthActivationState(
            BirthStateOwner.EPOCH, migration_id, None,
            getattr(exc, "code", "f5_activation_unavailable"),
        )
    if (certificate.migration_id != migration_id
            or certificate.installation_id != installation_id):
        return BirthActivationState(BirthStateOwner.EPOCH, migration_id, None,
                                    "f5_activation_migration_mismatch")
    return BirthActivationState(BirthStateOwner.EPOCH, migration_id, certificate, None)


def require_f5_certificate() -> F5Certification:
    """Authorize one F5 operation that needs the derived qualification."""
    state = read_birth_activation_state()
    if state.owner is BirthStateOwner.LEGACY:
        raise LifecycleError("f5_epoch_migration_required")
    if state.certificate is None:
        raise LifecycleError("f5_activation_invalid", state.certificate_refusal or "")
    return state.certificate


__all__ = [
    "BirthActivationState", "BirthStateOwner", "decode_migration_marker_v1",
    "encode_migration_marker_v1", "read_birth_activation_state",
    "require_f5_certificate",
]
