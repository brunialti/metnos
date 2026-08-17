#!/usr/bin/env python3
"""Offline author tests for the V26.5.6 compact K1/34 runner.

Every HTTP interaction is supplied by a local in-memory fake.  This script is
intended to run only as ``/usr/bin/python3 -I -B`` and must leave no cache or
temporary file in the candidate directory.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import types


HERE = Path(__file__).resolve(strict=True).parent
REPOSITORY = HERE.parents[4]
RUNNER_PATH = HERE / "metnos_v2656_k1_runner.py"
OFFLINE_EVALUATOR_PATH = HERE / "metnos_v2656_offline_evaluator.py"
COMPACT_FIXTURE_PATH = (
    REPOSITORY / "internal/tools/request_analysis_lab/candidates/v2651/"
    "metnos_v2651_compact_mutation_fixture.json"
)
COMPACT_FIXTURE_SHA256 = (
    "8ce526f2384c4e1a9e1103c97ee3eb0af3f4bff1302cae621d61f5fe792d023e"
)
ENDPOINT = "http://127.0.0.1:8080"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_raises(exception_type, action, label: str) -> BaseException:
    try:
        action()
    except exception_type as error:
        return error
    raise AssertionError(f"expected {exception_type.__name__}: {label}")


def load_runner():
    spec = importlib.util.spec_from_file_location("metnos_v2656_runner_test", RUNNER_PATH)
    check(spec is not None and spec.loader is not None, "runner loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None, f"loader unavailable: {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, *, read_error=None):
        self._body = body
        self._status = status
        self._read_error = read_error
        self.headers = {"Content-Length": str(len(body))}
        self.closed = False

    def getcode(self):
        return self._status

    def read(self, limit: int = -1):
        if self._read_error is not None:
            raise self._read_error
        return self._body if limit < 0 else self._body[:limit]

    def close(self):
        self.closed = True


def direct_frame(segment_count: int) -> dict:
    return {
        "status": "supported",
        "atoms": [{
            "atom_kind": "projection",
            "clause_start_segment_id": 1,
            "clause_end_segment_id": max(1, segment_count),
            "clause_role": "main_request",
            "clause_role_proof": {"kind": "discourse_structure"},
            "speech_act": "open_question",
            "speech_act_proof": {"kind": "clause_construction"},
            "relation": "spatial.located_at",
            "relation_proof": {"kind": "clause_construction"},
            "arguments": [
                {
                    "kind": "bound", "ref": "actor.current",
                    "proof": {
                        "kind": "predicate_morphology", "predicate_segment_id": 1,
                    },
                },
                {"kind": "unknown", "proof": {"kind": "interrogative_construction"}},
                {
                    "kind": "bound", "ref": "time.current",
                    "proof": {"kind": "utterance_context"},
                },
            ],
        }],
    }


class FakeTransport:
    """A urlopen-compatible deterministic GET/POST fake, never a socket client."""

    def __init__(
        self, *, schema_invalid_at=(), inner_json_invalid_at=(),
        outer_json_invalid_at=(), shape_invalid_at=(), transport_fail_at=(),
        preflight_failure: bool = False,
    ):
        self.schema_invalid_at = set(schema_invalid_at)
        self.inner_json_invalid_at = set(inner_json_invalid_at)
        self.outer_json_invalid_at = set(outer_json_invalid_at)
        self.shape_invalid_at = set(shape_invalid_at)
        self.transport_fail_at = set(transport_fail_at)
        self.preflight_failure = preflight_failure
        self.get_calls = 0
        self.post_calls = 0
        self.calls = []
        self.request_bodies = []

    def __call__(self, request, timeout):
        method = request.get_method()
        self.calls.append((method, request.full_url, timeout))
        if method == "GET":
            self.get_calls += 1
            check(request.full_url == ENDPOINT + "/v1/models", "wrong preflight URL")
            if self.preflight_failure:
                raise PermissionError(1, "synthetic sandbox EPERM")
            return FakeResponse(b'{"data":[]}')
        check(method == "POST", "unexpected fake method")
        self.post_calls += 1
        ordinal = self.post_calls
        check(request.full_url == ENDPOINT + "/v1/chat/completions", "wrong chat URL")
        check(type(request.data) is bytes, "request body is not bytes")
        body = json.loads(request.data)
        self.request_bodies.append(request.data)
        check(body["temperature"] == 0 and body["seed"] == 92, "sampling drift")
        check(body["max_tokens"] == 2200, "token limit drift")
        check(body["response_format"]["type"] == "json_schema", "schema mode drift")
        check(len(body["messages"]) == 2, "message count drift")
        user = json.loads(body["messages"][1]["content"])
        check(set(user) == {"original_request", "segments"}, "user envelope drift")
        check(
            [item["id"] for item in user["segments"]]
            == list(range(1, len(user["segments"]) + 1)),
            "segment IDs are not contiguous",
        )
        if ordinal in self.transport_fail_at:
            raise PermissionError(
                1, "synthetic sandbox EPERM token=secret " + user["original_request"],
            )
        if ordinal in self.outer_json_invalid_at:
            return FakeResponse(b"{")
        if ordinal in self.shape_invalid_at:
            return FakeResponse(b"{}")
        if ordinal in self.inner_json_invalid_at:
            content = "{"
        elif ordinal in self.schema_invalid_at:
            content = json.dumps({"unexpected": True}, separators=(",", ":"))
        else:
            content = json.dumps(
                direct_frame(len(user["segments"])),
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
        outer = {"choices": [{"message": {"content": content}}]}
        return FakeResponse(json.dumps(outer, separators=(",", ":")).encode("utf-8"))


def test_repo_reference_boundary(runner, tests: list[str]) -> None:
    temporary = Path(tempfile.mkdtemp(prefix=".v2656-ref-test-", dir=HERE))
    original_reader = runner._read_repo_relative_nofollow
    try:
        good = temporary / "good.json"
        good_bytes = b'{"live_authorization":false,"verdict":"STATIC_PASS"}'
        good.write_bytes(good_bytes)
        relative = good.relative_to(REPOSITORY).as_posix()
        reference = {"path": relative, "sha256": hashlib.sha256(good_bytes).hexdigest()}
        check(runner._repo_hash_reference(reference, "test") ["verdict"] == "STATIC_PASS", "good hash reference")

        link = temporary / "link.json"
        link.symlink_to(good.name)
        link_reference = {"path": link.relative_to(REPOSITORY).as_posix(), "sha256": reference["sha256"]}
        expect_raises(OSError, lambda: runner._repo_hash_reference(link_reference, "link"), "symlink")
        expect_raises(
            RuntimeError,
            lambda: runner._read_repo_relative_nofollow("../outside", 10),
            "nonmember path",
        )
        expect_raises(
            RuntimeError,
            lambda: runner._read_repo_relative_nofollow("/tmp/outside", 10),
            "absolute path",
        )

        oversized = temporary / "oversized.json"
        oversized.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
        oversized_reference = {
            "path": oversized.relative_to(REPOSITORY).as_posix(),
            "sha256": hashlib.sha256(oversized.read_bytes()).hexdigest(),
        }
        expect_raises(
            RuntimeError,
            lambda: runner._repo_hash_reference(oversized_reference, "oversized"),
            "oversized reference",
        )

        replacement = temporary / "replacement.json"
        replacement.write_bytes(b'{"different":true}')
        read_count = 0

        def read_then_replace(relative_name, maximum_size):
            nonlocal read_count
            read_count += 1
            snapshot = original_reader(relative_name, maximum_size)
            os.replace(replacement, good)
            return snapshot

        runner._read_repo_relative_nofollow = read_then_replace
        parsed = runner._repo_hash_reference(reference, "single_snapshot")
        check(parsed["verdict"] == "STATIC_PASS" and read_count == 1, "reference reopened after hash")
    finally:
        runner._read_repo_relative_nofollow = original_reader
        shutil.rmtree(temporary)
    tests.append("gate_reference_nofollow_bounded_single_snapshot")


def main() -> None:
    started = time.perf_counter()
    tests: list[str] = []
    runner = load_runner()
    check(
        runner.sys.executable == "/usr/bin/python3"
        and runner.sys.flags.isolated == 1
        and runner.sys.flags.dont_write_bytecode == 1,
        "selftest interpreter is not /usr/bin/python3 -I -B",
    )
    tests.append("isolated_pinned_interpreter_context")

    source = RUNNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    run_case_calls = [
        node for node in ast.walk(functions["_run_case"])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "call_once"
    ]
    call_once_transport = [
        node for node in ast.walk(functions["call_once"])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "_request_bytes_once"
    ]
    check(len(run_case_calls) == len(call_once_transport) == 1, "one-call source invariant")
    check("read_bytes(" not in source, "unbounded/reopened Path.read_bytes in runner")
    tests.append("one_call_zero_retry_and_single_snapshot_static")

    runner.verify_freeze()
    check(not runner.EXTERNAL_GATE_PATH.exists(), "external operational gate unexpectedly exists")
    expect_raises(
        RuntimeError,
        lambda: runner.verify_external_gate(ENDPOINT, Path("/tmp/v2656-forbidden.json")),
        "author checkpoint must block live",
    )
    tests.append("author_freeze_pass_external_gate_absent")

    first = runner.request_body_bytes("opaque-native-probe")
    second = runner.request_body_bytes("opaque-native-probe")
    check(first == second, "request bytes are not reproducible")
    request = json.loads(first)
    user = json.loads(request["messages"][1]["content"])
    check(user["segments"] == runner.unicode_segments("opaque-native-probe"), "segmentation drift")
    check(request["seed"] == 92 and request["temperature"] == 0, "determinism drift")
    tests.append("reproducible_compact_request_and_exact_uax_segmentation")

    post_paths = {pin[0] for pin in runner.POSTBATCH_EVIDENCE.values()}
    observed_postbatch_reads = []
    original_read_exact = runner._read_exact_file
    full_transport = FakeTransport()

    def read_spy(path, expected_sha, expected_size):
        if path in post_paths:
            observed_postbatch_reads.append((path, full_transport.post_calls))
        return original_read_exact(path, expected_sha, expected_size)

    runner._read_exact_file = read_spy
    full_started = time.perf_counter()
    try:
        full = runner._run_controls(ENDPOINT, full_transport, native_run=False)
    finally:
        runner._read_exact_file = original_read_exact
    full_elapsed_ms = (time.perf_counter() - full_started) * 1000
    check(
        full["status"] == "MODEL_BATCH_COMPLETE"
        and full["model_batch_complete"] is True
        and full["evaluation_status"] == "NOT_RUN"
        and full["accuracy_claimed"] is False
        and len(full["records"]) == 34,
        "fake 34 batch failed",
    )
    summary = full["summary"]
    counters = summary["inference_counters"]
    check(full_transport.get_calls == 1 and full_transport.post_calls == 34, "fake request count")
    check(
        summary["model_request_attempts"] == 34
        and summary["server_accepted_requests"] == 34
        and summary["decoded_chat_responses"] == 34
        and summary["model_evaluated_results"] == 34
        and counters["decoded_frames"] == counters["facade_evaluations"] == 34
        and counters["valid_cases"] == 34 and counters["invalid_cases"] == 0,
        "successful batch counters are not exact",
    )
    check(not observed_postbatch_reads, "live runner opened postbatch/gold evidence")
    batch_text = json.dumps(full, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    controls = runner._runtime_controls()
    check(
        all(item["query"] not in batch_text for item in controls)
        and "original_request" not in batch_text
        and "excerpt_redacted" not in batch_text
        and "message_redacted" not in batch_text,
        "complete model batch is not query-free/hash-only",
    )
    tests.append("fake_34_complete_exact_counters_zero_gold_reads_query_free")

    batch_bytes = (json.dumps(
        full, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True,
    ) + "\n").encode("utf-8")
    offline_evaluator = load_module(
        OFFLINE_EVALUATOR_PATH, "metnos_v2656_offline_evaluator_test",
    )
    evaluation = offline_evaluator.evaluate_batch_bytes(batch_bytes, "<selftest>")
    phase1_summary = evaluation["phase1"]["summary"]
    check(
        evaluation["status"] == "PHASE1_EVALUATED"
        and evaluation["batch_sha256"] == hashlib.sha256(batch_bytes).hexdigest()
        and phase1_summary["records"] == 34
        and phase1_summary["positive_total"] == 9
        and phase1_summary["negative_total"] == 25
        and evaluation["network_calls"] == evaluation["model_calls"] == 0,
        "separate offline Phase-1 evaluation failed",
    )
    tests.append("separate_offline_evaluator_phase1_metrics_and_batch_sha")

    pre_gold_reads = []
    original_fixed_artifact = offline_evaluator._fixed_artifact_bytes

    def evidence_spy(identity, pins, *, gold_allowed):
        pre_gold_reads.append((identity, gold_allowed))
        return original_fixed_artifact(identity, pins, gold_allowed=gold_allowed)

    malformed = copy.deepcopy(full)
    malformed["records"] = malformed["records"][:-1]
    malformed_bytes = json.dumps(malformed, sort_keys=True, separators=(",", ":")).encode()
    offline_evaluator._fixed_artifact_bytes = evidence_spy
    try:
        expect_raises(
            RuntimeError,
            lambda: offline_evaluator.evaluate_batch_bytes(malformed_bytes, "<malformed>"),
            "malformed batch before gold",
        )
    finally:
        offline_evaluator._fixed_artifact_bytes = original_fixed_artifact
    check(
        pre_gold_reads == [("runtime_controls34", False)],
        "malformed batch caused a gold/evaluator read",
    )
    tests.append("offline_evaluator_rejects_incomplete_batch_before_gold")

    original_batch_sha = hashlib.sha256(batch_bytes).hexdigest()

    def oracle_failure(identity, pins, *, gold_allowed):
        if identity == "phase1_fixture":
            raise RuntimeError("synthetic oracle read failure")
        return original_fixed_artifact(identity, pins, gold_allowed=gold_allowed)

    offline_evaluator._fixed_artifact_bytes = oracle_failure
    try:
        expect_raises(
            RuntimeError,
            lambda: offline_evaluator.evaluate_batch_bytes(batch_bytes, "<oracle-failure>"),
            "oracle failure",
        )
    finally:
        offline_evaluator._fixed_artifact_bytes = original_fixed_artifact
    check(hashlib.sha256(batch_bytes).hexdigest() == original_batch_sha, "oracle failure changed batch")
    tests.append("oracle_failure_does_not_change_or_lose_model_batch")

    with tempfile.TemporaryDirectory(prefix="metnos-v2656-evaluator-") as directory:
        batch_path = Path(directory) / "model_batch.json"
        evaluation_path = Path(directory) / "model_batch_evaluation.json"
        batch_path.write_bytes(batch_bytes)
        completed = subprocess.run(
            [
                "/usr/bin/python3", "-I", "-B", str(OFFLINE_EVALUATOR_PATH),
                "--batch", str(batch_path), "--output", str(evaluation_path),
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=REPOSITORY,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin"},
            check=False, timeout=60,
        )
        check(completed.returncode == 0 and not completed.stderr, "offline evaluator CLI failed")
        cli_evaluation = json.loads(evaluation_path.read_bytes())
        check(
            cli_evaluation["batch_sha256"] == original_batch_sha
            and hashlib.sha256(batch_path.read_bytes()).hexdigest() == original_batch_sha,
            "evaluator CLI changed or failed to bind batch",
        )
        bad_path = Path(directory) / "bad.json"
        bad_output = Path(directory) / "bad_evaluation.json"
        bad_path.write_bytes(malformed_bytes)
        rejected = subprocess.run(
            [
                "/usr/bin/python3", "-I", "-B", str(OFFLINE_EVALUATOR_PATH),
                "--batch", str(bad_path), "--output", str(bad_output),
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=REPOSITORY,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin"},
            check=False, timeout=60,
        )
        check(
            rejected.returncode != 0 and not bad_output.exists()
            and hashlib.sha256(bad_path.read_bytes()).hexdigest()
            == hashlib.sha256(malformed_bytes).hexdigest(),
            "failed evaluator CLI wrote output or changed batch",
        )
    tests.append("offline_evaluator_cli_exclusive_output_and_failure_recovery")

    mixed_transport = FakeTransport(schema_invalid_at={1}, inner_json_invalid_at={2})
    mixed = runner._run_controls(ENDPOINT, mixed_transport, native_run=False)
    mixed_counters = mixed["summary"]["inference_counters"]
    check(
        mixed["status"] == "MODEL_BATCH_COMPLETE" and len(mixed["records"]) == 34
        and mixed_transport.post_calls == 34
        and mixed_counters["model_request_attempts"] == 34
        and mixed_counters["server_accepted_requests"] == 34
        and mixed_counters["decoded_chat_responses"] == 34
        and mixed_counters["decoded_frames"] == 33
        and mixed_counters["facade_evaluations"] == 33
        and mixed_counters["evaluated_cases"] == 34
        and mixed_counters["valid_cases"] == 32
        and mixed_counters["invalid_cases"] == 2,
        "model-invalid results triggered fail-fast or blurred counters",
    )
    mixed_bytes = (json.dumps(
        mixed, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True,
    ) + "\n").encode("utf-8")
    mixed_evaluation = offline_evaluator.evaluate_batch_bytes(mixed_bytes, "<mixed-invalid>")
    check(
        mixed_evaluation["phase1"]["summary"]["records"] == 34
        and mixed_evaluation["phase1"]["summary"]["evidence_valid"] == 32
        and all(item["query"] not in mixed_bytes.decode("utf-8") for item in controls),
        "model-invalid query-free batch was not separately evaluable",
    )
    tests.append("model_json_and_schema_invalid_continue_full_batch")

    early_postbatch_reads = []
    failing_transport = FakeTransport(transport_fail_at={3})

    def failing_read_spy(path, expected_sha, expected_size):
        if path in post_paths:
            early_postbatch_reads.append((path, failing_transport.post_calls))
        return original_read_exact(path, expected_sha, expected_size)

    runner._read_exact_file = failing_read_spy
    try:
        failed = runner._run_controls(ENDPOINT, failing_transport, native_run=False)
    finally:
        runner._read_exact_file = original_read_exact
    failed_counters = failed["summary"]["inference_counters"]
    check(
        failed["status"] == "NOT_EVALUATED" and len(failed["records"]) == 3
        and failed["evaluation_status"] == "NOT_RUN" and not early_postbatch_reads
        and failed_counters["model_request_attempts"] == 3
        and failed_counters["server_accepted_requests"] == 2
        and failed_counters["decoded_chat_responses"] == 2
        and failed_counters["evaluated_cases"] == 2,
        "transport failure did not fail fast with exact counters",
    )
    diagnostic_text = json.dumps(failed["records"][-1]["result"]["diagnostic"])
    failed_query = runner._runtime_controls()[2]["query"]
    check(
        failed_query not in diagnostic_text and "secret" not in diagnostic_text
        and "message_redacted" not in diagnostic_text and "excerpt_redacted" not in diagnostic_text,
        "diagnostic leak",
    )
    tests.append("transport_failfast_no_postbatch_and_hash_only_diagnostics")

    local_transport = FakeTransport()
    original_run_case = runner._run_case
    local_calls = 0

    def unexpected_case(query, endpoint, opener):
        nonlocal local_calls
        local_calls += 1
        if local_calls == 4:
            raise ValueError("synthetic local failure with private query fragment")
        return original_run_case(query, endpoint, opener)

    runner._run_case = unexpected_case
    try:
        local_failure = runner._run_controls(ENDPOINT, local_transport, native_run=False)
    finally:
        runner._run_case = original_run_case
    check(
        local_failure["status"] == "NOT_EVALUATED"
        and len(local_failure["records"]) == 3
        and local_failure["abort"]["after_completed_records"] == 3
        and local_failure["abort"]["case_record_written"] is False
        and local_failure["summary"]["model_request_attempts"] == 3
        and local_transport.post_calls == 3
        and "private query fragment" not in json.dumps(local_failure),
        "unexpected local exception lost records or leaked text",
    )
    with tempfile.TemporaryDirectory(prefix="metnos-v2656-partial-") as directory:
        partial_path = Path(directory) / "partial.json"
        runner.write_json_exclusive_atomic(partial_path, local_failure)
        recovered = json.loads(partial_path.read_bytes())
        check(len(recovered["records"]) == 3, "partial batch was not recoverably persisted")
    tests.append("unexpected_case_exception_returns_and_persists_partial_batch")

    protocol_transport = FakeTransport(outer_json_invalid_at={3})
    protocol = runner._run_controls(ENDPOINT, protocol_transport, native_run=False)
    check(
        protocol["status"] == "NOT_EVALUATED"
        and protocol["summary"]["model_request_attempts"] == 3
        and protocol["summary"]["server_accepted_requests"] == 3
        and protocol["summary"]["decoded_chat_responses"] == 2
        and protocol["evaluation_status"] == "NOT_RUN",
        "outer protocol error was not distinct fail-fast",
    )
    tests.append("outer_protocol_error_failfast_distinct_from_model_invalid")

    preflight_failure = FakeTransport(preflight_failure=True)
    blocked = runner._run_controls(ENDPOINT, preflight_failure, native_run=False)
    check(
        blocked["status"] == "NOT_EVALUATED"
        and blocked["summary"]["model_request_attempts"] == 0
        and preflight_failure.get_calls == 1 and preflight_failure.post_calls == 0,
        "inline GET preflight did not block before POST",
    )
    tests.append("inline_get_preflight_consumes_zero_inference")

    request_object = runner.urllib.request.Request(ENDPOINT + "/probe", method="GET")
    oversized_body, oversized_diag = runner._request_bytes_once(
        request_object, timeout_s=1, success_limit=8,
        opener=lambda _request, timeout: FakeResponse(b"123456789"),
    )
    check(
        oversized_body is None and oversized_diag["error"] == "response_body_too_large"
        and oversized_diag["server_accepted_requests"] == 1,
        "accepted oversized response counter drift",
    )
    read_body, read_diag = runner._request_bytes_once(
        request_object, timeout_s=1, success_limit=8,
        opener=lambda _request, timeout: FakeResponse(
            b"", read_error=TimeoutError("synthetic body timeout"),
        ),
    )
    check(
        read_body is None and read_diag["error"] == "response_body_read_error"
        and read_diag["server_accepted_requests"] == 1,
        "accepted read-error counter drift",
    )
    tests.append("accepted_response_count_survives_body_failures")

    smoke_transport = FakeTransport()
    smoke = runner._transport_smoke(ENDPOINT, smoke_transport)
    runner._validate_external_preflight(
        smoke, ENDPOINT,
        now_unix_ns=smoke["completed_at_unix_ns"] + 1,
        now_monotonic_ns=smoke["completed_at_monotonic_ns"] + 1,
    )
    expect_raises(
        RuntimeError,
        lambda: runner._validate_external_preflight(
            smoke, "http://127.0.0.1:9999",
            now_unix_ns=smoke["completed_at_unix_ns"] + 1,
            now_monotonic_ns=smoke["completed_at_monotonic_ns"] + 1,
        ),
        "wrong endpoint",
    )
    stale = copy.deepcopy(smoke)
    stale["started_at_unix_ns"] -= runner.EXTERNAL_PREFLIGHT_MAX_AGE_NS + 1
    stale["completed_at_unix_ns"] -= runner.EXTERNAL_PREFLIGHT_MAX_AGE_NS + 1
    stale["started_at_monotonic_ns"] -= runner.EXTERNAL_PREFLIGHT_MAX_AGE_NS + 1
    stale["completed_at_monotonic_ns"] -= runner.EXTERNAL_PREFLIGHT_MAX_AGE_NS + 1
    expect_raises(
        RuntimeError,
        lambda: runner._validate_external_preflight(
            stale, ENDPOINT,
            now_unix_ns=smoke["completed_at_unix_ns"],
            now_monotonic_ns=smoke["completed_at_monotonic_ns"],
        ),
        "stale preflight",
    )
    wrong_context = copy.deepcopy(smoke)
    wrong_context["interpreter_context"]["isolated"] = False
    expect_raises(
        RuntimeError,
        lambda: runner._validate_external_preflight(
            wrong_context, ENDPOINT,
            now_unix_ns=smoke["completed_at_unix_ns"] + 1,
            now_monotonic_ns=smoke["completed_at_monotonic_ns"] + 1,
        ),
        "wrong interpreter context",
    )
    tests.append("external_preflight_endpoint_recency_and_context_fail_closed")

    raw_transport = FakeTransport()
    captured = {}
    prior_facade = runner._FACADE_MODULE

    def capture_evaluate(original_request, frame):
        captured["original_request"] = original_request
        captured["frame"] = frame
        return {"status": "evaluated_invalid", "stage": "schema", "codes": ["synthetic"]}

    runner._FACADE_MODULE = types.SimpleNamespace(evaluate=capture_evaluate)
    try:
        raw_result = runner._run_case("Where am I?", ENDPOINT, raw_transport)
    finally:
        runner._FACADE_MODULE = prior_facade
    check(
        raw_result["status"] == "evaluated_invalid"
        and captured["original_request"] == "Where am I?"
        and captured["frame"] == direct_frame(len(runner.unicode_segments("Where am I?"))),
        "raw decoded frame/original request were not handed directly to facade",
    )
    tests.append("raw_frame_and_original_request_handoff_to_v2655_facade")

    fragment_query = "Where is my private fragment?"
    fragment_transport = FakeTransport()

    def fragment_failure(_original_request, _frame):
        raise TypeError("private fragment? must never survive")

    runner._FACADE_MODULE = types.SimpleNamespace(evaluate=fragment_failure)
    try:
        fragment_result = runner._run_case(fragment_query, ENDPOINT, fragment_transport)
    finally:
        runner._FACADE_MODULE = prior_facade
    fragment_serialized = json.dumps(fragment_result, ensure_ascii=False)
    check(
        fragment_result["status"] == "evaluated_invalid"
        and "private fragment" not in fragment_serialized
        and "message_redacted" not in fragment_serialized,
        "facade exception persisted a query fragment",
    )
    tests.append("facade_invalid_exception_is_type_only_and_query_free")

    check(
        hashlib.sha256(COMPACT_FIXTURE_PATH.read_bytes()).hexdigest()
        == COMPACT_FIXTURE_SHA256,
        "compact structural fixture drift",
    )
    compact_fixture = json.loads(COMPACT_FIXTURE_PATH.read_bytes())
    positive = {item["id"]: item for item in compact_fixture["positive_controls"]}
    synthetic_original = " ".join(
        f"s{ordinal:03d}" for ordinal in range(1, compact_fixture["segment_count"] + 1)
    )
    for identity in ("P04_fanout_multi_action", "P05_multi_domain", "P08_typed_ambiguity"):
        outcome = runner._facade().evaluate(synthetic_original, positive[identity]["compact_frame"])
        check(outcome["status"] == "evaluated_valid", f"structural replay failed: {identity}")
    tests.append("facade_multi_action_multi_domain_and_typed_ambiguity_replay")

    test_repo_reference_boundary(runner, tests)

    with tempfile.TemporaryDirectory(prefix="metnos-v2656-output-") as directory:
        output = Path(directory) / "result.json"
        runner.write_json_exclusive_atomic(output, {"status": "offline"})
        check(json.loads(output.read_bytes()) == {"status": "offline"}, "atomic output content")
        expect_raises(
            FileExistsError,
            lambda: runner.write_json_exclusive_atomic(output, {"status": "overwrite"}),
            "no-clobber output",
        )
        check(not list(Path(directory).glob(".*.tmp")), "atomic temp file leaked")
    tests.append("exclusive_atomic_no_clobber_output")

    check(not list(HERE.rglob("__pycache__")), "candidate __pycache__ exists")
    elapsed_ms = (time.perf_counter() - started) * 1000
    result = {
        "version": "metnos.v26.5.6-offline-author-selftest/1.0",
        "status": "PASS", "tests": len(tests), "passed": len(tests),
        "test_names": tests,
        "network_calls": 0, "model_calls": 0,
        "fake_transport_calls": {
            "complete_batch_get": full_transport.get_calls,
            "complete_batch_post": full_transport.post_calls,
        },
        "timing": {
            "full_fake_34_batch_ms": full_elapsed_ms,
            "full_fake_median_case_ms": full["summary"]["latency_median_ms"],
            "selftest_total_ms": elapsed_ms,
        },
        "runner_sha256": hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest(),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
