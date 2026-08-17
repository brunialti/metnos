#!/usr/bin/env python3
"""Deterministic verifier for the offline candidate and its freeze."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from build_query_suite import check_artifacts
from intent_shadow_io import file_sha256, strict_json_file
from intent_shadow_projection import build_prompt, build_schema, materialize_schema
from intent_shadow_registry import load_frozen_registry
from intent_shadow_runner import load_legacy_panel, load_query_suite, run_offline
from verify_live_protocol import verify_live_protocol
from live_protocol import AUTHORIZATION_PATH


CANDIDATE_VERSION = "metnos.intent-shadow-candidate/0.1"
FREEZE_FORMAT = "metnos.intent-shadow-candidate-freeze/0.1"
HERE = Path(__file__).resolve().parent
FREEZE_PATH = HERE / "candidate_v0_1.freeze.json"
SCHEMA_PATH = HERE / "intent_shadow_model_v0_1.schema.json"
PROMPT_PATH = HERE / "intent_shadow_model_v0_1.prompt.txt"
ROOT = HERE.parents[5]


FROZEN_LOCAL_FILES = {
    "README.md",
    "__init__.py",
    "build_live_protocol.py",
    "build_query_suite.py",
    "control_current_snapshot_v0_1.json",
    "intent_shadow_evaluate.py",
    "intent_shadow_extract.py",
    "intent_shadow_io.py",
    "intent_shadow_model_v0_1.prompt.txt",
    "intent_shadow_model_v0_1.schema.json",
    "intent_shadow_normalize.py",
    "intent_shadow_projection.py",
    "intent_shadow_query_suite_v0_1.json",
    "intent_shadow_registry.py",
    "intent_shadow_runner.py",
    "intent_shadow_types.py",
    "intent_shadow_validate.py",
    "legacy_panel_v0_1.json",
    "live_arm_candidate.py",
    "live_arm_current.py",
    "live_evaluator.py",
    "live_measurement_protocol_v0_1.json",
    "live_protocol.py",
    "live_protocol_v0_1.freeze.json",
    "live_request_manifest_v0_1.json",
    "live_runner.py",
    "self_review.md",
    "test_intent_shadow.py",
    "test_intent_shadow_mutations.py",
    "test_live_protocol.py",
    "test_live_protocol_mutations.py",
    "verify_candidate.py",
    "verify_live_protocol.py",
}

AUTHORITY_PATHS = {
    "internal/design/contratto_ombra_prototipo_intento_12_8_2026.md",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_oracle_v0_1.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_oracle_v0_1.freeze.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_registry_v0_1.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_registry_v0_1.freeze.json",
    "internal/tools/request_analysis_lab/misure_11_8/prova_specchi_riparo_c11.json",
    "internal/tools/request_analysis_lab/question_focus_controls_v1.json",
    "internal/tools/request_analysis_lab/oracles/phase1_v1/metnos_phase1_typed_oracle_v1.overlay.json",
    "internal/tools/request_analysis_lab/oracles/phase1_v1/metnos_phase1_typed_oracle_v1.schema.json",
    "internal/tools/request_analysis_lab/oracles/phase1_v1/metnos_phase1_oracle_audit_v1.freeze.json",
}


def _errors(report: list[str], code: str, message: str) -> None:
    report.append(f"{code}: {message}")


def _contamination_errors(prompt: str, schema_text: str) -> list[str]:
    errors: list[str] = []
    suite = load_query_suite()
    legacy = load_legacy_panel()
    queries = [
        case["query"]
        for panel in suite["panels"].values()
        for case in panel
    ] + [case["query"] for case in legacy["cases"]]
    haystacks = {"prompt": prompt.casefold(), "schema": schema_text.casefold()}
    for query in queries:
        folded = query.casefold().strip()
        if folded and any(folded in value for value in haystacks.values()):
            errors.append("full_query_overlap")
            break
        tokens = folded.split()
        for width in range(4, len(tokens) + 1):
            for start in range(len(tokens) - width + 1):
                gram = " ".join(tokens[start : start + width])
                if any(gram in value for value in haystacks.values()):
                    errors.append("four_token_overlap")
                    return errors
    return errors


def verify_candidate(freeze_path: Path = FREEZE_PATH) -> dict[str, Any]:
    errors: list[str] = []
    try:
        registry, identity = load_frozen_registry(HERE.parent / "intent_shadow_registry_v0_1.json")
        schema = build_schema(registry)
        prompt = build_prompt(registry)
        if SCHEMA_PATH.read_bytes() != materialize_schema(schema):
            _errors(errors, "SCHEMA_DRIFT", "materialized schema differs")
        if PROMPT_PATH.read_text(encoding="utf-8") != prompt:
            _errors(errors, "PROMPT_DRIFT", "materialized prompt differs")
        for item in _contamination_errors(prompt, SCHEMA_PATH.read_text(encoding="utf-8")):
            _errors(errors, "CONTAMINATION", item)
        suite_report = check_artifacts()
        errors.extend(suite_report["errors"])
        dry = run_offline(include_legacy=True)
        if len(dry["records"]) != 158 or dry["gpu_mode_present"] is not False:
            _errors(errors, "DRY_RUN", "unexpected dry-run shape")
        live_report = verify_live_protocol(
            check_runtime_environment=True,
            authorization_state="armed" if AUTHORIZATION_PATH.is_file() else "disarmed",
            authorization_path=AUTHORIZATION_PATH,
        )
        if live_report["error_count"] != 0:
            _errors(errors, "LIVE_PROTOCOL", repr(live_report["errors"]))

        freeze = strict_json_file(freeze_path)
        actual_local_files = {
            path.name
            for path in HERE.iterdir()
            if path.is_file()
            and path.name not in {FREEZE_PATH.name, AUTHORIZATION_PATH.name}
        }
        if actual_local_files != FROZEN_LOCAL_FILES:
            _errors(errors, "LOCAL_FILE_SET", "unexpected or missing candidate file")
        if type(freeze) is not dict or set(freeze) != {
            "freeze_format", "candidate_version", "created_date", "algorithm",
            "local_files", "authorities", "contract_version",
            "registry_payload_sha256", "query_suite_payload_sha256",
            "legacy_panel_payload_sha256", "schema_sha256", "prompt_sha256",
            "gpu_mode_present", "gpu_inference_executed",
        }:
            _errors(errors, "FREEZE_SCHEMA", "root mismatch")
        else:
            if freeze["freeze_format"] != FREEZE_FORMAT or freeze["candidate_version"] != CANDIDATE_VERSION:
                _errors(errors, "FREEZE_IDENTITY", "version mismatch")
            if (
                freeze["created_date"] != "2026-08-12"
                or freeze["algorithm"] != "sha256"
                or freeze["gpu_mode_present"] is not True
                or freeze["gpu_inference_executed"] is not False
            ):
                _errors(errors, "FREEZE_POLICY", "algorithm/GPU mismatch")
            if type(freeze["local_files"]) is not dict or set(freeze["local_files"]) != FROZEN_LOCAL_FILES:
                _errors(errors, "FREEZE_LOCAL_SET", "local file set mismatch")
            else:
                for relative in sorted(FROZEN_LOCAL_FILES):
                    if freeze["local_files"].get(relative) != file_sha256(HERE / relative):
                        _errors(errors, "FREEZE_LOCAL_HASH", relative)
            if type(freeze["authorities"]) is not dict or set(freeze["authorities"]) != AUTHORITY_PATHS:
                _errors(errors, "FREEZE_AUTHORITY_SET", "authority set mismatch")
            else:
                for relative in sorted(AUTHORITY_PATHS):
                    if freeze["authorities"].get(relative) != file_sha256(ROOT / relative):
                        _errors(errors, "FREEZE_AUTHORITY_HASH", relative)
            suite = load_query_suite()
            legacy = load_legacy_panel()
            expected_scalars = {
                "contract_version": registry["contract_version"],
                "registry_payload_sha256": identity.payload_sha256,
                "query_suite_payload_sha256": suite["suite_payload_sha256"],
                "legacy_panel_payload_sha256": legacy["panel_payload_sha256"],
                "schema_sha256": sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
                "prompt_sha256": sha256(PROMPT_PATH.read_bytes()).hexdigest(),
            }
            for key, value in expected_scalars.items():
                if type(freeze.get(key)) is not str or freeze.get(key) != value:
                    _errors(errors, "FREEZE_BINDING", key)
    except Exception as exc:
        _errors(errors, "VERIFY_EXCEPTION", repr(exc))
    return {
        "status": "ok" if not errors else "error",
        "error_count": len(errors),
        "errors": errors,
        "counts": {"canonical": 120, "typed_controls": 4, "legacy": 34},
        "gpu_mode_present": True,
        "gpu_inference_executed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", type=Path, default=FREEZE_PATH)
    args = parser.parse_args()
    report = verify_candidate(args.freeze)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
