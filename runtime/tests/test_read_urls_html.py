"""Test di read_urls_html (ADR 0081, 4/5/2026).

Mock HTTP server condiviso fra test, ogni test sostituisce le pagine.
"""
from __future__ import annotations

import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "read_urls_html"))
from messages import get as _msg  # noqa: E402  # §11 i18n: assert language-independent


_pages: dict = {}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        page = _pages.get(self.path)
        if page is None:
            self.send_response(404); self.end_headers(); return
        status, ctype, body, extra = page
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in extra.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not (status == 304 or self.command == "HEAD"):
            self.wfile.write(body)


def _set_pages(d: dict):
    _pages.clear()
    for k, v in d.items():
        if len(v) == 3:
            status, ctype, body = v; extra = {}
        elif len(v) == 4:
            status, ctype, body, extra = v
        else:
            raise ValueError("bad page spec")
        if isinstance(body, str):
            body = body.encode("utf-8")
        _pages[k] = (status, ctype, body, extra)


class TestReadUrlsHtml(unittest.TestCase):
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

    def test_simple_html_page(self):
        import read_urls_html
        body = """<html lang="it"><head><title>My Title</title>
        <meta name="description" content="my desc">
        </head><body>
        <p>Hello world</p>
        </body></html>"""
        _set_pages({"/p1": (200, "text/html", body)})
        out = read_urls_html.invoke({"urls": [self.url("/p1")]})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["ok_count"], 1)
        e = out["entries"][0]
        self.assertEqual(e["title"], "My Title")
        self.assertIn("Hello world", e["body_text"])
        self.assertEqual(e["lang"], "it")
        self.assertEqual(e["meta"]["description"], "my desc")

    def test_article_preferred_over_body(self):
        """Se la pagina ha <article>, il body_text deve venire da li',
        non includere <header>/<nav>/<footer>."""
        import read_urls_html
        body = """<html><body>
        <header>SITO HEADER</header>
        <nav>NAVIGATION</nav>
        <article>The real content of the article goes here.</article>
        <footer>FOOTER</footer>
        </body></html>"""
        _set_pages({"/p2": (200, "text/html", body)})
        out = read_urls_html.invoke({"urls": [self.url("/p2")]})
        e = out["entries"][0]
        self.assertIn("The real content", e["body_text"])
        self.assertNotIn("HEADER", e["body_text"])
        self.assertNotIn("NAVIGATION", e["body_text"])
        self.assertNotIn("FOOTER", e["body_text"])

    def test_redirect_followed(self):
        """301/302 redirect → opener segue, final URL e' la destinazione."""
        import read_urls_html
        # /redir -> /target
        target_url = self.url("/target")
        _set_pages({
            "/redir": (302, "text/html", b"redirecting", {"Location": target_url}),
            "/target": (200, "text/html", "<html><title>Target</title><body><article>arrived</article></body></html>"),
        })
        out = read_urls_html.invoke({"urls": [self.url("/redir")]})
        self.assertTrue(out["ok"], out)
        e = out["entries"][0]
        self.assertEqual(e["title"], "Target")
        self.assertIn("arrived", e["body_text"])

    def test_non_html_skipped(self):
        """Content-Type non html → skip con error in failed."""
        import read_urls_html
        _set_pages({"/binary": (200, "application/octet-stream", b"\x00\x01\x02")})
        out = read_urls_html.invoke({"urls": [self.url("/binary")]})
        self.assertEqual(out["ok_count"], 0)
        self.assertEqual(out["fail_count"], 1)
        self.assertEqual(out["failed"][0]["error"],
                         _msg("ERR_NON_HTML_CONTENT", ctype="application/octet-stream"))

    def test_multi_url_batch(self):
        """3 URL → 3 entries, ognuna distinta."""
        import read_urls_html
        for i, content in enumerate(("AAA", "BBB", "CCC")):
            _pages[f"/m{i}"] = (200, "text/html",
                                f"<html><title>T{i}</title><body><article>{content}</article></body></html>".encode(),
                                {})
        out = read_urls_html.invoke({"urls": [self.url(f"/m{i}") for i in range(3)]})
        self.assertEqual(out["ok_count"], 3)
        bodies = [e["body_text"] for e in out["entries"]]
        for c in ("AAA", "BBB", "CCC"):
            self.assertTrue(any(c in b for b in bodies), bodies)

    # ── ADR 0098: iframe-following + PDF-linked + JS detection ─────────

    def test_iframe_urls_captured(self):
        """Pagina con iframe: src catturati in iframe_urls."""
        import read_urls_html
        body = (b"<html><body><article>x</article>"
                b'<iframe src="/inner.html"></iframe></body></html>')
        _pages["/iframe-page"] = (200, "text/html", body, {})
        out = read_urls_html.invoke({
            "urls": [self.url("/iframe-page")],
            "follow_iframes": False,
        })
        e = out["entries"][0]
        self.assertEqual(len(e["iframe_urls"]), 1)
        self.assertTrue(e["iframe_urls"][0].endswith("/inner.html"))

    def test_iframe_auto_follow_same_host(self):
        """Body vuoto + iframe same-host: contenuto preso dall'iframe."""
        import read_urls_html
        seed_body = (b"<html><body>"
                     b'<iframe src="/calendario-widget"></iframe></body></html>')
        widget = (b"<html><body><article>Girone F giornata 22: "
                  b"Volley A vs Volley B 3-1</article></body></html>")
        _pages["/seed-empty"] = (200, "text/html", seed_body, {})
        _pages["/calendario-widget"] = (200, "text/html", widget, {})
        out = read_urls_html.invoke({"urls": [self.url("/seed-empty")]})
        e = out["entries"][0]
        self.assertIn("Girone F", e["body_text"])
        self.assertTrue(e.get("iframe_followed"))
        self.assertIn("iframe", e.get("notice", ""))

    def test_iframe_no_follow_cross_host(self):
        """iframe verso host esterno NON viene seguito (legittimita')."""
        import read_urls_html
        body = (b"<html><body><article>tiny</article>"
                b'<iframe src="https://evil.example.com/widget"></iframe>'
                b"</body></html>")
        _pages["/ext-iframe"] = (200, "text/html", body, {})
        out = read_urls_html.invoke({"urls": [self.url("/ext-iframe")]})
        e = out["entries"][0]
        # body resta originale, iframe NON seguito
        self.assertNotIn("widget", e["body_text"])
        self.assertNotIn("iframe_followed", e)

    def test_pdf_linked_documents_with_relevance(self):
        """Anchor a PDF + anchor_text con keyword → linked_documents."""
        import read_urls_html
        body = (b"<html><body><article>"
                b'<a href="/calendario.pdf">Calendario stagione</a>'
                b'<a href="/random.pdf">Click here</a>'
                b'<a href="/risultati-girone-f.pdf">Risultati Girone F</a>'
                b"</article></body></html>")
        _pages["/links"] = (200, "text/html", body, {})
        out = read_urls_html.invoke({"urls": [self.url("/links")]})
        e = out["entries"][0]
        docs = e["linked_documents"]
        self.assertTrue(len(docs) >= 2)
        # Top doc deve avere relevance >0
        self.assertGreater(docs[0]["relevance_score"], 0)
        # PDF "random" con anchor_text generico → relevance 0
        random_pdfs = [d for d in docs if "random" in d.get("href", "")]
        if random_pdfs:
            self.assertEqual(random_pdfs[0]["relevance_score"], 0)

    def test_js_rendered_detected(self):
        """SPA scheletro: text/html ratio basso + root div + script numerosi."""
        import read_urls_html
        # HTML con pochi caratteri di body ma molti script + root div
        body = (
            b"<html><head>"
            b"<script src='/app.js'></script>"
            b"<script src='/vendor.js'></script>"
            b"<script src='/runtime.js'></script>"
            b"<script src='/polyfill.js'></script>"
            b"<script src='/main.js'></script>"
            b"<script src='/lib.js'></script>"
            b"</head><body>"
            b"<div id='root'></div>"
            b"<noscript>Per favore abilitare JavaScript per usare l'app.</noscript>"
            + b"<!-- " + (b"x" * 5000) + b" -->"  # padding per superare min HTML
            + b"</body></html>"
        )
        _pages["/spa"] = (200, "text/html", body, {})
        out = read_urls_html.invoke({"urls": [self.url("/spa")],
                                       "follow_iframes": False})
        e = out["entries"][0]
        self.assertTrue(e["js_rendered"], e.get("js_signals"))
        self.assertIn("JS", e.get("notice", ""))

    # ── ADR 0101: error_class deterministico ────────────────────────

    def test_failed_403_has_error_class_forbidden(self):
        """HTTP 403 → failed[].error_class == 'forbidden'."""
        import read_urls_html
        _set_pages({"/forbidden": (403, "text/plain", b"nope")})
        out = read_urls_html.invoke({"urls": [self.url("/forbidden")]})
        self.assertEqual(out["ok_count"], 0)
        self.assertEqual(len(out["failed"]), 1)
        self.assertEqual(out["failed"][0]["error_class"], "forbidden")
        # backward-compat: error stringa preservato
        self.assertIn("403", out["failed"][0]["error"])

    def test_failed_429_has_error_class_rate_limited(self):
        """HTTP 429 → error_class == 'rate_limited'."""
        import read_urls_html
        _set_pages({"/limited": (429, "text/plain", b"slow down")})
        out = read_urls_html.invoke({"urls": [self.url("/limited")]})
        self.assertEqual(out["failed"][0]["error_class"], "rate_limited")

    def test_failed_404_has_error_class_not_found(self):
        """HTTP 404 → error_class == 'not_found'."""
        import read_urls_html
        # /missing non e' in _pages → handler default 404
        _set_pages({})
        out = read_urls_html.invoke({"urls": [self.url("/missing")]})
        self.assertEqual(out["failed"][0]["error_class"], "not_found")

    def test_failed_5xx_has_error_class_server_error(self):
        """HTTP 500 → error_class == 'server_error'."""
        import read_urls_html
        _set_pages({"/oops": (500, "text/plain", b"boom")})
        out = read_urls_html.invoke({"urls": [self.url("/oops")]})
        self.assertEqual(out["failed"][0]["error_class"], "server_error")

    def test_failed_non_html_has_error_class_non_html(self):
        """Content-Type non text/html → error_class == 'non_html'."""
        import read_urls_html
        _set_pages({"/blob": (200, "application/octet-stream", b"\x00\x01")})
        out = read_urls_html.invoke({"urls": [self.url("/blob")]})
        self.assertEqual(out["failed"][0]["error_class"], "non_html")

    def test_failed_network_has_error_class_network(self):
        """Host inesistente (DNS/conn refused) → error_class == 'network'."""
        import read_urls_html
        # Porta libera + URL non risolvibile: usiamo .invalid TLD riservato.
        out = read_urls_html.invoke({
            "urls": ["http://noexiste.invalid/x"],
            "timeout_s": 3.0,
        })
        self.assertEqual(out["ok_count"], 0)
        # Puo' essere network o timeout (DNS lento), accetta entrambi i casi
        # rete-failure (NON forbidden/not_found).
        self.assertIn(out["failed"][0]["error_class"], ("network", "timeout"))

    def test_partial_success_keeps_entries_and_failed(self):
        """1 url ok + 1 url forbidden → entries=[1] + failed=[1] con classe."""
        import read_urls_html
        _set_pages({
            "/ok": (200, "text/html",
                    b"<html><body><article>contenuto reale</article></body></html>"),
            "/nope": (403, "text/plain", b"forbidden"),
        })
        out = read_urls_html.invoke({
            "urls": [self.url("/ok"), self.url("/nope")],
        })
        self.assertEqual(out["ok_count"], 1)
        self.assertEqual(out["fail_count"], 1)
        self.assertEqual(len(out["entries"]), 1)
        self.assertIn("contenuto reale", out["entries"][0]["body_text"])
        self.assertEqual(len(out["failed"]), 1)
        self.assertEqual(out["failed"][0]["error_class"], "forbidden")

    def test_static_html_not_marked_js(self):
        """Pagina HTML statica con contenuto: js_rendered=False."""
        import read_urls_html
        body = (b"<html><body><article>" + (b"contenuto reale " * 200) +
                b"</article></body></html>")
        _pages["/static"] = (200, "text/html", body, {})
        out = read_urls_html.invoke({"urls": [self.url("/static")]})
        e = out["entries"][0]
        self.assertFalse(e["js_rendered"])


if __name__ == "__main__":
    unittest.main()
