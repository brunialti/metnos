"""The writer's admission wait must not extend an execution deadline."""

from datetime import datetime, timedelta, timezone

import pytest

import durable_workloads.storage as storage_module
from durable_workloads.coordinator import CommitStatus, ValidatedResult, WorkerCapabilities
from durable_workloads.storage import DurableWorkloadStore
from test_service_parallel_progress import _admit, _Resolver


@pytest.mark.parametrize("explicit_clock", [False, True])
def test_commit_rechecks_deadline_after_waiting_for_the_writer_boundary(tmp_path, monkeypatch, explicit_clock):
    path = tmp_path / "state.sqlite3"
    resolver = _Resolver(3)
    _admit(path, resolver, count=1, timeout_s=1)
    capabilities = WorkerCapabilities.create(
        (("workload", resolver.contract.name),),
        {"cpu": 1, "device": 0, "llm": 0, "local_io": 0, "network_io": 0, "vlm": 0},
    )
    wall = [datetime.now(timezone.utc)]

    class ControlledDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall[0] if tz is not None else wall[0].replace(tzinfo=None)

    with DurableWorkloadStore.open(path) as store:
        lease = store.claim_next("delayed-commit", wall[0], timedelta(seconds=30), capabilities)
        assert lease is not None
        store.mark_running(lease, now=wall[0])
        monkeypatch.setattr(storage_module, "datetime", ControlledDateTime)

        def simulate_writer_wait(checkpoint):
            if checkpoint == "workload_transaction_before_begin":
                wall[0] += timedelta(seconds=2)

        store._checkpoint = simulate_writer_wait
        result = ValidatedResult.from_payload(
            lease.output_schema_version, {"source_id": "source_00000000"},
        )
        outcome = store.commit_result(lease, result, **({"clock": lambda: wall[0]} if explicit_clock else {}))
        assert outcome.status is CommitStatus.DEADLINE_EXPIRED
