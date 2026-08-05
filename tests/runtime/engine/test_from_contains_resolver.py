"""test_from_contains_resolver — §7.9: inietta from_contains=<NomeProprio> su
read_messages quando la query nomina il mittente ma l'LLM lo omette (bug live
22/6 «pagamenti Anthropic» → read broad → extract incompleto). Conservativo."""
from __future__ import annotations
import os, sys, unittest
from pathlib import Path
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

    # --- recupero autoreferenza (fix bug live 3/7) -------------------------
    # L'LLM a volte ripete la parola-categoria stessa come from_contains
    # ("bollette plenitude ed enel" -> from_contains="bollette") invece del
    # vendor nominato: DEFINIZIONALMENTE sbagliato (nessun mittente si
    # chiama "bollette"), quindi qui NON vince piu' — anche minuscolo.

    def test_self_referential_bollette_corrected_lowercase(self):
        self.assertEqual(
            _r("cerca in tutte le mie mailbox le bollette plenitude ed enel",
               from_contains="bollette"),
            "plenitude")

    def test_self_referential_bolletta_singolare_corrected(self):
        self.assertEqual(
            _r("cerca in tutte le mie mailbox le bollette enel",
               from_contains="bolletta"),
            "enel")

    def test_self_referential_fatture_corrected(self):
        self.assertEqual(
            _r("le fatture anthropic di questo mese", from_contains="fatture"),
            "anthropic")

    def test_self_referential_no_candidate_cleared(self):
        # Autoreferenziale ma nessun nome dopo: azzera invece di garantire
        # 0 risultati onesti-ma-inutili cercando "da bollette".
        self.assertIsNone(
            _r("cerca tutte le mie bollette", from_contains="bollette"))

    def test_self_referential_ambiguous_cleared(self):
        # Due candidati distinti dopo il recupero minuscolo -> resta ambiguo,
        # azzerato (mai un default a caso fra i due).
        self.assertIsNone(
            _r("le fatture da alpha e da beta", from_contains="fatture"))

    def test_non_self_referential_value_still_wins(self):
        # Un valore gia' plausibile (non la parola-categoria) resta intatto,
        # anche se la query contiene un altro nome: "l'LLM/utente vince".
        self.assertEqual(
            _r("le bollette plenitude", from_contains="Stripe"), "Stripe")


if __name__ == "__main__":
    unittest.main()
