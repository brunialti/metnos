"""Atomic model reservations using synthetic usage, never provider calls."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest

import executor_scheduler
from durable_workloads.admission import admit_candidate
from durable_workloads.compiler import _freeze_execution_policy
from durable_workloads.coordinator import LeaseMutationStatus, ValidatedResult, WorkerCapabilities
from durable_workloads.models import WorkloadState
from durable_workloads.storage import BudgetExceededError, DurableStoreError, DurableWorkloadStore
from helpers import inventory, map_stage, plan, source
from llm_telemetry import BoundedUsageSink, attempt_context, record
from test_execution_bridge import _contract, _record_model_facts, _schemas
from test_service_parallel_progress import (
    _Resolver as _ServiceResolver,
    _admit as _service_admit,
    _launch,
    _stop,
    _wait_completed,
)


class _Resolver:
    def __init__(self, *, resource="llm", small=False):
        self.contract = replace(_contract(
            "executor", "read_files_ocr", "metnos.test-map/1",
            inputs=("paths",), input_types=(("paths", "array"),),
            model=True, model_kind="vision" if resource == "vlm" else "chat",
        ), model_max_input_tokens=100, model_max_output_tokens=100)
        self.small = replace(
            self.contract, name="fixture_small_model",
            model_max_input_tokens=10, model_max_output_tokens=10,
        ) if small else None

    def resolve(self, kind, name):
        assert kind == "executor"
        for contract in (self.contract, self.small):
            if contract is not None and name == contract.name:
                return contract
        raise LookupError(name)

    def capabilities(self):
        bindings = [("executor", self.contract.name)]
        if self.small is not None:
            bindings.append(("executor", self.small.name))
        return WorkerCapabilities.create(
            bindings,
            {"cpu": 1, "device": 1, "llm": 1, "local_io": 1, "network_io": 1, "vlm": 1},
            effect_profiles=("pure",),
        )


def _prepare(store, *, budget=400, count=5, resource="llm", small=False,
             request="model-reservations", owner="owner-model", raw_patch=None):
    resolver = _Resolver(resource=resource, small=small)
    candidate = plan(with_map=True)
    candidate["budgets"].update(max_tokens=budget, max_concurrency=4)
    candidate["stages"][1]["resources"][resource] = 1
    candidate["stages"][1]["retry"]["max_attempts"] = 2
    candidate["stages"][1]["invalidation_keys"].extend([
        "model_binding.digest", "prompt.digest",
    ])
    if resolver.small is not None:
        stage = map_stage()
        stage.update(key="small", runner={"kind": "executor", "name": resolver.small.name})
        stage["resources"][resource] = 1
        stage["invalidation_keys"].extend(["model_binding.digest", "prompt.digest"])
        candidate["stages"].append(stage)
    draft = store.create_draft(owner, request, redacted_request={"summary": "Synthetic model units"})
    if raw_patch is None:
        admitted = admit_candidate(
            store, owner, draft.workload_id, candidate,
            inventory([source(index) for index in range(count)]),
            expected_version=draft.version, runners=resolver, output_schemas=_schemas(),
        )
        revision_id = admitted.revision.revision_id
    else:
        # Legacy storage accepts historical snapshots; their missing/unbounded
        # contracts must retain serial admission, never become parallel authority.
        entry = resolver.contract.snapshot(
            stage_key="map", output_schema=_schemas().resolve("metnos.test-map/1"),
        )
        entries = raw_patch(entry)
        revision = store.admit_revision(
            owner, draft.workload_id, candidate,
            inventory([source(index) for index in range(count)]),
            expected_version=draft.version,
            catalog_snapshot={"schema_version": "metnos.catalog-snapshot/1", "entries": entries},
        )
        revision_id = revision.revision_id
    store.transition_workload(
        owner, draft.workload_id, WorkloadState.QUEUED,
        expected_version=store.get_workload(owner, draft.workload_id).version,
    )
    return draft.workload_id, revision_id, resolver


def _claim(store, resolver, worker="model-worker", *, now=None, seconds=120):
    return store.claim_next(
        worker, now or datetime.now(timezone.utc), timedelta(seconds=seconds),
        resolver.capabilities(),
    )


def _start(store, resolver, lease, *, now=None):
    assert store.mark_running(lease, now=now) is LeaseMutationStatus.APPLIED
    contract = resolver.resolve(lease.runner_kind.value, lease.runner_name)
    assert _record_model_facts(store, lease, contract, now=now) is LeaseMutationStatus.APPLIED


def _usage(lease, *, input_tokens=10, output_tokens=10, kind="chat"):
    sink = BoundedUsageSink()
    with attempt_context(
        workload_id=lease.workload_id, stage_id=lease.stage_id,
        unit_key=lease.unit_key, attempt_id=lease.attempt_id, sink=sink,
    ):
        record(
            provider="llamacpp", kind=kind,
            result=SimpleNamespace(in_tokens=input_tokens, out_tokens=output_tokens, latency_ms=1),
        )
    return sink.summary()


@pytest.mark.parametrize("resource", ["llm", "vlm"])
def test_concurrent_claims_reserve_the_exact_boundary_and_survive_reopen(tmp_path, resource):
    path = tmp_path / "state.sqlite3"
    with DurableWorkloadStore.open(path) as store:
        _workload_id, _revision_id, resolver = _prepare(store, resource=resource)
    barrier = Barrier(8)
    now = datetime.now(timezone.utc)

    def claim(index):
        with DurableWorkloadStore.open(path) as store:
            barrier.wait(timeout=5)
            return _claim(store, resolver, f"worker-{index}", now=now)

    with ThreadPoolExecutor(max_workers=8) as pool:
        leases = [lease for lease in pool.map(claim, range(8)) if lease is not None]
    assert len(leases) == len({lease.unit_id for lease in leases}) == 2
    with DurableWorkloadStore.open(path) as store:
        assert _claim(store, resolver, "after-reopen", now=now) is None
        for lease in leases:
            assert store.remaining_model_budget(lease)["max_tokens"] == 200


def test_usage_replaces_only_its_reservation_atomically_and_idempotently(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        _workload_id, revision_id, resolver = _prepare(store, budget=450)
        first, second = _claim(store, resolver, "first"), _claim(store, resolver, "second")
        assert first is not None and second is not None
        assert _claim(store, resolver, "third") is None
        _start(store, resolver, first)
        usage = _usage(first)
        assert store.record_attempt_usage(first, usage) is LeaseMutationStatus.APPLIED
        assert store.record_attempt_usage(first, usage) is LeaseMutationStatus.ALREADY_APPLIED
        third = _claim(store, resolver, "third")
        assert third is not None
        assert _claim(store, resolver, "fourth") is None
        assert store.remaining_model_budget(second)["max_tokens"] == 230
        assert store._connection.execute(
            "SELECT input_tokens+output_tokens FROM revision_usage WHERE revision_id=?",
            (revision_id,),
        ).fetchone()[0] == 20


def test_expiry_requires_reconciliation_and_old_fences_cannot_release_budget(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        _workload_id, _revision_id, resolver = _prepare(store)
        now = datetime.now(timezone.utc)
        first = _claim(store, resolver, "first", now=now, seconds=10)
        second = _claim(store, resolver, "second", now=now, seconds=120)
        assert first is not None and second is not None
        expired = now + timedelta(seconds=11)
        assert _claim(store, resolver, "blocked", now=expired) is None
        with pytest.raises(BudgetExceededError):
            store.remaining_model_budget(first, now=expired)
        outcome = store.reconcile_expired(expired, batch_size=100)
        assert outcome.expired == 1 and outcome.needs_attention == 0
        retry = _claim(store, resolver, "retry", now=expired)
        assert retry is not None and retry.unit_id == first.unit_id
        assert retry.fence > first.fence
        assert store.record_attempt_usage(first, _usage(first), now=expired) is LeaseMutationStatus.STALE_FENCE
        assert _claim(store, resolver, "still-blocked", now=expired) is None
        assert store.remaining_model_budget(retry, now=expired)["max_tokens"] == 200
        with pytest.raises(DurableStoreError, match="active attempt fence"):
            store.remaining_model_budget(first)


@pytest.mark.parametrize("persist_missing_usage", [False, True])
def test_unknown_model_usage_never_releases_safe_spendable_budget(tmp_path, persist_missing_usage):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        workload_id, revision_id, resolver = _prepare(store)
        now = datetime.now(timezone.utc)
        first = _claim(store, resolver, "first", now=now, seconds=10)
        second = _claim(store, resolver, "second", now=now, seconds=120)
        assert first is not None and second is not None
        _start(store, resolver, first, now=now)
        if persist_missing_usage:
            assert store.record_attempt_usage(
                first, _usage(first, input_tokens=None), now=now,
            ) is LeaseMutationStatus.APPLIED
            assert _claim(store, resolver, "unknown", now=now) is None
        else:
            outcome = store.reconcile_expired(now + timedelta(seconds=11), batch_size=100)
            assert outcome.needs_attention == 1
        assert store._connection.execute(
            "SELECT usage_unknown FROM revision_usage WHERE revision_id=?", (revision_id,),
        ).fetchone()[0] == 1
        with pytest.raises(BudgetExceededError):
            store.remaining_model_budget(second)
        assert _claim(store, resolver, "unknown", now=now + timedelta(seconds=12)) is None


@pytest.mark.parametrize("patch", [
    lambda entry: [{**entry, "model_cost_policy": "metered"}],
    lambda entry: [{**entry, "model_cost_policy": "unbounded"}],
    lambda entry: [{**entry, "model_max_calls": True}],
    lambda entry: [{**entry, "model_max_input_tokens": None}],
    lambda entry: [{**entry, "model_binding_digest": "not-a-digest"}],
    lambda entry: [entry, entry],
    lambda entry: [],
])
def test_missing_or_unbounded_contracts_keep_serial_admission(tmp_path, patch):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        _workload_id, _revision_id, resolver = _prepare(store, raw_patch=patch)
        assert _claim(store, resolver, "first") is not None
        with store.open_peer() as peer:
            assert _claim(peer, resolver, "second") is None


def test_reserved_large_stage_does_not_starve_a_smaller_stage_or_another_workload(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        workload_id, _revision_id, resolver = _prepare(store, budget=230, count=2, small=True)
        first = _claim(store, resolver, "large")
        assert first is not None and first.stage_key == "map"
        smaller = _claim(store, resolver, "small")
        assert smaller is not None and smaller.stage_key == "small"
        assert _claim(store, resolver, "full") is None
        assert store.get_workload("owner-model", workload_id).state is WorkloadState.RUNNING
        other_id, _other_revision, _resolver = _prepare(store, request="other-workload")
        other = _claim(store, resolver, "other")
        assert other is not None and other.workload_id == other_id


def test_definitively_exhausted_budget_enters_attention_without_leasing(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        workload_id, revision_id, resolver = _prepare(store)
        first, second = _claim(store, resolver, "first"), _claim(store, resolver, "second")
        assert first is not None and second is not None
        for lease in (first, second):
            _start(store, resolver, lease)
            assert store.record_attempt_usage(
                lease, _usage(lease, input_tokens=100, output_tokens=100),
            ) is LeaseMutationStatus.APPLIED
            result = ValidatedResult.from_payload(
                lease.output_schema_version, {"entries": [], "source_id": "synthetic-source"},
            )
            assert store.commit_result(lease, result).status.value == "committed"
        assert _claim(store, resolver, "exhausted") is None
        assert store.get_workload("owner-model", workload_id).state is WorkloadState.NEEDS_ATTENTION
        assert store._connection.execute(
            "SELECT SUM(attempt_count) FROM units WHERE revision_id=?", (revision_id,),
        ).fetchone()[0] == 2


@pytest.mark.parametrize(("resource", "host_limit", "model_class", "expected_peak"), [
    ("llm", 1, 3, 1),
    ("llm", 2, 3, 2),
    ("llm", 2, 0, 1),
    ("vlm", 1, 3, 1),
    ("vlm", 2, 3, 2),
])
def test_supervised_model_units_respect_real_scheduler_caps_with_synthetic_usage(
    tmp_path, monkeypatch, resource, host_limit, model_class, expected_peak,
):
    """Exercise the actual bridge, fences, reservations and central scheduler."""

    scheduler = executor_scheduler.ExecutorScheduler(
        max_workers=4, max_in_flight=8, parallel_enabled=True, hardware_threads=4,
        resource_limits={resource: host_limit, "default": 8},
    )
    monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)
    monkeypatch.setenv("METNOS_LLM_PARALLELISM_CLASS", str(model_class))
    resolver = _ServiceResolver(3)
    kind = "vision" if resource == "vlm" else "chat"
    resolver.contract = replace(_contract(
        "workload", resolver.contract.name, "metnos.fixture-parallel-map/1",
        inputs=("record",), input_types=(("record", "object"),),
        model=True, model_kind=kind,
    ), model_max_input_tokens=100, model_max_output_tokens=100,
        execution_policy=_freeze_execution_policy({
            "effect": "read_only", "parallelism_class": 3,
            "resource_class": resource, "concurrency_key": "none",
            "equivalence_gate": "verified",
        }), execution_policy_declared=True,
    )
    path = tmp_path / "state.sqlite3"
    workload_id, revision_id = _service_admit(path, resolver, resource=resource)
    guard, entered, release = Lock(), Event(), Event()
    active = peak = 0

    def invoke(_name, args, _context):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
            if active == expected_peak:
                entered.set()
        try:
            assert release.wait(timeout=5)
            record(
                provider="llamacpp", kind=kind,
                result=SimpleNamespace(in_tokens=10, out_tokens=10, latency_ms=1),
            )
            return {"source_id": args["record"]["source_id"]}
        finally:
            with guard:
                active -= 1

    service, thread, exit_codes, _stores = _launch(
        path, resolver, invoke, parallel_workers=3,
        worker_resources={
            "cpu": 0, "device": 0, "llm": 1, "local_io": 0, "network_io": 0, "vlm": 1,
        },
    )
    try:
        assert entered.wait(timeout=5)
        release.set()
        _wait_completed(path, workload_id)
    finally:
        release.set()
        _stop(service, thread, exit_codes)
        scheduler.shutdown()
    assert peak == expected_peak
    with DurableWorkloadStore.open(path) as store:
        assert tuple(store._connection.execute(
            "SELECT input_tokens, output_tokens, usage_unknown FROM revision_usage WHERE revision_id=?",
            (revision_id,),
        ).fetchone()) == (60, 60, 0)
