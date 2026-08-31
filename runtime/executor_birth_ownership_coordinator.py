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
import threading
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterator, Mapping

from executor_birth_cutover import CurrentReceiptProof
from executor_birth_distribution_manifest import (
    VerifiedDistribution, is_verified_distribution,
    verify_current_installation_distribution_v1,
)
from executor_birth_ownership_authorities import (
    DEFAULT_OWNERSHIP_ROOT_V1, RootOwnershipAuthoritiesV1,
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
SUCCESSOR_CLAIMS_DIRECTORY_BASENAME_V1 = "successor-claims-v1"
TRANSACTIONS_DIRECTORY_BASENAME_V2 = "transactions-v2"
LEGACY_DISPOSITION_BASENAME_V2 = "legacy-disposition-v2.json"
DEPLOYMENT_LOCK_BASENAME_V1 = "ownership-deployment-v1.lock"
MAX_RECORD_BYTES_V1 = 8 * 1024 * 1024
MAX_COORDINATOR_CONTROL_BYTES_V2 = 16 * 1024
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TEMPORARY_RECORD_RE = re.compile(
    r"\.record-([0-9]{3})-v1\.json\.([0-9a-f]{64})\.tmp\Z"
)
_LEGACY_RECORD_RE_V1 = re.compile(r"record-([0-9]{3})-v1\.json\Z")
_TRANSACTION_RECORD_RE_V2 = re.compile(r"record-([0-9]{3})-v2\.json\Z")
_SUCCESSOR_CLAIM_BASENAME_RE_V1 = re.compile(
    r"(?:initial|[0-9a-f]{64})\.json\Z"
)
_RECORD_DOMAIN = b"metnos.executor-birth.ownership-coordinator-record/v1\0"
_RECORD_DOMAIN_V2 = b"metnos.executor-birth.ownership-coordinator-record/v2\0"
_REQUEST_DOMAIN = b"metnos.executor-birth.ownership-coordinator-request/v1\0"
_SUCCESSOR_CLAIM_DOMAIN_V1 = b"metnos.executor-birth.successor-claim/v1\0"
_LEGACY_JOURNAL_DOMAIN_V2 = b"metnos.executor-birth.legacy-journal/v2\0"
_LEGACY_DISPOSITION_DOMAIN_V2 = (
    b"metnos.executor-birth.legacy-disposition/v2\0"
)
_INSTALL_TRANSACTION_DOMAIN_V1 = (
    b"metnos.executor-birth.install-transaction/v1\0"
)
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
_SUCCESSOR_CLAIM_KEYS_V1 = frozenset({
    "schema_version", "claim_id", "previous_head_id", "release_sequence",
    "request_id", "source_id", "closed_build_id",
})
_LEGACY_DISPOSITION_KEYS_V2 = frozenset({
    "schema_version", "disposition_id", "legacy_journal_hash",
    "legacy_request_id", "legacy_state", "successor_request_id", "reason",
})
_INSTALL_TRANSACTION_KEYS_V1 = frozenset({
    "schema_version", "request_id", "source_id", "closed_build_id",
    "release_sequence", "previous_head_id", "successor_claim_id",
    "deployment_descriptor_id", "service_coverage_hash",
    "administrative_bundle_hash",
})
_RECORD_KEYS_V2 = _RECORD_KEYS | frozenset({
    "source_id", "successor_claim_id", "deployment_descriptor_id",
    "install_transaction_id", "installed_tree_hash", "release_sequence",
    "previous_head_id", "head_id", "head_payload_hash",
    "head_signature_hash", "required_head_frame_hash",
    "verified_chain_head_id", "preflight_attestation_hash",
    "service_coverage_hash", "administrative_bundle_hash",
})
_LEGACY_DISPOSITION_REASON_V2 = "superseded_before_certificate"


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
_POST_CERTIFICATE_STATES_V1 = frozenset({
    OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED.value,
    OwnershipCoordinatorStateV1.BUILD_VERIFIED.value,
    OwnershipCoordinatorStateV1.HEAD_REQUIRED.value,
    OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED.value,
})


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


def _decode_control_document_v2(
    encoded: bytes, *, keys: frozenset[str], schema_version: int,
    label: str,
) -> dict[str, object]:
    if (
        not isinstance(encoded, bytes)
        or not encoded
        or len(encoded) > MAX_COORDINATOR_CONTROL_BYTES_V2
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", label + " size",
        )
    try:
        value = json.loads(encoded.decode("ascii"), object_pairs_hook=_pairs)
    except OwnershipCoordinatorError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", label + " json",
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != schema_version
        or _canonical(value) != encoded
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", label + " schema",
        )
    return value


def _require_release_predecessor_v1(
    release_sequence: object, previous_head_id: object,
) -> tuple[int, str | None]:
    if type(release_sequence) is not int or release_sequence <= 0:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "release_sequence",
        )
    predecessor = _require_digest(
        previous_head_id, "previous_head_id", nullable=True,
    )
    if (release_sequence == 1) is not (predecessor is None):
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "previous_head_id",
        )
    return release_sequence, predecessor


def _coordinator_request_id_v1(
    closed_build_id: object, previous_closed_build_id: object,
    previous_cutover_id: object,
) -> str:
    closed = _require_digest(closed_build_id, "closed_build_id")
    previous_build = _require_digest(
        previous_closed_build_id, "previous_closed_build_id", nullable=True,
    )
    previous_cutover = _require_digest(
        previous_cutover_id, "previous_cutover_id", nullable=True,
    )
    framed = bytearray(_REQUEST_DOMAIN)
    for field in (closed, previous_build or "none", previous_cutover or "none"):
        encoded = field.encode("ascii")
        framed.extend(len(encoded).to_bytes(8, "big"))
        framed.extend(encoded)
    return _digest(bytes(framed))


@dataclass(frozen=True, slots=True)
class SuccessorClaimV1:
    claim_id: str
    previous_head_id: str | None
    release_sequence: int
    request_id: str
    source_id: str
    closed_build_id: str

    def __post_init__(self) -> None:
        _require_release_predecessor_v1(
            self.release_sequence, self.previous_head_id,
        )
        for field in ("request_id", "source_id", "closed_build_id"):
            _require_digest(getattr(self, field), field)
        _require_digest(self.claim_id, "claim_id")
        if self.claim_id != _successor_claim_id_v1(self.as_value(include_id=False)):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "claim_id",
            )

    def as_value(self, *, include_id: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": 1,
            "previous_head_id": self.previous_head_id,
            "release_sequence": self.release_sequence,
            "request_id": self.request_id,
            "source_id": self.source_id,
            "closed_build_id": self.closed_build_id,
        }
        if include_id:
            value["claim_id"] = self.claim_id
        return value

    def encode(self) -> bytes:
        return _canonical(self.as_value())


def _successor_claim_id_v1(value_without_id: dict[str, object]) -> str:
    if set(value_without_id) != _SUCCESSOR_CLAIM_KEYS_V1 - {"claim_id"}:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "successor claim identity",
        )
    return _digest(_SUCCESSOR_CLAIM_DOMAIN_V1 + _canonical(value_without_id))


