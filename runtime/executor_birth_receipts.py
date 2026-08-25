"""Pure authenticated receipt codecs for the observational Birth phase.

No receipt is stored or consumed here, and this module has no publication
dependency.  Operational authorities and uniqueness stores belong to F4.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from manifest_inventory import ContractId, ManifestOrigin


PRODUCER_ID_DOMAIN = b"metnos.executor-birth.producer-receipt-id/v1\0"
PRODUCER_SIGNATURE_DOMAIN = b"metnos.executor-birth.producer-receipt/v1\0"
ADMISSION_ID_DOMAIN = b"metnos.executor-birth.admission-receipt-id/v1\0"
ADMISSION_SIGNATURE_DOMAIN = b"metnos.executor-birth.admission-receipt/v1\0"
SCHEMA_VERSION = 1
IDENTITY_VERSION = 1

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_NONCE_RE = re.compile(r"[0-9a-f]{32}\Z")
_UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_AUTH_KEYS = frozenset({"algorithm", "key_id", "signature"})


class ReceiptError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class RevisionClass(str, Enum):
    FIRST_BIRTH = "first_birth"
    CODE_REVISION = "code_revision"
    AUTHORITY_REVISION = "authority_revision"
    CONTRACT_REVISION = "contract_revision"
    LOCALIZATION_REVISION = "localization_revision"
    EQUIVALENT_REPUBLISH = "equivalent_republish"
    PROMOTION_REVISION = "promotion_revision"
    REACTIVATION_REVISION = "reactivation_revision"
    REATTESTATION = "reattestation"


class AdmissionKind(str, Enum):
    ADMISSION = "admission"
    REATTESTATION = "reattestation"


class ApprovedLifecycle(str, Enum):
    ACTIVE = "active"
    PREEXERCISE = "preexercise"
    QUARANTINED = "quarantined"


class AdmittedCheckStatus(str, Enum):
    PASSED = "passed"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class Authentication:
    algorithm: str
    key_id: str
    signature: str


@dataclass(frozen=True, slots=True)
class ProducerReceipt:
    schema_version: int
    receipt_id: str
    issuer_id: str
    executor_origin: ExecutorOrigin
    revision_authorship: RevisionAuthor
    objective_hash: str
    candidate_source_id: str
    issued_at: str
    expires_at: str
    nonce: str
    authentication: Authentication


@dataclass(frozen=True, slots=True)
class IssuerKey:
    key_id: str
    public_key: Ed25519PublicKey
    allowed_origins: frozenset[ExecutorOrigin]
    allowed_authors: frozenset[RevisionAuthor]

    def __post_init__(self) -> None:
        if not _text(self.key_id) or not isinstance(self.public_key, Ed25519PublicKey):
            raise ReceiptError("producer_receipt_invalid", "issuer key")
        if not self.allowed_origins or not self.allowed_authors:
            raise ReceiptError("producer_receipt_invalid", "empty issuer authority")
        if any(not isinstance(item, ExecutorOrigin) for item in self.allowed_origins):
            raise ReceiptError("producer_receipt_invalid", "issuer origin")
        if any(not isinstance(item, RevisionAuthor) for item in self.allowed_authors):
            raise ReceiptError("producer_receipt_invalid", "issuer author")


@dataclass(frozen=True, slots=True)
class IssuerRegistry:
    entries: Mapping[str, tuple[IssuerKey, ...]]

    def __post_init__(self) -> None:
        normalized: dict[str, tuple[IssuerKey, ...]] = {}
        for issuer_id, keys in self.entries.items():
            if not _text(issuer_id) or not isinstance(keys, tuple) or not keys:
                raise ReceiptError("producer_receipt_invalid", "issuer registry")
            if any(not isinstance(item, IssuerKey) for item in keys):
                raise ReceiptError("producer_receipt_invalid", "issuer registry entry")
            names = [item.key_id for item in keys]
            if len(names) != len(set(names)):
                raise ReceiptError("producer_receipt_invalid", "duplicate issuer key")
            normalized[issuer_id] = keys
        object.__setattr__(self, "entries", MappingProxyType(normalized))


@dataclass(frozen=True, slots=True)
class AdmissionCheck:
    rule_version: str
    status: AdmittedCheckStatus
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class AdmissionReceipt:
    schema_version: int
    policy_version: str
    identity_version: int
    receipt_id: str
    contract_id: str
    generation_id: str
    candidate_id: str
    semantic_core_id: str
    admission_context_id: str
    birth_request_id: str
    authoring_journal_hash: str
    predecessor_id: str | None
    producer_receipt_hash: str
    revision_class: RevisionClass
    check_results: Mapping[str, AdmissionCheck]
    semantic_review_hash: str | None
    approval_hash: str | None
    approved_lifecycle: ApprovedLifecycle
    kind: AdmissionKind
    issued_at: str
    authentication: Authentication


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and "\x00" not in value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ReceiptError("receipt_invalid", f"duplicate key: {key}")
        result[key] = value
    return result


def _decode_json(encoded: bytes) -> dict[str, object]:
    try:
        value = json.loads(encoded.decode("utf-8"), object_pairs_hook=_pairs)
    except ReceiptError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError("receipt_invalid", "json") from exc
    if not isinstance(value, dict) or _canonical(value) != encoded:
        raise ReceiptError("receipt_invalid", "non-canonical json")
    return value


def _timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise ReceiptError("receipt_invalid", field)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ReceiptError("receipt_invalid", field) from exc
    return parsed


def _digest(value: object, *, field: str, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ReceiptError("receipt_invalid", field)


def _auth(value: object) -> Authentication:
    if not isinstance(value, dict) or set(value) != _AUTH_KEYS:
        raise ReceiptError("receipt_invalid", "authentication")
    if value.get("algorithm") != "ed25519" or not _text(value.get("key_id")):
        raise ReceiptError("receipt_invalid", "authentication")
    signature = value.get("signature")
    if not isinstance(signature, str):
        raise ReceiptError("receipt_invalid", "signature")
    try:
        raw = base64.b64decode(signature, validate=True)
    except ValueError as exc:
        raise ReceiptError("receipt_invalid", "signature") from exc
    if len(raw) != 64 or base64.b64encode(raw).decode("ascii") != signature:
        raise ReceiptError("receipt_invalid", "signature")
    return Authentication("ed25519", str(value["key_id"]), signature)


def _signed(payload: dict[str, object], *, domain: bytes, key_id: str, private_key: Ed25519PrivateKey) -> dict[str, object]:
    if not _text(key_id) or not isinstance(private_key, Ed25519PrivateKey):
        raise ReceiptError("receipt_invalid", "signing key")
    signature = private_key.sign(domain + _canonical(payload))
    return {**payload, "authentication": {
        "algorithm": "ed25519", "key_id": key_id,
        "signature": base64.b64encode(signature).decode("ascii"),
    }}


def _identifier(payload: Mapping[str, object], domain: bytes) -> str:
    material = {key: value for key, value in payload.items() if key != "receipt_id"}
    return "sha256:" + hashlib.sha256(domain + _canonical(material)).hexdigest()


_PRODUCER_KEYS = frozenset({
    "schema_version", "receipt_id", "issuer_id", "executor_origin",
    "revision_authorship", "objective_hash", "candidate_source_id",
    "issued_at", "expires_at", "nonce", "authentication",
})


def issue_producer_receipt(
    *, issuer_id: str, executor_origin: ExecutorOrigin,
    revision_authorship: RevisionAuthor, objective_hash: str,
    candidate_source_id: str, issued_at: str, expires_at: str, nonce: str,
    key_id: str, private_key: Ed25519PrivateKey,
) -> bytes:
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION, "issuer_id": issuer_id,
        "executor_origin": executor_origin.value if isinstance(executor_origin, ExecutorOrigin) else executor_origin,
        "revision_authorship": revision_authorship.value if isinstance(revision_authorship, RevisionAuthor) else revision_authorship,
        "objective_hash": objective_hash, "candidate_source_id": candidate_source_id,
        "issued_at": issued_at, "expires_at": expires_at, "nonce": nonce,
    }
    payload["receipt_id"] = _identifier(payload, PRODUCER_ID_DOMAIN)
    encoded = _canonical(_signed(payload, domain=PRODUCER_SIGNATURE_DOMAIN, key_id=key_id, private_key=private_key))
    # Issuance obeys the same closed schema, independently of the registry.
    _parse_producer(encoded, temporal_now=None)
    return encoded


def _parse_producer(encoded: bytes, *, temporal_now: datetime | None) -> tuple[ProducerReceipt, dict[str, object]]:
    value = _decode_json(encoded)
    if set(value) != _PRODUCER_KEYS or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ReceiptError("producer_receipt_invalid", "schema")
    for field in ("receipt_id", "objective_hash", "candidate_source_id"):
        _digest(value.get(field), field=field)
    if not _text(value.get("issuer_id")) or not _NONCE_RE.fullmatch(str(value.get("nonce", ""))):
        raise ReceiptError("producer_receipt_invalid", "issuer or nonce")
    try:
        origin = ExecutorOrigin(value["executor_origin"])
        author = RevisionAuthor(value["revision_authorship"])
    except (KeyError, ValueError) as exc:
        raise ReceiptError("producer_receipt_invalid", "provenance") from exc
    issued = _timestamp(value.get("issued_at"), field="issued_at")
    expires = _timestamp(value.get("expires_at"), field="expires_at")
    if expires <= issued:
        raise ReceiptError("producer_receipt_invalid", "expiry order")
    if temporal_now is not None:
        now = temporal_now.astimezone(timezone.utc)
        if issued.timestamp() > now.timestamp() + 30:
            raise ReceiptError("producer_receipt_invalid", "issued in future")
        if now >= expires:
            raise ReceiptError("producer_receipt_expired")
    auth = _auth(value.get("authentication"))
    unsigned = {key: item for key, item in value.items() if key != "authentication"}
    if value["receipt_id"] != _identifier(unsigned, PRODUCER_ID_DOMAIN):
        raise ReceiptError("producer_receipt_invalid", "receipt_id")
    return ProducerReceipt(
        1, str(value["receipt_id"]), str(value["issuer_id"]), origin, author,
        str(value["objective_hash"]), str(value["candidate_source_id"]),
        str(value["issued_at"]), str(value["expires_at"]), str(value["nonce"]), auth,
    ), unsigned


def verify_producer_receipt(encoded: bytes, *, registry: IssuerRegistry, now: datetime) -> ProducerReceipt:
    if not isinstance(registry, IssuerRegistry):
        raise ReceiptError("producer_receipt_invalid", "issuer registry")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ReceiptError("producer_receipt_invalid", "verification time")
    receipt, unsigned = _parse_producer(encoded, temporal_now=now)
    candidates = registry.entries.get(receipt.issuer_id, ())
    entry = next((item for item in candidates if item.key_id == receipt.authentication.key_id), None)
    if entry is None:
        raise ReceiptError("producer_receipt_invalid", "issuer key")
    if receipt.executor_origin not in entry.allowed_origins or receipt.revision_authorship not in entry.allowed_authors:
        raise ReceiptError("origin_authorship_mismatch")
    try:
        entry.public_key.verify(
            base64.b64decode(receipt.authentication.signature, validate=True),
            PRODUCER_SIGNATURE_DOMAIN + _canonical(unsigned),
        )
    except InvalidSignature as exc:
        raise ReceiptError("producer_receipt_invalid", "signature") from exc
    return receipt


_ADMISSION_KEYS = frozenset({
    "schema_version", "policy_version", "identity_version", "receipt_id",
    "contract_id", "generation_id", "candidate_id", "semantic_core_id",
    "admission_context_id", "birth_request_id", "authoring_journal_hash",
    "predecessor_id", "producer_receipt_hash",
    "revision_class", "check_results", "semantic_review_hash", "approval_hash",
    "approved_lifecycle", "kind", "issued_at", "authentication",
})


def _contract_id(value: object) -> str:
    if not isinstance(value, str) or ":" not in value:
        raise ReceiptError("receipt_invalid", "contract_id")
    origin, relative = value.split(":", 1)
    try:
        canonical = ContractId(ManifestOrigin(origin), relative).value
    except ValueError as exc:
        raise ReceiptError("receipt_invalid", "contract_id") from exc
    if canonical != value:
        raise ReceiptError("receipt_invalid", "contract_id")
    return value


def _checks(value: object) -> dict[str, AdmissionCheck]:
    if not isinstance(value, dict):
        raise ReceiptError("receipt_invalid", "check_results")
    result: dict[str, AdmissionCheck] = {}
    for check_id, raw in value.items():
        if not _text(check_id) or not isinstance(raw, dict) or set(raw) != {"rule_version", "status", "evidence_hash"}:
            raise ReceiptError("receipt_invalid", "check_results")
        if not _text(raw.get("rule_version")):
            raise ReceiptError("receipt_invalid", "rule_version")
        _digest(raw.get("evidence_hash"), field="evidence_hash")
        try:
            status = AdmittedCheckStatus(raw["status"])
        except (KeyError, ValueError) as exc:
            raise ReceiptError("receipt_invalid", "check status") from exc
        result[check_id] = AdmissionCheck(str(raw["rule_version"]), status, str(raw["evidence_hash"]))
    return dict(sorted(result.items(), key=lambda item: item[0].encode("utf-8")))


def issue_admission_receipt(
    *, policy_version: str, contract_id: ContractId, generation_id: str,
    candidate_id: str, semantic_core_id: str, admission_context_id: str,
    birth_request_id: str, authoring_journal_hash: str,
    predecessor_id: str | None, producer_receipt_hash: str,
    revision_class: RevisionClass, check_results: Mapping[str, AdmissionCheck],
    semantic_review_hash: str | None, approval_hash: str | None,
    approved_lifecycle: ApprovedLifecycle, kind: AdmissionKind, issued_at: str,
    key_id: str, private_key: Ed25519PrivateKey,
) -> bytes:
    checks = {key: {
        "rule_version": item.rule_version, "status": item.status.value,
        "evidence_hash": item.evidence_hash,
    } for key, item in check_results.items()}
    payload: dict[str, object] = {
        "schema_version": 1, "policy_version": policy_version,
        "identity_version": 1, "contract_id": contract_id.value,
        "generation_id": generation_id, "candidate_id": candidate_id,
        "semantic_core_id": semantic_core_id, "admission_context_id": admission_context_id,
        "birth_request_id": birth_request_id,
        "authoring_journal_hash": authoring_journal_hash,
        "predecessor_id": predecessor_id, "producer_receipt_hash": producer_receipt_hash,
        "revision_class": revision_class.value, "check_results": checks,
        "semantic_review_hash": semantic_review_hash, "approval_hash": approval_hash,
        "approved_lifecycle": approved_lifecycle.value, "kind": kind.value,
        "issued_at": issued_at,
    }
    payload["receipt_id"] = _identifier(payload, ADMISSION_ID_DOMAIN)
    encoded = _canonical(_signed(payload, domain=ADMISSION_SIGNATURE_DOMAIN, key_id=key_id, private_key=private_key))
    _parse_admission(encoded)
    return encoded


def _parse_admission(encoded: bytes) -> tuple[AdmissionReceipt, dict[str, object]]:
    value = _decode_json(encoded)
    if set(value) != _ADMISSION_KEYS or value.get("schema_version") != 1 or type(value.get("schema_version")) is not int or value.get("identity_version") != 1 or type(value.get("identity_version")) is not int:
        raise ReceiptError("receipt_invalid", "schema")
    if not _text(value.get("policy_version")):
        raise ReceiptError("receipt_invalid", "policy_version")
    for field in ("receipt_id", "generation_id", "candidate_id", "semantic_core_id", "admission_context_id", "birth_request_id", "authoring_journal_hash", "producer_receipt_hash"):
        _digest(value.get(field), field=field)
    for field in ("predecessor_id", "semantic_review_hash", "approval_hash"):
        _digest(value.get(field), field=field, nullable=True)
    contract = _contract_id(value.get("contract_id"))
    issued = _timestamp(value.get("issued_at"), field="issued_at")
    del issued
    checks = _checks(value.get("check_results"))
    try:
        revision = RevisionClass(value["revision_class"])
        lifecycle = ApprovedLifecycle(value["approved_lifecycle"])
        kind = AdmissionKind(value["kind"])
    except (KeyError, ValueError) as exc:
        raise ReceiptError("receipt_invalid", "enum") from exc
    auth = _auth(value.get("authentication"))
    unsigned = {key: item for key, item in value.items() if key != "authentication"}
    if value["receipt_id"] != _identifier(unsigned, ADMISSION_ID_DOMAIN):
        raise ReceiptError("receipt_invalid", "receipt_id")
    receipt = AdmissionReceipt(
        1, str(value["policy_version"]), 1, str(value["receipt_id"]), contract,
        str(value["generation_id"]), str(value["candidate_id"]),
        str(value["semantic_core_id"]), str(value["admission_context_id"]),
        str(value["birth_request_id"]), str(value["authoring_journal_hash"]),
        value["predecessor_id"], str(value["producer_receipt_hash"]), revision,
        MappingProxyType(checks), value["semantic_review_hash"], value["approval_hash"], lifecycle,
        kind, str(value["issued_at"]), auth,
    )
    return receipt, unsigned


def verify_admission_receipt(
    encoded: bytes, *, public_key: Ed25519PublicKey, expected_key_id: str,
) -> AdmissionReceipt:
    receipt, unsigned = _parse_admission(encoded)
    if (
        not isinstance(public_key, Ed25519PublicKey)
        or not _text(expected_key_id)
        or receipt.authentication.key_id != expected_key_id
    ):
        raise ReceiptError("receipt_invalid", "admission key")
    try:
        public_key.verify(
            base64.b64decode(receipt.authentication.signature, validate=True),
            ADMISSION_SIGNATURE_DOMAIN + _canonical(unsigned),
        )
    except InvalidSignature as exc:
        raise ReceiptError("receipt_invalid", "signature") from exc
    return receipt
