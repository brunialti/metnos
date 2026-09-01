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
