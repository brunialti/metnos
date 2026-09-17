"""Timing reports must distinguish aggregate work from elapsed time."""
import json
import sqlite3

import pytest

from internal.tools.lre_performance import read_report, summarize


def row(start, end, *, state="succeeded", latency=8000):
    return {"model_mode": "llm", "state": state, "started_at": f"2026-09-17T12:00:{start:02d}Z",
            "ended_at": f"2026-09-17T12:00:{end:02d}Z" if end is not None else None,
            "metrics_json": json.dumps({"llm_usage": {
                "schema_version": "metnos.durable-model-usage/2",
                "usage_missing": False, "cost_unknown": False, "dropped": 0,
                "records": [{"kind": "vision", "latency_ms": latency, "out_tokens": 20}]}})}


def test_parallel_work_is_not_elapsed_time_and_active_attempts_are_not_successes():
    report = summarize([row(0, 10), row(0, 10), row(10, 20),
                        row(20, None, state="running"), row(1, 4, state="failed")])
    assert report["successful_attempts"] == 3
    assert report["sum_attempt_seconds"] == 30
    assert report["completed_attempt_span_seconds"] == 20
    assert report["completed_attempt_occupied_seconds"] == 20
    assert report["peak_completed_attempt_overlap"] == 2
    assert report["model_calls"]["vision"]["latency_ms"] == 24000
    assert report["successful_attempts_per_minute"] == 9


def test_overlapping_calls_are_not_clamped_to_attempt_wall_time():
    report = summarize([row(0, 10, latency=20000)])
    assert report["model_calls"]["vision"]["latency_ms"] == 20000
    assert report["sum_attempt_seconds"] == 10
    assert "remaining_seconds" not in report


def test_empty_and_missing_accounting_are_explicit():
    assert summarize([])["mean_attempt_seconds"] is None
    item = row(0, 10)
    item["metrics_json"] = "{}"
    assert summarize([item])["attempts_with_incomplete_model_usage"] == 1
    item["model_mode"] = "none"
    assert summarize([item])["attempts_with_incomplete_model_usage"] == 0


def test_read_report_is_scoped_bounded_and_does_not_modify_database(tmp_path):
    path = tmp_path / "state.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE workloads (id, owner_user_id, active_revision_id);
            CREATE TABLE units (id, owner_user_id, revision_id, stage_id);
            CREATE TABLE stages (id, owner_user_id, stage_key);
            CREATE TABLE attempts (unit_id, owner_user_id, model_snapshot_json, state, started_at, ended_at, metrics_json);
            INSERT INTO workloads VALUES ('job','owner','rev');
            INSERT INTO stages VALUES ('stage','owner','analyze');
            INSERT INTO units VALUES ('unit','owner','rev','stage');
        """)
        for start in (0, 10, 20):
            item = row(start, start + 5)
            db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?)",
                       ("unit", "owner", json.dumps({"mode": item.pop("model_mode")}), *item.values()))
        item = row(0, 5)
        db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?)",
                   ("unit", "other-owner", json.dumps({"mode": item.pop("model_mode")}), *item.values()))
    before = path.read_bytes()
    args = dict(database=path, workload="job", stage="analyze",
                since="2026-09-17T14:00:00+02:00", until="2026-09-17T14:00:20+02:00")
    assert read_report(**args)["successful_attempts"] == 2
    with pytest.raises(ValueError, match="limit"):
        read_report(**args, max_attempts=1)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        db.execute("UPDATE attempts SET started_at='2026-09-17T12:00:19.999999Z', ended_at='2026-09-17T12:00:25Z' WHERE started_at LIKE '%:10Z'")
    assert read_report(**args)["successful_attempts"] == 2


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        read_report(path, workload="job", stage="analyze",
                    since="2026-09-17T00:00:00Z", until="2026-09-18T00:00:00Z")
    assert not path.exists()
