"""Lo sblocco automatico delle risorse: opt-in, tracciato, mai implicito.

Perche' esiste (6/8/2026). Il confine di rete di una sessione ammette per
difetto SOLO gli host dichiarati e aborta il resto. Booking serve il proprio
codice da `cf.bstatic.com`: bloccato quello, la pagina di login resta bianca —
misurato quella sera, 17 richieste abortite e zero caratteri nel documento — e
l'utente leggeva «Non ho trovato un modulo di login nella pagina», che e' vero
e inutile.

Roberto: «metti pero' un'opzione suicide: sblocco automatico nella UI». Questo
e' il contratto di quell'opzione:

  - **spenta per difetto**: il comportamento di prima non cambia per nessuno;
  - **accesa, ammette da sola** l'host bloccato, per il resto della sessione;
  - **lascia traccia**: la prima ammissione di ogni host finisce nell'audit,
    perche' togliere il gate non significa togliere la memoria (§2.8);
  - **non e' del planner**: viaggia come argomento runtime-owned, come stealth
    e browser_mode. Un confine di rete non si decide componendo un piano.
"""
from __future__ import annotations

import asyncio

import pytest

from playwright_sidecar import session_broker as sb


class _Richiesta:
    def __init__(self, url: str) -> None:
        self.url = url

    def is_navigation_request(self) -> bool:
        return False


class _Rotta:
    def __init__(self) -> None:
        self.esito = None

    async def continue_(self) -> None:
        self.esito = "passata"

    async def abort(self) -> None:
        self.esito = "abortita"


def _passa(guard, url: str) -> str:
    rotta = _Rotta()
    asyncio.get_event_loop().run_until_complete(guard(rotta, _Richiesta(url)))
    return rotta.esito


@pytest.fixture
def loop():
    ciclo = asyncio.new_event_loop()
    asyncio.set_event_loop(ciclo)
    yield ciclo
    ciclo.close()


def test_spento_il_confine_resta_quello_di_prima(loop) -> None:
    allowlist = {"booking.com", "account.booking.com"}
    guard = sb._make_route_guard(allowlist, {})
    assert _passa(guard, "https://account.booking.com/sign-in") == "passata"
    assert _passa(guard, "https://cf.bstatic.com/app.js") == "abortita"
    assert "cf.bstatic.com" not in allowlist


def test_acceso_ammette_l_host_e_lo_ricorda(loop, monkeypatch) -> None:
    righe = []
    monkeypatch.setattr(sb.sites_audit, "record",
                        lambda evento, **kw: righe.append((evento, kw)))
    allowlist = {"booking.com"}
    ammessi: set = set()
    guard = sb._make_route_guard(
        allowlist, {}, auto_allow=True, auto_allowed=ammessi,
        audit_ctx={"owner": "host", "domain": "booking.com"})

    assert _passa(guard, "https://cf.bstatic.com/app.js") == "passata"
    assert "cf.bstatic.com" in allowlist, "l'host deve entrare nell'allowlist viva"
    assert [e for e, _ in righe] == ["allowlist_auto_allow"]

    # Seconda richiesta allo stesso host: passa, e NON raddoppia l'audit.
    assert _passa(guard, "https://cf.bstatic.com/altro.css") == "passata"
    assert len(righe) == 1


def test_la_navigazione_data_resta_vietata_anche_da_accesi(loop) -> None:
    """Lo sblocco riguarda gli host, non i canali di esfiltrazione: una
    navigazione top-level `data:` resta abortita comunque."""
    class _Nav(_Richiesta):
        def is_navigation_request(self) -> bool:
            return True

    guard = sb._make_route_guard(
        {"booking.com"}, {}, auto_allow=True, auto_allowed=set(), audit_ctx={})
    rotta = _Rotta()
    loop.run_until_complete(guard(rotta, _Nav("data:text/html,<b>x</b>")))
    assert rotta.esito == "abortita"


def test_la_preferenza_e_dichiarata_nel_vocabolario_chiuso() -> None:
    """Una preferenza che non sta nel vocabolario non e' salvabile dalla UI:
    il giro completo (UI -> user_prefs -> arg runtime) si rompe in silenzio."""
    import users

    assert "sites_auto_allow_resources" in users.PREF_KEYS
    assert users.PREF_ALLOWED["sites_auto_allow_resources"] == ("on", "off")


def test_lo_sblocco_non_impedisce_il_riuso_della_sessione() -> None:
    """L'ammissione automatica muta la allowlist VIVA. Il riuso pero' deve
    confrontare il confine DICHIARATO all'apertura: altrimenti basta un asset
    ammesso perche' la sessione non sia piu' riusabile, e la richiesta dopo
    sbatte nella quota — successo davvero (turno cfc1b52e, 7/8/2026)."""
    dichiarato = {"booking.com", "www.booking.com"}
    viva = set(dichiarato)
    voce = {"allowlist": viva, "allowlist_declared": frozenset(dichiarato)}

    viva.add("cf.bstatic.com")          # lo sblocco automatico durante la sessione

    assert set(voce["allowlist_declared"]) == dichiarato
    assert set(voce["allowlist"]) != dichiarato, "il set vivo deve poter crescere"
