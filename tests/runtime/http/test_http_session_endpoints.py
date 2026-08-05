"""Test HTTP per gli endpoint /agent/session/* (Phase 7 Phase 1, 12/5/2026).

Coperti: register (success + conflict), le due modalita' di takeover,
ping (active + revoked), enforcement single-writer e revoke idempotente.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from aiohttp.test_utils import AioHTTPTestCase


ADMIN_KEY = "test-admin-key-fixed"


class SessionEndpointsTests(AioHTTPTestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        td = Path(cls._tmpdir.name)
        cls._orig_home = os.environ.get("HOME")
        cls._orig_tutor = os.environ.get("METNOS_TUTOR")
        os.environ["HOME"] = str(td)
        os.environ["METNOS_TUTOR"] = "0"
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
        if cls._orig_tutor is not None:
            os.environ["METNOS_TUTOR"] = cls._orig_tutor
        else:
            os.environ.pop("METNOS_TUTOR", None)

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
            conn.execute("DELETE FROM chat_conversations")
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
        self.assertIn("conversation_id", b)

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

    async def test_users_have_independent_sessions_history_and_browser_scope(self):
        """Host e guest non condividono lease, token, history o localStorage."""
        import re

        import http_auth
        import users

        host = users.list_users(role="host")[0]
        guest = users.create_user(
            "http_session_guest",
            owner_user_id=host["id"],
        )
        guest_device = "guest-http-session-device"
        users.add_channel(
            guest["id"], "http", guest_device, verified=True,
        )
        guest_cookie = http_auth.issue_user_cookie(ADMIN_KEY, guest_device)
        guest_headers = {
            "Cookie": f"{http_auth.USER_COOKIE}={guest_cookie}",
            "Content-Type": "application/json",
        }

        host_root = await self.client.get("/", headers=self.hdr())
        guest_root = await self.client.get("/", headers=guest_headers)
        self.assertEqual(host_root.status, 200)
        self.assertEqual(guest_root.status, 200)
        host_html = await host_root.text()
        guest_html = await guest_root.text()
        scope_re = re.compile(r'const CHAT_USER_SCOPE = "([0-9a-f]{24})";')
        host_scope = scope_re.search(host_html)
        guest_scope = scope_re.search(guest_html)
        self.assertIsNotNone(host_scope)
        self.assertIsNotNone(guest_scope)
        self.assertNotEqual(host_scope.group(1), guest_scope.group(1))
        self.assertIn("const CHAT_MIGRATE_LEGACY_STORAGE = true;", host_html)
        self.assertIn("const CHAT_MIGRATE_LEGACY_STORAGE = false;", guest_html)

        host_register = await self.client.post(
            "/agent/session/register",
            headers=self.hdr(),
            data=json.dumps({"conversation_id": "c_host_isolated"}),
        )
        guest_register = await self.client.post(
            "/agent/session/register",
            headers=guest_headers,
            data=json.dumps({"conversation_id": "c_guest_isolated"}),
        )
        self.assertEqual(host_register.status, 200)
        self.assertEqual(guest_register.status, 200)
        host_session = await host_register.json()
        guest_session = await guest_register.json()
        self.assertNotEqual(
            host_session["device_token"], guest_session["device_token"],
        )

        forbidden_history = await self.client.get(
            "/agent/turns/recent?conversation_id=c_host_isolated",
            headers=guest_headers,
        )
        self.assertEqual(forbidden_history.status, 403)

        foreign_ping = await self.client.post(
            "/agent/session/ping",
            headers=guest_headers,
            data=json.dumps({
                "device_token": host_session["device_token"],
                "conversation_id": "c_host_isolated",
            }),
        )
        self.assertEqual(foreign_ping.status, 409)

        foreign_submit = await self.client.post(
            "/agent/turn/submit",
            headers=guest_headers,
            data=json.dumps({
                "query": "non deve partire",
                "device_token": host_session["device_token"],
                "conversation_id": "c_host_isolated",
            }),
        )
        self.assertEqual(foreign_submit.status, 409)
        submit_body = await foreign_submit.json()
        self.assertEqual(submit_body["error"], "session_owner_mismatch")

        # Il tentativo guest non revoca ne' muta il writer dell'host.
        host_ping = await self.client.post(
            "/agent/session/ping",
            headers=self.hdr(),
            data=json.dumps({
                "device_token": host_session["device_token"],
                "conversation_id": "c_host_isolated",
            }),
        )
        self.assertEqual(host_ping.status, 200)

    async def test_unmapped_authenticated_device_never_falls_back_to_host(self):
        """Un device autenticato senza owner fallisce senza promozione host."""
        from aiohttp import web

        import http_routes_agent

        with self.assertRaises(web.HTTPUnauthorized):
            await http_routes_agent._resolve_session_user_id({
                "role": "user",
                "device_id": "unmapped-authenticated-device",
            })

    async def test_turn_id_surfaces_enforce_owner_for_every_user_action(self):
        """An opaque turn id never grants read, gallery, retry, or feedback."""
        from datetime import date

        import config as runtime_config
        import http_auth
        import photo_endpoint
        import turn_feedback
        import users
        from turn_events import TurnEventLog

        host = users.list_users(role="host")[0]
        guest = users.create_user(
            "turn_surface_guest",
            owner_user_id=host["id"],
        )
        guest_device = "turn-surface-guest-device"
        users.add_channel(guest["id"], "http", guest_device, verified=True)
        guest_cookie = http_auth.issue_user_cookie(ADMIN_KEY, guest_device)
        guest_headers = {
            "Cookie": f"{http_auth.USER_COOKIE}={guest_cookie}",
            "Content-Type": "application/json",
        }

        event_log = TurnEventLog.get()
        host_live = "host_owned_live_turn"
        event_log.create(
            host_live,
            conversation_id="host_owned_live_conversation",
            actor="host",
            owner_user_id=host["id"],
            query="private host request",
        )
        event_log.append(host_live, "final", {"message": "private result"})
        event_log.close(host_live)

        denied_status = await self.client.get(
            f"/agent/turns/{host_live}", headers=guest_headers,
        )
        self.assertEqual(denied_status.status, 403)
        self.assertEqual((await denied_status.json())["error"], "turn_forbidden")
        denied_stream = await self.client.get(
            f"/agent/turns/{host_live}/stream", headers=guest_headers,
        )
        self.assertEqual(denied_stream.status, 403)

        guest_live = "guest_owned_live_turn"
        event_log.create(
            guest_live,
            conversation_id="guest_owned_live_conversation",
            actor=guest_device,
            owner_user_id=guest["id"],
            query="guest request",
        )
        event_log.append(guest_live, "final", {"message": "guest result"})
        event_log.close(guest_live)
        own_status = await self.client.get(
            f"/agent/turns/{guest_live}", headers=guest_headers,
        )
        self.assertEqual(own_status.status, 200)

        turns_dir = runtime_config.PATH_TURNS
        turns_dir.mkdir(parents=True, exist_ok=True)
        persisted_id = "host_owned_persisted_turn"
        persisted = {
            "turn_id": persisted_id,
            "owner_user_id": host["id"],
            "actor": "host",
            "conversation_id": "host_owned_persisted_conversation",
            "user_query": "private persisted request",
            "final_message": "private persisted result",
            "final_kind": "answer",
            "steps": [],
            "attachments": [],
        }
        with (turns_dir / f"{date.today().isoformat()}.jsonl").open(
                "a", encoding="utf-8") as handle:
            handle.write(json.dumps(persisted) + "\n")

        # These modules resolve their constants at import time. Pinning them
        # to the test installation also proves that no production path is read.
        turn_feedback.TURNS_DIR = turns_dir
        photo_endpoint.TURNS_DIR = turns_dir

        denied_gallery = await self.client.get(
            f"/agent/gallery/{persisted_id}", headers=guest_headers,
        )
        self.assertEqual(denied_gallery.status, 403)
        denied_retry = await self.client.post(
            f"/agent/turns/{persisted_id}/retry",
            headers=guest_headers,
            data=json.dumps({}),
        )
        self.assertEqual(denied_retry.status, 403)
        denied_feedback = await self.client.post(
            f"/agent/turns/{persisted_id}/feedback",
            headers=guest_headers,
            data=json.dumps({"action": "ok"}),
        )
        self.assertEqual(denied_feedback.status, 403)

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

    async def test_continue_existing_returns_old_conversation(self):
        r1 = await self.client.post(
            "/agent/session/register", headers=self.hdr(),
            data=json.dumps({
                "device_label": "Phone",
                "conversation_id": "c_phone_history",
            }),
        )
        self.assertEqual(r1.status, 200)
        r2 = await self.client.post(
            "/agent/session/register", headers=self.hdr(),
            data=json.dumps({
                "device_label": "Windows",
                "conversation_id": "c_windows_local",
            }),
        )
        conflict = await r2.json()
        self.assertEqual(r2.status, 409)
        self.assertTrue(conflict["existing"]["can_continue"])
        takeover = await self.client.post(
            "/agent/session/takeover", headers=self.hdr(),
            data=json.dumps({
                "takeover_token": conflict["takeover_token"],
                "device_label": "Windows",
                "mode": "continue_existing",
            }),
        )
        body = await takeover.json()
        self.assertEqual(takeover.status, 200)
        self.assertEqual(body["mode"], "continue_existing")
        self.assertEqual(body["conversation_id"], "c_phone_history")

    async def test_activate_current_returns_new_device_conversation(self):
        await self.client.post(
            "/agent/session/register", headers=self.hdr(),
            data=json.dumps({"conversation_id": "c_other_device"}),
        )
        r2 = await self.client.post(
            "/agent/session/register", headers=self.hdr(),
            data=json.dumps({"conversation_id": "c_this_device"}),
        )
        conflict = await r2.json()
        takeover = await self.client.post(
            "/agent/session/takeover", headers=self.hdr(),
            data=json.dumps({
                "takeover_token": conflict["takeover_token"],
                "mode": "activate_current",
            }),
        )
        body = await takeover.json()
        self.assertEqual(takeover.status, 200)
        self.assertEqual(body["conversation_id"], "c_this_device")

    async def test_turn_submit_rejects_missing_or_stale_writer(self):
        r1 = await self.client.post(
            "/agent/session/register", headers=self.hdr(),
            data=json.dumps({"conversation_id": "c_writer_old"}),
        )
        first = await r1.json()

        missing = await self.client.post(
            "/agent/turn/submit", headers=self.hdr(),
            data=json.dumps({
                "query": "test",
                "conversation_id": "c_writer_old",
            }),
        )
        self.assertEqual(missing.status, 400)

        r2 = await self.client.post(
            "/agent/session/register", headers=self.hdr(),
            data=json.dumps({"conversation_id": "c_writer_new"}),
        )
        conflict = await r2.json()
        await self.client.post(
            "/agent/session/takeover", headers=self.hdr(),
            data=json.dumps({
                "takeover_token": conflict["takeover_token"],
                "mode": "activate_current",
            }),
        )
        stale = await self.client.post(
            "/agent/turn/submit", headers=self.hdr(),
            data=json.dumps({
                "query": "test",
                "conversation_id": "c_writer_old",
                "device_token": first["device_token"],
            }),
        )
        self.assertEqual(stale.status, 409)
        payload = await stale.json()
        self.assertEqual(payload["error"], "session_revoked")

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
