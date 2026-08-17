#!/usr/bin/env python3
"""Build/check query-only panels from frozen authorities.

This build-time module may read gold to prove bindings.  The runner does not
import it and receives only the materialized query-only artifacts.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from intent_shadow_io import canonical_json_bytes, file_sha256, strict_json_file


SUITE_FORMAT = "metnos.intent-shadow-query-suite/0.1"
LEGACY_FORMAT = "metnos.intent-shadow-legacy-panel/0.1"
CONTRACT_VERSION = "metnos.intent-shadow/0.1"
CREATED_DATE = "2026-08-12"


HERE = Path(__file__).resolve().parent


def find_repo_root() -> Path:
    for parent in (HERE, *HERE.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("repository root not found")


ROOT = find_repo_root()
ORACLE = ROOT / "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_oracle_v0_1.json"
SAMPLE = ROOT / "internal/tools/request_analysis_lab/misure_11_8/prova_specchi_riparo_c11.json"
BASE_CONTROLS = ROOT / "internal/tools/request_analysis_lab/question_focus_controls_v1.json"
PHASE1_ORACLE = ROOT / "internal/tools/request_analysis_lab/oracles/phase1_v1/metnos_phase1_typed_oracle_v1.overlay.json"
QUERY_SUITE = HERE / "intent_shadow_query_suite_v0_1.json"
LEGACY_PANEL = HERE / "legacy_panel_v0_1.json"


def _payload_sha(document: dict[str, Any], field: str) -> str:
    payload = deepcopy(document)
    payload.pop(field, None)
    return sha256(canonical_json_bytes(payload)).hexdigest()


def build_query_suite() -> dict[str, Any]:
    oracle = strict_json_file(ORACLE)
    sample = strict_json_file(SAMPLE)
    if type(oracle) is not dict or type(sample) is not dict:
        raise RuntimeError("query authorities must be objects")
    queries = sample.get("queries")
    cases = oracle.get("cases")
    controls = oracle.get("new_controls")
    if type(queries) is not list or len(queries) != 120:
        raise RuntimeError("sample does not contain exactly 120 queries")
    if type(cases) is not list or len(cases) != 120:
        raise RuntimeError("oracle does not contain exactly 120 cases")
    if type(controls) is not list or len(controls) != 4:
        raise RuntimeError("oracle does not contain exactly four typed controls")
    canonical: list[dict[str, Any]] = []
    for sample_index, (query, case) in enumerate(zip(queries, cases, strict=True)):
        ordinal = sample_index + 1
        if (
            type(query) is not str
            or type(case) is not dict
            or case.get("sample_index") != sample_index
            or case.get("query_text") != query
            or case.get("query_sha256") != sha256(query.encode("utf-8")).hexdigest()
            or type(case.get("case_id")) is not str
        ):
            raise RuntimeError(f"canonical query binding mismatch at {ordinal}")
        canonical.append(
            {
                "ordinal": ordinal,
                "sample_index": sample_index,
                "opaque_case_id": case["case_id"],
                "query": query,
                "query_sha256": case["query_sha256"],
            }
        )
    typed: list[dict[str, Any]] = []
    for ordinal, control in enumerate(controls, 1):
        if (
            type(control) is not dict
            or control.get("ordinal") != ordinal + 34
            or type(control.get("query_text")) is not str
            or control.get("query_sha256")
            != sha256(control["query_text"].encode("utf-8")).hexdigest()
            or type(control.get("control_id")) is not str
        ):
            raise RuntimeError(f"typed control binding mismatch at {ordinal}")
        typed.append(
            {
                "ordinal": ordinal,
                "authorized_control_ordinal": control["ordinal"],
                "opaque_case_id": control["control_id"],
                "query": control["query_text"],
                "query_sha256": control["query_sha256"],
            }
        )
    result = {
        "suite_format": SUITE_FORMAT,
        "contract_version": CONTRACT_VERSION,
        "created_date": CREATED_DATE,
        "status": "query_only_frozen",
        "gold_fields_present": False,
        "sample_sha256": oracle["binding"]["sample_sha256"],
        "registry_payload_sha256": oracle["registry_binding"]["registry_payload_sha256"],
        "source_file_sha256": {
            "oracle": file_sha256(ORACLE),
            "sample": file_sha256(SAMPLE),
        },
        "counts": {"canonical_120": 120, "typed_controls_4": 4, "total": 124},
        "panels": {
            "canonical_120": canonical,
            "typed_controls_4": typed,
        },
        "suite_payload_sha256": "",
    }
    result["suite_payload_sha256"] = _payload_sha(result, "suite_payload_sha256")
    return result


def build_legacy_panel() -> dict[str, Any]:
    controls = strict_json_file(BASE_CONTROLS)
    phase1 = strict_json_file(PHASE1_ORACLE)
    if type(controls) is not dict or type(phase1) is not dict:
        raise RuntimeError("legacy authorities must be objects")
    source_cases = controls.get("cases")
    oracle_cases = phase1.get("cases")
    if type(source_cases) is not list or len(source_cases) != 34:
        raise RuntimeError("legacy source must contain 34 cases")
    if type(oracle_cases) is not list or len(oracle_cases) != 34:
        raise RuntimeError("Phase-1 oracle must contain 34 cases")
    oracle_ids = [item.get("case_id") for item in oracle_cases if type(item) is dict]
    source_ids = [item.get("id") for item in source_cases if type(item) is dict]
    if len(oracle_ids) != 34 or oracle_ids != source_ids or len(set(source_ids)) != 34:
        raise RuntimeError("legacy source/oracle identity mismatch")
    query_only: list[dict[str, Any]] = []
    for ordinal, case in enumerate(source_cases, 1):
        query = case.get("query")
        if type(query) is not str or type(case.get("id")) is not str or type(case.get("lang")) is not str:
            raise RuntimeError(f"legacy query binding mismatch at {ordinal}")
        query_only.append(
            {
                "ordinal": ordinal,
                "opaque_case_id": case["id"],
                "language": case["lang"],
                "query": query,
                "query_sha256": sha256(query.encode("utf-8")).hexdigest(),
            }
        )
    result = {
        "panel_format": LEGACY_FORMAT,
        "created_date": CREATED_DATE,
        "status": "frozen_separate_phase1_panel",
        "decision": {
            "owner": "Roberto",
            "selected_option": "A",
            "automatic_intent_shadow_conversion": False,
            "cross_panel_compensation": False,
            "evaluation_authority": "phase1_typed_oracle_v1",
        },
        "gold_fields_present": False,
        "count": 34,
        "source_file_sha256": {
            "controls": file_sha256(BASE_CONTROLS),
            "phase1_oracle": file_sha256(PHASE1_ORACLE),
        },
        "cases": query_only,
        "panel_payload_sha256": "",
    }
    result["panel_payload_sha256"] = _payload_sha(result, "panel_payload_sha256")
    return result


def check_artifacts() -> dict[str, Any]:
    expected_suite = build_query_suite()
    expected_legacy = build_legacy_panel()
    observed_suite = strict_json_file(QUERY_SUITE)
    observed_legacy = strict_json_file(LEGACY_PANEL)
    errors: list[str] = []
    if observed_suite != expected_suite:
        errors.append("QUERY_SUITE_MISMATCH")
    if observed_legacy != expected_legacy:
        errors.append("LEGACY_PANEL_MISMATCH")
    return {
        "status": "ok" if not errors else "error",
        "error_count": len(errors),
        "errors": errors,
        "counts": {"canonical": 120, "typed_controls": 4, "legacy": 34},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--print", choices=("suite", "legacy"))
    args = parser.parse_args()
    if args.print == "suite":
        print(json.dumps(build_query_suite(), ensure_ascii=False, indent=2))
        return 0
    if args.print == "legacy":
        print(json.dumps(build_legacy_panel(), ensure_ascii=False, indent=2))
        return 0
    report = check_artifacts()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
