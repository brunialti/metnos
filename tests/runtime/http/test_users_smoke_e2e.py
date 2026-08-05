"""Smoke E2E /admin/users (sprint 4/5/2026, ADR 0083).

Esercita flusso completo in-process: list bootstrap → create guest →
pair token → detail → autonomy → delete. Equivalente al curl smoke
richiesto nel task ma senza dipendenza da systemd restart.

Run: `python3 -m pytest tests/runtime/http/test_users_smoke_e2e.py -v`.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402

ADMIN_KEY = "smoke-e2e-key"


class UsersSmokeE2E(AioHTTPTestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        td = Path(cls._tmpdir.name)
        cls._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(td)
        os.environ["METNOS_USERS_DB"] = str(td / "users.db")
        os.environ["METNOS_PROPOSALS_STATE_DB"] = str(td / "p.db")
        os.environ["SAFETY_DB_PATH"] = str(td / "s.db")
        os.environ["SCHEDULER_DB_PATH"] = str(td / "sc.db")

        import importlib
        import http_auth
        import proposals_state
        import users as _users
        importlib.reload(proposals_state)
        importlib.reload(http_auth)
        importlib.reload(_users)
        import http_routes_admin
        importlib.reload(http_routes_admin)
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

    def _hdr(self, accept="application/json"):
        return {"Authorization": f"Bearer {ADMIN_KEY}", "Accept": accept}

    async def test_full_lifecycle_e2e(self):
        # 1. list (bootstrap host)
        r = await self.client.get("/admin/users", headers=self._hdr())
        self.assertEqual(r.status, 200)
        body = await r.json()
        roles = [u["role"] for u in body["rows"]]
        self.assertIn("host", roles)
        # 2. create guest
        r = await self.client.post(
            "/admin/users",
            data={"name": "lucia", "role": "guest",
                  "autonomy_level": "restricted",
                  "display_name": "Lucia"},
            headers=self._hdr(),
        )
        self.assertEqual(r.status, 201)
        u = await r.json()
        self.assertEqual(u["name"], "lucia")
        # 3. pair telegram → token
        r = await self.client.post(
            f"/admin/users/{u['id']}/channels/telegram/pair",
            headers=self._hdr(),
        )
        self.assertEqual(r.status, 200)
        body = await r.json()
        self.assertEqual(len(body["token"]), 32)
        self.assertIn("/start", body["instructions"])
        # 4. detail JSON
        r = await self.client.get(f"/admin/users/{u['id']}",
                                    headers=self._hdr())
        self.assertEqual(r.status, 200)
        body = await r.json()
        chans = body["channels"]
        self.assertEqual(len(chans), 1)
        self.assertEqual(chans[0]["channel"], "telegram")
        self.assertFalse(chans[0]["verified"])  # token emesso, non consumato
        # 5. detail HTML
        r = await self.client.get(f"/admin/users/{u['id']}",
                                    headers=self._hdr("text/html"))
        text = await r.text()
        self.assertIn("lucia", text)
        self.assertIn("telegram", text)
        # 6. set autonomy → full
        r = await self.client.post(
            f"/admin/users/{u['id']}/autonomy",
            data={"autonomy_level": "full"}, headers=self._hdr(),
        )
        self.assertEqual(r.status, 200)
        # 7. dashboard root mostra users summary
        r = await self.client.get("/admin", headers=self._hdr("text/html"))
        text = await r.text()
        self.assertIn("Utenti", text)
        self.assertIn("/admin/users", text)
        # 8. delete
        r = await self.client.post(f"/admin/users/{u['id']}/delete",
                                     headers=self._hdr())
        self.assertEqual(r.status, 200)
        # 9. verify deletion
        r = await self.client.get(f"/admin/users/{u['id']}",
                                    headers=self._hdr())
        self.assertEqual(r.status, 404)


if __name__ == "__main__":
    import unittest
    unittest.main()
