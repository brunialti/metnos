#!/usr/bin/env python3
"""Offline author tests for the V26.5.6.9 clause-owned runner.

Every HTTP exchange is in memory: zero network calls, zero model calls, zero
gate created or consumed, zero live run. Frozen bytes are read only.

Inheritance note. The chain of runner self-tests inherits by RUNNING the
predecessor's suite. The direct predecessor V26.5.6.4 can no longer serve that
role: its suite asserts an author-time invariant that history has since
falsified -- "both operational gates must be absent" -- because those gates
were created, verified and consumed for the K1/34 live run of 9 August. The
last suite whose author-time state is still true is V26.5.6.3, and that is the
one inherited here (66 checks). The V26.5.6.4 checks that belong to the runner
rather than to its evaluator are re-implemented below against this bundle.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


HERE = Path(__file__).resolve(strict=True).parent
REPOSITORY = HERE.parents[4]
RUNNER_PATH = HERE / "metnos_v26569_k1_runner.py"
EVALUATOR_PATH = HERE / "metnos_v26569_offline_evaluator.py"
CONTRACT_DIR = HERE.parent / "v26568"
BASELINE_SELFTEST = (
    REPOSITORY / "internal/tools/request_analysis_lab/candidates/v26563/"
    "metnos_v26563_offline_selftest.py"
)
ENDPOINT = "http://127.0.0.1:8080"
MARKER = "REQUESTMATERIALMARKER"


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


def clause_owned_frame(segment_count: int) -> dict:
    """One schema-valid, semantically valid clause-owned frame."""
    return {
        "status": "supported",
        "clauses": [{
            "clause_start_segment_id": 1,
            "clause_end_segment_id": max(1, segment_count),
            "clause_role": "main_request",
            "clause_role_proof": {"kind": "discourse_structure"},
            "projection": {
                "relation": "spatial.located_at",
                "relation_proof": {"kind": "clause_construction"},
                "speech_act": "open_question",
                "speech_act_proof": {"kind": "clause_construction"},
                "arguments": [
                    {"kind": "bound", "ref": "actor.current",
                     "proof": {"kind": "predicate_morphology",
                               "predicate_segment_id": 1}},
                    {"kind": "unknown",
                     "proof": {"kind": "interrogative_construction"}},
                    {"kind": "bound", "ref": "time.current",
                     "proof": {"kind": "utterance_context"}},
                ],
            },
        }],
    }


def hostile_frame(segment_count: int) -> dict:
    """A frame that tries to smuggle request material through every string.

    Values, invented keys and proof kinds all carry the marker: nothing of it
    may survive into a batch that declares query_material_included=false.
    """
    frame = clause_owned_frame(segment_count)
    clause = frame["clauses"][0]
    clause[MARKER + "_key"] = MARKER
    clause["clause_role"] = MARKER
    clause["clause_role_proof"] = {"kind": MARKER}
    clause["projection"]["relation"] = MARKER
    clause["projection"]["speech_act"] = MARKER
    clause["projection"]["arguments"][0] = {"kind": MARKER, "ref": MARKER,
                                            "proof": {"kind": MARKER}}
    frame["status"] = MARKER
    return frame


def over_budget_frame(segment_count: int) -> dict:
    """Schema-valid, 8 clauses x 2 dependencies = 24 atoms over the bound 16."""
    end = max(1, segment_count)
    dependency = {
        "relation": "spatial.located_at",
        "relation_proof": {"kind": "relation_composition"},
        "arguments": [
            {"kind": "bound", "ref": "actor.current",
             "proof": {"kind": "predicate_morphology", "predicate_segment_id": 1}},
            {"kind": "output", "proof": {"kind": "relation_composition"}},
            {"kind": "bound", "ref": "time.current",
             "proof": {"kind": "utterance_context"}},
        ],
    }
    clause = {
        "clause_start_segment_id": 1,
        "clause_end_segment_id": end,
        "clause_role": "main_request",
        "clause_role_proof": {"kind": "discourse_structure"},
        "dependencies": [copy.deepcopy(dependency), copy.deepcopy(dependency)],
        "projection": {
            "relation": "spatial.near",
            "relation_proof": {"kind": "clause_construction"},
            "speech_act": "imperative",
            "speech_act_proof": {"kind": "clause_construction"},
            "arguments": [
                {"kind": "unknown",
                 "proof": {"kind": "interrogative_construction"}},
                {"kind": "from_prior_atom", "source_ordinal": 1,
                 "proof": {"kind": "relation_composition"}},
                {"kind": "bound", "ref": "time.current",
                 "proof": {"kind": "utterance_context"}},
            ],
        },
    }
    return {"status": "supported",
            "clauses": [copy.deepcopy(clause) for _ in range(8)]}


class CountedFakeTransport:
    """Frozen transport protocol fake; it never opens a socket."""

    def __init__(
        self, *, two_opens: bool = False, proxy_destination: bool = False,
        redirect_response: bool = False, inner_json_invalid_at=(),
        hostile_at=(), over_budget_at=(), semantic_break_at=(),
    ) -> None:
        self.open_count = 0
        self.last_effective_url: str | None = None
        self.get_calls = 0
        self.post_calls = 0
        self.two_opens = two_opens
        self.proxy_destination = proxy_destination
        self.redirect_response = redirect_response
        self.inner_json_invalid_at = set(inner_json_invalid_at)
        self.hostile_at = set(hostile_at)
        self.over_budget_at = set(over_budget_at)
        self.semantic_break_at = set(semantic_break_at)

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
        segment_count = len(user_payload["segments"])
        if self.post_calls in self.inner_json_invalid_at:
            content = "{"
        else:
            if self.post_calls in self.hostile_at:
                frame = hostile_frame(segment_count)
            elif self.post_calls in self.over_budget_at:
                frame = over_budget_frame(segment_count)
            else:
                frame = clause_owned_frame(segment_count)
                if self.post_calls in self.semantic_break_at:
                    # role/speech disagreement is now unrepresentable, so the
                    # break has to be one the schema cannot catch: an open
                    # question that asks nothing.
                    frame["clauses"][0]["projection"]["arguments"][1] = {
                        "kind": "bound", "ref": "place.explicit",
                        "proof": {"kind": "discourse_context"}}
            content = json.dumps(
                frame, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
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


def strings_of(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [item for value in node for item in strings_of(value)]
    if isinstance(node, dict):
        return [item for key, value in node.items()
                for item in ([key] + strings_of(value))]
    return []


def main() -> None:
    started = time.perf_counter()
    check(sys.executable == "/usr/bin/python3", "wrong selftest interpreter")
    check(sys.flags.isolated == 1 and sys.flags.dont_write_bytecode == 1,
          "selftest requires -I -B")
    runner = load_module(RUNNER_PATH, "metnos_v26569_runner_selftest")
    tests: list[str] = []

    baseline = run_cli(BASELINE_SELFTEST)
    check(baseline.returncode == 0 and not baseline.stderr, "V26563 baseline failed")
    baseline_result = json.loads(baseline.stdout)
    check(
        baseline_result.get("status") == "PASS"
        and baseline_result.get("total_asserted_tests") == 66
        and baseline_result.get("network_calls") == 0
        and baseline_result.get("model_calls") == 0,
        "V26563 baseline shape/count changed",
    )
    tests.append("inherited_v26563_66_of_66")

    freeze = runner.verify_freeze()
    check(freeze["status"] == "PRE_INFRA_REVIEW", "author freeze status changed")
    check(not runner.PREFLIGHT_GATE_PATH.exists(), "preflight gate unexpectedly exists")
    check(not runner.EXTERNAL_GATE_PATH.exists(), "external gate unexpectedly exists")
    check(freeze["author_inference_allowed"] is False, "freeze allows inference")
    tests.append("author_freeze_pass_both_operational_gates_absent")

    gate = json.loads(runner.AUTHOR_GATE_PATH.read_text(encoding="utf-8"))
    check(
        gate["inference_allowed"] is False
        and gate["live_runner_allowed"] is False
        and gate["transport_preflight_allowed"] is False
        and gate["external_gate_required"] is True
        and gate["preflight_gate_required"] is True
        and gate["preflight_gate_status"] == "absent"
        and gate["model_calls_before_checkpoint"] == 0
        and gate["network_calls_before_checkpoint"] == 0
        and gate["offline_evaluator_in_bundle"] is True,
        "author pre-gate does not fail closed",
    )
    tests.append("author_pre_gate_declares_no_inference")

    gate_test_parent = REPOSITORY / "internal/tools/request_analysis_lab/candidates"
    with tempfile.TemporaryDirectory(
        prefix=".v26569-gate-reachability-", dir=gate_test_parent,
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

    contract = runner._contract()
    materialised_schema = json.loads(
        (CONTRACT_DIR / "metnos_v26568_relation_typed.schema.json").read_text("utf-8"))
    materialised_prompt = (
        CONTRACT_DIR / "metnos_v26568_relation_typed.prompt.txt").read_text("utf-8")
    derived_schema = contract["projection"].build_schema(contract["registry"])
    check(runner._exact_json_equal(derived_schema, materialised_schema),
          "derived schema differs from the materialised contract")
    body = runner.request_body("opaque-native-probe")
    check(body["response_format"]["json_schema"]["schema"] == materialised_schema,
          "request body does not carry the pinned schema")
    check(body["messages"][0]["content"] == materialised_prompt,
          "request body does not carry the pinned prompt")
    check(body["response_format"]["json_schema"]["strict"] is True
          and body["temperature"] == 0 and body["seed"] == runner.FIXED_SEED
          and body["chat_template_kwargs"] == {"enable_thinking": False},
          "request contract drifted")
    check(contract["projection"].build_prompt(contract["registry"])
          == materialised_prompt, "derived prompt differs from the pinned one")
    tests.append("contract_schema_and_prompt_derived_from_one_pinned_registry")

    for invalid in (
        "http://127.0.0.1:8080/", "HTTP://127.0.0.1:8080",
        "http://127.0.0.1:8080@192.0.2.1", "http://localhost:8080",
        "http://user@127.0.0.1:8080", "http://127.0.0.1:8080?x=1",
        "http://127.0.0.1:0", "http://[::1]:0",
    ):
        check(not runner._valid_local_endpoint(invalid),
              f"runner endpoint parser accepted: {invalid}")
    for valid in ("http://127.0.0.1:1", "http://127.0.0.1:65535",
                  "http://[::1]:1", "http://[::1]:65535"):
        check(runner._valid_local_endpoint(valid), f"runner rejected boundary: {valid}")
    tests.append("literal_loopback_endpoint_only")

    source = RUNNER_PATH.read_text(encoding="utf-8")
    check("ProxyHandler({})" in source and "_DenyRedirectHandler" in source,
          "transport no longer pins an empty proxy handler and a redirect denial")
    check("getproxies" not in source, "runner consults environment proxies")
    tests.append("native_no_proxy_no_redirect_static")

    for label, fake, expected_error, expected_attempts in (
        ("two", CountedFakeTransport(two_opens=True),
         "transport_open_count_or_destination_violation", 2),
        ("proxy", CountedFakeTransport(proxy_destination=True),
         "transport_open_count_or_destination_violation", 1),
        ("redirect", CountedFakeTransport(redirect_response=True),
         "transport_redirect_violation", 1),
    ):
        payload, diagnostic = runner._request_bytes_once(
            runner.urllib.request.Request(ENDPOINT + "/v1/models", method="GET"),
            timeout_s=1, success_limit=1024, opener=fake,
        )
        check(payload is None and diagnostic["error"] == expected_error,
              f"{label} boundary did not fail closed")
        check(diagnostic["socket_attempts"] == expected_attempts,
              f"{label} actual open counter mismatch")
    tests.append("fake_two_open_proxy_redirect_rejected")

    # one complete fake batch, with a hostile case, a JSON-invalid case, an
    # over-budget case and a semantically broken case
    gold_reads: list[str] = []
    postbatch_paths = {
        str(path) for path, _sha, _size in runner.POSTBATCH_EVIDENCE.values()
    }

    def audit(event: str, arguments) -> None:
        if event == "open" and arguments and isinstance(arguments[0], str):
            if arguments[0] in postbatch_paths:
                gold_reads.append(arguments[0])

    sys.addaudithook(audit)
    fake = CountedFakeTransport(
        hostile_at={3}, inner_json_invalid_at={5},
        over_budget_at={7}, semantic_break_at={9},
    )
    batch = runner._run_controls(ENDPOINT, fake, native_run=False)
    serialized = json.dumps(
        batch, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    )
    check(batch["status"] == "MODEL_BATCH_COMPLETE"
          and batch["model_batch_complete"] is True
          and len(batch["records"]) == 34,
          "fake 34-case batch did not complete")
    check(fake.get_calls == 1 and fake.post_calls == 34 and fake.open_count == 35,
          "batch did not use exactly one call per case")
    check(batch["summary"]["retries"] == 0
          and batch["summary"]["model_request_attempts"] == 34,
          "batch retried or attempted more than once per case")
    check(batch["query_material_included"] is False
          and batch["accuracy_claimed"] is False
          and batch["evaluation_status"] == "NOT_RUN",
          "batch envelope claims more than it may")
    tests.append("fake_34_batch_complete_one_call_per_case_zero_retry")

    check(not gold_reads, f"runner opened gold-side evidence: {sorted(set(gold_reads))}")
    tests.append("runner_never_opens_gold_side_evidence")

    check(MARKER not in serialized, "hostile model output reached the batch")
    hostile_record = batch["records"][2]["result"]
    check(hostile_record["status"] == "evaluated_invalid"
          and hostile_record["validation"]["schema_ok"] is False,
          "hostile frame was not rejected by the schema stage")
    check(hostile_record["fingerprint"]["out_of_vocabulary_strings"] > 0,
          "hostile strings were not counted")
    check("expanded_frame" not in hostile_record,
          "a schema-invalid frame persisted its expansion")
    for query in {case["query"] for case in runner._runtime_controls()}:
        check(query not in serialized, "a control query appears in the batch")
    tests.append("batch_is_query_free_under_hostile_model_output")

    invented = runner._sanitised_schema_paths(
        ["/clauses/0/" + MARKER, "/status", "/clauses/2/projection"],
        contract["schema_keys"],
    )
    check(MARKER not in json.dumps(invented)
          and invented["out_of_contract_components"] == 1
          and invented["paths"] == ["/clauses/0/?", "/status",
                                    "/clauses/2/projection"],
          "schema error paths persist model-invented keys")
    tests.append("schema_error_paths_never_persist_model_keys")

    for record in batch["records"]:
        result = record["result"]
        check(set(record) == {"ordinal", "opaque_case_id", "query_sha256_utf8",
                              "result"}, "record envelope is not closed")
        check("fingerprint" in result and "fingerprint_sha256" in result,
              "a record carries no structural fingerprint")
        if result["validation"]["attempted"]:
            check(result["validation"]["stages_measured"] == 3,
                  "an attempted record measured fewer than three stages")
    measured = sum(1 for record in batch["records"]
                   if record["result"]["validation"].get("stages_measured") == 3)
    check(measured == 33, f"three-stage records: {measured}/33 expected")
    tests.append("every_record_carries_a_fingerprint_and_all_three_stages")

    # Everything a schema-valid expansion may contain, derived and not listed
    # by hand: the keys of the frozen normal form, the keys of the request
    # contract, and the closed value enums of the frozen registry.
    registry = contract["registry"]
    closed_vocabulary = (
        set(runner._schema_key_vocabulary(contract["validator"].live_schema()))
        | set(contract["schema_keys"])
        | set(contract["fingerprint_vocabulary"])
        | set(registry["reference_registry"])
        | {"out_of_registry", "projection", "dependency", "from_atom_output"}
    )
    persisted = 0
    for record in batch["records"]:
        result = record["result"]
        if "expanded_frame" not in result:
            continue
        persisted += 1
        check(result["validation"]["schema_ok"] is True,
              "an expansion was persisted for a schema-invalid frame")
        outside = [value for value in strings_of(result["expanded_frame"])
                   if value not in closed_vocabulary]
        check(not outside, f"expanded frame carries free-form strings: {outside[:3]}")
    check(persisted == 32, f"persisted expansions: {persisted}/32 expected")
    tests.append("expanded_frame_only_for_schema_valid_and_closed_vocabulary")

    over_budget = batch["records"][6]["result"]
    check(over_budget["validation"]["schema_ok"] is True,
          "the over-budget frame was not schema-valid")
    check(runner._contract()["expander"].CODE_ATOM_BUDGET
          in over_budget["validation"]["expansion_codes"],
          "the atom budget was not named by the expansion")
    check(over_budget["validation"]["validator_schema_censored"] is True
          and over_budget["validation"]["validator_codes"] == ["schema"],
          "the frozen validator did not censor itself as expected")
    check(over_budget["validation"]["validator_semantic_measured"] is True
          and over_budget["validation"]["validator_semantic_codes"],
          "the semantic surface was not measured behind the censorship")
    semantic_break = batch["records"][8]["result"]
    check(semantic_break["validation"]["schema_ok"] is True
          and semantic_break["status"] == "evaluated_invalid"
          and semantic_break["validation"]["validator_codes"],
          "a schema-valid semantic failure was not measured")
    tests.append("semantic_surface_measured_even_when_the_validator_censors")

    counters = batch["summary"]["inference_counters"]
    check(counters["evaluated_cases"] == 34 and counters["valid_cases"] == 30
          and counters["invalid_cases"] == 4
          and counters["schema_valid_cases"] == 32
          and counters["semantic_measured_cases"] == 33
          and counters["model_request_attempts"] == 34,
          f"batch counters drifted: {json.dumps(counters, sort_keys=True)}")
    tests.append("batch_counters_account_every_stage")

    # the offline evaluator: it must accept this batch, and it must refuse a
    # tampered one WITHOUT opening a single gold artifact
    evaluator = load_module(EVALUATOR_PATH, "metnos_v26569_evaluator_selftest")
    batch_bytes = (serialized + "\n").encode("utf-8")
    evaluation = evaluator.evaluate_batch_bytes(batch_bytes, "<selftest>")
    check(evaluation["status"] == "PHASE1_EVALUATED"
          and evaluation["batch_sha256"] == hashlib.sha256(batch_bytes).hexdigest()
          and evaluation["cutover_authorized"] is False
          and evaluation["network_calls"] == 0 and evaluation["model_calls"] == 0,
          "evaluator envelope claims more than it may")
    check(evaluation["phase1"]["summary"]["records"] == 34,
          "evaluator did not score every frozen case")
    tests.append("fake_34_batch_and_separate_evaluator_pass")

    original_fixed = evaluator._fixed_artifact_bytes
    for label, mutate in (
        ("record_status_forged", lambda value: value["records"][2]["result"].__setitem__(
            "status", "evaluated_valid")),
        ("fingerprint_removed", lambda value: value["records"][0]["result"].pop(
            "fingerprint")),
        ("fingerprint_hash_forged", lambda value: value["records"][0][
            "result"].__setitem__("fingerprint_sha256", "0" * 64)),
        ("fingerprint_carries_free_text", lambda value: value["records"][0][
            "result"]["fingerprint"]["relations"].append("una frase qualunque")),
        ("validator_codes_forged", lambda value: value["records"][8]["result"][
            "validation"].__setitem__("validator_codes", [])),
        ("expansion_persisted_for_schema_invalid",
         lambda value: value["records"][2]["result"].__setitem__(
             "expanded_frame", {"status": "supported", "atoms": []})),
        ("stages_measured_forged", lambda value: value["records"][0]["result"][
            "validation"].__setitem__("stages_measured", 1)),
        ("counters_forged", lambda value: value["records"][0]["result"][
            "counters"].__setitem__("schema_valid_cases", 0)),
        ("query_planted_in_a_code", lambda value: value["records"][8]["result"][
            "validation"]["validator_codes"].append(
                next(iter({case["query"] for case in runner._runtime_controls()})))),
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
                ).encode("utf-8"), label),
                label,
            )
        finally:
            evaluator._fixed_artifact_bytes = original_fixed
        check(not set(reads).intersection(evaluator.GOLD_IDENTITIES),
              f"{label} opened gold before rejection")
    tests.append("nine_forged_batches_rejected_with_zero_gold_reads")

    with tempfile.TemporaryDirectory(prefix="v26569-anchor-") as temporary:
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

    with tempfile.TemporaryDirectory(prefix="v26569-preflight-") as temporary:
        output = Path(temporary) / "blocked_preflight.json"
        blocked = CountedFakeTransport()
        expect_raises(
            RuntimeError,
            lambda: runner.run_preflight(ENDPOINT, output, blocked),
            "preflight without preliminary gate",
        )
        check(blocked.open_count == 0 and not output.exists(),
              "absent preliminary gate allowed transport/output")
        original_verifier = runner._verify_preflight_gate_snapshot
        runner._verify_preflight_gate_snapshot = lambda endpoint, path: ({
            "authority": "synthetic_offline_selftest_only",
        }, "0" * 64)
        try:
            produced = Path(temporary) / "synthetic_preflight.json"
            producer = CountedFakeTransport()
            result = runner.run_preflight(ENDPOINT, produced, producer)
            check(result["status"] == "PASS" and produced.is_file(),
                  "preflight producer did not write atomically")
            expect_raises(
                FileExistsError,
                lambda: runner.run_preflight(ENDPOINT, produced, producer),
                "preflight no-clobber",
            )
            check(producer.open_count == 1, "no-clobber happened after transport")
        finally:
            runner._verify_preflight_gate_snapshot = original_verifier
    tests.append("preflight_producer_requires_separate_gate_and_no_clobber")

    with tempfile.TemporaryDirectory(prefix="v26569-live-") as temporary:
        output = Path(temporary) / "blocked_live.json"
        expect_raises(
            RuntimeError,
            lambda: runner.run_live(ENDPOINT, output),
            "live without external gate",
        )
        check(not output.exists(), "blocked live wrote an output")
    tests.append("live_requires_the_absent_external_gate")

    with tempfile.TemporaryDirectory(prefix="v26569-cli-") as temporary:
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
        completed = run_cli(RUNNER_PATH, "--nonsense")
        check(completed.returncode == 2 and not completed.stdout,
              "unknown CLI action was accepted")
        malformed = Path(temporary) / "malformed.json"
        malformed.write_bytes(b"{")
        evaluation_out = Path(temporary) / "malformed_evaluation.json"
        completed = run_cli(
            EVALUATOR_PATH, "--batch", str(malformed),
            "--output", str(evaluation_out),
        )
        check(completed.returncode == 2 and not completed.stdout
              and not evaluation_out.exists(),
              "evaluator CLI failure contract failed")
        line = completed.stderr.decode("utf-8")
        check("Traceback" not in line and line.count("\n") == 1,
              "evaluator CLI leaked traceback/multiline diagnostics")
        cli_error = json.loads(line)
        check(cli_error.get("status") == "CLI_ERROR"
              and cli_error.get("traceback_emitted") is False,
              "evaluator CLI error shape failed")
    tests.append("runner_and_evaluator_cli_errors_are_sanitized")

    manifest = runner.verify_python_dependency_tree()
    check(manifest["packages"], "dependency manifest is empty")
    original_pins = dict(runner.ARTIFACT_PINS)
    path, sha, size = original_pins["python_dependency_tree"]
    try:
        runner.ARTIFACT_PINS["python_dependency_tree"] = (path, "0" * 64, size)
        runner.RUNTIME_ARTIFACTS["python_dependency_tree"] = (path, "0" * 64, size)
        expect_raises(
            RuntimeError, runner.verify_python_dependency_tree,
            "drifted dependency manifest",
        )
    finally:
        runner.ARTIFACT_PINS["python_dependency_tree"] = (path, sha, size)
        runner.RUNTIME_ARTIFACTS["python_dependency_tree"] = (path, sha, size)
    check(runner.verify_python_dependency_tree()["packages"] is not None,
          "dependency manifest verification did not recover")
    tests.append("dependency_tree_membership_recomputed_before_compile_exec")

    for identity in ("expander_v26566", "pipeline_v26566", "clause_owned_schema",
                     "clause_owned_prompt", "registry_projection_v26568"):
        path, sha, size = runner.RUNTIME_ARTIFACTS[identity]
        try:
            runner.RUNTIME_ARTIFACTS[identity] = (path, "0" * 64, size)
            expect_raises(
                RuntimeError,
                lambda name=identity: runner._runtime_artifact_bytes(name),
                f"drifted pin accepted: {identity}",
            )
        finally:
            runner.RUNTIME_ARTIFACTS[identity] = (path, sha, size)
    tests.append("contract_artifacts_are_pinned_and_fail_closed")

    with tempfile.TemporaryDirectory(prefix="v26569-cap-") as temporary:
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
        "version": "metnos.v26.5.6.9-offline-author-selftest/1.0",
        "status": "PASS",
        "inherited_v26563_passed": 66,
        "inheritance_note": (
            "V26.5.6.4's suite asserts author-time gate absence, which its own "
            "live run has falsified; V26.5.6.3 is the last still-true ancestor"
        ),
        "new_tests": len(tests),
        "new_passed": len(tests),
        "total_asserted_tests": 66 + len(tests),
        "test_names": tests,
        "fake_transport_calls": {
            "complete_batch_get": fake.get_calls,
            "complete_batch_post": fake.post_calls,
            "complete_batch_actual_opens": fake.open_count,
        },
        "batch_serialized_bytes_with_newline": len(serialized.encode("utf-8")) + 1,
        "batch_cap_bytes": runner.MODEL_BATCH_MAX_SERIALIZED_BYTES,
        "batch_counters": batch["summary"]["inference_counters"],
        "persisted_expansions": persisted,
        "gold_side_reads": len(gold_reads),
        "network_calls": 0,
        "model_calls": 0,
        "inference": False,
        "runner_sha256": hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest(),
        "selftest_total_ms": (time.perf_counter() - started) * 1000,
    }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
