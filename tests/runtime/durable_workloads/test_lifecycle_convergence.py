from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from durable_workloads.coordinator import (
    CommitStatus,
    FailureStatus,
    LeaseMutationStatus,
    RetryDecision,
    RetryPolicy,
    StructuredAttemptError,
    ValidatedResult,
    WorkerCapabilities,
    decide_retry,
)
from durable_workloads.models import (
    DurableEffect,
    EventType,
    RunnerKind,
    WorkloadState,
)
from durable_workloads.storage import DurableWorkloadStore, InvalidTransitionError
from helpers import inventory, map_stage, plan, source


BASE_TIME = datetime(2026, 8, 20, 14, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "error_class",
    (
        "budget_exhausted",
        "capability_unavailable",
        "publication_ambiguous",
        "source_missing",
        "executor_unknown",
    ),
)
def test_errors_requiring_changed_facts_never_retry_automatically(error_class):
    decision = decide_retry(
        effect_profile=DurableEffect.PURE,
        retry_policy=RetryPolicy(3, 0, 0, (error_class,)),
        attempt_number=1,
        error_class=error_class,
    )
    assert decision is RetryDecision.NEEDS_ATTENTION


@pytest.mark.parametrize("effect", [DurableEffect.PURE, DurableEffect.IDEMPOTENT])
@pytest.mark.parametrize("attempt,manual,expected", [
    (1, False, RetryDecision.RETRY), (2, False, RetryDecision.RETRY),
    (3, False, RetryDecision.NEEDS_ATTENTION), (4, False, RetryDecision.NEEDS_ATTENTION),
    (1, True, RetryDecision.NEEDS_ATTENTION), (4, True, RetryDecision.NEEDS_ATTENTION),
])
def test_transient_retry_limit_requires_attention_not_permanent_failure(effect, attempt, manual, expected):
    assert decide_retry(
        effect_profile=effect, retry_policy=RetryPolicy(3, 0, 0, ("executor_transient",)),
        attempt_number=attempt, error_class="executor_transient", manual_retry=manual,
    ) is expected


@pytest.mark.parametrize("error_class", ["executor_permanent", "contract_violation", "invalid_plan"])
def test_declaring_a_permanent_error_retryable_does_not_change_its_classification(error_class):
    assert decide_retry(
        effect_profile=DurableEffect.IDEMPOTENT,
        retry_policy=RetryPolicy(3, 0, 0, (error_class,)), attempt_number=1,
        error_class=error_class,
    ) is RetryDecision.FAIL_PERMANENT


@pytest.fixture
def store(tmp_path):
    repository = DurableWorkloadStore.open(
        tmp_path / "durable" / "state.sqlite3"
    )
    try:
        yield repository
    finally:
        repository.close()


def _capabilities(
    effect: DurableEffect = DurableEffect.PURE,
) -> WorkerCapabilities:
    return WorkerCapabilities.create(
        ((RunnerKind.EXECUTOR, "read_files_ocr"),),
        {
            key: 0
            for key in (
                "cpu", "device", "llm", "local_io", "network_io", "vlm",
            )
        },
        effect_profiles=(effect,),
    )


def _prepare(
    store: DurableWorkloadStore,
    suffix: str,
    *,
    count: int = 1,
    effect: DurableEffect = DurableEffect.PURE,
    max_attempts: int = 1,
    owner: str = "owner-a",
) -> str:
    selected_plan = plan(with_map=True)
    selected_plan["inventory"]["max_sources"] = max(100, count)
    selected_plan["stages"][1]["cardinality"]["max_units"] = max(100, count)
    selected_plan["budgets"]["max_units"] = max(1000, count + 1)
    selected_plan["stages"][1]["effect_profile"] = effect.value
    selected_plan["stages"][1]["retry"] = {
        "max_attempts": max_attempts,
        "base_delay_ms": 0,
        "max_delay_ms": 0,
        "retryable_error_classes": ["executor_transient"],
    }
    draft = store.create_draft(
        owner,
        f"lifecycle-{suffix}",
        redacted_request={"summary": "synthetic lifecycle fixture"},
    )
    store.admit_revision(
        owner,
        draft.workload_id,
        selected_plan,
        inventory([source(index) for index in range(count)]),
        expected_version=draft.version,
    )
    admitted = store.get_workload(owner, draft.workload_id)
    store.transition_workload(
        owner,
        draft.workload_id,
        WorkloadState.QUEUED,
        expected_version=admitted.version,
        now=BASE_TIME,
    )
    return draft.workload_id


