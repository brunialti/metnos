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
import os
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


@pytest.mark.parametrize("action", [
    "vai alla cookie policy",
    "naviga alla pagina privacy",
    "visit the cookie policy",
    "apri https://esempio.it/cookie-policy",
])
def test_chiedere_il_documento_non_e_rispondere_al_pannello(action) -> None:
    """Nominare il pannello non basta: una destinazione resta una destinazione.

    Riconoscere il consenso col marcatore del contenitore cattura anche
    «privacy» e «cookie» dentro una richiesta di NAVIGAZIONE. Senza questa
    distinzione, chi chiede di raggiungere l'informativa riceverebbe «gia'
    fatto» e non ci arriverebbe mai: una regressione silenziosa introdotta
    dalla correzione precedente, trovata rileggendola.

    Il segnale e' un verbo di navigazione del lessico, o un URL esplicito.
    """
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


class _Pagina:
    """Pagina finta: solo un URL e un'attesa che non dorme davvero."""

    def __init__(self, url: str) -> None:
        self.url = url

    async def wait_for_timeout(self, _ms) -> None:
        return None


def _precondizione(entry, **kw):
    return asyncio.run(sb._dismiss_privacy_obstruction(entry, **kw))


def test_un_pannello_tardivo_viene_atteso(monkeypatch) -> None:
    """`settle` deve attendere davvero: era accettato e ignorato.

    Una piattaforma di consenso disegna il banner DOPO che il suo script si e'
    caricato. Chi chiedeva di aspettare non aspettava, quindi osservava una
    pagina ancora pulita e proseguiva: sul turno `a8dbe80b` le credenziali
    sono finite sotto al banner dell'origine di login.
    """
    monkeypatch.setattr(sb.sites_audit, "record", lambda *_a, **_kw: None)
    esiti = [cp.CookieOutcome("clear", "unknown", "", 1, 0),
             cp.CookieOutcome("clear", "unknown", "", 1, 0),
             cp.CookieOutcome("resolved", "cookie", "", 1, 1)]
    visti = []

    async def reject(_page, _state, **_kw):
        out = esiti[min(len(visti), len(esiti) - 1)]
        visti.append(out)
        return out

    monkeypatch.setattr(sb.cookie_privacy, "reject_cookies", reject)

    entry = _sessione()
    entry["page"] = _Pagina("https://www.esempio.it/")
    esito = _precondizione(entry, settle=True)
    assert esito.panels == 1
    assert len(visti) == 3       # ha riosservato, non si e' fermato al primo


def test_senza_settle_si_osserva_una_volta_sola(monkeypatch) -> None:
    """L'attesa e' un costo: la si paga solo dove e' stata chiesta."""
    monkeypatch.setattr(sb.sites_audit, "record", lambda *_a, **_kw: None)
    visti = []

    async def reject(_page, _state, **_kw):
        visti.append(1)
        return cp.CookieOutcome("clear", "unknown", "", 1, 0)

    monkeypatch.setattr(sb.cookie_privacy, "reject_cookies", reject)

    entry = _sessione()
    entry["page"] = _Pagina("https://www.esempio.it/")
    _precondizione(entry)
    assert len(visti) == 1


def test_una_nuova_origine_e_un_consenso_nuovo(monkeypatch) -> None:
    """Il consenso appartiene a un'ORIGINE, non alla sessione.

    Passando a `login.` compare il banner di QUELL'origine, e lo stato del
    precedente non dice niente su di esso. Ereditare il budget faceva sembrare
    il secondo banner una ripetizione del primo, gia' chiuso.
    """
    monkeypatch.setattr(sb.sites_audit, "record", lambda *_a, **_kw: None)
    visti = []

    async def reject(_page, state, **_kw):
        visti.append(dict(state))
        state["clicks"] = state.get("clicks", 0) + 1
        return cp.CookieOutcome("resolved", "cookie", "", 1, 1)

    monkeypatch.setattr(sb.cookie_privacy, "reject_cookies", reject)

    entry = _sessione()
    entry["page"] = _Pagina("https://www.esempio.it/")
    _precondizione(entry)
    _precondizione(entry)
    speso = visti[-1].get("clicks")
    assert speso == 1                      # stessa origine: il budget resta

    entry["page"].url = "https://login.esempio.it/"
    _precondizione(entry)
    assert visti[-1].get("clicks") in (None, 0)   # nuova origine, budget nuovo
    assert entry["cookie_state"]["origin"] == "https://login.esempio.it:443"
    # Il tetto della SESSIONE non si azzera col salto: quel che si e' speso
    # sull'origine precedente viaggia con la sessione.
    assert visti[-1].get("carried") == 2


