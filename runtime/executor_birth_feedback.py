"""Inactive RM-0008 F5 execution-receipt and feedback contracts.

Nothing in this module is connected to the loader, durable plans, or the
publisher.  Mutating operations are supplied by a later integration layer as
small typed callbacks, so the ordering and compare-and-swap rules can already
be tested without making F5 productive before its admission threshold.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Mapping
from pathlib import Path

from executor_birth_failure_review import (
    FailureReview, FailureReviewRequest, FailureReviewVerdict,
)
from manifest_inventory import ContractId


EXECUTION_RECEIPT_DOMAIN = b"metnos.executor-birth.execution-receipt/v1\0"
EXECUTION_RECEIPT_RESULT_KEY = "_metnos_execution_receipt_v1"
FAILURE_JOB_DOMAIN = b"metnos.executor-birth.failure-review-job/v1\0"
REPAIR_REQUEST_DOMAIN = b"metnos.executor-birth.repair-birth-request/v1\0"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|pwd|secret|token|credential|authorization|cookie|"
    r"session|private[_-]?key|api[_-]?key|otp|passcode|path|filepath|"
    r"filename|url|uri|headers?|body|content)", re.IGNORECASE,
)
_MAX_RETAINED_DEPTH = 6
_MAX_RETAINED_ITEMS = 64
_MAX_RETAINED_TEXT_BYTES = 4096


class FeedbackError(ValueError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FeedbackError("feedback_binding_invalid", "non-json payload") from exc


def _hash(domain: bytes, value: object) -> str:
    return "sha256:" + hashlib.sha256(domain + _canonical(value)).hexdigest()


def payload_hash(value: Mapping[str, object]) -> str:
    """Hash one reduced, retainable payload (arguments or output)."""
    return _hash(EXECUTION_RECEIPT_DOMAIN + b"payload\0", dict(value))


def utc_now_seconds() -> str:
    """Return the sole timestamp shape accepted by execution receipts."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reduced_query_reference(query: str) -> str:
    """Authenticate an already-redacted query without retaining its text."""
    if not isinstance(query, str) or "\x00" in query:
        raise FeedbackError("feedback_binding_invalid", "reduced_query")
    return _hash(EXECUTION_RECEIPT_DOMAIN + b"query\0", {"query": query})


def dispatch_identifier_reference(kind: str, value: str) -> str:
    """Pseudonymize a bounded runtime identifier into canonical digest form."""
    if kind not in {"request", "turn"}:
        raise FeedbackError("feedback_binding_invalid", "identifier kind")
    _text(value, f"{kind}_id", 512)
    return _hash(
        EXECUTION_RECEIPT_DOMAIN + b"identifier\0",
        {"kind": kind, "value": value},
    )


def reduce_retainable_payload(value: Mapping[str, object]) -> dict[str, object]:
    """Produce a bounded JSON snapshot suitable for durable feedback.

    Secret-bearing fields are replaced before hashing.  Oversized collections
    and strings are represented by a digest rather than copied into StepLog;
    the digest proves which reduced value was observed without retaining its
    potentially sensitive or unbounded bytes.
    """
    if not isinstance(value, Mapping):
        raise FeedbackError("feedback_binding_invalid", "payload")

    def reduce(node: object, depth: int, *, key: str = "") -> object:
        if key and _SENSITIVE_KEY.search(key):
            return {"_redacted": "sensitive-field"}
        if depth > _MAX_RETAINED_DEPTH:
            return {"_reduced_sha256": _hash(
                EXECUTION_RECEIPT_DOMAIN + b"overflow\0", node)}
        if node is None or type(node) in (bool, int):
            return node
        if isinstance(node, float):
            if not (node == node and abs(node) != float("inf")):
                raise FeedbackError("feedback_binding_invalid", "non-json payload")
            return node
        if isinstance(node, str):
            encoded = node.encode("utf-8")
            if len(encoded) <= _MAX_RETAINED_TEXT_BYTES and "\x00" not in node:
                return node
            return {"_reduced_sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
                    "_utf8_bytes": len(encoded)}
        if isinstance(node, Mapping):
            items = sorted(node.items(), key=lambda item: str(item[0]).encode("utf-8"))
            if any(not isinstance(item_key, str) for item_key, _ in items):
                raise FeedbackError("feedback_binding_invalid", "non-string key")
            kept = items[:_MAX_RETAINED_ITEMS]
            result = {item_key: reduce(item, depth + 1, key=item_key)
                      for item_key, item in kept}
            if len(items) > len(kept):
                result["_reduced_tail"] = {
                    "count": len(items) - len(kept),
                    "sha256": _hash(EXECUTION_RECEIPT_DOMAIN + b"tail\0",
                                    dict(items[len(kept):])),
                }
            return result
        if isinstance(node, (list, tuple)):
            kept = list(node[:_MAX_RETAINED_ITEMS])
            result = [reduce(item, depth + 1) for item in kept]
            if len(node) > len(kept):
                result.append({"_reduced_tail": {
                    "count": len(node) - len(kept),
                    "sha256": _hash(EXECUTION_RECEIPT_DOMAIN + b"tail\0",
                                    list(node[len(kept):])),
                }})
            return result
        return {"_redacted": "non-json-value", "type": type(node).__name__}

    reduced = reduce(dict(value), 0)
    if not isinstance(reduced, dict):  # Mapping root above makes this defensive.
        raise FeedbackError("feedback_binding_invalid", "payload")
    _canonical(reduced)
    return reduced


