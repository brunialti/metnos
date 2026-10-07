"""Retention of jobs created by the actual durable and scheduler owners."""
from datetime import datetime, timezone
import os
import sqlite3

import pytest

from tests.portable import durable_workload_fixtures as fixtures

from durable_workloads.migrations import BUSY_TIMEOUT_MS, migrate
from durable_workloads.models import WorkloadState
from durable_workloads.storage import DurableWorkloadStore
from durable_workloads.temporary_storage import TemporaryStorage, TemporaryWorkspace
from durable_workloads.image_indexing import temporary_workspaces
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_jobs_sqlite import _WorkloadOwner, _scheduler_owners
from scheduler_v2.models import ScheduleEntry
from scheduler_v2.storage import SchedulerStorage


OLD = "2020-01-01T00:00:00Z"
NOW = datetime(2020, 1, 1, 0, 1, tzinfo=timezone.utc)
_NATIVE_WORKLOAD = pytest.param("native", marks=pytest.mark.skipif(
    os.name != "posix", reason="native POSIX workload bootstrap and workspace custody"))


def _no_fixture_workspaces(plan):
    # Only this exact synthetic inventory plan has no native workspace owner.
    assert plan == fixtures.plan()
    assert [stage["runner"] for stage in plan["stages"]] == [
        {"kind": "internal", "name": "sealed_inventory"},
    ]
    assert plan["required_artifacts"] == []
    return ()


@pytest.fixture(params=["sql", _NATIVE_WORKLOAD])
def workload(request, tmp_path, monkeypatch):
    monkeypatch.setattr("durable_workloads.storage.utc_now", lambda: OLD)
    monkeypatch.setattr("durable_workloads.temporary_storage.utc_now", lambda: OLD)
    path = tmp_path / "workloads.sqlite"
    owner = _WorkloadOwner(path=path, require_exclusion=lambda: None, owner=None,
                           resolve_workspaces=(_no_fixture_workspaces if request.param == "sql"
                                               else temporary_workspaces))
    if request.param == "sql":
        # Exercise real SQL owners without claiming the POSIX LRE bootstrap
        # or image-workspace custody. Keep its connection settings and schema.
        connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000,
                                     isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
            connection.execute("PRAGMA synchronous=NORMAL")
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
            migrate(connection)
            store = DurableWorkloadStore(connection)
        except BaseException:
            connection.close()
            raise
    else:
        store = DurableWorkloadStore.open(path)
    def create(user, *, finish=True):
        draft = store.create_draft(user, "request", redacted_request={"summary": "retention"},
                                   workload_id="same-local-id")
        store.admit_revision(user, draft.workload_id, fixtures.plan(), fixtures.inventory(),
                             expected_version=draft.version)
        if finish:
            current = store.get_workload(user, draft.workload_id)
            store.transition_workload(user, draft.workload_id, WorkloadState.CANCELLED,
                                      expected_version=current.version, now=NOW)
            row = store._connection.execute("SELECT * FROM workloads WHERE owner_user_id=? AND id=?",
                                            (user, draft.workload_id)).fetchone()
            TemporaryStorage(store, owner.resolve_workspaces).observe(row)
        return draft
    yield owner, store, create
    store.close()


def deliver(store):
    for channel in ("owner_event", "telegram"):
        for row in store.claim_outbox(channel=channel, worker_id="retention-test", now=NOW):
            assert store.confirm_outbox(row, worker_id="retention-test", now=NOW)


def test_closed_job_cascades_with_native_guards_and_preserves_other_owner(workload):
    owner, store, create = workload
    create("owner-a")
    create("owner-b")
    deliver(store)
    before = {row.values["owner_user_id"]: row for row in owner.scan()}
    first, other = before["owner-a"], before["owner-b"]
    assert first.identity != other.identity
    assert dict(first.related)["stages"]  # admitted, immutable native revision
    assert owner.state_of(first).state is NodeState.CLOSED
    owner.delete(first.identity, first.version)
    assert owner.version(first.identity) is None
    owner.delete(first.identity, first.version)
    assert owner.version(other.identity) == other.version
    with sqlite3.connect(owner.path) as connection:
        for table, _rows in first.related:
            assert connection.execute(
                f'SELECT count(*) FROM "{table}" WHERE owner_user_id=?', ("owner-a",)
            ).fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("finish", [False, True])
def test_unfinished_job_or_delivery_keeps_entire_bundle(workload, finish):
    owner, store, create = workload
    create("owner-a", finish=finish)
    if not finish:
        deliver(store)
    row, = owner.scan()
    assert owner.state_of(row).roots == (RootKind.IN_PROGRESS_JOB,)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        owner.delete(row.identity, row.version)


def test_native_delivery_change_invalidates_collected_version(workload):
    owner, store, create = workload
    create("owner-a")
    original, = owner.scan()
    deliver(store)
    assert owner.version(original.identity) != original.version
    with pytest.raises(RetentionError, match="retention_owner_changed"):
        owner.delete(original.identity, original.version)


