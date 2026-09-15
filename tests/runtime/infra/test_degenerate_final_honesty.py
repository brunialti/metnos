"""Honesty §2.8 — final_message degenere (nudo-conteggio) mai mostrato (banco #1).

Bug live 23/6: «leggi eventi, estrai, crea foglio» sul path decomposer →
final_message="0" (template-render a vuoto su piano monco: create_files_
spreadsheet droppato). "0" sfuggiva a _detect_false_success (items>0) e a
_detect_false_mutation (nessun claim regex). Terza guardia: un final degenere è
sostituito con la verità (no-results o conteggio onesto).

Run: `python3 -m pytest tests/runtime/infra/test_degenerate_final_honesty.py -xvs`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

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
        # TurnLog.write() drena intenzionalmente gli avvisi tardivi del
        # destinatario. Un test non deve usare il destinatario di produzione
        # (:host), altrimenti lo stato live rende l'asserzione non deterministica
        # e l'avviso reale viene consumato dal test runner.
        log = agent_runtime.TurnLog(
            ts_start=0.0, user_query="q",
            channel="test", actor="degenerate-final-tests")
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

    def test_nonempty_transforms_are_not_counted_as_additional_results(self):
        from messages import get as msg
        rows = [{"id": n} for n in range(14)]
        log = self._make_log_with("0", [
            {"tool": "read_objects", "result": {"ok": True, "entries": rows}},
            {"tool": "classify_entries", "result": {"ok": True, "entries": rows}},
        ])
        log.write()
        self.assertEqual(log.effect_counts["items"], 28)
        self.assertEqual(log.final_message, msg("MSG_DEGENERATE_FINAL_ITEMS", n=14))

    def test_successful_mutation_count_is_preserved_after_empty_read(self):
        from messages import get as msg
        log = self._make_log_with("0", [
            {"tool": "read_objects", "result": {"ok": True, "entries": []}},
            {"tool": "write_objects", "result": {"ok": True, "ok_count": 2, "results": [{}, {}]}},
        ])
        log.write()
        self.assertEqual(log.effect_counts["mutations"], 2)
        self.assertEqual(log.final_message, msg("MSG_DEGENERATE_FINAL_MUTATIONS", n=2))


@pytest.mark.parametrize("lang", ["it", "en"])
@pytest.mark.parametrize("upstream_hint", [False, True])
def test_degenerate_filter_fallback_never_revives_upstream_rows(lang, upstream_hint, monkeypatch):
    import i18n
    from messages import get as msg
    monkeypatch.setattr(i18n, "current_lang", lambda: lang)
    rows = [{"id": n} for n in range(14)]
    log = DegenerateFinalReplacementTests()._make_log_with("0", [
        {"tool": "read_objects", "result": {"ok": True, "entries": rows,
         **({"message": "Found 14 source rows."} if upstream_hint else {})}},
        {"tool": "classify_entries", "result": {"ok": True, "entries": rows}},
        {"tool": "filter_entries", "result": {"ok": True, "entries": [],
         "metadata": {"count_in": 14, "count_out": 0, "dropped": 14}}},
        {"tool": "final_answer", "result": {"ok": True}},
    ])
    log.write()
    assert log.effect_counts["items"] == 28
    assert log.final_message == msg("MSG_PROCESSOR_EMPTY", tool="filter_entries")


class NonDeletePartialHonestyTests(unittest.TestCase):
    """2ter (7/7): §2.8 sul PARZIALE non-delete. Il ramo partial_mutation di
    dispatch produce «Operazione completata: N» su un mutante con effetti reali
    in un run 'error'; il finalizer DEVE poi rendere visibili i falliti anche
    quando il mutante NON ritorna una lista `results` (send/share multi-account
    che ritornano solo un contatore + failed[]). Prima erano MASCHERATI."""

    def _log(self, tool, result, base):
        import agent_runtime
        log = agent_runtime.TurnLog(ts_start=0.0, user_query="invia a 3 account")
        log.actor = "host"
        log.channel = "http"
        sl = agent_runtime.StepLog(step_num=1)
        sl.chosen_tool = tool
        sl.result = result
        log.steps.append(sl)
        log.final_message = base
        log.final_kind = "answer"
        log._enforce_mutating_honesty()
        return log.final_message

    _BASE = "Operazione completata: 1 elemento modificato o rimosso."

    def test_send_partial_with_results_surfaces_recipients(self):
        out = self._log("send_messages", {
            "ok": False, "n_sent": 1, "fail_count": 2,
            "results": [{"ok": True, "to": "a@x"}],
            "failed": [{"to": "b@x", "error": "auth fallita"},
                       {"to": "c@x", "error": "auth fallita"}]}, self._BASE)
        self.assertIn("b@x", out)   # destinatario, non «?»
        self.assertIn("c@x", out)
        self.assertNotIn("«?»", out)

    def test_send_partial_without_results_not_masked(self):
        # IL BUG 2ter: n_sent+failed ma NESSUN `results` → prima il finalizer
        # saltava (richiedeva "results" in res) e i falliti sparivano.
        out = self._log("send_messages", {
            "ok": False, "n_sent": 1, "fail_count": 2,
            "failed": [{"to": "b@x", "error": "auth fallita"},
                       {"to": "c@x", "error": "auth fallita"}]}, self._BASE)
        self.assertIn("b@x", out)
        self.assertIn("c@x", out)
        self.assertNotEqual(out.strip(), self._BASE)

    def test_read_with_failed_not_treated_as_mutating(self):
        # Un READ con failed[] (account SSL-fail) NON è mutante: lo gestisce
        # _collect_failure_notices, non _enforce_mutating_honesty. Nessun
        # doppio-avviso, il final di lettura non viene sostituito.
        out = self._log("read_messages", {
            "entries": [{"id": 1}],
            "failed": [{"account": "imap1", "error": "ssl"}]},
            "Hai 1 messaggio.")
        self.assertEqual(out, "Hai 1 messaggio.")

    def test_top_level_mutation_error_after_success_is_not_masked(self):
        import agent_runtime
        log = agent_runtime.TurnLog(ts_start=0.0, user_query="crea due file")
        for index, (tool, result) in enumerate([
            ("write_files", {
                "ok": True, "ok_count": 1, "fail_count": 0,
                "results": [{"ok": True, "path": "riepilogo.md"}],
                "failed": [],
            }),
            ("create_files_spreadsheet", {
                "ok": False,
                "error": "InvocationError: payload non serializzabile",
                "error_class": "exception",
            }),
        ], start=1):
            step = agent_runtime.StepLog(step_num=index)
            step.chosen_tool = tool
            step.result = result
            log.steps.append(step)
        log.final_message = self._BASE

        log._enforce_mutating_honesty()

        self.assertIn("create_files_spreadsheet", log.final_message)
        self.assertIn("payload non serializzabile", log.final_message)
        self.assertNotEqual(log.final_message.strip(), self._BASE)

    def test_top_level_mutation_error_with_no_success_reports_none_done(self):
        out = self._log("create_files_spreadsheet", {
            "ok": False,
            "error": "InvocationError: payload non serializzabile",
            "error_class": "exception",
        }, self._BASE)
        self.assertIn("create_files_spreadsheet", out)
        self.assertIn("payload non serializzabile", out)
        self.assertNotEqual(out.strip(), self._BASE)


if __name__ == "__main__":
    unittest.main()
