"""Exploring means going to new places, and the budget is spent on those.

Two properties the goal pilot was missing (2026-08-07), both surfaced by a
real turn on an authenticated portal:

1. **The same place has ONE identity.** The anti-loop signed the ELEMENT, raw
   href included. A site that re-emits a different `label`/`sid` between two
   renders produces two keys for the very same link: the pilot failed to
   recognise it had already been there. What "the same place" means was
   already decided in two spots of the code (`_safe_navigation_identity`,
   `_destinazione_di`) and always the same way — scheme, host, path — only
   the anti-loop ignored it. Two functions answering the same question
   differently are a defect, not a difference.

2. **The budget counts progress, not attempts.** Four steps were four CLICKS:
   one that moved nothing burned a quarter of the exploration. Now an empty
   step does not consume the exploration budget but draws on a ceiling of its
   own, because not even nothing may repeat forever.

Progress cannot be measured by the URL alone: revealing the account menu does
NOT change the address, and it is the step that opens the only way into the
personal area. What is observed is the place PLUS the open controls.
"""
from __future__ import annotations

import asyncio
import hashlib
import time

from playwright_sidecar import action_resolver as ar
from playwright_sidecar import session_broker as sb

_AZIONE = "vai alle mie prenotazioni"
_FINE = "le mie prenotazioni"
_CHIAVE = hashlib.sha256(ar.normalize(_AZIONE).encode("utf-8")).hexdigest()
_QUI = "https://x.test/area"


def _link(nome: str, href: str, ident: str) -> dict:
    return {"id": ident, "tag": "a", "role": "link", "name": nome,
            "href": href, "visible": True, "in_viewport": True,
            "topmost": True}


# --- 1. one place, one identity -------------------------------------------

def test_lo_stesso_link_riemesso_e_lo_stesso_posto() -> None:
    prima = _link("Prenotazioni", "https://x.test/viaggi?label=AAA&sid=1", "a")
    dopo = _link("Prenotazioni", "https://x.test/viaggi?sid=2&label=BBB", "b")
    assert ar.goal_place_key(prima) == ar.goal_place_key(dopo)
    assert ar.goal_candidate_key(prima) != ar.goal_candidate_key(dopo), (
        "the ELEMENT identity must stay sensitive to the href: that is the "
        "one used to recognise a pagination control")


def test_un_posto_diverso_resta_diverso() -> None:
    viaggi = _link("Prenotazioni", "https://x.test/viaggi?a=1", "a")
    fatture = _link("Prenotazioni", "https://x.test/fatture?a=1", "b")
    assert ar.goal_place_key(viaggi) != ar.goal_place_key(fatture)


def test_un_controllo_senza_destinazione_e_identificato_da_se() -> None:
    bottone = {"tag": "button", "role": "button", "name": "Menu"}
    assert ar.goal_place_key(bottone) == ar.goal_candidate_key(bottone)


def test_il_posto_di_un_url_combacia_col_posto_del_link_che_ci_porta() -> None:
    link = _link("Prenotazioni", "https://x.test/viaggi?label=AAA", "a")
    assert ar.url_place_key("https://x.test/viaggi?altro=1") == (
        ar.goal_place_key(link))
    assert ar.url_place_key("javascript:void(0)") == "", (
        "what is not a web destination must not enter the visited set")


def test_un_posto_visitato_esclude_il_link_riemesso() -> None:
    andato = _link("Prenotazioni", "https://x.test/viaggi?label=AAA", "a")
    tornato = _link("Prenotazioni", "https://x.test/viaggi?label=ZZZ", "b")
    residui = ar.goal_navigation_candidates(
        [tornato], excluded={ar.goal_place_key(andato)})
    assert residui == []


# --- 2. the budget is spent on progress ------------------------------------

class _Corpo:
    def __init__(self, testo: str) -> None:
        self._testo = testo

    async def inner_text(self, **_kw) -> str:
        return self._testo


class _Pagina:
    """A page driven by the test: whoever builds it decides the controls."""

    def __init__(self, candidati: list[dict], testo: str = "area personale",
                 url: str = _QUI) -> None:
        self.url = url
        self.candidati = candidati
        self._testo = testo

    async def evaluate(self, script, _arg=None):
        if script == sb._ENUMERATE_ACTION_TARGETS_JS:
            return self.candidati
        if script == sb._GOAL_EVIDENCE_JS:
            return []
        return None

    def locator(self, selector):
        assert selector == "body"
        return _Corpo(self._testo)


