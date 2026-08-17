from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time
import unittest

from ..api import analyze, compile_ir
from ..canonical import canonical_sha256, strict_json_loads
from ..compiler import (
    compiled_document_json,
    semantic_projection,
    verify_continuation,
)
from ..projection import build_prompt, build_schema
from ..registry_projection import load_projection, validate_projection
from ..structured_client import StructuredClient, build_request
from ..types import DisagreementSignal
from .oracle_adapter import oracle_expected_to_ir


HERE = Path(__file__).resolve().parent.parent
PROTOTYPE = HERE.parent
ORACLE = PROTOTYPE / "intent_shadow_oracle_v0_1.json"
BATCH = PROTOTYPE / "candidate_v0_1/live_run_sealed_batch_v0_1.json"


def _resign(projection: dict) -> dict:
    value = deepcopy(projection)
    value["integrity"]["projection_payload_sha256"] = ""
    payload = deepcopy(value)
    payload["integrity"].pop("projection_payload_sha256", None)
    value["integrity"]["projection_payload_sha256"] = canonical_sha256(payload)
    return value


class AlwaysDisagree:
    def detect(self, query, primary):
        del query, primary
        return DisagreementSignal(True, ("mechanical_test_signal",))


class RoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_projection()
        cls.oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
        cls.base = [case["expected"] for case in cls.oracle["cases"]]
        cls.controls = [case["expected"] for case in cls.oracle["new_controls"]]
        cls.expected = cls.base + cls.controls

    def test_all_124_expected_round_trip_exactly(self):
        self.assertEqual(len(self.expected), 124)
        for expected in self.expected:
            compact = oracle_expected_to_ir(expected)
            result = compile_ir(compact, self.registry)
            self.assertTrue(result.valid, result.issues)
            self.assertEqual(semantic_projection(result), expected)

    def test_required_class_counts(self):
        base_roots = [item["kind"] for item in self.base]
        all_roots = [item["kind"] for item in self.expected]
        self.assertEqual(base_roots.count("unrepresentable"), 34)
        self.assertEqual(all_roots.count("unrepresentable"), 35)
        self.assertEqual(all_roots.count("system_control"), 2)
        self.assertEqual(
            sum(1 for item in self.expected for node in item.get("body", []) if node.get("kind") == "barrier"),
            3,
        )

    def test_barrier_expansion_and_hash_bound_continuation(self):
        barrier_expected = next(
            item for item in self.expected
            if any(node.get("kind") == "barrier" for node in item.get("body", []))
        )
        result = compile_ir(oracle_expected_to_ir(barrier_expected), self.registry)
        self.assertTrue(result.valid)
        self.assertEqual(len(result.continuations), 1)
        document = compiled_document_json(result.document)
        barrier = document["body"][0]
        self.assertEqual([case["outcome"] for case in barrier["cases"]], ["approved", "rejected"])
        self.assertEqual(barrier["cases"][1]["body"], [])
        self.assertTrue(verify_continuation(result.continuations[0], result, self.registry))
        changed = deepcopy(self.registry)
        changed["unrepresentable_reasons"]["synthetic"] = "Synthetic reason."
        changed = _resign(changed)
        self.assertFalse(verify_continuation(result.continuations[0], result, changed))

    def test_fake_structured_response_and_request_contract(self):
        raw = b'{"kind":"system_control","control":"undo_last_turn"}'
        client = StructuredClient.from_fake_responses([raw])
        exchange = client.run("testo sintetico", self.registry, "it-IT")
        result = compile_ir(exchange.content, self.registry)
        self.assertTrue(result.valid)
        request = client.captured_requests[0]
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertTrue(request["response_format"]["json_schema"]["strict"])
        self.assertNotIn("grammar", request)
        self.assertNotIn("tools", request)

    def test_critic_is_interface_only_and_bounded(self):
        raw = b'{"kind":"system_control","control":"undo_last_turn"}'
        result = analyze(
            "testo sintetico",
            [raw],
            self.registry,
            language_tag="it-IT",
            disagreement_detector=AlwaysDisagree(),
        )
        self.assertEqual(result.model_calls, 1)
        self.assertTrue(result.disagreement.required)
        self.assertIsNotNone(result.critic_request)

    def test_synthetic_renamed_registry(self):
        renamed = deepcopy(self.registry)
        metadata = renamed["operations"].pop("find/images")
        renamed["operations"]["locate/pictures"] = metadata
        renamed["operations"] = dict(sorted(renamed["operations"].items()))
        renamed = _resign(renamed)
        validate_projection(renamed)
        self.assertTrue(compile_ir({"kind": "operation_graph", "steps": [{"route": "locate/pictures"}]}, renamed).valid)
        self.assertFalse(compile_ir({"kind": "operation_graph", "steps": [{"route": "find/images"}]}, renamed).valid)
        self.assertIn("locate/pictures", build_prompt(renamed))
        self.assertIn("locate/pictures", json.dumps(build_schema(renamed)))

    def test_artifacts_have_no_benchmark_queries_or_hashes(self):
        artifacts = b"\n".join(
            (HERE / name).read_bytes()
            for name in (
                "intent_ir_registry_projection_v0_2.json",
                "intent_ir_v0_2.schema.json",
                "intent_ir_v0_2.prompt.txt",
            )
        )
        for case in self.oracle["cases"] + self.oracle["new_controls"]:
            self.assertNotIn(case["query_text"].encode("utf-8"), artifacts)
            self.assertNotIn(case["query_sha256"].encode("ascii"), artifacts)
        for marker in (b"frozen_sample.", b"intent_shadow.control.", b"query_sha256"):
            self.assertNotIn(marker, artifacts)

    def test_historical_arbitrary_ports_are_not_in_schema(self):
        batch = json.loads(BATCH.read_text(encoding="utf-8"))
        explicit_edges = []

        def walk(value):
            if type(value) is dict:
                edges = value.get("data_from")
                if type(edges) is list:
                    explicit_edges.extend(
                        edge for edge in edges
                        if type(edge) is dict and ("input" in edge or "output" in edge)
                    )
                for child in value.values():
                    walk(child)
            elif type(value) is list:
                for child in value:
                    walk(child)

        for record in batch["records"]:
            if record.get("arm") == "B":
                walk(record.get("extraction", {}).get("decoded_document"))
        self.assertGreater(len(explicit_edges), 0)
        schema = json.dumps(build_schema(self.registry), sort_keys=True)
        self.assertNotIn('"input"', schema)
        self.assertNotIn('"output"', schema)
        route = next(iter(self.registry["operations"]))
        for edge in explicit_edges:
            source = edge.get("from", 0)
            hostile = {
                "kind": "operation_graph",
                "steps": [
                    {"route": route},
                    {"route": route, "from": [source] if type(source) is int and source >= 0 else [0], "input": edge.get("input", "x"), "output": edge.get("output", "y")},
                ],
            }
            self.assertFalse(compile_ir(hostile, self.registry).valid)

    def test_historical_failed_edges_are_not_expressible_with_ports(self):
        batch = json.loads(BATCH.read_text(encoding="utf-8"))
        failed_edges = []

        def walk(value):
            if type(value) is dict:
                edges = value.get("data_from")
                if type(edges) is list:
                    failed_edges.extend(
                        edge for edge in edges
                        if type(edge) is dict and "input" in edge and "output" in edge
                    )
                for child in value.values():
                    walk(child)
            elif type(value) is list:
                for child in value:
                    walk(child)

        edge_issue_count = 0
        for record in batch["records"]:
            if record.get("arm") != "B":
                continue
            issues = record.get("extraction", {}).get("validation_result", {}).get("issues", [])
            local_count = sum(
                1 for issue in issues
                if type(issue) is dict and str(issue.get("code", "")).startswith("DATA_EDGE_")
            )
            if local_count:
                edge_issue_count += local_count
                walk(record.get("extraction", {}).get("decoded_document"))
        self.assertGreaterEqual(edge_issue_count, 139)
        self.assertGreaterEqual(len(failed_edges), 139)
        route = next(iter(self.registry["operations"]))
        for edge in failed_edges:
            hostile = {
                "kind": "operation_graph",
                "steps": [
                    {"route": route},
                    {
                        "route": route,
                        "from": [0],
                        "input": edge["input"],
                        "output": edge["output"],
                    },
                ],
            }
            self.assertFalse(compile_ir(hostile, self.registry).valid)

    def test_compiler_determinism_and_time(self):
        value = {
            "kind": "operation_graph",
            "steps": [
                {"route": "find/images"},
                {"route": "get/images", "from": [0]},
            ],
        }
        hashes = {compile_ir(value, self.registry).document_sha256 for _ in range(40)}
        self.assertEqual(len(hashes), 1)
        start = time.perf_counter()
        runs = 500
        for _ in range(runs):
            self.assertTrue(compile_ir(value, self.registry).valid)
        average_ms = (time.perf_counter() - start) * 1000 / runs
        self.assertLess(average_ms, 20.0)


if __name__ == "__main__":
    unittest.main()
