"""Tests per P3 (12/5/2026): suppress auto-final transformative quando
la user_query contiene congiunzioni multi-step.

Bug live turn b1d9c236 (12/5/2026 08:27): query «fissa appuntamento il
prossimo mercoledi mattina dopo le 9 per un ora se c'è posto e mandami
una email di conferma» → set_events ok → `_AUTO_FINAL_TRANSFORMATIVE`
ha chiuso il turno dopo step 1 → send_messages mai eseguito.

Fix: detection regex `_MULTISTEP_CONJUNCTIONS_RE` su user_query +
helper `_query_has_continuation()` → suppress auto-final transformative
se True. Determinismo §7.9.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Path setup
_RUNTIME = str(Path(__file__).resolve().parent.parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


class TestQueryHasContinuation(unittest.TestCase):
    """Unit test sul detection regex IT+EN."""

    def setUp(self):
        from agent_runtime import _query_has_continuation
        self.detect = _query_has_continuation

    # ── Positive cases (continuation detected) ────────────────────────
    def test_it_mandami_email(self):
        q = ("fissa appuntamento il prossimo mercoledi mattina dopo le 9 "
             "per un ora se c'è posto e mandami una email di conferma")
        self.assertTrue(self.detect(q))

    def test_it_e_poi(self):
        self.assertTrue(self.detect("crea evento e poi inviami il link"))

    def test_it_e_avvisami(self):
        self.assertTrue(self.detect(
            "prenota una riunione lunedi mattina se sono libero e avvisami"))

    def test_it_mandami_first_then_fissa(self):
        # Verbo d'azione in seconda parte: detection deve coglierla
        self.assertTrue(self.detect(
            "mandami l'email e fissa l'appuntamento"))

    def test_it_inoltre(self):
        self.assertTrue(self.detect(
            "scarica il file; inoltre, scrivimi un riassunto"))

    def test_en_and_send(self):
        self.assertTrue(self.detect(
            "book meeting friday and send confirmation"))

    def test_en_and_notify(self):
        self.assertTrue(self.detect(
            "schedule meeting tomorrow and notify the team"))

    def test_en_and_then(self):
        self.assertTrue(self.detect("create event and then email me"))

    def test_en_and_book(self):
        # Synonyms covered
        self.assertTrue(self.detect(
            "check schedule and book the slot"))

    # ── Negative cases (no continuation) ──────────────────────────────
    def test_alternative_o(self):
        # «fissa o mercoledi o giovedi» = alternativa, NON multi-step
        self.assertFalse(self.detect(
            "fissa appuntamento o mercoledi o giovedi alle 9"))

    def test_no_conjunction_mono_step(self):
        self.assertFalse(self.detect(
            "fissa appuntamento mercoledi 16 alle 9 per mezz'ora"))

    def test_noun_list_email_e_telefono(self):
        # «email e telefono di X»: e fra noun, NO action verbo dopo
        self.assertFalse(self.detect(
            "dimmi email e telefono di Mario"))

    def test_empty_string(self):
        self.assertFalse(self.detect(""))

    def test_none_input(self):
        self.assertFalse(self.detect(None))

    def test_non_string_input(self):
        self.assertFalse(self.detect(123))

    def test_no_conjunction_en(self):
        self.assertFalse(self.detect("book meeting friday at 3pm"))


class TestAutoFinalMultistepSkip(unittest.TestCase):
    """Integration: simula la decisione auto-final con continuation."""

    def test_auto_final_skipped_when_continuation(self):
        """Query con «e mandami email» → auto-final NON triggera."""
        from agent_runtime import (
            _query_has_continuation,
            _is_auto_final_transformative,
        )
        q = "fissa appuntamento mercoledi se c'è posto e mandami email"
        chosen_name = "create_events"
        obs = {
            "ok": True,
            "_undo": {"ids": ["evt_123"]},
            "results": [{"id": "evt_123", "htmlLink": "https://..."}],
        }
        # Simulazione della condizione runtime (post-fix):
        triggered = (
            _is_auto_final_transformative(chosen_name)
            and obs.get("ok") is True
            and isinstance(obs.get("_undo"), dict)
            and obs.get("_undo", {}).get("ids")
            and not _query_has_continuation(q)
        )
        self.assertFalse(triggered,
                         "auto-final transformative deve essere skipped "
                         "quando query ha continuation")

    def test_auto_final_kept_when_mono_step(self):
        """Query mono-step → auto-final TRIGGERA (preserva fix anti-loop)."""
        from agent_runtime import (
            _query_has_continuation,
            _is_auto_final_transformative,
        )
        q = "fissa appuntamento mercoledi 16 alle 9 per mezz'ora"
        chosen_name = "create_events"
        obs = {
            "ok": True,
            "_undo": {"ids": ["evt_456"]},
            "results": [{"id": "evt_456"}],
        }
        triggered = (
            _is_auto_final_transformative(chosen_name)
            and obs.get("ok") is True
            and isinstance(obs.get("_undo"), dict)
            and obs.get("_undo", {}).get("ids")
            and not _query_has_continuation(q)
        )
        self.assertTrue(triggered,
                        "auto-final deve triggerare se query mono-step "
                        "(preservare fix anti-loop 1e4a196)")

    def test_alternative_o_does_not_suppress(self):
        """«fissa o X o Y» = alternativa: NON multi-step, auto-final OK."""
        from agent_runtime import (
            _query_has_continuation,
            _is_auto_final_transformative,
        )
        q = "fissa appuntamento o mercoledi o giovedi alle 9"
        chosen_name = "create_events"
        obs = {
            "ok": True,
            "_undo": {"ids": ["evt_x"]},
            "results": [{"id": "evt_x"}],
        }
        triggered = (
            _is_auto_final_transformative(chosen_name)
            and obs.get("ok") is True
            and isinstance(obs.get("_undo"), dict)
            and obs.get("_undo", {}).get("ids")
            and not _query_has_continuation(q)
        )
        self.assertTrue(triggered,
                        "alternativa «o X o Y» non e' multi-step")


if __name__ == "__main__":
    unittest.main()
