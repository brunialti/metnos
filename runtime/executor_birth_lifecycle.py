"""Sealed RM-0008 F5 lifecycle integration.

The module stays unreachable from productive routing until an operator-issued,
authenticated F4 certification record is loaded.  Lifecycle publications are
accepted only after the caller's independent RM-0007 reread returns the exact
AdmissionReceipt and predecessor relation expected by this coordinator.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from executor_birth_epoch_store import (
    BirthLifecycle, EpochCacheKey, EpochReplacement, replace_current_epoch,
)
from executor_birth_receipts import AdmissionReceipt, ApprovedLifecycle
from manifest_inventory import ContractId


CERTIFICATION_DOMAIN = b"metnos.executor-birth.f4-certification/v1\0"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_ACTIVATION_SEAL = object()


class LifecycleError(RuntimeError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise LifecycleError("lifecycle_binding_invalid", field)
    return value


def _text(value: object, field: str, maximum: int = 256) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or "\0" in value or len(value.encode()) > maximum):
        raise LifecycleError("lifecycle_binding_invalid", field)
    return value


@dataclass(frozen=True, slots=True)
class F4Certification:
    certificate_id: str
    environment_id: str
    admission_receipt_ids: tuple[str, ...]
    producer_ids: tuple[str, ...]
    routing_cycles: int
    unresolved_defects: int
    certified_at: str
    key_id: str
    signature: str


class F5Activation:
    """Unforgeable-in-process result of authenticating a durable certificate."""
    __slots__ = ("certificate",)

    def __init__(self, certificate: F4Certification, *, _seal: object) -> None:
        if _seal is not _ACTIVATION_SEAL:
            raise LifecycleError("f5_activation_forbidden")
        self.certificate = certificate


def _certificate_payload(cert: F4Certification) -> dict[str, object]:
    return {
        "schema_version": 1,
        "certificate_id": cert.certificate_id,
        "environment_id": cert.environment_id,
        "admission_receipt_ids": list(cert.admission_receipt_ids),
        "producer_ids": list(cert.producer_ids),
        "routing_cycles": cert.routing_cycles,
        "unresolved_defects": cert.unresolved_defects,
        "certified_at": cert.certified_at,
        "key_id": cert.key_id,
    }


def load_f5_activation(encoded: bytes, *, authorities: Mapping[str, Ed25519PublicKey]) -> F5Activation:
    """Authenticate an operator-provisioned F4 certificate and enforce its gate."""
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LifecycleError("f4_certification_invalid", "json") from exc
    expected = {"schema_version", "certificate_id", "environment_id",
                "admission_receipt_ids", "producer_ids", "routing_cycles",
                "unresolved_defects", "certified_at", "key_id", "signature"}
    if not isinstance(value, dict) or set(value) != expected or _canonical(value) != encoded:
        raise LifecycleError("f4_certification_invalid", "schema or canonical encoding")
    if value["schema_version"] != 1:
        raise LifecycleError("f4_certification_invalid", "schema_version")
    receipts, producers = value["admission_receipt_ids"], value["producer_ids"]
    if (not isinstance(receipts, list) or not isinstance(producers, list)
            or len(receipts) != len(set(receipts))
            or len(producers) != len(set(producers))):
        raise LifecycleError("f4_certification_invalid", "evidence sets")
    receipt_ids = tuple(_digest(item, "admission_receipt_id") for item in receipts)
    producer_ids = tuple(_text(item, "producer_id") for item in producers)
    if (receipt_ids != tuple(sorted(receipt_ids, key=str.encode))
            or producer_ids != tuple(sorted(producer_ids, key=str.encode))):
        raise LifecycleError("f4_certification_invalid", "evidence order")
    cycles, defects = value["routing_cycles"], value["unresolved_defects"]
    if type(cycles) is not int or type(defects) is not int or cycles < 0 or defects < 0:
        raise LifecycleError("f4_certification_invalid", "counts")
    certified_at = value["certified_at"]
    if not isinstance(certified_at, str) or _UTC.fullmatch(certified_at) is None:
        raise LifecycleError("f4_certification_invalid", "certified_at")
    key_id = _text(value["key_id"], "key_id")
    key = authorities.get(key_id)
    if not isinstance(key, Ed25519PublicKey):
        raise LifecycleError("f4_certification_invalid", "unknown authority")
    signature = value["signature"]
    try:
        raw_signature = base64.b64decode(signature, validate=True)
        if (not isinstance(signature, str) or len(raw_signature) != 64
                or base64.b64encode(raw_signature).decode("ascii") != signature):
            raise ValueError("noncanonical signature")
        key.verify(raw_signature, CERTIFICATION_DOMAIN + _canonical(
            {key: item for key, item in value.items() if key != "signature"}))
    except (TypeError, ValueError, InvalidSignature) as exc:
        raise LifecycleError("f4_certification_invalid", "signature") from exc
    cert = F4Certification(
        _digest(value["certificate_id"], "certificate_id"),
        _text(value["environment_id"], "environment_id"), receipt_ids,
        producer_ids, cycles, defects, certified_at, key_id, signature,
    )
    expected_id = "sha256:" + hashlib.sha256(
        CERTIFICATION_DOMAIN + _canonical({
            key: item for key, item in _certificate_payload(cert).items()
            if key != "certificate_id"
        })).hexdigest()
    if cert.certificate_id != expected_id:
        raise LifecycleError("f4_certification_invalid", "certificate_id")
    if len(receipt_ids) < 5 or len(producer_ids) < 2 or cycles < 2 or defects != 0:
        raise LifecycleError("f4_certification_threshold_not_met")
    return F5Activation(cert, _seal=_ACTIVATION_SEAL)


@dataclass(frozen=True, slots=True)
class LifecyclePublication:
    """Result of publication followed by independent authenticated reread."""
    receipt: AdmissionReceipt
    encoded_admission_receipt: bytes
    reread_generation_id: str
    reread_predecessor_id: str | None
    reread_lifecycle: ApprovedLifecycle


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    publication: LifecyclePublication
    epochs: EpochReplacement


PublishRevision = Callable[[ContractId, str, BirthLifecycle, str | None], LifecyclePublication]
VerifyAdmission = Callable[[bytes], AdmissionReceipt]


class LifecycleCoordinator:
    """Productive F5 boundary; construction requires authenticated F4 proof."""
    __slots__ = ("_db_path", "_publish", "_verify_admission")

    def __init__(self, activation: F5Activation, *, db_path: Path,
                 publish_and_reread: PublishRevision,
                 verify_admission: VerifyAdmission) -> None:
        if not isinstance(activation, F5Activation):
            raise LifecycleError("f5_activation_required")
        if (not isinstance(db_path, Path) or not callable(publish_and_reread)
                or not callable(verify_admission)):
            raise LifecycleError("lifecycle_binding_invalid", "dependencies")
        self._db_path, self._publish = db_path, publish_and_reread
        self._verify_admission = verify_admission

    def revise(
        self, key: EpochCacheKey, *, expected_version: int,
        target: BirthLifecycle, name: str, source: str, occurred_at: str,
        historic_epoch_ref: str | None = None,
    ) -> LifecycleResult:
        if target not in {BirthLifecycle.PREEXERCISE, BirthLifecycle.ACTIVE,
                          BirthLifecycle.QUARANTINED}:
            raise LifecycleError("lifecycle_transition_invalid", "target")
        allowed = {
            BirthLifecycle.SYNTHESIZED: {BirthLifecycle.PREEXERCISE,
                                         BirthLifecycle.QUARANTINED},
            BirthLifecycle.PREEXERCISE: {BirthLifecycle.ACTIVE,
                                         BirthLifecycle.QUARANTINED},
            BirthLifecycle.ACTIVE: {BirthLifecycle.QUARANTINED},
        }
        # An active-to-active revision is reserved exclusively for rollback:
        # its new generation points at a separately authenticated historic
        # epoch and receives fresh counters.
        rollback = (key.lifecycle is BirthLifecycle.ACTIVE
                    and target is BirthLifecycle.ACTIVE
                    and historic_epoch_ref is not None)
        if not rollback and target not in allowed.get(key.lifecycle, set()):
            raise LifecycleError("lifecycle_transition_invalid", "edge")
        publication = self._publish(
            key.contract_id, key.generation_id, target, historic_epoch_ref)
        self._verify_publication(publication, key, target)
        replacement = replace_current_epoch(
            contract_id=key.contract_id,
            expected_generation_id=key.generation_id,
            expected_state_version=expected_version,
            generation_id=publication.reread_generation_id,
            name=name, source=source, lifecycle=target, observed_at=occurred_at,
            db_path=self._db_path,
            event_kind=f"lifecycle_{target.value}",
            historic_epoch_ref=historic_epoch_ref,
        )
        return LifecycleResult(publication, replacement)

    def quarantine_before_nonselection(self, key: EpochCacheKey, *, expected_version: int,
                                       name: str, source: str,
                                       occurred_at: str) -> LifecycleResult:
        """Publish/reread quarantine before the local selection CAS commits."""
        return self.revise(key, expected_version=expected_version,
                           target=BirthLifecycle.QUARANTINED, name=name,
                           source=source, occurred_at=occurred_at)

    def _verify_publication(self, publication: object, predecessor: EpochCacheKey,
                            target: BirthLifecycle) -> None:
        if not isinstance(publication, LifecyclePublication):
            raise LifecycleError("lifecycle_reread_invalid", "type")
        if not isinstance(publication.encoded_admission_receipt, bytes):
            raise LifecycleError("lifecycle_reread_invalid", "admission receipt bytes")
        try:
            receipt = self._verify_admission(publication.encoded_admission_receipt)
        except Exception as exc:
            raise LifecycleError("lifecycle_reread_invalid", "admission authentication") from exc
        if (not isinstance(receipt, AdmissionReceipt)
                or not isinstance(publication.receipt, AdmissionReceipt)
                or receipt.receipt_id != publication.receipt.receipt_id):
            raise LifecycleError("lifecycle_reread_invalid", "admission receipt")
        lifecycle = ApprovedLifecycle(target.value)
        if (receipt.contract_id != predecessor.contract_id.value
                or receipt.generation_id != publication.reread_generation_id
                or receipt.predecessor_id != predecessor.generation_id
                or receipt.approved_lifecycle is not lifecycle
                or publication.reread_predecessor_id != predecessor.generation_id
                or publication.reread_lifecycle is not lifecycle):
            raise LifecycleError("lifecycle_reread_invalid", "binding")
