"""The catalog's restrictions come from the store that owns lifecycle state."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import executor_birth_activation_mode as mode
import executor_lifecycle_state as state
from executor_birth_epoch_store import (
    BirthLifecycle, open_epoch, read_current_epoch, read_epoch, replace_current_epoch,
)
from manifest_inventory import ContractId, ManifestOrigin


def loaded(name, *, contract=None, generation="1", lifecycle="active"):
    return SimpleNamespace(
        name=name, source="synthesized", lifecycle=lifecycle,
        contract_id=ContractId(
            ManifestOrigin.USER, (contract or name) + "/manifest.toml").value,
        generation_id="sha256:" + generation * 64,
    )


def contract_of(executor):
    origin, relative = executor.contract_id.split(":", 1)
    return ContractId(ManifestOrigin(origin), relative)


def owned_by(monkeypatch, owner):
    monkeypatch.setattr(state, "read_birth_activation_state", lambda: mode.BirthActivationState(
        owner, "sha256:" + "2" * 64 if owner is mode.BirthStateOwner.EPOCH else None, None, None))


@pytest.fixture
def epochs(tmp_path, monkeypatch):
    path = tmp_path / "executor_epochs.sqlite"
    monkeypatch.setattr(state, "_epoch_db_path", lambda: path)
    return path


def admit(path, executor, lifecycle=BirthLifecycle.ACTIVE):
    open_epoch(contract_id=contract_of(executor), generation_id=executor.generation_id,
               name=executor.name, source="synth:reactive", lifecycle=lifecycle,
               observed_at="2026-09-16T10:00:00Z", db_path=path)


def test_the_legacy_owner_answers_by_name(monkeypatch):
    owned_by(monkeypatch, mode.BirthStateOwner.LEGACY)
    monkeypatch.setattr(state, "_epoch_restrictions",
                        lambda _executors: pytest.fail("epoch read on a legacy owner"))
    monkeypatch.setattr("executor_aging.lifecycle_override_map",
                        lambda *, read_only: {"gone": "archived", "old": "deprecated"})
    assert state.catalog_restrictions([loaded("gone")], read_only=True) == {
        "gone": (state.Restriction.REMOVED, "inactive too long"),
        "old": (state.Restriction.DEMOTED, "deprecated"),
    }


def test_the_legacy_owner_learns_a_newly_loaded_executor(monkeypatch):
    owned_by(monkeypatch, mode.BirthStateOwner.LEGACY)
    seen = []
    monkeypatch.setattr("executor_aging.register",
                        lambda name, *, source: seen.append((name, source)))
    assert state.register_loaded_executors([loaded("fresh")]) == 1
    assert seen == [("fresh", "synth:reactive")]


@pytest.mark.parametrize("lifecycle,expected", [
    (BirthLifecycle.ACTIVE, None),
    (BirthLifecycle.PREEXERCISE, None),
    (BirthLifecycle.DEPRECATED, state.Restriction.DEMOTED),
    (BirthLifecycle.ARCHIVED, state.Restriction.REMOVED),
    (BirthLifecycle.QUARANTINED, state.Restriction.REMOVED),
])
def test_the_epoch_owner_answers_for_the_exact_generation(
    monkeypatch, epochs, lifecycle, expected,
):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    executor = loaded("demo")
    admit(epochs, executor, lifecycle)
    observed = state.catalog_restrictions([executor], read_only=True)
    assert (observed.get("demo") or (None,))[0] == expected


def test_a_restriction_never_reaches_the_successor_generation(monkeypatch, epochs):
    """The predecessor stays restricted and the successor is admitted."""
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    restricted = loaded("demo", generation="1")
    admit(epochs, restricted)
    successor = loaded("demo", generation="7")
    replace_current_epoch(
        contract_id=contract_of(restricted),
        expected_generation_id=restricted.generation_id, expected_state_version=1,
        generation_id=successor.generation_id, name="demo", source="synth:reactive",
        lifecycle=BirthLifecycle.ACTIVE, observed_at="2026-09-16T11:00:00Z",
        db_path=epochs, event_kind="lifecycle_active",
    )
    assert state.catalog_restrictions([successor], read_only=True) == {}
    assert state.catalog_restrictions([restricted], read_only=True)["demo"][0] is (
        state.Restriction.REMOVED)


def test_no_decision_yet_is_not_a_restriction(monkeypatch, epochs):
    """A writable load records the generation first, so this is the audit case."""
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    admit(epochs, loaded("other"))
    assert state.catalog_restrictions([loaded("demo")], read_only=True) == {}


def test_an_executor_without_its_exact_identity_is_not_admitted(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    admit(epochs, loaded("other"))
    nameless = SimpleNamespace(name="legacy", source="handcrafted",
                               contract_id=None, generation_id=None)
    assert state.catalog_restrictions([nameless], read_only=True)["legacy"] == (
        state.Restriction.REMOVED, "no authenticated identity")


def test_a_missing_epoch_database_is_a_fault_not_an_empty_answer(monkeypatch, tmp_path):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    monkeypatch.setattr(state, "_epoch_db_path", lambda: tmp_path / "absent.sqlite")
    with pytest.raises(FileNotFoundError):
        state.catalog_restrictions([loaded("demo")], read_only=True)


def test_the_epoch_owner_records_the_exact_generation_it_loaded(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    epochs.touch()
    monkeypatch.setattr("executor_aging.register",
                        lambda *_a, **_k: pytest.fail("a name-based row was written"))
    executor = loaded("demo", lifecycle="preexercise")
    assert state.register_loaded_executors([executor]) == 1
    record = read_current_epoch(contract_id=contract_of(executor), db_path=epochs)
    assert (record.generation_id, record.lifecycle, record.source) == (
        executor.generation_id, BirthLifecycle.PREEXERCISE, "synth:reactive")
    # A second load of the same generation changes nothing.
    assert state.register_loaded_executors([executor]) == 0


def test_an_existing_decision_survives_every_later_load(monkeypatch, epochs):
    """The manifest says active; the epoch owner already said deprecated."""
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    executor = loaded("demo")
    admit(epochs, executor, BirthLifecycle.DEPRECATED)
    assert state.register_loaded_executors([executor]) == 0
    assert state.catalog_restrictions([executor], read_only=True)["demo"][0] is (
        state.Restriction.DEMOTED)


def test_a_new_signed_generation_closes_the_previous_epoch(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    first = loaded("demo", generation="1")
    admit(epochs, first, BirthLifecycle.DEPRECATED)
    successor = loaded("demo", generation="7")
    assert state.register_loaded_executors([successor]) == 1
    current = read_current_epoch(contract_id=contract_of(successor), db_path=epochs)
    assert current.generation_id == successor.generation_id
    assert current.lifecycle is BirthLifecycle.ACTIVE
    closed = read_epoch(contract_id=contract_of(first),
                        generation_id=first.generation_id, db_path=epochs)
    assert closed.state.value == "deprecated"


def test_an_unknown_declared_lifecycle_is_reported_not_guessed(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    epochs.touch()
    assert state.register_loaded_executors([loaded("demo", lifecycle="retired")]) == 0
    assert read_current_epoch(contract_id=contract_of(loaded("demo")),
                              db_path=epochs) is None


def test_a_call_is_counted_against_the_exact_generation(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    executor = loaded("demo")
    admit(epochs, executor)
    state.record_invocation(executor, ok=True)
    state.record_invocation(executor, ok=False)
    import sqlite3
    with sqlite3.connect(epochs) as connection:
        totals = connection.execute(
            "SELECT total_calls,successful_calls,failed_calls FROM executor_epochs "
            "WHERE generation_id=?", (executor.generation_id,)).fetchone()
    assert totals == (2, 1, 1)


def test_a_call_on_a_superseded_generation_is_not_reattributed(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    stale = loaded("demo", generation="1")
    admit(epochs, stale)
    successor = loaded("demo", generation="7")
    state.register_loaded_executors([successor])
    state.record_invocation(stale, ok=True)
    import sqlite3
    with sqlite3.connect(epochs) as connection:
        counted = dict(connection.execute(
            "SELECT generation_id,total_calls FROM executor_epochs").fetchall())
    assert counted == {stale.generation_id: 0, successor.generation_id: 0}


def test_the_legacy_owner_still_counts_by_name(monkeypatch):
    owned_by(monkeypatch, mode.BirthStateOwner.LEGACY)
    seen = []
    monkeypatch.setattr("executor_aging.record_invocation",
                        lambda name, *, ok: seen.append((name, ok)))
    state.record_invocation(loaded("demo"), ok=True)
    assert seen == [("demo", True)]


def test_inherited_uses_are_never_credited_to_a_retired_store(monkeypatch):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    monkeypatch.setattr("executor_aging.touch",
                        lambda *_a, **_k: pytest.fail("credited the retired store"))
    assert state.credit_uses("demo", 4) == 0


def test_inherited_uses_reach_the_legacy_store(monkeypatch):
    owned_by(monkeypatch, mode.BirthStateOwner.LEGACY)
    seen = []
    monkeypatch.setattr("executor_aging.touch", lambda name: seen.append(name))
    assert state.credit_uses("demo", 3) == 3
    assert seen == ["demo"] * 3


# --- the one inactivity decision, against the store that owns it -------------

def synth(name, *, generation="1"):
    executor = loaded(name, generation=generation)
    executor.source = "synthesized"
    return executor


def idle(epochs, executor, *, first_seen, last_used=None, source="synth:reactive"):
    open_epoch(contract_id=contract_of(executor), generation_id=executor.generation_id,
               name=executor.name, source=source, lifecycle=BirthLifecycle.ACTIVE,
               observed_at=first_seen, db_path=epochs)
    if last_used is not None:
        import sqlite3
        with sqlite3.connect(epochs) as connection:
            connection.execute("UPDATE executor_epochs SET last_used_at=? WHERE generation_id=?",
                               (last_used, executor.generation_id))


def override_of(epochs, executor):
    from executor_birth_epoch_store import read_current_epoch
    record = read_current_epoch(contract_id=contract_of(executor), db_path=epochs)
    return None if record.lifecycle_override is None else (
        record.lifecycle_override.value, record.override_reason)


def test_an_idle_generation_is_deprecated_then_archived(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    executor = synth("demo")
    idle(epochs, executor, first_seen="2026-01-01T00:00:00Z")
    first = state.apply_inactivity_decay(
        deprecate_days=30, archive_days=14, now_iso="2026-03-01T00:00:00Z")
    assert first["deprecated"] == ["demo"] and first["archived"] == []
    assert override_of(epochs, executor) == ("deprecated", "inactive too long")
    # Too soon to archive: the clock starts when the deprecation was recorded.
    middle = state.apply_inactivity_decay(
        deprecate_days=30, archive_days=14, now_iso="2026-03-10T00:00:00Z")
    assert middle["archived"] == [] and middle["already_deprecated"] == 1
    late = state.apply_inactivity_decay(
        deprecate_days=30, archive_days=14, now_iso="2026-04-01T00:00:00Z")
    assert late["archived"] == ["demo"]
    assert override_of(epochs, executor)[0] == "archived"
    assert state.catalog_restrictions([executor], read_only=True)["demo"] == (
        state.Restriction.REMOVED, "inactive too long")


def test_the_signed_lifecycle_is_never_rewritten_by_the_decision(monkeypatch, epochs):
    from executor_birth_epoch_store import read_current_epoch

    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    executor = synth("demo")
    idle(epochs, executor, first_seen="2026-01-01T00:00:00Z")
    state.apply_inactivity_decay(deprecate_days=30, archive_days=14,
                                 now_iso="2026-03-01T00:00:00Z")
    record = read_current_epoch(contract_id=contract_of(executor), db_path=epochs)
    assert record.lifecycle is BirthLifecycle.ACTIVE
    assert record.state.value == "current"


def test_recent_use_and_curated_capabilities_never_decay(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    recent = synth("recent")
    idle(epochs, recent, first_seen="2026-01-01T00:00:00Z",
         last_used="2026-02-28T00:00:00Z")
    curated = loaded("curated", contract="curated")
    idle(epochs, curated, first_seen="2026-01-01T00:00:00Z", source="handcrafted")
    report = state.apply_inactivity_decay(
        deprecate_days=30, archive_days=14, now_iso="2026-03-01T00:00:00Z")
    assert report["deprecated"] == [] and report["handcrafted_skipped"] == 1
    assert state.catalog_restrictions([recent, curated], read_only=True) == {}


def test_a_restriction_is_reversible_and_keeps_its_reason(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    executor = synth("demo")
    idle(epochs, executor, first_seen="2026-01-01T00:00:00Z")
    monkeypatch.setattr(state, "_catalog_executor", lambda name: executor)
    assert state.restrict_executor(
        "demo", restriction=state.Restriction.DEMOTED, reason="duplicate of other") is True
    assert override_of(epochs, executor) == ("deprecated", "duplicate of other")
    assert state.catalog_restrictions([executor], read_only=True)["demo"][0] is (
        state.Restriction.DEMOTED)
    assert state.revive_executor("demo", reason="dedupe rollback") is True
    assert override_of(epochs, executor) is None
    assert state.catalog_restrictions([executor], read_only=True) == {}
    # Reviving twice is not an error and changes nothing.
    assert state.revive_executor("demo", reason="dedupe rollback") is False


def test_a_restricted_generation_loses_execution_authority_when_archived(monkeypatch, epochs):
    from executor_birth_epoch_store import EpochStoreError, attest_execution_epoch

    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    executor = synth("demo")
    idle(epochs, executor, first_seen="2026-01-01T00:00:00Z")
    monkeypatch.setattr(state, "_catalog_executor", lambda name: executor)
    state.restrict_executor("demo", restriction=state.Restriction.DEMOTED, reason="idle")
    # Deprecated stays callable: it is demoted in the catalog, not removed.
    assert attest_execution_epoch(
        contract_id=contract_of(executor), generation_id=executor.generation_id,
        name="demo", db_path=epochs).state_version >= 1
    state.restrict_executor("demo", restriction=state.Restriction.REMOVED, reason="idle")
    with pytest.raises(EpochStoreError) as raised:
        attest_execution_epoch(
            contract_id=contract_of(executor), generation_id=executor.generation_id,
            name="demo", db_path=epochs)
    assert raised.value.code == "execution.retired"


def test_inherited_uses_reach_the_exact_current_generation(monkeypatch, epochs):
    import sqlite3

    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    executor = synth("demo")
    idle(epochs, executor, first_seen="2026-01-01T00:00:00Z")
    monkeypatch.setattr(state, "_catalog_executor", lambda name: executor)
    assert state.credit_uses("demo", 5) == 5
    with sqlite3.connect(epochs) as connection:
        totals = connection.execute(
            "SELECT total_calls,successful_calls,last_used_at IS NOT NULL "
            "FROM executor_epochs WHERE generation_id=?",
            (executor.generation_id,)).fetchone()
    assert totals == (5, 0, 1)


def test_an_unresolvable_name_credits_nothing(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    monkeypatch.setattr(state, "_catalog_executor", lambda name: None)
    assert state.credit_uses("ghost", 5) == 0


# --- explicit verdicts, counted where the execution happened -----------------

def dispatch_receipt(executor):
    from executor_birth_feedback import make_execution_receipt

    return make_execution_receipt(
        request_id="sha256:" + "a" * 64, turn_id="sha256:" + "b" * 64,
        reduced_query_ref="sha256:" + "c" * 64,
        arguments={}, reduced_output={"ok": False},
        contract_id=contract_of(executor), executor_name=executor.name,
        generation_id=executor.generation_id, candidate_id="sha256:" + "e" * 64,
        dispatched_at="2026-09-16T10:00:00Z", completed_at="2026-09-16T10:00:01Z",
    )


def test_a_negative_verdict_counts_consecutively_and_a_positive_one_clears_it(
    monkeypatch, epochs,
):
    import sqlite3

    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    executor = synth("demo")
    idle(epochs, executor, first_seen="2026-01-01T00:00:00Z")
    receipt = dispatch_receipt(executor)
    assert state.record_verdict(receipt, positive=False) == 1
    assert state.record_verdict(receipt, positive=False) == 2
    assert state.record_verdict(receipt, positive=True) == 0
    assert state.record_verdict(receipt, positive=False) == 1
    with sqlite3.connect(epochs) as connection:
        counters = connection.execute(
            "SELECT positive_feedback,negative_feedback FROM executor_epochs "
            "WHERE generation_id=?", (executor.generation_id,)).fetchone()
    assert counters == (1, 1)


def test_a_verdict_on_a_superseded_generation_is_dropped(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    stale = synth("demo", generation="1")
    idle(epochs, stale, first_seen="2026-01-01T00:00:00Z")
    successor = synth("demo", generation="7")
    state.register_loaded_executors([successor])
    assert state.record_verdict(dispatch_receipt(stale), positive=False) is None


def test_the_legacy_owner_keeps_no_verdict_counter(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.LEGACY)
    executor = synth("demo")
    assert state.record_verdict(dispatch_receipt(executor), positive=False) is None


def test_an_unreachable_store_drops_the_verdict_without_raising(monkeypatch, tmp_path):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    monkeypatch.setattr(state, "_epoch_db_path",
                        lambda: tmp_path / "absent" / "executor_epochs.sqlite")
    executor = synth("demo")
    assert state.record_verdict(dispatch_receipt(executor), positive=False) is None
    assert state.record_invocation(executor, ok=True) is None
