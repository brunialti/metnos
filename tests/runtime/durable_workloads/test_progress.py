"""Persisted, owner-scoped console progress; no estimated facts from queue time."""
from datetime import datetime, timedelta, timezone

import pytest

from durable_workloads.coordinator import ValidatedResult, WorkerCapabilities
from durable_workloads.models import RunnerKind, WorkloadState
from durable_workloads.storage import DurableWorkloadStore, WorkloadNotFoundError
from helpers import artifact_requirement, inventory, map_stage, plan, source

NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
OWNER = "owner-progress"


@pytest.fixture
def store(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "progress.sqlite3") as value:
        yield value


def prepare(store, *, extra_phase=False, artifacts=False):
    draft = store.create_draft(OWNER, "request-progress", redacted_request={})
    selected_plan = plan(with_map=True, required_artifacts=[artifact_requirement()] if artifacts else [])
    if extra_phase:
        following = map_stage()
        following.update(key="another_map", depends_on=["map"])
        selected_plan["stages"].append(following)
    store.admit_revision(OWNER, draft.workload_id, selected_plan,
                         inventory([source(i) for i in range(10)]),
                         expected_version=draft.version, usage_complete=True)
    admitted = store.get_workload(OWNER, draft.workload_id)
    store.transition_workload(OWNER, draft.workload_id, WorkloadState.QUEUED,
                              expected_version=admitted.version, now=NOW)
    return draft.workload_id


def claim(store, seconds):
    return store.claim_next("progress-worker", NOW + timedelta(seconds=seconds),
                            timedelta(seconds=60), WorkerCapabilities.create(
                                ((RunnerKind.EXECUTOR, "read_files_ocr"),),
                                {key: 0 for key in ("cpu", "device", "llm", "local_io", "network_io", "vlm")}))


def measured(store, **options):
    workload_id = prepare(store, **options)
    for offset in (0, 20, 40):
        lease = claim(store, offset)
        assert lease
        store.mark_running(lease, now=NOW + timedelta(seconds=offset + 2))
        store.commit_result(lease, ValidatedResult.from_payload(lease.output_schema_version, {"ok": True}),
                            now=NOW + timedelta(seconds=offset + 10))
    return workload_id


def test_start_excludes_admission_and_leasing(store):
    wid = prepare(store)
    assert store.progress_many(OWNER, [wid], now=NOW)[wid]["started_at"] is None
    lease = claim(store, 0)
    assert store.progress_many(OWNER, [wid], now=NOW)[wid]["started_at"] is None
    assert store.progress_many(OWNER, [wid], now=NOW)[wid]["parallelism"] == {
        "running_units": 0, "leased_units": 1, "max_concurrency": 2,
    }
    store.mark_running(lease, now=NOW + timedelta(seconds=2))
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=3))[wid]
    assert result["started_at"] == "2026-09-16T12:00:02.000000Z"
    assert result["estimated_end_at"] is None
    assert result["parallelism"] == {"running_units": 1, "leased_units": 0, "max_concurrency": 2}
    assert result["estimated_end_reason"] == "insufficient_data"


def test_known_units_and_cautious_persisted_eta(store):
    wid = measured(store)
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=51))[wid]
    assert result["known_units_percent"] == 30.0  # three saved units out of ten known units
    assert result["estimated_end_at"] == "2026-09-16T12:03:10.000000Z"
    assert result["estimated_end_reason"] is None
    assert store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=52))[wid]["estimated_end_at"] == result["estimated_end_at"]
    assert store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=180))[wid]["estimated_end_at"] is None
    assert store.progress_many(OWNER, [wid], now=NOW - timedelta(seconds=1))[wid]["started_at"] is None


@pytest.mark.parametrize("state", ["paused", "pause_requested", "needs_attention", "failed", "queued"])
def test_inactive_or_uncertain_jobs_have_no_eta(store, state):
    wid = measured(store)
    store._connection.execute("UPDATE workloads SET state=? WHERE owner_user_id=? AND id=?", (state, OWNER, wid))
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=51))[wid]
    assert result["started_at"] is not None
    assert result["estimated_end_at"] is None
    assert result["estimated_end_reason"] == "not_running"


def test_partial_materialization_and_retries_have_no_eta(store):
    wid = measured(store)
    store._connection.execute("UPDATE stage_materialization SET completed=0 WHERE owner_user_id=?", (OWNER,))
    assert store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=51))[wid]["estimated_end_at"] is None
    store._connection.execute("UPDATE stage_materialization SET completed=1 WHERE owner_user_id=?", (OWNER,))
    store._connection.execute("UPDATE units SET attempt_count=2 WHERE owner_user_id=? AND state='pending'", (OWNER,))
    assert store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=51))[wid]["estimated_end_at"] is None


@pytest.mark.parametrize("options", [{"extra_phase": True}, {"artifacts": True}])
def test_multi_phase_or_publication_plan_has_no_eta(store, options):
    wid = measured(store, **options)
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=51))[wid]
    assert result["started_at"] is not None
    assert result["known_units_percent"] is not None
    assert result["estimated_end_at"] is None
    assert result["estimated_end_reason"] == ("multi_phase" if options.get("extra_phase") else "insufficient_data")


def test_progress_owner_scope_and_bounded_empty_page(store):
    wid = measured(store)
    with pytest.raises(WorkloadNotFoundError):
        store.progress_many("other-owner", [wid], now=NOW)
    assert store.progress_many(OWNER, [], now=NOW) == {}
    with pytest.raises(ValueError):
        store.progress_many(OWNER, [wid] * 201, now=NOW)
    draft = store.create_draft(OWNER, "draft-only", redacted_request={})
    result = store.progress_many(OWNER, [draft.workload_id], now=NOW)[draft.workload_id]
    assert result["started_at"] is result["estimated_end_at"] is result["known_units_percent"] is None


def test_all_known_units_saved_does_not_imply_publication_completed(store):
    wid = measured(store)
    for offset in range(60, 200, 20):
        lease = claim(store, offset)
        store.mark_running(lease, now=NOW + timedelta(seconds=offset + 2))
        store.commit_result(lease, ValidatedResult.from_payload(lease.output_schema_version, {"ok": True}),
                            now=NOW + timedelta(seconds=offset + 10))
    assert store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=201))[wid]["known_units_percent"] == 99.9
    assert store.evaluate_completion(OWNER, wid, now=NOW + timedelta(seconds=201)).eligible
    assert store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=201))[wid]["known_units_percent"] == 100
