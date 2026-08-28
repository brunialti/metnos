from __future__ import annotations

import inspect
import shutil
import sqlite3
import threading
import tomllib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contract_store import ContractStoreError, PublicationResult
from executor_birth import observe_candidate
from executor_birth_identity import (
    AdmissionContextV1, ContextComponent, ExecutorOrigin, RevisionAuthor,
    admission_context_id,
)
from executor_birth_predecessor import (
    AdmissionContextPin, predecessor_snapshot, revision_facts_id,
)
from executor_birth_operational import (
    BirthRequest, _assemble_birth_runtime_bundle, _birth_executor_for_test,
    _install_birth_runtime_bundle, _runtime_bundle_snapshot, _sealed_core_for_test,
    birth_executor, candidate_source_id,
    approval_scope,
)
import executor_birth_operational as operational
import executor_birth_intent as intent_api
from executor_birth_producer_store import register_producer_receipt
from executor_birth_receipts import (
    IssuerKey, IssuerRegistry, issue_producer_receipt,
)
from executor_birth_shadow import (
    BirthOutcome, BirthReport, RevisionFacts, _assemble_production_dependencies,
    _sealed_dependencies_for_test,
)
from executor_birth_property_runner import PropertyRunResult
from manifest_inventory import ContractId, ManifestOrigin, ManifestRef, ManifestStatus


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
D = "sha256:" + "1" * 64
REQUEST_ID = "sha256:" + "2" * 64


class _AttestedPropertyRunner:
    """Closed unit-test oracle; no host sandbox/backend is involved."""

    def run(self, case, *, fixture_id, isolation):
        count = case.input_value.get("fixture_count", 0)
        limit = case.input_value.get("limit", count)
        size = min(count, limit)
        output = {"entries": [{} for _ in range(size)]}
        observations = {}
        if "fixture_total" in case.expectation:
            observations["fixture_total"] = case.expectation["fixture_total"]
            output["truncated"] = True
        if fixture_id == "private_mutable_state":
            observations.update(
                state_before_hash=D,
                state_after_forward_hash="sha256:" + "8" * 64,
                state_after_undo_hash=D,
            )
        if fixture_id == "private_deletion_tree":
            observations.update(
                filesystem_events=["copy", "delete"],
                source_before_hash=D,
                recovery_copy_hash=D,
            )
        return PropertyRunResult(output, observations, D)


def _context() -> AdmissionContextV1:
    component = ContextComponent("v1", D)
    return AdmissionContextV1(**{
        name: component for name in AdmissionContextV1.__dataclass_fields__
    })


def _candidate(tmp_path: Path) -> Path:
    source = Path("dist/metnos-public/executors/consult_frontier")
    destination = tmp_path / "candidate"
    destination.mkdir()
    manifest = tomllib.loads((source / "manifest.toml").read_text())
    for name in ("manifest.toml", "manifest.lang_state.json", *manifest["code"]["files"]):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    return destination


def _fixture(tmp_path: Path, publisher):
    candidate = _candidate(tmp_path)
    contract_id = ContractId(ManifestOrigin.USER, "demo/manifest.toml")
    context = _context()
    producer_private = Ed25519PrivateKey.generate()
    registry = IssuerRegistry({"human": (IssuerKey(
        "producer-1", producer_private.public_key(),
        frozenset({ExecutorOrigin.HUMAN}), frozenset({RevisionAuthor.HUMAN}),
    ),)})
    observed = observe_candidate(
        candidate, contract_id=contract_id, executor_origin=ExecutorOrigin.HUMAN,
        revision_authorship=RevisionAuthor.HUMAN, objective_hash=D,
        admission_context=context,
    )
    try:
        source_id = candidate_source_id(observed)
    finally:
        observed.close()
    encoded = issue_producer_receipt(
        issuer_id="human", executor_origin=ExecutorOrigin.HUMAN,
        revision_authorship=RevisionAuthor.HUMAN, objective_hash=D,
        candidate_source_id=source_id, issued_at="2026-08-25T12:00:00Z",
        expires_at="2026-08-25T13:00:00Z",
        nonce="0123456789abcdef0123456789abcdef", key_id="producer-1",
        private_key=producer_private,
    )
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=NOW, db_path=db)
    admission_private = Ed25519PrivateKey.generate()
    production = _assemble_production_dependencies()
    shadow = _sealed_dependencies_for_test(
        property_runner=_AttestedPropertyRunner(),
        semantic_authority=production.semantic_authority,
    )
    core = _sealed_core_for_test(
        producer_registry=registry, producer_db=db,
        context_resolver=lambda _request: (
            context, AdmissionContextPin(admission_context_id(context), D),
        ),
        predecessor_resolver=lambda _request: (
            predecessor_snapshot(None, "absent", None), None,
        ),
        context_epoch_resolver=lambda: D,
        shadow_dependencies=shadow, admission_private_key=admission_private,
        admission_public_key=admission_private.public_key(),
        admission_key_id="birth-1", policy_version="birth-policy-v1",
        now=lambda: NOW, publisher=publisher, publisher_options={},
    )
    ref = ManifestRef(
        contract_id, ManifestOrigin.USER, ManifestStatus.ADMITTED,
        candidate, candidate / "manifest.toml", "demo/manifest.toml", (candidate,),
    )
    request = BirthRequest(
        REQUEST_ID, ref, encoded, "operator", "first birth", (),
        "create", candidate,
    )
    return request, core