def test_empty_store_with_wrong_version_is_not_complete(workload):
    owner, _store, _create = workload
    with sqlite3.connect(owner.path) as connection:
        connection.execute("UPDATE durable_schema SET version=0")
    with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
        owner.scan()


def test_scheduler_retains_schedule_and_live_run_but_collects_finished_run(tmp_path, monkeypatch):
    monkeypatch.setattr("scheduler_v2.storage._utc_iso", lambda: OLD)
    path = tmp_path / "scheduler.sqlite"
    store = SchedulerStorage(path)
    try:
        entry = store.upsert(ScheduleEntry("kept", "interval:60", 1, True, "test"))
        done = store.begin_run(entry, "kept")
        store.end_run(done, status="success", duration_ms=1)
        live = store.begin_run(entry, "kept")
        schedules, runs = _scheduler_owners(path=path, require_exclusion=lambda: None, owner=None)
        schedule, = schedules.scan()
        assert schedules.state_of(schedule).roots == (RootKind.IN_PROGRESS_JOB,)
        rows = {row.values["id"]: row for row in runs.scan()}
        assert runs.state_of(rows[live]).roots == (RootKind.IN_PROGRESS_JOB,)
        with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
            runs.delete(rows[live].identity, rows[live].version)
        runs.delete(rows[done].identity, rows[done].version)
        assert runs.version(rows[done].identity) is None
        assert runs.version(rows[live].identity) == rows[live].version
        assert schedules.version(schedule.identity) == schedule.version
    finally:
        store.close()


@pytest.mark.parametrize("change", ["missing", "stale"])
def test_missing_or_stale_native_clean_report_does_not_close_job(workload, change):
    owner, store, create = workload
    create("owner-a"); deliver(store)
    row, = owner.scan()
    assert owner.state_of(row).state is NodeState.CLOSED
    with store._transaction() as connection:
        if change == "missing":
            connection.execute("DELETE FROM workload_temporary_storage")
        else:
            connection.execute("UPDATE workload_temporary_storage SET workload_version=workload_version-1")
    row, = owner.scan()
    assert owner.state_of(row).state is NodeState.OPEN
    with pytest.raises(RetentionError, match="state_invalid"):
        owner.delete(row.identity, row.version)


def test_clean_report_without_resolver_is_not_a_closure_proof(workload):
    owner, store, create = workload
    create("owner-a"); deliver(store)
    row, = owner.scan()
    owner.resolve_workspaces = None
    with pytest.raises(RetentionError, match="resolver missing"):
        owner.state_of(row)
    with pytest.raises(RetentionError, match="resolver missing"):
        owner.scan()


@pytest.mark.parametrize("workload", [_NATIVE_WORKLOAD], indirect=True)
def test_reappeared_scratch_blocks_native_job_deletion_even_with_fresh_clean_report(workload, tmp_path):
    owner, store, create = workload
    workspace = TemporaryWorkspace(tmp_path / "scratch-parent", "generation")
    owner.resolve_workspaces = lambda plan: (workspace,)
    create("owner-a"); deliver(store)
    row, = owner.scan()
    assert owner.state_of(row).state is NodeState.CLOSED
    directory = workspace.parent / workspace.name
    directory.mkdir(mode=0o700)
    (directory / "new-payload").write_bytes(b"unobserved content")
    # No SQL mutation: the old row version alone cannot detect this change.
    assert owner.version(row.identity) == row.version
    assert owner.state_of(row).state is NodeState.OPEN
    with pytest.raises(RetentionError, match="state_invalid"):
        owner.delete(row.identity, row.version)
    assert (directory / "new-payload").read_bytes() == b"unobserved content"


@pytest.mark.parametrize("change", ["parent-missing", "parent-symlink", "fence-missing", "fence-reset", "residual"])
@pytest.mark.parametrize("workload", [_NATIVE_WORKLOAD], indirect=True)
def test_absence_requires_custodied_parent_and_permanent_native_fence(workload, tmp_path, change):
    owner, store, create = workload
    workspace = TemporaryWorkspace(tmp_path / "scratch-parent", "generation")
    owner.resolve_workspaces = lambda plan: (workspace,)
    create("owner-a"); deliver(store)
    row, = owner.scan()
    assert owner.state_of(row).state is NodeState.CLOSED
    fence = workspace.parent / (".lre-lock-" + workspace.name)
    if change == "parent-missing":
        workspace.parent.rename(tmp_path / "moved")
    elif change == "parent-symlink":
        workspace.parent.rename(tmp_path / "moved")
        workspace.parent.symlink_to(tmp_path / "moved")
    elif change == "fence-missing":
        fence.unlink()
    elif change == "fence-reset":
        fence.write_bytes(b"")
    else:
        (workspace.parent / workspace.removing).mkdir(mode=0o700)
    if change == "parent-symlink":
        with pytest.raises(RetentionError, match="path_invalid"):
            owner.state_of(row)
    else:
        assert owner.state_of(row).state is NodeState.OPEN
    with pytest.raises(RetentionError):
        owner.delete(row.identity, row.version)
