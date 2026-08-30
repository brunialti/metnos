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
from types import SimpleNamespace

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


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


@pytest.fixture
def isolated_honesty_lexicon(monkeypatch, tmp_path):
    """Isolate manual-review state from the process-wide lexicon."""
    import detection_lexicon as dl
    import detection_lexicon_seed_runtime_safety as safety_lexicon
    import i18n

    old_conn = dl._conn
    monkeypatch.setattr(dl, "DB_PATH", tmp_path / "detection.sqlite")
    monkeypatch.setattr(dl, "_conn", None)
    monkeypatch.setattr(dl, "_seeded", False)
    monkeypatch.setattr(dl, "_cache", {})
    monkeypatch.setattr(dl, "_regex_cache", {})
    monkeypatch.setattr(dl, "_coverage_gaps_logged", set())
    monkeypatch.setattr(dl, "_declared_review_policies", {})
    monkeypatch.setattr(dl, "_declared_baseline_languages", {})
    monkeypatch.setattr(safety_lexicon, "_registered_target", None)
    monkeypatch.setattr(i18n, "current_lang", lambda: "it")
    yield dl, safety_lexicon, i18n
    new_conn = dl._conn
    if new_conn is not None and new_conn is not old_conn:
        new_conn.close()


def test_honesty_checks_fail_closed_without_native_reviewed_language(
        isolated_honesty_lexicon, monkeypatch):
    """Missing translations cannot substantiate an unverified receipt."""
    _dl, _safety_lexicon, i18n = isolated_honesty_lexicon
    from agent_runtime import (
        _detect_false_mutation,
        _detect_false_not_found,
        _detect_false_success,
        _detect_unbacked_artifact_claim,
        _detect_unbacked_promise,
        _is_degenerate_final,
    )

    monkeypatch.setattr(i18n, "current_lang", lambda: "es")
    neutral = "Respuesta completa."
    mutation = SimpleNamespace(
        chosen_tool="write_files", result={"ok": True, "ok_count": 1},
    )
    assert _detect_unbacked_promise(neutral, [])
    assert _detect_false_not_found(neutral, [mutation]) == {
        "tool": "write_files", "ok_count": 1,
    }
    assert _detect_false_success(neutral, {
        "failures": 0, "countable": 1, "items": 0, "mutations": 0,
    })
    assert _detect_false_mutation(neutral, {"mutations": 0})
    assert _is_degenerate_final(neutral)
    assert _detect_unbacked_artifact_claim(neutral, []) == {
        "archive", "document", "spreadsheet",
    }

    # Structural proof remains authoritative even with no language resource.
    assert not _detect_unbacked_promise(neutral, [_step("create_tasks")])
    assert not _detect_false_mutation(neutral, {"mutations": 1})


def test_honesty_third_language_pending_ready_and_manual_policy(
        isolated_honesty_lexicon, monkeypatch):
    dl, safety_lexicon, i18n = isolated_honesty_lexicon
    from agent_runtime import _detect_unbacked_promise

    safety_lexicon.register_all()
    dl.mark_for_translation(safety_lexicon.UNBACKED_PROMISE, "es")
    monkeypatch.setattr(i18n, "current_lang", lambda: "es")

    # Pending remains fail-closed.
    assert _detect_unbacked_promise("Respuesta completa.", [])

    dl.set_translated(
        safety_lexicon.UNBACKED_PROMISE, "es", [r"\bte\s+avisare\b"],
    )
    assert _detect_unbacked_promise("Te avisare manana.", [])
    assert not _detect_unbacked_promise("Respuesta completa.", [])

    # A non-manual row is unavailable even if its payload is syntactically
    # valid and marked ready.
    dl._open().execute(
        "UPDATE detection_lexicon SET review_policy='automatic' "
        "WHERE concept=? AND lang=?",
        (safety_lexicon.UNBACKED_PROMISE, "es"),
    )
    dl._open().commit()
    dl._invalidate(safety_lexicon.UNBACKED_PROMISE)
    assert _detect_unbacked_promise("Respuesta completa.", [])


if __name__ == "__main__":
    unittest.main()
