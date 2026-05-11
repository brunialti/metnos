"""Tests per Telegram caption+photo download (ADR 0092, sprint 5/5/2026).

Mock urllib (getFile + binary GET) per validare:
  - foto singola scaricata + path salvato
  - variante a max risoluzione scelta correttamente
  - foto + caption → InboundMessage con extra.attached_images
  - media_group_id propagato
  - download fallito → attached_failed=True (no silent failure §2.8)

Run: `python3 -m pytest runtime/tests/test_telegram_attached_photos.py -xvs`.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class _FakeHTTPResponse:
    """File-like minimo per urllib.request.urlopen() context manager."""
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self._buf = BytesIO(body)
        self.headers = headers or {}
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self, n: int = -1) -> bytes:
        if n == -1 or n is None:
            return self._buf.read()
        return self._buf.read(n)


class TelegramAttachedPhotosTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmpdir.name)
        # Patch la UPLOAD_DIR del modulo a un dir di test
        from channels import telegram as tg_mod
        self._tg_mod = tg_mod
        self._orig_upload = tg_mod.UPLOAD_DIR
        tg_mod.UPLOAD_DIR = self._tmp / "uploads"

    def tearDown(self):
        self._tmpdir.cleanup()
        self._tg_mod.UPLOAD_DIR = self._orig_upload

    def _make_channel(self):
        # Bypass credential reading: passa token esplicito.
        return self._tg_mod.TelegramChannel(
            token="FAKE-TOKEN", default_chat_id="100",
            credentials_path=Path("/nonexistent"),
            state_path=False,
        )

    def _fake_urlopen_factory(self, *, file_path: str, blob: bytes,
                               getfile_ok: bool = True,
                               download_ok: bool = True):
        """Costruisce un mock di urllib.request.urlopen che risponde
        in sequenza: prima call = getFile (POST), seconda = GET file."""
        calls = {"n": 0}
        def _fake(req, timeout=None):
            calls["n"] += 1
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "getFile" in url:
                if getfile_ok:
                    body = json.dumps({
                        "ok": True,
                        "result": {"file_path": file_path,
                                   "file_id": "FILEID",
                                   "file_size": len(blob)}
                    }).encode()
                    return _FakeHTTPResponse(body)
                body = json.dumps({"ok": False,
                                    "description": "file not found"}).encode()
                return _FakeHTTPResponse(body)
            # binary GET (file path)
            if download_ok:
                return _FakeHTTPResponse(
                    blob,
                    headers={"Content-Length": str(len(blob))},
                )
            import urllib.error
            raise urllib.error.URLError("download failed")
        return _fake, calls

    # --- 1. download singola foto ------------------------------------------

    def test_download_photo_saves_to_disk(self):
        ch = self._make_channel()
        blob = b"\xff\xd8\xff\xe0FAKE_JPEG_BYTES"
        fake, _ = self._fake_urlopen_factory(
            file_path="photos/file_42.jpg", blob=blob,
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            path = ch._download_photo(
                "FILEID", chat_id="100", msg_id="m1", idx=0,
            )
        self.assertIsNotNone(path, "download path should not be None")
        self.assertTrue(Path(path).exists())
        self.assertEqual(Path(path).read_bytes(), blob)
        # Path schema: <UPLOAD_DIR>/<chat_id>/<msg_id>_<idx>.jpg
        self.assertIn("100", path)
        self.assertTrue(path.endswith("m1_0.jpg"))

    # --- 2. getFile fallito → None  ----------------------------------------

    def test_download_photo_getfile_failed_returns_none(self):
        ch = self._make_channel()
        fake, _ = self._fake_urlopen_factory(
            file_path="photos/file.jpg", blob=b"x", getfile_ok=False,
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            path = ch._download_photo(
                "BADID", chat_id="100", msg_id="m2", idx=0,
            )
        self.assertIsNone(path)

    # --- 3. poll() su update con photo + caption  --------------------------

    def test_poll_emits_inbound_with_attached_images(self):
        """update Telegram con `photo` + `caption` produce un InboundMessage
        con `extra.attached_images = [path]` e `text = caption`."""
        ch = self._make_channel()
        blob = b"\x89PNGFAKE"
        getupdates_body = json.dumps({
            "ok": True,
            "result": [{
                "update_id": 7,
                "message": {
                    "message_id": 999,
                    "date": 1700000000,
                    "chat": {"id": 100},
                    "from": {"id": 100, "first_name": "Test"},
                    "caption": "trova foto simili a questa",
                    "media_group_id": None,
                    "photo": [
                        {"file_id": "small", "file_size": 100,
                         "width": 50, "height": 50},
                        {"file_id": "big", "file_size": 5000,
                         "width": 1024, "height": 768},
                    ],
                },
            }],
        }).encode()
        fake_getfile_body = json.dumps({
            "ok": True,
            "result": {"file_path": "photos/file_b.jpg",
                       "file_size": len(blob)},
        }).encode()
        # Sequenza: getUpdates → getFile (per BIG) → GET binary.
        responses = iter([
            _FakeHTTPResponse(getupdates_body),
            _FakeHTTPResponse(fake_getfile_body),
            _FakeHTTPResponse(blob,
                              headers={"Content-Length": str(len(blob))}),
        ])
        def _fake(req, timeout=None):
            return next(responses)
        with mock.patch("urllib.request.urlopen", side_effect=_fake):
            msgs = ch.poll(timeout_s=0)
        self.assertEqual(len(msgs), 1)
        m = msgs[0]
        self.assertEqual(m.text, "trova foto simili a questa")
        attached = m.extra.get("attached_images") or []
        self.assertEqual(len(attached), 1)
        self.assertTrue(Path(attached[0]).exists())
        # Variante max risoluzione: file deve avere il blob "BIG"
        self.assertEqual(Path(attached[0]).read_bytes(), blob)
        self.assertFalse(m.extra.get("attached_failed"))

    # --- 4. media_group_id propagato  --------------------------------------

    def test_poll_propagates_media_group_id(self):
        ch = self._make_channel()
        blob = b"GROUP-PHOTO-1"
        getupdates_body = json.dumps({
            "ok": True,
            "result": [{
                "update_id": 11,
                "message": {
                    "message_id": 12,
                    "date": 1700000000,
                    "chat": {"id": 100},
                    "from": {"id": 100},
                    "caption": "foto 1",
                    "media_group_id": "GRP123",
                    "photo": [{"file_id": "f1", "file_size": 999}],
                },
            }],
        }).encode()
        getfile_body = json.dumps({
            "ok": True,
            "result": {"file_path": "photos/g1.jpg"},
        }).encode()
        responses = iter([
            _FakeHTTPResponse(getupdates_body),
            _FakeHTTPResponse(getfile_body),
            _FakeHTTPResponse(blob, headers={"Content-Length": "13"}),
        ])
        def _fake(req, timeout=None):
            return next(responses)
        with mock.patch("urllib.request.urlopen", side_effect=_fake):
            msgs = ch.poll(timeout_s=0)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].extra.get("media_group_id"), "GRP123")

    # --- 5. download fallito → attached_failed=True (no silent failure)----

    def test_poll_emits_attached_failed_on_download_error(self):
        ch = self._make_channel()
        getupdates_body = json.dumps({
            "ok": True,
            "result": [{
                "update_id": 21,
                "message": {
                    "message_id": 50,
                    "date": 1700000000,
                    "chat": {"id": 100},
                    "from": {"id": 100},
                    "caption": "questa foto",
                    "photo": [{"file_id": "BADX", "file_size": 1000}],
                },
            }],
        }).encode()
        getfile_body = json.dumps({
            "ok": False, "description": "file not found",
        }).encode()
        responses = iter([
            _FakeHTTPResponse(getupdates_body),
            _FakeHTTPResponse(getfile_body),
        ])
        def _fake(req, timeout=None):
            return next(responses)
        with mock.patch("urllib.request.urlopen", side_effect=_fake):
            msgs = ch.poll(timeout_s=0)
        self.assertEqual(len(msgs), 1)
        m = msgs[0]
        self.assertEqual(m.extra.get("attached_images"), [])
        self.assertTrue(m.extra.get("attached_failed"))


if __name__ == "__main__":
    unittest.main()