def _decode_successor_claim_v1(encoded: bytes) -> SuccessorClaimV1:
    value = _decode_control_document_v2(
        encoded, keys=_SUCCESSOR_CLAIM_KEYS_V1, schema_version=1,
        label="successor claim",
    )
    return SuccessorClaimV1(
        claim_id=value.get("claim_id"),
        previous_head_id=value.get("previous_head_id"),
        release_sequence=value.get("release_sequence"),
        request_id=value.get("request_id"),
        source_id=value.get("source_id"),
        closed_build_id=value.get("closed_build_id"),
    )


def _successor_claim_basename_v1(
    release_sequence: int, previous_head_id: str | None,
) -> str:
    _, predecessor = _require_release_predecessor_v1(
        release_sequence, previous_head_id,
    )
    return "initial.json" if predecessor is None else predecessor[7:] + ".json"


def _legacy_journal_hash_v2(record_bytes: tuple[bytes, ...]) -> str:
    if (
        type(record_bytes) is not tuple
        or not 1 <= len(record_bytes) <= 2
        or any(
            type(encoded) is not bytes
            or not encoded
            or len(encoded) > MAX_RECORD_BYTES_V1
            for encoded in record_bytes
        )
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "legacy journal bytes",
        )
    framed = bytearray(_LEGACY_JOURNAL_DOMAIN_V2)
    framed.extend(len(record_bytes).to_bytes(8, "big"))
    for encoded in record_bytes:
        framed.extend(len(encoded).to_bytes(8, "big"))
        framed.extend(encoded)
    return _digest(bytes(framed))


@dataclass(frozen=True, slots=True)
class LegacyDispositionV2:
    disposition_id: str
    legacy_journal_hash: str
    legacy_request_id: str
    legacy_state: OwnershipCoordinatorStateV1
    successor_request_id: str
    reason: str = _LEGACY_DISPOSITION_REASON_V2

    def __post_init__(self) -> None:
        for field in (
            "disposition_id", "legacy_journal_hash", "legacy_request_id",
            "successor_request_id",
        ):
            _require_digest(getattr(self, field), field)
        if (
            type(self.legacy_state) is not OwnershipCoordinatorStateV1
            or self.legacy_state not in {
                OwnershipCoordinatorStateV1.PREPARED,
                OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE,
            }
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "legacy_state",
            )
        if (
            type(self.reason) is not str
            or self.reason != _LEGACY_DISPOSITION_REASON_V2
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "legacy reason",
            )
        expected = _legacy_disposition_id_v2(self.as_value(include_id=False))
        if self.disposition_id != expected:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "disposition_id",
            )

    def as_value(self, *, include_id: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": 2,
            "legacy_journal_hash": self.legacy_journal_hash,
            "legacy_request_id": self.legacy_request_id,
            "legacy_state": self.legacy_state.value,
            "successor_request_id": self.successor_request_id,
            "reason": self.reason,
        }
        if include_id:
            value["disposition_id"] = self.disposition_id
        return value

    def encode(self) -> bytes:
        return _canonical(self.as_value())


def _legacy_disposition_id_v2(value_without_id: dict[str, object]) -> str:
    if set(value_without_id) != _LEGACY_DISPOSITION_KEYS_V2 - {"disposition_id"}:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "legacy disposition identity",
        )
    return _digest(
        _LEGACY_DISPOSITION_DOMAIN_V2 + _canonical(value_without_id),
    )


def _decode_legacy_disposition_v2(encoded: bytes) -> LegacyDispositionV2:
    value = _decode_control_document_v2(
        encoded, keys=_LEGACY_DISPOSITION_KEYS_V2, schema_version=2,
        label="legacy disposition",
    )
    try:
        state = OwnershipCoordinatorStateV1(value.get("legacy_state"))
    except (TypeError, ValueError) as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "legacy_state",
        ) from exc
    return LegacyDispositionV2(
        disposition_id=value.get("disposition_id"),
        legacy_journal_hash=value.get("legacy_journal_hash"),
        legacy_request_id=value.get("legacy_request_id"),
        legacy_state=state,
        successor_request_id=value.get("successor_request_id"),
        reason=value.get("reason"),
    )


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
        or type(value.get("schema_version")) is not int
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


def _install_transaction_id_v1(value: dict[str, object]) -> str:
    if (
        set(value) != _INSTALL_TRANSACTION_KEYS_V1
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "install transaction identity",
        )
    return _digest(_INSTALL_TRANSACTION_DOMAIN_V1 + _canonical(value))


@dataclass(frozen=True, slots=True)
class OwnershipCoordinatorRecordV2:
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
    source_id: str
    successor_claim_id: str
    deployment_descriptor_id: str
    install_transaction_id: str
    release_sequence: int
    previous_head_id: str | None
    service_coverage_hash: str
    administrative_bundle_hash: str
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
    installed_tree_hash: str | None = None
    head_id: str | None = None
    head_payload_hash: str | None = None
    head_signature_hash: str | None = None
    required_head_frame_hash: str | None = None
    verified_chain_head_id: str | None = None
    preflight_attestation_hash: str | None = None

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
            "source_id", "successor_claim_id", "deployment_descriptor_id",
            "install_transaction_id", "service_coverage_hash",
            "administrative_bundle_hash",
        ):
            _require_digest(getattr(self, field), field)
        for field in (
            "previous_record_sha256", "previous_closed_build_id",
            "previous_cutover_id", "maintenance_before_hash",
            "maintenance_after_hash", "startup_prerequisite_id",
            "startup_prerequisite_digest", "cutover_id", "catalog_id",
            "certificate_payload_hash", "certificate_signature_hash",
            "installed_tree_hash", "head_id", "head_payload_hash",
            "head_signature_hash", "required_head_frame_hash",
            "verified_chain_head_id", "preflight_attestation_hash",
        ):
            _require_digest(getattr(self, field), field, nullable=True)
        if (self.sequence == 0) is not (self.previous_record_sha256 is None):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "previous_record_sha256",
            )
        _require_release_predecessor_v1(
            self.release_sequence, self.previous_head_id,
        )
        if (
            not isinstance(self.boundary_guard_version, str)
            or not self.boundary_guard_version
            or "\0" in self.boundary_guard_version
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "boundary_guard_version",
            )
        if self.sequence == 0 and any(value is not None for value in (
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
        certificate_fields = (
            self.startup_prerequisite_id, self.startup_prerequisite_digest,
            self.cutover_id, self.catalog_id, self.certificate_payload_hash,
            self.certificate_signature_hash,
        )
        if (
            self.sequence >= 2 and any(value is None for value in certificate_fields)
        ) or (
            self.sequence < 2 and any(value is not None for value in certificate_fields)
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "certificate threshold",
            )
        if (self.sequence >= 4) is not (self.installed_tree_hash is not None):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "installed tree threshold",
            )
        head_fields = (
            self.head_id, self.head_payload_hash, self.head_signature_hash,
            self.required_head_frame_hash, self.verified_chain_head_id,
        )
        if (
            self.sequence >= 5 and any(value is None for value in head_fields)
        ) or (
            self.sequence < 5 and any(value is not None for value in head_fields)
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "head threshold",
            )
        if self.sequence >= 5 and self.verified_chain_head_id != self.head_id:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "verified chain head",
            )
        if (self.sequence >= 6) is not (
            self.preflight_attestation_hash is not None
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "preflight threshold",
            )
        expected_transaction = _install_transaction_id_v1(
            self.install_transaction_value(),
        )
        if self.install_transaction_id != expected_transaction:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "install_transaction_id",
            )

    def install_transaction_value(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "request_id": self.request_id,
            "source_id": self.source_id,
            "closed_build_id": self.closed_build_id,
            "release_sequence": self.release_sequence,
            "previous_head_id": self.previous_head_id,
            "successor_claim_id": self.successor_claim_id,
            "deployment_descriptor_id": self.deployment_descriptor_id,
            "service_coverage_hash": self.service_coverage_hash,
            "administrative_bundle_hash": self.administrative_bundle_hash,
        }

    def as_value(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "sequence": self.sequence,
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
            "cutover_id": self.cutover_id,
            "catalog_id": self.catalog_id,
            "certificate_payload_hash": self.certificate_payload_hash,
            "certificate_signature_hash": self.certificate_signature_hash,
            "source_id": self.source_id,
            "successor_claim_id": self.successor_claim_id,
            "deployment_descriptor_id": self.deployment_descriptor_id,
            "install_transaction_id": self.install_transaction_id,
            "installed_tree_hash": self.installed_tree_hash,
            "release_sequence": self.release_sequence,
            "previous_head_id": self.previous_head_id,
            "head_id": self.head_id,
            "head_payload_hash": self.head_payload_hash,
            "head_signature_hash": self.head_signature_hash,
            "required_head_frame_hash": self.required_head_frame_hash,
            "verified_chain_head_id": self.verified_chain_head_id,
            "preflight_attestation_hash": self.preflight_attestation_hash,
            "service_coverage_hash": self.service_coverage_hash,
            "administrative_bundle_hash": self.administrative_bundle_hash,
        }

    def encode(self) -> bytes:
        return _canonical(self.as_value())


