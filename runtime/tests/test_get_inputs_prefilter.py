"""Test prefilter injection per `get_inputs` (ADR 0090).

Quando l'object detected dall'intent extractor e' `inputs`, il prefilter
deve iniettare `get_inputs` nel pool top-K cosi' il PLANNER lo vede
subito invece di scivolare a `request_new_executor`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


def test_object_primary_tools_includes_inputs():
    from prefilter import _OBJECT_PRIMARY_TOOLS
    assert "inputs" in _OBJECT_PRIMARY_TOOLS
    assert "get_inputs" in _OBJECT_PRIMARY_TOOLS["inputs"]


def test_vocab_includes_inputs_object():
    from vocab import OBJECTS
    assert "inputs" in OBJECTS
    # 15 objects: indices declassato a qualifier "modalita'" il 5/5/2026 (CLAUDE.md §2.2)
    assert len(OBJECTS) == 15


def test_get_inputs_in_canonical_naming():
    """`get_inputs` rispetta la struttura `verbo_oggetto`: verb=get, obj=inputs."""
    from vocab import ACTIONS, OBJECTS
    assert "get" in ACTIONS
    assert "inputs" in OBJECTS
