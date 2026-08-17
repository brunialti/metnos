#!/usr/bin/env python3
"""Generate durable compact-native mutation evidence for V26.5.1.

The historical probe is parsed only after its bytes are verified.  Its helper
functions construct the original technical frames, but its top-level loader,
runner and temporary paths are never executed.  Every emitted case is a
compact decoder input evaluated with the one canonical adapter and the
self-contained validator.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
import types
from typing import Any, Iterator

import jsonschema


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[4]
PATHS = {
    "historical_probe": REPOSITORY / "internal/tools/request_analysis_lab/candidates/v264/metnos_v264_independent_graph_probe.py",
    "historical_result": REPOSITORY / "internal/tools/request_analysis_lab/candidates/v264/metnos_v264_independent_graph_probe_result.json",
    "adapter": REPOSITORY / "internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact_adapter.py",
    "schema": REPOSITORY / "internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact.schema.json",
    "validator": HERE / "metnos_v2651_self_contained_validator.py",
}
EXPECTED = {
    "historical_probe": "9116a851241c5d1c03385a81a2eff23bb830599ea57e46abb2f6ddd1fd15e29a",
    "historical_result": "7b0ff821783d9f5d071b4da0ccaa7f562f57afd47136cfc73c863a12663fb62c",
    "adapter": "3375207bd7bbf6d99098d0ec2066d0c9f3eb6b76b34f1ccd450abaf89abecef1",
    "schema": "76cd30411704c780e5866fd165f945bd3e773f0dab1b359e875d41f6eb54a921",
    "validator": "b19b3aa781ab3ac44b973db2aed3f6da889c29703addfbdf8251a5c60ae97656",
}
OBSOLETE = {
    "M011_duplicate_clause_id": "clause identifiers are derived from distinct source spans",
    "M012_clause_id_reverse": "clause order is derived from source-span order",
    "X003_supported_unsupported_clause_collision": "numeric clause identifiers no longer exist on decoder input",
}
REPLACEMENTS = {
    "M009_cross_alternative_atom_ref": "R009_alternative_local_source_ordinal",
    "M044_auxiliary_zero_output": "R044_source_without_output",
    "M045_auxiliary_two_outputs": "R045_source_with_two_outputs",
    "M049_source_missing": "R049_source_ordinal_out_of_range",
    "M050_self_reference": "R050_source_ordinal_self",
    "M054_duplicate_atom_id": "R054_atom_id_reintroduced",
    "M055_atom_array_id_disagree": "R055_atom_id_array_disagreement_reintroduced",
    "M056_source_role_missing": "R056_output_index_reintroduced_out_of_range",
    "M057_source_role_bound": "R057_output_index_reintroduced_bound_slot",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sources() -> None:
    mismatches = {
        name: {"expected": EXPECTED[name], "actual": sha256(path)}
        for name, path in PATHS.items()
        if not path.is_file() or sha256(path) != EXPECTED[name]
    }
    if mismatches:
        raise RuntimeError(f"source hash mismatch before import/parse: {mismatches}")


def load_verified(name: str, path: Path, expected_sha256: str) -> Any:
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != expected_sha256:
        raise RuntimeError(f"verified module changed before execution: {name}")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


class ValidatorFacade:
    """Only the historical constructors need real validator registry state."""

    def __init__(self, validator: Any) -> None:
        self._validator = validator
        self.EXPECTED_ORACLE_HASHES: dict[Path, str] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._validator, name)

    @staticmethod
    def safety_obligations(*_values: Any) -> list[str]:
        return []

    @staticmethod
    def dependency_current_location(*_values: Any) -> str:
        return "not_applicable"

    @staticmethod
    def direct_current_location_targets(*_values: Any) -> list[int]:
        return []

    @staticmethod
    def _validate_external_gate_payload(*_values: Any) -> list[str]:
        return ["blocked"]


def historical_namespace(validator: Any) -> dict[str, Any]:
    source = PATHS["historical_probe"].read_text()
    tree = ast.parse(source)
    functions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name != "load_runner"
    ]
    module = ast.Module(body=functions, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {
        "ast": ast,
        "copy": copy,
        "hashlib": hashlib,
        "json": json,
        "contextmanager": contextmanager,
        "Path": Path,
        "Any": Any,
        "Iterator": Iterator,
        "v": ValidatorFacade(validator),
    }
    exec(compile(module, str(PATHS["historical_probe"]), "exec"), namespace)
    namespace["RUNNER_PATH"] = PATHS["validator"]
    namespace["REGISTRY_PATH"] = validator.REGISTRY_PATH
    namespace["SEGMENTS"] = namespace["segments"]()
    namespace["RESULTS"] = []
    return namespace


def capture_historical_mutations(namespace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    captured: dict[str, dict[str, Any]] = {}
    namespace["valid"] = lambda *_args, **_kwargs: None
    namespace["classifier"] = lambda *_args, **_kwargs: None
    namespace["static"] = lambda *_args, **_kwargs: None
    namespace["record"] = lambda *_args, **_kwargs: None

    def capture(test_id: str, value: dict[str, Any], _expected_code: str | None = None) -> None:
        if test_id in captured:
            raise RuntimeError(f"duplicate historical mutation {test_id}")
        captured[test_id] = copy.deepcopy(value)

    namespace["invalid"] = capture
    namespace["run"]()
    historical = json.loads(PATHS["historical_result"].read_text())
    expected_ids = [
        item["id"] for item in historical["results"]
        if item["layer"] == "validator_mutation"
    ]
    if len(expected_ids) != 68 or set(expected_ids) != set(captured):
        raise RuntimeError("historical mutation census is not exactly 68/68")
    return {test_id: captured[test_id] for test_id in expected_ids}


def pipeline(compact: dict[str, Any], adapter: Any, validator: Any, schema: dict[str, Any], segments: list[dict[str, Any]]) -> dict[str, Any]:
    schema_errors = list(jsonschema.Draft202012Validator(schema).iter_errors(compact))
    if schema_errors:
        return {"stage": "schema", "codes": ["schema"]}
    try:
        expanded = adapter.expand_frame(compact)
    except adapter.UnsafeCompactGraph as error:
        return {"stage": "adapter", "codes": [str(error)]}
    result = validator.validate_frame(expanded, segments)
    if not result["valid"]:
        return {
            "stage": "validator",
            "codes": sorted({item["code"] for item in result["errors"]}),
        }
    return {"stage": "accepted", "codes": []}


def compact_near(namespace: dict[str, Any], adapter: Any) -> dict[str, Any]:
    full = namespace["frame"](
        namespace["location_dependency"](), namespace["near_projection"](),
    )
    return adapter.compact_frame(full)


def replacement_cases(namespace: dict[str, Any], adapter: Any) -> dict[str, dict[str, Any]]:
    near = compact_near(namespace, adapter)
    result: dict[str, dict[str, Any]] = {}

    alternative = namespace["ambiguity"](
        [namespace["location_dependency"](), namespace["near_projection"]()],
        [
            namespace["location_dependency"](entity_ref="person.explicit"),
            namespace["near_projection"](),
        ],
    )
    value = adapter.compact_frame(alternative)
    value["alternatives"][1]["atoms"][1]["arguments"][1]["source_ordinal"] = 3
    result["R009_alternative_local_source_ordinal"] = value

    value = copy.deepcopy(near)
    value["atoms"][0]["arguments"][1] = namespace["bound"]("place.explicit")
    result["R044_source_without_output"] = value

    value = copy.deepcopy(near)
    value["atoms"][0]["arguments"][0] = namespace["output"]()
    result["R045_source_with_two_outputs"] = value

    value = copy.deepcopy(near)
    value["atoms"][1]["arguments"][1]["source_ordinal"] = 77
    result["R049_source_ordinal_out_of_range"] = value

    value = copy.deepcopy(near)
    value["atoms"][1]["arguments"][1]["source_ordinal"] = 2
    result["R050_source_ordinal_self"] = value

    value = copy.deepcopy(near)
    value["atoms"][0]["atom_id"] = 1
    value["atoms"][1]["atom_id"] = 1
    result["R054_atom_id_reintroduced"] = value

    value = copy.deepcopy(near)
    value["atoms"][0]["atom_id"] = 2
    value["atoms"][1]["atom_id"] = 1
    result["R055_atom_id_array_disagreement_reintroduced"] = value

    value = copy.deepcopy(near)
    value["atoms"][1]["arguments"][1]["output_index"] = 9
    result["R056_output_index_reintroduced_out_of_range"] = value

    value = copy.deepcopy(near)
    value["atoms"][1]["arguments"][1]["output_index"] = 1
    result["R057_output_index_reintroduced_bound_slot"] = value
    return result


def positive_cases(namespace: dict[str, Any], adapter: Any) -> dict[str, dict[str, Any]]:
    frame = namespace["frame"]
    controls = {
        "P01_direct": frame(namespace["direct_atom"]()),
        "P04_fanout_multi_action": frame(
            namespace["location_dependency"](), namespace["near_projection"](),
            namespace["send_projection"](3, 2, 4, 1),
            namespace["move_projection"](4, 3, 7, 1),
        ),
        "P05_multi_domain": frame(
            namespace["filesystem_query"](1, 1, 1),
            namespace["runtime_query"](2, 2, 4),
        ),
        "P06_projection_output_reuse": frame(
            namespace["direct_atom"](), namespace["send_projection"](2, 2, 4, 1),
        ),
        "P08_typed_ambiguity": namespace["ambiguity"](
            [
                namespace["polar_location"](role="main_assertion", speech="assertion"),
                namespace["move_projection"](2, 2, 4),
            ],
            [namespace["polar_location"](), namespace["move_projection"](2, 2, 4)],
        ),
        "P11_mixed_coverage": frame(
            namespace["direct_atom"](),
            unsupported=[namespace["unsupported_clause"](2, 4)],
        ),
    }
    return {test_id: adapter.compact_frame(value) for test_id, value in controls.items()}


def native_negative_cases(namespace: dict[str, Any], adapter: Any) -> dict[str, dict[str, Any]]:
    near = compact_near(namespace, adapter)
    result: dict[str, dict[str, Any]] = {}

    for test_id, value in (
        ("N001_source_ordinal_missing", None),
        ("N002_source_ordinal_zero", 0),
        ("N003_source_ordinal_negative", -1),
        ("N004_source_ordinal_boolean", True),
        ("N005_source_ordinal_string", "1"),
        ("N006_source_ordinal_self", 2),
        ("N007_source_ordinal_future", 3),
        ("N008_source_ordinal_out_of_range", 77),
    ):
        changed = copy.deepcopy(near)
        edge = changed["atoms"][1]["arguments"][1]
        if value is None:
            edge.pop("source_ordinal")
        else:
            edge["source_ordinal"] = value
        result[test_id] = changed

    changed = copy.deepcopy(near)
    changed["atoms"][0]["arguments"][1] = namespace["bound"]("place.explicit")
    result["N009_source_zero_outputs"] = changed
    changed = copy.deepcopy(near)
    changed["atoms"][0]["arguments"][0] = namespace["output"]()
    result["N010_source_multiple_outputs"] = changed

    mixed = namespace["frame"](
        namespace["direct_atom"](),
        unsupported=[namespace["unsupported_clause"](2, 4)],
    )
    changed = adapter.compact_frame(mixed)
    changed["unsupported_clauses"][0]["clause_start_segment_id"] = 1
    changed["unsupported_clauses"][0]["clause_end_segment_id"] = 3
    result["N011_supported_unsupported_span_collision"] = changed

    whole = {
        "status": "unsupported",
        "clauses": [
            namespace["unsupported_clause"](1, 1),
            namespace["unsupported_clause"](2, 4),
        ],
    }
    changed = adapter.compact_frame(whole)
    changed["clauses"][1]["clause_start_segment_id"] = 1
    changed["clauses"][1]["clause_end_segment_id"] = 3
    result["N012_unsupported_span_collision"] = changed

    common = {
        "status": "typed_ambiguity",
        "alternatives": [
            {"alternative_id": 1, "atoms": [namespace["direct_atom"]()]},
            {"alternative_id": 2, "atoms": [namespace["direct_atom"](start=7)]},
        ],
        "unsupported_clauses": [namespace["unsupported_clause"](2, 4)],
    }
    result["N013_alternative_common_coverage_disagrees"] = adapter.compact_frame(common)

    direct_compact = adapter.compact_frame(namespace["frame"](namespace["direct_atom"]()))
    changed = copy.deepcopy(direct_compact)
    changed["atoms"] = [copy.deepcopy(changed["atoms"][0]) for _ in range(17)]
    result["N014_atom_limit"] = changed

    changed = {
        "status": "unsupported",
        "clauses": [
            {
                key: value for key, value in namespace["unsupported_clause"](index, 1 + 3 * (index - 1)).items()
                if key != "clause_id"
            }
            for index in range(1, 10)
        ],
    }
    result["N015_clause_limit"] = changed

    atoms = [
        namespace["direct_atom"](index, index, 1 + 3 * (index - 1))
        for index in range(1, 17)
    ]
    unsupported = [
        namespace["unsupported_clause"](16 + index, 49 + 3 * (index - 1))
        for index in range(1, 9)
    ]
    result["N016_proof_and_clause_limits"] = adapter.compact_frame(
        namespace["frame"](*atoms, unsupported=unsupported)
    )
    return result


def build() -> tuple[dict[str, Any], dict[str, Any]]:
    verify_sources()
    adapter = load_verified(
        "metnos_v2651_canonical_adapter", PATHS["adapter"], EXPECTED["adapter"],
    )
    validator = load_verified(
        "metnos_v2651_self_validator", PATHS["validator"], EXPECTED["validator"],
    )
    schema = json.loads(PATHS["schema"].read_text())
    namespace = historical_namespace(validator)
    historical = capture_historical_mutations(namespace)
    replacements = replacement_cases(namespace, adapter)
    positives = positive_cases(namespace, adapter)
    native = native_negative_cases(namespace, adapter)
    segments = namespace["SEGMENTS"]

    historical_result = json.loads(PATHS["historical_result"].read_text())
    old_codes = {
        item["id"]: sorted(set(item["detail"].get("codes", [])))
        for item in historical_result["results"]
        if item["layer"] == "validator_mutation"
    }
    matrix: list[dict[str, Any]] = []
    historical_cases: list[dict[str, Any]] = []
    for test_id, full in historical.items():
        if test_id in OBSOLETE:
            compact = adapter.compact_frame(full)
            outcome = pipeline(compact, adapter, validator, schema, segments)
            if outcome["stage"] != "accepted":
                raise RuntimeError(f"obsolete witness no longer canonicalizes: {test_id}")
            classification = "obsolete"
            case_id = "O_" + test_id
            reason = OBSOLETE[test_id]
        elif test_id in REPLACEMENTS:
            case_id = REPLACEMENTS[test_id]
            compact = copy.deepcopy(replacements[case_id])
            outcome = pipeline(compact, adapter, validator, schema, segments)
            if outcome["stage"] == "accepted":
                raise RuntimeError(f"replacement fails open: {test_id}")
            classification = "replacement"
            reason = "removed identifier/invariant is exercised on the compact surface"
        else:
            compact = adapter.compact_frame(full)
            outcome = pipeline(compact, adapter, validator, schema, segments)
            if outcome["stage"] == "accepted":
                raise RuntimeError(f"retained mutation fails open: {test_id}")
            classification = "retained"
            case_id = test_id
            reason = "same semantic or structural fault remains representable in compact form"
        matrix.append({
            "historical_id": test_id,
            "classification": classification,
            "compact_case_id": case_id,
            "historical_validator_codes": old_codes[test_id],
            "reason": reason,
        })
        historical_cases.append({
            "id": case_id,
            "historical_id": test_id,
            "classification": classification,
            "expected": outcome,
            "compact_frame": compact,
        })

    positive_records = []
    for test_id, compact in positives.items():
        outcome = pipeline(compact, adapter, validator, schema, segments)
        if outcome["stage"] != "accepted":
            raise RuntimeError(f"positive compact control fails: {test_id}: {outcome}")
        positive_records.append({"id": test_id, "expected": outcome, "compact_frame": compact})

    native_records = []
    for test_id, compact in native.items():
        outcome = pipeline(compact, adapter, validator, schema, segments)
        if outcome["stage"] == "accepted":
            raise RuntimeError(f"native mutation fails open: {test_id}")
        native_records.append({"id": test_id, "expected": outcome, "compact_frame": compact})

    counts = {
        category: sum(item["classification"] == category for item in matrix)
        for category in ("retained", "obsolete", "replacement")
    }
    matrix_document = {
        "version": "metnos.v26.5.1-compact-mutation-matrix/0.1",
        "source_probe_sha256": EXPECTED["historical_probe"],
        "source_result_sha256": EXPECTED["historical_result"],
        "historical_mutations": len(matrix),
        "classification_counts": counts,
        "entries": matrix,
    }
    fixture_document = {
        "version": "metnos.v26.5.1-compact-mutation-fixture/0.1",
        "network_calls": 0,
        "raw_queries": 0,
        "segment_count": len(segments),
        "source_hashes": EXPECTED,
        "summary": {
            "historical_cases": len(historical_cases),
            "positive_controls": len(positive_records),
            "native_negative_cases": len(native_records),
        },
        "historical_cases": historical_cases,
        "positive_controls": positive_records,
        "native_negative_cases": native_records,
    }
    return matrix_document, fixture_document


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", required=True, choices=("matrix", "fixture"))
    args = parser.parse_args()
    matrix_document, fixture_document = build()
    value = matrix_document if args.document == "matrix" else fixture_document
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
