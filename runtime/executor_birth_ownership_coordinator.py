"""Root-owned, restartable RM-0008 ownership-cutover coordinator.

Group 5 may productively advance only through ``RECEIPTS_COMPLETE``.  The
certificate boundary exists here so its durable protocol can be certified in
isolation, but crossing it requires a sealed startup prerequisite that the
productive Group-5 entry cannot create.
"""
from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import sys
import threading
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterator, Mapping

from executor_birth_cutover import CurrentReceiptProof
from executor_birth_dominant_startup import is_dominant_startup_receipt_v1
from executor_birth_distribution_manifest import (
    VerifiedDistribution, is_verified_distribution,
    _verified_distribution_matches_payload_v1,
    installed_tree_hash_v1,
    verify_current_installation_distribution_v1,
)
from executor_birth_ownership_authorities import (
    DEFAULT_OWNERSHIP_ROOT_V1, RootOwnershipAuthoritiesV1,
    is_root_ownership_authorities_v1,
)
from executor_birth_ownership_cutover import (
    MAX_PAYLOAD_BYTES, PAYLOAD_BASENAME, SIGNATURE_BASENAME,
    OwnershipCutoverCertificate, OwnershipCutoverError,
    OwnershipCutoverRegistry,
    install_ownership_cutover_certificate,
    issue_ownership_cutover_certificate, read_ownership_cutover_certificate,
    verify_ownership_cutover_certificate, _prepare_recoverable_temporary,
    _publish_no_replace, _safe_read, _sync_directory, _write_temporary,
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
_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_PROVISIONING_TRANSACTION_RE = re.compile(r"[0-9a-f]{32}\Z")
_TEMPORARY_RECORD_RE = re.compile(
    r"\.record-([0-9]{3})-v1\.json\.([0-9a-f]{64})\.tmp\Z"
)
_LEGACY_RECORD_RE_V1 = re.compile(r"record-([0-9]{3})-v1\.json\Z")
_TRANSACTION_RECORD_RE_V2 = re.compile(r"record-([0-9]{3})-v2\.json\Z")
_TEMPORARY_TRANSACTION_RECORD_RE_V2 = re.compile(
    r"\.record-([0-9]{3})-v2\.json\.([0-9a-f]{64})\.tmp\Z"
)
_TEMPORARY_TRANSACTION_DIRECTORY_RE_V2 = re.compile(
    r"\.([0-9a-f]{64})\.v2\.tmp\Z"
)
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
_HEAD_PAYLOAD_HASH_DOMAIN_V2 = (
    b"metnos.executor-birth.head-payload-hash/v2\0"
)
_HEAD_SIGNATURE_HASH_DOMAIN_V2 = (
    b"metnos.executor-birth.head-signature-hash/v2\0"
)
_REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2 = (
    b"metnos.executor-birth.required-head-frame-hash/v2\0"
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
    "provisioning_transaction_id", "previous_set_id",
    "previous_admission_context_id", "previous_context_epoch",
    "target_set_id", "target_admission_context_id", "target_context_epoch",
    "target_context_material_sha256", "target_set_json_sha256",
    "context_transition_id", "current_inventory_hash",
    "dominant_startup_receipt",
})
_LEGACY_DISPOSITION_REASON_V2 = "superseded_before_certificate"


class OwnershipCoordinatorError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


_WRAPPED_TYPED_DETAIL_CODES_V1 = {
    ("birth_context_transition_recovery_required", "record"):
        "record_invalid",
    ("birth_context_transition_recovery_required", "record publication"):
        "record_publication",
    ("birth_context_transition_recovery_required", "record inventory"):
        "record_inventory",
    ("birth_context_transition_recovery_required", "unexpected record object"):
        "unexpected_record_object",
    ("birth_context_transition_recovery_required", "record object"):
        "record_object",
    ("birth_context_transition_recovery_required", "record name"):
        "record_name",
    ("birth_context_transition_recovery_required", "record duplicate"):
        "record_duplicate",
    ("birth_context_transition_recovery_required", "transition_id"):
        "transition_id",
    ("birth_context_transition_recovery_required", "record missing"):
        "record_missing",
    ("birth_context_transition_recovery_required", "record binding"):
        "record_binding",
}
_WRAPPED_CONTRACT_DETAIL_CODES_V1 = frozenset({
    "birth_cutover_generation_invalid",
    "birth_cutover_not_quiescent",
    "birth_cutover_receipt_binding_invalid",
    "birth_cutover_receipt_invalid",
    "birth_cutover_receipt_missing",
    "birth_cutover_receipt_not_durable",
    "birth_cutover_reattestation_failed",
    "birth_cutover_reattestation_missing",
})
_MAX_WRAPPED_CONTRACT_DETAIL_BYTES_V1 = 4096


def _wrapped_contract_detail_v1(code: str, detail: object) -> str | None:
    """Admit only one bounded, canonical contract identity as context."""
    if (
        code not in _WRAPPED_CONTRACT_DETAIL_CODES_V1
        or type(detail) is not str or ":" not in detail
        or not detail.isprintable()
    ):
        return None
    try:
        if len(detail.encode("utf-8")) > _MAX_WRAPPED_CONTRACT_DETAIL_BYTES_V1:
            return None
        origin, relative = detail.split(":", 1)
        from manifest_inventory import ContractId, ManifestOrigin

        canonical = ContractId(ManifestOrigin(origin), relative).value
    except (TypeError, UnicodeError, ValueError):
        return None
    return detail if canonical == detail else None


def _wrapped_cause_detail_v1(exc: BaseException) -> str:
    """Keep only allowlisted typed reason components from a known error."""
    code = getattr(exc, "code", None)
    if (
        not isinstance(code, str)
        or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code) is None
    ):
        return ""
    typed_detail = _WRAPPED_TYPED_DETAIL_CODES_V1.get(
        (code, getattr(exc, "detail", None)),
    )
    if typed_detail is not None:
        return f"{code}:{typed_detail}"
    contract_detail = _wrapped_contract_detail_v1(
        code, getattr(exc, "detail", None),
    )
    return f"{code}: {contract_detail}" if contract_detail is not None else code


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


def _framed_digest_v2(domain: bytes, encoded: bytes) -> str:
    if type(domain) is not bytes or not domain or type(encoded) is not bytes:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "framed digest",
        )
    return _digest(domain + len(encoded).to_bytes(8, "big") + encoded)


def _require_digest(value: object, field: str, *, nullable: bool = False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise OwnershipCoordinatorError("birth_ownership_journal_invalid", field)
    return value


def _require_hex_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _HEX_SHA256_RE.fullmatch(value) is None:
        raise OwnershipCoordinatorError("birth_ownership_journal_invalid", field)
    return value


def _require_provisioning_transaction_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or _PROVISIONING_TRANSACTION_RE.fullmatch(value) is None
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "provisioning_transaction_id",
        )
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
    provisioning_transaction_id: str
    previous_set_id: str
    previous_admission_context_id: str
    previous_context_epoch: str
    target_set_id: str
    target_admission_context_id: str
    target_context_epoch: str
    target_context_material_sha256: str
    target_set_json_sha256: str
    context_transition_id: str
    current_inventory_hash: str
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
    dominant_startup_receipt: str | None = None
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
            "administrative_bundle_hash", "previous_admission_context_id",
            "previous_context_epoch", "target_admission_context_id",
            "target_context_epoch", "context_transition_id",
            "current_inventory_hash",
        ):
            _require_digest(getattr(self, field), field)
        for field in (
            "previous_record_sha256", "previous_closed_build_id",
            "previous_cutover_id", "maintenance_before_hash",
            "maintenance_after_hash", "startup_prerequisite_id",
            "startup_prerequisite_digest", "cutover_id", "catalog_id",
            "certificate_payload_hash", "certificate_signature_hash",
            "dominant_startup_receipt",
            "installed_tree_hash", "head_id", "head_payload_hash",
            "head_signature_hash", "required_head_frame_hash",
            "verified_chain_head_id", "preflight_attestation_hash",
        ):
            _require_digest(getattr(self, field), field, nullable=True)
        _require_provisioning_transaction_id(self.provisioning_transaction_id)
        for field in (
            "previous_set_id", "target_set_id",
            "target_context_material_sha256", "target_set_json_sha256",
        ):
            _require_hex_sha256(getattr(self, field), field)
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
            from executor_birth_context_transition import current_inventory_hash_v1

            if self.current_inventory_hash != current_inventory_hash_v1(
                self.current_proof.inventory,
            ):
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_invalid", "current inventory binding",
                )
        certificate_fields = (
            self.startup_prerequisite_id, self.startup_prerequisite_digest,
            self.cutover_id, self.catalog_id, self.certificate_payload_hash,
            self.certificate_signature_hash, self.dominant_startup_receipt,
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
            "provisioning_transaction_id": self.provisioning_transaction_id,
            "previous_set_id": self.previous_set_id,
            "previous_admission_context_id": (
                self.previous_admission_context_id
            ),
            "previous_context_epoch": self.previous_context_epoch,
            "target_set_id": self.target_set_id,
            "target_admission_context_id": self.target_admission_context_id,
            "target_context_epoch": self.target_context_epoch,
            "target_context_material_sha256": (
                self.target_context_material_sha256
            ),
            "target_set_json_sha256": self.target_set_json_sha256,
            "context_transition_id": self.context_transition_id,
            "current_inventory_hash": self.current_inventory_hash,
            "dominant_startup_receipt": self.dominant_startup_receipt,
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
        provisioning_transaction_id=value.get("provisioning_transaction_id"),
        previous_set_id=value.get("previous_set_id"),
        previous_admission_context_id=value.get(
            "previous_admission_context_id",
        ),
        previous_context_epoch=value.get("previous_context_epoch"),
        target_set_id=value.get("target_set_id"),
        target_admission_context_id=value.get("target_admission_context_id"),
        target_context_epoch=value.get("target_context_epoch"),
        target_context_material_sha256=value.get(
            "target_context_material_sha256",
        ),
        target_set_json_sha256=value.get("target_set_json_sha256"),
        context_transition_id=value.get("context_transition_id"),
        current_inventory_hash=value.get("current_inventory_hash"),
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
        dominant_startup_receipt=value.get("dominant_startup_receipt"),
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
    "provisioning_transaction_id", "previous_set_id",
    "previous_admission_context_id", "previous_context_epoch",
    "target_set_id", "target_admission_context_id", "target_context_epoch",
    "target_context_material_sha256", "target_set_json_sha256",
    "context_transition_id", "current_inventory_hash",
})
_TRANSACTION_THRESHOLD_KEYS_V2 = (
    (1, frozenset({
        "current_receipts", "maintenance_before_hash",
        "maintenance_after_hash", "maintenance_proof_b64",
    })),
    (2, frozenset({
        "startup_prerequisite_id", "startup_prerequisite_digest",
        "cutover_id", "catalog_id", "certificate_payload_hash",
        "certificate_signature_hash", "dominant_startup_receipt",
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
        _require_transaction_record_link_v2(
            tuple(records), record, sequence=sequence,
            request_id=request_id, previous_hash=previous_hash,
        )
        records.append(record)
        encoded_records.append(encoded)
        previous_hash = _record_hash_v2(encoded)
    return tuple(records), tuple(encoded_records)


def _require_transaction_record_link_v2(
    records: tuple[OwnershipCoordinatorRecordV2, ...],
    record: OwnershipCoordinatorRecordV2, *, sequence: int,
    request_id: str, previous_hash: str | None,
) -> None:
    if (
        type(record) is not OwnershipCoordinatorRecordV2
        or record.sequence != sequence
        or record.request_id != request_id
        or record.previous_record_sha256 != previous_hash
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "transaction chain",
        )
    if not records:
        return
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


def _ensure_coordinator_child_directory_v2(
    parent: Path, basename: str, *, root_owned: bool,
) -> tuple[Path, bool]:
    _require_read_only_directory_v2(parent, root_owned=root_owned)
    child = parent / basename
    created = False
    try:
        child.mkdir(mode=0o755)
        created = True
        if os.name != "nt":
            child.chmod(0o755)
    except FileExistsError:
        pass
    except OSError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "transaction directory",
        ) from exc
    _require_read_only_directory_v2(child, root_owned=root_owned)
    if created:
        try:
            _sync_directory(parent)
        except OSError as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction directory sync",
            ) from exc
    return child, created


def _publish_control_no_replace_v2(
    parent: Path, basename: str, encoded: bytes, *, maximum: int,
    root_owned: bool,
) -> bytes:
    """Publish and reread one immutable control file without replacement."""
    if (
        not sys.platform.startswith("linux")
        or type(basename) is not str or not basename
        or "/" in basename or "\\" in basename
        or type(encoded) is not bytes or not 0 < len(encoded) <= maximum
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "control publication",
        )
    _require_read_only_directory_v2(parent, root_owned=root_owned)
    destination = parent / basename
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = None
    try:
        try:
            descriptor = os.open(destination, flags, 0o600)
        except FileExistsError:
            observed = _read_control_file_v2(
                destination, maximum, root_owned=root_owned,
            )
            if observed != encoded:
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_conflict", basename,
                )
            return observed
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short control write")
            offset += written
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
    except OwnershipCoordinatorError:
        raise
    except OSError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "control publication",
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        _sync_directory(parent)
    except OSError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "control publication sync",
        ) from exc
    observed = _read_control_file_v2(
        destination, maximum, root_owned=root_owned,
    )
    if observed != encoded:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "control publication reread",
        )
    return observed


