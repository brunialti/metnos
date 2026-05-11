"""Test del callback `proposals_eta_aggregate` (ADR 0122).

Verifica che (a) la job entry sia registrata in _BUILTIN_JOBS con il
trigger daily@04:30, (b) la callback venga installata con la giusta
chiave, (c) la callback accetti payload e ritorni un dict shape-compatibile.
"""
from __future__ import annotations

from scheduler_v2.builtin_callbacks import (
    _BUILTIN_JOBS,
    install_default_callbacks,
    task_proposals_eta_aggregate,
)
from scheduler_v2.daemon import SchedulerDaemon


def test_proposals_eta_aggregate_in_builtin_jobs():
    by_name = {j["name"]: j for j in _BUILTIN_JOBS}
    assert "proposals_eta_aggregate" in by_name
    job = by_name["proposals_eta_aggregate"]
    assert job["trigger"] == "daily@04:30"
    assert job["callback_key"] == "proposals_eta_aggregate"


def test_proposals_eta_aggregate_callback_registered(db_path):
    d = SchedulerDaemon(db_path)
    install_default_callbacks(d)
    info = d.callbacks.get("proposals_eta_aggregate")
    assert info is not None


def test_task_proposals_eta_aggregate_returns_shape(monkeypatch, tmp_path):
    """Smoke: la task non solleva e ritorna dict con `ok`/`shapes`/`samples`."""
    # Redirige HOME a tmp_path cosi' niente files reali toccati.
    monkeypatch.setenv("HOME", str(tmp_path))
    rep = task_proposals_eta_aggregate()
    assert isinstance(rep, dict)
    assert rep.get("ok") is True
    assert "shapes" in rep
    assert "samples" in rep
    assert "files_read" in rep
