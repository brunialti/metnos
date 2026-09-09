from __future__ import annotations

import copy
from contextlib import contextmanager
import os
import pickle
from types import SimpleNamespace

import pytest

import contract_cutover_guard as guard


pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="the maintenance guard controls the Linux systemd stack",
)


@pytest.mark.parametrize("idle", (True, False))
def test_successor_stops_under_exclusion_and_rechecks_additional_units(monkeypatch, idle):
    import stack_reconcile
    from install import executor_birth_systemd_quiescence as quiescence
    from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
    from executor_birth_ownership_preflight import canonical_maintenance_proof

    unit, catalog = "metnos-future.timer", object()
    events, held = [], []
    state = {"active": "active"}
    plan = quiescence.SystemdQuiescencePlanV1(
        (quiescence.SystemdQuiescenceBatchV1("system", (unit,)),),
        "sha256:" + "1" * 64,
    )
    monkeypatch.setattr(quiescence, "_plan_release_systemd_quiescence_v1", lambda value: (
        plan if value is catalog else pytest.fail("release catalog changed")
    ))

    class Effects:
        def observe(self, scope, name, _snapshot):
            assert held == [True] and (scope, name) == ("system", unit)
            return quiescence.SystemdUnitObservationV1(
                scope, name, "loaded", state["active"], "enabled", 0,
            )

        def apply(self, action, batch, _snapshot):
            assert held == [True] and action == "stop" and batch.units == (unit,)
            events.append("stop")
            state["active"] = "inactive"

    class Systemctl:
        def show(self, name, _scope):
            assert held == [True]
            return {
                "LoadState": "loaded", "MainPID": 0,
                "ActiveState": state["active"] if name == unit else "inactive",
            }

    @contextmanager
    def exclusion(**_kwargs):
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    reconciler = SimpleNamespace(systemctl=Systemctl(), require_quiescent=lambda: {
        "ok": idle, "source": "inactive_http_and_inactive_sidecar",
    })
    monkeypatch.setattr(stack_reconcile, "catalog_reconcile_lock", exclusion)
    monkeypatch.setattr(quiescence, "_SubprocessSystemdEffectsV1", Effects)
    if not idle:
        with pytest.raises(quiescence.SystemdQuiescenceError):
            with guard._contract_cutover_guard_core_v1(reconciler, release_catalog=catalog):
                pytest.fail("busy release entered maintenance")
        assert not events and not held
        return
    with guard._contract_cutover_guard_core_v1(reconciler, release_catalog=catalog) as (proof, evidence):
        assert events == ["stop"]
        assert tuple((item["scope"], item["unit"]) for item in evidence["units"]) == MAINTENANCE_TARGETS_V1
        encoded = canonical_maintenance_proof(**evidence)
        guard._begin_topology_transition_v1(proof, encoded)
        state["active"] = "active"
        with pytest.raises(guard.ContractCutoverGuardError):
            guard._require_maintenance_session_v1(proof)
    assert not held


def test_maintenance_session_is_live_only_inside_the_held_guard(monkeypatch):
    import stack_reconcile

    class Systemctl:
        @staticmethod
        def show(_unit: str, _scope: str) -> dict[str, object]:
            return {
                "LoadState": "loaded",
                "ActiveState": "inactive",
                "MainPID": 0,
            }

    reconciler = SimpleNamespace(
        systemctl=Systemctl(),
        require_quiescent=lambda: {
            "source": "inactive_http_and_inactive_sidecar",
        },
    )

    @contextmanager
    def exclusion():
        yield

    monkeypatch.setattr(
        stack_reconcile, "StackReconciler",
        lambda default_write_report=False: reconciler,
    )
    monkeypatch.setattr(
        stack_reconcile, "catalog_reconcile_lock",
        lambda wait_s: exclusion(),
    )

    with guard.contract_cutover_guard() as (session, evidence):
        assert evidence["source"] == "inactive_http_and_inactive_sidecar"
        guard._require_maintenance_session_v1(session)
        for attempt in (
            lambda: copy.copy(session),
            lambda: copy.deepcopy(session),
            lambda: pickle.dumps(session),
        ):
            with pytest.raises(TypeError):
                attempt()

    with pytest.raises(guard.ContractCutoverGuardError) as inactive:
        guard._require_maintenance_session_v1(session)
    assert inactive.value.code == "cutover_session_invalid"


def test_maintenance_session_rejects_a_look_alike() -> None:
    with pytest.raises(guard.ContractCutoverGuardError) as denied:
        guard._require_maintenance_session_v1(SimpleNamespace())
    assert denied.value.code == "cutover_session_invalid"


