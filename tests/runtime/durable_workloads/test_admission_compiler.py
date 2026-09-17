from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from durable_workloads.admission import admit_candidate
from durable_workloads.compiler import (
    ApprovedOutputSchema,
    CompilationError,
    FrozenRunnerContract,
    OutputSchemaRegistry,
    RegisteredWorkloadResolver,
    VerifiedCatalogResolver,
    _semantic_schema,
    affected_stages,
    compile_plan,
    invalidated_stages,
)
from durable_workloads.schema import MAX_SNAPSHOT_JSON_BYTES, SchemaValidationError, digest_json
from helpers import inventory, plan, source


def _digest(label: str) -> str:
    return digest_json("f5-test", {"label": label}, max_bytes=MAX_SNAPSHOT_JSON_BYTES)


def _schemas() -> OutputSchemaRegistry:
    return OutputSchemaRegistry((
        ApprovedOutputSchema.create(
            "metnos.inventory-seal/1",
            {
                "type": "object",
                "properties": {
                    "digest": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["digest", "sources"],
                "additionalProperties": False,
            },
        ),
        ApprovedOutputSchema.create(
            "metnos.test-map/1",
            {
                "type": "object",
                "properties": {
                    "entries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "entry_id": {"type": "string"},
                                "text": {"type": "string"},
                            },
                        },
                    },
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
    suffix: str = "v1",
    inputs: tuple[str, ...] = (),
    effect: str = "pure",
    model: str | None = None,
    prompt: str | None = None,
    model_cost_policy: str = "zero",
    input_types: tuple[tuple[str, str], ...] = (),
) -> FrozenRunnerContract:
    return FrozenRunnerContract(
        kind=kind,
        name=name,
        contract_digest=_digest(f"contract:{name}:{suffix}"),
        implementation_digest=_digest(f"implementation:{name}:{suffix}"),
        allowed_effects=(effect,),
        input_names=inputs,
        output_schema_names=(schema_name,),
        input_types=input_types,
        model_binding_digest=_digest(f"model:{model}") if model else None,
        prompt_digest=_digest(f"prompt:{prompt}") if prompt else None,
        prompt_language="it" if model else None,
        model_provider="fixture" if model else None,
        model_digest=(
            "sha256:" + hashlib.sha256(b"fixture-model").hexdigest()
            if model else None
        ),
        model_tier="wise" if model else None,
        model_kind="chat" if model else None,
        model_max_calls=1 if model else None,
        model_max_input_tokens=4_096 if model else None,
        model_max_output_tokens=4_096 if model else None,
        model_cost_policy=model_cost_policy if model else None,
        transport="local-subprocess" if kind == "executor" else "llm-gateway",
        intelligence="deterministic" if kind == "executor" else "model",
    )


class _Resolver:
    def __init__(self, *, map_suffix: str = "v1", model: str = "v1", prompt: str = "v1"):
        self.map = _contract(
            "executor", "read_files_ocr", "metnos.test-map/1",
            suffix=map_suffix, inputs=("files", "paths"),
            input_types=(("files", "array"), ("paths", "array")),
        )
        self.reduce = _contract(
            "workload", "entries.describe", "metnos.test-reduce/1",
            inputs=("entries",), input_types=(("entries", "array"),),
            model=model, prompt=prompt,
        )

    def resolve(self, kind: str, name: str) -> FrozenRunnerContract:
        if (kind, name) == ("executor", "read_files_ocr"):
            return self.map
        if (kind, name) == ("workload", "entries.describe"):
            return self.reduce
        raise CompilationError(f"runner is unknown: {kind}:{name}")


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
            "dependencies.digest",
            "runner.contract_digest",
            "model_binding.digest",
            "prompt.digest",
            "reduction.order",
        ],
        "resources": {
            "cpu": 0,
            "local_io": 0,
            "network_io": 0,
            "llm": 1,
            "vlm": 0,
            "device": 0,
        },
        "required": True,
    }


def _pipeline() -> dict:
    candidate = plan(with_map=True)
    candidate["stages"].append(_reduce_stage())
    return candidate


def _compiled(*, resolver: _Resolver | None = None, candidate: dict | None = None):
    return compile_plan(
        candidate or _pipeline(),
        inventory([source(0)]),
        runners=resolver or _Resolver(),
        output_schemas=_schemas(),
    )


