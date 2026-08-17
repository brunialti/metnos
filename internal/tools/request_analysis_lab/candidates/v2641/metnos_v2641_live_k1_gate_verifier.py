#!/usr/bin/env python3
"""Offline verifier for the one-shot V26.4.1 native K1/34 gate."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import time
from pathlib import Path
from typing import Any


VERSION = "metnos.v26.4.1-live-k1-gate-verifier/1.0"
ARCHIVE = Path("/opt/metnos/internal/tools/request_analysis_lab/candidates/v2641")
RUNNER_PATH = Path("/tmp/metnos_v2641_typed_phase1_runner.py")
FREEZE_PATH = Path("/tmp/metnos_v2641_typed_phase1.freeze.json")
PREFLIGHT_PATH = Path("/tmp/metnos_v2641_transport_preflight_result.json")
GATE_PATH = Path("/tmp/metnos_v2641_typed_phase1_external_gate.lock.json")
OUTPUT_PATH = Path("/tmp/metnos_v2641_typed_phase1_controls_k1.json")
REVIEW_PATH = ARCHIVE / "metnos_v2641_live_k1_authorization_review.json"
ORACLE_REVIEW_PATH = Path("/tmp/metnos_phase1_oracle_audit_v1.md")
VERIFIER_PATH = Path(__file__)
ENDPOINT = "http://127.0.0.1:8080"

FREEZE_SHA256 = "6f98647d6a9ea3b46ec521dca50e9f5bb00352d9b9360047219bd81976cd6eff"
RUNNER_SHA256 = "6f215b04e6dd543b6de5193199957cb653599164f107177f7465fbef7dc95459"
REVIEW_SHA256 = "4b6f9cf6bb4fe0d845ec65648891d5232bdd23ac390e0b5231d0ad638d49af19"
ORACLE_REVIEW_SHA256 = "538f24876768eb0e8612f3bb440c5f38a51a222122d5b34080e5a5c19ef9799a"
PREFLIGHT_SHA256 = "e91716319960b2f29c86667f3e6fd3a0e38c04bd2a0bf22717085ff741ec69c9"
REGISTRY_SHA256 = "448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f"
SCHEMA_SHA256 = "06452e75f0a1ec7a42c9bf87dfae3da4e1528fc07b02d9276272a1e9ff0c7fec"
PROMPT_SHA256 = "2e60f19500b6d6193d53f483707533ca39ec1fee6a0256b18e6a96994f53406f"
FIXTURE_SHA256 = "1dfbc5d2a7a83308a0f414d1b2c0b395f096d034471930fbaa886627aa79d954"
PARENT_FREEZE_SHA256 = "83d3b4441d83d8f90e5eb591f9eeaf052885150e6806cd3ceefd046d948ccde6"
ORACLE_FREEZE_SHA256 = "f2f5d6046a7fb0b6fead8f5ab27c35c230aebd928411d5d5a962bce90209a70f"
MAX_PREFLIGHT_AGE_MS = 900_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("metnos_v2641_live_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_gate() -> dict[str, Any]:
    return {
        "lock_version": "metnos.v26.4.1-external-gate-lock/1.0",
        "status": "authorized_one_native_run",
        "authority": "independent_v2641_infra_oracle_freeze_review",
        "freeze_sha256": FREEZE_SHA256,
        "runner_sha256": RUNNER_SHA256,
        "registry_sha256": REGISTRY_SHA256,
        "schema_sha256": SCHEMA_SHA256,
        "prompt_sha256": PROMPT_SHA256,
        "fixture_sha256": FIXTURE_SHA256,
        "parent_freeze_sha256": PARENT_FREEZE_SHA256,
        "oracle_freeze_sha256": ORACLE_FREEZE_SHA256,
        "authorized_case_count": 34,
        "authorized_repetitions": 1,
        "authorized_output_path": str(OUTPUT_PATH),
        "inference_attempt_limit": 34,
        "inline_transport_preflight_required": True,
        "inline_transport_preflight_method": "GET",
        "inline_transport_preflight_path": "/v1/models",
        "inline_transport_preflight_attempt_limit": 1,
        "inline_transport_preflight_max_age_before_first_post_ms": 5000,
        "external_transport_preflight_required": True,
        "external_transport_preflight_max_age_at_gate_verification_ms": MAX_PREFLIGHT_AGE_MS,
        "transport_preflight_result_path": str(PREFLIGHT_PATH),
        "retries": 0,
        "runtime_cutover_authorized": False,
        "network_calls_before_lock": 1,
        "transport_preflight_calls_before_lock": 1,
        "inference_calls_before_lock": 0,
        "candidate_outputs_read_before_lock": 0,
        "authorized_endpoint": ENDPOINT,
        "independent_review_path": str(REVIEW_PATH),
        "independent_review_sha256": REVIEW_SHA256,
        "oracle_review_path": str(ORACLE_REVIEW_PATH),
        "oracle_review_sha256": ORACLE_REVIEW_SHA256,
        "verifier_path": str(VERIFIER_PATH),
        "verifier_sha256": sha256(VERIFIER_PATH),
        "transport_preflight_result_sha256": PREFLIGHT_SHA256,
    }


def validate_preflight(now_ns: int | None = None) -> list[str]:
    errors: list[str] = []
    try:
        if sha256(PREFLIGHT_PATH) != PREFLIGHT_SHA256:
            return ["preflight_hash"]
        result = json.loads(PREFLIGHT_PATH.read_text())
    except Exception:
        return ["preflight_unreadable"]
    exact = {
        "status": "PASS", "endpoint": ENDPOINT, "method": "GET",
        "request_body_bytes": 0, "inference_calls": 0,
        "authorizes_inference": False, "freeze_sha256": FREEZE_SHA256,
        "runner_sha256": RUNNER_SHA256,
    }
    for key, value in exact.items():
        if result.get(key) != value:
            errors.append("preflight:" + key)
    counters = result.get("counters")
    if not isinstance(counters, dict):
        errors.append("preflight:counters")
    else:
        expected_counters = {
            "socket_attempts": 1, "http_responses": 1,
            "server_accepted_requests": 1, "preflight_json_documents_decoded": 1,
            "response_json_documents_decoded": 0, "decoded_chat_responses": 0,
            "decoded_frames": 0, "validation_attempts": 0,
            "evaluated_cases": 0, "valid_cases": 0, "invalid_cases": 0,
        }
        if counters != expected_counters:
            errors.append("preflight:counters")
    completed = result.get("completed_at_unix_ns")
    clock = time.time_ns() if now_ns is None else now_ns
    age_ms = (clock - completed) / 1_000_000 if isinstance(completed, int) else None
    if age_ms is None or age_ms < 0 or age_ms > MAX_PREFLIGHT_AGE_MS:
        errors.append("preflight:recency")
    return errors


def validate_review() -> list[str]:
    errors: list[str] = []
    try:
        if sha256(REVIEW_PATH) != REVIEW_SHA256:
            return ["review_hash"]
        review = json.loads(REVIEW_PATH.read_text())
    except Exception:
        return ["review_unreadable"]
    if review.get("version") != "metnos.v26.4.1-live-k1-authorization-review/1.0":
        errors.append("review_version")
    if review.get("status") != "AUTHORIZE_ONE_NATIVE_K1_34_AFTER_INLINE_PREFLIGHT":
        errors.append("review_status")
    authorization = review.get("authorization")
    expected_authorization = {
        "authorized_case_count": 34, "authorized_endpoint": ENDPOINT,
        "authorized_output_path": str(OUTPUT_PATH), "authorized_repetitions": 1,
        "candidate_outputs_read_before_lock": 0, "inference_attempt_limit": 34,
        "network_calls_before_lock": 1, "retries": 0,
        "runtime_cutover_authorized": False,
        "transport_preflight_calls_before_lock": 1,
    }
    if authorization != expected_authorization:
        errors.append("review_authorization")
    dependencies = review.get("dependencies")
    if not isinstance(dependencies, dict) or len(dependencies) != 17:
        errors.append("review_dependencies")
    else:
        for raw_path, expected_hash in dependencies.items():
            try:
                path = Path(raw_path)
                if not path.is_file() or sha256(path) != expected_hash:
                    errors.append("dependency:" + raw_path)
            except OSError:
                errors.append("dependency:" + raw_path)
    return errors


def validate_gate(
    gate: Any, *, verify_files: bool = True, output_exists: bool | None = None,
) -> list[str]:
    errors: list[str] = []
    expected = expected_gate()
    if not isinstance(gate, dict):
        return ["gate_not_object"]
    if set(gate) != set(expected):
        errors.append("gate_keys")
    for key, value in expected.items():
        if gate.get(key) != value:
            errors.append("gate:" + key)
    if verify_files:
        for path, expected_hash, label in (
            (FREEZE_PATH, FREEZE_SHA256, "freeze"),
            (RUNNER_PATH, RUNNER_SHA256, "runner"),
            (REVIEW_PATH, REVIEW_SHA256, "review"),
            (ORACLE_REVIEW_PATH, ORACLE_REVIEW_SHA256, "oracle_review"),
            (VERIFIER_PATH, expected["verifier_sha256"], "verifier"),
        ):
            try:
                if not path.is_file() or sha256(path) != expected_hash:
                    errors.append(label)
            except OSError:
                errors.append(label)
        errors.extend(validate_review())
        errors.extend(validate_preflight())
    if output_exists is None:
        output_exists = OUTPUT_PATH.exists()
    if output_exists:
        errors.append("authorized_output_already_exists")
    return errors


def mutation_suite(gate: dict[str, Any], runner: Any) -> list[dict[str, Any]]:
    values = {
        "status": "blocked", "authority": "author", "freeze_sha256": "0" * 64,
        "runner_sha256": "0" * 64, "registry_sha256": "0" * 64,
        "schema_sha256": "0" * 64, "prompt_sha256": "0" * 64,
        "fixture_sha256": "0" * 64, "parent_freeze_sha256": "0" * 64,
        "oracle_freeze_sha256": "0" * 64, "authorized_case_count": 33,
        "authorized_repetitions": 2, "authorized_output_path": "/tmp/other.json",
        "inference_attempt_limit": 35, "inline_transport_preflight_required": False,
        "inline_transport_preflight_method": "POST",
        "inline_transport_preflight_path": "/v1/chat/completions",
        "inline_transport_preflight_attempt_limit": 2,
        "inline_transport_preflight_max_age_before_first_post_ms": 5001,
        "external_transport_preflight_required": False,
        "external_transport_preflight_max_age_at_gate_verification_ms": 900001,
        "transport_preflight_result_path": "/tmp/other-preflight.json",
        "retries": 1, "runtime_cutover_authorized": True,
        "network_calls_before_lock": 0, "transport_preflight_calls_before_lock": 0,
        "inference_calls_before_lock": 1, "candidate_outputs_read_before_lock": 1,
        "authorized_endpoint": "http://127.0.0.1:8770",
        "independent_review_path": "/tmp/review", "independent_review_sha256": "0" * 64,
        "oracle_review_path": "/tmp/oracle", "oracle_review_sha256": "0" * 64,
        "verifier_path": "/tmp/verifier", "verifier_sha256": "0" * 64,
        "transport_preflight_result_sha256": "0" * 64,
    }
    results: list[dict[str, Any]] = []
    for key, value in values.items():
        changed = copy.deepcopy(gate)
        changed[key] = value
        independent = validate_gate(changed, verify_files=False, output_exists=False)
        runner_errors = runner._validate_external_gate_payload(
            changed, endpoint=ENDPOINT, require_output_absent=False,
        )
        results.append({
            "id": key, "pass": bool(independent) and bool(runner_errors),
            "independent_error_count": len(independent),
            "runner_error_count": len(runner_errors),
        })
    missing = copy.deepcopy(gate)
    missing.pop("status")
    extra = copy.deepcopy(gate)
    extra["extra"] = True
    for mutation_id, changed in (("missing_key", missing), ("extra_key", extra)):
        independent = validate_gate(changed, verify_files=False, output_exists=False)
        runner_errors = runner._validate_external_gate_payload(
            changed, endpoint=ENDPOINT, require_output_absent=False,
        )
        results.append({
            "id": mutation_id, "pass": bool(independent) and bool(runner_errors),
            "independent_error_count": len(independent),
            "runner_error_count": len(runner_errors),
        })
    output_errors = validate_gate(gate, verify_files=False, output_exists=True)
    results.append({
        "id": "preexisting_output",
        "pass": "authorized_output_already_exists" in output_errors,
        "independent_error_count": len(output_errors), "runner_error_count": 0,
    })
    try:
        completed = json.loads(PREFLIGHT_PATH.read_text())["completed_at_unix_ns"]
        stale_errors = validate_preflight(completed + (MAX_PREFLIGHT_AGE_MS + 1) * 1_000_000)
    except Exception:
        stale_errors = []
    results.append({
        "id": "stale_preflight", "pass": "preflight:recency" in stale_errors,
        "independent_error_count": len(stale_errors), "runner_error_count": 0,
    })
    return results


def main() -> int:
    errors: list[str] = []
    try:
        gate = json.loads(GATE_PATH.read_text())
    except Exception as exc:
        gate = {}
        errors.append("gate_json:" + type(exc).__name__)
    errors.extend(validate_gate(gate, verify_files=True))
    runner = load_runner()
    runner_errors = runner._validate_external_gate_payload(
        gate, endpoint=ENDPOINT, require_output_absent=True,
    )
    errors.extend("runner:" + item for item in runner_errors)
    mutations = mutation_suite(gate, runner) if not errors else []
    failed = [item["id"] for item in mutations if not item["pass"]]
    errors.extend("mutation:" + item for item in failed)
    result = {
        "version": VERSION, "valid": not errors, "errors": sorted(set(errors)),
        "network_calls": 0, "candidate_outputs_read": 0,
        "gate_path": str(GATE_PATH),
        "gate_sha256": sha256(GATE_PATH) if GATE_PATH.is_file() else None,
        "preflight_sha256": PREFLIGHT_SHA256,
        "authorized_command": (
            "python3 /tmp/metnos_v2641_typed_phase1_runner.py --controls "
            "--endpoint http://127.0.0.1:8080 "
            "--output /tmp/metnos_v2641_typed_phase1_controls_k1.json"
        ),
        "authorization": {
            "cases": 34, "repetitions": 1, "retries": 0,
            "inference_attempt_limit": 34, "runtime_cutover": False,
            "inline_preflight_required": True,
        },
        "mutations": mutations,
        "summary": {
            "mutations": len(mutations),
            "mutation_passed": len(mutations) - len(failed),
            "mutation_failed": len(failed),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
