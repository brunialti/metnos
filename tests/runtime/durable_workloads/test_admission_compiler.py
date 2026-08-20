from __future__ import annotations

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
            "reduction.fan_in",
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


def test_inline_output_prose_is_not_an_approved_schema():
    with pytest.raises(CompilationError, match="not approved"):
        compile_plan(
            plan(with_map=True), inventory([source(0)]),
            runners=_Resolver(), output_schemas=OutputSchemaRegistry(()),
        )


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


def test_entry_identity_fanout_is_typed_and_requires_one_entries_dependency():
    candidate = _pipeline()
    answer = candidate["stages"][2]
    answer["type"] = "map"
    answer["cardinality"] = {
        "mode": "per_dependency",
        "max_units": 50,
        "entry_identity_field": "entry_id",
    }
    compiled = _compiled(candidate=candidate)
    assert compiled.graph["stages"][-1]["key"] == "reduce"

    missing_identity = deepcopy(candidate)
    missing_identity["stages"][2]["cardinality"]["entry_identity_field"] = "missing"
    with pytest.raises(CompilationError, match="declared string entry field"):
        _compiled(candidate=missing_identity)

    multiple_dependencies = deepcopy(candidate)
    multiple_dependencies["stages"][2]["depends_on"].append("inventory")
    with pytest.raises(CompilationError, match="exactly one dependency"):
        _compiled(candidate=multiple_dependencies)


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
