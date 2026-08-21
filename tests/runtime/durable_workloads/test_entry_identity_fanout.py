from __future__ import annotations

from datetime import datetime, timedelta, timezone

from durable_workloads.coordinator import LeaseMutationStatus, ValidatedResult, WorkerCapabilities
from durable_workloads.execution import DurableExecutionBridge
from durable_workloads.models import RunnerKind, WorkloadState
from durable_workloads.storage import DurableWorkloadStore
from helpers import inventory, map_stage, plan, source


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


def _entry_fanout_plan() -> dict:
    selected = plan(with_map=True)
    selected["stages"].append({
        **map_stage(),
        "key": "answer",
        "depends_on": ["map"],
        "cardinality": {
            "mode": "per_dependency",
            "max_units": 10,
            "entry_identity_field": "entry_id",
        },
        "input_bindings": {
            "paths": {
                "ref": "dependency.entries",
                "stage": "map",
                "field": "text",
            },
        },
    })
    return selected


def _commit_parent_entries(
    store: DurableWorkloadStore,
    entries: list[dict],
    *,
    request_key: str,
):
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    draft = store.create_draft(
        "owner-a", request_key, redacted_request={"summary": "fixture"},
    )
    store.admit_revision(
        "owner-a",
        draft.workload_id,
        _entry_fanout_plan(),
        inventory([source(0)]),
        expected_version=draft.version,
    )
    admitted = store.get_workload("owner-a", draft.workload_id)
    store.transition_workload(
        "owner-a", draft.workload_id, WorkloadState.QUEUED,
        expected_version=admitted.version,
        now=now,
    )
    map_lease = store.claim_next(
        "worker-a", now, timedelta(seconds=30), _capabilities(),
    )
    assert map_lease is not None
    assert map_lease.stage_key == "map"
    assert store.mark_running(map_lease, now=now) is LeaseMutationStatus.APPLIED
    committed = store.commit_result(
        map_lease,
        ValidatedResult.from_payload(
            map_lease.output_schema_version,
            {"entries": entries, "source_id": "source_00000000"},
        ),
        now=now + timedelta(seconds=1),
    )
    assert committed.status.value == "committed"
    return draft, now


def test_entry_identity_fanout_is_idempotent_and_binds_one_entry(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "private" / "state.sqlite3") as store:
        draft, now = _commit_parent_entries(
            store,
            [
                {"entry_id": "question-a", "text": "Prima domanda"},
                {"entry_id": "question-b", "text": "Seconda domanda"},
            ],
            request_key="entry-fanout",
        )

        assert store.materialize_ready_units("owner-a", draft.workload_id) == 2
        assert store.materialize_ready_units("owner-a", draft.workload_id) == 0

        answer_rows = store._connection.execute(
            """
            SELECT unit.shard_key
            FROM units unit
            JOIN stages stage
              ON stage.owner_user_id=unit.owner_user_id
             AND stage.id=unit.stage_id
             AND stage.revision_id=unit.revision_id
            WHERE unit.owner_user_id=? AND stage.stage_key='answer'
            ORDER BY unit.shard_key
            """,
            ("owner-a",),
        ).fetchall()
        assert len(answer_rows) == 2
        assert all(str(row["shard_key"]).startswith("entry:") for row in answer_rows)

        answer_lease = store.claim_next(
            "worker-a", now + timedelta(seconds=2), timedelta(seconds=30), _capabilities(),
        )
        assert answer_lease is not None
        assert answer_lease.stage_key == "answer"
        facts = store.execution_inputs(answer_lease)
        assert len(facts["dependencies"]) == 1
        assert facts["dependencies"][0]["entry_selected"] is True
        assert "entries" not in facts["dependencies"][0]["payload"]
        bridge = DurableExecutionBridge(store, runners=None, output_schemas=None)
        value, dependency_ids = bridge._dependency_value(
            facts,
            {"ref": "dependency.entries", "stage": "map", "field": "text"},
            want_array=False,
        )
        assert value in {"Prima domanda", "Seconda domanda"}
        assert len(dependency_ids) == 1


def test_entry_fanout_uses_a_persistent_strict_batch_bound(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "private" / "state.sqlite3") as store:
        entries = [
            {"entry_id": f"question-{index}", "text": f"Domanda {index}"}
            for index in range(7)
        ]
        draft, _now = _commit_parent_entries(
            store, entries, request_key="entry-fanout-bounded",
        )

        assert store.materialize_all_ready_units(limit=2) == 2
        assert store._connection.execute(
            """
            SELECT COUNT(*) FROM units unit
            JOIN stages stage
              ON stage.owner_user_id=unit.owner_user_id
             AND stage.revision_id=unit.revision_id
             AND stage.id=unit.stage_id
            WHERE stage.stage_key='answer'
            """
        ).fetchone()[0] == 2

        created = 2
        calls = 1
        while created < len(entries):
            inserted = store.materialize_ready_units(
                "owner-a", draft.workload_id, limit=2,
            )
            assert 0 < inserted <= 2
            created += inserted
            calls += 1
        assert calls == 4
        assert store.materialize_ready_units(
            "owner-a", draft.workload_id, limit=2,
        ) == 0
        progress = store._connection.execute(
            """
            SELECT progress.completed
            FROM stage_materialization progress
            JOIN stages stage
              ON stage.owner_user_id=progress.owner_user_id
             AND stage.revision_id=progress.revision_id
             AND stage.id=progress.stage_id
            WHERE stage.stage_key='answer'
            """
        ).fetchone()
        assert progress["completed"] == 1


def test_entry_identity_duplicate_across_batches_fails_closed(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "private" / "state.sqlite3") as store:
        draft, _now = _commit_parent_entries(
            store,
            [
                {"entry_id": "duplicate", "text": "Prima"},
                {"entry_id": "duplicate", "text": "Seconda"},
            ],
            request_key="entry-fanout-duplicate",
        )
        assert store.materialize_ready_units(
            "owner-a", draft.workload_id, limit=1,
        ) == 1
        assert store.materialize_ready_units(
            "owner-a", draft.workload_id, limit=1,
        ) == 0
        progress = store._connection.execute(
            """
            SELECT attention_code FROM stage_materialization progress
            JOIN stages stage
              ON stage.owner_user_id=progress.owner_user_id
             AND stage.revision_id=progress.revision_id
             AND stage.id=progress.stage_id
            WHERE progress.owner_user_id='owner-a'
              AND stage.stage_key='answer'
            """
        ).fetchone()
        assert progress["attention_code"] == "duplicate_entry_identity"
        assert store.materialize_ready_units(
            "owner-a", draft.workload_id, limit=10,
        ) == 0
        assert store.settle_workload(
            "owner-a", draft.workload_id,
        ).state is WorkloadState.NEEDS_ATTENTION
