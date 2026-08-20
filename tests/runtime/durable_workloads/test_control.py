"""F9 contract proofs for the owner-scoped durable-workload façade."""

from __future__ import annotations

import pytest

from durable_workloads.control import DurableControlError, DurableWorkloadControl
from durable_workloads.models import (
    CONTROL_STATE_MATRIX,
    EventType,
    WorkloadState,
    control_transition,
)
from durable_workloads.storage import DurableWorkloadStore
from helpers import inventory, plan, source


OWNER = "owner-control-a"
OTHER_OWNER = "owner-control-b"


@pytest.fixture
def store(tmp_path):
    repository = DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3")
    try:
        yield repository
    finally:
        repository.close()


@pytest.fixture
def control(store):
    return DurableWorkloadControl(store, cursor_secret="test-control-secret")


def _draft(store, owner: str, number: int):
    return store.create_draft(
        owner,
        f"request-{number}",
        redacted_request={"summary": "synthetic"},
        workload_id=f"wrk_{number:08d}",
    )


def _admitted(store, owner: str, number: int):
    draft = _draft(store, owner, number)
    revision = store.admit_revision(
        owner,
        draft.workload_id,
        plan(with_map=True),
        inventory([source(0)]),
        expected_version=draft.version,
        usage_complete=True,
    )
    return draft, revision


def test_read_dtos_are_closed_owner_scoped_and_cursor_paged(store, control):
    first, _revision = _admitted(store, OWNER, 1)
    second = _draft(store, OWNER, 2)
    _draft(store, OTHER_OWNER, 3)

    page = control.list_workloads(OWNER, limit=1)
    assert page["schema_version"] == "metnos.durable-control/1"
    assert len(page["items"]) == 1
    assert page["next_cursor"]
    assert set(page["items"][0]) == {
        "workload_id", "state", "priority", "version", "active_revision_id",
        "created_at", "updated_at", "counters",
    }
    following = control.list_workloads(OWNER, limit=1, cursor=page["next_cursor"])
    assert {page["items"][0]["workload_id"], following["items"][0]["workload_id"]} == {
        first.workload_id, second.workload_id,
    }

    detail = control.detail(OWNER, first.workload_id)
    assert set(detail) == {"schema_version", "workload", "revision"}
    assert detail["revision"] is not None
    assert "request_key" not in detail["workload"]
    assert "plan_json" not in detail["revision"]
    assert "objective_redacted" not in detail["revision"]
    execution = detail["revision"]["execution"]
    assert execution["budget"]["max_units"] == 1000
    assert [stage["stage_key"] for stage in execution["stages"]] == ["inventory", "map"]
    assert execution["stages"][1]["runner_name"] == "read_files_ocr"
    assert execution["error_categories"] == []

    units = control.list_units(OWNER, first.workload_id, limit=1)
    assert len(units["items"]) == 1
    assert set(units["items"][0]) == {
        "unit_id", "revision_id", "stage_key", "state", "attempt_count",
        "next_attempt_at", "error_code", "updated_at",
    }
    events = control.list_events(OWNER, first.workload_id, limit=1)
    assert len(events["items"]) == 1
    assert set(events["items"][0]) == {"event_id", "event_type", "created_at"}
    with store._transaction() as connection:
        for number in range(3):
            store.append_event_in_transaction(
                connection,
                owner_user_id=OWNER,
                workload_id=first.workload_id,
                event_type=EventType.QUEUED,
                payload={"fixture": number},
            )
    recent = control.list_events(OWNER, first.workload_id, limit=2, recent=True)
    assert [event["event_id"] for event in recent["items"]] == [4, 5]
    assert recent["next_cursor"] is None


def test_owner_scope_and_cursors_fail_closed(store, control):
    workload, _revision = _admitted(store, OWNER, 11)
    for operation in (
        lambda: control.detail(OTHER_OWNER, workload.workload_id),
        lambda: control.list_events(OTHER_OWNER, workload.workload_id),
        lambda: control.list_units(OTHER_OWNER, workload.workload_id),
        lambda: control.cancel(
            OTHER_OWNER, workload.workload_id,
            expected_version=workload.version, idempotency_key="foreign-cancel",
        ),
    ):
        with pytest.raises(DurableControlError) as error:
            operation()
        assert (error.value.code, error.value.status) == (
            "durable_workload.not_found", 404,
        )

    with pytest.raises(DurableControlError) as error:
        control.list_workloads(OWNER, cursor="not-a-valid-cursor")
    assert (error.value.code, error.value.status) == (
        "durable_workload.invalid_cursor", 400,
    )
    with pytest.raises(DurableControlError) as error:
        control.list_workloads(OWNER, limit=101)
    assert (error.value.code, error.value.status) == (
        "durable_workload.invalid_limit", 400,
    )
    with pytest.raises(DurableControlError) as error:
        control.list_events(OWNER, workload.workload_id, cursor="not-used", recent=True)
    assert (error.value.code, error.value.status) == (
        "durable_workload.invalid_cursor", 400,
    )