def _prepare_unavailable_dependency(
    store: DurableWorkloadStore,
    *,
    partial_output_accepted: bool,
) -> str:
    selected_plan = plan(
        with_map=True,
        error_mode="declared",
        allowed_error_classes=("executor_permanent",),
    )
    downstream = map_stage()
    downstream.update({
        "key": "answer",
        "depends_on": ["map"],
        "cardinality": {"mode": "per_dependency", "max_units": 10},
        "input_bindings": {
            "paths": {"ref": "dependency.result", "stage": "map"},
        },
    })
    selected_plan["stages"].append(downstream)
    draft = store.create_draft(
        "owner-a",
        "unavailable-dependency-" + str(int(partial_output_accepted)),
        redacted_request={"summary": "synthetic dependency fixture"},
    )
    store.admit_revision(
        "owner-a",
        draft.workload_id,
        selected_plan,
        inventory([source(0)]),
        expected_version=draft.version,
        partial_output_accepted=partial_output_accepted,
    )
    admitted = store.get_workload("owner-a", draft.workload_id)
    store.transition_workload(
        "owner-a",
        draft.workload_id,
        WorkloadState.QUEUED,
        expected_version=admitted.version,
        now=BASE_TIME,
    )
    store._connection.execute(
        """
        UPDATE units
        SET state='failed_permanent', error_class='executor_permanent'
        WHERE owner_user_id='owner-a' AND revision_id=(
          SELECT active_revision_id FROM workloads
          WHERE owner_user_id='owner-a' AND id=?
        ) AND stage_id=(
          SELECT stage.id FROM stages stage
          JOIN workloads workload
            ON workload.owner_user_id=stage.owner_user_id
           AND workload.active_revision_id=stage.revision_id
          WHERE workload.owner_user_id='owner-a' AND workload.id=?
            AND stage.stage_key='map'
        )
        """,
        (draft.workload_id, draft.workload_id),
    )
    return draft.workload_id


def _result(lease, marker: str) -> ValidatedResult:
    return ValidatedResult.from_payload(
        lease.output_schema_version,
        {"ok": True, "marker": marker},
    )


def _error(
    *,
    error_class: str = "executor_transient",
    offset: int = 0,
) -> StructuredAttemptError:
    return StructuredAttemptError.create(
        error_class,
        code="executor.lifecycle_fixture",
        message_key="ERR_DURABLE_EXECUTOR_LIFECYCLE_FIXTURE",
        retry="automatic",
        occurred_at=BASE_TIME + timedelta(seconds=offset),
    )


def _unit_rows(store: DurableWorkloadStore, workload_id: str):
    return store._connection.execute(
        """
        SELECT unit.*
        FROM units unit
        JOIN revisions revision
          ON revision.owner_user_id=unit.owner_user_id
         AND revision.id=unit.revision_id
        WHERE revision.owner_user_id='owner-a' AND revision.workload_id=?
        ORDER BY unit.unit_key
        """,
        (workload_id,),
    ).fetchall()


def _event_count(
    store: DurableWorkloadStore,
    workload_id: str,
    event_type: EventType,
) -> int:
    return sum(
        event.event_type is event_type
        for event in store.list_events("owner-a", workload_id)
    )


def _claim_and_run(
    store: DurableWorkloadStore,
    worker_id: str,
    *,
    effect: DurableEffect = DurableEffect.PURE,
    offset: int = 0,
):
    lease = store.claim_next(
        worker_id,
        BASE_TIME + timedelta(seconds=offset),
        timedelta(seconds=30),
        _capabilities(effect),
    )
    assert lease is not None
    assert store.mark_running(
        lease,
        now=BASE_TIME + timedelta(seconds=offset, microseconds=1),
    ) is LeaseMutationStatus.APPLIED
    return lease


