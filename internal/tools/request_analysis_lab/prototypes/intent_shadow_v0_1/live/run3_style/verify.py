#!/usr/bin/env python3
"""Fail-closed verifier for disarmed or separately armed RUN3 style."""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .protocol import (
    ARMS,
    AUTHORIZATION_PATH,
    BACKEND_ABSOLUTE_SOURCES,
    BUILD_SOURCE_FILES,
    CREATED_DATE,
    FREEZE_FORMAT,
    FREEZE_PATH,
    HERE,
    IMMUTABLE_LOCAL_FILES,
    REQUEST_COUNT,
    ROOT,
    RUN_ARTIFACT_NAMES,
    RUN_ID,
    STYLE_ARMS,
    arm_order,
    canonical_json_bytes,
    exact_json_equal,
    file_sha256,
    load_control_snapshot,
    load_manifest,
    load_protocol,
    load_query_panel,
    payload_sha256,
    request_for,
    stable_file_sha256,
    strict_json_file,
    verify_candidate_environment,
    verify_control_environment,
    verify_hash_seed_environment,
)


ACTIVE_LIVE_FILES = ("protocol.py", "arm_style.py", "runner.py")
FORBIDDEN_GOLD_FILENAMES = (
    "intent_shadow_oracle_v0_1.json",
    "metnos_phase1_typed_oracle_v1.overlay.json",
    "question_focus_controls_v1.json",
)


def _add(errors: list[str], code: str, detail: str) -> None:
    errors.append(f"{code}:{detail}")


def _scan_live_sources(errors: list[str], panel: dict[str, Any]) -> None:
    source = "\n".join((HERE / name).read_text(encoding="utf-8") for name in ACTIVE_LIVE_FILES)
    for name in FORBIDDEN_GOLD_FILENAMES:
        if name in source:
            _add(errors, "GOLD_PATH_IN_LIVE", name)
    folded = source.casefold()
    for case in panel["cases"]:
        query = case["query"].casefold().strip()
        if query and query in folded:
            _add(errors, "QUERY_HARDCODING", case["opaque_case_id"])
            break
    for marker in ("candidate_v0_4", "coverage-before-root", "whole-compound"):
        if marker in folded:
            _add(errors, "NON_STYLE_HYPOTHESIS_IN_LIVE", marker)


def _scan_query_only(value: Any, errors: list[str], path: str = "$") -> None:
    if type(value) is dict:
        for key, item in value.items():
            lowered = key.casefold()
            if lowered in {"expected", "oracle", "answer", "label", "gold"}:
                _add(errors, "GOLD_FIELD", path + "." + key)
            _scan_query_only(item, errors, path + "." + key)
    elif type(value) is list:
        for index, item in enumerate(value):
            _scan_query_only(item, errors, f"{path}[{index}]")


def verify_frozen_files(
    freeze_path: Path = FREEZE_PATH, *, check_runtime_environment: bool = True,
) -> dict[str, Any]:
    snapshot = load_control_snapshot()
    protocol = load_protocol()
    manifest = load_manifest()
    verify_candidate_environment()
    if check_runtime_environment:
        verify_control_environment(snapshot)
    freeze = strict_json_file(freeze_path)
    expected_keys = {
        "freeze_format", "run_id", "created_date", "algorithm", "status",
        "protocol_payload_sha256", "manifest_payload_sha256",
        "control_snapshot_payload_sha256", "local_files", "build_sources",
        "absolute_authorities", "detection_lexicon_behavior_sha256",
        "lock_payload_sha256",
    }
    if type(freeze) is not dict or set(freeze) != expected_keys:
        raise RuntimeError("FREEZE_SCHEMA:root")
    constants = {
        "freeze_format": FREEZE_FORMAT, "run_id": RUN_ID,
        "created_date": CREATED_DATE, "algorithm": "sha256",
        "status": "disarmed_prepared_no_inference",
        "protocol_payload_sha256": protocol["protocol_payload_sha256"],
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "control_snapshot_payload_sha256": snapshot["snapshot_payload_sha256"],
        "detection_lexicon_behavior_sha256": snapshot["runtime_state"]["behavior_sha256"],
    }
    for key, expected in constants.items():
        if type(freeze.get(key)) is not type(expected) or freeze.get(key) != expected:
            raise RuntimeError(f"FREEZE_CONSTANT:{key}")
    if type(freeze.get("local_files")) is not dict or set(freeze["local_files"]) != set(IMMUTABLE_LOCAL_FILES):
        raise RuntimeError("FREEZE_LOCAL_SET:membership")
    for name in IMMUTABLE_LOCAL_FILES:
        if freeze["local_files"].get(name) != file_sha256(HERE / name):
            raise RuntimeError(f"FREEZE_LOCAL_HASH:{name}")
    if type(freeze.get("build_sources")) is not dict or set(freeze["build_sources"]) != set(BUILD_SOURCE_FILES):
        raise RuntimeError("FREEZE_BUILD_SET:membership")
    for name in BUILD_SOURCE_FILES:
        if freeze["build_sources"].get(name) != file_sha256(ROOT / name):
            raise RuntimeError(f"FREEZE_BUILD_HASH:{name}")
    if type(freeze.get("absolute_authorities")) is not dict or set(freeze["absolute_authorities"]) != set(BACKEND_ABSOLUTE_SOURCES):
        raise RuntimeError("FREEZE_AUTHORITY_SET:membership")
    for name in BACKEND_ABSOLUTE_SOURCES:
        if freeze["absolute_authorities"].get(name) != stable_file_sha256(Path(name)):
            raise RuntimeError(f"FREEZE_AUTHORITY_HASH:{name}")
    if freeze.get("lock_payload_sha256") != payload_sha256(freeze, "lock_payload_sha256"):
        raise RuntimeError("FREEZE_LOCK:payload")
    return freeze