def test_same_fixture_is_byte_identical_in_two_processes():
    root = Path(__file__).resolve().parents[3]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((
        str(root / "runtime"),
        str(Path(__file__).resolve().parent),
    ))
    command = [
        sys.executable,
        "-c",
        "from f5_process_fixture import process_bytes; print(process_bytes())",
    ]
    first = subprocess.check_output(command, env=environment)
    second = subprocess.check_output(command, env=environment)
    assert first == second


def test_unknown_plan_fields_and_cycle_fail_before_contract_resolution():
    unknown = _pipeline()
    unknown["tier"] = "creative"
    with pytest.raises(SchemaValidationError, match="unknown fields"):
        _compiled(candidate=unknown)

    cyclic = _pipeline()
    cyclic["stages"][1]["depends_on"] = ["reduce"]
    with pytest.raises(SchemaValidationError, match="cycle"):
        _compiled(candidate=cyclic)


def test_virtual_inventory_stage_does_not_require_a_giant_inline_inventory(
    monkeypatch,
):
    import durable_workloads.schema as schema

    monkeypatch.setattr(schema, "MAX_INVENTORY_JSON_BYTES", 2_048)
    selected_inventory = inventory([source(index) for index in range(20)])
    compiled = compile_plan(
        plan(with_map=True), selected_inventory,
        runners=_Resolver(), output_schemas=_schemas(),
    )
    assert len(compiled.graph["stages"]) == 2

    invalid = plan(with_map=True)
    invalid["stages"][1]["input_bindings"] = {
        "paths": {"ref": "revision.inventory"},
    }
    with pytest.raises(CompilationError, match="bounded inline"):
        compile_plan(
            invalid, selected_inventory,
            runners=_Resolver(), output_schemas=_schemas(),
        )


def test_unknown_or_untrusted_executor_fails_closed():
    class Missing:
        def resolve(self, _kind, _name):
            raise CompilationError("absent")

    with pytest.raises(CompilationError, match="absent"):
        compile_plan(
            plan(with_map=True), inventory([source(0)]),
            runners=Missing(), output_schemas=_schemas(),
        )

    executor = SimpleNamespace(
        signed_by="",
        lifecycle="active",
        dormant=False,
        digest="sha256:" + "a" * 64,
        args_schema={"type": "object", "properties": {"paths": {"type": "array"}}},
    )
    catalog = SimpleNamespace(get=lambda _name: executor)
    resolver = VerifiedCatalogResolver(
        catalog_loader=lambda **_kwargs: catalog,
        durable_effects={"read_files_ocr": ("pure",)},
        durable_output_schemas={"read_files_ocr": ("metnos.test-map/1",)},
    )
    with pytest.raises(CompilationError, match="not signed"):
        resolver.resolve("executor", "read_files_ocr")


def test_preexercise_executor_never_persists_a_durable_revision_or_memo(tmp_path):
    from durable_workloads.storage import DurableWorkloadStore

    executor = SimpleNamespace(
        name="read_files_ocr", signed_by="test-authority",
        lifecycle="preexercise", dormant=False,
        digest="sha256:" + "a" * 64, version="1.0.0",
        args_schema={
            "type": "object", "properties": {"paths": {"type": "array"}},
        },
        capabilities=(), placement={}, transport="local-subprocess",
        intelligence="deterministic", execution_policy_declared=False,
    )
    resolver = VerifiedCatalogResolver(
        catalog_loader=lambda **_kwargs: SimpleNamespace(get=lambda _name: executor),
        durable_effects={"read_files_ocr": ("pure",)},
        durable_output_schemas={"read_files_ocr": ("metnos.test-map/1",)},
    )
    with DurableWorkloadStore.open(tmp_path / "durable.sqlite") as store:
        draft = store.create_draft(
            "owner-preexercise", "preexercise-never-durable",
            redacted_request={"summary": "fixture"},
        )
        with pytest.raises(CompilationError, match="not active"):
            admit_candidate(
                store, "owner-preexercise", draft.workload_id,
                _pipeline(), inventory([source(0)]),
                expected_version=draft.version, runners=resolver,
                output_schemas=_schemas(),
            )
        assert store._connection.execute(
            "SELECT COUNT(*) FROM revisions"
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT COUNT(*) FROM units"
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT COUNT(*) FROM attempts"
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT COUNT(*) FROM results"
        ).fetchone()[0] == 0


