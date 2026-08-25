"""Fail-closed F4 startup preflight for the closed Executor Birth build.

The certificate codec lives in :mod:`executor_birth_ownership_cutover`.  This
module performs the independent checks needed by a root-owned ``ExecStartPre``
after that certificate exists.  The separate
:mod:`executor_birth_distribution_manifest` verifier supplies the authenticated
and sealed build identity; this module never selects distribution authority.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from executor_birth_cutover import CurrentReceiptProof
from executor_birth_ownership_cutover import (
    MAX_PAYLOAD_BYTES,
    PAYLOAD_BASENAME,
    SIGNATURE_BASENAME,
    OwnershipCutoverCertificate,
    OwnershipCutoverError,
    OwnershipCutoverRegistry,
    read_ownership_cutover_certificate,
)


MAINTENANCE_DOMAIN = b"metnos.executor-birth.maintenance-proof/v1\0"
DEFAULT_CERTIFICATE_DIRECTORY = Path("/var/lib/metnos/executor-birth")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SAFE_TEXT_RE = re.compile(r"[^\x00]{1,256}\Z")
_MAINTENANCE_KEYS = frozenset({"schema_version", "source", "units"})
_UNIT_KEYS = frozenset({
    "scope", "unit", "load_state", "active_state", "main_pid",
})
_QUIESCENT_STATES = frozenset({"inactive", "failed"})
# This is the closed enum already accepted by contract_cutover_guard.
MAINTENANCE_SOURCES = frozenset({
    "inactive_http_and_inactive_sidecar",
    "inactive_http_and_sidecar_broker",
})


class OwnershipPreflightError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


_BUILD_AUTHORITY_SEAL = object()


@dataclass(frozen=True, slots=True)
class ClosedBuildIdentity:
    """Values authenticated by the external signed-build verifier.

    The module-private seal prevents ordinary callers from turning claimed
    digests into authority.  Production creation remains reserved for the
    root-owned signed-distribution verifier; :func:`preflight_closed_build`
    refuses an unsealed value.
    """

    closed_build_id: str
    boundary_inventory_hash: str
    boundary_guard_version: str
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _BUILD_AUTHORITY_SEAL:
            raise OwnershipPreflightError("birth_ownership_build_authority_missing")
        if _DIGEST_RE.fullmatch(self.closed_build_id or "") is None:
            raise OwnershipPreflightError("birth_ownership_build_invalid", "closed_build_id")
        if _DIGEST_RE.fullmatch(self.boundary_inventory_hash or "") is None:
            raise OwnershipPreflightError(
                "birth_ownership_build_invalid", "boundary_inventory_hash",
            )
        if not _safe_text(self.boundary_guard_version, maximum=128):
            raise OwnershipPreflightError(
                "birth_ownership_build_invalid", "boundary_guard_version",
            )


def _sealed_build_identity_for_test(
    closed_build_id: str, boundary_inventory_hash: str,
    boundary_guard_version: str,
) -> ClosedBuildIdentity:
    """Test seam; production creation belongs to the distribution verifier."""
    return ClosedBuildIdentity(
        closed_build_id, boundary_inventory_hash, boundary_guard_version,
        _BUILD_AUTHORITY_SEAL,
    )


@dataclass(frozen=True, slots=True)
class OwnershipStartupAttestation:
    cutover_id: str
    closed_build_id: str
    catalog_id: str
    current_count: int


def _safe_text(value: object, *, maximum: int = 256) -> bool:
    return (
        isinstance(value, str)
        and _SAFE_TEXT_RE.fullmatch(value) is not None
        and len(value.encode("utf-8")) <= maximum
    )


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise OwnershipPreflightError("birth_ownership_maintenance_invalid") from exc


def _unique_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise OwnershipPreflightError(
                "birth_ownership_maintenance_invalid", "duplicate key",
            )
        result[key] = value
    return result


def canonical_maintenance_proof(
    *, source: str, units: Iterable[Mapping[str, object]],
) -> bytes:
    """Validate and encode the exact RM-0008 maintenance-proof document."""
    if source not in MAINTENANCE_SOURCES:
        raise OwnershipPreflightError("birth_ownership_maintenance_invalid", "source")
    normalized: list[dict[str, object]] = []
    identities: list[tuple[str, str]] = []
    try:
        materialized = tuple(units)
    except TypeError as exc:
        raise OwnershipPreflightError("birth_ownership_maintenance_invalid", "units") from exc
    for raw in materialized:
        if not isinstance(raw, Mapping) or set(raw) != _UNIT_KEYS:
            raise OwnershipPreflightError("birth_ownership_maintenance_invalid", "unit schema")
        scope = raw.get("scope")
        unit = raw.get("unit")
        load_state = raw.get("load_state")
        active_state = raw.get("active_state")
        main_pid = raw.get("main_pid")
        if (
            scope not in {"system", "user"}
            or not _safe_text(unit, maximum=256)
            or not _safe_text(load_state, maximum=64)
            or active_state not in _QUIESCENT_STATES
            or isinstance(main_pid, bool)
            or not isinstance(main_pid, int)
            or main_pid != 0
        ):
            raise OwnershipPreflightError("birth_ownership_not_quiescent", str(unit or ""))
        identity = (scope, unit)
        identities.append(identity)
        normalized.append({
            "scope": scope,
            "unit": unit,
            "load_state": load_state,
            "active_state": active_state,
            "main_pid": main_pid,
        })
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise OwnershipPreflightError("birth_ownership_maintenance_invalid", "unit order")
    return _canonical({"schema_version": 1, "source": source, "units": normalized})


def maintenance_evidence_hash(encoded: bytes) -> str:
    """Hash only a canonical, quiescent V1 maintenance proof."""
    if not isinstance(encoded, bytes) or len(encoded) > MAX_PAYLOAD_BYTES:
        raise OwnershipPreflightError("birth_ownership_maintenance_invalid", "size")
    try:
        value = json.loads(encoded.decode("ascii"), object_pairs_hook=_unique_pairs)
    except OwnershipPreflightError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnershipPreflightError("birth_ownership_maintenance_invalid", "json") from exc
    if not isinstance(value, dict) or set(value) != _MAINTENANCE_KEYS:
        raise OwnershipPreflightError("birth_ownership_maintenance_invalid", "schema")
    canonical = canonical_maintenance_proof(
        source=value.get("source"), units=value.get("units", ()),
    )
    if canonical != encoded:
        raise OwnershipPreflightError("birth_ownership_maintenance_invalid", "canonical")
    return "sha256:" + hashlib.sha256(MAINTENANCE_DOMAIN + encoded).hexdigest()


def verify_root_owned_certificate_directory(
    directory: Path = DEFAULT_CERTIFICATE_DIRECTORY,
) -> None:
    """Enforce Linux installer ownership before any certificate parsing."""
    if not sys.platform.startswith("linux"):
        raise OwnershipPreflightError("birth_ownership_platform_unsupported")
    directory = Path(directory)
    if not directory.is_absolute():
        raise OwnershipPreflightError("birth_ownership_path_invalid")
    chain = tuple(reversed((directory, *directory.parents)))
    for component in chain:
        try:
            info = component.lstat()
        except OSError as exc:
            raise OwnershipPreflightError("birth_ownership_proof_missing") from exc
        reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
        junction = bool(
            hasattr(component, "is_junction") and component.is_junction()
        )
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or reparse or junction
            or info.st_uid != 0 or info.st_gid != 0
            or info.st_mode & 0o022
            or (component == directory and stat.S_IMODE(info.st_mode) != 0o755)
        ):
            raise OwnershipPreflightError(
                "birth_ownership_path_unsafe", str(component),
            )
    for basename in (PAYLOAD_BASENAME, SIGNATURE_BASENAME):
        path = directory / basename
        try:
            info = path.lstat()
        except OSError as exc:
            raise OwnershipPreflightError("birth_ownership_proof_missing", basename) from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0) & 0x400)
            or info.st_uid != 0
            or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o644
            or info.st_nlink != 1
        ):
            raise OwnershipPreflightError("birth_ownership_path_unsafe", basename)


def preflight_closed_build(
    directory: Path, *, registry: OwnershipCutoverRegistry,
    authenticated_build: ClosedBuildIdentity | None,
    expected_current: CurrentReceiptProof,
) -> OwnershipStartupAttestation:
    """Authorize readiness only after every current binding matches exactly."""
    if (
        authenticated_build is None
        or not isinstance(authenticated_build, ClosedBuildIdentity)
        or authenticated_build._seal is not _BUILD_AUTHORITY_SEAL
    ):
        raise OwnershipPreflightError("birth_ownership_build_authority_missing")
    # Until a signed installation manifest is normatively defined, accepting a
    # caller-selected alternative path would manufacture deployment authority.
    if Path(directory) != DEFAULT_CERTIFICATE_DIRECTORY:
        raise OwnershipPreflightError("birth_ownership_path_invalid")
    verify_root_owned_certificate_directory(directory)
    try:
        certificate = read_ownership_cutover_certificate(
            directory, registry=registry, expected_proof=expected_current,
        )
    except OwnershipCutoverError as exc:
        raise OwnershipPreflightError(exc.code, exc.detail) from exc
    _verify_build_bindings(certificate, authenticated_build)
    return OwnershipStartupAttestation(
        certificate.cutover_id,
        certificate.closed_build_id,
        certificate.catalog_id,
        certificate.current_count,
    )


def verify_cutover_maintenance_evidence(
    certificate: OwnershipCutoverCertificate, encoded: bytes,
) -> None:
    """Bind the stopped-stack proof during cutover, never during ExecStartPre.

    The service may already be ``activating`` while its pre-start command runs;
    treating that state as the historical quiescence observation would either
    deadlock startup or weaken the stopped-stack rule.  The root coordinator
    calls this function before publishing the payload (the point of no return).
    """
    if not isinstance(certificate, OwnershipCutoverCertificate):
        raise OwnershipPreflightError("birth_ownership_proof_invalid")
    if certificate.maintenance_evidence_hash != maintenance_evidence_hash(encoded):
        raise OwnershipPreflightError("birth_ownership_maintenance_changed")


def _verify_build_bindings(
    certificate: OwnershipCutoverCertificate, build: ClosedBuildIdentity,
) -> None:
    if certificate.closed_build_id != build.closed_build_id:
        raise OwnershipPreflightError("birth_ownership_build_mismatch")
    if certificate.boundary_inventory_hash != build.boundary_inventory_hash:
        raise OwnershipPreflightError("birth_ownership_inventory_mismatch")
    if certificate.boundary_guard_version != build.boundary_guard_version:
        raise OwnershipPreflightError("birth_ownership_guard_mismatch")


__all__ = [
    "ClosedBuildIdentity", "DEFAULT_CERTIFICATE_DIRECTORY", "MAINTENANCE_SOURCES",
    "OwnershipPreflightError", "OwnershipStartupAttestation",
    "canonical_maintenance_proof", "maintenance_evidence_hash",
    "preflight_closed_build", "verify_cutover_maintenance_evidence",
    "verify_root_owned_certificate_directory",
]
