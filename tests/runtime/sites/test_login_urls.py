"""Test di login_urls (ADR 0082, normalizzazione 19/7/2026).

Mock HTTP server: simula login form GET (con csrf) + POST (verifica
credenziali, set-cookie SESSION_ID o ritorno alla login page).
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "login_urls"))


# Stato condiviso per il mock server
_state = {
    "good_user": "testuser",
    "good_pwd": "testpwd",
    "csrf_token": "csrf-fixed-12345",
    "next_status": 200,  # POST login: 200 ok, 401 fail
    "post_log": [],
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass

    def do_GET(self):
        if self.path == "/login":
            body = (f'<html><body><form action="/login" method="post">'
                    f'<input type="hidden" name="csrf" value="{_state["csrf_token"]}"/>'
                    f'<input type="text" name="username"/>'
                    f'<input type="password" name="password"/>'
                    f'<button type="submit">Login</button>'
                    f'</form></body></html>').encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/dashboard":
            body = b"<html><body><h1>Welcome</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path != "/login":
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        params = dict(urllib.parse.parse_qsl(raw))
        _state["post_log"].append(params)
        ok = (params.get("username") == _state["good_user"]
              and params.get("password") == _state["good_pwd"]
              and params.get("csrf") == _state["csrf_token"])
        if not ok or _state.get("next_status") == 401:
            body = (b'<html><body><form action="/login" method="post">'
                    b'<input type="password" name="password"/>'
                    b'</form><p>Login failed</p></body></html>')
            self.send_response(401)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # Successo: setta cookie session
        body = b"<html><body><h1>Logged in</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header(
            "Set-Cookie",
            "SESSION_ID=fake-session-abc; Path=/; HttpOnly",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestLoginUrls(unittest.TestCase):
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

    def setUp(self):
        # Override credentials.CRED_DIR + login_urls.COOKIES_DIR
        self.tmp = tempfile.mkdtemp()
        import credentials
        import login_urls
        self._old_cred = credentials.CRED_DIR
        self._old_cook = login_urls.COOKIES_DIR
        credentials.CRED_DIR = Path(self.tmp) / "credentials"
        login_urls.COOKIES_DIR = Path(self.tmp) / "cookies"
        _state["next_status"] = 200
        _state["post_log"] = []

    def tearDown(self):
        import credentials
        import login_urls
        credentials.CRED_DIR = self._old_cred
        login_urls.COOKIES_DIR = self._old_cook
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store_test_creds(self, domain="login.test.metnos"):
        import credentials
        login_url = f"http://127.0.0.1:{self.port}/login"
        credentials.store(domain, {
            "login_url": login_url,
            "method": "POST",
            "form_data": {
                "username": _state["good_user"],
                "password": _state["good_pwd"],
            },
            "session_cookie_names": ["SESSION_ID"],
        })
        return domain

    def test_login_success(self):
        import login_urls
        domain = self._store_test_creds()
        out = login_urls.invoke({"domain": domain})
        self.assertTrue(out["ok"], out)
        self.assertFalse(out["cached"])
        self.assertIn("SESSION_ID", out["session_cookies"])
        # Verifica: la POST ha incluso il csrf scoperto via GET
        self.assertEqual(_state["post_log"][0].get("csrf"),
                         _state["csrf_token"])

    def test_login_failure_401(self):
        import login_urls, credentials
        domain = "fail.test.metnos"
        credentials.store(domain, {
            "login_url": f"http://127.0.0.1:{self.port}/login",
            "method": "POST",
            "form_data": {"username": "wrong", "password": "wrong"},
            "session_cookie_names": ["SESSION_ID"],
        })
        out = login_urls.invoke({"domain": domain})
        self.assertFalse(out["ok"], out)
        # i18n IT: "operazione fallita: 401: unauthorized" — assert sul codice 401
        self.assertIn("401", out["error"])

    def test_cached_cookie_reuse(self):
        import login_urls
        domain = self._store_test_creds()
        out1 = login_urls.invoke({"domain": domain})
        self.assertTrue(out1["ok"])
        self.assertFalse(out1["cached"])
        n_posts = len(_state["post_log"])
        # Seconda chiamata: deve essere cached, niente nuovo POST
        out2 = login_urls.invoke({"domain": domain})
        self.assertTrue(out2["ok"], out2)
        self.assertTrue(out2["cached"], out2)
        self.assertEqual(len(_state["post_log"]), n_posts,
                         "cached call should not POST again")

    def test_force_relogin(self):
        import login_urls
        domain = self._store_test_creds()
        out1 = login_urls.invoke({"domain": domain})
        n_posts = len(_state["post_log"])
        out2 = login_urls.invoke({"domain": domain, "force": True})
        self.assertTrue(out2["ok"], out2)
        self.assertFalse(out2["cached"])
        self.assertEqual(len(_state["post_log"]), n_posts + 1)


if __name__ == "__main__":
    unittest.main()
