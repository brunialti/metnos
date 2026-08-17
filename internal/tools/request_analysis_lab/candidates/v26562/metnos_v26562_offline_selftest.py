#!/usr/bin/env python3
"""Offline author tests for V26.5.6.2; every HTTP exchange is in memory."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


HERE = Path(__file__).resolve(strict=True).parent
REPOSITORY = HERE.parents[4]
RUNNER_PATH = HERE / "metnos_v26562_k1_runner.py"
EVALUATOR_PATH = HERE / "metnos_v26562_offline_evaluator.py"
BASELINE_SELFTEST = (
    REPOSITORY / "internal/tools/request_analysis_lab/candidates/v26561/"
    "metnos_v26561_offline_selftest.py"
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


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None, f"loader unavailable: {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(
        self, body: bytes, *, status: int = 200, effective_url: str | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.effective_url = effective_url
        self.headers = {"Content-Length": str(len(body))}
        self.closed = False

    def getcode(self):
        return self.status

    def geturl(self):
        return self.effective_url

    def read(self, limit: int = -1):
        return self.body if limit < 0 else self.body[:limit]

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


class CountedFakeTransport:
    """Frozen transport protocol fake; it never opens a socket."""

    def __init__(
        self, *, two_opens: bool = False, proxy_destination: bool = False,
        redirect_response: bool = False, inner_json_invalid_at=(),
        schema_invalid_at=(),
    ) -> None:
        self.open_count = 0
        self.last_effective_url: str | None = None
        self.get_calls = 0
        self.post_calls = 0
        self.two_opens = two_opens
        self.proxy_destination = proxy_destination
        self.redirect_response = redirect_response
        self.inner_json_invalid_at = set(inner_json_invalid_at)
        self.schema_invalid_at = set(schema_invalid_at)

    def open_once(self, request, timeout):
        del timeout
        self.open_count += 2 if self.two_opens else 1
        self.last_effective_url = (
            "http://192.0.2.9/proxy" if self.proxy_destination else request.full_url
        )
        response_url = (
            "http://127.0.0.1:8081/redirected"
            if self.redirect_response else request.full_url
        )
        if request.get_method() == "GET":
            self.get_calls += 1
            return FakeResponse(b'{"data":[]}', effective_url=response_url)
        self.post_calls += 1
        request_payload = json.loads(request.data)
        user_payload = json.loads(request_payload["messages"][1]["content"])
        if self.post_calls in self.inner_json_invalid_at:
            content = "{"
        else:
            compact = (
                {"unexpected": True}
                if self.post_calls in self.schema_invalid_at
                else direct_frame(len(user_payload["segments"]))
            )
            content = json.dumps(
                compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
        outer = {"choices": [{"message": {"content": content}}]}
        return FakeResponse(
            json.dumps(outer, separators=(",", ":")).encode("utf-8"),
            effective_url=response_url,
        )


def run_cli(path: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/usr/bin/python3", "-I", "-B", str(path), *arguments],
        cwd=REPOSITORY, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )


def main() -> None:
    started = time.perf_counter()
    check(sys.executable == "/usr/bin/python3", "wrong selftest interpreter")
    check(sys.flags.isolated == 1 and sys.flags.dont_write_bytecode == 1,
          "selftest requires -I -B")
    runner = load_module(RUNNER_PATH, "metnos_v26562_runner_selftest")
    evaluator = load_module(EVALUATOR_PATH, "metnos_v26562_evaluator_selftest")
    tests: list[str] = []

    baseline = run_cli(BASELINE_SELFTEST)
    check(baseline.returncode == 0 and not baseline.stderr, "V26561 baseline failed")
    baseline_result = json.loads(baseline.stdout)
    check(
        baseline_result.get("status") == "PASS"
        and baseline_result.get("total_asserted_tests") == 34
        and baseline_result.get("network_calls") == 0
        and baseline_result.get("model_calls") == 0,
        "V26561 inherited 34/34 proof failed",
    )
    tests.append("inherited_v26561_34_of_34")

    check(runner.verify_freeze()["status"] == "PRE_INFRA_REVIEW",
          "author freeze failed")
    check(not runner.PREFLIGHT_GATE_PATH.exists(), "preflight gate unexpectedly exists")
    check(not runner.EXTERNAL_GATE_PATH.exists(), "external gate unexpectedly exists")
    tests.append("author_freeze_pass_both_operational_gates_absent")

    gate_test_parent = (
        REPOSITORY / "internal/tools/request_analysis_lab/candidates"
    )
    with tempfile.TemporaryDirectory(
        prefix=".v26562-gate-reachability-", dir=gate_test_parent,
    ) as temporary:
        root = Path(temporary)
        preflight_gate = root / "synthetic_preflight_gate.json"
        external_gate = root / "synthetic_external_gate.json"
        preflight_gate.write_text("{}\n", encoding="utf-8")
        external_gate.write_text("{}\n", encoding="utf-8")
        original_preflight_path = runner.PREFLIGHT_GATE_PATH
        original_external_path = runner.EXTERNAL_GATE_PATH
        runner.PREFLIGHT_GATE_PATH = preflight_gate
        runner.EXTERNAL_GATE_PATH = external_gate
        try:
            runner._verify_author_bytes()
            expect_raises(
                RuntimeError, runner._verify_author_gates_absent,
                "author-only gate-absence policy",
            )
            preflight_error = expect_raises(
                RuntimeError,
                lambda: runner._verify_preflight_gate_snapshot(
                    ENDPOINT, root / "probe_preflight.json",
                ),
                "present preflight gate validation",
            )
            external_error = expect_raises(
                RuntimeError,
                lambda: runner._verify_external_gate_snapshot(
                    ENDPOINT, root / "probe_batch.json",
                ),
                "present external gate validation",
            )
            check("envelope is not closed" in str(preflight_error),
                  "preflight verifier did not reach present gate bytes")
            check("envelope is not closed" in str(external_error),
                  "external verifier did not reach present gate bytes")
        finally:
            runner.PREFLIGHT_GATE_PATH = original_preflight_path
            runner.EXTERNAL_GATE_PATH = original_external_path
    tests.append("operational_gate_validation_reachable_author_absence_separate")

    check(runner.MODEL_BATCH_MAX_SERIALIZED_BYTES == 7_609_728,
          "batch cap drift")
    transport = runner._FrozenNativeTransport()
    proxy_handlers = [
        item for item in transport._director.handlers
        if isinstance(item, runner.urllib.request.ProxyHandler)
    ]
    check(not proxy_handlers, "native transport inherited a proxy handler")
    check(any(isinstance(item, runner._DenyRedirectHandler)
              for item in transport._director.handlers),
          "native redirect denial missing")
    tests.append("native_no_proxy_no_redirect_static")

    for invalid in (
        "http://localhost:8080", "http://127.0.0.2:8080",
        "https://127.0.0.1:8080", "http://user@127.0.0.1:8080",
    ):
        check(not runner._valid_local_endpoint(invalid), f"endpoint accepted: {invalid}")
    check(runner._valid_local_endpoint(ENDPOINT), "literal IPv4 loopback rejected")
    check(runner._valid_local_endpoint("http://[::1]:8080"),
          "literal IPv6 loopback rejected")
    tests.append("literal_loopback_endpoint_only")

    for label, fake, expected_error, expected_attempts in (
        ("two", CountedFakeTransport(two_opens=True),
         "transport_open_count_or_destination_violation", 2),
        ("proxy", CountedFakeTransport(proxy_destination=True),
         "transport_open_count_or_destination_violation", 1),
        ("redirect", CountedFakeTransport(redirect_response=True),
         "transport_redirect_violation", 1),
    ):
        body, diagnostic = runner._request_bytes_once(
            runner.urllib.request.Request(ENDPOINT + "/v1/models", method="GET"),
            timeout_s=1, success_limit=1024, opener=fake,
        )
        check(body is None and diagnostic["error"] == expected_error,
              f"{label} boundary did not fail closed")
        check(diagnostic["socket_attempts"] == expected_attempts,
              f"{label} actual open counter mismatch")
    tests.append("fake_two_open_proxy_redirect_rejected")

    with tempfile.TemporaryDirectory(prefix="v26562-anchor-") as temporary:
        root = Path(temporary)
        original = root / "original"
        moved = root / "moved"
        original.mkdir()
        anchor = runner._OutputAnchor(original / "batch.json", ".json")
        original.rename(moved)
        original.mkdir()
        try:
            runner.write_json_exclusive_atomic(anchor, {"anchored": True})
        finally:
            anchor.close()
        check((moved / "batch.json").is_file(), "stable dirfd did not retain target")
        check(not (original / "batch.json").exists(), "replacement parent was used")
    tests.append("output_parent_openat_anchor_survives_path_swap")

    with tempfile.TemporaryDirectory(prefix="v26562-preflight-") as temporary:
        output = Path(temporary) / "blocked_preflight.json"
        fake = CountedFakeTransport()
        expect_raises(
            RuntimeError,
            lambda: runner.run_preflight(ENDPOINT, output, fake),
            "preflight without preliminary gate",
        )
        check(fake.open_count == 0 and not output.exists(),
              "absent preliminary gate allowed transport/output")
        original_verifier = runner._verify_preflight_gate_snapshot
        runner._verify_preflight_gate_snapshot = lambda endpoint, path: ({
            "authority": "synthetic_offline_selftest_only",
        }, "0" * 64)
        try:
            produced = Path(temporary) / "synthetic_preflight.json"
            producer_fake = CountedFakeTransport()
            result = runner.run_preflight(ENDPOINT, produced, producer_fake)
            check(result["status"] == "PASS" and produced.is_file(),
                  "preflight producer did not write atomically")
            expect_raises(
                FileExistsError,
                lambda: runner.run_preflight(ENDPOINT, produced, producer_fake),
                "preflight no-clobber",
            )
            check(producer_fake.open_count == 1, "no-clobber happened after transport")
        finally:
            runner._verify_preflight_gate_snapshot = original_verifier
    tests.append("preflight_producer_requires_separate_gate_and_no_clobber")

    with tempfile.TemporaryDirectory(prefix="v26562-cli-") as temporary:
        output = Path(temporary) / "cli_preflight.json"
        completed = run_cli(
            RUNNER_PATH, "--preflight", "--endpoint", ENDPOINT,
            "--output", str(output),
        )
        check(completed.returncode == 2 and not completed.stdout and not output.exists(),
              "blocked preflight CLI contract failed")
        line = completed.stderr.decode("utf-8")
        check("Traceback" not in line and line.count("\n") == 1,
              "runner CLI leaked traceback/multiline diagnostics")
        cli_error = json.loads(line)
        check(cli_error.get("status") == "CLI_ERROR"
              and cli_error.get("traceback_emitted") is False,
              "runner CLI error shape failed")

        malformed = Path(temporary) / "malformed.json"
        malformed.write_bytes(b"{")
        evaluation = Path(temporary) / "malformed_evaluation.json"
        completed = run_cli(
            EVALUATOR_PATH, "--batch", str(malformed), "--output", str(evaluation),
        )
        check(completed.returncode == 2 and not completed.stdout
              and not evaluation.exists(), "evaluator CLI failure contract failed")
        line = completed.stderr.decode("utf-8")
        check("Traceback" not in line and line.count("\n") == 1,
              "evaluator CLI leaked traceback/multiline diagnostics")
        cli_error = json.loads(line)
        check(cli_error.get("status") == "CLI_ERROR"
              and cli_error.get("traceback_emitted") is False,
              "evaluator CLI error shape failed")
    tests.append("runner_and_evaluator_cli_errors_are_sanitized")

    nonisolated_probe = (
        "import importlib.util,json;"
        f"p={str(EVALUATOR_PATH)!r};"
        "s=importlib.util.spec_from_file_location('v26562_nonisolated',p);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "pins=json.load(open(m.FREEZE_PATH,encoding='utf-8'))['artifact_pins'];"
        "\ntry:m._load_pre_gold_validator(pins)\n"
        "except RuntimeError as e:print(str(e))\n"
        "else:raise SystemExit(9)"
    )
    nonisolated = subprocess.run(
        ["/usr/bin/python3", "-B", "-c", nonisolated_probe],
        cwd=REPOSITORY, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    check(
        nonisolated.returncode == 0 and not nonisolated.stderr
        and b"requires /usr/bin/python3 -I -B" in nonisolated.stdout,
        "non-isolated evaluator context reached dependency import/compile",
    )
    tests.append("evaluator_context_rejected_before_batch_or_exec")

    fake = CountedFakeTransport()
    batch = runner._run_controls(ENDPOINT, fake, native_run=False)
    check(batch["status"] == "MODEL_BATCH_COMPLETE" and len(batch["records"]) == 34,
          "new fake 34 batch failed")
    check(fake.get_calls == 1 and fake.post_calls == 34 and fake.open_count == 35,
          "new fake transport accounting failed")
    serialized = json.dumps(
        batch, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    check(len(serialized) + 1 <= runner.MODEL_BATCH_MAX_SERIALIZED_BYTES,
          "complete batch exceeds explicit cap")
    evaluation = evaluator.evaluate_batch_bytes(serialized, "<offline-selftest>")
    check(evaluation["status"] == "PHASE1_EVALUATED"
          and evaluation["network_calls"] == evaluation["model_calls"] == 0,
          "separate evaluator failed on new complete batch")
    tests.append("fake_34_and_separate_evaluator_pass")

    _freeze, evaluator_pins = evaluator._checkpoint_before_gold(batch)
    original_fixed_for_membership = evaluator._fixed_artifact_bytes
    manifest = json.loads(original_fixed_for_membership(
        "python_dependency_tree", evaluator_pins, gold_allowed=False,
    ))
    forged_manifest = copy.deepcopy(manifest)
    forged_package = forged_manifest["packages"][0]
    forged_package["files"] = forged_package["files"][1:]
    forged_package["file_count"] = len(forged_package["files"])
    forged_package["tree_sha256"] = hashlib.sha256(json.dumps(
        forged_package["files"], ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    forged_bytes = json.dumps(
        forged_manifest, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    compile_called = False

    def forged_fixed(identity, pins, *, gold_allowed):
        if identity == "python_dependency_tree":
            return forged_bytes
        return original_fixed_for_membership(
            identity, pins, gold_allowed=gold_allowed,
        )

    def forbidden_compile(*args, **kwargs):
        nonlocal compile_called
        compile_called = True
        raise AssertionError("compile reached after dependency membership mutation")

    evaluator._fixed_artifact_bytes = forged_fixed
    evaluator.compile = forbidden_compile
    try:
        membership_error = expect_raises(
            RuntimeError,
            lambda: evaluator._load_pre_gold_validator(evaluator_pins),
            "forged dependency membership",
        )
    finally:
        evaluator._fixed_artifact_bytes = original_fixed_for_membership
        del evaluator.compile
    check("membership changed" in str(membership_error) and not compile_called,
          "dependency membership mutation reached compile/exec")
    tests.append("dependency_tree_membership_recomputed_before_compile_exec")

    invalid_fake = CountedFakeTransport(
        inner_json_invalid_at={1}, schema_invalid_at={2},
    )
    invalid_batch = runner._run_controls(ENDPOINT, invalid_fake, native_run=False)
    check(invalid_batch["status"] == "MODEL_BATCH_COMPLETE",
          "model-invalid cases incorrectly stopped the batch")
    check(
        invalid_batch["records"][0]["result"]["failure_class"]
        == "model_content_json_invalid"
        and invalid_batch["records"][1]["result"]["failure_class"]
        == "model_frame_schema_adapter_or_semantic_invalid",
        "model-invalid status branches drifted",
    )
    invalid_serialized = json.dumps(
        invalid_batch, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    invalid_evaluation = evaluator.evaluate_batch_bytes(
        invalid_serialized, "<offline-invalid-selftest>",
    )
    check(invalid_evaluation["status"] == "PHASE1_EVALUATED",
          "pre-gold exact invalid status shapes rejected genuine model outputs")
    tests.append("pre_gold_exact_valid_and_two_invalid_status_shapes")

    original_fixed = evaluator._fixed_artifact_bytes
    for mutation_name, mutate in (
        ("empty_expanded_frame", lambda value: value["records"][0]["result"].__setitem__(
            "expanded_frame", {},
        )),
        ("extra_result_field", lambda value: value["records"][0]["result"].__setitem__(
            "unexpected", True,
        )),
        ("record_diagnostic_socket_mismatch", lambda value: value["records"][0][
            "result"
        ]["diagnostic"].__setitem__("socket_attempts", 2)),
        ("record_counter_boolean", lambda value: value["records"][0]["result"][
            "counters"
        ].__setitem__("socket_attempts", True)),
        ("preflight_counter_mismatch", lambda value: value[
            "inline_transport_preflight"
        ]["counters"].__setitem__("socket_attempts", 2)),
        ("preflight_diagnostic_and_counter_two", lambda value: (
            value["inline_transport_preflight"]["diagnostic"].__setitem__(
                "socket_attempts", 2,
            ),
            value["inline_transport_preflight"]["counters"].__setitem__(
                "socket_attempts", 2,
            ),
        )),
        ("summary_total_attempts", lambda value: value["summary"].__setitem__(
            "total_transport_attempts", 999,
        )),
        ("summary_latency_type", lambda value: value["summary"].__setitem__(
            "latency_min_ms", "0.0",
        )),
        ("summary_latency_not_derived", lambda value: value["summary"].__setitem__(
            "latency_max_ms", value["summary"]["latency_max_ms"] + 1.0,
        )),
    ):
        mutated = copy.deepcopy(batch)
        mutate(mutated)
        reads: list[str] = []

        def spy(identity, pins, *, gold_allowed):
            reads.append(identity)
            return original_fixed(identity, pins, gold_allowed=gold_allowed)

        evaluator._fixed_artifact_bytes = spy
        try:
            expect_raises(
                RuntimeError,
                lambda: evaluator.evaluate_batch_bytes(json.dumps(
                    mutated, ensure_ascii=False, allow_nan=False,
                    sort_keys=True, separators=(",", ":"),
                ).encode("utf-8"), mutation_name),
                mutation_name,
            )
        finally:
            evaluator._fixed_artifact_bytes = original_fixed
        check(not set(reads).intersection(evaluator.GOLD_IDENTITIES),
              f"{mutation_name} opened gold before rejection")
    tests.append("pre_gold_frame_counter_preflight_summary_mutations_zero_gold_reads")

    with tempfile.TemporaryDirectory(prefix="v26562-cap-") as temporary:
        root = Path(temporary)
        small = {"unicode": "é\\n\"", "keys": ["a", "b"]}
        exact = runner._canonical_json_size(small, 1000) + 1
        anchor = runner._OutputAnchor(root / "under.json", ".json")
        try:
            runner.write_json_exclusive_atomic(anchor, small, maximum_bytes=exact)
        finally:
            anchor.close()
        anchor = runner._OutputAnchor(root / "over.json", ".json")
        try:
            expect_raises(
                ValueError,
                lambda: runner.write_json_exclusive_atomic(
                    anchor, small, maximum_bytes=exact - 1,
                ),
                "exact boundary over",
            )
        finally:
            anchor.close()
        shared = "x" * 2048
        oversized = {"many_shared_strings": [shared] * 4000}
        original_dumps = runner.json.dumps
        runner.json.dumps = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("large json.dumps allocation reached")
        )
        anchor = runner._OutputAnchor(root / "large.json", ".json")
        try:
            expect_raises(
                ValueError,
                lambda: runner.write_json_exclusive_atomic(anchor, oversized),
                "pre-encoding batch cap",
            )
        finally:
            anchor.close()
            runner.json.dumps = original_dumps
        check(not (root / "large.json").exists(), "oversized batch was written")
    tests.append("exact_batch_cap_precedes_large_encoding")

    pycache = list(HERE.rglob("__pycache__")) + list(HERE.rglob("*.pyc"))
    check(not pycache, f"candidate contains bytecode cache: {pycache}")
    tests.append("candidate_has_no_bytecode_cache")

    result = {
        "version": "metnos.v26.5.6.2-offline-author-selftest/1.0",
        "status": "PASS",
        "inherited_v26561_passed": 34,
        "new_tests": len(tests),
        "new_passed": len(tests),
        "total_asserted_tests": 34 + len(tests),
        "test_names": tests,
        "fake_transport_calls": {
            "complete_batch_get": fake.get_calls,
            "complete_batch_post": fake.post_calls,
            "complete_batch_actual_opens": fake.open_count,
        },
        "batch_serialized_bytes_with_newline": len(serialized) + 1,
        "batch_cap_bytes": runner.MODEL_BATCH_MAX_SERIALIZED_BYTES,
        "network_calls": 0,
        "model_calls": 0,
        "runner_sha256": hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest(),
        "offline_evaluator_sha256": hashlib.sha256(
            EVALUATOR_PATH.read_bytes(),
        ).hexdigest(),
        "selftest_total_ms": (time.perf_counter() - started) * 1000,
    }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
