"""Test deterministici per /agent/dialog/<id>/preview/<idx> (PR5).

Coprono:
  - GET /preview/<idx> ritorna image/jpeg per dialog noto.
  - 404 dialog sconosciuto.
  - 403 path-traversal (preview_image_path fuori dai root consentiti).
  - bbox crop applicato correttamente (synthetic image).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402
from http_app_state import ADMIN_KEY as APP_ADMIN_KEY  # noqa: E402

ADMIN_KEY = "test-admin-key-preview"


class HttpDialogPreviewTests(AioHTTPTestCase):

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

        # Crea immagine reale sotto un root consentito artificiale.
        cls._td = td
        cls._img_dir = td / ".local" / "share" / "metnos" / "Immagini"
        cls._img_dir.mkdir(parents=True, exist_ok=True)
        cls._img_path = cls._img_dir / "ospite.jpg"
        from PIL import Image
        Image.new("RGB", (200, 200), "blue").save(cls._img_path, "JPEG")

        # Monkeypatch i root consentiti su tmpdir cosi' la safety check passa.
        # `~/.local/share/metnos` espande al tmpdir HOME monkeypatchato.
        import dialog_preview as _dpv
        cls._orig_roots = _dpv._ALLOWED_ROOTS_RAW
        _dpv._ALLOWED_ROOTS_RAW = (str(td / ".local" / "share" / "metnos"),)
        cls._dpv = _dpv

    @classmethod
    def tearDownClass(cls):
        cls._dpv._ALLOWED_ROOTS_RAW = cls._orig_roots
        cls._tmpdir.cleanup()
        if cls._orig_home is not None:
            os.environ["HOME"] = cls._orig_home
        else:
            os.environ.pop("HOME", None)

    async def get_application(self):
        # Ricicliamo make_app se possibile, ma su environment senza
        # minijinja (prompt_loader importa minijinja al boot per
        # validare i prompt) costruiamo un'app minimale che monta
        # solo le route /agent/dialog/* + /agent/health, sufficiente
        # per esercitare il preview endpoint senza dipendere da
        # PLANNER prompts.
        try:
            return self._server_mod.make_app(admin_key=ADMIN_KEY)
        except (ModuleNotFoundError, ImportError):
            pass
        from aiohttp import web
        import http_routes_agent as _ra
        app = web.Application()
        for method, path, handler in _ra.ROUTES:
            app.router.add_route(method, path, handler)
        app[APP_ADMIN_KEY] = ADMIN_KEY
        return app

    def admin_hdr(self, **extra) -> dict:
        h = {"Authorization": f"Bearer {ADMIN_KEY}"}
        h.update(extra)
        return h

    # ── Helpers ────────────────────────────────────────────────────

    def _seed_dialog(self, *, options: list[dict],
                      dialog_id: str | None = None,
                      sender_id: str = "host") -> str:
        """Scrive uno state dialog su disco per simulare un dialog
        creato da get_inputs. Ritorna il dialog_id."""
        import dialog_pending as _dp
        did = dialog_id or uuid.uuid4().hex[:16]
        # Lo state persistito e' proprieta' dell'identita' logica immutabile,
        # non del vecchio alias testuale ``host``.  L'admin middleware risolve
        # la stessa identita' bootstrap prima di servire il preview.
        import users
        hosts = users.list_users(role="host")
        self.assertEqual(len(hosts), 1)
        state = {
            "dialog_id": did,
            "title": "Pick one",
            "description": "",
            "dialog": [{
                "var": "chosen_slug",
                "prompt": "?",
                "schema": {"kind": "choice_with_preview",
                            "options": options},
            }],
            "fmt": "form",
            "values_collected": {},
            "step_index": 0,
            "started_at": "2026-05-08T12:00:00Z",
            "actor": "host",
            "owner_user_id": hosts[0]["id"],
            "channel": "http",
            "timeout_s": 600,
            "completed": False,
            "cancelled": False,
        }
        # Sovrascrivi DIALOG_DIR su HOME tmp.
        _dp.DIALOG_DIR = Path(os.environ["HOME"]) / ".local" / "share" / "metnos" / "get_inputs"
        _dp.save_pending(sender_id, did, state)
        return did

    # ── Tests ──────────────────────────────────────────────────────

    async def test_preview_endpoint_returns_image(self):
        """GET /preview/0 → 200 image/jpeg per option valida."""
        did = self._seed_dialog(options=[
            {"value": "ospite_alfa", "label": "Ospite Alfa",
             "preview_image_path": str(self._img_path)},
            {"value": "ospite_beta", "label": "Ospite Beta",
             "preview_image_path": str(self._img_path)},
        ])
        r = await self.client.get(
            f"/agent/dialog/{did}/preview/0",
            headers=self.admin_hdr(),
        )
        self.assertEqual(r.status, 200)
        self.assertEqual(r.content_type, "image/jpeg")
        body = await r.read()
        self.assertGreater(len(body), 100)

    async def test_preview_endpoint_404_when_dialog_unknown(self):
        r = await self.client.get(
            "/agent/dialog/notexist1234/preview/0",
            headers=self.admin_hdr(),
        )
        self.assertEqual(r.status, 404)

    async def test_preview_endpoint_403_on_traversal(self):
        """preview_image_path fuori dai root consentiti → 403."""
        did = self._seed_dialog(options=[
            {"value": "evil", "label": "Evil",
             "preview_image_path": "/etc/passwd"},
            {"value": "ok", "label": "OK",
             "preview_image_path": str(self._img_path)},
        ])
        r = await self.client.get(
            f"/agent/dialog/{did}/preview/0",
            headers=self.admin_hdr(),
        )
        self.assertEqual(r.status, 403, await r.text())

    async def test_preview_endpoint_with_bbox_crops_correctly(self):
        """preview_image_path con #bbox=... ritorna immagine ritagliata."""
        from PIL import Image
        import io
        did = self._seed_dialog(options=[
            {"value": "a", "label": "A",
             "preview_image_path": f"{self._img_path}#bbox=10,10,40,40"},
            {"value": "b", "label": "B",
             "preview_image_path": str(self._img_path)},
        ])
        r = await self.client.get(
            f"/agent/dialog/{did}/preview/0",
            headers=self.admin_hdr(),
        )
        self.assertEqual(r.status, 200)
        body = await r.read()
        out = Image.open(io.BytesIO(body))
        out.load()
        # Il crop bbox e' 40x40 dentro un'immagine 200x200, ma il
        # ridimensionamento massimo (max_dim=320) lo lascia invariato.
        self.assertLessEqual(max(out.size), 320)
        self.assertEqual(out.size, (40, 40))

    async def test_preview_endpoint_option_out_of_range(self):
        did = self._seed_dialog(options=[
            {"value": "a", "label": "A",
             "preview_image_path": str(self._img_path)},
            {"value": "b", "label": "B",
             "preview_image_path": str(self._img_path)},
        ])
        r = await self.client.get(
            f"/agent/dialog/{did}/preview/99",
            headers=self.admin_hdr(),
        )
        self.assertEqual(r.status, 404)


if __name__ == "__main__":
    unittest.main()
