#!/usr/bin/env python3
"""Fail-closed verifier for the frozen intent-shadow oracle 0.1."""

from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any


HERE = Path(__file__).resolve().parent


def find_repo_root() -> Path:
    for candidate in (HERE, *HERE.parents):
        if (candidate / "CLAUDE.md").is_file() and (
            candidate / "runtime"
        ).is_dir():
            return candidate
    raise RuntimeError("repository root not found")


ROOT = find_repo_root()
ORACLE_REL = (
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
    "intent_shadow_oracle_v0_1.json"
)
FREEZE_REL = (
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
    "intent_shadow_oracle_v0_1.freeze.json"
)
VERIFY_REL = (
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
    "verify_oracle.py"
)
MUTATIONS_REL = (
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
    "test_oracle_mutations.py"
)
REGISTRY_REL = (
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
    "intent_shadow_registry_v0_1.json"
)
REGISTRY_FREEZE_REL = (
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
    "intent_shadow_registry_v0_1.freeze.json"
)
SAMPLE_REL = (
    "internal/tools/request_analysis_lab/misure_11_8/"
    "prova_specchi_riparo_c11.json"
)
BASE_CONTROLS_REL = (
    "internal/tools/request_analysis_lab/question_focus_controls_v1.json"
)
A_ORACLE_REL = (
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
    "reviews/reviewer_a/intent_shadow_oracle_reviewer_a_v0_1.json"
)
B_ORACLE_REL = (
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
    "reviews/reviewer_b/intent_shadow_oracle_reviewer_b_v0_1.json"
)
ADJUDICATION_REL = (
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
    "reviews/reviewer_a/adjudication_a.json"
)
A_CONTROLS_REL = (
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
    "reviews/reviewer_a/controls_final_proposal_a.json"
)
B_CONTROLS_REL = (
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
    "reviews/reviewer_b/controls_final_proposal_b.json"
)

CONTRACT_VERSION = "metnos.intent-shadow/0.1"
ORACLE_FORMAT = "metnos.intent-shadow-oracle/0.1"
FREEZE_FORMAT = "metnos.intent-shadow-oracle-freeze/0.1"
CREATED_DATE = "2026-08-12"
SAMPLE_SHA256 = "36aba12b9ec0f366569498bba0fa4dd876f4d2354f736058791182ca41e37af4"
REGISTRY_PAYLOAD_SHA256 = (
    "7a4f2916af8963d8d16fb79cb355d23e9739163f2241a84fa28bc6c070ed4a0a"
)
ADJUDICATED_INDICES = [
    9,
    11,
    22,
    24,
    38,
    39,
    40,
    44,
    76,
    77,
    84,
    85,
    97,
    100,
    106,
    109,
    112,
    113,
]
CONTROL_SELECTION = (
    (
        35,
        "intent_shadow.control.035.compound_fail_closed",
        A_CONTROLS_REL,
        "intent_shadow.control.035.compound_outside_registry",
        "whole_compound_outside_registry",
    ),
    (
        36,
        "intent_shadow.control.036.multi_corpus_get_images",
        A_CONTROLS_REL,
        "intent_shadow.control.036.multi_corpus_get_images",
        "materialized_multi_corpus_image_index_view",
    ),
    (
        37,
        "intent_shadow.control.037.structured_projection",
        B_CONTROLS_REL,
        "final.control.037.structured_projection",
        "structured_field_projection",
    ),
    (
        38,
        "intent_shadow.control.038.reference_photo_similarity",
        A_CONTROLS_REL,
        "intent_shadow.control.038.reference_photo_find_persons",
        "reference_photo_person_similarity",
    ),
)

FREEZE_FILES = {
    ORACLE_REL,
    VERIFY_REL,
    MUTATIONS_REL,
    "internal/design/contratto_ombra_prototipo_intento_12_8_2026.md",
    "internal/design/scelte_oracolo_pendenti_12_8_2026.md",
    "internal/design/handover_prompt_ontologia_11_8_2026.md",
    REGISTRY_REL,
    REGISTRY_FREEZE_REL,
    "tests/benchmarks/catalog_snapshot.json",
    (
        "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/"
        "intent_shadow_oracle_source_audit_v0_1.json"
    ),
    SAMPLE_REL,
    BASE_CONTROLS_REL,
    A_ORACLE_REL,
    B_ORACLE_REL,
    ADJUDICATION_REL,
    A_CONTROLS_REL,
    B_CONTROLS_REL,
    "executors/get_images_indices/manifest.toml",
    "executors/get_images_indices/manifest.toml.sig",
    "executors/get_images_indices/get_images_indices.py",
    "executors/find_persons_indices/manifest.toml",
    "executors/find_persons_indices/manifest.toml.sig",
    "executors/find_persons_indices/find_persons_indices.py",
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SOURCE_LIST_SET_SHA256 = {
    "reviewer_a_cases": "ea4829c629b84ec54747e8825d53f77f5ce20da6d60a6b76a86f4783c77a7980",
    "reviewer_b_cases": "6e87c3be0fae4bab654aa0c412cff2539a2851c182132a0968fd16e05fdd8b9c",
    "adjudication_cases": "3b943f582f255a74d426d5cde67274303fe9f8903c88b88950148346742d9d60",
    "proposal_a_controls": "288cf11d914024cf8f31beb3caa75458e446c75dab68da583b1390056babb10e",
    "proposal_b_controls": "8c5f7d3e79c0ae2f77946f2e93b320d712acfdc56a614b9fc56232283814d18c",
    "baseline_cases": "a489852a519f4176599af648a253dd9834d7886e23cd0e801801b000efba83cc",
}

PROPOSAL_A_CONTROL_IDS = {
    "intent_shadow.control.035.compound_outside_registry",
    "intent_shadow.control.036.multi_corpus_get_images",
    "intent_shadow.control.037.structured_field_projection",
    "intent_shadow.control.038.reference_photo_find_persons",
}
PROPOSAL_B_CONTROL_IDS = {
    "final.control.035.compound_fail_closed",
    "final.control.036.multi_corpus_get_images",
    "final.control.037.structured_projection",
    "final.control.038.photo_similarity_find_persons",
}


def reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


class NonFiniteJsonNumber:
    __slots__ = ("lexeme",)

    def __init__(self, lexeme: str) -> None:
        self.lexeme = lexeme


def capture_nonfinite_json_constant(value: str) -> NonFiniteJsonNumber:
    return NonFiniteJsonNumber(value)


def parse_finite_json_float(value: str) -> float | NonFiniteJsonNumber:
    number = float(value)
    if not math.isfinite(number):
        return NonFiniteJsonNumber(value)
    return number


def json_value_path(parent: str, key: str) -> str:
    encoded = json.dumps(key, ensure_ascii=False, allow_nan=False)
    return f"{parent}[{encoded}]"


def reject_nonfinite_json_numbers(value: Any, path: str = "$") -> None:
    if isinstance(value, NonFiniteJsonNumber):
        raise ValueError(f"non-finite JSON number at {path}: {value.lexeme}")
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite JSON number at {path}: {value!r}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            reject_nonfinite_json_numbers(item, json_value_path(path, key))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            reject_nonfinite_json_numbers(item, f"{path}[{index}]")


def read_json(path: Path) -> Any:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_json_keys,
        parse_constant=capture_nonfinite_json_constant,
        parse_float=parse_finite_json_float,
    )
    reject_nonfinite_json_numbers(value)
    return value


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def canonical_unordered_list_sha256(values: list[Any]) -> tuple[str, bool]:
    item_hashes = [canonical_sha256(value) for value in values]
    return canonical_sha256(sorted(item_hashes)), len(item_hashes) == len(
        set(item_hashes)
    )


