from __future__ import annotations

import copy
from contextlib import contextmanager
import pickle
from types import SimpleNamespace

import pytest

import contract_cutover_guard as guard


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