def verify_protocol(
    freeze_path: Path = FREEZE_PATH, *, authorization_state: str = "disarmed",
    authorization_path: Path = AUTHORIZATION_PATH,
    check_runtime_environment: bool = True,
) -> dict[str, Any]:
    errors: list[str] = []
    authorization_sha256: str | None = None
    try:
        if authorization_state not in {"disarmed", "armed"}:
            raise ValueError("authorization_state")
        from .build_artifacts import check
        build_errors = check()
        if type(build_errors) is not list or build_errors != []:
            _add(errors, "BUILD_DRIFT", repr(build_errors))
        verify_frozen_files(freeze_path, check_runtime_environment=check_runtime_environment)
        verify_hash_seed_environment()
        protocol = load_protocol()
        manifest = load_manifest()
        panel = load_query_panel()
        _scan_query_only(protocol, errors)
        _scan_query_only(manifest, errors)
        _scan_live_sources(errors, panel)
        if len(manifest["records"]) != REQUEST_COUNT:
            _add(errors, "MANIFEST_COUNT", str(len(manifest["records"])))
        sequence_counts = {sequence: 0 for sequence in ("ABCD", "BCDA", "CDAB", "DABC")}
        names = {
            ("A_SYSTEM_CURRENT", "S0_CURRENT", "S1_METNOS_SHORT", "S2_PROCEDURAL"): "ABCD",
            ("S0_CURRENT", "S1_METNOS_SHORT", "S2_PROCEDURAL", "A_SYSTEM_CURRENT"): "BCDA",
            ("S1_METNOS_SHORT", "S2_PROCEDURAL", "A_SYSTEM_CURRENT", "S0_CURRENT"): "CDAB",
            ("S2_PROCEDURAL", "A_SYSTEM_CURRENT", "S0_CURRENT", "S1_METNOS_SHORT"): "DABC",
        }
        from ..run2.protocol import request_for as run2_request_for
        for case in panel["cases"]:
            sequence_counts[names[arm_order(case["sample_index"])]] += 1
            requests = {arm: request_for(arm, case["query"], case["language"]) for arm in ARMS}
            current_a = run2_request_for("A", case["query"], case["language"])
            current_b = run2_request_for("B", case["query"], case["language"])
            if canonical_json_bytes(requests["A_SYSTEM_CURRENT"]) != canonical_json_bytes(current_a):
                _add(errors, "A_NOT_RUN2_SYSTEM", case["opaque_case_id"])
                break
            if canonical_json_bytes(requests["S0_CURRENT"]) != canonical_json_bytes(current_b):
                _add(errors, "S0_NOT_CURRENT", case["opaque_case_id"])
                break
            baseline = requests["S0_CURRENT"]
            for arm in STYLE_ARMS:
                request = requests[arm]
                response_format = request.get("response_format")
                if (
                    type(response_format) is not dict
                    or response_format.get("type") != "json_schema"
                    or type(response_format.get("json_schema")) is not dict
                    or response_format["json_schema"].get("strict") is not True
                    or "grammar" in request or "tools" in request
                ):
                    _add(errors, "STRUCTURED_CONTRACT", arm)
                normalized = deepcopy(request)
                normalized["messages"][0]["content"] = baseline["messages"][0]["content"]
                if canonical_json_bytes(normalized) != canonical_json_bytes(baseline):
                    _add(errors, "NON_PROMPT_ARM_DIFF", f"{case['opaque_case_id']}:{arm}")
                    break
        if sequence_counts != {"ABCD": 40, "BCDA": 40, "CDAB": 39, "DABC": 39}:
            _add(errors, "LATIN_COUNTS", repr(sequence_counts))
        authorization_candidates = sorted(HERE.glob("authorization*.json"))
        if authorization_state == "disarmed":
            if authorization_candidates:
                _add(errors, "UNEXPECTED_AUTHORIZATION", ",".join(path.name for path in authorization_candidates))
        else:
            if not authorization_path.is_file():
                _add(errors, "MISSING_AUTHORIZATION", authorization_path.name)
            else:
                from .runner import validate_authorization
                validate_authorization(authorization_path)
                authorization_sha256 = file_sha256(authorization_path)
            extras = [path.name for path in authorization_candidates if path.resolve() != authorization_path.resolve()]
            if extras:
                _add(errors, "EXTRA_AUTHORIZATION", ",".join(extras))
        touched = sorted(name for name in RUN_ARTIFACT_NAMES if (HERE / name).exists())
        if touched:
            _add(errors, "RUN_ALREADY_TOUCHED", ",".join(touched))
    except Exception as exc:
        _add(errors, "VERIFY_EXCEPTION", f"{type(exc).__name__}:{exc}")
    return {
        "status": "ok" if not errors else "error", "run_id": RUN_ID,
        "error_count": len(errors), "errors": errors,
        "counts": {"queries": 158, "requests": REQUEST_COUNT, "arms": 4, "canonical": 120, "typed_controls": 4, "legacy": 34},
        "authorization_state": authorization_state,
        "authorization_sha256": authorization_sha256,
        "inference_executed": False, "network_touched": False, "gpu_touched": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", type=Path, default=FREEZE_PATH)
    parser.add_argument("--authorization-state", choices=("disarmed", "armed"), default="disarmed")
    parser.add_argument("--authorization", type=Path, default=AUTHORIZATION_PATH)
    parser.add_argument("--skip-runtime-environment", action="store_true")
    args = parser.parse_args()
    report = verify_protocol(
        args.freeze, authorization_state=args.authorization_state,
        authorization_path=args.authorization,
        check_runtime_environment=not args.skip_runtime_environment,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
