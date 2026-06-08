"""Test sidecar Playwright (ADR 0125, Phase 1).

Mock il sidecar via un mini-HTTP server locale (no playwright import live).
Verifica:
    - client.is_up() probe (UP / DOWN / json malformato)
    - client.render() success + timeout + sidecar-down
    - read_urls_html integration: js_render=true triggers sidecar
    - read_urls_html integration: sidecar DOWN → degrade graceful
    - read_urls_html: default js_render=false NON tocca sidecar
    - server: classifier error
"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "read_urls_html"))


# ── Mock sidecar HTTP server ──────────────────────────────────────────


class _MockSidecarState:
    """Stato condiviso dal mock — controllato dai test."""
    health_status = 200  # 200=UP, 503=DOWN, 0=connection_refused (server off)
    health_body = {"ok": True, "browser": "chromium", "version": "120.0"}
    render_status = 200
    render_body: dict = {
        "ok": True,
        "body_text": "Risultati Girone F: Volley A vs Volley B 3-1",
        "body_html": "<html><body>Risultati Girone F: ...</body></html>",
        "title": "Calendario Serie B",
        "final_url": "https://example.spa/results",
        "render_ms": 1234,
    }
    render_delay_s = 0.0  # sleep prima della response (per testare timeout)


_state = _MockSidecarState()


class _MockHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silenzia rumore in pytest
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(_state.health_status)
            self.send_header("Content-Type", "application/json")
            body = json.dumps(_state.health_body).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path == "/render":
            if _state.render_delay_s > 0:
                time.sleep(_state.render_delay_s)
            length = int(self.headers.get("Content-Length", "0"))
            try:
                _ = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                _ = {}
            self.send_response(_state.render_status)
            self.send_header("Content-Type", "application/json")
            body = json.dumps(_state.render_body).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


# ── Test base: client probe + render ─────────────────────────────────


class TestClientProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        # Reset stato mock fra test
        _state.health_status = 200
        _state.health_body = {"ok": True, "browser": "chromium", "version": "120.0"}
        _state.render_status = 200
        _state.render_body = {
            "ok": True,
            "body_text": "rendered content",
            "body_html": "<html>x</html>",
            "title": "Title",
            "final_url": "https://x",
            "render_ms": 100,
        }
        _state.render_delay_s = 0.0

    def test_is_up_returns_true_on_200_ok(self):
        from playwright_sidecar import client
        self.assertTrue(client.is_up("127.0.0.1", self.port, timeout_s=2.0))

    def test_is_up_false_on_503(self):
        from playwright_sidecar import client
        _state.health_status = 503
        self.assertFalse(client.is_up("127.0.0.1", self.port, timeout_s=2.0))

    def test_is_up_false_on_unreachable_port(self):
        """Probe verso una porta libera → connection refused → False."""
        from playwright_sidecar import client
        # Trova una porta SICURAMENTE libera (bind+close).
        import socket as _s
        sk = _s.socket()
        sk.bind(("127.0.0.1", 0))
        free_port = sk.getsockname()[1]
        sk.close()
        self.assertFalse(client.is_up("127.0.0.1", free_port, timeout_s=0.5))

    def test_is_up_false_on_malformed_json(self):
        from playwright_sidecar import client
        # body non json
        _state.health_body = "not-json"  # type: ignore
        # workaround: il mock json.dumps lo serializza come stringa,
        # che e' json valido ma non un dict. Sostituisci direttamente
        # facendo body un dict senza `ok` chiave.
        _state.health_body = {"status": "ok"}  # manca `ok`
        self.assertFalse(client.is_up("127.0.0.1", self.port, timeout_s=2.0))

    def test_render_returns_success_dict(self):
        from playwright_sidecar import client
        r = client.render("https://x.example/spa",
                          host="127.0.0.1", port=self.port, timeout_s=2.0)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["body_text"], "rendered content")
        self.assertEqual(r["title"], "Title")
        self.assertIn("render_ms", r)

    def test_render_passes_through_server_error_response(self):
        """Server ritorna ok=false (es. timeout di rendering): pass-through."""
        from playwright_sidecar import client
        _state.render_body = {
            "ok": False,
            "error": "timeout after 30s on goto",
            "error_class": "timeout",
        }
        r = client.render("https://x.example/spa",
                          host="127.0.0.1", port=self.port, timeout_s=2.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_class"], "timeout")

    def test_render_sidecar_down_returns_sidecar_down_error(self):
        from playwright_sidecar import client
        import socket as _s
        sk = _s.socket()
        sk.bind(("127.0.0.1", 0))
        free_port = sk.getsockname()[1]
        sk.close()
        r = client.render("https://x.example/spa",
                          host="127.0.0.1", port=free_port, timeout_s=0.5)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_class"], "sidecar_down")

    def test_render_invalid_url_returns_error_dict(self):
        from playwright_sidecar import client
        r = client.render("", host="127.0.0.1", port=self.port)
        self.assertFalse(r["ok"])
        self.assertIn("url required", r["error"])


# ── Test integration read_urls_html ──────────────────────────────────


class TestReadUrlsHtmlIntegration(unittest.TestCase):
    """Verifica che read_urls_html chiami il sidecar quando js_render=true.

    Tecnica: monkeypatch del client module dentro read_urls_html con un
    fake module che simula UP/DOWN/render-result senza HTTP reale (cosi'
    questi test non dipendono dalla porta libera del sidecar mock di sopra).
    """

    @classmethod
    def setUpClass(cls):
        # Mock pagina HTTP "SPA-like": ratio testo basso, root div, molti script
        # → detection js_rendered=true in _fetch_one. Per attivare la
        # detection servono: html_bytes >= 5000 + ratio < 0.05 + >=2 signal
        # (root div, noscript, scripts). Inflato con script src lunghi.
        long_script = b"<script src='/very/long/path/to/spa/bundle/v1234/main.js'></script>"
        cls.spa_body = (
            b"<html><head>"
            + long_script * 80   # ~ 5600 byte di solo script tags
            + b"</head><body><div id='root'></div>"
            + b"<noscript>You need to enable JavaScript to use this app</noscript>"
            + b"</body></html>"
        )
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls._make_handler())
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join(timeout=2)

    @classmethod
    def _make_handler(cls):
        spa_body = cls.spa_body  # capture

        class _H(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                if self.path == "/spa":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(spa_body)))
                    self.end_headers()
                    self.wfile.write(spa_body)
                    return
                self.send_response(404)
                self.end_headers()

        return _H

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _patch_client(self, *, up: bool, render_resp: dict):
        """Patch in-place del _playwright_client dentro read_urls_html."""
        import read_urls_html

        class _FakeClient:
            @staticmethod
            def is_up(*args, **kwargs):
                return up

            @staticmethod
            def render(url, **kwargs):
                # echo url cosi' verifichiamo che e' stato passato
                resp = dict(render_resp)
                resp.setdefault("final_url", url)
                return resp

        self._orig = read_urls_html._playwright_client
        read_urls_html._playwright_client = _FakeClient
        return read_urls_html

    def tearDown(self):
        import read_urls_html
        if hasattr(self, "_orig"):
            read_urls_html._playwright_client = self._orig

    def test_js_render_false_auto_escalates_on_spa(self):
        """§7.9 auto-escalation: js_render NON richiesto ma pagina SPA + sidecar
        UP → l'executor escala da solo (il path engine-v2 plan-then-execute non
        ha un retry LLM per onorare la regola planner js_rendered_retry)."""
        rmod = self._patch_client(up=True, render_resp={
            "ok": True,
            "body_text": "Contenuto reale dopo render JS",
            "body_html": "<html><body>ok</body></html>",
            "title": "Reso",
            "render_ms": 900,
        })
        out = rmod.invoke({"urls": [self.url("/spa")]})  # js_render NON settato
        self.assertEqual(out["ok_count"], 1)
        e = out["entries"][0]
        self.assertIn("Contenuto reale dopo render JS", e["body_text"])
        self.assertTrue(e.get("js_rendered_via_sidecar"))
        self.assertTrue(out.get("js_render_auto"))

    def test_js_render_false_spa_degrades_when_sidecar_down(self):
        """SPA + sidecar GIÙ + js_render non richiesto → degrada con grazia:
        l'entry resta flaggata error_class=js_rendered, nessun render, no crash."""
        rmod = self._patch_client(up=False, render_resp={})
        out = rmod.invoke({"urls": [self.url("/spa")]})
        self.assertEqual(out["ok_count"], 1)
        e = out["entries"][0]
        self.assertEqual(e.get("error_class"), "js_rendered")
        self.assertNotIn("body_html_rendered", e)

    def test_js_render_true_with_sidecar_up_succeeds(self):
        """js_render=true + sidecar UP: entry viene aggiornata col rendering."""
        rmod = self._patch_client(up=True, render_resp={
            "ok": True,
            "body_text": "Risultati Girone F: Volley A vs Volley B 3-1",
            "body_html": "<html><body>...</body></html>",
            "title": "Risultati",
            "render_ms": 1500,
        })
        out = rmod.invoke({
            "urls": [self.url("/spa")],
            "js_render": True,
        })
        self.assertEqual(out["ok_count"], 1)
        e = out["entries"][0]
        self.assertIn("Risultati Girone F", e["body_text"])
        self.assertTrue(e.get("body_html_rendered"))
        self.assertTrue(e.get("js_rendered_via_sidecar"))
        self.assertNotIn("error_class", e)  # pulito dopo render
        self.assertEqual(out["js_render_count"], 1)
        self.assertEqual(out["js_render_attempted"], 1)
        self.assertTrue(out["js_render_sidecar_available"])

    def test_js_render_true_with_sidecar_down_graceful(self):
        """js_render=true + sidecar DOWN: entry resta col marker js_rendered."""
        rmod = self._patch_client(up=False, render_resp={"ok": True})
        out = rmod.invoke({
            "urls": [self.url("/spa")],
            "js_render": True,
        })
        e = out["entries"][0]
        # SPA detection ancora attiva, sidecar non chiamato → error_class resta
        self.assertEqual(e.get("error_class"), "js_rendered")
        self.assertNotIn("body_html_rendered", e)
        self.assertEqual(out["js_render_count"], 0)
        self.assertFalse(out["js_render_sidecar_available"])

    def test_js_render_true_with_sidecar_render_failure(self):
        """Sidecar UP ma render fallisce (timeout): entry annota errore."""
        rmod = self._patch_client(up=True, render_resp={
            "ok": False,
            "error": "timeout after 30s on goto",
            "error_class": "timeout",
        })
        out = rmod.invoke({
            "urls": [self.url("/spa")],
            "js_render": True,
        })
        e = out["entries"][0]
        # Entry preserva il marker originale + annota js_render_error
        self.assertEqual(e.get("error_class"), "js_rendered")
        self.assertEqual(e.get("js_render_error_class"), "timeout")
        self.assertEqual(out["js_render_count"], 0)
        self.assertEqual(out["js_render_attempted"], 1)


# ── Test server error classification ─────────────────────────────────


class TestServerErrorClassification(unittest.TestCase):
    """Verifica _classify_playwright_error senza avviare il server."""

    def test_timeout_message(self):
        from playwright_sidecar import server
        self.assertEqual(
            server._classify_playwright_error(Exception("Timeout 30000ms exceeded.")),
            "timeout",
        )

    def test_network_message_dns(self):
        from playwright_sidecar import server
        self.assertEqual(
            server._classify_playwright_error(
                Exception("net::ERR_NAME_NOT_RESOLVED at https://x")),
            "network",
        )

    def test_unknown_fallback(self):
        from playwright_sidecar import server
        self.assertEqual(
            server._classify_playwright_error(Exception("random thing")),
            "unknown",
        )


if __name__ == "__main__":
    unittest.main()