def test_public_request_cannot_supply_trust_or_publication_authorities():
    assert {
        "checks", "issuer", "verifier", "publisher", "registry",
        "expected_revision_id", "predecessor_snapshot",
    }.isdisjoint(
        inspect.signature(BirthRequest).parameters
    )
    assert "_core" not in inspect.signature(birth_executor).parameters


def test_approval_is_resolved_from_observed_facts_per_request(tmp_path):
    seen = []
    request, core = _fixture(tmp_path, lambda *_args, **_kwargs: None)
    def resolver(actual_request, observed, revision, instant):
        seen.append((actual_request.request_id, observed.identities.candidate_id,
                     approval_scope(observed, revision), instant))
        return None, None
    core = replace(core, approval_resolver=resolver)
    _birth_executor_for_test(request, _core=core)
    assert seen == [(request.request_id, seen[0][1], None, NOW)]


def test_admitted_pipeline_commits_receipt_and_replays_verified_postcondition(tmp_path):
    calls = []

    def publisher(ref, *, expected_generation_id, snapshot, request_id,
                  birth_authorization, **_options):
        calls.append(snapshot)
        assert expected_generation_id is None
        assert birth_authorization.predecessor_id is None
        assert birth_authorization.predecessor_snapshot_id == predecessor_snapshot(
            None, "absent", None,
        ).snapshot_id
        assert birth_authorization.revision_facts_id == revision_facts_id(
            RevisionFacts(first_birth=True),
        )
        assert birth_authorization.context_epoch == D
        assert birth_authorization.context_epoch_resolver() == D
        generation = "sha256:" + "3" * 64
        journal = "sha256:" + "4" * 64
        encoded = birth_authorization.issuer(generation, {}, request_id, journal)
        receipt = birth_authorization.verifier(encoded)
        assert receipt.candidate_id == birth_authorization.candidate_id
        assert receipt.birth_request_id == request_id
        assert receipt.check_results["authoring_install_journal_v1"].evidence_hash == journal
        return PublicationResult(ref.contract_id, expected_generation_id, generation,
                                 "commit_birth_snapshot", False)

    request, core = _fixture(tmp_path, publisher)
    result = _birth_executor_for_test(request, _core=core)
    assert result.error_code is None
    assert result.report.outcome is BirthOutcome.ADMITTED
    assert result.publication is not None
    assert len(calls) == 1
    assert not calls[0].private_root.exists()

    replay = _birth_executor_for_test(request, _core=core)
    assert replay.publication is not None
    assert replay.report.outcome is BirthOutcome.ADMITTED
    assert len(calls) == 1


def test_complete_pipeline_rejects_a_context_that_moved(tmp_path):
    """The sealed publisher owns a prepared epoch and refuses a moved one.

    The previous shape drove a *live* epoch resolver and let the store notice
    the change mid-commit.  The prepared set fixes the epoch, so that window
    does not exist any more: what has to be refused is a commit whose observed
    epoch disagrees with the prepared one, and the publisher refuses it before
    the store is reached.
    """
    def publisher(_ref, **_kwargs):
        raise AssertionError("changed epoch accepted")

    request, core = _fixture(tmp_path, publisher)
    core = replace(core, context_epoch_resolver=lambda: "sha256:" + "9" * 64)
    result = _birth_executor_for_test(request, _core=core)
    assert result.publication is None
    assert result.error_code in {"birth_context_changed", "birth_context_pin_invalid"}


