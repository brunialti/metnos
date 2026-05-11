"""Test to_safe_html_full — HTML completo per browser (ADR 0110).

Verifica conversione markdown -> HTML pieno con whitelist tag
documentata: tabelle vere, heading multilivello, liste ul/ol,
blockquote, hr, code block, inline (b/i/code/a). Sicurezza:
HTML escape iniziale di `<>&`.

Determinismo §7.9: parser regex + state, no LLM.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from html_sanitizer import to_safe_html_full  # noqa: E402


class TestToSafeHtmlFullEmpty(unittest.TestCase):

    def test_empty_string(self):
        self.assertEqual(to_safe_html_full(""), "")

    def test_none(self):
        self.assertEqual(to_safe_html_full(None), "")


class TestToSafeHtmlFullTable(unittest.TestCase):

    def test_table_renders_as_html_table(self):
        md = (
            "| Processo | CPU | MEM |\n"
            "| --- | --- | --- |\n"
            "| myclaw | 12% | 800M |\n"
            "| llamacpp | 80% | 12G |\n"
        )
        html = to_safe_html_full(md)
        self.assertIn("<table>", html)
        self.assertIn("<thead>", html)
        self.assertIn("<tbody>", html)
        self.assertIn("<tr>", html)
        self.assertIn("<th>Processo</th>", html)
        self.assertIn("<th>CPU</th>", html)
        self.assertIn("<th>MEM</th>", html)
        self.assertIn("<td>myclaw</td>", html)
        self.assertIn("<td>12%</td>", html)
        self.assertIn("<td>llamacpp</td>", html)
        self.assertIn("</table>", html)

    def test_table_alignment_via_separator(self):
        md = (
            "| L | C | R |\n"
            "| :--- | :---: | ---: |\n"
            "| a | b | c |\n"
        )
        html = to_safe_html_full(md)
        # left = no style; center / right = style="text-align:..."
        self.assertIn('<th>L</th>', html)
        self.assertIn('text-align:center', html)
        self.assertIn('text-align:right', html)
        # Nessuno style left explicit
        self.assertNotIn('text-align:left', html)


class TestToSafeHtmlFullHeadings(unittest.TestCase):

    def test_h1(self):
        self.assertIn("<h1>Titolo</h1>", to_safe_html_full("# Titolo"))

    def test_h2(self):
        self.assertIn("<h2>Sub</h2>", to_safe_html_full("## Sub"))

    def test_h3(self):
        self.assertIn("<h3>Subsub</h3>", to_safe_html_full("### Subsub"))

    def test_heading_with_inline_bold(self):
        html = to_safe_html_full("## Hello **world**")
        self.assertIn("<h2>Hello <b>world</b></h2>", html)


class TestToSafeHtmlFullLists(unittest.TestCase):

    def test_bullet_list(self):
        md = "- item1\n- item2\n- item3"
        html = to_safe_html_full(md)
        self.assertIn("<ul>", html)
        self.assertIn("<li>item1</li>", html)
        self.assertIn("<li>item2</li>", html)
        self.assertIn("<li>item3</li>", html)
        self.assertIn("</ul>", html)
        # No <ol>
        self.assertNotIn("<ol>", html)

    def test_bullet_list_star(self):
        md = "* alpha\n* beta"
        html = to_safe_html_full(md)
        self.assertIn("<ul>", html)
        self.assertIn("<li>alpha</li>", html)
        self.assertIn("<li>beta</li>", html)

    def test_numbered_list(self):
        md = "1. primo\n2. secondo\n3. terzo"
        html = to_safe_html_full(md)
        self.assertIn("<ol>", html)
        self.assertIn("<li>primo</li>", html)
        self.assertIn("<li>secondo</li>", html)
        self.assertIn("<li>terzo</li>", html)
        self.assertIn("</ol>", html)

    def test_list_with_inline(self):
        md = "- **bold** item\n- plain"
        html = to_safe_html_full(md)
        self.assertIn("<li><b>bold</b> item</li>", html)


class TestToSafeHtmlFullBlockquote(unittest.TestCase):

    def test_simple_blockquote(self):
        html = to_safe_html_full("> citazione")
        self.assertIn("<blockquote>citazione</blockquote>", html)

    def test_multi_line_blockquote_merged(self):
        md = "> riga uno\n> riga due"
        html = to_safe_html_full(md)
        self.assertIn("<blockquote>", html)
        self.assertIn("riga uno", html)
        self.assertIn("riga due", html)
        # Una sola apertura
        self.assertEqual(html.count("<blockquote>"), 1)


class TestToSafeHtmlFullHr(unittest.TestCase):

    def test_hr(self):
        html = to_safe_html_full("prima\n\n---\n\ndopo")
        self.assertIn("<hr>", html)


class TestToSafeHtmlFullParagraphs(unittest.TestCase):

    def test_plain_wrapped_in_p(self):
        html = to_safe_html_full("ciao mondo")
        self.assertIn("<p>ciao mondo</p>", html)

    def test_bold_in_paragraph(self):
        html = to_safe_html_full("**bold** in plain")
        self.assertIn("<p><b>bold</b> in plain</p>", html)

    def test_two_paragraphs(self):
        md = "primo paragrafo\n\nsecondo paragrafo"
        html = to_safe_html_full(md)
        self.assertEqual(html.count("<p>"), 2)
        self.assertIn("<p>primo paragrafo</p>", html)
        self.assertIn("<p>secondo paragrafo</p>", html)


class TestToSafeHtmlFullCodeBlock(unittest.TestCase):

    def test_triple_backtick(self):
        md = "```\nx = 1\nprint(x)\n```"
        html = to_safe_html_full(md)
        self.assertIn("<pre><code>", html)
        self.assertIn("x = 1", html)
        self.assertIn("print(x)", html)
        self.assertIn("</code></pre>", html)

    def test_triple_backtick_with_lang(self):
        md = "```python\ny = 2\n```"
        html = to_safe_html_full(md)
        self.assertIn("<pre><code>", html)
        self.assertIn("y = 2", html)


class TestToSafeHtmlFullInline(unittest.TestCase):

    def test_inline_code(self):
        html = to_safe_html_full("usa `foo()` per chiamare")
        self.assertIn("<code>foo()</code>", html)

    def test_link(self):
        html = to_safe_html_full("[click](https://example.com)")
        self.assertIn('<a href="https://example.com">click</a>', html)

    def test_italic_underscore(self):
        html = to_safe_html_full("questo e' _enfatico_ qui")
        self.assertIn("<i>enfatico</i>", html)

    def test_italic_preserves_snake_case(self):
        # snake_case NON deve essere convertito in italic
        html = to_safe_html_full("vedi agent_runtime.py")
        self.assertNotIn("<i>", html)
        self.assertIn("agent_runtime.py", html)


class TestToSafeHtmlFullSecurity(unittest.TestCase):

    def test_html_escape_script(self):
        html = to_safe_html_full("<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_html_escape_img_onerror(self):
        html = to_safe_html_full('<img src=x onerror=alert(1)>')
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)

    def test_html_escape_ampersand(self):
        html = to_safe_html_full("a & b")
        self.assertIn("a &amp; b", html)


class TestToSafeHtmlFullCombined(unittest.TestCase):

    def test_heading_plus_list_plus_table(self):
        md = (
            "# Report\n"
            "\n"
            "Sintesi:\n"
            "\n"
            "- punto uno\n"
            "- punto due\n"
            "\n"
            "| col | val |\n"
            "| --- | --- |\n"
            "| a | 1 |\n"
        )
        html = to_safe_html_full(md)
        self.assertIn("<h1>Report</h1>", html)
        self.assertIn("<p>Sintesi:</p>", html)
        self.assertIn("<ul>", html)
        self.assertIn("<li>punto uno</li>", html)
        self.assertIn("<table>", html)
        self.assertIn("<td>a</td>", html)


if __name__ == "__main__":
    unittest.main()
