"""Test per `telos_synth_consumer` (C.8 fase 2, 24/5/2026).

Coperture:
  - run_once dry_run: legge marker, non chiama synth, conta correttamente.
  - rate limit daily_cap: rispetta budget.
  - priority queue: ordina per expected_alignment desc.
  - invalid marker (missing expected_name/intent): noop + moved a invalid.
  - empty pending: stats vuoto, no errors.
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

import telos_synth_consumer as tsc  # noqa: E402


class TestConsumer(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="telos_consume_"))
        self.pending = self.tmp / "synt_pending"
        self.processed = self.tmp / "synt_processed"
        self.pending.mkdir(parents=True)
        # Monkey-patch path costanti
        self._save = (tsc.SYNT_PENDING_DIR, tsc.SYNT_PROCESSED_DIR,
                      tsc.SYNT_AUDIT_LOG)
        tsc.SYNT_PENDING_DIR = self.pending
        tsc.SYNT_PROCESSED_DIR = self.processed
        tsc.SYNT_AUDIT_LOG = self.tmp / "audit.jsonl"

    def tearDown(self):
        tsc.SYNT_PENDING_DIR, tsc.SYNT_PROCESSED_DIR, tsc.SYNT_AUDIT_LOG = self._save
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_marker(self, sig: str, name: str, intent: str,
                      alignment: float = 0.7):
        (self.pending / f"{sig}.json").write_text(json.dumps({
            "kind": "synt_request",
            "sig": sig,
            "prop_id": f"prop_{sig}",
            "source": "telos",
            "executor_target": name,
            "proposed_action": intent,
            "expected_name": name,
            "intent": intent,
            "expected_alignment": alignment,
            "convergence_count": 2,
            "by": "admin",
            "ts": time.time(),
        }), encoding="utf-8")

    def test_empty_pending_returns_zero_stats(self):
        r = tsc.run_once(dry_run=True)
        self.assertEqual(r["scanned"], 0)
        self.assertEqual(r["processed"], 0)
        self.assertEqual(r["success"], 0)

    def test_dry_run_does_not_call_synth(self):
        self._write_marker("aaa", "find_users_active", "trova utenti attivi")
        self._write_marker("bbb", "find_users_idle", "trova utenti inattivi", 0.5)
        r = tsc.run_once(dry_run=True, max_jobs=10)
        self.assertEqual(r["scanned"], 2)
        self.assertEqual(r["processed"], 0)  # dry_run
        self.assertTrue(r["dry_run"])
        # Markers ancora presenti (no move)
        self.assertEqual(len(list(self.pending.iterdir())), 2)

    def test_rate_limit_caps_processing(self):
        for i in range(5):
            self._write_marker(f"sig_{i}", f"find_x_{i}", f"intent_{i}")
        # max_jobs=2 simula daily_cap=2
        r = tsc.run_once(dry_run=True, max_jobs=2)
        self.assertEqual(r["scanned"], 5)
        self.assertEqual(r["budget"], 2)
        self.assertEqual(r["skipped_rate_limit"], 3)

    def test_priority_queue_alignment_desc(self):
        self._write_marker("low",  "find_a", "ia", 0.3)
        self._write_marker("high", "find_b", "ib", 0.9)
        self._write_marker("mid",  "find_c", "ic", 0.6)
        # Mock handle_synth_request per evitare LLM
        with mock.patch("synth_request.handle_synth_request") as m:
            m.return_value = {"ok": True, "synthesized": True, "installed": True}
            r = tsc.run_once(max_jobs=10)
        # Call order: high (0.9), mid (0.6), low (0.3)
        called_names = [c.args[0]["expected_name"] for c in m.call_args_list]
        self.assertEqual(called_names, ["find_b", "find_c", "find_a"])
        self.assertEqual(r["success"], 3)

    def test_invalid_marker_moved_to_invalid(self):
        # Missing expected_name
        (self.pending / "bad.json").write_text(json.dumps({
            "kind": "synt_request", "sig": "bad", "intent": "x",
            "expected_alignment": 0.7,
        }), encoding="utf-8")
        with mock.patch("synth_request.handle_synth_request") as m:
            r = tsc.run_once(max_jobs=10)
            self.assertEqual(m.call_count, 0)  # NON chiamato
        self.assertEqual(r["failed"], 1)
        self.assertTrue((self.processed / "bad.invalid.json").exists())

    def test_success_marker_moved_with_result(self):
        self._write_marker("ok1", "find_z", "intent z")
        with mock.patch("synth_request.handle_synth_request") as m:
            m.return_value = {"ok": True, "synthesized": True, "installed": True,
                              "proposed_name": "find_z", "elapsed_s": 1.2}
            tsc.run_once(max_jobs=10)
        self.assertTrue((self.processed / "ok1.success.json").exists())
        self.assertTrue((self.processed / "ok1.success.result.json").exists())
        result = json.loads(
            (self.processed / "ok1.success.result.json").read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(result["proposed_name"], "find_z")

    def test_noop_short_circuit_counts_as_success(self):
        """already_in_catalog / redirected / l7_admission → noop ma OK."""
        self._write_marker("noop1", "find_files", "trova file")
        with mock.patch("synth_request.handle_synth_request") as m:
            m.return_value = {"ok": True, "synthesized": False,
                              "already_in_catalog": True, "name": "find_files"}
            r = tsc.run_once(max_jobs=10)
        self.assertEqual(r["success"], 1)
        self.assertEqual(r["failed"], 0)
        self.assertTrue((self.processed / "noop1.noop.json").exists())


if __name__ == "__main__":
    unittest.main()
