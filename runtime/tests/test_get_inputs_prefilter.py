"""Test prefilter injection per `get_inputs` (ADR 0090).

Quando l'object detected dall'intent extractor e' `inputs`, il prefilter
deve iniettare `get_inputs` nel pool top-K cosi' il PLANNER lo vede
subito invece di scivolare a `request_new_executor`.
"""
from __future__ import annotations

import sys
from pathlib import Path


_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


def test_object_primary_tools_includes_inputs():
    from prefilter import _OBJECT_PRIMARY_TOOLS
    assert "inputs" in _OBJECT_PRIMARY_TOOLS
    assert "get_inputs" in _OBJECT_PRIMARY_TOOLS["inputs"]


def test_vocab_includes_inputs_object():
    from vocab import OBJECTS
    assert "inputs" in OBJECTS
    assert "credentials" in OBJECTS
    assert "entries" in OBJECTS
    # 19 oggetti: `indices` declassato a qualifier "modalita'" 5/5/2026,
    # `credentials` aggiunto come 16° object 10/5/2026 (ADR 0123),
    # `entries` formalizzato come 17° object 12/5/2026 (meta-oggetto
    # pipeline in-memory). `tasks` 18° object 15/5/2026 (scheduler v2
    # ADR 0112). `persons` 19° object 15/5/2026 (registro nominale
    # ADR 0113). Vedi ADR 0137.
    assert len(OBJECTS) == 19


def test_get_inputs_in_canonical_naming():
    """`get_inputs` rispetta la struttura `verbo_oggetto`: verb=get, obj=inputs."""
    from vocab import ACTIONS, OBJECTS
    assert "get" in ACTIONS
    assert "inputs" in OBJECTS
