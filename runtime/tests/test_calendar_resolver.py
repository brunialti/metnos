"""Test calendar_resolver: targeting NL owned + default primary (no rete)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calendar_resolver as cr

_OWNED = [
    {"id": "primary", "summary": "Roberto Brunialti", "primary": True},
    {"id": "lavoro@group.calendar.google.com", "summary": "Lavoro", "primary": False},
    {"id": "metnos@group.calendar.google.com", "summary": "metnos", "primary": False},
]


def _patch_owned():
    return mock.patch.object(cr, "_owned_calendars", lambda: _OWNED)


class TestCalendarResolver(unittest.TestCase):
    def test_noop_non_event_tool(self):
        a = {"x": 1}
        self.assertIs(cr.resolve_calendar("send_messages", a, "q"), a)

    def test_noop_non_google_client(self):
        a = {"client": "local"}
        self.assertIs(cr.resolve_calendar("create_events", a, "evento nel calendario lavoro"), a)

    def test_read_default_primary_no_calword(self):
        out = cr.resolve_calendar("read_events", {}, "i miei appuntamenti di domani")
        self.assertEqual(out["calendar_id"], "primary")

    def test_create_default_untouched_no_calword(self):
        # create: nessun calendar_id forzato (backend default primary)
        out = cr.resolve_calendar("create_events", {"summary": "x"}, "crea standup domani")
        self.assertNotIn("calendar_id", out)

    def test_read_all_explicit(self):
        with _patch_owned():
            out = cr.resolve_calendar("read_events", {}, "mostrami tutti i calendari di domani")
        self.assertEqual(out["calendar_id"], "all")

    def test_target_owned_by_name_create(self):
        with _patch_owned():
            out = cr.resolve_calendar(
                "create_events", {"summary": "riunione"},
                "fissa una riunione nel calendario lavoro alle 10")
        self.assertEqual(out["calendar_id"], "lavoro@group.calendar.google.com")

    def test_target_owned_by_name_read(self):
        with _patch_owned():
            out = cr.resolve_calendar(
                "read_events", {}, "leggi gli eventi del calendario metnos")
        self.assertEqual(out["calendar_id"], "metnos@group.calendar.google.com")

    def test_calword_but_no_owned_match_read_primary(self):
        with _patch_owned():
            out = cr.resolve_calendar(
                "read_events", {}, "eventi nel calendario inesistente")
        self.assertEqual(out["calendar_id"], "primary")

    def test_explicit_real_id_respected(self):
        a = {"calendar_id": "xyz@group.calendar.google.com"}
        out = cr.resolve_calendar("create_events", a, "nel calendario lavoro")
        self.assertEqual(out["calendar_id"], "xyz@group.calendar.google.com")

    def test_bare_name_calendar_id_resolved_to_owned(self):
        # LLM emette calendar_id="lavoro" (nome, non id) → risolto fra gli owned
        with _patch_owned():
            out = cr.resolve_calendar("create_events",
                                       {"calendar_id": "Lavoro"}, "crea evento")
        self.assertEqual(out["calendar_id"], "lavoro@group.calendar.google.com")

    def test_bare_name_unresolved_dropped_for_create(self):
        # nome che non è un owned → rimosso (backend default primary)
        with _patch_owned():
            out = cr.resolve_calendar("create_events",
                                       {"calendar_id": "fantasma"}, "crea evento")
        self.assertNotIn("calendar_id", out)

    def test_primary_alias_not_treated_as_explicit(self):
        with _patch_owned():
            out = cr.resolve_calendar(
                "create_events", {"calendar_id": "primary"},
                "nel calendario lavoro alle 9")
        self.assertEqual(out["calendar_id"], "lavoro@group.calendar.google.com")


if __name__ == "__main__":
    unittest.main()
