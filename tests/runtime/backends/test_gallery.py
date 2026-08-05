"""Test della gallery dedicata `/agent/gallery/<turn_id>` (5/5/2026).

Copre:
- `photo_endpoint.resolve_turn_record` (positivo + missing).
- Route `/agent/gallery/<turn_id>` 404 su turn inesistente.
- Route 200 + HTML con thumb_url signed e link "next" su 25 attachments.
- Pagination `?from=20` mostra il secondo blocco e linka "prev".
- `_attachments_from_record` estrae correttamente da reversed steps.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
# Jinja2 e' system-package su questa macchina (vedi
# /etc/systemd/system/metnos-http.service: PYTHONPATH include
# /usr/lib/python3/dist-packages). I test usano lo stesso fallback per
# aiutarsi quando il venv suprastructure non ha jinja2 installato.
_SYSDIST = "/usr/lib/python3/dist-packages"
if Path(_SYSDIST).exists() and _SYSDIST not in sys.path:
    sys.path.append(_SYSDIST)

from aiohttp.test_utils import AioHTTPTestCase


ADMIN_KEY = "test-admin-key-gallery"


def _build_record(turn_id: str, n: int, query: str = "compleanno") -> dict:
    """Sintetizza un record JSONL con n attachments."""
    atts = []
    for i in range(n):
        atts.append({
            "kind": "image",
            "path": f"/nas/foto/{i:04d}.jpg",
            "score": round(0.9 - i * 0.001, 4),
            "basename": f"{i:04d}.jpg",
            "caption": f"+{0.9 - i * 0.001:.3f}  {i:04d}.jpg",
        })
    return {
        "turn_id": turn_id,
        "user_query": query,
        # I turni legacy senza owner_user_id restano leggibili soltanto dal
        # medesimo actor. Il fixture deve quindi modellare un turno dell'host,
        # non un record anonimo che il boundary multi-utente deve rifiutare.
        "actor": "host",
        "steps": [
            {"chosen_tool": "find_images_indices",
             "result": {"ok": True, "n_entries": n, "attachments": atts}},
        ],
    }


class TestResolveTurnRecord(unittest.TestCase):
    """Test diretti di `photo_endpoint.resolve_turn_record`."""

    def setUp(self):
        import photo_endpoint
        self.tmp = Path(tempfile.mkdtemp())
        self._old_dir = photo_endpoint.TURNS_DIR
        photo_endpoint.TURNS_DIR = self.tmp

    def tearDown(self):
        import photo_endpoint
        photo_endpoint.TURNS_DIR = self._old_dir
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_resolve_returns_record_dict(self):
        import photo_endpoint
        jsonl = self.tmp / f"{date.today().isoformat()}.jsonl"
        rec = _build_record("abc-123", 3)
        jsonl.write_text(json.dumps(rec) + "\n", encoding="utf-8")

        got = photo_endpoint.resolve_turn_record("abc-123")
        self.assertIsNotNone(got)
        self.assertEqual(got["turn_id"], "abc-123")
        self.assertEqual(got["user_query"], "compleanno")
        self.assertEqual(len(got["steps"]), 1)

    def test_resolve_missing_returns_none(self):
        import photo_endpoint
        self.assertIsNone(photo_endpoint.resolve_turn_record("nope-xyz"))

    def test_resolve_empty_turn_id(self):
        import photo_endpoint
        self.assertIsNone(photo_endpoint.resolve_turn_record(""))


class TestGalleryRoute(AioHTTPTestCase):
    """Test della route HTTP /agent/gallery/<turn_id>.

    La gallery richiede un principal autenticato. Il fixture usa il Bearer
    amministrativo dell'app isolata, quindi verifica anche che una route
    protetta non diventi leggibile dalla sola provenienza localhost.
    """

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
        import photo_endpoint
        importlib.reload(http_auth)
        importlib.reload(photo_endpoint)
        # Pin TURNS_DIR sulla home isolata
        cls._turns_dir = Path(os.environ["HOME"]) / ".local" / "share" / "metnos" / "turns"
        cls._turns_dir.mkdir(parents=True, exist_ok=True)
        photo_endpoint.TURNS_DIR = cls._turns_dir

        import http_routes_admin
        importlib.reload(http_routes_admin)
        import http_routes_agent
        importlib.reload(http_routes_agent)
        import metnos_http_server
        importlib.reload(metnos_http_server)
        cls._server_mod = metnos_http_server
        cls._photo_endpoint = photo_endpoint

    @classmethod
    def tearDownClass(cls):
        cls._tmpdir.cleanup()
        if cls._orig_home is not None:
            os.environ["HOME"] = cls._orig_home
        else:
            os.environ.pop("HOME", None)

    async def get_application(self):
        return self._server_mod.make_app(admin_key=ADMIN_KEY)

    def _seed_turn(self, turn_id: str, n: int):
        jsonl = self._turns_dir / f"{date.today().isoformat()}.jsonl"
        rec = _build_record(turn_id, n)
        with jsonl.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")

    @staticmethod
    def _auth_headers():
        return {"Authorization": f"Bearer {ADMIN_KEY}"}

    async def test_gallery_404_for_unknown_turn(self):
        r = await self.client.get("/agent/gallery/unknown-turn-zzz",
                                  headers=self._auth_headers())
        self.assertEqual(r.status, 404)

    async def test_gallery_200_with_attachments(self):
        self._seed_turn("turn-25", 25)
        r = await self.client.get("/agent/gallery/turn-25",
                                  headers=self._auth_headers())
        self.assertEqual(r.status, 200)
        body = await r.text()
        # Header con count
        self.assertIn("25 foto matched", body)
        self.assertIn("compleanno", body)
        # Ogni img ha src signed (token nel querystring)
        self.assertIn("<img", body)
        self.assertIn("/agent/photos/turn-25/0", body)
        # Token signing presente (HTML-escaped: &amp;t=...)
        self.assertIn("&amp;t=", body)
        # Lazy load
        self.assertIn('loading="lazy"', body)
        # Footer paginazione: 1-25 di 25 (tutte in una pagina, n<60)
        self.assertIn("1–25", body)
        self.assertIn("di 25", body)
        # Niente "next" attivo (solo 1 pagina)
        self.assertNotIn("?from=60", body)

    async def test_gallery_pagination_next_present_when_more_than_60(self):
        self._seed_turn("turn-90", 90)
        r = await self.client.get("/agent/gallery/turn-90",
                                  headers=self._auth_headers())
        self.assertEqual(r.status, 200)
        body = await r.text()
        self.assertIn("90 foto matched", body)
        # Pagina 1: 1-60
        self.assertIn("1–60", body)
        self.assertIn("di 90", body)
        # Link "successivo" → ?from=60
        self.assertIn("?from=60", body)

    async def test_gallery_pagination_from_param(self):
        self._seed_turn("turn-90b", 90)
        r = await self.client.get("/agent/gallery/turn-90b?from=60",
                                  headers=self._auth_headers())
        self.assertEqual(r.status, 200)
        body = await r.text()
        # Pagina 2: 61-90
        self.assertIn("61–90", body)
        # Link "precedente" → ?from=0
        self.assertIn("?from=0", body)


class TestAttachmentsFromRecord(unittest.TestCase):
    """Test diretti dell helper `_attachments_from_record`."""

    def test_extracts_from_last_step(self):
        from http_routes_agent import _attachments_from_record
        rec = {
            "steps": [
                {"chosen_tool": "find_files",
                 "result": {"ok": True, "entries": []}},
                {"chosen_tool": "find_images_indices",
                 "result": {"ok": True,
                            "attachments": [{"path": "/a.jpg"},
                                            {"path": "/b.jpg"}]}},
            ],
        }
        atts = _attachments_from_record(rec)
        self.assertEqual(len(atts), 2)
        self.assertEqual(atts[0]["path"], "/a.jpg")

    def test_no_attachments_returns_empty_list(self):
        from http_routes_agent import _attachments_from_record
        rec = {"steps": [{"result": {"ok": True}}]}
        self.assertEqual(_attachments_from_record(rec), [])

    def test_empty_record(self):
        from http_routes_agent import _attachments_from_record
        self.assertEqual(_attachments_from_record({}), [])
        self.assertEqual(_attachments_from_record(None), [])


if __name__ == "__main__":
    unittest.main()
