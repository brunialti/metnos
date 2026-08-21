from __future__ import annotations

import pytest

from durable_workloads.models import (
    UNIT_TRANSITIONS,
    WORKLOAD_TRANSITIONS,
    UnitState,
    WorkloadState,
    can_transition_unit,
    can_transition_workload,
)
from durable_workloads.storage import ReservedCompletionTransitionError
from helpers import inventory, plan


@pytest.fixture
def store(tmp_path):
    from durable_workloads.storage import DurableWorkloadStore

    repository = DurableWorkloadStore.open(
        tmp_path / "durable" / "state.sqlite3"
    )
    try:
        yield repository
    finally:
        repository.close()


EXPECTED_WORKLOAD = {
    WorkloadState.DRAFT: {
        WorkloadState.ADMITTED, WorkloadState.CANCELLED, WorkloadState.FAILED,
    },
    WorkloadState.ADMITTED: {
        WorkloadState.QUEUED, WorkloadState.CANCEL_REQUESTED,
        WorkloadState.CANCELLED,
        WorkloadState.NEEDS_ATTENTION, WorkloadState.FAILED,
    },
    WorkloadState.QUEUED: {
        WorkloadState.RUNNING, WorkloadState.PAUSED,
        WorkloadState.CANCEL_REQUESTED, WorkloadState.CANCELLED,
        WorkloadState.NEEDS_ATTENTION, WorkloadState.FAILED,
    },
    WorkloadState.RUNNING: {
        WorkloadState.PAUSE_REQUESTED, WorkloadState.CANCEL_REQUESTED,
        WorkloadState.NEEDS_ATTENTION, WorkloadState.FAILED,
        WorkloadState.COMPLETED_WITH_ERRORS, WorkloadState.COMPLETED,
    },
    WorkloadState.PAUSE_REQUESTED: {
        WorkloadState.PAUSED, WorkloadState.CANCEL_REQUESTED,
        WorkloadState.NEEDS_ATTENTION, WorkloadState.FAILED,
    },
    WorkloadState.PAUSED: {
        WorkloadState.QUEUED, WorkloadState.CANCEL_REQUESTED,
        WorkloadState.CANCELLED,
        WorkloadState.NEEDS_ATTENTION, WorkloadState.FAILED,
    },
    WorkloadState.CANCEL_REQUESTED: {
        WorkloadState.CANCELLED, WorkloadState.NEEDS_ATTENTION,
        WorkloadState.FAILED,
    },
    WorkloadState.NEEDS_ATTENTION: {
        WorkloadState.QUEUED, WorkloadState.RUNNING,
        WorkloadState.CANCEL_REQUESTED,
        WorkloadState.CANCELLED, WorkloadState.FAILED,
    },
    WorkloadState.CANCELLED: set(),
    WorkloadState.FAILED: set(),
    WorkloadState.COMPLETED_WITH_ERRORS: set(),
    WorkloadState.COMPLETED: set(),
}

EXPECTED_UNIT = {
    UnitState.PENDING: {UnitState.LEASED, UnitState.CANCELLED, UnitState.SKIPPED},
    UnitState.LEASED: {
        UnitState.RUNNING, UnitState.PENDING, UnitState.NEEDS_ATTENTION,
        UnitState.CANCELLED,
    },
    UnitState.RUNNING: {
        UnitState.COMMITTED, UnitState.RETRY_WAIT, UnitState.FAILED_PERMANENT,
        UnitState.NEEDS_ATTENTION, UnitState.CANCELLED,
    },
    UnitState.RETRY_WAIT: {UnitState.PENDING, UnitState.CANCELLED},
    UnitState.NEEDS_ATTENTION: {UnitState.PENDING, UnitState.CANCELLED},
    UnitState.COMMITTED: set(),
    UnitState.FAILED_PERMANENT: set(),
    UnitState.CANCELLED: set(),
    UnitState.SKIPPED: set(),
}


def test_every_workload_state_pair_matches_the_normative_graph():
    assert set(WORKLOAD_TRANSITIONS) == set(WorkloadState)
    for source in WorkloadState:
        assert set(WORKLOAD_TRANSITIONS[source]) == EXPECTED_WORKLOAD[source]
        for destination in WorkloadState:
            assert can_transition_workload(source, destination) is (
                destination in EXPECTED_WORKLOAD[source]
            )


def test_every_unit_state_pair_matches_the_normative_graph():
    assert set(UNIT_TRANSITIONS) == set(UnitState)
    for source in UnitState:
        assert set(UNIT_TRANSITIONS[source]) == EXPECTED_UNIT[source]
        for destination in UnitState:
            assert can_transition_unit(source, destination) is (
                destination in EXPECTED_UNIT[source]
            )


def test_completed_states_are_reserved_for_completion_evaluation(store):
    draft = store.create_draft(
        "owner-a", "reserve-completion", redacted_request={"summary": "x"}
    )
    store.admit_revision(
        "owner-a", draft.workload_id, plan(), inventory(),
        expected_version=draft.version, usage_complete=True,
    )
    admitted = store.get_workload("owner-a", draft.workload_id)
    queued = store.transition_workload(
        "owner-a", draft.workload_id, WorkloadState.QUEUED,
        expected_version=admitted.version,
    )
    running = store.transition_workload(
        "owner-a", draft.workload_id, WorkloadState.RUNNING,
        expected_version=queued.version,
    )
    with pytest.raises(ReservedCompletionTransitionError):
        store.transition_workload(
            "owner-a", draft.workload_id, WorkloadState.COMPLETED,
            expected_version=running.version,
        )
