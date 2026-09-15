"""Test di find_urls (ADR 0081, 4/5/2026).

Mock HTTP server con http.server.ThreadingHTTPServer su porta libera.
Ogni test prepara le pagine in un dict path → (status, content_type, body),
e verifica le entries restituite dall'executor.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

# import path
_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "find_urls"))


# Pagine fittizie: dict {path → (status, content_type, body, extra_headers)}
_pages: dict[str, tuple[int, str, bytes, dict]] = {}
_request_log: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silenzia il logging stderr

    def do_GET(self):
        # Annota request per le verifiche test
        _request_log.append({
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
        })
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
        self.wfile.write(body)


class _ServerCtx:
    def __init__(self):
        self.server = None
        self.thread = None
        self.port = 0

    def start(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"


def _set_pages(d: dict):
    """Sostituisce le pagine pubblicate dal mock server."""
    _pages.clear()
    _request_log.clear()
    for k, v in d.items():
        if isinstance(v, tuple) and len(v) == 3:
            status, ctype, body = v
            extra = {}
        elif isinstance(v, tuple) and len(v) == 4:
            status, ctype, body, extra = v
        else:
            raise ValueError(f"bad page spec for {k}: {v}")
        if isinstance(body, str):
            body = body.encode("utf-8")
        _pages[k] = (status, ctype, body, extra)


class TestFindUrls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = _ServerCtx()
        cls.srv.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def setUp(self):
        # Force tier 1 (default) by emptying owned/trusted.
        # Test diretti senza chiamate ai file di config.
        import find_urls
        # Forziamo OWNED_FILE / TRUSTED_FILE a un path inesistente.
        find_urls.OWNED_FILE = Path("/tmp/.metnos_test_nonexistent_owned.json")
        find_urls.TRUSTED_FILE = Path("/tmp/.metnos_test_nonexistent_trusted.json")

    def test_seed_html_with_5_internal_links_bfs(self):
        """seed con 5 link interni → entries=5+1 (seed)."""
        import find_urls
        seed_url = self.srv.url("/")
        body_seed = """
        <html><head><title>Seed Page</title></head><body>
        <a href="/a.html">Link A</a>
        <a href="/b.html">Link B</a>
        <a href="/c.html">Link C</a>
        <a href="/d.html">Link D</a>
        <a href="/e.html">Link E</a>
        </body></html>
        """
        _set_pages({
            "/": (200, "text/html; charset=utf-8", body_seed),
            "/a.html": (200, "text/html", "<html><title>A</title></html>"),
            "/b.html": (200, "text/html", "<html><title>B</title></html>"),
            "/c.html": (200, "text/html", "<html><title>C</title></html>"),
            "/d.html": (200, "text/html", "<html><title>D</title></html>"),
            "/e.html": (200, "text/html", "<html><title>E</title></html>"),
        })
        out = find_urls.invoke({
            "seed_urls": [seed_url],
            "max_depth": 2, "max_pages": 50,
            "respect_robots": False,
            "rate_limit_ms": 50,
        })
        self.assertTrue(out["ok"], out)
        urls = [e["url"] for e in out["entries"]]
        self.assertIn(seed_url, urls)
        for letter in "abcde":
            self.assertTrue(
                any(u.endswith(f"/{letter}.html") for u in urls),
                f"missing /{letter}.html in {urls}"
            )

    def test_sitemap_strategy(self):
        """sitemap.xml → strategia 'sitemap'."""
        import find_urls
        seed_url = self.srv.url("/")
        sitemap = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{base}/a.html</loc><lastmod>2026-05-04</lastmod></url>
  <url><loc>{base}/b.html</loc><lastmod>2026-05-03</lastmod></url>
  <url><loc>{base}/c.html</loc><lastmod>2026-05-02</lastmod></url>
</urlset>""".format(base=self.srv.url("").rstrip("/"))
        _set_pages({
            "/": (200, "text/html", "<html><title>Seed</title></html>"),
            "/sitemap.xml": (200, "application/xml", sitemap),
            "/a.html": (200, "text/html", "<html><title>A</title></html>"),
            "/b.html": (200, "text/html", "<html><title>B</title></html>"),
            "/c.html": (200, "text/html", "<html><title>C</title></html>"),
        })
        out = find_urls.invoke({
            "seed_urls": [seed_url],
            "respect_robots": False, "max_pages": 50, "rate_limit_ms": 50,
        })
        self.assertTrue(out["ok"], out)
        self.assertIn("sitemap", out["discovery_strategy"])
        # tutti i 3 url della sitemap devono essere nelle entries
        urls = [e["url"] for e in out["entries"]]
        self.assertEqual(sum(1 for u in urls if u.endswith(".html")), 3, urls)

    def test_search_crawls_only_with_explicit_depth_and_vetted_seeds(self):
        """An explicit crawl still follows links from the approved result."""
        import find_urls
        seed_url = self.srv.url("/")
        _set_pages({
            "/": (200, "text/html", '<html><title>Alpha</title><a href="/child">Alpha child</a></html>'),
            "/child": (200, "text/html", '<html><title>Alpha child</title><a href="/deep">Alpha deep</a></html>'),
            "/deep": (200, "text/html", "<html><title>Alpha deep</title></html>"),
        })
        with patch.object(find_urls, "_searxng_search_full", return_value=([
            {"url": seed_url, "title": "Alpha", "snippet": "Alpha result"},
        ], None)), patch.object(find_urls, "_llm_rerank_candidates", return_value=(
            [seed_url], {"used": True, "scores": {seed_url: 0.8}},
        )):
            out = find_urls.invoke({
                "search_query": "alpha", "max_depth": 1,
                "respect_robots": False, "max_pages": 10, "rate_limit_ms": 30,
            })
        self.assertTrue(out["ok"], out)
        urls = [entry["url"] for entry in out["entries"]]
        self.assertIn(self.srv.url("/child"), urls)
        self.assertNotIn(self.srv.url("/deep"), urls)
        self.assertEqual(out["metadata"]["max_depth_used"], 1)

    def test_rss_feed_strategy(self):
        """RSS feed via <link rel=alternate type=application/rss+xml>."""
        import find_urls
        seed_url = self.srv.url("/")
        rss_url = self.srv.url("/feed.xml")
        body_seed = f"""
        <html><head>
        <title>Seed</title>
        <link rel="alternate" type="application/rss+xml" href="{rss_url}" />
        </head><body><p>Hello</p></body></html>
        """
        rss = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>FeedTitle</title>
