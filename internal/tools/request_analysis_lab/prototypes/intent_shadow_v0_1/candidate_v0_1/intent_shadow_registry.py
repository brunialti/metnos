"""Validation and identity of a frozen intent-shadow registry."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from intent_shadow_io import (
    DEFAULT_OFFLINE_LIMITS,
    StrictJsonError,
    TechnicalLimits,
    canonical_sha256,
    file_sha256,
    json_path,
    require_exact_keys,
    require_exact_type,
    strict_json_file,
)


REGISTRY_FORMAT = "metnos.intent-shadow-registry/0.1"
CONTRACT_VERSION = "metnos.intent-shadow/0.1"


@dataclass(frozen=True, slots=True)
class RegistryIdentity:
    file_sha256: str
    payload_sha256: str


def registry_payload_sha256(registry: dict[str, Any]) -> str:
    payload = deepcopy(registry)
    integrity = payload.get("integrity")
    if type(integrity) is dict:
        integrity.pop("registry_payload_sha256", None)
    return canonical_sha256(payload)


def _string_list(value: Any, path: str, *, nonempty: bool) -> list[str]:
    require_exact_type(value, list, path)
    if nonempty and not value:
        raise StrictJsonError("REGISTRY_EMPTY_LIST", path, "must not be empty")
    if any(type(item) is not str or not item for item in value):
        raise StrictJsonError("REGISTRY_STRING_LIST", path, "invalid item")
    if len(value) != len(set(value)):
        raise StrictJsonError("REGISTRY_DUPLICATE", path, "duplicate item")
    return value


def validate_registry_document(
    registry: Any,
    *,
    verify_integrity: bool = True,
) -> dict[str, Any]:
    require_exact_type(registry, dict, "$registry")
    for key in (
        "registry_format",
        "contract_version",
        "operations",
        "system_controls",
        "barriers",
        "unrepresentable_reasons",
        "integrity",
    ):
        if key not in registry:
            raise StrictJsonError("REGISTRY_KEY", "$registry", f"missing={key!r}")
    if registry["registry_format"] != REGISTRY_FORMAT:
        raise StrictJsonError("REGISTRY_FORMAT", "$registry.registry_format", "mismatch")
    if registry["contract_version"] != CONTRACT_VERSION:
        raise StrictJsonError("CONTRACT_VERSION", "$registry.contract_version", "mismatch")

    operations = registry["operations"]
    require_exact_type(operations, dict, "$registry.operations")
    if not operations:
        raise StrictJsonError("REGISTRY_OPERATIONS", "$registry.operations", "empty")
    for route, metadata in operations.items():
        path = json_path("$registry.operations", route)
        if type(route) is not str or not route:
            raise StrictJsonError("REGISTRY_ROUTE", path, "invalid key")
        require_exact_type(metadata, dict, path)
        for key in ("input_ports", "output_ports"):
            if key not in metadata:
                raise StrictJsonError("REGISTRY_OPERATION_KEY", path, f"missing={key}")
        _string_list(metadata["input_ports"], f"{path}.input_ports", nonempty=False)
        _string_list(metadata["output_ports"], f"{path}.output_ports", nonempty=False)

    controls = registry["system_controls"]
    require_exact_type(controls, dict, "$registry.system_controls")
    for name, metadata in controls.items():
        path = json_path("$registry.system_controls", name)
        if type(name) is not str or not name:
            raise StrictJsonError("REGISTRY_CONTROL", path, "invalid key")
        require_exact_type(metadata, dict, path)
        if metadata.get("shadow_classification") != "system_control_root":
            raise StrictJsonError("REGISTRY_CONTROL_CLASS", path, "mismatch")
        model_inputs = _string_list(
            metadata.get("model_facing_inputs"),
            f"{path}.model_facing_inputs",
            nonempty=False,
        )
        source = metadata.get("source")
        require_exact_type(source, dict, f"{path}.source")
        runtime_inputs = _string_list(
            source.get("runtime_owned_inputs"),
            f"{path}.source.runtime_owned_inputs",
            nonempty=False,
        )
        if set(model_inputs) & set(runtime_inputs):
            raise StrictJsonError("REGISTRY_INPUT_OWNERSHIP", path, "overlap")

    barriers = registry["barriers"]
    require_exact_type(barriers, dict, "$registry.barriers")
    for name, metadata in barriers.items():
        path = json_path("$registry.barriers", name)
        if type(name) is not str or not name:
            raise StrictJsonError("REGISTRY_BARRIER", path, "invalid key")
        require_exact_type(metadata, dict, path)
        if metadata.get("shadow_classification") != "barrier_region":
            raise StrictJsonError("REGISTRY_BARRIER_CLASS", path, "mismatch")
        _string_list(metadata.get("input_ports"), f"{path}.input_ports", nonempty=False)
        _string_list(
            metadata.get("model_facing_inputs"),
            f"{path}.model_facing_inputs",
            nonempty=False,
        )
        _string_list(metadata.get("outcomes"), f"{path}.outcomes", nonempty=True)

    reasons = registry["unrepresentable_reasons"]
    require_exact_type(reasons, dict, "$registry.unrepresentable_reasons")
    if not reasons:
        raise StrictJsonError("REGISTRY_REASONS", "$registry.unrepresentable_reasons", "empty")
    if any(
        type(key) is not str or not key or type(value) is not str or not value
        for key, value in reasons.items()
    ):
        raise StrictJsonError("REGISTRY_REASON", "$registry.unrepresentable_reasons", "invalid")

    integrity = registry["integrity"]
    require_exact_keys(
        integrity,
        {"algorithm", "convention", "registry_payload_sha256"},
        set(),
        "$registry.integrity",
    )
    if integrity["algorithm"] != "sha256":
        raise StrictJsonError("REGISTRY_INTEGRITY", "$registry.integrity.algorithm", "mismatch")
    if type(integrity["convention"]) is not str:
        raise StrictJsonError("REGISTRY_INTEGRITY", "$registry.integrity.convention", "type")
    if type(integrity["registry_payload_sha256"]) is not str:
        raise StrictJsonError("REGISTRY_INTEGRITY", "$registry.integrity.registry_payload_sha256", "type")
    if verify_integrity:
        observed = registry_payload_sha256(registry)
        if integrity["registry_payload_sha256"] != observed:
            raise StrictJsonError(
                "REGISTRY_PAYLOAD_SHA256",
                "$registry.integrity.registry_payload_sha256",
                "mismatch",
            )
    return registry


def load_frozen_registry(
    path: Path,
    *,
    expected_file_sha256: str | None = None,
    expected_payload_sha256: str | None = None,
    limits: TechnicalLimits = DEFAULT_OFFLINE_LIMITS,
) -> tuple[dict[str, Any], RegistryIdentity]:
    registry = strict_json_file(
        path,
        limits=limits,
        expected_sha256=expected_file_sha256,
    )
    validate_registry_document(registry)
    identity = RegistryIdentity(
        file_sha256=file_sha256(path),
        payload_sha256=registry_payload_sha256(registry),
    )
    if expected_payload_sha256 is not None and identity.payload_sha256 != expected_payload_sha256:
        raise StrictJsonError("REGISTRY_PAYLOAD_SHA256", "$registry", "expected mismatch")
    return registry, identity
