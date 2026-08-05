"""Bonifica 28/5 — difese SSRF (photo_web_proxy) + spoof X-Forwarded-For.

- http_routes_agent: l'endpoint anonimo `/agent/photos/web?u=<url>` non deve
  poter colpire host interni (loopback/privati/link-local/riservati).
- http_auth: gli header standard inoltrati sono
  onorati SOLO se il peer TCP reale e' un proxy fidato; altrimenti uno spoof
  `X-Forwarded-For: 127.0.0.1` da Internet NON deve promuovere a `user`.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import http_auth  # noqa: E402
import http_routes_agent as R  # noqa: E402
import users  # noqa: E402


class TestSsrfGuard(unittest.TestCase):
    """Helper di validazione URL/IP in http_routes_agent."""

    def test_blocked_ips(self):
        for ip in ("127.0.0.1", "169.254.169.254", "10.0.0.5",
                   "192.168.1.1", "172.16.0.1", "::1", "0.0.0.0",
                   "fc00::1", "garbage"):
            self.assertTrue(R._ip_is_blocked(ip), f"{ip} should be blocked")

    def test_public_ips_allowed(self):
        for ip in ("8.8.8.8", "1.1.1.1", "93.184.216.34"):
            self.assertFalse(R._ip_is_blocked(ip), f"{ip} should be allowed")

    def test_validate_rejects_non_http_scheme(self):
        err, _ = R._validate_fetch_url("ftp://example.com/x")
        self.assertEqual(err, "invalid_url")
        err, _ = R._validate_fetch_url("file:///etc/passwd")
        self.assertEqual(err, "invalid_url")

    def test_validate_blocks_loopback_literal(self):
        err, _ = R._validate_fetch_url("http://127.0.0.1/admin")
        self.assertEqual(err, "blocked")
        err, _ = R._validate_fetch_url("http://169.254.169.254/latest/meta-data")
        self.assertEqual(err, "blocked")

    def test_validate_blocks_internal_hostname(self):
        # Host che risolve a loopback (es. "localhost") deve essere bloccato.
        with patch.object(R.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]):
            err, _ = R._validate_fetch_url("http://internal.example/x")
            self.assertEqual(err, "blocked")

    def test_validate_blocks_if_any_record_internal(self):
        # Un host con un record pubblico E uno interno → bloccato (rebinding).
        infos = [(2, 1, 6, "", ("8.8.8.8", 0)),
                 (2, 1, 6, "", ("127.0.0.1", 0))]
        with patch.object(R.socket, "getaddrinfo", return_value=infos):
            err, _ = R._validate_fetch_url("http://mixed.example/x")
            self.assertEqual(err, "blocked")

    def test_validate_allows_public_host(self):
        with patch.object(R.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("8.8.8.8", 0))]):
            err, _ = R._validate_fetch_url("http://public.example/img.jpg")
            self.assertIsNone(err)

    def test_pinned_resolution_returns_only_validated_addresses(self):
        infos = [
            (2, 1, 6, "", ("8.8.8.8", 443)),
            (2, 1, 6, "", ("1.1.1.1", 443)),
        ]
        with patch.object(R.socket, "getaddrinfo", return_value=infos):
            err, detail, addresses = R._resolve_public_addresses(
                "public.example", 443)
        self.assertIsNone(err, detail)
        self.assertEqual(addresses, [("8.8.8.8", 2), ("1.1.1.1", 2)])

    def test_capability_binds_url_and_expiry(self):
        exp = int(R.time.time()) + 60
        token = R._web_photo_sign("https://example.test/a.jpg", exp, "key")
        self.assertTrue(R._web_photo_verify(
            "https://example.test/a.jpg", exp, token, "key"))
        self.assertFalse(R._web_photo_verify(
            "https://example.test/b.jpg", exp, token, "key"))
        self.assertFalse(R._web_photo_verify(
            "https://example.test/a.jpg", 1, token, "key"))

    def test_streaming_limit_aborts_before_unbounded_buffering(self):
        class Content:
            async def iter_chunked(self, _size):
                yield b"a" * R._WEB_PHOTO_MAX_BYTES
                yield b"b"

        with self.assertRaises(R._ImageTooLarge):
            asyncio.run(R._read_web_photo_limited(Content()))

    def test_anonymous_proxy_requires_issued_capability(self):
        class Request:
            query = {"u": "https://example.test/a.jpg"}
            app = {"admin_key": "key"}

        response = asyncio.run(R.photo_web_proxy(Request()))
        self.assertEqual(response.status, 401)


class _FakeRequest:
    """Stub minimale di aiohttp.web.Request per auth_middleware."""

    def __init__(self, remote, headers=None, path="/agent/turn"):
        self.remote = remote
        self.headers = headers or {}
        self.cookies = {}
        self.path = path
        self.app = {"admin_key": "k"}
        self._store = {}

    def __setitem__(self, k, v):
        self._store[k] = v

    def __getitem__(self, k):
        return self._store[k]


def _run_mw(req):
    """Esegue il middleware e ritorna il ruolo classificato.

    Il middleware setta `request["role"]` PRIMA di applicare la policy del
    path (401/403), quindi leggiamo dal request object: e' robusto sia che
    l'handler venga raggiunto (user/admin) sia che venga corto-circuitato
    (anonymous su path protetto)."""
    async def handler(r):
        from aiohttp import web
        return web.Response(text="ok")

    asyncio.new_event_loop().run_until_complete(
        http_auth.auth_middleware(req, handler)
    )
    return req._store.get("role")


class TestForwardedHeaderSpoof(unittest.TestCase):

    def test_spoofed_xff_from_internet_not_user(self):
        # Peer reale = IP pubblico (NON proxy fidato) ma header spoofato.
        req = _FakeRequest("203.0.113.7",
                           {"X-Forwarded-For": "127.0.0.1"})
        self.assertEqual(_run_mw(req), "anonymous")

    def test_spoofed_forwarded_proto_from_internet_not_user(self):
        req = _FakeRequest("203.0.113.7",
                           {"Forwarded": "for=10.0.0.5;proto=https"})
        self.assertEqual(_run_mw(req), "anonymous")

    def test_trusted_proxy_lan_client_is_anonymous_by_default(self):
        # Il proxy fidato puo' attestare l'IP reale, ma la provenienza LAN da
        # sola non concede piu' l'identita' host.
        req = _FakeRequest("127.0.0.1",
                           {"X-Forwarded-For": "192.168.1.20"})
        self.assertEqual(_run_mw(req), "anonymous")

    def test_trusted_proxy_loopback_honors_xff_internet_anon(self):
        # Il client attestato dal proxy e' su Internet → anonymous.
        req = _FakeRequest("127.0.0.1",
                           {"X-Forwarded-For": "203.0.113.7"})
        self.assertEqual(_run_mw(req), "anonymous")

    def test_plain_lan_peer_is_anonymous_by_default(self):
        # Anche il peer LAN diretto deve autenticarsi o essere esplicitamente
        # isolato tramite la compatibilita' opt-in.
        req = _FakeRequest("192.168.1.50", {})
        self.assertEqual(_run_mw(req), "anonymous")

    def test_lan_compatibility_is_opt_in_and_synthetic(self):
        req = _FakeRequest("192.168.1.50", {})
        with patch.dict(os.environ, {"METNOS_TRUST_LAN_ANONYMOUS": "1"}):
            self.assertEqual(_run_mw(req), "user")
        self.assertTrue(req._store["lan_principal"].startswith("http_lan_"))
        self.assertIsNone(req._store["authenticated_user_id"])


class TestUserCookieRevocationLookup(unittest.TestCase):

    def test_valid_signature_reports_store_unavailable(self):
        cookie = http_auth.issue_user_cookie("admin-secret", "device-1")
        with patch.object(users, "find_user_by_recipient",
                          side_effect=OSError("users db unavailable")):
            with self.assertRaises(http_auth.IdentityStoreUnavailable):
                http_auth.verify_user_cookie(cookie, "admin-secret")

    def test_middleware_returns_retryable_503_for_valid_user_cookie(self):
        req = _FakeRequest("203.0.113.7")
        req.app = {"admin_key": "admin-secret"}
        req.cookies = {
            http_auth.USER_COOKIE: http_auth.issue_user_cookie(
                "admin-secret", "device-1"),
        }

        async def handler(_request):
            from aiohttp import web
            return web.Response(text="should not run")

        with patch.object(users, "find_user_by_recipient",
                          side_effect=OSError("users db unavailable")):
            response = asyncio.run(http_auth.auth_middleware(req, handler))
        self.assertEqual(response.status, 503)
        self.assertEqual(response.headers["Retry-After"], "2")


if __name__ == "__main__":
    unittest.main()