def test_verified_executor_scheduler_policy_is_frozen_into_its_contract():
    executor = SimpleNamespace(
        signed_by="test-authority",
        lifecycle="active",
        dormant=False,
        digest="sha256:" + "a" * 64,
        version="1.0.0",
        args_schema={
            "type": "object",
            "properties": {"paths": {"type": "array"}},
        },
        capabilities=(),
        placement={},
        transport="local-subprocess",
        intelligence="deterministic",
        execution_policy_declared=True,
        execution_policy={
            "effect": "read_only",
            "parallelism_class": 2,
            "resource_class": "local_io",
            "concurrency_key": "none",
            "equivalence_gate": "verified",
        },
    )
    catalog = SimpleNamespace(get=lambda _name: executor)
    resolver = VerifiedCatalogResolver(
        catalog_loader=lambda **_kwargs: catalog,
        durable_effects={"read_files_ocr": ("pure",)},
        durable_output_schemas={"read_files_ocr": ("metnos.test-map/1",)},
    )

    first = resolver.resolve("executor", "read_files_ocr")
    assert dict(first.execution_policy) == executor.execution_policy
    assert first.execution_policy_declared is True
    assert first.snapshot(
        stage_key="map",
        output_schema=_schemas().resolve("metnos.test-map/1"),
    )["execution_policy"] == executor.execution_policy

    executor.execution_policy = {**executor.execution_policy, "parallelism_class": 1}
    second = resolver.resolve("executor", "read_files_ocr")
    assert second.contract_digest != first.contract_digest


def test_inline_output_prose_is_not_an_approved_schema():
    with pytest.raises(CompilationError, match="not approved"):
        compile_plan(
            plan(with_map=True), inventory([source(0)]),
            runners=_Resolver(), output_schemas=OutputSchemaRegistry(()),
        )


def test_semantic_schema_preserves_property_names_and_literal_data():
    schema = {
        "type": "object", "title": "Translated heading",
        "properties": {
            "title": {"type": "string", "description": "Translated help"},
            "description": {"type": "object", "default": {"title": "literal"}},
            "examples": {"type": "array", "items": {
                "type": "object", "properties": {"title": {"type": "string"}},
                "const": {"description": "actual data"},
            }},
        },
    }
    semantic = _semantic_schema(schema)

    assert set(semantic["properties"]) == {"title", "description", "examples"}
    assert "title" not in semantic
    assert "description" not in semantic["properties"]["title"]
    assert semantic["properties"]["description"]["default"] == {"title": "literal"}
    item = semantic["properties"]["examples"]["items"]
    assert item["properties"] == {"title": {"type": "string"}}
    assert item["const"] == {"description": "actual data"}


def test_capability_scope_changes_invalidate_the_frozen_contract():
    executor = SimpleNamespace(
        signed_by="fixture-authority", lifecycle="active", dormant=False,
        digest="sha256:" + "a" * 64, version="1.0.0",
        args_schema={"type": "object", "properties": {"client": {"type": "string"}}},
        capabilities=({"name": "provider:access", "hint": ["provider-a"],
                       "when": {"arg": "client", "values": ["provider-a"]}},),
        placement={}, transport="local-subprocess", intelligence="deterministic",
    )
    resolver = VerifiedCatalogResolver(
        durable_effects={"read_fixture": ("pure",)},
        durable_output_schemas={"read_fixture": ("metnos.fixture/1",)},
    )
    before = resolver.attest_executor("read_fixture", executor)
    executor.capabilities = ({"name": "provider:access", "hint": ["provider-b"],
                              "when": {"arg": "client", "values": ["provider-b"]}},)
    after = resolver.attest_executor("read_fixture", executor)
    assert before.contract_digest != after.contract_digest


def test_capability_order_does_not_change_the_frozen_contract():
    executor = SimpleNamespace(
        signed_by="fixture-authority", lifecycle="active", dormant=False,
        digest="sha256:" + "a" * 64, version="1.0.0",
        args_schema={"type": "object", "properties": {}},
        capabilities=({"name": "files:read"}, {"name": "files:write"}),
    )
    resolver = VerifiedCatalogResolver(
        durable_effects={"read_fixture": ("pure",)},
        durable_output_schemas={"read_fixture": ("metnos.fixture/1",)},
    )
    before = resolver.attest_executor("read_fixture", executor)
    executor.capabilities = tuple(reversed(executor.capabilities))
    assert resolver.attest_executor("read_fixture", executor) == before


