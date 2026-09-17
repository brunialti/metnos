"""Recovery must not starve behind an unchanged control request."""

from datetime import timedelta

import pytest

from durable_workloads.models import WorkloadState
from durable_workloads.storage import DurableWorkloadStore
from test_lifecycle_convergence import BASE_TIME, _capabilities, _prepare


@pytest.mark.parametrize("command,seconds", [("pause", 4), ("cancel", 4), ("cancel", 3601)])
def test_settlement_passes_unchanged_control_request_to_drain_cancelled_work(tmp_path, command, seconds):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        first = _prepare(store, "still-pausing")
        lease = store.claim_next(
            "slow-worker", BASE_TIME, timedelta(seconds=120), _capabilities(),
        )
        assert lease is not None
        store.mark_running(lease, now=BASE_TIME + timedelta(seconds=1))
        pausing = getattr(store, "request_" + command)(
            "owner-a", first,
            expected_version=store.get_workload("owner-a", first).version,
            idempotency_key="pause-slow-worker",
            now=BASE_TIME + timedelta(seconds=2),
        )
        assert pausing.state is (WorkloadState.PAUSE_REQUESTED if command == "pause" else WorkloadState.CANCEL_REQUESTED)

        second = _prepare(store, "cancelled-with-residual-units", count=257)
        cancelled = store.request_cancel(
            "owner-a", second,
            expected_version=store.get_workload("owner-a", second).version,
            idempotency_key="cancel-many",
            now=BASE_TIME + timedelta(seconds=3),
        )
        assert cancelled.state is WorkloadState.CANCEL_REQUESTED

        def pending_count():
            return store._connection.execute(
                "SELECT COUNT(*) FROM units WHERE revision_id=? AND state='pending'",
                (cancelled.active_revision_id,),
            ).fetchone()[0]

        assert pending_count() == 1
        for _ in range(3):
            store.settle_workloads(limit=1, now=BASE_TIME + timedelta(seconds=seconds))
        assert pending_count() == 0