def _decode_record_v2(encoded: bytes) -> OwnershipCoordinatorRecordV2:
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
        not isinstance(value, dict)
        or set(value) != _RECORD_KEYS_V2
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 2
        or _canonical(value) != encoded
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "schema",
        )
    raw_proof = value.get("maintenance_proof_b64")
    try:
        maintenance = (
            None if raw_proof is None else base64.b64decode(raw_proof, validate=True)
        )
        if (
            maintenance is not None
            and base64.b64encode(maintenance).decode("ascii") != raw_proof
        ):
            raise ValueError("noncanonical base64")
        state = OwnershipCoordinatorStateV1(value.get("state"))
    except (TypeError, ValueError) as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "encoded field",
        ) from exc
    if state is OwnershipCoordinatorStateV1.PREPARED:
        if value.get("current_receipts") != []:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "prepared receipts",
            )
        proof = None
    else:
        proof = _proof_from_values(value.get("current_receipts"))
    return OwnershipCoordinatorRecordV2(
        sequence=value.get("sequence"),
        state=state,
        previous_record_sha256=value.get("previous_record_sha256"),
        request_id=value.get("request_id"),
        previous_closed_build_id=value.get("previous_closed_build_id"),
        previous_cutover_id=value.get("previous_cutover_id"),
        closed_build_id=value.get("closed_build_id"),
        distribution_payload_hash=value.get("distribution_payload_hash"),
        distribution_signature_hash=value.get("distribution_signature_hash"),
        boundary_inventory_hash=value.get("boundary_inventory_hash"),
        boundary_guard_version=value.get("boundary_guard_version"),
        source_id=value.get("source_id"),
        successor_claim_id=value.get("successor_claim_id"),
        deployment_descriptor_id=value.get("deployment_descriptor_id"),
        install_transaction_id=value.get("install_transaction_id"),
        release_sequence=value.get("release_sequence"),
        previous_head_id=value.get("previous_head_id"),
        service_coverage_hash=value.get("service_coverage_hash"),
        administrative_bundle_hash=value.get("administrative_bundle_hash"),
        current_proof=proof,
        maintenance_before_hash=value.get("maintenance_before_hash"),
        maintenance_after_hash=value.get("maintenance_after_hash"),
        maintenance_proof=maintenance,
        startup_prerequisite_id=value.get("startup_prerequisite_id"),
        startup_prerequisite_digest=value.get("startup_prerequisite_digest"),
        cutover_id=value.get("cutover_id"),
        catalog_id=value.get("catalog_id"),
        certificate_payload_hash=value.get("certificate_payload_hash"),
        certificate_signature_hash=value.get("certificate_signature_hash"),
        installed_tree_hash=value.get("installed_tree_hash"),
        head_id=value.get("head_id"),
        head_payload_hash=value.get("head_payload_hash"),
        head_signature_hash=value.get("head_signature_hash"),
        required_head_frame_hash=value.get("required_head_frame_hash"),
        verified_chain_head_id=value.get("verified_chain_head_id"),
        preflight_attestation_hash=value.get("preflight_attestation_hash"),
    )


def _record_hash_v2(encoded: bytes) -> str:
    return _digest(_RECORD_DOMAIN_V2 + encoded)


def _record_basename_v2(sequence: int) -> str:
    if type(sequence) is not int or not 0 <= sequence < len(_STATES):
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "state sequence",
        )
    return f"record-{sequence:03d}-v2.json"


@dataclass(frozen=True, slots=True)
class _ResolvedOwnershipTransactionV2:
    claim: SuccessorClaimV1
    records: tuple[OwnershipCoordinatorRecordV2, ...]
    encoded_records: tuple[bytes, ...]

    @property
    def latest(self) -> OwnershipCoordinatorRecordV2:
        return self.records[-1]


@dataclass(frozen=True, slots=True)
class _ObservedOwnershipCoordinatorGraphV2:
    """Durable graph observation; never proves the corresponding live state."""

    claims: tuple[SuccessorClaimV1, ...]
    pending_claims: tuple[SuccessorClaimV1, ...]
    transactions: tuple[_ResolvedOwnershipTransactionV2, ...]
    legacy_records: tuple[OwnershipCoordinatorRecordV1, ...]
    legacy_record_bytes: tuple[bytes, ...]
    legacy_disposition: LegacyDispositionV2 | None


class _LockedOwnershipCoordinatorGraphSnapshotV2:
    """Opaque fixed-root observation; it is not live certification."""

    __slots__ = ("_token", "__weakref__")

    def __init__(self, token: object) -> None:
        if type(token) is not object:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "graph authority",
            )
        object.__setattr__(self, "_token", token)

    def __copy__(self):
        raise TypeError("coordinator graph snapshots cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("coordinator graph snapshots cannot be copied")

    def __reduce__(self):
        raise TypeError("coordinator graph snapshots cannot be serialized")

    def __reduce_ex__(self, _protocol):
        raise TypeError("coordinator graph snapshots cannot be serialized")


_TEST_COORDINATOR_GRAPH_SNAPSHOT_SEAL_V2 = object()


@dataclass(frozen=True, slots=True)
class _OwnershipCoordinatorGraphSnapshotForTestV2:
    observation: _ObservedOwnershipCoordinatorGraphV2
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _TEST_COORDINATOR_GRAPH_SNAPSHOT_SEAL_V2
            or type(self.observation) is not _ObservedOwnershipCoordinatorGraphV2
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "test graph observation",
            )


