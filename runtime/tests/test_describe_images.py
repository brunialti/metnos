"""Test builtin describe_images (VLM content-describe). VLM mockato: nessuna
call reale. Verifica raccolta path + shape output (entries + query_text) §2.6."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

import describe_images as di  # noqa: E402


def _fake_describe(path, **kw):
    return {"description": f"contenuto di {Path(path).name}",
            "keywords": ["k1", "k2"], "location_hint": "", "activity_hint": ""}


class DescribeImagesTests(unittest.TestCase):
    def setUp(self):
        self._p = mock.patch("vlm_client.describe_image", _fake_describe)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def test_reference_images_to_entries_and_query_text(self):
        out = di.handle_describe_images({"reference_images": ["/u/a.jpg", "/u/b.jpg"]})
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["entries"]), 2)
        self.assertEqual(out["entries"][0]["path"], "/u/a.jpg")
        self.assertIn("contenuto di a.jpg", out["entries"][0]["description"])
        # query_text = descrizioni unite (per ricerca contenuto)
        self.assertIn("contenuto di a.jpg", out["query_text"])
        self.assertIn("contenuto di b.jpg", out["query_text"])
        self.assertEqual(out["ok_count"], 2)

    def test_entries_form_seed_wired(self):
        out = di.handle_describe_images(
            {"entries": [{"path": "/u/c.jpg", "reference_image": "/u/c.jpg"}]})
        self.assertTrue(out["ok"])
        self.assertEqual(out["entries"][0]["path"], "/u/c.jpg")

    def test_dedup_paths(self):
        out = di.handle_describe_images(
            {"reference_images": ["/u/a.jpg", "/u/a.jpg"], "paths": ["/u/a.jpg"]})
        self.assertEqual(len(out["entries"]), 1)

    def test_empty_args_honest_error(self):
        out = di.handle_describe_images({})
        self.assertFalse(out["ok"])
        self.assertEqual(out["entries"], [])
        self.assertEqual(out["query_text"], "")

    def test_vlm_error_propagated_not_crash(self):
        with mock.patch("vlm_client.describe_image",
                        lambda p, **k: {"description": "", "keywords": [],
                                        "_vlm_error": "http_failed"}):
            out = di.handle_describe_images({"reference_images": ["/u/x.jpg"]})
        self.assertFalse(out["ok"])  # nessuna descrizione → ok False
        self.assertEqual(out["entries"][0]["_vlm_error"], "http_failed")


if __name__ == "__main__":
    unittest.main()