@pytest.mark.parametrize("effect", [DurableEffect.PURE, DurableEffect.IDEMPOTENT])
def test_exhausted_transient_preserves_results_and_queue_until_one_manual_grant(store, effect):
    workload_id = _prepare(store, "recoverable-exhaustion", count=3, effect=effect, max_attempts=3)
    saved = _claim_and_run(store, "saved", effect=effect)
    receipt = _result(saved, "saved-before-error")
    assert store.commit_result(saved, receipt, now=BASE_TIME + timedelta(seconds=1)).status is CommitStatus.COMMITTED
    last = None
    for number in range(1, 4):
        # Keep another pending batch untouched even if normal scheduling could
        # otherwise choose it: the failing batch is oldest and retry is due.
        lease = _claim_and_run(store, f"failed-{number}", effect=effect, offset=number * 2)
        if last is not None:
            assert lease.unit_id == last.unit_id
        decision = decide_retry(effect_profile=lease.effect_profile, retry_policy=lease.retry_policy,
                                attempt_number=lease.attempt_number, error_class="executor_transient")
        status = store.fail_attempt(lease, _error(offset=number * 2), decision,
                                   now=BASE_TIME + timedelta(seconds=number * 2, microseconds=2)).status
        assert status is (FailureStatus.RETRY_SCHEDULED if number < 3 else FailureStatus.NEEDS_ATTENTION)
        store.reconcile_expired(BASE_TIME + timedelta(seconds=number * 2 + 1), 10)
        last = lease
    attention = store.get_workload("owner-a", workload_id)
    assert attention.state is WorkloadState.NEEDS_ATTENTION
    assert sorted(row["state"] for row in _unit_rows(store, workload_id)) == ["committed", "needs_attention", "pending"]
    for offset in (8, 9, 10):
        assert store.claim_next("no-loop", BASE_TIME + timedelta(seconds=offset), timedelta(seconds=30),
                                _capabilities(effect)) is None
    error = json.loads(store._connection.execute(
        "SELECT structured_error_json FROM attempts WHERE id=?", (last.attempt_id,),
    ).fetchone()[0])
    assert error["retry"] == "manual"
    assert error["error_class"] == "executor_transient"
    assert store.commit_result(saved, receipt, now=BASE_TIME + timedelta(seconds=10)).status is CommitStatus.IDEMPOTENT_REPLAY
    resumed = store.record_attention_resolution(
        "owner-a", workload_id, decision="retry", expected_version=attention.version,
        idempotency_key="one-approved-retry", now=BASE_TIME + timedelta(seconds=11),
    )
    assert resumed.state is WorkloadState.QUEUED
    retry = _claim_and_run(store, "owner-approved", effect=effect, offset=12)
    assert retry.unit_id == last.unit_id and retry.attempt_number == 4 and retry.manual_retry
    # An owner's retry is not a fresh automatic retry budget.
    decision = decide_retry(effect_profile=retry.effect_profile, retry_policy=retry.retry_policy,
                            attempt_number=retry.attempt_number, error_class="executor_transient",
                            manual_retry=retry.manual_retry)
    assert decision is RetryDecision.NEEDS_ATTENTION
    store.fail_attempt(retry, _error(offset=13), decision, now=BASE_TIME + timedelta(seconds=13))
    assert store.get_workload("owner-a", workload_id).state is WorkloadState.NEEDS_ATTENTION
    assert sorted(row["state"] for row in _unit_rows(store, workload_id)) == ["committed", "needs_attention", "pending"]


def test_quiescent_workload_does_not_retain_scheduler_credit(store):
    workload_id = _prepare(store, "scheduler-credit-cleanup")
    lease = store.claim_next(
        "worker-credit",
        BASE_TIME,
        timedelta(seconds=30),
        _capabilities(),
    )
    assert lease is not None
    assert store._connection.execute(
        "SELECT COUNT(*) FROM scheduler_credits WHERE workload_id=?",
        (workload_id,),
    ).fetchone()[0] == 1

    running = store.get_workload("owner-a", workload_id)
    paused = store.request_pause(
        "owner-a",
        workload_id,
        expected_version=running.version,
        idempotency_key="pause-credit-cleanup",
        now=BASE_TIME,
    )
    assert paused.state is WorkloadState.PAUSE_REQUESTED
    assert store._connection.execute(
        "SELECT COUNT(*) FROM scheduler_credits WHERE workload_id=?",
        (workload_id,),
    ).fetchone()[0] == 0


def test_unavailable_dependency_converges_to_an_explicit_accepted_skip(store):
    workload_id = _prepare_unavailable_dependency(
        store, partial_output_accepted=True,
    )
    assert store.materialize_ready_units(
        "owner-a", workload_id, limit=10,
    ) == 1
    row = store._connection.execute(
        """
        SELECT unit.state, unit.error_class, progress.completed
        FROM units unit
        JOIN stages stage
          ON stage.owner_user_id=unit.owner_user_id
         AND stage.revision_id=unit.revision_id
         AND stage.id=unit.stage_id
        JOIN stage_materialization progress
          ON progress.owner_user_id=stage.owner_user_id
         AND progress.revision_id=stage.revision_id
         AND progress.stage_id=stage.id
        WHERE unit.owner_user_id='owner-a' AND stage.stage_key='answer'
        """
    ).fetchone()
    assert (row["state"], row["error_class"], row["completed"]) == (
        "skipped", "dependency_result_unavailable", 1,
    )
    assert store.materialize_ready_units(
        "owner-a", workload_id, limit=10,
    ) == 0


def test_unaccepted_dependency_gap_stops_for_attention_without_retry_loop(store):
    workload_id = _prepare_unavailable_dependency(
        store, partial_output_accepted=False,
    )
    assert store.materialize_ready_units(
        "owner-a", workload_id, limit=10,
    ) == 1
    attention = store.settle_workload("owner-a", workload_id)
    assert attention.state is WorkloadState.NEEDS_ATTENTION
    with pytest.raises(
        InvalidTransitionError,
        match="requires a new revision or cancellation",
    ):
        store.record_attention_resolution(
            "owner-a",
            workload_id,
            decision="retry",
            expected_version=attention.version,
            idempotency_key="retry-unavailable-dependency",
        )