def test_store_verification_uses_owner_aware_read_only_catalog(monkeypatch):
    import loader
    import manifest_inventory
    import sign
    import skill_registry

    owner = (41, 42)
    predicate = lambda _name: True
    observed = []
    inventory = SimpleNamespace(problems=(), manifests=())
    catalog = SimpleNamespace(rejected=[], get=lambda _name: None)

    monkeypatch.setattr(
        skill_registry,
        "_skill_enabled_snapshot_for_owner_v1",
        lambda trusted_owner: (
            observed.append(("skill", trusted_owner)) or predicate
        ),
    )
    monkeypatch.setattr(
        manifest_inventory,
        "inventory_manifests",
        lambda *, skill_enabled=None: (
            observed.append(("inventory", skill_enabled)) or inventory
        ),
    )
    trusted = (("key", object()),)
    monkeypatch.setattr(
        sign, "list_trusted_publics",
        lambda: pytest.fail("transition audit reopened legacy trusted keys"),
    )
    monkeypatch.setattr(loader, "invalidate_catalog_cache", lambda: pytest.fail(
        "catalog verification invalidated process cache",
    ))

    def load_catalog(**kwargs):
        observed.append(("load", kwargs))
        return catalog

    monkeypatch.setattr(
        loader, "_load_catalog_for_cutover_audit_v1", load_catalog,
    )

    assert guard._verify_store_only_catalog_locked(
        catalog_trusted_owner=owner,
        trusted_publics=trusted,
    ) == {"bindings": 0, "loaded": 0, "retired": 0}
    assert observed == [
        ("skill", owner),
        ("inventory", predicate),
        ("load", {
            "catalog_trusted_owner": owner,
            "trusted_publics": trusted,
        }),
    ]


def test_initial_maintenance_accepts_an_absent_legacy_unit() -> None:
    class Systemctl:
        @staticmethod
        def show(_unit: str, _scope: str) -> dict[str, object]:
            return {
                "LoadState": "not-found",
                "ActiveState": "inactive",
                "MainPID": 0,
            }

    observed = guard.prove_stack_stopped(SimpleNamespace(
        systemctl=Systemctl(),
        require_quiescent=lambda: {
            "source": "inactive_http_and_inactive_sidecar",
        },
    ))
    assert observed["units"]
    assert {item["load_state"] for item in observed["units"]} == {"not-found"}


@pytest.mark.parametrize("load_state", ["loaded", "masked", "not-found"])
def test_initial_and_transition_maintenance_accept_named_quiescent_load_states(
    load_state,
) -> None:
    class Systemctl:
        @staticmethod
        def show(_unit: str, _scope: str) -> dict[str, object]:
            return {
                "LoadState": load_state,
                "ActiveState": "inactive",
                "MainPID": 0,
            }

    reconciler = SimpleNamespace(
        systemctl=Systemctl(),
        require_quiescent=lambda: {
            "source": "inactive_http_and_inactive_sidecar",
        },
    )
    for operation in (
        guard.prove_stack_stopped,
        guard._prove_transition_stack_stopped_v1,
    ):
        observed = operation(reconciler)
        assert {item["load_state"] for item in observed["units"]} == {
            load_state,
        }


def test_transition_guard_binds_user_scope_to_the_verified_account(
    monkeypatch,
) -> None:
    import stack_reconcile

    observed = []

    class Systemctl:
        def __init__(self, *, service_user: str) -> None:
            observed.append(("account", service_user))

        @staticmethod
        def _service_uid() -> int:
            return 1234

        @staticmethod
        def show(unit: str, scope: str) -> dict[str, object]:
            observed.append((scope, unit))
            return {
                "LoadState": "loaded",
                "ActiveState": "inactive",
                "MainPID": 0,
            }

    class Reconciler:
        def __init__(
            self, *, systemctl: Systemctl, default_write_report: bool,
        ) -> None:
            assert default_write_report is False
            self.systemctl = systemctl

        @staticmethod
        def require_quiescent() -> dict[str, str]:
            return {"source": "inactive_http_and_inactive_sidecar"}

    @contextmanager
    def exclusion():
        yield

    monkeypatch.setattr(stack_reconcile, "Systemctl", Systemctl)
    monkeypatch.setattr(stack_reconcile, "StackReconciler", Reconciler)
    monkeypatch.setattr(
        stack_reconcile, "catalog_reconcile_lock",
        lambda wait_s: exclusion(),
    )

    with guard._contract_cutover_guard_for_service_user_v1(
        "service-account",
    ) as (session, _evidence):
        guard._require_maintenance_session_v1(session)

    assert observed[0] == ("account", "service-account")
    assert all(
        item[0] in {"account", "system", "user"} for item in observed
    )
