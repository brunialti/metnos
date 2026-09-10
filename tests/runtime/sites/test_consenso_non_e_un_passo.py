"""Il consenso e' una PRECONDIZIONE, non un passo del piano (10/9/2026).

Turno reale `715e08e6`: la richiesta diceva «se il sito chiede quali cookies,
clicca su 'solo necessari'». Il banner era gia' stato risolto dal broker prima
del login — correttamente, e il login e' riuscito — ma il pianificatore aveva
comunque programmato un passo `act_sites(action="accetta cookie necessari")`.
Quel passo non ha piu' un controllo da colpire: risolverlo contro la pagina ha
scelto cio' che c'era piu' vicino, cioe' il pulsante **«Apri chat»**, con
confidenza 0,62, e l'ha cliccato. Il turno e' finito `wrong_args`.

E' lo stesso errore di forma di `test_login_stato_non_azione`: si modella
l'AZIONE invece dello STATO. Se lo stato e' gia' quello richiesto, non c'e'
niente da compiere, e cercare qualcosa da compiere e' peggio che non fare
nulla.

Il riconoscimento usa il marcatore del contenitore preso dal lessico di
rilevazione — la stessa fonte del localizzatore strutturale — quindi vale per
ogni lingua coperta dal lessico e non c'e' nessun elenco nel codice.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from playwright_sidecar import action_resolver
from playwright_sidecar import cookie_privacy as cp
from playwright_sidecar import session_broker as sb


class _Lock:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


def _sessione() -> dict:
    return {"owner": "host", "domain": "esempio.it", "lock": _Lock(),
            "authenticated": True, "credential_mode": "default",
            "last_used": time.time(), "page": None}


def _agisci(action: str) -> dict:
    sb._sessions["s1"] = _sessione()
    try:
        return asyncio.run(sb.op_act(session_id="s1", owner="host",
                                     action=action))
    finally:
        sb._sessions.pop("s1", None)


@pytest.mark.parametrize("action", [
    "accetta cookie necessari",
    "clicca su solo necessari nel banner dei cookie",
    "accept necessary cookies",
    "gestisci il consenso privacy",
    "reject advertising choices",
])
def test_un_azione_che_nomina_il_consenso_e_riconosciuta(action) -> None:
    assert action_resolver.names_privacy_container(action) is True


@pytest.mark.parametrize("action", [
    "clicca accedi",
    "cerca le fatture del 2026",
    "compila il campo utente",
    "",
])
def test_un_azione_ordinaria_non_lo_e(action) -> None:
    assert action_resolver.names_privacy_container(action) is False


def test_il_consenso_non_viene_risolto_contro_i_controlli_della_pagina(
        monkeypatch) -> None:
    """La prova che conta: quel percorso non deve proprio essere imboccato."""
    def mai(*_a, **_kw):
        raise AssertionError("il consenso e' stato risolto come un'azione")

    monkeypatch.setattr(sb, "_prepare_action_with_resource_fallback", mai)

    async def risolto(entry, settle=False):
        return cp.CookieOutcome("resolved", "cookie", "", 1, 1)

    monkeypatch.setattr(sb, "_dismiss_privacy_obstruction", risolto)

    esito = _agisci("accetta cookie necessari")
    assert esito["ok"] is True
    assert esito["executed"] is True          # un pannello c'era, ed e' caduto
    assert esito["precondition"] == "privacy_consent"
    assert esito["panels"] == 1


def test_nessun_pannello_e_un_esito_onesto_non_un_effetto(monkeypatch) -> None:
    """`ok` senza `executed`: lo stato e' quello chiesto, non si e' fatto nulla.

    Dichiarare `executed` qui sarebbe un effetto raccontato e mai avvenuto
    (§2.8), e il piano non deve poter dedurre che un pannello e' stato chiuso.
    """
    monkeypatch.setattr(
        sb, "_prepare_action_with_resource_fallback",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("risolto")))

    async def pulito(entry, settle=False):
        return cp.CookieOutcome("clear", "unknown", "", 1, 0)

    monkeypatch.setattr(sb, "_dismiss_privacy_obstruction", pulito)

    esito = _agisci("accept necessary cookies")
    assert esito["ok"] is True
    assert esito["executed"] is False
    assert esito["panels"] == 0


def test_un_consenso_che_non_si_risolve_resta_un_rifiuto(monkeypatch) -> None:
    """Bloccato non diventa riuscito: la pagina e' ancora coperta."""
    monkeypatch.setattr(
        sb, "_prepare_action_with_resource_fallback",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("risolto")))

    async def bloccato(entry, settle=False):
        return cp.CookieOutcome("blocked", "cookie", "dismissal_limit", 1, 1)

    monkeypatch.setattr(sb, "_dismiss_privacy_obstruction", bloccato)

    esito = _agisci("clicca su solo necessari nel banner dei cookie")
    assert esito["ok"] is False
    assert esito["error_class"] == "cookie_precondition_unresolved"
    assert esito["obstruction_reason"] == "dismissal_limit"
