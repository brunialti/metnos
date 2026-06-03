"""Migrate v1 recurring_tasks.db + state.sqlite into v2 scheduler_v2.sqlite."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scheduler_v2.builtin_callbacks import _BUILTIN_JOBS
from scheduler_v2.migrate_v1 import migrate
from scheduler_v2.storage import SchedulerStorage


# Builtin di fixture derivati dalla FONTE DI VERITA' (§7.3 universale): la
# migrate v1→v2 riconosce SOLO i nomi presenti nel set corrente
# (`_BUILTIN_NAME_TO_KEY`). Usare nomi correnti evita che il test resti stale
# quando i builtin vengono consolidati (ADR 0167: apply_ager+apply_executor_ager
# +synt_suggest → nightly_aging, introvertiva_apply rimosso). Il PRIMO porta
# storia di run (last_status="ok") per verificare che venga preservata.
_FIXTURE_BUILTINS = [j["name"] for j in _BUILTIN_JOBS[:7]]
_HISTORY_BUILTIN = _FIXTURE_BUILTINS[0]


# --- v1 schema fixtures -----------------------------------------------------

_V1_RECURRING_SCHEMA = """
CREATE TABLE recurring_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT UNIQUE NOT NULL,
    schedule      TEXT NOT NULL,
    query         TEXT NOT NULL,
    actor         TEXT NOT NULL,
    channel       TEXT NOT NULL,
    chat_id       TEXT,
    label         TEXT,
    callback_key  TEXT NOT NULL DEFAULT 'run_user_query',
    times                INTEGER,
    fired_count          INTEGER NOT NULL DEFAULT 0,
    grace_window_minutes INTEGER,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    enabled       INTEGER NOT NULL DEFAULT 1
);
"""

_V1_STATE_SCHEMA = """
CREATE TABLE tasks (
    name        TEXT PRIMARY KEY,
    schedule    TEXT NOT NULL,
    last_run_at TEXT,
    last_status TEXT,
    last_output TEXT,
    created_at  TEXT,
    grace_window_minutes INTEGER,
    enabled     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT,
    output      TEXT,
    duration_ms INTEGER
);
"""


def _make_v1_recurring(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(_V1_RECURRING_SCHEMA)
    conn.executemany(
        "INSERT INTO recurring_tasks (name, schedule, query, actor, channel, "
        "chat_id, label, callback_key, times, fired_count, "
        "grace_window_minutes, enabled) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("morning_mail", "daily@08:00", "leggi mail importanti",
             "host", "telegram", "12345", "check posta mattutina",
             "run_user_query", None, 0, 240, 1),
            ("ten_pings", "every_30m", "ping",
             "guest_abc123", "telegram", "67890", "ping ogni 30m",
             "run_user_query", 10, 3, None, 1),
            ("disabled_task", "daily@22:00", "scrivi log",
             "host", "telegram", "12345", "log serale",
             "run_user_query", None, 0, None, 0),
        ],
    )
    conn.commit()
    conn.close()


def _make_v1_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_V1_STATE_SCHEMA)
    rows = []
    for i, name in enumerate(_FIXTURE_BUILTINS):
        # Il primo builtin porta storia di run (last_run_at/last_status="ok")
        # per verificare che la migrate la preservi; gli altri "vergini".
        last_run = "2026-05-07T04:00:00+00:00" if i == 0 else None
        last_status = "ok" if i == 0 else None
        rows.append((name, f"daily@0{(i % 6) + 1}:00", last_run, last_status,
                     "2026-04-01T00:00:00+00:00", None, 1))
    # User mirror in state.sqlite — must be skipped (source of truth is
    # recurring_tasks.db).
    rows.append(("user_morning_mail", "daily@08:00",
                 "2026-05-07T08:00:00+00:00", "ok",
                 "2026-04-15T00:00:00+00:00", 240, 1))
    # Unknown builtin: must be skipped (no invent).
    rows.append(("legacy_dead_task", "daily@02:00", None, None,
                 "2026-04-01T00:00:00+00:00", None, 1))
    conn.executemany(
        "INSERT INTO tasks (name, schedule, last_run_at, last_status, "
        "created_at, grace_window_minutes, enabled) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


@pytest.fixture
def v1_dbs(tmp_path):
    rec = tmp_path / "recurring_tasks.db"
    state = tmp_path / "state.sqlite"
    _make_v1_recurring(rec)
    _make_v1_state(state)
    return rec, state


def test_migrate_inserts_user_and_builtin(tmp_path, v1_dbs):
    rec, state = v1_dbs
    target = tmp_path / "v2.sqlite"
    summary = migrate(
        recurring_db=rec, state_db=state, target_db=target, dry_run=False
    )
    # 3 user tasks (incl. disabled), len(_FIXTURE_BUILTINS) builtin
    # (user_ mirror + legacy skipped).
    assert summary["migrated_user"] == 3
    assert summary["migrated_builtin"] == len(_FIXTURE_BUILTINS)
    assert summary["errors"] == 0
    # legacy_dead_task + user_morning_mail in state.sqlite both skipped.
    assert summary["skipped"] >= 2

    # Verify rows.
    s = SchedulerStorage(target)
    try:
        names = {e.name for e in s.list_all()}
        assert "user_morning_mail" in names
        assert "user_ten_pings" in names
        assert "user_disabled_task" in names
        for _bn in _FIXTURE_BUILTINS:
            assert _bn in names, f"builtin {_bn} non migrato"
        assert "legacy_dead_task" not in names

        morning = s.get_by_name("user_morning_mail")
        assert morning is not None
        assert morning.callback_key == "run_user_query"
        assert morning.origin == "user"
        assert morning.payload["query"] == "leggi mail importanti"
        assert morning.payload["channel"] == "telegram"
        assert morning.payload["actor"] == "host"
        assert morning.payload["chat_id"] == "12345"
        assert morning.grace_window_s == 240 * 60
        assert morning.enabled is True

        disabled = s.get_by_name("user_disabled_task")
        assert disabled is not None
        assert disabled.enabled is False

        ten = s.get_by_name("user_ten_pings")
        assert ten is not None
        assert ten.remaining_runs == 7  # 10 times - 3 fired_count

        ager = s.get_by_name(_HISTORY_BUILTIN)
        assert ager is not None
        assert ager.callback_key == _BUILTIN_JOBS[0]["callback_key"]
        assert ager.origin == "system"
        assert ager.payload == {}
        assert ager.last_status == "ok"        # storia di run preservata
        assert ager.last_run_at is not None
    finally:
        s.close()


def test_migrate_idempotent(tmp_path, v1_dbs):
    rec, state = v1_dbs
    target = tmp_path / "v2.sqlite"
    first = migrate(recurring_db=rec, state_db=state, target_db=target)
    second = migrate(recurring_db=rec, state_db=state, target_db=target)
    assert first["migrated_user"] == 3
    assert first["migrated_builtin"] == len(_FIXTURE_BUILTINS)
    assert second["migrated_user"] == 0
    assert second["migrated_builtin"] == 0
    # The skipped count on the second pass includes everything we previously
    # migrated, plus the always-skipped legacy_dead_task and user_morning_mail
    # state mirror.
    assert second["skipped"] >= first["migrated_user"] + first["migrated_builtin"]


def test_dry_run_does_not_write(tmp_path, v1_dbs):
    rec, state = v1_dbs
    target = tmp_path / "v2.sqlite"
    summary = migrate(
        recurring_db=rec, state_db=state, target_db=target, dry_run=True
    )
    # Counts still reported, but the v2 DB must be empty.
    assert summary["migrated_user"] == 3
    assert summary["migrated_builtin"] == len(_FIXTURE_BUILTINS)
    s = SchedulerStorage(target)
    try:
        assert s.list_all() == []
    finally:
        s.close()


def test_missing_v1_dbs_no_crash(tmp_path):
    target = tmp_path / "v2.sqlite"
    summary = migrate(
        recurring_db=tmp_path / "nope1.db",
        state_db=tmp_path / "nope2.db",
        target_db=target,
    )
    assert summary == {
        "migrated_user": 0,
        "migrated_builtin": 0,
        "skipped": 0,
        "errors": 0,
    }


def test_only_state_db_present(tmp_path):
    state = tmp_path / "state.sqlite"
    _make_v1_state(state)
    target = tmp_path / "v2.sqlite"
    summary = migrate(
        recurring_db=tmp_path / "no_recurring.db",
        state_db=state,
        target_db=target,
    )
    assert summary["migrated_user"] == 0
    assert summary["migrated_builtin"] == len(_FIXTURE_BUILTINS)
