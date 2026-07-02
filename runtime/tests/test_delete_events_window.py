"""Test delete_events: selezione interna per `time_window` (§2.1).

"Elimina gli eventi di ieri" = UN passo diretto: l'executor risolve la
finestra internamente (parser canonico `time_window_parser` + filtro del
backend `read`, §7.2) e cancella, senza obbligare il piano
read_events -> delete_events a 2 step.

Copertura:
  1. time_window cancella SOLO gli eventi nella finestra (backend local
     ICS su storage temporaneo: nessuna chiamata reale).
  2. Finestra senza eventi -> ok con results vuoti (onesto §2.8, no errore).
  3. time_window + id espliciti -> invalid_args (mutuamente esclusivi).
  4. time_window non riconosciuta -> invalid_args, storage INTATTO.
  5. max_total cap esplicito -> truncation visibility §2.7 (intentional).
  6. results espongono id/uid cancellati + vevent (contratto undo §2.3).
  7. Propagazione needs_inputs / errore read dal backend (stub, no rete).
  8. Comportamento per id espliciti invariato (regressione).

Run: `python3 -m pytest runtime/tests/test_delete_events_window.py -v`.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_EXEC_DIR = Path(__file__).resolve().parents[2] / "executors/delete_events"
_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_EXEC_DIR))
sys.path.insert(0, str(_RUNTIME))

ROME = ZoneInfo("Europe/Rome")


@pytest.fixture
def de(monkeypatch, tmp_path):
    """Re-import delete_events fresh + backend local su ICS temporaneo.

    `default_event_client` forzato a 'local': senza patch, con OAuth presente i
    test girerebbero contro Google Calendar reale (vietato: stub only).
    """
    if "delete_events" in sys.modules:
        del sys.modules["delete_events"]
    mod = importlib.import_module("delete_events")
    monkeypatch.setattr(mod, "default_event_client", lambda: "local")
    ics = tmp_path / "calendar.ics"
    monkeypatch.setenv("METNOS_CALENDAR_ICS", str(ics))
    return mod


def _mk_event(summary: str, start: datetime, minutes: int = 60) -> str:
    """Crea un evento nel calendar ICS temporaneo; ritorna lo uid."""
    from backends.events import local_ics
    r = local_ics.create({
        "summary": summary,
        "start": start.isoformat(),
        "end": (start + timedelta(minutes=minutes)).isoformat(),
    })
    assert r["ok"], r
    return r["results"][0]["uid"]


def _at(day: datetime, hour: int) -> datetime:
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def _seed_calendar():
    """2 eventi IERI (10:00, 15:00) + 1 evento OGGI (10:00). Date relative a
    now: 'yesterday' e' sempre la finestra giusta, niente date hardcoded."""
    now = datetime.now(tz=ROME)
    yday = now - timedelta(days=1)
    uid_y1 = _mk_event("riunione-ieri-mattina", _at(yday, 10))
    uid_y2 = _mk_event("riunione-ieri-pomeriggio", _at(yday, 15))
    uid_today = _mk_event("riunione-oggi", _at(now, 10))
    return uid_y1, uid_y2, uid_today


def _read_window(window: str) -> list[dict]:
    from backends.events import local_ics
    r = local_ics.read({"time_window": window, "top_k": 0})
    assert r["ok"], r
    return r["entries"]


# --------------------------------------------------------------------------
# 1+6. time_window cancella solo la finestra; results con id per undo §2.3
# --------------------------------------------------------------------------

def test_window_deletes_only_window_events(de):
    uid_y1, uid_y2, uid_today = _seed_calendar()
    r = de.invoke({"time_window": "yesterday", "client": "local"})
    assert r["ok"] is True
    assert r["n_deleted"] == 2
    assert r["n_matched"] == 2
    assert r["time_window"] == "yesterday"
    # §2.3 contratto undo: i results elencano gli id cancellati...
    deleted_ids = {rec["id"] for rec in r["results"] if rec.get("ok")}
    assert deleted_ids == {uid_y1, uid_y2}
    # ...e per LOCAL ICS anche il blocco vevent per il restore verbatim.
    assert all(rec.get("vevent") for rec in r["results"] if rec.get("ok"))
    # L'evento di oggi e' INTATTO (mai overlap con la finestra yesterday).
    today = _read_window("today")
    assert [e["uid"] for e in today] == [uid_today]
    assert _read_window("yesterday") == []


def test_window_with_no_match_is_honest_noop(de):
    """§2.8: finestra senza eventi = ok + results vuoti, non errore finto."""
    now = datetime.now(tz=ROME)
    _mk_event("riunione-oggi", _at(now, 10))
    r = de.invoke({"time_window": "yesterday", "client": "local"})
    assert r["ok"] is True
    assert r["results"] == []
    assert r["n_deleted"] == 0
    assert r["n_matched"] == 0
    assert r["used"] == 0
    # Nessun side-effect sull'evento fuori finestra.
    assert len(_read_window("today")) == 1


# --------------------------------------------------------------------------
# 3+4. Validazione: mutua esclusione e spec invalida (zero side-effect)
# --------------------------------------------------------------------------

def test_window_and_explicit_ids_are_mutually_exclusive(de):
    _seed_calendar()
    for extra in ({"event_id": "x"},
                  {"event_ids": ["x", "y"]},
                  {"entries": [{"id": "x"}]}):
        r = de.invoke({"time_window": "yesterday", "client": "local", **extra})
        assert r["ok"] is False, extra
        assert r["error_class"] == "invalid_args"
        assert "time_window" in r["error"]
        assert r["results"] == []
    # Ambiguita' rifiutata = niente cancellazioni.
    assert len(_read_window("yesterday")) == 2


