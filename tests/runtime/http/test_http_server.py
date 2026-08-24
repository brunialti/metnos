"""Smoke + auth + ETag tests per metnos_http_server (HTTP API Phase 1).

Run con: `python3 -m pytest tests/runtime/http/test_http_server.py -v`.

Usa `AioHTTPTestCase` da aiohttp.test_utils (nessuna dipendenza esterna
oltre aiohttp; `pytest-aiohttp` non disponibile su questo sistema).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

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

    def host_id(self) -> str:
        import users
        hosts = users.list_users(role="host")
        self.assertEqual(len(hosts), 1)
        return str(hosts[0]["id"])

    # ── Tests ──────────────────────────────────────────────────────

    async def test_health_anonymous(self):
        """GET /agent/health -> 200, JSON ok=true (no auth)."""
        r = await self.client.get("/agent/health")
        self.assertEqual(r.status, 200)
        body = await r.json()
        self.assertTrue(body["ok"])
        self.assertIn("version", body)
        self.assertIn("uptime_s", body)
        self.assertIn(body["durable_workloads"]["state"], {
            "ready", "recovering", "degraded",
        })

    async def test_health_stays_available_when_durable_worker_is_stopped(self):
        """F8 must not move durable execution into the HTTP process."""
        from durable_workloads.service import DurableWorkerService

        root = Path(self._tmpdir.name) / "durable-worker-stopped"
        service = DurableWorkerService(
            enabled=False,
            store_path=root / "state.sqlite3",
            health_path=root / "service_health.json",
        )
        try:
            self.assertTrue(service.start())
        finally:
            service.stop()
        import durable_workloads.service as durable_service

        with mock.patch.object(
            durable_service, "default_health_path",
            return_value=root / "service_health.json",
        ):
            response = await self.client.get("/agent/health")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["durable_workloads"]["state"], "degraded")
        self.assertEqual(payload["durable_workloads"]["reason_code"], "stopped")

    async def test_well_known_anonymous(self):
        """GET /.well-known/metnos.json -> 200 JSON con discovery descriptor."""
        r = await self.client.get("/.well-known/metnos.json")
        self.assertEqual(r.status, 200)
        body = await r.json()
        self.assertEqual(body["name"], "metnos")
        self.assertIn("http", body["channels"])
        self.assertIn("agent.turn", body["capabilities"])
        self.assertEqual(body["pairing_url"], "/agent/register")
        self.assertNotIn("public_key_fingerprint", body)

    async def test_advertised_device_registration_route_is_real(self):
        # GET must be method-rejected, not route-missing. Avoid importing the
        # pairing signer under this suite's intentionally isolated HOME.
        r = await self.client.get("/agent/register")
        self.assertEqual(r.status, 405)

    async def test_chat_navigation_and_copy_contract(self):
        """Chat resta primaria; la copia include metadati e non espone clear."""
        r = await self.client.get("/", headers=self.admin_hdr())
        self.assertEqual(r.status, 200)
        body = await r.text()
        self.assertIn('href="/admin">', body)
        self.assertIn("id=\"copyChatBtn\"", body)
        self.assertNotIn("id=\"clearBtn\"", body)
        self.assertNotIn("case 'clear':", body)
        self.assertIn("lines.push(chatText('transcriptTurn'", body)
        self.assertIn("lines.push(chatText('transcriptPath'", body)
        self.assertIn("const response = await fetch(formPath", body)
        self.assertIn("ifr.srcdoc = formHtml", body)
        self.assertIn("removeDialogHistory(dialogId)", body)
        self.assertIn("if(inlineFormWrap) d.appendChild(inlineFormWrap)", body)

    async def test_admin_changes_unauthorized(self):
        """A JSON client gets a stable, actionable authentication error."""
        r = await self.client.get(
            "/admin/changes", headers={"Accept": "application/json"})
        self.assertEqual(r.status, 401)
        body = await r.json()
        self.assertEqual(body["error"], "admin_session_required")
        self.assertEqual(
            body["login_url"],
            "/admin/login?next=%2Fadmin%2Fchanges",
        )

    async def test_admin_html_navigation_returns_to_requested_page(self):
        first = await self.client.get(
            "/admin/users?view=all",
            headers={"Accept": "text/html"},
            allow_redirects=False,
        )
        self.assertEqual(first.status, 302)
        self.assertEqual(
            first.headers["Location"],
            "/admin/login?next=/admin/users?view%3Dall",
        )
        login = await self.client.post(
            first.headers["Location"],
            data={"key": ADMIN_KEY, "next": "/admin/users?view=all"},
            headers={"Accept": "text/html"},
            allow_redirects=False,
        )
        self.assertEqual(login.status, 302)
        self.assertEqual(login.headers["Location"], "/admin/users?view=all")

    async def test_admin_login_rejects_cross_origin_next(self):
        response = await self.client.post(
            "/admin/login?next=https://example.com/steal",
            data={"key": ADMIN_KEY,
                  "next": "https://example.com/steal"},
            allow_redirects=False,
        )
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], "/admin")

    async def test_admin_mutation_is_never_redirected_to_login(self):
        response = await self.client.post(
            "/admin/users",
            data={"name": "must-not-run"},
            headers={"Accept": "text/html"},
            allow_redirects=False,
        )
        self.assertEqual(response.status, 401)
        body = await response.json()
        self.assertEqual(body["error"], "admin_session_required")
        self.assertEqual(body["login_url"], "/admin/login")

    # NB (13/6/2026): /admin/proposals rimossa (superata da /admin/changes,
    # ADR 0158). Il test ETag usa ora una collezione admin viva (/admin/executors).

    async def test_etag_304(self):
        """Stesso GET due volte con If-None-Match -> secondo è 304."""
        r1 = await self.client.get(
            "/admin/executors",
            headers=self.admin_hdr(**{"Accept": "application/json"}),
        )
        self.assertEqual(r1.status, 200)
        etag = r1.headers.get("ETag", "").strip('"')
        self.assertTrue(etag, "ETag header missing")

        r2 = await self.client.get(
            "/admin/executors",
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
        """GET /admin -> HTML di Settings (home console)."""
        r = await self.client.get("/admin", headers=self.admin_hdr())
        self.assertEqual(r.status, 200)
        body = await r.text()
        # Nome canonico della home (nav + H1 + <title>): "Settings".
        self.assertIn("Settings", body)
        self.assertIn('class="chat-link" href="/">Chat</a>', body)
        self.assertIn("Turni recenti", body)

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
            "started_at": datetime.now(timezone.utc).isoformat(),
            "actor": "host", "channel": "http",
            "owner_user_id": self.host_id(),
            "origin_turn_id": "turn_dialog_001",
            "timeout_s": 3600,
            "completed": False, "cancelled": False,
        }
        _dp.save_pending("http:host", "test_dialog_001", state)
        r = await self.client.get(
            "/agent/dialog/test_dialog_001/form",
            headers=self.admin_hdr(**{"Accept": "text/html"}),
        )
        self.assertEqual(r.status, 200)
        body = await r.text()
        self.assertIn("Test form", body)
        self.assertIn("Nome:", body)
        self.assertIn("Ok?", body)
        self.assertIn("turn_dialog_001", body)
        self.assertIn("test_dialog_001", body)
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")

        cancelled = await self.client.get(
            "/agent/dialog/test_dialog_001/cancel",
            headers=self.admin_hdr(**{"Accept": "text/html"}),
        )
        self.assertEqual(cancelled.status, 200)
        self.assertEqual(
            cancelled.headers.get("X-Metnos-Dialog-State"), "cancelled")
        cancelled_form = await self.client.get(
            "/agent/dialog/test_dialog_001/form",
            headers=self.admin_hdr(**{"Accept": "text/html"}),
        )
        self.assertEqual(cancelled_form.status, 410)
        self.assertEqual(
            cancelled_form.headers.get("X-Metnos-Dialog-State"), "cancelled")

        state["completed"] = True
        _dp.save_pending("http:host", "test_dialog_001", state)
        completed = await self.client.get(
            "/agent/dialog/test_dialog_001/form",
            headers=self.admin_hdr(**{"Accept": "text/html"}),
        )
        self.assertEqual(completed.status, 410)
        self.assertEqual(
            completed.headers.get("X-Metnos-Dialog-State"), "completed")

        state["completed"] = False
        state["started_at"] = "2020-01-01T00:00:00+00:00"
        _dp.save_pending("http:host", "test_dialog_001", state)
        expired = await self.client.get(
            "/agent/dialog/test_dialog_001/form",
            headers=self.admin_hdr(**{"Accept": "text/html"}),
        )
        self.assertEqual(expired.status, 410)
        self.assertEqual(
            expired.headers.get("X-Metnos-Dialog-State"), "expired")
        expired_submit = await self.client.post(
            "/agent/dialog/test_dialog_001/submit",
            data={"name": "A", "ok": "yes"},
            headers=self.admin_hdr())
        self.assertEqual(expired_submit.status, 410)
        self.assertEqual((await expired_submit.json())["state"], "expired")

    async def test_dialog_owner_binding_and_cross_device_capability(self):
        """Un altro user è negato; il link firmato delega solo quel dialogo."""
        import dialog_pending as _dp
        import http_routes_agent as _routes

        dialog_id = "owned_dialog_001"
        state = {
            "dialog_id": dialog_id,
            "title": "Owner-bound form",
            "dialog": [{"var": "value", "prompt": "Valore:",
                        "schema": {"kind": "text"}}],
            "fmt": "form", "values_collected": {}, "step_index": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "actor": "other-device", "channel": "http",
            "owner_user_id": "other-user-id", "timeout_s": 3600,
            "completed": False, "cancelled": False,
        }
        _dp.save_pending("http:other-device:_", dialog_id, state)

        denied = await self.client.get(f"/agent/dialog/{dialog_id}/form")
        self.assertEqual(denied.status, 403)

        cap = _routes._dialog_cap_sign(dialog_id, ADMIN_KEY)
        allowed = await self.client.get(
            f"/agent/dialog/{dialog_id}/form?cap={cap}")
        self.assertEqual(allowed.status, 200, await allowed.text())
        html = await allowed.text()
        self.assertIn(f"/agent/dialog/{dialog_id}/submit?cap={cap}", html)
        self.assertEqual(allowed.headers.get("Referrer-Policy"), "no-referrer")

        tampered = cap[:-1] + ("0" if cap[-1] != "0" else "1")
        rejected = await self.client.get(
            f"/agent/dialog/{dialog_id}/form?cap={tampered}")
        self.assertEqual(rejected.status, 403)

        submitted = await self.client.post(
            f"/agent/dialog/{dialog_id}/submit?cap={cap}",
            data={"value": "ok"},
        )
        self.assertEqual(submitted.status, 200, await submitted.text())
        final = _dp.load_pending(
            "http:other-device:_", dialog_id,
            owner_user_id="other-user-id")
        self.assertTrue(final["completed"])
        self.assertEqual(final["values_collected"], {"value": "ok"})

    # ── /admin/praxis/fastpaths/{id}/delete (valvola L0) ───────────

    async def test_admin_praxis_fastpath_delete(self):
        """POST delete su fastpath esistente -> 200 + riga rimossa;
        secondo delete -> 404 (già rimosso)."""
        import tempfile as _tf
        from unittest import mock as _mock
        from engine import fastpath as eng_fastpath
        from engine.types import Framework, StepSpec
        tmp = _tf.mkdtemp()
        orig = eng_fastpath._db_path
        eng_fastpath._db_path = lambda: Path(tmp) / "fastpaths.sqlite"
        try:
            fw = Framework(steps=[StepSpec(tool="get_now", args={}),
                                  StepSpec(tool="final_answer", args={})],
                           final_message="x")
            with _mock.patch("engine.cluster.embed", new=lambda q: None):
                fp_id = eng_fastpath.record_success("che ora è", fw)
            self.assertGreater(fp_id, 0)
            r = await self.client.post(
                f"/admin/praxis/fastpaths/{fp_id}/delete",
                headers=self.admin_hdr())
            self.assertEqual(r.status, 200)
            body = await r.json()
            self.assertTrue(body["ok"])
            self.assertEqual(eng_fastpath.list_all(), [])
            r2 = await self.client.post(
                f"/admin/praxis/fastpaths/{fp_id}/delete",
                headers=self.admin_hdr())
            self.assertEqual(r2.status, 404)
        finally:
            eng_fastpath._db_path = orig

    async def test_admin_praxis_fastpath_delete_unauthorized(self):
        """POST delete senza sessione resta inerte e chiede autenticazione."""
        r = await self.client.post("/admin/praxis/fastpaths/1/delete")
        self.assertEqual(r.status, 401)
        body = await r.json()
        self.assertEqual(body["error"], "admin_session_required")
        self.assertEqual(body["login_url"], "/admin/login")

    async def test_admin_praxis_fastpath_delete_bad_id(self):
        """POST delete con id non-int -> 400."""
        r = await self.client.post(
            "/admin/praxis/fastpaths/abc/delete",
            headers=self.admin_hdr())
        self.assertEqual(r.status, 400)


if __name__ == "__main__":
    unittest.main()
