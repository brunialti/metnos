"""Test del modulo runtime.photo_endpoint (Opzione 1, 5/5/2026).

Copre signing/verify HMAC, resolve_path da JSONL turn log, e
get_or_make_thumb su PNG sintetico (Pillow obbligatorio).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


class TestPhotoEndpointSigning(unittest.TestCase):
    def setUp(self):
        self.admin_key = "a" * 64

    def test_make_url_verify_roundtrip(self):
        import photo_endpoint
        url = photo_endpoint.make_url("turn-abc", 0, "thumb", self.admin_key)
        # Estrai exp e t
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        exp = int(qs["exp"][0])
        t = qs["t"][0]
        self.assertTrue(photo_endpoint.verify(
            "turn-abc", 0, "thumb", exp, t, self.admin_key))

    def test_verify_expired_token(self):
        import photo_endpoint
        # Token scaduto manualmente (exp nel passato)
        exp = int(time.time()) - 10
        sig = photo_endpoint._sign("turn-x", 0, "thumb", exp, self.admin_key)
        self.assertFalse(photo_endpoint.verify(
            "turn-x", 0, "thumb", exp, sig, self.admin_key))

    def test_verify_tampered_token(self):
        import photo_endpoint
        url = photo_endpoint.make_url("turn-y", 1, "thumb", self.admin_key)
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        exp = int(qs["exp"][0])
        # Cambia idx -> firma non matcha piu
        self.assertFalse(photo_endpoint.verify(
            "turn-y", 2, "thumb", exp, qs["t"][0], self.admin_key))

    def test_verify_invalid_size(self):
        import photo_endpoint
        url = photo_endpoint.make_url("turn-z", 0, "thumb", self.admin_key)
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        exp = int(qs["exp"][0])
        self.assertFalse(photo_endpoint.verify(
            "turn-z", 0, "huge", exp, qs["t"][0], self.admin_key))

    def test_make_url_invalid_size_raises(self):
        import photo_endpoint
        with self.assertRaises(ValueError):
            photo_endpoint.make_url("turn-q", 0, "huge", self.admin_key)


class TestPhotoEndpointResolve(unittest.TestCase):
    def setUp(self):
        import photo_endpoint
        self.tmp = Path(tempfile.mkdtemp())
        self._old_dir = photo_endpoint.TURNS_DIR
        photo_endpoint.TURNS_DIR = self.tmp

    def tearDown(self):
        import photo_endpoint
        photo_endpoint.TURNS_DIR = self._old_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_resolve_path_finds_attachments(self):
        import photo_endpoint
        from datetime import date
        jsonl = self.tmp / f"{date.today().isoformat()}.jsonl"
        rec = {
            "turn_id": "abc-123",
            "steps": [
                {"chosen_tool": "find_images_indices",
                 "result": {
                     "ok": True,
                     "attachments": [
                         {"kind": "image", "path": "/nas/foto/a.jpg",
                          "score": 0.9, "basename": "a.jpg",
                          "caption": "+0.900  a.jpg"},
                         {"kind": "image", "path": "/nas/foto/b.jpg",
                          "score": 0.8, "basename": "b.jpg",
                          "caption": "+0.800  b.jpg"},
                     ],
                 }},
            ],
        }
        jsonl.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        self.assertEqual(photo_endpoint.resolve_path("abc-123", 0),
                         "/nas/foto/a.jpg")
        self.assertEqual(photo_endpoint.resolve_path("abc-123", 1),
                         "/nas/foto/b.jpg")

    def test_resolve_path_missing_turn(self):
        import photo_endpoint
        self.assertIsNone(photo_endpoint.resolve_path("nope", 0))


class TestPhotoEndpointThumb(unittest.TestCase):
    def setUp(self):
        import photo_endpoint
        self.tmp = Path(tempfile.mkdtemp())
        self._old_cache = photo_endpoint.THUMB_CACHE_DIR
        photo_endpoint.THUMB_CACHE_DIR = self.tmp / "thumbs"

    def tearDown(self):
        import photo_endpoint
        photo_endpoint.THUMB_CACHE_DIR = self._old_cache
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_or_make_thumb_creates_cache(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        import photo_endpoint
        # Sorgente PNG 800x600 sintetica
        src = self.tmp / "src.png"
        Image.new("RGB", (800, 600), color=(120, 200, 80)).save(src, "PNG")
        thumb = photo_endpoint.get_or_make_thumb(str(src), "thumb")
        self.assertIsNotNone(thumb)
        self.assertTrue(thumb.exists())
        self.assertTrue(thumb.stat().st_size > 0)
        # Idempotenza: seconda chiamata stessa cache hit
        thumb2 = photo_endpoint.get_or_make_thumb(str(src), "thumb")
        self.assertEqual(thumb, thumb2)
        # Sanity dimensioni: thumb <= 256x256
        with Image.open(thumb) as im:
            w, h = im.size
            self.assertLessEqual(w, 256)
            self.assertLessEqual(h, 256)

    def test_get_or_make_thumb_missing_source(self):
        import photo_endpoint
        self.assertIsNone(photo_endpoint.get_or_make_thumb(
            "/nope/does-not-exist.jpg", "thumb"))


if __name__ == "__main__":
    unittest.main()
