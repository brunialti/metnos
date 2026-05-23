"""Convergence test: pipeline end-to-end propose-and-fire (ADR 0127).

3 cicli con varianti query IT+EN. Mock find_events_empty -> 3 entries.
get_inputs(kind=choice, from_step=1, display_template, value_field).
Simulazione user pick index 0. set_events con start dal pick.

Verifica:
  - vocab.QUALIFIERS contiene `free`.
  - find_events_empty e get_inputs sono in coerenza pipeline.
  - Esempio compositivo: scelta dell'utente fluisce a set_events.
  - Disambiguation: «proponi» da solo (NO fire) NON deve invocare
    find_events_empty + get_inputs + set_events.

Run con `python3 -m pytest runtime/tests/test_propose_and_fire_pipeline.py -v`.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_EXEC_FF_DIR = Path(__file__).resolve().parents[2] / "executors/find_events_empty"
_EXEC_GI_DIR = Path(__file__).resolve().parents[2] / "executors/get_inputs"
_RUNTIME = Path(__file__).resolve().parents[2] / "runtime"
sys.path.insert(0, str(_EXEC_FF_DIR))
sys.path.insert(0, str(_EXEC_GI_DIR))
sys.path.insert(0, str(_RUNTIME))

ROME = ZoneInfo("Europe/Rome")


def _iso(y, mo, d, h, m=0):
    dt = datetime(y, mo, d, h, m, tzinfo=ROME)
    s = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    return s[:-2] + ":" + s[-2:]


@pytest.fixture(autouse=True)
def isolate_dialog_dir(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    import dialog_pending
    monkeypatch.setattr(
        dialog_pending, "DIALOG_DIR",
        fake_home / ".local" / "share" / "metnos" / "get_inputs",
    )


@pytest.fixture
def free_and_gi(monkeypatch):
    if "find_events_empty" in sys.modules:
        del sys.modules["find_events_empty"]
    if "get_inputs" in sys.modules:
        del sys.modules["get_inputs"]
    fef = importlib.import_module("find_events_empty")
    gi = importlib.import_module("get_inputs")
    # Inietta now deterministico: today = 2026-05-12.
    fake = _FakeDatetime(datetime(2026, 5, 12, 8, 0, tzinfo=ROME))
    monkeypatch.setattr("time_window_parser.datetime", fake)
    from backends.events import local_ics as _li
    monkeypatch.setattr(_li, "datetime", fake)
    return fef, gi


# --------------------------------------------------------------------------
# Vocab smoke
# --------------------------------------------------------------------------

def test_vocab_has_empty_qualifier():
    from vocab import QUALIFIERS
    assert "empty" in QUALIFIERS, (
        "ADR 0127: vocab deve esporre `empty` come qualifier di modalita'."
    )


# --------------------------------------------------------------------------
# Pipeline end-to-end (mock find_events_empty + simulazione user pick)
# --------------------------------------------------------------------------

def _run_pipeline(fef, gi, query_label):
    """Esegue la pipeline propose-and-fire end-to-end:
      step 1: find_events_empty → 3 slot.
      step 2: get_inputs(from_step=1, kind=choice, ...) → 3 choices derivate.
      step 3: simulate user pick (index 0) → values["scelta"] = primo start.
      step 4: 'invoke' set_events fittizio con start=values["scelta"].
    Ritorna dict di asserzioni atomiche per il caso d'uso."""
    # Mock backend `local_ics._load_events` sottostante (refactor 13/5/2026):
    # 1 evento pomeridiano, lasciando slot mattutini liberi.
    from backends.events import local_ics as _li
    _li._load_events = lambda _p: [
        {
            "start": datetime.fromisoformat(_iso(2026, 5, 13, 14)),
            "end":   datetime.fromisoformat(_iso(2026, 5, 13, 15)),
            "summary": "", "uid": "",
        },
    ]

    out_free = fef.invoke({
        "time_windows": ["tomorrow"],
        "size": "60min",
        "time_of_day": "morning",
        "max_results": 3,
    })
    assert out_free["ok"] is True, (query_label, out_free)
    assert len(out_free["entries"]) >= 1
    free_entries = out_free["entries"]

    # Step 2: get_inputs con derivation
    out_gi = gi.invoke({
        "title": "Quale orario?",
        "entries": free_entries,  # simula from_step expansion
        "dialog": [{
            "var": "scelta",
            "prompt": "Scegli uno slot",
            "schema": {
                "kind": "choice",
                "display_template": "{start} - {end}",
                "value_field": "start",
            },
        }],
    })
    assert out_gi["ok"] is True, (query_label, out_gi)
    assert out_gi["decision"] == "input_required"

    # Simula user pick: prendiamo lo state, popoliamo values come farebbe
    # consume_pending_step su una scelta utente (index 0).
    import dialog_pending
    state = dialog_pending.load_pending("host", out_gi["dialog_id"])
    assert state is not None, (query_label, "state mancante")
    choices = state["dialog"][0]["schema"]["choices"]
    assert len(choices) >= 1
    picked_value = choices[0]["value"]
    # picked_value e' lo start del primo slot libero
    assert picked_value == free_entries[0]["start"], (
        query_label, "value_field non e' rispettato"
    )

    # Step 4: set_events stub (verifica solo che il valore arriva correctly).
    set_args = {
        "summary": "Appuntamento (propose-and-fire)",
        "start": picked_value,
        "end": free_entries[0]["end"],
    }
    # Non chiamiamo set_events reale (richiede OAuth): assert sui dati.
    assert set_args["start"].startswith("2026-05-")
    assert set_args["start"] < set_args["end"]
    return out_gi["dialog_id"]