def test_i_rimbalzi_fra_origini_non_disarmano_il_tetto(monkeypatch) -> None:
    """Il tetto per origine si azzera, quello della sessione no.

    Difetto trovato rileggendo la mia stessa correzione: il tetto ai clic
    viveva DENTRO lo stato che il cambio d'origine azzera, quindi un sito che
    rimbalza fra due origini non lo raggiungeva mai e si poteva cliccare
    all'infinito. Ora il conteggio speso viaggia con la sessione.
    """
    monkeypatch.setattr(sb.sites_audit, "record", lambda *_a, **_kw: None)
    speso = []

    async def reject(_page, state, **_kw):
        speso.append(state.get("clicks", 0) + state.get("carried", 0))
        state["clicks"] = state.get("clicks", 0) + 1
        return cp.CookieOutcome("resolved", "cookie", "", 1, 1)

    monkeypatch.setattr(sb.cookie_privacy, "reject_cookies", reject)

    entry = _sessione()
    entry["page"] = _Pagina("https://a.esempio.it/")
    for giro in range(4):                       # quattro rimbalzi a/b
        entry["page"].url = f"https://{'ab'[giro % 2]}.esempio.it/"
        _precondizione(entry)
    # Senza il conteggio portato appresso questa sequenza sarebbe 0,0,0,0.
    assert speso == [0, 1, 2, 3]
    assert speso[-1] < cp.MAX_SESSION_DISMISSALS   # il tetto esiste ed e' vicino


def test_il_consenso_non_e_l_unica_cosa_che_copre_il_login(monkeypatch) -> None:
    """Tolto il banner, il modulo puo' restare sotto un altro strato.

    Turno `b6c37087` (10/9/2026): banner chiuso, modulo compilato, pulsante
    «Accedi» visibilmente scoperto — e il login fallisce lo stesso, perche' un
    modale promozionale accanto teneva un fondale a tutto schermo che si
    mangiava il clic. Il percorso ordinario ha sempre fatto le due cose in
    quest'ordine; quello di login faceva solo la prima.
    """
    fatti = []

    async def privacy(entry, settle=False):
        fatti.append(("consenso", settle))
        return cp.CookieOutcome("resolved", "cookie", "", 1, 1)

    async def overlay(entry, settle=False, **_kw):
        fatti.append(("strato", settle))
        return True

    monkeypatch.setattr(sb, "_dismiss_privacy_obstruction", privacy)
    monkeypatch.setattr(sb, "_dismiss_obstructing_overlay", overlay)

    esito = asyncio.run(sb._clear_login_surface(_sessione(), settle=True))
    assert fatti == [("consenso", True), ("strato", True)]
    assert esito.status == "resolved"   # il rifiuto puo' venire solo dal primo


_MODALE_SENZA_NOME = """
<style>
  body { margin: 0; font: 14px sans-serif; }
  .fondale { position: fixed; inset: 0; background: rgba(0,0,0,.3); z-index: 9; }
  .modale { position: fixed; left: 26%; top: 22%; width: 46%; height: 56%;
            background: #fff; z-index: 10; }
  .chiudi { position: absolute; right: 10px; top: 10px; width: 24px;
            height: 24px; border: 0; background: transparent; }
</style>
<form action="/entra" method="post">
  <label>Username <input name="u"></label>
  <label>Password <input type="password" name="p"></label>
  <button type="submit" id="accedi">Accedi</button>
</form>
<div class="fondale"></div>
<div class="modale" role="dialog" aria-modal="true">
  <h2>Inquadra il QR code</h2>
  <button class="chiudi" type="button"><svg width="16" height="16"
    viewBox="0 0 16 16"><path d="M2 2 L14 14 M14 2 L2 14"
    stroke="#000"/></svg></button>
</div>
<script>
  document.querySelector('.chiudi').addEventListener('click', () => {
    document.querySelector('.modale').remove();
    document.querySelector('.fondale').remove();
  });
</script>
"""


# Replica STRUTTURALE della pagina di accesso osservata il 10/9/2026: la
# geometria e i ruoli DOM che contano, non i testi ne' il marchio di nessuno.
# Misure prese dal vivo con una sonda in sola lettura, senza credenziali:
# `div.popup-overlay` fisso a tutto schermo con z-index 999999, dentro
# `div.popup-body` 600x450, e come unica chiusura `span.popup-close` 24x32 col
# cursore a mano, SENZA testo, senza nome e senza ruolo. Il modulo di accesso
# resta sotto: ogni clic su «Accedi» finisce nell'overlay.
_REPLICA_ACCESSO = """
<style>
  body { margin: 0; font: 14px sans-serif; }
  .popup-overlay { position: fixed; inset: 0; z-index: 999999;
                   background: rgba(0,0,0,.35); }
  .popup-body { position: absolute; left: 340px; top: 175px;
                width: 600px; height: 450px; background: #fff; }
  .popup-close { position: absolute; left: 559px; top: 17px;
                 width: 24px; height: 32px; cursor: pointer; }
  form { padding: 24px; }
</style>
<form action="/entra" method="post">
  <label>Username <input name="u"></label>
  <label>Password <input type="password" name="p"></label>
  <button type="submit" id="accedi">Accedi</button>
</form>
<div class="popup-overlay">
  <div class="popup-body">
    <h2>Scarica l'applicazione</h2>
    <p>Gestisci qui tutte le tue operazioni</p>
    <span class="popup-close"></span>
  </div>
</div>
<script>
  document.querySelector('.popup-close').addEventListener('click', () => {
    document.querySelector('.popup-overlay').remove();
  });
</script>
"""


