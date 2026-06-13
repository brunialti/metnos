"""Test del guard «notifica in-piano soppressa nei run schedulati a vuoto»
(13/6/2026, bug live: maintenance github → send_messages su 0 issue aperte).

`treated_issues_guard.suppress_scheduled_notify(tool, prior_steps)`:
- solo turno SCHEDULATO (scheduled_turn_scope);
- solo tool outbound (send_*);
- solo se gli step a monte sono no-op (counts_indicate_noop).
Pure-send senza upstream contabile (promemoria) → NON soppresso.

Unit puro, niente LLM né invii reali (§7.9 deterministico).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from treated_issues_guard import (  # noqa: E402
    suppress_scheduled_notify, scheduled_turn_scope,
)
from pipeline_effects import counts_indicate_noop, pipeline_effect_counts  # noqa: E402


def _step(tool, result):
    return {"tool": tool, "result": result}


# La pipeline github a vuoto: find 0 issue + write 0.
EMPTY_GITHUB = [
    _step("find_issues", {"ok": True, "ok_count": 0}),
    _step("write_issues", {"ok": True, "ok_count": 0, "results": []}),
]
# Pipeline con lavoro reale: 3 issue trovate.
NONEMPTY = [
    _step("find_issues", {"ok": True, "ok_count": 3,
                          "entries": [{"n": 1}, {"n": 2}, {"n": 3}]}),
]


class TestScheduledNotifyGuard(unittest.TestCase):

    def test_scheduled_empty_send_suppressed(self):
        with scheduled_turn_scope():
            self.assertTrue(
                suppress_scheduled_notify("send_messages", EMPTY_GITHUB))

    def test_scheduled_nonempty_send_NOT_suppressed(self):
        with scheduled_turn_scope():
            self.assertFalse(
                suppress_scheduled_notify("send_messages", NONEMPTY))

    def test_interactive_never_suppressed(self):
        # Fuori da scheduled_turn_scope: turno interattivo → mai soppresso.
        self.assertFalse(
            suppress_scheduled_notify("send_messages", EMPTY_GITHUB))

    def test_pure_send_reminder_NOT_suppressed(self):
        # Promemoria/heartbeat: il send È il deliverable, nessun upstream
        # contabile → counts None → NON soppresso.
        with scheduled_turn_scope():
            self.assertFalse(suppress_scheduled_notify("send_messages", []))

    def test_non_send_tool_not_gated(self):
        # Questo guard riguarda SOLO i send_*; un delete a vuoto è gestito
        # altrove (criterio efficacia fastpath), non qui.
        with scheduled_turn_scope():
            self.assertFalse(
                suppress_scheduled_notify("delete_files", EMPTY_GITHUB))

    def test_failures_upstream_NOT_suppressed(self):
        # Se a monte c'è un errore, la notifica NON va soppressa (§2.8).
        steps = [_step("find_issues", {"ok": False, "error": "boom"})]
        with scheduled_turn_scope():
            self.assertFalse(suppress_scheduled_notify("send_messages", steps))

    def test_counts_predicate_units(self):
        # counts None → non a vuoto; failures → non a vuoto; mutante 0 → a
        # vuoto; solo-lettura 0 items → a vuoto.
        self.assertFalse(counts_indicate_noop(None))
        self.assertFalse(counts_indicate_noop({"failures": 1, "countable": 1}))
        self.assertTrue(counts_indicate_noop(
            {"mutating_attempted": True, "mutations": 0, "countable": 1}))
        self.assertFalse(counts_indicate_noop(
            {"mutating_attempted": True, "mutations": 2, "countable": 1}))
        self.assertTrue(counts_indicate_noop(
            {"countable": 1, "items": 0, "mutations": 0}))
        self.assertFalse(counts_indicate_noop(
            {"countable": 1, "items": 5, "mutations": 0}))

    def test_scope_resets(self):
        # Dopo lo scope, la flag si resetta (worker thread riusati).
        with scheduled_turn_scope():
            pass
        self.assertFalse(
            suppress_scheduled_notify("send_messages", EMPTY_GITHUB))


if __name__ == "__main__":
    unittest.main()
