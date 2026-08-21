"""Canonical JSON boundaries for the dormant durable-workload kernel."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .models import DurableEffect, RESOURCE_KEYS, RunnerKind, SourceState, StageType


PLAN_SCHEMA_VERSION = "metnos.durable-plan/1"
INVENTORY_SCHEMA_VERSION = "metnos.durable-inventory/1"
EVENT_SCHEMA_VERSION = "metnos.durable-event/1"
ERROR_SCHEMA_VERSION = "metnos.durable-error/1"
IMAGE_OUTPUT_SCHEMA_VERSION = "metnos.image-preset-output/1"

MAX_PLAN_JSON_BYTES = 1_048_576
MAX_INVENTORY_JSON_BYTES = 16_777_216
MAX_SNAPSHOT_JSON_BYTES = 4_194_304
MAX_EVENT_JSON_BYTES = 65_536
MAX_ERROR_JSON_BYTES = 65_536
MAX_RESULT_JSON_BYTES = 8_388_608

_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ERROR_CLASS_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_PLAN_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
_RUNNER_RE = re.compile(r"^[a-z_][a-z0-9_.-]{1,95}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SCHEMA_REF_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,127}/[1-9][0-9]*$")
_ARTIFACT_SCHEMA_RE = re.compile(r"^metnos\.[a-z0-9_.-]+/[1-9][0-9]*$")
_MIME_RE = re.compile(r"^[a-z0-9.+-]+/[a-z0-9.+-]+$")
_INTERNAL_RUNNERS = frozenset({
    "sealed_inventory",
    "schema_and_coverage_validator",
    "artifact_store_publish",
})
_REFERENCE_KINDS = frozenset({
    "revision.inventory",
    "source.path",
    "source.record",
    "dependency.result",
    "dependency.entries",
})
_INVALIDATION_KEYS = frozenset({
    "source.digest",
    "dependencies.digest",
    "runner.contract_digest",
    "semantic_args.digest",
    "model_binding.digest",
    "prompt.digest",
    "reduction.order",
    "reduction.fan_in",
})


class SchemaValidationError(ValueError):
    """A versioned payload is malformed, oversized or incompatible."""


class _BoundedUniqueValues:
    """Exact uniqueness check that spills to a disposable database."""

    _MEMORY_LIMIT = 4096

    def __init__(self) -> None:
        self._memory: set[str] | None = set()
        self._connection: sqlite3.Connection | None = None

    def add(self, value: str) -> bool:
        memory = self._memory
        if memory is not None:
            if value in memory:
                return False
            memory.add(value)
            if len(memory) <= self._MEMORY_LIMIT:
                return True
            connection = sqlite3.connect("")
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute(
                "CREATE TABLE values_seen (value TEXT PRIMARY KEY) WITHOUT ROWID"
            )
            connection.executemany(
                "INSERT INTO values_seen(value) VALUES (?)",
                ((item,) for item in memory),
            )
            self._connection = connection
            self._memory = None
            return True
        assert self._connection is not None
        try:
            self._connection.execute(
                "INSERT INTO values_seen(value) VALUES (?)", (value,)
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def canonical_json(value: Any, *, max_bytes: int) -> str:
    """Return deterministic UTF-8 JSON and enforce the boundary byte cap."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(f"value is not canonical JSON: {exc}") from exc
    size = len(encoded.encode("utf-8"))
    if size > max_bytes:
        raise SchemaValidationError(
            f"canonical JSON is {size} bytes; limit is {max_bytes}"
        )
    return encoded


