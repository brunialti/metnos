"""Test del parser tollerante di extract_entries (recall §2.8, deterministico §7.9).

Il bug "0 record da contenuto con date" era spesso un array JSON TRONCATO dal
token-cap: `json.loads` falliva sull'intero array → 0 record. Il parser ora
(a) scala il budget output col numero di record attesi e (b) salva i record
gia' completi anche da output troncato/malformato/shape-alternativo.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extract_entries import (_parse_records, _extract_max_tokens,
                             _salvage_objects, _build_prompt)

F = ["summary", "start", "end"]


class TestDateGranularity(unittest.TestCase):
    """Regression (Roberto 16/6): un campo DATA va come data (YYYY-MM-DD),
    NON datetime con T00:00 spurio; un campo DATA/ORA resta ISO con orario."""

    def test_date_only_field_no_time(self):
        p = _build_prompt(["data", "importo"], "", 20)
        self.assertIn("YYYY-MM-DD", p)
        self.assertIn("SENZA orario", p)
        # nessuna istruzione di datetime con orario per un campo pura-data
        self.assertNotIn("T09:00:00", p)

    def test_datetime_field_keeps_time(self):
        p = _build_prompt(["summary", "start", "end"], "", 20)
        self.assertIn("ISO 8601 con orario", p)
        self.assertIn("T00:00", p)

    def test_mixed_date_and_datetime(self):
        p = _build_prompt(["start", "scadenza"], "", 20)
        self.assertIn("ISO 8601 con orario", p)   # start → datetime
        self.assertIn("YYYY-MM-DD", p)            # scadenza → date-only


class TestParseRecords(unittest.TestCase):
    def test_wellformed_array(self):
        r = _parse_records('[{"summary":"A","start":"2026-01-01","end":""}]', F)
        self.assertEqual(r, [{"summary": "A", "start": "2026-01-01", "end": ""}])

    def test_truncated_array_salvages_complete(self):
        # Array tagliato a meta' dal token-cap: l'ultimo oggetto e' incompleto.
        # DEVE salvare i 2 completi invece di azzerare tutto.
        raw = ('[{"summary":"A","start":"x","end":"y"},'
               '{"summary":"B","start":"z","end":"w"},{"summary":"C","sta')
        r = _parse_records(raw, F)
        self.assertEqual(len(r), 2)
        self.assertEqual([x["summary"] for x in r], ["A", "B"])

    def test_wrapper_object(self):
        r = _parse_records('{"records":[{"summary":"A"},{"summary":"B"}]}', F)
        self.assertEqual([x["summary"] for x in r], ["A", "B"])

    def test_single_object(self):
        r = _parse_records('{"summary":"A","start":"t"}', F)
        self.assertEqual(r, [{"summary": "A", "start": "t"}])

    def test_fenced_json(self):
        self.assertEqual(_parse_records('```json\n[{"summary":"A"}]\n```', F),
                         [{"summary": "A"}])

    def test_prose_plus_array(self):
        self.assertEqual(_parse_records('Ecco:\n[{"summary":"A"}]\nfine', F),
                         [{"summary": "A"}])

    def test_garbage_returns_empty(self):
        self.assertEqual(_parse_records("nessun evento trovato", F), [])
        self.assertEqual(_parse_records("", F), [])

    def test_brace_inside_string_value(self):
        # Le parentesi dentro le stringhe NON devono rompere il bilanciamento.
        r = _parse_records('[{"summary":"a } b {","start":"x"}]', F)
        self.assertEqual(r, [{"summary": "a } b {", "start": "x"}])

    def test_salvage_filters_objects_without_fields(self):
        # Un oggetto top-level senza alcun field richiesto (es. wrapper spurio)
        # non viene tenuto dal recupero tollerante.
        raw = '{"meta":"x"} garbage {"summary":"A","start":"t","end":"u"} tail {'
        r = [o for o in _salvage_objects(raw) if any(f in o for f in F)]
        self.assertEqual(r, [{"summary": "A", "start": "t", "end": "u"}])


class TestMaxTokens(unittest.TestCase):
    def test_scaling_floor_and_cap(self):
        self.assertEqual(_extract_max_tokens(1), 1200)      # floor
        self.assertEqual(_extract_max_tokens(20), 2912)     # 512 + 20*120
        self.assertEqual(_extract_max_tokens(100), 8192)    # cap


if __name__ == "__main__":
    unittest.main()
