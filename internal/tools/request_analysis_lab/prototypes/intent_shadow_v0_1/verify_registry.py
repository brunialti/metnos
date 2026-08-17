#!/usr/bin/env python3
"""Build and verify the frozen intent-shadow registry without model calls."""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tomllib
from typing import Any


HERE = Path(__file__).resolve().parent
REGISTRY_PATH = HERE / "intent_shadow_registry_v0_1.json"
LOCK_PATH = HERE / "intent_shadow_registry_v0_1.freeze.json"
CONTRACT_REL = Path(
    "internal/design/contratto_ombra_prototipo_intento_12_8_2026.md"
)
REGISTRY_FORMAT = "metnos.intent-shadow-registry/0.1"
CONTRACT_VERSION = "metnos.intent-shadow/0.1"


def find_repo_root() -> Path:
    for candidate in (HERE, *HERE.parents):
        if (candidate / "CLAUDE.md").is_file() and (
            candidate / "runtime"
        ).is_dir():
            return candidate
    raise RuntimeError("repository root not found")


ROOT = find_repo_root()
RUNTIME = ROOT / "runtime"


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def target_has_name(target: ast.expr, symbol: str) -> bool:
    return isinstance(target, ast.Name) and target.id == symbol


def container_literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        pass
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"set", "frozenset", "list", "tuple"}
        and len(node.args) == 1
        and not node.keywords
    ):
        return container_literal(node.args[0])
    raise ValueError("assignment is not a supported literal container")


def literal_assignment(path: Path, symbol: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            target_has_name(target, symbol) for target in node.targets
        ):
            return container_literal(node.value)
        if isinstance(node, ast.AnnAssign) and target_has_name(
            node.target, symbol
        ):
            return container_literal(node.value)
    raise KeyError(f"{symbol} not found in {relative(path)}")


def string_members(path: Path, symbol: str) -> list[str]:
    value = literal_assignment(path, symbol)
    if isinstance(value, dict):
        values = value.keys()
    else:
        values = value
    result = sorted({item for item in values if isinstance(item, str)})
    if not result:
        raise ValueError(f"{symbol} has no string members")
    return result


def dict_literal_keys(node: ast.AST) -> set[str]:
    if not isinstance(node, ast.Dict):
        return set()
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def registry_literal_keys(path: Path, symbol: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if target_has_name(target, symbol):
                    keys.update(dict_literal_keys(node.value))
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == symbol
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                ):
                    keys.add(target.slice.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == symbol
            and node.func.attr == "update"
            and node.args
        ):
            keys.update(dict_literal_keys(node.args[0]))
    return sorted(keys)


def find_symbol_file(symbol: str) -> Path:
    matches: list[Path] = []
    for path in sorted(RUNTIME.rglob("*.py")):
        try:
            tree = ast.parse(
                path.read_text(encoding="utf-8"), filename=str(path)
            )
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if any(target_has_name(target, symbol) for target in targets):
                    matches.append(path)
                    break
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise RuntimeError(
            f"expected one definition for {symbol}, found "
            f"{[relative(path) for path in unique]}"
        )
    return unique[0]


def manifest_contract(executor: str) -> dict[str, Any]:
    base = ROOT / "executors" / executor
    manifest_path = base / "manifest.toml"
    signature_path = base / "manifest.toml.sig"
    manifest = read_toml(manifest_path)
    declared_files = manifest.get("code", {}).get("files", [])
    code_files = [base / item for item in declared_files]
    if not code_files:
        raise ValueError(f"{executor}: manifest has no code files")
    actual_code_digests = {
        relative(path): file_sha256(path) for path in code_files
    }
    if len(code_files) == 1:
        declared_digest = manifest["code"]["digest"]
        expected_digest = "sha256:" + next(iter(actual_code_digests.values()))
        if declared_digest != expected_digest:
            raise ValueError(
                f"{executor}: manifest code digest does not match source"
            )
    properties = manifest.get("args", {}).get("properties", {})
    runtime_owned = sorted(
        key
        for key, spec in properties.items()
        if isinstance(spec, dict) and spec.get("runtime_resolved") is True
    )
    non_runtime = sorted(set(properties) - set(runtime_owned))
    return {
        "executor_name": manifest["name"],
        "manifest_version": manifest["version"],
        "manifest_path": relative(manifest_path),
        "manifest_sha256": file_sha256(manifest_path),
        "signature_path": relative(signature_path),
        "signature_sha256": file_sha256(signature_path),
        "signature_status": "source_signature_pinned",
        "declared_code_digest": manifest["code"]["digest"],
        "code_files": actual_code_digests,
        "capabilities": sorted(
            item["name"] for item in manifest.get("capabilities", [])
        ),
        "placement": manifest.get("placement", {}),
        "runtime_owned_inputs": runtime_owned,
        "non_runtime_manifest_inputs": non_runtime,
    }