@pytest.mark.skipif(os.environ.get("METNOS_SITES_SIM") != "1",
                    reason="prova opt-in con Chromium reale")
def test_una_chiusura_muta_che_non_e_un_controllo(monkeypatch) -> None:
    """Sulla replica: la chiusura e' uno `span`, e va comunque riconosciuta.

    Il localizzatore interrogava soltanto controlli semantici — `button`,
    `[role=button]`, link — e quel nodo non compariva mai. Nessuna chiusura
    veniva tentata, l'overlay restava, e il clic su «Accedi» ci finiva dentro:
    login fallito senza che il sito mostrasse alcun errore (turni `b6c37087` e
    `6a4a16c3`).

    Il riconoscimento resta per RUOLO, non per selettore: cursore a mano,
    piccolo, muto, nell'angolo di chiusura di una radice modale, in cima nel
    proprio punto di contatto, e mai un controllo che invii o navighi.
    """
    from playwright.async_api import async_playwright

    monkeypatch.setattr(sb.sites_audit, "record", lambda *_a, **_kw: None)

    async def esegui():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(
                viewport={"width": 1280, "height": 800})
            await page.route("**/*", lambda route: route.abort())
            await page.set_content(_REPLICA_ACCESSO)
            entry = _sessione()
            entry["page"] = page
            try:
                coperto = await page.evaluate(
                    "() => { const r = document.getElementById('accedi')"
                    "  .getBoundingClientRect();"
                    "  const el = document.elementFromPoint("
                    "    r.left + r.width / 2, r.top + r.height / 2);"
                    "  return el ? el.className : ''; }")
                assert "popup-overlay" in coperto   # il clic finirebbe li'
                assert await sb._dismiss_obstructing_overlay(
                    entry, settle=True) is True
                assert await page.evaluate(
                    "() => !document.querySelector('.popup-overlay')")
                scoperto = await page.evaluate(
                    "() => { const r = document.getElementById('accedi')"
                    "  .getBoundingClientRect();"
                    "  const el = document.elementFromPoint("
                    "    r.left + r.width / 2, r.top + r.height / 2);"
                    "  return el ? el.id : ''; }")
                assert scoperto == "accedi"        # ora il clic arriva
            finally:
                await browser.close()

    asyncio.run(esegui())


@pytest.mark.skipif(os.environ.get("METNOS_SITES_SIM") != "1",
                    reason="prova opt-in con Chromium reale")
def test_una_x_senza_nome_e_comunque_un_uscita(monkeypatch) -> None:
    """La X di chiusura e' quasi sempre un'icona senza nome accessibile.

    Turno `6a4a16c3` (10/9/2026): il modale promozionale sopra il modulo di
    login non veniva mai chiuso, perche' la regola accettava solo una «x»
    testuale. Il suo fondale si mangiava il clic su «Accedi» e il login
    falliva senza che il sito mostrasse alcun errore.

    L'assenza di nome da sola non decide niente: restano la geometria
    dell'angolo di chiusura dentro una radice modale, il vincolo di essere
    l'elemento in cima nel punto di contatto, e il rifiuto di ogni controllo
    che invii un modulo o navighi.
    """
    from playwright.async_api import async_playwright

    monkeypatch.setattr(sb.sites_audit, "record", lambda *_a, **_kw: None)

    async def esegui():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.route("**/*", lambda route: route.abort())
            await page.set_content(_MODALE_SENZA_NOME)
            entry = _sessione()
            entry["page"] = page
            try:
                coperto = await page.evaluate(
                    "() => document.elementFromPoint("
                    "  ...(([r]) => [r.left + r.width / 2, r.top + r.height / 2])"
                    "  ([document.getElementById('accedi')"
                    "    .getBoundingClientRect()])).className")
                assert "fondale" in coperto      # il clic finirebbe li'
                assert await sb._dismiss_obstructing_overlay(
                    entry, settle=True) is True
                assert await page.evaluate(
                    "() => !document.querySelector('.modale')")
            finally:
                await browser.close()

    asyncio.run(esegui())
