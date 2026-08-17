#!/usr/bin/env python3
"""Deterministically build/check RUN 2 artifacts; never opens gold or network."""
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
    """Return the exact immutable RUN1 control authority; never regenerate it."""
    return load_control_snapshot()


def build_query_panel() -> dict[str, Any]:
    """Return the exact immutable RUN1 query panel; never regenerate it."""
    return load_query_panel()


def build_protocol() -> dict[str, Any]:
    verify_candidate_environment()
    snapshot = load_control_snapshot()
    result = {
        "protocol_format": PROTOCOL_FORMAT, "run_id": RUN_ID,
        "created_date": CREATED_DATE, "status": "disarmed_prepared_no_inference",
        "iteration": {
            "run_number": 2,
            "measure_class": "adaptive_development",
            "logical_change": "prompt_projection_only",
            "automatic_next_run_authorized_after_green_suite_and_independent_audit": True,
            "rigid_run_limit": None,
            "stop_on_new_semantic_policy_or_identical_stalled_run": True,
        },
        "authorization": {"state": "disarmed", "authorization_file": "authorization_run2.json", "authorization_must_be_last": True, "independent_audit_required": True, "inference_executed": False},
        "arms": {
            "A": "exact RUN1 current Metnos control snapshot plus unchanged read-only lab adapter",
            "B": "candidate_v0_3 prompt-only overlay on v0.2 compact IR, strict structured output, deterministic compiler, critic off",
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
