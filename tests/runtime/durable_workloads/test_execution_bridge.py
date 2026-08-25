"""F7 integration proofs for the generic durable execution bridge."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from durable_workloads.admission import admit_candidate
from durable_workloads.compiler import (
    ApprovedOutputSchema,
    CompilationError,
    FrozenRunnerContract,
    OutputSchemaRegistry,
)
from durable_workloads.coordinator import (
    LeaseMutationStatus,
    WorkerCapabilities,
    parse_instant,
)
from durable_workloads.execution import DurableExecutionBridge
from durable_workloads.models import ExecutionContext, RunnerKind, UnitState, WorkloadState
from durable_workloads.schema import MAX_SNAPSHOT_JSON_BYTES, digest_json
from durable_workloads.service import DurableWorkerService
from durable_workloads.storage import DurableStoreError, DurableWorkloadStore
from durable_workloads.worker import DurableWorker, ExecutionFailure, WorkerRunStatus
from helpers import inventory, plan, source, source_resolution
from llm_telemetry import (
    BoundedTransportUsageSink,
    BoundedUsageSink,
    TRANSPORT_USAGE_KEY,
    attempt_context,
    record,
    transport_usage_context,
)


def test_bounded_control_progress_is_never_reported_as_idle():
    calls = {"worker": 0, "completion": 0}

    def run_worker(_adapter):
        calls["worker"] += 1
        raise AssertionError("execution must wait for bounded materialization")

    store = SimpleNamespace(
        settle_workloads=lambda: 0,
        adopt_reusable_results=lambda *, limit: 0,
        materialize_all_ready_units=lambda *, limit: limit,
        complete_ready_workloads=lambda *, limit: calls.__setitem__(
            "completion", calls["completion"] + 1,
        ),
    )
    worker = SimpleNamespace(
        coordinator=SimpleNamespace(reconcile=lambda: None),
        run_once=run_worker,
    )
    bridge = DurableExecutionBridge(
        store,
        runners=SimpleNamespace(),
        output_schemas=SimpleNamespace(),
    )

    outcome = bridge.run_once(worker)

    assert outcome.status is WorkerRunStatus.CONTROL_PROGRESS
    assert calls == {"worker": 0, "completion": 1}


def test_all_structured_lre_errors_are_localized_in_the_shipped_catalog():
    root = Path(__file__).resolve().parents[3]
    message_keys: set[str] = set()
    for path in (root / "runtime" / "durable_workloads").glob("*.py"):
        message_keys.update(re.findall(
            r'message_key="(ERR_DURABLE_[A-Z0-9_]+)"',
            path.read_text(encoding="utf-8"),
        ))
    assert message_keys

    connection = sqlite3.connect(root / "install" / "data" / "i18n_seed.sqlite")
    try:
        rows = connection.execute(
            """
            SELECT key, lang FROM i18n
            WHERE key IN ({}) AND lang IN ('it', 'en') AND trim(text)<>''
            """.format(",".join("?" for _key in message_keys)),
            tuple(sorted(message_keys)),
        ).fetchall()
    finally:
        connection.close()
    localized = {(str(key), str(lang)) for key, lang in rows}
    assert localized == {
        (key, lang) for key in message_keys for lang in ("it", "en")
    }


def _digest(label: str) -> str:
    return digest_json("f7-test", {"label": label}, max_bytes=MAX_SNAPSHOT_JSON_BYTES)


def _schemas() -> OutputSchemaRegistry:
    return OutputSchemaRegistry((
        ApprovedOutputSchema.create(
            "metnos.inventory-seal/1",
            {
                "type": "object",
                "properties": {"digest": {"type": "string"}, "sources": {"type": "array"}},
                "required": ["digest", "sources"],
                "additionalProperties": False,
            },
        ),
        ApprovedOutputSchema.create(
            "metnos.test-map/1",
            {
                "type": "object",
                "properties": {
                    "entries": {"type": "array", "items": {"type": "object"}},
                    "source_id": {"type": "string"},
                },
                "required": ["entries", "source_id"],
                "additionalProperties": False,
            },
        ),
        ApprovedOutputSchema.create(
            "metnos.test-reduce/1",
            {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
                "additionalProperties": False,
            },
        ),
    ))


def _contract(
    kind: str,
    name: str,
    schema_name: str,
    *,
    version: str = "v1",
    effect: str = "pure",
    inputs: tuple[str, ...] = (),
    input_types: tuple[tuple[str, str], ...] = (),
    model: bool = False,
    model_name: str = "",
    model_provider: str = "llamacpp",
    model_tier: str = "",
    model_kind: str = "chat",
    model_cost_policy: str | None = None,
) -> FrozenRunnerContract:
    return FrozenRunnerContract(
        kind=kind,
        name=name,
        contract_digest=_digest(f"contract:{name}:{version}"),
        implementation_digest=_digest(f"implementation:{name}:{version}"),
        allowed_effects=(effect,),
        input_names=inputs,
        output_schema_names=(schema_name,),
        input_types=input_types,
        model_binding_digest=_digest(f"binding:{name}:{version}") if model else None,
        prompt_digest=_digest(f"prompt:{name}:{version}") if model else None,
        prompt_language="it" if model else None,
        model_provider=model_provider if model else None,
        model_digest=(
            "sha256:" + hashlib.sha256(model_name.encode("utf-8")).hexdigest()
            if model else None
        ),
        model_tier=model_tier if model else None,
        model_kind=model_kind if model else None,
        model_max_calls=1 if model else None,
        model_max_input_tokens=4_096 if model else None,
        model_max_output_tokens=4_096 if model else None,
        model_cost_policy=(
            model_cost_policy
            or (
                "zero"
                if model_provider in {"llamacpp", "ollama"}
                else "unbounded"
            )
        ) if model else None,
        transport="llm-gateway" if model else "local-subprocess",
        intelligence="model" if model else "deterministic",
    )


def _model_snapshot(contract: FrozenRunnerContract) -> dict[str, object]:
    return {
        "schema_version": "metnos.durable-model-snapshot/2",
        "mode": "llm",
        "runner_name": contract.name,
        "binding_digest": contract.model_binding_digest,
        "prompt_digest": contract.prompt_digest,
        "prompt_language": contract.prompt_language,
        "provider": contract.model_provider,
        "model_digest": contract.model_digest,
        "tier": contract.model_tier,
        "kind": contract.model_kind,
        "max_calls": contract.model_max_calls,
        "max_input_tokens": contract.model_max_input_tokens,
        "max_output_tokens": contract.model_max_output_tokens,
        "cost_policy": contract.model_cost_policy,
    }


def _record_model_facts(
    store: DurableWorkloadStore,
    lease,
    contract: FrozenRunnerContract,
    *,
    now: datetime | None = None,
) -> LeaseMutationStatus:
    return store.record_execution_facts(
        lease,
        executor_snapshot={
            "schema_version": "metnos.durable-executor-snapshot/1",
            "mode": "verified",
        },
        model_snapshot=_model_snapshot(contract),
        now=now,
    )


class _Resolver:
    def __init__(
        self,
        *,
        version: str = "v1",
        map_version: str | None = None,
        reduce_version: str | None = None,
        reduce_model: bool = True,
        map_effect: str = "pure",
    ) -> None:
        map_version = map_version or version
        reduce_version = reduce_version or version
        self.map = _contract(
            "executor", "read_files_ocr", "metnos.test-map/1",
            version=map_version,
            effect=map_effect,
            inputs=("paths",),
            input_types=(("paths", "array"),),
        )
        self.reduce = _contract(
            "workload", "entries.describe", "metnos.test-reduce/1",
            version=reduce_version,
            inputs=("entries",),
            input_types=(("entries", "array"),),
            model=reduce_model,
        )

    def resolve(self, kind: str, name: str) -> FrozenRunnerContract:
        if (kind, name) == ("executor", "read_files_ocr"):
            return self.map
        if (kind, name) == ("workload", "entries.describe"):
            return self.reduce
        raise LookupError(name)


class _ExactResolver(_Resolver):
    def attest_executor(self, name, executor):
        assert name == executor.name
        return self.map


def _strict_invoke(bridge, contract):
    return bridge._invoke(
        contract,
        {"stage": {"effect_profile": "pure"}},
        {},
        ExecutionContext(
            "owner", "workload", "revision", "stage", "unit", "attempt",
            "normal", (), "2099-01-01T00:00:00Z",
        ),
        None,
    )


@pytest.mark.parametrize(("lifecycle", "dormant", "code"), [
    ("preexercise", False, "execution.dormant"),
    ("active", True, "execution.dormant"),
    ("deprecated", False, "execution.retired"),
    ("archived", False, "execution.retired"),
    ("quarantined", False, "execution.quarantined"),
])
def test_strict_f5_attempt_rejects_lifecycle_before_guard_or_invocation(
    lifecycle, dormant, code,
):
    resolver = _ExactResolver()
    calls = []
    executor = SimpleNamespace(
        name="read_files_ocr", lifecycle=lifecycle, dormant=dormant,
        contract_id="user:demo/manifest.toml",
        generation_id="sha256:" + "1" * 64,
    )
    bridge = DurableExecutionBridge(
        SimpleNamespace(), runners=resolver, output_schemas=_schemas(),
        executor_loader=lambda _name: executor,
        executor_invoker=lambda *_args: calls.append("invoked"),
        executor_generation_attestor=lambda _executor: calls.append("guarded"),
        require_generation_attestation=True,
    )
    with pytest.raises(ExecutionFailure) as raised:
        _strict_invoke(bridge, resolver.map)
    assert json.loads(raised.value.error.payload_json)["code"] == code
    assert calls == []


def test_strict_f5_guard_is_reexecuted_for_every_attempt_and_has_no_name_digest_fallback():
    resolver = _ExactResolver()
    counts = {"guard": 0, "invoke": 0}
    executor = SimpleNamespace(
        name="read_files_ocr", lifecycle="active", dormant=False,
        contract_id="user:demo/manifest.toml",
        generation_id="sha256:" + "1" * 64,
    )

    def guard(_executor):
        counts["guard"] += 1

    def invoke(*_args):
        counts["invoke"] += 1
        return {"ok": True}

    bridge = DurableExecutionBridge(
        SimpleNamespace(), runners=resolver, output_schemas=_schemas(),
        executor_loader=lambda _name: executor, executor_invoker=invoke,
        executor_generation_attestor=guard,
        require_generation_attestation=True,
    )
    _strict_invoke(bridge, resolver.map)
    _strict_invoke(bridge, resolver.map)
    assert counts == {"guard": 2, "invoke": 2}

    fallback = DurableExecutionBridge(
        SimpleNamespace(), runners=_Resolver(), output_schemas=_schemas(),
        executor_loader=lambda _name: executor,
        executor_invoker=lambda *_args: pytest.fail("fallback invoked"),
        executor_generation_attestor=lambda _executor: None,
        require_generation_attestation=True,
    )
    with pytest.raises(ExecutionFailure) as raised:
        _strict_invoke(fallback, resolver.map)
    assert json.loads(raised.value.error.payload_json)["code"] == (
        "execution.loaded_executor_changed"
    )


def _reduce_stage() -> dict:
    return {
        "key": "reduce",
        "type": "reduce",
        "depends_on": ["map"],
        "runner": {"kind": "workload", "name": "entries.describe"},
        "effect_profile": "pure",
        "cardinality": {"mode": "singleton", "max_units": 1},
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
            "dependencies.digest", "runner.contract_digest",
            "model_binding.digest", "prompt.digest", "reduction.order",
        ],
        "resources": {
            "cpu": 0, "device": 0, "llm": 1, "local_io": 0,
            "network_io": 0, "vlm": 0,
        },
        "required": True,
    }


def _pipeline(*, map_effect: str = "pure", with_reduce: bool = True) -> dict:
    candidate = plan(with_map=True)
    candidate["stages"][1]["effect_profile"] = map_effect
    if map_effect == "manual_only":
        candidate["stages"][1]["retry"]["max_attempts"] = 1
    if with_reduce:
        candidate["stages"].append(_reduce_stage())
    return candidate


def _worker(store: DurableWorkloadStore, resolver: _Resolver, *, effects=("pure",)):
    capabilities = WorkerCapabilities.create(
        (
            (RunnerKind.EXECUTOR, "read_files_ocr"),
            (RunnerKind.WORKLOAD, "entries.describe"),
        ),
        {"cpu": 1, "device": 1, "llm": 1, "local_io": 1, "network_io": 1, "vlm": 1},
        effect_profiles=effects,
    )
    return DurableWorker(
        store,
        "f7-worker",
        capabilities,
        lease_duration=timedelta(seconds=120),
    )


def _admit(
    store: DurableWorkloadStore,
    resolver: _Resolver,
    *,
    candidate=None,
    request_key: str = "f7-generic-map-reduce",
):
    draft = store.create_draft(
        "owner-f7", request_key, redacted_request={"summary": "fixture"},
    )
    admitted = admit_candidate(
        store,
        "owner-f7",
        draft.workload_id,
        candidate or _pipeline(),
        inventory([source(0)]),
        expected_version=draft.version,
        runners=resolver,
        output_schemas=_schemas(),
    )
    store.transition_workload(
        "owner-f7",
        draft.workload_id,
        WorkloadState.QUEUED,
        expected_version=store.get_workload("owner-f7", draft.workload_id).version,
    )
    return draft.workload_id, admitted.revision.revision_id


def _map_observation() -> dict[str, object]:
    return {
        "entries": [{"text": "Q"}],
        "source_id": "source_00000000",
    }


def _budget_bridge(
    store: DurableWorkloadStore,
    resolver: _Resolver,
    workload_invoker,
) -> DurableExecutionBridge:
    return DurableExecutionBridge(
        store,
        runners=resolver,
        output_schemas=_schemas(),
        source_resolver=lambda item, _context: source_resolution(
            item, "/authorized/source.png",
        ),
        executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
        executor_invoker=lambda *_args: _map_observation(),
        workload_invoker=workload_invoker,
    )


def test_generic_map_reduce_recovers_and_records_complete_provenance(tmp_path):
    seen: dict[str, object] = {}
    resolver = _Resolver()
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        workload_id, revision_id = _admit(store, resolver)

        def invoke_executor(executor, args, context, timeout_s, device_id, autonomy):
            seen["executor"] = executor.name
            seen["args"] = dict(args)
            seen["context"] = context
            seen["timeout_s"] = timeout_s
            seen["device_id"] = device_id
            seen["autonomy"] = autonomy
            return {"entries": [{"text": "Q"}], "source_id": "source_00000000"}

        def invoke_workload(name, args, context):
            assert name == "entries.describe"
            assert args == {"entries": [{"text": "Q"}]}
            record(
                provider="llamacpp",
                result=SimpleNamespace(in_tokens=7, out_tokens=3, latency_ms=4),
            )
            return {"summary": "ok"}

        bridge = DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(item),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=invoke_executor,
            workload_invoker=invoke_workload,
        )
        worker = _worker(store, resolver)
        first = bridge.run_once(worker)
        assert first.status is WorkerRunStatus.COMMITTED
        assert seen["executor"] == "read_files_ocr"
        assert seen["args"] == {"paths": ["/authorized/source_00000000.png"]}
        assert seen["context"].owner_user_id == "owner-f7"
        assert seen["context"].attempt_id == first.lease.attempt_id
        assert 0 < seen["timeout_s"] <= 60
        assert parse_instant(seen["context"].deadline_at) <= (
            datetime.now(timezone.utc) + timedelta(seconds=60)
        )
        assert parse_instant(seen["context"].deadline_at) < parse_instant(
            first.lease.lease_expires_at
        )
        assert seen["device_id"] == "device-test"
        assert seen["autonomy"] == "readonly"

        second = bridge.run_once(worker)
        assert second.status is WorkerRunStatus.COMMITTED
        assert store.get_workload("owner-f7", workload_id).state is WorkloadState.COMPLETED
        rows = store._connection.execute(
            """
            SELECT result.provenance_json, attempt.executor_snapshot_json,
                   attempt.model_snapshot_json, attempt.metrics_json
            FROM results result
            JOIN attempts attempt
              ON attempt.owner_user_id=result.owner_user_id
             AND attempt.id=result.attempt_id
            WHERE result.owner_user_id=? AND result.revision_id=?
            ORDER BY result.id
            """,
            ("owner-f7", revision_id),
        ).fetchall()
        assert len(rows) == 2
        provenance = [json.loads(row["provenance_json"]) for row in rows]
        assert {item["validation"] for item in provenance} == {"approved_output_schema"}
        assert all(
            item["metrics_digest"] == digest_json(
                "durable-attempt-metrics",
                json.loads(row["metrics_json"]),
                max_bytes=1_048_576,
            )
            for item, row in zip(provenance, rows, strict=True)
        )
        assert all(item["executor_snapshot_digest"].startswith("sha256:") for item in provenance)
        execution_snapshots = [
            json.loads(row["executor_snapshot_json"]) for row in rows
        ]
        assert all(
            snapshot["semantic_arguments_digest"].startswith("sha256:")
            for snapshot in execution_snapshots
        )
        source_snapshot = next(
            snapshot for snapshot in execution_snapshots
            if "source_resolution_digest" in snapshot
        )
        assert source_snapshot["schema_version"] == (
            "metnos.durable-executor-snapshot/2"
        )
        assert source_snapshot["source_resolution_digest"].startswith(
            "sha256:"
        )
        assert "/authorized/" not in str(execution_snapshots)
        model_attempt = next(
            row for row in rows
            if json.loads(row["model_snapshot_json"])["mode"] == "llm"
        )
        usage = json.loads(model_attempt["metrics_json"])["llm_usage"]
        assert usage["usage_missing"] is False
        assert usage["records"][0]["attempt_id"] == second.lease.attempt_id
        assert store._connection.execute(
            "SELECT COUNT(*) FROM dependencies WHERE owner_user_id='owner-f7'"
        ).fetchone()[0] == 1


def test_registered_workload_runs_through_the_central_scheduler(
    tmp_path,
    monkeypatch,
):
    import executor_scheduler
    from executor_scheduler import ExecutorScheduler

    scheduler = ExecutorScheduler(
        max_in_flight=2,
        parallel_enabled=False,
        resource_limits={"llm": 1},
    )
    monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)
    resolver = _Resolver()
    try:
        with DurableWorkloadStore.open(
            tmp_path / "durable" / "state.sqlite3"
        ) as store:
            _admit(store, resolver)

            def invoke_workload(_name, _args, _context):
                record(
                    provider="llamacpp",
                    result=SimpleNamespace(
                        in_tokens=1,
                        out_tokens=1,
                        latency_ms=1,
                    ),
                )
                return {"summary": "scheduled"}

            bridge = _budget_bridge(store, resolver, invoke_workload)
            worker = _worker(store, resolver)
            assert bridge.run_once(worker).status is WorkerRunStatus.COMMITTED
            assert bridge.run_once(worker).status is WorkerRunStatus.COMMITTED

        metric = scheduler.metrics.snapshot()["workload:entries.describe"]
        assert metric["calls"] == 1
        assert metric["failures"] == 0
        assert metric["max_in_flight"] == 1
    finally:
        scheduler.shutdown()


def test_two_independent_units_execute_concurrently_only_through_central_slots(
    tmp_path,
    monkeypatch,
):
    import executor_scheduler
    import sandbox
    from executor_scheduler import ExecutorScheduler
    from loader import Executor

    scheduler = ExecutorScheduler(
        max_workers=3,
        max_in_flight=4,
        parallel_enabled=True,
        hardware_threads=4,
    )
    monkeypatch.setattr(executor_scheduler, "_DEFAULT_SCHEDULER", scheduler)
    monkeypatch.setattr(
        sandbox,
        "wrap_command",
        lambda _executor, command, **_kwargs: command,
    )
    policy = (
        ("effect", "read_only"),
        ("parallelism_class", 1),
        ("resource_class", "local_io"),
        ("concurrency_key", "none"),
        ("equivalence_gate", "verified"),
    )
    resolver = _Resolver()
    resolver.map = replace(
        resolver.map,
        execution_policy=policy,
        execution_policy_declared=True,
    )
    code = tmp_path / "parallel_executor.py"
    code.write_text(
        "import json, sys, time\n"
        "json.load(sys.stdin)\n"
        "time.sleep(0.15)\n"
        "json.dump({'entries': [{'text': 'Q'}], "
        "'source_id': 'source-fixture'}, sys.stdout)\n",
        encoding="utf-8",
    )
    executor = Executor(
        name="read_files_ocr",
        version="1",
        description="",
        affinity=[],
        args_schema={
            "type": "object",
            "properties": {"paths": {"type": "array"}},
        },
        capabilities=[],
        tests=[],
        code_path=code,
        manifest_path=tmp_path / "manifest.toml",
        signed_by="test",
        digest=resolver.map.implementation_digest,
        execution_policy=dict(policy),
        execution_policy_declared=True,
    )
    database = tmp_path / "durable" / "state.sqlite3"
    candidate = _pipeline(with_reduce=False)
    with DurableWorkloadStore.open(database) as store:
        draft = store.create_draft(
            "owner-f7",
            "parallel-central-slots",
            redacted_request={"summary": "fixture"},
        )
        admit_candidate(
            store,
            "owner-f7",
            draft.workload_id,
            candidate,
            inventory([source(0), source(1)]),
            expected_version=draft.version,
            runners=resolver,
            output_schemas=_schemas(),
        )
        store.transition_workload(
            "owner-f7",
            draft.workload_id,
            WorkloadState.QUEUED,
            expected_version=store.get_workload(
                "owner-f7", draft.workload_id,
            ).version,
        )

    def run_lane(index: int):
        with DurableWorkloadStore.open(database) as lane_store:
            bridge = DurableExecutionBridge(
                lane_store,
                runners=resolver,
                output_schemas=_schemas(),
                source_resolver=lambda item, _context: source_resolution(
                    item, f"/authorized/{item['source_id']}.png",
                ),
                executor_loader=lambda _name: executor,
            )
            worker = DurableWorker(
                lane_store,
                f"parallel-f7-{index}",
                WorkerCapabilities.create(
                    ((RunnerKind.EXECUTOR, "read_files_ocr"),),
                    {
                        "cpu": 1,
                        "device": 1,
                        "llm": 1,
                        "local_io": 1,
                        "network_io": 1,
                        "vlm": 1,
                    },
                ),
                lease_duration=timedelta(seconds=120),
            )
            return bridge.run_once(worker)

    try:
        futures = [
            scheduler.submit_orchestration(
                lambda index=index: run_lane(index)
            )
            for index in range(2)
        ]
        outcomes = [future.result(timeout=5) for future in futures]
        assert all(
            outcome.status is WorkerRunStatus.COMMITTED
            for outcome in outcomes
        )
        metric = scheduler.metrics.snapshot()["read_files_ocr"]
        assert metric["calls"] == 2
        assert metric["max_in_flight"] == 2
        with DurableWorkloadStore.open(database) as store:
            assert store._connection.execute(
                "SELECT COUNT(*) FROM results WHERE owner_user_id='owner-f7'"
            ).fetchone()[0] == 2
            assert store._connection.execute(
                "SELECT COUNT(DISTINCT unit_id) FROM attempts "
                "WHERE owner_user_id='owner-f7'"
            ).fetchone()[0] == 2
    finally:
        scheduler.shutdown()


@pytest.mark.parametrize(
    "source_resolver",
    (
        lambda _item, _context: "/authorized/unattested.png",
        lambda item, _context: source_resolution(
            item,
            "/authorized/content-changed.png",
            content_digest="sha256:" + "f" * 64,
        ),
    ),
    ids=("raw_value", "identity_mismatch"),
)
def test_source_reopening_fails_closed_without_matching_attestation(
    tmp_path,
    source_resolver,
):
    resolver = _Resolver()
    calls = {"executor": 0}
    with DurableWorkloadStore.open(
        tmp_path / "durable" / "state.sqlite3",
    ) as store:
        _admit(store, resolver, candidate=_pipeline(with_reduce=False))

        def invoke_executor(*_args):
            calls["executor"] += 1
            return _map_observation()

        bridge = DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=source_resolver,
            executor_loader=lambda _name: SimpleNamespace(
                name="read_files_ocr",
            ),
            executor_invoker=invoke_executor,
        )
        outcome = bridge.run_once(_worker(store, resolver))

        assert outcome.status is WorkerRunStatus.FAILED
        assert outcome.failure is not None
        assert outcome.failure.status.value == "needs_attention"
        assert calls["executor"] == 0
        unit = store._connection.execute(
            "SELECT error_class FROM units WHERE owner_user_id='owner-f7'"
        ).fetchone()
        assert unit["error_class"] == "source_missing"
        assert store._connection.execute(
            "SELECT COUNT(*) FROM results WHERE owner_user_id='owner-f7'"
        ).fetchone()[0] == 0


def test_executor_model_usage_crosses_process_boundary_without_content(tmp_path):
    resolver = _Resolver(reduce_model=False)
    resolver.map = _contract(
        "executor",
        "read_files_ocr",
        "metnos.test-map/1",
        inputs=("paths",),
        input_types=(("paths", "array"),),
        model=True,
        model_name="private-model",
        model_tier="vlm:default",
        model_kind="vision",
    )
    candidate = _pipeline(with_reduce=False)
    map_stage = candidate["stages"][1]
    map_stage["resources"]["vlm"] = 1
    map_stage["invalidation_keys"].extend((
        "model_binding.digest", "prompt.digest",
    ))

    child = BoundedTransportUsageSink()
    with transport_usage_context(child):
        record(
            provider="llamacpp",
            model="private-model",
            system="private prompt",
            user="private input",
            result=SimpleNamespace(
                text="private output",
                in_tokens=17,
                out_tokens=4,
                latency_ms=3,
            ),
            kind="vision",
            tier="vlm:default",
        )

    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        workload_id, revision_id = _admit(
            store,
            resolver,
            candidate=candidate,
            request_key="f7-executor-model-usage",
        )
        bridge = DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(
                item, "/authorized/source.png",
            ),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=lambda *_args: {
                **_map_observation(),
                TRANSPORT_USAGE_KEY: child.export(),
            },
        )
        outcome = bridge.run_once(_worker(store, resolver))
        assert outcome.status is WorkerRunStatus.COMMITTED
        assert store.get_workload(
            "owner-f7", workload_id,
        ).state is WorkloadState.COMPLETED
        metrics = store._connection.execute(
            """
            SELECT metrics_json FROM attempts
            WHERE owner_user_id=? AND unit_id IN (
                SELECT id FROM units
                WHERE owner_user_id=? AND revision_id=? AND stage_id=(
                    SELECT id FROM stages
                    WHERE owner_user_id=? AND revision_id=? AND stage_key='map'
                )
            )
            """,
            ("owner-f7", "owner-f7", revision_id, "owner-f7", revision_id),
        ).fetchone()
        usage = json.loads(metrics["metrics_json"])["llm_usage"]
        assert usage["usage_missing"] is False
        assert (usage["input_tokens"], usage["output_tokens"]) == (17, 4)
        assert "private prompt" not in metrics["metrics_json"]
        assert "private input" not in metrics["metrics_json"]
        assert "private output" not in metrics["metrics_json"]


def test_second_unchanged_execution_reuses_pure_nodes_without_invocation(tmp_path):
    resolver = _Resolver()
    calls = {"map": 0, "reduce": 0}
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        first_workload, _first_revision = _admit(
            store, resolver, request_key="f7-reuse-first",
        )

        def invoke_executor(*_args):
            calls["map"] += 1
            return _map_observation()

        def invoke_workload(_name, _args, _context):
            calls["reduce"] += 1
            record(
                provider="llamacpp",
                result=SimpleNamespace(in_tokens=1, out_tokens=1, latency_ms=1),
            )
            return {"summary": "ok"}

        bridge = DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(
                item, "/authorized/source.png",
            ),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=invoke_executor,
            workload_invoker=invoke_workload,
        )
        worker = _worker(store, resolver)
        assert bridge.run_once(worker).status is WorkerRunStatus.COMMITTED
        assert bridge.run_once(worker).status is WorkerRunStatus.COMMITTED
        assert store.get_workload(
            "owner-f7", first_workload,
        ).state is WorkloadState.COMPLETED
        assert calls == {"map": 1, "reduce": 1}

        second_workload, second_revision = _admit(
            store, resolver, request_key="f7-reuse-second",
        )
        assert (
            bridge.run_once(worker).status
            is WorkerRunStatus.CONTROL_PROGRESS
        )
        assert store.get_workload(
            "owner-f7", second_workload,
        ).state is WorkloadState.COMPLETED
        assert calls == {"map": 1, "reduce": 1}

        attempts = store._connection.execute(
            """
            SELECT attempt.executor_snapshot_json, attempt.metrics_json
            FROM attempts attempt
            JOIN units unit
              ON unit.owner_user_id=attempt.owner_user_id
             AND unit.id=attempt.unit_id
            WHERE attempt.owner_user_id=? AND unit.revision_id=?
            ORDER BY attempt.started_at, attempt.id
            """,
            ("owner-f7", second_revision),
        ).fetchall()
        assert len(attempts) == 2
        assert all(
            json.loads(row["executor_snapshot_json"])["mode"] == "result_reuse"
            for row in attempts
        )
        assert all(
            json.loads(row["metrics_json"])["execution_started"] is False
            for row in attempts
        )


def test_result_reuse_stops_at_an_already_expired_workload_budget(tmp_path):
    resolver = _Resolver()
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        first_workload, _ = _admit(store, resolver, request_key="reuse-budget-first")

        def invoke_workload(_name, _args, _context):
            record(
                provider="llamacpp",
                result=SimpleNamespace(in_tokens=1, out_tokens=1, latency_ms=1),
            )
            return {"summary": "ok"}

        bridge = DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(
                item, "/authorized/source.png",
            ),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=lambda *_args: _map_observation(),
            workload_invoker=invoke_workload,
        )
        worker = _worker(store, resolver)
        assert bridge.run_once(worker).status is WorkerRunStatus.COMMITTED
        assert bridge.run_once(worker).status is WorkerRunStatus.COMMITTED
        assert store.get_workload("owner-f7", first_workload).state is WorkloadState.COMPLETED

        second_workload, second_revision = _admit(
            store, resolver, request_key="reuse-budget-second",
        )
        store._connection.execute(
            "UPDATE revision_usage SET started_at='2020-01-01T00:00:00.000000Z' "
            "WHERE owner_user_id='owner-f7' AND revision_id=?",
            (second_revision,),
        )
        assert store.adopt_reusable_results() == 1
        assert store.get_workload(
            "owner-f7", second_workload,
        ).state is WorkloadState.NEEDS_ATTENTION
        assert store._connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE owner_user_id='owner-f7' "
            "AND unit_id IN (SELECT id FROM units WHERE revision_id=?)",
            (second_revision,),
        ).fetchone()[0] == 0


def test_reuse_stops_on_conflicting_historical_digests(tmp_path):
    resolver = _Resolver()
    candidate = _pipeline(with_reduce=False)
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        for request_key, text in (
            ("f7-conflict-a", "A"),
            ("f7-conflict-b", "B"),
        ):
            _admit(
                store,
                resolver,
                candidate=candidate,
                request_key=request_key,
            )
            direct_bridge = DurableExecutionBridge(
                store,
                runners=resolver,
                output_schemas=_schemas(),
                source_resolver=lambda item, _context: source_resolution(
                    item, "/authorized/source.png",
                ),
                executor_loader=lambda _name: SimpleNamespace(
                    name="read_files_ocr"
                ),
                executor_invoker=lambda *_args, value=text: {
                    "entries": [{"text": value}],
                    "source_id": "source_00000000",
                },
            )
            assert _worker(store, resolver).run_once(
                direct_bridge,
            ).status is WorkerRunStatus.COMMITTED

        third_workload, third_revision = _admit(
            store,
            resolver,
            candidate=candidate,
            request_key="f7-conflict-c",
        )
        assert store.adopt_reusable_results() == 1
        unit = store._connection.execute(
            """
            SELECT state, error_class FROM units
            WHERE owner_user_id=? AND revision_id=?
            """,
            ("owner-f7", third_revision),
        ).fetchone()
        assert tuple(unit) == (
            UnitState.NEEDS_ATTENTION.value,
            "result_digest_conflict",
        )
        assert store.get_workload(
            "owner-f7", third_workload,
        ).state is WorkloadState.NEEDS_ATTENTION


def test_changed_frozen_runner_contract_fails_before_invocation(tmp_path):
    original = _Resolver(version="v1")
    changed = _Resolver(version="v2")
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        workload_id, _revision_id = _admit(store, original, candidate=_pipeline(with_reduce=False))
        bridge = DurableExecutionBridge(
            store,
            runners=changed,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(
                item, "/authorized/source.png",
            ),
            executor_loader=lambda _name: (_ for _ in ()).throw(AssertionError("must not invoke")),
        )
        outcome = bridge.run_once(_worker(store, changed))
        assert outcome.status is WorkerRunStatus.FAILED
        unit = store._connection.execute(
            "SELECT state, error_class FROM units WHERE owner_user_id='owner-f7'"
        ).fetchone()
        assert tuple(unit) == (UnitState.FAILED_PERMANENT.value, "contract_violation")
        assert store.get_workload(
            "owner-f7", workload_id,
        ).state is WorkloadState.FAILED


def test_exact_loaded_executor_is_attested_before_invocation(tmp_path):
    class AttestingResolver(_Resolver):
        def attest_executor(self, name, _executor):
            assert name == "read_files_ocr"
            return _contract(
                "executor",
                "read_files_ocr",
                "metnos.test-map/1",
                version="replaced-after-resolution",
                inputs=("paths",),
                input_types=(("paths", "array"),),
            )

    resolver = AttestingResolver()
    calls = {"executor": 0}
    with DurableWorkloadStore.open(
        tmp_path / "durable" / "state.sqlite3"
    ) as store:
        _admit(
            store,
            resolver,
            candidate=_pipeline(with_reduce=False),
        )

        def invoke_executor(*_args):
            calls["executor"] += 1
            return _map_observation()

        bridge = DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(
                item, "/authorized/source.png",
            ),
            executor_loader=lambda _name: SimpleNamespace(
                name="read_files_ocr",
            ),
            executor_invoker=invoke_executor,
        )
        outcome = bridge.run_once(_worker(store, resolver))

        assert outcome.status is WorkerRunStatus.FAILED
        assert calls["executor"] == 0
        assert store._connection.execute(
            "SELECT error_class FROM units WHERE owner_user_id='owner-f7'"
        ).fetchone()[0] == "contract_violation"


@pytest.mark.parametrize(
    "changed",
    (
        _Resolver(reduce_version="v2"),
        _Resolver(reduce_model=False),
    ),
    ids=("prompt_changed", "binding_missing"),
)
def test_changed_model_identity_fails_before_reduce_invocation(tmp_path, changed):
    original = _Resolver()
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        _admit(store, original)
        initial = DurableExecutionBridge(
            store,
            runners=original,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(
                item, "/authorized/source.png",
            ),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=lambda *_args: {
                "entries": [{"text": "Q"}], "source_id": "source_00000000",
            },
        )
        assert initial.run_once(_worker(store, original)).status is WorkerRunStatus.COMMITTED
        changed_bridge = DurableExecutionBridge(
            store,
            runners=changed,
            output_schemas=_schemas(),
            workload_invoker=lambda *_args: (_ for _ in ()).throw(
                AssertionError("a changed model identity must not run"),
            ),
        )
        outcome = changed_bridge.run_once(_worker(store, changed))
        assert outcome.status is WorkerRunStatus.FAILED
        unit = store._connection.execute(
            "SELECT state, error_class FROM units WHERE owner_user_id='owner-f7' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert tuple(unit) == (UnitState.FAILED_PERMANENT.value, "contract_violation")


def test_map_reduce_survives_store_reopen_without_duplicate_commits(tmp_path):
    path = tmp_path / "durable" / "state.sqlite3"
    resolver = _Resolver()

    def invoke_workload(_name, _args, _context):
        record(
            provider="llamacpp",
            result=SimpleNamespace(in_tokens=1, out_tokens=1, latency_ms=1),
        )
        return {"summary": "ok"}

    def bridge_for(store):
        return DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(
                item, "/authorized/source.png",
            ),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=lambda *_args: {
                "entries": [{"text": "Q"}], "source_id": "source_00000000",
            },
            workload_invoker=invoke_workload,
        )

    with DurableWorkloadStore.open(path) as first_store:
        workload_id, revision_id = _admit(first_store, resolver)
        first = bridge_for(first_store).run_once(_worker(first_store, resolver))
        assert first.status is WorkerRunStatus.COMMITTED

    with DurableWorkloadStore.open(path) as restarted_store:
        second = bridge_for(restarted_store).run_once(_worker(restarted_store, resolver))
        assert second.status is WorkerRunStatus.COMMITTED
        assert restarted_store.get_workload(
            "owner-f7", workload_id,
        ).state is WorkloadState.COMPLETED
        assert restarted_store._connection.execute(
            "SELECT COUNT(*) FROM results WHERE owner_user_id=? AND revision_id=?",
            ("owner-f7", revision_id),
        ).fetchone()[0] == 2


def test_malformed_executor_output_never_reaches_result_commit(tmp_path):
    resolver = _Resolver()
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        _admit(store, resolver, candidate=_pipeline(with_reduce=False))
        bridge = DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(
                item, "/authorized/source.png",
            ),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=lambda *_args: {"entries": "not-an-array"},
        )
        outcome = bridge.run_once(_worker(store, resolver))
        assert outcome.status is WorkerRunStatus.FAILED
        assert store._connection.execute(
            "SELECT COUNT(*) FROM results WHERE owner_user_id='owner-f7'"
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT error_class FROM units WHERE owner_user_id='owner-f7'"
        ).fetchone()[0] == "contract_violation"


def test_provider_outage_and_request_limit_retry_then_commit_once(tmp_path):
    resolver = _Resolver()
    candidate = _pipeline(with_reduce=False)
    candidate["stages"][1]["retry"] = {
        "max_attempts": 3,
        "base_delay_ms": 0,
        "max_delay_ms": 0,
        "retryable_error_classes": ["executor_transient"],
    }
    observations = iter((
        {"ok": False, "error_class": "provider_unavailable"},
        {"ok": False, "error_class": "rate_limited"},
        _map_observation(),
    ))
    with DurableWorkloadStore.open(
        tmp_path / "durable" / "state.sqlite3"
    ) as store:
        workload_id, revision_id = _admit(
            store,
            resolver,
            candidate=candidate,
            request_key="provider-outage-and-limit",
        )
        bridge = DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(item),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=lambda *_args: next(observations),
        )
        worker = _worker(store, resolver)

        first = bridge.run_once(worker)
        second = bridge.run_once(worker)
        third = bridge.run_once(worker)

        assert first.failure is not None
        assert first.failure.status.value == "retry_scheduled"
        assert second.failure is not None
        assert second.failure.status.value == "retry_scheduled"
        assert third.status is WorkerRunStatus.COMMITTED
        assert store.get_workload(
            "owner-f7", workload_id,
        ).state is WorkloadState.COMPLETED
        assert store._connection.execute(
            "SELECT COUNT(*) FROM attempts attempt JOIN units unit "
            "ON unit.owner_user_id=attempt.owner_user_id "
            "AND unit.id=attempt.unit_id "
            "WHERE attempt.owner_user_id='owner-f7' AND unit.revision_id=?",
            (revision_id,),
        ).fetchone()[0] == 3
        assert store._connection.execute(
            "SELECT COUNT(*) FROM results WHERE owner_user_id='owner-f7' "
            "AND revision_id=?",
            (revision_id,),
        ).fetchone()[0] == 1


def test_revoked_executor_credential_requires_attention_without_retry(tmp_path):
    resolver = _Resolver()
    candidate = _pipeline(with_reduce=False)
    candidate["stages"][1]["retry"] = {
        "max_attempts": 3,
        "base_delay_ms": 0,
        "max_delay_ms": 0,
        "retryable_error_classes": ["executor_transient"],
    }
    calls = []
    with DurableWorkloadStore.open(
        tmp_path / "durable" / "state.sqlite3"
    ) as store:
        workload_id, revision_id = _admit(
            store,
            resolver,
            candidate=candidate,
            request_key="revoked-executor-credential",
        )

        def denied(*_args):
            calls.append(True)
            return {"ok": False, "error_class": "permission_denied"}

        bridge = DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(item),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=denied,
        )
        worker = _worker(store, resolver)

        outcome = bridge.run_once(worker)

        assert outcome.failure is not None
        assert outcome.failure.status.value == "needs_attention"
        assert bridge.run_once(worker).status is WorkerRunStatus.IDLE
        assert calls == [True]
        row = store._connection.execute(
            "SELECT state, error_class, attempt_count FROM units "
            "WHERE owner_user_id='owner-f7' AND revision_id=?",
            (revision_id,),
        ).fetchone()
        assert tuple(row) == ("needs_attention", "capability_unavailable", 1)
        assert store.get_workload(
            "owner-f7", workload_id,
        ).state is WorkloadState.NEEDS_ATTENTION


def test_manual_only_timeout_requires_attention_and_never_retries(tmp_path):
    resolver = _Resolver(map_effect="manual_only")
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        _admit(
            store,
            resolver,
            candidate=_pipeline(map_effect="manual_only", with_reduce=False),
        )
        bridge = DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(
                item, "/authorized/source.png",
            ),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=lambda *_args: (_ for _ in ()).throw(TimeoutError()),
        )
        outcome = bridge.run_once(_worker(store, resolver, effects=("manual_only",)))
        assert outcome.status is WorkerRunStatus.FAILED
        assert outcome.failure.status.value == "needs_attention"
        unit = store._connection.execute(
            "SELECT state, attempt_count FROM units WHERE owner_user_id='owner-f7'"
        ).fetchone()
        assert tuple(unit) == (UnitState.NEEDS_ATTENTION.value, 1)
        assert store._connection.execute(
            "SELECT state FROM attempts WHERE owner_user_id='owner-f7'"
        ).fetchone()[0] == "timed_out"


def test_execution_facts_never_replace_a_frozen_remote_identity(tmp_path):
    resolver = _Resolver()
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        _admit(store, resolver, candidate=_pipeline(with_reduce=False))
        lease = _worker(store, resolver).claim_next()
        assert lease is not None
        assert store.mark_running(lease) is LeaseMutationStatus.APPLIED
        executor_snapshot = {
            "schema_version": "metnos.durable-executor-snapshot/1",
            "mode": "verified",
            "contract": {"digest": _digest("contract")},
        }
        model_snapshot = {
            "schema_version": "metnos.durable-model-snapshot/1",
            "mode": "none",
        }
        assert store.record_execution_facts(
            lease,
            executor_snapshot=executor_snapshot,
            model_snapshot=model_snapshot,
            device_id="device-a",
            invocation_id="invoke-a",
        ) is LeaseMutationStatus.APPLIED
        with pytest.raises(DurableStoreError, match="attempt device is already frozen"):
            store.record_execution_facts(
                lease,
                executor_snapshot=executor_snapshot,
                model_snapshot=model_snapshot,
                device_id="device-b",
                invocation_id="invoke-a",
            )
        with pytest.raises(DurableStoreError, match="attempt invocation is already frozen"):
            store.record_execution_facts(
                lease,
                executor_snapshot=executor_snapshot,
                model_snapshot=model_snapshot,
                device_id="device-a",
                invocation_id="invoke-b",
            )


def test_supervised_service_resumes_the_generic_f7_path_after_restart(tmp_path):
    path = tmp_path / "durable" / "state.sqlite3"
    health_path = tmp_path / "durable" / "service_health.json"
    resolver = _Resolver()
    with DurableWorkloadStore.open(path) as store:
        workload_id, revision_id = _admit(store, resolver)

    first: DurableWorkerService | None = None

    def first_executor_invoker(*_args):
        # This mirrors SIGTERM's handler: request a cooperative stop while an
        # admitted attempt is active. The worker must finish its fenced commit
        # before the next supervisor resumes the remaining unit.
        assert first is not None
        first.request_stop()
        return {
            "entries": [{"text": "Q"}], "source_id": "source_00000000",
        }

    def bridge_factory(store):
        return DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(
                item, "/authorized/source.png",
            ),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=first_executor_invoker,
            workload_invoker=lambda _name, _args, _context: (
                record(
                        provider="llamacpp",
                    result=SimpleNamespace(in_tokens=1, out_tokens=1, latency_ms=1),
                )
                or {"summary": "ok"}
            ),
        )

    def service() -> DurableWorkerService:
        return DurableWorkerService(
            enabled=True,
            store_path=path,
            health_path=health_path,
            worker_factory=lambda store: _worker(store, resolver),
            bridge_factory=bridge_factory,
            poll_interval_s=0.05,
        )

    first = service()
    try:
        assert first.start() is True
        first.run_cycle()
        assert first.worker is not None and first.worker.stopping is True
    finally:
        first.stop()

    second = service()
    try:
        assert second.start() is True
        second.run_cycle()
    finally:
        second.stop()

    with DurableWorkloadStore.open(path) as store:
        assert store.get_workload("owner-f7", workload_id).state is WorkloadState.COMPLETED
        assert store._connection.execute(
            "SELECT COUNT(*) FROM results WHERE owner_user_id=? AND revision_id=?",
            ("owner-f7", revision_id),
        ).fetchone()[0] == 2


def test_token_budget_exhaustion_stops_without_an_automatic_retry(tmp_path):
    resolver = _Resolver()
    candidate = _pipeline()
    candidate["budgets"]["max_tokens"] = 8_192
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        workload_id, revision_id = _admit(store, resolver, candidate=candidate)

        calls = []

        def invoke_workload(_name, _args, _context):
            calls.append(True)
            return {"summary": "must not be committed"}

        bridge = _budget_bridge(store, resolver, invoke_workload)
        worker = _worker(store, resolver)
        assert bridge.run_once(worker).status is WorkerRunStatus.COMMITTED
        with store._transaction() as connection:
            connection.execute(
                """
                UPDATE revision_usage SET input_tokens=1
                WHERE owner_user_id='owner-f7' AND revision_id=?
                """,
                (revision_id,),
            )
        outcome = bridge.run_once(worker)

        assert outcome.status is WorkerRunStatus.FAILED
        assert outcome.failure is not None
        assert outcome.failure.status.value == "needs_attention"
        unit = store._connection.execute(
            """
            SELECT state, error_class, attempt_count
            FROM units
            WHERE owner_user_id='owner-f7' AND revision_id=?
              AND state='needs_attention'
            """,
            (revision_id,),
        ).fetchone()
        assert tuple(unit) == ("needs_attention", "budget_exhausted", 1)
        usage = store._connection.execute(
            """
            SELECT input_tokens, output_tokens, usage_unknown
            FROM revision_usage
            WHERE owner_user_id='owner-f7' AND revision_id=?
            """,
            (revision_id,),
        ).fetchone()
        assert tuple(usage) == (1, 0, 0)
        assert calls == []
        assert store.get_workload(
            "owner-f7", workload_id,
        ).state is WorkloadState.NEEDS_ATTENTION
        assert bridge.run_once(worker).status is WorkerRunStatus.IDLE


def test_unknown_paid_provider_cost_fails_closed_before_admission(tmp_path):
    resolver = _Resolver()
    resolver.reduce = _contract(
        "workload",
        "entries.describe",
        "metnos.test-reduce/1",
        inputs=("entries",),
        input_types=(("entries", "array"),),
        model=True,
        model_provider="paid-provider",
    )
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        with pytest.raises(CompilationError, match="preauthorized cost bound"):
            _admit(store, resolver)
        assert store._connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE owner_user_id='owner-f7'"
        ).fetchone()[0] == 0


def test_usage_persistence_failure_is_not_reported_as_success(
    tmp_path,
    monkeypatch,
):
    resolver = _Resolver()
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        _workload_id, revision_id = _admit(store, resolver)

        def invoke_workload(_name, _args, _context):
            record(
                provider="llamacpp",
                result=SimpleNamespace(in_tokens=1, out_tokens=1, latency_ms=1),
            )
            return {"summary": "must not be committed"}

        bridge = _budget_bridge(store, resolver, invoke_workload)
        worker = _worker(store, resolver)
        assert bridge.run_once(worker).status is WorkerRunStatus.COMMITTED

        def reject_usage(*_args, **_kwargs):
            raise DurableStoreError("synthetic accounting failure")

        monkeypatch.setattr(store, "record_attempt_usage", reject_usage)
        outcome = bridge.run_once(worker)

        assert outcome.status is WorkerRunStatus.FAILED
        assert outcome.failure is not None
        assert outcome.failure.status.value == "needs_attention"
        row = store._connection.execute(
            """
            SELECT state, error_class FROM units
            WHERE owner_user_id='owner-f7' AND revision_id=?
              AND state='needs_attention'
            """,
            (revision_id,),
        ).fetchone()
        assert tuple(row) == ("needs_attention", "budget_exhausted")
        usage = store._connection.execute(
            "SELECT usage_unknown FROM revision_usage "
            "WHERE owner_user_id='owner-f7' AND revision_id=?",
            (revision_id,),
        ).fetchone()
        assert usage["usage_unknown"] == 1


def test_expired_model_attempt_with_missing_usage_requires_attention(tmp_path):
    resolver = _Resolver()
    candidate = _pipeline()
    candidate["stages"][2]["retry"]["max_attempts"] = 2
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        workload_id, revision_id = _admit(store, resolver, candidate=candidate)
        bridge = _budget_bridge(
            store,
            resolver,
            lambda _name, _args, _context: {"summary": "unused"},
        )
        worker = _worker(store, resolver)
        assert bridge.run_once(worker).status is WorkerRunStatus.COMMITTED

        started = datetime.now(timezone.utc)
        lease = store.claim_next(
            "model-crash-worker",
            started,
            timedelta(seconds=10),
            worker.capabilities,
        )
        assert lease is not None
        assert store.mark_running(
            lease, now=started + timedelta(microseconds=1),
        ) is LeaseMutationStatus.APPLIED
        assert _record_model_facts(
            store,
            lease,
            resolver.reduce,
            now=started + timedelta(microseconds=2),
        ) is LeaseMutationStatus.APPLIED

        recovery = store.reconcile_expired(
            started + timedelta(seconds=11),
            batch_size=100,
        )

        assert recovery.expired == 1
        assert recovery.needs_attention == 1
        assert recovery.retry_scheduled == 0
        unit = store._connection.execute(
            "SELECT state, attempt_count FROM units "
            "WHERE owner_user_id='owner-f7' AND revision_id=? "
            "AND stage_id=?",
            (revision_id, lease.stage_id),
        ).fetchone()
        assert tuple(unit) == ("needs_attention", 1)
        usage = store._connection.execute(
            "SELECT usage_unknown FROM revision_usage "
            "WHERE owner_user_id='owner-f7' AND revision_id=?",
            (revision_id,),
        ).fetchone()
        assert usage["usage_unknown"] == 1
        metrics = json.loads(store._connection.execute(
            "SELECT metrics_json FROM attempts "
            "WHERE owner_user_id='owner-f7' AND id=?",
            (lease.attempt_id,),
        ).fetchone()["metrics_json"])
        assert metrics["usage_accounting_unknown"] is True
        assert store.get_workload(
            "owner-f7", workload_id,
        ).state is WorkloadState.NEEDS_ATTENTION
        assert store.claim_next(
            "model-retry-worker",
            started + timedelta(seconds=12),
            timedelta(seconds=10),
            worker.capabilities,
        ) is None


def test_expired_model_attempt_with_exact_usage_can_retry(tmp_path):
    resolver = _Resolver()
    candidate = _pipeline()
    candidate["stages"][2]["retry"]["max_attempts"] = 2
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        _workload_id, revision_id = _admit(
            store, resolver, candidate=candidate,
        )
        bridge = _budget_bridge(
            store,
            resolver,
            lambda _name, _args, _context: {"summary": "unused"},
        )
        worker = _worker(store, resolver)
        assert bridge.run_once(worker).status is WorkerRunStatus.COMMITTED

        started = datetime.now(timezone.utc)
        lease = store.claim_next(
            "model-retry-worker",
            started,
            timedelta(seconds=10),
            worker.capabilities,
        )
        assert lease is not None
        assert store.mark_running(
            lease, now=started + timedelta(microseconds=1),
        ) is LeaseMutationStatus.APPLIED
        assert _record_model_facts(
            store,
            lease,
            resolver.reduce,
            now=started + timedelta(microseconds=2),
        ) is LeaseMutationStatus.APPLIED
        sink = BoundedUsageSink()
        with attempt_context(
            workload_id=lease.workload_id,
            stage_id=lease.stage_id,
            unit_key=lease.unit_key,
            attempt_id=lease.attempt_id,
            sink=sink,
        ):
            record(
                provider="llamacpp",
                result=SimpleNamespace(
                    in_tokens=3,
                    out_tokens=2,
                    latency_ms=1,
                ),
            )
        assert store.record_attempt_usage(
            lease,
            sink.summary(),
            now=started + timedelta(microseconds=3),
        ) is LeaseMutationStatus.APPLIED

        recovery = store.reconcile_expired(
            started + timedelta(seconds=11),
            batch_size=100,
        )

        assert recovery.expired == 1
        assert recovery.retry_scheduled == 1
        assert recovery.needs_attention == 0
        usage = store._connection.execute(
            "SELECT input_tokens, output_tokens, usage_unknown "
            "FROM revision_usage "
            "WHERE owner_user_id='owner-f7' AND revision_id=?",
            (revision_id,),
        ).fetchone()
        assert tuple(usage) == (3, 2, 0)
        retry = store.claim_next(
            "model-retry-worker",
            started + timedelta(seconds=12),
            timedelta(seconds=10),
            worker.capabilities,
        )
        assert retry is not None
        assert retry.unit_id == lease.unit_id
        assert retry.attempt_number == 2


def test_usage_aggregates_are_verified_and_accounted_once(tmp_path):
    resolver = _Resolver()
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        _workload_id, revision_id = _admit(store, resolver)
        bridge = _budget_bridge(
            store,
            resolver,
            lambda _name, _args, _context: {"summary": "unused"},
        )
        worker = _worker(store, resolver)
        assert bridge.run_once(worker).status is WorkerRunStatus.COMMITTED
        lease = worker.claim_next()
        assert lease is not None
        assert store.mark_running(lease) is LeaseMutationStatus.APPLIED
        assert _record_model_facts(
            store, lease, resolver.reduce,
        ) is LeaseMutationStatus.APPLIED

        sink = BoundedUsageSink()
        with attempt_context(
            workload_id=lease.workload_id,
            stage_id=lease.stage_id,
            unit_key=lease.unit_key,
            attempt_id=lease.attempt_id,
            sink=sink,
        ):
            record(
                provider="llamacpp",
                result=SimpleNamespace(in_tokens=3, out_tokens=2, latency_ms=1),
            )
        usage = sink.summary()
        inconsistent = dict(usage)
        inconsistent["input_tokens"] = 1
        with pytest.raises(DurableStoreError, match="aggregates are inconsistent"):
            store.record_attempt_usage(lease, inconsistent)

        wrong_binding = json.loads(json.dumps(usage))
        wrong_binding["records"][0]["provider"] = "other-provider"
        with pytest.raises(DurableStoreError, match="frozen contract"):
            store.record_attempt_usage(lease, wrong_binding)

        oversized_output = json.loads(json.dumps(usage))
        oversized_output["records"][0]["out_tokens"] = 4_097
        oversized_output["output_tokens"] = 4_097
        with pytest.raises(DurableStoreError, match="frozen token limit"):
            store.record_attempt_usage(lease, oversized_output)

        oversized_input = json.loads(json.dumps(usage))
        oversized_input["records"][0]["in_tokens"] = 4_097
        oversized_input["input_tokens"] = 4_097
        with pytest.raises(DurableStoreError, match="frozen token limit"):
            store.record_attempt_usage(lease, oversized_input)

        assert store.record_attempt_usage(
            lease, usage,
        ) is LeaseMutationStatus.APPLIED
        assert store.record_attempt_usage(
            lease, usage,
        ) is LeaseMutationStatus.ALREADY_APPLIED
        row = store._connection.execute(
            """
            SELECT input_tokens, output_tokens, cost_micros
            FROM revision_usage
            WHERE owner_user_id='owner-f7' AND revision_id=?
            """,
            (revision_id,),
        ).fetchone()
        assert tuple(row) == (3, 2, 0)


def test_output_byte_budget_rolls_back_the_rejected_result(tmp_path):
    resolver = _Resolver()
    candidate = _pipeline(with_reduce=False)
    candidate["budgets"]["max_bytes_written"] = 0
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        _workload_id, revision_id = _admit(store, resolver, candidate=candidate)
        bridge = _budget_bridge(
            store,
            resolver,
            lambda _name, _args, _context: {"summary": "unused"},
        )
        outcome = bridge.run_once(_worker(store, resolver))

        assert outcome.status is WorkerRunStatus.FAILED
        assert outcome.failure is not None
        assert outcome.failure.status.value == "needs_attention"
        assert store._connection.execute(
            """
            SELECT COUNT(*) FROM results
            WHERE owner_user_id='owner-f7' AND revision_id=?
            """,
            (revision_id,),
        ).fetchone()[0] == 0
        usage = store._connection.execute(
            """
            SELECT output_bytes FROM revision_usage
            WHERE owner_user_id='owner-f7' AND revision_id=?
            """,
            (revision_id,),
        ).fetchone()
        assert usage["output_bytes"] == 0


def test_execution_input_bytes_are_accounted_once_per_attempt(tmp_path):
    resolver = _Resolver()
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        _workload_id, revision_id = _admit(
            store,
            resolver,
            candidate=_pipeline(with_reduce=False),
        )
        lease = _worker(store, resolver).claim_next()
        assert lease is not None

        first = store.execution_inputs(lease)
        second = store.execution_inputs(lease)

        assert first == second
        usage = store._connection.execute(
            """
            SELECT input_bytes FROM revision_usage
            WHERE owner_user_id='owner-f7' AND revision_id=?
            """,
            (revision_id,),
        ).fetchone()
        assert usage["input_bytes"] == 1024


def test_expired_wall_budget_becomes_explicit_attention(tmp_path):
    resolver = _Resolver()
    candidate = _pipeline(with_reduce=False)
    candidate["budgets"]["max_wall_time_s"] = 1
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        workload_id, revision_id = _admit(store, resolver, candidate=candidate)
        lease = _worker(store, resolver).claim_next()
        assert lease is not None
        store._connection.execute(
            """
            UPDATE revision_usage
            SET started_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-2 seconds')
            WHERE owner_user_id='owner-f7' AND revision_id=?
            """,
            (revision_id,),
        )

        reason = store.budget_violation(lease)
        settled = store.settle_workload("owner-f7", workload_id)

        assert reason is not None
        assert reason["budget"] == "max_wall_time_s"
        assert settled.state is WorkloadState.NEEDS_ATTENTION
        assert _worker(store, resolver).claim_next() is None
