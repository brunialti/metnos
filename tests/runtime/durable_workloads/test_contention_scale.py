"""Bounded synthetic profiling of writer contention; never uses production data.

Run with ``timeout 90 python -m pytest .../test_contention_scale.py -s``.
The optional stress fixture is opt-in because it uses 31 independent connections.
It records measurements, not a machine-dependent performance pass threshold.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from durable_workloads.coordinator import ValidatedResult, instant_text
from durable_workloads.models import WorkloadState
from durable_workloads.storage import DurableWorkloadStore
from helpers import inventory, plan, source
from test_entry_identity_fanout import _capabilities, _entry_fanout_plan


class _Profile:
    def __init__(self):
        self.operation = "idle"
        self.started = self.acquired = None
        self.transactions = []
        self.calls = []
        self.queries = Counter()

    def trace(self, _statement):
        # Do not retain SQL or JSON payloads; count work at the current boundary.
        self.queries[self.operation] += 1

    def checkpoint(self, name):
        now = time.perf_counter()
        if name == "workload_transaction_before_begin":
            self.started, self.acquired = now, None
        elif name == "workload_transaction_after_begin":
            self.acquired = now
        elif name in {"workload_transaction_after_commit", "workload_transaction_after_rollback"}:
            self.transactions.append((self.operation, self.acquired - self.started, now - self.acquired))

    def call(self, name, function, *args, **kwargs):
        self.operation = name
        started = time.perf_counter()
        error = None
        try:
            return function(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            error = getattr(exc, "sqlite_errorname", type(exc).__name__)
            raise
        finally:
            self.calls.append((name, time.perf_counter() - started, error))
            self.operation = "idle"


def _seed(path, entry_bytes=0):
    """One executed parent and 965 children, below a strict 967-unit ceiling.

    The sealed inventory is a declared stage, not a runnable unit in this plan.
    """
    now = datetime.now(timezone.utc)
    candidate = _entry_fanout_plan()
    candidate["budgets"]["max_concurrency"] = 31
    candidate["budgets"]["max_units"] = 967
    candidate["stages"][1]["cardinality"]["max_units"] = 1
    candidate["stages"][-1]["cardinality"]["max_units"] = 965
    candidate["stages"][-1]["timeout_s"] = 300
    with DurableWorkloadStore.open(path) as store:
        draft = store.create_draft("owner-a", "contention-scale", redacted_request={"summary": "Synthetic fanout"})
        store.admit_revision("owner-a", draft.workload_id, candidate, inventory([source(0)]), expected_version=draft.version)
        admitted = store.get_workload("owner-a", draft.workload_id)
        store.transition_workload("owner-a", draft.workload_id, WorkloadState.QUEUED, expected_version=admitted.version, now=now)
        lease = store.claim_next("fixture-parent", now, timedelta(seconds=120), _capabilities())
        assert lease.stage_key == "map"
        store.mark_running(lease, now=now)
        entries = [{"entry_id": f"group-{index}", "text": f"Synthetic group {index}" + "x" * entry_bytes} for index in range(965)]
        committed = store.commit_result(lease, ValidatedResult.from_payload(lease.output_schema_version, {"entries": entries}), now=now)
        return draft.workload_id, committed.result_id


def _summary(profiles, wall_s):
    operations = defaultdict(lambda: {"calls": 0, "errors": Counter(), "total_s": 0, "max_call_s": 0,
                                      "transactions": 0, "max_wait_s": 0, "max_hold_s": 0, "hold_s": 0, "queries": 0})
    for profile in profiles:
        for name, elapsed, error in profile.calls:
            item = operations[name]
            item["calls"] += 1
            item["total_s"] += elapsed
            item["max_call_s"] = max(item["max_call_s"], elapsed)
            if error:
                item["errors"][error] += 1
        for name, wait, hold in profile.transactions:
            item = operations[name]
            item["transactions"] += 1
            item["max_wait_s"] = max(item["max_wait_s"], wait)
            item["max_hold_s"] = max(item["max_hold_s"], hold)
            item["hold_s"] += hold
        for name, count in profile.queries.items():
            operations[name]["queries"] += count
    return {"wall_s": wall_s, "operations": dict(operations)}


@pytest.mark.skipif(os.environ.get("METNOS_TEST_CONTENTION_SCALE") != "1", reason="31-lane profiling is explicitly opt-in")
@pytest.mark.parametrize(("rounds", "entry_bytes", "maintenance"), [
    (6, 0, "per_lane"), (32, 0, "per_lane"),
    (32, 4096, "per_lane"), (32, 4096, "once"),
])
def test_profile_31_lanes_967_fanout_units(tmp_path, rounds, entry_bytes, maintenance):
    path = tmp_path / "private" / "state.sqlite3"
    workload_id, parent_result_id = _seed(path, entry_bytes)
    barrier = Barrier(31)
    started = time.perf_counter()
    deadline = time.monotonic() + 45
    controller = _Profile()
    if maintenance == "once":
        # Counterfactual for this already-sealed graph, NOT an implementation:
        # production would still need periodic maintenance and lease recovery.
        with DurableWorkloadStore.open(path, checkpoint=controller.checkpoint) as store:
            store._connection.set_trace_callback(controller.trace)
            controller.call("reconcile", store.reconcile_expired, datetime.now(timezone.utc), 200)
            controller.call("settle", store.settle_workloads)
            controller.call("reuse", store.adopt_reusable_results, limit=200)
            while controller.call("materialize", store.materialize_all_ready_units, limit=200):
                assert time.monotonic() < deadline

    def lane(index):
        profile = _Profile()
        with DurableWorkloadStore.open(path, checkpoint=profile.checkpoint) as store:
            store._connection.set_trace_callback(profile.trace)
            barrier.wait(timeout=10)
            for _round in range(rounds):
                if time.monotonic() >= deadline:
                    break
                try:
                    if maintenance == "per_lane":
                        profile.call("reconcile", store.reconcile_expired, datetime.now(timezone.utc), 200)
                        profile.call("settle", store.settle_workloads)
                        profile.call("reuse", store.adopt_reusable_results, limit=200)
                        profile.call("materialize", store.materialize_all_ready_units, limit=200)
                    lease = profile.call("claim", store.claim_next, f"lane-{index}", datetime.now(timezone.utc), timedelta(seconds=120), _capabilities())
                    if lease:
                        profile.call("mark_running", store.mark_running, lease, now=datetime.now(timezone.utc))
                        profile.call("heartbeat", store.heartbeat, lease, datetime.now(timezone.utc) + timedelta(seconds=180))
                        profile.call("commit", store.commit_result, lease, ValidatedResult.from_payload(lease.output_schema_version, {"fixture": index}), dependency_result_ids=(parent_result_id,))
                except sqlite3.OperationalError:
                    # Capture the original busy event; do not invent a product retry.
                    continue
        return profile

    with ThreadPoolExecutor(max_workers=31) as pool:
        profiles = list(pool.map(lane, range(31)))
    report = _summary([*profiles, controller], time.perf_counter() - started)
    report["rounds_per_lane"] = rounds
    report["synthetic_extra_bytes_per_entry"] = entry_bytes
    report["maintenance"] = maintenance
    with DurableWorkloadStore.open(path) as store:
        report["unit_count"] = store._connection.execute("SELECT COUNT(*) FROM units").fetchone()[0]
        report["state"] = store.get_workload("owner-a", workload_id).state.value
        report["committed_units"] = store._connection.execute("SELECT COUNT(*) FROM units WHERE state='committed'").fetchone()[0]
        assert report["unit_count"] <= 967
        assert store._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    print("\nCONTENTION_PROFILE=" + json.dumps(report, sort_keys=True))
    assert len(profiles) == 31


def _admit_one(store, owner="owner-a", key="fixture"):
    draft = store.create_draft(owner, key, redacted_request={"summary": "Synthetic maintenance"})
    store.admit_revision(owner, draft.workload_id, plan(with_map=True), inventory([source(0)]), expected_version=draft.version)
    admitted = store.get_workload(owner, draft.workload_id)
    store.transition_workload(owner, draft.workload_id, WorkloadState.QUEUED, expected_version=admitted.version)
    return draft.workload_id


@contextmanager
def _writer_held(path):
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        yield writer
    finally:
        if writer.in_transaction:
            writer.rollback()
        writer.close()


def test_empty_maintenance_never_requests_external_writer(tmp_path):
    path = tmp_path / "private" / "state.sqlite3"
    checkpoints = []
    with DurableWorkloadStore.open(path, checkpoint=checkpoints.append) as store:
        _admit_one(store)
        now = datetime.now(timezone.utc)
        lease = store.claim_next("fixture", now, timedelta(seconds=60), _capabilities())
        assert lease is not None
        checkpoints.clear()
        store._connection.execute("PRAGMA busy_timeout=50")
        with _writer_held(path):
            assert store.reconcile_expired(now, 200).expired == 0
            assert store.adopt_reusable_results() == 0
        assert checkpoints == []


@pytest.mark.parametrize("reason", ["expired", "revision_clock", "unit_clock", "old_revision", "due_retry", "missing_usage"])
def test_recovery_preflight_keeps_all_recovery_reasons(tmp_path, reason):
    path = tmp_path / "private" / "state.sqlite3"
    with DurableWorkloadStore.open(path) as store:
        workload_id = _admit_one(store)
        now = datetime.now(timezone.utc)
        lease = store.claim_next("fixture", now, timedelta(seconds=60), _capabilities())
        future = instant_text(now + timedelta(seconds=120))
        past = instant_text(now - timedelta(seconds=1))
        selected_now = now
        if reason in {"expired", "old_revision", "missing_usage"}:
            selected_now = now + timedelta(seconds=61)
        if reason == "revision_clock":
            store._connection.execute("UPDATE revision_usage SET clock_high_water_at=? WHERE revision_id=?", (future, lease.revision_id))
        elif reason == "unit_clock":
            store._connection.execute("UPDATE units SET updated_at=? WHERE id=?", (future, lease.unit_id))
        elif reason == "old_revision":
            store._connection.execute("UPDATE workloads SET active_revision_id=NULL WHERE id=?", (workload_id,))
        elif reason == "due_retry":
            store._connection.execute("UPDATE units SET state='retry_wait', next_attempt_at=?, active_attempt_id=NULL, lease_worker_id=NULL, lease_expires_at=NULL WHERE id=?", (past, lease.unit_id))
        elif reason == "missing_usage":
            store._connection.execute("DELETE FROM revision_usage WHERE revision_id=?", (lease.revision_id,))
        store._connection.execute("PRAGMA busy_timeout=50")
        with _writer_held(path), pytest.raises(sqlite3.OperationalError) as error:
            store.reconcile_expired(selected_now, 200)
        assert error.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
        result = store.reconcile_expired(selected_now, 200)
        if reason == "due_retry":
            assert result.retry_promoted == 1
        elif reason == "missing_usage":
            # The original authoritative inner join still excludes broken
            # usage; the broad probe must not silently invent recovery.
            assert result.expired == 0
        else:
            assert result.expired == 1
            if reason in {"revision_clock", "unit_clock"}:
                assert result.needs_attention == 1


@pytest.mark.parametrize("same_owner", [True, False])
def test_reuse_preflight_preserves_owner_and_positive_writer_check(tmp_path, same_owner):
    path = tmp_path / "private" / "state.sqlite3"
    with DurableWorkloadStore.open(path) as store:
        _admit_one(store, key="first")
        now = datetime.now(timezone.utc)
        lease = store.claim_next("fixture", now, timedelta(seconds=60), _capabilities())
        store.mark_running(lease, now=now)
        store.commit_result(lease, ValidatedResult.from_payload(lease.output_schema_version, {"fixture": True}), now=now)
        _admit_one(store, owner="owner-a" if same_owner else "owner-b", key="second")
        store._connection.execute("PRAGMA busy_timeout=50")
        with _writer_held(path):
            if same_owner:
                with pytest.raises(sqlite3.OperationalError) as error:
                    store.adopt_reusable_results()
                assert error.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
            else:
                assert store.adopt_reusable_results() == 0
        assert store.adopt_reusable_results() == int(same_owner)


@pytest.mark.parametrize("operation", ["reconcile", "reuse"])
def test_positive_probe_is_reselected_under_writer_transaction(tmp_path, operation):
    path = tmp_path / "private" / "state.sqlite3"
    with DurableWorkloadStore.open(path) as store:
        workload_id = _admit_one(store, key="first")
        now = datetime.now(timezone.utc)
        lease = store.claim_next("fixture", now, timedelta(seconds=60), _capabilities())
        if operation == "reuse":
            store.mark_running(lease, now=now)
            store.commit_result(lease, ValidatedResult.from_payload(lease.output_schema_version, {"fixture": True}), now=now)
            workload_id = _admit_one(store, key="second")
        changes = []

        def before_begin(name):
            if name != "workload_transaction_before_begin" or changes:
                return
            changes.append(name)
            # A real independent connection changes the candidate after the
            # optimistic probe and before this connection obtains the writer.
            with sqlite3.connect(path, isolation_level=None) as peer:
                if operation == "reconcile":
                    peer.execute("UPDATE units SET lease_expires_at=? WHERE id=?", (instant_text(now + timedelta(seconds=180)), lease.unit_id))
                else:
                    peer.execute("UPDATE workloads SET state='paused' WHERE id=?", (workload_id,))

        store._checkpoint = before_begin
        if operation == "reconcile":
            result = store.reconcile_expired(now + timedelta(seconds=61), 200)
            assert result.expired == 0
            assert store._connection.execute("SELECT state FROM units WHERE id=?", (lease.unit_id,)).fetchone()[0] == "leased"
        else:
            assert store.adopt_reusable_results() == 0
            assert store._connection.execute("SELECT state FROM units WHERE revision_id=(SELECT active_revision_id FROM workloads WHERE id=?)", (workload_id,)).fetchone()[0] == "pending"
        assert len(changes) == 1