def _move_reconcilable_unit_to_attention(
    store: DurableWorkloadStore,
    workload_id: str,
):
    lease = _claim_and_run(
        store,
        "worker-attention",
        effect=DurableEffect.RECONCILABLE,
    )
    decision = decide_retry(
        effect_profile=lease.effect_profile,
        retry_policy=lease.retry_policy,
        attempt_number=lease.attempt_number,
        error_class="executor_transient",
    )
    assert decision is RetryDecision.NEEDS_ATTENTION
    failed = store.fail_attempt(
        lease,
        _error(offset=1),
        decision,
        now=BASE_TIME + timedelta(seconds=1),
    )
    assert failed.status is FailureStatus.NEEDS_ATTENTION
    attention = store.get_workload("owner-a", workload_id)
    assert attention.state is WorkloadState.NEEDS_ATTENTION
    return attention, lease


def test_pause_settles_when_the_last_active_attempt_finishes(store):
    workload_id = _prepare(store, "pause", count=2)
    lease = _claim_and_run(store, "worker-pause")
    running = store.get_workload("owner-a", workload_id)

    requested = store.request_pause(
        "owner-a",
        workload_id,
        expected_version=running.version,
        idempotency_key="pause-running",
        now=BASE_TIME + timedelta(seconds=1),
    )
    assert requested.state is WorkloadState.PAUSE_REQUESTED
    assert store.claim_next(
        "worker-blocked",
        BASE_TIME + timedelta(seconds=1),
        timedelta(seconds=30),
        _capabilities(),
    ) is None

    committed = store.commit_result(
        lease,
        _result(lease, "pause-drain"),
        now=BASE_TIME + timedelta(seconds=2),
    )
    assert committed.status is CommitStatus.COMMITTED
    paused = store.get_workload("owner-a", workload_id)
    assert paused.state is WorkloadState.PAUSED
    assert sorted(row["state"] for row in _unit_rows(store, workload_id)) == [
        "committed",
        "pending",
    ]

    resumed = store.request_resume(
        "owner-a",
        workload_id,
        expected_version=paused.version,
        idempotency_key="resume-after-drain",
        now=BASE_TIME + timedelta(seconds=2),
    )
    assert resumed.state is WorkloadState.QUEUED
    assert store.claim_next(
        "worker-resumed",
        BASE_TIME + timedelta(seconds=3),
        timedelta(seconds=30),
        _capabilities(),
    ) is not None


def test_cancel_drains_active_attempt_and_cancels_waiting_units_once(store):
    workload_id = _prepare(store, "cancel", count=2)
    lease = _claim_and_run(store, "worker-cancel")
    running = store.get_workload("owner-a", workload_id)

    requested = store.request_cancel(
        "owner-a",
        workload_id,
        expected_version=running.version,
        idempotency_key="cancel-running",
    )
    assert requested.state is WorkloadState.CANCEL_REQUESTED
    assert store.claim_next(
        "worker-blocked",
        BASE_TIME + timedelta(seconds=1),
        timedelta(seconds=30),
        _capabilities(),
    ) is None

    assert store.commit_result(
        lease,
        _result(lease, "cancel-drain"),
        now=BASE_TIME + timedelta(seconds=2),
    ).status is CommitStatus.COMMITTED
    cancelled = store.get_workload("owner-a", workload_id)
    assert cancelled.state is WorkloadState.CANCELLED
    assert sorted(row["state"] for row in _unit_rows(store, workload_id)) == [
        "cancelled",
        "committed",
    ]
    assert _event_count(store, workload_id, EventType.CANCEL_REQUESTED) == 1
    assert _event_count(store, workload_id, EventType.CANCELLED) == 1

    replay = store.request_cancel(
        "owner-a",
        workload_id,
        expected_version=running.version,
        idempotency_key="cancel-running",
    )
    assert replay == requested
    assert _event_count(store, workload_id, EventType.CANCELLED) == 1


def test_cancel_without_an_active_attempt_is_immediate_and_complete(store):
    workload_id = _prepare(store, "cancel-queued", count=2)
    queued = store.get_workload("owner-a", workload_id)

    cancelled = store.request_cancel(
        "owner-a",
        workload_id,
        expected_version=queued.version,
        idempotency_key="cancel-queued",
    )
    assert cancelled.state is WorkloadState.CANCELLED
    assert {row["state"] for row in _unit_rows(store, workload_id)} == {
        "cancelled"
    }


def test_large_cancellation_converges_in_strict_persistent_batches(store):
    workload_id = _prepare(store, "cancel-bounded", count=300)
    queued = store.get_workload("owner-a", workload_id)

    requested = store.request_cancel(
        "owner-a",
        workload_id,
        expected_version=queued.version,
        idempotency_key="cancel-bounded",
    )
    assert requested.state is WorkloadState.CANCEL_REQUESTED
    counters = {
        state: sum(row["state"] == state for row in _unit_rows(store, workload_id))
        for state in ("cancelled", "pending")
    }
    assert counters == {"cancelled": 256, "pending": 44}
    assert store.claim_next(
        "worker-cancel-bounded",
        BASE_TIME,
        timedelta(seconds=30),
        _capabilities(),
    ) is None

    assert store.settle_workloads(limit=10) == 10
    counters = {
        state: sum(row["state"] == state for row in _unit_rows(store, workload_id))
        for state in ("cancelled", "pending")
    }
    assert counters == {"cancelled": 266, "pending": 34}
    assert store.settle_workloads(limit=100) == 35
    assert store.get_workload(
        "owner-a", workload_id,
    ).state is WorkloadState.CANCELLED


