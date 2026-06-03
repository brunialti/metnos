"""Bonifica 28/5 — difese SSRF (photo_web_proxy) + spoof X-Forwarded-For.

- http_routes_agent: l'endpoint anonimo `/agent/photos/web?u=<url>` non deve
  poter colpire host interni (loopback/privati/link-local/riservati).
- http_auth: gli header forwarded (CF-Connecting-IP / X-Forwarded-For) sono
  onorati SOLO se il peer TCP reale e' un proxy fidato; altrimenti uno spoof
  `X-Forwarded-For: 127.0.0.1` da Internet NON deve promuovere a `user`.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

import http_auth  # noqa: E402
import http_routes_agent as R  # noqa: E402


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

    def test_spoofed_cf_ip_from_internet_not_user(self):
        req = _FakeRequest("203.0.113.7",
                           {"CF-Connecting-IP": "10.0.0.5"})
        self.assertEqual(_run_mw(req), "anonymous")

    def test_trusted_proxy_loopback_honors_cf_ip_lan(self):
        # Tunnel Cloudflare: peer = 127.0.0.1 (fidato di default), il client
        # reale e' su LAN → onora CF-Connecting-IP → ruolo user.
        req = _FakeRequest("127.0.0.1",
                           {"CF-Connecting-IP": "192.168.1.20"})
        self.assertEqual(_run_mw(req), "user")

    def test_trusted_proxy_loopback_honors_cf_ip_internet_anon(self):
        # Stesso tunnel ma il client reale e' su Internet → anonymous.
        req = _FakeRequest("127.0.0.1",
                           {"CF-Connecting-IP": "203.0.113.7"})
        self.assertEqual(_run_mw(req), "anonymous")

    def test_plain_lan_peer_still_user(self):
        # Nessun header forwarded, peer reale su LAN → user (comportamento
        # legittimo preservato).
        req = _FakeRequest("192.168.1.50", {})
        self.assertEqual(_run_mw(req), "user")


if __name__ == "__main__":
    unittest.main()
