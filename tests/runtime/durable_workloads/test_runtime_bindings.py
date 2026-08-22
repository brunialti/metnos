from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from durable_workloads.admission import admit_candidate
from durable_workloads.compiler import (
    ApprovedOutputSchema,
    FrozenRunnerContract,
    OutputSchemaRegistry,
    core_output_schemas,
)
from durable_workloads.inventory import InventoryLimits
from durable_workloads.models import RunnerKind, SourceResolution, WorkloadState
from durable_workloads.runtime_bindings import (
    BoundExecutionBridge,
    RuntimeFactory,
    RuntimeRegistration,
    RuntimeRegistry,
)
from durable_runtime_registry import default_runtime_registry
from durable_workloads.service import default_service
from durable_workloads.source_authority import SourceAuthority
from durable_workloads.storage import DurableWorkloadStore, StoreNotReadyError
from helpers import plan


_DIGEST = "sha256:" + "a" * 64
_RESOURCE_ENV = (
    "METNOS_DURABLE_RESOURCE_CPU",
    "METNOS_DURABLE_RESOURCE_DEVICE",
    "METNOS_DURABLE_RESOURCE_LLM",
    "METNOS_DURABLE_RESOURCE_LOCAL_IO",
    "METNOS_DURABLE_RESOURCE_NETWORK_IO",
    "METNOS_DURABLE_RESOURCE_VLM",
)


class _Runners:
    def __init__(self, contracts):
        self._contracts = {
            (contract.kind, contract.name): contract for contract in contracts
        }

    def resolve(self, kind, name):
        return self._contracts[(kind, name)]

    def attest_executor(self, name, executor):
        assert executor is not None
        return self.resolve("executor", name)


def _contract(
    kind: str,
    name: str,
    schema_name: str,
    *,
    input_names=(),
    input_types=(),
    required_input_names=(),
) -> FrozenRunnerContract:
    return FrozenRunnerContract(
        kind=kind,
        name=name,
        contract_digest=_DIGEST,
        implementation_digest=_DIGEST,
        allowed_effects=("pure",),
        input_names=tuple(input_names),
        output_schema_names=(schema_name,),
        required_input_names=tuple(required_input_names),
        input_types=tuple(input_types),
    )


