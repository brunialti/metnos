"""Smoke test pipeline web/news end-to-end (4/5/2026).

Verifica che find_urls -> read_urls_html -> group_entries componano
correttamente in pipeline, simulando il flow ReAct con un mock server.
"""
from __future__ import annotations

import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "find_urls"))
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "read_urls_html"))
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "group_entries"))


_pages: dict = {}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw): pass

    def do_GET(self):
        if self.path in _pages:
            s, c, b = _pages[self.path]
            self.send_response(s)
            self.send_header("Content-Type", c)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        else:
            self.send_response(404); self.end_headers()


class TestPipelineSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close(); cls.thread.join(timeout=2)

    def test_full_pipeline_find_read_group(self):
        """Simula la pipeline canonical:
        find_urls -> read_urls_html -> group_entries."""
        import find_urls, read_urls_html, group_entries
        find_urls.OWNED_FILE = Path("/dev/null")
        find_urls.TRUSTED_FILE = Path("/dev/null")

        _pages.clear()
        _pages.update({
            "/": (200, "text/html",
                  b'<html><head><title>Hub</title></head><body><h1>Sito</h1>'
                  b'<a href="/news.html">News</a>'
                  b'<a href="/about.html">About</a></body></html>'),
            "/news.html": (200, "text/html",
                           b'<html><head><title>News</title></head><body>'
                           b'<article>articolo su economia e politica</article>'
                           b'</body></html>'),
            "/about.html": (200, "text/html",
                            b'<html><head><title>About</title></head><body>'
                            b'<article>about page</article></body></html>'),
        })
        seed = f"http://127.0.0.1:{self.port}/"

        # Step 1: find_urls con topic. `min_score=0` disabilita l'auto-drop
        # delle entries con score==0 (10/5/2026 fix anti-rumore): qui il test
        # valida il FLOW della pipeline find→read→group, non il topic
        # filtering. Senza override solo /news.html passa il ranker e gli
        # step successivi non hanno nulla da deduplicare.
        out1 = find_urls.invoke({
            "seed_urls": [seed], "topic": "economia politica",
            "max_pages": 20, "max_depth": 2,
            "respect_robots": False, "rate_limit_ms": 30,
            "min_score": 0,
        })
        self.assertTrue(out1["ok"], out1)
        self.assertGreaterEqual(out1["ok_count"], 3, out1)
        # news.html deve essere top per topic match
        top = out1["entries"][0]
        self.assertIn("news", top["url"], out1["entries"])

        # Step 2: read_urls_html sui URL trovati
        urls = [e["url"] for e in out1["entries"]]
        out2 = read_urls_html.invoke({"urls": urls})
        self.assertTrue(out2["ok"], out2)
        self.assertGreaterEqual(out2["ok_count"], 3)
        bodies = [e["body_text"] for e in out2["entries"]]
        self.assertTrue(any("economia" in b for b in bodies), bodies)

        # Step 3: group_entries deduplica per url
        out3 = group_entries.invoke({
            "entries_lists": [out1["entries"], out2["entries"]],
            "dedup_key": "url",
        })
        self.assertTrue(out3["ok"])
        # Dedup: 3 da find + 3 da read - 3 dedup = 3 entries
        self.assertEqual(out3["ok_count"], 3, out3)
        self.assertGreaterEqual(out3["dedupes"], 3)


if __name__ == "__main__":
    unittest.main()
