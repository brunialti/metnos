"""Compile one finalized long executor invocation into a generic LRE plan."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from .compiler import VerifiedCatalogResolver, core_output_schemas
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
DIRECT_OUTPUT_SCHEMA: Final[str] = "metnos.executor-result/1"

_DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
_ARGUMENT_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_PLACEHOLDER_RE = re.compile(
    r"(?:\$\{[^{}]+\}|\{\{[^{}]+\}\}|^\s*from_step\s*[:=]\s*\d+\s*$)"
)
_PIPE_ARGUMENTS = frozenset({"from_step", "from_steps"})
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


def is_intrinsically_long(executor: object) -> bool:
    timeout = getattr(executor, "timeout_s", None)
    return type(timeout) is int and timeout >= AUTO_LRE_MIN_TIMEOUT_S


def _argument_schema(executor: object) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    schema = getattr(executor, "args_schema", None)
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise DirectInvocationUnsupported("executor argument schema is not an object")
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
        if (
            raw_definition.get("runtime_resolved") is True
            or raw_definition.get("writeOnly") is True
            or raw_definition.get("sensitive") is True
            or raw_definition.get("format") in {"password", "secret"}
        ):
            raise DirectInvocationUnsupported(
                "executor argument requires non-literal runtime authority"
            )
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
    try:
        return _EFFECTS[str(policy.get("effect"))]
    except KeyError as exc:
        raise DirectInvocationUnsupported("executor effect is not durable") from exc


def _eligible(executor: object) -> bool:
    try:
        signed_by = str(getattr(executor, "signed_by", "") or "")
        digest = str(getattr(executor, "digest", "") or "")
        if (
            not is_intrinsically_long(executor)
            or not signed_by
            or signed_by.startswith("(")
            or _DIGEST_RE.fullmatch(digest) is None
            or getattr(executor, "lifecycle", "") != "active"
            or bool(getattr(executor, "dormant", False))
            or getattr(executor, "transport", "") == "in-process"
            or getattr(executor, "intelligence", "") != "deterministic"
        ):
            return False
        _argument_schema(executor)
        _durable_effect(executor)
        _resources(executor, {"target": "server"})
        return True
    except (DirectInvocationUnsupported, TypeError, ValueError):
        return False


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
    if names & _PIPE_ARGUMENTS:
        raise DirectInvocationUnsupported(
            "finalized arguments contain a runtime reference"
        )
    literals: dict[str, dict[str, Any]] = {}
    for name in sorted(names, key=str.encode):
        value = finalized_args[name]
        if _contains_placeholder(value):
            raise DirectInvocationUnsupported(
                "finalized arguments contain a runtime reference"
            )
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


def build_direct_candidate(
    executor: object,
    finalized_args: Mapping[str, Any],
    target_device: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a canonicalizable two-stage plan without executing anything."""

    if not _eligible(executor):
        raise DirectInvocationUnsupported("executor contract is not admissible")
    bindings = _literal_args(executor, finalized_args)
    placement = _placement(executor, finalized_args, target_device)
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
    plan = {
        "schema_version": "metnos.durable-plan/1",
        "plan_id": DIRECT_PLAN_ID,
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
        "error_policy": {"mode": "strict", "allowed_error_classes": []},
        "budgets": {
            "max_units": 2,
            "max_attempts_per_unit": attempts,
            "max_wall_time_s": min(
                2_592_000,
                60 + timeout_s * attempts + (60 * max(0, attempts - 1)),
            ),
            "max_bytes_read": 9_223_372_036_854_775_807,
            # This counter includes the committed JSON result, not only an
            # executor's external mutations; zero would reject every useful
            # read-only observation after it had already run.
            "max_bytes_written": 9_223_372_036_854_775_807,
            "max_tokens": 0,
            "max_cost_micros": 0,
            "max_artifacts": 0,
            "max_concurrency": 1,
        },
        "stages": [_inventory_stage(), direct_stage],
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
    "build_direct_candidate",
    "direct_runtime_registration",
    "is_intrinsically_long",
]
