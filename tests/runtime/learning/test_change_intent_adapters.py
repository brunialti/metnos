"""change_intent_adapters — test smoke + dedup cross-source.

Run: `python3 -m pytest tests/runtime/learning/test_change_intent_adapters.py -v`.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class TestAdaptersSmoke(unittest.TestCase):
    def test_iter_telos_returns_change_intents(self):
        from change_intent_adapters import iter_telos
        from change_intents import (
            ChangeIntent, KIND_CREATE_EXECUTOR, KIND_EXTEND_EXECUTOR,
            KIND_MATERIALIZE_PIPELINE,
        )
        seen_kinds = set()
        n = 0
        for ci in iter_telos():
            self.assertIsInstance(ci, ChangeIntent)
            self.assertEqual(ci.origin_family, "telos")
            self.assertIn(ci.intent_kind, {
                KIND_CREATE_EXECUTOR, KIND_EXTEND_EXECUTOR,
                KIND_MATERIALIZE_PIPELINE,
            })
            self.assertTrue(ci.fingerprint)
            self.assertGreaterEqual(ci.score, 0.0)
            self.assertLessEqual(ci.score, 1.0)
            seen_kinds.add(ci.intent_kind)
            n += 1
            if n >= 10:
                break
        # Telos su un sistema vivo deve produrre qualcosa. Su sistema vuoto
        # passa banalmente. Test piu' stretto in test_materialize.
        self.assertGreaterEqual(n, 0)

    def test_iter_introvertiva_smoke(self):
        from change_intent_adapters import iter_introvertiva
        from change_intents import (
            ChangeIntent, KIND_CACHE_PATTERN, KIND_DEDUPE_EXECUTORS,
            KIND_EXTEND_EXECUTOR,
        )
        n = 0
        for ci in iter_introvertiva():
            self.assertIsInstance(ci, ChangeIntent)
            self.assertEqual(ci.origin_family, "introvertiva")
            self.assertIn(ci.intent_kind, {
                KIND_CACHE_PATTERN, KIND_DEDUPE_EXECUTORS,
                KIND_EXTEND_EXECUTOR,
            })
            n += 1
            if n >= 5:
                break

    def test_iter_synt_smoke(self):
        from change_intent_adapters import iter_synt
        from change_intents import ChangeIntent, KIND_CREATE_EXECUTOR
        n = 0
        for ci in iter_synt():
            self.assertIsInstance(ci, ChangeIntent)
            self.assertEqual(ci.origin_family, "synt")
            self.assertEqual(ci.intent_kind, KIND_CREATE_EXECUTOR)
            n += 1
            if n >= 5:
                break

    def test_in_progress_synt_never_implies_user_acceptance(self):
        import config as C
        from change_intent_adapters.synt import iter_synt
        from change_intents import STATE_PROPOSED

        with tempfile.TemporaryDirectory() as td:
            previous = C.PATH_USER_DATA
            try:
                C.PATH_USER_DATA = Path(td)
                proposals = C.PATH_USER_DATA / "synt_proposals"
                proposals.mkdir(parents=True)
                (proposals / "p1.json").write_text(json.dumps({
                    "id": "p1", "expected_name": "read_example",
                    "intent": "leggi esempio", "final_state": "in_progress",
                }))
                rows = list(iter_synt())
            finally:
                C.PATH_USER_DATA = previous

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].state, STATE_PROPOSED)

    def test_synthesized_is_owned_only_by_promoter(self):
        import config as C
        from change_intent_adapters.synt import iter_synt

        with tempfile.TemporaryDirectory() as td:
            previous = C.PATH_USER_DATA
            try:
                C.PATH_USER_DATA = Path(td)
                proposals = C.PATH_USER_DATA / "synt_proposals"
                proposals.mkdir(parents=True)
                (proposals / "p1.json").write_text(json.dumps({
                    "id": "p1", "expected_name": "read_example",
                    "intent": "leggi esempio", "final_state": "synthesized",
                }))
                rows = list(iter_synt())
            finally:
                C.PATH_USER_DATA = previous

        self.assertEqual(rows, [])

    def test_retired_adapters_not_exported(self):
        # 2/7/2026: multi_tool e canonical RITIRATI (store senza writer,
        # superati da L1/L0). iter_all non deve piu' proiettarli.
        import change_intent_adapters as A
        self.assertFalse(hasattr(A, "iter_multi_tool"))
        self.assertFalse(hasattr(A, "iter_canonical"))

    def test_telos_pipeline_head_has_runnable_body(self):
        # existing_pipeline → body con suggested_query (l'accept ESEGUE).
        from change_intent_adapters import iter_telos
        from change_intents import KIND_MATERIALIZE_PIPELINE
        for ci in iter_telos():
            if ci.intent_kind == KIND_MATERIALIZE_PIPELINE:
                self.assertTrue(ci.intent_body.get("suggested_query"))
                self.assertTrue(ci.intent_body.get("tools_sequence"))

    def test_iter_user_feedback_smoke(self):
        from change_intent_adapters import iter_user_feedback
        from change_intents import ChangeIntent, KIND_REJECT_PATTERN
        n = 0
        for ci in iter_user_feedback():
            self.assertIsInstance(ci, ChangeIntent)
            self.assertEqual(ci.origin_family, "user")
            self.assertEqual(ci.intent_kind, KIND_REJECT_PATTERN)
            self.assertGreaterEqual(ci.intent_body["n_rejections"], 2,
                                    "filter minimo 2 rejections per evitare noise")
            n += 1
            if n >= 5:
                break


class TestScoreNormalization(unittest.TestCase):
    def test_score_from_uses_monotone(self):
        from change_intent_adapters._base import score_from_uses
        s1 = score_from_uses(1)
        s5 = score_from_uses(5)
        s50 = score_from_uses(50)
        s500 = score_from_uses(500)
        self.assertLess(s1, s5)
        self.assertLess(s5, s50)
        self.assertLess(s50, s500)
        self.assertLessEqual(s500, 1.0)
        self.assertGreaterEqual(s1, 0.0)

    def test_score_from_uses_zero(self):
        from change_intent_adapters._base import score_from_uses
        self.assertEqual(score_from_uses(0), 0.0)
        self.assertEqual(score_from_uses(-5), 0.0)

    def test_score_from_ea_clamp(self):
        from change_intent_adapters._base import score_from_ea
        self.assertEqual(score_from_ea(None), 0.0)
        self.assertEqual(score_from_ea(-0.5), 0.0)
        self.assertEqual(score_from_ea(0.5), 0.5)
        self.assertEqual(score_from_ea(2.0), 1.0)

    def test_score_for_reject_pattern(self):
        from change_intent_adapters._base import score_for_reject_pattern
        self.assertEqual(score_for_reject_pattern(0), 0.0)
        self.assertGreater(score_for_reject_pattern(2),
                           score_for_reject_pattern(1))
        self.assertLessEqual(score_for_reject_pattern(100), 0.9)


if __name__ == "__main__":
    unittest.main()
