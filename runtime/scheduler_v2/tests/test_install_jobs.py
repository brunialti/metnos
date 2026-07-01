"""install_default_jobs: idempotent INSERT-OR-IGNORE for the 7 builtins."""
from __future__ import annotations

import time

from scheduler_v2.builtin_callbacks import _BUILTIN_JOBS, install_default_jobs
from scheduler_v2.daemon import SchedulerDaemon


def test_install_on_empty_db_inserts_all_builtins(db_path):
    d = SchedulerDaemon(db_path)
    n = install_default_jobs(d)
    assert n == len(_BUILTIN_JOBS)
    rows = d.storage.list_all()
    names = {r.name for r in rows}
    expected = {j["name"] for j in _BUILTIN_JOBS}
    assert names == expected


def test_install_twice_zero_new(db_path):
    d = SchedulerDaemon(db_path)
    first = install_default_jobs(d)
    second = install_default_jobs(d)
    assert first == len(_BUILTIN_JOBS)
    assert second == 0
    # Same row count after second call.
    assert len(d.storage.list_all()) == len(_BUILTIN_JOBS)


def test_next_fire_at_in_future(db_path):
    d = SchedulerDaemon(db_path)
    install_default_jobs(d)
    now = time.time()
    for r in d.storage.list_all():
        assert r.next_fire_at > now, (
            f"{r.name}: next_fire_at {r.next_fire_at} not > now {now}"
        )


def test_install_preserves_metrics_on_reinstall(db_path):
    """If a row already has last_run_at / total_runs, reinstall keeps them."""
    d = SchedulerDaemon(db_path)
    install_default_jobs(d)
    # Soggetto derivato dalla fonte di verita' (§7.3): un builtin qualsiasi
    # del set corrente, cosi' il test non si lega a un nome consolidato-via
    # (es. ADR 0167 ha rimosso `apply_ager` → `nightly_aging`).
    name = _BUILTIN_JOBS[0]["name"]
    entry = d.storage.get_by_name(name)
    assert entry is not None
    d.storage.record_outcome(
        entry.id,  # type: ignore[arg-type]
        status="success",
        duration_ms=1234,
        error=None,
        next_fire_at=entry.next_fire_at + 86400,
        decrement_remaining=False,
        disable=False,
    )
    before = d.storage.get_by_name(name)
    assert before.total_runs == 1  # type: ignore[union-attr]
    # Re-install: must not reset metrics.
    install_default_jobs(d)
    after = d.storage.get_by_name(name)
    assert after.total_runs == 1  # type: ignore[union-attr]
    assert after.last_duration_ms == 1234  # type: ignore[union-attr]


def test_origin_and_recurring_set(db_path):
    d = SchedulerDaemon(db_path)
    install_default_jobs(d)
    for r in d.storage.list_all():
        assert r.origin == "system"
        assert r.recurring is True
        assert r.callback_key  # non-empty
