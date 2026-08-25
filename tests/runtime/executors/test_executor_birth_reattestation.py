from __future__ import annotations

from dataclasses import replace

import pytest

from executor_birth_cutover import CurrentGeneration
from executor_birth_reattestation import (
    BirthReattestationError, ReattestationRequest,
    _reattest_current_for_test, _sealed_reattestation_core_for_test,
)
from executor_birth_receipts import (
    AdmissionKind, RevisionClass, issue_admission_receipt,
    verify_admission_receipt,
)
from executor_birth_shadow import BirthOutcome, BirthReport, RevisionClass as ShadowRevisionClass
from executor_birth_snapshot import acquire_candidate_snapshot

from test_executor_birth_operational import _fixture


GENERATION = "sha256:" + "a" * 64


class Rig:
    def __init__(self, birth, source):
        self.birth = birth
        self.source = source
        self.receipt = None
        self.capture_error = None
        self.read_error = None
        self.persist_error = None
        self.persist_calls = 0

    def capture(self, _current):
        if self.capture_error:
            raise BirthReattestationError(self.capture_error)
        return acquire_candidate_snapshot(self.source)

    def read(self, _current):
        if self.read_error:
            raise BirthReattestationError(self.read_error)
        return self.receipt

    def persist(self, _current, encoded, expected):
        self.persist_calls += 1
        if self.persist_error:
            raise BirthReattestationError(self.persist_error)
        receipt = verify_admission_receipt(
            encoded, verifier_keys=self.birth.admission_verifier_keys,
        )
        for field, wanted in expected.items():
            actual = getattr(receipt, field)
            assert getattr(actual, "value", actual) == getattr(wanted, "value", wanted)
        if self.receipt is not None and self.receipt != encoded:
            raise BirthReattestationError("birth_reattestation_receipt_conflict")
        self.receipt = encoded
        return encoded

    def core(self):
        return _sealed_reattestation_core_for_test(
            birth=self.birth, capture=self.capture,
            persist=self.persist, read_receipt=self.read,
        )


def prepared(tmp_path):
    original, birth = _fixture(tmp_path, lambda *_args, **_kwargs: None)
    current = CurrentGeneration(original.manifest_ref, GENERATION)
    request = ReattestationRequest(
        original.request_id, current, original.producer_receipt,
        "cutover", "reattest authenticated current",
    )
    rig = Rig(birth, original.candidate_source_root)
    return request, rig


def test_reattests_exact_generation_without_publication_and_durable_reread(tmp_path):
    request, rig = prepared(tmp_path)
    result = _reattest_current_for_test(request, _core=rig.core())
    receipt = verify_admission_receipt(
        result.receipt, verifier_keys=rig.birth.admission_verifier_keys,
    )
    assert result.generation_id == GENERATION
    assert result.repeated is False
    assert receipt.kind is AdmissionKind.REATTESTATION
    assert receipt.revision_class is RevisionClass.REATTESTATION
    assert receipt.generation_id == GENERATION
    assert receipt.predecessor_id == GENERATION
    assert receipt.birth_request_id == request.request_id
    assert "reattestation_current_generation_v1" in receipt.check_results
    assert rig.persist_calls == 1


def test_missing_or_unreadable_current_fails_before_receipt_claim(tmp_path):
    request, rig = prepared(tmp_path)
    rig.capture_error = "birth_reattestation_current_changed"
    with pytest.raises(BirthReattestationError, match="current_changed"):
        _reattest_current_for_test(request, _core=rig.core())
    rig.capture_error = None
    result = _reattest_current_for_test(request, _core=rig.core())
    assert result.receipt == rig.receipt


def test_producer_receipt_is_unrepeatable_by_another_request(tmp_path):
    request, rig = prepared(tmp_path)
    _reattest_current_for_test(request, _core=rig.core())
    other = replace(request, request_id="sha256:" + "b" * 64)
    with pytest.raises(BirthReattestationError, match="producer_receipt_replay"):
        _reattest_current_for_test(other, _core=rig.core())


def test_failed_applicable_check_writes_no_receipt(tmp_path, monkeypatch):
    request, rig = prepared(tmp_path)
    monkeypatch.setattr(
        "executor_birth_reattestation._observe_birth_for_test",
        lambda *_args, **_kwargs: BirthReport(
            1, request.current.ref.contract_id, None, None, None,
            ShadowRevisionClass.REATTESTATION, (), (), BirthOutcome.REJECTED,
            "check_unavailable",
        ),
    )
    with pytest.raises(BirthReattestationError):
        _reattest_current_for_test(request, _core=rig.core())
    assert rig.receipt is None
    assert rig.persist_calls == 0


