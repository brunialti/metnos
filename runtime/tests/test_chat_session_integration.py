"""Integration smoke per session takeover end-to-end (Phase 7 Phase 1, 12/5/2026).

Simula due "device" che si contendono la stessa sessione HTTP:
1. Device A registra → ottiene device_token_A.
2. Device B registra → riceve 409 conflict + takeover_token.
3. Device B chiama takeover → ottiene device_token_B, A e' revocato.
4. Device A pinga → riceve 409 (deve ri-registrare).
5. Device B pinga → 200 ok.
6. Device A si re-registra → di nuovo conflict (B e' adesso il writer).

Inoltre verifica SSE: A puo' sottoscriversi a /agent/session/events e
ricevere `session_revoked` quando B fa takeover.
"""
from __future__ import annotations

import asyncio
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


class TwoDeviceSessionTests(AioHTTPTestCase):

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
        await super().setUpAsync()
        from active_sessions import _PENDING_TAKEOVERS, _SUBSCRIBERS
        _PENDING_TAKEOVERS.clear()
        _SUBSCRIBERS.clear()
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

    async def _post(self, path, body):
        r = await self.client.post(
            path, headers=self.hdr(), data=json.dumps(body),
        )
        try:
            data = await r.json()
        except Exception:
            data = {}
        return r.status, data

    async def test_full_takeover_flow_two_devices(self):
        # Step 1: Device A registra
        st1, b1 = await self._post(
            "/agent/session/register", {"device_label": "Laptop A"},
        )
        self.assertEqual(st1, 200, f"unexpected register A status: {st1}")
        token_a = b1["device_token"]
        self.assertFalse(b1.get("conflict"))

        # Step 2: Device B registra -> 409 conflict
        st2, b2 = await self._post(
            "/agent/session/register", {"device_label": "Phone B"},
        )
        self.assertEqual(st2, 409)
        self.assertTrue(b2.get("conflict"))
        self.assertEqual(b2["existing"]["device_label"], "Laptop A")
        takeover_token = b2["takeover_token"]
        self.assertTrue(takeover_token)

        # Step 3: Device B accetta -> takeover
        st3, b3 = await self._post(
            "/agent/session/takeover", {
                "takeover_token": takeover_token,
                "device_label": "Phone B",
            },
        )
        self.assertEqual(st3, 200)
        token_b = b3["device_token"]
        self.assertNotEqual(token_a, token_b)
        self.assertEqual(b3["revoked_device_token"], token_a)

        # Step 4: Device A pinga -> 409 (revoked)
        st4, b4 = await self._post(
            "/agent/session/ping", {"device_token": token_a},
        )
        self.assertEqual(st4, 409)
        self.assertTrue(b4.get("revoked"))

        # Step 5: Device B pinga -> 200 ok
        st5, b5 = await self._post(
            "/agent/session/ping", {"device_token": token_b},
        )
        self.assertEqual(st5, 200)
        self.assertTrue(b5.get("ok"))

        # Step 6: Device A re-registra -> di nuovo conflict (B e' il writer)
        st6, b6 = await self._post(
            "/agent/session/register", {"device_label": "Laptop A redux"},
        )
        self.assertEqual(st6, 409)
        self.assertTrue(b6.get("conflict"))
        self.assertEqual(b6["existing"]["device_label"], "Phone B")

    async def test_sse_event_session_revoked_on_takeover(self):
        # Device A si registra; sottoscrive lo stream SSE eventi.
        st1, b1 = await self._post(
            "/agent/session/register", {"device_label": "A"},
        )
        self.assertEqual(st1, 200)
        token_a = b1["device_token"]

        # Stream SSE: apri con client.get (no auto-close), leggi i primi byte
        # mentre B fa takeover in parallelo.
        resp = await self.client.get(
            f"/agent/session/events?device_token={token_a}",
            headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        )
        self.assertEqual(resp.status, 200)

        # Avvia takeover in background
        async def _do_takeover():
            await asyncio.sleep(0.1)
            st2, b2 = await self._post(
                "/agent/session/register", {"device_label": "B"},
            )
            self.assertEqual(st2, 409)
            tk = b2["takeover_token"]
            await self._post("/agent/session/takeover", {
                "takeover_token": tk, "device_label": "B",
            })

        takeover_task = asyncio.create_task(_do_takeover())

        # Leggi dal stream finche' non trovi session_revoked o timeout 5s.
        body = b""
        try:
            async with asyncio.timeout(5.0):
                while b"session_revoked" not in body:
                    chunk = await resp.content.read(1024)
                    if not chunk:
                        break
                    body += chunk
        except (asyncio.TimeoutError, TimeoutError):
            pass

        await takeover_task
        resp.close()

        self.assertIn(b"session_revoked", body,
                      f"expected 'session_revoked' event in SSE stream, got: "
                      f"{body[:200]!r}")
        self.assertIn(b"takeover", body)

    async def test_orphan_token_after_server_restart_handled_by_register(self):
        """Caso edge: client tiene un device_token che il server non
        riconosce piu' (DB rispulito tra restart). Ping -> 409; client
        ri-registra normalmente -> 200 (niente conflict perche' la old
        row e' stata cancellata)."""
        st1, b1 = await self._post(
            "/agent/session/register", {"device_label": "A"},
        )
        token_a = b1["device_token"]
        # Wipe DB sotto al server (simula loss state)
        import sqlite3
        conn = sqlite3.connect(os.environ["METNOS_USERS_DB"])
        try:
            conn.execute("DELETE FROM active_sessions")
            conn.commit()
        finally:
            conn.close()
        # Ping con token vecchio -> 409 (sconosciuto = trattato come revoked)
        st_ping, _ = await self._post(
            "/agent/session/ping", {"device_token": token_a},
        )
        self.assertEqual(st_ping, 409)
        # Re-registra: niente conflict (DB vuoto)
        st2, b2 = await self._post(
            "/agent/session/register", {"device_label": "A redux"},
        )
        self.assertEqual(st2, 200)
        self.assertFalse(b2.get("conflict"))


if __name__ == "__main__":
    unittest.main()