def resolve_source_path(
    relative_path: str,
    source_overrides: dict[str, Path] | None = None,
) -> Path:
    if source_overrides is not None and relative_path in source_overrides:
        return source_overrides[relative_path]
    return ROOT / relative_path


def read_bound_json(
    relative_path: str,
    source_overrides: dict[str, Path] | None = None,
) -> Any:
    return read_json(resolve_source_path(relative_path, source_overrides))


def sample_sha256(queries: list[str]) -> str:
    payload = json.dumps(queries, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return sha256(payload).hexdigest()


def oracle_payload_sha256(document: dict[str, Any]) -> str:
    payload = deepcopy(document)
    integrity = payload.get("integrity")
    if isinstance(integrity, dict):
        integrity.pop("oracle_payload_sha256", None)
    return canonical_sha256(payload)


def freeze_payload_sha256(document: dict[str, Any]) -> str:
    payload = deepcopy(document)
    payload.pop("lock_payload_sha256", None)
    return canonical_sha256(payload)


def add_error(errors: list[str], code: str, message: str) -> None:
    errors.append(f"{code}: {message}")


def exact_keys(
    value: Any,
    required: set[str],
    optional: set[str],
    location: str,
    errors: list[str],
    code: str,
) -> bool:
    if type(value) is not dict:
        add_error(errors, code, f"{location} must be an object")
        return False
    keys = set(value)
    missing = required - keys
    extra = keys - required - optional
    if missing:
        add_error(errors, code, f"{location} missing keys {sorted(missing)}")
    if extra:
        add_error(errors, code, f"{location} has extra keys {sorted(extra)}")
    return not missing and not extra


def valid_sha(value: Any) -> bool:
    return type(value) is str and SHA256_RE.fullmatch(value) is not None


def json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is list:
        return "array"
    if type(value) is dict:
        return "object"
    return type(value).__name__


def first_json_mismatch(actual: Any, expected: Any, path: str) -> str | None:
    if type(actual) is not type(expected):
        return (
            f"{path} type {json_type_name(actual)} != "
            f"{json_type_name(expected)}"
        )
    if type(expected) is dict:
        actual_keys = set(actual)
        expected_keys = set(expected)
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        if missing:
            return f"{path} missing keys {missing}"
        if extra:
            return f"{path} has extra keys {extra}"
        for key in sorted(expected):
            mismatch = first_json_mismatch(
                actual[key], expected[key], json_value_path(path, key)
            )
            if mismatch is not None:
                return mismatch
        return None
    if type(expected) is list:
        if len(actual) != len(expected):
            return f"{path} length {len(actual)} != {len(expected)}"
        for index, (left, right) in enumerate(zip(actual, expected)):
            mismatch = first_json_mismatch(left, right, f"{path}[{index}]")
            if mismatch is not None:
                return mismatch
        return None
    if actual != expected:
        return f"{path} value {actual!r} != {expected!r}"
    return None


def same_json_value(actual: Any, expected: Any) -> bool:
    return first_json_mismatch(actual, expected, "$") is None


def require_json_value(
    actual: Any,
    expected: Any,
    location: str,
    errors: list[str],
    code: str,
) -> bool:
    mismatch = first_json_mismatch(actual, expected, location)
    if mismatch is not None:
        add_error(errors, code, mismatch)
        return False
    return True


def validate_data_edges(
    edges: Any,
    destination_ports: list[str],
    visible: set[int],
    operations_by_ordinal: dict[int, dict[str, Any]],
    location: str,
    errors: list[str],
) -> None:
    if not isinstance(edges, list) or not edges:
        add_error(errors, "DATA_EDGE_SHAPE", f"{location} must be non-empty list")
        return
    seen: set[tuple[Any, Any, Any]] = set()
    for edge_index, edge in enumerate(edges):
        edge_location = f"{location}[{edge_index}]"
        if not exact_keys(
            edge,
            {"from"},
            {"output", "input"},
            edge_location,
            errors,
            "DATA_EDGE_SHAPE",
        ):
            continue
        source = edge.get("from")
        if isinstance(source, bool) or not isinstance(source, int):
            add_error(errors, "DATA_EDGE_SOURCE", f"{edge_location}.from is not int")
            continue
        if source not in visible:
            add_error(
                errors,
                "DATA_EDGE_SOURCE",
                f"{edge_location}.from={source} is not a dominating prior operation",
            )
            continue
        source_ports = operations_by_ordinal[source].get("output_ports", [])
        output = edge.get("output")
        input_name = edge.get("input")
        if output is None:
            if len(source_ports) != 1:
                add_error(
                    errors,
                    "DATA_EDGE_PORT",
                    f"{edge_location} cannot derive output port",
                )
        elif output not in source_ports:
            add_error(
                errors,
                "DATA_EDGE_PORT",
                f"{edge_location}.output is not registered",
            )
        if input_name is None:
            if len(destination_ports) != 1:
                add_error(
                    errors,
                    "DATA_EDGE_PORT",
                    f"{edge_location} cannot derive input port",
                )
        elif input_name not in destination_ports:
            add_error(
                errors,
                "DATA_EDGE_PORT",
                f"{edge_location}.input is not registered",
            )
        signature = (source, output, input_name)
        if signature in seen:
            add_error(errors, "DATA_EDGE_DUPLICATE", f"{edge_location} is duplicated")
        seen.add(signature)


def validate_body(
    body: Any,
    visible: set[int],
    next_ordinal: list[int],
    operations_by_ordinal: dict[int, dict[str, Any]],
    registry: dict[str, Any],
    location: str,
    errors: list[str],
) -> set[int]:
    if not isinstance(body, list) or not body:
        add_error(errors, "EXPECTED_BODY", f"{location} must be non-empty list")
        return visible
    current_visible = set(visible)
    for node_index, node in enumerate(body):
        node_location = f"{location}[{node_index}]"
        if not isinstance(node, dict):
            add_error(errors, "EXPECTED_NODE", f"{node_location} is not object")
            continue
        kind = node.get("kind")
        if kind == "operation":
            if not exact_keys(
                node,
                {"kind", "route"},
                {"data_from"},
                node_location,
                errors,
                "OPERATION_SHAPE",
            ):
                continue
            route = node.get("route")
            operation = registry.get("operations", {}).get(route)
            if not isinstance(operation, dict):
                add_error(errors, "OPERATION_ROUTE", f"{node_location} route {route!r}")
                operation = {"input_ports": [], "output_ports": []}
            if "data_from" in node:
                validate_data_edges(
                    node["data_from"],
                    operation.get("input_ports", []),
                    current_visible,
                    operations_by_ordinal,
                    f"{node_location}.data_from",
                    errors,
                )
            ordinal = next_ordinal[0]
            next_ordinal[0] += 1
            operations_by_ordinal[ordinal] = operation
            current_visible.add(ordinal)
        elif kind == "barrier":
            if not exact_keys(
                node,
                {"kind", "barrier", "cases"},
                {"data_from"},
                node_location,
                errors,
                "BARRIER_SHAPE",
            ):
                continue
            barrier_name = node.get("barrier")
            barrier = registry.get("barriers", {}).get(barrier_name)
            if not isinstance(barrier, dict):
                add_error(
                    errors, "BARRIER_ROUTE", f"{node_location} barrier {barrier_name!r}"
                )
                barrier = {"input_ports": [], "outcomes": []}
            if "data_from" in node:
                validate_data_edges(
                    node["data_from"],
                    barrier.get("input_ports", []),
                    current_visible,
                    operations_by_ordinal,
                    f"{node_location}.data_from",
                    errors,
                )
            cases = node.get("cases")
            if not isinstance(cases, list) or not cases:
                add_error(errors, "BARRIER_CASES", f"{node_location}.cases invalid")
                continue
            emitted_outcomes: list[Any] = []
            registered_outcomes = barrier.get("outcomes", [])
            for case_index, case in enumerate(cases):
                case_location = f"{node_location}.cases[{case_index}]"
                if not exact_keys(
                    case,
                    {"outcome", "body"},
                    set(),
                    case_location,
                    errors,
                    "BARRIER_CASE_SHAPE",
                ):
                    continue
                outcome = case.get("outcome")
                emitted_outcomes.append(outcome)
                validate_body(
                    case.get("body"),
                    set(current_visible),
                    next_ordinal,
                    operations_by_ordinal,
                    registry,
                    f"{case_location}.body",
                    errors,
                )
            if len(set(emitted_outcomes)) != len(emitted_outcomes):
                add_error(errors, "BARRIER_OUTCOME", f"{node_location} duplicate outcome")
            if any(outcome not in registered_outcomes for outcome in emitted_outcomes):
                add_error(errors, "BARRIER_OUTCOME", f"{node_location} unknown outcome")
            expected_order = [
                outcome for outcome in registered_outcomes if outcome in emitted_outcomes
            ]
            if emitted_outcomes != expected_order:
                add_error(errors, "BARRIER_OUTCOME", f"{node_location} outcome order")
        else:
            add_error(errors, "EXPECTED_NODE_KIND", f"{node_location} kind {kind!r}")
    return current_visible


def validate_expected(
    expected: Any,
    registry: dict[str, Any],
    location: str,
    errors: list[str],
) -> None:
    if not isinstance(expected, dict):
        add_error(errors, "EXPECTED_ROOT_SHAPE", f"{location} is not object")
        return
    kind = expected.get("kind")
    if kind == "operation_graph":
        if not exact_keys(
            expected,
            {"kind", "body"},
            set(),
            location,
            errors,
            "EXPECTED_ROOT_SHAPE",
        ):
            return
        validate_body(expected["body"], set(), [0], {}, registry, f"{location}.body", errors)
    elif kind == "system_control":
        if not exact_keys(
            expected,
            {"kind", "control"},
            {"inputs"},
            location,
            errors,
            "EXPECTED_ROOT_SHAPE",
        ):
            return
        control = registry.get("system_controls", {}).get(expected.get("control"))
        if not isinstance(control, dict):
            add_error(errors, "SYSTEM_CONTROL", f"{location} unknown control")
            return
        allowed_inputs = control.get("model_facing_inputs", [])
        if "inputs" in expected:
            inputs = expected["inputs"]
            if not isinstance(inputs, dict) or set(inputs) != set(allowed_inputs):
                add_error(errors, "SYSTEM_CONTROL_INPUT", f"{location}.inputs invalid")
        elif allowed_inputs:
            add_error(errors, "SYSTEM_CONTROL_INPUT", f"{location}.inputs missing")
    elif kind == "unrepresentable":
        if not exact_keys(
            expected,
            {"kind", "reason"},
            set(),
            location,
            errors,
            "EXPECTED_ROOT_SHAPE",
        ):
            return
        if expected.get("reason") not in registry.get("unrepresentable_reasons", {}):
            add_error(errors, "UNREPRESENTABLE_REASON", f"{location} unknown reason")
    else:
        add_error(errors, "EXPECTED_ROOT_KIND", f"{location} kind {kind!r}")


def validate_metadata(
    oracle: Any,
    sample: dict[str, Any],
    registry: dict[str, Any],
    errors: list[str],
    source_overrides: dict[str, Path] | None = None,
) -> bool:
    top_keys = {
        "oracle_format",
        "contract_version",
        "created_date",
        "status",
        "review",
        "binding",
        "registry_binding",
        "provenance",
        "decisions",
        "counts",
        "cases",
        "new_controls",
        "integrity",
    }
    if not exact_keys(oracle, top_keys, set(), "oracle", errors, "ORACLE_SCHEMA"):
        return False

    expected_scalars = {
        "oracle_format": ORACLE_FORMAT,
        "contract_version": CONTRACT_VERSION,
        "created_date": CREATED_DATE,
        "status": "canonical_frozen",
    }
    for key, expected in expected_scalars.items():
        require_json_value(
            oracle[key],
            expected,
            json_value_path("oracle", key),
            errors,
            "ORACLE_META",
        )

    expected_review = {
        "process": "double_blind_independent_ai_review",
        "reviewer_kind": "ai",
        "human_review": False,
        "reviewer_count": 2,
        "blind_phase_completed": True,
        "independent_reviews": True,
        "exact_agreement_case_count": 102,
        "adjudicated_case_count": 18,
        "adjudication_source": ADJUDICATION_REL,
    }
    require_json_value(
        oracle["review"],
        expected_review,
        "oracle[\"review\"]",
        errors,
        "ORACLE_REVIEW_META",
    )

    expected_binding = {
        "sample_path": SAMPLE_REL,
        "sample_file_sha256": file_sha256(
            resolve_source_path(SAMPLE_REL, source_overrides)
        ),
        "sample_sha256": SAMPLE_SHA256,
        "query_count": 120,
        "unique_query_count": 120,
    }
    require_json_value(
        oracle["binding"],
        expected_binding,
        "oracle[\"binding\"]",
        errors,
        "ORACLE_BINDING_META",
    )

    expected_registry_binding = {
        "registry_path": REGISTRY_REL,
        "registry_file_sha256": file_sha256(
            resolve_source_path(REGISTRY_REL, source_overrides)
        ),
        "registry_payload_sha256": REGISTRY_PAYLOAD_SHA256,
        "registry_freeze_path": REGISTRY_FREEZE_REL,
        "contract_version": CONTRACT_VERSION,
    }
    require_json_value(
        oracle["registry_binding"],
        expected_registry_binding,
        "oracle[\"registry_binding\"]",
        errors,
        "ORACLE_REGISTRY_META",
    )

    expected_counts = {
        "sample_case_count": 120,
        "unique_sample_indices": 120,
        "unique_sample_queries": 120,
        "unique_sample_query_hashes": 120,
        "reviewer_exact_agreement_count": 102,
        "adjudicated_count": 18,
        "operation_graph_count": 84,
        "system_control_count": 2,
        "unrepresentable_count": 34,
        "existing_control_count": 34,
        "new_control_count": 4,
        "authorized_control_total": 38,
    }
    require_json_value(
        oracle["counts"],
        expected_counts,
        "oracle[\"counts\"]",
        errors,
        "ORACLE_COUNTS_META",
    )

    expected_provenance = {
        "reviewer_a_oracle": A_ORACLE_REL,
        "reviewer_b_oracle": B_ORACLE_REL,
        "adjudication": ADJUDICATION_REL,
        "control_proposal_a": A_CONTROLS_REL,
        "control_proposal_b": B_CONTROLS_REL,
        "source_audit": (
            "internal/tools/request_analysis_lab/prototypes/"
            "intent_shadow_v0_1/intent_shadow_oracle_source_audit_v0_1.json"
        ),
        "agreed_case_policy": (
            "expected is semantically identical in both independent AI reviews"
        ),
        "divergent_case_policy": (
            "all and only 18 divergences use reviewer A adjudication authorized "
            "by Roberto"
        ),
        "semantic_control_deduplication": {
            "reviewed_against_existing_34": True,
            "reviewed_pairwise": True,
            "declared_semantic_duplicates": [],
        },
    }
    require_json_value(
        oracle["provenance"],
        expected_provenance,
        "oracle[\"provenance\"]",
        errors,
        "ORACLE_PROVENANCE",
    )

    expected_decisions = {
        "exact_meaning_required": True,
        "safe_stop_accuracy_separation": True,
        "mutation_definition": "real_state_change_or_external_effect_only",
        "compound_fail_closed": {
            "rule": (
                "an indispensable outside-registry clause makes the whole request "
                "unrepresentable/outside_registry"
            ),
            "partial_subgraph_allowed": False,
        },
        "case_038": {
            "temporary": True,
            "expected": "unrepresentable/outside_registry",
            "future_direction": "text_and_pdf_invoice_pipeline",
            "future_change_is_not_retroactive": True,
        },
        "case_084": {
            "expected": "operation_graph:get/images",
            "scope": "current_view_of_materialized_unified_indices",
            "persistent_corpus_registry": False,
        },
        "case_113": {
            "expected": "read/events -> create/files",
            "structured_field_selection_and_rename": "projection",
        },
        "catalog_scope": {
            "catalog_snapshot_path": "tests/benchmarks/catalog_snapshot.json",
            "catalog_snapshot_executor_count": 96,
            "catalog_snapshot_is_stale_vs_current_manifests": True,
            "current_manifests_are_observed_but_not_silently_substituted": True,
            "frozen_snapshot_remains_v0_1_authority": True,
        },
        "future_corpus_registry": {
            "persistent": True,
            "independent_from_indices": True,
            "guides_nightly_reindexing": True,
            "part_of_expected_v0_1": False,
        },
        "corpus_unavailability": {
            "future_status": "unavailable_not_deleted",
            "part_of_expected_v0_1": False,
        },
    }
    require_json_value(
        oracle["decisions"],
        expected_decisions,
        "oracle[\"decisions\"]",
        errors,
        "ORACLE_DECISIONS",
    )

    expected_integrity = {
        "algorithm": "sha256",
        "convention": (
            "canonical UTF-8 JSON with sorted keys and compact separators, "
            "excluding integrity.oracle_payload_sha256"
        ),
        "oracle_payload_sha256": oracle_payload_sha256(oracle),
    }
    require_json_value(
        oracle["integrity"],
        expected_integrity,
        "oracle[\"integrity\"]",
        errors,
        "ORACLE_INTEGRITY",
    )

    if type(oracle["cases"]) is not list:
        add_error(errors, "ORACLE_SCHEMA", 'oracle["cases"] type must be array')
    if type(oracle["new_controls"]) is not list:
        add_error(errors, "ORACLE_SCHEMA", 'oracle["new_controls"] type must be array')

    if type(registry) is not dict:
        add_error(errors, "REGISTRY_VERSION", "registry root type must be object")
    else:
        require_json_value(
            registry.get("contract_version"),
            CONTRACT_VERSION,
            'registry["contract_version"]',
            errors,
            "REGISTRY_VERSION",
        )
        integrity = registry.get("integrity")
        registry_digest = (
            integrity.get("registry_payload_sha256")
            if type(integrity) is dict
            else None
        )
        require_json_value(
            registry_digest,
            REGISTRY_PAYLOAD_SHA256,
            'registry["integrity"]["registry_payload_sha256"]',
            errors,
            "REGISTRY_VERSION",
        )
    if type(sample) is not dict:
        add_error(errors, "SAMPLE_HASH", "sample root type must be object")
    else:
        queries = sample.get("queries")
        if type(queries) is not list or any(type(query) is not str for query in queries):
            add_error(errors, "SAMPLE_HASH", 'sample["queries"] must be string array')
        elif sample_sha256(queries) != SAMPLE_SHA256:
            add_error(errors, "SAMPLE_HASH", "sample semantic hash")
    return True
def validated_source_cases(
    document: Any,
    *,
    index_key: str,
    query_key: str,
    expected_indices: set[int],
    expected_set_sha256: str,
    label: str,
    sample_queries: list[str],
    errors: list[str],
) -> dict[int, dict[str, Any]] | None:
    error_count_before = len(errors)
    if type(document) is not dict:
        add_error(errors, "SOURCE_CASE_LIST", f"{label} root type must be object")
        return None
    cases = document.get("cases")
    if type(cases) is not list:
        add_error(errors, "SOURCE_CASE_LIST", f"{label}.cases type must be array")
        return None
    if len(cases) != len(expected_indices):
        add_error(
            errors,
            "SOURCE_CASE_LIST",
            f"{label}.cases length {len(cases)} != {len(expected_indices)}",
        )
    indices: list[int] = []
    seen_indices: set[int] = set()
    for position, case in enumerate(cases):
        location = f"{label}.cases[{position}]"
        if type(case) is not dict:
            add_error(errors, "SOURCE_CASE_LIST", f"{location} type must be object")
            continue
        if index_key not in case or "expected" not in case:
            add_error(
                errors,
                "SOURCE_CASE_LIST",
                f"{location} must contain {index_key!r} and 'expected'",
            )
            continue
        index = case[index_key]
        if type(index) is not int:
            add_error(
                errors,
                "SOURCE_CASE_LIST",
                f"{location}[{index_key!r}] type must be integer",
            )
            continue
        indices.append(index)
        if index in seen_indices:
            add_error(
                errors,
                "SOURCE_CASE_LIST",
                f"{label}.cases duplicate index {index}",
            )
        seen_indices.add(index)
        if index not in expected_indices:
            add_error(
                errors,
                "SOURCE_CASE_LIST",
                f"{label}.cases unexpected index {index}",
            )
            continue
        query = case.get(query_key)
        digest = case.get("query_sha256")
        expected_query = sample_queries[index]
        if type(query) is not str or query != expected_query:
            add_error(
                errors,
                "SOURCE_CASE_LIST",
                f"{location}[{query_key!r}] differs from frozen sample",
            )
        if not valid_sha(digest) or digest != text_sha256(expected_query):
            add_error(
                errors,
                "SOURCE_CASE_LIST",
                f"{location} query_sha256 differs from frozen sample",
            )
    if set(indices) != expected_indices:
        add_error(
            errors,
            "SOURCE_CASE_LIST",
            f"{label}.cases index set differs from canonical authority",
        )
    list_digest, records_unique = canonical_unordered_list_sha256(cases)
    if not records_unique:
        add_error(
            errors,
            "SOURCE_CASE_LIST",
            f"{label}.cases contains duplicate records",
        )
    if list_digest != expected_set_sha256:
        add_error(
            errors,
            "SOURCE_CASE_LIST",
            f"{label}.cases canonical set hash mismatch",
        )
    if len(errors) != error_count_before:
        return None
    return {case[index_key]: case for case in cases}
def validate_cases_and_sources(
    oracle: dict[str, Any],
    sample: dict[str, Any],
    registry: dict[str, Any],
    errors: list[str],
    source_overrides: dict[str, Path] | None = None,
) -> None:
    cases = oracle.get("cases")
    queries = sample.get("queries", [])
    if not isinstance(cases, list):
        add_error(errors, "CASE_COUNT", "oracle.cases is not list")
        return
    if len(cases) != 120:
        add_error(errors, "CASE_COUNT", f"expected 120, found {len(cases)}")
    indices = [case.get("sample_index") for case in cases if isinstance(case, dict)]
    if indices != list(range(120)):
        add_error(errors, "CASE_INDEX_SET", "indices must be ordered 0..119 exactly once")

    a_doc = read_bound_json(A_ORACLE_REL, source_overrides)
    b_doc = read_bound_json(B_ORACLE_REL, source_overrides)
    adjudication = read_bound_json(ADJUDICATION_REL, source_overrides)
    a_cases = validated_source_cases(
        a_doc,
        index_key="sample_index",
        query_key="query_text",
        expected_indices=set(range(120)),
        expected_set_sha256=SOURCE_LIST_SET_SHA256["reviewer_a_cases"],
        label="reviewer_a",
        sample_queries=queries,
        errors=errors,
    )
    b_cases = validated_source_cases(
        b_doc,
        index_key="index",
        query_key="query",
        expected_indices=set(range(120)),
        expected_set_sha256=SOURCE_LIST_SET_SHA256["reviewer_b_cases"],
        label="reviewer_b",
        sample_queries=queries,
        errors=errors,
    )
    adjudicated = validated_source_cases(
        adjudication,
        index_key="sample_index",
        query_key="query_text",
        expected_indices=set(ADJUDICATED_INDICES),
        expected_set_sha256=SOURCE_LIST_SET_SHA256["adjudication_cases"],
        label="adjudication_a",
        sample_queries=queries,
        errors=errors,
    )
    if a_cases is None or b_cases is None or adjudicated is None:
        return
    divergent = sorted(
        index
        for index in range(120)
        if not same_json_value(
            a_cases[index].get("expected"), b_cases[index].get("expected")
        )
    )
    if divergent != ADJUDICATED_INDICES:
        add_error(errors, "SOURCE_DIVERGENCES", f"unexpected divergences {divergent}")
    if sorted(adjudicated) != ADJUDICATED_INDICES:
        add_error(errors, "SOURCE_ADJUDICATION_SET", "adjudication indices mismatch")
    require_json_value(
        {
            "reviewer_a_case_count": len(a_cases),
            "reviewer_b_case_count": len(b_cases),
            "exact_agreement_case_count": 120 - len(divergent),
            "adjudicated_case_count": len(adjudicated),
        },
        {
            "reviewer_a_case_count": oracle["binding"]["query_count"],
            "reviewer_b_case_count": oracle["binding"]["query_count"],
            "exact_agreement_case_count": oracle["review"][
                "exact_agreement_case_count"
            ],
            "adjudicated_case_count": oracle["review"][
                "adjudicated_case_count"
            ],
        },
        "authority_case_counts",
        errors,
        "SOURCE_COUNT_COHERENCE",
    )

    root_counts = {"operation_graph": 0, "system_control": 0, "unrepresentable": 0}
    seen_queries: set[str] = set()
    seen_hashes: set[str] = set()
    for position, case in enumerate(cases):
        location = f"oracle.cases[{position}]"
        if not exact_keys(
            case,
            {"sample_index", "case_id", "query_text", "query_sha256", "expected", "source"},
            set(),
            location,
            errors,
            "CASE_SCHEMA",
        ):
            continue
        index = case.get("sample_index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < 120:
            add_error(errors, "CASE_INDEX", f"{location}.sample_index invalid")
            continue
        expected_query = queries[index]
        query = case.get("query_text")
        digest = case.get("query_sha256")
        if query != expected_query:
            add_error(errors, "CASE_QUERY_BINDING", f"case {index} query differs from sample")
        if not valid_sha(digest) or digest != text_sha256(query if isinstance(query, str) else ""):
            add_error(errors, "CASE_QUERY_HASH", f"case {index} query hash invalid")
        if digest != text_sha256(expected_query):
            add_error(errors, "CASE_QUERY_BINDING", f"case {index} hash differs from sample")
        if case.get("case_id") != f"frozen_sample.{index:03d}":
            add_error(errors, "CASE_ID", f"case {index} id mismatch")
        source = case.get("source")
        if index in adjudicated:
            source_expected = adjudicated[index].get("expected")
            expected_source_label = "reviewer_a_adjudication"
        else:
            source_expected = a_cases[index].get("expected")
            expected_source_label = "reviewer_exact_agreement"
            if not same_json_value(source_expected, b_cases[index].get("expected")):
                add_error(errors, "SOURCE_AGREEMENT", f"case {index} is not agreed")
        if not same_json_value(case.get("expected"), source_expected):
            add_error(errors, "CASE_EXPECTED_SOURCE", f"case {index} expected differs from authority")
        if source != expected_source_label:
            add_error(errors, "CASE_SOURCE_LABEL", f"case {index} source label mismatch")
        validate_expected(case.get("expected"), registry, f"{location}.expected", errors)
        expected_kind = case.get("expected", {}).get("kind") if isinstance(case.get("expected"), dict) else None
        if expected_kind in root_counts:
            root_counts[expected_kind] += 1
        if isinstance(query, str):
            if query in seen_queries:
                add_error(errors, "CASE_QUERY_DUPLICATE", f"case {index} duplicated query")
            seen_queries.add(query)
        if isinstance(digest, str):
            if digest in seen_hashes:
                add_error(errors, "CASE_HASH_DUPLICATE", f"case {index} duplicated hash")
            seen_hashes.add(digest)
    if root_counts != {
        "operation_graph": 84,
        "system_control": 2,
        "unrepresentable": 34,
    }:
        add_error(errors, "CASE_ROOT_COUNTS", f"root counts {root_counts}")


def validated_controls_by_id(
    document: Any,
    *,
    expected_ids: set[str],
    expected_set_sha256: str,
    label: str,
    errors: list[str],
) -> dict[str, dict[str, Any]] | None:
    error_count_before = len(errors)
    if type(document) is not dict:
        add_error(errors, "SOURCE_CONTROL_LIST", f"{label} root type must be object")
        return None
    controls = document.get("controls")
    if type(controls) is not list:
        add_error(errors, "SOURCE_CONTROL_LIST", f"{label}.controls type must be array")
        return None
    if len(controls) != len(expected_ids):
        add_error(
            errors,
            "SOURCE_CONTROL_LIST",
            f"{label}.controls length {len(controls)} != {len(expected_ids)}",
        )
    ids: list[str] = []
    for position, control in enumerate(controls):
        location = f"{label}.controls[{position}]"
        if type(control) is not dict:
            add_error(errors, "SOURCE_CONTROL_LIST", f"{location} type must be object")
            continue
        control_id = control.get("control_id")
        if type(control_id) is not str or not control_id:
            add_error(
                errors,
                "SOURCE_CONTROL_LIST",
                f"{location}.control_id must be non-empty string",
            )
            continue
        ids.append(control_id)
        if "expected" not in control:
            add_error(errors, "SOURCE_CONTROL_LIST", f"{location}.expected missing")
        query = control.get("query_text")
        digest = control.get("query_sha256")
        if type(query) is not str:
            add_error(errors, "SOURCE_CONTROL_LIST", f"{location}.query_text type")
        elif not valid_sha(digest) or digest != text_sha256(query):
            add_error(errors, "SOURCE_CONTROL_LIST", f"{location}.query_sha256 invalid")
    if len(ids) != len(set(ids)):
        add_error(errors, "SOURCE_CONTROL_LIST", f"{label}.controls duplicate ID")
    if set(ids) != expected_ids:
        add_error(
            errors,
            "SOURCE_CONTROL_LIST",
            f"{label}.controls ID set differs from canonical authority",
        )
    list_digest, records_unique = canonical_unordered_list_sha256(controls)
    if not records_unique:
        add_error(
            errors,
            "SOURCE_CONTROL_LIST",
            f"{label}.controls contains duplicate records",
        )
    if list_digest != expected_set_sha256:
        add_error(
            errors,
            "SOURCE_CONTROL_LIST",
            f"{label}.controls canonical set hash mismatch",
        )
    if len(errors) != error_count_before:
        return None
    return {control["control_id"]: control for control in controls}


def validated_baseline_cases(
    document: Any,
    errors: list[str],
) -> tuple[list[dict[str, Any]], set[str], set[str], set[str]] | None:
    error_count_before = len(errors)
    if type(document) is not dict:
        add_error(errors, "BASE_CONTROL_LIST", "baseline root type must be object")
        return None
    cases = document.get("cases")
    if type(cases) is not list:
        add_error(errors, "BASE_CONTROL_LIST", "baseline.cases type must be array")
        return None
    if len(cases) != 34:
        add_error(
            errors,
            "BASE_CONTROL_LIST",
            f"baseline.cases length {len(cases)} != 34",
        )
    ids: list[str] = []
    queries: list[str] = []
    query_hashes: list[str] = []
    for position, case in enumerate(cases):
        location = f"baseline.cases[{position}]"
        if type(case) is not dict:
            add_error(errors, "BASE_CONTROL_LIST", f"{location} type must be object")
            continue
        control_id = case.get("id")
        query = case.get("query")
        if type(control_id) is not str or not control_id:
            add_error(errors, "BASE_CONTROL_LIST", f"{location}.id invalid")
        else:
            ids.append(control_id)
        if type(query) is not str or not query:
            add_error(errors, "BASE_CONTROL_LIST", f"{location}.query invalid")
        else:
            queries.append(query)
            query_hashes.append(text_sha256(query))
        if type(case.get("lang")) is not str:
            add_error(errors, "BASE_CONTROL_LIST", f"{location}.lang type")
        if type(case.get("tuple")) is not list or any(
            type(item) is not str for item in case.get("tuple", [])
        ):
            add_error(errors, "BASE_CONTROL_LIST", f"{location}.tuple type")
        if type(case.get("expect_binding")) is not bool:
            add_error(errors, "BASE_CONTROL_LIST", f"{location}.expect_binding type")
    if len(ids) != 34 or len(set(ids)) != 34:
        add_error(errors, "BASE_CONTROL_LIST", "baseline must have 34 unique IDs")
    if len(queries) != 34 or len(set(queries)) != 34:
        add_error(errors, "BASE_CONTROL_LIST", "baseline must have 34 unique queries")
    if len(query_hashes) != 34 or len(set(query_hashes)) != 34:
        add_error(
            errors,
            "BASE_CONTROL_LIST",
            "baseline must have 34 unique query hashes",
        )
    list_digest, records_unique = canonical_unordered_list_sha256(cases)
    if not records_unique:
        add_error(errors, "BASE_CONTROL_LIST", "baseline contains duplicate records")
    if list_digest != SOURCE_LIST_SET_SHA256["baseline_cases"]:
        add_error(
            errors,
            "BASE_CONTROL_LIST",
            "baseline canonical set hash mismatch",
        )
    if len(errors) != error_count_before:
        return None
    return cases, set(ids), set(queries), set(query_hashes)
def validate_controls(
    oracle: dict[str, Any],
    sample: dict[str, Any],
    registry: dict[str, Any],
    errors: list[str],
    source_overrides: dict[str, Path] | None = None,
) -> None:
    controls = oracle.get("new_controls")
    if not isinstance(controls, list):
        add_error(errors, "CONTROL_COUNT", "new_controls is not list")
        return
    if len(controls) != 4:
        add_error(errors, "CONTROL_COUNT", f"expected 4, found {len(controls)}")
    baseline = read_bound_json(BASE_CONTROLS_REL, source_overrides)
    baseline_validation = validated_baseline_cases(baseline, errors)
    if baseline_validation is None:
        return
    baseline_cases, baseline_ids, baseline_queries, baseline_hashes = (
        baseline_validation
    )
    frozen_queries = set(sample.get("queries", []))
    frozen_hashes = {text_sha256(query) for query in frozen_queries}
    proposal_a = validated_controls_by_id(
        read_bound_json(A_CONTROLS_REL, source_overrides),
        expected_ids=PROPOSAL_A_CONTROL_IDS,
        expected_set_sha256=SOURCE_LIST_SET_SHA256["proposal_a_controls"],
        label="proposal_a",
        errors=errors,
    )
    proposal_b = validated_controls_by_id(
        read_bound_json(B_CONTROLS_REL, source_overrides),
        expected_ids=PROPOSAL_B_CONTROL_IDS,
        expected_set_sha256=SOURCE_LIST_SET_SHA256["proposal_b_controls"],
        label="proposal_b",
        errors=errors,
    )
    if proposal_a is None or proposal_b is None:
        return
    source_documents = {
        A_CONTROLS_REL: proposal_a,
        B_CONTROLS_REL: proposal_b,
    }
    require_json_value(
        {
            "baseline_count": len(baseline_cases),
            "proposal_a_count": len(proposal_a),
            "proposal_b_count": len(proposal_b),
            "selected_new_count": len(controls),
            "authorized_total": len(baseline_cases) + len(controls),
        },
        {
            "baseline_count": oracle["counts"]["existing_control_count"],
            "proposal_a_count": oracle["counts"]["new_control_count"],
            "proposal_b_count": oracle["counts"]["new_control_count"],
            "selected_new_count": oracle["counts"]["new_control_count"],
            "authorized_total": oracle["counts"]["authorized_control_total"],
        },
        "authority_control_counts",
        errors,
        "CONTROL_COUNT_COHERENCE",
    )
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    seen_hashes: set[str] = set()
    seen_axes: set[str] = set()
    for position, selection in enumerate(CONTROL_SELECTION):
        ordinal, canonical_id, source_path, source_id, semantic_axis = selection
        location = f"oracle.new_controls[{position}]"
        if position >= len(controls):
            continue
        control = controls[position]
        if not exact_keys(
            control,
            {
                "ordinal",
                "control_id",
                "language",
                "query_text",
                "query_sha256",
                "expected",
                "source_proposal",
                "source_control_id",
                "semantic_axis",
                "semantic_duplicate_of_existing_control_ids",
                "rationale",
            },
            set(),
            location,
            errors,
            "CONTROL_SCHEMA",
        ):
            continue
        source = source_documents[source_path].get(source_id)
        if source is None:
            add_error(errors, "CONTROL_SOURCE", f"missing source {source_id}")
            continue
        expected_values = {
            "ordinal": ordinal,
            "control_id": canonical_id,
            "language": source.get("language"),
            "query_text": source.get("query_text"),
            "query_sha256": source.get("query_sha256"),
            "expected": source.get("expected"),
            "source_proposal": source_path,
            "source_control_id": source_id,
            "semantic_axis": semantic_axis,
            "semantic_duplicate_of_existing_control_ids": [],
            "rationale": source.get("reason", source.get("rationale")),
        }
        for key, expected in expected_values.items():
            if not same_json_value(control.get(key), expected):
                add_error(errors, "CONTROL_SOURCE", f"{location}.{key} differs from selection")
        query = control.get("query_text")
        digest = control.get("query_sha256")
        if not isinstance(query, str) or not valid_sha(digest) or digest != text_sha256(query):
            add_error(errors, "CONTROL_QUERY_HASH", f"{location} query hash")
        if query in baseline_queries or digest in baseline_hashes:
            add_error(errors, "CONTROL_BASE_COLLISION", f"{location} collides with existing 34")
        if query in frozen_queries or digest in frozen_hashes:
            add_error(errors, "CONTROL_SAMPLE_COLLISION", f"{location} collides with frozen sample")
        control_id = control.get("control_id")
        axis = control.get("semantic_axis")
        if control_id in seen_ids:
            add_error(errors, "CONTROL_ID_DUPLICATE", f"{location} duplicate id")
        if query in seen_queries:
            add_error(errors, "CONTROL_QUERY_DUPLICATE", f"{location} duplicate query")
        if digest in seen_hashes:
            add_error(errors, "CONTROL_HASH_DUPLICATE", f"{location} duplicate hash")
        if axis in seen_axes:
            add_error(errors, "CONTROL_SEMANTIC_DUPLICATE", f"{location} duplicate axis")
        seen_ids.add(control_id)
        seen_queries.add(query)
        seen_hashes.add(digest)
        seen_axes.add(axis)
        duplicates = control.get("semantic_duplicate_of_existing_control_ids")
        if duplicates != [] or any(item not in baseline_ids for item in duplicates or []):
            add_error(errors, "CONTROL_SEMANTIC_DUPLICATE", f"{location} declaration invalid")
        validate_expected(control.get("expected"), registry, f"{location}.expected", errors)
    if len(baseline_cases) + len(controls) != 38:
        add_error(errors, "CONTROL_TOTAL", "34 existing plus new controls must total 38")


def validate_freeze(
    freeze: Any,
    oracle: dict[str, Any],
    oracle_path: Path,
    errors: list[str],
    source_overrides: dict[str, Path] | None = None,
) -> None:
    if type(freeze) is not dict:
        add_error(errors, "FREEZE_SCHEMA", "freeze type must be object")
        return
    expected_files: dict[str, str] = {}
    for relative_path in sorted(FREEZE_FILES):
        source_path = (
            oracle_path
            if relative_path == ORACLE_REL
            else resolve_source_path(relative_path, source_overrides)
        )
        if not source_path.is_file():
            add_error(errors, "FREEZE_FILE_MISSING", relative_path)
            continue
        expected_files[relative_path] = file_sha256(source_path)
    expected_freeze = {
        "algorithm": "sha256",
        "created_date": CREATED_DATE,
        "files": expected_files,
        "freeze_format": FREEZE_FORMAT,
        "lock_payload_sha256": freeze_payload_sha256(freeze),
        "oracle_payload_sha256": oracle_payload_sha256(oracle),
        "registry_payload_sha256": REGISTRY_PAYLOAD_SHA256,
        "sample_sha256": SAMPLE_SHA256,
    }
    require_json_value(
        freeze,
        expected_freeze,
        "freeze",
        errors,
        "FREEZE_SCHEMA",
    )
def verify(
    oracle_path: Path | None = None,
    freeze_path: Path | None = None,
    source_overrides: dict[str, Path] | None = None,
) -> dict[str, Any]:
    oracle_path = oracle_path or ROOT / ORACLE_REL
    freeze_path = freeze_path or ROOT / FREEZE_REL
    errors: list[str] = []
    try:
        oracle = read_json(oracle_path)
    except Exception as exc:  # fail closed on malformed or absent oracle
        return {
            "status": "error",
            "error_count": 1,
            "errors": [f"ORACLE_READ: {exc}"],
            "counts": {},
        }
    try:
        freeze = read_json(freeze_path)
    except Exception as exc:  # fail closed on malformed or absent freeze
        return {
            "status": "error",
            "error_count": 1,
            "errors": [f"FREEZE_READ: {exc}"],
            "counts": {},
        }
    try:
        sample = read_bound_json(SAMPLE_REL, source_overrides)
        registry = read_bound_json(REGISTRY_REL, source_overrides)
        metadata_shape_ok = validate_metadata(
            oracle, sample, registry, errors, source_overrides
        )
        if metadata_shape_ok:
            validate_cases_and_sources(
                oracle, sample, registry, errors, source_overrides
            )
            validate_controls(oracle, sample, registry, errors, source_overrides)
        validate_freeze(
            freeze, oracle, oracle_path, errors, source_overrides
        )
    except Exception as exc:  # malformed nested data must never pass
        add_error(errors, "VERIFY_EXCEPTION", repr(exc))
    counts = {
        "sample_cases": len(oracle.get("cases", [])) if isinstance(oracle, dict) else 0,
        "agreed_cases": 102,
        "adjudicated_cases": 18,
        "existing_controls": 34,
        "new_controls": len(oracle.get("new_controls", [])) if isinstance(oracle, dict) else 0,
        "authorized_control_total": 38,
        "frozen_files": len(freeze.get("files", {})) if isinstance(freeze, dict) else 0,
    }
    return {
        "status": "ok" if not errors else "error",
        "error_count": len(errors),
        "errors": errors,
        "counts": counts,
        "oracle_payload_sha256": (
            oracle_payload_sha256(oracle) if isinstance(oracle, dict) else None
        ),
        "lock_payload_sha256": (
            freeze_payload_sha256(freeze) if isinstance(freeze, dict) else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", type=Path, default=ROOT / ORACLE_REL)
    parser.add_argument("--freeze", type=Path, default=ROOT / FREEZE_REL)
    args = parser.parse_args()
    report = verify(args.oracle, args.freeze)
    print(
        json.dumps(
            report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
    )
    return 0 if report["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
