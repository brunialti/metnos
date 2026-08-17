"""Project the frozen registry and manifests into a query-free model catalog."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re
import tomllib
from typing import Any

from .canonical import canonical_sha256, file_sha256, load_json_file
from .language_tag import normalize_language_tag


PROJECTION_FORMAT = "metnos.intent-ir-registry-projection/0.2"
CONTRACT_VERSION = "metnos.intent-ir/0.2"
SOURCE_REGISTRY_FORMAT = "metnos.intent-shadow-registry/0.1"
SOURCE_CONTRACT_VERSION = "metnos.intent-shadow/0.1"


class RegistryProjectionError(ValueError):
    pass


def repository_root() -> Path:
    return Path(__file__).resolve().parents[6]


def source_registry_path() -> Path:
    return Path(__file__).resolve().parent.parent / "intent_shadow_registry_v0_1.json"


def _payload_sha256(document: dict[str, Any]) -> str:
    payload = deepcopy(document)
    integrity = payload.get("integrity")
    if type(integrity) is dict:
        integrity.pop("projection_payload_sha256", None)
    return canonical_sha256(payload)


def _source_payload_sha256(document: dict[str, Any]) -> str:
    payload = deepcopy(document)
    integrity = payload.get("integrity")
    if type(integrity) is dict:
        integrity.pop("registry_payload_sha256", None)
    return canonical_sha256(payload)


def _validate_source_registry(registry: Any) -> dict[str, Any]:
    if type(registry) is not dict:
        raise RegistryProjectionError("source registry must be an object")
    if registry.get("registry_format") != SOURCE_REGISTRY_FORMAT:
        raise RegistryProjectionError("source registry format mismatch")
    if registry.get("contract_version") != SOURCE_CONTRACT_VERSION:
        raise RegistryProjectionError("source contract version mismatch")
    for key in ("operations", "system_controls", "barriers", "unrepresentable_reasons"):
        if type(registry.get(key)) is not dict or not registry[key]:
            raise RegistryProjectionError(f"source registry.{key}: invalid")
    integrity = registry.get("integrity")
    if type(integrity) is not dict or type(integrity.get("registry_payload_sha256")) is not str:
        raise RegistryProjectionError("source registry integrity invalid")
    if integrity["registry_payload_sha256"] != _source_payload_sha256(registry):
        raise RegistryProjectionError("source registry payload hash mismatch")
    return registry


def _chapters(text: str) -> dict[str, str]:
    result: dict[str, str] = {"full": text}
    matches = list(re.finditer(r"(?:^|\s)(SCOPO|PATTERN|NON|OUT):\s*", text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end() : end].strip()
        if match.group(1) == "SCOPO":
            result["scope"] = value
        elif match.group(1) == "NON":
            result["not"] = value
    return result


def _description_from_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return None
    description = manifest.get("description")
    if type(description) is not dict:
        return None
    languages: dict[str, dict[str, str]] = {}
    for raw_language, value in sorted(description.items()):
        if type(raw_language) is not str or type(value) is not str or not value.strip():
            raise RegistryProjectionError(f"{path}: invalid localized description")
        try:
            language = normalize_language_tag(raw_language)
        except ValueError as exc:
            raise RegistryProjectionError(f"{path}: invalid language tag") from exc
        if language in languages:
            raise RegistryProjectionError(f"{path}: duplicate normalized language tag")
        languages[language] = _chapters(value.strip())
    if not languages:
        return None
    return {
        "source": str(path.relative_to(repository_root())),
        "source_sha256": file_sha256(path),
        "languages": languages,
    }


def _snapshot_descriptions() -> dict[str, str]:
    path = repository_root() / "tests/benchmarks/catalog_snapshot.json"
    payload = load_json_file(path)
    if type(payload) is not list:
        return {}
    result: dict[str, str] = {}
    for item in payload:
        if type(item) is not dict:
            continue
        name = item.get("name")
        description = item.get("description")
        if type(name) is str and type(description) is str and description.strip():
            result[name] = description.strip()
    return result


def _executor_description(name: str, snapshot: dict[str, str]) -> dict[str, Any] | None:
    path = repository_root() / "executors" / name / "manifest.toml"
    current = _description_from_manifest(path)
    if current is not None:
        current["executor"] = name
        return current
    old = snapshot.get(name)
    if old is None:
        return None
    snapshot_path = repository_root() / "tests/benchmarks/catalog_snapshot.json"
    return {
        "executor": name,
        "source": "tests/benchmarks/catalog_snapshot.json",
        "source_sha256": file_sha256(snapshot_path),
        "languages": {"und": _chapters(old)},
    }


def _require_string_list(value: Any, path: str, *, nonempty: bool) -> tuple[str, ...]:
    if type(value) is not list or (nonempty and not value):
        raise RegistryProjectionError(f"{path}: invalid list")
    if any(type(item) is not str or not item for item in value):
        raise RegistryProjectionError(f"{path}: invalid string")
    if len(value) != len(set(value)):
        raise RegistryProjectionError(f"{path}: duplicate")
    return tuple(value)


def build_projection(registry: dict[str, Any]) -> dict[str, Any]:
    _validate_source_registry(registry)
    snapshot = _snapshot_descriptions()
    operations: dict[str, Any] = {}
    for route, metadata in sorted(registry["operations"].items()):
        if type(route) is not str or type(metadata) is not dict:
            raise RegistryProjectionError("invalid operation entry")
        input_ports = _require_string_list(metadata.get("input_ports"), f"{route}.input_ports", nonempty=False)
        output_ports = _require_string_list(metadata.get("output_ports"), f"{route}.output_ports", nonempty=False)
        names = _require_string_list(
            metadata.get("source_executor_names"),
            f"{route}.source_executor_names",
            nonempty=True,
        )
        descriptions = [
            item
            for name in sorted(names)
            if (item := _executor_description(name, snapshot)) is not None
        ]
        operations[route] = {
            "source_executors": sorted(names),
            "input_ports": sorted(input_ports),
            "output_ports": sorted(output_ports),
            "descriptions": descriptions,
        }

    controls: dict[str, Any] = {}
    for name, metadata in sorted(registry["system_controls"].items()):
        if type(name) is not str or type(metadata) is not dict:
            raise RegistryProjectionError("invalid control entry")
        if metadata.get("model_facing_inputs") != []:
            raise RegistryProjectionError("candidate 0.2 forbids model-facing control inputs")
        if metadata.get("shadow_classification") != "system_control_root":
            raise RegistryProjectionError("source control classification mismatch")
        source = metadata.get("source")
        executor = source.get("executor_name") if type(source) is dict else None
        descriptions = []
        if type(executor) is str:
            item = _executor_description(executor, snapshot)
            if item is not None:
                descriptions.append(item)
        controls[name] = {"source_executor": executor, "descriptions": descriptions}

    barriers: dict[str, Any] = {}
    for name, metadata in sorted(registry["barriers"].items()):
        if type(name) is not str or type(metadata) is not dict:
            raise RegistryProjectionError("invalid barrier entry")
        if metadata.get("model_facing_inputs") != [] or metadata.get("input_ports") != []:
            raise RegistryProjectionError("compact barriers cannot expose inputs")
        if metadata.get("shadow_classification") != "barrier_region":
            raise RegistryProjectionError("source barrier classification mismatch")
        outcomes = _require_string_list(metadata.get("outcomes"), f"{name}.outcomes", nonempty=True)
        if outcomes != ("approved", "rejected"):
            raise RegistryProjectionError("candidate 0.2 supports one approved/rejected barrier only")
        source = metadata.get("source")
        executor = source.get("executor_name") if type(source) is dict else None
        descriptions = []
        if type(executor) is str:
            item = _executor_description(executor, snapshot)
            if item is not None:
                descriptions.append(item)
        barriers[name] = {
            "source_executor": executor,
            "body_outcome": outcomes[0],
            "empty_outcomes": list(outcomes[1:]),
            "descriptions": descriptions,
        }

    reasons = registry["unrepresentable_reasons"]
    if not reasons or any(type(k) is not str or type(v) is not str for k, v in reasons.items()):
        raise RegistryProjectionError("invalid unrepresentable reasons")
    source_path = source_registry_path()
    source_integrity = registry.get("integrity", {})
    projected = {
        "projection_format": PROJECTION_FORMAT,
        "contract_version": CONTRACT_VERSION,
        "source_registry": {
            "path": str(source_path.relative_to(repository_root())),
            "file_sha256": file_sha256(source_path),
            "payload_sha256": source_integrity.get("registry_payload_sha256"),
        },
        "operations": operations,
        "system_controls": controls,
        "barriers": barriers,
        "unrepresentable_reasons": dict(sorted(reasons.items())),
        "integrity": {
            "algorithm": "sha256",
            "projection_payload_sha256": "",
        },
    }
    projected["integrity"]["projection_payload_sha256"] = _payload_sha256(projected)
    return projected


def validate_projection(document: Any, *, verify_integrity: bool = True) -> dict[str, Any]:
    if type(document) is not dict:
        raise RegistryProjectionError("projection must be an object")
    if document.get("projection_format") != PROJECTION_FORMAT:
        raise RegistryProjectionError("projection format mismatch")
    if document.get("contract_version") != CONTRACT_VERSION:
        raise RegistryProjectionError("contract version mismatch")
    if set(document) != {
        "projection_format",
        "contract_version",
        "source_registry",
        "operations",
        "system_controls",
        "barriers",
        "unrepresentable_reasons",
        "integrity",
    }:
        raise RegistryProjectionError("projection root is not closed")
    source_registry = document.get("source_registry")
    if type(source_registry) is not dict or set(source_registry) != {
        "path", "file_sha256", "payload_sha256"
    }:
        raise RegistryProjectionError("source registry binding invalid")
    if any(type(source_registry[key]) is not str for key in source_registry):
        raise RegistryProjectionError("source registry binding type invalid")
    for key in ("operations", "system_controls", "barriers", "unrepresentable_reasons"):
        if type(document.get(key)) is not dict or not document[key]:
            raise RegistryProjectionError(f"projection.{key}: invalid")
    for route, metadata in document["operations"].items():
        if type(route) is not str or type(metadata) is not dict:
            raise RegistryProjectionError("projection operation invalid")
        if set(metadata) != {"source_executors", "input_ports", "output_ports", "descriptions"}:
            raise RegistryProjectionError("projection operation is not closed")
        sources = _require_string_list(metadata.get("source_executors"), f"{route}.source_executors", nonempty=True)
        if list(sources) != sorted(sources):
            raise RegistryProjectionError("source executor list is not canonical")
        input_ports = _require_string_list(metadata.get("input_ports"), f"{route}.input_ports", nonempty=False)
        output_ports = _require_string_list(metadata.get("output_ports"), f"{route}.output_ports", nonempty=False)
        if list(input_ports) != sorted(input_ports) or list(output_ports) != sorted(output_ports):
            raise RegistryProjectionError("port authority list is not canonical")
        if type(metadata.get("descriptions")) is not list:
            raise RegistryProjectionError("projection descriptions invalid")
        described: list[str] = []
        for item in metadata["descriptions"]:
            _validate_description(item)
            described.append(item["executor"])
        if len(described) != len(set(described)) or described != sorted(described):
            raise RegistryProjectionError("description source list is not canonical")
        if any(name not in sources for name in described):
            raise RegistryProjectionError("description source is unauthorized")
    for name, metadata in document["system_controls"].items():
        if type(name) is not str or type(metadata) is not dict or set(metadata) != {
            "source_executor", "descriptions"
        }:
            raise RegistryProjectionError("projection control is not closed")
        if type(metadata["source_executor"]) is not str:
            raise RegistryProjectionError("projection control source invalid")
        if type(metadata["descriptions"]) is not list:
            raise RegistryProjectionError("projection control descriptions invalid")
        for item in metadata["descriptions"]:
            _validate_description(item)
            if item["executor"] != metadata["source_executor"]:
                raise RegistryProjectionError("projection control source mismatch")
    for name, metadata in document["barriers"].items():
        if type(name) is not str or type(metadata) is not dict or set(metadata) != {
            "source_executor", "body_outcome", "empty_outcomes", "descriptions"
        }:
            raise RegistryProjectionError("projection barrier is not closed")
        if type(metadata["source_executor"]) is not str:
            raise RegistryProjectionError("projection barrier source invalid")
        if metadata.get("body_outcome") != "approved" or metadata.get("empty_outcomes") != ["rejected"]:
            raise RegistryProjectionError(f"{name}: unsupported compact barrier")
        if type(metadata["descriptions"]) is not list:
            raise RegistryProjectionError("projection barrier descriptions invalid")
        for item in metadata["descriptions"]:
            _validate_description(item)
            if item["executor"] != metadata["source_executor"]:
                raise RegistryProjectionError("projection barrier source mismatch")
    reasons = document["unrepresentable_reasons"]
    if any(type(key) is not str or not key or type(value) is not str or not value for key, value in reasons.items()):
        raise RegistryProjectionError("projection reasons invalid")
    integrity = document.get("integrity")
    if type(integrity) is not dict or set(integrity) != {
        "algorithm", "projection_payload_sha256"
    } or integrity.get("algorithm") != "sha256":
        raise RegistryProjectionError("projection integrity invalid")
    expected = integrity.get("projection_payload_sha256")
    if type(expected) is not str or (verify_integrity and expected != _payload_sha256(document)):
        raise RegistryProjectionError("projection payload hash mismatch")
    return document


def _validate_description(item: Any) -> None:
    if type(item) is not dict or set(item) != {
        "executor", "source", "source_sha256", "languages"
    }:
        raise RegistryProjectionError("description record is not closed")
    if any(type(item[key]) is not str or not item[key] for key in ("executor", "source", "source_sha256")):
        raise RegistryProjectionError("description source invalid")
    languages = item["languages"]
    if type(languages) is not dict or not languages:
        raise RegistryProjectionError("description languages invalid")
    normalized: set[str] = set()
    for language, chapter in languages.items():
        if type(language) is not str:
            raise RegistryProjectionError("description language tag invalid")
        try:
            canonical = normalize_language_tag(language)
        except ValueError as exc:
            raise RegistryProjectionError("description language tag invalid") from exc
        if canonical != language or canonical in normalized:
            raise RegistryProjectionError("description language tag is not canonical or unique")
        normalized.add(canonical)
        if type(chapter) is not dict or "full" not in chapter or any(
            key not in {"full", "scope", "not"} for key in chapter
        ):
            raise RegistryProjectionError("description chapter is not closed")
        if any(type(value) is not str or not value for value in chapter.values()):
            raise RegistryProjectionError("description chapter type invalid")


def load_source_registry() -> dict[str, Any]:
    value = load_json_file(source_registry_path())
    if type(value) is not dict:
        raise RegistryProjectionError("source registry is not an object")
    return value


def load_projection(path: Path | None = None) -> dict[str, Any]:
    selected = path or Path(__file__).resolve().parent / "intent_ir_registry_projection_v0_2.json"
    value = load_json_file(selected)
    projection = validate_projection(value)
    if path is None:
        return validate_projection_against_source(projection)
    return projection


def projection_sha256(projection: dict[str, Any]) -> str:
    validate_projection(projection)
    return projection["integrity"]["projection_payload_sha256"]


def validate_projection_against_source(document: Any) -> dict[str, Any]:
    """Bind a projection to the exact frozen source registry and manifests."""
    selected = validate_projection(document)
    expected = build_projection(load_source_registry())
    if canonical_sha256(selected) != canonical_sha256(expected):
        raise RegistryProjectionError("projection authority or source set differs from frozen source")
    return selected
