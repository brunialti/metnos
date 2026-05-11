"""scheduler_v2.client — stateless wrapper around SchedulerStorage."""
from __future__ import annotations

import time

import pytest

from scheduler_v2 import client as sched_client
from scheduler_v2 import daemon_handle


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, db_path):
    """Point the client at a per-test sqlite + clear daemon_handle."""
    monkeypatch.setenv("METNOS_SCHEDULER_V2_DB", str(db_path))
    daemon_handle.clear()
    yield
    daemon_handle.clear()


# --- get_storage ----------------------------------------------------------


def test_get_storage_uses_env_override(db_path):
    s = sched_client.get_storage()
    try:
        assert s.db_path == db_path
    finally:
        s.close()


def test_get_storage_returns_fresh_instance():
    a = sched_client.get_storage()
    b = sched_client.get_storage()
    try:
        assert a is not b
    finally:
        a.close()
        b.close()


# --- add_job --------------------------------------------------------------


def test_add_job_persists_with_next_fire_at():
    out = sched_client.add_job(
        name="job_a",
        trigger="every_60s",
        callback_key="noop",
        payload={"k": "v"},
        origin="user",
        label="alpha",
    )
    assert out["name"] == "job_a"
    assert out["recurring"] is True
    assert out["enabled"] is True
    assert out["next_fire_at"] > time.time()
    assert out["payload"] == {"k": "v"}
    assert out["origin"] == "user"
    assert out["label"] == "alpha"
    assert out["id"] is not None


def test_add_job_upsert_on_same_name():
    a = sched_client.add_job(
        name="job_b", trigger="every_60s", callback_key="cb1",
    )
    b = sched_client.add_job(
        name="job_b", trigger="every_120s", callback_key="cb2",
        label="updated",
    )
    assert a["id"] == b["id"]  # same row
    assert b["trigger"] == "every_120s"
    assert b["callback_key"] == "cb2"
    assert b["label"] == "updated"

    listed = sched_client.list_jobs()
    assert len(listed) == 1


def test_add_job_grace_window_seconds_passes_through():
    out = sched_client.add_job(
        name="grace", trigger="daily@03:00", callback_key="noop",
        grace_window_s=14400,
    )
    assert out["grace_window_s"] == 14400


# --- add_timer ------------------------------------------------------------


def test_add_timer_with_delay():
    out = sched_client.add_timer(
        callback_key="ping", delay_s=30, label="t30",
    )
    assert out["recurring"] is False
    assert out["callback_key"] == "ping"
    assert out["next_fire_at"] >= time.time() + 29
    assert out["next_fire_at"] <= time.time() + 31
    assert out["label"] == "t30"
    assert out["trigger"].startswith("at:")


def test_add_timer_with_fire_at_iso():
    target = "2099-01-01T00:00:00+00:00"
    out = sched_client.add_timer(
        callback_key="ping", fire_at_iso=target, label="far_future",
    )
    assert out["recurring"] is False
    assert out["trigger"] == f"at:{target}"


def test_add_timer_rejects_both_args():
    with pytest.raises(ValueError):
        sched_client.add_timer(
            callback_key="ping", delay_s=10, fire_at_iso="2099-01-01T00:00:00+00:00",
        )


def test_add_timer_rejects_neither_arg():
    with pytest.raises(ValueError):
        sched_client.add_timer(callback_key="ping")


def test_add_timer_rejects_negative_delay():
    with pytest.raises(ValueError):
        sched_client.add_timer(callback_key="ping", delay_s=-1)


# --- cancel ---------------------------------------------------------------


def test_cancel_job_returns_true_on_existing():
    sched_client.add_job(name="del_me", trigger="every_60s", callback_key="noop")
    assert sched_client.cancel_job("del_me") is True
    assert sched_client.cancel_job("del_me") is False  # already gone


def test_cancel_timer_alias():
    out = sched_client.add_timer(callback_key="ping", delay_s=10)
    assert sched_client.cancel_timer(out["name"]) is True


# --- toggle ---------------------------------------------------------------


def test_toggle_job_disable_then_enable():
    sched_client.add_job(name="t", trigger="every_60s", callback_key="noop")
    assert sched_client.toggle_job("t", False) is True
    [job] = [j for j in sched_client.list_jobs() if j["name"] == "t"]
    assert job["enabled"] is False
    assert sched_client.toggle_job("t", True) is True
    [job2] = [j for j in sched_client.list_jobs() if j["name"] == "t"]
    assert job2["enabled"] is True


