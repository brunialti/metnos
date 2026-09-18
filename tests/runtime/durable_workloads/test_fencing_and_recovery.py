from __future__ import annotations

import json
import multiprocessing
import os
import signal
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty
from threading import Event
from time import monotonic

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from durable_workloads.activity import activity_snapshot
from durable_workloads.coordinator import (
    CommitStatus,
    FailureStatus,
    LeaseMutationStatus,
    RetryDecision,
    StructuredAttemptError,
    ValidatedResult,
    WorkerCapabilities,
    decide_retry,
    deterministic_retry_delay_ms,
    parse_instant,
)
from durable_workloads.models import RunnerKind, WorkloadState
from durable_workloads.storage import (
    DurableWorkloadStore,
    RetryDecisionConflictError,
)
from durable_workloads.worker import DurableWorker, WorkerRunStatus
from helpers import inventory, plan, source


BASE_TIME = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _capabilities() -> WorkerCapabilities:
    return WorkerCapabilities.create(
        ((RunnerKind.EXECUTOR, "read_files_ocr"),),
        {key: 0 for key in (
            "cpu", "device", "llm", "local_io", "network_io", "vlm",
        )},
    )


def _model_capabilities() -> WorkerCapabilities:
    return WorkerCapabilities.create(
        ((RunnerKind.EXECUTOR, "read_files_ocr"),),
        {
            "cpu": 0, "device": 0, "llm": 1,
            "local_io": 0, "network_io": 0, "vlm": 0,
        },
    )


def _prepare_one(
    store: DurableWorkloadStore,
    suffix: str,
    *,
    max_attempts: int = 3,
    max_wall_time_s: int = 3600,
    base_delay_ms: int = 0,
    max_delay_ms: int = 0,
    retryable_error_classes: tuple[str, ...] = ("executor_transient",),
    queue_at: datetime = BASE_TIME,
) -> str:
    selected_plan = plan(with_map=True)
    selected_plan["budgets"]["max_wall_time_s"] = max_wall_time_s
    selected_plan["stages"][1]["retry"] = {
        "max_attempts": max_attempts,
        "base_delay_ms": base_delay_ms,
        "max_delay_ms": max_delay_ms,
        "retryable_error_classes": list(retryable_error_classes),
    }
    draft = store.create_draft(
        "owner-a",
        f"f3-request-{suffix}",
        redacted_request={"summary": "synthetic F3 fixture"},
    )
    store.admit_revision(
        "owner-a",
        draft.workload_id,
        selected_plan,
        inventory([source(0)]),
        expected_version=draft.version,
    )
    admitted = store.get_workload("owner-a", draft.workload_id)
    store.transition_workload(
        "owner-a",
        draft.workload_id,
        WorkloadState.QUEUED,
        expected_version=admitted.version,
        now=queue_at,
    )
    return draft.workload_id


def _prepare_many(
    store: DurableWorkloadStore,
    suffix: str,
    count: int,
) -> str:
    selected_plan = plan(with_map=True)
    draft = store.create_draft(
        "owner-a",
        f"f3-many-{suffix}",
        redacted_request={"summary": "synthetic fairness fixture"},
    )
    store.admit_revision(
        "owner-a",
        draft.workload_id,
        selected_plan,
        inventory([source(index) for index in range(count)]),
        expected_version=draft.version,
    )
    admitted = store.get_workload("owner-a", draft.workload_id)
    store.transition_workload(
        "owner-a",
        draft.workload_id,
        WorkloadState.QUEUED,
        expected_version=admitted.version,
        now=BASE_TIME,
    )
    return draft.workload_id


def _unit_row(store: DurableWorkloadStore, workload_id: str):
    return store._connection.execute(
        """
        SELECT u.* FROM units u
        JOIN revisions r
          ON r.owner_user_id=u.owner_user_id AND r.id=u.revision_id
        WHERE r.owner_user_id='owner-a' AND r.workload_id=?
        """,
        (workload_id,),
    ).fetchone()


def _result(lease, marker: str) -> ValidatedResult:
    return ValidatedResult.from_payload(
        lease.output_schema_version,
        {"ok": True, "marker": marker},
    )


def _race_worker(
    db_path: str,
    worker_id: str,
    barrier,
    commands,
    outcomes,
) -> None:
    with DurableWorkloadStore.open(db_path) as store:
        while True:
            round_number = commands.recv()
            if round_number is None:
                return
            barrier.wait(timeout=10)
            now = BASE_TIME + timedelta(minutes=int(round_number))
            lease = store.claim_next(
                worker_id,
                now,
                timedelta(seconds=30),
                _capabilities(),
            )
            if lease is None:
                outcomes.put((round_number, worker_id, "none", None))
                continue
            running = store.mark_running(
                lease, now=now + timedelta(microseconds=1),
            )
            commit = store.commit_result(
                lease,
                _result(lease, worker_id),
                now=now + timedelta(microseconds=2),
            )
            outcomes.put((
                round_number,
                worker_id,
                f"{running.value}:{commit.status.value}",
                lease.unit_id,
            ))


def _fenced_commit_worker(
    db_path: str,
    process_name: str,
    barrier,
    commands,
    outcomes,
) -> None:
    with DurableWorkloadStore.open(db_path) as store:
        while True:
            command = commands.recv()
            if command is None:
                return
            round_number, lease, marker, now = command
            barrier.wait(timeout=10)
            commit = store.commit_result(
                lease,
                _result(lease, marker),
                now=now,
            )
            outcomes.put((
                round_number,
                process_name,
                commit.status.value,
                commit.winning_digest,
            ))


def _crash_worker(db_path: str, mode: str, control, now: datetime) -> None:
    with DurableWorkloadStore.open(db_path) as store:
        lease = store.claim_next(
            "crash-worker",
            now,
            timedelta(seconds=10),
            _capabilities(),
        )
        if lease is None:
            control.send(("claim_missing",))
            return
        if mode != "before_execution":
            store.mark_running(lease, now=now + timedelta(microseconds=1))
            result = _result(lease, mode)
            if mode == "after_commit":
                store.commit_result(
                    lease, result, now=now + timedelta(microseconds=2),
                )
        control.send(("at_control_point", mode))
        control.recv()


