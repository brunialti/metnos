"""Schema-first compiler for dormant durable-plan v1 candidates.

Compilation is deterministic and side-effect free apart from reading verified
catalog/configuration ports.  It never invokes an executor or an LLM.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from jsonschema import Draft202012Validator, ValidationError
from executor_metadata import (
    DEFAULT_EXECUTION_POLICY,
    execution_policy as normalize_execution_policy,
)
from hashutil import sha256_prefixed
from llm_pricing import cost_policy

from .models import DurableEffect, RunnerKind
from .reduction import hierarchical_node_bound
from .schema import (
    MAX_PLAN_JSON_BYTES,
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
_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,3}$")
_MODEL_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_DEFAULT_FROZEN_EXECUTION_POLICY = tuple(DEFAULT_EXECUTION_POLICY.items())


def _freeze_execution_policy(
    value: Mapping[str, Any] | None,
) -> tuple[tuple[str, Any], ...]:
    """Return the canonical, fail-closed scheduler authority."""

    normalized = normalize_execution_policy({
        "execution": dict(value) if isinstance(value, Mapping) else {},
    })
    return tuple(
        (name, normalized[name]) for name in DEFAULT_EXECUTION_POLICY
    )


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


def _model_digest(value: object) -> str:
    return sha256_prefixed(str(value or ""))


@dataclass(frozen=True, slots=True)
class ApprovedOutputSchema:
    """One registry-owned JSON Schema, never plan-owned inline prose."""

    name: str
    schema: Mapping[str, Any]
    digest: str
    validator: Callable[[Any], None] | None = None
    _schema_validator: Draft202012Validator | None = field(
        default=None,
        repr=False,
        compare=False,
    )

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
        try:
            Draft202012Validator.check_schema(normalized)
            schema_validator = Draft202012Validator(normalized)
        except Exception as exc:
            raise CompilationError("approved output schema is invalid JSON Schema") from exc
        return cls(
            name=name,
            schema=normalized,
            digest=_digest(normalized, "durable-output-schema"),
            validator=validator,
            _schema_validator=schema_validator,
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

    def entry_field_schema(self, dotted_name: str) -> Mapping[str, Any] | None:
        """Return one field declared by the items of the ``entries`` array."""
        entries = self.field_schema("entries")
        if not isinstance(entries, Mapping) or entries.get("type") != "array":
            return None
        items = entries.get("items")
        if not isinstance(items, Mapping) or items.get("type") != "object":
            return None
        current: Mapping[str, Any] = items
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
        try:
            (self._schema_validator or Draft202012Validator(self.schema)).validate(value)
        except ValidationError as exc:
            location = ".".join(str(item) for item in exc.absolute_path) or "result"
            raise OutputValidationError(
                f"{location} violates the approved output schema"
            ) from exc
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

    @property
    def names(self) -> tuple[str, ...]:
        """Return the closed schema identities in canonical order."""

        return tuple(sorted(self._schemas, key=str.encode))

    @property
    def schemas(self) -> tuple[ApprovedOutputSchema, ...]:
        """Return the immutable schemas in the same canonical order."""

        return tuple(self._schemas[name] for name in self.names)


def core_output_schemas() -> OutputSchemaRegistry:
    """Return schemas owned by generic LRE internal runners."""

    digest = {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"}
    return OutputSchemaRegistry((
        ApprovedOutputSchema.create(
            "metnos.executor-result/1",
            {
                "type": "object",
                "additionalProperties": True,
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
        ),
        ApprovedOutputSchema.create(
            "metnos.internal-artifacts/1",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entries": {
                        "type": "array",
                        "maxItems": 1_000_000,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "logical_name": {
                                    "type": "string", "maxLength": 64,
                                },
                                "artifact_id": {
                                    "type": "string", "maxLength": 128,
                                },
                                "digest": digest,
                            },
                            "required": [
                                "logical_name", "artifact_id", "digest",
                            ],
                        },
                    },
                },
                "required": ["entries"],
            },
        ),
        ApprovedOutputSchema.create(
            "metnos.inventory-seal/1",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "digest": digest,
                    "sources": {
                        "type": "array", "items": {"type": "object"},
                    },
                },
                "required": ["digest", "sources"],
            },
        ),
    ))


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
    prompt_language: str | None = None
    model_provider: str | None = None
    model_digest: str | None = None
    model_tier: str | None = None
    model_kind: str | None = None
    model_max_calls: int | None = None
    model_max_input_tokens: int | None = None
    model_max_output_tokens: int | None = None
    model_cost_policy: str | None = None
    transport: str = "internal"
    intelligence: str = "deterministic"
    supports_hierarchical_reduction: bool = False
    execution_policy: tuple[tuple[str, Any], ...] = (
        _DEFAULT_FROZEN_EXECUTION_POLICY
    )
    execution_policy_declared: bool = False

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
        model_prompt_fields = (
            self.model_binding_digest,
            self.prompt_digest,
            self.prompt_language,
            self.model_provider,
            self.model_digest,
            self.model_tier,
            self.model_kind,
            self.model_max_calls,
            self.model_max_input_tokens,
            self.model_max_output_tokens,
            self.model_cost_policy,
        )
        if any(value is not None for value in model_prompt_fields) and any(
            value is None for value in model_prompt_fields
        ):
            raise CompilationError(
                "model binding, prompt digest and prompt language must be frozen together"
            )
        if self.prompt_language is not None and not _LANGUAGE_RE.fullmatch(
            self.prompt_language
        ):
            raise CompilationError("prompt language is not a canonical language tag")
        if self.model_provider is not None and not _MODEL_LABEL_RE.fullmatch(
            self.model_provider
        ):
            raise CompilationError("model provider is not a technical label")
        if self.model_digest is not None:
            _require_digest(self.model_digest, context="model identity digest")
        if self.model_tier is not None and (
            self.model_tier != ""
            and not _MODEL_LABEL_RE.fullmatch(self.model_tier)
        ):
            raise CompilationError("model tier is not a technical label")
        if self.model_kind is not None and not _MODEL_LABEL_RE.fullmatch(
            self.model_kind
        ):
            raise CompilationError("model kind is not a technical label")
        if self.model_max_calls is not None and (
            isinstance(self.model_max_calls, bool)
            or not isinstance(self.model_max_calls, int)
            or not 1 <= self.model_max_calls <= 64
        ):
            raise CompilationError("model max calls must be an integer in 1..64")
        if self.model_max_input_tokens is not None and (
            isinstance(self.model_max_input_tokens, bool)
            or not isinstance(self.model_max_input_tokens, int)
            or not 1 <= self.model_max_input_tokens <= 16_777_216
        ):
            raise CompilationError(
                "model max input tokens must be an integer in 1..16777216"
            )
        if self.model_max_output_tokens is not None and (
            isinstance(self.model_max_output_tokens, bool)
            or not isinstance(self.model_max_output_tokens, int)
            or not 1 <= self.model_max_output_tokens <= 1_000_000
        ):
            raise CompilationError(
                "model max output tokens must be an integer in 1..1000000"
            )
        if self.model_cost_policy is not None and self.model_cost_policy not in {
            "zero", "metered", "unbounded",
        }:
            raise CompilationError("model cost policy is invalid")
        if not isinstance(self.supports_hierarchical_reduction, bool):
            raise CompilationError(
                "runner hierarchical reduction support must be boolean"
            )
        try:
            policy = dict(self.execution_policy)
        except (TypeError, ValueError) as exc:
            raise CompilationError("runner execution policy is malformed") from exc
        if (
            self.execution_policy != _freeze_execution_policy(policy)
            or set(policy) != set(DEFAULT_EXECUTION_POLICY)
        ):
            raise CompilationError("runner execution policy is not canonical")
        if not isinstance(self.execution_policy_declared, bool):
            raise CompilationError(
                "runner execution policy declaration must be boolean"
            )

    def snapshot(self, *, stage_key: str, output_schema: ApprovedOutputSchema) -> dict[str, Any]:
        snapshot = {
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
            "execution_policy": dict(self.execution_policy),
            "execution_policy_declared": self.execution_policy_declared,
        }
        if self.prompt_language is not None:
            snapshot.update({
                "prompt_language": self.prompt_language,
                "model_provider": self.model_provider,
                "model_digest": self.model_digest,
                "model_tier": self.model_tier,
                "model_kind": self.model_kind,
                "model_max_calls": self.model_max_calls,
                "model_max_input_tokens": self.model_max_input_tokens,
                "model_max_output_tokens": self.model_max_output_tokens,
                "model_cost_policy": self.model_cost_policy,
            })
        if self.supports_hierarchical_reduction:
            snapshot["supports_hierarchical_reduction"] = True
        return snapshot


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
        model_bindings: Mapping[str, Mapping[str, Any]] | None = None,
        prompt_digests: Mapping[str, str] | None = None,
        prompt_languages: Mapping[str, str] | None = None,
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
        self._model_bindings = {
            str(name): dict(binding)
            for name, binding in (model_bindings or {}).items()
        }
        self._model_binding_digests = {
            name: _digest(binding, "durable-executor-model-binding")
            for name, binding in self._model_bindings.items()
        }
        self._prompt_digests = {
            str(name): _require_digest(value, context=f"executor {name} prompt digest")
            for name, value in (prompt_digests or {}).items()
        }
        self._prompt_languages = {
            str(name): str(value)
            for name, value in (prompt_languages or {}).items()
        }
        if not (
            set(self._model_binding_digests)
            == set(self._prompt_digests)
            == set(self._prompt_languages)
        ):
            raise CompilationError(
                "executor model bindings, prompt digests and languages must name the same runners"
            )
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
        return self.attest_executor(name, executor)

    def attest_executor(
        self,
        name: str,
        executor: object,
    ) -> FrozenRunnerContract:
        """Derive the contract from the exact verified object to be invoked."""

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
        execution_policy_declared = bool(
            getattr(executor, "execution_policy_declared", False)
        )
        execution_policy = _freeze_execution_policy(
            getattr(executor, "execution_policy", None)
            if execution_policy_declared else None
        )
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
            "model_binding_digest": self._model_binding_digests.get(name),
            "prompt_digest": self._prompt_digests.get(name),
            "execution_policy": dict(execution_policy),
            "execution_policy_declared": execution_policy_declared,
        }
        if name in self._prompt_languages:
            binding = self._model_bindings[name]
            contract_facts.update({
                "prompt_language": self._prompt_languages[name],
                "model_provider": str(binding.get("provider") or ""),
                "model_digest": _model_digest(binding.get("model")),
                "model_tier": str(binding.get("usage_tier") or ""),
                "model_kind": str(binding.get("usage_kind") or ""),
                "model_max_calls": binding.get("max_calls_per_attempt"),
                "model_max_input_tokens": binding.get("max_input_tokens"),
                "model_max_output_tokens": binding.get("max_tokens"),
                "model_cost_policy": cost_policy(
                    str(binding.get("provider") or ""),
                    str(binding.get("model") or ""),
                ),
            })
        return FrozenRunnerContract(
            kind=RunnerKind.EXECUTOR.value,
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
            model_binding_digest=contract_facts["model_binding_digest"],
            prompt_digest=contract_facts["prompt_digest"],
            prompt_language=contract_facts.get("prompt_language"),
            model_provider=contract_facts.get("model_provider"),
            model_digest=contract_facts.get("model_digest"),
            model_tier=contract_facts.get("model_tier"),
            model_kind=contract_facts.get("model_kind"),
            model_max_calls=contract_facts.get("model_max_calls"),
            model_max_input_tokens=contract_facts.get(
                "model_max_input_tokens"
            ),
            model_max_output_tokens=contract_facts.get(
                "model_max_output_tokens"
            ),
            model_cost_policy=contract_facts.get("model_cost_policy"),
            execution_policy=execution_policy,
            execution_policy_declared=execution_policy_declared,
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
        prompt_language: str | None = None,
        max_input_tokens: Mapping[str, int] | None = None,
        max_output_tokens: Mapping[str, int] | None = None,
        max_calls_per_attempt: Mapping[str, int] | None = None,
        hierarchical_reducers: Sequence[str] = (),
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
        self._prompt_language = prompt_language
        self._max_input_tokens = dict(max_input_tokens or {})
        self._max_output_tokens = dict(max_output_tokens or {})
        self._max_calls_per_attempt = dict(max_calls_per_attempt or {})
        self._hierarchical_reducers = frozenset(map(str, hierarchical_reducers))
        if not self._hierarchical_reducers <= set(self._output_schemas):
            raise CompilationError(
                "hierarchical reducers must be registered workloads"
            )
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
        execution_policy = _freeze_execution_policy({
            "effect": "read_only",
            "parallelism_class": 1,
            "resource_class": "llm",
            "concurrency_key": "none",
            "equivalence_gate": "verified",
        })
        contract_facts = {
            "name": name,
            "tier": str(tier_request),
            "level": tier_request.level,
            "family": registered.family,
            "output_constraint": registered.output_constraint,
            "model_binding_digest": binding_digest,
            "prompt_digest": self._prompt_digests.get(name),
            "prompt_language": self._prompt_language,
            "model_provider": str(binding.get("provider") or ""),
            "model_digest": _model_digest(binding.get("model")),
            "model_tier": str(tier_request),
            "model_kind": "chat",
            "model_max_calls": self._max_calls_per_attempt.get(name),
            "model_max_input_tokens": self._max_input_tokens.get(name),
            "model_max_output_tokens": self._max_output_tokens.get(name),
            "model_cost_policy": cost_policy(
                str(binding.get("provider") or ""),
                str(binding.get("model") or ""),
            ),
            "input_names": list(self._input_names.get(name, ())),
            "input_types": dict(self._input_types.get(name, ())),
            "required_inputs": list(self._required_inputs.get(name, ())),
            "output_schemas": list(self._output_schemas.get(name, ())),
            "supports_hierarchical_reduction":
                name in self._hierarchical_reducers,
            "execution_policy": dict(execution_policy),
            "execution_policy_declared": True,
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
            prompt_language=self._prompt_language,
            model_provider=contract_facts["model_provider"],
            model_digest=contract_facts["model_digest"],
            model_tier=contract_facts["model_tier"],
            model_kind=contract_facts["model_kind"],
            model_max_calls=contract_facts["model_max_calls"],
            model_max_input_tokens=contract_facts[
                "model_max_input_tokens"
            ],
            model_max_output_tokens=contract_facts[
                "model_max_output_tokens"
            ],
            model_cost_policy=contract_facts["model_cost_policy"],
            transport="llm-gateway",
            intelligence="model",
            supports_hierarchical_reduction=name in self._hierarchical_reducers,
            execution_policy=execution_policy,
            execution_policy_declared=True,
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

    def attest_executor(
        self,
        name: str,
        executor: object,
    ) -> FrozenRunnerContract:
        attestor = getattr(self._executors, "attest_executor", None)
        if not callable(attestor):
            raise CompilationError("executor resolver cannot attest a loaded runner")
        return attestor(name, executor)


_INTERNAL_EFFECTS = {
    "sealed_inventory": DurableEffect.PURE.value,
    "schema_and_coverage_validator": DurableEffect.PURE.value,
    "artifact_store_publish": DurableEffect.IDEMPOTENT.value,
}
_INTERNAL_INPUT_TYPES = {
    "sealed_inventory": {"inventory": "object"},
    "schema_and_coverage_validator": {"assembled": "object"},
    "artifact_store_publish": {"artifacts": "array", "validation": "array"},
}


def _internal_contract(name: str, output_schema_name: str) -> FrozenRunnerContract:
    try:
        effect = _INTERNAL_EFFECTS[name]
    except KeyError as exc:
        raise CompilationError(f"internal runner is not approved: {name}") from exc
    input_types = _INTERNAL_INPUT_TYPES[name]
    execution_policy = _freeze_execution_policy(
        {
            "effect": "read_only",
            "parallelism_class": 1,
            "resource_class": "default",
            "concurrency_key": "none",
            "equivalence_gate": "verified",
        }
        if effect == DurableEffect.PURE.value
        else None
    )
    facts = {
        "kind": "internal",
        "name": name,
        "version": 1,
        "input_types": input_types,
        "execution_policy": dict(execution_policy),
    }
    digest = _digest(facts, "durable-internal-runner")
    return FrozenRunnerContract(
        kind=RunnerKind.INTERNAL.value,
        name=name,
        contract_digest=digest,
        implementation_digest=digest,
        allowed_effects=(effect,),
        input_names=tuple(sorted(input_types, key=str.encode)),
        output_schema_names=(output_schema_name,),
        required_input_names=tuple(sorted(input_types, key=str.encode)),
        input_types=tuple(sorted(input_types.items(), key=lambda item: item[0].encode())),
        execution_policy=execution_policy,
        execution_policy_declared=True,
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
        if ref == "literal":
            value = reference["value"]
            canonical_json(value, max_bytes=MAX_PLAN_JSON_BYTES)
            if value is None:
                provided_types = set()
            elif type(value) is bool:
                provided_types = {"boolean"}
            elif type(value) is int:
                provided_types = {"integer", "number"}
            elif type(value) is float and math.isfinite(value):
                provided_types = {"number"}
            elif type(value) is str:
                provided_types = {"string"}
            elif type(value) is list:
                provided_types = {"array"}
            elif type(value) is dict and all(
                isinstance(key, str) for key in value
            ):
                provided_types = {"object"}
            else:
                provided_types = set()
        elif ref.startswith("dependency."):
            schema = schemas_by_stage.get(str(dependency))
            if schema is None:
                raise CompilationError(
                    f"stage {stage['key']} references an unresolved dependency: {dependency}"
                )
            if ref == "dependency.entries":
                entries = schema.field_schema("entries")
                if entries is None:
                    raise CompilationError(
                        f"stage {stage['key']} expects entries from a schema without entries"
                )
                if field is None:
                    provided_types = {"array"}
                    if stage["cardinality"].get("entry_identity_field") is not None:
                        # A typed entry fan-out selects exactly one entry before
                        # invoking the runner, so the binding may be an object.
                        provided_types.add("object")
                else:
                    field_definition = schema.entry_field_schema(str(field))
                    if field_definition is None:
                        raise CompilationError(
                            f"stage {stage['key']} binding {argument} references a missing field in entries"
                        )
                    field_type = field_definition.get("type")
                    provided_types = {str(field_type)} if isinstance(field_type, str) else set()
            elif field is not None:
                field_definition = schema.field_schema(str(field))
                if field_definition is None:
                    raise CompilationError(
                        f"stage {stage['key']} binding {argument} references a missing field"
                    )
                field_type = field_definition.get("type")
                provided_types = {str(field_type)} if isinstance(field_type, str) else set()
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
) -> CompiledPlan:
    """Compile one candidate and sealed inventory without executing anything."""

    canonical_plan = validate_plan(candidate)
    inline_inventory, sources = validate_inventory(inventory)
    normalized_plan = json.loads(canonical_plan)
    inventory_hash = str(inventory["digest"])
    inventory_contract = normalized_plan["inventory"]
    if len(sources) > int(inventory_contract["max_sources"]):
        raise CompilationError("sealed inventory exceeds plan source budget")
    inventory_bytes = sum(int(source["size_bytes"]) for source in sources)
    if inventory_bytes > int(inventory_contract["max_total_bytes"]):
        raise CompilationError("sealed inventory exceeds plan byte budget")
    if inline_inventory is None and any(
        reference.get("ref") == "revision.inventory"
        for stage in normalized_plan["stages"]
        if stage["type"] != "inventory"
        for reference in stage["input_bindings"].values()
    ):
        raise CompilationError(
            "revision.inventory is available only for a bounded inline "
            "inventory; use per-source bindings for a large inventory"
        )
    schemas_by_stage: dict[str, ApprovedOutputSchema] = {}
    exposed_units_by_stage: dict[str, int] = {}
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
        resources = stage["resources"]
        uses_model = int(resources["llm"]) > 0 or int(resources["vlm"]) > 0
        has_model_contract = contract.model_binding_digest is not None
        if uses_model != has_model_contract:
            raise CompilationError(
                f"stage {key} model resources and frozen model contract disagree"
            )
        if uses_model and int(normalized_plan["budgets"]["max_tokens"]) == 0:
            raise CompilationError(
                f"model stage {key} requires a positive plan token budget"
            )
        if uses_model:
            required_token_reservation = int(contract.model_max_calls or 0) * (
                int(contract.model_max_input_tokens or 0)
                + int(contract.model_max_output_tokens or 0)
            )
            if int(normalized_plan["budgets"]["max_tokens"]) < required_token_reservation:
                raise CompilationError(
                    f"model stage {key} token reservation exceeds the plan token budget"
                )
            if contract.model_cost_policy != "zero":
                raise CompilationError(
                    f"model stage {key} has no preauthorized cost bound"
                )
        if uses_model and not {
            "model_binding.digest", "prompt.digest",
        } <= set(stage["invalidation_keys"]):
            raise CompilationError(
                f"model stage {key} must invalidate binding and prompt digests"
            )

        mode = str(stage["cardinality"]["mode"])
        stage_type = str(stage["type"])
        entry_identity_field = stage["cardinality"].get("entry_identity_field")
        if stage_type == "map" and mode not in {"per_source", "per_dependency"}:
            raise CompilationError("map stages need per_source or per_dependency cardinality")
        if stage_type == "map" and mode == "per_dependency" and entry_identity_field is None:
            raise CompilationError("per_dependency map stages require an entry identity field")
        if stage_type == "reduce" and mode not in {"per_dependency", "singleton"}:
            raise CompilationError("reduce stages need per_dependency or singleton cardinality")
        reduction_fan_in = stage["cardinality"].get("fan_in")
        if reduction_fan_in is not None:
            if not contract.supports_hierarchical_reduction:
                raise CompilationError(
                    f"runner does not approve hierarchical reduction for stage {key}"
                )
            if schema.field_schema("entries") is None:
                raise CompilationError(
                    f"hierarchical reduction stage {key} must output entries"
                )
            if len(stage["input_bindings"]) != 1:
                raise CompilationError(
                    f"hierarchical reduction stage {key} needs exactly one input"
                )
            required_invalidation = {"reduction.order", "reduction.fan_in"}
            if not required_invalidation <= set(stage["invalidation_keys"]):
                raise CompilationError(
                    f"hierarchical reduction stage {key} must invalidate "
                    "its ordering and fan-in"
                )
        if mode == "per_source" and not any(
            reference["ref"] in {"source.path", "source.record"}
            for reference in stage["input_bindings"].values()
        ):
            raise CompilationError(f"per_source stage {key} has no source binding")
        if entry_identity_field is not None:
            if stage_type != "map":
                raise CompilationError("entry identity fan-out is only valid for map stages")
            dependencies = tuple(map(str, stage["depends_on"]))
            if len(dependencies) != 1:
                raise CompilationError("entry identity fan-out needs exactly one dependency")
            dependency_key = dependencies[0]
            entry_schema = schemas_by_stage[dependency_key].entry_field_schema(
                str(entry_identity_field)
            )
            if entry_schema is None or entry_schema.get("type") != "string":
                raise CompilationError(
                    "entry identity field must be a declared string entry field"
                )
            if not any(
                reference["ref"] == "dependency.entries"
                and reference.get("stage") == dependency_key
                for reference in stage["input_bindings"].values()
            ):
                raise CompilationError(
                    "entry identity fan-out needs a binding from dependency entries"
                )

        invalidation_facts: dict[str, Any] = {}
        for invalidation_key in stage["invalidation_keys"]:
            if invalidation_key == "source.digest":
                invalidation_facts[invalidation_key] = inventory_hash
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
                if reduction_fan_in is None:
                    raise CompilationError(
                        f"stage {key} declares reduction.fan_in without "
                        "a hierarchical reduction"
                    )
                invalidation_facts[invalidation_key] = int(reduction_fan_in)

        reduction = None
        if stage_type == "reduce":
            reduction = {
                "mode": (
                    "hierarchical" if reduction_fan_in is not None else "direct"
                ),
                "fan_in": reduction_fan_in,
                "input": stage["cardinality"].get("reduction_input"),
                "max_input_bytes":
                    stage["cardinality"].get("max_input_bytes"),
            }
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

        dependencies = tuple(map(str, stage["depends_on"]))
        declared_max = int(stage["cardinality"]["max_units"])
        if reduction_fan_in is not None:
            input_bound = exposed_units_by_stage[dependencies[0]]
            required_units = hierarchical_node_bound(input_bound)
            if declared_max < required_units:
                raise CompilationError(
                    f"hierarchical reduction stage {key} needs at least "
                    f"{required_units} units for its admitted dependency bound"
                )
            exposed_units_by_stage[key] = 1
        elif mode == "singleton":
            dependency_bound = sum(
                exposed_units_by_stage[dependency] for dependency in dependencies
            )
            if stage_type != "inventory" and dependency_bound > 1024:
                raise CompilationError(
                    f"direct singleton stage {key} can receive more than 1024 inputs"
                )
            exposed_units_by_stage[key] = 1
        elif mode == "per_source":
            exposed_units_by_stage[key] = min(declared_max, len(sources))
        else:
            exposed_units_by_stage[key] = declared_max

    plan_hash = plan_digest(normalized_plan)
    graph = {
        "schema_version": "metnos.durable-graph/1",
        "plan_digest": plan_hash,
        "inventory_digest": inventory_hash,
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
