"""recurring_tasks.handle_create_tasks (ex handle_schedule_recurring) writes both DBs in PR5.

Validates that registering a user task lands BOTH in `recurring_tasks.db`
(user-facing source of truth) AND in scheduler v2 `schedule_entries`
(scheduler runtime).
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

from scheduler_v2 import client as sched_client
from scheduler_v2 import daemon_handle


@pytest.fixture
def isolated_dbs(tmp_path, monkeypatch):
    """Point both DBs at tmp paths and reload recurring_tasks fresh."""
    user_db = tmp_path / "recurring_tasks.db"
    v2_db = tmp_path / "scheduler_v2.sqlite"
    monkeypatch.setenv("METNOS_SCHEDULER_V2_DB", str(v2_db))
    daemon_handle.clear()

    # recurring_tasks resolves DB_PATH at import time; reload after monkeypatch.
    if "recurring_tasks" in sys.modules:
        del sys.modules["recurring_tasks"]
    runtime_dir = Path(__file__).resolve().parents[2]
    if str(runtime_dir) not in sys.path:
        sys.path.insert(0, str(runtime_dir))
    rt = importlib.import_module("recurring_tasks")
    monkeypatch.setattr(rt, "DB_PATH", user_db)

    yield rt, user_db, v2_db
    daemon_handle.clear()


def test_handle_create_tasks_writes_both_dbs(isolated_dbs):
    rt, user_db, v2_db = isolated_dbs

    out = rt.handle_create_tasks(  # ex handle_schedule_recurring
        {
            "label": "check posta mattutina",
            "when": "daily@08:00",
            "query": "leggi le mail di oggi importanti",
            "grace_window_minutes": 240,
        },
        actor="host",
        channel="telegram",
        chat_id="123",
    )
    assert out["ok"] is True
    user_name = out["task"]["name"]

    # 1) user DB row present
    conn = sqlite3.connect(str(user_db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM recurring_tasks WHERE name = ?", (user_name,)
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["query"] == "leggi le mail di oggi importanti"
    assert rows[0]["actor"] == "host"
    assert rows[0]["chat_id"] == "123"
    assert rows[0]["grace_window_minutes"] == 240

    # 2) v2 schedule_entries row present, prefixed with "user_"
    jobs = sched_client.list_jobs()
    matching = [j for j in jobs if j["name"] == f"user_{user_name}"]
    assert len(matching) == 1
    j = matching[0]
    assert j["trigger"] == "daily@08:00"
    assert j["callback_key"] == "run_user_query"
    assert j["origin"] == "user"
    # grace_window_s = 240 min * 60s
    assert j["grace_window_s"] == 240 * 60
    # payload carries enough context for the callback to dispatch the turn
    p = j["payload"]
    assert p["actor"] == "host"
    assert p["channel"] == "telegram"
    assert p["chat_id"] == "123"
    assert p["query"] == "leggi le mail di oggi importanti"
    assert p["name"] == user_name


def test_delete_tasks_removes_v2_entry(isolated_dbs):
    rt, _user_db, _v2_db = isolated_dbs
    out = rt.handle_create_tasks(
        {"label": "x", "when": "every_30m", "query": "ping"},
        actor="host", channel="telegram", chat_id="9",
    )
    user_name = out["task"]["name"]
    full_name = f"user_{user_name}"
    assert any(j["name"] == full_name for j in sched_client.list_jobs())

    res = rt.handle_delete_tasks({"name": user_name}, actor="host")
    assert res["ok"] is True
    assert not any(j["name"] == full_name for j in sched_client.list_jobs())