def _registration(
    name: str,
    *,
    runner_name: str,
    schema_name: str,
    kind: str = "executor",
    invoker=None,
    candidate_plan_factory=None,
) -> RuntimeRegistration:
    schema = ApprovedOutputSchema.create(
        schema_name,
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    contract = _contract(kind, runner_name, schema_name)
    return RuntimeRegistration(
        name=name,
        runner_bindings=((kind, runner_name),),
        runners=_Runners((contract,)),
        output_schemas=OutputSchemaRegistry((schema,)),
        output_schema_names=(schema_name,),
        workload_invoker=invoker,
        candidate_plan_factory=candidate_plan_factory,
    )


def test_default_registry_exposes_generic_core_and_registered_capabilities():
    registry = default_runtime_registry()
    assert default_runtime_registry() is registry
    capabilities = registry.capabilities({
        "cpu": 1,
        "local_io": 1,
        "network_io": 1,
        "llm": 1,
        "vlm": 1,
        "device": 1,
    })
    bindings = set(capabilities.runner_bindings)

    assert (RunnerKind.INTERNAL, "sealed_inventory") in bindings
    assert (RunnerKind.INTERNAL, "schema_and_coverage_validator") in bindings
    assert (RunnerKind.INTERNAL, "artifact_store_publish") in bindings
    assert (RunnerKind.EXECUTOR, "read_files_ocr") in bindings
    assert (RunnerKind.WORKLOAD, "durable.images.answer") in bindings
    assert registry.output_schemas.resolve(
        "metnos.inventory-seal/1"
    ).name == "metnos.inventory-seal/1"
    assert registry.admission_names == ("images.questions.v1",)

    first = registry.candidate_plan("images.questions.v1")
    first["objective_redacted"] = "mutated outside the registry"
    assert registry.candidate_plan(
        "images.questions.v1"
    )["objective_redacted"] != first["objective_redacted"]


def test_registry_validates_candidate_plans_and_their_exact_identity():
    invalid = _registration(
        "tests.invalid-plan.v1",
        runner_name="runner_invalid_plan",
        schema_name="tests.invalid-plan/1",
        candidate_plan_factory=lambda: {},
    )
    with pytest.raises(ValueError, match="candidate plan declaration"):
        RuntimeRegistry((invalid,))

    candidate = plan()
    mismatch = _registration(
        "tests.mismatched-plan.v1",
        runner_name="runner_mismatched_plan",
        schema_name="tests.mismatched-plan/1",
        candidate_plan_factory=lambda: candidate,
    )
    with pytest.raises(ValueError, match="identity does not match"):
        RuntimeRegistry((mismatch,))


def test_registry_returns_only_declared_candidate_plans():
    candidate = plan()
    candidate["plan_id"] = "tests.admitted-plan.v1"
    registered = _registration(
        candidate["plan_id"],
        runner_name="runner_admitted_plan",
        schema_name="tests.admitted-plan/1",
        candidate_plan_factory=lambda: candidate,
    )
    registry = RuntimeRegistry((registered,))

    assert registry.admission_names == (candidate["plan_id"],)
    assert registry.candidate_plan(candidate["plan_id"]) == candidate
    with pytest.raises(LookupError, match="not registered"):
        registry.candidate_plan("tests.unknown-plan.v1")


def test_core_runtime_composition_does_not_import_a_task_domain():
    root = Path(__file__).resolve().parents[3] / "runtime" / "durable_workloads"
    for path in root.glob("*.py"):
        if path.name == "image_preset.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert "image_preset" not in source
        assert "read_files_ocr" not in source


def test_package_can_reuse_a_core_schema_without_redeclaring_its_authority():
    schemas = core_output_schemas()
    contract = _contract(
        "executor", "runner_core_schema", "metnos.inventory-seal/1",
    )
    registration = RuntimeRegistration(
        name="tests.core-schema.v1",
        runner_bindings=(("executor", "runner_core_schema"),),
        runners=_Runners((contract,)),
        output_schemas=schemas,
        output_schema_names=(),
    )

    registry = RuntimeRegistry((registration,))
    assert registry.runners.resolve(
        "executor", "runner_core_schema",
    ) is contract
    assert registry.output_schemas.resolve(
        "metnos.inventory-seal/1"
    ).digest == schemas.resolve("metnos.inventory-seal/1").digest


def test_registry_dispatches_workloads_by_registration_without_preset_branches():
    seen = []

    def invoke(name, arguments, context):
        seen.append((name, dict(arguments), context))
        return {"selected": name}

    schema_name = "tests.runtime-workloads/1"
    schema = ApprovedOutputSchema.create(
        schema_name,
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    names = ("workload.alpha", "workload.beta")
    registration = RuntimeRegistration(
        name="tests.workloads.v1",
        runner_bindings=tuple(("workload", name) for name in names),
        runners=_Runners(tuple(
            _contract("workload", name, schema_name) for name in names
        )),
        output_schemas=OutputSchemaRegistry((schema,)),
        output_schema_names=(schema_name,),
        workload_invoker=invoke,
    )
    registry = RuntimeRegistry((registration,))
    context = object()

    assert registry.invoke_workload("workload.alpha", {"value": 1}, context) == {
        "selected": "workload.alpha",
    }
    assert registry.invoke_workload("workload.beta", {"value": 2}, context) == {
        "selected": "workload.beta",
    }
    assert [item[0] for item in seen] == list(names)


def test_registry_rejects_duplicate_and_false_authority_declarations():
    first = _registration(
        "tests.first.v1",
        runner_name="runner_a",
        schema_name="tests.schema-a/1",
    )
    duplicate_runner = _registration(
        "tests.second.v1",
        runner_name="runner_a",
        schema_name="tests.schema-b/1",
    )
    with pytest.raises(ValueError, match="duplicate runtime runner authority"):
        RuntimeRegistry((first, duplicate_runner))

    duplicate_schema = _registration(
        "tests.third.v1",
        runner_name="runner_b",
        schema_name="tests.schema-a/1",
    )
    with pytest.raises(ValueError, match="duplicate runtime output schema authority"):
        RuntimeRegistry((first, duplicate_schema))

    false_contract = _registration(
        "tests.false.v1",
        runner_name="runner_c",
        schema_name="tests.schema-c/1",
    )
    false_contract.runners._contracts[("executor", "runner_c")] = _contract(
        "executor", "runner_d", "tests.schema-c/1",
    )
    with pytest.raises(ValueError, match="another identity"):
        RuntimeRegistry((false_contract,))


def test_bound_bridge_closes_every_resource_once_in_reverse_order():
    closed = []

    class Resource:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)

    bridge = BoundExecutionBridge(
        SimpleNamespace(run_once=lambda worker: worker),
        (Resource("first"), Resource("second")),
    )

    assert bridge.run_once("worker") == "worker"
    bridge.close()
    bridge.close()
    assert closed == ["second", "first"]
    with pytest.raises(RuntimeError, match="closed"):
        bridge.run_once("worker")

    with pytest.raises(ValueError, match="finite"):
        BoundExecutionBridge(
            SimpleNamespace(run_once=lambda worker: worker),
            (),
            maintenance_interval_s=float("nan"),
        )


def test_bound_bridge_closes_remaining_resources_after_baseexception():
    closed = []

    class InjectedInterrupt(BaseException):
        pass

    class Resource:
        def __init__(self, name, interrupt=False):
            self.name = name
            self.interrupt = interrupt

        def close(self):
            closed.append(self.name)
            if self.interrupt:
                raise InjectedInterrupt()

    bridge = BoundExecutionBridge(
        SimpleNamespace(run_once=lambda worker: worker),
        (Resource("first"), Resource("second", interrupt=True)),
    )
    with pytest.raises(InjectedInterrupt):
        bridge.close()
    assert closed == ["second", "first"]


def test_runtime_factory_builds_unique_workers_and_owned_lane_resources(
    tmp_path,
    monkeypatch,
):
    for name in _RESOURCE_ENV:
        monkeypatch.delenv(name, raising=False)
    registry = RuntimeRegistry((_registration(
        "tests.factory.v1",
        runner_name="runner_factory",
        schema_name="tests.factory-schema/1",
    ),))
    factory = RuntimeFactory(
        registry_factory=lambda: registry,
        source_authority_path=tmp_path / "private" / "authority.sqlite3",
        artifact_root=tmp_path / "artifacts",
    )

    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        first = factory.worker(store)
        second = factory.worker(store)
        assert first.worker_id != second.worker_id
        assert first.capabilities.resource_map() == {
            "cpu": 1,
            "local_io": 1,
            "network_io": 1,
            "llm": 1,
            "vlm": 1,
            "device": 1,
        }

        bridge = factory.bridge(store)
        authority, artifacts = bridge._resources
        bridge.close()
        bridge.close()
        assert authority._closed is True
        assert artifacts._repository._owns_connection is False


def test_runtime_factory_executes_an_admitted_generic_source_workload(tmp_path):
    schema_name = "tests.factory-source/1"
    schema = ApprovedOutputSchema.create(
        schema_name,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 1,
                },
            },
            "required": ["entries"],
        },
    )
    contract = _contract(
        "workload",
        "workload.read_source",
        schema_name,
        input_names=("paths",),
        input_types=(("paths", "array"),),
        required_input_names=("paths",),
    )
    original = tmp_path / "input" / "source.txt"
    original.parent.mkdir()
    original.write_text("sealed payload", encoding="utf-8")
    observed_paths = []

    def invoke(_name, arguments, _context):
        selected = arguments["paths"][0]
        observed_paths.append(selected)
        return {"entries": [Path(selected).read_text(encoding="utf-8")]}

    registration = RuntimeRegistration(
        name="tests.factory-source.v1",
        runner_bindings=(("workload", "workload.read_source"),),
        runners=_Runners((contract,)),
        output_schemas=OutputSchemaRegistry((schema,)),
        output_schema_names=(schema_name,),
        workload_invoker=invoke,
    )
    registry = RuntimeRegistry((registration,))
    authority_path = tmp_path / "private" / "authority.sqlite3"
    database_path = tmp_path / "state.sqlite3"
    with DurableWorkloadStore.open(database_path) as store:
        draft = store.create_draft(
            "owner-a",
            "runtime-factory-source",
            redacted_request={"summary": "fixture"},
        )
        with SourceAuthority.open(authority_path) as authority:
            inventory = authority.seal_and_register(
                [original],
                owner_user_id="owner-a",
                workload_id=draft.workload_id,
                device_id="server",
                limits=InventoryLimits(
                    max_sources=1,
                    max_total_bytes=1024,
                    max_depth=1,
                ),
                valid_until=datetime.now(timezone.utc) + timedelta(days=1),
            )
        candidate = plan(with_map=True)
        candidate["stages"][1]["runner"] = {
            "kind": "workload", "name": "workload.read_source",
        }
        candidate["stages"][1]["output_schema"]["name"] = schema_name
        admit_candidate(
            store,
            "owner-a",
            draft.workload_id,
            candidate,
            inventory,
            expected_version=draft.version,
            runners=registry.runners,
            output_schemas=registry.output_schemas,
            usage_complete=True,
        )
        current = store.get_workload("owner-a", draft.workload_id)
        store.transition_workload(
            "owner-a",
            draft.workload_id,
            WorkloadState.QUEUED,
            expected_version=current.version,
        )

        factory = RuntimeFactory(
            registry_factory=lambda: registry,
            source_authority_path=authority_path,
            artifact_root=tmp_path / "artifacts",
        )
        worker = factory.worker(store)
        bridge = factory.bridge(store)
        try:
            bridge.run_once(worker)
            bridge._next_maintenance = 0.0
            bridge.run_once(worker)
        finally:
            bridge.close()

        assert store.get_workload(
            "owner-a", draft.workload_id,
        ).state is WorkloadState.COMPLETED
        assert len(observed_paths) == 1
        assert observed_paths[0] != str(original)
        assert not Path(observed_paths[0]).exists()
        with SourceAuthority.open(authority_path) as authority:
            assert authority._connection.execute(
                "SELECT COUNT(*) FROM source_grants"
            ).fetchone()[0] == 0