def _require_read_only_directory_v2(
    directory: Path, *, root_owned: bool,
) -> None:
    try:
        info = directory.lstat()
    except OSError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "directory inventory",
        ) from exc
    invalid = (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    )
    if os.name != "nt":
        expected_owner = (0, 0) if root_owned else (os.geteuid(), os.getegid())
        invalid = invalid or (
            stat.S_IMODE(info.st_mode) != 0o755
            or (info.st_uid, info.st_gid) != expected_owner
        )
    if invalid:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "directory metadata",
        )


def _read_control_file_v2(
    path: Path, maximum: int, *, root_owned: bool,
) -> bytes:
    try:
        before = path.lstat()
        invalid = (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or bool(getattr(before, "st_file_attributes", 0) & 0x400)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > maximum
        )
        if os.name != "nt":
            expected_owner = (
                (0, 0) if root_owned else (os.geteuid(), os.getegid())
            )
            invalid = invalid or (
                stat.S_IMODE(before.st_mode) != 0o644
                or (before.st_uid, before.st_gid) != expected_owner
            )
        if invalid:
            raise ValueError("unsafe control file")
        encoded = _safe_read(path, maximum)
        after = path.lstat()
        identity_before = (
            before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
            before.st_uid, before.st_gid, before.st_size,
            getattr(before, "st_file_attributes", 0),
        )
        identity_after = (
            after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
            after.st_uid, after.st_gid, after.st_size,
            getattr(after, "st_file_attributes", 0),
        )
        if identity_after != identity_before or len(encoded) != before.st_size:
            raise ValueError("control file changed")
        return encoded
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "control file metadata",
        ) from exc


def _read_directory_entries_v2(directory: Path) -> tuple[Path, ...]:
    try:
        return tuple(sorted(directory.iterdir(), key=lambda item: item.name))
    except OSError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "directory inventory",
        ) from exc


_LEGACY_CARRY_KEYS_V1 = frozenset({
    "request_id", "previous_closed_build_id", "previous_cutover_id",
    "closed_build_id", "distribution_payload_hash",
    "distribution_signature_hash", "boundary_inventory_hash",
    "boundary_guard_version",
})
_TRANSACTION_CARRY_KEYS_V2 = _LEGACY_CARRY_KEYS_V1 | frozenset({
    "source_id", "successor_claim_id", "deployment_descriptor_id",
    "install_transaction_id", "release_sequence", "previous_head_id",
    "service_coverage_hash", "administrative_bundle_hash",
})
_TRANSACTION_THRESHOLD_KEYS_V2 = (
    (1, frozenset({
        "current_receipts", "maintenance_before_hash",
        "maintenance_after_hash", "maintenance_proof_b64",
    })),
    (2, frozenset({
        "startup_prerequisite_id", "startup_prerequisite_digest",
        "cutover_id", "catalog_id", "certificate_payload_hash",
        "certificate_signature_hash",
    })),
    (4, frozenset({"installed_tree_hash"})),
    (5, frozenset({
        "head_id", "head_payload_hash", "head_signature_hash",
        "required_head_frame_hash", "verified_chain_head_id",
    })),
    (6, frozenset({"preflight_attestation_hash"})),
)


def _read_legacy_journal_snapshot_v2(
    paths: tuple[Path, ...], *, root_owned: bool,
) -> tuple[tuple[OwnershipCoordinatorRecordV1, ...], tuple[bytes, ...]]:
    records: list[OwnershipCoordinatorRecordV1] = []
    encoded_records: list[bytes] = []
    previous_hash = None
    for sequence, path in enumerate(paths):
        if path.name != _record_basename(sequence):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "legacy journal gap",
            )
        encoded = _read_control_file_v2(
            path, MAX_RECORD_BYTES_V1, root_owned=root_owned,
        )
        try:
            record = _decode_record(encoded)
        except Exception as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "legacy journal record",
            ) from exc
        if (
            record.sequence != sequence
            or record.previous_record_sha256 != previous_hash
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "legacy journal chain",
            )
        if records:
            first_value = records[0].as_value()
            value = record.as_value()
            if any(value[key] != first_value[key] for key in _LEGACY_CARRY_KEYS_V1):
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required", "legacy journal carry",
                )
        records.append(record)
        encoded_records.append(encoded)
        previous_hash = _record_hash(encoded)
    if len(records) > 2 or (
        records
        and records[-1].state not in {
            OwnershipCoordinatorStateV1.PREPARED,
            OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE,
        }
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "legacy journal state",
        )
    if records and records[0].request_id != _coordinator_request_id_v1(
        records[0].closed_build_id,
        records[0].previous_closed_build_id,
        records[0].previous_cutover_id,
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "legacy request",
        )
    return tuple(records), tuple(encoded_records)


def _read_successor_claims_snapshot_v1(
    directory: Path | None, *, root_owned: bool,
) -> tuple[SuccessorClaimV1, ...]:
    if directory is None:
        return ()
    _require_read_only_directory_v2(directory, root_owned=root_owned)
    claims: list[SuccessorClaimV1] = []
    seen_claim_ids: set[str] = set()
    seen_request_ids: set[str] = set()
    for path in _read_directory_entries_v2(directory):
        if _SUCCESSOR_CLAIM_BASENAME_RE_V1.fullmatch(path.name) is None:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "claim inventory",
            )
        encoded = _read_control_file_v2(
            path, MAX_COORDINATOR_CONTROL_BYTES_V2, root_owned=root_owned,
        )
        try:
            claim = _decode_successor_claim_v1(encoded)
        except Exception as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "successor claim",
            ) from exc
        if path.name != _successor_claim_basename_v1(
            claim.release_sequence, claim.previous_head_id,
        ) or claim.claim_id in seen_claim_ids or claim.request_id in seen_request_ids:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "claim binding",
            )
        seen_claim_ids.add(claim.claim_id)
        seen_request_ids.add(claim.request_id)
        claims.append(claim)
    claims.sort(key=lambda item: item.release_sequence)
    if tuple(item.release_sequence for item in claims) != tuple(
        range(1, len(claims) + 1)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "claim sequence",
        )
    return tuple(claims)


def _read_transaction_directory_v2(
    directory: Path, *, request_id: str, root_owned: bool,
) -> tuple[tuple[OwnershipCoordinatorRecordV2, ...], tuple[bytes, ...]]:
    _require_digest(request_id, "request_id")
    _require_read_only_directory_v2(directory, root_owned=root_owned)
    paths = _read_directory_entries_v2(directory)
    if not 1 <= len(paths) <= len(_STATES):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "transaction cardinality",
        )
    records: list[OwnershipCoordinatorRecordV2] = []
    encoded_records: list[bytes] = []
    previous_hash = None
    for sequence, path in enumerate(paths):
        if (
            _TRANSACTION_RECORD_RE_V2.fullmatch(path.name) is None
            or path.name != _record_basename_v2(sequence)
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction inventory",
            )
        encoded = _read_control_file_v2(
            path, MAX_RECORD_BYTES_V1, root_owned=root_owned,
        )
        try:
            record = _decode_record_v2(encoded)
        except Exception as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction record",
            ) from exc
        if (
            record.sequence != sequence
            or record.request_id != request_id
            or record.previous_record_sha256 != previous_hash
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction chain",
            )
        if records:
            first_value = records[0].as_value()
            value = record.as_value()
            if any(
                value[key] != first_value[key]
                for key in _TRANSACTION_CARRY_KEYS_V2
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required", "transaction carry",
                )
            for threshold, keys in _TRANSACTION_THRESHOLD_KEYS_V2:
                if sequence > threshold:
                    threshold_value = records[threshold].as_value()
                    if any(value[key] != threshold_value[key] for key in keys):
                        raise OwnershipCoordinatorError(
                            "birth_ownership_recovery_required",
                            "transaction threshold carry",
                        )
        records.append(record)
        encoded_records.append(encoded)
        previous_hash = _record_hash_v2(encoded)
    return tuple(records), tuple(encoded_records)


