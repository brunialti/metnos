#!/usr/bin/env python3
"""Deterministically materialize the frozen live protocol artifacts.

The default command only compares on-disk files.  ``--print`` emits one
artifact for review/apply_patch; it never writes and never performs network
I/O.  Gold is not imported or read.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from typing import Any

from intent_shadow_io import file_sha256
from live_arm_candidate import build_request as candidate_request
from live_arm_current import build_request as current_request
from live_protocol import (
    BACKEND_ABSOLUTE_SOURCES,
    CONTROL_REPO_SOURCES,
    CONTROL_SNAPSHOT_PATH,
    CREATED_DATE,
    HERE,
    IMMUTABLE_PROTOCOL_LOCAL_FILES,
    MANIFEST_FORMAT,
    MODEL_PROMPT_PATH,
    MODEL_SCHEMA_PATH,
    PROTOCOL_FORMAT,
    PROTOCOL_FREEZE_FORMAT,
    PROTOCOL_FREEZE_PATH,
    PROTOCOL_PATH,
    QUERY_SUITE_PATH,
    LEGACY_PANEL_PATH,
    REGISTRY_PATH,
    REQUEST_MANIFEST_PATH,
    ROOT,
    SNAPSHOT_FORMAT,
    all_query_cases,
    arm_order,
    canonical_write_bytes,
    detection_lexicon_behavior_sha256,
    load_control_snapshot,
    payload_sha256,
    stable_file_sha256,
)


def build_control_snapshot() -> dict[str, Any]:
    behavior_sha, row_count = detection_lexicon_behavior_sha256()
    result = {
        "snapshot_format": SNAPSHOT_FORMAT,
        "created_date": CREATED_DATE,
        "arm_id": "A",
        "provenance": {
            "label": "current_metnos_intent_extractor_with_lab_adapter",
            "fresh_control": True,
            "production_read_only": True,
            "historic_v23lite_repair": False,
            "snapshot_kind": "hash_pinned_sources_plus_logical_detection_lexicon",
        },
        "current_extractor": {
            "entrypoint": "runtime.intent_extractor.extract_intent",
            "workload": "intent.extract",
            "tier": "fast",
            "level": "micro",
            "scaffold": False,
            "prompt_role": "intent_extractor_v4",
            "prompt_languages": ["en", "it"],
            "response_schema": "none_tolerant_parser_in_entrypoint",
            "production_max_tokens": 320,
            "measurement_profile_override": 4000,
            "adapter_delta": (
                "exactly_one_primary_model_response; deterministic bypasses still "
                "receive one paired request; optional open-source probe is disabled "
                "after the primary response"
            ),
        },
        "repo_sources": {
            relative: file_sha256(ROOT / relative)
            for relative in sorted(CONTROL_REPO_SOURCES)
        },
        "runtime_state": {
            "detection_lexicon_path": "/home/roberto/.local/share/metnos/detection.sqlite",
            "behavior_sha256": behavior_sha,
            "row_count": row_count,
            "language_policy": (
                "canonical_and_typed=it; legacy=case_language; missing prompt falls "
                "back to en exactly as current loader"
            ),
        },
        "backend_identity": {
            "provider": "llamacpp",
            "endpoint": "http://localhost:8080/v1/chat/completions",
            "api_model_id": "local",
            "physical_model_id": "qwen3.6-35b-a3b",
            "weights_path": "/home/roberto/models/qwen36-35b-mtp/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
            "weights_size_bytes": 22663387424,
            "weights_sha256": "0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b",
            "source_revision": "5bc3e238d916f48a861bac2f8a1990a0e9b7e98d",
            "backend": "llama.cpp Vulkan",
            "backend_build": "1422 (e3546c794)",
            "backend_binary": "/home/roberto/llama.cpp-0712/build/bin/llama-server",
            "backend_binary_sha256": "14f86e51ea42cc2362285d1139d475d58b985a968e78cfbfdbdf9d9e9ee87c78",
            "absolute_sources": {
                absolute: stable_file_sha256(Path(absolute))
                for absolute in sorted(BACKEND_ABSOLUTE_SOURCES)
            },
        },
        "snapshot_payload_sha256": "",
    }
    result["snapshot_payload_sha256"] = payload_sha256(result, "snapshot_payload_sha256")
    return result


def build_protocol() -> dict[str, Any]:
    snapshot = load_control_snapshot(CONTROL_SNAPSHOT_PATH)
    result = {
        "protocol_format": PROTOCOL_FORMAT,
        "protocol_version": "intent-shadow-paired-gpu/0.1",
        "created_date": CREATED_DATE,
        "status": "prepared_not_authorized",
        "authorization": {
            "owner": "Roberto",
            "decisions_approved": 7,
            "inference_executed": False,
            "network_preflight_allowed": False,
            "independent_audit_required": True,
            "root_final_authorization_required": True,
            "authorization_file": "live_run_authorization_v0_1.json",
        },
        "arms": {
            "A": "fresh current Metnos intent extractor plus frozen lab adapter",
            "B": "intent-shadow candidate 0.1 direct schema extractor",
        },
        "backend": deepcopy(snapshot["backend_identity"]),
        "generation_profile": {
            "temperature": 0,
            "seed": 42,
            "max_output_tokens": 4000,
            "timeout_seconds": 120,
            "thinking": False,
            "retry_count": 0,
            "same_for_both_arms": True,
        },
        "technical_limits": {
            "json_bytes": 262144,
            "depth": 64,
            "nodes": 10000,
            "string_chars": 65536,
            "integer_digits": 64,
            "classification": "technical_invalid",
            "never_unrepresentable": True,
        },
        "schedule": {
            "algorithm": "AB on even sample_index; BA on odd sample_index",
            "sample_index_range": [0, 157],
            "query_count": 158,
            "arm_count": 2,
            "request_count": 316,
            "paired_request_optimization": False,
        },
        "panels": {
            "canonical_120": {"queries": 120, "both_arms": True, "reported_separately": True},
            "typed_controls_4": {"queries": 4, "both_arms": True, "reported_separately": True},
            "legacy_phase1_34": {
                "queries": 34,
                "both_arms": True,
                "separate_phase1_oracle": True,
                "automatic_conversion": False,
                "cross_panel_compensation": False,
            },
        },
        "failure_policy": {
            "consumed_at": "first accepted HTTP POST",
            "marker_written_before_first_socket_attempt": True,
            "ambiguous_first_transport_attempt_blocks_rerun": True,
            "retry": False,
            "transport_or_timeout_after_consumption": "stop_and_seal_partial",
            "invalid_json_or_semantics": "count_error_and_continue",
            "raw_response_capture": True,
            "append_only_journal": True,
            "atomic_checkpoints": True,
        },
        "evaluation": {
            "gold_access": "only_after_complete_316_record_batch_is_sealed",
            "typed_special_gate": "candidate_B_exact_4_of_4",
            "critical_columns_no_regression": [
                "semantic_exact", "root_exact", "correct_abstention", "technical_valid",
                "false_action_avoided", "undo_exact", "consent_exact",
                "negation_exact", "branch_ownership_exact",
            ],
            "canonical_improvement_gate": "candidate_B_minus_control_A_at_least_3_exact_of_120",
            "inconclusive_delta": [-2, 2],
            "legacy_report": "separate Phase-1 direct-binding panel; no compensation",
        },
        "bindings": {
            "control_snapshot_file_sha256": file_sha256(CONTROL_SNAPSHOT_PATH),
            "registry_file_sha256": file_sha256(REGISTRY_PATH),
            "query_suite_file_sha256": file_sha256(QUERY_SUITE_PATH),
            "legacy_panel_file_sha256": file_sha256(LEGACY_PANEL_PATH),
            "candidate_prompt_sha256": file_sha256(MODEL_PROMPT_PATH),
            "candidate_schema_sha256": file_sha256(MODEL_SCHEMA_PATH),
        },
        "protocol_payload_sha256": "",
    }
    result["protocol_payload_sha256"] = payload_sha256(result, "protocol_payload_sha256")
    return result


def build_manifest() -> dict[str, Any]:
    protocol = build_protocol()
    records: list[dict[str, Any]] = []
    for case in all_query_cases():
        requests = {
            "A": current_request(case["query"], case["language"]),
            "B": candidate_request(case["query"], case["language"]),
        }
        for within, arm in enumerate(arm_order(case["sample_index"]), 1):
            request = requests[arm]
            records.append(
                {
                    "request_ordinal": len(records) + 1,
                    "sample_index": case["sample_index"],
                    "within_case_arm_ordinal": within,
                    "panel": case["panel"],
                    "panel_ordinal": case["panel_ordinal"],
                    "opaque_case_id": case["opaque_case_id"],
                    "language": case["language"],
                    "query": case["query"],
                    "query_sha256": case["query_sha256"],
                    "arm": arm,
                    "request": request,
                    "request_sha256": file_digest(request),
                }
            )
    result = {
        "manifest_format": MANIFEST_FORMAT,
        "created_date": CREATED_DATE,
        "status": "query_only_frozen",
        "gold_fields_present": False,
        "protocol_payload_sha256": protocol["protocol_payload_sha256"],
        "counts": {"queries": 158, "arms": 2, "requests": 316},
        "records": records,
        "manifest_payload_sha256": "",
    }
    result["manifest_payload_sha256"] = payload_sha256(result, "manifest_payload_sha256")
    return result


def file_digest(value: Any) -> str:
    from hashlib import sha256
    from intent_shadow_io import canonical_json_bytes
    return sha256(canonical_json_bytes(value)).hexdigest()


def build_protocol_freeze() -> dict[str, Any]:
    protocol = build_protocol()
    manifest = build_manifest()
    snapshot = load_control_snapshot(CONTROL_SNAPSHOT_PATH)
    repo_authorities = set(CONTROL_REPO_SOURCES) | {
        "internal/design/checkpoint_qualita_intento_12_8_2026.md",
        "internal/design/contratto_ombra_prototipo_intento_12_8_2026.md",
        "internal/design/handover_prompt_ontologia_11_8_2026.md",
        "internal/tools/request_analysis_lab/oracles/phase1_v1/metnos_phase1_typed_oracle_v1.overlay.json",
        "internal/tools/request_analysis_lab/question_focus_controls_v1.json",
        "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_oracle_v0_1.freeze.json",
        "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_oracle_v0_1.json",
        "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_registry_v0_1.freeze.json",
        "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_registry_v0_1.json",
    }
    result = {
        "freeze_format": PROTOCOL_FREEZE_FORMAT,
        "created_date": CREATED_DATE,
        "algorithm": "sha256",
        "status": "prepared_not_authorized_no_inference",
        "protocol_payload_sha256": protocol["protocol_payload_sha256"],
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "control_snapshot_payload_sha256": snapshot["snapshot_payload_sha256"],
        "local_files": {
            name: file_sha256(HERE / name)
            for name in sorted(IMMUTABLE_PROTOCOL_LOCAL_FILES)
        },
        "repo_authorities": {
            relative: file_sha256(ROOT / relative)
            for relative in sorted(repo_authorities)
        },
        "absolute_authorities": {
            absolute: stable_file_sha256(Path(absolute))
            for absolute in sorted(BACKEND_ABSOLUTE_SOURCES)
        },
        "detection_lexicon_behavior_sha256": snapshot["runtime_state"]["behavior_sha256"],
        "lock_payload_sha256": "",
    }
    result["lock_payload_sha256"] = payload_sha256(result, "lock_payload_sha256")
    return result


BUILDERS = {
    "snapshot": (CONTROL_SNAPSHOT_PATH, build_control_snapshot),
    "protocol": (PROTOCOL_PATH, build_protocol),
    "manifest": (REQUEST_MANIFEST_PATH, build_manifest),
    "freeze": (PROTOCOL_FREEZE_PATH, build_protocol_freeze),
}


def check_materialized(include_freeze: bool = True) -> list[str]:
    errors: list[str] = []
    for name, (path, builder) in BUILDERS.items():
        if name == "freeze" and not include_freeze:
            continue
        if not path.is_file() or path.read_bytes() != canonical_write_bytes(builder()):
            errors.append(name)
    return errors


def apply_materialized(name: str) -> None:
    """Write only by invoking the repository apply_patch helper."""
    path, builder = BUILDERS[name]
    data = canonical_write_bytes(builder()).decode("utf-8").rstrip("\n")
    if path.is_file() and path.read_text(encoding="utf-8") == data + "\n":
        return
    if path.exists():
        deletion = f"*** Begin Patch\n*** Delete File: {path}\n*** End Patch\n"
        subprocess.run(["apply_patch"], input=deletion, text=True, check=True)
    additions = "\n".join("+" + line for line in data.split("\n"))
    patch = (
        f"*** Begin Patch\n*** Add File: {path}\n"
        f"{additions}\n*** End Patch\n"
    )
    subprocess.run(["apply_patch"], input=patch, text=True, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", choices=tuple(BUILDERS))
    parser.add_argument("--apply", choices=tuple(BUILDERS))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--without-freeze", action="store_true")
    args = parser.parse_args()
    if args.print:
        print(canonical_write_bytes(BUILDERS[args.print][1]()).decode("utf-8"), end="")
        return 0
    if args.apply:
        apply_materialized(args.apply)
        return 0
    errors = check_materialized(include_freeze=not args.without_freeze)
    print(json.dumps({"status": "ok" if not errors else "error", "error_count": len(errors), "errors": errors}, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
