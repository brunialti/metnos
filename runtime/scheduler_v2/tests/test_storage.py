"""SchedulerStorage CRUD + WAL + crash recovery."""
from __future__ import annotations

import time

from scheduler_v2.models import ScheduleEntry
from scheduler_v2.storage import SchedulerStorage


def _entry(name="t", trigger="every_60s", next_fire=None, **kw):
    return ScheduleEntry(
        name=name,
        trigger=trigger,
        next_fire_at=next_fire if next_fire is not None else time.time() + 60,
        recurring=True,
        callback_key="noop",
        **kw,
    )


def test_schema_init_idempotent(db_path):
    s1 = SchedulerStorage(db_path)
    s1.close()
    s2 = SchedulerStorage(db_path)  # re-init must not raise
    s2.close()


def test_wal_mode_active(db_path):
    s = SchedulerStorage(db_path)
    try:
        assert s.is_wal_mode()
    finally:
        s.close()


def test_upsert_insert_then_update(db_path):
    s = SchedulerStorage(db_path)
    try:
        e = _entry(name="alpha")
        eid1 = s.upsert(e)
        e2 = _entry(name="alpha", trigger="daily@08:00", next_fire=time.time() + 3600)
        eid2 = s.upsert(e2)
        assert eid1 == eid2
        got = s.get_by_name("alpha")
        assert got is not None
        assert got.trigger == "daily@08:00"
    finally:
        s.close()


def test_get_and_list(db_path):
    s = SchedulerStorage(db_path)
    try:
        s.upsert(_entry(name="a", next_fire=10.0))
        s.upsert(_entry(name="b", next_fire=5.0))
        s.upsert(_entry(name="c", next_fire=20.0))
        all_ = s.list_all()
        assert [e.name for e in all_] == ["b", "a", "c"]
    finally:
        s.close()


def test_fetch_due_filters_enabled_and_time(db_path):
    s = SchedulerStorage(db_path)
    try:
        now = 1000.0
        s.upsert(_entry(name="past", next_fire=now - 10))
        s.upsert(_entry(name="future", next_fire=now + 10))
        e_dis = _entry(name="disabled_past", next_fire=now - 10, enabled=False)
        s.upsert(e_dis)
        due = s.fetch_due(now)
        assert {e.name for e in due} == {"past"}
    finally:
        s.close()


def test_delete(db_path):
    s = SchedulerStorage(db_path)
    try:
        s.upsert(_entry(name="x"))
        assert s.delete("x") is True
        assert s.get_by_name("x") is None
        assert s.delete("x") is False  # second delete is a no-op
    finally:
        s.close()


def test_record_outcome_and_runs(db_path):
    s = SchedulerStorage(db_path)
    try:
        eid = s.upsert(_entry(name="job"))
        run_id = s.begin_run(eid, "job")
        s.end_run(run_id, status="success", duration_ms=42, output="ok")
        s.record_outcome(
            eid,
            status="success",
            duration_ms=42,
            error=None,
            next_fire_at=time.time() + 60,
            decrement_remaining=False,
            disable=False,
        )
        e = s.get_by_id(eid)
        assert e is not None
        assert e.total_runs == 1 and e.last_status == "success"
        runs = s.list_runs()
        assert runs and runs[0].status == "success" and runs[0].entry_name == "job"
    finally:
        s.close()


def test_mark_crashed_runs(db_path):
    s = SchedulerStorage(db_path)
    try:
        eid = s.upsert(_entry(name="job"))
        # Simulate process crash mid-fire: a 'running' row never finalized.
        s.begin_run(eid, "job")
        marked = s.mark_crashed_runs()
        assert marked == 1
        runs = s.list_runs()
        assert runs[0].status == "crashed"
        # Second pass is idempotent (no more 'running' rows).
        assert s.mark_crashed_runs() == 0
    finally:
        s.close()


def test_next_fire_at_min_skips_disabled(db_path):
    s = SchedulerStorage(db_path)
    try:
        s.upsert(_entry(name="enabled", next_fire=100.0))
        s.upsert(_entry(name="disabled", next_fire=50.0, enabled=False))
        assert s.next_fire_at_min() == 100.0
    finally:
        s.close()


def test_next_fire_at_min_empty(db_path):
    s = SchedulerStorage(db_path)
    try:
        assert s.next_fire_at_min() is None
    finally:
        s.close()


def test_payload_roundtrip(db_path):
    s = SchedulerStorage(db_path)
    try:
        e = _entry(name="p", payload={"key": [1, 2], "nested": {"x": True}})
        s.upsert(e)
        got = s.get_by_name("p")
        assert got is not None
        assert got.payload == {"key": [1, 2], "nested": {"x": True}}
    finally:
        s.close()


def test_record_outcome_decrements_remaining(db_path):
    s = SchedulerStorage(db_path)
    try:
        eid = s.upsert(_entry(name="r", remaining_runs=3))
        s.record_outcome(
            eid,
            status="success",
            duration_ms=1,
            error=None,
            next_fire_at=time.time() + 60,
            decrement_remaining=True,
            disable=False,
        )
        e = s.get_by_id(eid)
        assert e.remaining_runs == 2
    finally:
        s.close()
