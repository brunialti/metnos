"""Real isolated publication and terminal recovery for reduce-only quarantine.

Ephemeral authorities and the existing property oracle are fixture evidence,
not qualifying real admissions or production activation.
"""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import shutil
import sqlite3

import pytest
import tomlkit
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import contract_store
import executor_birth_bootstrap as bootstrap
import executor_birth_operational as operational
import manifest_inventory
from executor_birth_commit_publisher import _BirthCommitPublisher, _PUBLISHER_TOKEN
from executor_birth_feedback import make_execution_receipt
from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_intent import BirthIntent, _PROMOTER_QUARANTINE
from executor_birth_quarantine import quarantine_reason
from executor_birth_receipts import ApprovedLifecycle, IssuerKey, IssuerRegistry, verify_admission_receipt
from executor_birth_shadow import BirthOutcome
from executor_birth_snapshot import materialize_birth_candidate_from_authoring
from tests.runtime.executors.test_executor_birth_operational import _fixture, _context, NOW, D


@pytest.fixture
def quarantine(tmp_path, monkeypatch):
    request, core = _fixture(tmp_path, lambda *_a, **_kw: pytest.fail("unused publisher"))
    source_root = tmp_path / "authoring"
    canonical = source_root / "demo"
    shutil.copytree(request.manifest_ref.manifest_dir, canonical)
    ref = replace(request.manifest_ref, source_root=source_root,
                  manifest_path=canonical / "manifest.toml", allowed_code_roots=(source_root,))
    author = Ed25519PrivateKey.generate()
    trusted = (("author", author.public_key()),)
    (canonical / "manifest.toml.sig").write_bytes(contract_store.sign_manifest_bytes(
        (canonical / "manifest.toml").read_bytes(), private_key=author,
    ))
    store = tmp_path / "store"
    commits = []

    def commit(ref, **kwargs):
        result = contract_store.commit_birth_snapshot(ref, **kwargs)
        commits.append(result)
        return result

    publisher = _BirthCommitPublisher(
        _PUBLISHER_TOKEN, author_private=author, author_ring=trusted,
        admission_private=core.admission_private_key, admission_key_id=core.admission_key_id,
        admission_verifiers=core.admission_verifier_keys, prepared_context_epoch=D,
        primitive=commit, store_root=store, registry_reconciler=lambda _revision: None,
    )
    verifier = bootstrap._PostconditionAdapter(
        trusted_publics=trusted, verifier_keys=core.admission_verifier_keys, store_root=store,
    )
    core = replace(core, commit_publisher=publisher, predecessor_resolver=publisher.resolve_predecessor,
                   postcondition_verifier=verifier.verify)
    request = replace(request, manifest_ref=ref)
    first = operational._execute(request, core)
    assert first.error_code is None, first.diagnostic
    execution = make_execution_receipt(
        request_id=D, turn_id=D, reduced_query_ref=D, arguments={}, reduced_output={},
        contract_id=ref.contract_id, executor_name="consult_frontier",
        generation_id=first.publication.current_generation_id, candidate_id=first.report.candidate_id,
        dispatched_at="2026-08-25T12:00:00Z", completed_at="2026-08-25T12:00:00Z",
    )
    stage = materialize_birth_candidate_from_authoring(canonical, tmp_path / "quarantine")
    document = tomlkit.parse((stage / "manifest.toml").read_text())
    document["lifecycle"] = "quarantined"
    (stage / "manifest.toml").write_text(tomlkit.dumps(document))
    private = Ed25519PrivateKey.generate()
    authority = bootstrap._ProducerAuthority(
        _PROMOTER_QUARANTINE, "promoter", "quarantine-key", private, RevisionAuthor.MAINTENANCE,
    )
    registry = IssuerRegistry({**dict(core.producer_registry.entries), "promoter": (IssuerKey(
        "quarantine-key", private.public_key(), frozenset({ExecutorOrigin.HUMAN}),
        frozenset({RevisionAuthor.MAINTENANCE}),
    ),)})
    core = replace(core, producer_registry=registry, quarantine_key_ids=frozenset({"quarantine-key"}))
    monkeypatch.setattr(bootstrap, "_manifest_ref", lambda _intent: ref)
    monkeypatch.setattr(manifest_inventory, "inventory_authoring_manifests",
                        lambda: manifest_inventory.ManifestInventory((ref,), ()))
    factory = bootstrap._request_factory(
        authority, registry, core.producer_db, 3600, lambda: NOW,
        SimpleNamespace(preview=lambda _intent: (_context(), None)),
    )

    def build(execution=execution):
        return factory(BirthIntent(stage, ref.contract_id, quarantine_reason(execution)))

    return SimpleNamespace(core=core, execution=execution, request=build(), build=build,
                           stage=stage, ref=ref, store=store, commits=commits, first=first,
                           verifier=verifier, trusted=trusted, factory=factory)


