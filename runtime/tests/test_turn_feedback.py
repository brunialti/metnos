"""turn_feedback OK/Errore (E.1, 22/5/2026).

Test apply_feedback con turn log fittizio: rinforza fast-path HIT,
demote fast-path su error, promote candidate→active dopo cumulative OK.

Run: python3 -m pytest runtime/tests/test_turn_feedback.py -v
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


def _fake_turn(turn_id="abc123", canonical="test query",
                fast_path_hit=True, tools=None) -> dict:
    """Costruisce un turn dict come quelli serializzati nel turn log."""
    tools = tools or ["find_files", "compute_entries", "final_answer"]
    steps = []
    for i, t in enumerate(tools):
        # Fast-path HIT: llm_in_tokens=0 e llm_latency_ms=0 sugli step
        # pre-final. Path LLM-generato: llm_in_tokens>0.
        is_final = t == "final_answer"
        is_pre_final = not is_final
        if fast_path_hit and is_pre_final:
            llm_tokens = 0
            llm_ms = 0
        elif is_pre_final:
            llm_tokens = 1000
            llm_ms = 500
        else:
            llm_tokens = 0
            llm_ms = 0
        steps.append({
            "step_num": i + 1,
            "chosen_tool": t,
            "canonical_query": canonical if i == 0 else "",
            "llm_in_tokens": llm_tokens,
            "llm_latency_ms": llm_ms,
        })
    return {"turn_id": turn_id, "user_query": "x", "steps": steps}


class ApplyFeedbackTests(unittest.TestCase):

    def setUp(self):
        import turn_feedback as TF
        self.tmp = tempfile.TemporaryDirectory()
        self.fb_path = Path(self.tmp.name) / "fb.jsonl"
        self.turns_dir = Path(self.tmp.name) / "turns"
        self.turns_dir.mkdir()
        self._orig_fb = TF.FEEDBACK_PATH
        self._orig_turns = TF.TURNS_DIR
        TF.FEEDBACK_PATH = self.fb_path
        TF.TURNS_DIR = self.turns_dir
        self.TF = TF

    def tearDown(self):
        self.TF.FEEDBACK_PATH = self._orig_fb
        self.TF.TURNS_DIR = self._orig_turns
        self.tmp.cleanup()

    def _write_turn(self, turn: dict):
        (self.turns_dir / "today.jsonl").write_text(
            json.dumps(turn) + "\n", encoding="utf-8"
        )

    def test_action_invalid_raises(self):
        with self.assertRaises(ValueError):
            self.TF.apply_feedback("abc", "maybe")

    def test_ok_without_canonical_is_noop(self):
        turn = _fake_turn(canonical="")  # no canonical_query
        self._write_turn(turn)
        rec = self.TF.apply_feedback("abc123", "ok")
        self.assertEqual(rec["action"], "ok")
        self.assertEqual(rec["effects"][0]["type"], "noop")

    def test_error_with_fast_path_demote(self):
        turn = _fake_turn(canonical="test query", fast_path_hit=True)
        self._write_turn(turn)
        with mock.patch.object(self.TF, "_demote_path",
                                return_value={"action": "demoted", "rows_deleted": 1}):
            rec = self.TF.apply_feedback("abc123", "error")
        self.assertEqual(rec["fast_path_hit"], True)
        self.assertEqual(rec["effects"][0]["type"], "demote_path")
        self.assertEqual(rec["effects"][0]["action"], "demoted")

    def test_ok_with_fast_path_reinforces(self):
        turn = _fake_turn(canonical="test query", fast_path_hit=True)
        self._write_turn(turn)
        with mock.patch.object(self.TF, "_reinforce_path",
                                return_value={"action": "reinforced",
                                              "uses_before": 1, "uses_after": 2}):
            rec = self.TF.apply_feedback("abc123", "ok")
        self.assertEqual(rec["effects"][0]["type"], "reinforce_path")

    def test_ok_without_fast_path_is_neutral(self):
        """OK su path LLM-generato (no fast-path HIT) = neutro per design.
        Path nuovi sono incerti; non rinforziamo finché il sistema non
        li osserva stabilmente."""
        turn = _fake_turn(canonical="test query", fast_path_hit=False)
        self._write_turn(turn)
        rec = self.TF.apply_feedback("abc123", "ok")
        self.assertEqual(rec["fast_path_hit"], False)
        self.assertEqual(rec["effects"][0]["type"], "noop")
        self.assertEqual(rec["effects"][0]["reason"], "ok_neutral_llm_path")

    def test_error_without_fast_path_is_neutral(self):
        """Error su path LLM-generato = neutro. Niente da cancellare nella
        cache, e non penalizziamo la canonical (next turn ripassa da LLM
        comunque)."""
        turn = _fake_turn(canonical="test query", fast_path_hit=False)
        self._write_turn(turn)
        rec = self.TF.apply_feedback("abc123", "error")
        self.assertEqual(rec["fast_path_hit"], False)
        self.assertEqual(rec["effects"][0]["type"], "noop")
        self.assertEqual(rec["effects"][0]["reason"], "error_neutral_llm_path")

    def test_turn_not_found_returns_warning(self):
        rec = self.TF.apply_feedback("missing_turn", "ok")
        self.assertEqual(rec.get("warning"), "turn_not_found")

    def test_feedback_persisted(self):
        turn = _fake_turn(canonical="test")
        self._write_turn(turn)
        with mock.patch.object(self.TF, "_reinforce_path",
                                return_value={"action": "noop"}):
            self.TF.apply_feedback("abc123", "ok")
        self.assertTrue(self.fb_path.exists())
        line = self.fb_path.read_text().strip()
        rec = json.loads(line)
        self.assertEqual(rec["turn_id"], "abc123")
        self.assertEqual(rec["action"], "ok")


class RejectedPipelinesTests(unittest.TestCase):
    """rejected_pipelines_for_query: lista pipeline rifiutate via ✗ feedback."""

    def setUp(self):
        import turn_feedback as TF
        self.tmp = tempfile.TemporaryDirectory()
        self.fb_path = Path(self.tmp.name) / "fb.jsonl"
        self.turns_dir = Path(self.tmp.name) / "turns"
        self.turns_dir.mkdir()
        self._orig_fb = TF.FEEDBACK_PATH
        self._orig_turns = TF.TURNS_DIR
        TF.FEEDBACK_PATH = self.fb_path
        TF.TURNS_DIR = self.turns_dir
        self.TF = TF

    def tearDown(self):
        self.TF.FEEDBACK_PATH = self._orig_fb
        self.TF.TURNS_DIR = self._orig_turns
        self.tmp.cleanup()

    def _record_error(self, turn_id, user_query, tools):
        steps = [{"chosen_tool": t, "canonical_query": user_query.lower()
                                                       if i == 0 else "",
                  "llm_in_tokens": 0, "llm_latency_ms": 0}
                 for i, t in enumerate(tools)]
        (self.turns_dir / "x.jsonl").write_text(
            (self.turns_dir / "x.jsonl").read_text() if (self.turns_dir / "x.jsonl").exists() else ""
            + json.dumps({"turn_id": turn_id, "user_query": user_query,
                          "steps": steps}) + "\n",
            encoding="utf-8",
        )
        with mock.patch.object(self.TF, "_demote_path",
                                return_value={"action": "demoted"}):
            self.TF.apply_feedback(turn_id, "error")

    def test_no_feedback_returns_empty(self):
        out = self.TF.rejected_pipelines_for_query("nessuna query")
        self.assertEqual(out, [])

    def test_error_feedback_saves_pipeline(self):
        self._record_error("t1", "conta file in X",
                            ["find_dirs", "compute_entries", "final_answer"])
        out = self.TF.rejected_pipelines_for_query("conta file in X")
        self.assertEqual(len(out), 1)
        # final_answer escluso (non e' un executor)
        self.assertEqual(out[0], ["find_dirs", "compute_entries"])

    def test_different_query_no_match(self):
        self._record_error("t1", "conta file in X", ["find_dirs"])
        out = self.TF.rejected_pipelines_for_query("conta file in Y")
        self.assertEqual(out, [])

    def test_match_case_insensitive_and_trimmed(self):
        self._record_error("t1", "conta file in X  ", ["find_dirs"])
        out = self.TF.rejected_pipelines_for_query("CONTA file IN x")
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
