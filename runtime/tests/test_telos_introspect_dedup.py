"""telos_introspect._persist — dedup generativo (target, lens) (12/6/2026).

Qualita'>quantita' alla SORGENTE: la ripetizione intra-lente sullo stesso
target non aggiunge evidenza (la convergenza si misura su lenti distinte).
Sui dati reali la regola avrebbe ridotto 1017 → 75 righe persistite (−93%).

Run: python3 -m pytest runtime/tests/test_telos_introspect_dedup.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


def _rec(target="compress_files_zip", lens="scamper", ts=1.0):
    return {
        "ts": ts,
        "telos_id": "t.tempo",
        "lens": lens,
        "executor_target": target,
        "proposed_action": "azione test",
        "rationale": "rationale test",
        "expected_alignment": 0.4,
    }


class PersistDedupTests(unittest.TestCase):

    def setUp(self):
        import telos_introspect as TI
        import telos_proposals_store as S
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self._orig_telemetry = TI.TELEMETRY_PATH
        TI.TELEMETRY_PATH = self.tmpdir / "telos_proposals.jsonl"
        # Decisioni isolate: nessun rejected target dal file reale.
        self._orig_decisions = S.DECISIONS_PATH
        S.DECISIONS_PATH = self.tmpdir / "telos_decisions.jsonl"
        self.TI = TI
        self.S = S

    def tearDown(self):
        self.TI.TELEMETRY_PATH = self._orig_telemetry
        self.S.DECISIONS_PATH = self._orig_decisions
        self.tmp.cleanup()

    def _stored(self):
        if not self.TI.TELEMETRY_PATH.is_file():
            return []
        return [json.loads(l) for l in
                self.TI.TELEMETRY_PATH.read_text().splitlines() if l.strip()]

    def test_first_persist_succeeds(self):
        self.assertTrue(self.TI._persist(_rec()))
        self.assertEqual(len(self._stored()), 1)

    def test_same_target_same_lens_skipped(self):
        self.assertTrue(self.TI._persist(_rec(ts=1.0)))
        self.assertFalse(self.TI._persist(_rec(ts=2.0)))
        self.assertEqual(len(self._stored()), 1)

    def test_same_target_new_lens_persists(self):
        """Lente DISTINTA sullo stesso target = evidenza vera → persiste."""
        self.assertTrue(self.TI._persist(_rec(lens="scamper")))
        self.assertTrue(self.TI._persist(_rec(lens="oulipo", ts=2.0)))
        self.assertEqual(len(self._stored()), 2)

    def test_different_target_same_lens_persists(self):
        self.assertTrue(self.TI._persist(_rec(target="find_files_empty")))
        self.assertTrue(self.TI._persist(
            _rec(target="write_files_doc", ts=2.0)))
        self.assertEqual(len(self._stored()), 2)

    def test_rejected_target_skipped(self):
        """Anti-resurrezione C.5 invariata: target rejected → skip."""
        self.S.apply_decision(
            "9.000000", "reject", executor_target="compress_files_zip")
        self.assertFalse(self.TI._persist(_rec()))
        self.assertEqual(len(self._stored()), 0)


if __name__ == "__main__":
    unittest.main()
