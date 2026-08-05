"""Test temporal overlap filter (filter_entries §7.3, 15/5/2026)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]
                          / "executors" / "filter_entries"))

import filter_entries as fe  # noqa: E402


def _ev(summary, start, end=None):
    """Helper: event entry."""
    return {"summary": summary, "start": start, "end": end or start}


def test_no_overlap_args_no_filter():
    """Senza overlap_entries, niente filter temporale."""
    a = [_ev("HLT-1", "2026-01-01T10:00:00", "2026-01-01T11:00:00")]
    r = fe.invoke({"entries": a})
    assert r["ok"] is True
    assert len(r["entries"]) == 1


def test_overlap_simple_hit():
    """HLT 10-11 overlap con MNM 10:30-11:30."""
    a = [_ev("HLT-1", "2026-01-01T10:00:00", "2026-01-01T11:00:00")]
    b = [_ev("MNM-1", "2026-01-01T10:30:00", "2026-01-01T11:30:00")]
    r = fe.invoke({"entries": a, "overlap_entries": b})
    assert r["ok"] is True
    assert len(r["entries"]) == 1
    assert "_overlap_with" in r["entries"][0]
    assert "MNM-1" in r["entries"][0]["_overlap_with"][0]


def test_overlap_disjoint_no_hit():
    """HLT 10-11 NO overlap con MNM 14-15."""
    a = [_ev("HLT-1", "2026-01-01T10:00:00", "2026-01-01T11:00:00")]
    b = [_ev("MNM-1", "2026-01-01T14:00:00", "2026-01-01T15:00:00")]
    r = fe.invoke({"entries": a, "overlap_entries": b})
    assert r["ok"] is True
    assert len(r["entries"]) == 0


def test_overlap_adjacent_touching():
    """HLT termina alle 11:00, MNM inizia alle 11:00 → overlap=touching."""
    a = [_ev("HLT-1", "2026-01-01T10:00:00", "2026-01-01T11:00:00")]
    b = [_ev("MNM-1", "2026-01-01T11:00:00", "2026-01-01T12:00:00")]
    r = fe.invoke({"entries": a, "overlap_entries": b})
    assert r["ok"] is True
    # Inclusive: e_start <= o_end AND o_start <= e_end → 11:00<=12:00 and 11:00<=11:00 → True
    assert len(r["entries"]) == 1


def test_overlap_instantaneous_entry():
    """Foto (solo taken_at_iso, no end) overlap con event range."""
    a = [{"name": "foto.jpg", "taken_at_iso": "2026-01-01T10:30:00"}]
    b = [_ev("evento", "2026-01-01T10:00:00", "2026-01-01T11:00:00")]
    r = fe.invoke({"entries": a, "overlap_entries": b})
    assert r["ok"] is True
    assert len(r["entries"]) == 1


def test_overlap_epoch_input():
    """Accept epoch float."""
    a = [{"start": 1735731600.0, "end": 1735735200.0, "summary": "A"}]
    b = [{"start": 1735733400.0, "end": 1735737000.0, "summary": "B"}]
    r = fe.invoke({"entries": a, "overlap_entries": b})
    assert r["ok"] is True
    assert len(r["entries"]) == 1


def test_overlap_no_time_info_excluded():
    """Entry senza start/end → esclusa dal filter overlap (deterministico)."""
    a = [
        _ev("HLT-1", "2026-01-01T10:00:00", "2026-01-01T11:00:00"),
        {"summary": "no_time"},
    ]
    b = [_ev("MNM-1", "2026-01-01T10:30:00", "2026-01-01T11:30:00")]
    r = fe.invoke({"entries": a, "overlap_entries": b})
    assert r["ok"] is True
    assert len(r["entries"]) == 1
    assert r["entries"][0]["summary"] == "HLT-1"


def test_overlap_multiple_hits_in_other():
    """Entry A overlap con 2 entries B → _overlap_with lista con 2 label."""
    a = [_ev("HLT-1", "2026-01-01T10:00:00", "2026-01-01T14:00:00")]
    b = [
        _ev("MNM-1", "2026-01-01T10:30:00", "2026-01-01T11:30:00"),
        _ev("MNM-2", "2026-01-01T12:30:00", "2026-01-01T13:30:00"),
    ]
    r = fe.invoke({"entries": a, "overlap_entries": b})
    assert r["ok"] is True
    assert len(r["entries"]) == 1
    matched = r["entries"][0]["_overlap_with"]
    assert len(matched) == 2
    assert "MNM-1" in matched[0] or "MNM-2" in matched[0]


def test_overlap_field_override():
    """overlap_field_start/end permette di puntare a field custom."""
    a = [{"name": "x", "fired_at_iso": "2026-01-01T10:00:00"}]
    b = [{"name": "y", "fired_at_iso": "2026-01-01T10:00:00"}]  # stesso istante
    r = fe.invoke({
        "entries": a, "overlap_entries": b,
        "overlap_field_start": "fired_at_iso",
        "overlap_field_end": "fired_at_iso",
    })
    assert r["ok"] is True
    assert len(r["entries"]) == 1


def test_overlap_combined_with_where_filter():
    """Combina where_starts_with + overlap_entries: filtra prima per
    prefisso, poi per overlap temporale. AND."""
    a = [
        _ev("HLT-Dentista", "2026-01-01T10:00:00", "2026-01-01T11:00:00"),
        _ev("MNM-X", "2026-01-01T10:30:00", "2026-01-01T11:30:00"),
        _ev("HLT-Visita", "2026-01-01T14:00:00", "2026-01-01T15:00:00"),
    ]
    b_other = [_ev("MNM-Y", "2026-01-01T10:00:00", "2026-01-01T11:00:00")]
    r = fe.invoke({
        "entries": a,
        "where_field": "summary",
        "where_starts_with": "HLT",
        "overlap_entries": b_other,
    })
    assert r["ok"] is True
    # Solo HLT-Dentista (10-11) overlap MNM-Y (10-11). HLT-Visita NO.
    assert len(r["entries"]) == 1
    assert r["entries"][0]["summary"] == "HLT-Dentista"