def test_cancel_from_attention_drains_parallel_work_before_terminating(store):
    workload_id = _prepare(
        store,
        "cancel-attention",
        count=2,
        effect=DurableEffect.RECONCILABLE,
    )
    first = _claim_and_run(
        store,
        "worker-cancel-attention-a",
        effect=DurableEffect.RECONCILABLE,
    )
    second = _claim_and_run(
        store,
        "worker-cancel-attention-b",
        effect=DurableEffect.RECONCILABLE,
        offset=1,
    )
    decision = decide_retry(
        effect_profile=first.effect_profile,
        retry_policy=first.retry_policy,
        attempt_number=first.attempt_number,
        error_class="executor_transient",
    )
    store.fail_attempt(
        first,
        _error(offset=2),
        decision,
        now=BASE_TIME + timedelta(seconds=2),
    )
    attention = store.get_workload("owner-a", workload_id)
    assert attention.state is WorkloadState.NEEDS_ATTENTION

    requested = store.request_cancel(
        "owner-a",
        workload_id,
        expected_version=attention.version,
        idempotency_key="cancel-from-attention",
    )
    assert requested.state is WorkloadState.CANCEL_REQUESTED
    assert store.commit_result(
        second,
        _result(second, "attention-cancel-drained"),
        now=BASE_TIME + timedelta(seconds=3),
    ).status is CommitStatus.COMMITTED

    cancelled = store.get_workload("owner-a", workload_id)
    assert cancelled.state is WorkloadState.CANCELLED
    reason = json.loads(cancelled.terminal_reason_json)
    assert reason["unresolved_unit_attention"] == 1
    assert sorted(row["state"] for row in _unit_rows(store, workload_id)) == [
        "cancelled",
        "committed",
    ]


def test_attention_retry_with_parallel_work_resumes_as_running(store):
    workload_id = _prepare(
        store,
        "retry-attention-parallel",
        count=2,
        effect=DurableEffect.RECONCILABLE,
    )
    first = _claim_and_run(
        store,
        "worker-retry-attention-a",
        effect=DurableEffect.RECONCILABLE,
    )
    _claim_and_run(
        store,
        "worker-retry-attention-b",
        effect=DurableEffect.RECONCILABLE,
        offset=1,
    )
    decision = decide_retry(
        effect_profile=first.effect_profile,
        retry_policy=first.retry_policy,
        attempt_number=first.attempt_number,
        error_class="executor_transient",
    )
    store.fail_attempt(
        first,
        _error(offset=2),
        decision,
        now=BASE_TIME + timedelta(seconds=2),
    )
    attention = store.get_workload("owner-a", workload_id)

    resumed = store.record_attention_resolution(
        "owner-a",
        workload_id,
        decision="retry",
        expected_version=attention.version,
        idempotency_key="retry-with-active-unit",
        now=BASE_TIME + timedelta(seconds=2),
    )
    assert resumed.state is WorkloadState.RUNNING


def test_claim_enforces_the_frozen_workload_concurrency_budget(store):
    workload_id = _prepare(store, "max-concurrency", count=3)
    first = _claim_and_run(store, "worker-concurrency-a")
    _claim_and_run(store, "worker-concurrency-b", offset=1)

    assert store.claim_next(
        "worker-concurrency-blocked",
        BASE_TIME + timedelta(seconds=2),
        timedelta(seconds=30),
        _capabilities(),
    ) is None
    assert store.commit_result(
        first,
        _result(first, "free-concurrency-slot"),
        now=BASE_TIME + timedelta(seconds=2),
    ).status is CommitStatus.COMMITTED
    replacement = store.claim_next(
        "worker-concurrency-replacement",
        BASE_TIME + timedelta(seconds=3),
        timedelta(seconds=30),
        _capabilities(),
    )
    assert replacement is not None
    assert replacement.workload_id == workload_id


def test_persistent_claim_credit_round_robins_owners_before_workloads(store):
    _prepare(store, "fair-owner-a-one", count=2, owner="owner-a")
    _prepare(store, "fair-owner-a-two", count=2, owner="owner-a")
    _prepare(store, "fair-owner-b-one", count=2, owner="owner-b")

    observed: list[str] = []
    for offset in range(4):
        lease = store.claim_next(
            f"worker-owner-fair-{offset}",
            BASE_TIME + timedelta(seconds=offset * 2),
            timedelta(seconds=30),
            _capabilities(),
        )
        assert lease is not None
        observed.append(lease.owner_user_id)
        assert store.mark_running(
            lease,
            now=BASE_TIME + timedelta(seconds=offset * 2 + 1),
        ) is LeaseMutationStatus.APPLIED
        assert store.commit_result(
            lease,
            _result(lease, f"owner-fair-{offset}"),
            now=BASE_TIME + timedelta(seconds=offset * 2 + 1, microseconds=1),
        ).status is CommitStatus.COMMITTED

    assert observed == ["owner-a", "owner-b", "owner-a", "owner-b"]


