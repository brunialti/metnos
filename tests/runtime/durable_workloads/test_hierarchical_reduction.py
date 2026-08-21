from __future__ import annotations

from datetime import datetime, timedelta, timezone

from durable_workloads.coordinator import (
    CommitStatus,
    RetryDecision,
    StructuredAttemptError,
    ValidatedResult,
    WorkerCapabilities,
)
from durable_workloads.models import RunnerKind, WorkloadState
from durable_workloads.reduction import hierarchical_node_bound
from durable_workloads.storage import DurableWorkloadStore
from helpers import inventory, plan, source


def _capabilities() -> WorkerCapabilities:
    return WorkerCapabilities.create(
        ((RunnerKind.EXECUTOR, "read_files_ocr"),),
        {
            "cpu": 0,
            "device": 0,
            "llm": 0,
            "local_io": 0,
            "network_io": 0,
            "vlm": 0,
        },
    )


def _plan(source_count: int, *, max_input_bytes: int = 4096) -> dict:
    selected = plan(with_map=True)
    selected["inventory"]["max_sources"] = source_count
    selected["stages"][1]["cardinality"]["max_units"] = source_count
    selected["stages"].append({
        "key": "reduce",
        "type": "reduce",
        "depends_on": ["map"],
        "runner": {"kind": "executor", "name": "read_files_ocr"},
        "effect_profile": "pure",
        "cardinality": {
            "mode": "singleton",
            "max_units": hierarchical_node_bound(source_count),
            "fan_in": 2,
            "reduction_input": "entries",
            "max_input_bytes": max_input_bytes,
        },
        "input_bindings": {
            "entries": {"ref": "dependency.entries", "stage": "map"},
        },
        "output_schema": {
            "schema_version": "metnos.output-schema-ref/1",
            "name": "metnos.test-reduce/1",
        },
        "retry": {
            "max_attempts": 1,
            "base_delay_ms": 0,
            "max_delay_ms": 0,
            "retryable_error_classes": [],
        },
        "timeout_s": 60,
        "invalidation_keys": [
            "dependencies.digest",
            "runner.contract_digest",
            "reduction.order",
            "reduction.fan_in",
        ],
        "resources": {
            "cpu": 0,
            "device": 0,
            "llm": 0,
            "local_io": 0,
            "network_io": 0,
            "vlm": 0,
        },
        "required": True,
    })
    selected["budgets"]["max_units"] = sum(
        int(stage["cardinality"]["max_units"])
        for stage in selected["stages"]
    )
    return selected


def _admit_and_commit_map(
    store: DurableWorkloadStore,
    source_count: int,
    *,
    max_input_bytes: int = 4096,
    entry_text: str = "bounded",
) -> str:
    draft = store.create_draft(
        "owner-a",
        f"hierarchy-{source_count}-{max_input_bytes}",
        redacted_request={"summary": "synthetic hierarchy"},
    )
    store.admit_revision(
        "owner-a",
        draft.workload_id,
        _plan(source_count, max_input_bytes=max_input_bytes),
        inventory([source(index) for index in range(source_count)]),
        expected_version=draft.version,
    )
    admitted = store.get_workload("owner-a", draft.workload_id)
    store.transition_workload(
        "owner-a",
        draft.workload_id,
        WorkloadState.QUEUED,
        expected_version=admitted.version,
    )
    start = datetime.now(timezone.utc)
    for index in range(source_count):
        now = start + timedelta(seconds=index)
        lease = store.claim_next(
            "hierarchy-worker", now, timedelta(minutes=5), _capabilities(),
        )
        assert lease is not None and lease.stage_key == "map"
        store.mark_running(lease, now=now + timedelta(microseconds=1))
        result = ValidatedResult.from_payload(
            lease.output_schema_version,
            {"entries": [{"ordinal": index, "text": entry_text}]},
        )
        assert store.commit_result(
            lease, result, now=now + timedelta(microseconds=2),
        ).status is CommitStatus.COMMITTED
    return draft.workload_id


