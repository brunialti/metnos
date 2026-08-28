"""Root-owned, restartable RM-0008 ownership-cutover coordinator.

Group 5 may productively advance only through ``RECEIPTS_COMPLETE``.  The
certificate boundary exists here so its durable protocol can be certified in
isolation, but crossing it requires a sealed startup prerequisite that the
productive Group-5 entry cannot create.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterator, Mapping

from executor_birth_cutover import CurrentReceiptProof
from executor_birth_distribution_manifest import (
    VerifiedDistribution, is_verified_distribution, verify_distribution_manifest,
)
from executor_birth_ownership_authorities import (
    DEFAULT_OWNERSHIP_ROOT_V1, RootOwnershipAuthoritiesV1,
    load_root_ownership_authorities_v1,
)
from executor_birth_ownership_cutover import (
    MAX_PAYLOAD_BYTES, PAYLOAD_BASENAME, SIGNATURE_BASENAME,
    OwnershipCutoverRegistry, install_ownership_cutover_certificate,
    issue_ownership_cutover_certificate, read_ownership_cutover_certificate,
    verify_ownership_cutover_certificate, _publish_no_replace, _safe_read,
    _sync_directory, _write_temporary,
)


COORDINATOR_DIRECTORY_BASENAME_V1 = "coordinator-v1"
DEFAULT_COORDINATOR_DIRECTORY_V1 = (
    DEFAULT_OWNERSHIP_ROOT_V1 / COORDINATOR_DIRECTORY_BASENAME_V1
)
DEPLOYMENT_LOCK_BASENAME_V1 = "ownership-deployment-v1.lock"
MAX_RECORD_BYTES_V1 = 8 * 1024 * 1024
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TEMPORARY_RECORD_RE = re.compile(
    r"\.record-([0-9]{3})-v1\.json\.([0-9a-f]{64})\.tmp\Z"
)
_RECORD_DOMAIN = b"metnos.executor-birth.ownership-coordinator-record/v1\0"
_REQUEST_DOMAIN = b"metnos.executor-birth.ownership-coordinator-request/v1\0"
_RECORD_KEYS = frozenset({
    "schema_version", "sequence", "state", "previous_record_sha256",
    "request_id", "previous_closed_build_id", "previous_cutover_id",
    "closed_build_id", "distribution_payload_hash",
    "distribution_signature_hash", "boundary_inventory_hash",
    "boundary_guard_version", "current_receipts",
    "maintenance_before_hash", "maintenance_after_hash",
    "maintenance_proof_b64", "startup_prerequisite_id",
    "startup_prerequisite_digest", "cutover_id", "catalog_id",
    "certificate_payload_hash", "certificate_signature_hash",
})
_RECEIPT_KEYS = frozenset({"contract_id", "generation_id", "receipt_hash"})


class OwnershipCoordinatorError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class OwnershipCoordinatorStateV1(str, Enum):
    PREPARED = "PREPARED"
    RECEIPTS_COMPLETE = "RECEIPTS_COMPLETE"
    CERTIFICATE_READY = "CERTIFICATE_READY"
    CERTIFICATE_PUBLISHED = "CERTIFICATE_PUBLISHED"
    BUILD_VERIFIED = "BUILD_VERIFIED"
    HEAD_REQUIRED = "HEAD_REQUIRED"
    PREFLIGHT_VERIFIED = "PREFLIGHT_VERIFIED"


_STATES = tuple(OwnershipCoordinatorStateV1)


def _digest(encoded: bytes) -> str:
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _require_digest(value: object, field: str, *, nullable: bool = False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise OwnershipCoordinatorError("birth_ownership_journal_invalid", field)
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "json",
        ) from exc


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "duplicate key",
            )
        result[key] = value
    return result


def _proof_values(proof: CurrentReceiptProof | None) -> list[dict[str, str]]:
    if proof is None:
        return []
    rebound = CurrentReceiptProof(proof.identities, proof.receipt_hashes)
    return [{
        "contract_id": contract_id,
        "generation_id": generation_id,
        "receipt_hash": rebound.receipt_hashes[(contract_id, generation_id)],
    } for contract_id, generation_id in rebound.identities]


def _proof_from_values(values: object) -> CurrentReceiptProof | None:
    if not isinstance(values, list):
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "current_receipts",
        )
    identities: list[tuple[str, str]] = []
    hashes: dict[tuple[str, str], str] = {}
    for value in values:
        if not isinstance(value, dict) or set(value) != _RECEIPT_KEYS:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "current receipt schema",
            )
        contract_id = value.get("contract_id")
        if not isinstance(contract_id, str) or not contract_id or "\0" in contract_id:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "contract_id",
            )
        generation_id = _require_digest(value.get("generation_id"), "generation_id")
        receipt_hash = _require_digest(value.get("receipt_hash"), "receipt_hash")
        identity = (contract_id, generation_id)
        identities.append(identity)
        hashes[identity] = receipt_hash
    try:
        return CurrentReceiptProof(tuple(identities), MappingProxyType(hashes))
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "current receipts",
        ) from exc


@dataclass(frozen=True, slots=True)
class OwnershipCoordinatorRecordV1:
    sequence: int
    state: OwnershipCoordinatorStateV1
    previous_record_sha256: str | None
    request_id: str
    previous_closed_build_id: str | None
    previous_cutover_id: str | None
    closed_build_id: str
    distribution_payload_hash: str
    distribution_signature_hash: str
    boundary_inventory_hash: str
    boundary_guard_version: str
    current_proof: CurrentReceiptProof | None = None
    maintenance_before_hash: str | None = None
    maintenance_after_hash: str | None = None
    maintenance_proof: bytes | None = None
    startup_prerequisite_id: str | None = None
    startup_prerequisite_digest: str | None = None
    cutover_id: str | None = None
    catalog_id: str | None = None
    certificate_payload_hash: str | None = None
    certificate_signature_hash: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or self.sequence < 0 or self.sequence >= len(_STATES)
            or self.state is not _STATES[self.sequence]
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "state sequence",
            )
        for field in (
            "request_id", "closed_build_id", "distribution_payload_hash",
            "distribution_signature_hash", "boundary_inventory_hash",
        ):
            _require_digest(getattr(self, field), field)
        for field in (
            "previous_record_sha256", "previous_closed_build_id",
            "previous_cutover_id", "maintenance_before_hash",
            "maintenance_after_hash", "startup_prerequisite_id",
            "startup_prerequisite_digest", "cutover_id", "catalog_id",
            "certificate_payload_hash", "certificate_signature_hash",
        ):
            _require_digest(getattr(self, field), field, nullable=True)
        if (
            not isinstance(self.boundary_guard_version, str)
            or not self.boundary_guard_version
            or "\0" in self.boundary_guard_version
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "boundary_guard_version",
            )
        if self.sequence == 0:
            if any(value is not None for value in (
                self.current_proof, self.maintenance_before_hash,
                self.maintenance_after_hash, self.maintenance_proof,
                self.startup_prerequisite_id, self.startup_prerequisite_digest,
                self.cutover_id, self.catalog_id, self.certificate_payload_hash,
                self.certificate_signature_hash,
            )):
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_invalid", "prepared fields",
                )
        if self.sequence >= 1:
            if (
                not isinstance(self.current_proof, CurrentReceiptProof)
                or self.maintenance_before_hash is None
                or self.maintenance_after_hash is None
                or not isinstance(self.maintenance_proof, bytes)
                or not self.maintenance_proof
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_invalid", "receipt proof fields",
                )
            from executor_birth_ownership_preflight import maintenance_evidence_hash

            observed_hash = maintenance_evidence_hash(self.maintenance_proof)
            if (
                self.maintenance_before_hash != observed_hash
                or self.maintenance_after_hash != observed_hash
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_invalid", "maintenance binding",
                )
        if self.sequence >= 2 and any(value is None for value in (
            self.startup_prerequisite_id, self.startup_prerequisite_digest,
            self.cutover_id, self.catalog_id, self.certificate_payload_hash,
            self.certificate_signature_hash,
        )):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "certificate ready fields",
            )
        if self.sequence < 2 and any(value is not None for value in (
            self.startup_prerequisite_id, self.startup_prerequisite_digest,
            self.cutover_id, self.catalog_id, self.certificate_payload_hash,
            self.certificate_signature_hash,
        )):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "premature certificate fields",
            )

    def as_value(self) -> dict[str, object]:
        return {
            "schema_version": 1, "sequence": self.sequence,
            "state": self.state.value,
            "previous_record_sha256": self.previous_record_sha256,
            "request_id": self.request_id,
            "previous_closed_build_id": self.previous_closed_build_id,
            "previous_cutover_id": self.previous_cutover_id,
            "closed_build_id": self.closed_build_id,
            "distribution_payload_hash": self.distribution_payload_hash,
            "distribution_signature_hash": self.distribution_signature_hash,
            "boundary_inventory_hash": self.boundary_inventory_hash,
            "boundary_guard_version": self.boundary_guard_version,
            "current_receipts": _proof_values(self.current_proof),
            "maintenance_before_hash": self.maintenance_before_hash,
            "maintenance_after_hash": self.maintenance_after_hash,
            "maintenance_proof_b64": (
                base64.b64encode(self.maintenance_proof).decode("ascii")
                if self.maintenance_proof is not None else None
            ),
            "startup_prerequisite_id": self.startup_prerequisite_id,
            "startup_prerequisite_digest": self.startup_prerequisite_digest,
            "cutover_id": self.cutover_id, "catalog_id": self.catalog_id,
            "certificate_payload_hash": self.certificate_payload_hash,
            "certificate_signature_hash": self.certificate_signature_hash,
        }

    def encode(self) -> bytes:
        return _canonical(self.as_value())


def _decode_record(encoded: bytes) -> OwnershipCoordinatorRecordV1:
    if not isinstance(encoded, bytes) or len(encoded) > MAX_RECORD_BYTES_V1:
        raise OwnershipCoordinatorError("birth_ownership_journal_invalid", "size")
    try:
        value = json.loads(encoded.decode("ascii"), object_pairs_hook=_pairs)
    except OwnershipCoordinatorError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "json",
        ) from exc
    if (
        not isinstance(value, dict) or set(value) != _RECORD_KEYS
        or value.get("schema_version") != 1 or _canonical(value) != encoded
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "schema",
        )
    raw_proof = value.get("maintenance_proof_b64")
    try:
        maintenance = (
            None if raw_proof is None else base64.b64decode(raw_proof, validate=True)
        )
        if maintenance is not None and base64.b64encode(maintenance).decode("ascii") != raw_proof:
            raise ValueError("noncanonical base64")
        state = OwnershipCoordinatorStateV1(value.get("state"))
    except (TypeError, ValueError) as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "encoded field",
        ) from exc
    return OwnershipCoordinatorRecordV1(
        sequence=value.get("sequence"), state=state,
        previous_record_sha256=value.get("previous_record_sha256"),
        request_id=value.get("request_id"),
        previous_closed_build_id=value.get("previous_closed_build_id"),
        previous_cutover_id=value.get("previous_cutover_id"),
        closed_build_id=value.get("closed_build_id"),
        distribution_payload_hash=value.get("distribution_payload_hash"),
        distribution_signature_hash=value.get("distribution_signature_hash"),
        boundary_inventory_hash=value.get("boundary_inventory_hash"),
        boundary_guard_version=value.get("boundary_guard_version"),
        current_proof=(
            None if state is OwnershipCoordinatorStateV1.PREPARED
            else _proof_from_values(value.get("current_receipts"))
        ),
        maintenance_before_hash=value.get("maintenance_before_hash"),
        maintenance_after_hash=value.get("maintenance_after_hash"),
        maintenance_proof=maintenance,
        startup_prerequisite_id=value.get("startup_prerequisite_id"),
        startup_prerequisite_digest=value.get("startup_prerequisite_digest"),
        cutover_id=value.get("cutover_id"), catalog_id=value.get("catalog_id"),
        certificate_payload_hash=value.get("certificate_payload_hash"),
        certificate_signature_hash=value.get("certificate_signature_hash"),
    )


def _record_hash(encoded: bytes) -> str:
    return _digest(_RECORD_DOMAIN + encoded)


def _record_basename(sequence: int) -> str:
    return f"record-{sequence:03d}-v1.json"


class OwnershipCoordinatorJournalV1:
    """Immutable-record journal with a closed, monotonic state grammar."""

    __slots__ = ("directory", "_root_owned")

    def __init__(self, directory: Path, *, root_owned: bool) -> None:
        self.directory = Path(directory)
        self._root_owned = root_owned
        _ensure_directory(self.directory, root_owned=root_owned)

    def load(self) -> tuple[OwnershipCoordinatorRecordV1, ...]:
        try:
            entries = tuple(sorted(self.directory.iterdir(), key=lambda item: item.name))
        except OSError as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "inventory",
            ) from exc
        committed = []
        temporary = []
        for path in entries:
            if re.fullmatch(r"record-[0-9]{3}-v1\.json", path.name):
                committed.append(path)
            elif _TEMPORARY_RECORD_RE.fullmatch(path.name):
                temporary.append(path)
            else:
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_invalid", "gap or extra file",
                )
        records = []
        previous_hash = None
        for sequence, path in enumerate(committed):
            expected = _record_basename(sequence)
            if path.name != expected:
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_invalid", "gap or extra file",
                )
            encoded = _safe_read(path, MAX_RECORD_BYTES_V1)
            record = _decode_record(encoded)
            if (
                record.sequence != sequence
                or record.previous_record_sha256 != previous_hash
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_invalid", "chain",
                )
            records.append(record)
            previous_hash = _record_hash(encoded)
        if temporary:
            if len(temporary) != 1:
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_invalid", "temporary inventory",
                )
            path = temporary[0]
            match = _TEMPORARY_RECORD_RE.fullmatch(path.name)
            assert match is not None
            sequence = int(match.group(1))
            try:
                info = path.lstat()
            except OSError as exc:
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_invalid", "temporary metadata",
                ) from exc
            if (
                not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_nlink != 1
                or (self._root_owned and (info.st_uid != 0 or info.st_gid != 0))
                or (os.name != "nt" and stat.S_IMODE(info.st_mode) not in {0o600, 0o644})
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_invalid", "temporary metadata",
                )
            destination = self.directory / _record_basename(sequence)
            partial = os.name != "nt" and stat.S_IMODE(info.st_mode) == 0o600
            if sequence < len(records):
                final = records[sequence].encode()
                if not partial and _safe_read(path, len(final)) != final:
                    raise OwnershipCoordinatorError(
                        "birth_ownership_journal_conflict", path.name,
                    )
                path.unlink()
                _sync_directory(self.directory)
                return self.load()
            if sequence != len(records):
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_invalid", "temporary gap",
                )
            if partial:
                path.unlink()
                _sync_directory(self.directory)
                return tuple(records)
            encoded = _safe_read(path, MAX_RECORD_BYTES_V1)
            record = _decode_record(encoded)
            if (
                record.sequence != sequence
                or record.previous_record_sha256 != previous_hash
                or match.group(2) != record.request_id[7:]
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_invalid", "temporary chain",
                )
            _publish_no_replace(path, destination, encoded)
            return self.load()
        return tuple(records)

    def append(self, record: OwnershipCoordinatorRecordV1) -> OwnershipCoordinatorRecordV1:
        records = self.load()
        sequence = len(records)
        if record.sequence != sequence or record.state is not _STATES[sequence]:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "nonmonotonic append",
            )
        previous_hash = records[-1] and _record_hash(records[-1].encode()) if records else None
        if record.previous_record_sha256 != previous_hash:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "previous record",
            )
        encoded = record.encode()
        destination = self.directory / _record_basename(sequence)
        temporary = self.directory / f".{destination.name}.{record.request_id[7:]}.tmp"
        try:
            if not temporary.exists():
                _write_temporary(temporary, encoded)
            elif _safe_read(temporary, len(encoded)) != encoded:
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_conflict", temporary.name,
                )
            _publish_no_replace(temporary, destination, encoded)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        loaded = self.load()
        if len(loaded) != sequence + 1 or loaded[-1] != record:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "reread",
            )
        return loaded[-1]


def _append_coordinator_record_v1(
    journal: OwnershipCoordinatorJournalV1,
    record: OwnershipCoordinatorRecordV1,
) -> OwnershipCoordinatorRecordV1:
    """Unique static-boundary name for one immutable journal append."""
    return journal.append(record)


def _ensure_directory(directory: Path, *, root_owned: bool) -> None:
    try:
        directory.mkdir(mode=0o755, parents=True, exist_ok=True)
        info = directory.lstat()
    except OSError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", str(directory),
        ) from exc
    if (
        not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_mode & 0o022
        or (root_owned and (info.st_uid != 0 or info.st_gid != 0))
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "directory metadata",
        )


@contextmanager
def _deployment_lock_v1(
    root: Path = DEFAULT_OWNERSHIP_ROOT_V1, *, root_owned: bool = True,
) -> Iterator[None]:
    """Outermost process lock; catalog and lifecycle locks are always nested."""
    if not sys.platform.startswith("linux"):
        raise OwnershipCoordinatorError("birth_ownership_platform_unsupported")
    root = Path(root)
    _ensure_directory(root, root_owned=root_owned)
    path = root / DEPLOYMENT_LOCK_BASENAME_V1
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or (root_owned and (info.st_uid != 0 or info.st_gid != 0))
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
        if info.st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
        elif info.st_size != 1:
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    except OwnershipCoordinatorError:
        raise
    except OSError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_deployment_lock_invalid",
        ) from exc
    finally:
        if "fd" in locals():
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)


def _request_id(
    distribution: VerifiedDistribution, previous_cutover_id: str | None,
) -> str:
    fields = (
        distribution.identity.closed_build_id,
        distribution.previous_closed_build_id or "none",
        previous_cutover_id or "none",
    )
    framed = bytearray(_REQUEST_DOMAIN)
    for field in fields:
        encoded = field.encode("ascii")
        framed.extend(len(encoded).to_bytes(8, "big"))
        framed.extend(encoded)
    return _digest(bytes(framed))


def _prepared_record(
    distribution: VerifiedDistribution, *, previous_cutover_id: str | None,
) -> OwnershipCoordinatorRecordV1:
    return OwnershipCoordinatorRecordV1(
        0, OwnershipCoordinatorStateV1.PREPARED, None,
        _request_id(distribution, previous_cutover_id),
        distribution.previous_closed_build_id, previous_cutover_id,
        distribution.identity.closed_build_id, _digest(distribution.encoded),
        _digest(distribution.signature),
        distribution.identity.boundary_inventory_hash,
        distribution.identity.boundary_guard_version,
    )


def _same_distribution(
    record: OwnershipCoordinatorRecordV1, distribution: VerifiedDistribution,
) -> bool:
    return (
        record.closed_build_id == distribution.identity.closed_build_id
        and record.previous_closed_build_id == distribution.previous_closed_build_id
        and record.distribution_payload_hash == _digest(distribution.encoded)
        and record.distribution_signature_hash == _digest(distribution.signature)
        and record.boundary_inventory_hash
        == distribution.identity.boundary_inventory_hash
        and record.boundary_guard_version
        == distribution.identity.boundary_guard_version
    )


def _append_receipts_complete(
    journal: OwnershipCoordinatorJournalV1,
    prepared: OwnershipCoordinatorRecordV1, *, proof: CurrentReceiptProof,
    maintenance_before: bytes, maintenance_after: bytes,
) -> OwnershipCoordinatorRecordV1:
    from executor_birth_ownership_preflight import maintenance_evidence_hash

    before_hash = maintenance_evidence_hash(maintenance_before)
    after_hash = maintenance_evidence_hash(maintenance_after)
    if maintenance_before != maintenance_after or before_hash != after_hash:
        raise OwnershipCoordinatorError("birth_ownership_maintenance_changed")
    return _append_coordinator_record_v1(journal, OwnershipCoordinatorRecordV1(
        1, OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE,
        _record_hash(prepared.encode()), prepared.request_id,
        prepared.previous_closed_build_id, prepared.previous_cutover_id,
        prepared.closed_build_id, prepared.distribution_payload_hash,
        prepared.distribution_signature_hash, prepared.boundary_inventory_hash,
        prepared.boundary_guard_version, proof, before_hash, after_hash,
        maintenance_after,
    ))


@dataclass(frozen=True, slots=True)
class OwnershipCoordinatorResultV1:
    state: OwnershipCoordinatorStateV1
    request_id: str
    current_count: int
    cutover_id: str | None


def _result(record: OwnershipCoordinatorRecordV1) -> OwnershipCoordinatorResultV1:
    return OwnershipCoordinatorResultV1(
        record.state, record.request_id,
        len(record.current_proof.identities) if record.current_proof else 0,
        record.cutover_id,
    )


def _prepare_under_maintenance_v1(
    distribution: VerifiedDistribution, *,
    journal: OwnershipCoordinatorJournalV1,
    initial_maintenance: bytes,
    observe_maintenance: Callable[[], bytes],
    prepare_receipts: Callable[[], CurrentReceiptProof],
) -> OwnershipCoordinatorResultV1:
    records = journal.load()
    if records:
        first = records[0]
        if not _same_distribution(first, distribution):
            raise OwnershipCoordinatorError("birth_ownership_request_conflict")
        if first.request_id != _request_id(distribution, first.previous_cutover_id):
            raise OwnershipCoordinatorError("birth_ownership_journal_invalid", "request")
        if records[-1].state is OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE:
            latest = records[-1]
            proof = prepare_receipts()
            final_maintenance = observe_maintenance()
            if (
                proof != latest.current_proof
                or initial_maintenance != latest.maintenance_proof
                or final_maintenance != latest.maintenance_proof
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required", "receipt or maintenance drift",
                )
            return _result(latest)
        if records[-1].sequence > 1:
            return _result(records[-1])
        prepared = first
    else:
        prepared = _append_coordinator_record_v1(
            journal,
            _prepared_record(distribution, previous_cutover_id=None),
        )
    proof = prepare_receipts()
    if not isinstance(proof, CurrentReceiptProof):
        raise OwnershipCoordinatorError("birth_ownership_receipt_proof_invalid")
    final_maintenance = observe_maintenance()
    complete = _append_receipts_complete(
        journal, prepared, proof=proof,
        maintenance_before=initial_maintenance,
        maintenance_after=final_maintenance,
    )
    return _result(complete)


def prepare_ownership_cutover_v1(
    distribution: VerifiedDistribution,
) -> OwnershipCoordinatorResultV1:
    """Productive Group-5 entry; it can stop only at receipt completeness."""
    if not is_verified_distribution(distribution):
        raise OwnershipCoordinatorError("birth_ownership_distribution_untrusted")
    with _deployment_lock_v1():
        authorities = load_root_ownership_authorities_v1()
        verified = verify_distribution_manifest(
            distribution.encoded, distribution.signature,
            registry=authorities.public.distribution,
        )
        if not _same_distribution(_prepared_record(
            distribution, previous_cutover_id=None,
        ), verified):
            raise OwnershipCoordinatorError("birth_ownership_distribution_changed")
        from contract_cutover_guard import (
            _verify_store_only_catalog_locked, contract_cutover_guard,
        )
        from executor_birth_cutover import prepare_current_receipt_proof
        from executor_birth_ownership_preflight import canonical_maintenance_proof
        from executor_birth_bootstrap import bootstrap_birth_runtime
        from executor_birth_operational import _runtime_bundle_snapshot
        from executor_birth_reattestation import reattest_current_generation
        from executor_birth_commit_publisher import _is_birth_reattestation_port

        bundle = _runtime_bundle_snapshot()
        if bundle is None:
            try:
                bundle = bootstrap_birth_runtime()
            except Exception as exc:
                raise OwnershipCoordinatorError(
                    "birth_ownership_birth_runtime_unavailable",
                ) from exc
        port_factory = getattr(
            getattr(bundle, "core", None), "commit_publisher", None,
        )
        port_factory = getattr(port_factory, "reattestation_port", None)
        port = port_factory() if callable(port_factory) else None
        if not _is_birth_reattestation_port(port):
            raise OwnershipCoordinatorError("birth_ownership_birth_runtime_unavailable")
        journal = OwnershipCoordinatorJournalV1(
            DEFAULT_COORDINATOR_DIRECTORY_V1, root_owned=True,
        )
        with contract_cutover_guard() as (maintenance, evidence):
            initial = canonical_maintenance_proof(
                source=evidence["source"], units=evidence["units"],
            )

            def prepare_receipts() -> CurrentReceiptProof:
                report = prepare_current_receipt_proof(
                    prove_quiescent=maintenance,
                    enumerate_current=port.enumerate_current,
                    read_receipt=port.read,
                    reattest_via_birth=lambda current: (
                        reattest_current_generation(current).receipt
                    ),
                    verify_receipt=port.verify_receipt,
                )
                _verify_store_only_catalog_locked()
                return report.proof

            return _prepare_under_maintenance_v1(
                verified, journal=journal, initial_maintenance=initial,
                observe_maintenance=lambda: canonical_maintenance_proof(
                    source=(fresh := maintenance.observe())["source"],
                    units=fresh["units"],
                ),
                prepare_receipts=prepare_receipts,
            )


_PREREQUISITE_SEAL = object()


@dataclass(frozen=True, slots=True)
class _StartupPrerequisiteV1:
    prerequisite_id: str
    evidence_digest: str
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _PREREQUISITE_SEAL:
            raise OwnershipCoordinatorError("birth_ownership_prerequisite_untrusted")
        _require_digest(self.prerequisite_id, "startup_prerequisite_id")
        _require_digest(self.evidence_digest, "startup_prerequisite_digest")


def _startup_prerequisite_for_test(
    prerequisite_id: str, evidence_digest: str,
) -> _StartupPrerequisiteV1:
    return _StartupPrerequisiteV1(
        prerequisite_id, evidence_digest, _PREREQUISITE_SEAL,
    )


def _single_cutover_key(authorities: RootOwnershipAuthoritiesV1) -> str:
    if not isinstance(authorities, RootOwnershipAuthoritiesV1):
        raise OwnershipCoordinatorError("birth_ownership_authority_untrusted")
    keys = tuple(authorities.public.cutover.keys)
    if len(keys) != 1:
        raise OwnershipCoordinatorError("birth_ownership_authority_untrusted")
    return keys[0]


def _copy_with_state(
    previous: OwnershipCoordinatorRecordV1, *,
    state: OwnershipCoordinatorStateV1,
    prerequisite: _StartupPrerequisiteV1,
    cutover_id: str, catalog_id: str, payload_hash: str, signature_hash: str,
) -> OwnershipCoordinatorRecordV1:
    return OwnershipCoordinatorRecordV1(
        previous.sequence + 1, state, _record_hash(previous.encode()),
        previous.request_id, previous.previous_closed_build_id,
        previous.previous_cutover_id, previous.closed_build_id,
        previous.distribution_payload_hash, previous.distribution_signature_hash,
        previous.boundary_inventory_hash, previous.boundary_guard_version,
        previous.current_proof, previous.maintenance_before_hash,
        previous.maintenance_after_hash, previous.maintenance_proof,
        prerequisite.prerequisite_id, prerequisite.evidence_digest,
        cutover_id, catalog_id, payload_hash, signature_hash,
    )


def _publish_certificate_with_prerequisite_v1(
    *, journal: OwnershipCoordinatorJournalV1,
    certificate_directory: Path,
    authorities: RootOwnershipAuthoritiesV1,
    prerequisite: _StartupPrerequisiteV1,
    observe_maintenance: Callable[[], bytes],
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorResultV1:
    """Isolated Group-5 proof of READY/publish/recovery; no productive caller."""
    if (
        not isinstance(prerequisite, _StartupPrerequisiteV1)
        or prerequisite._seal is not _PREREQUISITE_SEAL
        or not callable(observe_maintenance)
    ):
        raise OwnershipCoordinatorError("birth_ownership_prerequisite_untrusted")
    records = journal.load()
    if not records or records[-1].sequence < 1:
        raise OwnershipCoordinatorError("birth_ownership_receipts_incomplete")
    latest = records[-1]
    proof = latest.current_proof
    assert proof is not None and latest.maintenance_after_hash is not None
    if (
        latest.state is OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE
        and observe_maintenance() != latest.maintenance_proof
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "maintenance drift",
        )
    key_id = _single_cutover_key(authorities)
    payload, signature = issue_ownership_cutover_certificate(
        proof=proof, previous_cutover_id=latest.previous_cutover_id,
        request_id=latest.request_id, signing_key_id=key_id,
        maintenance_evidence_hash=latest.maintenance_after_hash,
        boundary_inventory_hash=latest.boundary_inventory_hash,
        boundary_guard_version=latest.boundary_guard_version,
        closed_build_id=latest.closed_build_id,
        private_key=authorities.cutover_private,
    )
    certificate = verify_ownership_cutover_certificate(
        payload, signature, registry=authorities.public.cutover,
        expected_proof=proof,
    )
    payload_hash = _digest(payload)
    signature_hash = _digest(signature)
    certificate_directory = Path(certificate_directory)
    payload_path = certificate_directory / PAYLOAD_BASENAME
    signature_path = certificate_directory / SIGNATURE_BASENAME
    if latest.state is OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE:
        if payload_path.exists() or signature_path.exists():
            raise OwnershipCoordinatorError("birth_ownership_recovery_required")
        latest = _append_coordinator_record_v1(journal, _copy_with_state(
            latest, state=OwnershipCoordinatorStateV1.CERTIFICATE_READY,
            prerequisite=prerequisite, cutover_id=certificate.cutover_id,
            catalog_id=certificate.catalog_id, payload_hash=payload_hash,
            signature_hash=signature_hash,
        ))
        if _crash_seam:
            _crash_seam("certificate_ready")
    if latest.sequence >= 2:
        expected = (
            prerequisite.prerequisite_id, prerequisite.evidence_digest,
            certificate.cutover_id, certificate.catalog_id,
            payload_hash, signature_hash,
        )
        actual = (
            latest.startup_prerequisite_id, latest.startup_prerequisite_digest,
            latest.cutover_id, latest.catalog_id,
            latest.certificate_payload_hash, latest.certificate_signature_hash,
        )
        if actual != expected:
            raise OwnershipCoordinatorError("birth_ownership_recovery_required")
    if latest.state is OwnershipCoordinatorStateV1.CERTIFICATE_READY:
        installed = install_ownership_cutover_certificate(
            certificate_directory, payload, signature,
            registry=authorities.public.cutover, expected_proof=proof,
            _crash_seam=_crash_seam,
        )
        if installed.cutover_id != certificate.cutover_id:
            raise OwnershipCoordinatorError("birth_ownership_recovery_required")
        if _crash_seam:
            _crash_seam("certificate_verified")
        latest = _append_coordinator_record_v1(journal, _copy_with_state(
            latest, state=OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED,
            prerequisite=prerequisite, cutover_id=certificate.cutover_id,
            catalog_id=certificate.catalog_id, payload_hash=payload_hash,
            signature_hash=signature_hash,
        ))
    if latest.sequence >= 3:
        reread = read_ownership_cutover_certificate(
            certificate_directory, registry=authorities.public.cutover,
            expected_proof=proof,
        )
        if reread.cutover_id != latest.cutover_id:
            raise OwnershipCoordinatorError("birth_ownership_recovery_required")
    return _result(latest)


__all__ = [
    "OwnershipCoordinatorError", "OwnershipCoordinatorResultV1",
    "OwnershipCoordinatorStateV1", "prepare_ownership_cutover_v1",
]