def _text(value: object, field: str, maximum: int = 256) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or "\x00" in value or len(value.encode("utf-8")) > maximum):
        raise FeedbackError("feedback_binding_invalid", field)
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise FeedbackError("feedback_binding_invalid", field)
    return value


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    """Exact dispatch identity retained in ``StepLog`` for later feedback."""

    schema_version: int
    receipt_id: str
    request_id: str
    turn_id: str
    reduced_query_ref: str
    arguments_hash: str
    arguments: Mapping[str, object]
    output_hash: str
    reduced_output: Mapping[str, object]
    contract_id: ContractId
    executor_name: str
    candidate_id: str
    generation_id: str
    dispatched_at: str
    completed_at: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise FeedbackError("feedback_binding_invalid", "schema_version")
        _digest(self.receipt_id, "receipt_id")
        _digest(self.request_id, "request_id")
        _digest(self.turn_id, "turn_id")
        _digest(self.reduced_query_ref, "reduced_query_ref")
        if not isinstance(self.contract_id, ContractId):
            raise FeedbackError("feedback_binding_invalid", "contract_id")
        _text(self.executor_name, "executor_name")
        _digest(self.candidate_id, "candidate_id")
        _digest(self.generation_id, "generation_id")
        if not isinstance(self.dispatched_at, str) or _UTC.fullmatch(self.dispatched_at) is None:
            raise FeedbackError("feedback_binding_invalid", "dispatched_at")
        if not isinstance(self.completed_at, str) or _UTC.fullmatch(self.completed_at) is None:
            raise FeedbackError("feedback_binding_invalid", "completed_at")
        if self.completed_at < self.dispatched_at:
            raise FeedbackError("feedback_binding_invalid", "completed_at")
        arguments = dict(self.arguments)
        output = dict(self.reduced_output)
        if _digest(self.arguments_hash, "arguments_hash") != payload_hash(arguments):
            raise FeedbackError("feedback_binding_invalid", "arguments_hash")
        if _digest(self.output_hash, "output_hash") != payload_hash(output):
            raise FeedbackError("feedback_binding_invalid", "output_hash")
        # Retain detached JSON values.  Plain dictionaries are intentional:
        # ``TurnLog.write`` serializes StepLog through ``dataclasses.asdict``.
        object.__setattr__(self, "arguments", json.loads(_canonical(arguments)))
        object.__setattr__(self, "reduced_output", json.loads(_canonical(output)))
        if self.receipt_id != execution_receipt_id(self, omit_receipt_id=True):
            raise FeedbackError("feedback_binding_invalid", "receipt_id")


def _receipt_payload(receipt: ExecutionReceipt, *, omit_receipt_id: bool) -> dict[str, object]:
    value = {
        "schema_version": receipt.schema_version,
        "request_id": receipt.request_id,
        "turn_id": receipt.turn_id,
        "reduced_query_ref": receipt.reduced_query_ref,
        "arguments_hash": receipt.arguments_hash,
        "arguments": dict(receipt.arguments),
        "output_hash": receipt.output_hash,
        "reduced_output": dict(receipt.reduced_output),
        "contract_id": receipt.contract_id.value,
        "executor_name": receipt.executor_name,
        "candidate_id": receipt.candidate_id,
        "generation_id": receipt.generation_id,
        "dispatched_at": receipt.dispatched_at,
        "completed_at": receipt.completed_at,
    }
    if not omit_receipt_id:
        value["receipt_id"] = receipt.receipt_id
    return value


