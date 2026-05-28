"""Test unitari pronoia_classify_fail (ADR 0161 ext, 26/5/2026)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pronoia_classify_fail as pcf


class TestParseResponse(unittest.TestCase):

    def test_clean_format_fail(self):
        cls, reason = pcf._parse_response("format_fail|template senza numero")
        self.assertEqual(cls, "format_fail")
        self.assertEqual(reason, "template senza numero")

    def test_clean_args_fail(self):
        cls, reason = pcf._parse_response("args_fail|path inesistente")
        self.assertEqual(cls, "args_fail")
        self.assertEqual(reason, "path inesistente")

    def test_clean_pipeline_fail(self):
        cls, reason = pcf._parse_response("pipeline_fail|executor wrong")
        self.assertEqual(cls, "pipeline_fail")
        self.assertEqual(reason, "executor wrong")

    def test_empty_response_default(self):
        cls, reason = pcf._parse_response("")
        self.assertEqual(cls, pcf.DEFAULT_CLASS)
        self.assertEqual(reason, "empty_llm_response")

    def test_ambiguous_response_default(self):
        cls, _ = pcf._parse_response("non lo so")
        self.assertEqual(cls, pcf.DEFAULT_CLASS)

    def test_thinking_tags_stripped(self):
        raw = "<think>analyzing...</think>\nformat_fail|template empty"
        cls, reason = pcf._parse_response(raw)
        self.assertEqual(cls, "format_fail")
        self.assertEqual(reason, "template empty")

    def test_multiline_reason(self):
        raw = "args_fail\nil path non e' stato risolto correttamente"
        cls, reason = pcf._parse_response(raw)
        self.assertEqual(cls, "args_fail")
        self.assertIn("path", reason)


class TestClassifyFail(unittest.TestCase):

    def test_disabled_returns_default(self):
        old = os.environ.get("METNOS_PRONOIA_CLASSIFY_FAIL")
        os.environ["METNOS_PRONOIA_CLASSIFY_FAIL"] = "0"
        import importlib
        import praxis_constants
        importlib.reload(praxis_constants)
        importlib.reload(pcf)
        result = pcf.classify_fail("q", {}, [], "", llm_call=lambda *a, **k: "x")
        self.assertEqual(result["class"], pcf.DEFAULT_CLASS)
        self.assertEqual(result["reason"], "disabled")
        if old is None:
            del os.environ["METNOS_PRONOIA_CLASSIFY_FAIL"]
        else:
            os.environ["METNOS_PRONOIA_CLASSIFY_FAIL"] = old
        importlib.reload(praxis_constants)
        importlib.reload(pcf)

    def test_no_llm_returns_default(self):
        result = pcf.classify_fail("q", {"steps": []}, [], "out",
                                     llm_call=None)
        self.assertEqual(result["reason"], "no_llm")

    def test_llm_format_fail_classified(self):
        def fake_llm(system, user, **kw):
            return "format_fail|template senza numero"
        result = pcf.classify_fail("quanti file",
                                     {"steps": [{"tool": "find_files"}],
                                      "final_message": "Hai file"},
                                     [{"ok": True}], "Hai file",
                                     llm_call=fake_llm)
        self.assertEqual(result["class"], "format_fail")
        self.assertIn("template", result["reason"])
        self.assertGreaterEqual(result["elapsed_ms"], 0)

    def test_llm_exception_fallback(self):
        def fake_llm(system, user, **kw):
            raise RuntimeError("boom")
        result = pcf.classify_fail("q", {"steps": []}, [], "",
                                     llm_call=fake_llm)
        self.assertEqual(result["class"], pcf.DEFAULT_CLASS)
        self.assertIn("llm_error", result["reason"])


class TestBuildPrompt(unittest.TestCase):

    def test_prompt_contains_query(self):
        system, user = pcf._build_prompt(
            "quanti file?", {"steps": []}, [], "")
        self.assertIn("quanti file?", user)
        self.assertIn("MATCH", system)
        self.assertIn("NO_MATCH", system)

    def test_prompt_contains_steps(self):
        framework = {"steps": [
            {"tool": "find_files", "args": {"base_path": "/tmp"}}
        ], "final_message": "${step1.@count} file"}
        system, user = pcf._build_prompt(
            "quanti?", framework, [{"ok": True, "available_total": 100}],
            "file")
        self.assertIn("find_files", user)
        self.assertIn("step1.@count", user)
        self.assertIn("file", user)


if __name__ == "__main__":
    unittest.main()
