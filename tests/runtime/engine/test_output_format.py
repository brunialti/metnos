"""Test deterministici per `runtime/output_format.py` (ADR 0095).

Niente LLM, niente network. Solo pure funzioni di formatting.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_RUNTIME = str((Path(__file__).resolve().parents[3] / "runtime"))


class TestFormatKv(unittest.TestCase):
    def test_basic(self):
        from output_format import format_kv
        self.assertEqual(format_kv("Path", "/tmp/x.txt"),
                         "**Path**: /tmp/x.txt")

    def test_with_pct_unit_no_space(self):
        from output_format import format_kv
        self.assertEqual(format_kv("RAM", "38.4", "%"),
                         "**RAM**: 38.4%")

    def test_with_word_unit_with_space(self):
        from output_format import format_kv
        self.assertEqual(format_kv("Memoria", "47.7", "GB"),
                         "**Memoria**: 47.7 GB")

    def test_int_value(self):
        from output_format import format_kv
        self.assertEqual(format_kv("Conteggio", 42), "**Conteggio**: 42")


class TestFormatKvGroup(unittest.TestCase):
    def test_load_average_no_slash_ambiguity(self):
        from output_format import format_kv_group
        out = format_kv_group("Carico", [
            ("1m", 0.98, None),
            ("5m", 0.94, None),
            ("15m", 0.47, None),
        ])
        # Niente slash che confondono. Etichette esplicite.
        self.assertNotIn("/", out)
        self.assertIn("**Carico**", out)
        self.assertIn("• 1m: 0.98", out)
        self.assertIn("• 5m: 0.94", out)
        self.assertIn("• 15m: 0.47", out)

    def test_pct_no_space(self):
        from output_format import format_kv_group
        out = format_kv_group(None, [("Used", 38.4, "%")])
        self.assertIn("• Used: 38.4%", out)

    def test_no_title(self):
        from output_format import format_kv_group
        out = format_kv_group(None, [("k", "v", None)])
        self.assertEqual(out, "  • k: v")


class TestFormatList(unittest.TestCase):
    def test_basic(self):
        from output_format import format_list
        out = format_list("File trovati", ["a.py", "b.py"])
        self.assertIn("**File trovati**", out)
        self.assertIn("• a.py", out)
        self.assertIn("• b.py", out)

    def test_cap_with_omission_notice(self):
        from output_format import format_list
        out = format_list("X", ["a", "b", "c", "d", "e"], cap=3)
        self.assertIn("• a", out)
        self.assertIn("• c", out)
        self.assertNotIn("• d", out)
        self.assertIn("(altri 2 omessi)", out)

    def test_no_title(self):
        from output_format import format_list
        out = format_list(None, ["x", "y"])
        self.assertNotIn("**", out)
        self.assertIn("• x", out)


class TestFormatTable(unittest.TestCase):
    def test_processes_table(self):
        from output_format import format_table
        out = format_table(
            ["Processo", "CPU%", "MEM%"],
            [["python", "100.0", "0.0"], ["llama-server", "3.8", "11.1"]],
            align=["left", "right", "right"],
        )
        self.assertIn("| Processo | CPU% | MEM% |", out)
        self.assertIn("| --- | ---: | ---: |", out)
        self.assertIn("| python | 100.0 | 0.0 |", out)
        self.assertIn("| llama-server | 3.8 | 11.1 |", out)

    def test_with_title(self):
        from output_format import format_table
        out = format_table(["A", "B"], [["1", "2"]], title="Coppie")
        self.assertIn("**Coppie**", out)
        self.assertTrue(out.startswith("**Coppie**"))

    def test_pad_short_rows(self):
        from output_format import format_table
        out = format_table(["A", "B", "C"], [["1"]])
        # Riga "1" → "1 |  |  |"
        self.assertIn("| 1 |  |  |", out)


class TestFormatSection(unittest.TestCase):
    def test_basic(self):
        from output_format import format_section
        out = format_section("Riassunto", "Tutto verde.")
        self.assertEqual(out, "### Riassunto\n\nTutto verde.")

    def test_empty_body(self):
        from output_format import format_section
        self.assertEqual(format_section("Titolo", ""), "### Titolo")


class TestFormatTldr(unittest.TestCase):
    def test_basic(self):
        from output_format import format_tldr
        out = format_tldr("3 servizi su 4 attivi")
        self.assertEqual(out, "_Riepilogo: 3 servizi su 4 attivi_")


class TestFormatOffer(unittest.TestCase):
    def test_offer_block_separated(self):
        from output_format import format_offer
        out = format_offer("Allargamento risultato",
                           "Hai chiesto 10. In totale ce ne sono 493. Allargo a 1000?")
        self.assertIn("---", out)
        self.assertIn("### Allargamento risultato", out)
        self.assertIn("Allargo a 1000?", out)


class TestSearchResultsHtmlStrip(unittest.TestCase):
    """Bug 'azione schedulata invia messaggio errato': uno snippet con HTML
    grezzo (fetch interrotto) non deve trapelare nel blocco risultati."""

    def test_raw_html_snippet_stripped(self):
        from output_format import format_search_results
        entries = [{
            "url": "https://rocm.docs.amd.com/versions.html",
            "title": "ROCm versions",
            "score": 0.8,
            "snippet": ('<!DOCTYPE html> <html lang="en"><head>'
                        "<title>ROCm</title></head><body>x</body></html>"),
        }]
        out = format_search_results(entries, query="versione AMD ROCm")
        self.assertNotIn("<!DOCTYPE", out)
        self.assertNotIn("<html", out)
        self.assertIn("rocm.docs.amd.com", out)  # il link resta

    def test_clean_snippet_preserved(self):
        from output_format import format_search_results
        entries = [{
            "url": "https://x/y",
            "title": "Titolo",
            "score": 0.9,
            "snippet": "ROCm 6.2 rilasciata a giugno.",
        }]
        out = format_search_results(entries, query="rocm")
        self.assertIn("ROCm 6.2", out)


if __name__ == "__main__":
    unittest.main()
