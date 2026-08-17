#!/usr/bin/env python3
"""Offline verifier for the one-shot V26.4.1 transport preflight gate.

This program performs no network operation and never reads model candidate
outputs. It validates the exact gate, its transitive review/oracle chain, and
the frozen runner's own preflight-gate contract.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


VERSION = "metnos.v26.4.1-transport-preflight-gate-verifier/1.0"
ROOT = Path("/opt/metnos/internal/tools/request_analysis_lab/candidates")
ARCHIVE = ROOT / "v2641"
GATE_PATH = Path("/tmp/metnos_v2641_transport_preflight.lock.json")
OUTPUT_PATH = Path("/tmp/metnos_v2641_transport_preflight_result.json")
LIVE_GATE_PATH = Path("/tmp/metnos_v2641_typed_phase1_external_gate.lock.json")
RUNTIME_RUNNER_PATH = Path("/tmp/metnos_v2641_typed_phase1_runner.py")
RUNTIME_FREEZE_PATH = Path("/tmp/metnos_v2641_typed_phase1.freeze.json")
REVIEW_PATH = ARCHIVE / "metnos_v2641_transport_preflight_authorization_review.json"
VERIFIER_PATH = Path(__file__)

ENDPOINT = "http://127.0.0.1:8080"
AUTHORIZED_URL = ENDPOINT + "/v1/models"
RUNNER_SHA256 = "6f215b04e6dd543b6de5193199957cb653599164f107177f7465fbef7dc95459"
FREEZE_SHA256 = "6f98647d6a9ea3b46ec521dca50e9f5bb00352d9b9360047219bd81976cd6eff"
REVIEW_SHA256 = "0709b7997577ad2b61c18eb87f114a0ff3fa679ecb07a314af2bc9d378f003ac"

EXPECTED_DEPENDENCIES = {
    str(ROOT / "v264/metnos_v264_external_gate_authorization_review.md"):
        "135f153c02cadac69d9d0303622599c55e677832e2bcd2ac23acd5fdf50eea84",
    str(ROOT / "v264/metnos_v264_graph_invariant_review_preoutput.md"):
        "001f834d67f0937173123c0fab7398957d6aefd0ea3f35d62f6219382dd21b41",
    str(ROOT / "v264/metnos_v264_independent_graph_final_review.addendum.md"):
        "53254dca278c2d2da7f4ac2958502866b3758dd332f672643950c87ccffe1c4d",
    str(ROOT / "v264/metnos_v264_independent_graph_final_review.json"):
        "ccbc518837af72a60e67da5e2ae445bffd17f64ff1dfba727d54fadcd2c8ac62",
    str(ROOT / "v264/metnos_v264_independent_graph_final_review.md"):
        "9e77d7261a144615d2329febefcfbab1d2b05c174e2c43819d149bf8d6dca35a",
    str(ROOT / "v264/metnos_v264_typed_phase1.freeze.json"):
        "83d3b4441d83d8f90e5eb591f9eeaf052885150e6806cd3ceefd046d948ccde6",
    str(ARCHIVE / "metnos_v2641_independent_infra_review.md"):
        "598dac69fe53e5b1dd1884b400a6d48de35f586db1b4a6394b49a9392c195bfa",
    str(ARCHIVE / "metnos_v2641_typed_phase1.freeze.json"): FREEZE_SHA256,
    str(ARCHIVE / "metnos_v2641_typed_phase1_runner.py"): RUNNER_SHA256,
    "/tmp/metnos_phase1_oracle_audit_v1.freeze.json":
        "f2f5d6046a7fb0b6fead8f5ab27c35c230aebd928411d5d5a962bce90209a70f",
    "/tmp/metnos_phase1_oracle_audit_v1.json":
        "b8443ad2e27a2d773b971147c1b3d37ee19beb1091b1d357c0a84e6aa0c98a77",
    "/tmp/metnos_phase1_oracle_audit_v1.md":
        "538f24876768eb0e8612f3bb440c5f38a51a222122d5b34080e5a5c19ef9799a",
    "/tmp/metnos_phase1_typed_oracle_v1.overlay.json":
        "e62d0605622e5f2c71e329e63448d29ad27fbfd3dafc64b880d4240cda9846af",
    "/tmp/metnos_phase1_typed_oracle_v1.schema.json":
        "4929fbc4ac524491f452d11d0f2d4d032887dd423c7487233c3d89065f4e17f2",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("metnos_v2641_preflight_runner", RUNTIME_RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen V26.4.1 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_gate() -> dict[str, Any]:
    return {
        "lock_version": "metnos.v26.4.1-transport-preflight-lock/1.0",
        "status": "authorized_one_inference_free_transport_preflight",
        "authority": "independent_v2641_transport_preflight_review",
        "freeze_sha256": FREEZE_SHA256,
        "runner_sha256": RUNNER_SHA256,
        "authorized_method": "GET",
        "request_body_allowed": False,
        "authorized_attempts": 1,
        "inference_calls": 0,
        "authorized_output_path": str(OUTPUT_PATH),
        "network_calls_before_lock": 0,
        "authorized_endpoint": ENDPOINT,
        "authorized_url": AUTHORIZED_URL,
        "independent_review_path": str(REVIEW_PATH),
        "independent_review_sha256": REVIEW_SHA256,
        "verifier_path": str(VERIFIER_PATH),
        "verifier_sha256": sha256(VERIFIER_PATH),
    }


def validate_review(review: Any, *, verify_files: bool = True) -> list[str]:
    errors: list[str] = []
    if not isinstance(review, dict):
        return ["review_not_object"]
    expected_keys = {
        "authorization", "dependencies", "independent_review_sha256",
        "network_calls_during_review", "status", "version",
    }
    if set(review) != expected_keys:
        errors.append("review_keys")
    if review.get("version") != "metnos.v26.4.1-transport-preflight-authorization-review/1.0":
        errors.append("review_version")
    if review.get("status") != "AUTHORIZE_ONE_INFERENCE_FREE_TRANSPORT_PREFLIGHT":
        errors.append("review_status")
    if review.get("network_calls_during_review") != 0:
        errors.append("review_network_calls")
    if review.get("independent_review_sha256") != EXPECTED_DEPENDENCIES[
        str(ARCHIVE / "metnos_v2641_independent_infra_review.md")
    ]:
        errors.append("review_independent_hash")
    expected_authorization = {
        "attempts": 1,
        "authorizes_inference": False,
        "authorizes_live_gate": False,
        "authorizes_runtime_cutover": False,
        "method": "GET",
        "network_calls_before_lock": 0,
        "output_path": str(OUTPUT_PATH),
        "request_body_allowed": False,
        "url": AUTHORIZED_URL,
    }
    if review.get("authorization") != expected_authorization:
        errors.append("review_authorization")
    if review.get("dependencies") != EXPECTED_DEPENDENCIES:
        errors.append("review_dependencies")
    if verify_files:
        for raw_path, expected_hash in EXPECTED_DEPENDENCIES.items():
            path = Path(raw_path)
            try:
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
            (RUNTIME_FREEZE_PATH, FREEZE_SHA256, "runtime_freeze"),
            (RUNTIME_RUNNER_PATH, RUNNER_SHA256, "runtime_runner"),
            (REVIEW_PATH, REVIEW_SHA256, "review"),
            (VERIFIER_PATH, expected["verifier_sha256"], "verifier"),
        ):
            try:
                if not path.is_file() or sha256(path) != expected_hash:
                    errors.append(label)
            except OSError:
                errors.append(label)
        try:
            review = json.loads(REVIEW_PATH.read_text())
        except Exception:
            errors.append("review_json")
        else:
            errors.extend(validate_review(review, verify_files=True))
    if output_exists is None:
        output_exists = OUTPUT_PATH.exists()
    if output_exists:
        errors.append("authorized_output_already_exists")
    if LIVE_GATE_PATH.exists():
        errors.append("live_gate_must_not_preexist")
    return errors


def mutation_suite(gate: dict[str, Any], runner: Any) -> list[dict[str, Any]]:
    mutations: list[tuple[str, str, Any]] = [
        ("status", "status", "authorized_one_native_run"),
        ("authority", "authority", "author_pre_gate"),
        ("freeze", "freeze_sha256", "0" * 64),
        ("runner", "runner_sha256", "0" * 64),
        ("method", "authorized_method", "POST"),
        ("body", "request_body_allowed", True),
        ("attempts", "authorized_attempts", 2),
        ("inference", "inference_calls", 1),
        ("output", "authorized_output_path", "/tmp/other.json"),
        ("network_before_lock", "network_calls_before_lock", 1),
        ("endpoint", "authorized_endpoint", "http://127.0.0.1:8770"),
        ("url", "authorized_url", ENDPOINT + "/v1/chat/completions"),
        ("review_path", "independent_review_path", "/tmp/author-review.json"),
        ("review_hash", "independent_review_sha256", "0" * 64),
        ("verifier_path", "verifier_path", "/tmp/other-verifier.py"),
        ("verifier_hash", "verifier_sha256", "0" * 64),
    ]
    results: list[dict[str, Any]] = []
    for mutation_id, key, value in mutations:
        changed = copy.deepcopy(gate)
        changed[key] = value
        independent = validate_gate(changed, verify_files=False, output_exists=False)
        runner_errors = runner._validate_preflight_gate_payload(
            changed, endpoint=ENDPOINT, require_output_absent=False,
        )
        results.append({
            "id": mutation_id,
            "pass": bool(independent) and bool(runner_errors),
            "independent_error_count": len(independent),
            "runner_error_count": len(runner_errors),
        })
    missing = copy.deepcopy(gate)
    missing.pop("status")
    extra = copy.deepcopy(gate)
    extra["extra"] = True
    for mutation_id, changed in (("missing_key", missing), ("extra_key", extra)):
        independent = validate_gate(changed, verify_files=False, output_exists=False)
        runner_errors = runner._validate_preflight_gate_payload(
            changed, endpoint=ENDPOINT, require_output_absent=False,
        )
        results.append({
            "id": mutation_id,
            "pass": bool(independent) and bool(runner_errors),
            "independent_error_count": len(independent),
            "runner_error_count": len(runner_errors),
        })
    output_errors = validate_gate(gate, verify_files=False, output_exists=True)
    results.append({
        "id": "preexisting_output",
        "pass": "authorized_output_already_exists" in output_errors,
        "independent_error_count": len(output_errors),
        "runner_error_count": 0,
    })
    review = json.loads(REVIEW_PATH.read_text())
    for mutation_id, dependency in (
        ("oracle_dependency", "/tmp/metnos_phase1_oracle_audit_v1.freeze.json"),
        ("v264_review_dependency", str(ROOT / "v264/metnos_v264_independent_graph_final_review.md")),
        ("v2641_review_dependency", str(ARCHIVE / "metnos_v2641_independent_infra_review.md")),
    ):
        changed_review = copy.deepcopy(review)
        changed_review["dependencies"][dependency] = "0" * 64
        review_errors = validate_review(changed_review, verify_files=False)
        results.append({
            "id": mutation_id,
            "pass": bool(review_errors),
            "independent_error_count": len(review_errors),
            "runner_error_count": 0,
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
    runner_errors = runner._validate_preflight_gate_payload(
        gate, endpoint=ENDPOINT, require_output_absent=True,
    )
    errors.extend("runner:" + item for item in runner_errors)
    mutations = mutation_suite(gate, runner) if not errors else []
    failed = [item["id"] for item in mutations if not item["pass"]]
    if failed:
        errors.extend("mutation:" + item for item in failed)
    result = {
        "version": VERSION,
        "valid": not errors,
        "errors": sorted(set(errors)),
        "network_calls": 0,
        "candidate_outputs_read": 0,
        "gate_path": str(GATE_PATH),
        "gate_sha256": sha256(GATE_PATH) if GATE_PATH.is_file() else None,
        "authorized_command": (
            "python3 /tmp/metnos_v2641_typed_phase1_runner.py --preflight "
            "--endpoint http://127.0.0.1:8080 "
            "--output /tmp/metnos_v2641_transport_preflight_result.json"
        ),
        "authorization": {
            "method": "GET",
            "url": AUTHORIZED_URL,
            "attempts": 1,
            "request_body_allowed": False,
            "inference_calls": 0,
            "live_gate_authorized": False,
        },
        "mutations": mutations,
        "summary": {
            "dependencies": len(EXPECTED_DEPENDENCIES),
            "mutations": len(mutations),
            "mutation_passed": len(mutations) - len(failed),
            "mutation_failed": len(failed),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