def catalog_names(path: Path) -> list[str]:
    document = read_json(path)
    if not isinstance(document, list):
        raise ValueError("catalog snapshot root must be a list")
    names: list[str] = []
    for index, item in enumerate(document):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError(f"catalog entry {index} has no canonical name")
        names.append(item["name"])
    if len(names) != len(set(names)):
        raise ValueError("catalog snapshot has duplicate executor names")
    return sorted(names)


def load_name_parser():
    runtime_text = str(RUNTIME)
    if runtime_text not in sys.path:
        sys.path.insert(0, runtime_text)
    from naming_grammar import parse_name  # type: ignore

    return parse_name


def source_hashes(paths: list[Path]) -> dict[str, str]:
    return {relative(path): file_sha256(path) for path in sorted(set(paths))}


def with_payload_hash(document: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(document)
    result["integrity"] = {
        "algorithm": "sha256",
        "convention": (
            "canonical UTF-8 JSON with sorted keys and compact separators, "
            "excluding integrity.registry_payload_sha256"
        ),
    }
    payload = deepcopy(result)
    digest = canonical_sha256(payload)
    result["integrity"]["registry_payload_sha256"] = digest
    return result


def build_expected_registry() -> dict[str, Any]:
    vocab_path = RUNTIME / "vocab.py"
    naming_path = RUNTIME / "naming_grammar.py"
    catalog_path = ROOT / "tests" / "benchmarks" / "catalog_snapshot.json"
    helpers_path = find_symbol_file("_UNIVERSAL_HELPERS")
    unique_path = find_symbol_file("VERB_UNIQUE_REGISTRY")
    contract_path = ROOT / CONTRACT_REL

    actions = string_members(vocab_path, "ACTIONS")
    objects = string_members(vocab_path, "OBJECTS")
    qualifiers = string_members(vocab_path, "QUALIFIERS")
    system_verbs = string_members(vocab_path, "SYSTEM_VERBS")
    system_names = string_members(vocab_path, "SYSTEM_EXECUTOR_NAMES")
    helpers = string_members(helpers_path, "_UNIVERSAL_HELPERS")
    unique_verbs = registry_literal_keys(unique_path, "VERB_UNIQUE_REGISTRY")
    privileged_non_route_names = set(unique_verbs)

    undo = manifest_contract("undo_last_turn")
    approval = manifest_contract("get_approval")
    reserved_names = {undo["executor_name"], approval["executor_name"]}

    names = catalog_names(catalog_path)
    parse_name = load_name_parser()
    route_sources: dict[str, list[str]] = {}
    parsed_qualifiers: set[str] = set()
    parsed_providers: set[str] = set()
    reserved_internal_aliases: list[str] = []
    privileged_non_route: list[str] = []
    reserved_system_verb_non_route: list[str] = []
    unparsed: list[str] = []
    for name in names:
        if name in reserved_names:
            continue
        if name.startswith("_") and name[1:] in reserved_names:
            reserved_internal_aliases.append(name)
            continue
        parsed = parse_name(name)
        if parsed is None:
            if name in privileged_non_route_names:
                privileged_non_route.append(name)
            elif name in set(system_verbs):
                reserved_system_verb_non_route.append(name)
            else:
                unparsed.append(name)
            continue
        route = f"{parsed.verb}/{parsed.obj}"
        route_sources.setdefault(route, []).append(name)
        if parsed.qualifier:
            parsed_qualifiers.add(parsed.qualifier)
        if parsed.provider:
            parsed_providers.add(parsed.provider)

    operations: dict[str, Any] = {}
    for route in sorted(route_sources):
        verb, obj = route.split("/", 1)
        operations[route] = {
            "verb": verb,
            "object": obj,
            "source_executor_names": sorted(route_sources[route]),
            "input_ports": ["primary"],
            "output_ports": ["result"],
            "port_semantics": "abstract_intent_flow_v0_1",
            "verb_in_actions": verb in actions,
            "object_in_objects": obj in objects,
        }

    ordinary_executor_names = {
        name for values in route_sources.values() for name in values
    }
    sources = [
        vocab_path,
        naming_path,
        catalog_path,
        helpers_path,
        unique_path,
        contract_path,
        ROOT / undo["manifest_path"],
        ROOT / undo["signature_path"],
        *(ROOT / path for path in undo["code_files"]),
        ROOT / approval["manifest_path"],
        ROOT / approval["signature_path"],
        *(ROOT / path for path in approval["code_files"]),
    ]

    document: dict[str, Any] = {
        "registry_format": REGISTRY_FORMAT,
        "contract_version": CONTRACT_VERSION,
        "created_date": "2026-08-12",
        "authority": {
            "kind": "reviewed_shadow",
            "production_authority": False,
            "scope": "read_only_laboratory",
            "classification_rule": "explicit_entry_only",
        },
        "sources": source_hashes(sources),
        "observed_source_sets": {
            "actions": actions,
            "objects": objects,
            "qualifiers": qualifiers,
            "system_verbs_not_used_as_classifier": system_verbs,
            "system_executor_names_not_used_as_classifier": system_names,
            "universal_helpers_not_used_as_classifier": helpers,
            "verb_unique_registry_not_used_as_classifier": unique_verbs,
            "catalog_qualifiers_observed": sorted(parsed_qualifiers),
            "catalog_providers_observed": sorted(parsed_providers),
        },
        "operations": operations,
        "system_controls": {
            "undo_last_turn": {
                "shadow_classification": "system_control_root",
                "source": undo,
                "model_facing_inputs": [],
            }
        },
        "barriers": {
            "get/approval": {
                "shadow_classification": "barrier_region",
                "source": approval,
                "source_route": "get/approval",
                "outcomes": ["approved", "rejected"],
                "outcome_sources": {
                    "approved": "manifest.args.on_approve",
                    "rejected": "manifest.args.on_reject",
                },
                "model_facing_inputs": [],
                "input_ports": [],
            }
        },
        "unrepresentable_reasons": {
            "ambiguous_intent": "More than one incompatible analysis remains.",
            "missing_required_information": (
                "The requested intent lacks information needed to form a "
                "typed structure."
            ),
            "mixed_root_kinds": (
                "System control and ordinary operation cannot share the "
                "exclusive root."
            ),
            "no_actionable_intent": (
                "The utterance contains no independently actionable request."
            ),
            "outside_registry": (
                "The meaning is understood but has no reviewed registry entry."
            ),
            "unsupported_dependency": (
                "The requested data or control dependency is outside version 0.1."
            ),
        },
        "reconciliation": {
            "catalog_executor_count": len(names),
            "ordinary_executor_count": len(ordinary_executor_names),
            "ordinary_route_count": len(operations),
            "reserved_catalog_entries": sorted(set(names) & reserved_names),
            "reserved_internal_alias_catalog_entries": sorted(
                reserved_internal_aliases
            ),
            "classified_outside_catalog": sorted(reserved_names - set(names)),
            "privileged_non_route_catalog_entries": sorted(
                privileged_non_route
            ),
            "reserved_system_verb_catalog_entries_not_promoted": sorted(
                reserved_system_verb_non_route
            ),
            "unparsed_catalog_entries": sorted(unparsed),
            "verbs_outside_actions": sorted(
                {
                    item["verb"]
                    for item in operations.values()
                    if not item["verb_in_actions"]
                }
            ),
            "objects_outside_objects": sorted(
                {
                    item["object"]
                    for item in operations.values()
                    if not item["object_in_objects"]
                }
            ),
            "classification_precedence": [
                "explicit_system_control",
                "explicit_barrier",
                "privileged_non_route_observation",
                "catalog_operation",
                "unrepresentable",
            ],
        },
        "review": {
            "status": "codex_adversarial_reviewed",
            "independent_agent_review": False,
            "known_limits": [
                "abstract ports are semantic, not executor argument contracts",
                "signature files are pinned but cryptographic trust is delegated to production",
                "registry coverage is bounded by the pinned catalog snapshot",
            ],
        },
    }
    return with_payload_hash(document)


def registry_payload_hash(document: dict[str, Any]) -> str:
    payload = deepcopy(document)
    integrity = payload.get("integrity")
    if not isinstance(integrity, dict):
        return ""
    declared = integrity.pop("registry_payload_sha256", None)
    if not isinstance(declared, str):
        return ""
    return canonical_sha256(payload)


def invariant_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("registry_format") != REGISTRY_FORMAT:
        errors.append("registry_format")
    if document.get("contract_version") != CONTRACT_VERSION:
        errors.append("contract_version")
    authority = document.get("authority", {})
    if authority.get("production_authority") is not False:
        errors.append("production_authority")
    if authority.get("classification_rule") != "explicit_entry_only":
        errors.append("classification_rule")

    operations = document.get("operations", {})
    controls = document.get("system_controls", {})
    barriers = document.get("barriers", {})
    reasons = document.get("unrepresentable_reasons", {})
    if not all(isinstance(item, dict) and item for item in (
        operations,
        controls,
        barriers,
        reasons,
    )):
        errors.append("nonempty_classes")
        return errors

    executor_classes: dict[str, set[str]] = {}
    for route, entry in operations.items():
        if route != f"{entry.get('verb')}/{entry.get('object')}":
            errors.append("operation_route_key")
        if entry.get("input_ports") != ["primary"]:
            errors.append("operation_input_ports")
        if entry.get("output_ports") != ["result"]:
            errors.append("operation_output_ports")
        for name in entry.get("source_executor_names", []):
            executor_classes.setdefault(name, set()).add("operation")

    for key, entry in controls.items():
        if entry.get("shadow_classification") != "system_control_root":
            errors.append("system_control_classification")
        source = entry.get("source", {})
        if key != source.get("executor_name"):
            errors.append("system_control_key")
        if entry.get("model_facing_inputs"):
            errors.append("system_control_model_inputs")
        if set(entry.get("model_facing_inputs", [])) & set(
            source.get("runtime_owned_inputs", [])
        ):
            errors.append("system_control_runtime_input_leak")
        executor_classes.setdefault(source.get("executor_name", ""), set()).add(
            "system_control"
        )

    for key, entry in barriers.items():
        if entry.get("shadow_classification") != "barrier_region":
            errors.append("barrier_classification")
        outcomes = entry.get("outcomes", [])
        if not outcomes or len(outcomes) != len(set(outcomes)):
            errors.append("barrier_outcomes")
        if set(outcomes) != set(entry.get("outcome_sources", {})):
            errors.append("barrier_outcome_sources")
        source = entry.get("source", {})
        if entry.get("model_facing_inputs"):
            errors.append("barrier_model_inputs")
        if set(entry.get("model_facing_inputs", [])) & set(
            source.get("runtime_owned_inputs", [])
        ):
            errors.append("barrier_runtime_input_leak")
        executor_classes.setdefault(source.get("executor_name", ""), set()).add(
            "barrier"
        )
        if key in operations:
            errors.append("barrier_route_collision")

    if any(len(classes) != 1 for classes in executor_classes.values()):
        errors.append("executor_class_collision")
    if "" in executor_classes:
        errors.append("empty_executor_name")

    integrity = document.get("integrity", {})
    actual_payload_hash = registry_payload_hash(document)
    if not actual_payload_hash or integrity.get(
        "registry_payload_sha256"
    ) != actual_payload_hash:
        errors.append("registry_payload_sha256")

    sources = document.get("sources", {})
    for rel, declared in sources.items():
        path = ROOT / rel
        if not path.is_file() or file_sha256(path) != declared:
            errors.append(f"source_sha256:{rel}")

    reconciliation = document.get("reconciliation", {})
    if reconciliation.get("unparsed_catalog_entries"):
        errors.append("unparsed_catalog_entries")

    for entry in [*controls.values(), *barriers.values()]:
        source = entry["source"]
        manifest = ROOT / source["manifest_path"]
        signature = ROOT / source["signature_path"]
        if file_sha256(manifest) != source["manifest_sha256"]:
            errors.append("manifest_sha256")
        if file_sha256(signature) != source["signature_sha256"]:
            errors.append("signature_sha256")
        for rel, digest in source["code_files"].items():
            if file_sha256(ROOT / rel) != digest:
                errors.append("executor_code_sha256")
        if len(source["code_files"]) == 1:
            actual = next(iter(source["code_files"].values()))
            if source["declared_code_digest"] != f"sha256:{actual}":
                errors.append("declared_code_digest")

    return sorted(set(errors))


def validate_registry(document: dict[str, Any]) -> list[str]:
    errors = invariant_errors(document)
    expected = build_expected_registry()
    if document != expected:
        errors.append("registry_differs_from_sources")
    return sorted(set(errors))


def mutation_self_tests(valid: dict[str, Any]) -> dict[str, str]:
    tests: dict[str, tuple[dict[str, Any], str]] = {}

    changed_hash = deepcopy(valid)
    first_source = next(iter(changed_hash["sources"]))
    changed_hash["sources"][first_source] = "0" * 64
    tests["source_hash_drift"] = (changed_hash, "source_sha256:")

    duplicate_outcome = deepcopy(valid)
    duplicate_outcome["barriers"]["get/approval"]["outcomes"].append(
        "approved"
    )
    tests["duplicate_outcome"] = (duplicate_outcome, "barrier_outcomes")

    leaked_runtime = deepcopy(valid)
    leaked_runtime["system_controls"]["undo_last_turn"][
        "model_facing_inputs"
    ] = ["_actor"]
    tests["runtime_input_leak"] = (
        leaked_runtime,
        "system_control_model_inputs",
    )

    wrong_class = deepcopy(valid)
    wrong_class["system_controls"]["undo_last_turn"][
        "shadow_classification"
    ] = "operation"
    tests["wrong_control_class"] = (
        wrong_class,
        "system_control_classification",
    )

    route_collision = deepcopy(valid)
    route_collision["operations"]["get/approval"] = {
        "verb": "get",
        "object": "approval",
        "source_executor_names": ["get_approval"],
        "input_ports": ["primary"],
        "output_ports": ["result"],
        "port_semantics": "abstract_intent_flow_v0_1",
        "verb_in_actions": True,
        "object_in_objects": True,
    }
    tests["barrier_route_collision"] = (
        route_collision,
        "barrier_route_collision",
    )

    broken_payload = deepcopy(valid)
    broken_payload["integrity"]["registry_payload_sha256"] = "f" * 64
    tests["payload_hash_drift"] = (
        broken_payload,
        "registry_payload_sha256",
    )

    unparsed_entry = deepcopy(valid)
    unparsed_entry["reconciliation"]["unparsed_catalog_entries"] = [
        "unknown_legacy_name"
    ]
    tests["unparsed_catalog_entry"] = (
        unparsed_entry,
        "unparsed_catalog_entries",
    )

    results: dict[str, str] = {}
    for name, (mutated, expected_code) in tests.items():
        errors = invariant_errors(mutated)
        if not any(item.startswith(expected_code) for item in errors):
            raise AssertionError(
                f"mutation {name} escaped; expected {expected_code}, got {errors}"
            )
        results[name] = "caught"
    return results


def build_lock() -> dict[str, Any]:
    files = {
        relative(REGISTRY_PATH): file_sha256(REGISTRY_PATH),
        relative(Path(__file__).resolve()): file_sha256(Path(__file__).resolve()),
        CONTRACT_REL.as_posix(): file_sha256(ROOT / CONTRACT_REL),
    }
    registry = read_json(REGISTRY_PATH)
    lock = {
        "freeze_format": "metnos.intent-shadow-registry-freeze/0.1",
        "created_date": "2026-08-12",
        "files": files,
        "registry_payload_sha256": registry["integrity"][
            "registry_payload_sha256"
        ],
        "lock_payload_sha256": "",
    }
    payload = deepcopy(lock)
    payload.pop("lock_payload_sha256")
    lock["lock_payload_sha256"] = canonical_sha256(payload)
    return lock


def lock_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected = build_lock()
    if document != expected:
        errors.append("freeze_lock_differs")
    payload = deepcopy(document)
    declared = payload.pop("lock_payload_sha256", None)
    if declared != canonical_sha256(payload):
        errors.append("freeze_lock_payload_sha256")
    return errors


def verify() -> tuple[dict[str, Any], int]:
    registry = read_json(REGISTRY_PATH)
    registry_errors = validate_registry(registry)
    self_tests = mutation_self_tests(registry)
    lock = read_json(LOCK_PATH)
    freeze_errors = lock_errors(lock)
    errors = sorted(set(registry_errors + freeze_errors))
    warnings = list(registry.get("review", {}).get("known_limits", []))
    result = {
        "status": "ok" if not errors else "error",
        "error_count": len(errors),
        "errors": errors,
        "warning_count": len(warnings),
        "warnings": warnings,
        "counts": {
            "operations": len(registry["operations"]),
            "system_controls": len(registry["system_controls"]),
            "barriers": len(registry["barriers"]),
            "barrier_outcomes": sum(
                len(item["outcomes"])
                for item in registry["barriers"].values()
            ),
            "unrepresentable_reasons": len(
                registry["unrepresentable_reasons"]
            ),
            "source_files": len(registry["sources"]),
            "mutation_tests": len(self_tests),
        },
        "mutation_tests": self_tests,
        "registry_payload_sha256": registry["integrity"][
            "registry_payload_sha256"
        ],
        "lock_payload_sha256": lock["lock_payload_sha256"],
    }
    return result, 0 if not errors else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-registry", action="store_true")
    parser.add_argument("--emit-lock", action="store_true")
    args = parser.parse_args()
    if args.emit_registry:
        print(
            json.dumps(
                build_expected_registry(),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    if args.emit_lock:
        print(
            json.dumps(
                build_lock(),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    result, code = verify()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
