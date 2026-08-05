"""test_compare_entries — distanza semantica universale (17/6/2026)."""
from __future__ import annotations

import os
import sys
import unittest


import numpy as np
import affinity_semantic
import compare_entries as ce


def _n(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


_V = {"ref": [1, 0, 0, 0], "near": [0.95, 0.31, 0, 0],
      "mid": [0.6, 0.8, 0, 0], "far": [0, 0, 1, 0]}


class _FakeEmb:
    def embed_query(self, t):
        return _n(_V.get((t or "").strip(), [0, 0, 0, 1]))

    def embed_texts(self, texts):
        return np.array([_n(_V.get((t or "").strip(), [0, 0, 0, 1]))
                         for t in texts])


class TestCompareEntries(unittest.TestCase):
    def setUp(self):
        self._orig = affinity_semantic._get_embedder
        affinity_semantic._get_embedder = lambda: _FakeEmb()

    def tearDown(self):
        affinity_semantic._get_embedder = self._orig

    def test_missing_reference(self):
        r = ce.handle_compare_entries({"entries": [{"text": "x"}]})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_class"], "invalid_args")

    def test_entries_not_list(self):
        r = ce.handle_compare_entries({"reference": "ref", "entries": "x"})
        self.assertFalse(r["ok"])

    def test_empty_entries_ok(self):
        r = ce.handle_compare_entries({"reference": "ref", "entries": []})
        self.assertTrue(r["ok"])
        self.assertEqual(r["entries"], [])

    def test_ranking_desc_default(self):
        r = ce.handle_compare_entries({"reference": "ref", "entries": [
            {"text": "far"}, {"text": "near"}, {"text": "mid"}]})
        self.assertTrue(r["ok"])
        self.assertTrue(r["embedder_available"])
        order = [e["text"] for e in r["entries"]]
        self.assertEqual(order, ["near", "mid", "far"])  # similarity desc
        # similarity monotona decrescente + campo presente
        sims = [e["similarity"] for e in r["entries"]]
        self.assertEqual(sims, sorted(sims, reverse=True))

    def test_order_asc(self):
        r = ce.handle_compare_entries({"reference": "ref", "order": "asc",
                                       "entries": [{"text": "near"},
                                                   {"text": "far"}]})
        self.assertEqual([e["text"] for e in r["entries"]], ["far", "near"])

    def test_top_n_intentional_truncation(self):
        r = ce.handle_compare_entries({"reference": "ref", "top_n": 1,
                                       "entries": [{"text": "far"},
                                                   {"text": "near"},
                                                   {"text": "mid"}]})
        self.assertEqual(len(r["entries"]), 1)
        self.assertEqual(r["entries"][0]["text"], "near")
        self.assertTrue(r["truncated"] and r["truncated_intentional"])
        self.assertEqual(r["available_total"], 3)

    def test_min_similarity_threshold(self):
        r = ce.handle_compare_entries({"reference": "ref", "min_similarity": 0.9,
                                       "entries": [{"text": "near"},
                                                   {"text": "far"}]})
        self.assertEqual([e["text"] for e in r["entries"]], ["near"])

    def test_preserves_original_fields(self):
        r = ce.handle_compare_entries({"reference": "ref", "entries": [
            {"text": "near", "id": 7, "extra": "keep"}]})
        e = r["entries"][0]
        self.assertEqual(e["id"], 7)
        self.assertEqual(e["extra"], "keep")
        self.assertIn("similarity", e)

    def test_degrade_no_embedder(self):
        affinity_semantic._get_embedder = lambda: None
        r = ce.handle_compare_entries({"reference": "ref",
                                       "entries": [{"text": "x"}]})
        self.assertTrue(r["ok"])
        self.assertFalse(r["embedder_available"])
        self.assertEqual(r["entries"], [])


if __name__ == "__main__":
    unittest.main()