def _late_worker(db_path: str, control) -> None:
    with DurableWorkloadStore.open(db_path) as store:
        lease = store.claim_next(
            "old-worker",
            BASE_TIME,
            timedelta(seconds=10),
            _capabilities(),
        )
        assert lease is not None
        assert store.mark_running(
            lease, now=BASE_TIME + timedelta(microseconds=1),
        ) is LeaseMutationStatus.APPLIED
        result = _result(lease, "old")
        control.send(("result_ready", lease.fence))
        control.recv()
        heartbeat = store.heartbeat(
            lease,
            BASE_TIME + timedelta(seconds=60),
            now=BASE_TIME + timedelta(seconds=21),
        )
        commit = store.commit_result(
            lease,
            result,
            now=BASE_TIME + timedelta(seconds=21),
        )
        control.send((heartbeat.value, commit.status.value))


def _reconcile_worker(db_path: str, now: datetime, control) -> None:
    with DurableWorkloadStore.open(db_path) as store:
        outcome = store.reconcile_expired(now, 100)
        row = store._connection.execute(
            """
            SELECT state, next_attempt_at, fence
            FROM units WHERE owner_user_id='owner-a'
            """
        ).fetchone()
        control.send((outcome, tuple(row) if row is not None else None))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "durable" / "state.sqlite3"
    with DurableWorkloadStore.open(path):
        pass
    return path


