"""The exact-execution review outbox has a bounded, idempotent consumer."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import executor_birth_activation_mode as mode
import executor_birth_failure_review as review_module
import executor_birth_feedback as feedback
from executor_birth_failure_review import (
    FailureReview, FailureReviewDecision, FailureReviewError,
    FailureReviewRequest, FailureReviewVerdict,
)
from jobs import birth_failure_reviews as job


def digest(label: str) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def request(label: str = "one") -> FailureReviewRequest:
    return FailureReviewRequest(
        execution_receipt_id=digest("receipt " + label),
        execution_receipt_hash=digest("record " + label),
        candidate_id=digest("candidate " + label),
        generation_id=digest("generation " + label),
        failure_evidence_hash=digest("evidence " + label),
        error_code="user_feedback_error",
        reduced_arguments={"pattern": "*"},
        reduced_output={"ok": False},
    )


def enqueue(db_path: Path, value: FailureReviewRequest) -> str:
    job_id = feedback.failure_job_id(value.execution_receipt_id)
    feedback.enqueue_failure_review_inactive(
        job_id, value, created_at="2026-09-16T10:00:00Z", db_path=db_path)
    return job_id


def decision(value: FailureReviewRequest, verdict=FailureReviewVerdict.MISALIGNED):
    return FailureReviewDecision(
        FailureReview(
            verdict, value.execution_receipt_id, value.execution_receipt_hash,
            value.candidate_id, value.generation_id, value.failure_evidence_hash,
            "reduced output does not match the declared contract", None, 80,
        ),
        digest("decision evidence"), review_module.WORKLOAD, "frontier",
    )


def rows(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        return [row[0] for row in connection.execute(
            "SELECT job_id FROM executor_failure_review_queue ORDER BY job_id")]


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    path = tmp_path / "failure_reviews.sqlite"
    path.touch()
    monkeypatch.setattr(job, "_outbox_path", lambda: path)
    monkeypatch.setattr(job, "_frontier_consent", lambda: True)
    return path


def test_an_unmigrated_installation_reports_no_outbox(monkeypatch):
    monkeypatch.setattr(mode, "read_birth_activation_state", lambda: mode.BirthActivationState(
        mode.BirthStateOwner.LEGACY, None, None, None))
    result = job.task_birth_failure_reviews()
    assert result == {"ok": True, "reviewed": 0, "exhausted": 0, "deferred": 0,
                      "pending": 0, "verdicts": {}, "status": "no_outbox"}


def test_the_enqueued_request_is_read_back_exactly(outbox):
    original = request()
    job_id = enqueue(outbox, original)
    assert feedback.pending_failure_reviews(db_path=outbox, limit=10) == (
        (job_id, original),)


def test_reviewing_retires_the_job_and_counts_its_verdict(outbox, monkeypatch):
    original = request()
    enqueue(outbox, original)
    seen = {}

    def review_once(value, *, consent_valid, db_path):
        seen.update(request=value, consent=consent_valid, path=db_path)
        return decision(value)

    monkeypatch.setattr(review_module, "review_failure_once", review_once)
    result = job.task_birth_failure_reviews()
    assert result["reviewed"] == 1 and result["verdicts"] == {"misaligned": 1}
    assert seen["request"] == original and seen["consent"] is True
    assert rows(outbox) == []
    # A second pass finds nothing left and stays honest about it.
    assert job.task_birth_failure_reviews()["pending"] == 0


def test_without_the_frontier_opt_in_the_backlog_is_preserved(outbox, monkeypatch):
    enqueue(outbox, request())
    monkeypatch.setattr(job, "_frontier_consent", lambda: False)
    monkeypatch.setattr(review_module, "review_failure_once",
                        lambda *_a, **_k: pytest.fail("reviewed without consent"))
    result = job.task_birth_failure_reviews()
    assert result["status"] == "consent_absent"
    assert result["deferred"] == 1 and result["reviewed"] == 0
    assert len(rows(outbox)) == 1


def test_an_unavailable_service_defers_without_losing_the_job(outbox, monkeypatch):
    enqueue(outbox, request())
    monkeypatch.setattr(review_module, "review_failure_once", lambda *_a, **_k: (
        (_ for _ in ()).throw(FailureReviewError("failure_review_unavailable", "TimeoutError"))))
    result = job.task_birth_failure_reviews()
    assert result["deferred"] == 1 and result["last_error"] == "failure_review_unavailable"
    assert len(rows(outbox)) == 1


def test_a_consumed_attempt_retires_its_job_instead_of_promising_a_retry(outbox, monkeypatch):
    enqueue(outbox, request())
    monkeypatch.setattr(review_module, "review_failure_once", lambda *_a, **_k: (
        (_ for _ in ()).throw(FailureReviewError("failure_review_already_attempted"))))
    result = job.task_birth_failure_reviews()
    assert result["exhausted"] == 1 and result["reviewed"] == 0
    assert rows(outbox) == []


def test_one_damaged_row_is_reported_and_never_reviewed(outbox, monkeypatch):
    enqueue(outbox, request())
    with sqlite3.connect(outbox) as connection:
        connection.execute(
            "UPDATE executor_failure_review_queue SET request_json=?",
            (sqlite3.Binary(json.dumps({"unexpected": True}).encode()),))
    monkeypatch.setattr(review_module, "review_failure_once",
                        lambda *_a, **_k: pytest.fail("reviewed a damaged row"))
    result = job.task_birth_failure_reviews()
    assert result == {"ok": False, "error_class": "failure_review_queue_corrupt",
                      "error": "fields"}
    assert len(rows(outbox)) == 1


def test_the_batch_is_bounded_and_ordered(outbox, monkeypatch):
    for index in range(4):
        enqueue(outbox, request(str(index)))
    monkeypatch.setattr(job, "REVIEW_LIMIT", 2)
    monkeypatch.setattr(review_module, "review_failure_once",
                        lambda value, **_k: decision(value))
    assert job.task_birth_failure_reviews()["reviewed"] == 2
    assert len(rows(outbox)) == 2


def test_retiring_a_finished_job_twice_is_not_an_error(outbox):
    job_id = enqueue(outbox, request())
    assert feedback.resolve_failure_review_job(job_id, db_path=outbox) is True
    assert feedback.resolve_failure_review_job(job_id, db_path=outbox) is False
