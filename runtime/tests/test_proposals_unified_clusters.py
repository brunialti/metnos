"""proposals_unified — hub cluster-first (mandato 12/6/2026).

Verifica: (1) group_clusters=True serve 1 head per cluster relaxed con
ranking_score = cluster_score; (2) apply_decision_unified normalizza il
source granulare "telos:<lens>" (prima: 400 unknown source su OGNI bottone
del hub — una causa dell'accettazione ~0).

Run: python3 -m pytest runtime/tests/test_proposals_unified_clusters.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


def _prop(ts, lens, target, ea, action):
    return {
        "ts": ts, "telos_id": "t.tempo", "telos_phrase": "tempo",
        "lens": lens, "operator": "S", "executor_target": target,
        "proposed_action": action, "rationale": "test",
        "paternalism_flag": False, "expected_alignment": ea,
        "alignment_per_telos": [],
    }


class UnifiedClusterTests(unittest.TestCase):

    def setUp(self):
        import telos_proposals_store as S
        import proposals_unified as U
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.props_path = self.tmpdir / "telos_proposals.rescored.recomposed.jsonl"
        self._orig = (S._DATA_DIR, S._PROPOSALS_CANDIDATES, S.DECISIONS_PATH,
                      S._TURN_LOG_DIR, dict(S._CATALOG_CACHE))
        S._DATA_DIR = self.tmpdir
        S._PROPOSALS_CANDIDATES = (self.props_path,)
        S.DECISIONS_PATH = self.tmpdir / "telos_decisions.jsonl"
        S._TURN_LOG_DIR = self.tmpdir / "turns"
        S._CATALOG_CACHE["names"] = frozenset({"create_events", "get_files"})
        S._CATALOG_CACHE["mtime"] = time.time() + 3600
        self.S, self.U = S, U
        rows = [
            _prop(1.0, "scamper", "create_events", 0.40,
                  "combina get_files e create_events"),
            _prop(2.0, "oulipo", "create_events", 0.50,
                  "pipeline get_files verso create_events"),
            _prop(3.0, "scamper", "get_files", 0.30,
                  "riproponi questo strumento"),
        ]
        self.props_path.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def tearDown(self):
        S = self.S
        (S._DATA_DIR, S._PROPOSALS_CANDIDATES, S.DECISIONS_PATH,
         S._TURN_LOG_DIR, cache) = self._orig
        S._CATALOG_CACHE.update(cache)
        self.tmp.cleanup()

    def test_group_clusters_serves_heads(self):
        rows = self.U.load_unified(
            source_filter="telos", group_clusters=True, enrich=False)
        self.assertEqual(len(rows), 2)  # 3 istanze → 2 cluster
        head = rows[0]
        self.assertTrue(head["is_cluster_head"])
        self.assertEqual(head["executor_target"], "create_events")
        self.assertEqual(head["cluster_size"], 2)
        # ranking_score = cluster_score (0.50 + 0.05 per 2a lente).
        self.assertAlmostEqual(head["ranking_score"], 0.55)
        # Azionabile (existing_pipeline) prima del redundant.
        self.assertTrue(head["actionable"])
        self.assertFalse(rows[1]["actionable"])

    def test_no_grouping_serves_instances(self):
        rows = self.U.load_unified(
            source_filter="telos", group_clusters=False, enrich=False)
        self.assertEqual(len(rows), 3)

    def test_granular_source_normalized_in_decision(self):
        """source 'telos:<lens>' dai bottoni della dashboard → famiglia."""
        rec = self.U.apply_decision_unified(
            "3.000000", "telos:scamper", "reject", by="test")
        self.assertEqual(rec["source"], "telos")
        self.assertEqual(rec["action"], "reject")
        # La decisione e' persistita nello store nativo.
        idx = self.S.decisions_index()
        self.assertIn("3.000000", idx)

    def test_unknown_family_still_raises(self):
        with self.assertRaises(ValueError):
            self.U.apply_decision_unified("x", "boh:foo", "reject")


if __name__ == "__main__":
    unittest.main()