def _read_transactions_snapshot_v2(
    directory: Path | None, *, root_owned: bool,
) -> tuple[tuple[OwnershipCoordinatorRecordV2, ...], tuple[tuple[bytes, ...], ...]]:
    if directory is None:
        return (), ()
    _require_read_only_directory_v2(directory, root_owned=root_owned)
    transactions: list[tuple[OwnershipCoordinatorRecordV2, ...]] = []
    encoded_transactions: list[tuple[bytes, ...]] = []
    for path in _read_directory_entries_v2(directory):
        if _DIGEST_RE.fullmatch(path.name) is None:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction directory",
            )
        records, encoded = _read_transaction_directory_v2(
            path, request_id=path.name, root_owned=root_owned,
        )
        transactions.append(records)
        encoded_transactions.append(encoded)
    return tuple(transactions), tuple(encoded_transactions)


def _coordinator_inventory_snapshot_v2(
    directory: Path,
) -> tuple[tuple[object, ...], ...]:
    observed: list[tuple[object, ...]] = []

    def add(path: Path, relative: str) -> os.stat_result:
        info = path.lstat()
        observed.append((
            relative, info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_uid, info.st_gid, info.st_size,
            getattr(info, "st_mtime_ns", None),
            getattr(info, "st_ctime_ns", None),
            getattr(info, "st_file_attributes", 0),
        ))
        return info

    def is_plain_directory(info: os.stat_result) -> bool:
        return (
            stat.S_ISDIR(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and not bool(getattr(info, "st_file_attributes", 0) & 0x400)
        )

    try:
        add(directory, ".")
        for path in _read_directory_entries_v2(directory):
            path_info = add(path, path.name)
            if (
                path.name == SUCCESSOR_CLAIMS_DIRECTORY_BASENAME_V1
                and is_plain_directory(path_info)
            ):
                for claim_path in _read_directory_entries_v2(path):
                    add(claim_path, f"{path.name}/{claim_path.name}")
            elif (
                path.name == TRANSACTIONS_DIRECTORY_BASENAME_V2
                and is_plain_directory(path_info)
            ):
                for transaction_path in _read_directory_entries_v2(path):
                    transaction_info = add(
                        transaction_path,
                        f"{path.name}/{transaction_path.name}",
                    )
                    if is_plain_directory(transaction_info):
                        for record_path in _read_directory_entries_v2(
                            transaction_path,
                        ):
                            add(record_path, (
                                f"{path.name}/{transaction_path.name}/"
                                f"{record_path.name}"
                            ))
    except OwnershipCoordinatorError:
        raise
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "coordinator snapshot",
        ) from exc
    return tuple(observed)


def _resolve_ownership_coordinator_at_v2(
    directory: Path, *, root_owned: bool,
) -> _ObservedOwnershipCoordinatorGraphV2:
    directory = Path(directory)
    _require_read_only_directory_v2(directory, root_owned=root_owned)
    initial_snapshot = _coordinator_inventory_snapshot_v2(directory)
    entries = _read_directory_entries_v2(directory)
    legacy_paths: list[Path] = []
    claim_directory = None
    transaction_directory = None
    disposition_path = None
    for path in entries:
        if _LEGACY_RECORD_RE_V1.fullmatch(path.name):
            legacy_paths.append(path)
        elif path.name == SUCCESSOR_CLAIMS_DIRECTORY_BASENAME_V1:
            if claim_directory is not None:
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required", "claim directory",
                )
            claim_directory = path
        elif path.name == TRANSACTIONS_DIRECTORY_BASENAME_V2:
            if transaction_directory is not None:
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required", "transaction root",
                )
            transaction_directory = path
        elif path.name == LEGACY_DISPOSITION_BASENAME_V2:
            disposition_path = path
        else:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "coordinator inventory",
            )
    legacy_records, legacy_bytes = _read_legacy_journal_snapshot_v2(
        tuple(sorted(legacy_paths, key=lambda item: item.name)),
        root_owned=root_owned,
    )
    claims = _read_successor_claims_snapshot_v1(
        claim_directory, root_owned=root_owned,
    )
    raw_transactions, encoded_transactions = _read_transactions_snapshot_v2(
        transaction_directory, root_owned=root_owned,
    )
    disposition = None
    if disposition_path is not None:
        encoded = _read_control_file_v2(
            disposition_path, MAX_COORDINATOR_CONTROL_BYTES_V2,
            root_owned=root_owned,
        )
        try:
            disposition = _decode_legacy_disposition_v2(encoded)
        except Exception as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "legacy disposition",
            ) from exc

    claims_by_id = {claim.claim_id: claim for claim in claims}
    transaction_by_claim: dict[str, _ResolvedOwnershipTransactionV2] = {}
    resolved_transactions: list[_ResolvedOwnershipTransactionV2] = []
    for records, encoded_records in zip(
        raw_transactions, encoded_transactions, strict=True,
    ):
        first = records[0]
        claim = claims_by_id.get(first.successor_claim_id)
        if (
            claim is None
            or claim.claim_id in transaction_by_claim
            or claim.request_id != first.request_id
            or claim.source_id != first.source_id
            or claim.closed_build_id != first.closed_build_id
            or claim.release_sequence != first.release_sequence
            or claim.previous_head_id != first.previous_head_id
            or first.request_id != _coordinator_request_id_v1(
                first.closed_build_id, first.previous_closed_build_id,
                first.previous_cutover_id,
            )
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "claim transaction binding",
            )
        resolved = _ResolvedOwnershipTransactionV2(
            claim, records, encoded_records,
        )
        transaction_by_claim[claim.claim_id] = resolved
        resolved_transactions.append(resolved)
    resolved_transactions.sort(key=lambda item: item.claim.release_sequence)

    pending: list[SuccessorClaimV1] = []
    previous_transaction = None
    for index, claim in enumerate(claims):
        transaction = transaction_by_claim.get(claim.claim_id)
        if claim.release_sequence == 1:
            expected_request_id = _coordinator_request_id_v1(
                claim.closed_build_id, None, None,
            )
        else:
            if (
                previous_transaction is None
                or previous_transaction.latest.sequence != 6
                or claim.previous_head_id != previous_transaction.latest.head_id
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required", "claim predecessor",
                )
            expected_request_id = _coordinator_request_id_v1(
                claim.closed_build_id,
                previous_transaction.records[0].closed_build_id,
                previous_transaction.latest.cutover_id,
            )
        if claim.request_id != expected_request_id:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "claim request",
            )
        if transaction is None:
            if index != len(claims) - 1:
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required", "nonterminal pending claim",
                )
            pending.append(claim)
            continue
        first = transaction.records[0]
        if claim.release_sequence == 1:
            if (
                first.previous_closed_build_id is not None
                or first.previous_cutover_id is not None
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required", "initial predecessor",
                )
        else:
            if (
                first.previous_closed_build_id
                != previous_transaction.records[0].closed_build_id
                or first.previous_cutover_id != previous_transaction.latest.cutover_id
                or first.previous_head_id != previous_transaction.latest.head_id
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required", "successor predecessor",
                )
        previous_transaction = transaction

    if legacy_records:
        latest_legacy = legacy_records[-1]
        if disposition is None:
            if raw_transactions or len(claims) > 1:
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required", "legacy prefix order",
                )
        else:
            successor = tuple(
                claim for claim in claims
                if claim.request_id == disposition.successor_request_id
            )
            if (
                len(successor) != 1
                or successor[0].release_sequence != 1
                or disposition.legacy_journal_hash
                != _legacy_journal_hash_v2(legacy_bytes)
                or disposition.legacy_request_id != latest_legacy.request_id
                or disposition.legacy_state is not latest_legacy.state
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required", "legacy disposition binding",
                )
    elif disposition is not None:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "orphan legacy disposition",
        )

    if _coordinator_inventory_snapshot_v2(directory) != initial_snapshot:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "coordinator changed",
        )
    return _ObservedOwnershipCoordinatorGraphV2(
        claims, tuple(pending), tuple(resolved_transactions), legacy_records,
        legacy_bytes, disposition,
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
        or (
            os.name != "nt"
            and (
                info.st_mode & 0o022
                or (root_owned and (info.st_uid != 0 or info.st_gid != 0))
            )
        )
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "directory metadata",
        )


