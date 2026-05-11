"""Test HTTP chat HTML-mode rendering (ADR 0109 + ADR 0110).

Verifica che `_safe_final_html` produca HTML completo (browser) tramite
`to_safe_html_full`: tabelle markdown rese come `<table>`, heading
come `<hN>`, liste come `<ul>/<ol>`. Gestisce senza errori input
None/vuoto/eccezione interna (no silent failure §2.8).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from html_sanitizer import to_safe_html_full  # noqa: E402
from http_routes_agent import _safe_final_html  # noqa: E402


class TestSafeFinalHtml(unittest.TestCase):

    def test_markdown_bold_renders(self):
        html = _safe_final_html("**ciao** mondo")
        self.assertIn("<b>ciao</b>", html)

    def test_markdown_table_to_html_table(self):
        md = (
            "| Processo | CPU |\n"
            "| --- | --- |\n"
            "| myclaw | 12% |\n"
            "| llamacpp | 80% |\n"
        )
        html = _safe_final_html(md)
        # ADR 0110: HTTP usa <table> vero, NON <pre>
        self.assertIn("<table>", html)
        self.assertIn("<thead>", html)
        self.assertIn("<tbody>", html)
        self.assertIn("<th>", html)
        self.assertIn("<td>", html)
        self.assertIn("Processo", html)
        self.assertIn("myclaw", html)
        self.assertIn("</table>", html)

    def test_empty_input_returns_empty_string(self):
        self.assertEqual(_safe_final_html(""), "")
        self.assertEqual(_safe_final_html(None), "")

    def test_html_escape_is_safe(self):
        # Input malevolo: il `<script>` letterale viene escapato a entita'
        html = _safe_final_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_equivalent_to_to_safe_html_full_on_normal_md(self):
        md = "# Titolo\n\n**bold** e `code`"
        self.assertEqual(_safe_final_html(md), to_safe_html_full(md))

    def test_heading_renders_as_h1(self):
        html = _safe_final_html("# Titolo")
        self.assertIn("<h1>Titolo</h1>", html)

    def test_link_renders_as_anchor(self):
        html = _safe_final_html("[ciao](https://example.com)")
        self.assertIn('<a href="https://example.com">ciao</a>', html)

    def test_code_block_renders_as_pre_code(self):
        md = "```\nx = 1\nprint(x)\n```"
        html = _safe_final_html(md)
        self.assertIn("<pre><code>", html)
        self.assertIn("x = 1", html)


class TestHttpRoutesAgentResponseShape(unittest.TestCase):
    """Smoke check: il modulo importa, _safe_final_html callable."""

    def test_imports_work(self):
        from http_routes_agent import _safe_final_html as f
        self.assertTrue(callable(f))


if __name__ == "__main__":
    unittest.main()