def _temporary_transaction_directory_v2(
    transactions: Path, request_id: str,
) -> Path:
    _require_digest(request_id, "request_id")
    return transactions / f".{request_id[7:]}.v2.tmp"


def _require_staged_transaction_directory_v2(
    directory: Path, *, root_owned: bool,
) -> os.stat_result:
    """Accept only a private subset of the final directory mode."""
    try:
        info = directory.lstat()
    except OSError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required",
            "staged transaction directory",
        ) from exc
    invalid = (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    )
    if os.name != "nt":
        expected_owner = (
            (0, 0) if root_owned else (os.geteuid(), os.getegid())
        )
        mode = stat.S_IMODE(info.st_mode)
        invalid = invalid or (
            (info.st_uid, info.st_gid) != expected_owner
            or mode & ~0o755 != 0
            or mode & 0o700 != 0o700
        )
    if invalid:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required",
            "staged transaction directory",
        )
    return info


def _read_staged_control_file_v2(
    path: Path, maximum: int, *, root_owned: bool,
) -> bytes:
    """Read a stable unpublished file, including a recoverable strict prefix."""
    flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        path_before = path.lstat()
        expected_owner = (
            (0, 0) if root_owned else (os.geteuid(), os.getegid())
        )
        invalid = (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 0
            or before.st_size > maximum
            or stat.S_ISLNK(path_before.st_mode)
            or bool(getattr(path_before, "st_file_attributes", 0) & 0x400)
            or (before.st_dev, before.st_ino)
            != (path_before.st_dev, path_before.st_ino)
        )
        if os.name != "nt":
            invalid = invalid or (
                stat.S_IMODE(before.st_mode) not in {0o600, 0o644}
                or (before.st_uid, before.st_gid) != expected_owner
            )
        if invalid:
            raise ValueError("staged control file metadata")
        chunks = bytearray()
        while len(chunks) < before.st_size:
            chunk = os.read(descriptor, before.st_size - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
        after = os.fstat(descriptor)
        path_after = path.lstat()
        identity = lambda info: (
            info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_uid, info.st_gid, info.st_size,
            getattr(info, "st_file_attributes", 0),
        )
        if (
            identity(after) != identity(before)
            or identity(path_after) != identity(path_before)
            or len(chunks) != before.st_size
        ):
            raise ValueError("staged control file changed")
        return bytes(chunks)
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required",
            "staged control file metadata",
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_staged_transaction_directory_v2(
    directory: Path, *, request_id: str, root_owned: bool,
) -> bytes | None:
    """Validate one unpublished directory without treating it as committed."""
    _require_staged_transaction_directory_v2(
        directory, root_owned=root_owned,
    )
    paths = _read_directory_entries_v2(directory)
    if not paths:
        return None
    if len(paths) != 1 or paths[0].name != _record_basename_v2(0):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required",
            "staged transaction inventory",
        )
    encoded = _read_staged_control_file_v2(
        paths[0], MAX_RECORD_BYTES_V1, root_owned=root_owned,
    )
    try:
        record = _decode_record_v2(encoded)
        _require_transaction_record_link_v2(
            (), record, sequence=0, request_id=request_id,
            previous_hash=None,
        )
    except Exception:
        # A power loss may leave a strict prefix in this unpublished location.
        # The writer compares it with the exact record before replacing it.
        return encoded
    return encoded


def _publish_transaction_directory_no_replace_v2(
    temporary: Path, destination: Path,
) -> bool:
    """Atomically publish a complete transaction directory on Linux."""
    if not sys.platform.startswith("linux"):
        raise OwnershipCoordinatorError(
            "birth_ownership_platform_unsupported",
        )
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "directory rename support",
        )
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
            return False
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", destination.name,
        ) from OSError(number, os.strerror(number), destination)
    try:
        _sync_directory(destination.parent)
    except OSError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "transaction directory sync",
        ) from exc
    return True


class _OwnershipCoordinatorTransactionJournalV2:
    """Append and recover one exact V2 transaction under the outer lock."""

    __slots__ = (
        "coordinator_directory", "directory", "request_id", "_root_owned",
    )

    def __init__(
        self, coordinator_directory: Path,
        record: OwnershipCoordinatorRecordV2, *, root_owned: bool,
    ) -> None:
        if type(record) is not OwnershipCoordinatorRecordV2:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "transaction record",
            )
        self.coordinator_directory = Path(coordinator_directory)
        self.request_id = record.request_id
        claims = _read_successor_claims_snapshot_v1(
            self.coordinator_directory
            / SUCCESSOR_CLAIMS_DIRECTORY_BASENAME_V1,
            root_owned=root_owned,
        )
        matching_claims = tuple(
            claim for claim in claims if claim.request_id == record.request_id
        )
        if len(matching_claims) != 1:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "claim transaction binding",
            )
        claim = matching_claims[0]
        if (
            claim.claim_id != record.successor_claim_id
            or claim.source_id != record.source_id
            or claim.closed_build_id != record.closed_build_id
            or claim.release_sequence != record.release_sequence
            or claim.previous_head_id != record.previous_head_id
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "claim transaction binding",
            )
        transactions = (
            self.coordinator_directory / TRANSACTIONS_DIRECTORY_BASENAME_V2
        )
        try:
            transactions.lstat()
            transactions_exists = True
        except FileNotFoundError:
            transactions_exists = False
        except OSError as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction directory",
            ) from exc
        if not transactions_exists and record.sequence != 0:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction gap",
            )
        if transactions_exists:
            _require_read_only_directory_v2(
                transactions, root_owned=root_owned,
            )
        else:
            transactions, _ = _ensure_coordinator_child_directory_v2(
                self.coordinator_directory,
                TRANSACTIONS_DIRECTORY_BASENAME_V2,
                root_owned=root_owned,
            )
        candidate = transactions / self.request_id
        try:
            candidate.lstat()
            candidate_exists = True
        except FileNotFoundError:
            candidate_exists = False
        except OSError as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction directory",
            ) from exc
        if not candidate_exists and record.sequence != 0:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction gap",
            )
        if candidate_exists:
            _require_read_only_directory_v2(
                candidate, root_owned=root_owned,
            )
            if not _read_directory_entries_v2(candidate):
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required",
                    "transaction cardinality",
                )
        self.directory = candidate
        self._root_owned = root_owned

    def _append_initial(
        self, record: OwnershipCoordinatorRecordV2, *,
        _crash_seam: Callable[[str], None] | None,
    ) -> OwnershipCoordinatorRecordV2:
        transactions = self.directory.parent
        temporary = _temporary_transaction_directory_v2(
            transactions, self.request_id,
        )
        try:
            temporary.mkdir(mode=0o755)
        except FileExistsError:
            pass
        except OSError as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required",
                "staged transaction directory",
            ) from exc
        temporary_info = _require_staged_transaction_directory_v2(
            temporary, root_owned=self._root_owned,
        )
        if (
            os.name != "nt"
            and stat.S_IMODE(temporary_info.st_mode) != 0o755
        ):
            try:
                temporary.chmod(0o755)
                _sync_directory(transactions)
            except OSError as exc:
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required",
                    "staged transaction directory",
                ) from exc
            _require_read_only_directory_v2(
                temporary, root_owned=self._root_owned,
            )
        if _crash_seam is not None:
            _crash_seam("transaction_directory_staged")

        encoded = record.encode()
        staged_record = temporary / _record_basename_v2(0)
        observed = _read_staged_transaction_directory_v2(
            temporary, request_id=self.request_id,
            root_owned=self._root_owned,
        )
        try:
            if observed is not None and observed != encoded:
                if len(observed) >= len(encoded) or not encoded.startswith(observed):
                    raise OwnershipCoordinatorError(
                        "birth_ownership_journal_conflict", staged_record.name,
                    )
                staged_record.unlink()
                _sync_directory(temporary)
                observed = None
            if observed is None:
                _write_temporary(staged_record, encoded)
                _sync_directory(temporary)
            if _crash_seam is not None:
                _crash_seam("transaction_record_staged")
            published = _publish_transaction_directory_no_replace_v2(
                temporary, self.directory,
            )
        except (OwnershipCoordinatorError, InterruptedError):
            raise
        except OSError as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "staged transaction",
            ) from exc
        if not published:
            loaded, _ = _read_transaction_directory_v2(
                self.directory, request_id=self.request_id,
                root_owned=self._root_owned,
            )
            if len(loaded) != 1 or loaded[0] != record:
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_conflict",
                    _record_basename_v2(0),
                )
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required",
                "staged transaction collision",
            )
        if _crash_seam is not None:
            _crash_seam("transaction_record_published")
        loaded, _ = _read_transaction_directory_v2(
            self.directory, request_id=self.request_id,
            root_owned=self._root_owned,
        )
        if len(loaded) != 1 or loaded[0] != record:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction reread",
            )
        return loaded[0]

    def _inventory(
        self,
    ) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
        committed: list[Path] = []
        temporary: list[Path] = []
        for path in _read_directory_entries_v2(self.directory):
            if _TRANSACTION_RECORD_RE_V2.fullmatch(path.name):
                committed.append(path)
            elif _TEMPORARY_TRANSACTION_RECORD_RE_V2.fullmatch(path.name):
                temporary.append(path)
            else:
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required",
                    "transaction inventory",
                )
        return (
            tuple(sorted(committed, key=lambda item: item.name)),
            tuple(sorted(temporary, key=lambda item: item.name)),
        )

    def _committed(
        self, paths: tuple[Path, ...],
    ) -> tuple[OwnershipCoordinatorRecordV2, ...]:
        if not paths:
            return ()
        records: list[OwnershipCoordinatorRecordV2] = []
        previous_hash = None
        for sequence, path in enumerate(paths):
            if path.name != _record_basename_v2(sequence):
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required",
                    "transaction inventory",
                )
            encoded = _read_control_file_v2(
                path, MAX_RECORD_BYTES_V1, root_owned=self._root_owned,
            )
            record = _decode_record_v2(encoded)
            _require_transaction_record_link_v2(
                tuple(records), record, sequence=sequence,
                request_id=self.request_id, previous_hash=previous_hash,
            )
            records.append(record)
            previous_hash = _record_hash_v2(encoded)
        return tuple(records)

    def append_transaction_record(
        self, record: OwnershipCoordinatorRecordV2, *,
        _crash_seam: Callable[[str], None] | None = None,
    ) -> OwnershipCoordinatorRecordV2:
        if type(record) is not OwnershipCoordinatorRecordV2:
            raise OwnershipCoordinatorError(
                "birth_ownership_journal_invalid", "transaction record",
            )
        if record.request_id != self.request_id:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "claim transaction binding",
            )
        if not self.directory.exists():
            _require_transaction_record_link_v2(
                (), record, sequence=0, request_id=self.request_id,
                previous_hash=None,
            )
            return self._append_initial(record, _crash_seam=_crash_seam)
        committed_paths, temporary_paths = self._inventory()
        records = self._committed(committed_paths)
        sequence = len(records)
        expected_temporary_name = (
            f".{_record_basename_v2(record.sequence)}."
            f"{record.request_id[7:]}.tmp"
        )
        if temporary_paths and (
            len(temporary_paths) != 1
            or temporary_paths[0].name != expected_temporary_name
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required",
                "transaction temporary inventory",
            )
        if record.sequence < sequence:
            if temporary_paths or records[record.sequence] != record:
                raise OwnershipCoordinatorError(
                    "birth_ownership_journal_conflict",
                    _record_basename_v2(record.sequence),
                )
            return records[record.sequence]
        previous_hash = (
            _record_hash_v2(records[-1].encode()) if records else None
        )
        _require_transaction_record_link_v2(
            records, record, sequence=sequence,
            request_id=self.request_id, previous_hash=previous_hash,
        )
        encoded = record.encode()
        destination = self.directory / _record_basename_v2(sequence)
        temporary = self.directory / expected_temporary_name
        try:
            publish = _prepare_recoverable_temporary(
                temporary, destination, encoded,
            )
            if publish and _crash_seam is not None:
                _crash_seam("transaction_record_staged")
            if publish:
                _publish_no_replace(temporary, destination, encoded)
            if _crash_seam is not None:
                _crash_seam("transaction_record_published")
        except OwnershipCutoverError as exc:
            code = (
                "birth_ownership_journal_conflict"
                if exc.code == "birth_ownership_cutover_conflict"
                else "birth_ownership_recovery_required"
            )
            raise OwnershipCoordinatorError(code, exc.detail) from exc
        loaded, _ = _read_transaction_directory_v2(
            self.directory, request_id=self.request_id,
            root_owned=self._root_owned,
        )
        if len(loaded) != sequence + 1 or loaded[-1] != record:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction reread",
            )
        return loaded[-1]


