"""test_time_window_resolver_units — il resolver deterministico ESTRAE la finestra
NL (settimane/mesi/anni inclusi) dalla query e DE-CONFLA il numero temporale dagli
arg di conteggio. Bug live 21/6: «ultimi 12 mesi» → l'LLM legava 12 a max_results.
"""
from __future__ import annotations
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import time_window_resolver as twr  # noqa: E402

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

    def test_mutating_verb_noop(self):
        out = twr.resolve_time_window(
            "delete_messages", {"max_results": 12}, "ultimi 12 mesi", _SCHEMA)
        self.assertEqual(out.get("max_results"), 12)  # delete → mai iniettare


if __name__ == "__main__":
    unittest.main()
