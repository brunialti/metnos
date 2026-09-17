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

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from html_sanitizer import to_safe_html, to_safe_html_full  # noqa: E402
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
        self.assertIn(
            '<a href="https://example.com" target="_blank" '
            'rel="noopener noreferrer">ciao</a>',
            html,
        )

    def test_lre_receipt_links_in_both_languages_without_changing_plain_text(self):
        import sqlite3

        seed = _RUNTIME.parent / "install/data/i18n_seed.sqlite"
        with sqlite3.connect(f"file:{seed}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT key,lang,text FROM i18n WHERE key IN (?,?) AND lang IN ('it','en')",
                ("MSG_LRE_SUBMITTED", "MSG_LRE_SUBMITTED_WITH_SUMMARY"),
            ).fetchall()
        self.assertEqual(len(rows), 4)
        for key, lang, text in rows:
            with self.subTest(key=key, lang=lang):
                message = text.format(workload_id="wrk_fixture", status_url="/admin/lre",
                                      source_count=12, stage_count=5, max_concurrency=2)
                html = _safe_final_html(message)
                self.assertIn('<a href="/admin/lre">/admin/lre</a>', html)
                self.assertIn("wrk_fixture", html)
                self.assertNotIn("href=", to_safe_html(message), "Telegram/plain receipts stay unchanged")

    def test_internal_links_preserve_existing_anchors_code_and_url_boundaries(self):
        for message in ("`/admin/lre`", "```\n/admin/lre\n```",
                        "https://example.test/admin/lre", "/admin/lre/secret",
                        "/admin/lre?next=https://example.test", "/admin/lre.html",
                        "not/admin/lre", "//admin/lre", "/admin/not-registered"):
            with self.subTest(message=message):
                self.assertNotIn('href="/admin/lre"', _safe_final_html(message))
        existing = _safe_final_html("[/admin/lre](/admin/lre)")
        self.assertEqual(existing.count("<a "), 1)
        self.assertIn('href="/admin/lre"', existing)
        escaped = _safe_final_html('<script>/admin/lre</script> & "safe"')
        self.assertNotIn("<script>", escaped)
        self.assertIn("&lt;script&gt;", escaped)
        self.assertIn("&amp;", escaped)

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
