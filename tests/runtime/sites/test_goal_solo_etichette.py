"""Un paragrafo non e' una destinazione (7/8/2026).

Turno reale `d095f7ee`: per «mostrami le mie prenotazioni» il candidato in
TESTA alla classifica era il pulsante «I prezzi mostrati sono stime e quindi
possono variare. Vedrai l'importo effettivo al momento della prenotazione…» —
punteggio 0,733 — perche' contiene la parola «prenotazione». Con lui in lista
tutto il resto diventava ambiguo, e il flusso moriva.

La proprieta' mancante non riguarda Booking ne' le prenotazioni: **un nome
accessibile lungo come una frase e' prosa descrittiva, non un posto dove si
va**. Due segnali indipendenti dalla lingua: quante parole, e la punteggiatura
di frase. I due punti restano ammessi, perche' le etichette li usano.
"""
from __future__ import annotations

import pytest

from playwright_sidecar.action_resolver import (
    _looks_like_prose, goal_navigation_candidates)

_DISCLAIMER = ("I prezzi mostrati sono stime e quindi possono variare. Vedrai "
               "l'importo effettivo al momento della prenotazione.")


@pytest.mark.parametrize("etichetta", [
    "Prenotazioni e viaggi",
    "Il tuo account: roberto brunialti, Livello 3 di Genius",
    "Viaggio per lavoro",
    "Accedi",
    "Gestisci le tue prenotazioni e i tuoi viaggi",
])
def test_le_etichette_restano_candidabili(etichetta) -> None:
    assert _looks_like_prose(etichetta) is False


@pytest.mark.parametrize("testo", [
    _DISCLAIMER,
    "Continuando accetti i termini del servizio. Leggi l'informativa completa.",
    ("Questo elenco mostra soltanto le strutture disponibili nelle date "
     "selezionate e puo' cambiare durante la ricerca perche' le disponibilita' "
     "vengono aggiornate di continuo"),
])
def test_la_prosa_non_e_un_candidato(testo) -> None:
    assert _looks_like_prose(testo) is True


def test_il_filtro_toglie_la_prosa_dai_candidati() -> None:
    candidati = [
        {"id": "1", "name": _DISCLAIMER, "tag": "button", "role": "button",
         "visible": True},
        {"id": "2", "name": "Prenotazioni e viaggi", "tag": "a", "role": "link",
         "visible": True},
    ]
    ammessi = [c["id"] for c in goal_navigation_candidates(candidati)]
    assert ammessi == ["2"]