def test_missing_dependency_field_and_effect_authority_are_rejected():
    missing = _pipeline()
    missing["stages"][2]["input_bindings"]["entries"]["field"] = "missing"
    with pytest.raises(CompilationError, match="missing field"):
        _compiled(candidate=missing)

    unauthorized = _pipeline()
    unauthorized["stages"][1]["effect_profile"] = "idempotent"
    with pytest.raises(CompilationError, match="effect authority"):
        _compiled(candidate=unauthorized)

    wrong_type = _Resolver()
    wrong_type.map = _contract(
        "executor",
        "read_files_ocr",
        "metnos.test-map/1",
        inputs=("paths",),
        input_types=(("paths", "integer"),),
    )
    with pytest.raises(CompilationError, match="incompatible reference type"):
        _compiled(resolver=wrong_type)


def test_model_resources_require_frozen_binding_prompt_and_invalidation():
    missing_contract = _pipeline()
    missing_contract["stages"][1]["resources"]["vlm"] = 1
    with pytest.raises(CompilationError, match="model resources"):
        _compiled(candidate=missing_contract)

    resolver = _Resolver()
    resolver.map = _contract(
        "executor",
        "read_files_ocr",
        "metnos.test-map/1",
        inputs=("files", "paths"),
        input_types=(("files", "array"), ("paths", "array")),
        model="vlm-v1",
        prompt="ocr-v1",
    )
    missing_invalidation = _pipeline()
    missing_invalidation["stages"][1]["resources"]["vlm"] = 1
    with pytest.raises(CompilationError, match="invalidate binding and prompt"):
        _compiled(candidate=missing_invalidation, resolver=resolver)

    admitted = deepcopy(missing_invalidation)
    admitted["stages"][1]["invalidation_keys"].extend((
        "model_binding.digest", "prompt.digest",
    ))
    compiled = _compiled(candidate=admitted, resolver=resolver)
    snapshot = next(
        item for item in compiled.catalog_snapshot["entries"]
        if item["stage_key"] == "map"
    )
    assert snapshot["model_binding_digest"].startswith("sha256:")
    assert snapshot["prompt_digest"].startswith("sha256:")
    assert snapshot["prompt_language"] == "it"


def test_model_stage_requires_a_positive_plan_token_budget():
    candidate = _pipeline()
    candidate["budgets"]["max_tokens"] = 0

    with pytest.raises(CompilationError, match="positive plan token budget"):
        _compiled(candidate=candidate)

    insufficient = _pipeline()
    insufficient["budgets"]["max_tokens"] = 8_191
    with pytest.raises(CompilationError, match="token reservation"):
        _compiled(candidate=insufficient)


def test_model_stage_rejects_a_binding_without_a_preauthorized_cost_bound():
    resolver = _Resolver()
    resolver.reduce = _contract(
        "workload",
        "entries.describe",
        "metnos.test-reduce/1",
        inputs=("entries",),
        input_types=(("entries", "array"),),
        model="paid-fixture",
        prompt="paid-fixture",
        model_cost_policy="unbounded",
    )

    with pytest.raises(CompilationError, match="preauthorized cost bound"):
        _compiled(resolver=resolver)


def test_entry_identity_fanout_is_typed_and_requires_one_entries_dependency():
    candidate = _pipeline()
    answer = candidate["stages"][2]
    answer["type"] = "map"
    answer["cardinality"] = {
        "mode": "per_dependency",
        "max_units": 50,
        "entry_identity_field": "entry_id",
    }
    answer["input_bindings"] = {
        "entries": {"ref": "dependency.entries", "stage": "map"},
    }
    resolver = _Resolver()
    resolver.reduce = _contract(
        "workload", "entries.describe", "metnos.test-reduce/1",
        inputs=("entries",), input_types=(("entries", "object"),),
        model="v1", prompt="v1",
    )
    compiled = _compiled(candidate=candidate, resolver=resolver)
    assert compiled.graph["stages"][-1]["key"] == "reduce"

    missing_identity = deepcopy(candidate)
    missing_identity["stages"][2]["cardinality"]["entry_identity_field"] = "missing"
    with pytest.raises(CompilationError, match="declared string entry field"):
        _compiled(candidate=missing_identity, resolver=resolver)

    multiple_dependencies = deepcopy(candidate)
    multiple_dependencies["stages"][2]["depends_on"].append("inventory")
    with pytest.raises(CompilationError, match="exactly one dependency"):
        _compiled(candidate=multiple_dependencies, resolver=resolver)