def test_complete_pipeline_forwards_predecessor_pin_for_pointer_toctou(tmp_path):
    expected_pin = predecessor_snapshot(None, "absent", None).snapshot_id

    def publisher(_ref, *, birth_authorization, **_kwargs):
        assert birth_authorization.predecessor_id is None
        assert birth_authorization.predecessor_snapshot_id == expected_pin
        # This is the failure emitted by the productive publisher when its
        # locked reconstruction observes a pointer different from this pin.
        raise ContractStoreError("birth_predecessor_changed")

    request, core = _fixture(tmp_path, publisher)
    result = _birth_executor_for_test(request, _core=core)
    assert result.publication is None
    assert result.error_code == "birth_predecessor_changed"


def test_terminal_replay_survives_signing_key_rotation(tmp_path):
    calls = []

    def publisher(ref, *, expected_generation_id, **_kwargs):
        calls.append(1)
        return PublicationResult(
            ref.contract_id, expected_generation_id, "sha256:" + "3" * 64,
            "commit_birth_snapshot", False,
        )

    request, old_core = _fixture(tmp_path, publisher)
    assert _birth_executor_for_test(request, _core=old_core).error_code is None
    new_private = Ed25519PrivateKey.generate()
    rotated_core = replace(
        old_core, admission_private_key=new_private, admission_key_id="birth-2",
        admission_verifier_keys={
            "birth-1": old_core.admission_verifier_keys["birth-1"],
            "birth-2": new_private.public_key(),
        },
    )
    replay = _birth_executor_for_test(request, _core=rotated_core)
    assert replay.error_code is None
    assert replay.publication is not None
    assert calls == [1]


def test_rotation_issues_new_terminal_and_admission_signatures_only_with_active_key(tmp_path):
    admission_key_ids = []

    def publisher(ref, *, expected_generation_id, request_id,
                  birth_authorization, **_kwargs):
        encoded = birth_authorization.issuer(
            "sha256:" + "3" * 64, {}, request_id, "sha256:" + "4" * 64,
        )
        admission_key_ids.append(birth_authorization.verifier(encoded).authentication.key_id)
        return PublicationResult(
            ref.contract_id, expected_generation_id, "sha256:" + "3" * 64,
            "commit_birth_snapshot", False,
        )

    request, old_core = _fixture(tmp_path, publisher)
    old_public = old_core.admission_verifier_keys["birth-1"]
    new_private = Ed25519PrivateKey.generate()
    # The Admission identity is owned by the sealed publisher, so a rotation
    # is a new bundle and not a field swapped underneath one: replacing the
    # field alone would leave the old key signing, which is the point.
    rotated_core = _sealed_core_for_test(
        producer_registry=old_core.producer_registry,
        producer_db=old_core.producer_db,
        context_resolver=old_core.context_resolver,
        predecessor_resolver=old_core.predecessor_resolver,
        context_epoch_resolver=old_core.context_epoch_resolver,
        approval_resolver=old_core.approval_resolver,
        shadow_dependencies=old_core.shadow_dependencies,
        admission_private_key=new_private, admission_key_id="birth-2",
        admission_verifier_keys={
            "birth-1": old_public, "birth-2": new_private.public_key(),
        },
        policy_version=old_core.policy_version, now=old_core.now,
        publisher=publisher, publisher_options={},
        postcondition_verifier=old_core.postcondition_verifier,
    )
    assert _birth_executor_for_test(request, _core=rotated_core).error_code is None
    assert admission_key_ids == ["birth-2"]
    with sqlite3.connect(rotated_core.producer_db) as db:
        encoded = bytes(db.execute(
            "SELECT terminal_envelope FROM birth_producer_receipts"
        ).fetchone()[0])
    assert b'"signing_key_id":"birth-2"' in encoded
    with pytest.raises(TypeError):
        rotated_core.admission_verifier_keys["birth-3"] = new_private.public_key()  # type: ignore[index]


def test_terminal_replay_rejects_revoked_historical_key(tmp_path):
    request, old_core = _fixture(
        tmp_path,
        lambda ref, *, expected_generation_id, **_kwargs: PublicationResult(
            ref.contract_id, expected_generation_id, "sha256:" + "3" * 64,
            "commit_birth_snapshot", False,
        ),
    )
    assert _birth_executor_for_test(request, _core=old_core).error_code is None
    new_private = Ed25519PrivateKey.generate()
    revoked_core = replace(
        old_core, admission_private_key=new_private, admission_key_id="birth-2",
        admission_verifier_keys={"birth-2": new_private.public_key()},
    )
    assert _birth_executor_for_test(request, _core=revoked_core).error_code == "birth_unavailable"