def test_quarantine_publishes_only_lifecycle_and_replays(quarantine, monkeypatch):
    q = quarantine
    before = contract_store.current_contract(q.ref, trusted_publics=q.trusted, store_root=q.store)
    monkeypatch.setattr(operational, "_observe_birth_for_test",
                        lambda *_a, **_kw: pytest.fail("suspect code was checked again"))
    result = operational._execute(q.request, q.core, quarantine_execution=q.execution)
    assert result.error_code is None, result.diagnostic
    assert result.report.outcome is BirthOutcome.QUARANTINED
    assert result.publication.previous_generation_id == q.execution.generation_id
    assert result.publication.current_generation_id != q.execution.generation_id
    publication, encoded = q.verifier.verify(q.request, result.publication, None)
    receipt = verify_admission_receipt(encoded, verifier_keys=q.core.admission_verifier_keys)
    assert receipt.approved_lifecycle is ApprovedLifecycle.QUARANTINED
    assert receipt.check_results["quarantine_execution"].evidence_hash == q.execution.receipt_id
    after = contract_store.current_contract(q.ref, trusted_publics=q.trusted, store_root=q.store)
    old, new = dict(before.parsed), dict(after.parsed)
    assert old.pop("lifecycle") == "active"
    assert new.pop("lifecycle") == "quarantined"
    assert old == new
    assert before.language_state_bytes == after.language_state_bytes
    repeated = operational._execute(q.request, q.core, quarantine_execution=q.execution)
    assert repeated.error_code is None
    assert repeated.report == result.report
    assert repeated.publication == replace(result.publication, repeated=True)
    assert len(q.commits) == 2  # initial publication plus quarantine, never a third


@pytest.mark.parametrize("route", ["ordinary", "foreign-key", "wrong-execution"])
def test_quarantine_authority_is_not_interchangeable(quarantine, route):
    q = quarantine
    core, execution = q.core, q.execution
    if route == "ordinary":
        execution = None
    elif route == "foreign-key":
        core = replace(core, quarantine_key_ids=frozenset())
    else:
        execution.arguments["changed"] = True
    result = operational._execute(q.request, core, quarantine_execution=execution)
    assert result.error_code is not None
    assert len(q.commits) == 1


@pytest.mark.parametrize("change", ["code", "contract", "language", "lifecycle"])
def test_quarantine_cannot_smuggle_other_changes(quarantine, change):
    q = quarantine
    manifest = q.stage / "manifest.toml"
    document = tomlkit.parse(manifest.read_text())
    if change == "code":
        path = q.stage / document["code"]["files"][0]
        path.write_bytes(path.read_bytes() + b"\n# unrelated change\n")
    elif change == "language":
        path = q.stage / "manifest.lang_state.json"
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        document["version" if change == "contract" else "lifecycle"] = "9.0.0" if change == "contract" else "active"
        manifest.write_text(tomlkit.dumps(document))
    result = operational._execute(q.build(), q.core, quarantine_execution=q.execution)
    assert result.error_code is not None
    assert len(q.commits) == 1