def _read_transactions_snapshot_v2(
    directory: Path | None, *, root_owned: bool,
) -> tuple[tuple[OwnershipCoordinatorRecordV2, ...], tuple[tuple[bytes, ...], ...]]:
    if directory is None:
        return (), ()
    _require_read_only_directory_v2(directory, root_owned=root_owned)
    transactions: list[tuple[OwnershipCoordinatorRecordV2, ...]] = []
    encoded_transactions: list[tuple[bytes, ...]] = []
    committed_request_ids: set[str] = set()
    staged_request_ids: set[str] = set()
    for path in _read_directory_entries_v2(directory):
        staged_match = _TEMPORARY_TRANSACTION_DIRECTORY_RE_V2.fullmatch(
            path.name,
        )
        if staged_match is not None:
            request_id = "sha256:" + staged_match.group(1)
            if request_id in staged_request_ids:
                raise OwnershipCoordinatorError(
                    "birth_ownership_recovery_required",
                    "staged transaction duplicate",
                )
            _read_staged_transaction_directory_v2(
                path, request_id=request_id, root_owned=root_owned,
            )
            staged_request_ids.add(request_id)
            continue
        if _DIGEST_RE.fullmatch(path.name) is None:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction directory",
            )
        records, encoded = _read_transaction_directory_v2(
            path, request_id=path.name, root_owned=root_owned,
        )
        committed_request_ids.add(path.name)
        transactions.append(records)
        encoded_transactions.append(encoded)
    if committed_request_ids & staged_request_ids:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required",
            "transaction publication ambiguity",
        )
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


def _append_ownership_transaction_locked_v2(
    session: _DeploymentLockSessionV1,
    record: OwnershipCoordinatorRecordV2,
) -> OwnershipCoordinatorRecordV2:
    _require_deployment_lock_session_v1(session)
    if type(record) is not OwnershipCoordinatorRecordV2:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "transaction record",
        )
    journal = _OwnershipCoordinatorTransactionJournalV2(
        DEFAULT_COORDINATOR_DIRECTORY_V1, record, root_owned=True,
    )
    result = journal.append_transaction_record(record)
    _require_deployment_lock_session_v1(session)
    return result


def _append_ownership_transaction_locked_for_test_v2(
    session: _DeploymentLockSessionForTestV1, ownership_root: Path,
    record: OwnershipCoordinatorRecordV2, *,
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorRecordV2:
    """Portable persistence seam; never accepts a productive lock session."""
    ownership_root = Path(ownership_root)
    _require_test_deployment_lock_session_v1(session, ownership_root)
    if type(record) is not OwnershipCoordinatorRecordV2:
        raise OwnershipCoordinatorError(
            "birth_ownership_journal_invalid", "transaction record",
        )
    journal = _OwnershipCoordinatorTransactionJournalV2(
        ownership_root / COORDINATOR_DIRECTORY_BASENAME_V1,
        record, root_owned=False,
    )
    result = journal.append_transaction_record(
        record, _crash_seam=_crash_seam,
    )
    _require_test_deployment_lock_session_v1(session, ownership_root)
    return result


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


def _prepared_record_v2(
    *, claim: object, distribution: object, predecessor: object,
    previous_context: object, prepared_authority_set: object,
    current_inventory: object, deployment_descriptor: object,
) -> tuple[OwnershipCoordinatorRecordV2, object]:
    """Bind one exact staged set and frozen inventory before publication."""
    from executor_birth_admin_preflight import (
        PreflightError, _administrative_bundle_hash_v1,
    )
    from executor_birth_context_selection import is_context_selection_v1
    from executor_birth_context_transition import (
        ContextTransitionError, issue_context_transition_v1,
    )
    from executor_birth_cutover import CurrentInventoryV1
    from executor_birth_distribution_assembler import DeploymentDescriptorV1
    from executor_birth_prepared_set import (
        is_prepared_authority_set_v2, is_prepared_set_v1,
    )

    if (
        type(claim) is not SuccessorClaimV1
        or not is_verified_distribution(distribution)
        or not is_prepared_authority_set_v2(prepared_authority_set)
        or type(current_inventory) is not CurrentInventoryV1
        or type(deployment_descriptor) is not DeploymentDescriptorV1
    ):
        raise OwnershipCoordinatorError("birth_ownership_request_conflict")
    target = prepared_authority_set
    descriptor = deployment_descriptor
    payload_hash = _digest(distribution.encoded)
    signature_hash = _digest(distribution.signature)
    if (
        claim.closed_build_id != distribution.identity.closed_build_id
        or claim.release_sequence != distribution.release_sequence
        or target.request_id != claim.request_id
        or target.closed_build_id != claim.closed_build_id
        or target.distribution_payload_hash != payload_hash
        or target.distribution_signature_hash != signature_hash
        or descriptor.release_sequence != claim.release_sequence
        or descriptor.installation_root != distribution.installation_root
    ):
        raise OwnershipCoordinatorError("birth_ownership_request_conflict")

    if predecessor is None:
        if (
            claim.release_sequence != 1
            or claim.previous_head_id is not None
            or distribution.previous_closed_build_id is not None
            or not is_prepared_set_v1(previous_context)
        ):
            raise OwnershipCoordinatorError("birth_ownership_request_conflict")
        previous_cutover_id = None
        previous_closed_build_id = None
        previous_set_id = previous_context.set_id
        previous_admission_context_id = (
            previous_context.prepared_admission_context_id
        )
        previous_context_epoch = previous_context.prepared_context_epoch
    else:
        if (
            type(predecessor) is not OwnershipCoordinatorRecordV2
            or predecessor.state
            is not OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED
            or predecessor.release_sequence + 1 != claim.release_sequence
            or predecessor.head_id != claim.previous_head_id
            or predecessor.closed_build_id
            != distribution.previous_closed_build_id
            or not is_context_selection_v1(previous_context)
            or previous_context.distribution.identity.closed_build_id
            != predecessor.closed_build_id
        ):
            raise OwnershipCoordinatorError("birth_ownership_request_conflict")
        previous_cutover_id = predecessor.cutover_id
        previous_closed_build_id = predecessor.closed_build_id
        previous_set_id = previous_context.set_id
        previous_admission_context_id = previous_context.admission_context_id
        previous_context_epoch = previous_context.context_epoch

    expected_request_id = _coordinator_request_id_v1(
        claim.closed_build_id,
        previous_closed_build_id,
        previous_cutover_id,
    )
    if (
        claim.request_id != expected_request_id
        or target.previous_set_id != previous_set_id
    ):
        raise OwnershipCoordinatorError("birth_ownership_request_conflict")
    try:
        transition_encoded, transition = issue_context_transition_v1(
            request_id=claim.request_id,
            closed_build_id=claim.closed_build_id,
            previous_cutover_id=previous_cutover_id,
            previous_set_id=previous_set_id,
            previous_admission_context_id=previous_admission_context_id,
            previous_context_epoch=previous_context_epoch,
            set_id=target.target_set_id,
            prepared_admission_context_id=(
                target.target_admission_context_id
            ),
            prepared_context_epoch=target.target_context_epoch,
            context_material_sha256=(
                target.target_context_material_sha256
            ),
            set_json_sha256=target.target_set_json_sha256,
            current_inventory=current_inventory,
        )
        administrative_bundle_hash = _administrative_bundle_hash_v1(
            descriptor,
        )
    except (ContextTransitionError, PreflightError) as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_request_conflict",
            _wrapped_cause_detail_v1(exc),
        ) from exc
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_request_conflict",
        ) from exc
    if transition.encoded != transition_encoded:
        raise OwnershipCoordinatorError("birth_ownership_request_conflict")
    install_value = {
        "schema_version": 1,
        "request_id": claim.request_id,
        "source_id": claim.source_id,
        "closed_build_id": claim.closed_build_id,
        "release_sequence": claim.release_sequence,
        "previous_head_id": claim.previous_head_id,
        "successor_claim_id": claim.claim_id,
        "deployment_descriptor_id": descriptor.descriptor_id,
        "service_coverage_hash": descriptor.service_coverage_hash,
        "administrative_bundle_hash": administrative_bundle_hash,
    }
    record = OwnershipCoordinatorRecordV2(
        sequence=0,
        state=OwnershipCoordinatorStateV1.PREPARED,
        previous_record_sha256=None,
        request_id=claim.request_id,
        previous_closed_build_id=previous_closed_build_id,
        previous_cutover_id=previous_cutover_id,
        closed_build_id=claim.closed_build_id,
        distribution_payload_hash=payload_hash,
        distribution_signature_hash=signature_hash,
        boundary_inventory_hash=(
            distribution.identity.boundary_inventory_hash
        ),
        boundary_guard_version=distribution.identity.boundary_guard_version,
        source_id=claim.source_id,
        successor_claim_id=claim.claim_id,
        deployment_descriptor_id=descriptor.descriptor_id,
        install_transaction_id=_install_transaction_id_v1(install_value),
        release_sequence=claim.release_sequence,
        previous_head_id=claim.previous_head_id,
        service_coverage_hash=descriptor.service_coverage_hash,
        administrative_bundle_hash=administrative_bundle_hash,
        provisioning_transaction_id=target.transaction_id,
        previous_set_id=previous_set_id,
        previous_admission_context_id=previous_admission_context_id,
        previous_context_epoch=previous_context_epoch,
        target_set_id=target.target_set_id,
        target_admission_context_id=target.target_admission_context_id,
        target_context_epoch=target.target_context_epoch,
        target_context_material_sha256=(
            target.target_context_material_sha256
        ),
        target_set_json_sha256=target.target_set_json_sha256,
        context_transition_id=transition.transition_id,
        current_inventory_hash=transition.current_inventory_hash,
    )
    return record, transition


_PREPARED_TRANSITION_PUBLICATION_SEAL_V2 = object()


@dataclass(frozen=True, slots=True)
class PreparedTransitionPublicationV2:
    """Exact artifacts available after PREPARED and set publication."""

    record: OwnershipCoordinatorRecordV2
    transition: object
    prepared_authority_set: object
    distribution: VerifiedDistribution
    deployment_descriptor: object
    current_inventory: object
    _seal: object

    def __post_init__(self) -> None:
        from executor_birth_context_transition import (
            ContextTransitionError, ContextTransitionV1,
            current_inventory_hash_v1,
            verify_context_transition_v1,
        )
        from executor_birth_cutover import CurrentInventoryV1
        from executor_birth_distribution_assembler import DeploymentDescriptorV1
        from executor_birth_prepared_set import is_prepared_authority_set_v2

        if (
            self._seal is not _PREPARED_TRANSITION_PUBLICATION_SEAL_V2
            or type(self.record) is not OwnershipCoordinatorRecordV2
            or self.record.state is not OwnershipCoordinatorStateV1.PREPARED
            or type(self.transition) is not ContextTransitionV1
            or type(self.deployment_descriptor) is not DeploymentDescriptorV1
            or type(self.current_inventory) is not CurrentInventoryV1
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_request_conflict",
            )
        try:
            verified_transition = verify_context_transition_v1(
                self.transition.encoded,
                expected_transition_id=self.record.context_transition_id,
                expected_inventory=self.current_inventory,
            )
        except ContextTransitionError as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_request_conflict",
                _wrapped_cause_detail_v1(exc),
            ) from exc
        except Exception as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_request_conflict",
            ) from exc
        target = self.prepared_authority_set
        if (
            verified_transition != self.transition
            or not is_prepared_authority_set_v2(target)
            or not _verified_distribution_matches_payload_v1(
                self.distribution,
            )
            or self.record.request_id != target.request_id
            or self.record.closed_build_id
            != self.distribution.identity.closed_build_id
            or self.record.provisioning_transaction_id
            != target.transaction_id
            or self.record.target_set_id != target.target_set_id
            or self.record.target_admission_context_id
            != target.target_admission_context_id
            or self.record.target_context_epoch != target.target_context_epoch
            or self.record.deployment_descriptor_id
            != self.deployment_descriptor.descriptor_id
            or self.record.context_transition_id
            != self.transition.transition_id
            or self.record.current_inventory_hash
            != current_inventory_hash_v1(self.current_inventory)
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_request_conflict",
            )


def _prepared_transition_publication_v2(
    record: OwnershipCoordinatorRecordV2, transition: object, *,
    prepared_authority_set: object, distribution: VerifiedDistribution,
    deployment_descriptor: object, current_inventory: object,
) -> PreparedTransitionPublicationV2:
    return PreparedTransitionPublicationV2(
        record, transition, prepared_authority_set, distribution,
        deployment_descriptor, current_inventory,
        _PREPARED_TRANSITION_PUBLICATION_SEAL_V2,
    )


