"""F7 integration proofs for the generic durable execution bridge."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from durable_workloads.admission import admit_candidate
from durable_workloads.compiler import (
    ApprovedOutputSchema,
    FrozenRunnerContract,
    OutputSchemaRegistry,
)
from durable_workloads.coordinator import LeaseMutationStatus, WorkerCapabilities
from durable_workloads.execution import DurableExecutionBridge
from durable_workloads.models import DurableEffect, RunnerKind, UnitState, WorkloadState
from durable_workloads.schema import MAX_SNAPSHOT_JSON_BYTES, digest_json
from durable_workloads.storage import DurableStoreError, DurableWorkloadStore
from durable_workloads.worker import DurableWorker, WorkerRunStatus
from helpers import inventory, plan, source
from llm_telemetry import record


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
        transport="llm-gateway" if model else "local-subprocess",
        intelligence="model" if model else "deterministic",
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
            "reduction.fan_in",
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


def _admit(store: DurableWorkloadStore, resolver: _Resolver, *, candidate=None):
    draft = store.create_draft(
        "owner-f7", "f7-generic-map-reduce", redacted_request={"summary": "fixture"},
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
                provider="fixture",
                model="fixture-model",
                result=SimpleNamespace(in_tokens=7, out_tokens=3, latency_ms=4),
            )
            return {"summary": "ok"}

        bridge = DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda item: f"/authorized/{item['source_id']}.png",
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
        assert all(item["executor_snapshot_digest"].startswith("sha256:") for item in provenance)
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


def test_changed_frozen_runner_contract_fails_before_invocation(tmp_path):
    original = _Resolver(version="v1")
    changed = _Resolver(version="v2")
    with DurableWorkloadStore.open(tmp_path / "durable" / "state.sqlite3") as store:
        workload_id, _revision_id = _admit(store, original, candidate=_pipeline(with_reduce=False))
        bridge = DurableExecutionBridge(
            store,
            runners=changed,
            output_schemas=_schemas(),
            source_resolver=lambda _item: "/authorized/source.png",
            executor_loader=lambda _name: (_ for _ in ()).throw(AssertionError("must not invoke")),
        )
        outcome = bridge.run_once(_worker(store, changed))
        assert outcome.status is WorkerRunStatus.FAILED
        unit = store._connection.execute(
            "SELECT state, error_class FROM units WHERE owner_user_id='owner-f7'"
        ).fetchone()
        assert tuple(unit) == (UnitState.FAILED_PERMANENT.value, "contract_violation")
        assert store.get_workload("owner-f7", workload_id).state is WorkloadState.RUNNING


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
            source_resolver=lambda _item: "/authorized/source.png",
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
            provider="fixture",
            result=SimpleNamespace(in_tokens=1, out_tokens=1, latency_ms=1),
        )
        return {"summary": "ok"}

    def bridge_for(store):
        return DurableExecutionBridge(
            store,
            runners=resolver,
            output_schemas=_schemas(),
            source_resolver=lambda _item: "/authorized/source.png",
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
            source_resolver=lambda _item: "/authorized/source.png",
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
            source_resolver=lambda _item: "/authorized/source.png",
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
