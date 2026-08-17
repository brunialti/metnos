#!/usr/bin/env python3
"""Offline positive tests for the paired protocol; no socket can be opened."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
from typing import Any, Callable

from intent_shadow_io import canonical_json_bytes, strict_json_file
from live_evaluator import evaluate, typed_verdict
from live_arm_candidate import extract_response as candidate_extract
from live_arm_current import extract_response as current_extract
from live_protocol import (
    CRITICAL_NO_REGRESSION_COLUMNS,
    AUTHORIZATION_PATH,
    all_query_cases,
    arm_order,
    load_protocol,
    load_request_manifest,
)
from live_runner import (
    DEFAULT_PATHS,
    RunPaths,
    TransportResponse,
    _existing_run_files,
    execute_manifest,
    preflight,
    run_authorized_once,
)


def _check(name: str, function: Callable[[], None], failures: list[dict[str, str]]) -> None:
    try:
        function()
    except Exception as exc:
        failures.append({"name": name, "error": f"{type(exc).__name__}:{exc}"})


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def manifest_shape() -> None:
    manifest = load_request_manifest()
    _assert(len(manifest["records"]) == 316, "316 records")
    _assert(len(all_query_cases()) == 158, "158 queries")
    for index in range(158):
        records = manifest["records"][2 * index : 2 * index + 2]
        _assert(tuple(record["arm"] for record in records) == arm_order(index), f"order {index}")
        _assert({record["sample_index"] for record in records} == {index}, f"pair {index}")
    _assert(sum(record["panel"] == "canonical_120" for record in manifest["records"]) == 240, "canonical")
    _assert(sum(record["panel"] == "typed_controls_4" for record in manifest["records"]) == 8, "typed")
    _assert(sum(record["panel"] == "legacy_phase1_34" for record in manifest["records"]) == 68, "legacy")


def profile_and_no_gold() -> None:
    manifest = load_request_manifest()
    expected = {
        "temperature": 0,
        "seed": 42,
        "max_tokens": 4000,
        "stream": False,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    for record in manifest["records"]:
        request = record["request"]
        for key, value in expected.items():
            _assert(request[key] == value, f"profile {record['request_ordinal']}:{key}")
        _assert(request["messages"][1] == {"role": "user", "content": record["query"]}, "query only")
        lowered = canonical_json_bytes(request).lower()
        _assert(b'"expected"' not in lowered and b'"gold"' not in lowered, "gold contamination")


def roots_and_limits() -> None:
    for document, status in (
        ({"kind": "unrepresentable", "reason": "outside_registry"}, "valid_unrepresentable"),
        ({"kind": "system_control", "control": "undo_last_turn"}, "valid_representable"),
        ({"kind": "operation_graph", "body": [{"kind": "barrier", "barrier": "get/approval", "cases": [{"outcome": "approved", "body": [{"kind": "operation", "route": "set/issues"}]}]}]}, "valid_representable"),
    ):
        result = candidate_extract(canonical_json_bytes(document))
        _assert(result["status"] == status, repr(document))
    for raw in (b'{"kind":"unrepresentable","reason":NaN}', b'{"x":1e999}', b'"' + b'x' * (65536 + 1) + b'"'):
        result = candidate_extract(raw)
        _assert(result["status"] == "technical_invalid", repr(raw[:40]))
    control_bad = current_extract(b'{"verb":NaN}', query="azione", language="it")
    _assert(control_bad["status"] == "technical_invalid", "control limits")


def fake_complete_and_single_use() -> None:
    manifest = load_request_manifest()

    def response_for(record: dict[str, Any]) -> bytes:
        if record["arm"] == "A":
            content = b'{"verb":"get","object":"places"}'
        else:
            content = b'{"kind":"unrepresentable","reason":"outside_registry"}'
        wrapper = {"choices": [{"message": {"content": content.decode("utf-8")}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return canonical_json_bytes(wrapper)

    by_request = {record["request_sha256"]: response_for(record) for record in manifest["records"]}

    def fake(request: dict[str, Any], timeout: int) -> TransportResponse:
        _assert(timeout == 120, "timeout")
        digest = sha256(canonical_json_bytes(request)).hexdigest()
        body = by_request[digest]
        return TransportResponse(True, 200, (("content-type", "application/json"),), body, 1, None)

    with tempfile.TemporaryDirectory(prefix="intent-shadow-live-positive-") as temporary:
        paths = RunPaths.in_directory(Path(temporary))
        batch = execute_manifest(manifest, authorization_sha256="1" * 64, transport=fake, paths=paths)
        _assert(batch["state"] == "complete" and batch["record_count"] == 316, "complete fake")
        _assert(paths.sealed_batch.is_file() and paths.sealed_freeze.is_file(), "sealed files")
        evaluation = evaluate(paths.sealed_batch, paths.sealed_freeze)
        _assert(evaluation["status"] == "ok", "post-seal evaluation")
        _assert(len(evaluation["rows"]) == 248, "typed paired rows")
        _assert(len(evaluation["legacy_phase1"]["rows"]) == 68, "legacy paired rows")
        _assert(evaluation["legacy_phase1"]["cross_panel_compensation"] is False, "legacy separation")
        try:
            execute_manifest(manifest, authorization_sha256="1" * 64, transport=fake, paths=paths)
        except RuntimeError as exc:
            _assert("single-run guard" in str(exc), "rerun reason")
        else:
            raise AssertionError("rerun accepted")


def invalid_semantics_continue() -> None:
    manifest = load_request_manifest()
    calls = 0

    def fake(_request: dict[str, Any], _timeout: int) -> TransportResponse:
        nonlocal calls
        calls += 1
        content = "not-json" if calls == 1 else '{"kind":"unrepresentable","reason":"outside_registry"}'
        body = canonical_json_bytes({"choices": [{"message": {"content": content}}]})
        return TransportResponse(True, 200, (), body, 1, None)

    with tempfile.TemporaryDirectory(prefix="intent-shadow-live-invalid-") as temporary:
        paths = RunPaths.in_directory(Path(temporary))
        batch = execute_manifest(manifest, authorization_sha256="3" * 64, transport=fake, paths=paths)
        _assert(batch["state"] == "complete" and batch["record_count"] == 316, "invalid must continue")
        _assert(batch["records"][0]["extraction"]["status"] == "technical_invalid", "invalid counted")


def fake_transport_stop() -> None:
    manifest = load_request_manifest()
    calls = 0

    def fake(_request: dict[str, Any], _timeout: int) -> TransportResponse:
        nonlocal calls
        calls += 1
        if calls == 3:
            return TransportResponse(False, None, (), b"", 2, "TRANSPORT_TIMEOUT")
        body = canonical_json_bytes({"choices": [{"message": {"content": '{"kind":"unrepresentable","reason":"outside_registry"}'}}]})
        return TransportResponse(True, 200, (), body, 1, None)

    with tempfile.TemporaryDirectory(prefix="intent-shadow-live-stop-") as temporary:
        paths = RunPaths.in_directory(Path(temporary))
        batch = execute_manifest(manifest, authorization_sha256="2" * 64, transport=fake, paths=paths)
        _assert(batch["state"] == "partial_transport_stop" and batch["record_count"] == 3, "partial")
        _assert(paths.partial.is_file() and not paths.sealed_batch.exists(), "partial files")


def armed_preflight_and_guards() -> None:
    with tempfile.TemporaryDirectory(prefix="intent-shadow-armed-preflight-") as temporary:
        directory = Path(temporary)
        paths = RunPaths.in_directory(directory)
        report = preflight(
            authorization_state="armed",
            authorization_path=AUTHORIZATION_PATH,
            paths=paths,
            check_runtime_environment=False,
        )
        _assert(report["status"] == "armed_ready", "armed preflight state")
        _assert(report["request_count"] == 316 and report["single_run_guard_clear"] is True, "armed shape")
        _assert(report["network_touched"] is False and report["gpu_touched"] is False, "preflight side effects")

        missing = directory / "missing-authorization.json"
        for label, path in (("missing", missing),):
            try:
                preflight(
                    authorization_state="armed",
                    authorization_path=path,
                    paths=paths,
                    check_runtime_environment=False,
                )
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"{label} authorization accepted")

        stale = strict_json_file(AUTHORIZATION_PATH)
        stale["manifest_file_sha256"] = "0" * 64
        stale_path = directory / "stale-authorization.json"
        stale_path.write_bytes(canonical_json_bytes(stale) + b"\n")
        extra_path = directory / "extra-authorization.json"
        extra_path.write_bytes(AUTHORIZATION_PATH.read_bytes())
        for label, path in (("stale", stale_path), ("extra", extra_path)):
            try:
                preflight(
                    authorization_state="armed",
                    authorization_path=path,
                    paths=paths,
                    check_runtime_environment=False,
                )
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"{label} authorization accepted")

        artifacts = (
            paths.consumption,
            paths.journal,
            paths.checkpoint,
            paths.partial,
            paths.sealed_batch,
            paths.sealed_freeze,
            directory / "live_evaluation_v0_1.json",
        )
        for artifact in artifacts:
            artifact.write_text("{}", encoding="utf-8")
            try:
                preflight(
                    authorization_state="armed",
                    authorization_path=AUTHORIZATION_PATH,
                    paths=paths,
                    check_runtime_environment=False,
                )
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"run artifact accepted:{artifact.name}")
            artifact.unlink()

        class TransportSentinel(Exception):
            pass

        calls = 0

        def sentinel(_request: dict[str, Any], _timeout: int) -> TransportResponse:
            nonlocal calls
            calls += 1
            raise TransportSentinel

        try:
            run_authorized_once(
                AUTHORIZATION_PATH,
                transport=sentinel,
                paths=paths,
                check_runtime_environment=False,
            )
        except TransportSentinel:
            pass
        else:
            raise AssertionError("transport sentinel not reached")
        _assert(calls == 1, "exactly one fake transport threshold")
        _assert(not _existing_run_files(DEFAULT_PATHS), "no real run consumption")


def _verdict_aggregates() -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    canonical_a = {column: 20 for column in CRITICAL_NO_REGRESSION_COLUMNS}
    canonical_b = dict(canonical_a)
    canonical_a["semantic_exact"] = 60
    canonical_b["semantic_exact"] = 63
    return canonical_a, canonical_b, {"semantic_exact": 4}


def correct_abstention_regression_blocks_pass() -> None:
    protocol_columns = load_protocol()["evaluation"]["critical_columns_no_regression"]
    canonical_a, canonical_b, typed_b = _verdict_aggregates()
    canonical_a["correct_abstention"] = 5
    canonical_b["correct_abstention"] = 4
    report = typed_verdict(canonical_a, canonical_b, typed_b, protocol_columns)
    _assert(report["verdict"] != "candidate_pass", "abstention regression must block pass")
    _assert(report["critical_regressions"] == ["correct_abstention"], "abstention regression reported")
    _assert(
        set(protocol_columns)
        == set(report["critical_columns_no_regression"])
        == set(CRITICAL_NO_REGRESSION_COLUMNS),
        "canonical critical set",
    )


def no_critical_regression_allows_pass() -> None:
    protocol_columns = load_protocol()["evaluation"]["critical_columns_no_regression"]
    canonical_a, canonical_b, typed_b = _verdict_aggregates()
    report = typed_verdict(canonical_a, canonical_b, typed_b, protocol_columns)
    _assert(report["verdict"] == "candidate_pass", "approved gates must pass")
    _assert(report["critical_regressions"] == [], "no critical regression")
    _assert(
        set(protocol_columns)
        == set(report["critical_columns_no_regression"])
        == set(CRITICAL_NO_REGRESSION_COLUMNS),
        "canonical critical set",
    )


def main() -> int:
    failures: list[dict[str, str]] = []
    tests = (
        ("manifest_shape", manifest_shape),
        ("profile_and_no_gold", profile_and_no_gold),
        ("roots_and_limits", roots_and_limits),
        ("fake_complete_and_single_use", fake_complete_and_single_use),
        ("invalid_semantics_continue", invalid_semantics_continue),
        ("fake_transport_stop", fake_transport_stop),
        ("armed_preflight_and_guards", armed_preflight_and_guards),
        ("correct_abstention_regression_blocks_pass", correct_abstention_regression_blocks_pass),
        ("no_critical_regression_allows_pass", no_critical_regression_allows_pass),
    )
    for name, function in tests:
        _check(name, function, failures)
    print(json.dumps({"status": "ok" if not failures else "error", "passed": len(tests) - len(failures), "total": len(tests), "failures": failures}, ensure_ascii=False, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
