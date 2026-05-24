"""proposal_actions on_accept (C.8, 22/5/2026).

Test deterministici: classifica accept → marker corretto per name_status.

Run: python3 -m pytest runtime/tests/test_proposal_actions.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


class FakeProposal:
    """Helper per dict UnifiedProposal-shape."""
    @staticmethod
    def new(name_status="new_valid", target="compress_files_zip",
            sig_relaxed="abc", prop_id="t1", expected_alignment=0.7):
        return {
            "prop_id": prop_id,
            "source": "telos",
            "executor_target": target,
            "proposed_action": "azione test",
            "rationale": "rationale test",
            "name_status": name_status,
            "signature_relaxed": sig_relaxed,
            "convergence_count": 1,
            "convergence_lenses": [],
            "pipeline_tools_mentioned": [],
            "is_parametric_extension": False,
            "expected_alignment": expected_alignment,
        }


def _decision(action="accept"):
    return {"action": action, "ts": 1.0, "by": "test"}


class OnAcceptTests(unittest.TestCase):

    def setUp(self):
        import proposal_actions as PA
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self._orig = (PA.SYNT_PENDING_DIR, PA.CHANGE_PENDING_DIR,
                       PA.PIPELINE_PENDING_DIR, PA.TELOS_FILTERED_LOG)
        PA.SYNT_PENDING_DIR = self.tmpdir / "synt_pending"
        PA.CHANGE_PENDING_DIR = self.tmpdir / "change_pending"
        PA.PIPELINE_PENDING_DIR = self.tmpdir / "pipeline_pending"
        PA.TELOS_FILTERED_LOG = self.tmpdir / "telos_filtered.jsonl"
        self.PA = PA

    def tearDown(self):
        (self.PA.SYNT_PENDING_DIR, self.PA.CHANGE_PENDING_DIR,
         self.PA.PIPELINE_PENDING_DIR, self.PA.TELOS_FILTERED_LOG) = self._orig
        self.tmp.cleanup()

    def test_new_valid_creates_synt_marker(self):
        r = self.PA.on_accept(
            FakeProposal.new(name_status="new_valid"), _decision())
        self.assertEqual(r["kind"], "synt_pending")
        self.assertTrue(r["created"])
        self.assertTrue(Path(r["marker_path"]).exists())

    def test_existing_parametric_creates_change_marker(self):
        r = self.PA.on_accept(
            FakeProposal.new(name_status="existing_parametric"), _decision())
        self.assertEqual(r["kind"], "change_pending")
        self.assertTrue(r["created"])

    def test_existing_pipeline_creates_pipeline_marker(self):
        r = self.PA.on_accept(
            FakeProposal.new(name_status="existing_pipeline"), _decision())
        self.assertEqual(r["kind"], "pipeline_pending")
        self.assertTrue(r["created"])

    def test_existing_redundant_is_noop(self):
        r = self.PA.on_accept(
            FakeProposal.new(name_status="existing_redundant"), _decision())
        self.assertEqual(r["kind"], "noop")
        self.assertIn("existing_redundant", r.get("reason", ""))

    def test_new_invalid_is_noop(self):
        r = self.PA.on_accept(
            FakeProposal.new(name_status="new_invalid"), _decision())
        self.assertEqual(r["kind"], "noop")

    def test_not_accept_is_noop(self):
        r = self.PA.on_accept(
            FakeProposal.new(), _decision(action="reject"))
        self.assertEqual(r["kind"], "noop")

    def test_cluster_dedup_idempotent(self):
        """Stessa signature_relaxed → 2 accept producono 1 solo marker."""
        p1 = FakeProposal.new(prop_id="t1", sig_relaxed="cluster_X")
        p2 = FakeProposal.new(prop_id="t2", sig_relaxed="cluster_X")
        r1 = self.PA.on_accept(p1, _decision())
        r2 = self.PA.on_accept(p2, _decision())
        self.assertTrue(r1["created"])
        self.assertFalse(r2["created"],
                          "second accept stesso cluster → no nuovo marker")
        n_markers = len(list(self.PA.SYNT_PENDING_DIR.glob("*.json")))
        self.assertEqual(n_markers, 1, "1 solo marker per cluster")

    def test_pending_markers_listing(self):
        self.PA.on_accept(
            FakeProposal.new(name_status="new_valid", sig_relaxed="a"),
            _decision())
        self.PA.on_accept(
            FakeProposal.new(name_status="existing_parametric", sig_relaxed="b"),
            _decision())
        all_pending = self.PA.pending_markers()
        self.assertEqual(len(all_pending), 2)
        only_synt = self.PA.pending_markers(kind="synt_pending")
        self.assertEqual(len(only_synt), 1)
        self.assertEqual(only_synt[0]["_marker_kind"], "synt_pending")


class HardGateTests(unittest.TestCase):
    """Test C.8 fase 2 (24/5/2026): hard gate alignment in on_accept."""

    def setUp(self):
        import proposal_actions as PA
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self._orig = (PA.SYNT_PENDING_DIR, PA.CHANGE_PENDING_DIR,
                       PA.PIPELINE_PENDING_DIR, PA.TELOS_FILTERED_LOG)
        PA.SYNT_PENDING_DIR = self.tmpdir / "synt_pending"
        PA.CHANGE_PENDING_DIR = self.tmpdir / "change_pending"
        PA.PIPELINE_PENDING_DIR = self.tmpdir / "pipeline_pending"
        PA.TELOS_FILTERED_LOG = self.tmpdir / "telos_filtered.jsonl"
        self.PA = PA

    def tearDown(self):
        (self.PA.SYNT_PENDING_DIR, self.PA.CHANGE_PENDING_DIR,
         self.PA.PIPELINE_PENDING_DIR, self.PA.TELOS_FILTERED_LOG) = self._orig
        self.tmp.cleanup()

    def test_below_hard_gate_blocked_no_marker(self):
        """Proposta con expected_alignment < 0.45 → noop, niente marker."""
        p = FakeProposal.new(expected_alignment=0.30)
        r = self.PA.on_accept(p, _decision())
        self.assertEqual(r["kind"], "noop")
        self.assertIn("below_hard_gate", r["reason"])
        self.assertFalse(list(self.PA.SYNT_PENDING_DIR.glob("*")))
        # Audit log scritto
        self.assertTrue(self.PA.TELOS_FILTERED_LOG.exists())
        import json as _json
        line = self.PA.TELOS_FILTERED_LOG.read_text().strip()
        rec = _json.loads(line)
        self.assertEqual(rec["expected_alignment"], 0.30)

    def test_above_hard_gate_proceeds(self):
        """Proposta con expected_alignment >= 0.45 → marker creato."""
        p = FakeProposal.new(expected_alignment=0.50)
        r = self.PA.on_accept(p, _decision())
        self.assertEqual(r["kind"], "synt_pending")
        self.assertTrue(r["created"])
        # Audit log NON scritto (non filtrata)
        self.assertFalse(self.PA.TELOS_FILTERED_LOG.exists())

    def test_exactly_at_gate_proceeds(self):
        """Boundary: expected_alignment == hard_gate → ammessa."""
        p = FakeProposal.new(expected_alignment=0.45)
        r = self.PA.on_accept(p, _decision())
        self.assertEqual(r["kind"], "synt_pending")


if __name__ == "__main__":
    unittest.main()
