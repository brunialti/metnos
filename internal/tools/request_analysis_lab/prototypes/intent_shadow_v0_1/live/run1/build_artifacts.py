#!/usr/bin/env python3
"""Deterministically build/check RUN 1 artifacts; never opens gold or network."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from typing import Any, Callable

from .protocol import (
    BACKEND_ABSOLUTE_SOURCES,
    BUILD_SOURCE_FILES,
    CANDIDATE_FREEZE_PATH,
    CANDIDATE_PROJECTION_PATH,
    CANDIDATE_PROMPT_PATH,
    CANDIDATE_SCHEMA_PATH,
    CONTROL_REGISTRY_PATH,
    CONTROL_REPO_SOURCES,
    CONTROL_SNAPSHOT_PATH,
    CREATED_DATE,
    FREEZE_FORMAT,
    FREEZE_PATH,
    HERE,
    IMMUTABLE_LOCAL_FILES,
    LEGACY_DIR,
    MANIFEST_FORMAT,
    MANIFEST_PATH,
    PROTOCOL_FORMAT,
    PROTOCOL_PATH,
    QUERY_PANEL_FORMAT,
    QUERY_PANEL_PATH,
    ROOT,
    RUN_ID,
    SNAPSHOT_FORMAT,
    arm_order,
    backend_constants,
    canonical_json_bytes,
    detection_lexicon_identity,
    file_sha256,
    generation_profile,
    load_control_snapshot,
    load_query_panel,
    payload_sha256,
    pretty_json_bytes,
    request_for,
    stable_file_sha256,
    strict_json_file,
    verify_candidate_environment,
)


def build_control_snapshot() -> dict[str, Any]:
    behavior_sha, row_count = detection_lexicon_identity()
    backend = backend_constants()
    backend["absolute_sources"] = {
        path: stable_file_sha256(Path(path)) for path in sorted(BACKEND_ABSOLUTE_SOURCES)
    }
    result = {
        "snapshot_format": SNAPSHOT_FORMAT,
        "run_id": RUN_ID,
        "created_date": CREATED_DATE,
        "arm_id": "A",
        "provenance": {
            "label": "current_metnos_intent_extractor_fresh_run1_snapshot",
            "fresh_control": True,
            "production_read_only": True,
            "historic_candidate_repair": False,
        },
        "current_extractor": {
            "entrypoint": "runtime.intent_extractor.extract_intent",
            "workload": "intent.extract",
            "tier": "fast",
            "level": "micro",
            "scaffold": False,
            "prompt_role": "intent_extractor_v4",
            "prompt_languages": ["en", "it"],
            "response_format": "current_control_unchanged_none",
            "measurement_max_tokens": 4000,
            "single_primary_response": True,
            "secondary_probe_disabled": True,
        },
        "repo_sources": {
            relative: file_sha256(ROOT / relative)
            for relative in sorted(CONTROL_REPO_SOURCES)
        },
        "runtime_state": {
            "path": "/home/roberto/.local/share/metnos/detection.sqlite",
            "mode": "read_only",
            "behavior_sha256": behavior_sha,
            "row_count": row_count,
            "language_policy": "canonical_and_typed=it; legacy=case_language; loader fallback unchanged",
        },
        "backend_identity": backend,
        "snapshot_payload_sha256": "",
    }
    result["snapshot_payload_sha256"] = payload_sha256(result, "snapshot_payload_sha256")
    return result


def build_query_panel() -> dict[str, Any]:
    suite = strict_json_file(LEGACY_DIR / "intent_shadow_query_suite_v0_1.json")
    legacy = strict_json_file(LEGACY_DIR / "legacy_panel_v0_1.json")
    if suite.get("gold_fields_present") is not False or legacy.get("gold_fields_present") is not False:
        raise RuntimeError("query sources are not gold-free")
    cases: list[dict[str, Any]] = []
    for panel in ("canonical_120", "typed_controls_4"):
        for case in suite["panels"][panel]:
            cases.append({
                "sample_index": len(cases), "panel": panel,
                "panel_ordinal": case["ordinal"], "opaque_case_id": case["opaque_case_id"],
                "language": "it", "query": case["query"], "query_sha256": case["query_sha256"],
            })
    for case in legacy["cases"]:
        cases.append({
            "sample_index": len(cases), "panel": "legacy_phase1_34",
            "panel_ordinal": case["ordinal"], "opaque_case_id": case["opaque_case_id"],
            "language": case["language"], "query": case["query"], "query_sha256": case["query_sha256"],
        })
    if len(cases) != 158:
        raise RuntimeError("query count")
    result = {
        "panel_format": QUERY_PANEL_FORMAT, "run_id": RUN_ID,
        "created_date": CREATED_DATE, "status": "query_only_frozen",
        "gold_fields_present": False,
        "counts": {"canonical_120": 120, "typed_controls_4": 4, "legacy_phase1_34": 34, "total": 158},
        "cases": cases, "panel_payload_sha256": "",
    }
    result["panel_payload_sha256"] = payload_sha256(result, "panel_payload_sha256")
    return result


def build_protocol() -> dict[str, Any]:
    verify_candidate_environment()
    snapshot = load_control_snapshot()
    result = {
        "protocol_format": PROTOCOL_FORMAT, "run_id": RUN_ID,
        "created_date": CREATED_DATE, "status": "disarmed_prepared_no_inference",
        "iteration": {"run_number": 1, "maximum_new_runs_authorized": 3, "later_runs_require_result_review_and_new_protocol": True},
        "authorization": {"state": "disarmed", "authorization_file": "authorization_run1.json", "authorization_must_be_last": True, "independent_audit_required": True, "inference_executed": False},
        "arms": {
            "A": "fresh current Metnos intent extractor snapshot plus read-only lab adapter",
            "B": "candidate_v0_2 compact IR, strict structured output, deterministic compiler, critic off",
        },
        "backend": deepcopy(snapshot["backend_identity"]),
        "generation_profile": generation_profile(),
        "technical_limits": {"json_bytes": 262144, "depth": 64, "nodes": 10000, "string_chars": 65536, "integer_digits": 64, "classification": "technical_invalid", "never_unrepresentable": True},
        "schedule": {"algorithm": "AB on even sample_index; BA on odd sample_index", "sample_index_range": [0, 157], "query_count": 158, "arm_count": 2, "request_count": 316, "serial": True, "paired_request_optimization": False},
        "panels": {
            "canonical_120": {"queries": 120, "both_arms": True, "reported_separately": True},
            "typed_controls_4": {"queries": 4, "both_arms": True, "reported_separately": True},
            "legacy_phase1_34": {"queries": 34, "both_arms": True, "reported_separately": True, "automatic_conversion": False, "cross_panel_compensation": False},
        },
        "failure_policy": {"consumed_at": "first accepted HTTP POST", "marker_written_before_first_socket_attempt": True, "ambiguous_first_transport_attempt_blocks_rerun": True, "retry": False, "transport_or_timeout": "stop_and_seal_partial", "envelope_or_adapter_failure": "stop_and_seal_partial", "document_invalid": "count_error_and_continue", "raw_response_capture": True, "append_only_journal": True, "atomic_checkpoints": True},
        "evaluation": {"version": "v0.3-symmetric", "gold_access": "only_after_complete_316_record_batch_and_seal_validate", "canonical_projection_symmetric": True, "critical_columns": ["semantic_exact_canonical", "root_exact", "correct_abstention", "technical_valid", "false_action_avoided", "undo_exact", "consent_exact", "negation_exact", "branch_ownership_exact"], "typed_special_gate": "candidate_B_exact_4_of_4", "canonical_improvement_gate": "candidate_B_minus_control_A_at_least_3_exact_of_120", "inconclusive_delta": [-2, 2], "legacy_report": "separate Phase-1 direct-binding panel; no compensation"},
        "bindings": {
            "control_snapshot_file_sha256": file_sha256(CONTROL_SNAPSHOT_PATH),
            "query_panel_file_sha256": file_sha256(QUERY_PANEL_PATH),
            "candidate_freeze_file_sha256": file_sha256(CANDIDATE_FREEZE_PATH),
            "candidate_projection_file_sha256": file_sha256(CANDIDATE_PROJECTION_PATH),
            "candidate_prompt_file_sha256": file_sha256(CANDIDATE_PROMPT_PATH),
            "candidate_schema_file_sha256": file_sha256(CANDIDATE_SCHEMA_PATH),
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
        "counts": {"queries": 158, "arms": 2, "requests": 316},
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
    "snapshot": (CONTROL_SNAPSHOT_PATH, build_control_snapshot),
    "query_panel": (QUERY_PANEL_PATH, build_query_panel),
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
