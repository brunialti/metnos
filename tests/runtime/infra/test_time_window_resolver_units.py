"""test_time_window_resolver_units — il resolver deterministico ESTRAE la finestra
NL (settimane/mesi/anni inclusi) dalla query e DE-CONFLA il numero temporale dagli
arg di conteggio. Bug live 21/6: «ultimi 12 mesi» → l'LLM legava 12 a max_results.
"""
from __future__ import annotations
import sys, unittest
from pathlib import Path
import time_window_resolver as twr  # noqa: E402
from unittest import mock

_SCHEMA = {"properties": {"time_window": {}, "max_results": {}, "max_total": {}}}


class TestUnits(unittest.TestCase):
    def test_parse_months_weeks_years(self):
        self.assertEqual(twr.parse_query_time_window("ultimi 12 mesi"), "last-12m")
        self.assertEqual(twr.parse_query_time_window("last 2 weeks"), "last-2w")
        self.assertEqual(twr.parse_query_time_window("degli ultimi 3 anni"), "last-3y")
        self.assertEqual(twr.parse_query_time_window("12 mesi fa"), "last-12m")

    def test_days_hours_still_work(self):
        self.assertEqual(twr.parse_query_time_window("ultime 24 ore"), "last-24h")
        self.assertEqual(twr.parse_query_time_window("ultimi 7 giorni"), "last-7d")

    def test_no_window_no_spurious(self):
        self.assertIsNone(twr.parse_query_time_window("le fatture di Anthropic"))

    def test_deconflation_removes_count_equal_to_temporal_n(self):
        out = twr.resolve_time_window(
            "read_messages", {"max_results": 12, "max_total": 12, "account": "all"},
            "le fatture Anthropic degli ultimi 12 mesi", _SCHEMA)
        self.assertEqual(out.get("time_window"), "last-12m")
        self.assertNotIn("max_results", out)
        self.assertNotIn("max_total", out)
        self.assertEqual(out.get("account"), "all")

    def test_legit_count_not_removed(self):
        out = twr.resolve_time_window(
            "read_messages", {"max_results": 50}, "ultimi 3 giorni", _SCHEMA)
        self.assertEqual(out.get("max_results"), 50)
        self.assertEqual(out.get("time_window"), "last-3d")

    def test_schema_gated_noop_without_time_window_arg(self):
        out = twr.resolve_time_window(
            "read_messages", {"max_results": 12}, "ultimi 12 mesi",
            {"properties": {"max_results": {}}})
        self.assertEqual(out.get("max_results"), 12)  # no time_window arg → noop

    def test_filter_entries_materialized_files_get_mtime_bounds(self):
        schema = {"properties": {"entries": {}, "mtime_after": {},
                                 "mtime_before": {}}}
        args = {"entries": [{"path": "/tmp/a.pdf", "mtime": 123.0}],
                "name_regex": ".*"}
        with mock.patch("time_window_parser.parse_time_window",
                        return_value=("2026-05-21T10:00:00+02:00",
                                      "2026-07-20T10:00:00+02:00")):
            out = twr.resolve_time_window(
                "filter_entries", args, "file modificati negli ultimi 60 giorni",
                schema)
        self.assertEqual(out["mtime_after"], "2026-05-21T10:00:00+02:00")
        self.assertEqual(out["mtime_before"], "2026-07-20T10:00:00+02:00")
        self.assertEqual(out["name_regex"], ".*")

    def test_filter_entries_non_files_never_get_mtime_bounds(self):
        schema = {"properties": {"entries": {}, "mtime_after": {},
                                 "mtime_before": {}}}
        args = {"entries": [{"subject": "mail", "received_at": "2026-07-20"}]}
        out = twr.resolve_time_window(
            "filter_entries", args, "mail degli ultimi 60 giorni", schema)
        self.assertEqual(out, args)

    def test_filter_entries_unmaterialized_plan_is_noop(self):
        schema = {"properties": {"entries": {}, "mtime_after": {},
                                 "mtime_before": {}}}
        args = {"from_step": 1}
        out = twr.resolve_time_window(
            "filter_entries", args, "file degli ultimi 60 giorni", schema)
        self.assertEqual(out, args)

    def test_mutating_verb_noop(self):
        out = twr.resolve_time_window(
            "delete_messages", {"max_results": 12}, "ultimi 12 mesi", _SCHEMA)
        self.assertEqual(out.get("max_results"), 12)  # delete → mai iniettare


if __name__ == "__main__":
    unittest.main()