@dataclass(slots=True)
class _DeploymentLockLeaseV1:
    active: bool
    process_id: int
    root: Path
    root_owned: bool
    descriptor: int
    identity: tuple[int, int]


class _DeploymentLockSessionV1:
    """Non-transferable proof that the fixed deployment lock is held."""

    __slots__ = ("_token", "_seal")

    def __init__(self, token: object, seal: object) -> None:
        if seal is not _DEPLOYMENT_LOCK_SESSION_SEAL:
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
        self._token = token
        self._seal = seal

    def __copy__(self):
        raise TypeError("deployment lock sessions cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("deployment lock sessions cannot be copied")

    def __reduce__(self):
        raise TypeError("deployment lock sessions cannot be serialized")

    def __reduce_ex__(self, _protocol):
        raise TypeError("deployment lock sessions cannot be serialized")


class _DeploymentLockSessionForTestV1:
    """Nominally separate session emitted only by the portable test seam."""

    __slots__ = ("_token", "_seal")

    def __init__(self, token: object, seal: object) -> None:
        if seal is not _TEST_DEPLOYMENT_LOCK_SESSION_SEAL:
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
        self._token = token
        self._seal = seal

    def __copy__(self):
        raise TypeError("deployment lock sessions cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("deployment lock sessions cannot be copied")

    def __reduce__(self):
        raise TypeError("deployment lock sessions cannot be serialized")

    def __reduce_ex__(self, _protocol):
        raise TypeError("deployment lock sessions cannot be serialized")


_DEPLOYMENT_LOCK_SESSION_SEAL = object()
_TEST_DEPLOYMENT_LOCK_SESSION_SEAL = object()
_DEPLOYMENT_LOCK_FORK_GUARD = threading.Lock()
_OPEN_DEPLOYMENT_LOCK_FDS_V1: set[int] = set()
_ACTIVE_DEPLOYMENT_LOCK_LEASES_V1: dict[int, _DeploymentLockLeaseV1] = {}
_ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1: dict[
    object, tuple[object, _DeploymentLockLeaseV1]
] = {}


def _install_deployment_lock_fork_handlers_v1() -> None:
    def before() -> None:
        _DEPLOYMENT_LOCK_FORK_GUARD.acquire()

    def after_parent() -> None:
        _DEPLOYMENT_LOCK_FORK_GUARD.release()

    def after_child() -> None:
        for lease in _ACTIVE_DEPLOYMENT_LOCK_LEASES_V1.values():
            lease.active = False
        for descriptor in _OPEN_DEPLOYMENT_LOCK_FDS_V1:
            try:
                os.close(descriptor)
            except OSError:
                pass
        _ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1.clear()
        _ACTIVE_DEPLOYMENT_LOCK_LEASES_V1.clear()
        _OPEN_DEPLOYMENT_LOCK_FDS_V1.clear()
        _DEPLOYMENT_LOCK_FORK_GUARD.release()

    if hasattr(os, "register_at_fork"):
        os.register_at_fork(
            before=before,
            after_in_parent=after_parent,
            after_in_child=after_child,
        )


_install_deployment_lock_fork_handlers_v1()
del _install_deployment_lock_fork_handlers_v1


def _require_live_deployment_lock_session_v1(
    session: object, *, expected_type: type, seal: object, root: Path,
    root_owned: bool,
) -> None:
    try:
        if type(session) is not expected_type:
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
        token = session._token
        if session._seal is not seal or type(token) is not object:
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
        with _DEPLOYMENT_LOCK_FORK_GUARD:
            registration = _ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1.get(
                token,
            )
            if (
                registration is None or registration[0] is not session
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_deployment_lock_invalid",
                )
            lease = registration[1]
            if (
                type(lease) is not _DeploymentLockLeaseV1
                or not lease.active
                or lease.process_id != os.getpid()
                or lease.root != Path(root)
                or lease.root_owned is not root_owned
                or _ACTIVE_DEPLOYMENT_LOCK_LEASES_V1.get(
                    lease.descriptor,
                ) is not lease
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_deployment_lock_invalid",
                )
        _require_deployment_lock_root_v1(Path(root), root_owned=root_owned)
        current = os.fstat(lease.descriptor)
        lock_info = (
            Path(root) / DEPLOYMENT_LOCK_BASENAME_V1
        ).lstat()
        expected_owner = (
            (0, 0) if root_owned else (os.geteuid(), os.getegid())
        )
        if (
            lease.identity != (current.st_dev, current.st_ino)
            or (current.st_dev, current.st_ino)
            != (lock_info.st_dev, lock_info.st_ino)
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or stat.S_IMODE(current.st_mode) != 0o600
            or (current.st_uid, current.st_gid) != expected_owner
            or current.st_size != 1
            or stat.S_ISLNK(lock_info.st_mode)
            or bool(getattr(lock_info, "st_file_attributes", 0) & 0x400)
            or os.pread(lease.descriptor, 1, 0) != b"\0"
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
    except OwnershipCoordinatorError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_deployment_lock_invalid",
        ) from exc


def _require_deployment_lock_session_v1(session: object) -> None:
    _require_live_deployment_lock_session_v1(
        session, expected_type=_DeploymentLockSessionV1,
        seal=_DEPLOYMENT_LOCK_SESSION_SEAL,
        root=DEFAULT_OWNERSHIP_ROOT_V1,
        root_owned=True,
    )


def _require_test_deployment_lock_session_v1(
    session: object, root: Path,
) -> None:
    _require_live_deployment_lock_session_v1(
        session, expected_type=_DeploymentLockSessionForTestV1,
        seal=_TEST_DEPLOYMENT_LOCK_SESSION_SEAL, root=Path(root),
        root_owned=False,
    )


def _require_deployment_lock_root_v1(root: Path, *, root_owned: bool) -> None:
    absolute = Path(os.path.abspath(root))
    for component in reversed((absolute, *absolute.parents)):
        try:
            info = component.lstat()
        except OSError as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            ) from exc
        if (
            not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0) & 0x400)
            or (root_owned and (
                info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022
            ))
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
    root_info = absolute.lstat()
    if stat.S_IMODE(root_info.st_mode) != 0o755:
        raise OwnershipCoordinatorError(
            "birth_ownership_deployment_lock_invalid",
        )


