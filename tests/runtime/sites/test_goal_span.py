"""La risposta e' il tratto di pagina che riguarda il fine (7/8/2026).

Tre modi sbagliati di rispondere a «mostrami le mie prenotazioni», tutti visti
la stessa notte:

- «Azioni web completate: 1» — una ricevuta al posto del dato;
- il corpo INTERO della pagina — 3800 caratteri per due terzi menu e piede,
  cioe' il traboccamento segnalato dal proprietario;
- i soli «blocchi di contenuto» — l'estrattore che serve a decidere se il fine
  e' raggiunto scarta link e pulsanti, e un elenco di prenotazioni E' fatto di
  link: restituiva una riga su undici.

La regola che regge non conosce siti: si parte dalla prima riga che tocca il
fine e si prosegue finche' le righe che lo toccano restano VICINE. Le righe in
mezzo (una citta', una data) sono la risposta anche se non contengono la parola
del fine; il piede, che la contiene ma sta venti righe dopo, resta fuori.
"""
from __future__ import annotations

from playwright_sidecar.session_broker import _goal_text_span

_PAGINA = "\n".join([
    "Vai al contenuto principale", "EUR", "Registra la tua struttura",
    "roberto brunialti", "Livello 3 di Genius", "Soggiorni", "Voli",
    "Prenotazioni e viaggi", "Trova una prenotazione",
    "Rimini", "5 nov - 7 nov1 prenotazione",
    "Sabaudia", "29 mag - 2 giu1 prenotazione",
    "Palermo", "14 gen - 14 gen1 prenotazione",
    # piede: venti righe estranee, poi una che nomina i viaggi
    *[f"voce di piede {n}" for n in range(20)],
    "Gestisci i tuoi viaggi", "Contatta l'Assistenza Clienti",
])


def test_il_tratto_e_l_elenco_non_la_pagina() -> None:
    tratto = _goal_text_span(_PAGINA, "vai alle mie prenotazioni")
    assert tratto.startswith("Prenotazioni e viaggi")
    assert "Rimini" in tratto and "Sabaudia" in tratto and "Palermo" in tratto
    assert "5 nov - 7 nov1 prenotazione" in tratto


def test_l_intestazione_del_sito_resta_fuori() -> None:
    tratto = _goal_text_span(_PAGINA, "vai alle mie prenotazioni")
    assert "Vai al contenuto principale" not in tratto, (
        "il verbo di movimento del fine non deve agganciare il primo link "
        "di ogni pagina accessibile")
    assert "Registra la tua struttura" not in tratto


def test_il_piede_resta_fuori_anche_se_nomina_il_fine() -> None:
    tratto = _goal_text_span(_PAGINA, "vai alle mie prenotazioni")
    assert "Gestisci i tuoi viaggi" not in tratto
    assert "voce di piede 0" not in tratto


def test_un_fine_che_non_tocca_niente_non_inventa_un_tratto() -> None:
    assert _goal_text_span(_PAGINA, "vai alle fatture") == ""


def test_il_tetto_di_caratteri_e_rispettato() -> None:
    lungo = "\n".join(["prenotazione %d" % n for n in range(500)])
    assert len(_goal_text_span(lungo, "prenotazioni", max_char=200)) <= 200
