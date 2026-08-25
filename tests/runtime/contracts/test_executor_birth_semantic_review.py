from __future__ import annotations

import json

import pytest

import executor_birth_semantic_review as semantic
from executor_birth_semantic_review import (
    EvidenceStatus,
    IndependentEvidence,
    IndependentEvidenceKind,
    ReviewPolicyV1,
    SemanticReviewError,
    SemanticReviewRequest,
    SemanticVerdict,
    review_candidate_semantics,
    validate_semantic_review,
)
from llm_workloads import WORKLOADS


D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
D4 = "sha256:" + "4" * 64


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _review(**changes) -> str:
    value = {
        "verdict": "aligned", "observed_effects": ["reads declared input"],
        "undeclared_effects": [], "reason": "Code matches the contract.",
        "tests": [{"test_id": "zero", "kind": "example", "description": "Empty input."}],
        "confidence": 91,
    }
    value.update(changes)
    return _canonical(value)


def _request() -> SemanticReviewRequest:
    return SemanticReviewRequest(
        D1, D2, b'name="sample"\n', b"{}\n",
        {"pkg/helper.py": b"HELPER", "main.py": b"MAIN"},
    )


def _policy() -> ReviewPolicyV1:
    return ReviewPolicyV1(
        versions={kind: frozenset({"v1"}) for kind in IndependentEvidenceKind},
        owners={kind: frozenset({f"owner:{kind.value}"}) for kind in IndependentEvidenceKind},
    )


def _evidence(**changes) -> IndependentEvidence:
    values = {
        "evidence_id": D3, "evidence_version": "v1",
        "kind": IndependentEvidenceKind.DETERMINISTIC_ORACLE,
        "owner_id": "owner:deterministic_oracle", "candidate_id": D1,
        "admission_context_id": D2, "status": EvidenceStatus.PASSED,
        "evidence_hash": D4,
    }
    values.update(changes)
    return IndependentEvidence(**values)


def test_workload_is_fixed_wise_json() -> None:
    contract = WORKLOADS["executor.birth.semantic_review"]
    assert contract.tier == "wise"
    assert contract.output_constraint == "json"


def test_strict_review_round_trip() -> None:
    result = validate_semantic_review(_review())
    assert result.verdict is SemanticVerdict.ALIGNED
    assert result.observed_effects == ("reads declared input",)
    assert result.tests[0].test_id == "zero"


@pytest.mark.parametrize(
    "payload",
    [
        lambda: _review() + "\n",
        lambda: "prefix" + _review(),
        lambda: _review(extra=True),
        lambda: _canonical({"verdict": "aligned"}),
        lambda: _review(confidence=True),
        lambda: _review(confidence=101),
        lambda: _review(verdict="other"),
        lambda: _review(observed_effects=[]),
        lambda: _review(undeclared_effects=["network"]),
        lambda: _review(reason=""),
        lambda: _review(reason="x\x00y"),
        lambda: _review(observed_effects=["x" * 257]),
        lambda: _review(observed_effects=[str(index) for index in range(33)]),
        lambda: _review(tests=[{"test_id": "x", "kind": "shell", "description": "bad"}]),
        lambda: _review(tests=[
            {"test_id": "x", "kind": "example", "description": "one"},
            {"test_id": "x", "kind": "example", "description": "two"},
        ]),
    ],
)
def test_malformed_reviews_are_rejected(payload) -> None:
    with pytest.raises(SemanticReviewError, match="semantic_review_failed"):
        validate_semantic_review(payload())


def test_duplicate_json_key_and_non_utf8_are_rejected() -> None:
    duplicate = _review().replace('"verdict":"aligned"', '"verdict":"aligned","verdict":"uncertain"')
    with pytest.raises(SemanticReviewError, match="duplicate"):
        validate_semantic_review(duplicate)
    with pytest.raises(SemanticReviewError):
        validate_semantic_review(b"\xff")


