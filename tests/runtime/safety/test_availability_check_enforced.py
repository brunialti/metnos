"""Tests per P4 (12/5/2026): runtime enforcement availability check
pre-set_events quando la query contiene marker di disponibilita'.

Bug live turn b1d9c236 (12/5/2026 08:27): query «fissa appuntamento il
prossimo mercoledi mattina dopo le 9 per un ora SE C'È POSTO ...» →
planner ha chiamato set_events direttamente bypassando il workflow
(check_availability) di `calendar.j2`. Roberto aveva impegno tutto il
giorno: evento creato lo stesso, overlap.

Fix: regex `_AVAILABILITY_MARKERS_RE` su user_query + helper
`_query_requires_availability_check()` + check `_has_prior_read_events_ok()`
nel runtime. Gate scatta PRIMA di validazione/vaglio/invoke_executor.
Determinismo §7.9.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

# Path setup
_RUNTIME = str((Path(__file__).resolve().parents[3] / "runtime"))


class TestAvailabilityMarkerDetection(unittest.TestCase):
    """Unit test sul detection regex IT+EN."""

    def setUp(self):
        from agent_runtime import _query_requires_availability_check
        self.detect = _query_requires_availability_check

    # ── Positive cases IT ─────────────────────────────────────────────
    def test_se_ce_posto(self):
        self.assertTrue(self.detect(
            "fissa appuntamento mercoledi se c'è posto"))

    def test_se_ce_un_buco(self):
        # Marker ambiguo ma comune
        self.assertTrue(self.detect(
            "se c'è un buco mercoledi mattina"))

    def test_se_sono_libero(self):
        self.assertTrue(self.detect(
            "prenota una riunione lunedi mattina se sono libero"))

    def test_se_non_ho_altro(self):
        self.assertTrue(self.detect(
            "fissa appuntamento mercoledi alle 9 se non ho altro"))

    def test_verifica_disponibilita(self):
        self.assertTrue(self.detect(
            "verifica disponibilità venerdi e fissa"))

    def test_finestra_libera(self):
        self.assertTrue(self.detect(
            "prenota se la finestra e' libera"))

    # ── Positive cases EN ─────────────────────────────────────────────
    def test_en_if_available(self):
        self.assertTrue(self.detect(
            "book meeting friday if available"))

    def test_en_if_free(self):
        self.assertTrue(self.detect(
            "schedule call if I'm free"))

    def test_en_if_theres_a_slot(self):
        self.assertTrue(self.detect(
            "book if there's a slot tomorrow"))

    def test_en_check_availability(self):
        self.assertTrue(self.detect(
            "check availability and book the slot"))

    # ── Negative cases ────────────────────────────────────────────────
    def test_no_marker_mono(self):
        self.assertFalse(self.detect(
            "fissa appuntamento giovedi alle 10"))

    def test_no_marker_en_mono(self):
        self.assertFalse(self.detect(
            "book meeting friday at 3pm"))

    def test_empty(self):
        self.assertFalse(self.detect(""))

    def test_none(self):
        self.assertFalse(self.detect(None))

    def test_alternative_no_availability(self):
        # «fissa o mercoledi o giovedi» = no availability marker
        self.assertFalse(self.detect(
            "fissa o mercoledi o giovedi alle 9"))


class TestHasPriorReadEventsOk(unittest.TestCase):
    """Test sul tracking dei step precedenti."""

    def _step(self, tool, ok=True):
        return SimpleNamespace(chosen_tool=tool,
                                result={"ok": ok, "entries": []})

    def test_empty_steps(self):
        from agent_runtime import _has_prior_read_events_ok
        self.assertFalse(_has_prior_read_events_ok([]))

    def test_read_events_ok_present(self):
        from agent_runtime import _has_prior_read_events_ok
        steps = [
            self._step("get_now"),
            self._step("read_events"),
        ]
        self.assertTrue(_has_prior_read_events_ok(steps))

    def test_read_events_failed_does_not_count(self):
        # read_events ok=False non e' una verifica valida
        from agent_runtime import _has_prior_read_events_ok
        steps = [
            self._step("read_events", ok=False),
        ]
        self.assertFalse(_has_prior_read_events_ok(steps))

    def test_other_tools_dont_count(self):
        from agent_runtime import _has_prior_read_events_ok
        steps = [
            self._step("get_now"),
            self._step("read_messages"),
        ]
        self.assertFalse(_has_prior_read_events_ok(steps))

    def test_set_events_alone_does_not_count(self):
        # set_events nel passato NON conta come availability check
        from agent_runtime import _has_prior_read_events_ok
        steps = [
            self._step("set_events"),
        ]
        self.assertFalse(_has_prior_read_events_ok(steps))


class TestAvailabilityGateLogic(unittest.TestCase):
    """Test della logica composita del gate (simulazione runtime)."""

    def test_gate_triggers_set_events_no_prior_read(self):
        """Query con marker + set_events + no read_events → gate triggers."""
        from agent_runtime import (
            _query_requires_availability_check,
            _has_prior_read_events_ok,
            _calendar_write_tools,
        )
        q = "fissa appuntamento mercoledi se c'è posto"
        chosen_name = "create_events"  # post ADR 0128 (era set_events)
        prev_steps = [
            SimpleNamespace(chosen_tool="get_now",
                             result={"ok": True}),
        ]
        gate_triggered = (
            chosen_name in _calendar_write_tools()
            and _query_requires_availability_check(q)
            and not _has_prior_read_events_ok(prev_steps)
        )
        self.assertTrue(gate_triggered)

    def test_gate_skipped_when_read_events_done(self):
        """Query con marker + set_events + read_events fatto → gate NO trigger."""
        from agent_runtime import (
            _query_requires_availability_check,
            _has_prior_read_events_ok,
            _calendar_write_tools,
        )
        q = "fissa appuntamento mercoledi se c'è posto"
        chosen_name = "create_events"  # post ADR 0128 (era set_events)
        prev_steps = [
            SimpleNamespace(chosen_tool="get_now",
                             result={"ok": True}),
            SimpleNamespace(chosen_tool="read_events",
                             result={"ok": True, "entries": []}),
        ]
        gate_triggered = (
            chosen_name in _calendar_write_tools()
            and _query_requires_availability_check(q)
            and not _has_prior_read_events_ok(prev_steps)
        )
        self.assertFalse(gate_triggered)

    def test_gate_skipped_when_no_marker(self):
        """Query senza marker + set_events → gate NO trigger."""
        from agent_runtime import (
            _query_requires_availability_check,
            _has_prior_read_events_ok,
            _calendar_write_tools,
        )
        q = "fissa appuntamento mercoledi alle 9 per mezz'ora"
        chosen_name = "create_events"  # post ADR 0128 (era set_events)
        prev_steps = []
        gate_triggered = (
            chosen_name in _calendar_write_tools()
            and _query_requires_availability_check(q)
            and not _has_prior_read_events_ok(prev_steps)
        )
        self.assertFalse(gate_triggered)

    def test_gate_skipped_for_non_calendar_write(self):
        """Tool non-calendar (es. send_messages) NON e' gated da P4."""
        from agent_runtime import (
            _query_requires_availability_check,
            _has_prior_read_events_ok,
            _calendar_write_tools,
        )
        q = "fissa appuntamento mercoledi se c'è posto e mandami email"
        chosen_name = "send_messages"
        prev_steps = []
        gate_triggered = (
            chosen_name in _calendar_write_tools()
            and _query_requires_availability_check(q)
            and not _has_prior_read_events_ok(prev_steps)
        )
        self.assertFalse(gate_triggered)


if __name__ == "__main__":
    unittest.main()
