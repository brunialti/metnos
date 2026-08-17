#!/usr/bin/env python3
"""Offline adversarial mutations for protocol, manifest, freeze and guard."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from typing import Any, Callable

from intent_shadow_io import strict_json_file
from live_protocol import (
    AUTHORIZATION_PATH,
    CONTROL_SNAPSHOT_PATH,
    PROTOCOL_FREEZE_PATH,
    PROTOCOL_PATH,
    REQUEST_MANIFEST_PATH,
    canonical_write_bytes,
    load_control_snapshot,
    load_protocol,
    load_request_manifest,
    payload_sha256,
)
from live_runner import RunPaths, preflight, validate_authorization
from verify_live_protocol import verify_live_protocol


def _write(path: Path, value: Any) -> None:
    path.write_bytes(canonical_write_bytes(value))


def _expect_reject(name: str, function: Callable[[], Any], failures: list[dict[str, str]]) -> None:
    try:
        function()
    except Exception:
        return
    failures.append({"name": name, "error": "mutation accepted"})


def _expect_accept(name: str, function: Callable[[], Any], failures: list[dict[str, str]]) -> None:
    try:
        function()
    except Exception as exc:
        failures.append({"name": name, "error": f"{type(exc).__name__}:{exc}"})


def _mutated_json(
    source: Path,
    temporary: Path,
    name: str,
    mutate: Callable[[dict[str, Any]], None],
    *,
    payload_field: str | None = None,
) -> Path:
    value = strict_json_file(source)
    mutate(value)
    if payload_field is not None:
        value[payload_field] = payload_sha256(value, payload_field)
    target = temporary / f"{name}.json"
    _write(target, value)
    return target


def main() -> int:
    failures: list[dict[str, str]] = []
    negatives = 0
    positives = 0
    with tempfile.TemporaryDirectory(prefix="intent-shadow-live-mutations-") as directory:
        temp = Path(directory)

        def reject(name: str, function: Callable[[], Any]) -> None:
            nonlocal negatives
            negatives += 1
            _expect_reject(name, function, failures)

        def accept(name: str, function: Callable[[], Any]) -> None:
            nonlocal positives
            positives += 1
            _expect_accept(name, function, failures)

        protocol_mutations = (
            ("protocol_missing", lambda d: d.pop("status"), True),
            ("protocol_extra", lambda d: d.__setitem__("junk", 1), True),
            ("protocol_temperature_bool", lambda d: d["generation_profile"].__setitem__("temperature", False), True),
            ("protocol_seed_float", lambda d: d["generation_profile"].__setitem__("seed", 42.0), True),
            ("protocol_retry", lambda d: d["generation_profile"].__setitem__("retry_count", 1), True),
            ("protocol_thinking", lambda d: d["generation_profile"].__setitem__("thinking", True), True),
            ("protocol_timeout", lambda d: d["generation_profile"].__setitem__("timeout_seconds", 121), True),
            ("protocol_limit", lambda d: d["technical_limits"].__setitem__("nodes", 10001), True),
            ("protocol_order", lambda d: d["schedule"].__setitem__("algorithm", "all A then all B"), True),
            ("protocol_count", lambda d: d["schedule"].__setitem__("request_count", 315), True),
            ("protocol_arm_swap", lambda d: d["arms"].__setitem__("A", d["arms"]["B"]), True),
            ("protocol_gate", lambda d: d["evaluation"].__setitem__("canonical_improvement_gate", "at least 0"), True),
            ("protocol_payload_raw", lambda d: d.__setitem__("protocol_payload_sha256", "0" * 64), False),
        )
        for name, mutation, reseal in protocol_mutations:
            path = _mutated_json(PROTOCOL_PATH, temp, name, mutation, payload_field="protocol_payload_sha256" if reseal else None)
            reject(name, lambda path=path: load_protocol(path))

        manifest_mutations = (
            ("manifest_missing_record", lambda d: d["records"].pop()),
            ("manifest_duplicate_record", lambda d: d["records"].__setitem__(1, deepcopy(d["records"][0]))),
            ("manifest_query", lambda d: d["records"][0].__setitem__("query", d["records"][0]["query"] + "x")),
            ("manifest_query_hash", lambda d: d["records"][0].__setitem__("query_sha256", "0" * 64)),
            ("manifest_arm", lambda d: d["records"][0].__setitem__("arm", "B")),
            ("manifest_order", lambda d: d["records"].__setitem__(0, d["records"].pop(1))),
            ("manifest_request_seed", lambda d: d["records"][0]["request"].__setitem__("seed", 43)),
            ("manifest_request_hash", lambda d: d["records"][0].__setitem__("request_sha256", "0" * 64)),
            ("manifest_gold_field", lambda d: d["records"][0].__setitem__("expected", {})),
            ("manifest_bool_index", lambda d: d["records"][0].__setitem__("sample_index", False)),
        )
        for name, mutation in manifest_mutations:
            path = _mutated_json(REQUEST_MANIFEST_PATH, temp, name, mutation, payload_field="manifest_payload_sha256")
            reject(name, lambda path=path: load_request_manifest(path))

        snapshot_mutations = (
            ("snapshot_source_omit", lambda d: d["repo_sources"].pop(next(iter(d["repo_sources"])))),
            ("snapshot_source_replace", lambda d: d["repo_sources"].__setitem__(next(iter(d["repo_sources"])), "0" * 64)),
            ("snapshot_backend", lambda d: d["backend_identity"].__setitem__("backend_build", "unknown")),
            ("snapshot_weights", lambda d: d["backend_identity"].__setitem__("weights_sha256", "0" * 64)),
            ("snapshot_scaffold", lambda d: d["current_extractor"].__setitem__("scaffold", True)),
            ("snapshot_adapter_delta", lambda d: d["current_extractor"].__setitem__("adapter_delta", "second calls allowed")),
        )
        for name, mutation in snapshot_mutations:
            path = _mutated_json(CONTROL_SNAPSHOT_PATH, temp, name, mutation, payload_field="snapshot_payload_sha256")
            reject(name, lambda path=path: _load_and_verify_snapshot(path))

        raw_protocol = PROTOCOL_PATH.read_bytes()
        for name, raw in (
            ("protocol_duplicate_key", raw_protocol.replace(b'{\n', b'{\n  "status": "prepared_not_authorized",\n', 1)),
            ("protocol_nan", raw_protocol.replace(b'{\n', b'{\n  "probe": NaN,\n', 1)),
            ("protocol_overflow", raw_protocol.replace(b'{\n', b'{\n  "probe": 1e999,\n', 1)),
        ):
            path = temp / f"{name}.json"
            path.write_bytes(raw)
            reject(name, lambda path=path: load_protocol(path))

        freeze_mutations = (
            ("freeze_local_omit", lambda d: d["local_files"].pop(next(iter(d["local_files"])))),
            ("freeze_local_junk", lambda d: d["local_files"].__setitem__("junk", "0" * 64)),
            ("freeze_repo_hash", lambda d: d["repo_authorities"].__setitem__(next(iter(d["repo_authorities"])), "0" * 64)),
            ("freeze_absolute_omit", lambda d: d["absolute_authorities"].pop(next(iter(d["absolute_authorities"])))),
            ("freeze_status", lambda d: d.__setitem__("status", "authorized")),
            ("freeze_manifest_payload", lambda d: d.__setitem__("manifest_payload_sha256", "0" * 64)),
        )
        for name, mutation in freeze_mutations:
            path = _mutated_json(PROTOCOL_FREEZE_PATH, temp, name, mutation, payload_field="lock_payload_sha256")
            reject(name, lambda path=path: _require_verifier_error(path))

        authorization_base = {
            "authorization_format": "metnos.intent-shadow-live-authorization/0.1",
            "created_date": "2026-08-12",
            "protocol_file_sha256": "0" * 64,
            "protocol_freeze_file_sha256": "0" * 64,
            "protocol_payload_sha256": "0" * 64,
            "manifest_file_sha256": "0" * 64,
            "independent_audit": {"reviewer": "reviewer_b_independent", "report_path": "missing", "report_sha256": "0" * 64, "pass": True},
            "root_authorization": {"authorized": True, "authorized_by": "root", "authorization_id": "x"},
            "single_use_nonce_sha256": "0" * 64,
        }
        auth = temp / "authorization.json"
        _write(auth, authorization_base)
        reject("authorization_wrong_bindings", lambda: validate_authorization(auth))
        authorization_bool = deepcopy(authorization_base)
        authorization_bool["root_authorization"]["authorized"] = 1
        _write(auth, authorization_bool)
        reject("authorization_bool_int", lambda: validate_authorization(auth))

        with tempfile.TemporaryDirectory(prefix="intent-shadow-live-guard-") as run_directory:
            paths = RunPaths.in_directory(Path(run_directory))
            paths.consumption.write_text("{}", encoding="utf-8")
            reject("existing_consumption_guard", lambda: preflight(
                authorization_state="armed",
                authorization_path=AUTHORIZATION_PATH,
                paths=paths,
                check_runtime_environment=False,
            ))

        accept("canonical_protocol", load_protocol)
        accept("canonical_manifest", load_request_manifest)
        accept("canonical_snapshot", load_control_snapshot)
        accept("canonical_freeze", lambda: _require_verifier_ok(PROTOCOL_FREEZE_PATH))
        with tempfile.TemporaryDirectory(prefix="intent-shadow-live-clean-") as clean_directory:
            accept("clean_offline_preflight", lambda: preflight(
                authorization_state="armed",
                authorization_path=AUTHORIZATION_PATH,
                paths=RunPaths.in_directory(Path(clean_directory)),
                check_runtime_environment=False,
            ))

    print(json.dumps({
        "status": "ok" if not failures else "error",
        "negative_total": negatives,
        "negative_rejected": negatives - sum(1 for item in failures if item["name"] not in {"canonical_protocol", "canonical_manifest", "canonical_snapshot", "canonical_freeze", "clean_offline_preflight"}),
        "positive_total": positives,
        "positive_accepted": positives - sum(1 for item in failures if item["name"] in {"canonical_protocol", "canonical_manifest", "canonical_snapshot", "canonical_freeze", "clean_offline_preflight"}),
        "failures": failures,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if not failures else 1


def _require_verifier_error(path: Path) -> None:
    report = verify_live_protocol(
        path,
        check_runtime_environment=False,
        authorization_state="armed",
        authorization_path=AUTHORIZATION_PATH,
    )
    if report["error_count"] != 0:
        raise RuntimeError("rejected as expected")


def _load_and_verify_snapshot(path: Path) -> None:
    from live_protocol import verify_control_environment
    verify_control_environment(load_control_snapshot(path))


def _require_verifier_ok(path: Path) -> None:
    report = verify_live_protocol(
        path,
        check_runtime_environment=False,
        authorization_state="armed",
        authorization_path=AUTHORIZATION_PATH,
    )
    if report["error_count"] != 0:
        raise AssertionError(report["errors"])


if __name__ == "__main__":
    raise SystemExit(main())
