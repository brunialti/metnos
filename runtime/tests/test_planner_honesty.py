"""Test del self-check anti-allucinazione su final_message (Bug B, 5/5/2026).

Caso live turn 23be1548: dopo che la chain web e' fallita su validation
(read_urls_html senza urls), il PLANNER ha emesso un final_message che
prometteva: "ti aggiorno non appena", "sto effettuando una ricerca mirata".
Nessuno step ok ha registrato un'azione futura — promessa vuota.

Fix strutturale (no LLM, regex deterministico §7.9): se final_message contiene
verbi di promessa futura E nessuno step ok ha chiamato un tool che registra
azioni (create_tasks/send_messages/write_files/...), il runtime
prepende una notice "azione NON registrata".
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))


def _step(tool: str, ok: bool = True) -> object:
    """Costruisce uno StepLog minimale per il test."""
    from agent_runtime import StepLog
    s = StepLog(step_num=1)
    s.chosen_tool = tool
    s.result = {"ok": ok}
    return s


class TestUnbackedPromiseDetection(unittest.TestCase):
    def test_promise_ti_aggiornero_no_step(self):
        """final_message con 'ti aggiornerò' e nessuno step → notice."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Ho cercato sul sito ufficiale, ti aggiornerò non appena trovo i numeri."
        self.assertTrue(_detect_unbacked_promise(msg, []))

    def test_promise_with_create_tasks_ok(self):
        """final_message con promessa MA c'e' create_tasks ok → no notice."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Ho schedulato il monitoraggio quotidiano, ti aggiornerò appena trovo dati."
        steps = [_step("create_tasks", ok=True)]
        self.assertFalse(_detect_unbacked_promise(msg, steps))

    def test_promise_with_send_messages_ok(self):
        """send_messages ok → promessa supportata."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Ti farò sapere quando arriva la risposta."
        steps = [_step("send_messages", ok=True)]
        self.assertFalse(_detect_unbacked_promise(msg, steps))

    def test_promise_with_failed_schedule_no_support(self):
        """create_tasks FAIL → promessa NON supportata → notice."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Ti aggiornerò domani con i risultati."
        steps = [_step("create_tasks", ok=False)]
        self.assertTrue(_detect_unbacked_promise(msg, steps))

    def test_neutral_message_no_notice(self):
        """final_message neutro ('ho fatto X') → no notice."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Ho letto 5 file e li ho elencati qui sopra."
        self.assertFalse(_detect_unbacked_promise(msg, []))

    def test_promise_sto_cercando_no_step(self):
        """'sto cercando' senza tool registrato → notice."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Sto cercando ulteriori informazioni e ti contatterò appena disponibili."
        self.assertTrue(_detect_unbacked_promise(msg, []))

    def test_promise_sto_effettuando_no_step(self):
        """'sto effettuando una ricerca' (live caso 23be1548) → notice."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Sto effettuando una ricerca mirata sui siti istituzionali."
        self.assertTrue(_detect_unbacked_promise(msg, []))

    def test_promise_appena_avro_no_step(self):
        """'appena avrò i dati' senza step ok → notice."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Appena avrò i dati ti faccio sapere."
        self.assertTrue(_detect_unbacked_promise(msg, []))

    def test_promise_ti_informero_no_step(self):
        """'ti informerò' senza step → notice."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Ti informerò appena trovo qualcosa di interessante."
        self.assertTrue(_detect_unbacked_promise(msg, []))

    def test_promise_ti_dirò_with_accents(self):
        """Accento ò → match correttamente."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Ti dirò il risultato non appena disponibile."
        self.assertTrue(_detect_unbacked_promise(msg, []))

    def test_promise_ti_diro_without_accent(self):
        """Variante senza accento → match."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Ti diro' il risultato non appena disponibile."
        self.assertTrue(_detect_unbacked_promise(msg, []))

    def test_empty_message_no_notice(self):
        """final_message vuoto → no notice."""
        from agent_runtime import _detect_unbacked_promise
        self.assertFalse(_detect_unbacked_promise("", []))
        self.assertFalse(_detect_unbacked_promise(None, []))

    def test_promise_with_write_files_ok(self):
        """write_files ok → promessa supportata."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Ho scritto il file e ti aggiornerò se cambia."
        steps = [_step("write_files", ok=True)]
        self.assertFalse(_detect_unbacked_promise(msg, steps))

    def test_promise_with_unrelated_step(self):
        """Step ok ma non in registered_future_tools → promessa unbacked."""
        from agent_runtime import _detect_unbacked_promise
        msg = "Ti aggiornerò sui risultati."
        steps = [_step("read_files", ok=True)]
        self.assertTrue(_detect_unbacked_promise(msg, steps))


class TestTurnLogIntegration(unittest.TestCase):
    """Test integrazione: TurnLog.write() prepende notice quando rilevata."""

    def test_turnlog_appends_notice_on_unbacked_promise(self):
        """write() prepende notice se final_kind=answer + unbacked promise."""
        import time
        import tempfile
        from agent_runtime import TurnLog
        # Mock TURN_LOG_DIR a un tempdir per non sporcare lo storage reale.
        with tempfile.TemporaryDirectory() as td:
            import agent_runtime as _ar
            _orig = _ar.TURN_LOG_DIR
            _ar.TURN_LOG_DIR = Path(td)
            try:
                log = TurnLog(ts_start=time.time(), ts_end=time.time(),
                              user_query="test query", turn_id="test_turn",
                              mode="local")
                log.final_kind = "answer"
                log.final_message = "Ti aggiornerò domani con i numeri."
                log.write()
                self.assertTrue(log.unbacked_promise_detected)
                self.assertIn("NON sono state effettivamente registrate",
                              log.final_message)
            finally:
                _ar.TURN_LOG_DIR = _orig

    def test_turnlog_no_notice_on_neutral_message(self):
        """write() NON prepende notice su messaggio neutro."""
        import time
        import tempfile
        from agent_runtime import TurnLog
        with tempfile.TemporaryDirectory() as td:
            import agent_runtime as _ar
            _orig = _ar.TURN_LOG_DIR
            _ar.TURN_LOG_DIR = Path(td)
            try:
                log = TurnLog(ts_start=time.time(), ts_end=time.time(),
                              user_query="test", turn_id="t1", mode="local")
                log.final_kind = "answer"
                log.final_message = "Ho letto 3 file."
                log.write()
                self.assertFalse(log.unbacked_promise_detected)
                self.assertNotIn("NON sono state effettivamente",
                                 log.final_message)
            finally:
                _ar.TURN_LOG_DIR = _orig


if __name__ == "__main__":
    unittest.main()