def _transition_edge_from_graph_v2(
    graph: object, distribution: object,
) -> tuple[SuccessorClaimV1, OwnershipCoordinatorRecordV2 | None]:
    """Select only the terminal claim and its completed predecessor."""
    if (
        type(graph) is not _ObservedOwnershipCoordinatorGraphV2
        or not is_verified_distribution(distribution)
        or len(graph.pending_claims) > 1
    ):
        raise OwnershipCoordinatorError("birth_ownership_request_conflict")
    active = graph.transactions[-1] if graph.transactions else None
    if graph.pending_claims:
        claim = graph.pending_claims[0]
        if active is not None and active.claim.release_sequence >= claim.release_sequence:
            raise OwnershipCoordinatorError("birth_ownership_request_conflict")
        current = None
    elif active is not None:
        claim = active.claim
        current = active
    else:
        raise OwnershipCoordinatorError("birth_ownership_request_conflict")
    if (
        claim is not graph.claims[-1]
        or claim.closed_build_id != distribution.identity.closed_build_id
        or claim.release_sequence != distribution.release_sequence
    ):
        raise OwnershipCoordinatorError("birth_ownership_request_conflict")
    predecessor = None
    if claim.release_sequence > 1:
        predecessor_offset = -2 if current is not None else -1
        try:
            predecessor_transaction = graph.transactions[predecessor_offset]
        except IndexError as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_request_conflict",
                "predecessor_transaction_missing",
            ) from exc
        predecessor = predecessor_transaction.latest
        if (
            predecessor.state
            is not OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED
            or predecessor.release_sequence + 1 != claim.release_sequence
            or predecessor.head_id != claim.previous_head_id
        ):
            raise OwnershipCoordinatorError("birth_ownership_request_conflict")
    return claim, predecessor


def _successor_claim_for_transition_v2(
    graph: object, distribution: object, source_id: object,
) -> SuccessorClaimV1:
    """Derive the only claim that can extend one verified durable graph."""
    if (
        type(graph) is not _ObservedOwnershipCoordinatorGraphV2
        or not is_verified_distribution(distribution)
    ):
        raise OwnershipCoordinatorError("birth_ownership_request_conflict")
    source = _require_digest(source_id, "source_id")
    if graph.pending_claims:
        existing = graph.pending_claims[0]
        if (
            existing.release_sequence != distribution.release_sequence
            or existing.closed_build_id
            != distribution.identity.closed_build_id
            or existing.source_id != source
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_successor_conflict",
            )
        return existing
    if graph.claims:
        current = graph.transactions[-1]
        latest = current.latest
        existing = current.claim
        if (
            existing.release_sequence == distribution.release_sequence
            and existing.closed_build_id
            == distribution.identity.closed_build_id
        ):
            if existing.source_id != source:
                raise OwnershipCoordinatorError(
                    "birth_ownership_successor_conflict",
                )
            return existing
        if (
            latest.state
            is not OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED
            or latest.sequence != 6
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_successor_conflict",
            )
        release_sequence = latest.release_sequence + 1
        previous_head_id = latest.head_id
        previous_closed_build_id = latest.closed_build_id
        previous_cutover_id = latest.cutover_id
    else:
        if graph.transactions:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "transaction without claim",
            )
        release_sequence = 1
        previous_head_id = None
        previous_closed_build_id = None
        previous_cutover_id = None
    if (
        distribution.release_sequence != release_sequence
        or distribution.previous_closed_build_id != previous_closed_build_id
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_successor_conflict",
        )
    value: dict[str, object] = {
        "schema_version": 1,
        "previous_head_id": previous_head_id,
        "release_sequence": release_sequence,
        "request_id": _coordinator_request_id_v1(
            distribution.identity.closed_build_id,
            previous_closed_build_id,
            previous_cutover_id,
        ),
        "source_id": source,
        "closed_build_id": distribution.identity.closed_build_id,
    }
    return SuccessorClaimV1(
        claim_id=_successor_claim_id_v1(value),
        previous_head_id=previous_head_id,
        release_sequence=release_sequence,
        request_id=value["request_id"],
        source_id=source,
        closed_build_id=distribution.identity.closed_build_id,
    )


def _legacy_disposition_for_claim_v2(
    graph: _ObservedOwnershipCoordinatorGraphV2,
    claim: SuccessorClaimV1,
) -> LegacyDispositionV2 | None:
    if not graph.legacy_records:
        return None
    if graph.legacy_disposition is not None:
        if graph.legacy_disposition.successor_request_id != claim.request_id:
            raise OwnershipCoordinatorError(
                "birth_ownership_successor_conflict",
            )
        return graph.legacy_disposition
    latest = graph.legacy_records[-1]
    value: dict[str, object] = {
        "schema_version": 2,
        "legacy_journal_hash": _legacy_journal_hash_v2(
            graph.legacy_record_bytes,
        ),
        "legacy_request_id": latest.request_id,
        "legacy_state": latest.state.value,
        "successor_request_id": claim.request_id,
        "reason": _LEGACY_DISPOSITION_REASON_V2,
    }
    return LegacyDispositionV2(
        disposition_id=_legacy_disposition_id_v2(value),
        legacy_journal_hash=value["legacy_journal_hash"],
        legacy_request_id=latest.request_id,
        legacy_state=latest.state,
        successor_request_id=claim.request_id,
    )


def _reserve_transition_edge_core_v2(
    session: object, ownership_root: Path, *, root_owned: bool,
    distribution: object, source_id: object,
    require_session: Callable[[], None],
) -> SuccessorClaimV1:
    """Publish claim and any V1 disposition, then reread the exact edge."""
    require_session()
    coordinator, _created = _ensure_coordinator_child_directory_v2(
        ownership_root, COORDINATOR_DIRECTORY_BASENAME_V1,
        root_owned=root_owned,
    )
    require_session()
    graph = _resolve_ownership_coordinator_at_v2(
        coordinator, root_owned=root_owned,
    )
    claim = _successor_claim_for_transition_v2(
        graph, distribution, source_id,
    )
    if claim not in graph.claims:
        claims, _created = _ensure_coordinator_child_directory_v2(
            coordinator, SUCCESSOR_CLAIMS_DIRECTORY_BASENAME_V1,
            root_owned=root_owned,
        )
        _publish_control_no_replace_v2(
            claims,
            _successor_claim_basename_v1(
                claim.release_sequence, claim.previous_head_id,
            ),
            claim.encode(), maximum=MAX_COORDINATOR_CONTROL_BYTES_V2,
            root_owned=root_owned,
        )
        require_session()
        graph = _resolve_ownership_coordinator_at_v2(
            coordinator, root_owned=root_owned,
        )
    disposition = _legacy_disposition_for_claim_v2(graph, claim)
    if disposition is not None and graph.legacy_disposition is None:
        _publish_control_no_replace_v2(
            coordinator, LEGACY_DISPOSITION_BASENAME_V2,
            disposition.encode(), maximum=MAX_COORDINATOR_CONTROL_BYTES_V2,
            root_owned=root_owned,
        )
    require_session()
    reread = _resolve_ownership_coordinator_at_v2(
        coordinator, root_owned=root_owned,
    )
    matches = tuple(item for item in reread.claims if item == claim)
    if len(matches) != 1:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "successor claim reread",
        )
    if disposition is not None and reread.legacy_disposition != disposition:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "legacy disposition reread",
        )
    return matches[0]


def _reserve_transition_edge_locked_v2(
    session: _DeploymentLockSessionV1, *, distribution: object,
    source_id: object,
) -> SuccessorClaimV1:
    """Reserve the productive edge under the fixed root and live lock."""
    return _reserve_transition_edge_core_v2(
        session, DEFAULT_OWNERSHIP_ROOT_V1, root_owned=True,
        distribution=distribution, source_id=source_id,
        require_session=lambda: _require_deployment_lock_session_v1(session),
    )


def _reserve_transition_edge_locked_for_test_v2(
    session: _DeploymentLockSessionForTestV1, ownership_root: Path, *,
    distribution: object, source_id: object,
) -> SuccessorClaimV1:
    """Portable reservation seam with a nominally separate lock session."""
    root = Path(ownership_root)
    return _reserve_transition_edge_core_v2(
        session, root, root_owned=False,
        distribution=distribution, source_id=source_id,
        require_session=lambda: _require_test_deployment_lock_session_v1(
            session, root,
        ),
    )


def _transition_edge_locked_v2(
    session: _DeploymentLockSessionV1, distribution: object,
) -> tuple[SuccessorClaimV1, OwnershipCoordinatorRecordV2 | None]:
    """Resolve the productive transition edge under the fixed outer lock."""
    snapshot = _resolve_ownership_coordinator_locked_v2(session)
    graph = _require_locked_coordinator_graph_snapshot_v2(snapshot, session)
    return _transition_edge_from_graph_v2(graph, distribution)


def _completed_transition_from_graph_v2(
    graph: object, distribution: object,
) -> OwnershipCoordinatorRecordV2 | None:
    """Return only the final transaction for the exact current release."""
    if (
        type(graph) is not _ObservedOwnershipCoordinatorGraphV2
        or not is_verified_distribution(distribution)
    ):
        raise OwnershipCoordinatorError("birth_ownership_request_conflict")
    matches = tuple(
        transaction for transaction in graph.transactions
        if transaction.claim.closed_build_id
        == distribution.identity.closed_build_id
        and transaction.claim.release_sequence == distribution.release_sequence
    )
    if not matches:
        return None
    transaction = matches[0] if len(matches) == 1 else None
    if (
        transaction is None
        or transaction is not graph.transactions[-1]
        or transaction.claim is not graph.claims[-1]
        or graph.pending_claims
    ):
        raise OwnershipCoordinatorError("birth_ownership_request_conflict")
    latest = transaction.latest
    if latest.sequence != 6:
        return None
    if (
        latest.state is not OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED
        or latest.distribution_payload_hash != _digest(distribution.encoded)
        or latest.distribution_signature_hash != _digest(distribution.signature)
        or latest.previous_closed_build_id
        != distribution.previous_closed_build_id
        or latest.boundary_inventory_hash
        != distribution.identity.boundary_inventory_hash
        or latest.boundary_guard_version
        != distribution.identity.boundary_guard_version
    ):
        raise OwnershipCoordinatorError("birth_ownership_request_conflict")
    return latest


def _completed_transition_locked_v2(
    session: _DeploymentLockSessionV1, distribution: object,
) -> OwnershipCoordinatorRecordV2 | None:
    """Reread an exact completed transition under the productive lock."""
    snapshot = _resolve_ownership_coordinator_locked_v2(session)
    graph = _require_locked_coordinator_graph_snapshot_v2(snapshot, session)
    completed = _completed_transition_from_graph_v2(graph, distribution)
    _require_deployment_lock_session_v1(session)
    return completed


def _prepared_transition_from_graph_v2(
    graph: object, *, distribution: object, previous_context: object,
    prepared_authority_set: object, current_inventory: object,
    deployment_descriptor: object,
) -> tuple[OwnershipCoordinatorRecordV2, object]:
    """Derive PREPARED only from the terminal edge of one locked graph."""
    claim, predecessor = _transition_edge_from_graph_v2(graph, distribution)
    return _prepared_record_v2(
        claim=claim,
        distribution=distribution,
        predecessor=predecessor,
        previous_context=previous_context,
        prepared_authority_set=prepared_authority_set,
        current_inventory=current_inventory,
        deployment_descriptor=deployment_descriptor,
    )


def _require_prepared_transition_reread_v2(
    graph: _ObservedOwnershipCoordinatorGraphV2,
    record: OwnershipCoordinatorRecordV2,
) -> None:
    matches = tuple(
        transaction for transaction in graph.transactions
        if transaction.claim.request_id == record.request_id
    )
    if (
        len(matches) != 1
        or not matches[0].records
        or matches[0].records[0] != record
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "prepared reread",
        )


def _append_prepared_transition_locked_v2(
    session: _DeploymentLockSessionV1, *, distribution: object,
    previous_context: object, prepared_authority_set: object,
    current_inventory: object, deployment_descriptor: object,
) -> tuple[OwnershipCoordinatorRecordV2, object]:
    """Append and reread one productive PREPARED record under the fixed lock."""
    snapshot = _resolve_ownership_coordinator_locked_v2(session)
    graph = _require_locked_coordinator_graph_snapshot_v2(snapshot, session)
    record, transition = _prepared_transition_from_graph_v2(
        graph,
        distribution=distribution,
        previous_context=previous_context,
        prepared_authority_set=prepared_authority_set,
        current_inventory=current_inventory,
        deployment_descriptor=deployment_descriptor,
    )
    persisted = _append_ownership_transaction_locked_v2(session, record)
    reread_snapshot = _resolve_ownership_coordinator_locked_v2(session)
    reread = _require_locked_coordinator_graph_snapshot_v2(
        reread_snapshot, session,
    )
    _require_prepared_transition_reread_v2(reread, persisted)
    return persisted, transition


