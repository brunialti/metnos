"""Aprire un SITO e' aprire una sessione, non leggere un documento (10/9/2026).

Turno reale `7f6bddbd`, richiesta «apri telepass.com, fai login e vai su
movimenti e fatture»: **zero passi eseguiti**, `capability_missing`, e
nell'utente un «questo non lo so fare» per una cosa che il prodotto sa fare
benissimo — l'aveva appena fatta con un'altra frase.

La catena e' questa. Il rilevatore lessicale porta «apri» al verbo canonico
`read`: giusto per un file, dove aprire *e'* leggere. Il piano corretto per un
sito e' pero' `open_sites -> login_sites -> act_sites`, e nessuna di quelle
teste e' un lettore. La guardia di copertura vede un `read` richiesto e mai
soddisfatto e ferma il turno **prima** di eseguire un solo passo.

Il produttore di quel dominio e' `open_sites`: ADR 0218 tiene `open` come
verbo della sola sessione browser. La frase piu' lunga sopravviveva per caso,
perche' conteneva anche una lettura che metteva `read_sites` nel piano.

Run: `python3 -m pytest tests/runtime/engine/test_aprire_un_sito_non_e_leggere.py -v`
"""
from __future__ import annotations

import unittest

from engine.types import Intent, Framework, StepSpec
from engine.dispatch import _dropped_required_verbs

QUERY = "apri telepass.com, fai login e vai su movimenti e fatture"


def _fw(*tools) -> Framework:
    steps = [StepSpec(tool=t, args={}) for t in tools]
    steps.append(StepSpec(tool="final_answer", args={}))
    return Framework(steps=steps)


def _intent(verb: str, obj: str, actions=None) -> Intent:
    return Intent(kind="action", verb=verb, object=obj, confidence=1.0,
                  actions=list(actions or ()))


class TestAprireUnSitoNonELeggere(unittest.TestCase):

    def test_la_sessione_soddisfa_la_lettura_richiesta_da_apri(self):
        """Il caso reale: nessun verbo resta scoperto, il turno puo' partire."""
        self.assertEqual(
            _dropped_required_verbs(
                _fw("open_sites", "login_sites", "act_sites"),
                QUERY, _intent("open", "sites")),
            set())

    def test_senza_la_sessione_la_lettura_resta_richiesta(self):
        """Verifica in negativo: la deroga vale per la sessione, non in genere.

        Se il piano non apre nessuna sessione, «apri» torna a essere una
        lettura da soddisfare, e il cancello continua a fare il suo mestiere.
        """
        self.assertIn(
            "read",
            _dropped_required_verbs(
                _fw("login_sites", "act_sites"),
                QUERY, _intent("open", "sites")))

    def test_un_altro_oggetto_non_eredita_la_deroga(self):
        """`open_sites` nel piano non copre una lettura chiesta altrove.

        La deroga e' legata all'oggetto della richiesta: aprire un sito non
        risponde a «leggi il documento», e confonderle rimetterebbe in piedi
        proprio l'anti-pattern §2.8 che il cancello difende.
        """
        self.assertIn(
            "read",
            _dropped_required_verbs(
                _fw("open_sites", "login_sites"),
                "apri il file contratto.pdf e fai login",
                _intent("read", "files")))


if __name__ == "__main__":
    unittest.main()