def test_untolerated_failure_waits_for_parallel_attempts_then_fails(store):
    workload_id = _prepare(store, "parallel-failure", count=3)
    first = _claim_and_run(store, "worker-failure-a")
    second = _claim_and_run(store, "worker-failure-b", offset=1)

    error = _error(error_class="executor_permanent", offset=2)
    decision = decide_retry(
        effect_profile=first.effect_profile,
        retry_policy=first.retry_policy,
        attempt_number=first.attempt_number,
        error_class=error.error_class,
    )
    assert decision is RetryDecision.FAIL_PERMANENT
    assert store.fail_attempt(
        first,
        error,
        decision,
        now=BASE_TIME + timedelta(seconds=2),
    ).status is FailureStatus.FAILED_PERMANENT

    assert store.get_workload(
        "owner-a", workload_id,
    ).state is WorkloadState.RUNNING
    assert sorted(row["state"] for row in _unit_rows(store, workload_id)) == [
        "cancelled",
        "failed_permanent",
        "running",
    ]
    assert store.claim_next(
        "worker-failure-c",
        BASE_TIME + timedelta(seconds=3),
        timedelta(seconds=30),
        _capabilities(),
    ) is None

    result = _result(second, "parallel-drained")
    assert store.commit_result(
        second,
        result,
        now=BASE_TIME + timedelta(seconds=3),
    ).status is CommitStatus.COMMITTED
    assert store.get_workload(
        "owner-a", workload_id,
    ).state is WorkloadState.FAILED
    assert _event_count(store, workload_id, EventType.FAILED) == 1
    assert store.commit_result(
        second,
        result,
        now=BASE_TIME + timedelta(seconds=4),
    ).status is CommitStatus.IDEMPOTENT_REPLAY
    assert _event_count(store, workload_id, EventType.FAILED) == 1


def test_attention_propagates_and_each_manual_retry_is_one_shot(store):
    workload_id = _prepare(
        store,
        "manual-retry",
        effect=DurableEffect.RECONCILABLE,
    )
    attention, first = _move_reconcilable_unit_to_attention(store, workload_id)
    assert _event_count(store, workload_id, EventType.NEEDS_ATTENTION) == 1
    assert store.settle_workload(
        "owner-a", workload_id, now=BASE_TIME + timedelta(seconds=1),
    ) == attention
    assert _event_count(store, workload_id, EventType.NEEDS_ATTENTION) == 1

    granted = store.record_attention_resolution(
        "owner-a",
        workload_id,
        decision="retry",
        expected_version=attention.version,
        idempotency_key="manual-retry-one",
        now=BASE_TIME + timedelta(seconds=1),
    )
    assert granted.state is WorkloadState.QUEUED
    unit = _unit_rows(store, workload_id)[0]
    assert unit["manual_retry_tokens"] == 0
    assert unit["manual_retry_generation"] == 0
    assert store._connection.execute(
        """
        SELECT revision.manual_retry_generation
        FROM revisions revision
        JOIN workloads workload
          ON workload.owner_user_id=revision.owner_user_id
         AND workload.active_revision_id=revision.id
        WHERE workload.owner_user_id='owner-a' AND workload.id=?
        """,
        (workload_id,),
    ).fetchone()[0] == 1
    assert store.record_attention_resolution(
        "owner-a",
        workload_id,
        decision="retry",
        expected_version=attention.version,
        idempotency_key="manual-retry-one",
        now=BASE_TIME + timedelta(seconds=1),
    ) == granted
    assert _unit_rows(store, workload_id)[0]["manual_retry_tokens"] == 0

    second = _claim_and_run(
        store,
        "worker-manual-two",
        effect=DurableEffect.RECONCILABLE,
        offset=2,
    )
    assert second.manual_retry is True
    assert second.attempt_number == first.attempt_number + 1
    assert second.fence == first.fence + 1
    assert _unit_rows(store, workload_id)[0]["manual_retry_tokens"] == 0
    assert _unit_rows(store, workload_id)[0]["manual_retry_generation"] == 1
    decision = decide_retry(
        effect_profile=second.effect_profile,
        retry_policy=second.retry_policy,
        attempt_number=second.attempt_number,
        error_class="executor_transient",
        manual_retry=second.manual_retry,
    )
    assert decision is RetryDecision.NEEDS_ATTENTION
    assert store.fail_attempt(
        second,
        _error(offset=3),
        decision,
        now=BASE_TIME + timedelta(seconds=3),
    ).status is FailureStatus.NEEDS_ATTENTION
    second_attention = store.get_workload("owner-a", workload_id)
    assert second_attention.state is WorkloadState.NEEDS_ATTENTION
    assert store.claim_next(
        "worker-no-implicit-third",
        BASE_TIME + timedelta(seconds=4),
        timedelta(seconds=30),
        _capabilities(DurableEffect.RECONCILABLE),
    ) is None

    store.record_attention_resolution(
        "owner-a",
        workload_id,
        decision="retry",
        expected_version=second_attention.version,
        idempotency_key="manual-retry-two",
        now=BASE_TIME + timedelta(seconds=3),
    )
    third = _claim_and_run(
        store,
        "worker-manual-three",
        effect=DurableEffect.RECONCILABLE,
        offset=5,
    )
    assert third.manual_retry is True
    assert third.attempt_number == second.attempt_number + 1
    assert store.commit_result(
        third,
        _result(third, "manual-success"),
        now=BASE_TIME + timedelta(seconds=6),
    ).status is CommitStatus.COMMITTED


