"""Test HTTP per gli endpoint /agent/session/* (Phase 7 Phase 1, 12/5/2026).

Coperti: register (success + conflict), takeover (success + expired token),
ping (active + revoked), revoke (success + idempotent).
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from aiohttp.test_utils import AioHTTPTestCase


ADMIN_KEY = "test-admin-key-fixed"


class SessionEndpointsTests(AioHTTPTestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        td = Path(cls._tmpdir.name)
        cls._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(td)
        os.environ["METNOS_PROPOSALS_STATE_DB"] = str(td / "proposals_state.db")
        os.environ["SAFETY_DB_PATH"] = str(td / "safety.db")
        os.environ["SCHEDULER_DB_PATH"] = str(td / "scheduler.sqlite")
        os.environ["METNOS_USERS_DB"] = str(td / "users.db")

        import http_auth
        import proposals_state
        import users
        import active_sessions
        importlib.reload(proposals_state)
        importlib.reload(http_auth)
        importlib.reload(users)
        importlib.reload(active_sessions)
        import http_routes_admin
        importlib.reload(http_routes_admin)
        import http_routes_agent
        importlib.reload(http_routes_agent)
        import metnos_http_server
        importlib.reload(metnos_http_server)
        cls._server_mod = metnos_http_server
        cls._active_sessions_mod = active_sessions

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()
        if cls._orig_home is not None:
            os.environ["HOME"] = cls._orig_home
        else:
            os.environ.pop("HOME", None)

    async def get_application(self):
        return self._server_mod.make_app(admin_key=ADMIN_KEY)

    async def setUpAsync(self):
        # IMPORTANT: parent setUpAsync inizializza self.client. Senza super()
        # call la chain rompe (AttributeError 'no attribute client').
        await super().setUpAsync()
        # Pulisci pending takeover tra test (state in-memory).
        from active_sessions import _PENDING_TAKEOVERS, _SUBSCRIBERS
        _PENDING_TAKEOVERS.clear()
        _SUBSCRIBERS.clear()
        # Garantisci schema e poi pulisci DB sessions per isolation.
        import active_sessions as _as
        _as.init_db()
        import sqlite3
        conn = sqlite3.connect(os.environ["METNOS_USERS_DB"])
        try:
            conn.execute("DELETE FROM active_sessions")
            conn.commit()
        finally:
            conn.close()

    def hdr(self):
        return {"Authorization": f"Bearer {ADMIN_KEY}",
                "Content-Type": "application/json"}

    # ── Register ──────────────────────────────────────────────────────

    async def test_register_first_returns_token(self):
        r = await self.client.post(
            "/agent/session/register",
            headers=self.hdr(),
            data=json.dumps({"device_label": "Test Device"}),
        )
        self.assertEqual(r.status, 200)
        b = await r.json()
        self.assertFalse(b.get("conflict"))
        self.assertIn("device_token", b)

    async def test_register_second_returns_409_conflict(self):
        # Primo register
        r1 = await self.client.post(
            "/agent/session/register",
            headers=self.hdr(),
            data=json.dumps({"device_label": "Device A"}),
        )
        self.assertEqual(r1.status, 200)
        # Secondo register: stesso user (admin LAN trusted), stesso channel
        r2 = await self.client.post(
            "/agent/session/register",
            headers=self.hdr(),
            data=json.dumps({"device_label": "Device B"}),
        )
        self.assertEqual(r2.status, 409)
        b = await r2.json()
        self.assertTrue(b.get("conflict"))
        self.assertIn("takeover_token", b)
        self.assertIn("existing", b)
        self.assertEqual(b["existing"]["device_label"], "Device A")

    # ── Takeover ──────────────────────────────────────────────────────

    async def test_takeover_with_valid_token_succeeds(self):
        r1 = await self.client.post(
            "/agent/session/register",
            headers=self.hdr(),
            data=json.dumps({"device_label": "A"}),
        )
        b1 = await r1.json()
        old_token = b1["device_token"]
        r2 = await self.client.post(
            "/agent/session/register",
            headers=self.hdr(),
            data=json.dumps({"device_label": "B"}),
        )
        b2 = await r2.json()
        rt = await self.client.post(
            "/agent/session/takeover",
            headers=self.hdr(),
            data=json.dumps({
                "takeover_token": b2["takeover_token"],
                "device_label": "B",
            }),
        )
        self.assertEqual(rt.status, 200)
        bt = await rt.json()
        self.assertIn("device_token", bt)
        self.assertEqual(bt["revoked_device_token"], old_token)

    async def test_takeover_with_invalid_token_409(self):
        r = await self.client.post(
            "/agent/session/takeover",
            headers=self.hdr(),
            data=json.dumps({"takeover_token": "f" * 32}),
        )
        self.assertEqual(r.status, 409)

    # ── Ping ─────────────────────────────────────────────────────────

    async def test_ping_active_returns_200(self):
        r1 = await self.client.post(
            "/agent/session/register",
            headers=self.hdr(),
            data=json.dumps({"device_label": "A"}),
        )
        b1 = await r1.json()
        rp = await self.client.post(
            "/agent/session/ping",
            headers=self.hdr(),
            data=json.dumps({"device_token": b1["device_token"]}),
        )
        self.assertEqual(rp.status, 200)
        bp = await rp.json()
        self.assertTrue(bp.get("ok"))

    async def test_ping_revoked_returns_409(self):
        r1 = await self.client.post(
            "/agent/session/register",
            headers=self.hdr(),
            data=json.dumps({"device_label": "A"}),
        )
        b1 = await r1.json()
        # Revoca via API
        await self.client.post(
            "/agent/session/revoke",
            headers=self.hdr(),
            data=json.dumps({"device_token": b1["device_token"]}),
        )
        rp = await self.client.post(
            "/agent/session/ping",
            headers=self.hdr(),
            data=json.dumps({"device_token": b1["device_token"]}),
        )
        self.assertEqual(rp.status, 409)
        bp = await rp.json()
        self.assertTrue(bp.get("revoked"))

    # ── Revoke ────────────────────────────────────────────────────────

    async def test_revoke_success_returns_ok_changed(self):
        r1 = await self.client.post(
            "/agent/session/register",
            headers=self.hdr(),
            data=json.dumps({"device_label": "A"}),
        )
        b1 = await r1.json()
        rv = await self.client.post(
            "/agent/session/revoke",
            headers=self.hdr(),
            data=json.dumps({
                "device_token": b1["device_token"],
                "reason": "manual",
            }),
        )
        self.assertEqual(rv.status, 200)
        bv = await rv.json()
        self.assertTrue(bv.get("ok"))
        self.assertTrue(bv.get("changed"))

    async def test_revoke_idempotent(self):
        r1 = await self.client.post(
            "/agent/session/register",
            headers=self.hdr(),
            data=json.dumps({"device_label": "A"}),
        )
        b1 = await r1.json()
        await self.client.post(
            "/agent/session/revoke",
            headers=self.hdr(),
            data=json.dumps({"device_token": b1["device_token"]}),
        )
        rv2 = await self.client.post(
            "/agent/session/revoke",
            headers=self.hdr(),
            data=json.dumps({"device_token": b1["device_token"]}),
        )
        self.assertEqual(rv2.status, 200)
        bv = await rv2.json()
        self.assertTrue(bv.get("ok"))
        self.assertFalse(bv.get("changed"))


if __name__ == "__main__":
    unittest.main()
