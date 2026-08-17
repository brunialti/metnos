from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from ...candidate_v0_2.api import compile_ir
from ...candidate_v0_2.canonical import canonical_json_bytes, canonical_sha256
from ...candidate_v0_2.compiler import semantic_projection
from ...candidate_v0_2.registry_projection import load_projection, validate_projection
from ...candidate_v0_2.tests.oracle_adapter import oracle_expected_to_ir
from ...candidate_v0_3.structured_client import build_request as build_current_request
from .. import build_artifacts
from ..authority_inventory import (
    CURRENT_STATEMENTS,
    ROOT_TEMPLATES,
    RULES,
    inventory_document,
    split_current_prompt,
    validate_inventory,
)
from ..projection import PromptStyle, build_prompt, build_schema, prompt_metrics
from ..structured_client import build_request


HERE = Path(__file__).resolve().parent.parent
CURRENT = HERE.parent / "candidate_v0_3"
PROTOTYPE = HERE.parent
ORACLE = PROTOTYPE / "intent_shadow_oracle_v0_1.json"


def _resign(projection: dict) -> dict:
    value = deepcopy(projection)
    value["integrity"]["projection_payload_sha256"] = ""
    payload = deepcopy(value)
    payload["integrity"].pop("projection_payload_sha256", None)
    value["integrity"]["projection_payload_sha256"] = canonical_sha256(payload)
    return value


class PromptStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_projection()

    def test_artifacts_and_freeze_are_current(self):
        self.assertEqual(build_artifacts.check(), [])

    def test_s0_and_schema_are_byte_identical_to_candidate_v03(self):
        self.assertEqual(
            (HERE / "prompt_s0_current.txt").read_bytes(),
            (CURRENT / "intent_ir_v0_3.prompt.txt").read_bytes(),
        )
        self.assertEqual(
            (HERE / "intent_ir_style_v0_1.schema.json").read_bytes(),
            (CURRENT / "intent_ir_v0_3.schema.json").read_bytes(),
        )
        self.assertEqual(
            build_schema(self.registry),
            json.loads((CURRENT / "intent_ir_v0_3.schema.json").read_text()),
        )

    def test_current_inventory_has_exact_unique_statement_coverage(self):
        validate_inventory(self.registry)
        current_lines = build_prompt(PromptStyle.S0_CURRENT, self.registry).splitlines()
        positions = []
        for statement in CURRENT_STATEMENTS:
            position = statement.line_number if statement.line_number > 0 else len(current_lines) + statement.line_number + 1
            positions.append(position)
            self.assertEqual(current_lines[position - 1], statement.text)
        self.assertEqual(len(positions), len(set(positions)))
        self.assertEqual(
            set(positions),
            set(range(2, 17)) | set(range(len(current_lines) - 4, len(current_lines) + 1)),
        )
        document = inventory_document(self.registry)
        self.assertEqual(len(document["rules"]), len(RULES))
        self.assertEqual(document["equivalence_contract"]["new_semantic_rules"], 0)

    def test_s1_s2_render_each_identical_rule_once_in_canonical_order(self):
        for style in (PromptStyle.S1_METNOS_SHORT, PromptStyle.S2_PROCEDURAL):
            prompt = build_prompt(style, self.registry)
            positions = []
            for rule in RULES:
                self.assertEqual(prompt.count(rule.constraint), 1, rule.rule_id)
                positions.append(prompt.index(rule.constraint))
            self.assertEqual(positions, sorted(positions))
        s1 = build_prompt(PromptStyle.S1_METNOS_SHORT, self.registry)
        s2 = build_prompt(PromptStyle.S2_PROCEDURAL, self.registry)
        self.assertIn("CONSTRAINTS:", s1)
        self.assertIn("PROCEDURE:", s2)
        for ordinal, rule in enumerate(RULES, 1):
            self.assertIn(f"{rule.label}: {rule.constraint}", s1)
            self.assertIn(f"{ordinal} {rule.label} -> {rule.constraint}", s2)

    def test_root_templates_and_registry_authority_are_exactly_shared(self):
        _language, templates, registry_data = split_current_prompt(self.registry)
        self.assertEqual(templates, ROOT_TEMPLATES)
        expected_tail = "\n".join(registry_data) + "\n"
        for style in PromptStyle:
            prompt = build_prompt(style, self.registry)
            for template in ROOT_TEMPLATES:
                self.assertEqual(prompt.count(template), 1)
            if style is not PromptStyle.S0_CURRENT:
                self.assertTrue(prompt.endswith(expected_tail))
        current = build_prompt(PromptStyle.S0_CURRENT, self.registry)
        start = current.index(registry_data[0])
        end = current.index("FINAL MINI-CHECK:")
        self.assertEqual(current[start:end], expected_tail)

    def test_s1_s2_have_no_long_example_repetition_or_new_semantic_rule(self):
        for style in (PromptStyle.S1_METNOS_SHORT, PromptStyle.S2_PROCEDURAL):
            prompt = build_prompt(style, self.registry)
            instruction = prompt[:prompt.index("ROOT TEMPLATES:")]
            for marker in (
                "VALID COMPLETE GRAPH", "VALID BARRIER", "FINAL MINI-CHECK",
                '"from":[0]', "coverage-before-root", "whole-compound",
            ):
                self.assertNotIn(marker, instruction.casefold() if marker.islower() else instruction)

    def test_static_style_metrics_are_smaller_and_materialized_exactly(self):
        metrics = {style: prompt_metrics(build_prompt(style, self.registry)) for style in PromptStyle}
        for style in (PromptStyle.S1_METNOS_SHORT, PromptStyle.S2_PROCEDURAL):
            for key in ("utf8_bytes", "lines", "whitespace_tokens"):
                self.assertLess(metrics[style][key], metrics[PromptStyle.S0_CURRENT][key])
        artifact = json.loads((HERE / "prompt_style_metrics_v0_1.json").read_text())
        self.assertEqual(
            artifact["styles"],
            {style.value: metrics[style] for style in PromptStyle},
        )

    def test_requests_differ_only_in_system_prompt(self):
        requests = {
            style: build_request("synthetic Unicode 雪", self.registry, "zh-Hant-TW", style)
            for style in PromptStyle
        }
        current = build_current_request("synthetic Unicode 雪", self.registry, "zh-Hant-TW")
        self.assertEqual(canonical_json_bytes(requests[PromptStyle.S0_CURRENT]), canonical_json_bytes(current))
        baseline = deepcopy(requests[PromptStyle.S0_CURRENT])
        for style, request in requests.items():
            self.assertEqual(request["response_format"]["type"], "json_schema")
            self.assertIs(request["response_format"]["json_schema"]["strict"], True)
            self.assertNotIn("grammar", request)
            self.assertNotIn("tools", request)
            candidate = deepcopy(request)
            candidate["messages"][0]["content"] = baseline["messages"][0]["content"]
            self.assertEqual(canonical_json_bytes(candidate), canonical_json_bytes(baseline), style)
        with self.assertRaises(TypeError):
            build_request("synthetic", self.registry, "it-IT", "S0_CURRENT")  # type: ignore[arg-type]

    def test_locale_path_is_shared_and_malformed_tags_fail(self):
        for tag in ("it-IT", "zh-Hant-TW", "x-labtest", "i-klingon", "en-GB-oed"):
            prompts = [build_request("Unicode 雪", self.registry, tag, style)["messages"][0]["content"] for style in PromptStyle]
            headers = [prompt.splitlines()[0] for prompt in prompts]
            self.assertEqual(len(set(headers)), 1)
        for malformed in ("", " it-IT", "it_IT", "sl-rozaj-ROZAJ", "i-Klingon", "ſgn-BE-FR"):
            for style in PromptStyle:
                with self.subTest(tag=malformed, style=style), self.assertRaises(ValueError):
                    build_request("valid Unicode query 雪", self.registry, malformed, style)

    def test_synthetic_renamed_registry_preserves_style_equivalence(self):
        renamed = deepcopy(self.registry)
        route = sorted(renamed["operations"])[0]
        renamed["operations"]["synthetic/renamed-capability"] = renamed["operations"].pop(route)
        renamed["operations"] = dict(reversed(tuple(renamed["operations"].items())))
        renamed = _resign(renamed)
        validate_projection(renamed)
        _language, _templates, registry_data = split_current_prompt(renamed)
        expected_tail = "\n".join(registry_data) + "\n"
        for style in (PromptStyle.S1_METNOS_SHORT, PromptStyle.S2_PROCEDURAL):
            prompt = build_prompt(style, renamed)
            self.assertTrue(prompt.endswith(expected_tail))
            self.assertIn("synthetic/renamed-capability", prompt)

    def test_all_124_expected_round_trip_through_unchanged_core(self):
        oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
        cases = oracle["cases"] + oracle["new_controls"]
        self.assertEqual(len(cases), 124)
        for case in cases:
            ir = oracle_expected_to_ir(case["expected"])
            result = compile_ir(ir, self.registry)
            self.assertTrue(result.valid, result.issues)
            self.assertEqual(semantic_projection(result), case["expected"])

    def test_artifacts_are_query_free_and_logic_has_no_benchmark_literals(self):
        oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
        artifacts = b"".join(path.read_bytes() for path in build_artifacts.PROMPT_FILES.values())
        for case in oracle["cases"] + oracle["new_controls"]:
            query = case.get("query_text", case.get("query"))
            self.assertNotIn(query.encode("utf-8"), artifacts)
            self.assertNotIn(case["query_sha256"].encode("ascii"), artifacts)
        source = "\n".join(path.read_text(encoding="utf-8") for path in HERE.glob("*.py"))
        for marker in (
            "frozen_sample.", "intent_shadow.control.", "query_sha256",
            "find/images", "read/messages", "undo_last_turn",
            'language == "it"', 'language == "en"',
        ):
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()