def test_commands_have_versioned_idempotency_and_closed_state_matrix(store, control):
    draft, _revision = _admitted(store, OWNER, 21)
    admitted = store.get_workload(OWNER, draft.workload_id)
    queued = store.transition_workload(
        OWNER, draft.workload_id, WorkloadState.QUEUED,
        expected_version=admitted.version,
    )

    paused = control.pause(
        OWNER, draft.workload_id,
        expected_version=queued.version, idempotency_key="pause-21",
    )
    replay = control.pause(
        OWNER, draft.workload_id,
        expected_version=queued.version, idempotency_key="pause-21",
    )
    assert replay == paused
    assert paused["workload"]["state"] == "paused"

    with pytest.raises(DurableControlError) as stale:
        control.resume(
            OWNER, draft.workload_id,
            expected_version=queued.version, idempotency_key="resume-stale",
        )
    assert (stale.value.code, stale.value.status) == (
        "durable_workload.version_conflict", 409,
    )

    resumed = control.resume(
        OWNER, draft.workload_id,
        expected_version=paused["workload"]["version"], idempotency_key="resume-21",
    )
    cancelled = control.cancel(
        OWNER, draft.workload_id,
        expected_version=resumed["workload"]["version"], idempotency_key="cancel-21",
    )
    assert cancelled["workload"]["state"] == "cancelled"
    with pytest.raises(DurableControlError) as illegal:
        control.pause(
            OWNER, draft.workload_id,
            expected_version=cancelled["workload"]["version"], idempotency_key="pause-terminal",
        )
    assert (illegal.value.code, illegal.value.status) == (
        "durable_workload.illegal_state", 409,
    )


def test_attention_resolution_is_owner_scoped_idempotent_and_closed(store, control):
    draft, _revision = _admitted(store, OWNER, 31)
    admitted = store.get_workload(OWNER, draft.workload_id)
    attention = store.transition_workload(
        OWNER,
        draft.workload_id,
        WorkloadState.NEEDS_ATTENTION,
        expected_version=admitted.version,
    )

    resolved = control.resolve_attention(
        OWNER,
        draft.workload_id,
        decision="retry",
        expected_version=attention.version,
        idempotency_key="attention-retry-31",
    )
    replay = control.resolve_attention(
        OWNER,
        draft.workload_id,
        decision="retry",
        expected_version=attention.version,
        idempotency_key="attention-retry-31",
    )
    assert replay == resolved
    assert resolved["workload"]["state"] == "queued"
    assert "note" not in resolved

    with pytest.raises(DurableControlError) as foreign:
        control.resolve_attention(
            OTHER_OWNER,
            draft.workload_id,
            decision="cancel",
            expected_version=attention.version,
            idempotency_key="foreign-attention",
        )
    assert (foreign.value.code, foreign.value.status) == (
        "durable_workload.not_found", 404,
    )


def test_sse_read_model_replays_ten_thousand_persistent_events(store, control):
    draft = _draft(store, OWNER, 41)
    with store._transaction() as connection:
        for _ in range(10_000):
            store.append_event_in_transaction(
                connection,
                owner_user_id=OWNER,
                workload_id=draft.workload_id,
                event_type=EventType.QUEUED,
                payload={"version": draft.version},
            )

    after = 0
    replayed = []
    while True:
        batch = control.stream_events(
            OWNER, draft.workload_id, after_event_id=after,
        )
        if not batch:
            break
        assert len(batch) <= 500
        replayed.extend(batch)
        after = batch[-1].event_id
    assert len(replayed) == 10_001  # draft_created plus the durable history
    assert [event.event_id for event in replayed] == list(range(1, 10_002))
    assert set(replayed[-1].to_dict()) == {"event_id", "event_type", "created_at"}


def test_control_operation_matrix_covers_every_workload_state():
    expected = {
        "pause": {
            WorkloadState.QUEUED: WorkloadState.PAUSED,
            WorkloadState.RUNNING: WorkloadState.PAUSE_REQUESTED,
            WorkloadState.PAUSE_REQUESTED: None,
            WorkloadState.PAUSED: None,
        },
        "resume": {
            WorkloadState.QUEUED: None,
            WorkloadState.PAUSED: WorkloadState.QUEUED,
        },
        "cancel": {
            WorkloadState.DRAFT: WorkloadState.CANCELLED,
            WorkloadState.ADMITTED: WorkloadState.CANCELLED,
            WorkloadState.QUEUED: WorkloadState.CANCELLED,
            WorkloadState.RUNNING: WorkloadState.CANCEL_REQUESTED,
            WorkloadState.PAUSE_REQUESTED: WorkloadState.CANCEL_REQUESTED,
            WorkloadState.PAUSED: WorkloadState.CANCELLED,
            WorkloadState.CANCEL_REQUESTED: None,
            WorkloadState.CANCELLED: None,
            WorkloadState.NEEDS_ATTENTION: WorkloadState.CANCELLED,
        },
    }
    assert CONTROL_STATE_MATRIX == expected
    for command, legal_states in expected.items():
        for state in WorkloadState:
            if state in legal_states:
                assert control_transition(command, state) is legal_states[state]
            else:
                with pytest.raises(ValueError):
                    control_transition(command, state)
