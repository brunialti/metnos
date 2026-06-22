"""test_from_contains_resolver — §7.9: inietta from_contains=<NomeProprio> su
read_messages quando la query nomina il mittente ma l'LLM lo omette (bug live
22/6 «pagamenti Anthropic» → read broad → extract incompleto). Conservativo."""
from __future__ import annotations
import os, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from from_contains_resolver import resolve_from_contains  # noqa: E402


def _r(q, **args):
    a = dict(args)
    a.setdefault("via_channel", "email")
    return resolve_from_contains("read_messages", a, q).get("from_contains")


class TestFromContains(unittest.TestCase):
    def test_pagamenti_anthropic(self):
        self.assertEqual(_r("Cerca nelle mie email i pagamenti Anthropic dell ultimo anno"), "Anthropic")

    def test_da_anthropic(self):
        self.assertEqual(_r("le fatture da Anthropic delle mie mail"), "Anthropic")

    def test_vendor_enel(self):
        self.assertEqual(_r("le fatture di Enel del 2026"), "Enel")

    def test_ordini_amazon(self):
        self.assertEqual(_r("cerca gli ordini Amazon"), "Amazon")

    def test_persona(self):
        self.assertEqual(_r("email da Roberto"), "Roberto")

    def test_noop_verbo_minuscolo(self):
        self.assertIsNone(_r("le mail da inviare"))

    def test_noop_nessun_proprio(self):
        self.assertIsNone(_r("le mie email importanti"))

    def test_noop_giorno(self):
        self.assertIsNone(_r("mail da Lunedì scorso"))

    def test_noop_mese(self):
        self.assertIsNone(_r("le mail di Gennaio"))

    def test_noop_ambiguo(self):
        self.assertIsNone(_r("fatture da Anthropic e da Amazon"))

    def test_noop_minuscolo(self):
        self.assertIsNone(_r("cerca i pagamenti anthropic"))

    def test_noop_gia_settato(self):
        self.assertEqual(_r("fatture da Anthropic", from_contains="Stripe"), "Stripe")

    def test_noop_subject_settato(self):
        self.assertIsNone(_r("fatture da Anthropic", subject_contains="fattura"))

    def test_noop_non_read(self):
        out = resolve_from_contains("send_messages", {"via_channel": "email"}, "manda a Anthropic")
        self.assertIsNone(out.get("from_contains"))


if __name__ == "__main__":
    unittest.main()
