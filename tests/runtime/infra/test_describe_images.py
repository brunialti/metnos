"""Test builtin describe_images (VLM content-describe). VLM mockato: nessuna
call reale. Verifica raccolta path + shape output (entries + query_text) §2.6."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

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

    def test_upstream_cardinality_is_bounded_and_reported(self):
        calls = []

        def describe(path, **kwargs):
            calls.append((path, kwargs))
            return _fake_describe(path, **kwargs)

        paths = [f"/u/{index}.jpg" for index in range(1000)]
        with (mock.patch("vlm_client.describe_image", describe),
              mock.patch.object(di, "_work_policy", return_value=(8, 45.0))):
            out = di.handle_describe_images({"reference_images": paths})

        self.assertEqual(len(calls), 8)
        self.assertTrue(all("deadline_at" in kwargs for _, kwargs in calls))
        self.assertEqual(out["used"], 8)
        self.assertEqual(out["available_total"], 1000)
        self.assertEqual(out["cap_field"], "max_images_per_request")
        self.assertEqual(out["cap_value"], 8)
        self.assertFalse(out["cap_expandable"])
        self.assertTrue(out["truncated"])
        self.assertFalse(out["budget_exhausted"])

    def test_shared_wall_budget_stops_new_vlm_calls(self):
        clock = [0.0]
        calls = []

        def describe(path, **kwargs):
            calls.append((path, kwargs))
            clock[0] += 2.0
            return _fake_describe(path, **kwargs)

        with (mock.patch("vlm_client.describe_image", describe),
              mock.patch.object(di, "_work_policy", return_value=(8, 3.0)),
              mock.patch.object(di.time, "monotonic",
                                side_effect=lambda: clock[0])):
            out = di.handle_describe_images({
                "reference_images": [f"/u/{index}.jpg" for index in range(8)],
            })

        self.assertEqual(len(calls), 2)
        self.assertEqual(out["used"], 2)
        self.assertEqual(out["available_total"], 8)
        self.assertEqual(out["cap_field"], "request_budget_s")
        self.assertEqual(out["cap_value"], 3)
        self.assertTrue(out["budget_exhausted"])
        self.assertTrue(out["partial"])


if __name__ == "__main__":
    unittest.main()
