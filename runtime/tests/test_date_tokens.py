"""Test di date_tokens + wiring ai due boundary di render (§7.11-per-le-date).

date_tokens.substitute_date_tokens risolve `{{ current_year }}` / `{{ current_date }}`
deterministicamente DOVE Jinja non arriva: sezioni planner .yaml (rese in prosa)
e description dei manifest .toml (lette dal Proposer). Stessa convenzione dei .j2.
"""
from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class TestSubstitute(unittest.TestCase):
    def setUp(self):
        from date_tokens import substitute_date_tokens
        self.s = substitute_date_tokens
        self.fake = datetime(2027, 3, 9)

    def test_year_date_month(self):
        self.assertEqual(self.s("budget {{ current_year }}", now=self.fake), "budget 2027")
        self.assertEqual(self.s("after={{current_date}}", now=self.fake), "after=2027-03-09")
        self.assertEqual(self.s("q{{ current_month }}", now=self.fake), "q03")

    def test_unknown_token_untouched(self):
        # I placeholder di piping (from_step) NON sono token-data → invariati.
        self.assertEqual(
            self.s("{{ stepN.field }} {{ current_year }}", now=self.fake),
            "{{ stepN.field }} 2027",
        )

    def test_literal_year_not_changed(self):
        # Un anno LETTERALE non e' un token → resta (idempotenza vs falsi positivi).
        self.assertEqual(self.s("report 2024", now=self.fake), "report 2024")

    def test_empty_and_no_token_passthrough(self):
        self.assertEqual(self.s("", now=self.fake), "")
        self.assertEqual(self.s("plain", now=self.fake), "plain")


class TestProposerWiring(unittest.TestCase):
    def test_render_tool_pool_resolves_token(self):
        from engine.proposer import _render_tool_pool
        entry = types.SimpleNamespace(
            name="find_urls",
            description="SCOPO: cerca. PATTERN: find_urls(query=\"news {{ current_year }}\"). OUT: entries.",
            args_schema={"properties": {"query": {}}, "required": ["query"]},
        )
        out = _render_tool_pool(["find_urls"], [entry])
        # Il token e' risolto all'anno reale; nessun `{{` residuo nel pool.
        assert "{{ current_year }}" not in out
        assert str(datetime.now().year) in out


class TestYamlSectionWiring(unittest.TestCase):
    def test_yaml_section_resolves_token(self, tmp_path=None):
        import tempfile
        import prompt_loader
        d = Path(tempfile.mkdtemp())
        y = d / "demo.yaml"
        y.write_text(
            "section:\n  name: demo\n  when: always\n"
            "rules:\n  - name: r1\n    must: \"usa {{ current_year }}\"\n"
            "    must_not: x\n    ok: y\n    error: z\n",
            encoding="utf-8",
        )
        out = prompt_loader._render_yaml_section(y, fmt="prose")
        assert "{{ current_year }}" not in out
        assert str(datetime.now().year) in out


if __name__ == "__main__":
    unittest.main()