def _append_prepared_transition_locked_for_test_v2(
    session: _DeploymentLockSessionForTestV1, ownership_root: Path, *,
    distribution: object, previous_context: object,
    prepared_authority_set: object, current_inventory: object,
    deployment_descriptor: object,
) -> tuple[OwnershipCoordinatorRecordV2, object]:
    """Portable proof seam kept nominally separate from the productive lock."""
    snapshot = _resolve_ownership_coordinator_locked_for_test_v2(
        session, ownership_root,
    )
    record, transition = _prepared_transition_from_graph_v2(
        snapshot.observation,
        distribution=distribution,
        previous_context=previous_context,
        prepared_authority_set=prepared_authority_set,
        current_inventory=current_inventory,
        deployment_descriptor=deployment_descriptor,
    )
    persisted = _append_ownership_transaction_locked_for_test_v2(
        session, ownership_root, record,
    )
    reread = _resolve_ownership_coordinator_locked_for_test_v2(
        session, ownership_root,
    ).observation
    _require_prepared_transition_reread_v2(reread, persisted)
    return persisted, transition


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


def _receipts_complete_record_v2(
    prepared: object, *, proof: object,
    maintenance_before: bytes, maintenance_after: bytes,
) -> OwnershipCoordinatorRecordV2:
    """Carry every PREPARED binding into exact receipt completeness."""
    from executor_birth_context_transition import current_inventory_hash_v1
    from executor_birth_ownership_preflight import maintenance_evidence_hash

    if (
        type(prepared) is not OwnershipCoordinatorRecordV2
        or prepared.sequence != 0
        or prepared.state is not OwnershipCoordinatorStateV1.PREPARED
        or type(proof) is not CurrentReceiptProof
        or proof.inventory.identities != proof.identities
        or current_inventory_hash_v1(proof.inventory)
        != prepared.current_inventory_hash
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_receipt_proof_invalid",
        )
    before_hash = maintenance_evidence_hash(maintenance_before)
    after_hash = maintenance_evidence_hash(maintenance_after)
    if maintenance_before != maintenance_after or before_hash != after_hash:
        raise OwnershipCoordinatorError("birth_ownership_maintenance_changed")
    return replace(
        prepared,
        sequence=1,
        state=OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE,
        previous_record_sha256=_record_hash_v2(prepared.encode()),
        current_proof=proof,
        maintenance_before_hash=before_hash,
        maintenance_after_hash=after_hash,
        maintenance_proof=maintenance_after,
    )


def _append_receipts_complete_locked_v2(
    session: _DeploymentLockSessionV1,
    publication: object, *, proof: object,
    maintenance_before: bytes, maintenance_after: bytes,
) -> OwnershipCoordinatorRecordV2:
    """Append receipt completeness only for a sealed published transition."""
    if (
        type(publication) is not PreparedTransitionPublicationV2
        or publication._seal
        is not _PREPARED_TRANSITION_PUBLICATION_SEAL_V2
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_request_conflict",
        )
    record = _receipts_complete_record_v2(
        publication.record,
        proof=proof,
        maintenance_before=maintenance_before,
        maintenance_after=maintenance_after,
    )
    persisted = _append_ownership_transaction_locked_v2(session, record)
    snapshot = _resolve_ownership_coordinator_locked_v2(session)
    graph = _require_locked_coordinator_graph_snapshot_v2(snapshot, session)
    _require_transaction_record_reread_v2(
        graph, persisted, detail="receipt reread",
    )
    return persisted


def _require_transaction_record_reread_v2(
    graph: object, record: object, *, detail: str,
) -> None:
    """Require one exact record at its durable transaction sequence."""
    if (
        type(graph) is not _ObservedOwnershipCoordinatorGraphV2
        or type(record) is not OwnershipCoordinatorRecordV2
        or type(detail) is not str
        or not detail
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", detail or "record reread",
        )
    matches = tuple(
        transaction for transaction in graph.transactions
        if transaction.claim.request_id == record.request_id
    )
    if (
        len(matches) != 1
        or len(matches[0].records) <= record.sequence
        or matches[0].records[record.sequence] != record
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", detail,
        )


_CERTIFICATE_READY_MATERIAL_SEAL_V2 = object()


@dataclass(frozen=True, slots=True)
class _CertificateReadyMaterialV2:
    """Exact authenticated bytes bound by one CERTIFICATE_READY record."""

    record: OwnershipCoordinatorRecordV2
    payload: bytes
    signature: bytes
    certificate: OwnershipCutoverCertificate
    _seal: object

    def __post_init__(self) -> None:
        record = self.record
        certificate = self.certificate
        proof = (
            record.current_proof
            if type(record) is OwnershipCoordinatorRecordV2 else None
        )
        if (
            self._seal is not _CERTIFICATE_READY_MATERIAL_SEAL_V2
            or type(record) is not OwnershipCoordinatorRecordV2
            or record.state is not OwnershipCoordinatorStateV1.CERTIFICATE_READY
            or type(self.payload) is not bytes
            or type(self.signature) is not bytes
            or type(certificate) is not OwnershipCutoverCertificate
            or record.certificate_payload_hash != _digest(self.payload)
            or record.certificate_signature_hash != _digest(self.signature)
            or record.cutover_id != certificate.cutover_id
            or record.catalog_id != certificate.catalog_id
            or record.request_id != certificate.request_id
            or record.previous_cutover_id != certificate.previous_cutover_id
            or record.closed_build_id != certificate.closed_build_id
            or record.boundary_inventory_hash
            != certificate.boundary_inventory_hash
            or record.boundary_guard_version
            != certificate.boundary_guard_version
            or record.maintenance_after_hash
            != certificate.maintenance_evidence_hash
            or record.context_transition_id
            != certificate.context_transition_id
            or record.dominant_startup_receipt
            != certificate.dominant_startup_receipt
            or proof is None
            or certificate.as_proof() != proof
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required",
                "certificate ready material",
            )


def _certificate_ready_material_v2(
    complete: object, *, authorities: object,
    prerequisite: object, observe_maintenance: object,
    crossing_receipt: object,
) -> _CertificateReadyMaterialV2:
    """Issue and bind exact certificate bytes after a sealed dominant crossing."""
    if (
        type(complete) is not OwnershipCoordinatorRecordV2
        or complete.sequence != 1
        or complete.state is not OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE
        or not is_root_ownership_authorities_v1(authorities)
        or not isinstance(prerequisite, _StartupPrerequisiteV1)
        or prerequisite._seal is not _PREREQUISITE_SEAL
        or not callable(observe_maintenance)
        or not is_dominant_startup_receipt_v1(crossing_receipt)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_prerequisite_untrusted",
        )
    proof = complete.current_proof
    assert proof is not None and complete.maintenance_after_hash is not None
    if observe_maintenance() != complete.maintenance_proof:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "maintenance drift",
        )
    receipt_digest = crossing_receipt.dominant_startup_receipt
    payload, signature = issue_ownership_cutover_certificate(
        proof=proof,
        previous_cutover_id=complete.previous_cutover_id,
        request_id=complete.request_id,
        signing_key_id=_single_cutover_key(authorities),
        maintenance_evidence_hash=complete.maintenance_after_hash,
        boundary_inventory_hash=complete.boundary_inventory_hash,
        boundary_guard_version=complete.boundary_guard_version,
        closed_build_id=complete.closed_build_id,
        context_transition_id=complete.context_transition_id,
        dominant_startup_receipt=receipt_digest,
        private_key=authorities.cutover_private,
    )
    certificate = verify_ownership_cutover_certificate(
        payload,
        signature,
        registry=authorities.public.cutover,
        expected_proof=proof,
        expected_previous_cutover_id=complete.previous_cutover_id,
        expected_context_transition_id=complete.context_transition_id,
        expected_dominant_startup_receipt=receipt_digest,
    )
    if (
        certificate.request_id != complete.request_id
        or certificate.closed_build_id != complete.closed_build_id
        or certificate.maintenance_evidence_hash
        != complete.maintenance_after_hash
        or certificate.boundary_inventory_hash
        != complete.boundary_inventory_hash
        or certificate.boundary_guard_version
        != complete.boundary_guard_version
        or crossing_receipt.bindings.request_id != complete.request_id
        or crossing_receipt.bindings.context_transition_id
        != complete.context_transition_id
        or crossing_receipt.bindings.catalog_id != certificate.catalog_id
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required",
            "certificate ready binding",
        )
    record = replace(
        complete,
        sequence=2,
        state=OwnershipCoordinatorStateV1.CERTIFICATE_READY,
        previous_record_sha256=_record_hash_v2(complete.encode()),
        startup_prerequisite_id=prerequisite.prerequisite_id,
        startup_prerequisite_digest=prerequisite.evidence_digest,
        cutover_id=certificate.cutover_id,
        catalog_id=certificate.catalog_id,
        certificate_payload_hash=_digest(payload),
        certificate_signature_hash=_digest(signature),
        dominant_startup_receipt=receipt_digest,
    )
    return _CertificateReadyMaterialV2(
        record, payload, signature, certificate,
        _CERTIFICATE_READY_MATERIAL_SEAL_V2,
    )


def _certificate_published_record_v2(
    ready: object,
) -> OwnershipCoordinatorRecordV2:
    """Carry exact ready bindings across the certificate publication boundary."""
    if (
        type(ready) is not OwnershipCoordinatorRecordV2
        or ready.sequence != 2
        or ready.state is not OwnershipCoordinatorStateV1.CERTIFICATE_READY
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "certificate ready record",
        )
    return replace(
        ready,
        sequence=3,
        state=OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED,
        previous_record_sha256=_record_hash_v2(ready.encode()),
    )


def _build_verified_record_v2(
    published: object, distribution: object,
) -> OwnershipCoordinatorRecordV2:
    """Bind one fully verified live tree after certificate publication."""
    if (
        type(published) is not OwnershipCoordinatorRecordV2
        or published.sequence != 3
        or published.state
        is not OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED
        or type(distribution) is not VerifiedDistribution
        or not _verified_distribution_matches_payload_v1(distribution)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "build verification",
        )
    if (
        distribution.identity.closed_build_id != published.closed_build_id
        or distribution.previous_closed_build_id
        != published.previous_closed_build_id
        or distribution.release_sequence != published.release_sequence
        or _digest(distribution.encoded)
        != published.distribution_payload_hash
        or _digest(distribution.signature)
        != published.distribution_signature_hash
        or distribution.identity.boundary_inventory_hash
        != published.boundary_inventory_hash
        or distribution.identity.boundary_guard_version
        != published.boundary_guard_version
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "build binding",
        )
    try:
        tree_hash = installed_tree_hash_v1(distribution.files)
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "installed tree",
        ) from exc
    return replace(
        published,
        sequence=4,
        state=OwnershipCoordinatorStateV1.BUILD_VERIFIED,
        previous_record_sha256=_record_hash_v2(published.encode()),
        installed_tree_hash=tree_hash,
    )


_HEAD_REQUIRED_MATERIAL_SEAL_V2 = object()


@dataclass(frozen=True, slots=True)
class _HeadRequiredMaterialV2:
    record: OwnershipCoordinatorRecordV2
    encoded: bytes
    signature: bytes
    head: object
    frame: bytes
    _seal: object

    def __post_init__(self) -> None:
        from executor_birth_ownership_chain import OwnershipHead

        if (
            self._seal is not _HEAD_REQUIRED_MATERIAL_SEAL_V2
            or type(self.record) is not OwnershipCoordinatorRecordV2
            or self.record.sequence != 5
            or self.record.state is not OwnershipCoordinatorStateV1.HEAD_REQUIRED
            or type(self.encoded) is not bytes
            or type(self.signature) is not bytes
            or type(self.head) is not OwnershipHead
            or type(self.frame) is not bytes
            or self.record.head_id != self.head.head_id
            or self.record.head_payload_hash != _framed_digest_v2(
                _HEAD_PAYLOAD_HASH_DOMAIN_V2, self.encoded,
            )
            or self.record.head_signature_hash != _framed_digest_v2(
                _HEAD_SIGNATURE_HASH_DOMAIN_V2, self.signature,
            )
            or self.record.required_head_frame_hash != _framed_digest_v2(
                _REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2, self.frame,
            )
            or self.record.verified_chain_head_id != self.head.head_id
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "head material",
            )


def _single_head_key(authorities: RootOwnershipAuthoritiesV1) -> str:
    if not is_root_ownership_authorities_v1(authorities):
        raise OwnershipCoordinatorError("birth_ownership_authority_untrusted")
    keys = tuple(authorities.public.head.keys)
    if len(keys) != 1:
        raise OwnershipCoordinatorError("birth_ownership_authority_untrusted")
    return keys[0]