def test_aligned_requires_exact_independent_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(semantic, "_invoke_semantic_review", lambda system, user, **kw: (
        calls.append((system, user, kw)) or _review()
    ))
    without = review_candidate_semantics(
        _request(), independent_evidence=(), policy=_policy(),
    )
    assert without.review.verdict is SemanticVerdict.ALIGNED
    assert without.operational_verdict is SemanticVerdict.UNCERTAIN
    assert without.independent_evidence_id is None

    with_evidence = review_candidate_semantics(
        _request(), independent_evidence=(_evidence(),), policy=_policy(),
    )
    assert with_evidence.operational_verdict is SemanticVerdict.ALIGNED
    assert with_evidence.independent_evidence_id == D3
    assert with_evidence.review_evidence_hash.startswith("sha256:")
    assert all(call[2]["tier"] == "wise" for call in calls)
    assert all(call[2]["timeout_s"] == semantic.REQUEST_TIMEOUT_S for call in calls)


@pytest.mark.parametrize(
    "change",
    [
        {"candidate_id": D4},
        {"admission_context_id": D4},
        {"evidence_version": "v2"},
        {"owner_id": "model:generator"},
    ],
)
def test_obsolete_or_untrusted_evidence_is_rejected(
    monkeypatch: pytest.MonkeyPatch, change: dict,
) -> None:
    monkeypatch.setattr(semantic, "_invoke_semantic_review", lambda *a, **k: _review())
    with pytest.raises(SemanticReviewError, match="evidence_obsolete"):
        review_candidate_semantics(
            _request(), independent_evidence=(_evidence(**change),), policy=_policy(),
        )


@pytest.mark.parametrize("status", [EvidenceStatus.FAILED, EvidenceStatus.UNAVAILABLE, EvidenceStatus.NOT_APPLICABLE])
def test_nonpassing_evidence_does_not_make_model_sufficient(
    monkeypatch: pytest.MonkeyPatch, status: EvidenceStatus,
) -> None:
    monkeypatch.setattr(semantic, "_invoke_semantic_review", lambda *a, **k: _review())
    result = review_candidate_semantics(
        _request(), independent_evidence=(_evidence(status=status),), policy=_policy(),
    )
    assert result.operational_verdict is SemanticVerdict.UNCERTAIN


def test_retry_once_only_for_malformed_same_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter(["not-json", _review()])
    calls = []

    def invoke(system, user, **kwargs):
        calls.append((system, user, kwargs))
        return next(replies)

    monkeypatch.setattr(semantic, "_invoke_semantic_review", invoke)
    result = review_candidate_semantics(
        _request(), independent_evidence=(_evidence(),), policy=_policy(),
    )
    assert result.operational_verdict is SemanticVerdict.ALIGNED
    assert len(calls) == 2
    assert calls[0] == calls[1]


def test_two_malformed_payloads_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def invoke(*args, **kwargs):
        nonlocal calls
        calls += 1
        return "{}"

    monkeypatch.setattr(semantic, "_invoke_semantic_review", invoke)
    with pytest.raises(SemanticReviewError, match="semantic_review_failed"):
        review_candidate_semantics(_request(), independent_evidence=(_evidence(),), policy=_policy())
    assert calls == 2


def test_transport_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def invoke(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("offline")

    monkeypatch.setattr(semantic, "_invoke_semantic_review", invoke)
    with pytest.raises(SemanticReviewError, match="semantic_review_unavailable"):
        review_candidate_semantics(_request(), independent_evidence=(_evidence(),), policy=_policy())
    assert calls == 1


def test_entire_ordered_candidate_is_sent_as_untrusted_data(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def invoke(system, user, **kwargs):
        captured.update(system=system, payload=json.loads(user), kwargs=kwargs)
        return _review()

    monkeypatch.setattr(semantic, "_invoke_semantic_review", invoke)
    review_candidate_semantics(_request(), independent_evidence=(_evidence(),), policy=_policy())
    assert list(captured["payload"]["code_files"]) == ["main.py", "pkg/helper.py"]
    assert captured["payload"]["candidate_id"] == D1
    assert "untrusted data" in captured["system"]


def test_misaligned_is_not_overridden_by_independent_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(semantic, "_invoke_semantic_review", lambda *a, **k: _review(
        verdict="misaligned", observed_effects=[], undeclared_effects=["network"],
        reason="Undeclared network effect.",
    ))
    result = review_candidate_semantics(
        _request(), independent_evidence=(_evidence(),), policy=_policy(),
    )
    assert result.operational_verdict is SemanticVerdict.MISALIGNED
