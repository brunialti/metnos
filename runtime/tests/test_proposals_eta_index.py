"""Test proposals_eta_index — store sqlite di latenze per shape (ADR 0122)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

import proposals_eta_index as pei


def test_upsert_and_lookup_roundtrip(tmp_path):
    db = tmp_path / "eta.sqlite"
    pei.upsert_aggregate(
        "abc123",
        [1000, 2000, 3000, 4000, 5000],
        sample_steps=["find_urls", "read_urls_html"],
        db_path=db,
        last_seen=1234567.0,
    )
    rec = pei.lookup("abc123", db_path=db)
    assert rec is not None
    assert rec["sample_count"] == 5
    assert rec["p50_ms"] == 3000
    assert rec["p95_ms"] == 5000
    assert rec["sample_steps"] == ["find_urls", "read_urls_html"]
    assert abs(rec["last_seen"] - 1234567.0) < 0.001


def test_lookup_missing_returns_none(tmp_path):
    db = tmp_path / "eta.sqlite"
    # init the db
    pei.upsert_aggregate("known", [100], db_path=db)
    assert pei.lookup("nonexistent", db_path=db) is None


def test_lookup_no_db_returns_none(tmp_path):
    # File doesn't exist: should not raise.
    db = tmp_path / "absent.sqlite"
    assert pei.lookup("any", db_path=db) is None


def test_upsert_idempotent_overwrites_aggregate(tmp_path):
    db = tmp_path / "eta.sqlite"
    pei.upsert_aggregate("h1", [100, 200], db_path=db)
    pei.upsert_aggregate("h1", [500, 600, 700], db_path=db)
    rec = pei.lookup("h1", db_path=db)
    assert rec["sample_count"] == 3
    assert rec["p50_ms"] == 600


def test_upsert_empty_samples_returns_zero_count(tmp_path):
    db = tmp_path / "eta.sqlite"
    rep = pei.upsert_aggregate("empty", [], db_path=db)
    assert rep["sample_count"] == 0


def test_aggregate_from_jsonls_walks_turn_logs(tmp_path):
    turns_dir = tmp_path / "turns"
    turns_dir.mkdir()
    db = tmp_path / "eta.sqlite"
    now = time.time()
    # Two turns with same shape, different timing
    rec1 = {
        "ts_start": now - 100,
        "ts_end": now - 98,  # 2000ms
        "steps": [
            {"chosen_tool": "find_files"},
            {"chosen_tool": "filter_entries"},
        ],
    }
    rec2 = {
        "ts_start": now - 50,
        "ts_end": now - 47,  # 3000ms
        "steps": [
            {"chosen_tool": "find_files"},
            {"chosen_tool": "filter_entries"},
        ],
    }
    # Different shape
    rec3 = {
        "ts_start": now - 30,
        "ts_end": now - 29.5,  # 500ms
        "steps": [{"chosen_tool": "get_now"}],
    }
    (turns_dir / "today.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [rec1, rec2, rec3]) + "\n",
        encoding="utf-8",
    )
    rep = pei.aggregate_from_jsonls(
        since_ts=now - 200, turns_dir=turns_dir, db_path=db,
    )
    assert rep["shapes"] == 2
    assert rep["samples"] == 3
    # Verify the merged shape has both samples
    from path_shape import path_shape_hash
    h_files = path_shape_hash(rec1["steps"])
    h_now = path_shape_hash(rec3["steps"])
    rec_files = pei.lookup(h_files, db_path=db)
    assert rec_files["sample_count"] == 2
    rec_now = pei.lookup(h_now, db_path=db)
    assert rec_now["sample_count"] == 1


def test_aggregate_skips_old_turns(tmp_path):
    turns_dir = tmp_path / "turns"
    turns_dir.mkdir()
    db = tmp_path / "eta.sqlite"
    now = time.time()
    old = {
        "ts_start": now - 1000000,  # very old
        "ts_end": now - 999999,
        "steps": [{"chosen_tool": "find_urls"}],
    }
    fresh = {
        "ts_start": now - 100,
        "ts_end": now - 99,
        "steps": [{"chosen_tool": "find_urls"}],
    }
    (turns_dir / "logs.jsonl").write_text(
        json.dumps(old) + "\n" + json.dumps(fresh) + "\n", encoding="utf-8",
    )
    rep = pei.aggregate_from_jsonls(
        since_ts=now - 1000, turns_dir=turns_dir, db_path=db,
    )
    assert rep["samples"] == 1


def test_count_shape_calls(tmp_path):
    turns_dir = tmp_path / "turns"
    turns_dir.mkdir()
    now = time.time()
    rec = {
        "ts_start": now - 50,
        "ts_end": now - 49,
        "steps": [{"chosen_tool": "find_urls"}, {"chosen_tool": "read_urls_html"}],
    }
    other = {
        "ts_start": now - 30,
        "ts_end": now - 29,
        "steps": [{"chosen_tool": "get_now"}],
    }
    (turns_dir / "x.jsonl").write_text(
        json.dumps(rec) + "\n" + json.dumps(other) + "\n", encoding="utf-8",
    )
    from path_shape import path_shape_hash
    target = path_shape_hash(rec["steps"])
    n = pei.count_shape_calls(target, since_ts=now - 100, turns_dir=turns_dir)
    assert n == 1
    n_unknown = pei.count_shape_calls("0123456789abcdef", since_ts=now - 100, turns_dir=turns_dir)
    assert n_unknown == 0


def test_aggregate_handles_missing_dir(tmp_path):
    # Turns dir doesn't exist: should not crash.
    rep = pei.aggregate_from_jsonls(
        since_ts=0.0, turns_dir=tmp_path / "absent", db_path=tmp_path / "eta.sqlite",
    )
    assert rep == {"shapes": 0, "samples": 0, "files_read": 0}