def _sessione(pagina: _Pagina) -> dict:
    return {"page": pagina, "pending_actions": {},
            "web_content_ingested": True, "authenticated": True,
            "goal_flows": {}}


def _osserva(entry: dict) -> dict:
    """One pilot observation: typed goal, the way `act_sites` passes it."""
    return asyncio.run(sb._prepare_action(
        entry, "sid-esplora", _AZIONE, None, primitive_override="search",
        target_override=_FINE, allow_model=False))


def _flusso(entry: dict) -> dict:
    return entry["goal_flows"][_CHIAVE]


def _dopo_una_navigazione(flusso: dict) -> None:
    """What `_execute_plan` writes once a goal step has started."""
    flusso["steps"] = int(flusso.get("steps", 0)) + 1
    flusso["navigazione_da_verificare"] = True


def test_un_passo_che_non_muove_nulla_non_consuma_l_esplorazione() -> None:
    pagina = _Pagina([_link("Prenotazioni", "https://x.test/viaggi", "a")])
    entry = _sessione(pagina)
    _osserva(entry)
    _dopo_una_navigazione(_flusso(entry))

    _osserva(entry)  # the page is identical: the click fell into the void

    assert _flusso(entry)["steps"] == 0, (
        "a click that moves nothing is not a quarter of the exploration")
    assert _flusso(entry)["sterile"] == 1


def test_il_nulla_ripetuto_ha_comunque_una_fine() -> None:
    pagina = _Pagina([_link("Prenotazioni", "https://x.test/viaggi", "a")])
    entry = _sessione(pagina)
    for _ in range(sb._MAX_GOAL_STERILE + 1):
        _osserva(entry)
        _dopo_una_navigazione(_flusso(entry))
    esito = _osserva(entry)

    assert _flusso(entry)["sterile"] >= sb._MAX_GOAL_STERILE
    assert not esito.get("ok")
    assert esito.get("error_class") == "goal_step_limit"


def test_rivelare_un_menu_e_progresso_anche_senza_cambiare_indirizzo() -> None:
    """The personal-area step does not move the URL: it is not an empty step."""
    pagina = _Pagina([_link("Il tuo account", "https://x.test/area", "a")])
    entry = _sessione(pagina)
    _osserva(entry)
    _dopo_una_navigazione(_flusso(entry))
    # the menu opened: same address, new controls
    pagina.candidati = [*pagina.candidati,
                        _link("Prenotazioni e viaggi",
                              "https://x.test/viaggi", "b")]

    _osserva(entry)

    assert _flusso(entry)["steps"] == 1, (
        "a reveal that exposes the way is progress, not an empty click")
    assert _flusso(entry).get("sterile", 0) == 0


def test_arrivare_altrove_e_progresso() -> None:
    pagina = _Pagina([_link("Prenotazioni", "https://x.test/viaggi", "a")])
    entry = _sessione(pagina)
    _osserva(entry)
    _dopo_una_navigazione(_flusso(entry))
    pagina.url = "https://x.test/viaggi"
    pagina.candidati = [_link("Rimini", "https://x.test/viaggi/1", "r")]

    _osserva(entry)

    assert _flusso(entry)["steps"] == 1
    assert _flusso(entry).get("sterile", 0) == 0


def test_il_posto_in_cui_si_e_non_e_una_meta() -> None:
    """A link back to where you already are is not an exploration step.

    On this fake page no candidate ever reaches a real locator: the one that
    gets PICKED dies shortly after with `target_changed`, the one that is not
    even considered exits with `selector_missing` and no observed candidate.
    The difference between the two outcomes is the proof.
    """
    altrove = _osserva(_sessione(
        _Pagina([_link("Prenotazioni", "https://x.test/viaggi", "a")])))
    assert altrove.get("error_class") == "target_changed", (
        "a link towards a new place must be picked")

    fermo = _osserva(_sessione(
        _Pagina([_link("Prenotazioni", _QUI + "?label=ZZZ", "a")])))
    assert fermo.get("error_class") == "selector_missing"
    assert fermo.get("observed_candidates") == [], (
        "the pilot was about to spend a step to stay put")


def test_il_flusso_nasce_con_i_contatori_del_progresso() -> None:
    entry = _sessione(_Pagina([]))
    _osserva(entry)
    flusso = _flusso(entry)
    assert flusso["sterile"] == 0
    assert flusso["last_state"], (
        "the observed state must be recorded at once: it is the yardstick "
        "for the next step")
    assert time.time() - float(flusso["started"]) < 60
