"""Test compute_lists (set ops + aggregates su 1+ liste, ADR 15/5/2026)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                          / "executors" / "compute_lists"))
import compute_lists as cl  # noqa: E402


# ----- Set ops --------------------------------------------------------------

def test_intersect_on_single_key():
    a = [{"path": "x"}, {"path": "y"}, {"path": "z"}]
    b = [{"path": "y"}, {"path": "z"}, {"path": "w"}]
    r = cl.invoke({"op": "intersect", "entries": a, "entries_b": b,
                    "on_keys": ["path"]})
    assert r["ok"] is True
    paths = sorted(e["path"] for e in r["entries"])
    assert paths == ["y", "z"]


def test_intersect_on_composite_keys():
    a = [{"lat": 1.0, "lon": 2.0, "k": "A"},
         {"lat": 3.0, "lon": 4.0, "k": "B"}]
    b = [{"lat": 1.0, "lon": 2.0, "k": "X"}]
    r = cl.invoke({"op": "intersect", "entries": a, "entries_b": b,
                    "on_keys": ["lat", "lon"]})
    assert r["ok"] is True
    assert len(r["entries"]) == 1
    assert r["entries"][0]["k"] == "A"


def test_difference():
    a = [{"id": 1}, {"id": 2}, {"id": 3}]
    b = [{"id": 2}]
    r = cl.invoke({"op": "difference", "entries": a, "entries_b": b,
                    "on_keys": ["id"]})
    assert r["ok"] is True
    ids = sorted(e["id"] for e in r["entries"])
    assert ids == [1, 3]


def test_union_dedup():
    a = [{"k": "a"}, {"k": "b"}]
    b = [{"k": "b"}, {"k": "c"}]
    r = cl.invoke({"op": "union", "entries": a, "entries_b": b,
                    "on_keys": ["k"]})
    assert r["ok"] is True
    ks = sorted(e["k"] for e in r["entries"])
    assert ks == ["a", "b", "c"]


def test_symdiff():
    a = [{"k": "a"}, {"k": "b"}, {"k": "c"}]
    b = [{"k": "b"}, {"k": "d"}]
    r = cl.invoke({"op": "symdiff", "entries": a, "entries_b": b,
                    "on_keys": ["k"]})
    assert r["ok"] is True
    ks = sorted(e["k"] for e in r["entries"])
    assert ks == ["a", "c", "d"]


def test_set_op_without_on_keys_errors():
    r = cl.invoke({"op": "intersect", "entries": [{"x": 1}],
                    "entries_b": [{"x": 1}]})
    assert r["ok"] is False
    assert "on_keys" in r["error"]


def test_intersect_missing_key_skips_entry():
    """Entry senza la key cercata viene saltata."""
    a = [{"id": 1}, {"name": "no_id"}, {"id": 2}]
    b = [{"id": 1}, {"id": 2}]
    r = cl.invoke({"op": "intersect", "entries": a, "entries_b": b,
                    "on_keys": ["id"]})
    assert r["ok"] is True
    assert len(r["entries"]) == 2


# ----- Overlap (temporal AND) ----------------------------------------------

def test_overlap_temporal_auto_detect():
    a = [{"start": "2026-01-01T10:00:00", "end": "2026-01-01T11:00:00",
          "summary": "HLT-1"}]
    b = [{"start": "2026-01-01T10:30:00", "end": "2026-01-01T11:30:00",
          "summary": "MNM-1"}]
    r = cl.invoke({"op": "overlap", "entries": a, "entries_b": b})
    assert r["ok"] is True
    assert len(r["entries"]) == 1
    assert "MNM-1" in r["entries"][0]["_overlap_with"][0]


def test_overlap_disjoint_excluded():
    a = [{"start": "2026-01-01T10:00:00", "end": "2026-01-01T11:00:00",
          "summary": "A"}]
    b = [{"start": "2026-01-01T14:00:00", "end": "2026-01-01T15:00:00",
          "summary": "B"}]
    r = cl.invoke({"op": "overlap", "entries": a, "entries_b": b})
    assert r["ok"] is True
    assert len(r["entries"]) == 0


def test_overlap_with_instantaneous_entry():
    a = [{"taken_at_iso": "2026-01-01T10:30:00", "name": "foto.jpg"}]
    b = [{"start": "2026-01-01T10:00:00", "end": "2026-01-01T11:00:00",
          "summary": "evento"}]
    r = cl.invoke({"op": "overlap", "entries": a, "entries_b": b})
    assert r["ok"] is True
    assert len(r["entries"]) == 1


# ----- Aggregates -----------------------------------------------------------

def test_count():
    r = cl.invoke({"op": "count",
                     "entries": [{"x": 1}, {"x": 2}, {"x": 3}, {"x": 4}]})
    assert r["ok"] is True
    assert r["value"] == 4


def test_sum_field():
    r = cl.invoke({"op": "sum", "field": "size",
                     "entries": [{"size": 10}, {"size": 20}, {"size": 30}]})
    assert r["ok"] is True
    assert r["value"] == 60
    assert r["count"] == 3


def test_avg_field():
    r = cl.invoke({"op": "avg", "field": "score",
                     "entries": [{"score": 4.0}, {"score": 6.0}]})
    assert r["ok"] is True
    assert r["value"] == 5.0


def test_max_field():
    r = cl.invoke({"op": "max", "field": "x",
                     "entries": [{"x": 1}, {"x": 5}, {"x": 3}]})
    assert r["value"] == 5


def test_min_field():
    r = cl.invoke({"op": "min", "field": "x",
                     "entries": [{"x": 1}, {"x": 5}, {"x": 3}]})
    assert r["value"] == 1


def test_aggregate_without_field_errors():
    r = cl.invoke({"op": "sum", "entries": [{"x": 1}]})
    assert r["ok"] is False
    assert "field" in r["error"]


def test_aggregate_no_numeric_values_returns_zero():
    """Lista senza numeric → value=0 + note, non error."""
    r = cl.invoke({"op": "sum", "field": "missing",
                     "entries": [{"x": 1}, {"x": 2}]})
    assert r["ok"] is True
    assert r["value"] == 0
    assert r["count"] == 0


# ----- Validation ----------------------------------------------------------

def test_invalid_op_errors():
    r = cl.invoke({"op": "fakeop", "entries": []})
    assert r["ok"] is False


def test_op_required():
    r = cl.invoke({"entries": []})
    assert r["ok"] is False


def test_args_not_dict_errors():
    r = cl.invoke("not a dict")
    assert r["ok"] is False
