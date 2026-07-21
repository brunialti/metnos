"""Test del callback `proposals_eta_aggregate` (ADR 0122).

Verifica che (a) il task sia consolidato sotto `nightly_maintenance`
(ADR 0167 ext, 2026-06-04): non piu' entry standalone ma membro della
NIGHTLY_SEQUENCE, callback ancora registrato e invocabile per chiave;
(b) la callback venga installata con la giusta chiave; (c) la callback
accetti payload e ritorni un dict shape-compatibile.
"""
from __future__ import annotations

from scheduler_v2.builtin_callbacks import (
    _BUILTIN_JOBS,
    _NIGHTLY_CONSOLIDATED,
    install_default_callbacks,
    task_proposals_eta_aggregate,
)
from scheduler_v2.daemon import SchedulerDaemon


def test_proposals_eta_aggregate_consolidated_under_nightly():
    by_name = {j["name"]: j for j in _BUILTIN_JOBS}
    # Consolidato (2026-06-04): NON e' piu' una schedule entry standalone...
    assert "proposals_eta_aggregate" not in by_name
    # ...ma e' membro della sequenza notturna eseguita da nightly_maintenance.
    assert "proposals_eta_aggregate" in _NIGHTLY_CONSOLIDATED
    assert "nightly_maintenance" in by_name
    assert by_name["nightly_maintenance"]["trigger"].startswith("daily@")


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