def _head_required_material_v2(
    build_verified: object, *, authorities: object,
) -> _HeadRequiredMaterialV2:
    """Issue deterministic head bytes and bind the future atomic pointer."""
    from executor_birth_ownership_chain import (
        encode_required_head, issue_ownership_head, verify_ownership_head,
    )

    if (
        type(build_verified) is not OwnershipCoordinatorRecordV2
        or build_verified.sequence != 4
        or build_verified.state is not OwnershipCoordinatorStateV1.BUILD_VERIFIED
        or not is_root_ownership_authorities_v1(authorities)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "build verified record",
        )
    encoded, signature = issue_ownership_head(
        release_sequence=build_verified.release_sequence,
        cutover_id=build_verified.cutover_id,
        closed_build_id=build_verified.closed_build_id,
        previous_head_id=build_verified.previous_head_id,
        signing_key_id=_single_head_key(authorities),
        private_key=authorities.head_private,
    )
    head = verify_ownership_head(
        encoded, signature, registry=authorities.public.head,
    )
    frame = encode_required_head(head)
    record = replace(
        build_verified,
        sequence=5,
        state=OwnershipCoordinatorStateV1.HEAD_REQUIRED,
        previous_record_sha256=_record_hash_v2(build_verified.encode()),
        head_id=head.head_id,
        head_payload_hash=_framed_digest_v2(
            _HEAD_PAYLOAD_HASH_DOMAIN_V2, encoded,
        ),
        head_signature_hash=_framed_digest_v2(
            _HEAD_SIGNATURE_HASH_DOMAIN_V2, signature,
        ),
        required_head_frame_hash=_framed_digest_v2(
            _REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2, frame,
        ),
        verified_chain_head_id=head.head_id,
    )
    return _HeadRequiredMaterialV2(
        record, encoded, signature, head, frame,
        _HEAD_REQUIRED_MATERIAL_SEAL_V2,
    )


def _preflight_verified_record_v2(
    head_required: object, encoded_attestation: object,
) -> OwnershipCoordinatorRecordV2:
    """Bind the exact, self-authenticating operational preflight document."""
    from executor_birth_admin_preflight import (
        _DecodedPreflightAttestationV1,
        _decode_preflight_attestation_record_v1,
    )

    if (
        type(head_required) is not OwnershipCoordinatorRecordV2
        or head_required.sequence != 5
        or head_required.state is not OwnershipCoordinatorStateV1.HEAD_REQUIRED
        or type(encoded_attestation) is not bytes
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "preflight input",
    )
    try:
        attestation, attestation_hash = (
            _decode_preflight_attestation_record_v1(encoded_attestation)
        )
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "preflight attestation",
        ) from exc
    if (
        type(attestation) is not _DecodedPreflightAttestationV1
        or attestation.request_id != head_required.request_id
        or attestation.closed_build_id != head_required.closed_build_id
        or attestation.release_sequence != head_required.release_sequence
        or attestation.head_id != head_required.head_id
        or attestation.required_head_frame_hash
        != head_required.required_head_frame_hash
        or attestation.deployment_descriptor_id
        != head_required.deployment_descriptor_id
        or attestation.service_coverage_hash
        != head_required.service_coverage_hash
        or attestation.administrative_bundle_hash
        != head_required.administrative_bundle_hash
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "preflight binding",
        )
    return replace(
        head_required,
        sequence=6,
        state=OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED,
        previous_record_sha256=_record_hash_v2(head_required.encode()),
        preflight_attestation_hash=attestation_hash,
    )


def _path_present_v2(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "certificate inventory",
        ) from exc
    return True


def _certificate_target_paths_v2(
    material: _CertificateReadyMaterialV2,
    certificate_directory: Path, chain_store: object | None,
) -> tuple[Path, Path]:
    if material.record.release_sequence == 1:
        return (
            certificate_directory / PAYLOAD_BASENAME,
            certificate_directory / SIGNATURE_BASENAME,
        )
    from executor_birth_ownership_chain import OwnershipChainStore

    if not isinstance(chain_store, OwnershipChainStore):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "ownership chain store",
        )
    stem = material.certificate.cutover_id.removeprefix("sha256:")
    return (
        chain_store.root / "cutovers-v1" / f"{stem}.json",
        chain_store.root / "cutovers-v1" / f"{stem}.sig",
    )


def _require_unpublished_certificate_target_v2(
    material: _CertificateReadyMaterialV2,
    certificate_directory: Path, chain_store: object | None,
) -> None:
    if any(_path_present_v2(path) for path in _certificate_target_paths_v2(
        material, certificate_directory, chain_store,
    )):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required",
            "certificate exists before ready",
        )


def _publish_certificate_material_v2(
    material: object, *, certificate_directory: Path,
    authorities: object, chain_store: object | None,
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorRecordV2:
    """Publish and reread the exact bytes already committed by READY."""
    if (
        type(material) is not _CertificateReadyMaterialV2
        or material._seal is not _CERTIFICATE_READY_MATERIAL_SEAL_V2
        or not is_root_ownership_authorities_v1(authorities)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_prerequisite_untrusted",
        )
    record = material.record
    proof = record.current_proof
    assert proof is not None
    if record.release_sequence == 1:
        observed = install_ownership_cutover_certificate(
            certificate_directory,
            material.payload,
            material.signature,
            registry=authorities.public.cutover,
            expected_proof=proof,
            expected_context_transition_id=record.context_transition_id,
            expected_dominant_startup_receipt=(
                record.dominant_startup_receipt
            ),
            _crash_seam=_crash_seam,
        )
        reread = read_ownership_cutover_certificate(
            certificate_directory,
            registry=authorities.public.cutover,
            expected_proof=proof,
            expected_context_transition_id=record.context_transition_id,
            expected_dominant_startup_receipt=(
                record.dominant_startup_receipt
            ),
        )
    else:
        from executor_birth_ownership_chain import OwnershipChainStore

        if not isinstance(chain_store, OwnershipChainStore):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "ownership chain store",
            )
        observed = chain_store.append_cutover(
            material.payload, material.signature, _crash_seam=_crash_seam,
        )
        stem = material.certificate.cutover_id.removeprefix("sha256:")
        encoded, signature = chain_store._read_pair(
            chain_store.root / "cutovers-v1", stem,
            maximum=MAX_PAYLOAD_BYTES,
        )
        if encoded != material.payload or signature != material.signature:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "certificate reread",
            )
        reread = verify_ownership_cutover_certificate(
            encoded,
            signature,
            registry=authorities.public.cutover,
            expected_proof=proof,
            expected_previous_cutover_id=record.previous_cutover_id,
            expected_context_transition_id=record.context_transition_id,
            expected_dominant_startup_receipt=(
                record.dominant_startup_receipt
            ),
        )
    if observed != material.certificate or reread != material.certificate:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "certificate reread",
        )
    return _certificate_published_record_v2(record)


def _cross_certificate_boundary_core_v2(
    *, material: object, authorities: object,
    certificate_directory: Path, chain_store: object | None,
    append_record: object, observe_graph: object,
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorRecordV2:
    """Share exact ordering; wrappers retain nominal lock authority."""
    if (
        type(material) is not _CertificateReadyMaterialV2
        or material._seal is not _CERTIFICATE_READY_MATERIAL_SEAL_V2
        or not is_root_ownership_authorities_v1(authorities)
        or not callable(append_record)
        or not callable(observe_graph)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_prerequisite_untrusted",
        )
    before = observe_graph()
    if type(before) is not _ObservedOwnershipCoordinatorGraphV2:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "coordinator graph",
        )
    matches = tuple(
        item for item in before.transactions
        if item.claim.request_id == material.record.request_id
    )
    if len(matches) != 1 or len(matches[0].records) < 2:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "receipt reread",
        )
    complete = matches[0].records[1]
    if material.record.previous_record_sha256 != _record_hash_v2(
        complete.encode(),
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "certificate predecessor",
        )
    if matches[0].latest.sequence == 1:
        _require_unpublished_certificate_target_v2(
            material, certificate_directory, chain_store,
        )
    ready = append_record(material.record)
    reread_graph = observe_graph()
    _require_transaction_record_reread_v2(
        reread_graph, ready, detail="certificate ready reread",
    )
    if _crash_seam is not None:
        _crash_seam("certificate_ready")
    published_record = _publish_certificate_material_v2(
        material,
        certificate_directory=certificate_directory,
        authorities=authorities,
        chain_store=chain_store,
        _crash_seam=_crash_seam,
    )
    published = append_record(published_record)
    final_graph = observe_graph()
    _require_transaction_record_reread_v2(
        final_graph, published, detail="certificate published reread",
    )
    return published


