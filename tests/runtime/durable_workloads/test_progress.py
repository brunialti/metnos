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


def prepare(store, *, extra_phase=False, artifacts=False, timeout_s=60):
    draft = store.create_draft(OWNER, "request-progress", redacted_request={})
    selected_plan = plan(with_map=True, required_artifacts=[artifact_requirement()] if artifacts else [])
    if extra_phase:
        following = map_stage()
        following.update(key="another_map", depends_on=["map"])
        selected_plan["stages"].append(following)
    for stage in selected_plan["stages"]:
        stage["timeout_s"] = timeout_s
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


def measured(store, *, model_usage=None, **options):
    workload_id = prepare(store, **options)
    for offset in (0, 20, 40):
        lease = claim(store, offset)
        assert lease
        store.mark_running(lease, now=NOW + timedelta(seconds=offset + 2))
        if model_usage is not None:
            store._connection.execute("UPDATE attempts SET model_snapshot_json=json_set(model_snapshot_json, '$.mode', 'llm') WHERE state='running'")
            assert model_usage == "known"
            store._connection.execute("UPDATE attempts SET metrics_json=json_set(metrics_json, '$.usage_missing', 0, '$.llm_usage', json('{\"schema_version\":\"metnos.durable-model-usage/2\",\"records\":[],\"zero_calls_verified\":true,\"usage_missing\":false,\"cost_unknown\":false}')) WHERE state='running'")
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
    assert result["estimated_end_reason"] == ("needs_attention" if state == "needs_attention" else "not_running")


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


def active_measured(store, **options):
    wid = measured(store, **options)
    lease = claim(store, 51)
    store.mark_running(lease, now=NOW + timedelta(seconds=52))
    return wid


def test_current_phase_eta_is_distinct_and_persistently_anchored(store):
    wid = active_measured(store, extra_phase=True)
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=53))[wid]
    assert result["estimated_end_at"] is None  # Later unmaterialized phases are not forecast.
    assert result["current_phase"] == {"stage_key": "map", "estimated_end_at": "2026-09-16T12:03:10.000000Z", "estimated_end_reason": None}
    assert store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=54))[wid]["current_phase"] == result["current_phase"]


def test_between_claims_retains_whole_job_eta_without_inventing_active_phase(store):
    wid = measured(store)
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=51))[wid]
    assert result["estimated_end_at"] is not None
    assert result["current_phase"] == {"stage_key": None, "estimated_end_at": None, "estimated_end_reason": "no_active_phase"}


@pytest.mark.parametrize("mutation,reason", [
    ("UPDATE stage_materialization SET completed=0", "phase_expanding"),
    ("UPDATE revision_usage SET usage_unknown=1", "uncertain_progress"),
    ("UPDATE units SET attempt_count=2 WHERE state='pending'", "uncertain_progress"),
    ("UPDATE units SET state='needs_attention' WHERE state='pending'", "uncertain_progress"),
    ("UPDATE workloads SET state='needs_attention'", "needs_attention"),
    ("UPDATE workloads SET state='paused'", "not_running"),
])
def test_phase_eta_refuses_uncertain_or_inactive_work(store, mutation, reason):
    wid = active_measured(store, extra_phase=True)
    store._connection.execute(mutation)
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=53))[wid]
    assert result["current_phase"]["estimated_end_at"] is None
    assert result["current_phase"]["estimated_end_reason"] == reason
    if reason == "needs_attention":
        assert result["estimated_end_reason"] == "needs_attention"


def test_phase_eta_excludes_other_stage_samples_and_refuses_two_active_phases(store):
    wid = active_measured(store, extra_phase=True)
    next_stage = store._connection.execute("SELECT id FROM stages WHERE stage_key='another_map'").fetchone()[0]
    # Move one persisted successful sample to the future phase. The current phase
    # now has only two samples; the aggregate job's third must not qualify it.
    store._connection.execute("UPDATE units SET stage_id=? WHERE id=(SELECT id FROM units WHERE state='committed' LIMIT 1)", (next_stage,))
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=53))[wid]
    assert result["current_phase"]["estimated_end_reason"] == "insufficient_data"
    store._connection.execute("UPDATE units SET stage_id=?,state='leased' WHERE id=(SELECT id FROM units WHERE state='pending' LIMIT 1)", (next_stage,))
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=53))[wid]
    assert result["current_phase"] == {"stage_key": None, "estimated_end_at": None, "estimated_end_reason": "multiple_active_phases"}