def test_current_change_at_persistence_fails_closed_and_writes_nothing(tmp_path):
    request, rig = prepared(tmp_path)
    rig.persist_error = "birth_reattestation_current_changed"
    with pytest.raises(BirthReattestationError, match="current_changed"):
        _reattest_current_for_test(request, _core=rig.core())
    assert rig.receipt is None


def test_current_change_during_durable_reread_is_not_success(tmp_path):
    request, rig = prepared(tmp_path)
    reads = 0
    original = rig.read
    def changing(current):
        nonlocal reads
        reads += 1
        if reads >= 2:
            raise BirthReattestationError("birth_reattestation_current_changed")
        return original(current)
    rig.read = changing
    with pytest.raises(BirthReattestationError, match="current_changed"):
        _reattest_current_for_test(request, _core=rig.core())


def test_exact_retry_is_idempotent_and_does_not_persist_again(tmp_path):
    request, rig = prepared(tmp_path)
    first = _reattest_current_for_test(request, _core=rig.core())
    second = _reattest_current_for_test(request, _core=rig.core())
    assert second.receipt == first.receipt
    assert second.repeated is True
    assert rig.persist_calls == 1


def test_error_after_durable_replace_keeps_claim_recoverable(tmp_path):
    request, rig = prepared(tmp_path)
    original = rig.persist
    failed_once = False

    def persisted_then_failed(current, encoded, expected):
        nonlocal failed_once
        durable = original(current, encoded, expected)
        if not failed_once:
            failed_once = True
            raise BirthReattestationError("simulated_post_replace_failure")
        return durable

    rig.persist = persisted_then_failed
    with pytest.raises(BirthReattestationError, match="post_replace_failure"):
        _reattest_current_for_test(request, _core=rig.core())

    recovered = _reattest_current_for_test(request, _core=rig.core())
    assert recovered.receipt == rig.receipt
    assert recovered.repeated is True
    assert rig.persist_calls == 1


def test_existing_admission_receipt_is_preserved_and_rejected(tmp_path):
    request, rig = prepared(tmp_path)
    # A valid receipt bound to another request/generation authority must never
    # be overwritten or silently accepted as this reattestation.
    first = _reattest_current_for_test(request, _core=rig.core()).receipt
    parsed = verify_admission_receipt(
        first, verifier_keys=rig.birth.admission_verifier_keys,
    )
    existing = issue_admission_receipt(
        policy_version=parsed.policy_version,
        contract_id=request.current.ref.contract_id,
        generation_id=parsed.generation_id, candidate_id=parsed.candidate_id,
        semantic_core_id=parsed.semantic_core_id,
        admission_context_id=parsed.admission_context_id,
        birth_request_id=parsed.birth_request_id,
        authoring_journal_hash=parsed.authoring_journal_hash,
        predecessor_id=parsed.predecessor_id,
        producer_receipt_hash=parsed.producer_receipt_hash,
        revision_class=RevisionClass.CODE_REVISION,
        check_results=parsed.check_results,
        semantic_review_hash=parsed.semantic_review_hash,
        approval_hash=parsed.approval_hash,
        approved_lifecycle=parsed.approved_lifecycle,
        kind=AdmissionKind.ADMISSION, issued_at=parsed.issued_at,
        key_id=rig.birth.admission_key_id,
        private_key=rig.birth.admission_private_key,
    )
    rig.receipt = existing
    # Use a fresh fixture/claim while retaining the same Birth signing key.
    (tmp_path / "fresh").mkdir()
    fresh_request, fresh_rig = prepared(tmp_path / "fresh")
    fresh_rig.birth = replace(
        fresh_rig.birth,
        admission_private_key=rig.birth.admission_private_key,
        admission_verifier_keys=rig.birth.admission_verifier_keys,
        admission_key_id=rig.birth.admission_key_id,
    )
    fresh_rig.receipt = existing
    with pytest.raises(BirthReattestationError, match="receipt_exists"):
        _reattest_current_for_test(fresh_request, _core=fresh_rig.core())
    assert rig.receipt == existing
    assert fresh_rig.receipt == existing
    assert fresh_rig.persist_calls == 0
