"""Test per la generalizzazione `kind=choice` con `from_step`/`display_template`/
`value_field` (ADR 0127, propose-and-fire).

Copertura:
  1. from_step lookup tramite arg `entries` iniettato dal runtime.
  2. display_template formatting (substitution `{campo}`).
  3. value_field extraction (campo singolo).
  4. options vs from_step priority (esplicite vincono).
  5. malformed template error (placeholder mancante).
  6. multi_choice variant con derivation.
  7. entries vuote → errore esplicito.

Run con `python3 -m pytest runtime/tests/test_get_inputs_choice_from_step.py -v`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
_EXECUTORS = _RUNTIME.parent / "executors" / "get_inputs"
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_EXECUTORS))


@pytest.fixture(autouse=True)
def isolate_dialog_dir(tmp_path, monkeypatch):
    """Isola lo storage dialog_pending in tmp_path via HOME env override."""
    # dialog_pending usa Path.home() / .local/share/metnos/get_inputs.
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    # Hot-reload dialog_pending cosi' DIALOG_DIR rilegge HOME.
    import dialog_pending
    monkeypatch.setattr(
        dialog_pending,
        "DIALOG_DIR",
        fake_home / ".local" / "share" / "metnos" / "get_inputs",
    )


@pytest.fixture
def gi():
    if "get_inputs" in sys.modules:
        del sys.modules["get_inputs"]
    import importlib
    return importlib.import_module("get_inputs")


# --------------------------------------------------------------------------
# 1. from_step lookup tramite arg `entries`
# --------------------------------------------------------------------------

def test_choice_derived_from_entries(gi):
    """Quando `entries` e' iniettato (runtime ha espanso from_step), e lo
    step ha display_template + value_field, le choices devono essere derivate."""
    entries = [
        {"start": "2026-05-13T09:00", "end": "2026-05-13T10:00",
         "duration_min": 60},
        {"start": "2026-05-13T11:00", "end": "2026-05-13T12:00",
         "duration_min": 60},
    ]
    args = {
        "title": "Quale orario?",
        "entries": entries,  # iniettato dal runtime (from_step=N expansion)
        "dialog": [{
            "var": "scelta",
            "prompt": "Scegli uno slot",
            "schema": {
                "kind": "choice",
                "display_template": "{start} - {end}",
                "value_field": "start",
            },
        }],
    }
    r = gi.invoke(args)
    assert r["ok"] is True, r
    assert r["decision"] == "input_required"


def test_derivation_preserves_label_format(gi):
    """Il label deve usare display_template esattamente."""
    entries = [{"start": "09:00", "end": "10:00"}]
    # Aggiungo un terzo per superare il check len>=2 minimo originale
    entries.append({"start": "11:00", "end": "12:00"})
    args = {
        "title": "Slot",
        "entries": entries,
        "dialog": [{
            "var": "scelta",
            "prompt": "Scegli",
            "schema": {"kind": "choice",
                        "display_template": "{start} - {end}",
                        "value_field": "start"},
        }],
    }
    # Hook: ispeziono il dialog effettivamente persistito via dialog_pending.
    r = gi.invoke(args)
    assert r["ok"] is True
    # Ricarica lo stato salvato
    import dialog_pending
    sender = "host"  # default actor + empty channel
    state = dialog_pending.load_pending(sender, r["dialog_id"])
    assert state is not None
    step = state["dialog"][0]
    choices = step["schema"]["choices"]
    labels = [c["label"] for c in choices]
    assert "09:00 - 10:00" in labels
    assert "11:00 - 12:00" in labels


# --------------------------------------------------------------------------
# 2. value_field extraction
# --------------------------------------------------------------------------

def test_value_field_extraction(gi):
    entries = [
        {"start": "T1", "end": "E1", "id": "A"},
        {"start": "T2", "end": "E2", "id": "B"},
    ]
    args = {
        "title": "Pick",
        "entries": entries,
        "dialog": [{
            "var": "scelta",
            "prompt": "?",
            "schema": {"kind": "choice",
                        "display_template": "{start}/{end}",
                        "value_field": "id"},
        }],
    }
    r = gi.invoke(args)
    assert r["ok"] is True
    import dialog_pending
    state = dialog_pending.load_pending("host", r["dialog_id"])
    choices = state["dialog"][0]["schema"]["choices"]
    values = [c["value"] for c in choices]
    assert values == ["A", "B"]


def test_no_value_field_serializes_entry(gi):
    """Senza value_field, il value e' l'intera entry serializzata JSON."""
    entries = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
    args = {
        "title": "Pick",
        "entries": entries,
        "dialog": [{
            "var": "scelta",
            "prompt": "?",
            "schema": {"kind": "choice",
                        "display_template": "x={x} y={y}"},
        }],
    }
    r = gi.invoke(args)
    assert r["ok"] is True
    import dialog_pending
    state = dialog_pending.load_pending("host", r["dialog_id"])
    choices = state["dialog"][0]["schema"]["choices"]
    import json
    assert json.loads(choices[0]["value"]) == {"x": 1, "y": 2}


# --------------------------------------------------------------------------
# 3. options vs from_step priority
# --------------------------------------------------------------------------

def test_explicit_choices_win_over_entries(gi):
    """Se sia `choices` espliciti sia entries+template sono presenti, le
    choices esplicite hanno priorita' (backwards compat)."""
    entries = [{"start": "x", "end": "y"}]
    args = {
        "title": "Pick",
        "entries": entries,
        "dialog": [{
            "var": "scelta",
            "prompt": "?",
            "schema": {
                "kind": "choice",
                "choices": ["alpha", "beta"],
                "display_template": "{start}",  # ignored
            },
        }],
    }
    r = gi.invoke(args)
    assert r["ok"] is True
    import dialog_pending
    state = dialog_pending.load_pending("host", r["dialog_id"])
    choices = state["dialog"][0]["schema"]["choices"]
    # Le choices esplicite restano com'erano (lista di stringhe).
    assert choices == ["alpha", "beta"]