def test_phase_eta_survives_inflight_model_usage_without_weakening_accounting(store):
    wid = active_measured(store, extra_phase=True)
    # Reproduce the production lifecycle: a live model attempt has a frozen
    # model but cannot report final consumption until its response arrives.
    store._connection.execute("UPDATE attempts SET model_snapshot_json=json_set(model_snapshot_json, '$.mode', 'llm') WHERE state='running'")
    assert store.refresh_usage_complete(OWNER, wid) is False
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=53))[wid]
    assert result["current_phase"]["estimated_end_at"] == "2026-09-16T12:03:10.000000Z"
    assert store._connection.execute("SELECT usage_complete FROM revisions").fetchone()[0] == 0
    assert store.refresh_usage_complete(OWNER, wid) is False


@pytest.mark.parametrize("state", ["running", "failed"])
def test_phase_eta_rejects_explicit_unknown_model_consumption(store, state):
    wid = active_measured(store, extra_phase=True)
    store._connection.execute("UPDATE attempts SET model_snapshot_json=json_set(model_snapshot_json, '$.mode', 'llm'), metrics_json=json_set(metrics_json, '$.usage_missing', 1), state=? WHERE state='running'", (state,))
    assert store.refresh_usage_complete(OWNER, wid) is False
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=53))[wid]
    assert result["current_phase"]["estimated_end_reason"] == "uncertain_progress"


def test_phase_eta_rejects_terminal_model_attempts_without_usage(store):
    wid = active_measured(store, extra_phase=True)
    # Defense in depth: reject the persisted terminal attempt even if the
    # enclosing unit state has not yet been reconciled to needs_attention.
    store._connection.execute("UPDATE attempts SET model_snapshot_json=json_set(model_snapshot_json, '$.mode', 'llm'), state='failed' WHERE state='running'")
    assert store.refresh_usage_complete(OWNER, wid) is False
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=53))[wid]
    assert result["current_phase"]["estimated_end_reason"] == "uncertain_progress"


def test_phase_eta_accepts_accounted_model_samples_while_next_call_runs(store):
    wid = active_measured(store, extra_phase=True, model_usage="known")
    store._connection.execute("UPDATE attempts SET model_snapshot_json=json_set(model_snapshot_json, '$.mode', 'llm') WHERE state='running'")
    assert store.refresh_usage_complete(OWNER, wid) is False
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=53))[wid]
    assert result["current_phase"]["estimated_end_at"] == "2026-09-16T12:03:10.000000Z"


def test_phase_eta_freshness_handles_slow_blocks_but_never_extends_forecast(store):
    wid = prepare(store, extra_phase=True, timeout_s=1200)
    for offset in (0, 240, 480):
        lease = claim(store, offset)
        store.mark_running(lease, now=NOW + timedelta(seconds=offset + 2))
        store.commit_result(lease, ValidatedResult.from_payload(lease.output_schema_version, {"ok": True}), now=NOW + timedelta(seconds=offset + 10))
    lease = claim(store, 600)
    store.mark_running(lease, now=NOW + timedelta(seconds=601))
    result = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=700))[wid]
    assert result["current_phase"]["estimated_end_at"] == "2026-09-16T12:36:10.000000Z"
    assert store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=900))[wid]["current_phase"] == result["current_phase"]
    stale = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=971))[wid]["current_phase"]
    assert stale["estimated_end_at"] is None and stale["estimated_end_reason"] == "stale_progress"


def test_phase_eta_overdue_and_future_clock_are_not_predictions(store):
    wid = active_measured(store)
    # Keep only one remaining unit: sample cadence predicts 70s, not now + 20s.
    store._connection.execute("DELETE FROM units WHERE state='pending'")
    overdue = store.progress_many(OWNER, [wid], now=NOW + timedelta(seconds=71))[wid]["current_phase"]
    assert overdue["estimated_end_at"] is None and overdue["estimated_end_reason"] == "estimate_overdue"
    assert store.progress_many(OWNER, [wid], now=NOW - timedelta(seconds=1))[wid]["current_phase"]["estimated_end_at"] is None
