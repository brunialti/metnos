"""Compile one finalized long invocation on several partitions into a plan.

Sibling of ``direct_invocation``: the same admitted executor, the same literal
arguments, the same result envelope. What differs is the number of units. The
single-unit plan runs the tool once; this one runs it once per partition:

    inventory  ->  partitions  ->  execute
    (sealed)       (the list,      (one unit per row,
                    as rows)        each an ordinary call)

Each unit is given the row of its partition in the tool's list argument, so
the call enters the same choke point as any other (`partitioned_runs.run`) and
commits a result in the one partitioned shape. A partition the tool could not
work on is then an item lost by that unit, not a failed attempt: the durable
engine keeps the result and the other units go on
(`domain_outcome.item_failures_outcome`). A unit that crashes or times out is
still a failed attempt, retried and reported as for any job.

Nothing here reads a path or lists a folder: the partitions arrive already
reduced to literal values (`partitioned_runs.resolve`).
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

from .compiler import PARTITION_LIST_SCHEMA
from .direct_invocation import (
    DIRECT_OUTPUT_SCHEMA,
    DirectInvocationUnsupported,
    _argument_schema,
    _durable_effect,
    _eligible,
    _inventory_stage,
    _literal_args,
    _placement,
    _resources,
)
from .models import RESOURCE_KEYS, DurableEffect
from .schema import MAX_PLAN_JSON_BYTES, canonical_json, inventory_digest

PARTITIONED_PLAN_ID: Final[str] = "metnos.partitioned.v1"
PARTITIONS_STAGE: Final[str] = "partitions"
EXECUTE_STAGE: Final[str] = "execute"
PARTITION_FIELD: Final[str] = "partition"
# What a unit may fail on for good without sinking the others. Both leave no
# committed result for that partition, which the answer then names as missing.
_TOLERATED: Final[tuple[str, ...]] = ("contract_violation", "executor_permanent")
_MAX_WALL_TIME_S: Final[int] = 2_592_000


def unit_concurrency(executor: object, partitions: int) -> int:
    """How many units of one partitioned job may run together.

    One, for now. A tool the scheduler lets overlap (a declared class above
    zero) already spends the whole worker budget inside one call: several
    units together would each ask for all of it. Dividing that budget among
    the units in flight is the step that raises this number, and it is taken
    after the measure the design asks for (§7.7), not before.
    """
    del executor, partitions
    return 1


def build_partitioned_candidate(
    executor: object,
    policy: Mapping[str, Any],
    base_args: Mapping[str, Any],
    partitions: Sequence[str],
    target_device: str | None,
    *,
    max_concurrency: int = 1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a canonicalizable three-stage plan without executing anything.

    ``policy`` is the partition the tool declares; ``base_args`` the finalized
    arguments shared by every partition (neither the single argument nor the
    list); ``partitions`` the literal list, in the order to keep.
    """
    if not _eligible(executor):
        raise DirectInvocationUnsupported("executor contract is not admissible")
    arg, list_arg = str(policy["arg"]), str(policy["list_arg"])
    properties, _required = _argument_schema(executor)
    if (properties.get(arg, {}).get("type") != "string"
            or properties.get(list_arg, {}).get("type") != "array"):
        raise DirectInvocationUnsupported("partition arguments are not declared")
    if {arg, list_arg} & set(base_args):
        raise DirectInvocationUnsupported(
            "shared arguments cannot name a partition")
    values = list(partitions)
    if (not 1 <= len(values) <= 1024
            or len(set(values)) != len(values)
            or any(not isinstance(value, str) or not value or "\x00" in value
                   for value in values)):
        raise DirectInvocationUnsupported("partition list is invalid")
    if type(max_concurrency) is not int or not 1 <= max_concurrency <= 256:
        raise DirectInvocationUnsupported("unit concurrency is invalid")

    bindings = _literal_args(executor, base_args)
    # Placement and resources are those of the ordinary call on one partition.
    placement = _placement(executor, {**base_args, arg: values[0]}, target_device)
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
    rows = [{PARTITION_FIELD: value} for value in values]
    try:
        rows = json.loads(canonical_json(rows, max_bytes=MAX_PLAN_JSON_BYTES))
    except Exception as exc:
        raise DirectInvocationUnsupported("partition list is not JSON") from exc

    partitions_stage = {
        "key": PARTITIONS_STAGE,
        "type": "validate",
        "depends_on": ["inventory"],
        "runner": {"kind": "internal", "name": "literal_entries"},
        "effect_profile": DurableEffect.PURE.value,
        "cardinality": {"mode": "singleton", "max_units": 1},
        "input_bindings": {"entries": {"ref": "literal", "value": rows}},
        "output_schema": {
            "schema_version": "metnos.output-schema-ref/1",
            "name": PARTITION_LIST_SCHEMA,
        },
        "retry": {
            "max_attempts": 1, "base_delay_ms": 0, "max_delay_ms": 0,
            "retryable_error_classes": [],
        },
        "timeout_s": 60,
        "invalidation_keys": ["runner.contract_digest", "semantic_args.digest"],
        "resources": {name: 0 for name in RESOURCE_KEYS},
        "required": True,
    }
    execute_stage = {
        "key": EXECUTE_STAGE,
        "type": "map",
        "depends_on": [PARTITIONS_STAGE],
        "runner": {"kind": "executor", "name": str(executor.name)},
        "effect_profile": effect,
        "cardinality": {
            "mode": "per_dependency", "max_units": len(values),
            "entry_identity_field": PARTITION_FIELD,
        },
        # The unit's own row, in the list argument: one partition, resolved by
        # the choke point into the ordinary single call.
        "input_bindings": {
            **bindings,
            list_arg: {"ref": "dependency.entries", "stage": PARTITIONS_STAGE},
        },
        "output_schema": {
            "schema_version": "metnos.output-schema-ref/1",
            "name": DIRECT_OUTPUT_SCHEMA,
        },
        "retry": retry,
        "timeout_s": timeout_s,
        "invalidation_keys": [
            "dependencies.digest", "runner.contract_digest",
            "semantic_args.digest",
        ],
        "resources": _resources(executor, placement),
        "required": True,
        "placement": placement,
    }
    per_unit_s = timeout_s * attempts + 60 * max(0, attempts - 1)
    waves = -(-len(values) // max_concurrency)
    plan = {
        "schema_version": "metnos.durable-plan/1",
        "plan_id": PARTITIONED_PLAN_ID,
        "objective_redacted": (
            "Automatic durable executor invocation, one unit per partition."),
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
        # Partitions are independent: a unit that fails for good is counted
        # and named, and does not cancel the units of the other partitions.
        "error_policy": {
            "mode": "declared", "allowed_error_classes": list(_TOLERATED),
        },
        "budgets": {
            "max_units": len(values) + 2,
            "max_attempts_per_unit": attempts,
            "max_wall_time_s": min(_MAX_WALL_TIME_S, 120 + per_unit_s * waves),
            "max_bytes_read": 9_223_372_036_854_775_807,
            "max_bytes_written": 9_223_372_036_854_775_807,
            "max_tokens": 0,
            "max_cost_micros": 0,
            "max_artifacts": 0,
            "max_concurrency": max_concurrency,
        },
        "stages": [_inventory_stage(), partitions_stage, execute_stage],
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
    "EXECUTE_STAGE",
    "PARTITIONED_PLAN_ID",
    "PARTITIONS_STAGE",
    "build_partitioned_candidate",
    "unit_concurrency",
]
