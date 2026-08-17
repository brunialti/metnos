from __future__ import annotations

from copy import deepcopy
import difflib
import json
from pathlib import Path
import unittest

from ...candidate_v0_2.canonical import canonical_json_bytes, canonical_sha256, file_sha256
from ...candidate_v0_2.registry_projection import load_projection, validate_projection
from ...candidate_v0_3.projection import build_prompt as build_current_prompt
from ...candidate_v0_3.structured_client import build_request as build_current_request
from .. import build_artifacts
from ..projection import (
    BASE_PROMPT_SHA256,
    BASE_ROOT_LINE,
    CHALLENGER_PROMPT_SHA256,
    COVERAGE_ROOT_LINES,
    FINAL_CHECK_COVERAGE_BULLET,
    FINAL_CHECK_HEADER,
    apply_challenger_delta,
    build_prompt,
    build_schema,
)
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


class PromptChallengerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_projection()

    def test_01_artifacts_freeze_and_pre_holdout_state_are_valid(self):
        self.assertEqual(build_artifacts.check(), [])
        self.assertEqual(build_artifacts.check_pre_holdout(), [])
        self.assertEqual(build_artifacts.find_future_holdout_paths(), [])

    def test_02_source_and_result_have_exact_expected_bytes(self):
        source = CURRENT / "intent_ir_v0_3.prompt.txt"
        result = HERE / "intent_ir_prompt_challenger_v0_1.txt"
        self.assertEqual(file_sha256(source), BASE_PROMPT_SHA256)
        self.assertEqual(file_sha256(result), CHALLENGER_PROMPT_SHA256)
        self.assertEqual(len(result.read_bytes()), 23320)

    def test_03_line_diff_is_exactly_one_removed_and_four_added(self):
        current = build_current_prompt(self.registry).splitlines()
        challenger = build_prompt(self.registry).splitlines()
        delta = list(difflib.ndiff(current, challenger))
        removed = [line[2:] for line in delta if line.startswith("- ")]
        added = [line[2:] for line in delta if line.startswith("+ ")]
        self.assertEqual(removed, [BASE_ROOT_LINE])
        self.assertEqual(added, [*COVERAGE_ROOT_LINES, FINAL_CHECK_COVERAGE_BULLET])

    def test_04_new_lines_have_exact_positions_and_existing_content_is_preserved(self):
        prompt = build_prompt(self.registry)
        lines = prompt.splitlines()
        self.assertEqual(lines[1:4], list(COVERAGE_ROOT_LINES))
        check = lines.index(FINAL_CHECK_HEADER)
        self.assertEqual(lines[check + 1], FINAL_CHECK_COVERAGE_BULLET)
        restored = prompt.replace("\n".join(COVERAGE_ROOT_LINES), BASE_ROOT_LINE, 1)
        restored = restored.replace(
            FINAL_CHECK_HEADER + "\n" + FINAL_CHECK_COVERAGE_BULLET,
            FINAL_CHECK_HEADER,
            1,
        )
        self.assertEqual(restored, build_current_prompt(self.registry))

    def test_05_examples_registry_and_schema_are_byte_identical(self):
        current = build_current_prompt(self.registry)
        challenger = build_prompt(self.registry)
        for marker in ("EQUIVALENT ROOT TEMPLATES:", "VALID COMPLETE GRAPH:", "VALID BARRIER:"):
            current_line = next(line for line in current.splitlines() if line.startswith(marker))
            challenger_line = next(line for line in challenger.splitlines() if line.startswith(marker))
            self.assertEqual(challenger_line, current_line)
        current_catalog = current[current.index("OPERATION REGISTRY"):current.index(FINAL_CHECK_HEADER)]
        challenger_catalog = challenger[challenger.index("OPERATION REGISTRY"):challenger.index(FINAL_CHECK_HEADER)]
        self.assertEqual(challenger_catalog, current_catalog)
        self.assertEqual(
            (HERE / "intent_ir_prompt_challenger_v0_1.schema.json").read_bytes(),
            (CURRENT / "intent_ir_v0_3.schema.json").read_bytes(),
        )
        self.assertEqual(build_schema(self.registry), json.loads((CURRENT / "intent_ir_v0_3.schema.json").read_text()))

    def test_06_request_diff_is_only_the_system_prompt(self):
        for tag in ("it-IT", "zh-Hant-TW", "x-unlisted", "i-klingon"):
            current = build_current_request("synthetic Unicode 雪", self.registry, tag)
            challenger = build_request("synthetic Unicode 雪", self.registry, tag)
            self.assertEqual(challenger["messages"][1], current["messages"][1])
            normalized = deepcopy(challenger)
            normalized["messages"][0]["content"] = current["messages"][0]["content"]
            self.assertEqual(canonical_json_bytes(normalized), canonical_json_bytes(current))
            self.assertEqual(
                challenger["messages"][0]["content"],
                apply_challenger_delta(current["messages"][0]["content"]),
            )

    def test_07_language_and_query_path_remain_general(self):
        system_prompts = []
        for tag in ("de-DE", "zh-Hant-TW", "x-unlisted", "en-GB-oed"):
            request = build_request("arbitrary Unicode 🧭 雪", self.registry, tag)
            self.assertEqual(request["messages"][1]["content"], "arbitrary Unicode 🧭 雪")
            system_prompts.append("\n".join(request["messages"][0]["content"].splitlines()[1:]))
        self.assertEqual(len(set(system_prompts)), 1)
        for malformed in ("", " it-IT", "it_IT", "sl-rozaj-ROZAJ", "i-Klingon", "ſgn-BE-FR"):
            with self.subTest(tag=malformed), self.assertRaises(ValueError):
                build_request("valid Unicode query 雪", self.registry, malformed)

    def test_08_synthetic_registry_rename_changes_data_not_delta(self):
        renamed = deepcopy(self.registry)
        old = sorted(renamed["operations"])[0]
        renamed["operations"]["synthetic/renamed-capability"] = renamed["operations"].pop(old)
        renamed["operations"] = dict(sorted(renamed["operations"].items()))
        renamed = _resign(renamed)
        validate_projection(renamed)
        current = build_current_prompt(renamed)
        challenger = build_prompt(renamed)
        self.assertEqual(challenger, apply_challenger_delta(current))
        self.assertIn("synthetic/renamed-capability", challenger)
        self.assertNotIn("\n- " + old + " |", challenger)

    def test_09_delta_uses_only_an_existing_reviewed_reason(self):
        self.assertIn("outside_registry", self.registry["unrepresentable_reasons"])
        new_text = "\n".join([*COVERAGE_ROOT_LINES, FINAL_CHECK_COVERAGE_BULLET])
        self.assertEqual(new_text.count("outside_registry"), 1)
        for reason in self.registry["unrepresentable_reasons"]:
            if reason != "outside_registry":
                self.assertNotIn(reason, new_text)

    def test_10_artifacts_are_free_of_regression_queries_and_identifiers(self):
        oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
        artifacts = b"".join(
            path.read_bytes()
            for path in (build_artifacts.PROMPT_FILE, build_artifacts.SCHEMA_FILE, build_artifacts.DIFF_FILE)
        )
        for case in oracle["cases"] + oracle["new_controls"]:
            self.assertNotIn(case["query_text"].encode("utf-8"), artifacts)
            self.assertNotIn(case["query_sha256"].encode("ascii"), artifacts)
        for marker in (b"frozen_sample.", b"intent_shadow.control.", b"query_sha256"):
            self.assertNotIn(marker, artifacts)

    def test_11_logic_has_no_archived_candidate_live_or_query_branch(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in HERE.glob("*.py")
            if path.name != "build_artifacts.py"
        )
        for marker in (
            "candidate_v0_4", "live.run", "query_sha256", "frozen_sample.",
            'language == "it"', 'language == "en"', "find/images", "read/messages",
        ):
            self.assertNotIn(marker, source)

    def test_12_chronology_is_closed_and_path_detection_is_general(self):
        proof = json.loads(build_artifacts.CHRONOLOGY_FILE.read_text(encoding="utf-8"))
        self.assertEqual(proof["phase"], "challenger_frozen_before_new_holdout")
        self.assertEqual(proof["observed_matches"], [])
        self.assertTrue(proof["sequence_contract"]["future_holdout_must_bind_exact_challenger_freeze_sha256"])
        self.assertTrue(build_artifacts._is_future_holdout_path(("holdout_v9", "panel.json")))
        self.assertTrue(build_artifacts._is_future_holdout_path(("live", "run4_replicated")))
        self.assertFalse(build_artifacts._is_future_holdout_path(("prompt_challenger_v0_1", "tests")))


if __name__ == "__main__":
    unittest.main()
