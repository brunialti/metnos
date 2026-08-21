"""Small canonical fixtures for durable-workload storage tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from durable_workloads.models import SourceResolution
from durable_workloads.schema import inventory_digest


def _resources() -> dict[str, int]:
    return {
        "cpu": 0,
        "local_io": 0,
        "network_io": 0,
        "llm": 0,
        "vlm": 0,
        "device": 0,
    }


def _retry() -> dict[str, Any]:
    return {
        "max_attempts": 1,
        "base_delay_ms": 0,
        "max_delay_ms": 0,
        "retryable_error_classes": [],
    }


def inventory_stage() -> dict[str, Any]:
    return {
        "key": "inventory",
        "type": "inventory",
        "depends_on": [],
        "runner": {"kind": "internal", "name": "sealed_inventory"},
        "effect_profile": "pure",
        "cardinality": {"mode": "singleton", "max_units": 1},
        "input_bindings": {"inventory": {"ref": "revision.inventory"}},
        "output_schema": {
            "schema_version": "metnos.output-schema-ref/1",
            "name": "metnos.inventory-seal/1",
        },
        "retry": _retry(),
        "timeout_s": 60,
        "invalidation_keys": ["source.digest"],
        "resources": _resources(),
        "required": True,
    }


def map_stage(*, required: bool = True) -> dict[str, Any]:
    return {
        "key": "map",
        "type": "map",
        "depends_on": ["inventory"],
        "runner": {"kind": "executor", "name": "read_files_ocr"},
        "effect_profile": "pure",
        "cardinality": {"mode": "per_source", "max_units": 100},
        "input_bindings": {"paths": {"ref": "source.path"}},
        "output_schema": {
            "schema_version": "metnos.output-schema-ref/1",
            "name": "metnos.test-map/1",
        },
        "retry": _retry(),
        "timeout_s": 60,
        "invalidation_keys": ["source.digest", "runner.contract_digest"],
        "resources": _resources(),
        "required": required,
    }


def plan(
    *,
    with_map: bool = False,
    required_artifacts: Sequence[Mapping[str, Any]] = (),
    error_mode: str = "strict",
    allowed_error_classes: Sequence[str] = (),
) -> dict[str, Any]:
    stages = [inventory_stage()]
    if with_map:
        stages.append(map_stage())
    return {
        "schema_version": "metnos.durable-plan/1",
        "plan_id": "tests.durable.v1",
        "objective_redacted": "Fixture sintetica senza dati personali.",
        "inventory": {
            "mode": "sealed",
            "dynamic": False,
            "max_sources": 100,
            "max_total_bytes": 104857600,
            "max_depth": 8,
            "symlink_policy": "ignore",
            "unstable_policy": "reject",
            "missing_policy": "needs_attention",
        },
        "terminal_criteria": {
            "require_inventory_sealed": True,
            "require_usage_complete": True,
            "reject_unaccepted_truncation": True,
        },
        "error_policy": {
            "mode": error_mode,
            "allowed_error_classes": list(allowed_error_classes),
        },
        "budgets": {
            "max_units": 1000,
            "max_attempts_per_unit": 3,
            "max_wall_time_s": 3600,
            "max_bytes_read": 104857600,
            "max_bytes_written": 10485760,
            "max_tokens": 10000,
            "max_cost_micros": 0,
            "max_artifacts": 8,
            "max_concurrency": 2,
        },
        "stages": stages,
        "required_artifacts": [dict(item) for item in required_artifacts],
    }


def source(
    ordinal: int,
    *,
    state: str = "ready",
    accounted: bool = True,
    source_id: str | None = None,
) -> dict[str, Any]:
    digit = f"{ordinal % 16:x}"
    return {
        "source_id": source_id or f"source_{ordinal:08d}",
        "ordinal": ordinal,
        "device_id": "device-test",
        "locator_redacted": f"fixture://source/{ordinal}",
        "kind": "image",
        "size_bytes": 1024,
        "mtime_ns": ordinal + 1,
        "content_digest": f"sha256:{digit * 64}",
        "state": state,
        "accounted": accounted,
    }


def source_resolution(
    item: Mapping[str, Any],
    value: object | None = None,
    *,
    authority: str = "test-rehash",
    **observed_overrides: object,
) -> SourceResolution:
    """Build a synthetic post-rehash attestation for bridge tests."""

    observed = {
        "source_id": item["source_id"],
        "device_id": item["device_id"],
        "content_digest": item["content_digest"],
        "size_bytes": item["size_bytes"],
        "mtime_ns": item["mtime_ns"],
    }
    observed.update(observed_overrides)
    return SourceResolution(
        value=(
            f"/authorized/{item['source_id']}.png"
            if value is None else value
        ),
        source_id=str(observed["source_id"]),
        device_id=str(observed["device_id"]),
        content_digest=str(observed["content_digest"]),
        size_bytes=int(observed["size_bytes"]),
        mtime_ns=int(observed["mtime_ns"]),
        authority=authority,
    )


def inventory(sources: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    normalized = [dict(item) for item in sources]
    return {
        "schema_version": "metnos.durable-inventory/1",
        "sealed": True,
        "digest": inventory_digest(normalized),
        "sources": normalized,
    }


def artifact_requirement(name: str = "report") -> dict[str, str]:
    return {
        "name": name,
        "mime_type": "application/json",
        "schema_version": "metnos.test-artifact/1",
        "publication": "internal_store",
    }
