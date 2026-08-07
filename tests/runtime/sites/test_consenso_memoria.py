"""Il consenso non si chiede due volte, e lo switch lo pre-concede (7/8/2026).

Due richieste del proprietario, una proprieta' sola: chi ha gia' detto «vai»
non deve ridirlo. Prima valeva il contrario — un percorso di due passi (apri il
menu, poi scegli la voce) chiedeva conferma due volte, e con lo sblocco
automatico acceso chiedeva lo stesso, perche' quella preferenza governava solo
gli host di rete.

Il confine resta, ed e' quello che rende l'opzione «suicida» e non «cieca»:
valgono solo gli SPOSTAMENTI. Un invio di modulo, una compilazione di
credenziali o uno scaricamento continuano a chiedere.
"""
from __future__ import annotations

from playwright_sidecar.session_broker import (
    _destinazione_di, _navigazione_preautorizzata)


def _piano(**extra) -> dict:
    piano = {"destination_url": "https://secure.esempio.it/area/mie-cose?sid=abc",
             "sensitivity_reasons": ["navigation", "tainted_turn"],
             "candidate": {"tag": "a"}}
    piano.update(extra)
    return piano


def test_lo_switch_preconcede_lo_spostamento() -> None:
    assert _navigazione_preautorizzata({"auto_allow": True}, _piano()) is True


def test_senza_switch_e_senza_precedenti_si_chiede() -> None:
    assert _navigazione_preautorizzata({"auto_allow": False}, _piano()) is False


def test_un_consenso_gia_dato_vale_per_quel_posto() -> None:
    sessione = {"auto_allow": False,
                "approved_destinations": {"https://secure.esempio.it/area/mie-cose"}}
    assert _navigazione_preautorizzata(sessione, _piano()) is True


def test_i_parametri_non_fanno_identita() -> None:
    """Booking riemette gli stessi parametri in ordine diverso fra due render:
    un consenso ricordato sulla stringa intera non varrebbe mai due volte."""
    a = _destinazione_di(_piano(destination_url="https://x.it/p?a=1&b=2"))
    b = _destinazione_di(_piano(destination_url="https://x.it/p?b=2&a=1"))
    assert a == b == "https://x.it/p"


def test_un_altro_posto_chiede_lo_stesso() -> None:
    sessione = {"auto_allow": False,
                "approved_destinations": {"https://secure.esempio.it/altro"}}
    assert _navigazione_preautorizzata(sessione, _piano()) is False


def test_cio_che_non_e_spostamento_chiede_sempre() -> None:
    """Lo switch non e' cieco: un invio di modulo resta un consenso umano."""
    modulo = _piano(sensitivity_reasons=["post"],
                    candidate={"tag": "button", "form_method": "POST"})
    assert _navigazione_preautorizzata({"auto_allow": True}, modulo) is False

    credenziali = _piano(sensitivity_reasons=["credential_fill"])
    assert _navigazione_preautorizzata({"auto_allow": True}, credenziali) is False
