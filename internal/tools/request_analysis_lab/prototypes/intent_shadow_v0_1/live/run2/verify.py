#!/usr/bin/env python3
"""Fail-closed verifier for disarmed or separately armed RUN 2."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .protocol import (
    AUTHORIZATION_PATH,
    BACKEND_ABSOLUTE_SOURCES,
    BUILD_SOURCE_FILES,
    CREATED_DATE,
    FREEZE_FORMAT,
    FREEZE_PATH,
    HERE,
    IMMUTABLE_LOCAL_FILES,
    RUN_ARTIFACT_NAMES,
    RUN_ID,
    ROOT,
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
)


# Only modules reachable by the request path are scanned.  The verifier itself
# names forbidden filenames in its audit policy; the post-seal evaluator is a
# separate import graph and is deliberately excluded.
ACTIVE_LIVE_FILES = ("protocol.py", "arm_a.py", "arm_b.py", "runner.py")
FORBIDDEN_GOLD_FILENAMES = (
    "intent_shadow_oracle_v0_1.json",
    "metnos_phase1_typed_oracle_v1.overlay.json",
    "question_focus_controls_v1.json",
)


def _add(errors: list[str], code: str, detail: str) -> None:
    errors.append(f"{code}:{detail}")


def _scan_gold_and_hardcoding(errors: list[str], panel: dict[str, Any]) -> None:
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
    """Verify the complete frozen code/data/backend perimeter without run-state checks."""
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
        if freeze["local_files"].get(name) != stable_file_sha256(HERE / name):
            raise RuntimeError(f"FREEZE_LOCAL_HASH:{name}")
    if type(freeze.get("build_sources")) is not dict or set(freeze["build_sources"]) != set(BUILD_SOURCE_FILES):
        raise RuntimeError("FREEZE_SOURCE_SET:membership")
    for name in BUILD_SOURCE_FILES:
        if freeze["build_sources"].get(name) != stable_file_sha256(ROOT / name):
            raise RuntimeError(f"FREEZE_SOURCE_HASH:{name}")
    if type(freeze.get("absolute_authorities")) is not dict or set(freeze["absolute_authorities"]) != set(BACKEND_ABSOLUTE_SOURCES):
        raise RuntimeError("FREEZE_ABSOLUTE_SET:membership")
    for name in BACKEND_ABSOLUTE_SOURCES:
        if freeze["absolute_authorities"].get(name) != stable_file_sha256(Path(name)):
            raise RuntimeError(f"FREEZE_ABSOLUTE_HASH:{name}")
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
            raise RuntimeError("authorization state")
        snapshot = load_control_snapshot()
        panel = load_query_panel()
        protocol = load_protocol()
        manifest = load_manifest()
        verify_frozen_files(
            freeze_path, check_runtime_environment=check_runtime_environment,
        )
        _scan_gold_and_hardcoding(errors, panel)
        _scan_query_only(panel, errors)
        _scan_query_only(manifest, errors)

        first = panel["cases"][0]
        request_a = request_for("A", first["query"], first["language"])
        request_b = request_for("B", first["query"], first["language"])
        if "response_format" in request_a or "grammar" in request_a or "tools" in request_a:
            _add(errors, "CONTROL_REQUEST_CHANGED", "structured-control")
        response_format = request_b.get("response_format")
        if (
            type(response_format) is not dict
            or response_format.get("type") != "json_schema"
            or type(response_format.get("json_schema")) is not dict
            or response_format["json_schema"].get("strict") is not True
            or "grammar" in request_b
            or "tools" in request_b
        ):
            _add(errors, "CANDIDATE_REQUEST_NOT_STRICT", "response_format")
        common = ("model", "temperature", "seed", "max_tokens", "stream", "cache_prompt", "chat_template_kwargs")
        if any(request_a.get(key) != request_b.get(key) for key in common):
            _add(errors, "ARM_PROFILE_DRIFT", "common-generation-profile")

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
        "counts": {"queries": 158, "requests": 316, "canonical": 120, "typed_controls": 4, "legacy": 34},
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
