"""test_stepref_count_in_args — `@count` magic risolto anche negli ARG.

Bug 20/6/2026 (consent-gate): il prompt del gate usa `${stepN.@count}` («N
elementi pronti») ma `_resolve_stepref_with_fallback` (render degli ARG) NON
gestiva `@count` — solo `_render_final_message` lo faceva → il conteggio usciva
VUOTO. Il fix rende `@count` consistente fra arg e messaggio finale.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.executor import _resolve_stepref_with_fallback, _resolve_stepref  # noqa: E402
from engine.types import StepRun  # noqa: E402


class TestCountInArgs(unittest.TestCase):
    def test_count_from_entries_list(self):
        r = {"ok": True, "entries": [{"a": 1}, {"a": 2}, {"a": 3}]}
        self.assertEqual(_resolve_stepref_with_fallback(r, "@count"), 3)

    def test_count_prefers_available_total(self):
        r = {"ok": True, "available_total": 27, "entries": [{"a": 1}]}
        self.assertEqual(_resolve_stepref_with_fallback(r, "@count"), 27)

    def test_count_results_synonym(self):
        r = {"ok": True, "results": [{"x": 1}, {"x": 2}]}
        self.assertEqual(_resolve_stepref_with_fallback(r, "@count"), 2)

    def test_count_empty_is_zero(self):
        self.assertEqual(_resolve_stepref_with_fallback({"ok": True}, "@count"), 0)

    def test_rendered_in_arg_string(self):
        """${stepN.@count} dentro una stringa-arg (es. prompt del gate)."""
        hist = [StepRun(step_idx=1, tool="find_entries", args={},
                        result={"ok": True, "entries": [{"i": 53}]},
                        ok=True, latency_ms=0)]
        out = _resolve_stepref("Pronti: ${step1.@count} elementi", hist)
        self.assertEqual(out, "Pronti: 1 elementi")


class TestBriefInArgs(unittest.TestCase):
    """@brief: riassunto per-item (id+titolo) per il prompt del consent-gate
    (Roberto 20/6: dire COSA si approva, non solo quanti)."""

    def test_brief_single_issue(self):
        r = {"ok": True, "entries": [
            {"issue_number": 53, "title": "Come cambio la lingua?"}]}
        self.assertEqual(_resolve_stepref_with_fallback(r, "@brief"),
                         "#53 Come cambio la lingua?")

    def test_brief_caps_at_three_with_overflow(self):
        r = {"entries": [{"issue_number": i, "title": f"T{i}"}
                         for i in range(5)]}
        out = _resolve_stepref_with_fallback(r, "@brief")
        self.assertIn("… (+2)", out)
        self.assertEqual(out.count("#"), 3)        # solo 3 item mostrati

    def test_brief_truncates_long_title(self):
        r = {"entries": [{"id": 1, "title": "x" * 80}]}
        out = _resolve_stepref_with_fallback(r, "@brief")
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 45)         # #1 + 40 char + ellissi

    def test_brief_empty_list(self):
        self.assertEqual(_resolve_stepref_with_fallback({"ok": True}, "@brief"), "")

    def test_brief_rendered_in_gate_prompt(self):
        hist = [StepRun(step_idx=1, tool="find_entries", args={},
                        result={"entries": [
                            {"issue_number": 53, "title": "Lingua"}]},
                        ok=True, latency_ms=0)]
        out = _resolve_stepref(
            "Invio: ${step1.@count} (${step1.@brief}). Ok?", hist)
        self.assertEqual(out, "Invio: 1 (#53 Lingua). Ok?")


if __name__ == "__main__":
    unittest.main()