def execution_receipt_id(receipt: ExecutionReceipt, *, omit_receipt_id: bool = False) -> str:
    return _hash(EXECUTION_RECEIPT_DOMAIN, _receipt_payload(receipt, omit_receipt_id=True))


def _validate_execution_receipt(receipt: ExecutionReceipt) -> None:
    """Recheck retained mutable JSON immediately before any feedback mutation."""
    if payload_hash(dict(receipt.arguments)) != receipt.arguments_hash:
        raise FeedbackError("feedback_binding_invalid", "arguments_hash")
    if payload_hash(dict(receipt.reduced_output)) != receipt.output_hash:
        raise FeedbackError("feedback_binding_invalid", "output_hash")
    if execution_receipt_id(receipt, omit_receipt_id=True) != receipt.receipt_id:
        raise FeedbackError("feedback_binding_invalid", "receipt_id")


def make_execution_receipt(
    *, request_id: str, turn_id: str, reduced_query_ref: str,
    arguments: Mapping[str, object], reduced_output: Mapping[str, object],
    contract_id: ContractId, executor_name: str, generation_id: str,
    candidate_id: str, dispatched_at: str,
    completed_at: str,
) -> ExecutionReceipt:
    args, output = dict(arguments), dict(reduced_output)
    provisional = object.__new__(ExecutionReceipt)
    for key, value in {
        "schema_version": 1, "receipt_id": "sha256:" + "0" * 64,
        "request_id": request_id, "turn_id": turn_id,
        "reduced_query_ref": reduced_query_ref,
        "arguments_hash": payload_hash(args), "arguments": args,
        "output_hash": payload_hash(output), "reduced_output": output,
        "contract_id": contract_id, "executor_name": executor_name,
        "candidate_id": candidate_id,
        "generation_id": generation_id, "dispatched_at": dispatched_at,
        "completed_at": completed_at,
    }.items():
        object.__setattr__(provisional, key, value)
    receipt_id = execution_receipt_id(provisional, omit_receipt_id=True)
    return ExecutionReceipt(1, receipt_id, request_id, turn_id, reduced_query_ref,
                            payload_hash(args), args, payload_hash(output), output,
                            contract_id, executor_name, candidate_id, generation_id,
                            dispatched_at, completed_at)


class QuarantineCAS(str, Enum):
    APPLIED = "applied"
    ALREADY_QUARANTINED = "already_quarantined"
    STALE = "stale"


class FeedbackStatus(str, Enum):
    QUARANTINED = "quarantined"
    ENQUEUE_FAILED = "enqueue_failed"
    STALE_FEEDBACK = "stale_feedback"


@dataclass(frozen=True, slots=True)
class FeedbackResult:
    status: FeedbackStatus
    receipt_id: str
    failure_job_id: str | None
    quarantine_applied: bool


def failure_job_id(receipt_id: str) -> str:
    return _hash(FAILURE_JOB_DOMAIN, {"execution_receipt_id": _digest(receipt_id, "receipt_id")})


_QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS executor_failure_review_queue (
  job_id TEXT PRIMARY KEY,
  execution_receipt_id TEXT NOT NULL UNIQUE,
  request_json BLOB NOT NULL,
  created_at TEXT NOT NULL
)
"""


def enqueue_failure_review_inactive(
    job_id: str, request: FailureReviewRequest, *, created_at: str, db_path: Path,
) -> bool:
    """Persist an exact idempotent job in the isolated, inactive F5 queue."""
    _digest(job_id, "failure_job_id")
    if not isinstance(request, FailureReviewRequest):
        raise FeedbackError("feedback_binding_invalid", "failure review request")
    if not isinstance(created_at, str) or _UTC.fullmatch(created_at) is None:
        raise FeedbackError("feedback_binding_invalid", "created_at")
    payload = _canonical({
        "execution_receipt_id": request.execution_receipt_id,
        "execution_receipt_hash": request.execution_receipt_hash,
        "candidate_id": request.candidate_id,
        "generation_id": request.generation_id,
        "failure_evidence_hash": request.failure_evidence_hash,
        "error_code": request.error_code,
        "reduced_arguments": dict(request.reduced_arguments),
        "reduced_output": dict(request.reduced_output),
    })
    connection = sqlite3.connect(str(db_path), isolation_level=None, timeout=5)
    try:
        connection.execute(_QUEUE_SCHEMA)
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT job_id,request_json FROM executor_failure_review_queue "
            "WHERE execution_receipt_id=?", (request.execution_receipt_id,),
        ).fetchone()
        if existing is not None:
            if existing[0] != job_id or bytes(existing[1]) != payload:
                raise FeedbackError("failure_review_enqueue_conflict")
            connection.commit()
            return True
        connection.execute(
            "INSERT INTO executor_failure_review_queue VALUES(?,?,?,?)",
            (job_id, request.execution_receipt_id, sqlite3.Binary(payload), created_at),
        )
        connection.commit()
        return True
    except sqlite3.IntegrityError as exc:
        raise FeedbackError("failure_review_enqueue_conflict") from exc
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def apply_negative_feedback(
    receipt: ExecutionReceipt, *, failure_evidence_hash: str, error_code: str,
    quarantine_exact: Callable[[ContractId, str], QuarantineCAS],
    enqueue_idempotent: Callable[[str, FailureReviewRequest], bool],
) -> FeedbackResult:
    """Quarantine exact generation first, then enqueue its idempotent review.

    ``quarantine_exact`` must atomically compare the current generation with
    the supplied generation and make that exact generation unselectable.  A
    retry may return ``ALREADY_QUARANTINED``; it will retry only the enqueue.
    """
    if not isinstance(receipt, ExecutionReceipt):
        raise FeedbackError("feedback_binding_invalid", "execution_receipt")
    _validate_execution_receipt(receipt)
    failure_hash = _digest(failure_evidence_hash, "failure_evidence_hash")
    code = _text(error_code, "error_code", 128)
    cas = quarantine_exact(receipt.contract_id, receipt.generation_id)
    if not isinstance(cas, QuarantineCAS):
        raise FeedbackError("feedback_binding_invalid", "quarantine result")
    if cas is QuarantineCAS.STALE:
        return FeedbackResult(FeedbackStatus.STALE_FEEDBACK, receipt.receipt_id, None, False)
    request = FailureReviewRequest(
        execution_receipt_id=receipt.receipt_id,
        execution_receipt_hash=_hash(EXECUTION_RECEIPT_DOMAIN + b"record\0",
                                     _receipt_payload(receipt, omit_receipt_id=False)),
        candidate_id=receipt.candidate_id,
        generation_id=receipt.generation_id,
        failure_evidence_hash=failure_hash,
        error_code=code,
        reduced_arguments=receipt.arguments,
        reduced_output=receipt.reduced_output,
    )
    job_id = failure_job_id(receipt.receipt_id)
    try:
        queued = enqueue_idempotent(job_id, request)
    except Exception:
        # Quarantine has already committed.  Preserve the retryable job key and
        # do not disguise the state as stale or roll the generation back.
        queued = False
    return FeedbackResult(
        FeedbackStatus.QUARANTINED if queued else FeedbackStatus.ENQUEUE_FAILED,
        receipt.receipt_id, job_id, cas is QuarantineCAS.APPLIED,
    )


def apply_step_negative_feedback(
    step: object, *, failure_evidence_hash: str, error_code: str,
    quarantine_exact: Callable[[ContractId, str], QuarantineCAS],
    enqueue_idempotent: Callable[[str, FailureReviewRequest], bool],
) -> FeedbackResult:
    """Consume only the typed receipt retained by the exact failed StepLog.

    Legacy, incomplete and forged step shapes are rejected before either
    mutating callback is reachable.
    """
    receipt = getattr(step, "execution_receipt", None)
    if not isinstance(receipt, ExecutionReceipt):
        raise FeedbackError("feedback_binding_invalid", "execution_receipt")
    return apply_negative_feedback(
        receipt,
        failure_evidence_hash=failure_evidence_hash,
        error_code=error_code,
        quarantine_exact=quarantine_exact,
        enqueue_idempotent=enqueue_idempotent,
    )


@dataclass(frozen=True, slots=True)
class RepairBirthRequest:
    """Clean typed input for a future Birth run; never executable or published."""

    request_id: str
    objective: str
    predecessor_candidate_id: str
    source_execution_receipt_id: str


def repair_birth_request(review: FailureReview) -> RepairBirthRequest:
    if (not isinstance(review, FailureReview)
            or review.verdict is not FailureReviewVerdict.REPAIRABLE
            or review.repair_objective is None):
        raise FeedbackError("repair_request_invalid")
    payload = {
        "objective": review.repair_objective,
        "predecessor_candidate_id": review.candidate_id,
        "source_execution_receipt_id": review.execution_receipt_id,
    }
    return RepairBirthRequest(
        _hash(REPAIR_REQUEST_DOMAIN, payload), review.repair_objective,
        review.candidate_id, review.execution_receipt_id,
    )
