"""Honesty §2.8 — final_message degenere (nudo-conteggio) mai mostrato (banco #1).

Bug live 23/6: «leggi eventi, estrai, crea foglio» sul path decomposer →
final_message="0" (template-render a vuoto su piano monco: create_files_
spreadsheet droppato). "0" sfuggiva a _detect_false_success (items>0) e a
_detect_false_mutation (nessun claim regex). Terza guardia: un final degenere è
sostituito con la verità (no-results o conteggio onesto).

Run: `python3 -m pytest runtime/tests/test_degenerate_final_honesty.py -xvs`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from agent_runtime import _is_degenerate_final  # noqa: E402


class IsDegenerateFinalTests(unittest.TestCase):

    def test_degenerate_cases_true(self):
        for s in ["0", "3", "", "   ", "(2 elementi)", "[5]", "12 risultati",
                  "0 results", "  7  ", "3,5", "(0)", "1 voci", "9 items"]:
            self.assertTrue(_is_degenerate_final(s),
                            f"atteso degenere: {s!r}")

    def test_none_is_degenerate(self):
        self.assertTrue(_is_degenerate_final(None))

    def test_real_answers_false(self):
        for s in [
            "Ho creato il foglio con 3 eventi.",
            "Sono le 14:30.",
            "Nessun risultato trovato.",
            "Ho trovato 3 foto simili nel tuo album.",
            "3 eventi: Riunione, Call, Pranzo.",
            "Elaborato 1 elemento. Nessun'altra azione completata.",
            "La posizione attuale è Milano.",
            "0 mail trovate nella casella, ma 2 nello spam.",
            "Processed 5 messages and sent the summary.",
        ]:
            self.assertFalse(_is_degenerate_final(s),
                             f"NON deve essere degenere: {s!r}")


class DegenerateFinalReplacementTests(unittest.TestCase):
    """La sostituzione nel finalize (write) — testata sul TurnLog reale via
    effect_counts simulati. Verifica che '0' diventi un messaggio onesto."""

    def _make_log_with(self, final_message, steps_results):
        import agent_runtime
        log = agent_runtime.TurnLog(ts_start=0.0, user_query="q")
        for i, res in enumerate(steps_results, start=1):
            sl = agent_runtime.StepLog(step_num=i)
            sl.chosen_tool = res["tool"]
            sl.result = res["result"]
            log.steps.append(sl)
        log.final_message = final_message
        log.final_kind = "answer"
        return log

    def test_bare_zero_replaced_with_honest_count(self):
        # read_events produsse 1 item, NESSUNA mutazione → '0' degenere →
        # messaggio onesto sul conteggio, NON '0'.
        log = self._make_log_with("0", [
            {"tool": "read_events",
             "result": {"ok": True, "entries": [{"summary": "R"}]}},
            {"tool": "extract_entries",
             "result": {"ok": True, "entries": [{"summary": "R"}]}},
        ])
        log.write()
        self.assertNotEqual((log.final_message or "").strip(), "0")
        self.assertFalse(_is_degenerate_final(log.final_message),
                         f"sostituzione ancora degenere: {log.final_message!r}")
        self.assertTrue(log.false_success_detected)

    def test_bare_zero_no_items_becomes_no_results(self):
        # pipeline a 0 items e 0 mutazioni → MSG_NO_RESULTS.
        from messages import get as _msg
        log = self._make_log_with("0", [
            {"tool": "read_events", "result": {"ok": True, "entries": []}},
        ])
        log.write()
        self.assertEqual(log.final_message, _msg("MSG_NO_RESULTS"))

    def test_legit_message_untouched(self):
        # un messaggio reale non viene toccato dalla guardia degenere.
        log = self._make_log_with("Ho trovato 3 eventi in calendario.", [
            {"tool": "read_events",
             "result": {"ok": True, "entries": [{"a": 1}, {"a": 2}, {"a": 3}]}},
        ])
        log.write()
        self.assertEqual(log.final_message, "Ho trovato 3 eventi in calendario.")


if __name__ == "__main__":
    unittest.main()