<item><link>{base}/post-1</link><title>Post 1</title>
<description>The first post</description><pubDate>Mon, 04 May 2026 10:00:00 +0000</pubDate></item>
<item><link>{base}/post-2</link><title>Post 2</title>
<description>The second post</description><pubDate>Sun, 03 May 2026 09:00:00 +0000</pubDate></item>
</channel></rss>""".format(base=self.srv.url("").rstrip("/"))
        _set_pages({
            "/": (200, "text/html", body_seed),
            "/feed.xml": (200, "application/rss+xml", rss),
            "/post-1": (200, "text/html", "<html><title>P1</title></html>"),
            "/post-2": (200, "text/html", "<html><title>P2</title></html>"),
        })
        out = find_urls.invoke({
            "seed_urls": [seed_url],
            "respect_robots": False, "max_pages": 50, "rate_limit_ms": 50,
        })
        self.assertTrue(out["ok"], out)
        self.assertIn("rss", out["discovery_strategy"])
        urls = [e["url"] for e in out["entries"]]
        self.assertTrue(any(u.endswith("/post-1") for u in urls), urls)

    def test_max_depth_1_caps_recursion(self):
        """depth=1, 10 link sotto seed → entries include seed + 10 link
        (depth 0 + depth 1) ma NON link nidificati piu' profondi."""
        import find_urls
        seed_url = self.srv.url("/")
        body_seed = "<html><body>" + "".join(
            f'<a href="/page{i}.html">P{i}</a>' for i in range(10)
        ) + "</body></html>"
        # ogni pagina figlia ha un altro link a /deep.html
        body_child = '<html><body><a href="/deep.html">DEEP</a></body></html>'
        pages = {"/": (200, "text/html", body_seed)}
        for i in range(10):
            pages[f"/page{i}.html"] = (200, "text/html", body_child)
        pages["/deep.html"] = (200, "text/html", "<html><title>DEEP</title></html>")
        _set_pages(pages)
        out = find_urls.invoke({
            "seed_urls": [seed_url],
            "max_depth": 1, "respect_robots": False,
            "max_pages": 50, "rate_limit_ms": 30,
        })
        self.assertTrue(out["ok"], out)
        urls = [e["url"] for e in out["entries"]]
        # /deep.html sarebbe a depth 2: non deve apparire
        self.assertFalse(any(u.endswith("/deep.html") for u in urls),
                         f"deep.html should be at depth 2 but appears: {urls}")

    def test_path_exclude_default(self):
        """/login, /logout, ecc. devono essere esclusi by default."""
        import find_urls
        seed_url = self.srv.url("/")
        body_seed = """
        <html><body>
        <a href="/login">login</a>
        <a href="/logout">logout</a>
        <a href="/articolo">articolo</a>
        </body></html>
        """
        _set_pages({
            "/": (200, "text/html", body_seed),
            "/login": (200, "text/html", "x"),
            "/logout": (200, "text/html", "x"),
            "/articolo": (200, "text/html", "x"),
        })
        out = find_urls.invoke({
            "seed_urls": [seed_url],
            "respect_robots": False, "max_pages": 50, "rate_limit_ms": 30,
        })
        urls = [e["url"] for e in out["entries"]]
        self.assertFalse(any(u.endswith("/login") for u in urls), urls)
        self.assertFalse(any(u.endswith("/logout") for u in urls), urls)
        self.assertTrue(any(u.endswith("/articolo") for u in urls), urls)

    def test_robots_txt_respected(self):
        """robots.txt Disallow: /private/ deve far skippare i match."""
        import find_urls
        seed_url = self.srv.url("/")
        robots = "User-agent: *\nDisallow: /private/\n"
        body_seed = """
        <html><body>
        <a href="/public.html">pub</a>
        <a href="/private/sec.html">sec</a>
        </body></html>
        """
        _set_pages({
            "/": (200, "text/html", body_seed),
            "/robots.txt": (200, "text/plain", robots),
            "/public.html": (200, "text/html", "<html><title>P</title></html>"),
            "/private/sec.html": (200, "text/html", "<html><title>S</title></html>"),
        })
        out = find_urls.invoke({
            "seed_urls": [seed_url],
            "respect_robots": True, "max_pages": 50, "rate_limit_ms": 30,
        })
        self.assertTrue(out["ok"], out)
        urls = [e["url"] for e in out["entries"]]
        self.assertFalse(any("/private/" in u for u in urls), urls)
        self.assertTrue(any(u.endswith("/public.html") for u in urls), urls)
        self.assertTrue(len(out["robots_skipped"]) >= 1, out["robots_skipped"])

    def test_truncated_when_max_pages_reached(self):
        """20 link, max_pages=5 → truncated:true."""
        import find_urls
        seed_url = self.srv.url("/")
        body_seed = "<html><body>" + "".join(
            f'<a href="/p{i}.html">P{i}</a>' for i in range(20)
        ) + "</body></html>"
        pages = {"/": (200, "text/html", body_seed)}
        for i in range(20):
            pages[f"/p{i}.html"] = (200, "text/html", "<html><title>X</title></html>")
        _set_pages(pages)
        out = find_urls.invoke({
            "seed_urls": [seed_url],
            "max_pages": 5, "respect_robots": False,
            "rate_limit_ms": 30,
        })
        self.assertTrue(out["ok"], out)
        self.assertTrue(out.get("truncated"), out)
        from messages import get as _msg
        self.assertEqual(out["truncated_what"], _msg("MSG_OBJECT_URLS"))
        self.assertEqual(out["cap_field"], "max_pages")
        self.assertEqual(out["cap_value"], 5)
        self.assertEqual(out["used"], 5)

    def test_meta_refresh_followed_to_real_homepage(self):
        """Seed `/` con `<meta http-equiv="refresh" content="0;URL=/atp/">` →
        BFS atterra su `/atp/` e scopre i link interni del landing reale.

        Caso reale: WordPress dietro Aruba (atpromaistruzione.it) restituisce
        81 byte di meta-refresh sul root, con i veri contenuti su /atp/.
        urllib non segue meta-refresh (solo HTTP 30x). Senza fix, find_urls
        ritorna solo l'entry meta-refresh con 0 link.
        """
        import find_urls
        seed_url = self.srv.url("/")
        target_url = self.srv.url("/atp/")
        # Body 81-byte tipico Aruba: solo il tag meta-refresh.
        meta_body = f'<meta http-equiv="refresh" content="0;URL={target_url}">'
        # Landing reale con 5 link interni + 1 PDF.
        atp_body = """
        <html><head><title>ATP Roma</title></head><body>
        <a href="/atp/news/">news</a>
        <a href="/atp/2026/05/decreto-organico/">decreto</a>
        <a href="/atp/contatti/">contatti</a>
        <a href="/atp/wp-content/uploads/2026/05/m_pi.pdf">m_pi documento</a>
        <a href="/atp/scuole/">scuole</a>
        </body></html>
        """
        _set_pages({
            "/": (200, "text/html; charset=UTF-8", meta_body),
            "/atp/": (200, "text/html", atp_body),
            "/atp/news/": (200, "text/html", "<html><title>News</title></html>"),
            "/atp/2026/05/decreto-organico/": (200, "text/html",
                "<html><title>Decreto organico</title></html>"),
            "/atp/contatti/": (200, "text/html", "<html><title>C</title></html>"),
            "/atp/scuole/": (200, "text/html", "<html><title>S</title></html>"),
            "/atp/wp-content/uploads/2026/05/m_pi.pdf":
                (200, "application/pdf", b"%PDF-1.4\n"),
        })
        out = find_urls.invoke({
            "seed_urls": [seed_url],
            "max_depth": 3, "max_pages": 50,
            "respect_robots": False, "rate_limit_ms": 50,
        })
        self.assertTrue(out["ok"], out)
        urls = [e["url"] for e in out["entries"]]
        # /atp/ deve essere visitato (e' il vero landing post meta-refresh)
        self.assertTrue(any(u.endswith("/atp/") for u in urls),
                        f"meta-refresh target /atp/ not in entries: {urls}")
        # i link interni del landing devono essere scoperti
        self.assertTrue(any(u.endswith("/atp/news/") for u in urls),
                        f"missing /atp/news/ in {urls}")
        self.assertTrue(any("decreto-organico" in u for u in urls),
                        f"missing /atp/2026/05/decreto-organico/ in {urls}")
        # il PDF dev'essere catalogato fra i discovered_documents
        docs = out.get("discovered_documents", [])
        self.assertTrue(any(d["url"].endswith("/m_pi.pdf") for d in docs),
                        f"missing m_pi.pdf in discovered_documents: {docs}")

    def test_meta_refresh_loop_capped(self):
        """A → B → A meta-refresh loop deve essere capped (no infinite hop).

        Cap: 4 hop per host. Dopo il limite la BFS si ferma senza divergere.
        """
        import find_urls
        seed_url = self.srv.url("/")
        a_url = self.srv.url("/a")
        b_url = self.srv.url("/b")
        _set_pages({
            "/": (200, "text/html",
                  f'<meta http-equiv="refresh" content="0;URL={a_url}">'),
            "/a": (200, "text/html",
                   f'<meta http-equiv="refresh" content="0;URL={b_url}">'),
            "/b": (200, "text/html",
                   f'<meta http-equiv="refresh" content="0;URL={a_url}">'),
        })
        out = find_urls.invoke({
            "seed_urls": [seed_url],
            "max_depth": 1, "max_pages": 20,
            "respect_robots": False, "rate_limit_ms": 30,
        })
        # Niente esplosione, niente fail catastrofico.
        self.assertTrue(out["ok"], out)
        # Cap rispettato: nessun divergence (max ok_count <= 20).
        self.assertLessEqual(out.get("ok_count", 0), 20)

    def test_cookie_file_used(self):
        """auth_cookies_file → cookie inviati nelle Request."""
        import find_urls
        seed_url = self.srv.url("/")
        # Genera un file Mozilla cookies.txt con un cookie SESSION.
        cookie_path = Path("/tmp/metnos_test_find_urls_cookies.txt")
        # MozillaCookieJar richiede header magic + format esatto.
        # Domain prefisso punto ('.') per match suffix; expires far future
        # (anche con ignore_expires=True alcune versioni di stdlib filtrano
        # session cookie con expires=0 dal jar).
        far_future = int(time.time()) + 10 * 365 * 86400
        cookies_txt = (
            "# Netscape HTTP Cookie File\n"
            f"127.0.0.1\tFALSE\t/\tFALSE\t{far_future}\tSESSION\tabc123\n"
        )
        cookie_path.write_text(cookies_txt)
        try:
            _set_pages({
                "/": (200, "text/html",
                      '<html><body><a href="/x.html">X</a></body></html>'),
                "/x.html": (200, "text/html", "<html><title>X</title></html>"),
            })
            out = find_urls.invoke({
                "seed_urls": [seed_url],
                "auth_cookies_file": str(cookie_path),
                "respect_robots": False, "max_pages": 50, "rate_limit_ms": 30,
            })
            self.assertTrue(out["ok"], out)
            # Verifica: almeno una request ha l'header Cookie con SESSION=abc123
            cookie_headers = [r["headers"].get("cookie", "") for r in _request_log]
            self.assertTrue(
                any("SESSION=abc123" in c for c in cookie_headers),
                f"No SESSION cookie sent. Logged cookies: {cookie_headers}",
            )
        finally:
            cookie_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
