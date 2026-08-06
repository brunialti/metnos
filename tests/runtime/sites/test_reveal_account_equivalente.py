"""Lo stesso controllo reso tre volte non sono tre controlli (7/8/2026).

Turno reale `7803a3f2` — «i tuoi prossimi viaggi» su Booking. La procedura vera
e' due passi: si apre il controllo in alto a destra, poi si sceglie
«Prenotazioni e viaggi». Il primo passo si fermava: Booking rende il pulsante
account in testata, nel menu compatto e in una copia nascosta, e il risolutore
vedeva TRE candidati equivalenti — quindi dichiarava ambiguita' e non apriva
niente.

I candidati osservati quella notte, testualmente: «Il tuo account: <nome>,
Livello 3 di Genius» tre volte, piu' «Viaggio per lavoro» tre volte.

Il confine resta: nomi accessibili DIVERSI sono un'ambiguita' vera, e nessuno
la scioglie al posto dell'utente. Qui si scioglie solo la ripetizione.
"""
from __future__ import annotations

from playwright_sidecar import action_resolver as ar


def _controllo(id_: str, nome: str, *, tag: str = "button") -> dict:
    return {"id": id_, "name": nome, "tag": tag, "role": "button",
            "aria_expanded": "false", "visible": True, "clickable": True}


def test_tre_copie_dello_stesso_pulsante_si_aprono(monkeypatch) -> None:
    monkeypatch.setattr(ar, "_candidate_matches_concept",
                        lambda candidate, concept: True)
    nome = "Il tuo account: roberto brunialti, Livello 3 di Genius"
    esito = ar.choose_authenticated_reveal_candidate(
        [_controllo("a1", nome), _controllo("a2", nome), _controllo("a3", nome)])
    assert esito["ok"] is True, esito.get("error_class")
    assert esito["candidate"]["id"] == "a1"       # scelta deterministica


def test_nomi_diversi_restano_ambigui(monkeypatch) -> None:
    monkeypatch.setattr(ar, "_candidate_matches_concept",
                        lambda candidate, concept: True)
    esito = ar.choose_authenticated_reveal_candidate(
        [_controllo("a1", "Il tuo account"),
         _controllo("a2", "Viaggio per lavoro")])
    assert esito["ok"] is False
    assert esito["error_class"] == "selector_ambiguous"
    assert len(esito["ranked"]) == 2, "i candidati restano visibili all'utente"


def test_un_solo_controllo_resta_il_caso_semplice(monkeypatch) -> None:
    monkeypatch.setattr(ar, "_candidate_matches_concept",
                        lambda candidate, concept: True)
    esito = ar.choose_authenticated_reveal_candidate(
        [_controllo("solo", "Il tuo account")])
    assert esito["ok"] is True and esito["candidate"]["id"] == "solo"