def _cross_certificate_boundary_locked_v2(
    session: _DeploymentLockSessionV1, material: object, *,
    authorities: object,
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorRecordV2:
    """Productive READY-to-PUBLISHED crossing under the fixed outer lock."""
    from executor_birth_ownership_chain import OwnershipChainStore

    if type(material) is not _CertificateReadyMaterialV2:
        raise OwnershipCoordinatorError(
            "birth_ownership_prerequisite_untrusted",
        )
    chain_store = (
        None if material.record.release_sequence == 1
        else OwnershipChainStore()
    )

    def observe_certificate_graph():
        snapshot = _resolve_ownership_coordinator_locked_v2(session)
        return _require_locked_coordinator_graph_snapshot_v2(
            snapshot, session,
        )

    return _cross_certificate_boundary_core_v2(
        material=material,
        authorities=authorities,
        certificate_directory=DEFAULT_OWNERSHIP_ROOT_V1,
        chain_store=chain_store,
        append_record=lambda record: _append_ownership_transaction_locked_v2(
            session, record,
        ),
        observe_graph=observe_certificate_graph,
        _crash_seam=_crash_seam,
    )


def _cross_certificate_boundary_locked_for_test_v2(
    session: _DeploymentLockSessionForTestV1, ownership_root: Path,
    material: object, *, authorities: object, chain_store: object | None = None,
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorRecordV2:
    """Portable proof seam; it never accepts the productive lock session."""
    ownership_root = Path(ownership_root)
    _require_test_deployment_lock_session_v1(session, ownership_root)
    return _cross_certificate_boundary_core_v2(
        material=material,
        authorities=authorities,
        certificate_directory=ownership_root,
        chain_store=chain_store,
        append_record=lambda record: (
            _append_ownership_transaction_locked_for_test_v2(
                session, ownership_root, record,
            )
        ),
        observe_graph=lambda: (
            _resolve_ownership_coordinator_locked_for_test_v2(
                session, ownership_root,
            ).observation
        ),
        _crash_seam=_crash_seam,
    )


def _certificate_material_for_head_v2(
    record: OwnershipCoordinatorRecordV2, *, certificate_directory: Path,
    chain_store: object, authorities: RootOwnershipAuthoritiesV1,
) -> tuple[bytes, bytes, OwnershipCutoverCertificate]:
    """Reread the exact published certificate before archiving its head."""
    from executor_birth_ownership_chain import OwnershipChainStore

    if (
        type(record) is not OwnershipCoordinatorRecordV2
        or record.sequence != 4
        or record.state is not OwnershipCoordinatorStateV1.BUILD_VERIFIED
        or not isinstance(chain_store, OwnershipChainStore)
        or not is_root_ownership_authorities_v1(authorities)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "head certificate input",
        )
    try:
        if record.release_sequence == 1:
            encoded = _safe_read(
                Path(certificate_directory) / PAYLOAD_BASENAME,
                MAX_PAYLOAD_BYTES,
            )
            signature = _safe_read(
                Path(certificate_directory) / SIGNATURE_BASENAME, 64,
            )
        else:
            encoded, signature = chain_store._read_pair(
                chain_store.root / "cutovers-v1",
                record.cutover_id.removeprefix("sha256:"),
                maximum=MAX_PAYLOAD_BYTES,
            )
        proof = record.current_proof
        assert proof is not None
        certificate = verify_ownership_cutover_certificate(
            encoded,
            signature,
            registry=authorities.public.cutover,
            expected_proof=proof,
            expected_previous_cutover_id=record.previous_cutover_id,
            expected_context_transition_id=record.context_transition_id,
            expected_dominant_startup_receipt=(
                record.dominant_startup_receipt
            ),
        )
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "head certificate reread",
        ) from exc
    if (
        _digest(encoded) != record.certificate_payload_hash
        or _digest(signature) != record.certificate_signature_hash
        or certificate.cutover_id != record.cutover_id
        or certificate.catalog_id != record.catalog_id
        or certificate.request_id != record.request_id
        or certificate.closed_build_id != record.closed_build_id
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "head certificate binding",
        )
    return encoded, signature, certificate


def _cross_head_boundary_core_v2(
    *, published: object, distribution: object, authorities: object,
    certificate_directory: Path, chain_store: object,
    append_record: object, observe_graph: object,
    verify_installation: object, verify_required_chain: object,
    require_sessions: object,
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorRecordV2:
    """Advance sequence 3 to 5 around one recoverable required-head CAS."""
    from executor_birth_ownership_chain import (
        OwnershipChainStore, OwnershipHead, VerifiedOwnershipChain,
    )

    if (
        type(published) is not OwnershipCoordinatorRecordV2
        or published.sequence != 3
        or published.state
        is not OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED
        or not _verified_distribution_matches_payload_v1(distribution)
        or not is_root_ownership_authorities_v1(authorities)
        or not isinstance(chain_store, OwnershipChainStore)
        or not callable(append_record)
        or not callable(observe_graph)
        or not callable(verify_installation)
        or not callable(verify_required_chain)
        or not callable(require_sessions)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "head crossing input",
        )

    def require() -> None:
        require_sessions()

    def head_transaction():
        require()
        graph = observe_graph()
        if type(graph) is not _ObservedOwnershipCoordinatorGraphV2:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "head graph",
            )
        matches = tuple(
            item for item in graph.transactions
            if item.claim.request_id == published.request_id
        )
        if (
            len(matches) != 1 or len(matches[0].records) < 4
            or matches[0].records[3] != published
            or matches[0].latest.sequence not in {3, 4, 5}
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "head predecessor",
            )
        return matches[0]

    transaction = head_transaction()
    require()
    observed_distribution = verify_installation()
    require()
    if (
        type(observed_distribution) is not VerifiedDistribution
        or observed_distribution != distribution
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "installed distribution",
        )
    build_record = _build_verified_record_v2(
        published, observed_distribution,
    )
    if transaction.latest.sequence == 3:
        build_record = append_record(build_record)
        _require_transaction_record_reread_v2(
            observe_graph(), build_record, detail="build verified reread",
        )
        require()
        if _crash_seam is not None:
            _crash_seam("build_verified")
    elif transaction.records[4] != build_record:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "build verified binding",
        )

    encoded_certificate, certificate_signature, certificate = (
        _certificate_material_for_head_v2(
            build_record,
            certificate_directory=Path(certificate_directory),
            chain_store=chain_store,
            authorities=authorities,
        )
    )
    require()
    archived_certificate = chain_store.append_cutover(
        encoded_certificate,
        certificate_signature,
        _crash_seam=(
            (lambda point: _crash_seam("cutover_" + point))
            if _crash_seam is not None else None
        ),
    )
    require()
    if archived_certificate != certificate:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "cutover archive reread",
        )
    if _crash_seam is not None:
        _crash_seam("cutover_archived")

    chain_store.append_authenticated_build(
        observed_distribution,
        _crash_seam=(
            (lambda point: _crash_seam("build_" + point))
            if _crash_seam is not None else None
        ),
    )
    require()
    if _crash_seam is not None:
        _crash_seam("build_archived")

    material = _head_required_material_v2(
        build_record, authorities=authorities,
    )
    archived_head = chain_store.append_head(
        material.encoded,
        material.signature,
        _crash_seam=(
            (lambda point: _crash_seam("head_" + point))
            if _crash_seam is not None else None
        ),
    )
    require()
    if archived_head != material.head:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "head archive reread",
        )
    if _crash_seam is not None:
        _crash_seam("head_archived")

    required = chain_store.update_required_head(
        material.encoded,
        material.signature,
        expected_head_id=build_record.previous_head_id,
        _crash_seam=(
            (lambda point: _crash_seam("required_" + point))
            if _crash_seam is not None else None
        ),
    )
    require()
    if required != material.head:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "required head reread",
        )
    verified_chain = verify_required_chain(certificate)
    require()
    if (
        type(verified_chain) is not VerifiedOwnershipChain
        or type(verified_chain.required_head) is not OwnershipHead
        or verified_chain.required_head != material.head
        or (
            verified_chain.required_distribution is not None
            and verified_chain.required_distribution != observed_distribution
        )
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "required chain reread",
        )
    if _crash_seam is not None:
        _crash_seam("required_chain_verified")

    transaction = head_transaction()
    if transaction.latest.sequence == 4:
        persisted = append_record(material.record)
        _require_transaction_record_reread_v2(
            observe_graph(), persisted, detail="head required reread",
        )
        require()
        if _crash_seam is not None:
            _crash_seam("head_required")
    elif transaction.latest.sequence == 5:
        persisted = transaction.latest
        if persisted != material.record:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "head required binding",
            )
    else:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "head journal order",
        )
    final = head_transaction().latest
    if final != persisted:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "head final reread",
        )
    return final


def _cross_head_boundary_locked_v2(
    sessions: tuple[object, ...], published: object, distribution: object, *,
    authorities: object,
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorRecordV2:
    """Product crossing with all three live sessions continuously held."""
    from executor_birth_dominant_startup import _require_product_sessions_v1
    from executor_birth_ownership_chain import OwnershipChainStore

    held = _require_product_sessions_v1(sessions)
    store = OwnershipChainStore()

    def observe_head_graph():
        snapshot = _resolve_ownership_coordinator_locked_v2(held[0])
        return _require_locked_coordinator_graph_snapshot_v2(
            snapshot, held[0],
        )

    return _cross_head_boundary_core_v2(
        published=published,
        distribution=distribution,
        authorities=authorities,
        certificate_directory=DEFAULT_OWNERSHIP_ROOT_V1,
        chain_store=store,
        append_record=lambda record: _append_ownership_transaction_locked_v2(
            held[0], record,
        ),
        observe_graph=observe_head_graph,
        verify_installation=lambda: verify_current_installation_distribution_v1(
            distribution.encoded, distribution.signature,
        ),
        verify_required_chain=lambda _certificate: (
            store.read_required_chain_cold_v1()
        ),
        require_sessions=lambda: _require_product_sessions_v1(held),
        _crash_seam=_crash_seam,
    )


def _cross_head_boundary_locked_for_test_v2(
    deployment_session: object, startup_session: object, *,
    ownership_root: Path, gate_path: Path, published: object,
    distribution: object, authorities: object, chain_store: object,
    builds: Mapping[str, VerifiedDistribution],
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorRecordV2:
    """Portable nominal seam; productive sessions cannot enter it."""
    from executor_birth_ownership_chain import _OwnershipChainStoreForTest
    from executor_birth_startup_gate import (
        _require_exclusive_startup_gate_session_for_test_v1,
    )

    ownership_root = Path(ownership_root)
    gate_path = Path(gate_path)
    if (
        type(chain_store) is not _OwnershipChainStoreForTest
        or type(builds) is not dict
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "test head crossing",
        )

    def require() -> None:
        _require_test_deployment_lock_session_v1(
            deployment_session, ownership_root,
        )
        _require_exclusive_startup_gate_session_for_test_v1(
            startup_session, gate_path,
        )

    require()
    return _cross_head_boundary_core_v2(
        published=published,
        distribution=distribution,
        authorities=authorities,
        certificate_directory=ownership_root,
        chain_store=chain_store,
        append_record=lambda record: (
            _append_ownership_transaction_locked_for_test_v2(
                deployment_session, ownership_root, record,
            )
        ),
        observe_graph=lambda: (
            _resolve_ownership_coordinator_locked_for_test_v2(
                deployment_session, ownership_root,
            ).observation
        ),
        verify_installation=lambda: distribution,
        verify_required_chain=lambda certificate: chain_store.read_required_chain(
            anchor=certificate, builds=builds,
        ),
        require_sessions=require,
        _crash_seam=_crash_seam,
    )


def _cross_preflight_boundary_core_v2(
    *, head_required: object, append_record: object, observe_graph: object,
    publish_attestation: object, reread_attestation: object,
    require_sessions: object,
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorRecordV2:
    """Advance sequence 5 to 6 only around one exact durable attestation."""
    if (
        type(head_required) is not OwnershipCoordinatorRecordV2
        or head_required.sequence != 5
        or head_required.state is not OwnershipCoordinatorStateV1.HEAD_REQUIRED
        or not callable(append_record)
        or not callable(observe_graph)
        or not callable(publish_attestation)
        or not callable(reread_attestation)
        or not callable(require_sessions)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "preflight crossing input",
        )

    def require() -> None:
        require_sessions()

    def preflight_transaction():
        require()
        graph = observe_graph()
        if type(graph) is not _ObservedOwnershipCoordinatorGraphV2:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "preflight graph",
            )
        matches = tuple(
            item for item in graph.transactions
            if item.claim.request_id == head_required.request_id
        )
        if (
            len(matches) != 1 or len(matches[0].records) < 6
            or matches[0].records[5] != head_required
            or matches[0].latest.sequence not in {5, 6}
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "preflight predecessor",
            )
        return matches[0]

    transaction = preflight_transaction()
    require()
    try:
        encoded = publish_attestation()
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "preflight publication",
        ) from exc
    require()
    verified_record = _preflight_verified_record_v2(
        head_required, encoded,
    )
    if _crash_seam is not None:
        _crash_seam("preflight_attestation_published")

    transaction = preflight_transaction()
    if transaction.latest.sequence == 5:
        persisted = append_record(verified_record)
        _require_transaction_record_reread_v2(
            observe_graph(), persisted, detail="preflight verified reread",
        )
        require()
        if _crash_seam is not None:
            _crash_seam("preflight_verified")
    elif transaction.latest.sequence == 6:
        persisted = transaction.latest
        if persisted != verified_record:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "preflight record binding",
            )
    else:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "preflight journal order",
        )

    require()
    try:
        observed = reread_attestation()
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "preflight final reread",
        ) from exc
    require()
    if (
        observed != encoded
        or _preflight_verified_record_v2(head_required, observed) != persisted
        or preflight_transaction().latest != persisted
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "preflight final binding",
        )
    return persisted


def _cross_preflight_boundary_locked_v2(
    sessions: tuple[object, ...], head_required: object, *,
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorRecordV2:
    """Product crossing with fixed-root preflight and all sessions retained."""
    from executor_birth_admin_preflight import (
        _attest_operational_preflight_v1,
        _publish_preflight_attestation_v1,
        _read_preflight_attestation_v1,
    )
    from executor_birth_dominant_startup import _require_product_sessions_v1

    held = _require_product_sessions_v1(sessions)

    def observe_preflight_graph():
        snapshot = _resolve_ownership_coordinator_locked_v2(held[0])
        return _require_locked_coordinator_graph_snapshot_v2(
            snapshot, held[0],
        )

    return _cross_preflight_boundary_core_v2(
        head_required=head_required,
        append_record=lambda record: _append_ownership_transaction_locked_v2(
            held[0], record,
        ),
        observe_graph=observe_preflight_graph,
        publish_attestation=lambda: _publish_preflight_attestation_v1(
            _attest_operational_preflight_v1(),
        ),
        reread_attestation=lambda: _read_preflight_attestation_v1(
            head_required.request_id,
        ),
        require_sessions=lambda: _require_product_sessions_v1(held),
        _crash_seam=_crash_seam,
    )


def _cross_preflight_boundary_locked_for_test_v2(
    deployment_session: object, startup_session: object, *,
    ownership_root: Path, gate_path: Path, attestation_root: Path,
    head_required: object, encoded_attestation: bytes,
    _crash_seam: Callable[[str], None] | None = None,
) -> OwnershipCoordinatorRecordV2:
    """Portable nominal seam; productive sessions and roots cannot enter it."""
    from executor_birth_admin_preflight import (
        _decode_preflight_attestation_v1,
        _publish_preflight_attestation_core_v1,
        _read_preflight_attestation_for_test_v1,
    )
    from executor_birth_startup_gate import (
        _require_exclusive_startup_gate_session_for_test_v1,
    )

    ownership_root = Path(ownership_root)
    gate_path = Path(gate_path)
    attestation_root = Path(attestation_root)
    if (
        type(encoded_attestation) is not bytes
        or not attestation_root.is_absolute()
        or attestation_root.parent != ownership_root
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "test preflight crossing",
        )

    def require() -> None:
        _require_test_deployment_lock_session_v1(
            deployment_session, ownership_root,
        )
        _require_exclusive_startup_gate_session_for_test_v1(
            startup_session, gate_path,
        )

    def publish() -> bytes:
        decoded = _decode_preflight_attestation_v1(encoded_attestation)
        _publish_preflight_attestation_core_v1(
            encoded_attestation, decoded.request_id,
            root=attestation_root, uid=os.getuid(), gid=os.getgid(),
            chain_stop=ownership_root.parent,
        )
        return _read_preflight_attestation_for_test_v1(
            decoded.request_id, attestation_root,
        )

    require()
    return _cross_preflight_boundary_core_v2(
        head_required=head_required,
        append_record=lambda record: (
            _append_ownership_transaction_locked_for_test_v2(
                deployment_session, ownership_root, record,
            )
        ),
        observe_graph=lambda: (
            _resolve_ownership_coordinator_locked_for_test_v2(
                deployment_session, ownership_root,
            ).observation
        ),
        publish_attestation=publish,
        reread_attestation=lambda: _read_preflight_attestation_for_test_v1(
            head_required.request_id, attestation_root,
        ),
        require_sessions=require,
        _crash_seam=_crash_seam,
    )


def _publish_context_transition_locked_v2(
    session: _DeploymentLockSessionV1,
    publication: object,
    complete: object,
):
    """Publish the transition only after exact receipt completeness reread."""
    if (
        type(publication) is not PreparedTransitionPublicationV2
        or publication._seal
        is not _PREPARED_TRANSITION_PUBLICATION_SEAL_V2
        or type(complete) is not OwnershipCoordinatorRecordV2
        or complete.state is not OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE
        or complete.sequence != 1
        or complete.request_id != publication.record.request_id
        or complete.context_transition_id
        != publication.transition.transition_id
        or complete.current_proof is None
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_receipt_proof_invalid",
        )
    _require_deployment_lock_session_v1(session)
    snapshot = _resolve_ownership_coordinator_locked_v2(session)
    graph = _require_locked_coordinator_graph_snapshot_v2(snapshot, session)
    matches = tuple(
        transaction for transaction in graph.transactions
        if transaction.claim.request_id == complete.request_id
    )
    if (
        len(matches) != 1
        or len(matches[0].records) < 2
        or matches[0].records[1] != complete
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "receipt reread",
        )
    from executor_birth_ownership_chain import (
        OwnershipChainError, OwnershipChainStore,
    )

    try:
        observed = OwnershipChainStore().append_context_transition(
            publication.transition.encoded,
            expected_proof=complete.current_proof,
        )
    except OwnershipChainError as exc:
        raise OwnershipCoordinatorError(
            "birth_context_transition_recovery_required",
            _wrapped_cause_detail_v1(exc),
        ) from exc
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_context_transition_recovery_required",
        ) from exc
    _require_deployment_lock_session_v1(session)
    if observed != publication.transition:
        raise OwnershipCoordinatorError(
            "birth_context_transition_recovery_required",
        )
    return observed