# --------------------------------------------------------------------------
# 4. malformed template (missing placeholder)
# --------------------------------------------------------------------------

def test_missing_placeholder_field_uses_fallback(gi):
    """Placeholder che non esiste nell'entry → fallback '<missing:campo>'
    NON un crash. Determinismo §7.9 + robustezza al confine NL."""
    entries = [{"a": 1}, {"a": 2}]
    args = {
        "title": "Pick",
        "entries": entries,
        "dialog": [{
            "var": "scelta",
            "prompt": "?",
            "schema": {"kind": "choice",
                        "display_template": "{a} - {nonexistent}",
                        "value_field": "a"},
        }],
    }
    r = gi.invoke(args)
    assert r["ok"] is True
    import dialog_pending
    state = dialog_pending.load_pending("host", r["dialog_id"])
    choices = state["dialog"][0]["schema"]["choices"]
    # Label contiene marker missing ma value e' corretto.
    assert "missing" in choices[0]["label"].lower()
    assert choices[0]["value"] == 1


# --------------------------------------------------------------------------
# 5. multi_choice variant
# --------------------------------------------------------------------------

def test_multi_choice_with_from_step(gi):
    entries = [
        {"path": "/a.txt", "size": 100},
        {"path": "/b.txt", "size": 200},
        {"path": "/c.txt", "size": 50},
    ]
    args = {
        "title": "Quali archiviare",
        "entries": entries,
        "dialog": [{
            "var": "selez",
            "prompt": "Scegli uno o piu' file",
            "schema": {"kind": "multi_choice",
                        "display_template": "{path} ({size}B)",
                        "value_field": "path"},
        }],
    }
    r = gi.invoke(args)
    assert r["ok"] is True
    import dialog_pending
    state = dialog_pending.load_pending("host", r["dialog_id"])
    choices = state["dialog"][0]["schema"]["choices"]
    assert len(choices) == 3
    assert choices[0]["label"] == "/a.txt (100B)"
    assert choices[0]["value"] == "/a.txt"


# --------------------------------------------------------------------------
# 6. entries vuote → errore esplicito
# --------------------------------------------------------------------------

def test_empty_entries_fails_clearly(gi):
    """Se from_step espande a [], il PLANNER ha sbagliato pipeline: errore
    chiaro invece di proseguire con choice 0 opzioni."""
    args = {
        "title": "Pick",
        "entries": [],
        "dialog": [{
            "var": "scelta",
            "prompt": "?",
            "schema": {"kind": "choice",
                        "display_template": "{x}"},
        }],
    }
    r = gi.invoke(args)
    # entries=[] arg => skip derivation (resta requisito choices esplicite).
    # Senza choices nel schema, la validazione fallisce.
    assert r["ok"] is False


def test_entries_with_one_element_derives_single_choice(gi):
    """Un'entry sola: la derivazione e' ok. Cap inferiore §2.1: lista N=1
    legittima per propose-and-fire (es. unico slot libero della giornata)."""
    entries = [{"start": "09:00", "end": "10:00"}]
    args = {
        "title": "Pick",
        "entries": entries,
        "dialog": [{
            "var": "scelta",
            "prompt": "?",
            "schema": {"kind": "choice",
                        "display_template": "{start} - {end}",
                        "value_field": "start"},
        }],
    }
    r = gi.invoke(args)
    assert r["ok"] is True
    import dialog_pending
    state = dialog_pending.load_pending("host", r["dialog_id"])
    choices = state["dialog"][0]["schema"]["choices"]
    assert len(choices) == 1
    assert choices[0]["value"] == "09:00"


# --------------------------------------------------------------------------
# 7. Backwards compatibility (caso legacy con choices esplicite)
# --------------------------------------------------------------------------

def test_legacy_choices_still_work(gi):
    """Senza from_step/entries/display_template: choices esplicite >=2."""
    args = {
        "title": "Legacy",
        "dialog": [{
            "var": "kind",
            "prompt": "Tipo?",
            "schema": {"kind": "choice",
                        "choices": ["one", "two", "three"]},
        }],
    }
    r = gi.invoke(args)
    assert r["ok"] is True


def test_legacy_validation_missing_choices_still_fails(gi):
    """Senza choices/display_template/from_entries: errore chiaro."""
    args = {
        "title": "Bad",
        "dialog": [{
            "var": "kind",
            "prompt": "?",
            "schema": {"kind": "choice"},
        }],
    }
    r = gi.invoke(args)
    assert r["ok"] is False
    assert "choices" in r["error"] or "display_template" in r["error"]