def test_runtime_factory_uses_explicit_remote_source_authority(tmp_path):
    schema_name = "tests.factory-remote-source/1"
    schema = ApprovedOutputSchema.create(
        schema_name,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 1,
                },
            },
            "required": ["entries"],
        },
    )
    contract = _contract(
        "workload",
        "workload.read_remote_source",
        schema_name,
        input_names=("paths",),
        input_types=(("paths", "array"),),
        required_input_names=("paths",),
    )
    remote_path = tmp_path / "remote-device" / "source.txt"
    remote_path.parent.mkdir()
    remote_path.write_text("remote payload", encoding="utf-8")
    observed = []

    def invoke(_name, arguments, _context):
        selected = arguments["paths"][0]
        return {"entries": [Path(selected).read_text(encoding="utf-8")]}

    def attest(device_id, locator, source, context):
        observed.append((device_id, locator, context.owner_user_id))
        return SourceResolution(
            value=locator,
            source_id=source["source_id"],
            device_id=device_id,
            content_digest=source["content_digest"],
            size_bytes=source["size_bytes"],
            mtime_ns=source["mtime_ns"],
            authority="remote-device-test-v1",
        )

    registration = RuntimeRegistration(
        name="tests.factory-remote-source.v1",
        runner_bindings=(("workload", "workload.read_remote_source"),),
        runners=_Runners((contract,)),
        output_schemas=OutputSchemaRegistry((schema,)),
        output_schema_names=(schema_name,),
        workload_invoker=invoke,
    )
    registry = RuntimeRegistry((registration,))
    authority_path = tmp_path / "private" / "authority.sqlite3"
    database_path = tmp_path / "state.sqlite3"
    with DurableWorkloadStore.open(database_path) as store:
        draft = store.create_draft(
            "owner-a",
            "runtime-factory-remote-source",
            redacted_request={"summary": "fixture"},
        )
        with SourceAuthority.open(authority_path) as authority:
            sealed = authority.seal_and_register(
                [remote_path],
                owner_user_id="owner-a",
                workload_id=draft.workload_id,
                device_id="device-a",
                limits=InventoryLimits(
                    max_sources=1,
                    max_total_bytes=1024,
                    max_depth=1,
                ),
                valid_until=datetime.now(timezone.utc) + timedelta(days=1),
            )
        candidate = plan(with_map=True)
        candidate["stages"][1]["runner"] = {
            "kind": "workload", "name": "workload.read_remote_source",
        }
        candidate["stages"][1]["output_schema"]["name"] = schema_name
        admit_candidate(
            store,
            "owner-a",
            draft.workload_id,
            candidate,
            sealed,
            expected_version=draft.version,
            runners=registry.runners,
            output_schemas=registry.output_schemas,
            usage_complete=True,
        )
        current = store.get_workload("owner-a", draft.workload_id)
        store.transition_workload(
            "owner-a",
            draft.workload_id,
            WorkloadState.QUEUED,
            expected_version=current.version,
        )

        factory = RuntimeFactory(
            registry_factory=lambda: registry,
            source_authority_path=authority_path,
            remote_attestor=attest,
            artifact_root=tmp_path / "artifacts",
        )
        bridge = factory.bridge(store)
        try:
            assert bridge.run_once(factory.worker(store)).status.value == "committed"
        finally:
            bridge.close()

        assert store.get_workload(
            "owner-a", draft.workload_id,
        ).state is WorkloadState.COMPLETED
        assert observed == [("device-a", str(remote_path), "owner-a")]


