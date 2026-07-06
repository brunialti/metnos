"""Isolamento multi-utente dell'undo (7/7/2026).

Buco scovato dall'audit owner-filter fase 7: `latest_turn_done()` prendeva
l'ultimo turno GLOBALE senza filtro actor — un guest che diceva «annulla»
poteva ribaltare l'operazione di un ALTRO utente. Il record pending porta
gia' `actor`: il filtro e' per-richiedente; `_actor` e' garantito a
undo_last_turn dal choke-point invoke_executor (copre anche il fast-path
«annulla», che non passa dall'injection dell'engine).

DB/log isolato su tmp (§ feedback_never_purge_real_stores_in_tests). No LLM.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_RT = Path(__file__).resolve().parent.parent
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))

from undo import UndoLog  # noqa: E402


def _log_with_two_actors(tmp: Path) -> UndoLog:
    log = UndoLog(tmp / "undo.jsonl")
    # turno T1 di host (piu' vecchio)
    log.append_pending("op1", "T1", "write_files", {"path": "/tmp/a"},
                       plan={}, actor="host")
    log.append_done("op1", {"ok": True, "results": [{"path": "/tmp/a"}]})
    # turno T2 del guest (piu' recente)
    log.append_pending("op2", "T2", "move_files", {"src": "x"},
                       plan={}, actor="guest:g1")
    log.append_done("op2", {"ok": True, "results": [{"src": "x"}]})
    return log


def test_unfiltered_keeps_legacy_behavior():
    with tempfile.TemporaryDirectory() as td:
        log = _log_with_two_actors(Path(td))
        recs = log.latest_turn_done()
        assert [r["turn_id"] for r in recs] == ["T2"]


def test_actor_sees_only_own_latest_turn():
    with tempfile.TemporaryDirectory() as td:
        log = _log_with_two_actors(Path(td))
        host = log.latest_turn_done(actor="host")
        assert [r["turn_id"] for r in host] == ["T1"]      # NON il T2 del guest
        guest = log.latest_turn_done(actor="guest:g1")
        assert [r["turn_id"] for r in guest] == ["T2"]


def test_actor_without_ops_gets_nothing():
    with tempfile.TemporaryDirectory() as td:
        log = _log_with_two_actors(Path(td))
        assert log.latest_turn_done(actor="guest:g2") == []


def test_missing_actor_field_counts_as_host():
    with tempfile.TemporaryDirectory() as td:
        log = UndoLog(Path(td) / "undo.jsonl")
        log.append_pending("op1", "T1", "write_files", {}, plan={})  # default host
        log.append_done("op1", {"ok": True, "results": []})
        assert [r["turn_id"] for r in log.latest_turn_done(actor="host")] == ["T1"]
        assert log.latest_turn_done(actor="guest:g1") == []


def test_executor_honest_zero_for_foreign_actor():
    """undo_last_turn con _actor estraneo → 0 annullabili, esito onesto."""
    sys.path.insert(0, str(_RT.parent / "executors" / "undo_last_turn"))
    import undo_last_turn as ult
    with tempfile.TemporaryDirectory() as td:
        _log_with_two_actors(Path(td))
        out = ult.invoke({"log_path": str(Path(td) / "undo.jsonl"),
                          "_actor": "guest:g2"})
        assert out["ok"] is True and out["undone_count"] == 0