def test_terminal_envelope_tampering_fails_closed_before_checks_or_publish(monkeypatch, tmp_path):
    calls = []
    def publisher(ref, *, expected_generation_id, **_kwargs):
        calls.append(1)
        return PublicationResult(
            ref.contract_id, expected_generation_id, "sha256:" + "3" * 64,
            "commit_birth_snapshot", False,
        )
    request, core = _fixture(tmp_path, publisher)
    assert _birth_executor_for_test(request, _core=core).error_code is None
    with sqlite3.connect(core.producer_db) as db:
        envelope = db.execute(
            "SELECT terminal_envelope FROM birth_producer_receipts"
        ).fetchone()[0]
        db.execute(
            "UPDATE birth_producer_receipts SET terminal_envelope=?",
            (bytes(envelope) + b" ",),
        )
    monkeypatch.setattr(
        operational, "_observe_birth_for_test",
        lambda *_args, **_kwargs: pytest.fail("tampered terminal reran checks"),
    )
    replay = _birth_executor_for_test(request, _core=core)
    assert replay.error_code == "birth_unavailable"
    assert calls == [1]


def test_source_binding_rejection_reports_and_never_calls_publisher(tmp_path):
    calls = []
    request, core = _fixture(tmp_path, lambda *args, **kwargs: calls.append(1))
    (request.candidate_source_root / "consult_frontier.py").write_bytes(b"pass\n")
    result = _birth_executor_for_test(request, _core=core)
    assert result.report.outcome is BirthOutcome.REJECTED
    assert result.publication is None
    assert result.error_code == "producer_receipt_binding_invalid"
    assert calls == []


def test_publisher_failure_is_a_rejection_not_a_false_admission(tmp_path):
    def unavailable(*_args, **_kwargs):
        raise OSError("publisher unavailable")

    request, core = _fixture(tmp_path, unavailable)
    result = _birth_executor_for_test(request, _core=core)
    assert result.report.outcome is BirthOutcome.REJECTED
    assert result.report.error_code == "birth_unavailable"
    assert result.publication is None


