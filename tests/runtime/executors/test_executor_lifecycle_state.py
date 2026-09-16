"""The catalog's restrictions come from the store that owns lifecycle state."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import executor_birth_activation_mode as mode
import executor_lifecycle_state as state
from executor_birth_epoch_store import (
    BirthLifecycle, open_epoch, replace_current_epoch,
)
from manifest_inventory import ContractId, ManifestOrigin


def loaded(name, *, contract=None, generation="1"):
    return SimpleNamespace(
        name=name, source="synthesized",
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


def test_the_epoch_owner_never_learns_a_name_from_a_catalog_load(monkeypatch):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    monkeypatch.setattr("executor_aging.register",
                        lambda *_a, **_k: pytest.fail("a catalog load created an epoch"))
    assert state.register_loaded_executors([loaded("fresh")]) == 0


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


def test_a_generation_the_epoch_store_never_saw_is_not_admitted(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    admit(epochs, loaded("other"))
    assert state.catalog_restrictions([loaded("demo")], read_only=True)["demo"] == (
        state.Restriction.REMOVED, "no epoch for the loaded generation")


def test_an_executor_without_its_exact_identity_is_not_admitted(monkeypatch, epochs):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    admit(epochs, loaded("other"))
    nameless = SimpleNamespace(name="legacy", source="handcrafted",
                               contract_id=None, generation_id=None)
    assert state.catalog_restrictions([nameless], read_only=True)["legacy"][0] is (
        state.Restriction.REMOVED)


def test_a_missing_epoch_database_is_a_fault_not_an_empty_answer(monkeypatch, tmp_path):
    owned_by(monkeypatch, mode.BirthStateOwner.EPOCH)
    monkeypatch.setattr(state, "_epoch_db_path", lambda: tmp_path / "absent.sqlite")
    with pytest.raises(FileNotFoundError):
        state.catalog_restrictions([loaded("demo")], read_only=True)
