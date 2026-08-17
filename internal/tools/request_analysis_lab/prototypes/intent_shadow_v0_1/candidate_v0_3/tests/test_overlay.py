from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from ...candidate_v0_2 import build_artifacts as base_build
from ...candidate_v0_2.api import compile_ir
from ...candidate_v0_2.canonical import canonical_sha256
from ...candidate_v0_2.compiler import semantic_projection
from ...candidate_v0_2.registry_projection import load_projection, validate_projection
from ...candidate_v0_2.tests.oracle_adapter import oracle_expected_to_ir
from .. import build_artifacts
from ..projection import build_prompt, build_schema
from ..structured_client import build_request


HERE = Path(__file__).resolve().parent.parent
BASE = HERE.parent / "candidate_v0_2"
PROTOTYPE = HERE.parent
ORACLE = PROTOTYPE / "intent_shadow_oracle_v0_1.json"


def _resign(projection: dict) -> dict:
    value = deepcopy(projection)
    value["integrity"]["projection_payload_sha256"] = ""
    payload = deepcopy(value)
    payload["integrity"].pop("projection_payload_sha256", None)
    value["integrity"]["projection_payload_sha256"] = canonical_sha256(payload)
    return value


class PromptOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_projection()

    def test_base_and_overlay_artifacts_are_frozen(self):
        self.assertEqual(base_build.check(), [])
        self.assertEqual(build_artifacts.check(), [])

    def test_schema_is_byte_identical_to_v02(self):
        self.assertEqual(
            (HERE / "intent_ir_v0_3.schema.json").read_bytes(),
            (BASE / "intent_ir_v0_2.schema.json").read_bytes(),
        )
        self.assertEqual(build_schema(self.registry), json.loads((BASE / "intent_ir_v0_2.schema.json").read_text()))

    def test_balanced_roots_precede_catalog_and_check_follows_it(self):
        prompt = build_prompt(self.registry)
        catalog = prompt.index("OPERATION REGISTRY")
        final_check = prompt.index("FINAL MINI-CHECK")
        templates = (
            '{"kind":"operation_graph","steps":[{"route":"<registry-route>"}]}',
            '{"kind":"system_control","control":"<registry-control>"}',
            '{"kind":"unrepresentable","reason":"<registry-reason>"}',
        )
        self.assertLess(prompt.index("CHOOSE ROOT FIRST"), catalog)
        self.assertTrue(all(prompt.index(template) < catalog for template in templates))
        self.assertGreater(final_check, prompt.index("UNREPRESENTABLE REASONS"))

    def test_dependency_examples_distinguish_valid_graph_and_self_reference(self):
        prompt = build_prompt(self.registry)
        self.assertIn("OPERATION ORDINAL 0 MUST OMIT FROM", prompt)
        self.assertIn("VALID COMPLETE GRAPH", prompt)
        self.assertIn("MUST NEVER REFERENCE ITS OWN ORDINAL OR A LATER ORDINAL", prompt)
        self.assertEqual(prompt.count('"from":[0]'), 1)
        self.assertIn(
            '{"route":"<source-route>"},{"route":"<consumer-route>","from":[0]}',
            prompt,
        )
        routes = sorted(self.registry["operations"])
        valid = {
            "kind": "operation_graph",
            "steps": [{"route": routes[0]}, {"route": routes[1], "from": [0]}],
        }
        invalid = {"kind": "operation_graph", "steps": [{"route": routes[0], "from": [0]}]}
        self.assertTrue(compile_ir(valid, self.registry).valid)
        result = compile_ir(invalid, self.registry)
        self.assertFalse(result.valid)
        self.assertEqual({issue.code for issue in result.issues}, {"FROM_SELF_OR_FORWARD"})

    def test_request_contract_and_locale_path_are_unchanged(self):
        normalized_prompts = []
        for tag in ("it-IT", "zh-hant-tw", "x-labtest"):
            request = build_request("synthetic Unicode 雪", self.registry, tag)
            self.assertEqual(request["response_format"]["type"], "json_schema")
            self.assertTrue(request["response_format"]["json_schema"]["strict"])
            self.assertNotIn("grammar", request)
            self.assertNotIn("tools", request)
            prompt = request["messages"][0]["content"]
            normalized_prompts.append("\n".join(prompt.splitlines()[1:]))
        self.assertEqual(len(set(normalized_prompts)), 1)
        for malformed in ("", " it-IT", "it_IT", "sl-rozaj-ROZAJ", "en-a-test-a-more"):
            with self.subTest(tag=malformed), self.assertRaises(ValueError):
                build_request("synthetic", self.registry, malformed)

        for confusable in ("i-Klingon", "ſgn-BE-FR", "ｅｎ-GB", "it–IT"):
            with self.subTest(tag=confusable), self.assertRaises(ValueError):
                build_request("valid Unicode query 雪", self.registry, confusable)
        unicode_query = build_request("valid Unicode query 雪", self.registry, "i-klingon")
        self.assertEqual(unicode_query["messages"][1]["content"], "valid Unicode query 雪")

        for grandfathered, preferred in (
            ("i-klingon", "tlh"),
            ("en-GB-oed", "en-GB-oxendict"),
            ("cel-gaulish", "cel-gaulish"),
        ):
            request = build_request("synthetic", self.registry, grandfathered)
            self.assertIn(
                f"INPUT_LANGUAGE_TAG: {preferred}",
                request["messages"][0]["content"],
            )

    def test_registry_renaming_drives_prompt_without_logic_change(self):
        renamed = deepcopy(self.registry)
        metadata = renamed["operations"].pop(sorted(renamed["operations"])[0])
        synthetic_route = "synthetic/renamed-operation"
        renamed["operations"][synthetic_route] = metadata
        renamed["operations"] = dict(sorted(renamed["operations"].items()))
        renamed = _resign(renamed)
        validate_projection(renamed)
        self.assertIn(f"- {synthetic_route}", build_prompt(renamed))

    def test_all_124_expected_round_trip_through_unchanged_core(self):
        oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
        cases = oracle["cases"] + oracle["new_controls"]
        self.assertEqual(len(cases), 124)
        for case in cases:
            ir = oracle_expected_to_ir(case["expected"])
            result = compile_ir(ir, self.registry)
            self.assertTrue(result.valid, result.issues)
            self.assertEqual(semantic_projection(result), case["expected"])

    def test_prompt_artifacts_are_query_free(self):
        oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
        artifacts = (HERE / "intent_ir_v0_3.prompt.txt").read_bytes() + (HERE / "intent_ir_v0_3.schema.json").read_bytes()
        for case in oracle["cases"] + oracle["new_controls"]:
            self.assertNotIn(case["query_text"].encode("utf-8"), artifacts)
            self.assertNotIn(case["query_sha256"].encode("ascii"), artifacts)
        for marker in (b"frozen_sample.", b"intent_shadow.control.", b"query_sha256"):
            self.assertNotIn(marker, artifacts)

    def test_no_binary_locale_or_benchmark_branch_in_overlay(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in HERE.glob("*.py"))
        for marker in ('language == "it"', 'language == "en"', "query_sha256", "frozen_sample."):
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()
