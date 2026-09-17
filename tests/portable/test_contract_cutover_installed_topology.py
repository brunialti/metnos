"""The generic maintenance barrier proves the running topology idle.

`MAINTENANCE_TARGETS_V1` is the legacy bindings list: the entry points the F4
transition retired.  They are masked, so asking systemd whether they are
stopped always answers yes, while the services that really run carry the same
names in system scope.  A barrier that consults only that list accepts a
maintenance window with the worker still writing.

The release path is deliberately not covered by this addition: it already
stops and verifies the units of the catalog it is given, and its successor
process legitimately holds a catalog different from the one still selected.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from types import SimpleNamespace

import pytest

import contract_cutover_guard as guard


pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="the maintenance guard controls the Linux systemd stack",
)

PRODUCTIVE_UNITS_V1 = (
    ("system", "metnos-durable-worker.service"),
    ("system", "metnos-http.service"),
)


def _reconciler(states: dict[tuple[str, str], str]) -> SimpleNamespace:
    """A stub whose legacy targets are all idle, like the masked real ones.

    It answers per scope, because that is the whole point: the retired user
    unit and the productive system unit share a name.
    """

    class Systemctl:
        @staticmethod
        def show(unit: str, scope: str) -> dict[str, object]:
            active = states.get((scope, unit), "inactive")
            return {
                "LoadState": "loaded",
                "ActiveState": active,
                "MainPID": 4242 if active == "active" else 0,
            }

    return SimpleNamespace(
        systemctl=Systemctl(),
        require_quiescent=lambda: {
            "source": "inactive_http_and_inactive_sidecar",
        },
    )


@pytest.fixture
def exclusion(monkeypatch):
    import stack_reconcile

    @contextmanager
    def lock(**_kwargs):
        yield

    monkeypatch.setattr(stack_reconcile, "catalog_reconcile_lock", lock)


def test_running_productive_unit_blocks_a_barrier_the_legacy_list_accepts(
        monkeypatch, exclusion):
    """The defect itself: legacy targets idle, the worker still writing."""
    import stack_reconcile
    from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1

    running = ("system", "metnos-durable-worker.service")
    # The legacy list holds only the retired user counterpart of this name.
    assert running not in MAINTENANCE_TARGETS_V1
    assert ("user", running[1]) in MAINTENANCE_TARGETS_V1
    reconciler = _reconciler({running: "active"})
    monkeypatch.setattr(
        stack_reconcile, "StackReconciler",
        lambda default_write_report=False: reconciler,
    )
    monkeypatch.setattr(
        guard, "_installed_service_units_v1", lambda: PRODUCTIVE_UNITS_V1,
    )
    with pytest.raises(guard.ContractCutoverGuardError) as refused:
        with guard.contract_cutover_guard():
            pytest.fail("maintenance entered with a productive writer running")
    assert refused.value.code == "cutover_blocked"
    assert running[1] in refused.value.detail


def test_idle_productive_topology_still_enters_maintenance(
        monkeypatch, exclusion):
    import stack_reconcile
    from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1

    monkeypatch.setattr(
        stack_reconcile, "StackReconciler",
        lambda default_write_report=False: _reconciler({}),
    )
    monkeypatch.setattr(
        guard, "_installed_service_units_v1", lambda: PRODUCTIVE_UNITS_V1,
    )
    with guard.contract_cutover_guard() as (_session, evidence):
        # The added observations stay outside the historical proof schema, so
        # the bytes a topology transition compares are unchanged.
        assert tuple(
            (item["scope"], item["unit"]) for item in evidence["units"]
        ) == MAINTENANCE_TARGETS_V1


def test_unreadable_installed_topology_refuses_instead_of_accepting(
        monkeypatch, exclusion):
    import stack_reconcile

    def unreadable():
        raise guard.ContractCutoverGuardError(
            "quiescence_unknown", "installed service catalog is unreadable",
        )

    monkeypatch.setattr(
        stack_reconcile, "StackReconciler",
        lambda default_write_report=False: _reconciler({}),
    )
    monkeypatch.setattr(guard, "_installed_service_units_v1", unreadable)
    with pytest.raises(guard.ContractCutoverGuardError) as refused:
        with guard.contract_cutover_guard():
            pytest.fail("maintenance entered over an unknown topology")
    assert refused.value.code == "quiescence_unknown"


def test_first_installation_has_no_topology_to_add(monkeypatch, exclusion):
    """Phase 3 of the first installation reaches the barrier before any chain."""
    import stack_reconcile

    monkeypatch.setattr(
        stack_reconcile, "StackReconciler",
        lambda default_write_report=False: _reconciler({}),
    )
    monkeypatch.setattr(guard, "_installed_service_units_v1", lambda: None)
    with guard.contract_cutover_guard() as (_session, evidence):
        assert evidence["source"] == "inactive_http_and_inactive_sidecar"


def test_release_path_keeps_its_own_catalog_and_never_reads_the_selected_one(
        monkeypatch):
    """A successor's catalog may legitimately differ from the selected one."""
    import stack_reconcile
    from install import executor_birth_systemd_quiescence as quiescence

    catalog, unit = object(), "metnos-future.timer"
    plan = quiescence.SystemdQuiescencePlanV1(
        (quiescence.SystemdQuiescenceBatchV1("system", (unit,)),),
        "sha256:" + "1" * 64,
    )
    monkeypatch.setattr(
        quiescence, "_plan_release_systemd_quiescence_v1",
        lambda value: plan if value is catalog else pytest.fail("catalog changed"),
    )
    monkeypatch.setattr(
        guard, "_installed_service_units_v1",
        lambda: pytest.fail("the release path read the selected catalog"),
    )
    monkeypatch.setattr(
        stack_reconcile, "catalog_reconcile_lock",
        lambda **_kwargs: pytest.fail("unbound lifecycle lock acquired"),
    )
    # The owner binding is refused before any unit is stopped, which is enough
    # to prove the installed reader was never consulted on this path.
    with pytest.raises(guard.ContractCutoverGuardError):
        with guard._contract_cutover_guard_core_v1(
                _reconciler({}), release_catalog=catalog,
                catalog_trusted_owner=(0, 0)):
            pytest.fail("unbound release entered maintenance")


def test_initial_state_passes_and_damaged_state_refuses(monkeypatch, tmp_path):
    """The reader itself: only genuinely initial state has nothing to observe."""
    import executor_birth_ownership_chain as chain

    def refuse():
        raise chain.OwnershipChainError(
            "birth_ownership_recovery_required", "productive store",
        )

    # No root at all: the first installation, before any chain exists.
    monkeypatch.setattr(
        chain, "DEFAULT_OWNERSHIP_CHAIN_ROOT_V1", tmp_path / "absent",
    )
    assert guard._installed_service_units_v1() is None

    # The root exists but holds no chain yet: still initial, still nothing.
    present = tmp_path / "chain-v1"
    present.mkdir()
    monkeypatch.setattr(chain, "DEFAULT_OWNERSHIP_CHAIN_ROOT_V1", present)
    monkeypatch.setattr(
        chain, "inspect_ownership_chain_state_v1",
        lambda: chain._mint_initial_ownership_chain_state_v1(present),
    )
    assert guard._installed_service_units_v1() is None

    # The root exists and the chain cannot be read: that is not initial.
    monkeypatch.setattr(chain, "inspect_ownership_chain_state_v1", refuse)
    with pytest.raises(guard.ContractCutoverGuardError) as refused:
        guard._installed_service_units_v1()
    assert refused.value.code == "quiescence_unknown"