def _observe_dominant_identity_core_v2(
    graph: object, complete: object, read_transition: object,
) -> tuple[str, str, str]:
    """Reread the exact request, predecessor anchor and context transition."""
    from executor_birth_context_transition import ContextTransitionV1
    from executor_birth_ownership_chain import OwnershipChainError

    if (
        type(graph) is not _ObservedOwnershipCoordinatorGraphV2
        or type(complete) is not OwnershipCoordinatorRecordV2
        or complete.sequence != 1
        or complete.state is not OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE
        or not callable(read_transition)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "dominant identity input",
        )
    matches = tuple(
        item for item in graph.transactions
        if item.claim.request_id == complete.request_id
    )
    if (
        len(matches) != 1
        or len(matches[0].records) < 2
        or matches[0].records[1] != complete
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "dominant identity record",
        )
    proof = complete.current_proof
    if proof is None:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "dominant identity proof",
        )
    try:
        transition = read_transition(complete.context_transition_id, proof)
    except OwnershipChainError as exc:
        raise OwnershipCoordinatorError(
            "birth_context_transition_recovery_required",
            _wrapped_cause_detail_v1(exc),
        ) from exc
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_context_transition_recovery_required",
        ) from exc
    if (
        type(transition) is not ContextTransitionV1
        or transition.request_id != complete.request_id
        or transition.closed_build_id != complete.closed_build_id
        or transition.previous_cutover_id != complete.previous_cutover_id
        or transition.previous_set_id != complete.previous_set_id
        or transition.set_id != complete.target_set_id
        or transition.transition_id != complete.context_transition_id
        or transition.current_inventory_hash != complete.current_inventory_hash
    ):
        raise OwnershipCoordinatorError(
            "birth_context_transition_recovery_required",
        )
    predecessor = complete.previous_head_id
    if predecessor is None:
        predecessor = "sha256:" + complete.previous_set_id
    _require_digest(predecessor, "dominant predecessor")
    return complete.request_id, predecessor, transition.transition_id


def _observe_dominant_identity_locked_v2(
    session: _DeploymentLockSessionV1, complete: object,
) -> tuple[str, str, str]:
    """Product identity observer under the fixed deployment session."""
    from executor_birth_ownership_chain import OwnershipChainStore

    snapshot = _resolve_ownership_coordinator_locked_v2(session)
    graph = _require_locked_coordinator_graph_snapshot_v2(snapshot, session)
    store = OwnershipChainStore()
    observed = _observe_dominant_identity_core_v2(
        graph, complete,
        lambda transition_id, proof: store.read_context_transition(
            transition_id, expected_proof=proof,
        ),
    )
    _require_deployment_lock_session_v1(session)
    return observed


@dataclass(frozen=True, slots=True)
class OwnershipCoordinatorResultV1:
    state: OwnershipCoordinatorStateV1
    request_id: str
    current_count: int
    cutover_id: str | None


def _result(
    record: OwnershipCoordinatorRecordV1 | OwnershipCoordinatorRecordV2,
) -> OwnershipCoordinatorResultV1:
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


def _prepare_staged_current_receipts_v2(
    staged_runtime: object, *, prove_quiescent: Callable[[], bool],
    expected_inventory: object,
) -> CurrentReceiptProof:
    """Build a V2-only receipt proof for one frozen transition inventory."""
    from executor_birth_bootstrap import _is_staged_reattestation_runtime_v2
    from executor_birth_cutover import (
        BirthCutoverError, CurrentInventoryV1, prepare_current_receipt_proof,
    )

    if (
        not _is_staged_reattestation_runtime_v2(staged_runtime)
        or not callable(prove_quiescent)
        or not isinstance(expected_inventory, CurrentInventoryV1)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_birth_runtime_unavailable",
        )
    prepared_by_identity: dict[tuple[str, str], object] = {}

    def prepared_for(current):
        identity = current.identity
        prepared = prepared_by_identity.get(identity)
        if prepared is None:
            prepared = staged_runtime.prepare(current)
            prepared_by_identity[identity] = prepared
        elif prepared.current != current:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required",
                "current inventory changed",
            )
        return prepared

    try:
        report = prepare_current_receipt_proof(
            prove_quiescent=prove_quiescent,
            enumerate_current=staged_runtime.enumerate_current,
            read_receipt=lambda current: staged_runtime.read_receipt(
                prepared_for(current),
            ),
            reattest_via_birth=lambda current: staged_runtime.reattest(
                prepared_for(current),
            ),
            verify_receipt=staged_runtime.verify_receipt,
        )
    except BirthCutoverError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_receipt_proof_invalid",
            _wrapped_cause_detail_v1(exc),
        ) from exc
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_receipt_proof_invalid",
        ) from exc
    if (
        not isinstance(report.proof, CurrentReceiptProof)
        or report.proof.inventory != expected_inventory
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "current inventory changed",
        )
    return report.proof


def _build_staged_current_receipts_v2(
    staged_context: object, *, now: Callable[[], datetime],
    prove_quiescent: Callable[[], bool], expected_inventory: object,
) -> CurrentReceiptProof:
    """Own staged-runtime composition at the receipt-proof boundary."""
    from executor_birth_bootstrap import (
        _build_staged_reattestation_runtime_v2,
    )

    staged_runtime = _build_staged_reattestation_runtime_v2(
        staged_context, now=now,
    )
    return _prepare_staged_current_receipts_v2(
        staged_runtime,
        prove_quiescent=prove_quiescent,
        expected_inventory=expected_inventory,
    )


def _current_reattestation_port_v1():
    """Load the sole fixed Birth port that can enumerate current generations."""
    from executor_birth_bootstrap import BirthBootstrapError, bootstrap_birth_runtime
    from executor_birth_commit_publisher import _is_birth_reattestation_port
    from executor_birth_operational import _runtime_bundle_snapshot

    bundle = _runtime_bundle_snapshot()
    if bundle is None:
        try:
            bundle = bootstrap_birth_runtime()
        except BirthBootstrapError as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_birth_runtime_unavailable",
                _wrapped_cause_detail_v1(exc),
            ) from exc
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
        raise OwnershipCoordinatorError(
            "birth_ownership_birth_runtime_unavailable",
        )
    return port


@contextmanager
def _transition_inventory_under_maintenance_v2(maintenance, evidence):
    """Freeze exact current identities under an already held maintenance guard."""
    from contract_cutover_guard import (
        _maintenance_evidence_under_transition_v1,
        _verify_store_only_catalog_locked,
    )
    from executor_birth_cutover import freeze_current_inventory_v1
    from executor_birth_ownership_preflight import canonical_maintenance_proof

    port = _current_reattestation_port_v1()
    initial = _maintenance_evidence_under_transition_v1(maintenance)
    supplied = canonical_maintenance_proof(
        source=evidence["source"], units=evidence["units"],
    )
    if supplied != initial or maintenance() is not True:
        raise OwnershipCoordinatorError(
            "birth_ownership_maintenance_changed",
        )
    inventory = freeze_current_inventory_v1(port.enumerate_current())
    _verify_store_only_catalog_locked()
    if (
        _maintenance_evidence_under_transition_v1(maintenance) != initial
        or maintenance() is not True
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_maintenance_changed",
        )
    try:
        yield maintenance, inventory, initial
    except BaseException:
        raise
    else:
        _verify_store_only_catalog_locked()
        final_inventory = freeze_current_inventory_v1(
            port.enumerate_current(),
        )
        if (
            final_inventory != inventory
            or _maintenance_evidence_under_transition_v1(maintenance)
            != initial
            or maintenance() is not True
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required",
                "current inventory or maintenance changed",
            )


@contextmanager
def _transition_maintenance_inventory_v2():
    """Acquire the ordinary guard and freeze the exact current identities."""
    from contract_cutover_guard import contract_cutover_guard

    with contract_cutover_guard() as (maintenance, evidence):
        with _transition_inventory_under_maintenance_v2(
            maintenance, evidence,
        ) as frozen:
            yield frozen


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
        from executor_birth_reattestation import reattest_current_generation

        port = _current_reattestation_port_v1()
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


def _startup_prerequisite_from_record_v2(
    prerequisite: object, complete: object,
) -> _StartupPrerequisiteV1:
    """Seal canonical prerequisite bytes bound to the complete V2 request."""
    from executor_birth_distribution_assembler import (
        DistributionAssemblerError, StartupPrerequisiteV1,
        decode_startup_prerequisite_v1,
        encode_startup_prerequisite_v1,
    )

    if (
        type(prerequisite) is not StartupPrerequisiteV1
        or type(complete) is not OwnershipCoordinatorRecordV2
        or complete.sequence != 1
        or complete.state is not OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_prerequisite_untrusted",
        )
    if (
        prerequisite.request_id != complete.request_id
        or prerequisite.closed_build_id != complete.closed_build_id
        or prerequisite.release_sequence != complete.release_sequence
        or prerequisite.deployment_descriptor_id
        != complete.deployment_descriptor_id
        or prerequisite.administrative_bundle_hash
        != complete.administrative_bundle_hash
        or prerequisite.service_coverage_hash
        != complete.service_coverage_hash
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required",
            "startup prerequisite binding",
        )
    try:
        encoded = encode_startup_prerequisite_v1(prerequisite)
        decoded = decode_startup_prerequisite_v1(encoded)
    except DistributionAssemblerError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_prerequisite_untrusted",
            _wrapped_cause_detail_v1(exc),
        ) from exc
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_prerequisite_untrusted",
        ) from exc
    if (
        decoded != prerequisite
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required",
            "startup prerequisite binding",
        )
    return _StartupPrerequisiteV1(
        prerequisite.prerequisite_id, _digest(encoded), _PREREQUISITE_SEAL,
    )


def _startup_prerequisite_for_test(
    prerequisite_id: str, evidence_digest: str,
) -> _StartupPrerequisiteV1:
    return _StartupPrerequisiteV1(
        prerequisite_id, evidence_digest, _PREREQUISITE_SEAL,
    )


def _single_cutover_key(authorities: RootOwnershipAuthoritiesV1) -> str:
    if not is_root_ownership_authorities_v1(authorities):
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
    context_transition_id: str,
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
        or type(context_transition_id) is not str
        or _DIGEST_RE.fullmatch(context_transition_id) is None
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
        context_transition_id=context_transition_id,
        dominant_startup_receipt=crossing_receipt,
        private_key=authorities.cutover_private,
    )
    certificate = verify_ownership_cutover_certificate(
        payload, signature, registry=authorities.public.cutover,
        expected_proof=proof,
        expected_context_transition_id=context_transition_id,
        expected_dominant_startup_receipt=crossing_receipt,
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
            expected_context_transition_id=context_transition_id,
            expected_dominant_startup_receipt=crossing_receipt,
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
            expected_context_transition_id=context_transition_id,
            expected_dominant_startup_receipt=crossing_receipt,
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
    "PreparedTransitionPublicationV2",
    "OwnershipCoordinatorStateV1", "prepare_ownership_cutover_v1",
]