def test_claim_heartbeat_commit_replay_and_digest_conflict(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(store, "basic")
        lease = store.claim_next(
            "worker-a", BASE_TIME, timedelta(seconds=30), _capabilities(),
        )
        assert lease is not None
        assert lease.fence == 1
        assert store.get_workload(
            "owner-a", workload_id,
        ).state is WorkloadState.RUNNING
        assert store.mark_running(
            lease, now=BASE_TIME + timedelta(seconds=1),
        ) is LeaseMutationStatus.APPLIED
        assert store.mark_running(
            lease, now=BASE_TIME + timedelta(seconds=1),
        ) is LeaseMutationStatus.ALREADY_APPLIED
        assert store.heartbeat(
            lease,
            BASE_TIME + timedelta(seconds=60),
            now=BASE_TIME + timedelta(seconds=2),
        ) is LeaseMutationStatus.APPLIED

        first = _result(lease, "first")
        committed = store.commit_result(
            lease, first, now=BASE_TIME + timedelta(seconds=3),
        )
        assert committed.status is CommitStatus.COMMITTED
        assert store.commit_result(
            lease, first, now=BASE_TIME + timedelta(seconds=4),
        ).status is CommitStatus.IDEMPOTENT_REPLAY
        conflict = store.commit_result(
            lease,
            _result(lease, "different"),
            now=BASE_TIME + timedelta(seconds=5),
        )
        assert conflict.status is CommitStatus.DIGEST_CONFLICT
        assert conflict.winning_digest == first.digest
        assert store._connection.execute(
            "SELECT COUNT(*) FROM results WHERE owner_user_id='owner-a'"
        ).fetchone()[0] == 1


def test_commit_marks_only_the_last_unit_in_a_stage_as_terminal(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_many(store, "stage-terminal", 2)
        for offset, expected_terminal in enumerate((False, True)):
            lease = store.claim_next(
                "worker-terminal",
                BASE_TIME + timedelta(seconds=offset * 2),
                timedelta(seconds=30),
                _capabilities(),
            )
            assert lease is not None
            assert store.mark_running(
                lease,
                now=BASE_TIME + timedelta(seconds=offset * 2 + 1),
            ) is LeaseMutationStatus.APPLIED
            outcome = store.commit_result(
                lease,
                _result(lease, f"terminal-{offset}"),
                now=BASE_TIME + timedelta(seconds=offset * 2 + 1, microseconds=1),
            )
            assert outcome.status is CommitStatus.COMMITTED
            assert outcome.stage_terminal is expected_terminal
            assert store.stage_is_terminal(lease) is expected_terminal


def test_capability_filter_and_full_lease_token_fail_closed(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(store, "closed-token")
        wrong_capability = WorkerCapabilities.create((
            (RunnerKind.INTERNAL, "sealed_inventory"),
        ), {key: 0 for key in (
            "cpu", "device", "llm", "local_io", "network_io", "vlm",
        )})
        assert store.claim_next(
            "worker-a",
            BASE_TIME,
            timedelta(seconds=30),
            wrong_capability,
        ) is None
        untouched = _unit_row(store, workload_id)
        assert untouched["state"] == "pending"
        assert untouched["fence"] == 0
        assert untouched["attempt_count"] == 0

        lease = store.claim_next(
            "worker-a", BASE_TIME, timedelta(seconds=30), _capabilities(),
        )
        assert lease is not None
        forged = replace(lease, fence=lease.fence + 1)
        assert store.mark_running(
            forged, now=BASE_TIME + timedelta(seconds=1),
        ) is LeaseMutationStatus.STALE_FENCE
        assert store.heartbeat(
            forged,
            BASE_TIME + timedelta(seconds=60),
            now=BASE_TIME + timedelta(seconds=1),
        ) is LeaseMutationStatus.STALE_FENCE
        assert store.commit_result(
            forged,
            _result(forged, "forged"),
            now=BASE_TIME + timedelta(seconds=1),
        ).status is CommitStatus.STALE_FENCE
        current = _unit_row(store, workload_id)
        assert current["state"] == "leased"
        assert current["fence"] == lease.fence
        assert current["active_attempt_id"] == lease.attempt_id


def test_f3_dummy_claim_does_not_admit_non_pure_effects(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        selected_plan = plan(with_map=True)
        selected_plan["stages"][1]["effect_profile"] = "manual_only"
        draft = store.create_draft(
            "owner-a",
            "f3-non-pure",
            redacted_request={"summary": "synthetic non-pure fixture"},
        )
        store.admit_revision(
            "owner-a",
            draft.workload_id,
            selected_plan,
            inventory([source(0)]),
            expected_version=draft.version,
        )
        admitted = store.get_workload("owner-a", draft.workload_id)
        store.transition_workload(
            "owner-a",
            draft.workload_id,
            WorkloadState.QUEUED,
            expected_version=admitted.version,
            now=BASE_TIME,
        )
        assert store.claim_next(
            "worker-a", BASE_TIME, timedelta(seconds=30), _capabilities(),
        ) is None
        assert _unit_row(store, draft.workload_id)["state"] == "pending"


def test_claim_rolls_back_attempt_fence_event_and_credit_together(
    db_path, monkeypatch,
):
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(store, "claim-rollback")

        def fail_event(*_args, **_kwargs):
            raise RuntimeError("injected claim event failure")

        monkeypatch.setattr(store, "append_event_in_transaction", fail_event)
        with pytest.raises(RuntimeError, match="injected claim event"):
            store.claim_next(
                "worker-a", BASE_TIME, timedelta(seconds=30), _capabilities(),
            )
        row = _unit_row(store, workload_id)
        assert row["state"] == "pending"
        assert row["attempt_count"] == 0
        assert row["fence"] == 0
        assert store.get_workload(
            "owner-a", workload_id,
        ).state is WorkloadState.QUEUED
        assert store._connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE owner_user_id='owner-a'"
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT COUNT(*) FROM scheduler_credits WHERE owner_user_id='owner-a'"
        ).fetchone()[0] == 0


def test_expired_lease_rejects_heartbeat_commit_and_then_recovers(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(store, "expired", base_delay_ms=0, max_delay_ms=0)
        lease = store.claim_next(
            "worker-a", BASE_TIME, timedelta(seconds=10), _capabilities(),
        )
        assert lease is not None
        assert store.mark_running(
            lease, now=BASE_TIME + timedelta(seconds=1),
        ) is LeaseMutationStatus.APPLIED
        assert store.heartbeat(
            lease,
            BASE_TIME + timedelta(seconds=30),
            now=BASE_TIME + timedelta(seconds=11),
        ) is LeaseMutationStatus.LEASE_EXPIRED
        assert store.commit_result(
            lease,
            _result(lease, "late"),
            now=BASE_TIME + timedelta(seconds=11),
        ).status is CommitStatus.LEASE_EXPIRED
        outcome = store.reconcile_expired(
            BASE_TIME + timedelta(seconds=11), 10,
        )
        assert outcome.expired == 1
        assert outcome.retry_scheduled == 1
        assert outcome.retry_promoted == 1
        replacement = store.claim_next(
            "worker-b",
            BASE_TIME + timedelta(seconds=12),
            timedelta(seconds=10),
            _capabilities(),
        )
        assert replacement is not None
        assert replacement.fence == lease.fence + 1


def test_attempt_budget_bounds_recovery_before_and_during_execution(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        before_workload = _prepare_one(
            store, "budget-before", max_attempts=1,
        )
        before = store.claim_next(
            "worker-before",
            BASE_TIME,
            timedelta(seconds=10),
            _capabilities(),
        )
        assert before is not None

        running_workload = _prepare_one(
            store, "budget-running", max_attempts=1,
        )
        running = store.claim_next(
            "worker-running",
            BASE_TIME,
            timedelta(seconds=10),
            _capabilities(),
        )
        assert running is not None
        assert store.mark_running(
            running, now=BASE_TIME + timedelta(seconds=1),
        ) is LeaseMutationStatus.APPLIED

        outcome = store.reconcile_expired(
            BASE_TIME + timedelta(seconds=11), 10,
        )
        assert outcome.expired == 2
        assert outcome.needs_attention == 2
        assert outcome.failed_permanent == 0
        assert _unit_row(store, before_workload)["state"] == "needs_attention"
        assert _unit_row(store, running_workload)["state"] == "needs_attention"
        error = json.loads(store._connection.execute(
            "SELECT structured_error_json FROM attempts WHERE id=?", (running.attempt_id,),
        ).fetchone()[0])
        assert error["retry"] == "manual"
        assert store.claim_next(
            "worker-late",
            BASE_TIME + timedelta(seconds=12),
            timedelta(seconds=10),
            _capabilities(),
        ) is None


def test_failure_decision_is_derived_and_retry_time_is_deterministic(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(
            store,
            "failure",
            base_delay_ms=1000,
            max_delay_ms=10_000,
        )
        lease = store.claim_next(
            "worker-a", BASE_TIME, timedelta(seconds=30), _capabilities(),
        )
        assert lease is not None
        store.mark_running(lease, now=BASE_TIME + timedelta(seconds=1))
        error = StructuredAttemptError.create(
            "executor_transient",
            code="executor.transient_fixture",
            message_key="ERR_DURABLE_EXECUTOR_TRANSIENT",
            retry="automatic",
            occurred_at=BASE_TIME + timedelta(seconds=2),
        )
        schema_path = (
            Path(__file__).resolve().parents[2]
            / "fixtures/durable_workloads/schemas/error-v1.schema.json"
        )
        validator = Draft202012Validator(
            json.loads(schema_path.read_text()),
            format_checker=FormatChecker(),
        )
        assert list(validator.iter_errors(json.loads(error.payload_json))) == []
        decision = decide_retry(
            effect_profile=lease.effect_profile,
            retry_policy=lease.retry_policy,
            attempt_number=lease.attempt_number,
            error_class=error.error_class,
        )
        assert decision is RetryDecision.RETRY
        with pytest.raises(RetryDecisionConflictError):
            store.fail_attempt(
                lease,
                error,
                RetryDecision.FAIL_PERMANENT,
                now=BASE_TIME + timedelta(seconds=2),
            )
        outcome = store.fail_attempt(
            lease, error, decision, now=BASE_TIME + timedelta(seconds=2),
        )
        assert outcome.status is FailureStatus.RETRY_SCHEDULED
        expected_delay = deterministic_retry_delay_ms(
            lease.unit_key,
            lease.attempt_number,
            base_delay_ms=1000,
            max_delay_ms=10_000,
        )
        assert parse_instant(outcome.next_attempt_at) == (
            BASE_TIME + timedelta(seconds=2, milliseconds=expected_delay)
        )


def test_worker_stops_claiming_and_does_not_heartbeat_after_stop(db_path):
    current = [BASE_TIME]
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(store, "stop")
        worker = DurableWorker(
            store,
            "worker-a",
            _capabilities(),
            lease_duration=timedelta(seconds=30),
            shutdown_grace=timedelta(seconds=5),
            clock=lambda: current[0],
        )
        lease = worker.claim_next()
        assert lease is not None
        current[0] += timedelta(seconds=1)
        worker.request_stop()
        assert worker.heartbeat(
            lease,
            current[0] + timedelta(seconds=30),
            now=current[0],
        ) is LeaseMutationStatus.STOP_REQUESTED
        outcome = worker.run_claimed(
            lease,
            lambda claimed: _result(claimed, "must-not-run"),
        )
        assert outcome.status is WorkerRunStatus.ABANDONED
        assert worker.claim_next() is None
        row = _unit_row(store, workload_id)
        assert row["state"] == "pending"
        assert row["active_attempt_id"] is None
        assert store._connection.execute(
            "SELECT COUNT(*) FROM results WHERE owner_user_id='owner-a'"
        ).fetchone()[0] == 0


def test_shutdown_deadline_abandons_the_active_lease_without_a_private_timer(
    db_path,
):
    current = [BASE_TIME]
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(store, "shutdown-deadline")
        worker = DurableWorker(
            store,
            "worker-a",
            _capabilities(),
            lease_duration=timedelta(seconds=30),
            shutdown_grace=timedelta(seconds=5),
            clock=lambda: current[0],
        )
        assert worker.claim_next() is not None
        current[0] += timedelta(seconds=1)
        worker.request_stop()
        assert worker.abandon_if_shutdown_due(
            now=current[0] + timedelta(seconds=4),
        ) is None
        assert worker.abandon_if_shutdown_due(
            now=current[0] + timedelta(seconds=5),
        ) is LeaseMutationStatus.APPLIED
        assert worker.shutdown_complete is True
        assert _unit_row(store, workload_id)["state"] == "pending"


def test_dummy_callable_executes_outside_the_database_transaction(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(store, "outside-transaction")
        worker = DurableWorker(
            store,
            "worker-a",
            _capabilities(),
            lease_duration=timedelta(seconds=30),
            clock=lambda: BASE_TIME + timedelta(seconds=1),
        )

        def adapter(lease):
            assert store._connection.in_transaction is False
            return _result(lease, "outside")

        outcome = worker.run_once(adapter)
        assert outcome.status is WorkerRunStatus.COMMITTED


def test_worker_renews_long_running_file_backed_lease_and_joins_heartbeat(db_path):
    observed_extension = []
    poll = Event()
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(
            store,
            "automatic-heartbeat",
            queue_at=datetime.now(timezone.utc),
        )
        worker = DurableWorker(
            store,
            "worker-a",
            _capabilities(),
            lease_duration=timedelta(milliseconds=250),
            heartbeat_interval=timedelta(milliseconds=40),
        )

        def adapter(lease):
            original_expiry = parse_instant(lease.lease_expires_at)
            wait_deadline = monotonic() + 2
            with store.open_peer() as observer:
                while monotonic() < wait_deadline:
                    row = observer._connection.execute(
                        """
                        SELECT lease_expires_at FROM units
                        WHERE owner_user_id=? AND id=?
                        """,
                        (lease.owner_user_id, lease.unit_id),
                    ).fetchone()
                    assert row is not None
                    renewed_expiry = parse_instant(str(row["lease_expires_at"]))
                    if renewed_expiry > original_expiry:
                        observed_extension.append(renewed_expiry)
                        break
                    poll.wait(0.005)
            assert observed_extension
            return _result(lease, "heartbeat")

        outcome = worker.run_once(adapter)
        assert outcome.status is WorkerRunStatus.COMMITTED
        assert worker._heartbeat_monitor is None


def test_stop_request_keeps_active_lease_alive_until_safe_commit(db_path):
    observed_extension = []
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(
            store,
            "drain-heartbeat",
            queue_at=datetime.now(timezone.utc),
        )
        worker = DurableWorker(
            store,
            "worker-a",
            _capabilities(),
            lease_duration=timedelta(milliseconds=250),
            heartbeat_interval=timedelta(milliseconds=40),
        )

        def adapter(lease):
            worker.request_stop()
            with store.open_peer() as observer:
                baseline = parse_instant(str(observer._connection.execute(
                    "SELECT lease_expires_at FROM units WHERE id=?",
                    (lease.unit_id,),
                ).fetchone()["lease_expires_at"]))
                wait_deadline = monotonic() + 2
                while monotonic() < wait_deadline:
                    renewed = parse_instant(str(observer._connection.execute(
                        "SELECT lease_expires_at FROM units WHERE id=?",
                        (lease.unit_id,),
                    ).fetchone()["lease_expires_at"]))
                    if renewed > baseline:
                        observed_extension.append(renewed)
                        break
                    time.sleep(0.005)
            assert observed_extension
            return _result(lease, "drained")

        outcome = worker.run_once(adapter)

        assert outcome.status is WorkerRunStatus.COMMITTED
        assert worker.stopping is True
        assert worker._heartbeat_monitor is None
        assert worker.claim_next() is None


def test_activity_snapshot_counts_only_authoritative_active_fences(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(store, "activity-snapshot")
        assert activity_snapshot(path=db_path) == {
            "schema_version": "metnos.durable-activity/1",
            "known": True,
            "reason_code": "none",
            "active_attempts": 0,
            "leased_attempts": 0,
            "running_attempts": 0,
        }
        lease = store.claim_next(
            "worker-a", BASE_TIME, timedelta(seconds=30), _capabilities(),
        )
        assert lease is not None
        leased = activity_snapshot(path=db_path)
        assert leased["active_attempts"] == 1
        assert leased["leased_attempts"] == 1
        assert leased["running_attempts"] == 0
        assert store.mark_running(lease, now=BASE_TIME) is LeaseMutationStatus.APPLIED
        running = activity_snapshot(path=db_path)
        assert running["active_attempts"] == 1
        assert running["leased_attempts"] == 0
        assert running["running_attempts"] == 1


def test_worker_discards_result_returned_after_frozen_stage_deadline(db_path):
    current = [BASE_TIME]
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(store, "hard-deadline")
        worker = DurableWorker(
            store,
            "worker-a",
            _capabilities(),
            lease_duration=timedelta(seconds=120),
            clock=lambda: current[0],
        )

        def adapter(lease):
            current[0] += timedelta(seconds=lease.timeout_s + 1)
            return _result(lease, "too-late")

        outcome = worker.run_once(adapter)
        assert outcome.status is WorkerRunStatus.FAILED
        assert outcome.failure.status is FailureStatus.RETRY_SCHEDULED
        assert store._connection.execute(
            "SELECT COUNT(*) FROM results WHERE owner_user_id='owner-a'"
        ).fetchone()[0] == 0
        attempt = store._connection.execute(
            """
            SELECT state, structured_error_json, metrics_json FROM attempts
            WHERE owner_user_id='owner-a'
            """
        ).fetchone()
        assert attempt["state"] == "timed_out"
        assert json.loads(attempt["structured_error_json"])["code"] == (
            "execution.timeout"
        )
        assert json.loads(attempt["metrics_json"])["timed_out"] is True


def test_running_lease_cannot_outlive_the_frozen_stage_deadline(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(store, "bounded-running-lease")
        lease = store.claim_next(
            "worker-a", BASE_TIME, timedelta(seconds=120), _capabilities(),
        )
        assert lease is not None
        assert store.mark_running(lease, now=BASE_TIME) is LeaseMutationStatus.APPLIED

        row = store._connection.execute(
            "SELECT lease_expires_at FROM units WHERE owner_user_id='owner-a'"
        ).fetchone()
        assert parse_instant(str(row["lease_expires_at"])) == (
            BASE_TIME + timedelta(seconds=lease.timeout_s)
        )
        with pytest.raises(ValueError, match="frozen execution deadline"):
            store.heartbeat(
                lease,
                BASE_TIME + timedelta(seconds=lease.timeout_s + 1),
                now=BASE_TIME + timedelta(seconds=1),
            )

        recovered = store.reconcile_expired(
            BASE_TIME + timedelta(seconds=lease.timeout_s), 1,
        )
        assert recovered.expired == 1


def test_backward_wall_clock_jump_cannot_extend_the_stage_deadline(db_path):
    wall = [BASE_TIME]
    monotonic_time = [100.0]
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(store, "backward-clock-deadline")
        worker = DurableWorker(
            store,
            "worker-a",
            _capabilities(),
            lease_duration=timedelta(seconds=120),
            clock=lambda: wall[0],
            monotonic_clock=lambda: monotonic_time[0],
        )

        def adapter(lease):
            wall[0] -= timedelta(hours=1)
            monotonic_time[0] += lease.timeout_s + 1
            return _result(lease, "must-be-discarded")

        outcome = worker.run_once(adapter)

        assert outcome.status is WorkerRunStatus.FAILED
        assert outcome.failure is not None
        assert outcome.failure.status is FailureStatus.RETRY_SCHEDULED
        assert store._connection.execute(
            "SELECT COUNT(*) FROM results WHERE owner_user_id='owner-a'"
        ).fetchone()[0] == 0
        attempt = store._connection.execute(
            "SELECT state FROM attempts WHERE owner_user_id='owner-a'"
        ).fetchone()
        assert attempt["state"] == "timed_out"


def test_queue_clock_rollback_converges_before_the_first_claim(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(store, "queued-backward-clock")
        usage = store._connection.execute(
            """
            SELECT started_at, clock_high_water_at
            FROM revision_usage
            WHERE owner_user_id='owner-a' AND revision_id=(
              SELECT active_revision_id FROM workloads
              WHERE owner_user_id='owner-a' AND id=?
            )
            """,
            (workload_id,),
        ).fetchone()
        assert usage["started_at"] == usage["clock_high_water_at"]

        regressed = BASE_TIME - timedelta(hours=1)
        assert store.claim_next(
            "worker-after-pre-claim-clock-jump",
            regressed,
            timedelta(seconds=120),
            _capabilities(),
        ) is None
        assert store.settle_workloads(now=regressed) == 1
        assert store.get_workload(
            "owner-a", workload_id,
        ).state is WorkloadState.NEEDS_ATTENTION
        event = store._connection.execute(
            """
            SELECT payload_json FROM events
            WHERE owner_user_id='owner-a' AND workload_id=?
            ORDER BY event_id DESC LIMIT 1
            """,
            (workload_id,),
        ).fetchone()
        evidence = json.loads(event["payload_json"])
        assert evidence["reason_code"] == "budget_accounting_incomplete"
        assert evidence["budget"] == "max_wall_time_s"
        assert evidence["clock_regressed"] is True


def test_restart_reconciles_an_active_lease_after_a_wall_clock_rollback(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(
            store,
            "restart-backward-clock",
            max_attempts=3,
        )
        lease = store.claim_next(
            "worker-before-clock-jump",
            BASE_TIME,
            timedelta(seconds=120),
            _capabilities(),
        )
        assert lease is not None
        assert store.mark_running(
            lease,
            now=BASE_TIME + timedelta(seconds=1),
        ) is LeaseMutationStatus.APPLIED

    regressed = BASE_TIME - timedelta(hours=1)
    with DurableWorkloadStore.open(db_path) as recovered:
        outcome = recovered.reconcile_expired(regressed, batch_size=100)

        assert outcome.expired == 1
        assert outcome.needs_attention == 1
        assert outcome.retry_scheduled == 0
        unit = _unit_row(recovered, workload_id)
        assert unit["state"] == "needs_attention"
        attempt = recovered._connection.execute(
            "SELECT structured_error_json, metrics_json FROM attempts "
            "WHERE owner_user_id='owner-a' AND id=?",
            (lease.attempt_id,),
        ).fetchone()
        assert json.loads(attempt["structured_error_json"])["code"] == (
            "lease.clock_regressed"
        )
        assert json.loads(attempt["metrics_json"])["clock_regressed"] is True
        assert recovered.get_workload(
            "owner-a", workload_id,
        ).state is WorkloadState.NEEDS_ATTENTION
        assert recovered.claim_next(
            "worker-after-clock-jump",
            regressed,
            timedelta(seconds=120),
            _capabilities(),
        ) is None


def test_small_wall_clock_adjustment_does_not_poison_the_revision(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(store, "small-backward-clock")
        lease = store.claim_next(
            "worker-small-clock-adjustment",
            BASE_TIME,
            timedelta(seconds=120),
            _capabilities(),
        )
        assert lease is not None

        assert store.budget_violation(
            lease,
            now=BASE_TIME - timedelta(seconds=1),
        ) is None


def test_commit_transaction_rejects_result_after_stage_deadline(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(store, "transaction-deadline")
        lease = store.claim_next(
            "worker-a", BASE_TIME, timedelta(seconds=120), _capabilities(),
        )
        assert lease is not None
        assert store.mark_running(lease, now=BASE_TIME) is LeaseMutationStatus.APPLIED

        outcome = store.commit_result(
            lease,
            _result(lease, "too-late-in-transaction"),
            now=BASE_TIME + timedelta(seconds=lease.timeout_s),
        )

        assert outcome.status is CommitStatus.DEADLINE_EXPIRED
        assert store._connection.execute(
            "SELECT COUNT(*) FROM results WHERE owner_user_id='owner-a'"
        ).fetchone()[0] == 0


def test_commit_uses_one_operation_time_for_the_wall_budget(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(
            store, "transaction-clock", max_wall_time_s=60,
        )
        revision_id = store.get_workload(
            "owner-a", workload_id,
        ).active_revision_id
        store._connection.execute(
            """
            UPDATE revision_usage SET started_at=?
            WHERE owner_user_id='owner-a' AND revision_id=?
            """,
            (BASE_TIME.isoformat().replace("+00:00", "Z"), revision_id),
        )
        lease = store.claim_next(
            "worker-a", BASE_TIME, timedelta(seconds=120), _capabilities(),
        )
        assert lease is not None
        assert store.mark_running(
            lease, now=BASE_TIME,
        ) is LeaseMutationStatus.APPLIED

        outcome = store.commit_result(
            lease,
            _result(lease, "consistent-transaction-clock"),
            now=BASE_TIME + timedelta(seconds=1),
        )

        assert outcome.status is CommitStatus.COMMITTED


def test_model_budget_is_serialized_per_revision_across_connections(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        selected_plan = plan(with_map=True)
        selected_plan["stages"][1]["resources"]["llm"] = 1
        draft = store.create_draft(
            "owner-a", "model-budget-race",
            redacted_request={"summary": "synthetic model budget race"},
        )
        store.admit_revision(
            "owner-a",
            draft.workload_id,
            selected_plan,
            inventory([source(0), source(1)]),
            expected_version=draft.version,
        )
        admitted = store.get_workload("owner-a", draft.workload_id)
        store.transition_workload(
            "owner-a", draft.workload_id, WorkloadState.QUEUED,
            expected_version=admitted.version,
            now=BASE_TIME,
        )
        first = store.claim_next(
            "model-worker-a", BASE_TIME, timedelta(seconds=30),
            _model_capabilities(),
        )
        assert first is not None

        with store.open_peer() as peer:
            assert peer.claim_next(
                "model-worker-b",
                BASE_TIME + timedelta(seconds=1),
                timedelta(seconds=30),
                _model_capabilities(),
            ) is None

            assert store.mark_running(
                first, now=BASE_TIME + timedelta(seconds=1),
            ) is LeaseMutationStatus.APPLIED
            assert store.commit_result(
                first,
                _result(first, "first-model-unit"),
                now=BASE_TIME + timedelta(seconds=2),
            ).status is CommitStatus.COMMITTED

            second = peer.claim_next(
                "model-worker-b",
                BASE_TIME + timedelta(seconds=3),
                timedelta(seconds=30),
                _model_capabilities(),
            )
            assert second is not None
            assert second.unit_id != first.unit_id


def test_model_stage_is_not_claimed_when_token_budget_has_no_remainder(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        selected_plan = plan(with_map=True)
        selected_plan["stages"][1]["resources"]["llm"] = 1
        selected_plan["budgets"]["max_tokens"] = 7
        draft = store.create_draft(
            "owner-a", "model-budget-empty",
            redacted_request={"summary": "synthetic exhausted model budget"},
        )
        revision = store.admit_revision(
            "owner-a",
            draft.workload_id,
            selected_plan,
            inventory([source(0)]),
            expected_version=draft.version,
        )
        admitted = store.get_workload("owner-a", draft.workload_id)
        store.transition_workload(
            "owner-a", draft.workload_id, WorkloadState.QUEUED,
            expected_version=admitted.version,
            now=BASE_TIME,
        )
        store._connection.execute(
            """
            UPDATE revision_usage SET input_tokens=4, output_tokens=3
            WHERE owner_user_id='owner-a' AND revision_id=?
            """,
            (revision.revision_id,),
        )

        assert store.claim_next(
            "model-worker-a", BASE_TIME, timedelta(seconds=30),
            _model_capabilities(),
        ) is None


def test_dummy_worker_records_output_contract_violation_with_message_key(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(store, "bad-output-schema")
        worker = DurableWorker(
            store,
            "worker-a",
            _capabilities(),
            lease_duration=timedelta(seconds=30),
            clock=lambda: BASE_TIME + timedelta(seconds=1),
        )
        outcome = worker.run_once(
            lambda _lease: ValidatedResult.from_payload(
                "metnos.synthetic-wrong/1", {"ok": True},
            )
        )
        assert outcome.status is WorkerRunStatus.FAILED
        assert outcome.failure.status is FailureStatus.FAILED_PERMANENT
        assert _unit_row(store, workload_id)["state"] == "failed_permanent"
        attempt = store._connection.execute(
            """
            SELECT structured_error_json FROM attempts
            WHERE owner_user_id='owner-a'
            """
        ).fetchone()
        error = json.loads(attempt["structured_error_json"])
        assert error["error_class"] == "contract_violation"
        assert error["message_key"] == "ERR_DURABLE_RESULT_CONTRACT_VIOLATION"
        assert "message" not in error


def test_worker_unclassified_exception_preserves_the_job_without_automatic_retry(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(store, "unknown-exception")
        worker = DurableWorker(store, "worker-unknown", _capabilities(),
                               lease_duration=timedelta(seconds=30),
                               clock=lambda: BASE_TIME + timedelta(seconds=1))

        def broken(_lease):
            raise RuntimeError("private provider response")

        outcome = worker.run_once(broken)
        assert outcome.failure.status is FailureStatus.NEEDS_ATTENTION
        assert store.get_workload("owner-a", workload_id).state is WorkloadState.NEEDS_ATTENTION
        assert _unit_row(store, workload_id)["state"] == "needs_attention"
        record = store._connection.execute("SELECT structured_error_json FROM attempts").fetchone()[0]
        assert "private provider response" not in record
        assert json.loads(record)["error_class"] == "executor_unknown"
        assert worker.run_once(broken).status is WorkerRunStatus.IDLE


def test_worker_records_a_missing_adapter_result_as_a_contract_violation(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(store, "missing-adapter-result")
        worker = DurableWorker(
            store,
            "worker-a",
            _capabilities(),
            lease_duration=timedelta(seconds=30),
            clock=lambda: BASE_TIME + timedelta(seconds=1),
        )

        outcome = worker.run_once(lambda _lease: None)  # type: ignore[arg-type]

        assert outcome.status is WorkerRunStatus.FAILED
        assert outcome.failure is not None
        assert outcome.failure.status is FailureStatus.FAILED_PERMANENT
        assert _unit_row(store, workload_id)["state"] == "failed_permanent"
        error = json.loads(store._connection.execute(
            "SELECT structured_error_json FROM attempts "
            "WHERE owner_user_id='owner-a'",
        ).fetchone()[0])
        assert error["error_class"] == "contract_violation"
        assert error["code"] == "result.invalid_adapter_type"


def test_dummy_fairness_credit_survives_reopen(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        first_workload = _prepare_many(store, "fair-a", 2)
        second_workload = _prepare_many(store, "fair-b", 2)

        first = store.claim_next(
            "worker-a", BASE_TIME, timedelta(seconds=30), _capabilities(),
        )
        assert first is not None
        store.mark_running(first, now=BASE_TIME + timedelta(microseconds=1))
        store.commit_result(
            first,
            _result(first, "fair-1"),
            now=BASE_TIME + timedelta(microseconds=2),
        )
        assert first.workload_id == first_workload

    observed = [first.workload_id]
    with DurableWorkloadStore.open(db_path) as store:
        for index in range(1, 4):
            now = BASE_TIME + timedelta(seconds=index)
            lease = store.claim_next(
                "worker-a", now, timedelta(seconds=30), _capabilities(),
            )
            assert lease is not None
            observed.append(lease.workload_id)
            store.mark_running(lease, now=now + timedelta(microseconds=1))
            store.commit_result(
                lease,
                _result(lease, f"fair-{index + 1}"),
                now=now + timedelta(microseconds=2),
            )
    assert observed == [
        first_workload,
        second_workload,
        first_workload,
        second_workload,
    ]


def test_two_real_processes_produce_one_commit_in_100_races(db_path):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    outcomes = context.Queue()
    parent_a, child_a = context.Pipe()
    parent_b, child_b = context.Pipe()
    processes = [
        context.Process(
            target=_race_worker,
            args=(str(db_path), "race-a", barrier, child_a, outcomes),
        ),
        context.Process(
            target=_race_worker,
            args=(str(db_path), "race-b", barrier, child_b, outcomes),
        ),
    ]
    for process in processes:
        process.start()
    try:
        with DurableWorkloadStore.open(db_path) as store:
            for round_number in range(100):
                origin = BASE_TIME + timedelta(minutes=round_number)
                _prepare_one(
                    store,
                    f"race-{round_number}",
                    queue_at=origin,
                )
                parent_a.send(round_number)
                parent_b.send(round_number)
                barrier.wait(timeout=10)
                try:
                    pair = [outcomes.get(timeout=10), outcomes.get(timeout=10)]
                except Empty as exc:
                    raise AssertionError("race worker did not report") from exc
                assert {item[0] for item in pair} == {round_number}
                committed = [
                    item for item in pair
                    if item[2] == "applied:committed"
                ]
                idle = [item for item in pair if item[2] == "none"]
                assert len(committed) == 1
                assert len(idle) == 1
                assert store._connection.execute(
                    "SELECT COUNT(*) FROM results WHERE owner_user_id='owner-a'"
                ).fetchone()[0] == round_number + 1
    finally:
        for channel in (parent_a, parent_b):
            try:
                channel.send(None)
            except (BrokenPipeError, EOFError):
                pass
        for process in processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        assert all(process.exitcode == 0 for process in processes)


def test_old_fence_never_changes_state_in_100_real_process_races(db_path):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    outcomes = context.Queue()
    parent_old, child_old = context.Pipe()
    parent_new, child_new = context.Pipe()
    processes = [
        context.Process(
            target=_fenced_commit_worker,
            args=(str(db_path), "old", barrier, child_old, outcomes),
        ),
        context.Process(
            target=_fenced_commit_worker,
            args=(str(db_path), "new", barrier, child_new, outcomes),
        ),
    ]
    for process in processes:
        process.start()
    try:
        with DurableWorkloadStore.open(db_path) as store:
            for round_number in range(100):
                origin = BASE_TIME + timedelta(minutes=round_number)
                _prepare_one(
                    store,
                    f"fence-race-{round_number}",
                    queue_at=origin,
                )
                old = store.claim_next(
                    "old-worker",
                    origin,
                    timedelta(seconds=1),
                    _capabilities(),
                )
                assert old is not None
                assert store.mark_running(
                    old, now=origin + timedelta(microseconds=1),
                ) is LeaseMutationStatus.APPLIED
                reconciled = store.reconcile_expired(
                    origin + timedelta(seconds=2), 10,
                )
                assert reconciled.expired == 1
                new = store.claim_next(
                    "new-worker",
                    origin + timedelta(seconds=3),
                    timedelta(seconds=30),
                    _capabilities(),
                )
                assert new is not None
                assert new.unit_id == old.unit_id
                assert new.fence == old.fence + 1
                assert store.mark_running(
                    new, now=origin + timedelta(seconds=3, microseconds=1),
                ) is LeaseMutationStatus.APPLIED

                commit_at = origin + timedelta(seconds=4)
                parent_old.send((round_number, old, "old", commit_at))
                parent_new.send((round_number, new, "new", commit_at))
                barrier.wait(timeout=10)
                try:
                    pair = [outcomes.get(timeout=10), outcomes.get(timeout=10)]
                except Empty as exc:
                    raise AssertionError("fenced commit worker did not report") from exc
                by_process = {item[1]: item for item in pair}
                assert by_process["old"][2] == CommitStatus.STALE_FENCE.value
                assert by_process["new"][2] == CommitStatus.COMMITTED.value
                expected = _result(new, "new")
                row = store._connection.execute(
                    """
                    SELECT u.state, u.fence, r.digest
                    FROM units u
                    JOIN results r
                      ON r.owner_user_id=u.owner_user_id
                     AND r.id=u.committed_result_id
                    WHERE u.owner_user_id='owner-a' AND u.id=?
                    """,
                    (new.unit_id,),
                ).fetchone()
                assert tuple(row) == ("committed", new.fence, expected.digest)
                assert store._connection.execute(
                    """
                    SELECT COUNT(*) FROM results
                    WHERE owner_user_id='owner-a' AND unit_id=?
                    """,
                    (new.unit_id,),
                ).fetchone()[0] == 1
    finally:
        for channel in (parent_old, parent_new):
            try:
                channel.send(None)
            except (BrokenPipeError, EOFError):
                pass
        for process in processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        assert all(process.exitcode == 0 for process in processes)


@pytest.mark.parametrize(
    "mode,expected_state",
    [
        ("before_execution", "pending"),
        ("after_result", "pending"),
        ("after_commit", "committed"),
    ],
)
@pytest.mark.skipif(
    not hasattr(signal, "SIGKILL"),
    reason="controlled durable-crash tests require SIGKILL",
)
def test_real_process_crash_at_controlled_boundaries(
    db_path, mode, expected_state,
):
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(store, f"crash-{mode}")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(
        target=_crash_worker,
        args=(str(db_path), mode, child, BASE_TIME),
    )
    process.start()
    try:
        assert parent.poll(10)
        assert parent.recv() == ("at_control_point", mode)
        assert process.pid is not None
        os.kill(process.pid, signal.SIGKILL)
        process.join(timeout=10)
        assert not process.is_alive()
        assert process.exitcode == -signal.SIGKILL
        with DurableWorkloadStore.open(db_path) as store:
            store.reconcile_expired(BASE_TIME + timedelta(seconds=11), 10)
            row = _unit_row(store, workload_id)
            assert row["state"] == expected_state
            if mode == "after_commit":
                assert store.claim_next(
                    "replacement",
                    BASE_TIME + timedelta(seconds=12),
                    timedelta(seconds=10),
                    _capabilities(),
                ) is None
                assert row["committed_result_id"] is not None
            else:
                replacement = store.claim_next(
                    "replacement",
                    BASE_TIME + timedelta(seconds=12),
                    timedelta(seconds=10),
                    _capabilities(),
                )
                assert replacement is not None
                assert replacement.fence == 2
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


def test_real_database_writer_lock_is_bounded_and_recovery_is_lossless(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        workload_id = _prepare_one(store, "database-busy")
        store._connection.execute("PRAGMA busy_timeout=100")
        blocker = sqlite3.connect(
            db_path,
            timeout=0.1,
            isolation_level=None,
        )
        try:
            blocker.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                store.claim_next(
                    "blocked-worker",
                    BASE_TIME,
                    timedelta(seconds=30),
                    _capabilities(),
                )
            elapsed = time.monotonic() - started
            assert 0.05 <= elapsed < 1.0
            row = _unit_row(store, workload_id)
            assert row["state"] == "pending"
            assert row["attempt_count"] == 0
            assert row["fence"] == 0
        finally:
            if blocker.in_transaction:
                blocker.rollback()
            blocker.close()

        lease = store.claim_next(
            "recovered-worker",
            BASE_TIME,
            timedelta(seconds=30),
            _capabilities(),
        )
        assert lease is not None
        assert lease.fence == 1
        assert store.mark_running(
            lease, now=BASE_TIME + timedelta(microseconds=1),
        ) is LeaseMutationStatus.APPLIED
        assert store.commit_result(
            lease,
            _result(lease, "after-database-lock"),
            now=BASE_TIME + timedelta(microseconds=2),
        ).status is CommitStatus.COMMITTED


def test_old_real_worker_cannot_heartbeat_or_commit_after_new_fence(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(store, "old-worker")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_late_worker, args=(str(db_path), child))
    process.start()
    try:
        assert parent.poll(10)
        assert parent.recv() == ("result_ready", 1)
        with DurableWorkloadStore.open(db_path) as store:
            reconciled = store.reconcile_expired(
                BASE_TIME + timedelta(seconds=11), 10,
            )
            assert reconciled.expired == 1
            replacement = store.claim_next(
                "new-worker",
                BASE_TIME + timedelta(seconds=12),
                timedelta(seconds=30),
                _capabilities(),
            )
            assert replacement is not None
            assert replacement.fence == 2
            store.mark_running(
                replacement, now=BASE_TIME + timedelta(seconds=13),
            )
            winner = _result(replacement, "new")
            assert store.commit_result(
                replacement,
                winner,
                now=BASE_TIME + timedelta(seconds=14),
            ).status is CommitStatus.COMMITTED
        parent.send("finish-late")
        assert parent.poll(10)
        heartbeat, commit = parent.recv()
        assert heartbeat == LeaseMutationStatus.STALE_FENCE.value
        assert commit == CommitStatus.STALE_FENCE.value
        process.join(timeout=10)
        assert process.exitcode == 0
        with DurableWorkloadStore.open(db_path) as store:
            result = store._connection.execute(
                "SELECT digest FROM results WHERE owner_user_id='owner-a'"
            ).fetchone()
            assert result["digest"] == winner.digest
            assert store._connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE owner_user_id='owner-a'"
            ).fetchone()[0] == 2
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


def test_retry_schedule_survives_two_real_process_starts(db_path):
    with DurableWorkloadStore.open(db_path) as store:
        _prepare_one(
            store,
            "stable-retry",
            base_delay_ms=1000,
            max_delay_ms=10_000,
        )
        lease = store.claim_next(
            "worker-a", BASE_TIME, timedelta(seconds=10), _capabilities(),
        )
        assert lease is not None
        store.mark_running(lease, now=BASE_TIME + timedelta(seconds=1))

    context = multiprocessing.get_context("spawn")
    observations = []
    for offset in (timedelta(microseconds=1), timedelta(microseconds=2)):
        parent, child = context.Pipe()
        process = context.Process(
            target=_reconcile_worker,
            args=(str(db_path), BASE_TIME + timedelta(seconds=10) + offset, child),
        )
        process.start()
        assert parent.poll(10)
        observations.append(parent.recv())
        process.join(timeout=10)
        assert process.exitcode == 0

    first_outcome, first_row = observations[0]
    second_outcome, second_row = observations[1]
    assert first_outcome.expired == 1
    assert second_outcome.expired == 0
    assert first_row[0] == second_row[0] == "retry_wait"
    assert first_row[1] == second_row[1]
    assert first_row[2] == second_row[2] == 1

    with DurableWorkloadStore.open(db_path) as store:
        due = parse_instant(first_row[1])
        promoted = store.reconcile_expired(due + timedelta(microseconds=1), 10)
        assert promoted.retry_promoted == 1
        replacement = store.claim_next(
            "worker-b",
            due + timedelta(microseconds=2),
            timedelta(seconds=10),
            _capabilities(),
        )
        assert replacement is not None
        assert replacement.fence == 2
