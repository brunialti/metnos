"""Compile one finalized long executor invocation into a generic LRE plan."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from .compiler import VerifiedCatalogResolver, core_output_schemas
from .domain_outcome import EXECUTOR_RESULT_SCHEMA
from .models import RESOURCE_KEYS, DurableEffect, RunnerKind
from .runtime_bindings import RuntimeRegistration
from .schema import (
    MAX_PLAN_JSON_BYTES,
    canonical_json,
    inventory_digest,
)


AUTO_LRE_MIN_TIMEOUT_S: Final[int] = 600
DIRECT_PLAN_ID: Final[str] = "metnos.direct.v1"
DIRECT_REGISTRATION_ID: Final[str] = "metnos.direct-runners.v1"
DIRECT_OUTPUT_SCHEMA: Final[str] = EXECUTOR_RESULT_SCHEMA
# The argument of the consent protocol: the runtime sets it after the person
# approved the card of the first call; a planner never writes it.
CONSENT_ARGUMENT: Final[str] = "actor_consent_token"

_DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
_ARGUMENT_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_PLACEHOLDER_RE = re.compile(
    r"(?:\$\{[^{}]+\}|\{\{[^{}]+\}\}|^\s*from_step\s*[:=]\s*\d+\s*$)"
)
_PIPE_ARGUMENTS = frozenset({"from_step", "from_steps"})
_STEP_REFERENCE_RE = re.compile(
    r"(?:\$\{\s*step\d|\{\{\s*step\d|^\s*from_step\s*[:=]\s*\d+\s*$)")
_JSON_TYPES = frozenset({
    "array", "boolean", "integer", "number", "object", "string",
})
_EFFECTS = {
    "read_only": DurableEffect.PURE.value,
    "create_only": DurableEffect.MANUAL_ONLY.value,
    "reversible": DurableEffect.MANUAL_ONLY.value,
    "mutating": DurableEffect.MANUAL_ONLY.value,
}
_RESOURCE_CLASS = {
    "default": "cpu",
    "cpu": "cpu",
    "local_io": "local_io",
    "network_io": "network_io",
    "llm": "llm",
    "vlm": "vlm",
    "browser": "network_io",
    "device": "device",
}


class DirectInvocationUnsupported(ValueError):
    """A finalized executor invocation cannot be represented by plan v1."""


class _CatalogView:
    """Small immutable-name view accepted by ``VerifiedCatalogResolver``."""

    def __init__(self, executors: Sequence[object]) -> None:
        self._executors = {
            str(getattr(executor, "name", "")): executor
            for executor in executors
            if isinstance(getattr(executor, "name", None), str)
        }

    def get(self, name: str) -> object | None:
        return self._executors.get(name)


def _catalog_executors(catalog: object) -> tuple[object, ...]:
    entries = getattr(catalog, "executors", None)
    if isinstance(entries, Mapping):
        values = entries.values()
    elif isinstance(catalog, Mapping):
        values = catalog.values()
    elif isinstance(catalog, Sequence) and not isinstance(catalog, (str, bytes)):
        values = catalog
    else:
        raise TypeError("catalog snapshot is not enumerable")
    return tuple(sorted(
        values,
        key=lambda item: str(getattr(item, "name", "")).encode("utf-8"),
    ))


def asks_the_person(executor: object) -> bool:
    """True when the signed contract makes the person part of every run.

    Three declarations say it, all of them in the signed execution policy or
    argument schema, never an executor name: an ``interactive`` effect; the
    consent token the runtime sets only after the person approved the card of
    the first call; a frozen plan the person confirms before it is applied.
    A missing or undeclared policy does not acquire this.
    """
    policy = getattr(executor, "execution_policy", None)
    if (
        getattr(executor, "execution_policy_declared", False) is not True
        or not isinstance(policy, Mapping)
    ):
        return False
    if policy.get("effect") == "interactive" or policy.get("frozen_plan"):
        return True
    schema = getattr(executor, "args_schema", None)
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    consent = (
        properties.get(CONSENT_ARGUMENT) if isinstance(properties, Mapping) else None
    )
    return isinstance(consent, Mapping) and consent.get("runtime_resolved") is True


def is_intrinsically_long(executor: object) -> bool:
    # Work the person takes part in stays in the foreground even when its
    # safety timeout is large: it asks and waits, so it cannot promise an
    # unattended restart or replay. This is classification before admission,
    # never a fallback after LRE rejects work.
    if asks_the_person(executor):
        return False
    timeout = getattr(executor, "timeout_s", None)
    return type(timeout) is int and timeout >= AUTO_LRE_MIN_TIMEOUT_S


def row_policy(executor):
    from partitioned_runs import policy_of
    policy = policy_of(executor)
    return policy if policy and policy["kind"] == "independent_rows" else None


def _require_persistable_schema(schema: Mapping[str, Any]) -> None:
    """Reject authority/secret annotations at every schema depth, not data depth."""

    pending: list[object] = [schema]
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        if not isinstance(node, Mapping) or id(node) in seen:
            continue
        seen.add(id(node))
        if len(seen) > 4096:
            raise DirectInvocationUnsupported("executor argument schema is too complex")
        if (
            node.get("runtime_resolved") is True
            or node.get("writeOnly") is True
            or node.get("sensitive") is True
            or node.get("format") in ("password", "secret")
        ):
            raise DirectInvocationUnsupported(
                "executor argument requires non-literal runtime authority"
            )
        if any(key in node for key in ("$ref", "$dynamicRef", "$recursiveRef")):
            raise DirectInvocationUnsupported("executor argument schema is not self-contained")
        for key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
            children = node.get(key)
            if isinstance(children, Mapping):
                pending.extend(children.values())
        for key in (
            "items", "additionalItems", "additionalProperties", "contains",
            "propertyNames", "not", "if", "then", "else",
            "unevaluatedItems", "unevaluatedProperties", "contentSchema",
        ):
            child = node.get(key)
            pending.extend(child if isinstance(child, list) else (child,))
        for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            children = node.get(key)
            if isinstance(children, list):
                pending.extend(children)
        # Draft-07 dependencies can be either schemas or arrays of names.
        dependencies = node.get("dependencies")
        if isinstance(dependencies, Mapping):
            pending.extend(dependencies.values())


def _argument_schema(executor: object) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    schema = getattr(executor, "args_schema", None)
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise DirectInvocationUnsupported("executor argument schema is not an object")
    policy = row_policy(executor)
    if policy:
        schema = {**schema, "properties": {name: {key: value for key, value in definition.items()
            if key != "runtime_resolved" or not (name == policy["list_arg"]
                or definition.get("enum") == [definition.get("default")])}
            for name, definition in schema["properties"].items()}}
    _require_persistable_schema(schema)
    additional = schema.get("additionalProperties")
    if additional is not None and additional is not False:
        raise DirectInvocationUnsupported("executor argument schema is open")
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise DirectInvocationUnsupported("executor argument schema has no properties")
    normalized: dict[str, Any] = {}
    for raw_name, raw_definition in properties.items():
        name = str(raw_name)
        if not _ARGUMENT_RE.fullmatch(name) or not isinstance(raw_definition, Mapping):
            raise DirectInvocationUnsupported("executor argument declaration is invalid")
        if raw_definition.get("type") not in _JSON_TYPES:
            raise DirectInvocationUnsupported("executor argument type is not closed")
        normalized[name] = raw_definition
    required = schema.get("required") or ()
    if (
        isinstance(required, (str, bytes))
        or not isinstance(required, Sequence)
        or any(not isinstance(name, str) for name in required)
        or len(required) != len(set(required))
        or not set(required) <= set(normalized)
    ):
        raise DirectInvocationUnsupported("executor required arguments are invalid")
    return normalized, tuple(sorted(required, key=str.encode))


def _durable_effect(executor: object) -> str:
    if not bool(getattr(executor, "execution_policy_declared", False)):
        raise DirectInvocationUnsupported("executor effect is not declared")
    policy = getattr(executor, "execution_policy", None)
    if not isinstance(policy, Mapping):
        raise DirectInvocationUnsupported("executor execution policy is invalid")
    if row_policy(executor) and policy.get("effect") != "read_only":
        return DurableEffect.RECONCILABLE.value
    try:
        return _EFFECTS[str(policy.get("effect"))]
    except KeyError as exc:
        raise DirectInvocationUnsupported("executor effect is not durable") from exc


def contract_refusal(executor: object) -> str | None:
    """Why this contract can never be a direct plan, or ``None``.

    The answer depends on the signed contract alone, not on one call: a long
    executor that gets a reason here is refused whatever it is asked. The
    reason is a fixed sentence, never an argument, a path or exception text.
    """
    try:
        if getattr(executor, "transport", "") == "in-process":
            return "executor runs in process"
        if getattr(executor, "intelligence", "") != "deterministic":
            return "executor is not deterministic"
        _argument_schema(executor)
        _durable_effect(executor)
        _resources(executor, {"target": "server"})
    except DirectInvocationUnsupported as exc:
        return str(exc)
    except (TypeError, ValueError):
        return "executor contract is malformed"
    return None


def _eligible(executor: object) -> bool:
    signed_by = str(getattr(executor, "signed_by", "") or "")
    digest = str(getattr(executor, "digest", "") or "")
    return bool(
        (is_intrinsically_long(executor) or row_policy(executor))
        and signed_by
        and not signed_by.startswith("(")
        and _DIGEST_RE.fullmatch(digest) is not None
        and getattr(executor, "lifecycle", "") == "active"
        and not bool(getattr(executor, "dormant", False))
        and contract_refusal(executor) is None
    )


def direct_runtime_registration(
    catalog_snapshot: object,
) -> RuntimeRegistration | None:
    """Return one catalog-derived runner contribution, or none when empty."""

    eligible = tuple(
        executor for executor in _catalog_executors(catalog_snapshot)
        if _eligible(executor)
    )
    if not eligible:
        return None
    view = _CatalogView(eligible)
    effects = {
        str(executor.name): (_durable_effect(executor),)
        for executor in eligible
    }
    schemas = {
        str(executor.name): (DIRECT_OUTPUT_SCHEMA,)
        for executor in eligible
    }
    resolver = VerifiedCatalogResolver(
        durable_output_schemas=schemas,
        durable_effects=effects,
        catalog_loader=lambda **_kwargs: view,
    )
    bindings = tuple(
        (RunnerKind.EXECUTOR, str(executor.name))
        for executor in eligible
    )
    # Resolve every declaration now: invalid signatures or contract facts must
    # fail process composition, never first execution.
    for _kind, name in bindings:
        resolver.resolve(RunnerKind.EXECUTOR.value, name)
    return RuntimeRegistration(
        name=DIRECT_REGISTRATION_ID,
        runner_bindings=bindings,
        runners=resolver,
        output_schemas=core_output_schemas(),
        output_schema_names=(),
    )


def _contains_placeholder(value: object) -> bool:
    if isinstance(value, str):
        return _PLACEHOLDER_RE.search(value) is not None
    if isinstance(value, list):
        return any(_contains_placeholder(item) for item in value)
    if isinstance(value, Mapping):
        return any(
            not isinstance(key, str) or _contains_placeholder(item)
            for key, item in value.items()
        )
    return False


def references_runtime(finalized_args: object) -> bool:
    """True when the arguments need a value only the interactive turn has:
    the result of another step, a filler or a runtime placeholder."""
    if not isinstance(finalized_args, Mapping):
        return False
    return (bool(set(finalized_args) & _PIPE_ARGUMENTS)
            or any(_contains_placeholder(value)
                   for value in finalized_args.values()))


def references_step(finalized_args: object) -> bool:
    """True when the arguments read the result of another step of the plan."""

    def reads(value: object) -> bool:
        if isinstance(value, str):
            return _STEP_REFERENCE_RE.search(value) is not None
        if isinstance(value, list):
            return any(reads(item) for item in value)
        if isinstance(value, Mapping):
            return any(reads(item) for item in value.values())
        return False

    return isinstance(finalized_args, Mapping) and (
        bool(set(finalized_args) & _PIPE_ARGUMENTS) or reads(finalized_args))


def _literal_args(
    executor: object,
    finalized_args: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    if not isinstance(finalized_args, Mapping):
        raise DirectInvocationUnsupported("finalized arguments are not an object")
    properties, required = _argument_schema(executor)
    if any(not isinstance(name, str) for name in finalized_args):
        raise DirectInvocationUnsupported("finalized argument names are invalid")
    names = set(finalized_args)
    if names - set(properties) or set(required) - names:
        raise DirectInvocationUnsupported("finalized arguments do not match the schema")
    if references_runtime(finalized_args):
        raise DirectInvocationUnsupported(
            "finalized arguments contain a runtime reference"
        )
    literals: dict[str, dict[str, Any]] = {}
    for name in sorted(names, key=str.encode):
        value = finalized_args[name]
        try:
            copied = json.loads(canonical_json(value, max_bytes=MAX_PLAN_JSON_BYTES))
        except Exception as exc:
            raise DirectInvocationUnsupported("finalized argument is not JSON") from exc
        literals[name] = {"ref": "literal", "value": copied}
    return literals


def _resources(executor: object, placement: Mapping[str, str]) -> dict[str, int]:
    resources = {name: 0 for name in RESOURCE_KEYS}
    policy = getattr(executor, "execution_policy", {})
    resource_class = (
        policy.get("resource_class") if isinstance(policy, Mapping) else None
    )
    resource = _RESOURCE_CLASS.get(str(resource_class))
    if resource is None:
        raise DirectInvocationUnsupported("executor resource class is unknown")
    if resource in {"llm", "vlm"}:
        raise DirectInvocationUnsupported(
            "executor model resource has no frozen direct binding"
        )
    resources[resource] = 1
    if placement["target"] == "device":
        resources["device"] = 1
    return resources


def _placement(
    executor: object,
    finalized_args: Mapping[str, Any],
    target_device: str | None,
) -> dict[str, str]:
    if target_device is not None and (
        not isinstance(target_device, str)
        or not 1 <= len(target_device) <= 128
        or "\x00" in target_device
    ):
        raise DirectInvocationUnsupported("execution placement is invalid")
    declared = getattr(executor, "placement", {}) or {}
    if not isinstance(declared, Mapping):
        raise DirectInvocationUnsupported("executor placement contract is invalid")
    scope = str(declared.get("scope") or "any").strip().lower()
    if scope not in {"any", "device", "server"}:
        raise DirectInvocationUnsupported("executor placement scope is unknown")
    if scope == "server":
        return {"target": "server"}
    if scope == "device":
        if target_device is None:
            raise DirectInvocationUnsupported(
                "device-only execution has no frozen target"
            )
        return {"target": "device", "device": target_device}
    if target_device is None or declared.get("device_ok") is not True:
        return {"target": "server"}

    # Provider-backed capabilities are pinned to the server by the normal
    # invocation boundary.  Use that same pure contract calculation here so
    # the persisted placement cannot claim a device and later run locally.
    try:
        from sandbox import invocation_skills

        if invocation_skills(executor, finalized_args):
            return {"target": "server"}
    except Exception as exc:
        raise DirectInvocationUnsupported(
            "executor placement authority cannot be resolved"
        ) from exc
    return {"target": "device", "device": target_device}


def _inventory_stage() -> dict[str, Any]:
    return {
        "key": "inventory",
        "type": "inventory",
        "depends_on": [],
        "runner": {"kind": "internal", "name": "sealed_inventory"},
        "effect_profile": DurableEffect.PURE.value,
        "cardinality": {"mode": "singleton", "max_units": 1},
        "input_bindings": {"inventory": {"ref": "revision.inventory"}},
        "output_schema": {
            "schema_version": "metnos.output-schema-ref/1",
            "name": "metnos.inventory-seal/1",
        },
        "retry": {
            "max_attempts": 1,
            "base_delay_ms": 0,
            "max_delay_ms": 0,
            "retryable_error_classes": [],
        },
        "timeout_s": 60,
        "invalidation_keys": ["source.digest"],
        "resources": {name: 0 for name in RESOURCE_KEYS},
        "required": True,
    }


def row_blocks(values):
    from server_filesystem import MAX_ITEMS
    return [{"partition": str(start).zfill(8), "rows": values[start:start + MAX_ITEMS]}
            for start in range(0, len(values), MAX_ITEMS)]


def build_direct_candidate(
    executor: object,
    finalized_args: Mapping[str, Any],
    target_device: str | None,
    *, policy=None, partitions=(), max_concurrency=1, row_references=(), owner_user_id=None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a canonicalizable two-stage plan without executing anything."""

    if not _eligible(executor):
        raise DirectInvocationUnsupported("executor contract is not admissible")
    if type(max_concurrency) is not int or not 1 <= max_concurrency <= 256:
        raise DirectInvocationUnsupported("unit concurrency is invalid")
    rows = []
    if row_references:
        from .storage import DurableWorkloadStore
        if policy != row_policy(executor) or not policy or policy["kind"] != "independent_rows":
            raise DirectInvocationUnsupported("references require independent rows")
        with DurableWorkloadStore.open() as store:
            if store.read_committed_entries(owner_user_id, row_references) != finalized_args[policy["list_arg"]]:
                row_references = ()
    bindings = _literal_args(executor, {**finalized_args, policy["list_arg"]: []} if row_references else finalized_args)
    if policy:
        properties, _ = _argument_schema(executor)
        if (policy["arg"] not in properties or policy["list_arg"] not in properties
                or properties[policy["list_arg"]].get("type") != "array"):
            raise DirectInvocationUnsupported("partition arguments are not declared")
        if policy["kind"] == "independent_rows":
            if policy != row_policy(executor) or max_concurrency != 1:
                raise DirectInvocationUnsupported("row contract or concurrency is invalid")
            values = finalized_args[policy["list_arg"]]
            if not isinstance(values, list) or not 1 <= len(values) <= 102400:
                raise DirectInvocationUnsupported("row list is invalid")
            rows = row_blocks(values)
            finalized_args = {key: value for key, value in finalized_args.items() if key != policy["list_arg"]}
            del bindings[policy["list_arg"]]
        else:
            values = list(partitions)
            if (not 1 <= len(values) <= 1024 or len(set(values)) != len(values)
                    or any(not isinstance(value, str) or not value or "\x00" in value for value in values)
                    or {policy["arg"], policy["list_arg"]} & set(finalized_args)):
                raise DirectInvocationUnsupported("partition list is invalid")
            rows = [{"partition": value} for value in values]
    from .schema import MAX_RESULT_JSON_BYTES
    rows = json.loads(canonical_json({"entries": rows}, max_bytes=MAX_RESULT_JSON_BYTES if row_references else MAX_PLAN_JSON_BYTES))["entries"]
    placement = _placement(executor, finalized_args, target_device)
    if policy and policy["kind"] == "independent_rows" and placement["target"] != "server":
        raise DirectInvocationUnsupported("row execution is server only")
    effect = _durable_effect(executor)
    attempts = 3 if effect == DurableEffect.PURE.value else 1
    timeout_s = min(86_400, int(executor.timeout_s))
    retry = {
        "max_attempts": attempts,
        "base_delay_ms": 1_000 if attempts > 1 else 0,
        "max_delay_ms": 60_000 if attempts > 1 else 0,
        "retryable_error_classes": (
            ["executor_transient"] if attempts > 1 else []
        ),
    }
    direct_stage = {
        "key": "execute",
        "type": "validate",
        "depends_on": ["inventory"],
        "runner": {"kind": "executor", "name": str(executor.name)},
        "effect_profile": effect,
        "cardinality": {"mode": "singleton", "max_units": 1},
        "input_bindings": bindings,
        "output_schema": {
            "schema_version": "metnos.output-schema-ref/1",
            "name": DIRECT_OUTPUT_SCHEMA,
        },
        "retry": retry,
        "timeout_s": timeout_s,
        "invalidation_keys": [
            "runner.contract_digest", "semantic_args.digest",
        ],
        "resources": _resources(executor, placement),
        "required": True,
        "placement": placement,
    }
    stages = [_inventory_stage()]
    if rows:
        listing = {**_inventory_stage(), "key": "partitions", "type": "validate",
            "depends_on": ["inventory"], "runner": {"kind": "internal", "name": "literal_entries"},
            "input_bindings": {"entries": {"ref": "literal", "value": rows}},
            "output_schema": {"schema_version": "metnos.output-schema-ref/1", "name": "metnos.partition-list/1"}}
        if row_references:
            listing.update(runner={"kind": "internal", "name": "committed_entries"},
                           input_bindings={"references": {"ref": "literal", "value": row_references}})
        stages.append(listing)
        direct_stage.update(type="map", depends_on=["partitions"],
            cardinality={"mode": "per_dependency", "max_units": len(rows), "entry_identity_field": "partition"})
        direct_stage["input_bindings"][policy["list_arg"]] = {"ref": "dependency.entries", "stage": "partitions"}
        direct_stage["invalidation_keys"].append("dependencies.digest")
    stages.append(direct_stage)
    plan = {
        "schema_version": "metnos.durable-plan/1",
        "plan_id": "metnos.partitioned.v1" if rows else DIRECT_PLAN_ID,
        "objective_redacted": "Automatic durable executor invocation.",
        "inventory": {
            "mode": "sealed",
            "dynamic": False,
            "max_sources": 0,
            "max_total_bytes": 0,
            "max_depth": 0,
            "symlink_policy": "ignore",
            "unstable_policy": "reject",
            "missing_policy": "needs_attention",
        },
        "terminal_criteria": {
            "require_inventory_sealed": True,
            "require_usage_complete": True,
            "reject_unaccepted_truncation": True,
        },
        "error_policy": {"mode": "declared" if rows else "strict",
                         "allowed_error_classes": ["contract_violation", "executor_permanent"] if rows else []},
        "budgets": {
            "max_units": len(rows) + 2 if rows else 2,
            "max_attempts_per_unit": attempts,
            "max_wall_time_s": min(
                2_592_000,
                120 + max(1, (len(rows) + max_concurrency - 1) // max_concurrency)
                * (timeout_s * attempts + 60 * max(0, attempts - 1)),
            ),
            "max_bytes_read": 9_223_372_036_854_775_807,
            # This counter includes the committed JSON result, not only an
            # executor's external mutations; zero would reject every useful
            # read-only observation after it had already run.
            "max_bytes_written": 9_223_372_036_854_775_807,
            "max_tokens": 0,
            "max_cost_micros": 0,
            "max_artifacts": 0,
            "max_concurrency": max_concurrency,
        },
        "stages": stages,
        "required_artifacts": [],
    }
    inventory = {
        "schema_version": "metnos.durable-inventory/1",
        "sealed": True,
        "digest": inventory_digest(()),
        "sources": [],
    }
    return plan, inventory


__all__ = [
    "AUTO_LRE_MIN_TIMEOUT_S",
    "DIRECT_PLAN_ID",
    "DirectInvocationUnsupported",
    "CONSENT_ARGUMENT",
    "asks_the_person",
    "build_direct_candidate",
    "contract_refusal",
    "direct_runtime_registration",
    "is_intrinsically_long",
    "references_runtime",
    "references_step",
]