@contextmanager
def _deployment_lock_at_v1(
    root: Path, *, root_owned: bool,
) -> Iterator[_DeploymentLockLeaseV1]:
    if not sys.platform.startswith("linux"):
        raise OwnershipCoordinatorError("birth_ownership_platform_unsupported")
    root = Path(root)
    _require_deployment_lock_root_v1(root, root_owned=root_owned)
    path = root / DEPLOYMENT_LOCK_BASENAME_V1
    flags = (
        os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = None
    locked = False
    lease = None
    owner_process_id = os.getpid()
    try:
        with _DEPLOYMENT_LOCK_FORK_GUARD:
            try:
                fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                _OPEN_DEPLOYMENT_LOCK_FDS_V1.add(fd)
                created = True
                os.fchmod(fd, 0o600)
            except FileExistsError:
                fd = os.open(path, flags)
                _OPEN_DEPLOYMENT_LOCK_FDS_V1.add(fd)
                created = False
        info = os.fstat(fd)
        path_info = path.lstat()
        expected_owner = (0, 0) if root_owned else (os.geteuid(), os.getegid())
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (info.st_uid, info.st_gid) != expected_owner
            or stat.S_ISLNK(path_info.st_mode)
            or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
        mode = stat.S_IMODE(info.st_mode)
        if mode != 0o600:
            # A process can die after O_EXCL and before the first fchmod.  Only
            # that unambiguous, owner-only empty residue is safe to repair.
            if (
                created or info.st_size != 0
                or mode & ~0o600
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_deployment_lock_invalid",
                )
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise OwnershipCoordinatorError(
                    "birth_ownership_deployment_lock_invalid",
                )
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
        locked = True
        after_lock = os.fstat(fd)
        path_info = path.lstat()
        if (
            (after_lock.st_dev, after_lock.st_ino, after_lock.st_mode,
             after_lock.st_nlink, after_lock.st_uid, after_lock.st_gid)
            != (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_uid, info.st_gid)
            or (after_lock.st_dev, after_lock.st_ino)
            != (path_info.st_dev, path_info.st_ino)
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
        if info.st_size == 0:
            if os.write(fd, b"\0") != 1:
                raise OwnershipCoordinatorError(
                    "birth_ownership_deployment_lock_invalid",
                )
        elif info.st_size != 1:
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
        elif os.pread(fd, 1, 0) != b"\0":
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
        final = os.fstat(fd)
        if final.st_size != 1:
            raise OwnershipCoordinatorError(
                "birth_ownership_deployment_lock_invalid",
            )
        os.fsync(fd)
        _sync_directory(root)
        lease = _DeploymentLockLeaseV1(
            True, os.getpid(), root, root_owned, fd,
            (final.st_dev, final.st_ino),
        )
        with _DEPLOYMENT_LOCK_FORK_GUARD:
            _ACTIVE_DEPLOYMENT_LOCK_LEASES_V1[fd] = lease
        yield lease
    except OwnershipCoordinatorError:
        raise
    except OSError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_deployment_lock_invalid",
        ) from exc
    finally:
        if fd is not None and owner_process_id == os.getpid():
            with _DEPLOYMENT_LOCK_FORK_GUARD:
                if lease is not None:
                    lease.active = False
                _ACTIVE_DEPLOYMENT_LOCK_LEASES_V1.pop(fd, None)
                try:
                    if locked:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(fd)
                finally:
                    _OPEN_DEPLOYMENT_LOCK_FDS_V1.discard(fd)


@contextmanager
def _deployment_lock_v1() -> Iterator[_DeploymentLockSessionV1]:
    """Acquire the fixed outer lock and emit its non-transferable session."""
    with _deployment_lock_at_v1(
        DEFAULT_OWNERSHIP_ROOT_V1, root_owned=True,
    ) as lease:
        token = object()
        session = _DeploymentLockSessionV1(
            token, _DEPLOYMENT_LOCK_SESSION_SEAL,
        )
        with _DEPLOYMENT_LOCK_FORK_GUARD:
            if (
                _ACTIVE_DEPLOYMENT_LOCK_LEASES_V1.get(
                    lease.descriptor,
                ) is not lease
                or token in _ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_deployment_lock_invalid",
                )
            _ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1[token] = (session, lease)
        try:
            yield session
        finally:
            with _DEPLOYMENT_LOCK_FORK_GUARD:
                registration = _ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1.get(token)
                if registration is not None and registration[0] is session:
                    _ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1.pop(token, None)


@contextmanager
def _deployment_lock_for_test_v1(
    root: Path,
) -> Iterator[_DeploymentLockSessionForTestV1]:
    _ensure_directory(Path(root), root_owned=False)
    with _deployment_lock_at_v1(
        Path(root), root_owned=False,
    ) as lease:
        token = object()
        session = _DeploymentLockSessionForTestV1(
            token, _TEST_DEPLOYMENT_LOCK_SESSION_SEAL,
        )
        with _DEPLOYMENT_LOCK_FORK_GUARD:
            if (
                _ACTIVE_DEPLOYMENT_LOCK_LEASES_V1.get(
                    lease.descriptor,
                ) is not lease
                or token in _ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_deployment_lock_invalid",
                )
            _ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1[token] = (session, lease)
        try:
            yield session
        finally:
            with _DEPLOYMENT_LOCK_FORK_GUARD:
                registration = _ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1.get(token)
                if registration is not None and registration[0] is session:
                    _ACTIVE_DEPLOYMENT_LOCK_SESSIONS_V1.pop(token, None)


def _build_locked_coordinator_graph_registry_v2():
    issued = weakref.WeakKeyDictionary()

    def resolve_issued(
        session: _DeploymentLockSessionV1,
    ) -> _LockedOwnershipCoordinatorGraphSnapshotV2:
        if not sys.platform.startswith("linux"):
            raise OwnershipCoordinatorError(
                "birth_ownership_platform_unsupported",
            )
        _require_deployment_lock_session_v1(session)
        observation = _resolve_ownership_coordinator_at_v2(
            DEFAULT_COORDINATOR_DIRECTORY_V1, root_owned=True,
        )
        _require_deployment_lock_session_v1(session)
        token = object()
        snapshot = _LockedOwnershipCoordinatorGraphSnapshotV2(token)
        issued[snapshot] = (token, session, observation)
        return snapshot

    def require_issued(
        snapshot: object, session: _DeploymentLockSessionV1,
    ) -> _ObservedOwnershipCoordinatorGraphV2:
        _require_deployment_lock_session_v1(session)
        if type(snapshot) is not _LockedOwnershipCoordinatorGraphSnapshotV2:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "graph authority",
            )
        try:
            registration = issued.get(snapshot)
        except (AttributeError, TypeError) as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "graph authority",
            ) from exc
        if (
            registration is None
            or snapshot._token is not registration[0]
            or registration[1] is not session
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "graph authority",
            )
        return registration[2]

    return resolve_issued, require_issued


