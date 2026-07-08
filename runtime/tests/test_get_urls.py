"""Test di get_urls — HTTP GET/HEAD, entries-pattern (ADR 0081).

Ermetico: mock HTTP server locale su 127.0.0.1 (porta effimera), stesso pattern
di test_read_urls_html/test_find_urls/test_read_urls_pdf. Copre il comportamento
di RETE (200/HEAD/404/multi-url) che prima era demandato a born-test verso
httpbin.org (servizio esterno = flaky, anti-pattern §8). I born-test del
manifest restano ora solo ERMETICI (validazione arg + scope), come i sibling.
"""
from __future__ import annotations

import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "get_urls"))


_BODY = b'{"note": "hermetic fixture", "ok": true}'


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _serve(self, write_body: bool):
        # `/ok` → 200 con corpo JSON; qualsiasi altro path → 404 (urllib alza
        # HTTPError, che get_urls classifica come failed con status_code).
        if self.path == "/ok":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_BODY)))
            self.end_headers()
            if write_body:
                self.wfile.write(_BODY)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self):
        self._serve(write_body=True)

    def do_HEAD(self):
        self._serve(write_body=False)


class TestGetUrls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()
        cls.thread.join(timeout=2)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    # ── rete (ermetica) ──────────────────────────────────────────────────────
    def test_get_200_con_corpo(self):
        import get_urls
        out = get_urls.invoke({"url": self.url("/ok")})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["ok_count"], 1)
        self.assertEqual(out["fail_count"], 0)
        e = out["entries"][0]
        self.assertEqual(e["status_code"], 200)
        self.assertEqual(e["method"], "GET")
        self.assertEqual(e["host"], "127.0.0.1")
        self.assertIn("hermetic fixture", e["body_text"])
        self.assertGreater(e["bytes"], 0)

    def test_head_senza_corpo(self):
        import get_urls
        out = get_urls.invoke({"url": self.url("/ok"), "method": "HEAD"})
        self.assertTrue(out["ok"], out)
        e = out["entries"][0]
        self.assertEqual(e["status_code"], 200)
        self.assertEqual(e["method"], "HEAD")
        self.assertEqual(e["body_text"], "")  # HEAD non legge corpo
        self.assertEqual(e["bytes"], 0)

    def test_404_finisce_in_failed(self):
        import get_urls
        out = get_urls.invoke({"url": self.url("/inesistente")})
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["ok_count"], 0)
        self.assertEqual(out["fail_count"], 1)
        f = out["failed"][0]
        self.assertEqual(f["status_code"], 404)
        self.assertIn("404", f["error"])

    def test_multi_url_esito_misto(self):
        # vettoriale (§2.1): un 200 + un 404 → ok=false (un fallito), conteggi netti.
        import get_urls
        out = get_urls.invoke({"urls": [self.url("/ok"), self.url("/nope")]})
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["ok_count"], 1)
        self.assertEqual(out["fail_count"], 1)

    # ── validazione arg (senza rete) ─────────────────────────────────────────
    def test_scheme_non_http_rifiutato(self):
        import get_urls
        out = get_urls.invoke({"url": "ftp://127.0.0.1/x"})
        self.assertFalse(out["ok"], out)
        self.assertIn("ftp", out["failed"][0]["error"])

    def test_metodo_non_supportato(self):
        import get_urls
        out = get_urls.invoke({"url": self.url("/ok"), "method": "POST"})
        self.assertFalse(out["ok"], out)
        self.assertIn("POST", out["failed"][0]["error"])

    def test_url_mancante(self):
        import get_urls
        out = get_urls.invoke({})
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["ok_count"], 0)


if __name__ == "__main__":
    unittest.main()
