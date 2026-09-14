"""Invarianti runtime sul final_message (§2.8 no silent failure).

Due famiglie di fix (24/5/2026):

1) `_collect_truncation_notices` skippa i PROCESSOR (`describe`, `classify`,
   `filter`, `sort`, `group`, `compute`, `compare`). Il loro `truncated:True`
   e' metadata di cap_expand interno al PLANNER (§2.11), non un evento sul
   dato sorgente — il producer upstream ha gia' emesso il proprio notice.

2) `TurnLog.write()` garantisce che `final_kind in {answer, ask, error,
   loop_break}` ⇒ `final_message` non vuoto. Sintesi best-effort dal contesto
   (last step obs → MSG_FINAL_FALLBACK_FROM_ERROR → MSG_FINAL_FALLBACK_GENERIC).

Run: python3 -m pytest tests/runtime/engine/test_final_message_invariant.py -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


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

    def test_authoritative_fragment_can_cover_its_display_limit(self):
        log = self._make_log(
            ("generic_exact_comparison", {
                "ok": True,
                "truncated": True,
                "used": 5,
                "available_total": 20,
                "authoritative_presentation": {
                    "scope": "always",
                    "text": "Scansione completa; elenco limitato.",
                    "covers_truncation": True,
                },
            }),
        )
        self.assertEqual(log._collect_truncation_notices(), [])

    def test_fragment_cannot_hide_limit_when_whole_turn_is_uncovered(self):
        log = self._make_log(
            ("generic_exact_comparison", {
                "ok": True,
                "truncated": True,
                "truncated_what": "duplicati",
                "used": 500,
                "available_total": 3084,
                "authoritative_presentation": {
                    "scope": "always",
                    "text": "Scansione completa; elenco limitato.",
                    "covers_truncation": True,
                },
            }),
            ("generic_uncovered_processor", {
                "ok": True,
                "entries": [{"value": 1}],
            }),
        )
        log.intent_verb = "compute"
        notices = log._collect_truncation_notices()
        self.assertEqual(len(notices), 1)
        self.assertIn("3084", notices[0])
        self.assertIn("500", notices[0])

    def test_key_shaped_truncation_label_is_localized(self):
        log = self._make_log(
            ("generic_producer", {
                "ok": True,
                "truncated": True,
                "truncated_what": "MSG_OBJECT_DUPLICATE_FILES",
                "used": 3,
                "available_total": 7,
            }),
        )
        notice = log._collect_truncation_notices()[0]
        self.assertNotIn("MSG_OBJECT_DUPLICATE_FILES", notice)

    def test_unknown_key_shaped_label_uses_localized_fallback(self):
        from agent_runtime import _resolve_i18n_payload_value
        label = _resolve_i18n_payload_value("MSG_KEY_THAT_DOES_NOT_EXIST")
        self.assertNotIn("<missing:", label)
        self.assertNotEqual(label, "MSG_KEY_THAT_DOES_NOT_EXIST")


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
        # Deve menzionare l'error in forma user-friendly (i18n
        # ERR_NO_VERIFIED_CHANNEL) o almeno il tool.
        fm = log.final_message.lower()
        self.assertTrue(
            "canale verificato" in fm
            or "no_verified_channel" in fm
            or "send_messages" in fm,
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

    def test_no_effect_receipt_survives_turn_log_honesty_guards(self):
        from agent_runtime import StepLog, TurnLog
        from engine.executor import _finalize_answer_text
        from engine.types import Framework, StepRun

        for hint in ("Risultano già chiusi sul dispositivo: Example.",
                     "Already closed on the device: Example."):
            with self.subTest(hint=hint):
                result = {"ok": True, "ok_count": 1, "fail_count": 0,
                          "results": [{"already_closed": True, "closed": True}],
                          "_undo": {"outcome": "no_effect"},
                          "final_message_hint": hint}
                step = StepRun(step_idx=1, tool="set_processes", args={},
                               result=result, ok=True, latency_ms=1)
                final = _finalize_answer_text(
                    Framework(steps=[], final_message="Ho chiuso Example."),
                    [step], "q", lambda *a, **k: self.fail("Unexpected LLM"))
                log = TurnLog(ts_start=0.0, ts_end=0.1, turn_id="t_no_effect",
                              user_query="q", intent_verb="set", final_kind="answer",
                              final_message=final, steps=[StepLog(
                                  step_num=1, chosen_tool="set_processes",
                                  result=result)])
                log.write()
                self.assertEqual(log.final_message, hint)
                self.assertEqual(log.effect_counts["mutations"], 0)
                self.assertFalse(log.false_success_detected)

    def test_successful_admin_receipt_is_authoritative_for_admin_only_turn(self):
        from agent_runtime import StepLog, TurnLog, _finalize_engine_result

        log = TurnLog(
            ts_start=0.0, turn_id="t_admin_receipt",
            user_query="fai ping a 192.0.2.10",
        )
        step = StepLog(step_num=1, chosen_tool="admin", raw_args={})
        step.result = {
            "ok": True,
            "decision": "execute_silent",
            "summary": "Eseguito ping: 4 ricevuti, 0% persi.",
        }

        _finalize_engine_result(
            log,
            {
                "steps": [step],
                "match_source": "engine",
                "final_kind": "answer",
                "final_text": "Operazione completata.",
                "verb": "run",
                "error_class": "",
            },
            actor="host", channel="http",
            conversation_id="", turn_id=log.turn_id,
        )

        self.assertEqual(
            log.final_message, "Eseguito ping: 4 ricevuti, 0% persi.",
        )

    def test_mutation_receipt_appends_missing_human_target_identity(self):
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(
            ts_start=0.0, turn_id="t_mutation_receipt",
            user_query="create and remove one event",
            final_kind="answer",
            final_message="Evento creato e successivamente rimosso.",
        )
        created = StepLog(
            step_num=1, chosen_tool="create_events", raw_args={})
        created.result = {
            "ok": True, "n_created": 1,
            "results": [{
                "ok": True, "id": "opaque-event-id",
                "summary": "Quarterly review",
            }],
        }
        deleted = StepLog(
            step_num=2, chosen_tool="delete_events", raw_args={})
        deleted.result = {
            "ok": True, "n_deleted": 1,
            "results": [{
                "ok": True, "id": "opaque-event-id", "status": "deleted",
            }],
        }
        log.steps.extend((created, deleted))
        log.ts_end = 0.1

        log.write()

        self.assertIn("Quarterly review", log.final_message)
        self.assertNotIn("opaque-event-id", log.final_message)

    def test_mutation_receipt_does_not_duplicate_visible_identity(self):
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(
            ts_start=0.0, turn_id="t_mutation_receipt_existing",
            user_query="create a file", final_kind="answer",
            final_message="Creato /tmp/report.txt.",
        )
        step = StepLog(
            step_num=1, chosen_tool="write_files", raw_args={})
        step.result = {
            "ok": True, "n_written": 1,
            "results": [{"ok": True, "path": "/tmp/report.txt"}],
        }
        log.steps.append(step)
        log.ts_end = 0.1

        log.write()

        self.assertEqual(log.final_message, "Creato /tmp/report.txt.")

    def test_applicable_authoritative_fragments_replace_llm_prose(self):
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(
            ts_start=0.0,
            turn_id="t_authoritative",
            user_query="conta file e directory e trova i duplicati",
            final_kind="answer",
            final_message="500 file forse simili per dimensione",
        )
        for number, tool, result in (
            (1, "generic_listing", {
                "ok": True,
                "entries": [{"path": "a"}],
                "truncated": True,
                "used": 1,
                "available_total": 10,
                "authoritative_presentation": {
                    "scope": "count",
                    "text": "Conteggio completo: 10.",
                    "covers_truncation": True,
                },
            }),
            (2, "generic_exact_comparison", {
                "ok": True,
                "entries": [{"path": "b", "duplicate_of": "a"}],
                "authoritative_presentation": {
                    "scope": "always",
                    "text": "Duplicati esatti: 1.",
                    "covers_truncation": True,
                },
            }),
        ):
            step = StepLog(step_num=number, chosen_tool=tool, raw_args={})
            step.result = result
            log.steps.append(step)
        log.ts_end = 0.1

        log.write()

        self.assertEqual(
            log.final_message,
            "Conteggio completo: 10.\n\nDuplicati esatti: 1.",
        )

    def test_authoritative_fragment_fills_an_empty_final(self):
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(
            ts_start=0.0,
            turn_id="t_authoritative_empty",
            user_query="conta tutti i file",
            final_kind="answer",
            final_message="",
        )
        log.intent_verb = "compute"
        step = StepLog(
            step_num=1, chosen_tool="generic_listing", raw_args={})
        step.result = {
            "ok": True,
            "truncated": True,
            "used": 1000,
            "available_total": 34060,
            "authoritative_presentation": {
                "scope": "count",
                "text": "Conteggio completo: 34060; elenco limitato a 1000.",
                "covers_truncation": True,
            },
        }
        log.steps.append(step)
        log.ts_end = 0.1

        log.write()

        self.assertEqual(
            log.final_message,
            "Conteggio completo: 34060; elenco limitato a 1000.",
        )

    def test_semantic_scope_does_not_hijack_non_count_listing(self):
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(
            ts_start=0.0,
            turn_id="t_non_count",
            user_query="elenca i file della cartella",
            final_kind="answer",
            final_message="- a\n- b",
        )
        step = StepLog(step_num=1, chosen_tool="generic_listing", raw_args={})
        step.result = {
            "ok": True,
            "entries": [{"path": "a"}, {"path": "b"}],
            "authoritative_presentation": {
                "scope": "count",
                "text": "Conteggio completo: 2.",
                "covers_truncation": True,
            },
        }
        log.steps.append(step)
        log.ts_end = 0.1

        log.write()

        self.assertEqual(log.final_message, "- a\n- b")

    def test_mixed_uncovered_plan_keeps_normal_finalizer(self):
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(
            ts_start=0.0,
            turn_id="t_mixed",
            user_query="trova duplicati e consulta anche il calendario",
            final_kind="answer",
            final_message="Risposta composta originale.",
        )
        covered = StepLog(
            step_num=1, chosen_tool="generic_exact_comparison", raw_args={})
        covered.result = {
            "ok": True,
            "entries": [{"path": "b"}],
            "authoritative_presentation": {
                "scope": "always", "text": "Duplicati esatti: 1."},
        }
        uncovered = StepLog(
            step_num=2, chosen_tool="generic_calendar_read", raw_args={})
        uncovered.result = {"ok": True, "entries": [{"title": "Evento"}]}
        log.steps.extend((covered, uncovered))
        log.ts_end = 0.1

        log.write()

        self.assertEqual(log.final_message, "Risposta composta originale.")

    def test_synthetic_final_does_not_hide_uncovered_display_limit(self):
        from agent_runtime import TurnLog, StepLog
        log = TurnLog(
            ts_start=0.0,
            turn_id="t_mixed_truncation",
            user_query="conta tutto e trova i duplicati",
            final_kind="answer",
            final_message="Sintesi composta.",
        )
        log.intent_verb = "compute"
        covered = StepLog(
            step_num=1, chosen_tool="generic_exact_comparison", raw_args={})
        covered.result = {
            "ok": True,
            "truncated": True,
            "truncated_what": "duplicati",
            "used": 500,
            "available_total": 3084,
            "entries": [{"path": "b"}],
            "authoritative_presentation": {
                "scope": "always",
                "text": "Scansione completa; elenco limitato.",
                "covers_truncation": True,
            },
        }
        uncovered = StepLog(
            step_num=2, chosen_tool="generic_processor", raw_args={})
        uncovered.result = {"ok": True, "entries": [{"value": 1}]}
        final = StepLog(
            step_num=3, chosen_tool="final_answer", raw_args={})
        final.result = {"ok": True}
        log.steps.extend((covered, uncovered, final))
        log.ts_end = 0.1

        log.write()

        self.assertIn("3084", log.final_message)
        self.assertIn("500", log.final_message)
        self.assertIn("Sintesi composta.", log.final_message)

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
        # i18n humanize: "no_verified_channel" → "canale verificato"
        fm = log.final_message.lower()
        self.assertTrue(
            "canale verificato" in fm or "no_verified_channel" in fm,
            f"error_class non riportato: {log.final_message!r}",
        )

    def test_error_class_translated_via_i18n(self):
        """error_class technical viene tradotto via i18n ERR_<UPPER> in
        forma user-friendly (es. no_verified_channel → ITA 'canale
        verificato')."""
        log = self._make_turn_with_failed_step(
            "send_messages",
            {"ok": False,
             "failed": [{"error": "no_verified_channel"}]},
        )
        log.write()
        self.assertIn("canale verificato", log.final_message.lower())
        # raw class non deve apparire (humanize ha sostituito)
        self.assertNotIn("no_verified_channel", log.final_message)

    def test_unknown_error_class_falls_back_to_raw(self):
        """error_class senza i18n ERR_<UPPER> resta in forma raw (no crash,
        no <missing:>)."""
        log = self._make_turn_with_failed_step(
            "compute_xyz",
            {"ok": False, "error": "boom_unknown_class_xyz_123"},
        )
        log.write()
        self.assertIn("boom_unknown_class_xyz_123", log.final_message)
        self.assertNotIn("<missing:", log.final_message)

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


class TestCapSameExecutorMessage(unittest.TestCase):
    """final_message di `cap_same_executor` deve essere user-facing,
    non «(stop: 'tool' chiamato N volte)» criptico (§7.3, judge-friendly).
    """

    def test_cap_same_message_is_user_facing(self):
        """Smoke i18n: MSG_CAP_SAME_EXECUTOR esiste e formatta correttamente."""
        from messages import get as _msg
        out = _msg("MSG_CAP_SAME_EXECUTOR", tool="find_x", n=2)
        self.assertNotIn("<missing:", out)
        self.assertIn("find_x", out)
        self.assertIn("2", out)
        # Non deve iniziare con "(stop:" — quel pattern legacy era criptico.
        self.assertFalse(out.startswith("(stop:"))


class TestComposeFromObsZeroWithErrors(unittest.TestCase):
    """`_compose_final_message_from_obs`: ok_count=0 + errors!=[] → fail message,
    non «completato (0 elementi)» disonesto (§2.8, turn live 2a5f2711)."""

    def test_zero_entries_with_errors_emits_error_msg(self):
        from agent_runtime import _compose_final_message_from_obs
        obs = {
            "ok": True, "ok_count": 0,
            "entries": [],
            "errors": [{"source": "/local/path.jpg",
                         "error": "File is not a valid URL.",
                         "error_class": "invalid_url"}],
        }
        msg, ok_count, _ = _compose_final_message_from_obs(
            "find_images_web", obs)
        self.assertNotIn("completato", msg.lower(),
                          f"0 elementi + errors NON e' completato: {msg!r}")
        self.assertIn("find_images_web", msg)
        # L'errore reale deve apparire
        self.assertTrue("valid url" in msg.lower() or "invalid_url" in msg,
                        f"errore reale deve apparire: {msg!r}")

    def test_zero_entries_no_errors_is_completato(self):
        """Caso legitto: 0 entries + 0 errors = input vuoto, OK completato."""
        from agent_runtime import _compose_final_message_from_obs
        obs = {"ok": True, "ok_count": 0, "entries": [], "errors": []}
        msg, _, _ = _compose_final_message_from_obs("find_x", obs)
        # Qui "completato (0 elementi)" e' legittimo (nessun fail mascherato)
        self.assertIn("0", msg)

    def test_some_entries_falls_through(self):
        """ok_count>0 → non triggera detection, comportamento normale."""
        from agent_runtime import _compose_final_message_from_obs
        obs = {"ok": True, "ok_count": 2,
                "entries": [{"a": 1}, {"a": 2}],
                "errors": []}
        msg, _, _ = _compose_final_message_from_obs("find_x", obs)
        self.assertIn("2", msg)


if __name__ == "__main__":
    unittest.main()
