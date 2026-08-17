#!/usr/bin/env python3
"""Replay the durable V26.5.1 compact-native mutation suite offline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import types
from typing import Any

import jsonschema


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[4]
PATHS = {
    "adapter": REPOSITORY / "internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact_adapter.py",
    "schema": REPOSITORY / "internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact.schema.json",
    "registry": REPOSITORY / "internal/tools/request_analysis_lab/candidates/v2641/metnos_v2641_typed_registry.json",
    "validator": HERE / "metnos_v2651_self_contained_validator.py",
    "matrix": HERE / "metnos_v2651_compact_mutation_matrix.json",
    "fixture": HERE / "metnos_v2651_compact_mutation_fixture.json",
}
EXPECTED = {
    "adapter": "3375207bd7bbf6d99098d0ec2066d0c9f3eb6b76b34f1ccd450abaf89abecef1",
    "schema": "76cd30411704c780e5866fd165f945bd3e773f0dab1b359e875d41f6eb54a921",
    "registry": "448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f",
    "validator": "b19b3aa781ab3ac44b973db2aed3f6da889c29703addfbdf8251a5c60ae97656",
    "matrix": "69103b64374527feb0d40bc709043066e030fa81101651f724e52e79d5a9c1cc",
    "fixture": "8ce526f2384c4e1a9e1103c97ee3eb0af3f4bff1302cae621d61f5fe792d023e",
}
EXPECTED_CLASSIFICATIONS = {"retained": 56, "obsolete": 3, "replacement": 9}
EXPECTED_NATIVE_IDS = {
    *(f"N00{index}_source_ordinal_{suffix}" for index, suffix in (
        (1, "missing"), (2, "zero"), (3, "negative"), (4, "boolean"),
        (5, "string"), (6, "self"), (7, "future"), (8, "out_of_range"),
    )),
    "N009_source_zero_outputs",
    "N010_source_multiple_outputs",
    "N011_supported_unsupported_span_collision",
    "N012_unsupported_span_collision",
    "N013_alternative_common_coverage_disagrees",
    "N014_atom_limit",
    "N015_clause_limit",
    "N016_proof_and_clause_limits",
}
EXPECTED_POSITIVE_IDS = {
    "P01_direct", "P04_fanout_multi_action", "P05_multi_domain",
    "P06_projection_output_reuse", "P08_typed_ambiguity",
    "P11_mixed_coverage",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_before_import() -> list[dict[str, Any]]:
    checks = []
    for name, path in PATHS.items():
        actual = sha256(path) if path.is_file() else None
        checks.append({
            "id": f"preimport_hash:{name}",
            "pass": actual == EXPECTED[name],
            "expected": EXPECTED[name],
            "actual": actual,
        })
    if not all(item["pass"] for item in checks):
        raise RuntimeError("pre-import hash verification failed")
    return checks


def load_verified(name: str, path: Path, expected_sha256: str) -> Any:
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != expected_sha256:
        raise RuntimeError(f"verified module changed before execution: {name}")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def evaluate(
    compact: dict[str, Any], adapter: Any, validator: Any,
    schema_validator: jsonschema.Draft202012Validator,
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    if list(schema_validator.iter_errors(compact)):
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


def run() -> dict[str, Any]:
    checks = verify_before_import()
    adapter = load_verified(
        "metnos_v2651_suite_adapter", PATHS["adapter"], EXPECTED["adapter"],
    )
    validator = load_verified(
        "metnos_v2651_suite_validator", PATHS["validator"], EXPECTED["validator"],
    )
    schema = json.loads(PATHS["schema"].read_text())
    matrix = json.loads(PATHS["matrix"].read_text())
    fixture = json.loads(PATHS["fixture"].read_text())
    schema_validator = jsonschema.Draft202012Validator(schema)
    segments = [{"id": index} for index in range(1, fixture["segment_count"] + 1)]

    def record(test_id: str, passed: bool, detail: Any = None) -> None:
        checks.append({"id": test_id, "pass": bool(passed), "detail": detail})

    entries = matrix["entries"]
    entry_by_historical = {item["historical_id"]: item for item in entries}
    historical = fixture["historical_cases"]
    fixture_by_historical = {item["historical_id"]: item for item in historical}
    record("matrix_exactly_68_unique", len(entries) == len(entry_by_historical) == 68)
    record("fixture_exactly_68_unique", len(historical) == len(fixture_by_historical) == 68)
    record("matrix_fixture_same_ids", set(entry_by_historical) == set(fixture_by_historical))
    record("classification_counts", matrix["classification_counts"] == EXPECTED_CLASSIFICATIONS, matrix["classification_counts"])
    record(
        "case_ids_bound_to_matrix",
        all(
            entry_by_historical[test_id]["compact_case_id"] == item["id"]
            and entry_by_historical[test_id]["classification"] == item["classification"]
            for test_id, item in fixture_by_historical.items()
        ),
    )

    historical_stage_counts: dict[str, int] = {}
    for item in historical:
        actual = evaluate(item["compact_frame"], adapter, validator, schema_validator, segments)
        expected = item["expected"]
        passed = actual == expected
        if item["classification"] == "obsolete":
            passed = passed and actual["stage"] == "accepted"
        else:
            passed = passed and actual["stage"] != "accepted"
        historical_stage_counts[actual["stage"]] = historical_stage_counts.get(actual["stage"], 0) + 1
        record(f"historical:{item['historical_id']}", passed, {"expected": expected, "actual": actual})

    native = fixture["native_negative_cases"]
    record("native_case_ids_complete", {item["id"] for item in native} == EXPECTED_NATIVE_IDS)
    for item in native:
        actual = evaluate(item["compact_frame"], adapter, validator, schema_validator, segments)
        record(
            f"native:{item['id']}",
            actual == item["expected"] and actual["stage"] != "accepted",
            {"expected": item["expected"], "actual": actual},
        )

    positives = fixture["positive_controls"]
    record("positive_case_ids_complete", {item["id"] for item in positives} == EXPECTED_POSITIVE_IDS)
    for item in positives:
        actual = evaluate(item["compact_frame"], adapter, validator, schema_validator, segments)
        record(
            f"positive:{item['id']}",
            actual == item["expected"] and actual["stage"] == "accepted",
            {"expected": item["expected"], "actual": actual},
        )

    record("fixture_has_no_raw_queries", fixture.get("raw_queries") == 0)
    record("registry_consistency", validator.registry_errors() == [], validator.registry_errors())
    record(
        "obsolete_is_only_derived_clause_ids",
        {
            item["historical_id"] for item in entries
            if item["classification"] == "obsolete"
        } == {
            "M011_duplicate_clause_id", "M012_clause_id_reverse",
            "X003_supported_unsupported_clause_collision",
        },
    )

    failed = [item for item in checks if not item["pass"]]
    return {
        "version": "metnos.v26.5.1-compact-mutation-suite/0.1",
        "network_calls": 0,
        "model_calls": 0,
        "historical_runner_imports": 0,
        "dynamic_imports_after_hash_verification": 2,
        "summary": {
            "tests": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "historical_mutations": len(historical),
            "native_negative_cases": len(native),
            "positive_controls": len(positives),
            "classifications": matrix["classification_counts"],
            "historical_stages": historical_stage_counts,
        },
        "hashes": {name: sha256(path) for name, path in PATHS.items()},
        "failed": failed,
        "checks": checks,
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if result["summary"]["failed"] == 0 else 1)
