"""Un'etichetta ARIA sbagliata non nasconde il testo visibile (9/9/2026).

Turno reale `ffe2a2d6` — «entra in Telepass». La procedura vera e' due passi:
si apre il menu, poi si preme «Accedi». Il primo passo riusciva, il secondo no.

Il motivo, misurato sulla pagina viva: il vero collegamento di accesso porta
`aria-label="aria-label-middle_menu"` — una chiave di traduzione mai risolta,
scritta a mano dal sito. Il nome accessibile e' quella chiave, quindi il
collegamento non somigliava per niente a «login» e prendeva zero; i due
involucri grafici dentro di esso, invece, portavano il testo «Accedi» ma
nessuna destinazione. Il risolutore vedeva due cose identiche senza meta e si
fermava per ambiguita', mentre il controllo vero — unico, con href — era li'.

Il nome accessibile e' scritto a mano e puo' essere sbagliato; il testo
visibile e' quello che la pagina mostra davvero. Da qui in poi il controllo
porta entrambi, e nessuno dei due nasconde l'altro.

Il confine resta: nomi diversi con destinazioni diverse sono un'ambiguita'
vera, e nessuno la scioglie al posto dell'utente.
"""
from __future__ import annotations

import pytest

from playwright_sidecar import action_resolver as ar

_DESTINAZIONE = "https://www.telepass.com/KTI/dashboard"


def _osservato(id_: str, *, tag: str, name: str = "", text: str = "",
               href: str = "") -> dict:
    """Un candidato come lo consegna l'enumeratore del broker."""
    return {"id": id_, "tag": tag, "role": "", "type": "", "name": name,
            "text": text, "label": "", "context_name": "", "href": href,
            "visible": True, "in_viewport": True, "topmost": True,
            "disabled": False}


def _menu_telepass() -> list[dict]:
    """I tre nodi osservati nel menu aperto, testualmente."""
    return [
        _osservato("m14", tag="a", name="aria-label-middle_menu",
                   text="Accedi", href=_DESTINAZIONE),
        _osservato("m25", tag="div", name="Accedi", text="Accedi"),
        _osservato("m26", tag="div", name="Accedi", text="Accedi"),
    ]


def test_il_collegamento_vero_vince_sui_suoi_involucri() -> None:
    esito = ar.choose_candidate("login", _menu_telepass(), "click")
    assert esito["ok"] is True, esito.get("error_class")
    assert esito["candidate"]["id"] == "m14"        # l'ancora, non il wrapper
    assert esito["candidate"]["href"] == _DESTINAZIONE
    assert esito["ambiguous"] is False


def test_senza_il_testo_visibile_il_turno_si_fermava() -> None:
    """La stessa pagina, letta come la leggeva prima: ambigua e senza meta."""
    prima = [{k: v for k, v in candidato.items() if k != "text"}
             for candidato in _menu_telepass()]
    esito = ar.choose_candidate("login", prima, "click")
    assert esito["ok"] is True
    assert esito["candidate"]["tag"] == "div"       # un involucro senza href
    assert esito["ambiguous"] is True               # -> selector_ambiguous


def test_una_etichetta_giusta_resta_il_nome_del_controllo() -> None:
    """Un'icona senza testo continua a farsi riconoscere dalla sua etichetta."""
    candidati = [_osservato("i1", tag="button", name="Accedi", text=""),
                 _osservato("i2", tag="button", name="Carrello", text="")]
    esito = ar.choose_candidate("login", candidati, "click")
    assert esito["ok"] is True, esito.get("error_class")
    assert esito["candidate"]["id"] == "i1"
    assert esito["ambiguous"] is False


def test_destinazioni_diverse_restano_ambigue() -> None:
    """Due accessi veri e distinti: la scelta non e' del risolutore."""
    candidati = [
        _osservato("p1", tag="a", name="", text="Accedi",
                   href="https://www.telepass.com/privati/login"),
        _osservato("p2", tag="a", name="", text="Accedi",
                   href="https://www.telepass.com/business/login"),
    ]
    esito = ar.choose_candidate("login", candidati, "click")
    assert esito["ambiguous"] is True


def test_l_enumeratore_consegna_il_testo_visibile() -> None:
    """La pagina vera, in locale: il campo esiste e porta il testo mostrato."""
    import asyncio

    async_playwright = pytest.importorskip(
        "playwright.async_api", reason="playwright non installato",
    ).async_playwright

    from playwright_sidecar import session_broker

    pagina = (
        "<body><a href='https://esempio.invalid/area'"
        " aria-label='aria-label-middle_menu'"
        " style='display:block;width:120px;height:40px'>"
        "<div><div>Accedi</div></div></a></body>"
    )

    async def _osserva() -> list[dict] | None:
        async with async_playwright() as motore:
            # Un browser assente e' un ambiente incompleto, non un difetto del
            # prodotto: le quattro prove qui sopra misurano la stessa regola
            # senza browser, e questa aggiunge soltanto l'enumeratore vero.
            try:
                browser = await motore.chromium.launch(headless=True)
            except Exception as motivo:      # browser non installato
                if "Executable doesn't exist" not in str(motivo):
                    raise
                return None
            try:
                scheda = await browser.new_page()
                await scheda.set_content(pagina)
                return await session_broker._enumerate_candidates(scheda)
            finally:
                await browser.close()

    candidati = asyncio.run(_osserva())
    if candidati is None:
        pytest.skip("browser Playwright non installato in questo ambiente")
    ancore = [c for c in candidati if c.get("tag") == "a"]
    assert ancore, candidati
    assert ancore[0]["name"] == "aria-label-middle_menu"
    assert ancore[0]["text"] == "Accedi"
    esito = ar.choose_candidate("login", candidati, "click")
    assert esito["ok"] is True, esito.get("error_class")
    assert esito["candidate"]["tag"] == "a"
    assert esito["ambiguous"] is False