def test_attention_retry_authority_is_constant_size_for_large_workloads(store):
    workload_id = _prepare(store, "manual-retry-bounded", count=300)
    store._connection.execute(
        """
        UPDATE units
        SET state='needs_attention', error_class='executor_transient'
        WHERE owner_user_id='owner-a' AND revision_id=(
          SELECT active_revision_id FROM workloads
          WHERE owner_user_id='owner-a' AND id=?
        )
        """,
        (workload_id,),
    )
    queued = store.get_workload("owner-a", workload_id)
    attention = store.transition_workload(
        "owner-a",
        workload_id,
        WorkloadState.NEEDS_ATTENTION,
        expected_version=queued.version,
        now=BASE_TIME,
    )

    resumed = store.record_attention_resolution(
        "owner-a",
        workload_id,
        decision="retry",
        expected_version=attention.version,
        idempotency_key="manual-retry-bounded",
        now=BASE_TIME,
    )
    assert resumed.state is WorkloadState.QUEUED
    assert sum(
        row["state"] == "needs_attention"
        for row in _unit_rows(store, workload_id)
    ) == 300

    lease = store.claim_next(
        "worker-manual-bounded",
        BASE_TIME,
        timedelta(seconds=30),
        _capabilities(),
    )
    assert lease is not None and lease.manual_retry
    assert sum(
        row["state"] == "leased" for row in _unit_rows(store, workload_id)
    ) == 1
    assert store.settle_workload(
        "owner-a", workload_id, now=BASE_TIME,
    ).state is WorkloadState.RUNNING


def test_manual_grant_is_returned_only_before_execution(store):
    workload_id = _prepare(
        store,
        "manual-expiry",
        effect=DurableEffect.RECONCILABLE,
    )
    attention, first = _move_reconcilable_unit_to_attention(store, workload_id)
    store.record_attention_resolution(
        "owner-a",
        workload_id,
        decision="retry",
        expected_version=attention.version,
        idempotency_key="grant-before-expiry",
        now=BASE_TIME + timedelta(seconds=1),
    )
    manual = store.claim_next(
        "worker-manual-expiry",
        BASE_TIME + timedelta(seconds=2),
        timedelta(seconds=10),
        _capabilities(DurableEffect.RECONCILABLE),
    )
    assert manual is not None and manual.manual_retry

    outcome = store.reconcile_expired(
        BASE_TIME + timedelta(seconds=13),
        10,
    )
    assert outcome.returned_pending == 1
    row = _unit_rows(store, workload_id)[0]
    assert row["state"] == "pending"
    assert row["manual_retry_tokens"] == 1
    replacement = store.claim_next(
        "worker-manual-replacement",
        BASE_TIME + timedelta(seconds=14),
        timedelta(seconds=30),
        _capabilities(DurableEffect.RECONCILABLE),
    )
    assert replacement is not None and replacement.manual_retry
    assert replacement.attempt_number == manual.attempt_number + 1
    assert replacement.fence == manual.fence + 1
    assert replacement.fence > first.fence
    assert store.mark_running(
        manual,
        now=BASE_TIME + timedelta(seconds=14),
    ) is LeaseMutationStatus.STALE_FENCE


def test_started_manual_grant_returns_to_attention_on_abandonment(store):
    workload_id = _prepare(store, "manual-abandon")
    first = store.claim_next(
        "worker-initial-expiry",
        BASE_TIME,
        timedelta(seconds=10),
        _capabilities(),
    )
    assert first is not None and first.manual_retry is False
    outcome = store.reconcile_expired(
        BASE_TIME + timedelta(seconds=11),
        10,
    )
    assert outcome.needs_attention == 1
    attention = store.get_workload("owner-a", workload_id)
    assert attention.state is WorkloadState.NEEDS_ATTENTION
    store.record_attention_resolution(
        "owner-a",
        workload_id,
        decision="retry",
        expected_version=attention.version,
        idempotency_key="grant-before-abandon",
        now=BASE_TIME + timedelta(seconds=11),
    )
    manual = store.claim_next(
        "worker-manual-abandon",
        BASE_TIME + timedelta(seconds=12),
        timedelta(seconds=30),
        _capabilities(),
    )
    assert manual is not None and manual.manual_retry
    assert store.mark_running(
        manual,
        now=BASE_TIME + timedelta(seconds=13),
    ) is LeaseMutationStatus.APPLIED
    assert store.abandon_attempt(
        manual,
        now=BASE_TIME + timedelta(seconds=14),
    ) is LeaseMutationStatus.APPLIED
    row = _unit_rows(store, workload_id)[0]
    assert row["state"] == "needs_attention"
    assert row["manual_retry_tokens"] == 0
    assert store.get_workload(
        "owner-a", workload_id,
    ).state is WorkloadState.NEEDS_ATTENTION


