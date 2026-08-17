#!/usr/bin/env python3
"""Offline adversarial probe for the V26.4.1 transport-only runner delta.

All URL opens are monkeypatched.  This script performs zero network/model
calls and reads no candidate output.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch


RUNNER_PATH = Path("/tmp/metnos_v2641_typed_phase1_runner.py")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("metnos_v2641_infra_target", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load V26.4.1 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_runner()
tests: list[dict[str, Any]] = []


def check(test_id: str, condition: bool, detail: str = "") -> None:
    tests.append({"id": test_id, "pass": bool(condition), "detail": detail if not condition else ""})


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None):
        super().__init__(body)
        self._status = status
        self.headers = headers or {"Content-Length": str(len(body)), "Content-Type": "application/json"}

    def getcode(self) -> int:
        return self._status


class RaisingResponse(FakeResponse):
    def read(self, size: int = -1) -> bytes:
        raise TimeoutError("response read timeout")


def request() -> urllib.request.Request:
    return urllib.request.Request("http://127.0.0.1:8080/v1/models", method="GET")


def envelope(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def success_diagnostic() -> dict[str, Any]:
    return {
        "error": "", "phase": "complete", "socket_attempts": 1,
        "http_responses": 1, "server_accepted_requests": 1,
        "request_reached_server": True, "http_status": 200,
        "response_body": None, "exception_chain": [], "latency_ms": 1.0,
        "response_json_documents_decoded": 1,
        "decoded_chat_responses": 1, "decoded_frames": 1,
    }


# The semantic/model-facing artifacts and generated request contract stay exact.
for path, expected in runner.SEMANTIC_PARENT_HASHES.items():
    check(f"semantic_parent_hash:{path.name}", path.is_file() and sha(path) == expected)
generated_schema = json.dumps(runner.live_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
check("schema_generated_bytes_unchanged", generated_schema.encode() == runner.SCHEMA_PATH.read_bytes())
check("prompt_generated_bytes_unchanged", runner.system_prompt().encode() == runner.PROMPT_PATH.read_bytes())
parent_freeze = json.loads(runner.PARENT_FREEZE_PATH.read_text())
derived = runner._derived_freeze_fields()
for key in (
    "segmenter_source_sha256", "schema_prompt_bundle_sha256", "validator_bundle_sha256",
    "classifier_bundle_sha256", "evaluator_bundle_sha256",
    "request_body_probe_sha256", "registry_canonical_sha256",
):
    check(f"semantic_source_bundle_unchanged:{key}", derived[key] == parent_freeze[key])


# Local sandbox EPERM remains a transport failure with its complete cause chain.
eperm = urllib.error.URLError(PermissionError(1, "Operation not permitted"))
with patch.object(urllib.request, "urlopen", side_effect=eperm):
    body, diag = runner._request_bytes_once(request(), timeout_s=1, success_limit=1024)
check("eperm_no_body", body is None)
check("eperm_classified_transport", diag["error"] == "transport_error" and diag["phase"] == "transport")
check("eperm_exception_preserved", diag.get("exception_type") == "URLError" and diag.get("cause_type") == "PermissionError")
check("eperm_errno_preserved", diag.get("errno") == 1)
check("eperm_counters", diag["socket_attempts"] == 1 and diag["http_responses"] == 0 and not diag["request_reached_server"])


# HTTP status/body are distinct and the persisted excerpt is redacted.
secret_body = b'{"original_request":"DO_NOT_LEAK","message":"Bearer TOPSECRET password=HUNTER2"}'
http_error = urllib.error.HTTPError(
    request().full_url, 422, "unprocessable", {"Content-Length": str(len(secret_body))},
    io.BytesIO(secret_body),
)
with patch.object(urllib.request, "urlopen", side_effect=http_error):
    body, diag = runner._request_bytes_once(request(), timeout_s=1, success_limit=1024)
excerpt = diag["response_body"].get("excerpt_redacted", "")
check("http_error_classified", body is None and diag["error"] == "http_error" and diag["http_status"] == 422)
check("http_body_hash_complete", diag["response_body"]["body_sha256"] == hashlib.sha256(secret_body).hexdigest())
check("http_body_redacted", all(canary not in excerpt for canary in ("DO_NOT_LEAK", "TOPSECRET", "HUNTER2")), excerpt)
check("http_counters", diag["http_responses"] == 1 and diag["server_accepted_requests"] == 0 and diag["request_reached_server"])
private_query = "the exact private words must disappear"
echo_body = json.dumps({"message": f"failed for {private_query}"}).encode()
echo_error = urllib.error.HTTPError(
    request().full_url, 400, "bad request", {"Content-Length": str(len(echo_body))},
    io.BytesIO(echo_body),
)
with patch.object(urllib.request, "urlopen", side_effect=echo_error):
    frame, echo_diag = runner.call_once(private_query, "http://127.0.0.1:8080")
check("query_echo_redacted", private_query not in echo_diag["response_body"].get("excerpt_redacted", ""))


# Non-2xx returned as a regular response and bounded oversized bodies are explicit.
with patch.object(urllib.request, "urlopen", return_value=FakeResponse(b'{"error":"x"}', 503)):
    body, diag = runner._request_bytes_once(request(), timeout_s=1, success_limit=1024)
check("regular_non2xx", body is None and diag["error"] == "http_status_error" and diag["http_status"] == 503)
with patch.object(urllib.request, "urlopen", return_value=FakeResponse(b"01234567890")):
    body, diag = runner._request_bytes_once(request(), timeout_s=1, success_limit=10)
check("oversized_bounded", body is None and diag["error"] == "response_body_too_large" and diag["response_body"]["captured_bytes"] == 10 and diag["server_accepted_requests"] == 1)
check("oversized_hash_not_misrepresented", diag["response_body"]["body_complete"] is False and diag["response_body"]["body_sha256"] is None)
with patch.object(urllib.request, "urlopen", return_value=RaisingResponse(b"")):
    body, diag = runner._request_bytes_once(request(), timeout_s=1, success_limit=10)
check("response_read_error_distinct", body is None and diag["error"] == "response_body_read_error" and diag["phase"] == "response_body" and diag["http_responses"] == 1 and diag["server_accepted_requests"] == 1)


# API envelope JSON, shape, model JSON and success are separate phases/counters.
with patch.object(urllib.request, "urlopen", return_value=FakeResponse(b"not-json secret=DO_NOT_LEAK")):
    frame, diag = runner.call_once("opaque", "http://127.0.0.1:8080")
check("api_json_error", not frame and diag["error"] == "api_response_json_error" and diag["response_json_documents_decoded"] == 0)
with patch.object(urllib.request, "urlopen", return_value=FakeResponse(b"{}")):
    frame, diag = runner.call_once("opaque", "http://127.0.0.1:8080")
check("api_shape_error", not frame and diag["error"] == "api_response_shape_error" and diag["response_json_documents_decoded"] == 1 and diag["decoded_chat_responses"] == 0)
with patch.object(urllib.request, "urlopen", return_value=FakeResponse(envelope("not-json"))):
    frame, diag = runner.call_once("opaque", "http://127.0.0.1:8080")
check("model_json_error", not frame and diag["error"] == "model_frame_json_error" and diag["decoded_chat_responses"] == 1 and diag["decoded_frames"] == 0)
valid_frame = runner.canonical_direct_frame()[0]
with patch.object(urllib.request, "urlopen", return_value=FakeResponse(envelope(json.dumps(valid_frame)))):
    frame, diag = runner.call_once("opaque", "http://127.0.0.1:8080")
check("decode_success", frame == valid_frame and not diag["error"] and diag["decoded_frames"] == 1)


# Batch transport and schema failures both stop after exactly one attempted case.
original_verify_freeze = runner.verify_freeze
original_verify_gate = runner.verify_external_gate
original_call_once = runner.call_once
original_transport_smoke = runner._transport_smoke_once
original_freeze_path = runner.FREEZE_PATH
runner.FREEZE_PATH = runner.PARENT_FREEZE_PATH
runner.verify_freeze = lambda: None
runner.verify_external_gate = lambda endpoint=None: {
    "inline_transport_preflight_max_age_before_first_post_ms": 5000,
}
runner._transport_smoke_once = lambda endpoint: {
    "status": "PASS", "endpoint": endpoint, "method": "GET",
    "request_body_bytes": 0, "inference_calls": 0,
    "diagnostic": success_diagnostic(), "counters": runner._counters(success_diagnostic()),
    "authorizes_inference": False, "completed_monotonic_ns": runner.time.monotonic_ns(),
}
transport_calls = 0


def transport_failure(query: str, endpoint: str, seed: int = 92) -> tuple[dict[str, Any], dict[str, Any]]:
    global transport_calls
    transport_calls += 1
    diagnostic = success_diagnostic()
    diagnostic.update({
        "error": "transport_error", "phase": "transport", "http_responses": 0,
        "server_accepted_requests": 0, "response_json_documents_decoded": 0,
        "decoded_chat_responses": 0, "decoded_frames": 0, "request_reached_server": False,
        "http_status": None, "exception_type": "URLError", "cause_type": "PermissionError",
        "errno": 1, "exception_chain": [],
    })
    return {}, diagnostic


runner.call_once = transport_failure
transport_result = runner.run_controls("http://127.0.0.1:8080")
check("transport_failfast_one_attempt", transport_calls == 1 and len(transport_result["records"]) == 1)
check("transport_not_evaluated_no_accuracy", transport_result["status"] == "NOT_EVALUATED" and transport_result["accuracy_claimed"] is False and not transport_result["evaluation_records"])
check("transport_truthful_counters", transport_result["summary"]["inference_transport_and_decode_counters"]["socket_attempts"] == 1 and transport_result["summary"]["inference_transport_and_decode_counters"]["http_responses"] == 0)

schema_calls = 0


def invalid_frame(query: str, endpoint: str, seed: int = 92) -> tuple[dict[str, Any], dict[str, Any]]:
    global schema_calls
    schema_calls += 1
    return {}, success_diagnostic()


runner.call_once = invalid_frame
schema_result = runner.run_controls("http://127.0.0.1:8080")
check("schema_invalid_continues_all_cases", schema_calls == 34 and len(schema_result["records"]) == 34)
check("schema_invalid_is_model_evaluated", schema_result["status"] == "evaluated" and schema_result["summary"]["inference_transport_and_decode_counters"]["invalid_cases"] == 34 and schema_result["accuracy_claimed"] is True)
content_calls = 0


def invalid_model_json(query: str, endpoint: str, seed: int = 92) -> tuple[dict[str, Any], dict[str, Any]]:
    global content_calls
    content_calls += 1
    diagnostic = success_diagnostic()
    diagnostic.update({
        "error": "model_frame_json_error", "phase": "model_frame_json",
        "decoded_frames": 0,
    })
    return {}, diagnostic


runner.call_once = invalid_model_json
content_result = runner.run_controls("http://127.0.0.1:8080")
check("model_content_json_invalid_continues", content_calls == 34 and content_result["status"] == "evaluated")
check("model_content_invalid_exact_counters", content_result["summary"]["inference_transport_and_decode_counters"]["decoded_chat_responses"] == 34 and content_result["summary"]["inference_transport_and_decode_counters"]["decoded_frames"] == 0 and content_result["summary"]["inference_transport_and_decode_counters"]["invalid_cases"] == 34)
check("no_model_calls_claim", "model_calls" not in json.dumps(transport_result) and "model_calls" not in json.dumps(schema_result))
try:
    runner.evaluate_complete_records([], json.loads(runner.FIXTURE_PATH.read_text()))
    partial_rejected = False
except RuntimeError:
    partial_rejected = True
check("partial_accuracy_evaluation_rejected", partial_rejected)
blocked_post_calls = 0


def forbidden_post(query: str, endpoint: str, seed: int = 92) -> tuple[dict[str, Any], dict[str, Any]]:
    global blocked_post_calls
    blocked_post_calls += 1
    return {}, success_diagnostic()


runner.call_once = forbidden_post
failed_smoke_diag = success_diagnostic()
failed_smoke_diag.update({
    "error": "transport_error", "phase": "transport", "http_responses": 0,
    "server_accepted_requests": 0, "response_json_documents_decoded": 0,
    "decoded_chat_responses": 0, "decoded_frames": 0, "request_reached_server": False,
})
runner._transport_smoke_once = lambda endpoint: {
    "status": "NOT_EVALUATED", "endpoint": endpoint, "method": "GET",
    "request_body_bytes": 0, "inference_calls": 0,
    "diagnostic": failed_smoke_diag, "counters": runner._counters(failed_smoke_diag),
    "authorizes_inference": False, "completed_monotonic_ns": runner.time.monotonic_ns(),
}
smoke_blocked = runner.run_controls("http://127.0.0.1:8080")
check("inline_smoke_failure_blocks_all_posts", blocked_post_calls == 0 and not smoke_blocked["records"] and smoke_blocked["status"] == "NOT_EVALUATED")
check("inline_smoke_request_not_inference", smoke_blocked["summary"]["total_network_request_attempts"] == 1 and smoke_blocked["summary"]["inference_transport_and_decode_counters"]["socket_attempts"] == 0)
runner.call_once = original_call_once
runner.verify_freeze = original_verify_freeze
runner.verify_external_gate = original_verify_gate
runner._transport_smoke_once = original_transport_smoke
runner.FREEZE_PATH = original_freeze_path


# Both gates are new-version, hash-bound, exact-output and fail closed.
with tempfile.TemporaryDirectory(prefix="metnos-v2641-probe-") as temp_dir:
    review = Path(temp_dir) / "review.md"
    verifier = Path(temp_dir) / "verifier.py"
    review.write_text("independent offline review\n")
    verifier.write_text("independent offline verifier\n")
    ref = {"path": str(review), "sha": sha(review)}
    original_preflight_output_path = runner.PREFLIGHT_OUTPUT_PATH
    runner.PREFLIGHT_OUTPUT_PATH = Path(temp_dir) / "preflight-result.json"
    preflight_evidence = {
        "version": runner.VERSION,
        "status": "PASS",
        "freeze_sha256": sha(runner.FREEZE_PATH) if runner.FREEZE_PATH.exists() else "",
        "runner_sha256": sha(RUNNER_PATH),
        "endpoint": "http://127.0.0.1:8080", "method": "GET",
        "request_body_bytes": 0, "inference_calls": 0,
        "authorizes_inference": False, "completed_at_unix_ns": runner.time.time_ns(),
    }
    runner.PREFLIGHT_OUTPUT_PATH.write_text(json.dumps(preflight_evidence))
    external = {
        "lock_version": "metnos.v26.4.1-external-gate-lock/1.0",
        "status": "authorized_one_native_run",
        "authority": "independent_v2641_infra_oracle_freeze_review",
        "freeze_sha256": sha(runner.FREEZE_PATH) if runner.FREEZE_PATH.exists() else "",
        "runner_sha256": sha(RUNNER_PATH),
        "registry_sha256": runner.SEMANTIC_PARENT_HASHES[runner.REGISTRY_PATH],
        "schema_sha256": runner.SEMANTIC_PARENT_HASHES[runner.SCHEMA_PATH],
        "prompt_sha256": runner.SEMANTIC_PARENT_HASHES[runner.PROMPT_PATH],
        "fixture_sha256": runner.SEMANTIC_PARENT_HASHES[runner.FIXTURE_PATH],
        "parent_freeze_sha256": runner.SEMANTIC_PARENT_HASHES[runner.PARENT_FREEZE_PATH],
        "oracle_freeze_sha256": runner.EXPECTED_ORACLE_HASHES[runner.ORACLE_FREEZE_PATH],
        "authorized_case_count": 34, "authorized_repetitions": 1,
        "authorized_output_path": str(runner.MODEL_OUTPUT_PATH),
        "inference_attempt_limit": 34,
        "inline_transport_preflight_required": True,
        "inline_transport_preflight_method": "GET",
        "inline_transport_preflight_path": "/v1/models",
        "inline_transport_preflight_attempt_limit": 1,
        "inline_transport_preflight_max_age_before_first_post_ms": 5000,
        "external_transport_preflight_required": True,
        "external_transport_preflight_max_age_at_gate_verification_ms": 900000,
        "transport_preflight_result_path": str(runner.PREFLIGHT_OUTPUT_PATH),
        "transport_preflight_result_sha256": sha(runner.PREFLIGHT_OUTPUT_PATH),
        "retries": 0,
        "runtime_cutover_authorized": False, "network_calls_before_lock": 1,
        "transport_preflight_calls_before_lock": 1,
        "inference_calls_before_lock": 0,
        "candidate_outputs_read_before_lock": 0,
        "authorized_endpoint": "http://127.0.0.1:8080",
        "independent_review_path": ref["path"], "independent_review_sha256": ref["sha"],
        "oracle_review_path": ref["path"], "oracle_review_sha256": ref["sha"],
        "verifier_path": str(verifier), "verifier_sha256": sha(verifier),
    }
    check("new_external_gate_contract_valid", not runner._validate_external_gate_payload(external))
    for test_id, key, value in (
        ("old_external_gate_rejected", "lock_version", "metnos.v26.4-external-gate-lock/1.0"),
        ("external_runner_hash_mutation_rejected", "runner_sha256", "0" * 64),
        ("external_output_mutation_rejected", "authorized_output_path", "/tmp/wrong.json"),
        ("external_endpoint_mutation_rejected", "authorized_endpoint", "https://example.com"),
        ("external_inference_before_lock_rejected", "inference_calls_before_lock", 1),
        ("external_preflight_count_rejected", "transport_preflight_calls_before_lock", 0),
    ):
        mutated = copy.deepcopy(external); mutated[key] = value
        check(test_id, bool(runner._validate_external_gate_payload(mutated)))
    mutated = copy.deepcopy(external)
    mutated["inline_transport_preflight_required"] = False
    check("external_inline_preflight_required", bool(runner._validate_external_gate_payload(mutated)))
    mutated = copy.deepcopy(external); mutated["unbound_extra"] = True
    check("external_extra_key_rejected", bool(runner._validate_external_gate_payload(mutated)))
    mutated = copy.deepcopy(external); mutated["transport_preflight_result_sha256"] = "0" * 64
    check("external_smoke_hash_mutation_rejected", bool(runner._validate_external_gate_payload(mutated)))
    stale = copy.deepcopy(preflight_evidence)
    stale["completed_at_unix_ns"] = runner.time.time_ns() - 901_000_000_000
    runner.PREFLIGHT_OUTPUT_PATH.write_text(json.dumps(stale))
    stale_external = copy.deepcopy(external)
    stale_external["transport_preflight_result_sha256"] = sha(runner.PREFLIGHT_OUTPUT_PATH)
    check("external_stale_smoke_rejected", bool(runner._validate_external_gate_payload(stale_external)))
    runner.PREFLIGHT_OUTPUT_PATH.unlink()

    preflight = {
        "lock_version": "metnos.v26.4.1-transport-preflight-lock/1.0",
        "status": "authorized_one_inference_free_transport_preflight",
        "authority": "independent_v2641_transport_preflight_review",
        "freeze_sha256": sha(runner.FREEZE_PATH) if runner.FREEZE_PATH.exists() else "",
        "runner_sha256": sha(RUNNER_PATH),
        "authorized_method": "GET", "request_body_allowed": False,
        "authorized_attempts": 1, "inference_calls": 0,
        "authorized_output_path": str(runner.PREFLIGHT_OUTPUT_PATH),
        "network_calls_before_lock": 0,
        "authorized_endpoint": "http://127.0.0.1:8080",
        "authorized_url": "http://127.0.0.1:8080/v1/models",
        "independent_review_path": ref["path"], "independent_review_sha256": ref["sha"],
        "verifier_path": str(verifier), "verifier_sha256": sha(verifier),
    }
    check("preflight_gate_contract_valid", not runner._validate_preflight_gate_payload(preflight))
    for test_id, key, value in (
        ("preflight_post_rejected", "authorized_method", "POST"),
        ("preflight_body_rejected", "request_body_allowed", True),
        ("preflight_attempt_mutation_rejected", "authorized_attempts", 2),
        ("preflight_inference_mutation_rejected", "inference_calls", 1),
        ("preflight_url_mutation_rejected", "authorized_url", "http://127.0.0.1:8080/v1/chat/completions"),
    ):
        mutated = copy.deepcopy(preflight); mutated[key] = value
        check(test_id, bool(runner._validate_preflight_gate_payload(mutated)))
    mutated = copy.deepcopy(preflight); mutated["unbound_extra"] = True
    check("preflight_extra_key_rejected", bool(runner._validate_preflight_gate_payload(mutated)))
    runner.PREFLIGHT_OUTPUT_PATH = original_preflight_output_path


# The optional preflight itself is one mocked GET with no body and no inference.
seen_requests: list[urllib.request.Request] = []


def preflight_response(req: urllib.request.Request, timeout: float) -> FakeResponse:
    seen_requests.append(req)
    return FakeResponse(b'{"data":[]}')


original_verify_freeze = runner.verify_freeze
original_verify_preflight = runner.verify_preflight_gate
original_freeze_path = runner.FREEZE_PATH
runner.FREEZE_PATH = runner.PARENT_FREEZE_PATH
runner.verify_freeze = lambda: None
runner.verify_preflight_gate = lambda endpoint=None: {}
with patch.object(urllib.request, "urlopen", side_effect=preflight_response):
    preflight_result = runner.run_preflight("http://127.0.0.1:8080")
check("preflight_one_get", len(seen_requests) == 1 and seen_requests[0].get_method() == "GET")
check("preflight_no_body_no_completion", seen_requests[0].data is None and seen_requests[0].full_url.endswith("/v1/models"))
check("preflight_pass_not_authority", preflight_result["status"] == "PASS" and preflight_result["inference_calls"] == 0 and preflight_result["authorizes_inference"] is False)
check("preflight_counter_not_chat", preflight_result["counters"]["preflight_json_documents_decoded"] == 1 and preflight_result["counters"]["decoded_chat_responses"] == 0 and preflight_result["counters"]["evaluated_cases"] == 0)
with patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError(PermissionError(1, "Operation not permitted"))):
    failed_preflight = runner.run_preflight("http://127.0.0.1:8080")
check("preflight_eperm_diagnostic", failed_preflight["status"] == "NOT_EVALUATED" and failed_preflight["diagnostic"].get("cause_type") == "PermissionError")
runner.verify_freeze = original_verify_freeze
runner.verify_preflight_gate = original_verify_preflight
runner.FREEZE_PATH = original_freeze_path


# Gate-authorized outputs are published atomically and can never be overwritten.
with tempfile.TemporaryDirectory(prefix="metnos-v2641-output-") as temp_dir:
    output = Path(temp_dir) / "result.json"
    runner.write_json_exclusive_atomic(output, {"status": "first"})
    first_ok = json.loads(output.read_text()) == {"status": "first"}
    try:
        runner.write_json_exclusive_atomic(output, {"status": "second"})
        no_clobber = False
    except FileExistsError:
        no_clobber = json.loads(output.read_text()) == {"status": "first"}
    check("atomic_output_first_publish", first_ok)
    check("atomic_output_no_clobber", no_clobber)


# The archived failed K1 supports the infra, not model, hypothesis.
diagnosis = json.loads(runner.K1_DIAGNOSIS_JSON_PATH.read_text())
root = diagnosis["root_cause"]
behavior = diagnosis["runner_behavior"]
check("failed_k1_hash_chain", all(path.is_file() and sha(path) == expected for path, expected in runner.EXPECTED_K1_DIAGNOSIS_HASHES.items()))
check("failed_k1_confirmed_eperm", root["confidence"] == "confirmed" and root["errno"] == 1 and root["request_reached_server"] is False)
check("failed_k1_latency_signature", 9.0 < behavior["first_attempt_latency_ms"] < 11.0 and behavior["remaining_attempt_latency_min_ms"] < 0.1)
check("failed_k1_not_model", diagnosis["evidence"]["schema_or_model_was_exercised"] is False and behavior["server_accepted_model_requests"] == 0)


failed = [item["id"] for item in tests if not item["pass"]]
result = {
    "version": "metnos.v26.4.1-independent-infra-mutation-probe/1.0",
    "runner_sha256": sha(RUNNER_PATH),
    "network_calls": 0,
    "candidate_outputs_read": 0,
    "summary": {
        "tests": len(tests),
        "passed": len(tests) - len(failed),
        "failed": len(failed),
    },
    "failed": failed,
    "tests": tests,
}
print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