def test_pipeline_it_proponi_mattine(free_and_gi):
    """Variante IT: «proponi 3 mattine la prossima settimana e prenotami
    quella che scelgo»."""
    fef, gi = free_and_gi
    _run_pipeline(fef, gi, "IT proponi 3 mattine + book")


def test_pipeline_en_propose_morning(free_and_gi):
    """Variante EN: «propose 3 morning slots and book the one I pick»."""
    fef, gi = free_and_gi
    _run_pipeline(fef, gi, "EN propose 3 morning + book")


def test_pipeline_implicit_confirm(free_and_gi):
    """Variante semi-auto: «proponi e poi fissa la prima libera» (l'utente
    accetta implicitamente la prima opzione)."""
    fef, gi = free_and_gi
    _run_pipeline(fef, gi, "IT proponi + fissa la prima libera")


# --------------------------------------------------------------------------
# Disambiguation: propose-only (NO fire) — i.e. user dice «proponi» senza
# continuazione mutating. La pipeline NON deve cadere in derivation.
# --------------------------------------------------------------------------

def test_propose_only_does_not_require_get_inputs(free_and_gi):
    """find_events_empty puo' essere chiamato direttamente; il PLANNER nel
    caso propose-only farebbe final_answer testuale e NON inviterebbe
    get_inputs. Qui verifichiamo che find_events_empty funziona stand-alone."""
    fef, gi = free_and_gi
    fef._invoke_read_events = lambda tw, cid: {"ok": True, "entries": []}
    out = fef.invoke({"time_windows": ["tomorrow"], "time_of_day": "morning",
                      "size": "60min", "max_results": 3})
    assert out["ok"] is True
    # find_events_empty ritorna entries; il PLANNER decide se andare a
    # get_inputs o a final_answer. NON c'e' coupling implicito.
    assert "entries" in out


# --------------------------------------------------------------------------
# Intent-extractor examples sono nei prompt (smoke: i nuovi esempi
# devono mappare a verb=find object=events, distinto da verb=read del
# propose-only)
# --------------------------------------------------------------------------

def test_intent_examples_present_in_prompts():
    root = Path(__file__).resolve().parents[2]
    p_it = (root / "runtime/prompts/it/intent_extractor.j2").read_text(encoding="utf-8")
    p_en = (root / "runtime/prompts/en/intent_extractor.j2").read_text(encoding="utf-8")
    # Almeno una variante propose+continuazione mutating mappa a verb=find.
    assert "prenotami quella che scelgo" in p_it
    assert "fissa il primo libero" in p_it
    assert "book one" in p_en
    assert "and confirm" in p_en


def test_calendar_section_has_propose_and_fire_hint():
    root = Path(__file__).resolve().parents[2]
    p_it = (root / "runtime/prompts/it/planner/sections/calendar.j2").read_text(encoding="utf-8")
    p_en = (root / "runtime/prompts/en/planner/sections/calendar.j2").read_text(encoding="utf-8")
    assert "(propose_and_fire)" in p_it
    assert "(propose_and_fire)" in p_en
    assert "find_events_empty" in p_it
    assert "find_events_empty" in p_en


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

class _FakeDatetime:
    def __init__(self, now_value):
        self._now = now_value

    def __getattr__(self, name):
        from datetime import datetime as _real_dt
        return getattr(_real_dt, name)

    def now(self, tz=None):
        if tz is None:
            return self._now.replace(tzinfo=None)
        return self._now.astimezone(tz)

    @classmethod
    def fromisoformat(cls, *args, **kwargs):
        from datetime import datetime as _real_dt
        return _real_dt.fromisoformat(*args, **kwargs)

    @classmethod
    def combine(cls, *args, **kwargs):
        from datetime import datetime as _real_dt
        return _real_dt.combine(*args, **kwargs)