def digest_json(domain: str, value: Any, *, max_bytes: int) -> str:
    if not isinstance(domain, str) or not domain or "\x00" in domain:
        raise SchemaValidationError("digest domain must be a non-empty string")
    canonical = canonical_json(value, max_bytes=max_bytes)
    payload = f"metnos:{domain}:1\x00{canonical}".encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _object(
    value: Any,
    *,
    context: str,
    required: set[str],
    allowed: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{context} must be an object")
    keys = set(value)
    missing = sorted(required - keys)
    unknown = sorted(keys - allowed)
    if missing:
        raise SchemaValidationError(f"{context} misses fields: {missing}")
    if unknown:
        raise SchemaValidationError(f"{context} has unknown fields: {unknown}")
    return value


def _integer(value: Any, *, context: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaValidationError(f"{context} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f"..{maximum}" if maximum is not None else " or greater"
        raise SchemaValidationError(f"{context} must be {minimum}{suffix}")
    return value


def _string(value: Any, *, context: str, minimum: int = 1, maximum: int = 2048) -> str:
    if not isinstance(value, str) or not (minimum <= len(value) <= maximum):
        raise SchemaValidationError(
            f"{context} must be a string of {minimum}..{maximum} characters"
        )
    return value


def _closed_strings(value: Any, *, context: str, maximum: int = 32) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SchemaValidationError(f"{context} must be an array")
    if len(value) > maximum:
        raise SchemaValidationError(f"{context} exceeds {maximum} items")
    items: list[str] = []
    for index, item in enumerate(value):
        text = _string(item, context=f"{context}[{index}]", maximum=96)
        if not _KEY_RE.fullmatch(text):
            raise SchemaValidationError(f"{context}[{index}] is not a closed key")
        items.append(text)
    if len(items) != len(set(items)):
        raise SchemaValidationError(f"{context} contains duplicates")
    return tuple(items)


def _validate_inventory_contract(value: Any) -> None:
    contract = _object(
        value,
        context="plan.inventory",
        required={
            "mode", "dynamic", "max_sources", "max_total_bytes", "max_depth",
            "symlink_policy", "unstable_policy", "missing_policy",
        },
        allowed={
            "mode", "dynamic", "max_sources", "max_total_bytes", "max_depth",
            "symlink_policy", "unstable_policy", "missing_policy",
        },
    )
    if contract["mode"] != "sealed" or contract["dynamic"] is not False:
        raise SchemaValidationError("v1 inventory must be sealed and non-dynamic")
    _integer(contract["max_sources"], context="inventory.max_sources", minimum=0, maximum=1_000_000)
    _integer(contract["max_total_bytes"], context="inventory.max_total_bytes", minimum=0)
    _integer(contract["max_depth"], context="inventory.max_depth", minimum=0, maximum=128)
    if contract["symlink_policy"] != "ignore":
        raise SchemaValidationError("v1 symlink_policy must be ignore")
    if contract["unstable_policy"] != "reject":
        raise SchemaValidationError("v1 unstable_policy must be reject")
    if contract["missing_policy"] != "needs_attention":
        raise SchemaValidationError("v1 missing_policy must be needs_attention")


def _validate_stage(stage: Any, index: int) -> Mapping[str, Any]:
    context = f"plan.stages[{index}]"
    fields = {
        "key", "type", "depends_on", "runner", "effect_profile",
        "cardinality", "input_bindings", "output_schema", "retry", "timeout_s",
        "invalidation_keys", "resources", "required",
    }
    item = _object(stage, context=context, required=fields, allowed=fields)
    key = _string(item["key"], context=f"{context}.key", maximum=64)
    if not _KEY_RE.fullmatch(key):
        raise SchemaValidationError(f"{context}.key is not a closed key")
    try:
        stage_type = StageType(item["type"])
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(f"{context}.type is unknown") from exc

    dependencies = _closed_strings(item["depends_on"], context=f"{context}.depends_on")
    if key in dependencies:
        raise SchemaValidationError(f"{context} cannot depend on itself")

    runner = _object(
        item["runner"], context=f"{context}.runner",
        required={"kind", "name"}, allowed={"kind", "name"},
    )
    try:
        runner_kind = RunnerKind(runner["kind"])
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(f"{context}.runner.kind is unknown") from exc
    runner_name = _string(runner["name"], context=f"{context}.runner.name", maximum=96)
    if not _RUNNER_RE.fullmatch(runner_name):
        raise SchemaValidationError(f"{context}.runner.name is invalid")
    if runner_kind is RunnerKind.INTERNAL and runner_name not in _INTERNAL_RUNNERS:
        raise SchemaValidationError(f"{context}.runner.name is not a v1 internal runner")

    try:
        effect = DurableEffect(item["effect_profile"])
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(f"{context}.effect_profile is unknown") from exc

    cardinality = _object(
        item["cardinality"], context=f"{context}.cardinality",
        required={"mode", "max_units"},
        allowed={
            "mode", "max_units", "entry_identity_field", "fan_in",
            "reduction_input", "max_input_bytes",
        },
    )
    if cardinality["mode"] not in {"singleton", "per_source", "per_dependency"}:
        raise SchemaValidationError(f"{context}.cardinality.mode is unknown")
    _integer(cardinality["max_units"], context=f"{context}.cardinality.max_units", minimum=1, maximum=10_000_000)
    entry_identity_field = cardinality.get("entry_identity_field")
    if entry_identity_field is not None:
        if cardinality["mode"] != "per_dependency":
            raise SchemaValidationError(
                f"{context}.cardinality.entry_identity_field requires per_dependency"
            )
        identity_name = _string(
            entry_identity_field,
            context=f"{context}.cardinality.entry_identity_field",
            maximum=64,
        )
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", identity_name):
            raise SchemaValidationError(
                f"{context}.cardinality.entry_identity_field is invalid"
            )
    reduction_fields = (
        cardinality.get("fan_in"),
        cardinality.get("reduction_input"),
        cardinality.get("max_input_bytes"),
    )
    if any(value is not None for value in reduction_fields):
        if any(value is None for value in reduction_fields):
            raise SchemaValidationError(
                f"{context}.cardinality requires fan_in, reduction_input "
                "and max_input_bytes together"
            )
        if stage_type is not StageType.REDUCE or cardinality["mode"] != "singleton":
            raise SchemaValidationError(
                f"{context}.cardinality hierarchical reduction requires "
                "a singleton reduce stage"
            )
        _integer(
            cardinality["fan_in"],
            context=f"{context}.cardinality.fan_in",
            minimum=2,
            maximum=1024,
        )
        reduction_input = _string(
            cardinality["reduction_input"],
            context=f"{context}.cardinality.reduction_input",
            maximum=64,
        )
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reduction_input):
            raise SchemaValidationError(
                f"{context}.cardinality.reduction_input is invalid"
            )
        _integer(
            cardinality["max_input_bytes"],
            context=f"{context}.cardinality.max_input_bytes",
            minimum=1024,
            maximum=16_777_216,
        )

    bindings = item["input_bindings"]
    if not isinstance(bindings, Mapping) or len(bindings) > 64:
        raise SchemaValidationError(f"{context}.input_bindings must be a bounded object")
    for name, raw_reference in bindings.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise SchemaValidationError(f"{context}.input_bindings has an invalid key")
        reference = _object(
            raw_reference, context=f"{context}.input_bindings.{name}",
            required={"ref"}, allowed={"ref", "stage", "field"},
        )
        ref = reference["ref"]
        if ref not in _REFERENCE_KINDS:
            raise SchemaValidationError(f"{context}.input_bindings.{name}.ref is unknown")
        if str(ref).startswith("dependency."):
            dependency_stage = reference.get("stage")
            if dependency_stage not in dependencies:
                raise SchemaValidationError(
                    f"{context}.input_bindings.{name}.stage must be a direct dependency"
                )
        elif "stage" in reference:
            raise SchemaValidationError(
                f"{context}.input_bindings.{name}.stage is only valid for dependencies"
            )
        if "field" in reference:
            field = _string(reference["field"], context=f"{context}.input_bindings.{name}.field", maximum=128)
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", field):
                raise SchemaValidationError(f"{context}.input_bindings.{name}.field is invalid")

    reduction_input = cardinality.get("reduction_input")
    if reduction_input is not None:
        reference = bindings.get(str(reduction_input))
        if (
            len(dependencies) != 1
            or not isinstance(reference, Mapping)
            or reference.get("ref") != "dependency.entries"
            or reference.get("stage") != dependencies[0]
            or reference.get("field") is not None
        ):
            raise SchemaValidationError(
                f"{context}.cardinality.reduction_input must bind the entries "
                "of the stage's sole dependency"
            )

    output_schema = _object(
        item["output_schema"], context=f"{context}.output_schema",
        required={"schema_version", "name"}, allowed={"schema_version", "name"},
    )
    if output_schema["schema_version"] != "metnos.output-schema-ref/1":
        raise SchemaValidationError(f"{context}.output_schema has an incompatible version")
    if not isinstance(output_schema["name"], str) or not _SCHEMA_REF_RE.fullmatch(output_schema["name"]):
        raise SchemaValidationError(f"{context}.output_schema.name is invalid")

    retry = _object(
        item["retry"], context=f"{context}.retry",
        required={"max_attempts", "base_delay_ms", "max_delay_ms", "retryable_error_classes"},
        allowed={"max_attempts", "base_delay_ms", "max_delay_ms", "retryable_error_classes"},
    )
    attempts = _integer(retry["max_attempts"], context=f"{context}.retry.max_attempts", minimum=1, maximum=32)
    base_delay = _integer(retry["base_delay_ms"], context=f"{context}.retry.base_delay_ms", minimum=0, maximum=86_400_000)
    max_delay = _integer(retry["max_delay_ms"], context=f"{context}.retry.max_delay_ms", minimum=0, maximum=86_400_000)
    if base_delay > max_delay:
        raise SchemaValidationError(f"{context}.retry base delay exceeds maximum")
    retryable_errors = _closed_strings(
        retry["retryable_error_classes"],
        context=f"{context}.retry.retryable_error_classes",
    )
    if any(not _ERROR_CLASS_RE.fullmatch(name) for name in retryable_errors):
        raise SchemaValidationError(
            f"{context}.retry.retryable_error_classes contains an invalid class"
        )
    if effect is DurableEffect.MANUAL_ONLY and attempts != 1:
        raise SchemaValidationError(f"{context}: manual_only cannot retry automatically")

    _integer(item["timeout_s"], context=f"{context}.timeout_s", minimum=1, maximum=86_400)
    invalidation = _closed_strings(item["invalidation_keys"], context=f"{context}.invalidation_keys", maximum=16)
    if not invalidation or not set(invalidation) <= _INVALIDATION_KEYS:
        raise SchemaValidationError(f"{context}.invalidation_keys contains an unknown key")

    resources = _object(
        item["resources"], context=f"{context}.resources",
        required=set(RESOURCE_KEYS), allowed=set(RESOURCE_KEYS),
    )
    for resource_name, amount in resources.items():
        maximum = 32 if resource_name in {"llm", "vlm"} else 64
        _integer(
            amount,
            context=f"{context}.resources.{resource_name}",
            minimum=0,
            maximum=maximum,
        )
    if not isinstance(item["required"], bool):
        raise SchemaValidationError(f"{context}.required must be boolean")

    if stage_type is StageType.INVENTORY:
        if dependencies or runner_kind is not RunnerKind.INTERNAL or runner_name != "sealed_inventory":
            raise SchemaValidationError("inventory stage must be a root sealed_inventory runner")
        if cardinality["mode"] != "singleton":
            raise SchemaValidationError("inventory stage cardinality must be singleton")
    if stage_type is StageType.PUBLISH and runner_kind is RunnerKind.INTERNAL:
        if runner_name != "artifact_store_publish":
            raise SchemaValidationError("internal publish must use artifact_store_publish")
        if effect is not DurableEffect.IDEMPOTENT:
            raise SchemaValidationError("internal artifact publication must be idempotent")
    return item


def _assert_acyclic(stages: Sequence[Mapping[str, Any]]) -> None:
    graph = {str(stage["key"]): tuple(stage["depends_on"]) for stage in stages}
    names = set(graph)
    for stage, dependencies in graph.items():
        unknown = sorted(set(dependencies) - names)
        if unknown:
            raise SchemaValidationError(f"stage {stage} depends on unknown stages: {unknown}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise SchemaValidationError(f"stage graph contains a cycle at {node}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for name in graph:
        visit(name)


def validate_plan(value: Any) -> str:
    """Validate a plan-v1 object and return its canonical representation."""
    fields = {
        "schema_version", "plan_id", "objective_redacted", "inventory",
        "terminal_criteria", "error_policy", "budgets", "stages",
        "required_artifacts",
    }
    plan = _object(value, context="plan", required=fields, allowed=fields)
    if plan["schema_version"] != PLAN_SCHEMA_VERSION:
        raise SchemaValidationError("unsupported durable plan schema_version")
    plan_id = _string(plan["plan_id"], context="plan.plan_id", maximum=64)
    if not _PLAN_ID_RE.fullmatch(plan_id):
        raise SchemaValidationError("plan.plan_id is invalid")
    _string(plan["objective_redacted"], context="plan.objective_redacted", maximum=2048)
    _validate_inventory_contract(plan["inventory"])

    terminal = _object(
        plan["terminal_criteria"], context="plan.terminal_criteria",
        required={"require_inventory_sealed", "require_usage_complete", "reject_unaccepted_truncation"},
        allowed={"require_inventory_sealed", "require_usage_complete", "reject_unaccepted_truncation"},
    )
    if any(terminal[name] is not True for name in terminal):
        raise SchemaValidationError("all v1 terminal criteria must be enabled")

    error_policy = _object(
        plan["error_policy"], context="plan.error_policy",
        required={"mode", "allowed_error_classes"}, allowed={"mode", "allowed_error_classes"},
    )
    if error_policy["mode"] not in {"strict", "declared"}:
        raise SchemaValidationError("plan.error_policy.mode is unknown")
    allowed_errors = _closed_strings(error_policy["allowed_error_classes"], context="plan.error_policy.allowed_error_classes")
    if any(not _ERROR_CLASS_RE.fullmatch(name) for name in allowed_errors):
        raise SchemaValidationError(
            "plan.error_policy.allowed_error_classes contains an invalid class"
        )
    if error_policy["mode"] == "strict" and allowed_errors:
        raise SchemaValidationError("strict error policy cannot allow error classes")
    if error_policy["mode"] == "declared" and not allowed_errors:
        raise SchemaValidationError("declared error policy needs at least one class")

    budget_keys = {
        "max_units", "max_attempts_per_unit", "max_wall_time_s", "max_bytes_read",
        "max_bytes_written", "max_tokens", "max_cost_micros", "max_artifacts",
        "max_concurrency",
    }
    budgets = _object(plan["budgets"], context="plan.budgets", required=budget_keys, allowed=budget_keys)
    _integer(budgets["max_units"], context="plan.budgets.max_units", minimum=1, maximum=10_000_000)
    _integer(budgets["max_attempts_per_unit"], context="plan.budgets.max_attempts_per_unit", minimum=1, maximum=32)
    _integer(budgets["max_wall_time_s"], context="plan.budgets.max_wall_time_s", minimum=1, maximum=2_592_000)
    for name in ("max_bytes_read", "max_bytes_written", "max_tokens", "max_cost_micros"):
        _integer(
            budgets[name],
            context=f"plan.budgets.{name}",
            minimum=0,
            maximum=9_223_372_036_854_775_807,
        )
    _integer(budgets["max_artifacts"], context="plan.budgets.max_artifacts", minimum=0, maximum=1024)
    _integer(budgets["max_concurrency"], context="plan.budgets.max_concurrency", minimum=1, maximum=256)

    raw_stages = plan["stages"]
    if isinstance(raw_stages, (str, bytes)) or not isinstance(raw_stages, Sequence):
        raise SchemaValidationError("plan.stages must be an array")
    if not (1 <= len(raw_stages) <= 64):
        raise SchemaValidationError("plan.stages must contain 1..64 stages")
    stages = tuple(_validate_stage(stage, index) for index, stage in enumerate(raw_stages))
    keys = tuple(str(stage["key"]) for stage in stages)
    if len(keys) != len(set(keys)):
        raise SchemaValidationError("plan stage keys must be unique")
    if sum(stage["type"] == StageType.INVENTORY.value for stage in stages) != 1:
        raise SchemaValidationError("plan v1 requires exactly one inventory stage")
    _assert_acyclic(stages)
    if sum(int(stage["cardinality"]["max_units"]) for stage in stages) > int(
        budgets["max_units"]
    ):
        raise SchemaValidationError(
            "sum of stage max_units exceeds plan.budgets.max_units"
        )
    if any(
        int(stage["retry"]["max_attempts"])
        > int(budgets["max_attempts_per_unit"])
        for stage in stages
    ):
        raise SchemaValidationError(
            "a stage retry limit exceeds plan.budgets.max_attempts_per_unit"
        )

    artifacts = plan["required_artifacts"]
    if isinstance(artifacts, (str, bytes)) or not isinstance(artifacts, Sequence) or len(artifacts) > 32:
        raise SchemaValidationError("plan.required_artifacts must be a bounded array")
    artifact_names: list[str] = []
    for index, raw_artifact in enumerate(artifacts):
        artifact = _object(
            raw_artifact, context=f"plan.required_artifacts[{index}]",
            required={"name", "mime_type", "schema_version", "publication"},
            allowed={"name", "mime_type", "schema_version", "publication"},
        )
        name = _string(artifact["name"], context=f"artifact[{index}].name", maximum=64)
        if not _KEY_RE.fullmatch(name):
            raise SchemaValidationError(f"artifact[{index}].name is invalid")
        artifact_names.append(name)
        if not isinstance(artifact["mime_type"], str) or not _MIME_RE.fullmatch(artifact["mime_type"]):
            raise SchemaValidationError(f"artifact[{index}].mime_type is invalid")
        if not isinstance(artifact["schema_version"], str) or not _ARTIFACT_SCHEMA_RE.fullmatch(artifact["schema_version"]):
            raise SchemaValidationError(f"artifact[{index}].schema_version is invalid")
        if artifact["publication"] != "internal_store":
            raise SchemaValidationError("v1 permits only internal_store publication")
    if len(artifact_names) != len(set(artifact_names)):
        raise SchemaValidationError("required artifact names must be unique")
    if len(artifact_names) > budgets["max_artifacts"]:
        raise SchemaValidationError("required artifacts exceed max_artifacts")
    return canonical_json(plan, max_bytes=MAX_PLAN_JSON_BYTES)


def plan_digest(value: Any) -> str:
    validate_plan(value)
    return digest_json("durable-plan", value, max_bytes=MAX_PLAN_JSON_BYTES)


def _inventory_hasher() -> Any:
    hasher = hashlib.sha256()
    hasher.update(b"metnos:durable-inventory:1\x00[")
    return hasher


def _inventory_source_json(source: Mapping[str, Any]) -> str:
    # The inventory as a whole can be much larger than its optional inline
    # copy.  A single source record is nevertheless bounded by its validated
    # fields, so canonicalising one record at a time keeps memory constant.
    return canonical_json(source, max_bytes=MAX_INVENTORY_JSON_BYTES)


def inventory_digest(sources: Sequence[Mapping[str, Any]]) -> str:
    """Hash a canonical source array without materialising the whole array."""

    hasher = _inventory_hasher()
    for index, source in enumerate(sources):
        if index:
            hasher.update(b",")
        hasher.update(_inventory_source_json(source).encode("utf-8"))
    hasher.update(b"]")
    return f"sha256:{hasher.hexdigest()}"


def validate_inventory(
    value: Any,
) -> tuple[str | None, Sequence[Mapping[str, Any]]]:
    """Validate a sealed inventory and return its optional inline encoding.

    Source rows are authoritative once admitted.  Inventories up to
    ``MAX_INVENTORY_JSON_BYTES`` also retain the original canonical envelope
    for bounded ``revision.inventory`` bindings.  Larger inventories are not
    rejected or duplicated into one giant JSON value: their envelope is
    represented by the digest, count and normalized ``sources`` rows.
    """

    inventory = _object(
        value, context="inventory",
        required={"schema_version", "sealed", "digest", "sources"},
        allowed={"schema_version", "sealed", "digest", "sources"},
    )
    if inventory["schema_version"] != INVENTORY_SCHEMA_VERSION:
        raise SchemaValidationError("unsupported inventory schema_version")
    if inventory["sealed"] is not True:
        raise SchemaValidationError("inventory must be sealed")
    sources = inventory["sources"]
    if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
        raise SchemaValidationError("inventory.sources must be an array")
    if len(sources) > 1_000_000:
        raise SchemaValidationError("inventory.sources exceeds the v1 limit")
    source_ids = _BoundedUniqueValues()
    inline_buffer: io.StringIO | None = None
    try:
        declared_digest = inventory["digest"]
        if not isinstance(declared_digest, str) or not _DIGEST_RE.fullmatch(
            declared_digest
        ):
            raise SchemaValidationError("inventory.digest is invalid")

        inline_prefix = (
            '{"digest":'
            + json.dumps(
                declared_digest,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + ',"schema_version":'
            + json.dumps(
                INVENTORY_SCHEMA_VERSION,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + ',"sealed":true,"sources":['
        )
        inline_buffer = io.StringIO()
        inline_buffer.write(inline_prefix)
        inline_bytes = len(inline_prefix.encode("utf-8")) + 2  # closing ]}
        hasher = _inventory_hasher()
        for index, raw_source in enumerate(sources):
            fields = {
                "source_id", "ordinal", "device_id", "locator_redacted",
                "kind", "size_bytes", "mtime_ns", "content_digest",
                "state", "accounted",
            }
            source = _object(
                raw_source,
                context=f"inventory.sources[{index}]",
                required=fields,
                allowed=fields,
            )
            source_id = _string(
                source["source_id"],
                context=f"source[{index}].source_id",
                minimum=8,
                maximum=160,
            )
            if not source_ids.add(source_id):
                raise SchemaValidationError(
                    "inventory source_id values must be unique"
                )
            if source["ordinal"] != index:
                raise SchemaValidationError(
                    "inventory ordinals must be contiguous from zero"
                )
            _string(
                source["device_id"],
                context=f"source[{index}].device_id",
                maximum=128,
            )
            _string(
                source["locator_redacted"],
                context=f"source[{index}].locator_redacted",
                maximum=1024,
            )
            _string(
                source["kind"],
                context=f"source[{index}].kind",
                maximum=64,
            )
            _integer(
                source["size_bytes"],
                context=f"source[{index}].size_bytes",
                minimum=0,
            )
            _integer(
                source["mtime_ns"],
                context=f"source[{index}].mtime_ns",
                minimum=0,
            )
            if (
                not isinstance(source["content_digest"], str)
                or not _DIGEST_RE.fullmatch(source["content_digest"])
            ):
                raise SchemaValidationError(
                    f"source[{index}].content_digest is invalid"
                )
            try:
                SourceState(source["state"])
            except (TypeError, ValueError) as exc:
                raise SchemaValidationError(
                    f"source[{index}].state is unknown"
                ) from exc
            if not isinstance(source["accounted"], bool):
                raise SchemaValidationError(
                    f"source[{index}].accounted must be boolean"
                )
            source_json = _inventory_source_json(source)
            source_bytes = source_json.encode("utf-8")
            if index:
                hasher.update(b",")
            hasher.update(source_bytes)
            if inline_buffer is not None:
                separator_bytes = int(index > 0)
                if (
                    inline_bytes + separator_bytes + len(source_bytes)
                    > MAX_INVENTORY_JSON_BYTES
                ):
                    inline_buffer.close()
                    inline_buffer = None
                else:
                    if index:
                        inline_buffer.write(",")
                    inline_buffer.write(source_json)
                    inline_bytes += separator_bytes + len(source_bytes)
        hasher.update(b"]")
        expected_digest = f"sha256:{hasher.hexdigest()}"
        if declared_digest != expected_digest:
            raise SchemaValidationError(
                "inventory digest does not match canonical sources"
            )
        canonical = None
        if inline_buffer is not None:
            inline_buffer.write("]}")
            canonical = inline_buffer.getvalue()
        return canonical, sources
    finally:
        source_ids.close()
        if inline_buffer is not None:
            inline_buffer.close()


def validate_event_payload(value: Any) -> str:
    if not isinstance(value, Mapping):
        raise SchemaValidationError("event payload must be an object")
    if len(value) > 64:
        raise SchemaValidationError("event payload exceeds 64 fields")
    return canonical_json(value, max_bytes=MAX_EVENT_JSON_BYTES)