def test_runtime_factory_rejects_memory_store_and_invalid_resource_limit(
    monkeypatch,
):
    registry = RuntimeRegistry((_registration(
        "tests.memory.v1",
        runner_name="runner_memory",
        schema_name="tests.memory-schema/1",
    ),))
    factory = RuntimeFactory(registry_factory=lambda: registry)
    with DurableWorkloadStore.open(":memory:") as store:
        with pytest.raises(StoreNotReadyError, match="file-backed"):
            factory.bridge(store)
        monkeypatch.setenv("METNOS_DURABLE_RESOURCE_CPU", "65")
        with pytest.raises(ValueError, match="outside the supported range"):
            factory.worker(store)


def test_default_service_composes_bindings_only_when_gate_is_enabled(
    monkeypatch,
):
    import durable_runtime_registry as bindings

    def unavailable():
        raise AssertionError("production bindings must remain lazy while disabled")

    monkeypatch.setattr(bindings, "production_factories", unavailable)
    monkeypatch.setenv("METNOS_DURABLE_WORKLOADS_ENABLED", "0")
    dormant = default_service()
    assert dormant.enabled is False
    assert dormant._worker_factory is None
    assert dormant._bridge_factory is None

    worker_factory = lambda store: store
    bridge_factory = lambda store: store
    monkeypatch.setattr(
        bindings,
        "production_factories",
        lambda: (worker_factory, bridge_factory),
    )
    monkeypatch.setenv("METNOS_DURABLE_WORKLOADS_ENABLED", "1")
    enabled = default_service()
    assert enabled.enabled is True
    assert enabled._worker_factory is worker_factory
    assert enabled._bridge_factory is bridge_factory