def test_invalid_window_rejected_storage_intact(de):
    _seed_calendar()
    r = de.invoke({"time_window": "not-a-window", "client": "local"})
    assert r["ok"] is False
    assert r["error_class"] == "invalid_args"
    assert "not-a-window" in r["error"]
    assert len(_read_window("yesterday")) == 2
    assert len(_read_window("today")) == 1


def test_invalid_max_total_rejected(de):
    _seed_calendar()
    r = de.invoke({"time_window": "yesterday", "max_total": "molti",
                   "client": "local"})
    assert r["ok"] is False
    assert r["error_class"] == "invalid_args"
    r = de.invoke({"time_window": "yesterday", "max_total": -1,
                   "client": "local"})
    assert r["ok"] is False
    assert r["error_class"] == "invalid_args"
    assert len(_read_window("yesterday")) == 2


# --------------------------------------------------------------------------
# 5. Cap esplicito max_total -> truncation visibility §2.7
# --------------------------------------------------------------------------

def test_max_total_caps_with_truncation_visibility(de):
    uid_y1, uid_y2, _ = _seed_calendar()
    r = de.invoke({"time_window": "yesterday", "max_total": 1,
                   "client": "local"})
    assert r["ok"] is True
    assert r["n_deleted"] == 1
    # Read ordina per start: cancellato il primo (mattina), resta il pomeriggio.
    assert r["results"][0]["id"] == uid_y1
    assert [e["uid"] for e in _read_window("yesterday")] == [uid_y2]
    # §2.7: cap raggiunto -> visibility completa, intentional (cap richiesto).
    assert r["truncated"] is True
    from messages import get as _msg
    assert r["truncated_what"] == _msg("MSG_OBJECT_EVENTS")
    assert r["available_total"] == 2
    assert r["cap_field"] == "max_total"
    assert r["cap_value"] == 1
    assert r["truncated_intentional"] is True
    assert r["used"] == 1


def test_max_total_zero_means_no_cap(de):
    _seed_calendar()
    r = de.invoke({"time_window": "yesterday", "max_total": 0,
                   "client": "local"})
    assert r["ok"] is True
    assert r["n_deleted"] == 2
    assert r.get("truncated") is not True


# --------------------------------------------------------------------------
# 7. Stub del provider: needs_inputs / errore read propagati (no rete)
# --------------------------------------------------------------------------

class _StubBackend:
    """Provider calendar finto: read scriptato, delete registra le chiamate."""

    def __init__(self, read_out):
        self._read_out = read_out
        self.delete_calls = []

    def read(self, args):
        return self._read_out

    def delete(self, args):
        self.delete_calls.append(args)
        ids = [e.get("uid") or e.get("id") for e in args.get("entries") or []]
        return {"ok": True, "n_deleted": len(ids),
                "results": [{"ok": True, "id": i, "uid": i} for i in ids]}


def test_window_propagates_needs_inputs_without_deleting(de, monkeypatch):
    stub = _StubBackend({"ok": True, "decision": "needs_inputs",
                         "needs_inputs": {"title": "setup"},
                         "entries": [], "used": 0})
    monkeypatch.setitem(de._HANDLERS, "local", stub)
    r = de.invoke({"time_window": "yesterday", "client": "local"})
    assert r.get("decision") == "needs_inputs"
    assert stub.delete_calls == []  # mai cancellare prima del setup


def test_window_propagates_read_error(de, monkeypatch):
    stub = _StubBackend({"ok": False, "error": "boom",
                         "error_class": "auth_required",
                         "entries": [], "used": 0})
    monkeypatch.setitem(de._HANDLERS, "local", stub)
    r = de.invoke({"time_window": "yesterday", "client": "local"})
    assert r["ok"] is False
    assert r["error_class"] == "auth_required"
    assert r["results"] == []
    assert stub.delete_calls == []


def test_window_entries_flow_into_backend_delete(de, monkeypatch):
    """La composizione interna passa le entries del read al delete del
    backend (uid|id), qualunque sia il provider (google usa `id`)."""
    stub = _StubBackend({"ok": True,
                         "entries": [{"id": "g-1"}, {"id": "g-2"}],
                         "used": 2})
    monkeypatch.setitem(de._HANDLERS, "local", stub)
    r = de.invoke({"time_window": "last-7d", "client": "local"})
    assert r["ok"] is True
    assert r["n_deleted"] == 2
    assert {rec["id"] for rec in r["results"]} == {"g-1", "g-2"}
    assert r["n_matched"] == 2
    assert r["time_window"] == "last-7d"
    # Il delete del backend ha ricevuto entries, NON una time_window residua.
    assert "time_window" not in stub.delete_calls[0]
    assert "max_total" not in stub.delete_calls[0]


# --------------------------------------------------------------------------
# 8. Regressione: percorso per id espliciti invariato
# --------------------------------------------------------------------------

def test_explicit_ids_path_unchanged(de):
    uid_y1, uid_y2, uid_today = _seed_calendar()
    r = de.invoke({"event_ids": [uid_y1], "client": "local"})
    assert r["ok"] is True
    assert r["n_deleted"] == 1
    assert r["results"][0]["id"] == uid_y1
    # Niente campi finestra nel percorso per id.
    assert "time_window" not in r
    assert [e["uid"] for e in _read_window("yesterday")] == [uid_y2]
    assert [e["uid"] for e in _read_window("today")] == [uid_today]
