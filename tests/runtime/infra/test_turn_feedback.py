"""turn_feedback OK/Errore (E.1, 22/5/2026).

Test apply_feedback con turn log fittizio: persistenza record, campi audit
(canonical, fast_path_hit), rejected pipelines LWW, counter consecutivi.
(11/6/2026: rimossi i test di rinforzo/demote multi_tool_paths — ADR 0150
ritirato insieme ai rami corrispondenti di apply_feedback.)

Run: python3 -m pytest tests/runtime/infra/test_turn_feedback.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


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

    def test_ok_without_canonical_records(self):
        turn = _fake_turn(canonical="")  # no canonical_query
        self._write_turn(turn)
        rec = self.TF.apply_feedback("abc123", "ok")
        self.assertEqual(rec["action"], "ok")
        self.assertIsNone(rec["canonical"])

    def test_ok_audit_fields(self):
        """OK = neutro (nessun effetto cache); il record conserva i campi
        audit canonical + fast_path_hit (consumati da change_intents)."""
        turn = _fake_turn(canonical="test query", fast_path_hit=False)
        self._write_turn(turn)
        rec = self.TF.apply_feedback("abc123", "ok")
        self.assertEqual(rec["fast_path_hit"], False)
        self.assertEqual(rec["canonical"], "test query")
        self.assertEqual(rec["effects"], [])

    def test_error_audit_fields(self):
        """Error sotto soglia E12 = nessun effetto; record con audit fields
        + rejected_pipeline per il LWW del PLANNER."""
        turn = _fake_turn(canonical="test query", fast_path_hit=True)
        self._write_turn(turn)
        rec = self.TF.apply_feedback("abc123", "error")
        self.assertEqual(rec["fast_path_hit"], True)
        self.assertEqual(rec["effects"], [])
        self.assertEqual(rec["rejected_pipeline"],
                          ["find_files", "compute_entries"])

    def test_turn_not_found_returns_warning(self):
        rec = self.TF.apply_feedback("missing_turn", "ok")
        self.assertEqual(rec.get("warning"), "turn_not_found")

    def test_feedback_persisted(self):
        turn = _fake_turn(canonical="test")
        self._write_turn(turn)
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
        existing = ""
        tf = self.turns_dir / "x.jsonl"
        if tf.exists():
            existing = tf.read_text()
        tf.write_text(
            existing
            + json.dumps({"turn_id": turn_id, "user_query": user_query,
                          "steps": steps}) + "\n",
            encoding="utf-8",
        )
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

    def test_count_consecutive_errors_basic(self):
        self._record_error("t1", "test q", ["a"])
        self._record_error("t2", "test q", ["b"])
        n = self.TF.count_consecutive_errors_for_query("test q")
        self.assertEqual(n, 2)

    def test_count_consecutive_errors_resets_on_ok(self):
        """Un ✓ in mezzo resetta il counter consecutivo."""
        import time as _time
        self._record_error("t1", "q", ["a"])
        _time.sleep(0.01)
        with self.fb_path.open("a") as fh:
            fh.write(json.dumps({
                "turn_id": "t2", "action": "ok", "by": "u",
                "ts": _time.time(), "user_query": "q",
            }) + "\n")
        _time.sleep(0.01)
        self._record_error("t3", "q", ["b"])
        n = self.TF.count_consecutive_errors_for_query("q")
        self.assertEqual(n, 1,
                          "ok in mezzo deve resettare il counter; "
                          "ultimo error count = 1")

    def test_ok_after_error_revokes_rejection(self):
        """Utente preme ✗, poi ↻, sistema rifa stesso path corretto,
        utente preme ✓. La pipeline non deve restare in rejected (LWW)."""
        import time as _time
        # Step 1: error sulla pipeline A
        self._record_error("t1", "test query", ["find_dirs", "compute_entries"])
        # Step 2: simula feedback ok sulla stessa pipeline + stessa query.
        # _record_error usa apply_feedback(error). Per simulate ok, scrivo
        # direttamente nel file (idempotent format).
        _time.sleep(0.01)  # ts ordering
        with self.fb_path.open("a") as fh:
            fh.write(json.dumps({
                "turn_id": "t2", "action": "ok", "by": "user",
                "ts": _time.time(),
                "user_query": "test query",
                "approved_pipeline": ["find_dirs", "compute_entries"],
            }) + "\n")
        out = self.TF.rejected_pipelines_for_query("test query")
        self.assertEqual(out, [],
                          "OK successivo deve annullare rejection LWW")


if __name__ == "__main__":
    unittest.main()
