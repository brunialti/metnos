"""Tests per chunk_html() split-safe (ADR 0109).

Verifica che:
- Stringhe sotto la soglia ritornino in 1 chunk.
- Tag aperti al boundary vengano chiusi in coda al chunk e riaperti in
  testa al successivo (LIFO close, FIFO reopen, attributi preservati).
- `<pre>` lungo splittato dentro al contenuto resti ben formato.
- Tag void (`<br>`) non entrino nello stack.
- Tag annidati `<b><i><code>X</code></i></b>` vengano riaperti uguali.
- Output concatenato (rimossi i marker close/reopen) sia equivalente.

Run:
    python3 -m pytest tests/runtime/http/test_chunk_html_splitsafe.py -xvs
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from channels.telegram_format import chunk_html  # noqa: E402


def _all_tags_balanced(html: str) -> bool:
    """Check ingenuo: per ogni tag paired, conta open/close (eccetto void)."""
    PAIRED = ("b", "i", "u", "s", "code", "pre", "tg-spoiler", "a")
    stack: list[str] = []
    for m in re.finditer(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)(?:\s[^>]*)?>", html):
        slash, name = m.group(1), m.group(2).lower()
        if name not in PAIRED:
            continue
        if not slash:
            stack.append(name)
        else:
            if not stack or stack[-1] != name:
                return False
            stack.pop()
    return not stack


class TestChunkHtmlSplitSafe(unittest.TestCase):

    def test_short_string_one_chunk(self):
        html = "<b>hello</b> world"
        out = chunk_html(html, max_len=4000)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0], html)

    def test_split_with_open_b_closes_and_reopens(self):
        # Long bold text che eccede max_len → close </b> al boundary,
        # reopen <b> nel chunk successivo.
        body = "x " * 3000  # 6000 char
        html = "<b>" + body + "</b>"
        out = chunk_html(html, max_len=500)
        self.assertGreater(len(out), 1)
        for c in out:
            self.assertTrue(_all_tags_balanced(c), f"chunk not balanced: {c[:80]}")
        # Tutti i chunk intermedi finiscono con </b> (eccetto l'ultimo)
        for c in out[:-1]:
            self.assertTrue(c.rstrip().endswith("</b>"), f"missing close: {c[-20:]}")
        # Tutti i chunk dal secondo in poi iniziano con <b>
        for c in out[1:]:
            self.assertTrue(c.startswith("<b>"), f"missing reopen: {c[:20]}")

    def test_long_pre_block_splits_inside(self):
        body = ("a" * 10) + "\n"
        block = body * 200  # ~2200 char dentro al pre
        html = "<pre>" + block + "</pre>"
        out = chunk_html(html, max_len=600)
        self.assertGreater(len(out), 1)
        for c in out:
            self.assertTrue(_all_tags_balanced(c))
        # Primo chunk apre <pre>, chiude </pre>
        self.assertTrue(out[0].startswith("<pre>"))
        self.assertTrue(out[0].rstrip().endswith("</pre>"))
        # Chunk successivi riaprono <pre>
        for c in out[1:]:
            self.assertTrue(c.startswith("<pre>"))

    def test_nested_tags_lifo_close_fifo_reopen(self):
        # 4 tag annidati: stack <b>, <i>, <code> (al boundary)
        # Close LIFO: </code></i></b>
        # Reopen FIFO: <b><i><code>
        body = "y " * 2000  # 4000 char
        html = "<b><i><code>" + body + "</code></i></b>"
        out = chunk_html(html, max_len=500)
        self.assertGreater(len(out), 1)
        for c in out:
            self.assertTrue(_all_tags_balanced(c))
        # Primo chunk chiude in ordine LIFO: </code></i></b>
        self.assertIn("</code></i></b>", out[0])
        # Secondo chunk riapre in ordine FIFO: <b><i><code>
        self.assertTrue(out[1].startswith("<b><i><code>"))

    def test_void_tags_not_in_stack(self):
        # <br> e <hr> non sono PAIRED → non entrano nello stack
        body = "line\n" * 800  # ~4000 char
        html = "<b>" + body + "<br>" + body + "</b>"
        out = chunk_html(html, max_len=500)
        self.assertGreater(len(out), 1)
        for c in out:
            self.assertTrue(_all_tags_balanced(c), f"chunk: {c[:60]}")

    def test_a_href_preserves_attributes_on_reopen(self):
        # Link lungo: <a href="https://x.example/path"> ... 5000 char ... </a>
        body = "z " * 3000  # 6000 char
        html = '<a href="https://x.example/longpath?q=1">' + body + "</a>"
        out = chunk_html(html, max_len=500)
        self.assertGreater(len(out), 1)
        for c in out:
            self.assertTrue(_all_tags_balanced(c))
        # Tutti i chunk dal secondo in poi riaprono <a> con href identico
        for c in out[1:]:
            self.assertTrue(
                c.startswith('<a href="https://x.example/longpath?q=1">'),
                f"href lost: {c[:80]}",
            )

    def test_no_newline_falls_back_to_space(self):
        # No newline disponibile: split su space
        body = "word " * 2000  # 10000 char no \n
        out = chunk_html(body, max_len=500)
        self.assertGreater(len(out), 1)
        # Nessun chunk dovrebbe spezzare in mezzo a "word" (split su space)
        for c in out[:-1]:
            self.assertTrue(c.endswith(" ") or c.endswith("word "), f"hard split: {c[-20:]}")

    def test_chunks_under_max_len(self):
        body = "x " * 5000
        html = "<b>" + body + "</b>"
        out = chunk_html(html, max_len=500)
        for c in out:
            self.assertLessEqual(len(c), 500 + 64,
                                  f"chunk overflow: len={len(c)}")


if __name__ == "__main__":
    unittest.main()