def _finish_reduction(store: DurableWorkloadStore, workload_id: str) -> None:
    # Stay after the synthetic map commits but strictly inside the one-hour
    # wall budget.  Using exactly +1h tests budget expiry, not reduction.
    now = datetime.now(timezone.utc) + timedelta(minutes=5)
    for step in range(100):
        store.materialize_ready_units("owner-a", workload_id, limit=10)
        lease = store.claim_next(
            "hierarchy-worker",
            now + timedelta(seconds=step),
            timedelta(minutes=5),
            _capabilities(),
        )
        if lease is None:
            if store.materialization_complete("owner-a", workload_id):
                return
            continue
        assert lease.stage_key == "reduce"
        store.mark_running(
            lease, now=now + timedelta(seconds=step, microseconds=1),
        )
        facts = store.execution_inputs(lease)
        dependency_ids = tuple(
            dependency["result_id"] for dependency in facts["dependencies"]
        )
        result = ValidatedResult.from_payload(
            lease.output_schema_version,
            {"entries": [{"unit_key": lease.unit_key}]},
        )
        assert store.commit_result(
            lease,
            result,
            dependency_result_ids=dependency_ids,
            now=now + timedelta(seconds=step, microseconds=2),
        ).status is CommitStatus.COMMITTED
    raise AssertionError("hierarchical reduction did not converge")


def _reduction_keys(store: DurableWorkloadStore, workload_id: str) -> tuple[str, ...]:
    return tuple(row[0] for row in store._connection.execute(
        """
        SELECT unit.unit_key
        FROM units unit
        JOIN stages stage
          ON stage.owner_user_id=unit.owner_user_id
         AND stage.revision_id=unit.revision_id
         AND stage.id=unit.stage_id
        JOIN revisions revision
          ON revision.owner_user_id=unit.owner_user_id
         AND revision.id=unit.revision_id
        WHERE revision.workload_id=? AND stage.stage_key='reduce'
        ORDER BY unit.reduction_level, unit.reduction_ordinal
        """,
        (workload_id,),
    ))


def test_hierarchy_resumes_between_groups_and_exposes_one_root(tmp_path):
    database = tmp_path / "private" / "state.sqlite3"
    with DurableWorkloadStore.open(database) as store:
        workload_id = _admit_and_commit_map(store, 7)
        assert store.materialize_ready_units(
            "owner-a", workload_id, limit=1,
        ) == 1

    with DurableWorkloadStore.open(database) as store:
        _finish_reduction(store, workload_id)
        rows = store._connection.execute(
            """
            SELECT unit.reduction_level, COUNT(*) AS units,
                   SUM(unit.reduction_root) AS roots
            FROM units unit
            JOIN stages stage
              ON stage.owner_user_id=unit.owner_user_id
             AND stage.revision_id=unit.revision_id
             AND stage.id=unit.stage_id
            WHERE stage.stage_key='reduce'
            GROUP BY unit.reduction_level
            ORDER BY unit.reduction_level
            """
        ).fetchall()
        assert [(row["reduction_level"], row["units"]) for row in rows] == [
            (0, 4), (1, 2), (2, 1),
        ]
        assert sum(int(row["roots"]) for row in rows) == 1
        progress = store._connection.execute(
            """
            SELECT progress.unit_count, progress.completed
            FROM stage_materialization progress
            JOIN stages stage
              ON stage.owner_user_id=progress.owner_user_id
             AND stage.revision_id=progress.revision_id
             AND stage.id=progress.stage_id
            WHERE stage.stage_key='reduce'
            """
        ).fetchone()
        assert progress["unit_count"] == hierarchical_node_bound(7)
        assert progress["completed"] == 1


def test_hierarchy_keys_do_not_depend_on_random_database_ids(tmp_path):
    observed = []
    for suffix in ("a", "b"):
        with DurableWorkloadStore.open(
            tmp_path / suffix / "state.sqlite3"
        ) as store:
            workload_id = _admit_and_commit_map(store, 5)
            _finish_reduction(store, workload_id)
            observed.append(_reduction_keys(store, workload_id))
    assert observed[0] == observed[1]


