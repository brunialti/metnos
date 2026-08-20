"""Schema-first compiler for dormant durable-plan v1 candidates.

Compilation is deterministic and side-effect free apart from reading verified
catalog/configuration ports.  It never invokes an executor or an LLM.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .models import DurableEffect, RunnerKind
from .reduction import DEFAULT_FAN_IN, ReductionPlanError, build_reduction_graph
from .schema import (
    MAX_RESULT_JSON_BYTES,
    MAX_SNAPSHOT_JSON_BYTES,
    canonical_json,
    digest_json,
    plan_digest,
    validate_inventory,
    validate_plan,
)


_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SCHEMA_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,127}/[1-9][0-9]*$")


class CompilationError(ValueError):
    """A valid plan shape cannot be admitted against current contracts."""


class OutputValidationError(ValueError):
    """An executor result does not conform to its approved output schema."""


def _digest(value: Any, domain: str = "durable-contract") -> str:
    return digest_json(domain, value, max_bytes=MAX_SNAPSHOT_JSON_BYTES)


def _require_digest(value: str | None, *, context: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise CompilationError(f"{context} is not a SHA-256 digest")
    return value


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _validate_json_subset(value: Any, schema: Mapping[str, Any], context: str) -> None:
    expected = schema.get("type")
    expected_types = (expected,) if isinstance(expected, str) else tuple(expected or ())
    if expected_types and not any(_json_type_matches(value, item) for item in expected_types):
        raise OutputValidationError(f"{context} has an incompatible JSON type")
    if "enum" in schema and value not in schema["enum"]:
        raise OutputValidationError(f"{context} is outside the approved enum")
    if isinstance(value, Mapping):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        missing = sorted(set(required) - set(value))
        if missing:
            raise OutputValidationError(f"{context} misses required fields: {missing}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise OutputValidationError(f"{context} has unknown fields: {unknown}")
        for name, item in value.items():
            child = properties.get(name)
            if isinstance(child, Mapping):
                _validate_json_subset(item, child, f"{context}.{name}")
    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise OutputValidationError(f"{context} has too few items")
        if isinstance(maximum, int) and len(value) > maximum:
            raise OutputValidationError(f"{context} has too many items")
        child = schema.get("items")
        if isinstance(child, Mapping):
            for index, item in enumerate(value):
                _validate_json_subset(item, child, f"{context}[{index}]")


@dataclass(frozen=True, slots=True)
class ApprovedOutputSchema:
    """One registry-owned JSON Schema, never plan-owned inline prose."""

    name: str
    schema: Mapping[str, Any]
    digest: str
    validator: Callable[[Any], None] | None = None

    @classmethod
    def create(
        cls,
        name: str,
        schema: Mapping[str, Any],
        *,
        validator: Callable[[Any], None] | None = None,
    ) -> "ApprovedOutputSchema":
        if not isinstance(name, str) or not _SCHEMA_NAME_RE.fullmatch(name):
            raise CompilationError("approved output schema name is invalid")
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            raise CompilationError("approved output schema must describe an object")
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            raise CompilationError("approved output schema needs object properties")
        required = schema.get("required", [])
        if (
            isinstance(required, (str, bytes))
            or not isinstance(required, Sequence)
            or any(not isinstance(item, str) or item not in properties for item in required)
        ):
            raise CompilationError("approved output schema has invalid required fields")
        normalized = json.loads(
            canonical_json(schema, max_bytes=MAX_RESULT_JSON_BYTES)
        )
        return cls(
            name=name,
            schema=normalized,
            digest=_digest(normalized, "durable-output-schema"),
            validator=validator,
        )

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(sorted(self.schema["properties"], key=str.encode))

    def has_field(self, dotted_name: str) -> bool:
        return self.field_schema(dotted_name) is not None

    def field_schema(self, dotted_name: str) -> Mapping[str, Any] | None:
        current: Mapping[str, Any] = self.schema
        for part in dotted_name.split("."):
            properties = current.get("properties")
            if not isinstance(properties, Mapping) or part not in properties:
                return None
            child = properties[part]
            if not isinstance(child, Mapping):
                return None
            current = child
        return current

    def validate(self, value: Any) -> None:
        if self.validator is not None:
            self.validator(value)
        else:
            _validate_json_subset(value, self.schema, "result")
        canonical_json(value, max_bytes=MAX_RESULT_JSON_BYTES)


class OutputSchemaResolver(Protocol):
    def resolve(self, name: str) -> ApprovedOutputSchema: ...


class OutputSchemaRegistry:
    """Closed, exact-name registry for approved result contracts."""

    def __init__(self, schemas: Sequence[ApprovedOutputSchema]) -> None:
        entries: dict[str, ApprovedOutputSchema] = {}
        for schema in schemas:
            if schema.name in entries:
                raise CompilationError(f"duplicate approved output schema: {schema.name}")
            entries[schema.name] = schema
        self._schemas = entries

    def resolve(self, name: str) -> ApprovedOutputSchema:
        try:
            return self._schemas[name]
        except KeyError as exc:
            raise CompilationError(f"output schema is not approved: {name}") from exc


@dataclass(frozen=True, slots=True)
class FrozenRunnerContract:
    """Security-relevant runner facts frozen into an admitted revision."""

    kind: str
    name: str
    contract_digest: str
    implementation_digest: str
    allowed_effects: tuple[str, ...]
    input_names: tuple[str, ...]
    output_schema_names: tuple[str, ...]
    required_input_names: tuple[str, ...] = ()
    input_types: tuple[tuple[str, str], ...] = ()
    verified: bool = True
    model_binding_digest: str | None = None
    prompt_digest: str | None = None
    transport: str = "internal"
    intelligence: str = "deterministic"

    def __post_init__(self) -> None:
        try:
            RunnerKind(self.kind)
        except ValueError as exc:
            raise CompilationError("runner contract kind is unknown") from exc
        _require_digest(self.contract_digest, context="runner contract digest")
        _require_digest(self.implementation_digest, context="runner implementation digest")
        if not self.verified:
            raise CompilationError(f"runner is not verified: {self.name}")
        if not self.allowed_effects:
            raise CompilationError(f"runner has no durable effect authority: {self.name}")
        for effect in self.allowed_effects:
            try:
                DurableEffect(effect)
            except ValueError as exc:
                raise CompilationError(f"runner has an unknown durable effect: {self.name}") from exc
        if len(self.input_names) != len(set(self.input_names)):
            raise CompilationError(f"runner has duplicate input names: {self.name}")
        if not set(self.required_input_names) <= set(self.input_names):
            raise CompilationError(f"runner has invalid required inputs: {self.name}")
        declared_types = dict(self.input_types)
        if len(declared_types) != len(self.input_types) or not set(declared_types) <= set(
            self.input_names
        ):
            raise CompilationError(f"runner has invalid input types: {self.name}")
        if any(
            value not in {"array", "boolean", "integer", "number", "object", "string"}
            for value in declared_types.values()
        ):
            raise CompilationError(f"runner has an unknown input type: {self.name}")
        if self.model_binding_digest is not None:
            _require_digest(self.model_binding_digest, context="model binding digest")
        if self.prompt_digest is not None:
            _require_digest(self.prompt_digest, context="prompt digest")

    def snapshot(self, *, stage_key: str, output_schema: ApprovedOutputSchema) -> dict[str, Any]:
        return {
            "stage_key": stage_key,
            "kind": self.kind,
            "name": self.name,
            "contract_digest": self.contract_digest,
            "implementation_digest": self.implementation_digest,
            "allowed_effects": list(self.allowed_effects),
            "input_names": list(self.input_names),
            "required_input_names": list(self.required_input_names),
            "input_types": {name: value for name, value in self.input_types},
            "output_schema": {
                "name": output_schema.name,
                "digest": output_schema.digest,
            },
            "model_binding_digest": self.model_binding_digest,
            "prompt_digest": self.prompt_digest,
            "transport": self.transport,
            "intelligence": self.intelligence,
        }


class RunnerContractResolver(Protocol):
    def resolve(self, kind: str, name: str) -> FrozenRunnerContract: ...


def _semantic_schema(value: Any) -> Any:
    """Strip localized/presentational JSON-Schema fields before hashing."""

    if isinstance(value, Mapping):
        return {
            str(key): _semantic_schema(item)
            for key, item in value.items()
            if key not in {"description", "title", "examples", "$comment"}
        }
    if isinstance(value, list):
        return [_semantic_schema(item) for item in value]
    return value


class VerifiedCatalogResolver:
    """Resolve exact executor names through the signature-verifying loader."""

    def __init__(
        self,
        *,
        durable_output_schemas: Mapping[str, Sequence[str]] | None = None,
        durable_effects: Mapping[str, Sequence[str]] | None = None,
        catalog_loader: Callable[..., Any] | None = None,
    ) -> None:
        self._output_schemas = {
            str(name): tuple(sorted(map(str, values), key=str.encode))
            for name, values in (durable_output_schemas or {}).items()
        }
        self._effects = {
            str(name): tuple(sorted(map(str, values), key=str.encode))
            for name, values in (durable_effects or {}).items()
        }
        self._catalog_loader = catalog_loader

    def resolve(self, kind: str, name: str) -> FrozenRunnerContract:
        if kind != RunnerKind.EXECUTOR.value:
            raise CompilationError("verified catalog resolver accepts only executors")
        if self._catalog_loader is None:
            from loader import load_catalog

            loader = load_catalog
        else:
            loader = self._catalog_loader
        catalog = loader(verify=True, lang="en")
        executor = catalog.get(name)
        if executor is None:
            raise CompilationError(f"executor is absent from the verified catalog: {name}")
        signed_by = str(getattr(executor, "signed_by", "") or "")
        if not signed_by or signed_by.startswith("("):
            raise CompilationError(f"executor is not signed: {name}")
        if getattr(executor, "lifecycle", "") != "active" or bool(
            getattr(executor, "dormant", False)
        ):
            raise CompilationError(f"executor is not active for durable admission: {name}")
        implementation_digest = str(getattr(executor, "digest", "") or "")
        _require_digest(implementation_digest, context=f"executor {name} code digest")
        args_schema = _semantic_schema(getattr(executor, "args_schema", {}) or {})
        properties = args_schema.get("properties") if isinstance(args_schema, Mapping) else None
        if not isinstance(properties, Mapping):
            raise CompilationError(f"executor has no closed argument schema: {name}")
        contract_facts = {
            "name": name,
            "version": str(getattr(executor, "version", "") or ""),
            "implementation_digest": implementation_digest,
            "args_schema": args_schema,
            "capabilities": sorted(
                str(item.get("name"))
                for item in (getattr(executor, "capabilities", ()) or ())
                if isinstance(item, Mapping) and isinstance(item.get("name"), str)
            ),
            "placement": getattr(executor, "placement", {}) or {},
            "transport": str(getattr(executor, "transport", "") or ""),
            "intelligence": str(getattr(executor, "intelligence", "") or ""),
            "signed_by": signed_by,
            "durable_effects": list(self._effects.get(name, ())),
            "durable_output_schemas": list(self._output_schemas.get(name, ())),
        }
        return FrozenRunnerContract(
            kind=kind,
            name=name,
            contract_digest=_digest(contract_facts, "durable-executor-contract"),
            implementation_digest=implementation_digest,
            allowed_effects=self._effects.get(name, ()),
            input_names=tuple(sorted(map(str, properties), key=str.encode)),
            output_schema_names=tuple(sorted(self._output_schemas.get(name, ()), key=str.encode)),
            required_input_names=tuple(
                sorted(map(str, args_schema.get("required") or ()), key=str.encode)
            ),
            input_types=tuple(sorted(
                (
                    (str(argument), str(definition.get("type")))
                    for argument, definition in properties.items()
                    if isinstance(definition, Mapping) and isinstance(definition.get("type"), str)
                ),
                key=lambda item: item[0].encode("utf-8"),
            )),
            transport=contract_facts["transport"],
            intelligence=contract_facts["intelligence"],
        )


class RegisteredWorkloadResolver:
    """Resolve workload -> logical tier -> effective router binding."""

    def __init__(
        self,
        *,
        output_schemas: Mapping[str, Sequence[str]],
        input_names: Mapping[str, Sequence[str]] | None = None,
        input_types: Mapping[str, Mapping[str, str]] | None = None,
        required_inputs: Mapping[str, Sequence[str]] | None = None,
        prompt_digests: Mapping[str, str] | None = None,
        binding_resolver: Callable[..., Mapping[str, Any]] | None = None,
    ) -> None:
        self._output_schemas = {
            str(key): tuple(sorted(map(str, value), key=str.encode))
            for key, value in output_schemas.items()
        }
        self._input_names = {
            str(key): tuple(sorted(map(str, value), key=str.encode))
            for key, value in (input_names or {}).items()
        }
        self._input_types = {
            str(key): tuple(sorted(value.items(), key=lambda item: item[0].encode("utf-8")))
            for key, value in (input_types or {}).items()
        }
        self._required_inputs = {
            str(key): tuple(sorted(map(str, value), key=str.encode))
            for key, value in (required_inputs or {}).items()
        }
        self._prompt_digests = dict(prompt_digests or {})
        self._binding_resolver = binding_resolver

    def resolve(self, kind: str, name: str) -> FrozenRunnerContract:
        if kind != RunnerKind.WORKLOAD.value:
            raise CompilationError("workload resolver accepts only workload runners")
        from llm_workloads import WORKLOADS, tier_for

        try:
            registered = WORKLOADS[name]
            tier_request = tier_for(name)
        except (KeyError, ValueError) as exc:
            raise CompilationError(f"LLM workload is not registered: {name}") from exc
        if self._binding_resolver is None:
            from llm_router import resolved_tier_spec

            resolver = resolved_tier_spec
        else:
            resolver = self._binding_resolver
        try:
            binding = dict(resolver(str(tier_request), level=tier_request.level))
        except Exception as exc:
            raise CompilationError(f"LLM binding is unavailable for workload: {name}") from exc
        binding_facts = {
            "tier": str(tier_request),
            "level": tier_request.level,
            "binding": binding,
        }
        binding_digest = _digest(binding_facts, "durable-model-binding")
        contract_facts = {
            "name": name,
            "tier": str(tier_request),
            "level": tier_request.level,
            "family": registered.family,
            "output_constraint": registered.output_constraint,
            "model_binding_digest": binding_digest,
            "prompt_digest": self._prompt_digests.get(name),
            "input_names": list(self._input_names.get(name, ())),
            "input_types": dict(self._input_types.get(name, ())),
            "required_inputs": list(self._required_inputs.get(name, ())),
            "output_schemas": list(self._output_schemas.get(name, ())),
        }
        return FrozenRunnerContract(
            kind=kind,
            name=name,
            contract_digest=_digest(contract_facts, "durable-workload-contract"),
            implementation_digest=_digest(contract_facts, "durable-workload-implementation"),
            allowed_effects=(DurableEffect.PURE.value,),
            input_names=tuple(sorted(self._input_names.get(name, ()), key=str.encode)),
            output_schema_names=tuple(sorted(self._output_schemas.get(name, ()), key=str.encode)),
            required_input_names=tuple(
                sorted(self._required_inputs.get(name, ()), key=str.encode)
            ),
            input_types=self._input_types.get(name, ()),
            model_binding_digest=binding_digest,
            prompt_digest=self._prompt_digests.get(name),
            transport="llm-gateway",
            intelligence="model",
        )


class CompositeRunnerResolver:
    def __init__(
        self,
        *,
        executors: RunnerContractResolver,
        workloads: RunnerContractResolver,
    ) -> None:
        self._executors = executors
        self._workloads = workloads

    def resolve(self, kind: str, name: str) -> FrozenRunnerContract:
        if kind == RunnerKind.EXECUTOR.value:
            return self._executors.resolve(kind, name)
        if kind == RunnerKind.WORKLOAD.value:
            return self._workloads.resolve(kind, name)
        raise CompilationError(f"composite resolver cannot resolve runner kind: {kind}")


_INTERNAL_EFFECTS = {
    "sealed_inventory": DurableEffect.PURE.value,
    "schema_and_coverage_validator": DurableEffect.PURE.value,
    "artifact_store_publish": DurableEffect.IDEMPOTENT.value,
}


def _internal_contract(name: str, output_schema_name: str) -> FrozenRunnerContract:
    try:
        effect = _INTERNAL_EFFECTS[name]
    except KeyError as exc:
        raise CompilationError(f"internal runner is not approved: {name}") from exc
    facts = {"kind": "internal", "name": name, "version": 1}
    digest = _digest(facts, "durable-internal-runner")
    return FrozenRunnerContract(
        kind=RunnerKind.INTERNAL.value,
        name=name,
        contract_digest=digest,
        implementation_digest=digest,
        allowed_effects=(effect,),
        input_names=(),
        output_schema_names=(output_schema_name,),
    )


@dataclass(frozen=True, slots=True)
class CompiledPlan:
    plan: Mapping[str, Any]
    canonical_plan_json: str
    plan_digest: str
    graph: Mapping[str, Any]
    canonical_graph_json: str
    graph_digest: str
    catalog_snapshot: Mapping[str, Any]
    policy_snapshot: Mapping[str, Any]

    def stage_fingerprints(self) -> dict[str, str]:
        return {
            str(stage["key"]): str(stage["invalidation_digest"])
            for stage in self.graph["stages"]
        }


def _validate_binding_fields(
    stage: Mapping[str, Any],
    contract: FrozenRunnerContract,
    schemas_by_stage: Mapping[str, ApprovedOutputSchema],
) -> None:
    binding_names = set(map(str, stage["input_bindings"]))
    if contract.kind != RunnerKind.INTERNAL.value:
        unknown = sorted(binding_names - set(contract.input_names), key=str.encode)
        if unknown:
            raise CompilationError(
                f"stage {stage['key']} binds undeclared runner arguments: {unknown}"
            )
        missing = sorted(
            set(contract.required_input_names) - binding_names,
            key=str.encode,
        )
        if missing:
            raise CompilationError(
                f"stage {stage['key']} misses required runner arguments: {missing}"
            )
    input_types = dict(contract.input_types)
    for argument, reference in stage["input_bindings"].items():
        ref = str(reference["ref"])
        dependency = reference.get("stage")
        field = reference.get("field")
        provided_types: set[str]
        if ref.startswith("dependency."):
            schema = schemas_by_stage.get(str(dependency))
            if schema is None:
                raise CompilationError(
                    f"stage {stage['key']} references an unresolved dependency: {dependency}"
                )
            if field is not None:
                field_definition = schema.field_schema(str(field))
                if field_definition is None:
                    raise CompilationError(
                        f"stage {stage['key']} binding {argument} references a missing field"
                    )
                field_type = field_definition.get("type")
                provided_types = {str(field_type)} if isinstance(field_type, str) else set()
            elif ref == "dependency.entries":
                entries = schema.field_schema("entries")
                if entries is None:
                    raise CompilationError(
                        f"stage {stage['key']} expects entries from a schema without entries"
                    )
                provided_types = {"array"}
            else:
                provided_types = {"object"}
        else:
            if field is not None:
                raise CompilationError(
                    f"stage {stage['key']} uses field outside a dependency reference"
                )
            provided_types = {
                "revision.inventory": {"object"},
                "source.path": {"array", "string"},
                "source.record": {"object"},
            }.get(ref, set())
        expected_type = input_types.get(str(argument))
        if contract.kind != RunnerKind.INTERNAL.value:
            if expected_type is None:
                raise CompilationError(
                    f"stage {stage['key']} argument {argument} has no approved input type"
                )
            if expected_type not in provided_types:
                raise CompilationError(
                    f"stage {stage['key']} argument {argument} has an incompatible reference type"
                )


def _topological_stages(plan: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    remaining = {str(stage["key"]): stage for stage in plan["stages"]}
    emitted: list[Mapping[str, Any]] = []
    completed: set[str] = set()
    while remaining:
        ready = sorted(
            (
                key for key, stage in remaining.items()
                if set(map(str, stage["depends_on"])) <= completed
            ),
            key=str.encode,
        )
        if not ready:
            raise CompilationError("stage graph cannot be topologically ordered")
        for key in ready:
            emitted.append(remaining.pop(key))
            completed.add(key)
    return tuple(emitted)


def compile_plan(
    candidate: Mapping[str, Any],
    inventory: Mapping[str, Any],
    *,
    runners: RunnerContractResolver,
    output_schemas: OutputSchemaResolver,
    reduction_fan_in: int = DEFAULT_FAN_IN,
) -> CompiledPlan:
    """Compile one candidate and sealed inventory without executing anything."""

    canonical_plan = validate_plan(candidate)
    canonical_inventory, sources = validate_inventory(inventory)
    normalized_plan = json.loads(canonical_plan)
    normalized_inventory = json.loads(canonical_inventory)
    inventory_contract = normalized_plan["inventory"]
    if len(sources) > int(inventory_contract["max_sources"]):
        raise CompilationError("sealed inventory exceeds plan source budget")
    if sum(int(source["size_bytes"]) for source in sources) > int(
        inventory_contract["max_total_bytes"]
    ):
        raise CompilationError("sealed inventory exceeds plan byte budget")

    schemas_by_stage: dict[str, ApprovedOutputSchema] = {}
    ordered = _topological_stages(normalized_plan)
    catalog_entries: list[dict[str, Any]] = []
    policy_rules: list[dict[str, Any]] = []
    graph_stages: list[dict[str, Any]] = []

    for stage in ordered:
        key = str(stage["key"])
        schema_name = str(stage["output_schema"]["name"])
        schema = output_schemas.resolve(schema_name)
        kind = str(stage["runner"]["kind"])
        name = str(stage["runner"]["name"])
        contract = (
            _internal_contract(name, schema_name)
            if kind == RunnerKind.INTERNAL.value
            else runners.resolve(kind, name)
        )
        if contract.kind != kind or contract.name != name:
            raise CompilationError(f"runner resolver returned a mismatched contract for {key}")
        effect = str(stage["effect_profile"])
        if effect not in contract.allowed_effects:
            raise CompilationError(f"runner lacks declared effect authority for stage {key}")
        if schema_name not in contract.output_schema_names:
            raise CompilationError(f"runner does not declare approved output schema for stage {key}")
        _validate_binding_fields(stage, contract, schemas_by_stage)

        mode = str(stage["cardinality"]["mode"])
        stage_type = str(stage["type"])
        if stage_type == "map" and mode != "per_source":
            raise CompilationError("map stages must use per_source cardinality")
        if stage_type == "reduce" and mode not in {"per_dependency", "singleton"}:
            raise CompilationError("reduce stages need per_dependency or singleton cardinality")
        if mode == "per_source" and not any(
            reference["ref"] in {"source.path", "source.record"}
            for reference in stage["input_bindings"].values()
        ):
            raise CompilationError(f"per_source stage {key} has no source binding")

        invalidation_facts: dict[str, Any] = {}
        for invalidation_key in stage["invalidation_keys"]:
            if invalidation_key == "source.digest":
                invalidation_facts[invalidation_key] = normalized_inventory["digest"]
            elif invalidation_key == "dependencies.digest":
                invalidation_facts[invalidation_key] = _digest(
                    [
                        graph_stage["invalidation_digest"]
                        for graph_stage in graph_stages
                        if graph_stage["key"] in stage["depends_on"]
                    ],
                    "durable-dependencies",
                )
            elif invalidation_key == "runner.contract_digest":
                invalidation_facts[invalidation_key] = contract.contract_digest
            elif invalidation_key == "semantic_args.digest":
                invalidation_facts[invalidation_key] = _digest(
                    stage["input_bindings"], "durable-semantic-bindings"
                )
            elif invalidation_key == "model_binding.digest":
                if contract.model_binding_digest is None:
                    raise CompilationError(f"stage {key} has no model binding to invalidate")
                invalidation_facts[invalidation_key] = contract.model_binding_digest
            elif invalidation_key == "prompt.digest":
                if contract.prompt_digest is None:
                    raise CompilationError(f"stage {key} has no approved prompt digest")
                invalidation_facts[invalidation_key] = contract.prompt_digest
            elif invalidation_key == "reduction.order":
                invalidation_facts[invalidation_key] = "utf8-bytewise"
            elif invalidation_key == "reduction.fan_in":
                invalidation_facts[invalidation_key] = reduction_fan_in

        reduction = None
        if stage_type == "reduce":
            dependency_keys = tuple(sorted(map(str, stage["depends_on"]), key=str.encode))
            try:
                reduction = build_reduction_graph(
                    dependency_keys,
                    fan_in=reduction_fan_in,
                    max_inputs=int(stage["cardinality"]["max_units"]),
                ).as_dict()
            except ReductionPlanError as exc:
                raise CompilationError(f"stage {key} reduction is not admissible") from exc
        stage_fingerprint = _digest(
            {
                "stage": stage,
                "runner_contract_digest": contract.contract_digest,
                "output_schema_digest": schema.digest,
                "invalidation_facts": invalidation_facts,
                "reduction": reduction,
            },
            "durable-stage-invalidation",
        )
        graph_stages.append({
            "key": key,
            "depends_on": list(stage["depends_on"]),
            "invalidation_digest": stage_fingerprint,
            "invalidation_facts": invalidation_facts,
            "reduction": reduction,
        })
        catalog_entries.append(contract.snapshot(stage_key=key, output_schema=schema))
        policy_rules.append({
            "stage_key": key,
            "effect_profile": effect,
            "retry": stage["retry"],
            "timeout_s": stage["timeout_s"],
            "resources": stage["resources"],
            "required": stage["required"],
            "invalidation_digest": stage_fingerprint,
        })
        schemas_by_stage[key] = schema

    plan_hash = plan_digest(normalized_plan)
    graph = {
        "schema_version": "metnos.durable-graph/1",
        "plan_digest": plan_hash,
        "inventory_digest": normalized_inventory["digest"],
        "stages": graph_stages,
    }
    graph_json = canonical_json(graph, max_bytes=MAX_SNAPSHOT_JSON_BYTES)
    return CompiledPlan(
        plan=normalized_plan,
        canonical_plan_json=canonical_plan,
        plan_digest=plan_hash,
        graph=graph,
        canonical_graph_json=graph_json,
        graph_digest=_digest(graph, "durable-graph"),
        catalog_snapshot={
            "schema_version": "metnos.catalog-snapshot/1",
            "entries": catalog_entries,
        },
        policy_snapshot={
            "schema_version": "metnos.policy-snapshot/1",
            "graph_digest": _digest(graph, "durable-graph"),
            "rules": policy_rules,
        },
    )


def affected_stages(
    compiled: CompiledPlan,
    changed_stage_keys: Sequence[str],
) -> tuple[str, ...]:
    """Return changed stages and only their transitive descendants."""

    graph = {
        str(stage["key"]): tuple(map(str, stage["depends_on"]))
        for stage in compiled.graph["stages"]
    }
    changed = set(map(str, changed_stage_keys))
    unknown = changed - set(graph)
    if unknown:
        raise CompilationError(f"unknown changed stages: {sorted(unknown)}")
    while True:
        descendants = {
            key for key, dependencies in graph.items()
            if set(dependencies) & changed
        }
        expanded = changed | descendants
        if expanded == changed:
            break
        changed = expanded
    return tuple(stage["key"] for stage in compiled.graph["stages"] if stage["key"] in changed)


def invalidated_stages(previous: CompiledPlan, current: CompiledPlan) -> tuple[str, ...]:
    """Compare frozen stage facts and expand only from changed roots."""

    previous_facts = previous.stage_fingerprints()
    current_facts = current.stage_fingerprints()
    if set(previous_facts) != set(current_facts):
        raise CompilationError("plan revisions have different stage identities")
    roots = [key for key, digest in current_facts.items() if previous_facts[key] != digest]
    return affected_stages(current, roots)