def test_ambiguous_publisher_failure_keeps_claim_for_exact_retry(tmp_path):
    calls = []

    def publisher(ref, *, expected_generation_id, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("lost response after possible commit")
        return PublicationResult(
            ref.contract_id, expected_generation_id, "sha256:" + "3" * 64,
            "commit_birth_snapshot", True,
        )

    request, core = _fixture(tmp_path, publisher)
    first = _birth_executor_for_test(request, _core=core)
    assert first.error_code == "birth_unavailable"
    retry = _birth_executor_for_test(request, _core=core)
    assert retry.error_code is None
    assert retry.publication is not None and retry.publication.repeated
    replay = _birth_executor_for_test(request, _core=core)
    assert replay.error_code is None
    assert len(calls) == 2


def test_crash_between_publisher_postcondition_and_finalize_is_retryable(
    monkeypatch, tmp_path,
):
    publications = []

    def publisher(ref, *, expected_generation_id, **_kwargs):
        publications.append(1)
        return PublicationResult(
            ref.contract_id, expected_generation_id, "sha256:" + "3" * 64,
            "commit_birth_snapshot", bool(len(publications) > 1),
        )

    request, core = _fixture(tmp_path, publisher)
    reconciliations = []
    def verify_postcondition(_request, expected, _receipt):
        reconciliations.append(expected)
        if expected is not None:
            return expected
        if publications:
            return PublicationResult(
                request.manifest_ref.contract_id, None, "sha256:" + "3" * 64,
                "commit_birth_snapshot", True,
            )
        return None
    core = replace(core, postcondition_verifier=verify_postcondition)
    real_finalize = operational.finalize_producer_receipt
    finalizations = []

    def crash_once(*args, **kwargs):
        finalizations.append(1)
        if len(finalizations) == 1:
            raise OSError("process lost before receipt finalization")
        return real_finalize(*args, **kwargs)

    monkeypatch.setattr(operational, "finalize_producer_receipt", crash_once)
    first = _birth_executor_for_test(request, _core=core)
    assert first.error_code == "birth_unavailable"
    retry = _birth_executor_for_test(request, _core=core)
    assert retry.error_code is None
    assert retry.publication is not None and retry.publication.repeated
    assert len(publications) == 1 and len(finalizations) == 2
    assert reconciliations == [None]


def test_non_admission_is_durably_rejected_and_replayed_without_checks_or_publish(
    monkeypatch, tmp_path,
):
    calls = []
    request, core = _fixture(tmp_path, lambda *_args, **_kwargs: calls.append(1))
    rejected = BirthReport(
        1, request.manifest_ref.contract_id, None, None, None, None, (), (),
        BirthOutcome.REJECTED, "semantic_review_failed",
    )
    observations = []

    def reject(*_args, **_kwargs):
        observations.append(1)
        return rejected

    monkeypatch.setattr(operational, "_observe_birth_for_test", reject)
    first = _birth_executor_for_test(request, _core=core)
    assert first.error_code == "semantic_review_failed"
    assert len(observations) == 1 and calls == []
    monkeypatch.setattr(
        operational, "_observe_birth_for_test",
        lambda *_args, **_kwargs: pytest.fail("terminal rejection reran checks"),
    )
    replay = _birth_executor_for_test(request, _core=core)
    assert replay.error_code == "semantic_review_failed"
    assert replay.report.outcome is BirthOutcome.REJECTED
    assert calls == []


def test_concurrent_exact_retries_converge_on_one_committed_binding(tmp_path):
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    publications = []

    def publisher(ref, *, expected_generation_id, **_kwargs):
        barrier.wait(timeout=5)
        result = PublicationResult(
            ref.contract_id, expected_generation_id, "sha256:" + "3" * 64,
            "commit_birth_snapshot", True,
        )
        with lock:
            publications.append(result)
        return result

    request, core = _fixture(tmp_path, publisher)
    results = []

    def execute():
        value = _birth_executor_for_test(request, _core=core)
        with lock:
            results.append(value)

    threads = [threading.Thread(target=execute) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert all(result.error_code is None for result in results)
    assert len(publications) == 2


def test_runtime_bundle_install_is_atomic_and_install_once(monkeypatch, tmp_path):
    request, core = _fixture(tmp_path, lambda *_args, **_kwargs: None)
    capability = intent_api._producer_capabilities_for_bootstrap()[0]
    bundle = _assemble_birth_runtime_bundle(
        core, {capability: lambda _intent: request}, lambda _current: object(),
    )
    monkeypatch.setattr(operational, "_RUNTIME_BUNDLE", None)
    barrier = threading.Barrier(3)
    outcomes = []

    def install() -> None:
        barrier.wait()
        try:
            _install_birth_runtime_bundle(bundle)
            outcomes.append("installed")
        except ValueError as exc:
            outcomes.append(str(exc))

    threads = [threading.Thread(target=install) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert outcomes.count("installed") == 1
    assert outcomes.count("birth_runtime_bundle_already_installed") == 1
    snapshot = _runtime_bundle_snapshot()
    assert snapshot is bundle
    assert snapshot.core is core
    assert snapshot.producer_factories[capability](
        intent_api.BirthIntent(tmp_path, request.manifest_ref.contract_id, "test")
    ) is request


def test_racing_readers_never_observe_a_partial_runtime(monkeypatch, tmp_path):
    request, core = _fixture(tmp_path, lambda *_args, **_kwargs: None)
    capability = intent_api._producer_capabilities_for_bootstrap()[0]
    bundle = _assemble_birth_runtime_bundle(
        core, {capability: lambda _intent: request}, lambda _current: object(),
    )
    monkeypatch.setattr(operational, "_RUNTIME_BUNDLE", None)
    barrier = threading.Barrier(9)
    observations = []

    def read() -> None:
        barrier.wait()
        snapshot = _runtime_bundle_snapshot()
        observations.append(None if snapshot is None else (
            snapshot.core, snapshot.producer_factories.get(capability),
        ))

    readers = [threading.Thread(target=read) for _ in range(8)]
    for reader in readers:
        reader.start()
    barrier.wait()
    _install_birth_runtime_bundle(bundle)
    for reader in readers:
        reader.join()
    assert all(item is None or (item[0] is core and callable(item[1]))
               for item in observations)


def test_forged_actor_cannot_be_expressed_or_select_another_capability(tmp_path):
    with pytest.raises(TypeError):
        intent_api.BirthIntent(
            tmp_path, ContractId(ManifestOrigin.USER, "x/manifest.toml"), "reason",
            actor="promoter",  # type: ignore[call-arg]
        )
    capability = intent_api._producer_capabilities_for_bootstrap()[0]
    forged = object()
    assert capability is not forged
    assert not intent_api._is_producer_capability(forged)