def test_toggle_job_unknown_returns_false():
    assert sched_client.toggle_job("ghost", True) is False


# --- list -----------------------------------------------------------------


def test_list_jobs_filters_recurring_only():
    sched_client.add_job(name="r1", trigger="every_60s", callback_key="noop")
    sched_client.add_timer(callback_key="ping", delay_s=10)
    listed = sched_client.list_jobs()
    assert all(j["recurring"] is True for j in listed)
    assert any(j["name"] == "r1" for j in listed)


def test_list_jobs_filter_by_origin():
    sched_client.add_job(name="u1", trigger="every_60s", callback_key="noop", origin="user")
    sched_client.add_job(name="s1", trigger="every_60s", callback_key="noop", origin="system")
    user_only = sched_client.list_jobs(origin="user")
    assert {j["name"] for j in user_only} == {"u1"}


def test_list_jobs_filter_by_enabled():
    sched_client.add_job(name="on", trigger="every_60s", callback_key="noop")
    sched_client.add_job(name="off", trigger="every_60s", callback_key="noop")
    sched_client.toggle_job("off", False)
    enabled = sched_client.list_jobs(enabled=True)
    assert {j["name"] for j in enabled} == {"on"}


def test_list_timers_only_one_shots():
    sched_client.add_job(name="r", trigger="every_60s", callback_key="noop")
    sched_client.add_timer(callback_key="ping", delay_s=10, label="t1")
    timers = sched_client.list_timers()
    assert all(t["recurring"] is False for t in timers)
    assert all(t["enabled"] is True for t in timers)
    assert len(timers) == 1


# --- history --------------------------------------------------------------


def test_history_empty():
    assert sched_client.history() == []


def test_history_filters_by_name():
    storage = sched_client.get_storage()
    try:
        rid_a = storage.begin_run(None, "job_a")
        storage.end_run(rid_a, status="success", duration_ms=10)
        rid_b = storage.begin_run(None, "job_b")
        storage.end_run(rid_b, status="error", duration_ms=20)
    finally:
        storage.close()
    all_runs = sched_client.history(limit=10)
    assert len(all_runs) == 2
    only_a = sched_client.history(name="job_a", limit=10)
    assert len(only_a) == 1
    assert only_a[0]["entry_name"] == "job_a"


# --- run_now --------------------------------------------------------------


def test_run_now_advances_next_fire_at_to_now():
    sched_client.add_job(name="rn", trigger="daily@23:59", callback_key="noop")
    before = sched_client.list_jobs()[0]
    assert before["next_fire_at"] > time.time() + 1000  # far in the future

    out = sched_client.run_now("rn")
    assert out["ok"] is True
    assert out["next_fire_at"] <= time.time() + 1


def test_run_now_unknown_name():
    out = sched_client.run_now("ghost")
    assert out["ok"] is False
    assert "ghost" in out["error"]


# --- kick best-effort -----------------------------------------------------


def test_kick_no_daemon_no_error():
    """Without a registered daemon handle, mutations must not raise."""
    daemon_handle.clear()
    sched_client.add_job(name="x", trigger="every_60s", callback_key="noop")
    sched_client.toggle_job("x", False)
    sched_client.cancel_job("x")
    # If we got here, kick was a clean no-op.


def test_kick_invokes_daemon_kick():
    """When a fake daemon is registered, mutations call kick()."""
    calls = {"n": 0}

    class FakeDaemon:
        def kick(self):
            calls["n"] += 1

    fake = FakeDaemon()
    daemon_handle.set_active(fake)  # type: ignore[arg-type]
    try:
        sched_client.add_job(name="kk", trigger="every_60s", callback_key="noop")
        sched_client.toggle_job("kk", False)
        sched_client.cancel_job("kk")
    finally:
        daemon_handle.clear()
    assert calls["n"] >= 3


def test_kick_failure_swallowed():
    """A kick that raises must not propagate."""
    class BoomDaemon:
        def kick(self):
            raise RuntimeError("boom")

    daemon_handle.set_active(BoomDaemon())  # type: ignore[arg-type]
    try:
        sched_client.add_job(name="z", trigger="every_60s", callback_key="noop")
    finally:
        daemon_handle.clear()
