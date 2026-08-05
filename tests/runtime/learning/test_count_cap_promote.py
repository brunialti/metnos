"""E.2 (6/7): `_promote_count_cap` — gemello di _demote_overtight_caps.
Una quantità nella clausola («3 foto», «primi 5») inietta il cap se il
proposer l'ha omesso; ignora anni e finestre temporali."""
from __future__ import annotations

import sys
from pathlib import Path

_RT = (Path(__file__).resolve().parents[3] / "runtime")

import os
os.environ.setdefault("METNOS_ENGINE", "v3")

from engine.dispatch import _promote_count_cap  # noqa: E402

_SCHEMA = {"properties": {"max_results": {"type": "integer", "default": 50},
                          "pattern": {"type": "string"}}}


def test_injects_count_when_absent():
    a = {"pattern": "*"}
    _promote_count_cap(a, _SCHEMA, "mostrami 3 foto della zip line")
    assert a["max_results"] == 3


def test_selector_frame_injects():
    a = {}
    _promote_count_cap(a, _SCHEMA, "elenca i primi 5 file")
    assert a["max_results"] == 5


def test_tighter_existing_cap_kept():
    a = {"max_results": 2}
    _promote_count_cap(a, _SCHEMA, "primi 5 file")
    assert a["max_results"] == 2   # 2 < 5 → non allargare


def test_year_is_not_a_count():
    a = {}
    _promote_count_cap(a, _SCHEMA, "foto del 2020")
    assert "max_results" not in a


def test_time_window_is_not_a_count():
    a = {}
    _promote_count_cap(a, _SCHEMA, "file degli ultimi 12 mesi")
    assert "max_results" not in a


def test_no_declared_cap_no_injection():
    a = {}
    _promote_count_cap(a, {"properties": {"pattern": {"type": "string"}}},
                       "3 foto")
    assert a == {}


def test_no_quantity_no_op():
    a = {}
    _promote_count_cap(a, _SCHEMA, "tutte le foto")
    assert a == {}