def test_executor_prompt_and_binding_mutations_invalidate_only_descendants():
    baseline = _compiled()
    changed_executor = _compiled(resolver=_Resolver(map_suffix="v2"))
    assert invalidated_stages(baseline, changed_executor) == ("map", "reduce")

    changed_prompt = _compiled(resolver=_Resolver(prompt="v2"))
    assert invalidated_stages(baseline, changed_prompt) == ("reduce",)

    changed_binding = _compiled(resolver=_Resolver(model="v2"))
    assert invalidated_stages(baseline, changed_binding) == ("reduce",)
    assert affected_stages(baseline, ("map",)) == ("map", "reduce")


def test_semantic_input_binding_mutation_changes_the_stage_and_descendants():
    baseline_plan = _pipeline()
    changed_plan = deepcopy(baseline_plan)
    changed_plan["stages"][1]["input_bindings"] = {
        "files": {"ref": "source.path"},
    }
    baseline = _compiled(candidate=baseline_plan)
    changed = _compiled(candidate=changed_plan)
    assert invalidated_stages(baseline, changed) == ("map", "reduce")


def test_registered_workload_rejects_unknown_workload_and_missing_tier(monkeypatch):
    resolver = RegisteredWorkloadResolver(
        output_schemas={"entries.describe": ("metnos.test-reduce/1",)},
    )
    with pytest.raises(CompilationError, match="not registered"):
        resolver.resolve("workload", "unknown.workload")

    from llm_workloads import WORKLOADS, WorkloadContract

    monkeypatch.setitem(
        WORKLOADS,
        "tests.bad-tier",
        WorkloadContract("unknown-tier", None, "test", "json"),
    )
    with pytest.raises(CompilationError, match="binding is unavailable"):
        resolver.resolve("workload", "tests.bad-tier")


def test_registered_creative_tier_is_resolved_by_the_router_binding():
    seen = []

    def binding(tier, *, level=None):
        seen.append((tier, level))
        return {
            "provider": "fixture",
            "model": "fixture-model",
            "temperature": 0.35,
        }

    resolver = RegisteredWorkloadResolver(
        output_schemas={"promotion.commentary": ("metnos.test-reduce/1",)},
        prompt_digests={"promotion.commentary": _digest("promotion-prompt")},
        prompt_language="it",
        max_input_tokens={"promotion.commentary": 1_024},
        max_output_tokens={"promotion.commentary": 512},
        max_calls_per_attempt={"promotion.commentary": 1},
        binding_resolver=binding,
    )
    contract = resolver.resolve("workload", "promotion.commentary")
    assert seen == [("creative", None)]
    assert contract.model_binding_digest is not None


def test_admission_persists_exact_compiler_snapshots_without_execution(tmp_path):
    from durable_workloads.storage import DurableWorkloadStore

    store = DurableWorkloadStore.open(tmp_path / "private" / "state.sqlite3")
    try:
        draft = store.create_draft(
            "owner-a", "f5-admission", redacted_request={"summary": "fixture"}
        )
        result = admit_candidate(
            store,
            "owner-a",
            draft.workload_id,
            _pipeline(),
            inventory([source(0)]),
            expected_version=draft.version,
            runners=_Resolver(),
            output_schemas=_schemas(),
        )
        row = store._connection.execute(
            "SELECT catalog_snapshot_json, policy_snapshot_json FROM revisions WHERE id=?",
            (result.revision.revision_id,),
        ).fetchone()
        assert json.loads(row["catalog_snapshot_json"]) == result.compiled.catalog_snapshot
        assert json.loads(row["policy_snapshot_json"]) == result.compiled.policy_snapshot
        assert store._connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    finally:
        store.close()