def test_oversized_pair_stops_once_without_a_materialization_loop(tmp_path):
    with DurableWorkloadStore.open(
        tmp_path / "private" / "state.sqlite3"
    ) as store:
        workload_id = _admit_and_commit_map(
            store,
            2,
            max_input_bytes=1024,
            entry_text="x" * 700,
        )
        assert store.materialize_ready_units(
            "owner-a", workload_id, limit=10,
        ) == 1
        assert store.materialize_ready_units(
            "owner-a", workload_id, limit=10,
        ) == 0
        root = store._connection.execute(
            """
            SELECT unit.state, unit.error_class, unit.reduction_root
            FROM units unit
            JOIN stages stage
              ON stage.owner_user_id=unit.owner_user_id
             AND stage.revision_id=unit.revision_id
             AND stage.id=unit.stage_id
            WHERE stage.stage_key='reduce'
            """
        ).fetchone()
        assert tuple(root) == ("needs_attention", "reduction_not_converging", 1)
        assert store.settle_workload(
            "owner-a", workload_id,
        ).state is WorkloadState.NEEDS_ATTENTION


def test_failed_reducer_is_the_root_instead_of_hiding_behind_a_success(tmp_path):
    with DurableWorkloadStore.open(
        tmp_path / "private" / "state.sqlite3"
    ) as store:
        selected_plan = _plan(3)
        selected_plan["error_policy"] = {
            "mode": "declared",
            "allowed_error_classes": ["executor_permanent"],
        }
        draft = store.create_draft(
            "owner-a",
            "hierarchy-mixed-terminal-level",
            redacted_request={"summary": "synthetic hierarchy"},
        )
        store.admit_revision(
            "owner-a",
            draft.workload_id,
            selected_plan,
            inventory([source(index) for index in range(3)]),
            expected_version=draft.version,
            partial_output_accepted=True,
        )
        admitted = store.get_workload("owner-a", draft.workload_id)
        store.transition_workload(
            "owner-a",
            draft.workload_id,
            WorkloadState.QUEUED,
            expected_version=admitted.version,
        )
        start = datetime.now(timezone.utc)
        for index in range(3):
            now = start + timedelta(seconds=index)
            lease = store.claim_next(
                "hierarchy-worker", now, timedelta(minutes=5), _capabilities(),
            )
            assert lease is not None and lease.stage_key == "map"
            store.mark_running(lease, now=now + timedelta(microseconds=1))
            result = ValidatedResult.from_payload(
                lease.output_schema_version,
                {"entries": [{"ordinal": index, "text": "bounded"}]},
            )
            assert store.commit_result(
                lease, result, now=now + timedelta(microseconds=2),
            ).status is CommitStatus.COMMITTED

        assert store.materialize_ready_units(
            "owner-a", draft.workload_id, limit=10,
        ) == 2
        first = store.claim_next(
            "hierarchy-worker", start + timedelta(minutes=1),
            timedelta(minutes=5), _capabilities(),
        )
        assert first is not None and first.stage_key == "reduce"
        store.mark_running(first, now=start + timedelta(minutes=1, microseconds=1))
        first_facts = store.execution_inputs(first)
        first_result = ValidatedResult.from_payload(
            first.output_schema_version,
            {"entries": [{"unit_key": first.unit_key}]},
        )
        assert store.commit_result(
            first,
            first_result,
            dependency_result_ids=tuple(
                item["result_id"] for item in first_facts["dependencies"]
            ),
            now=start + timedelta(minutes=1, microseconds=2),
        ).status is CommitStatus.COMMITTED

        second = store.claim_next(
            "hierarchy-worker", start + timedelta(minutes=2),
            timedelta(minutes=5), _capabilities(),
        )
        assert second is not None and second.stage_key == "reduce"
        store.mark_running(second, now=start + timedelta(minutes=2, microseconds=1))
        store.fail_attempt(
            second,
            StructuredAttemptError.create(
                "executor_permanent",
                code="executor.synthetic_failure",
                message_key="ERR_DURABLE_EXECUTION_FAILED",
                retry="never",
                occurred_at=start + timedelta(minutes=2, microseconds=2),
            ),
            RetryDecision.FAIL_PERMANENT,
            now=start + timedelta(minutes=2, microseconds=2),
        )

        store.materialize_ready_units("owner-a", draft.workload_id, limit=10)
        roots = store._connection.execute(
            """
            SELECT unit.state
            FROM units unit
            JOIN stages stage
              ON stage.owner_user_id=unit.owner_user_id
             AND stage.revision_id=unit.revision_id
             AND stage.id=unit.stage_id
            WHERE stage.stage_key='reduce' AND unit.reduction_root=1
            """
        ).fetchall()
        assert [row["state"] for row in roots] == ["failed_permanent"]
