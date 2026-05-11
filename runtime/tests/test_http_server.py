"""Smoke + auth + ETag tests per metnos_http_server (HTTP API Phase 1).

Run con: `python3 -m pytest runtime/tests/test_http_server.py -v`.

Usa `AioHTTPTestCase` da aiohttp.test_utils (nessuna dipendenza esterna
oltre aiohttp; `pytest-aiohttp` non disponibile su questo sistema).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from aiohttp.test_utils import AioHTTPTestCase


ADMIN_KEY = "test-admin-key-fixed"


class HttpServerTests(AioHTTPTestCase):

    @classmethod
    def setUpClass(cls):
        # Dirs isolate per non toccare ~/.local del sistema reale.
        cls._tmpdir = tempfile.TemporaryDirectory()
        td = Path(cls._tmpdir.name)
        # Preserva l'HOME originale per ripristinarlo in tearDownClass (evita
        # di rompere test successivi che usano Path.home() per classificare
        # path - es. test_safety_admin_sudoer::test_basic_ls).
        cls._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(td)
        os.environ["METNOS_PROPOSALS_STATE_DB"] = str(td / "proposals_state.db")
        os.environ["SAFETY_DB_PATH"] = str(td / "safety.db")
        os.environ["SCHEDULER_DB_PATH"] = str(td / "scheduler.sqlite")
        os.environ["METNOS_USERS_DB"] = str(td / "users.db")

        # Reload moduli che leggono path da env all'import.
        import importlib
        import http_auth
        import proposals_state
        import users
        importlib.reload(proposals_state)
        importlib.reload(http_auth)
        importlib.reload(users)
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

    # ── Helpers ────────────────────────────────────────────────────

    def admin_hdr(self, **extra) -> dict:
        h = {"Authorization": f"Bearer {ADMIN_KEY}"}
        h.update(extra)
        return h

    # ── Tests ──────────────────────────────────────────────────────

    async def test_health_anonymous(self):
        """GET /agent/health -> 200, JSON ok=true (no auth)."""
        r = await self.client.get("/agent/health")
        self.assertEqual(r.status, 200)
        body = await r.json()
        self.assertTrue(body["ok"])
        self.assertIn("version", body)
        self.assertIn("uptime_s", body)

    async def test_well_known_anonymous(self):
        """GET /.well-known/metnos.json -> 200 JSON con discovery descriptor."""
        r = await self.client.get("/.well-known/metnos.json")
        self.assertEqual(r.status, 200)
        body = await r.json()
        self.assertEqual(body["name"], "metnos")
        self.assertIn("http", body["channels"])
        self.assertIn("agent.turn", body["capabilities"])

    async def test_admin_proposals_unauthorized(self):
        """GET /admin/proposals senza header -> 403 (anonymous su path admin).

        Nota: il test client puo' presentarsi come 127.0.0.1 (LAN trusted →
        ruolo `user`), che pero' non e' admin: la policy admin_prefix
        richiede admin → 403.
        """
        r = await self.client.get("/admin/proposals")
        self.assertEqual(r.status, 403)

    async def test_admin_proposals_with_admin_key(self):
        """GET /admin/proposals con admin key + Accept JSON -> 200 con rows."""
        r = await self.client.get(
            "/admin/proposals",
            headers=self.admin_hdr(**{"Accept": "application/json"}),
        )
        self.assertEqual(r.status, 200)
        body = await r.json()
        self.assertIn("rows", body)
        self.assertIsInstance(body["rows"], list)

    async def test_etag_304(self):
        """Stesso GET due volte con If-None-Match -> secondo è 304."""
        r1 = await self.client.get(
            "/admin/proposals",
            headers=self.admin_hdr(**{"Accept": "application/json"}),
        )
        self.assertEqual(r1.status, 200)
        etag = r1.headers.get("ETag", "").strip('"')
        self.assertTrue(etag, "ETag header missing")

        r2 = await self.client.get(
            "/admin/proposals",
            headers=self.admin_hdr(**{
                "Accept": "application/json",
                "If-None-Match": f'"{etag}"',
            }),
        )
        self.assertEqual(r2.status, 304)

    async def test_admin_executors_html(self):
        """GET /admin/executors con Accept text/html -> HTML response."""
        r = await self.client.get(
            "/admin/executors",
            headers=self.admin_hdr(**{"Accept": "text/html"}),
        )
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", r.headers.get("Content-Type", ""))
        body = await r.text()
        self.assertIn("Catalog", body)
        self.assertIn("<table>", body)

    async def test_dashboard_root(self):
        """GET /admin -> HTML dashboard."""
        r = await self.client.get("/admin", headers=self.admin_hdr())
        self.assertEqual(r.status, 200)
        body = await r.text()
        self.assertIn("Metnos admin", body)
        self.assertIn("Turni recenti", body)

    async def test_admin_proposal_action_404(self):
        """POST /admin/proposals/{unknown}/approve -> 404."""
        r = await self.client.post(
            "/admin/proposals/no-such-sig-key/approve",
            headers=self.admin_hdr(),
        )
        self.assertEqual(r.status, 404)

    async def test_admin_proposal_action_invalid_action(self):
        """POST con action diversa da approve|reject|defer -> 404 (route mismatch)."""
        r = await self.client.post(
            "/admin/proposals/some-sig/bogus",
            headers=self.admin_hdr(),
        )
        self.assertEqual(r.status, 404)

    async def test_admin_runs_json(self):
        """GET /admin/runs -> 200 JSON con rows."""
        r = await self.client.get(
            "/admin/runs",
            headers=self.admin_hdr(**{"Accept": "application/json"}),
        )
        self.assertEqual(r.status, 200)
        body = await r.json()
        self.assertIn("rows", body)

    async def test_admin_safety_json(self):
        """GET /admin/safety -> 200 JSON."""
        r = await self.client.get(
            "/admin/safety",
            headers=self.admin_hdr(**{"Accept": "application/json"}),
        )
        self.assertEqual(r.status, 200)
        body = await r.json()
        self.assertIn("rows", body)

    async def test_admin_executors_stats(self):
        """GET /admin/executors/stats -> JSON con counts + daily events."""
        r = await self.client.get(
            "/admin/executors/stats", headers=self.admin_hdr(),
        )
        self.assertEqual(r.status, 200)
        body = await r.json()
        self.assertIn("counts_by_source_lifecycle", body)
        self.assertIn("daily_event_counts", body)

    # --- /admin/users endpoint (multi-user, ADR 0083) ---------------------

    async def test_admin_users_list_creates_host_bootstrap(self):
        """GET /admin/users -> 200 con almeno 1 host (bootstrap automatico)."""
        r = await self.client.get(
            "/admin/users",
            headers=self.admin_hdr(**{"Accept": "application/json"}),
        )
        self.assertEqual(r.status, 200)
        body = await r.json()
        self.assertIn("rows", body)
        roles = [u["role"] for u in body["rows"]]
        self.assertIn("host", roles)

    async def test_admin_users_create_and_pair(self):
        """POST /admin/users crea guest; POST .../channels/telegram/pair emette token."""
        r = await self.client.post(
            "/admin/users",
            data={"name": "lucia", "role": "guest", "autonomy_level": "restricted"},
            headers=self.admin_hdr(**{"Accept": "application/json"}),
        )
        self.assertEqual(r.status, 201)
        u = await r.json()
        self.assertEqual(u["name"], "lucia")
        # Pair telegram
        r2 = await self.client.post(
            f"/admin/users/{u['id']}/channels/telegram/pair",
            headers=self.admin_hdr(**{"Accept": "application/json"}),
        )
        self.assertEqual(r2.status, 200)
        body2 = await r2.json()
        self.assertTrue(body2["ok"])
        self.assertEqual(len(body2["token"]), 32)
        self.assertIn("/start", body2["instructions"])

    async def test_admin_users_invalid_name(self):
        """POST /admin/users con name invalido -> 400."""
        r = await self.client.post(
            "/admin/users",
            data={"name": "Lu Cia!", "role": "guest"},
            headers=self.admin_hdr(**{"Accept": "application/json"}),
        )
        self.assertEqual(r.status, 400)

    # --- /agent/dialog/<id>/{form,submit,cancel} (ADR 0090) ---------------

    async def test_dialog_form_404_for_unknown_id(self):
        """GET /agent/dialog/<id>/form per dialogo inesistente -> 404."""
        r = await self.client.get("/agent/dialog/nonexistentXYZ/form")
        self.assertEqual(r.status, 404)

    async def test_dialog_form_renders_for_known_dialogue(self):
        """Crea un dialogo via dialog_pending, poi GET form -> 200 HTML."""
        import sys as _sys
        _sys.path.insert(0, str(_RUNTIME))
        import dialog_pending as _dp
        # Re-pin DIALOG_DIR alla home isolata (HOME e' gia' sovrascritto
        # in setUpClass; ricarichiamo per leggere il path corrente)
        import importlib
        importlib.reload(_dp)
        state = {
            "dialog_id": "test_dialog_001",
            "title": "Test form",
            "description": "Compila i campi",
            "dialog": [
                {"var": "name", "prompt": "Nome:",
                 "schema": {"kind": "text"}},
                {"var": "ok", "prompt": "Ok?",
                 "schema": {"kind": "yes_no"}},
            ],
            "fmt": "form",
            "values_collected": {},
            "step_index": 0,
            "started_at": "2026-05-04T19:00:00+00:00",
            "actor": "host", "channel": "http",
            "timeout_s": 3600,
            "completed": False, "cancelled": False,
        }
        _dp.save_pending("http:host", "test_dialog_001", state)
        r = await self.client.get(
            "/agent/dialog/test_dialog_001/form",
            headers={"Accept": "text/html"},
        )
        self.assertEqual(r.status, 200)
        body = await r.text()
        self.assertIn("Test form", body)
        self.assertIn("Nome:", body)
        self.assertIn("Ok?", body)


if __name__ == "__main__":
    unittest.main()