def test_settlement_repairs_an_exhausted_pending_unit_idempotently(store):
    workload_id = _prepare(store, "exhausted-pending")
    store._connection.execute(
        """
        UPDATE units
        SET attempt_count=1
        WHERE owner_user_id='owner-a' AND state='pending'
        """
    )

    attention = store.settle_workload(
        "owner-a", workload_id, now=BASE_TIME,
    )
    assert attention.state is WorkloadState.NEEDS_ATTENTION
    row = _unit_rows(store, workload_id)[0]
    assert row["state"] == "needs_attention"
    assert row["error_class"] == "budget_exhausted"
    version = attention.version
    assert store.settle_workload(
        "owner-a", workload_id, now=BASE_TIME,
    ).version == version
    assert _event_count(store, workload_id, EventType.NEEDS_ATTENTION) == 1


def test_queued_work_exhausts_wall_budget_without_a_compatible_worker(store):
    workload_id = _prepare(store, "no-compatible-worker")
    incompatible = WorkerCapabilities.create(
        ((RunnerKind.EXECUTOR, "different_runner"),),
        {
            key: 0
            for key in (
                "cpu", "device", "llm", "local_io", "network_io", "vlm",
            )
        },
    )
    assert store.claim_next(
        "worker-incompatible",
        BASE_TIME,
        timedelta(seconds=30),
        incompatible,
    ) is None

    revision_id = store.get_workload(
        "owner-a", workload_id,
    ).active_revision_id
    assert revision_id is not None
    started_at = store._connection.execute(
        """
        SELECT started_at FROM revision_usage
        WHERE owner_user_id='owner-a' AND revision_id=?
        """,
        (revision_id,),
    ).fetchone()[0]
    assert started_at is not None
    store._connection.execute(
        """
        UPDATE revision_usage
        SET started_at='2020-01-01T00:00:00.000000Z'
        WHERE owner_user_id='owner-a' AND revision_id=?
        """,
        (revision_id,),
    )

    attention = store.settle_workload("owner-a", workload_id)
    assert attention.state is WorkloadState.NEEDS_ATTENTION
    reason = json.loads(store._connection.execute(
        """
        SELECT payload_json FROM events
        WHERE owner_user_id='owner-a' AND workload_id=?
          AND type='needs_attention'
        ORDER BY event_id DESC LIMIT 1
        """,
        (workload_id,),
    ).fetchone()[0])
    assert reason["reason_code"] == "budget_limit_exceeded"
    assert reason["budget"] == "max_wall_time_s"
    version = attention.version
    generation = store._connection.execute(
        "SELECT manual_retry_generation FROM revisions "
        "WHERE owner_user_id='owner-a' AND id=?",
        (attention.active_revision_id,),
    ).fetchone()[0]
    with pytest.raises(InvalidTransitionError, match="budget attention"):
        store.record_attention_resolution(
            "owner-a", workload_id, decision="retry",
            expected_version=version, idempotency_key="budget-retry-blocked",
        )
    assert store.get_workload("owner-a", workload_id).version == version
    assert store._connection.execute(
        "SELECT manual_retry_generation FROM revisions "
        "WHERE owner_user_id='owner-a' AND id=?",
        (attention.active_revision_id,),
    ).fetchone()[0] == generation


def test_exhausted_retry_repair_converges_in_strict_batches(store):
    workload_id = _prepare(store, "exhausted-bounded", count=300)
    store._connection.execute(
        """
        UPDATE units
        SET attempt_count=1
        WHERE owner_user_id='owner-a' AND revision_id=(
          SELECT active_revision_id FROM workloads
          WHERE owner_user_id='owner-a' AND id=?
        )
        """,
        (workload_id,),
    )

    first = store.settle_workload(
        "owner-a", workload_id, now=BASE_TIME,
    )
    assert first.state is WorkloadState.QUEUED
    assert sum(
        row["state"] == "needs_attention"
        for row in _unit_rows(store, workload_id)
    ) == 256
    assert store.settle_workloads(limit=10, now=BASE_TIME) == 10
    assert store.get_workload(
        "owner-a", workload_id,
    ).state is WorkloadState.QUEUED
    assert store.settle_workloads(limit=100, now=BASE_TIME) == 35
    assert store.get_workload(
        "owner-a", workload_id,
    ).state is WorkloadState.NEEDS_ATTENTION