(
    _resolve_locked_coordinator_graph_issued_v2,
    _require_locked_coordinator_graph_issued_v2,
) = _build_locked_coordinator_graph_registry_v2()
del _build_locked_coordinator_graph_registry_v2


def _resolve_ownership_coordinator_locked_v2(
    session: _DeploymentLockSessionV1,
) -> _LockedOwnershipCoordinatorGraphSnapshotV2:
    return _resolve_locked_coordinator_graph_issued_v2(session)


def _require_locked_coordinator_graph_snapshot_v2(
    snapshot: object, session: _DeploymentLockSessionV1,
) -> _ObservedOwnershipCoordinatorGraphV2:
    return _require_locked_coordinator_graph_issued_v2(snapshot, session)


def _resolve_ownership_coordinator_locked_for_test_v2(
    session: _DeploymentLockSessionForTestV1, ownership_root: Path,
) -> _OwnershipCoordinatorGraphSnapshotForTestV2:
    """Portable nominal seam; its session is never accepted by product."""
    ownership_root = Path(ownership_root)
    _require_test_deployment_lock_session_v1(session, ownership_root)
    observation = _resolve_ownership_coordinator_at_v2(
        ownership_root / COORDINATOR_DIRECTORY_BASENAME_V1,
        root_owned=False,
    )
    _require_test_deployment_lock_session_v1(session, ownership_root)
    return _OwnershipCoordinatorGraphSnapshotForTestV2(
        observation, _TEST_COORDINATOR_GRAPH_SNAPSHOT_SEAL_V2,
    )


def _request_id(
    distribution: VerifiedDistribution, previous_cutover_id: str | None,
) -> str:
    return _coordinator_request_id_v1(
        distribution.identity.closed_build_id,
        distribution.previous_closed_build_id,
        previous_cutover_id,
    )


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
        verified = verify_current_installation_distribution_v1(
            distribution.encoded, distribution.signature,
        )
        if not _same_distribution(
            _prepared_record(distribution, previous_cutover_id=None), verified,
        ):
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
    crossing_receipt: str,
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorResultV1:
    """Isolated Group-5 proof of READY/publish/recovery; no productive caller.

    `crossing_receipt` is the digest the group 7 wrapper produced after it read
    every binding twice under the three locks and consumed its capability. It
    is REQUIRED, and required by position in the flow rather than by trust: this
    function cannot verify what the wrapper observed, but it can refuse to cross
    for a caller that never went through it. Without the parameter the crossing
    would be reachable from anywhere that holds a journal and a certificate
    directory, which is precisely the door group 7 exists to close.
    """
    if (
        not isinstance(prerequisite, _StartupPrerequisiteV1)
        or prerequisite._seal is not _PREREQUISITE_SEAL
        or not callable(observe_maintenance)
        or type(crossing_receipt) is not str
        or _DIGEST_RE.fullmatch(crossing_receipt) is None
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


def _advance_to_preflight_verified_v1(
    *, journal: OwnershipCoordinatorJournalV1,
    prerequisite: _StartupPrerequisiteV1,
    observe_installation: Callable[[], str],
    observe_required_head: Callable[[], str],
    observe_preflight: Callable[[], str],
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorResultV1:
    """Isolated proof of the last three durable boundaries; no productive caller.

    Each boundary is the same shape as the certificate one: observe the live
    evidence, append ONE record, and only then let the seam interrupt. A new
    process re-entering the function re-reads the evidence for every boundary
    already crossed and refuses if it moved, so resumption never trusts what a
    caller remembers — only what the journal and the system still say.

    The three observers are injected rather than imported. The coordinator must
    not grow a productive edge to the installed distribution, to the ownership
    head or to the preflight: it is the thing those subsystems are cut over
    BY, and an import here would make the boundary guard's graph a cycle.

    No new record field is needed. What each boundary must agree with is
    already carried forward: the closed build, the current proof and the
    cutover identity. Adding fields would have changed the durable codec for
    evidence the record already names.
    """
    if (
        not isinstance(prerequisite, _StartupPrerequisiteV1)
        or prerequisite._seal is not _PREREQUISITE_SEAL
        or not callable(observe_installation)
        or not callable(observe_required_head)
        or not callable(observe_preflight)
    ):
        raise OwnershipCoordinatorError("birth_ownership_prerequisite_untrusted")
    records = journal.load()
    if not records:
        raise OwnershipCoordinatorError("birth_ownership_receipts_incomplete")
    latest = records[-1]
    if latest.state.value not in _POST_CERTIFICATE_STATES_V1:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "certificate not published",
        )

    def _carry(state: OwnershipCoordinatorStateV1):
        return _copy_with_state(
            latest, state=state, prerequisite=prerequisite,
            cutover_id=latest.cutover_id, catalog_id=latest.catalog_id,
            payload_hash=latest.certificate_payload_hash,
            signature_hash=latest.certificate_signature_hash,
        )

    if latest.state is OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED:
        if observe_installation() != latest.closed_build_id:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "build drift",
            )
        latest = _append_coordinator_record_v1(
            journal, _carry(OwnershipCoordinatorStateV1.BUILD_VERIFIED),
        )
        if _crash_seam:
            _crash_seam("build_verified")
    elif latest.state is not OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED:
        # Already past the boundary: re-read it instead of trusting the record.
        if observe_installation() != latest.closed_build_id:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "build drift",
            )

    if latest.state is OwnershipCoordinatorStateV1.BUILD_VERIFIED:
        if observe_required_head() != latest.cutover_id:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "head drift",
            )
        latest = _append_coordinator_record_v1(
            journal, _carry(OwnershipCoordinatorStateV1.HEAD_REQUIRED),
        )
        if _crash_seam:
            _crash_seam("head_required")
    elif latest.state is OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED:
        if observe_required_head() != latest.cutover_id:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "head drift",
            )

    if latest.state is OwnershipCoordinatorStateV1.HEAD_REQUIRED:
        # The definitive preflight runs again on the effective topology and
        # publishes its attestation. After the point of no return the recovery
        # may not delete the certificate or the required head, so this is the
        # last boundary and it is crossed only once the attestation exists.
        if not _DIGEST_RE.fullmatch(observe_preflight() or ""):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "preflight attestation",
            )
        latest = _append_coordinator_record_v1(
            journal, _carry(OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED),
        )
        if _crash_seam:
            _crash_seam("preflight_verified")

    reread = journal.load()[-1]
    if (
        reread.state is not OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED
        or reread.request_id != latest.request_id
        or reread.cutover_id != latest.cutover_id
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "final record",
        )
    return _result(reread)


__all__ = [
    "OwnershipCoordinatorError", "OwnershipCoordinatorResultV1",
    "OwnershipCoordinatorStateV1", "prepare_ownership_cutover_v1",
]