def test_quarantine_recovers_after_publication_before_terminal_commit(quarantine, monkeypatch):
    q = quarantine
    finalize = operational.finalize_producer_receipt
    attempts = []

    def interrupted(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("interrupted after publication")
        return finalize(*args, **kwargs)

    monkeypatch.setattr(operational, "finalize_producer_receipt", interrupted)
    failed = operational._execute(q.request, q.core, quarantine_execution=q.execution)
    assert failed.error_code is not None
    assert failed.report.outcome is BirthOutcome.REJECTED
    assert len(q.commits) == 2
    recovered = operational._execute(q.request, q.core, quarantine_execution=q.execution)
    assert recovered.error_code is None, recovered.diagnostic
    assert recovered.report.outcome is BirthOutcome.QUARANTINED
    assert recovered.publication.current_generation_id == q.commits[-1].current_generation_id
    assert len(q.commits) == 2


@pytest.mark.parametrize("field", ["generation_id", "candidate_id", "executor_name", "contract_id"])
def test_quarantine_rejects_feedback_for_another_identity(quarantine, field):
    q = quarantine
    values = {key: getattr(q.execution, key) for key in (
        "request_id", "turn_id", "reduced_query_ref", "arguments", "reduced_output",
        "contract_id", "executor_name", "generation_id", "candidate_id", "dispatched_at", "completed_at",
    )}
    values[field] = {
        "generation_id": D, "candidate_id": D, "executor_name": "another_executor",
        "contract_id": manifest_inventory.ContractId(manifest_inventory.ManifestOrigin.USER, "other/manifest.toml"),
    }[field]
    execution = make_execution_receipt(**values)
    result = operational._execute(q.build(execution), q.core, quarantine_execution=execution)
    assert result.error_code is not None
    assert len(q.commits) == 1


@pytest.mark.parametrize("interrupt", [False, True])
@pytest.mark.parametrize("delay_hours", [0, 2])
def test_owner_rebuilds_the_same_request_without_retaining_staging(quarantine, monkeypatch, interrupt, delay_hours):
    q = quarantine
    bundle = SimpleNamespace(core=q.core, producer_factories={_PROMOTER_QUARANTINE: q.factory})
    finalize = operational.finalize_producer_receipt
    if interrupt:
        monkeypatch.setattr(operational, "finalize_producer_receipt",
                            lambda *_a, **_kw: (_ for _ in ()).throw(OSError("process lost")))
        from executor_birth_lifecycle import LifecycleError
        with pytest.raises(LifecycleError, match="birth_unavailable"):
            operational._quarantine_execution_with_bundle(q.execution, bundle)
        monkeypatch.setattr(operational, "finalize_producer_receipt", finalize)
    else:
        first = operational._quarantine_execution_with_bundle(q.execution, bundle)
        assert first.reread_lifecycle is ApprovedLifecycle.QUARANTINED
    bundle.core = replace(q.core, now=lambda: NOW + timedelta(hours=delay_hours))
    recovered = operational._quarantine_execution_with_bundle(q.execution, bundle)
    assert recovered.receipt.predecessor_id == q.execution.generation_id
    assert recovered.reread_generation_id == q.commits[-1].current_generation_id
    assert len(q.commits) == 2


def test_productive_facade_requires_fixed_activation_before_bootstrap(monkeypatch):
    import executor_birth_lifecycle as lifecycle
    from executor_birth_intent import submit_promoter_quarantine_birth

    def inactive():
        raise lifecycle.LifecycleError("f5_activation_required")

    monkeypatch.setattr(lifecycle, "load_f5_activation", inactive)
    monkeypatch.setattr(bootstrap, "bootstrap_birth_runtime",
                        lambda: pytest.fail("inactive F5 prepared publication"))
    with pytest.raises(lifecycle.LifecycleError, match="f5_activation_required"):
        submit_promoter_quarantine_birth(object())


def test_expired_unclaimed_quarantine_is_not_new_authority(quarantine):
    q = quarantine
    expired = replace(q.core, now=lambda: NOW + timedelta(hours=2))
    result = operational._execute(q.request, expired, quarantine_execution=q.execution)
    assert result.error_code == "producer_receipt_expired"
    assert len(q.commits) == 1


@pytest.fixture
def feedback_runtime(quarantine, tmp_path):
    from executor_birth_epoch_store import BirthLifecycle, EpochCacheKey, open_epoch, put_cache

    q = quarantine
    epochs, queue = tmp_path / "epochs.sqlite", tmp_path / "review.sqlite"
    open_epoch(contract_id=q.ref.contract_id, generation_id=q.execution.generation_id,
               name=q.execution.executor_name, source="birth", lifecycle=BirthLifecycle.ACTIVE,
               observed_at=q.execution.completed_at, db_path=epochs)
    key = EpochCacheKey(q.ref.contract_id, q.execution.generation_id, BirthLifecycle.ACTIVE)
    put_cache(key, b"old cache", created_at=q.execution.completed_at, db_path=epochs)
    other = manifest_inventory.ContractId(manifest_inventory.ManifestOrigin.USER, "unrelated/manifest.toml")
    open_epoch(contract_id=other, generation_id=D, name="unrelated", source="birth",
               lifecycle=BirthLifecycle.ACTIVE, observed_at=q.execution.completed_at, db_path=epochs)
    bundle = SimpleNamespace(core=q.core, producer_factories={_PROMOTER_QUARANTINE: q.factory})
    return SimpleNamespace(q=q, bundle=bundle, epochs=epochs, queue=queue, key=key, other=other)


def apply_failure(runtime):
    from executor_birth_lifecycle import _apply_execution_failure_with_bundle
    return _apply_execution_failure_with_bundle(
        runtime.q.execution, bundle=runtime.bundle, epoch_db=runtime.epochs,
        queue_db=runtime.queue, occurred_at=runtime.q.execution.completed_at,
        failure_evidence_hash=D, error_code="execution_failed",
    )


@pytest.mark.parametrize("failure", ["none", "epoch", "queue"])
def test_feedback_recovers_publication_epoch_and_queue(feedback_runtime, monkeypatch, failure):
    import executor_birth_lifecycle as lifecycle
    import executor_birth_feedback as feedback
    from executor_birth_epoch_store import BirthLifecycle, EpochState, read_epoch, get_cache

    runtime = feedback_runtime
    q = runtime.q
    if failure == "epoch":
        from executor_birth_epoch_store import EpochStoreError
        original = lifecycle.replace_current_epoch
        def interrupted(**_kwargs):
            raise EpochStoreError("epoch_conflict", "interrupted before epoch commit")
        monkeypatch.setattr(lifecycle, "replace_current_epoch", interrupted)
        with pytest.raises(lifecycle.LifecycleError, match="epoch_recovery_required"):
            apply_failure(runtime)
        assert not runtime.queue.exists()
        assert get_cache(runtime.key, db_path=runtime.epochs) == b"old cache"
        monkeypatch.setattr(lifecycle, "replace_current_epoch", original)
    elif failure == "queue":
        original = feedback.enqueue_failure_review_inactive
        def interrupted(*_args, **_kwargs):
            raise OSError("outbox unavailable")
        monkeypatch.setattr(feedback, "enqueue_failure_review_inactive", interrupted)
        failed = apply_failure(runtime)
        assert failed.status is feedback.FeedbackStatus.ENQUEUE_FAILED
        assert failed.quarantine_applied
        monkeypatch.setattr(feedback, "enqueue_failure_review_inactive", original)
    result = apply_failure(runtime)
    assert result.status is feedback.FeedbackStatus.QUARANTINED
    assert result.quarantine_applied is (failure != "queue")
    repeated = apply_failure(runtime)
    assert repeated.status is feedback.FeedbackStatus.QUARANTINED
    assert not repeated.quarantine_applied
    assert len(q.commits) == 2
    assert get_cache(runtime.key, db_path=runtime.epochs) is None
    old = read_epoch(contract_id=q.ref.contract_id, generation_id=q.execution.generation_id, db_path=runtime.epochs)
    current = read_epoch(contract_id=q.ref.contract_id, generation_id=q.commits[-1].current_generation_id,
                         db_path=runtime.epochs)
    assert old.state is EpochState.DEPRECATED
    assert current.state is EpochState.CURRENT
    assert current.lifecycle is BirthLifecycle.QUARANTINED
    assert read_epoch(contract_id=runtime.other, generation_id=D, db_path=runtime.epochs).lifecycle is BirthLifecycle.ACTIVE
    with sqlite3.connect(runtime.queue) as db:
        assert db.execute("SELECT COUNT(*) FROM executor_failure_review_queue").fetchone() == (1,)
    with sqlite3.connect(runtime.epochs) as db:
        assert db.execute("SELECT COUNT(*) FROM executor_epoch_history WHERE contract_id=?",
                          (q.ref.contract_id.value,)).fetchone() == (3,)


def test_feedback_publication_failure_leaves_epoch_and_queue_untouched(feedback_runtime, monkeypatch):
    from executor_birth_commit_publisher import _BirthCommitPublisher, BirthCommitLinkError
    from executor_birth_epoch_store import get_cache
    from executor_birth_lifecycle import LifecycleError

    runtime = feedback_runtime
    def refused(*_args):
        raise BirthCommitLinkError("birth_context_changed")
    monkeypatch.setattr(_BirthCommitPublisher, "commit", refused)
    with pytest.raises(LifecycleError, match="birth_context_changed"):
        apply_failure(runtime)
    assert len(runtime.q.commits) == 1
    assert not runtime.queue.exists()
    assert get_cache(runtime.key, db_path=runtime.epochs) == b"old cache"


def test_stale_feedback_never_quarantines_a_newer_publication(feedback_runtime, tmp_path):
    from types import MappingProxyType
    from executor_birth_snapshot import acquire_candidate_snapshot
    from tests.runtime.contracts.test_contract_store import _birth_authorization
    from executor_birth_feedback import FeedbackStatus
    from executor_birth_epoch_store import get_cache

    runtime = feedback_runtime
    q = runtime.q
    # The shared store fixture issues under its own test key identifier. Bind
    # that same ephemeral public key in this isolated publisher's trusted ring.
    q.core.commit_publisher._admission_verifiers = MappingProxyType({
        **dict(q.core.admission_verifier_keys),
        "birth-test-key": q.core.admission_private_key.public_key(),
    })
    stage = materialize_birth_candidate_from_authoring(q.ref.manifest_dir, tmp_path / "newer")
    manifest = stage / "manifest.toml"
    document = tomlkit.parse(manifest.read_text())
    document["version"] = "9.0.0"
    manifest.write_text(tomlkit.dumps(document))
    with acquire_candidate_snapshot(stage) as snapshot:
        successor = contract_store.commit_birth_snapshot(
            q.ref, expected_generation_id=q.execution.generation_id, snapshot=snapshot,
            request_id=D, private_key=q.core.commit_publisher._author_private, trusted_publics=q.trusted,
            birth_authorization=_birth_authorization(q.ref, q.execution.generation_id, q.core.admission_private_key),
            store_root=q.store,
        )
    # The real authenticated successor remains active. The old execution can
    # neither change it nor create a review job for that later generation.
    result = apply_failure(runtime)
    assert result.status is FeedbackStatus.STALE_FEEDBACK
    assert not runtime.queue.exists()
    assert get_cache(runtime.key, db_path=runtime.epochs) == b"old cache"
    assert contract_store.current_contract(q.ref, trusted_publics=q.trusted,
                                           store_root=q.store).generation_id == successor.current_generation_id


def test_certified_feedback_does_not_create_missing_migrated_state(quarantine, tmp_path, monkeypatch):
    import config
    import executor_birth_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "load_f5_activation", lambda: object())
    monkeypatch.setattr(bootstrap, "bootstrap_birth_runtime", lambda: quarantine.core)
    monkeypatch.setattr(config, "PATH_USER_STATE", tmp_path / "state")
    with pytest.raises(lifecycle.LifecycleError, match="f5_epoch_migration_required"):
        lifecycle.apply_execution_failure(quarantine.execution, failure_evidence_hash=D, error_code="failure")
    assert not (tmp_path / "state/birth/executor_epochs.sqlite").exists()
    assert not (tmp_path / "state/birth/failure_reviews.sqlite").exists()
