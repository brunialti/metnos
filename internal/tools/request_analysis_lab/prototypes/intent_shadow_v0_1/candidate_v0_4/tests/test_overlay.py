from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import unittest

from ...candidate_v0_2.api import compile_ir
from ...candidate_v0_2.canonical import canonical_json_bytes, canonical_sha256
from ...candidate_v0_2.compiler import semantic_projection
from ...candidate_v0_2.registry_projection import load_projection, validate_projection
from ...candidate_v0_2.tests.oracle_adapter import oracle_expected_to_ir
from ...candidate_v0_3 import build_artifacts as base_build
from ...candidate_v0_3.structured_client import build_request as build_v03_request
from .. import build_artifacts
from ..projection import build_prompt, build_schema
from ..structured_client import build_request


HERE = Path(__file__).resolve().parent.parent
BASE = HERE.parent / "candidate_v0_3"
PROTOTYPE = HERE.parent
ORACLE = PROTOTYPE / "intent_shadow_oracle_v0_1.json"


def _resign(projection: dict) -> dict:
    value = deepcopy(projection)
    value["integrity"]["projection_payload_sha256"] = ""
    payload = deepcopy(value)
    payload["integrity"].pop("projection_payload_sha256", None)
    value["integrity"]["projection_payload_sha256"] = canonical_sha256(payload)
    return value


class CoveragePromptOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_projection()

    def test_base_and_overlay_artifacts_are_frozen(self):
        self.assertEqual(base_build.check(), [])
        self.assertEqual(build_artifacts.check(), [])

    def test_schema_is_byte_identical_to_v03(self):
        self.assertEqual(
            (HERE / "intent_ir_v0_4.schema.json").read_bytes(),
            (BASE / "intent_ir_v0_3.schema.json").read_bytes(),
        )
        self.assertEqual(
            build_schema(self.registry),
            json.loads((BASE / "intent_ir_v0_3.schema.json").read_text()),
        )

    def test_coverage_procedure_precedes_root_templates_and_catalog(self):
        prompt = build_prompt(self.registry)
        order = "DECISION ORDER: CONTROL -> COVERAGE -> CHECK -> GRAPH -> EDGES -> OUTPUT"
        self.assertIn(order, prompt)
        positions = [prompt.index(f"{index} {name}:") for index, name in enumerate(
            ("CONTROL", "COVERAGE", "CHECK", "GRAPH", "EDGES", "OUTPUT"), 1
        )]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(prompt.index("6 OUTPUT:"), prompt.index("ROOT TEMPLATES"))
        self.assertLess(prompt.index("ROOT TEMPLATES"), prompt.index("REGISTRY DATA"))

    def test_whole_compound_and_exclusive_control_contract(self):
        prompt = build_prompt(self.registry)
        required = (
            "exact whole-request registry control",
            "exclusive system_control",
            "every indispensable capability and clause of the whole request",
            "whole-compound unrepresentable/outside_registry",
            "Never partial, approximate, or omitted coverage",
        )
        for phrase in required:
            self.assertIn(phrase, prompt)

    def test_runtime_arguments_reasons_and_indispensable_operations_contract(self):
        prompt = build_prompt(self.registry)
        self.assertIn("missing executor-time arguments", prompt)
        self.assertIn("do not convert them to missing_required_information", prompt)
        self.assertIn("Add no reason precedence", prompt)
        self.assertIn("emit only indispensable operations", prompt)
        self.assertIn("Add from only for real data consumption", prompt)
        self.assertIn("Step 0 omits from", prompt)
        self.assertEqual(prompt.count('"from"'), 0)

    def test_prompt_is_more_compact_than_v03_and_has_only_three_json_templates(self):
        before = (BASE / "intent_ir_v0_3.prompt.txt").read_text(encoding="utf-8")
        after = build_prompt(self.registry)
        metrics = lambda value: (
            len(value.encode("utf-8")), len(value.splitlines()), len(re.findall(r"\S+", value))
        )
        self.assertTrue(all(new < old for new, old in zip(metrics(after), metrics(before), strict=True)))
        json_lines = [line for line in after.splitlines() if line.startswith('- {"kind"')]
        self.assertEqual(len(json_lines), 3)
        self.assertFalse(any('"from"' in line for line in json_lines))

    def test_registry_records_are_separate_uniform_scope_not_data(self):
        prompt = build_prompt(self.registry)
        data = prompt[prompt.index("REGISTRY DATA"):]
        for line in data.splitlines():
            if line.startswith("- "):
                self.assertIn("SCOPE:", line)
                self.assertIn("NOT:", line)

    def test_synthetic_covered_uncovered_and_dependency_shapes_compile(self):
        routes = sorted(self.registry["operations"])
        covered = {
            "kind": "operation_graph",
            "steps": [{"route": routes[0]}, {"route": routes[1]}],
        }
        consumed = {
            "kind": "operation_graph",
            "steps": [{"route": routes[0]}, {"route": routes[1], "from": [0]}],
        }
        whole_uncovered = {"kind": "unrepresentable", "reason": "outside_registry"}
        control = {"kind": "system_control", "control": sorted(self.registry["system_controls"])[0]}
        for document in (covered, consumed, whole_uncovered, control):
            self.assertTrue(compile_ir(document, self.registry).valid)
        for document in (
            {"kind": "operation_graph", "steps": [{"route": routes[0], "from": [0]}]},
            {"kind": "operation_graph", "steps": [{"route": routes[0]}, {"route": routes[1], "from": [1]}]},
        ):
            result = compile_ir(document, self.registry)
            self.assertFalse(result.valid)
            self.assertEqual({issue.code for issue in result.issues}, {"FROM_SELF_OR_FORWARD"})

    def test_registry_renaming_and_order_are_data_driven(self):
        renamed = deepcopy(self.registry)
        old_route = sorted(renamed["operations"])[0]
        renamed["operations"]["synthetic/renamed-capability"] = renamed["operations"].pop(old_route)
        old_control = sorted(renamed["system_controls"])[0]
        renamed["system_controls"]["synthetic_control"] = renamed["system_controls"].pop(old_control)
        old_reason = sorted(renamed["unrepresentable_reasons"])[0]
        renamed["unrepresentable_reasons"]["synthetic_reason"] = renamed["unrepresentable_reasons"].pop(old_reason)
        renamed = _resign(renamed)
        validate_projection(renamed)
        prompt = build_prompt(renamed)
        for identifier in ("synthetic/renamed-capability", "synthetic_control", "synthetic_reason"):
            self.assertIn(identifier, prompt)

        reordered = deepcopy(renamed)
        for key in ("operations", "system_controls", "barriers", "unrepresentable_reasons"):
            reordered[key] = dict(reversed(tuple(reordered[key].items())))
        reordered = _resign(reordered)
        self.assertEqual(build_prompt(renamed), build_prompt(reordered))

    def test_request_diff_from_v03_is_prompt_only_and_locale_neutral(self):
        for tag in ("it-IT", "zh-Hant-TW", "x-synthetic", "i-klingon"):
            before = build_v03_request("synthetic Unicode 雪", self.registry, tag)
            after = build_request("synthetic Unicode 雪", self.registry, tag)
            self.assertNotEqual(before["messages"][0]["content"], after["messages"][0]["content"])
            before["messages"][0]["content"] = after["messages"][0]["content"]
            self.assertEqual(canonical_json_bytes(before), canonical_json_bytes(after))
        for malformed in ("", " it-IT", "it_IT", "sl-rozaj-ROZAJ", "i-Klingon"):
            with self.subTest(tag=malformed), self.assertRaises(ValueError):
                build_request("synthetic", self.registry, malformed)

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
        artifacts = (
            (HERE / "intent_ir_v0_4.prompt.txt").read_bytes()
            + (HERE / "intent_ir_v0_4.schema.json").read_bytes()
        )
        for case in oracle["cases"] + oracle["new_controls"]:
            query = case.get("query_text", case.get("query"))
            self.assertNotIn(query.encode("utf-8"), artifacts)
            self.assertNotIn(case["query_sha256"].encode("ascii"), artifacts)
        for marker in (b"frozen_sample.", b"intent_shadow.control.", b"query_sha256"):
            self.assertNotIn(marker, artifacts)

    def test_no_locale_branch_benchmark_or_route_literal_in_overlay_logic(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in HERE.glob("*.py"))
        for marker in (
            'language == "it"', 'language == "en"', "query_sha256",
            "frozen_sample.", "find/images", "read/messages", "undo_last_turn",
        ):
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()
