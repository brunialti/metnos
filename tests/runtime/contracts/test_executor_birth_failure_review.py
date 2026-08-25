import json

import pytest

import executor_birth_failure_review as subject


D = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64


def request():
    return subject.FailureReviewRequest(
        D, D2, D, "generation-1", D2, "executor_failed",
        {"query": "reduced"}, {"error": "reduced"},
    )


def encoded(**changes):
    value = {
        "verdict": "uncertain", "execution_receipt_id": D,
        "execution_receipt_hash": D2, "candidate_id": D,
        "generation_id": "generation-1", "failure_evidence_hash": D2,
        "reason": "The reduced evidence is insufficient.",
        "repair_objective": None, "confidence": 40,
    }
    value.update(changes)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def test_validator_requires_exact_canonical_schema_and_rejects_duplicates():
    assert subject.validate_failure_review(encoded()).verdict is subject.FailureReviewVerdict.UNCERTAIN
    with pytest.raises(subject.FailureReviewError, match="canonical"):
        subject.validate_failure_review(encoded() + "\n")
    duplicate = encoded().replace('"verdict":', '"verdict":"uncertain","verdict":', 1)
    with pytest.raises(subject.FailureReviewError, match="duplicate"):
        subject.validate_failure_review(duplicate)


def test_repair_objective_is_only_allowed_for_repairable_and_is_non_executable():
    with pytest.raises(subject.FailureReviewError, match="unexpected"):
        subject.validate_failure_review(encoded(repair_objective="Clarify expected result"))
    ok = subject.validate_failure_review(encoded(
        verdict="repairable", repair_objective="Clarify the empty-result behavior"))
    assert ok.repair_objective
    with pytest.raises(subject.FailureReviewError, match="repair_objective"):
        subject.validate_failure_review(encoded(
            verdict="repairable", repair_objective="run bash publish /tmp/fix"))


def test_consent_is_required_before_any_model_invocation():
    called = []
    with pytest.raises(subject.FailureReviewError, match="consent_required"):
        subject.review_failure(request(), consent_valid=False,
                               _invoke_review=lambda *a, **k: called.append(1))
    assert called == []


def test_no_retry_on_malformed_or_transport_failure(monkeypatch):
    monkeypatch.setattr(subject, "tier_for", lambda _workload: "frontier")
    calls = []
    def malformed(*_args, **_kwargs):
        calls.append(1)
        return "{}"
    with pytest.raises(subject.FailureReviewError, match="failure_review_failed"):
        subject.review_failure(request(), consent_valid=True, _invoke_review=malformed)
    assert len(calls) == 1

    calls.clear()
    def unavailable(*_args, **_kwargs):
        calls.append(1)
        raise TimeoutError("down")
    with pytest.raises(subject.FailureReviewError, match="failure_review_unavailable"):
        subject.review_failure(request(), consent_valid=True, _invoke_review=unavailable)
    assert len(calls) == 1


def test_exact_execution_binding_and_evidence_hash(monkeypatch):
    monkeypatch.setattr(subject, "tier_for", lambda _workload: "frontier")
    decision = subject.review_failure(
        request(), consent_valid=True,
        _invoke_review=lambda *_a, **_k: encoded(),
    )
    assert decision.workload == subject.WORKLOAD
    assert decision.evidence_hash.startswith("sha256:")
    with pytest.raises(subject.FailureReviewError, match="binding_invalid"):
        subject.review_failure(
            request(), consent_valid=True,
            _invoke_review=lambda *_a, **_k: encoded(generation_id="generation-2"),
        )


def test_persistent_review_is_exactly_once_and_replayable(tmp_path):
    calls = 0
    def invoke(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return encoded()
    db = tmp_path / "failure-reviews.sqlite"
    first = subject.review_failure_once(request(), consent_valid=True, db_path=db,
                                        _invoke_review=invoke)
    replay = subject.review_failure_once(
        request(), consent_valid=True, db_path=db,
        _invoke_review=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retried")),
    )
    assert replay == first
    assert calls == 1


def test_failed_attempt_is_durably_consumed(tmp_path):
    db = tmp_path / "failure-reviews.sqlite"
    with pytest.raises(subject.FailureReviewError, match="unavailable"):
        subject.review_failure_once(
            request(), consent_valid=True, db_path=db,
            _invoke_review=lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError()),
        )
    with pytest.raises(subject.FailureReviewError, match="already_attempted"):
        subject.review_failure_once(request(), consent_valid=True, db_path=db,
                                    _invoke_review=lambda *_a, **_k: encoded())


def test_missing_consent_does_not_consume_persistent_attempt(tmp_path):
    db = tmp_path / "failure-reviews.sqlite"
    with pytest.raises(subject.FailureReviewError, match="consent_required"):
        subject.review_failure_once(request(), consent_valid=False, db_path=db,
                                    _invoke_review=lambda *_a, **_k: encoded())
    assert not db.exists()


def test_persistent_review_rejects_changed_binding(tmp_path):
    db = tmp_path / "failure-reviews.sqlite"
    subject.review_failure_once(request(), consent_valid=True, db_path=db,
                                _invoke_review=lambda *_a, **_k: encoded())
    original = request()
    changed = subject.FailureReviewRequest(
        original.execution_receipt_id, original.execution_receipt_hash,
        original.candidate_id, original.generation_id, D,
        original.error_code, original.reduced_arguments, original.reduced_output,
    )
    with pytest.raises(subject.FailureReviewError, match="binding_invalid"):
        subject.review_failure_once(changed, consent_valid=True, db_path=db,
                                    _invoke_review=lambda *_a, **_k: encoded())
