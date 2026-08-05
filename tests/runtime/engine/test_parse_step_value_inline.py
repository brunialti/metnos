"""Test inline del parser modulo-level `parse_step_value` (ADR 0090).

Garantisce che il parser viva al modulo, non solo come metodo della
ChannelDaemon, e che gestisca correttamente tutti i kind.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def test_yes_no_tolerant():
    from channels.daemon import parse_step_value
    assert parse_step_value("si", {"kind": "yes_no"}) == (True, True, "")
    assert parse_step_value("Sì", {"kind": "yes_no"}) == (True, True, "")
    assert parse_step_value("no", {"kind": "yes_no"}) == (True, False, "")
    ok, _, err = parse_step_value("forse", {"kind": "yes_no"})
    assert ok is False
    assert "sì" in err.lower() or "no" in err.lower()


def test_number_int_vs_float():
    from channels.daemon import parse_step_value
    ok, val, _ = parse_step_value("42", {"kind": "number"})
    assert ok and val == 42 and isinstance(val, int)
    ok, val, _ = parse_step_value("3.14", {"kind": "number"})
    assert ok and val == 3.14 and isinstance(val, float)
    ok, _, err = parse_step_value("abc", {"kind": "number"})
    assert ok is False


def test_date_iso_and_eu():
    from channels.daemon import parse_step_value
    assert parse_step_value("2026-05-04", {"kind": "date"}) == (True, "2026-05-04", "")
    ok, val, _ = parse_step_value("04/05/2026", {"kind": "date"})
    assert ok and val == "2026-05-04"
    ok, _, _ = parse_step_value("ieri", {"kind": "date"})
    assert ok is False


def test_choice_by_value_or_index():
    from channels.daemon import parse_step_value
    schema = {"kind": "choice", "choices": ["alpha", "beta", "gamma"]}
    assert parse_step_value("beta", schema) == (True, "beta", "")
    assert parse_step_value("BETA", schema) == (True, "beta", "")
    assert parse_step_value("2", schema) == (True, "beta", "")
    ok, _, err = parse_step_value("delta", schema)
    assert ok is False
    assert "alpha" in err and "beta" in err


def test_multi_choice_csv():
    from channels.daemon import parse_step_value
    schema = {"kind": "multi_choice", "choices": ["a", "b", "c", "d"]}
    ok, val, _ = parse_step_value("a, c", schema)
    assert ok and val == ["a", "c"]
    ok, val, _ = parse_step_value("1,4", schema)
    assert ok and val == ["a", "d"]


def test_empty_response_rejected():
    from channels.daemon import parse_step_value
    ok, _, err = parse_step_value("", {"kind": "text"})
    assert ok is False
    assert "vuota" in err.lower()


def test_text_credentials_passthrough():
    from channels.daemon import parse_step_value
    assert parse_step_value("hello", {"kind": "text"}) == (True, "hello", "")
    assert parse_step_value("hunter2", {"kind": "credentials"}) == (True, "hunter2", "")
