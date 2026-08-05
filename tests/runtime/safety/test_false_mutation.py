"""test_false_mutation — guard §2.8: il synth non deve dichiarare una MUTAZIONE
(creato il foglio / inviato la mail) quando non c'è stata alcuna mutazione reale
(mutations==0). Bug live 21/6 (fatture Anthropic: 'Ho creato il foglio' con 0
file creati). _detect_false_mutation NON deve toccare creazioni reali, claim di
sintesi testuale ('riepilogo'), claim di lettura, o negazioni esplicite.
"""
from __future__ import annotations
import sys, unittest
from pathlib import Path
import agent_runtime as ar  # noqa: E402


class TestFalseMutation(unittest.TestCase):
    def _d(self, final, counts): return ar._detect_false_mutation(final, counts)

    def test_false_create_file(self):
        self.assertTrue(self._d(
            "Ho creato il foglio 'Anthropic Billing' con le fatture estratte.",
            {"mutations": 0, "items": 1}))

    def test_false_send(self):
        self.assertTrue(self._d("Ho inviato la mail a Mario.", {"mutations": 0}))

    def test_real_create_not_flagged(self):
        self.assertFalse(self._d("Ho creato il foglio con i dati.",
                                 {"mutations": 1}))

    def test_text_summary_not_flagged(self):
        self.assertFalse(self._d("Ho creato un riepilogo dei tuoi eventi.",
                                 {"mutations": 0}))

    def test_read_claim_not_flagged(self):
        self.assertFalse(self._d("Ho trovato 12 email.", {"mutations": 0}))

    def test_negation_not_flagged(self):
        for m in ("Non ho creato il foglio perche non ho trovato dati.",
                  "Non sono riuscito a creare il foglio.",
                  "I couldn't create the file."):
            self.assertFalse(self._d(m, {"mutations": 0}), m)

    def test_mutating_attempted_not_flagged(self):
        # mutazione TENTATA ma fallita → altro path (errore), non false-success.
        self.assertFalse(self._d("Ho creato il foglio.",
                                 {"mutations": 0, "mutating_attempted": True}))


if __name__ == "__main__":
    unittest.main()
