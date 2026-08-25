from dataclasses import asdict

import pytest

from agent_runtime import StepLog
from executor_birth_failure_review import FailureReview, FailureReviewVerdict
from executor_birth_feedback import (
    FeedbackError, FeedbackStatus, QuarantineCAS, RepairBirthRequest,
    apply_negative_feedback, enqueue_failure_review_inactive,
    make_execution_receipt, repair_birth_request,
)
from manifest_inventory import ContractId, ManifestOrigin


D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
D4 = "sha256:" + "4" * 64
CID = ContractId(ManifestOrigin.USER, "demo/manifest.toml")


def receipt(*, generation=D3):
    return make_execution_receipt(
        request_id="request-1", turn_id="turn-1", reduced_query_ref=D1,
        arguments={"needle": "safe"}, reduced_output={"ok": False},
        contract_id=CID, executor_name="demo", candidate_id=D2,
        generation_id=generation, dispatched_at="2030-01-01T00:00:00Z",
    )


def test_execution_receipt_is_typed_in_step_log_and_serializable():
    value = receipt()
    step = StepLog(step_num=1, execution_receipt=value)
    encoded = asdict(step)["execution_receipt"]
    assert encoded["receipt_id"] == value.receipt_id
    assert encoded["contract_id"]["origin"] == ManifestOrigin.USER
    assert encoded["contract_id"]["relative_manifest"] == "demo/manifest.toml"


def test_execution_receipt_rejects_tampered_retained_payload():
    value = receipt()
    with pytest.raises(FeedbackError, match="arguments_hash"):
        type(value)(
            value.schema_version, value.receipt_id, value.request_id, value.turn_id,
            value.reduced_query_ref, value.arguments_hash, {"needle": "changed"},
            value.output_hash, value.reduced_output, value.contract_id,
            value.executor_name, value.candidate_id, value.generation_id,
            value.dispatched_at,
        )


def test_mutated_receipt_is_rejected_before_quarantine():
    value = receipt()
    value.arguments["needle"] = "changed"
    calls = []
    with pytest.raises(FeedbackError, match="feedback_binding_invalid"):
        apply_negative_feedback(
            value, failure_evidence_hash=D4, error_code="wrong_result",
            quarantine_exact=lambda *_: calls.append("quarantine") or QuarantineCAS.APPLIED,
            enqueue_idempotent=lambda *_: True,
        )
    assert calls == []


def test_feedback_for_a_after_b_is_stale_and_mutates_nothing():
    events = []

    def quarantine(_contract, generation):
        events.append(("cas", generation))
        return QuarantineCAS.STALE

    def enqueue(_key, _request):
        events.append(("enqueue",))
        return True

    result = apply_negative_feedback(
        receipt(generation=D3), failure_evidence_hash=D4, error_code="wrong_result",
        quarantine_exact=quarantine, enqueue_idempotent=enqueue,
    )
    assert result.status is FeedbackStatus.STALE_FEEDBACK
    assert result.failure_job_id is None
    assert events == [("cas", D3)]


def test_quarantine_precedes_idempotent_failure_review_enqueue():
    events = []
    jobs = set()

    def quarantine(_contract, generation):
        events.append(("quarantine", generation))
        return QuarantineCAS.APPLIED

    def enqueue(key, request):
        events.append(("enqueue", key, request.generation_id))
        jobs.add(key)
        return True

    result = apply_negative_feedback(
        receipt(), failure_evidence_hash=D4, error_code="wrong_result",
        quarantine_exact=quarantine, enqueue_idempotent=enqueue,
    )
    assert result.status is FeedbackStatus.QUARANTINED
    assert result.quarantine_applied
    assert events[0][0] == "quarantine" and events[1][0] == "enqueue"
    assert jobs == {result.failure_job_id}


def test_enqueue_failure_leaves_quarantine_and_retry_is_repeatable():
    lifecycle = {"value": "active"}
    attempts = []
    jobs = set()

    def quarantine(_contract, _generation):
        if lifecycle["value"] == "active":
            lifecycle["value"] = "quarantined"
            return QuarantineCAS.APPLIED
        return QuarantineCAS.ALREADY_QUARANTINED

    def enqueue(key, _request):
        attempts.append(key)
        if len(attempts) == 1:
            return False
        jobs.add(key)
        return True

    first = apply_negative_feedback(
        receipt(), failure_evidence_hash=D4, error_code="wrong_result",
        quarantine_exact=quarantine, enqueue_idempotent=enqueue,
    )
    assert first.status is FeedbackStatus.ENQUEUE_FAILED
    assert lifecycle["value"] == "quarantined"
    second = apply_negative_feedback(
        receipt(), failure_evidence_hash=D4, error_code="wrong_result",
        quarantine_exact=quarantine, enqueue_idempotent=enqueue,
    )
    assert second.status is FeedbackStatus.QUARANTINED
    assert not second.quarantine_applied
    assert first.failure_job_id == second.failure_job_id
    assert jobs == {second.failure_job_id}


def test_enqueue_exception_is_reported_after_quarantine_and_can_be_retried():
    states = []
    result = apply_negative_feedback(
        receipt(), failure_evidence_hash=D4, error_code="wrong_result",
        quarantine_exact=lambda *_: states.append("quarantined") or QuarantineCAS.APPLIED,
        enqueue_idempotent=lambda *_: (_ for _ in ()).throw(OSError("offline")),
    )
    assert result.status is FeedbackStatus.ENQUEUE_FAILED
    assert result.failure_job_id is not None
    assert states == ["quarantined"]


def test_inactive_queue_is_exactly_idempotent(tmp_path):
    captured = []
    result = apply_negative_feedback(
        receipt(), failure_evidence_hash=D4, error_code="wrong_result",
        quarantine_exact=lambda _cid, _gid: QuarantineCAS.APPLIED,
        enqueue_idempotent=lambda key, request: captured.append((key, request)) or True,
    )
    key, request = captured[0]
    db = tmp_path / "feedback.sqlite"
    assert enqueue_failure_review_inactive(
        key, request, created_at="2030-01-01T00:00:01Z", db_path=db)
    assert enqueue_failure_review_inactive(
        key, request, created_at="2030-01-01T00:00:02Z", db_path=db)
    assert result.failure_job_id == key


def test_repair_review_returns_clean_typed_birth_request_only():
    review = FailureReview(
        FailureReviewVerdict.REPAIRABLE, receipt().receipt_id, D1, D2, D3, D4,
        "The observed result does not match", "Clarify the empty result behavior", 90,
    )
    result = repair_birth_request(review)
    assert isinstance(result, RepairBirthRequest)
    assert result.predecessor_candidate_id == D2
    assert result.source_execution_receipt_id == receipt().receipt_id
    assert set(asdict(result)) == {
        "request_id", "objective", "predecessor_candidate_id",
        "source_execution_receipt_id",
    }
    with pytest.raises(FeedbackError, match="repair_request_invalid"):
        repair_birth_request(FailureReview(
            FailureReviewVerdict.MISALIGNED, receipt().receipt_id, D1, D2, D3, D4,
            "Mismatch", None, 90,
        ))
