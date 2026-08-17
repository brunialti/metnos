#!/usr/bin/env python3
"""Independent fail-closed verifier for one V26.4 K1/34 native run.

The verifier performs no network/model call.  It verifies the frozen candidate,
oracle, contamination audit, independent review, adversarial probe and exact
one-shot output contract.  It also mutation-tests the gate payload.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable


LOCK_PATH = Path("/tmp/metnos_v264_typed_phase1_external_gate.lock.json")
OUTPUT_PATH = Path("/tmp/metnos_v264_typed_phase1_controls_k1.json")
RUNNER_PATH = Path("/tmp/metnos_v264_typed_phase1_runner.py")
AUTHORIZATION_REVIEW_PATH = Path("/tmp/metnos_v264_external_gate_authorization_review.md")
SELF_PATH = Path(__file__)

EXPECTED_ARTIFACTS = {
    "/tmp/metnos_v264_typed_phase1_runner.py": "ea3fc46b50ce25a72652214b380e251f092ada87420a3e056b67eda60b24057d",
    "/tmp/metnos_v264_typed_registry.json": "448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f",
    "/tmp/metnos_v264_typed_phase1.schema.json": "06452e75f0a1ec7a42c9bf87dfae3da4e1528fc07b02d9276272a1e9ff0c7fec",
    "/tmp/metnos_v264_typed_phase1.prompt.txt": "2e60f19500b6d6193d53f483707533ca39ec1fee6a0256b18e6a96994f53406f",
    "/tmp/metnos_v264_typed_phase1_mutations_frozen.json": "ff2c02177cab32eb8d84e6d0f714442f3161a9371f85189b6ae55589bbf34922",
    "/tmp/metnos_v264_typed_phase1_contamination_audit.json": "6845ef5ab1a3b7f7871909a285b070c292cfe97b2cf923471ebfeee99af78ff9",
    "/tmp/metnos_v264_typed_phase1.freeze.json": "83d3b4441d83d8f90e5eb591f9eeaf052885150e6806cd3ceefd046d948ccde6",
    "/tmp/metnos_phase1_oracle_audit_v1.md": "538f24876768eb0e8612f3bb440c5f38a51a222122d5b34080e5a5c19ef9799a",
    "/tmp/metnos_phase1_oracle_audit_v1.json": "b8443ad2e27a2d773b971147c1b3d37ee19beb1091b1d357c0a84e6aa0c98a77",
    "/tmp/metnos_phase1_typed_oracle_v1.schema.json": "4929fbc4ac524491f452d11d0f2d4d032887dd423c7487233c3d89065f4e17f2",
    "/tmp/metnos_phase1_typed_oracle_v1.overlay.json": "e62d0605622e5f2c71e329e63448d29ad27fbfd3dafc64b880d4240cda9846af",
    "/tmp/metnos_phase1_oracle_audit_v1.freeze.json": "f2f5d6046a7fb0b6fead8f5ab27c35c230aebd928411d5d5a962bce90209a70f",
    "/tmp/metnos_v264_independent_graph_final_review.md": "9e77d7261a144615d2329febefcfbab1d2b05c174e2c43819d149bf8d6dca35a",
    "/tmp/metnos_v264_independent_graph_final_review.json": "ccbc518837af72a60e67da5e2ae445bffd17f64ff1dfba727d54fadcd2c8ac62",
    "/tmp/metnos_v264_independent_graph_final_review.addendum.md": "53254dca278c2d2da7f4ac2958502866b3758dd332f672643950c87ccffe1c4d",
    "/opt/metnos/internal/tools/request_analysis_lab/candidates/v264/metnos_v264_independent_graph_final_review.addendum.md": "53254dca278c2d2da7f4ac2958502866b3758dd332f672643950c87ccffe1c4d",
    "/tmp/metnos_v264_independent_graph_probe.py": "9116a851241c5d1c03385a81a2eff23bb830599ea57e46abb2f6ddd1fd15e29a",
    "/tmp/metnos_v264_independent_graph_probe_result.json": "7b0ff821783d9f5d071b4da0ccaa7f562f57afd47136cfc73c863a12663fb62c",
}

REQUIRED_KEYS = {
    "lock_version",
    "status",
    "authority",
    "authorized_case_count",
    "authorized_repetitions",
    "authorized_output_path",
    "freeze_sha256",
    "registry_sha256",
    "contamination_audit_sha256",
    "oracle_freeze_sha256",
    "oracle_report_path",
    "oracle_report_sha256",
    "oracle_audit_path",
    "oracle_audit_sha256",
    "oracle_schema_path",
    "oracle_schema_sha256",
    "oracle_overlay_path",
    "oracle_overlay_sha256",
    "independent_review_path",
    "independent_review_sha256",
    "independent_review_json_path",
    "independent_review_json_sha256",
    "independent_review_addendum_path",
    "independent_review_addendum_sha256",
    "independent_review_addendum_archive_path",
    "independent_review_addendum_archive_sha256",
    "oracle_review_path",
    "oracle_review_sha256",
    "probe_path",
    "probe_sha256",
    "probe_result_path",
    "probe_result_sha256",
    "authorization_review_path",
    "authorization_review_sha256",
    "verifier_path",
    "verifier_sha256",
    "artifact_hashes",
    "one_shot",
    "network_calls_before_lock",
    "candidate_outputs_read_before_lock",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("metnos_v264_gate_target", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def validate_payload(lock: dict[str, Any], *, require_output_absent: bool = True) -> list[str]:
    errors: list[str] = []
    if set(lock) != REQUIRED_KEYS:
        errors.append("lock_keys")

    expected_scalars = {
        "lock_version": "metnos.v26.4-external-gate-lock/1.0",
        "status": "authorized_one_native_run",
        "authority": "independent_graph_oracle_and_freeze_review",
        "authorized_case_count": 34,
        "authorized_repetitions": 1,
        "authorized_output_path": str(OUTPUT_PATH),
        "freeze_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_v264_typed_phase1.freeze.json"],
        "registry_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_v264_typed_registry.json"],
        "contamination_audit_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_v264_typed_phase1_contamination_audit.json"],
        "oracle_freeze_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_phase1_oracle_audit_v1.freeze.json"],
        "oracle_report_path": "/tmp/metnos_phase1_oracle_audit_v1.md",
        "oracle_report_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_phase1_oracle_audit_v1.md"],
        "oracle_audit_path": "/tmp/metnos_phase1_oracle_audit_v1.json",
        "oracle_audit_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_phase1_oracle_audit_v1.json"],
        "oracle_schema_path": "/tmp/metnos_phase1_typed_oracle_v1.schema.json",
        "oracle_schema_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_phase1_typed_oracle_v1.schema.json"],
        "oracle_overlay_path": "/tmp/metnos_phase1_typed_oracle_v1.overlay.json",
        "oracle_overlay_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_phase1_typed_oracle_v1.overlay.json"],
        "independent_review_path": "/tmp/metnos_v264_independent_graph_final_review.md",
        "independent_review_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_v264_independent_graph_final_review.md"],
        "independent_review_json_path": "/tmp/metnos_v264_independent_graph_final_review.json",
        "independent_review_json_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_v264_independent_graph_final_review.json"],
        "independent_review_addendum_path": "/tmp/metnos_v264_independent_graph_final_review.addendum.md",
        "independent_review_addendum_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_v264_independent_graph_final_review.addendum.md"],
        "independent_review_addendum_archive_path": "/opt/metnos/internal/tools/request_analysis_lab/candidates/v264/metnos_v264_independent_graph_final_review.addendum.md",
        "independent_review_addendum_archive_sha256": EXPECTED_ARTIFACTS["/opt/metnos/internal/tools/request_analysis_lab/candidates/v264/metnos_v264_independent_graph_final_review.addendum.md"],
        "oracle_review_path": "/tmp/metnos_phase1_oracle_audit_v1.md",
        "oracle_review_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_phase1_oracle_audit_v1.md"],
        "probe_path": "/tmp/metnos_v264_independent_graph_probe.py",
        "probe_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_v264_independent_graph_probe.py"],
        "probe_result_path": "/tmp/metnos_v264_independent_graph_probe_result.json",
        "probe_result_sha256": EXPECTED_ARTIFACTS["/tmp/metnos_v264_independent_graph_probe_result.json"],
        "authorization_review_path": str(AUTHORIZATION_REVIEW_PATH),
        "verifier_path": str(SELF_PATH),
        "verifier_sha256": sha(SELF_PATH),
        "network_calls_before_lock": 0,
        "candidate_outputs_read_before_lock": 0,
    }
    for key, expected in expected_scalars.items():
        if lock.get(key) != expected:
            errors.append(key)

    expected_one_shot = {
        "output_must_not_exist_before_run": True,
        "runner_model_calls_per_case": 1,
        "runner_retries": 0,
        "lock_authorizes_only_exact_output_path": True,
        "lock_authorizes_no_runtime_cutover": True,
    }
    if lock.get("one_shot") != expected_one_shot:
        errors.append("one_shot")
    if lock.get("artifact_hashes") != EXPECTED_ARTIFACTS:
        errors.append("artifact_hashes")

    for path_text, expected in EXPECTED_ARTIFACTS.items():
        path = Path(path_text)
        if not path.is_file() or sha(path) != expected:
            errors.append(f"artifact:{path_text}")

    auth_hash = lock.get("authorization_review_sha256")
    if not AUTHORIZATION_REVIEW_PATH.is_file() or auth_hash != sha(AUTHORIZATION_REVIEW_PATH):
        errors.append("authorization_review_sha256")
    else:
        auth_text = AUTHORIZATION_REVIEW_PATH.read_text()
        if "Verdict: **AUTHORIZE ONE K1/34 RUN**" not in auth_text:
            errors.append("authorization_review_verdict")
        if sha(SELF_PATH) not in auth_text:
            errors.append("authorization_review_verifier")

    if require_output_absent and OUTPUT_PATH.exists():
        errors.append("authorized_output_already_exists")

    try:
        freeze = load_json(Path("/tmp/metnos_v264_typed_phase1.freeze.json"))
        if freeze.get("status") != "frozen_author_pre_inference":
            errors.append("freeze_status")
        if freeze.get("registry_sha256") != expected_scalars["registry_sha256"]:
            errors.append("freeze_registry")
        if freeze.get("contamination_audit_sha256") != expected_scalars["contamination_audit_sha256"]:
            errors.append("freeze_contamination")
        if freeze.get("author_pre_gate_inference_allowed") is not False:
            errors.append("freeze_author_gate")
        if freeze.get("runtime_cutover_eligible") is not False:
            errors.append("freeze_cutover")
    except Exception:
        errors.append("freeze_json")

    try:
        oracle_freeze = load_json(Path("/tmp/metnos_phase1_oracle_audit_v1.freeze.json"))
        oracle_expected = {
            "report": ("/tmp/metnos_phase1_oracle_audit_v1.md", expected_scalars["oracle_report_sha256"]),
            "audit": ("/tmp/metnos_phase1_oracle_audit_v1.json", expected_scalars["oracle_audit_sha256"]),
            "schema": ("/tmp/metnos_phase1_typed_oracle_v1.schema.json", expected_scalars["oracle_schema_sha256"]),
            "overlay": ("/tmp/metnos_phase1_typed_oracle_v1.overlay.json", expected_scalars["oracle_overlay_sha256"]),
        }
        for name, (path_text, expected_hash) in oracle_expected.items():
            item = oracle_freeze.get("artifacts", {}).get(name, {})
            if item.get("path") != path_text or item.get("sha256") != expected_hash:
                errors.append(f"oracle_freeze:{name}")
        if oracle_freeze.get("validation", {}).get("case_count") != 34:
            errors.append("oracle_case_count")
        if oracle_freeze.get("candidate_outputs_read") != 0:
            errors.append("oracle_output_isolation")
    except Exception:
        errors.append("oracle_freeze_json")

    try:
        review = load_json(Path("/tmp/metnos_v264_independent_graph_final_review.json"))
        if review.get("verdict") != "pass_relation_graph_contract":
            errors.append("review_verdict")
        if review.get("inference_calls") != 0 or review.get("candidate_outputs_read") != 0:
            errors.append("review_isolation")
        if review.get("artifacts", {}).get("freeze_sha256") != expected_scalars["freeze_sha256"]:
            errors.append("review_freeze")
        if review.get("verification", {}).get("independent", {}).get("failed") != 0:
            errors.append("review_failures")
    except Exception:
        errors.append("review_json")

    try:
        addendum = Path("/tmp/metnos_v264_independent_graph_final_review.addendum.md").read_text()
        if "The statement that the frozen registry permits no dependency chain deeper than" not in addendum:
            errors.append("review_addendum_correction")
        if "This correction does not weaken or change the PASS verdict" not in addendum:
            errors.append("review_addendum_verdict")
    except Exception:
        errors.append("review_addendum")

    try:
        probe = load_json(Path("/tmp/metnos_v264_independent_graph_probe_result.json"))
        if probe.get("network_calls") != 0 or probe.get("candidate_outputs_read") != 0:
            errors.append("probe_isolation")
        if probe.get("summary", {}).get("tests") != 125 or probe.get("summary", {}).get("failed") != 0:
            errors.append("probe_result")
        if probe.get("runner_sha256") != EXPECTED_ARTIFACTS[str(RUNNER_PATH)]:
            errors.append("probe_runner")
    except Exception:
        errors.append("probe_json")

    try:
        contamination = load_json(Path("/tmp/metnos_v264_typed_phase1_contamination_audit.json"))
        prompt = contamination.get("prompt", {})
        inventory = prompt.get("line_inventory", {})
        overlaps = prompt.get("dataset_overlap", {})
        if prompt.get("maximum_objective_acceptance_credit_eligible") is not True:
            errors.append("contamination_acceptance")
        if inventory.get("surface_or_mixed_line_count") != 0:
            errors.append("contamination_surface")
        if any(item.get("cases_with_maximal_overlap_n_ge_3") != 0 for item in overlaps.values()):
            errors.append("contamination_overlap")
    except Exception:
        errors.append("contamination_json")

    runner_errors = runner._validate_external_gate_payload(lock)
    errors.extend(f"runner:{item}" for item in runner_errors)
    return sorted(set(errors))


def mutate(lock: dict[str, Any], name: str, change: Callable[[dict[str, Any]], None], *, runner_must_reject: bool = False) -> dict[str, Any]:
    changed = copy.deepcopy(lock)
    change(changed)
    independent_errors = validate_payload(changed)
    runner_errors = runner._validate_external_gate_payload(changed)
    return {
        "id": name,
        "pass": bool(independent_errors) and (bool(runner_errors) if runner_must_reject else True),
        "independent_error_count": len(independent_errors),
        "runner_error_count": len(runner_errors),
    }


def mutation_tests(lock: dict[str, Any]) -> list[dict[str, Any]]:
    zero = "0" * 64
    tests = [
        mutate(lock, "gate_status", lambda item: item.__setitem__("status", "blocked"), runner_must_reject=True),
        mutate(lock, "freeze_hash", lambda item: item.__setitem__("freeze_sha256", zero), runner_must_reject=True),
        mutate(lock, "registry_hash", lambda item: item.__setitem__("registry_sha256", zero), runner_must_reject=True),
        mutate(lock, "review_md_hash", lambda item: item.__setitem__("independent_review_sha256", zero), runner_must_reject=True),
        mutate(lock, "review_json_hash", lambda item: item.__setitem__("independent_review_json_sha256", zero)),
        mutate(lock, "review_addendum_hash", lambda item: item.__setitem__("independent_review_addendum_sha256", zero)),
        mutate(lock, "review_addendum_archive_hash", lambda item: item.__setitem__("independent_review_addendum_archive_sha256", zero)),
        mutate(lock, "oracle_freeze_hash", lambda item: item.__setitem__("oracle_freeze_sha256", zero), runner_must_reject=True),
        mutate(lock, "oracle_report_hash", lambda item: item.__setitem__("oracle_report_sha256", zero)),
        mutate(lock, "oracle_review_hash", lambda item: item.__setitem__("oracle_review_sha256", zero), runner_must_reject=True),
        mutate(lock, "oracle_audit_hash", lambda item: item.__setitem__("oracle_audit_sha256", zero)),
        mutate(lock, "oracle_schema_hash", lambda item: item.__setitem__("oracle_schema_sha256", zero)),
        mutate(lock, "oracle_overlay_hash", lambda item: item.__setitem__("oracle_overlay_sha256", zero)),
        mutate(lock, "contamination_hash", lambda item: item.__setitem__("contamination_audit_sha256", zero)),
        mutate(lock, "probe_result_hash", lambda item: item.__setitem__("probe_result_sha256", zero)),
        mutate(lock, "verifier_hash", lambda item: item.__setitem__("verifier_sha256", zero), runner_must_reject=True),
        mutate(lock, "case_count", lambda item: item.__setitem__("authorized_case_count", 33), runner_must_reject=True),
        mutate(lock, "repetitions", lambda item: item.__setitem__("authorized_repetitions", 2), runner_must_reject=True),
        mutate(lock, "output_path", lambda item: item.__setitem__("authorized_output_path", "/tmp/not_v264.json"), runner_must_reject=True),
        mutate(lock, "author_gate_substitution", lambda item: item.update({"lock_version": "metnos.v26.4-author-pre-gate/1.0", "status": "blocked"}), runner_must_reject=True),
        mutate(lock, "missing_field", lambda item: item.pop("artifact_hashes")),
        mutate(lock, "extra_field", lambda item: item.__setitem__("unreviewed", True)),
    ]

    descriptor, path_text = tempfile.mkstemp(prefix="metnos_v264_gate_probe_", suffix=".json", dir="/tmp")
    os.close(descriptor)
    try:
        tests.append(mutate(
            lock,
            "output_preexisting",
            lambda item: item.__setitem__("authorized_output_path", path_text),
            runner_must_reject=True,
        ))
    finally:
        Path(path_text).unlink(missing_ok=True)
    return tests


def verify() -> dict[str, Any]:
    if not LOCK_PATH.is_file():
        return {"valid": False, "errors": ["lock_missing"], "mutations": []}
    lock = load_json(LOCK_PATH)
    errors = validate_payload(lock)
    mutation_results = mutation_tests(lock) if not errors else []
    failed_mutations = [item for item in mutation_results if not item["pass"]]
    if not errors:
        try:
            runner.verify_freeze()
            runner.verify_external_gate()
        except Exception as exc:
            errors.append(f"runner_verification:{type(exc).__name__}:{exc}")
    return {
        "version": "metnos.v26.4-external-gate-verifier/1.0",
        "network_calls": 0,
        "candidate_outputs_read": 0,
        "valid": not errors and not failed_mutations,
        "errors": errors,
        "mutations": mutation_results,
        "summary": {
            "artifact_count": len(EXPECTED_ARTIFACTS),
            "mutations": len(mutation_results),
            "mutation_passed": len(mutation_results) - len(failed_mutations),
            "mutation_failed": len(failed_mutations),
            "authorized_case_count": 34,
            "authorized_repetitions": 1,
            "authorized_output_path": str(OUTPUT_PATH),
        },
    }


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["valid"] else 1)
