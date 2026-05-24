"""Invarianti runtime sul final_message (§2.8 no silent failure).

Due famiglie di fix (24/5/2026):

1) `_collect_truncation_notices` skippa i PROCESSOR (`describe`, `classify`,
   `filter`, `sort`, `group`, `compute`, `compare`). Il loro `truncated:True`
   e' metadata di cap_expand interno al PLANNER (§2.11), non un evento sul
   dato sorgente — il producer upstream ha gia' emesso il proprio notice.

2) `TurnLog.write()` garantisce che `final_kind in {answer, ask, error,
   loop_break}` ⇒ `final_message` non vuoto. Sintesi best-effort dal contesto
   (last step obs → MSG_FINAL_FALLBACK_FROM_ERROR → MSG_FINAL_FALLBACK_GENERIC).

Run: python3 -m pytest runtime/tests/test_final_message_invariant.py -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


class TestTruncationNoticeSkipsProcessors(unittest.TestCase):
    """`_collect_truncation_notices` ignora i processor verbs."""

    def _make_log(self, *steps_spec):
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(ts_start=0.0, turn_id="t_tr", user_query="q")
        for n, (tool, result) in enumerate(steps_spec, start=1):
            s = StepLog(step_num=n, chosen_tool=tool, raw_args={})
            s.result = result
            log.steps.append(s)
        return log

    def test_producer_truncated_emits_notice(self):
        log = self._make_log(
            ("find_files", {"ok": True, "truncated": True,
                            "truncated_what": "file",
                            "used": 1000, "available_total": 63178,
                            "entries": []}),
        )
        notices = log._collect_truncation_notices()
        self.assertEqual(len(notices), 1)
        self.assertIn("63178", notices[0])
        self.assertIn("1000", notices[0])
        self.assertIn("file", notices[0])

    def test_processor_describe_truncated_skipped(self):
        log = self._make_log(
            ("find_files", {"ok": True, "truncated": True,
                            "truncated_what": "file",
                            "used": 1000, "available_total": 63178,
                            "entries": []}),
            ("describe_entries", {"ok": True, "truncated": True,
                                  "truncated_what": "describe",
                                  "used": 20, "available_total": 1000,
                                  "summary": "..."}),
        )
        notices = log._collect_truncation_notices()
        # Solo il producer emette notice, NON il processor.
        self.assertEqual(len(notices), 1)
        self.assertNotIn("describe", notices[0].lower())

    def test_all_processor_verbs_skipped(self):
        for verb in ("describe", "classify", "filter", "sort", "group",
                     "compute", "compare"):
            log = self._make_log(
                (f"{verb}_entries", {"ok": True, "truncated": True,
                                     "truncated_what": verb,
                                     "used": 5, "available_total": 100}),
            )
            notices = log._collect_truncation_notices()
            self.assertEqual(
                notices, [],
                f"verb={verb!r}: processor truncation must not emit notice",
            )


class TestFinalMessageInvariant(unittest.TestCase):
    """`final_kind` terminale ⇒ `final_message` non vuoto."""

    def setUp(self):
        # Isolare scrittura turn jsonl in tmp.
        self._tmp = tempfile.TemporaryDirectory()
        import agent_runtime
        self._orig_dir = agent_runtime.TURN_LOG_DIR
        agent_runtime.TURN_LOG_DIR = Path(self._tmp.name)
        self._agent_runtime = agent_runtime

    def tearDown(self):
        self._agent_runtime.TURN_LOG_DIR = self._orig_dir
        self._tmp.cleanup()

    def _make_turn_with_failed_step(self, tool, result):
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(ts_start=0.0, turn_id="t_inv", user_query="q",
                      final_kind="answer", final_message="")
        s = StepLog(step_num=1, chosen_tool=tool, raw_args={})
        s.result = result
        log.steps.append(s)
        log.ts_end = 0.1
        return log

    def test_empty_final_with_step_error_uses_error_fallback(self):
        log = self._make_turn_with_failed_step(
            "send_messages",
            {"ok": False, "failed": [{"target": "roberto",
                                       "error": "no_verified_channel"}]},
        )
        log.write()
        # final_message NON deve essere vuoto.
        self.assertGreater(len(log.final_message.strip()), 0,
                            "final_message vuoto viola §2.8")
        # Deve menzionare l'error (no_verified_channel) o il tool.
        fm = log.final_message.lower()
        self.assertTrue(
            "no_verified_channel" in fm or "send_messages" in fm,
            f"fallback non informativo: {log.final_message!r}",
        )

    def test_empty_final_no_steps_uses_generic_fallback(self):
        from agent_runtime import TurnLog
        log = TurnLog(ts_start=0.0, turn_id="t_empty", user_query="q",
                      final_kind="answer", final_message="")
        log.ts_end = 0.1
        log.write()
        self.assertGreater(len(log.final_message.strip()), 0,
                            "final_message vuoto viola §2.8")

    def test_non_empty_final_preserved(self):
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(ts_start=0.0, turn_id="t_pres", user_query="q",
                      final_kind="answer", final_message="ciao")
        s = StepLog(step_num=1, chosen_tool="get_now", raw_args={})
        s.result = {"ok": True, "entries": [{"now": "x"}]}
        log.steps.append(s)
        log.ts_end = 0.1
        log.write()
        self.assertEqual(log.final_message, "ciao",
                         "non sovrascrivere final_message non vuoto")

    def test_ok_false_never_says_completato(self):
        """§2.8: ok=False non deve ricadere su «completato (0 elementi)»
        (output di compose_from_obs onestamente disonesto sui fail)."""
        log = self._make_turn_with_failed_step(
            "send_messages",
            {"ok": False, "ok_count": 0,
             "failed": [{"target": "x", "error": "no_verified_channel"}]},
        )
        log.write()
        self.assertNotIn("completato", log.final_message.lower(),
                          f"fail non puo' essere etichettato 'completato': "
                          f"{log.final_message!r}")
        self.assertIn("no_verified_channel", log.final_message.lower())

    def test_invariant_applies_to_loop_break(self):
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(ts_start=0.0, turn_id="t_lb", user_query="q",
                      final_kind="loop_break", final_message="")
        s = StepLog(step_num=1, chosen_tool="some_tool", raw_args={})
        s.result = {"ok": False, "error": "boom"}
        log.steps.append(s)
        log.ts_end = 0.1
        log.write()
        self.assertGreater(len(log.final_message.strip()), 0)


if __name__ == "__main__":
    unittest.main()
