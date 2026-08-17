#!/usr/bin/env python3
"""Fail-closed verifier for disarmed or explicitly armed live protocol state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from intent_shadow_io import file_sha256, require_exact_keys, strict_json_file
from live_protocol import (
    AUTHORIZATION_PATH,
    BACKEND_ABSOLUTE_SOURCES,
    CONTROL_REPO_SOURCES,
    CONTROL_SNAPSHOT_PATH,
    CREATED_DATE,
    HERE,
    IMMUTABLE_PROTOCOL_LOCAL_FILES,
    PROTOCOL_FREEZE_FORMAT,
    PROTOCOL_FREEZE_PATH,
    PROTOCOL_PATH,
    REQUEST_MANIFEST_PATH,
    ROOT,
    load_control_snapshot,
    load_protocol,
    load_request_manifest,
    payload_sha256,
    stable_file_sha256,
    verify_control_environment,
)


def _error(errors: list[str], code: str, detail: str) -> None:
    errors.append(f"{code}:{detail}")


def _static_query_only(errors: list[str], manifest: dict[str, Any]) -> None:
    forbidden_by_file = {
        "live_runner.py": (
            "intent_shadow_oracle_v0_1.json",
            "metnos_phase1_typed_oracle_v1.overlay.json",
            "question_focus_controls_v1.json",
        ),
        "live_arm_current.py": ("intent_shadow_oracle_v0_1.json",),
        "live_arm_candidate.py": ("intent_shadow_oracle_v0_1.json",),
        "live_protocol.py": ("intent_shadow_oracle_v0_1.json",),
    }
    for relative, forbidden in forbidden_by_file.items():
        source = (HERE / relative).read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                _error(errors, "GOLD_CONTAMINATION", f"{relative}:{token}")
    sources = "\n".join(
        (HERE / relative).read_text(encoding="utf-8")
        for relative in (
            "live_arm_current.py", "live_arm_candidate.py", "live_protocol.py", "live_runner.py"
        )
    ).casefold()
    for record in manifest["records"][::2]:
        query = record["query"].casefold().strip()
        if query and query in sources:
            _error(errors, "QUERY_HARDCODING", record["opaque_case_id"])
            break


def verify_live_protocol(
    freeze_path: Path = PROTOCOL_FREEZE_PATH,
    *,
    check_runtime_environment: bool = True,
    authorization_state: str = "disarmed",
    authorization_path: Path = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        if authorization_state not in {"disarmed", "armed"}:
            raise RuntimeError("authorization state must be disarmed or armed")
        snapshot = load_control_snapshot(CONTROL_SNAPSHOT_PATH)
        protocol = load_protocol(PROTOCOL_PATH)
        manifest = load_request_manifest(REQUEST_MANIFEST_PATH)
        if check_runtime_environment:
            verify_control_environment(snapshot)
        expected_bindings = {
            "control_snapshot_file_sha256": file_sha256(CONTROL_SNAPSHOT_PATH),
            "registry_file_sha256": file_sha256(HERE.parent / "intent_shadow_registry_v0_1.json"),
            "query_suite_file_sha256": file_sha256(HERE / "intent_shadow_query_suite_v0_1.json"),
            "legacy_panel_file_sha256": file_sha256(HERE / "legacy_panel_v0_1.json"),
            "candidate_prompt_sha256": file_sha256(HERE / "intent_shadow_model_v0_1.prompt.txt"),
            "candidate_schema_sha256": file_sha256(HERE / "intent_shadow_model_v0_1.schema.json"),
        }
        if protocol["bindings"] != expected_bindings:
            _error(errors, "PROTOCOL_BINDING", "files")
        if manifest["protocol_payload_sha256"] != protocol["protocol_payload_sha256"]:
            _error(errors, "MANIFEST_BINDING", "protocol")
        _static_query_only(errors, manifest)

        freeze = strict_json_file(freeze_path)
        expected_root = {
            "freeze_format", "created_date", "algorithm", "status",
            "protocol_payload_sha256", "manifest_payload_sha256",
            "control_snapshot_payload_sha256", "local_files", "repo_authorities",
            "absolute_authorities", "detection_lexicon_behavior_sha256",
            "lock_payload_sha256",
        }
        require_exact_keys(freeze, expected_root, set(), "$")
        constants = {
            "freeze_format": PROTOCOL_FREEZE_FORMAT,
            "created_date": CREATED_DATE,
            "algorithm": "sha256",
            "status": "prepared_not_authorized_no_inference",
            "protocol_payload_sha256": protocol["protocol_payload_sha256"],
            "manifest_payload_sha256": manifest["manifest_payload_sha256"],
            "control_snapshot_payload_sha256": snapshot["snapshot_payload_sha256"],
            "detection_lexicon_behavior_sha256": snapshot["runtime_state"]["behavior_sha256"],
        }
        for key, expected in constants.items():
            if type(freeze.get(key)) is not type(expected) or freeze.get(key) != expected:
                _error(errors, "FREEZE_CONSTANT", key)
        if type(freeze["local_files"]) is not dict or set(freeze["local_files"]) != set(IMMUTABLE_PROTOCOL_LOCAL_FILES):
            _error(errors, "FREEZE_LOCAL_SET", "membership")
        else:
            for relative in sorted(IMMUTABLE_PROTOCOL_LOCAL_FILES):
                if freeze["local_files"][relative] != stable_file_sha256(HERE / relative):
                    _error(errors, "FREEZE_LOCAL_HASH", relative)
        expected_repo = set(CONTROL_REPO_SOURCES) | {
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
        if type(freeze["repo_authorities"]) is not dict or set(freeze["repo_authorities"]) != expected_repo:
            _error(errors, "FREEZE_REPO_SET", "membership")
        else:
            for relative in sorted(expected_repo):
                if freeze["repo_authorities"][relative] != stable_file_sha256(ROOT / relative):
                    _error(errors, "FREEZE_REPO_HASH", relative)
        if type(freeze["absolute_authorities"]) is not dict or set(freeze["absolute_authorities"]) != set(BACKEND_ABSOLUTE_SOURCES):
            _error(errors, "FREEZE_ABSOLUTE_SET", "membership")
        else:
            for absolute in sorted(BACKEND_ABSOLUTE_SOURCES):
                if freeze["absolute_authorities"][absolute] != stable_file_sha256(Path(absolute)):
                    _error(errors, "FREEZE_ABSOLUTE_HASH", absolute)
        if freeze["lock_payload_sha256"] != payload_sha256(freeze, "lock_payload_sha256"):
            _error(errors, "FREEZE_LOCK", "payload")
        authorization_candidates = sorted(HERE.glob("live_run_authorization*.json"))
        authorization_sha256 = None
        if authorization_state == "disarmed":
            if authorization_candidates:
                _error(
                    errors,
                    "UNEXPECTED_AUTHORIZATION",
                    ",".join(path.name for path in authorization_candidates),
                )
        else:
            expected_authorization = authorization_path.resolve()
            if not authorization_path.is_file():
                _error(errors, "MISSING_AUTHORIZATION", str(authorization_path))
            else:
                from live_runner import validate_authorization

                validate_authorization(authorization_path)
                authorization_sha256 = file_sha256(authorization_path)
            extras = [
                path.name
                for path in authorization_candidates
                if path.resolve() != expected_authorization
            ]
            if extras:
                _error(errors, "EXTRA_AUTHORIZATION", ",".join(extras))
        forbidden_run_artifacts = sorted(
            path.name for path in HERE.glob("live_run_*")
            if path.name not in {"live_runner.py", AUTHORIZATION_PATH.name}
        )
        evaluation_path = HERE / "live_evaluation_v0_1.json"
        if evaluation_path.exists():
            forbidden_run_artifacts.append(evaluation_path.name)
        if forbidden_run_artifacts:
            _error(errors, "RUN_ALREADY_TOUCHED", ",".join(forbidden_run_artifacts))
    except Exception as exc:
        _error(errors, "VERIFY_EXCEPTION", f"{type(exc).__name__}:{exc}")
    return {
        "status": "ok" if not errors else "error",
        "error_count": len(errors),
        "errors": errors,
        "counts": {"queries": 158, "requests": 316, "canonical": 120, "typed_controls": 4, "legacy": 34},
        "inference_executed": False,
        "network_touched": False,
        "authorization_state": authorization_state,
        "authorization_sha256": authorization_sha256 if "authorization_sha256" in locals() else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", type=Path, default=PROTOCOL_FREEZE_PATH)
    parser.add_argument("--skip-runtime-environment", action="store_true")
    parser.add_argument("--authorization-state", choices=("disarmed", "armed"), default="disarmed")
    parser.add_argument("--authorization", type=Path, default=AUTHORIZATION_PATH)
    args = parser.parse_args()
    report = verify_live_protocol(
        args.freeze,
        check_runtime_environment=not args.skip_runtime_environment,
        authorization_state=args.authorization_state,
        authorization_path=args.authorization,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
