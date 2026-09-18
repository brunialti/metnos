"""Reduce-only proof for exact-execution quarantine through Executor Birth.

This module holds no key and performs no publication. The sealed operational
core supplies the authenticated predecessor and its publisher-owned admission.
"""
from __future__ import annotations

import hashlib
import tomllib

from executor_birth_feedback import ExecutionReceipt, _validate_execution_receipt
from executor_birth_receipts import producer_objective_hash_v1
from executor_birth_shadow import BirthOutcome, BirthReport, CheckResult, CheckStatus, classify_revision


def quarantine_reason(execution: ExecutionReceipt) -> str:
    if type(execution) is not ExecutionReceipt:
        raise ValueError("birth_quarantine_execution_invalid")
    _validate_execution_receipt(execution)
    return f"quarantine execution {execution.receipt_id}"


def _validate_quarantine_request(request, producer, execution, key_ids) -> None:
    reason = quarantine_reason(execution)
    if (producer.issuer_id != "promoter"
            or producer.authentication.key_id not in key_ids
            or request.manifest_ref.contract_id != execution.contract_id
            or request.reason != reason or request.approval_refs
            or producer.objective_hash != producer_objective_hash_v1(reason)):
        raise ValueError("birth_quarantine_request_invalid")


def _quarantine_report(request, execution, observed, predecessor, payloads, facts, publisher):
    """Prove that only execution permission is removed; never run suspect code."""
    if predecessor.revision_id != execution.generation_id or predecessor.revision_kind != "generation":
        raise ValueError("birth_quarantine_generation_stale")
    admission, encoded = publisher.authenticate_quarantine_predecessor(execution)
    if admission.admission_context_id != observed.identities.admission_context_id:
        raise ValueError("birth_quarantine_context_changed")
    old = tomllib.loads(payloads["manifest.toml"].decode("utf-8"))
    new = tomllib.loads(observed.snapshot.manifest_bytes.decode("utf-8"))
    if (new.pop("lifecycle", None) != "quarantined"
            or old.pop("lifecycle", "active") not in {"active", "preexercise"}
            or old != new
            or payloads.get("manifest.lang_state.json") != observed.snapshot.language_state_bytes):
        raise ValueError("birth_quarantine_not_reduce_only")
    code = old["code"]
    if set(code["files"]) != set(observed.snapshot.code_files):
        raise ValueError("birth_quarantine_code_changed")
    actual = hashlib.sha256(b"".join(observed.snapshot.code_files[name] for name in code["files"]))
    if "sha256:" + actual.hexdigest() != code["digest"]:
        raise ValueError("birth_quarantine_code_changed")
    # These are references to already authenticated facts, not fresh passes of
    # properties, approvals or semantic review. The new admission signs them.
    evidence = "sha256:" + hashlib.sha256(
        b"metnos.executor-birth.quarantine-proof/v1\0"
        + encoded + execution.receipt_id.encode("ascii")
        + predecessor.snapshot_id.encode("ascii")
    ).hexdigest()
    decision = classify_revision(facts)
    return BirthReport(
        1, request.manifest_ref.contract_id, observed.identities.candidate_id,
        observed.identities.semantic_core_id, observed.identities.admission_context_id,
        decision.revision_class, decision.changed_dimensions,
        (CheckResult("quarantine_exact_revision", "1", CheckStatus.PASSED, None,
                     evidence, "Authenticated unchanged admission; execution authority removed."),
         CheckResult("quarantine_execution", "1", CheckStatus.PASSED, None,
                     execution.receipt_id, "Exact execution receipt.")),
        BirthOutcome.QUARANTINED, None,
    )
