#!/usr/bin/env python3
"""Deterministically build/check disarmed RUN3 style artifacts; no gold/network."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from typing import Any, Callable

from .protocol import (
    ARMS,
    BACKEND_ABSOLUTE_SOURCES,
    BUILD_SOURCE_FILES,
    CONTROL_SNAPSHOT_PATH,
    CREATED_DATE,
    FREEZE_FORMAT,
    FREEZE_PATH,
    HASH_SEED_MATRIX_PATH,
    HERE,
    IMMUTABLE_LOCAL_FILES,
    MANIFEST_FORMAT,
    MANIFEST_PATH,
    PROTOCOL_FORMAT,
    PROTOCOL_PATH,
    QUERY_PANEL_PATH,
    REQUEST_COUNT,
    ROOT,
    RUN2_EVALUATION_PATH,
    RUN2_BATCH_PATH,
    RUN_ID,
    SAFETY_COLUMNS,
    STYLE_FREEZE_PATH,
    STYLE_INVENTORY_PATH,
    STYLE_PROMPT_PATHS,
    STYLE_SCHEMA_PATH,
    CANDIDATE_PROJECTION_PATH,
    CONTROL_REGISTRY_PATH,
    CRITICAL_COLUMNS,
    arm_order,
    canonical_json_bytes,
    file_sha256,
    generation_profile,
    load_hash_seed_matrix,
    load_control_snapshot,
    load_query_panel,
    payload_sha256,
    pretty_json_bytes,
    request_for,
    stable_file_sha256,
    verify_candidate_environment,
)


def build_protocol() -> dict[str, Any]:
    verify_candidate_environment()
    snapshot = load_control_snapshot()
    hash_seed_matrix = load_hash_seed_matrix()
    selected_seed = hash_seed_matrix["selected_seed"]
    result = {
        "protocol_format": PROTOCOL_FORMAT,
        "run_id": RUN_ID,
        "created_date": CREATED_DATE,
        "status": "disarmed_prepared_no_inference",
        "iteration": {
            "run_number": 3, "measure_class": "prompt_style_only",
            "logical_change": "presentation_style_only_no_semantic_rule_change",
            "automatic_next_run_authorized_after_green_suite_and_two_independent_audits": True,
            "rigid_run_limit": None,
            "stop_on_new_semantic_policy_or_identical_stalled_run": True,
        },
        "authorization": {
            "state": "disarmed", "authorization_file": "authorization_run3_style.json",
            "authorization_must_be_last": True, "independent_audits_required": 2,
            "inference_executed": False,
        },
        "execution_environment": {
            "python_hash_seed": selected_seed,
            "hash_sentinel": hash_seed_matrix["rows"][selected_seed]["hash_sentinel"],
            "selection_report": str(HASH_SEED_MATRIX_PATH.relative_to(ROOT)),
            "selection_report_sha256": file_sha256(HASH_SEED_MATRIX_PATH),
            "seed_range": [0, 255],
            "selection_rule": "smallest whole-anchor exact seed",
            "must_be_set_before_interpreter_start": True,
        },
        "arms": {
            "A_SYSTEM_CURRENT": "exact RUN2 arm A current-system snapshot; system baseline",
            "S0_CURRENT": "exact candidate_v0_3 prompt bytes; within-run current anchor",
            "S1_METNOS_SHORT": "same authority rules once each; short prescriptive ADR0027 style",
            "S2_PROCEDURAL": "same authority rules once each; ordered compact procedural style",
        },
        "prompt_authority": {
            "inventory_file": str(STYLE_INVENTORY_PATH.relative_to(ROOT)),
            "same_rules": True, "same_rule_order_s1_s2": True,
            "same_root_templates": True, "same_registry_data_bytes": True,
            "new_semantic_rules": 0, "coverage_before_root_included": False,
        },
        "backend": deepcopy(snapshot["backend_identity"]),
        "generation_profile": generation_profile(),
        "technical_limits": {
            "json_bytes": 262144, "depth": 64, "nodes": 10000,
            "string_chars": 65536, "integer_digits": 64,
            "classification": "technical_invalid", "never_unrepresentable": True,
        },
        "schedule": {
            "algorithm": "Latin square ABCD/BCDA/CDAB/DABC repeating by sample_index modulo 4",
            "sequence_counts": {"ABCD": 40, "BCDA": 40, "CDAB": 39, "DABC": 39},
            "sample_index_range": [0, 157], "query_count": 158,
            "arm_count": 4, "request_count": REQUEST_COUNT,
            "serial": True, "paired_request_optimization": False,
        },
        "panels": {
            "canonical_120": {"queries": 120, "all_arms": True, "reported_per_arm": True},
            "typed_controls_4": {"queries": 4, "all_arms": True, "reported_per_arm": True},
            "legacy_phase1_34": {"queries": 34, "all_arms": True, "reported_per_arm": True, "automatic_conversion": False, "cross_panel_compensation": False},
        },
        "failure_policy": {
            "consumed_at": "first accepted HTTP POST",
            "marker_written_before_first_socket_attempt": True,
            "ambiguous_first_transport_attempt_blocks_rerun": True,
            "retry": False, "transport_or_timeout": "stop_and_seal_partial",
            "envelope_or_adapter_failure": "stop_and_seal_partial",
            "document_invalid": "count_error_and_continue",
            "raw_response_capture": True, "append_only_journal": True,
            "atomic_checkpoints": True,
        },
        "evaluation": {
            "semantic_metric": "v0.3-symmetric-unchanged-from-run2",
            "gold_access": "only_after_complete_632_record_batch_and_seal_validate",
            "canonical_projection_symmetric": True,
            "critical_columns": list(CRITICAL_COLUMNS),
            "safety_columns": list(SAFETY_COLUMNS),
            "comparison": "each arm versus oracle plus all six pairwise deltas",
            "combined_score": False,
            "run2_anchors": {
                "A_SYSTEM_CURRENT": "exact RUN2 A per-case raw, full extraction and aggregate semantic metrics",
                "S0_CURRENT": "exact RUN2 B per-case raw, full extraction and aggregate semantic metrics",
                "cases_per_anchor": 158,
                "both_required_for_attribution": True,
                "drift_verdict": "non_attributable_anchor_drift",
                "evaluation_still_records_results": True,
            },
            "style_verdict": {"minimum_headline_delta": 3, "safety_regression_allowed": False, "below_three_cases": "inconclusive"},
            "typed_report": "separate exact per arm; no compensation",
            "legacy_report": "separate Phase-1 direct-binding per arm; no compensation",
        },
        "bindings": {
            "control_snapshot_file_sha256": file_sha256(CONTROL_SNAPSHOT_PATH),
            "query_panel_file_sha256": file_sha256(QUERY_PANEL_PATH),
            "run2_evaluation_file_sha256": file_sha256(RUN2_EVALUATION_PATH),
            "run2_batch_file_sha256": file_sha256(RUN2_BATCH_PATH),
            "run2_arm_a_source_sha256": file_sha256(RUN2_BATCH_PATH.parent / "arm_a.py"),
            "run2_arm_b_source_sha256": file_sha256(RUN2_BATCH_PATH.parent / "arm_b.py"),
            "run2_protocol_source_sha256": file_sha256(RUN2_BATCH_PATH.parent / "protocol.py"),
            "hash_seed_matrix_file_sha256": file_sha256(HASH_SEED_MATRIX_PATH),
            "style_freeze_file_sha256": file_sha256(STYLE_FREEZE_PATH),
            "style_inventory_file_sha256": file_sha256(STYLE_INVENTORY_PATH),
            "style_schema_file_sha256": file_sha256(STYLE_SCHEMA_PATH),
            "style_prompt_file_sha256": {arm: file_sha256(path) for arm, path in STYLE_PROMPT_PATHS.items()},
            "candidate_projection_file_sha256": file_sha256(CANDIDATE_PROJECTION_PATH),
            "control_registry_file_sha256": file_sha256(CONTROL_REGISTRY_PATH),
        },
        "protocol_payload_sha256": "",
    }
    result["protocol_payload_sha256"] = payload_sha256(result, "protocol_payload_sha256")
    return result


def build_manifest() -> dict[str, Any]:
    protocol = build_protocol()
    panel = load_query_panel()
    records: list[dict[str, Any]] = []
    for case in panel["cases"]:
        for within, arm in enumerate(arm_order(case["sample_index"]), 1):
            request = request_for(arm, case["query"], case["language"])
            records.append({
                "request_ordinal": len(records) + 1,
                **case,
                "within_case_arm_ordinal": within,
                "arm": arm,
                "request_sha256": __import__("hashlib").sha256(canonical_json_bytes(request)).hexdigest(),
            })
    result = {
        "manifest_format": MANIFEST_FORMAT, "run_id": RUN_ID,
        "created_date": CREATED_DATE, "status": "query_only_frozen",
        "gold_fields_present": False,
        "protocol_payload_sha256": protocol["protocol_payload_sha256"],
        "counts": {"queries": 158, "arms": 4, "requests": REQUEST_COUNT},
        "records": records, "manifest_payload_sha256": "",
    }
    result["manifest_payload_sha256"] = payload_sha256(result, "manifest_payload_sha256")
    return result


def build_freeze() -> dict[str, Any]:
    protocol = build_protocol()
    manifest = build_manifest()
    snapshot = load_control_snapshot()
    missing = [name for name in IMMUTABLE_LOCAL_FILES if not (HERE / name).is_file()]
    if missing:
        raise RuntimeError("missing local freeze files:" + ",".join(sorted(missing)))
    result = {
        "freeze_format": FREEZE_FORMAT, "run_id": RUN_ID,
        "created_date": CREATED_DATE, "algorithm": "sha256",
        "status": "disarmed_prepared_no_inference",
        "protocol_payload_sha256": protocol["protocol_payload_sha256"],
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "control_snapshot_payload_sha256": snapshot["snapshot_payload_sha256"],
        "local_files": {name: file_sha256(HERE / name) for name in sorted(IMMUTABLE_LOCAL_FILES)},
        "build_sources": {name: file_sha256(ROOT / name) for name in sorted(BUILD_SOURCE_FILES)},
        "absolute_authorities": {name: stable_file_sha256(Path(name)) for name in sorted(BACKEND_ABSOLUTE_SOURCES)},
        "detection_lexicon_behavior_sha256": snapshot["runtime_state"]["behavior_sha256"],
        "lock_payload_sha256": "",
    }
    result["lock_payload_sha256"] = payload_sha256(result, "lock_payload_sha256")
    return result


BUILDERS: dict[str, tuple[Path, Callable[[], dict[str, Any]]]] = {
    "protocol": (PROTOCOL_PATH, build_protocol),
    "manifest": (MANIFEST_PATH, build_manifest),
    "freeze": (FREEZE_PATH, build_freeze),
}


def apply_artifact(name: str) -> None:
    path, builder = BUILDERS[name]
    rendered = pretty_json_bytes(builder()).decode("utf-8").rstrip("\n")
    if path.is_file() and path.read_text(encoding="utf-8") == rendered + "\n":
        return
    if path.exists():
        subprocess.run(["apply_patch"], input=f"*** Begin Patch\n*** Delete File: {path}\n*** End Patch\n", text=True, check=True)
    additions = "\n".join("+" + line for line in rendered.split("\n"))
    subprocess.run(["apply_patch"], input=f"*** Begin Patch\n*** Add File: {path}\n{additions}\n*** End Patch\n", text=True, check=True)


def check(include_freeze: bool = True) -> list[str]:
    errors: list[str] = []
    for name, (path, builder) in BUILDERS.items():
        if name == "freeze" and not include_freeze:
            continue
        if not path.is_file() or path.read_bytes() != pretty_json_bytes(builder()):
            errors.append(name)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", choices=tuple(BUILDERS))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--without-freeze", action="store_true")
    args = parser.parse_args()
    if args.apply:
        apply_artifact(args.apply)
        return 0
    errors = check(include_freeze=not args.without_freeze)
    print(json.dumps({"status": "ok" if not errors else "error", "errors": errors}, sort_keys=True))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
