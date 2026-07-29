"""Test prefilter injection per `get_inputs` (ADR 0090).

Quando l'object detected dall'intent extractor e' `inputs`, il prefilter
deve iniettare `get_inputs` nel pool top-K cosi' il PLANNER lo vede
subito invece di scivolare a `request_new_executor`.
"""
from __future__ import annotations

import sys
from pathlib import Path


_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def test_object_primary_tools_includes_inputs():
    from prefilter import _OBJECT_PRIMARY_TOOLS
    assert "inputs" in _OBJECT_PRIMARY_TOOLS
    assert "get_inputs" in _OBJECT_PRIMARY_TOOLS["inputs"]


def test_vocab_includes_inputs_object():
    from vocab import OBJECTS
    assert "inputs" in OBJECTS
    assert "credentials" in OBJECTS
    assert "entries" in OBJECTS
    # 21 oggetti: `indices` declassato a qualifier "modalita'" 5/5/2026,
    # `credentials` 10/5/2026 (ADR 0123), `entries` meta-oggetto pipeline
    # 12/5/2026, `tasks` 15/5/2026 (scheduler v2 ADR 0112), `persons`
    # 15/5/2026 (ADR 0113), `issues`+`pulls` provider github 2/6/2026
    # (ADR 0141). Vedi ADR 0137/0141.
    assert "issues" in OBJECTS and "pulls" in OBJECTS
    # `calendars` aggiunto (provider google_workspace: create/delete_calendars)
    assert "calendars" in OBJECTS
    # `approval` aggiunto 17/6/2026 (executor get_approval, gate di consenso
    # umano cross-skill, commit 0d36ab0) → 23° oggetto canonico.
    assert "approval" in OBJECTS
    # `sites` aggiunto 10/7/2026 (dominio interazione web sicura, spec sites F1,
    # RATIFICATO D-A) → 24° oggetto canonico.
    assert "sites" in OBJECTS
    # `preferences` aggiunto 29/7/2026 (preferenze personali gestibili dalla
    # chat: get/set/delete_preferences in user_preferences.py, W2 v1 ADR 0187).
    assert "preferences" in OBJECTS
    assert len(OBJECTS) == 27


def test_get_inputs_in_canonical_naming():
    """`get_inputs` rispetta la struttura `verbo_oggetto`: verb=get, obj=inputs."""
    from vocab import ACTIONS, OBJECTS
    assert "get" in ACTIONS
    assert "inputs" in OBJECTS
