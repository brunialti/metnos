"""Replica dell'ambiente che l'executor ha davvero incontrato (10/9/2026).

Ricostruisce, in locale e con Chromium vero, la CATENA di ostacoli osservata
accedendo a mano a un portale reale con una sonda in sola lettura. Struttura e
geometria, mai testi o marchio di nessuno:

  1. origine del portale: banner di consenso dentro uno **shadow root aperto**;
  2. l'ingresso porta su **un'altra origine**;
  3. la seconda origine ha il **proprio** banner di consenso, in altra lingua;
  4. sopra al modulo, uno strato fisso a tutto schermo con z-index altissimo,
     la cui unica uscita e' uno `span` **muto**, col cursore a mano;
  5. il modulo di accesso;
  6. dopo l'invio, una **pagina-intermezzo**: non si chiude, si attraversa;
  7. l'area riservata, con la sezione dei documenti raggiungibile per nome.

Serve a due cose. Regressione: la catena deve restare attraversabile. Ma
soprattutto **bilancio**: un turno reale si e' piantato per 135 secondi e in
produzione non era osservabile. Qui l'attraversamento ha un tetto di tempo, e
sforarlo e' un fallimento con un nome.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from playwright_sidecar import credential_injection as ci
from playwright_sidecar import session_broker as sb

PORTALE = "https://portale.example.test"
ACCESSO = "https://accesso.example.test"

# Tetto d'attraversamento. Il turno reale che si e' piantato ci ha messo 135 s
# e il broker lo ha ucciso; qui trenta secondi sono gia' un guasto.
TETTO_S = 30.0

_CONSENSO_SHADOW = """
<div id="cmp"></div>
<script>
  const r = document.getElementById('cmp').attachShadow({mode: 'open'});
  r.innerHTML = '<div id="p" style="position:fixed;inset:0;background:#eef;'
    + 'z-index:99"><h2>Quali cookie vuoi?</h2>'
    + '<button>Accetta tutti</button><button>Solo necessari</button></div>';
  for (const b of r.querySelectorAll('button'))
    b.addEventListener('click', () => r.getElementById('p').remove());
</script>
"""

_PORTALE_HOME = f"""<!doctype html><meta charset="utf-8"><body>
  <h1>Portale</h1>
  <a href="{ACCESSO}/entra">Accedi</a>
  {_CONSENSO_SHADOW}
</body>"""

# Seconda origine: consenso proprio (altra lingua), strato promozionale la cui
# unica uscita e' muta, e sotto il modulo.
_ACCESSO_FORM = """<!doctype html><meta charset="utf-8">
<style>
  body { margin: 0; font: 14px sans-serif; }
  .popup-overlay { position: fixed; inset: 0; z-index: 999999;
                   background: rgba(0,0,0,.35); }
  .popup-body { position: absolute; left: 26%; top: 22%;
                width: 47%; height: 50%; background: #fff; }
  .popup-close { position: absolute; right: 17px; top: 17px;
                 width: 24px; height: 32px; cursor: pointer; }
  .consenso { position: fixed; left: 0; right: 0; bottom: 0; height: 30%;
              background: #123; color: #fff; z-index: 500; }
</style>
<body>
  <h1>Sign in</h1>
  <form method="post" action="/intermezzo">
    <label>Username <input name="username" autocomplete="username"></label>
    <label>Password <input type="password" name="password"></label>
    <button type="submit">Sign in</button>
  </form>
  <div class="consenso">
    <h2>What cookies do you want?</h2>
    <button type="button">Accept all</button>
    <button type="button">Only necessary</button>
  </div>
  <div class="popup-overlay">
    <div class="popup-body">
      <h2>Get the app</h2><span class="popup-close"></span>
    </div>
  </div>
  <script>
    for (const b of document.querySelectorAll('.consenso button'))
      b.addEventListener('click', () =>
        document.querySelector('.consenso').remove());
    document.querySelector('.popup-close').addEventListener('click', () =>
      document.querySelector('.popup-overlay').remove());
  </script>
</body>"""

# Non si chiude: si attraversa.
_INTERMEZZO = f"""<!doctype html><meta charset="utf-8"><body>
  <h1>Con l'applicazione fai prima</h1>
  <a href="{ACCESSO}/area">Continua qui</a>
  <a href="{ACCESSO}/area">L'ho gia' scaricata</a>
</body>"""

_AREA = """<!doctype html><meta charset="utf-8"><body>
  <h1>Area riservata</h1>
  <nav>
    <a href="/area">Dashboard</a>
    <a href="/documenti">Movimenti e fatture</a>
    <a href="/servizi">Servizi</a>
    <a href="/esci">Esci</a>
  </nav>
</body>"""


def _pagina(percorso: str) -> str:
    if percorso in ("", "/"):
        return _PORTALE_HOME
    if percorso.startswith("/entra"):
        return _ACCESSO_FORM
    if percorso.startswith("/intermezzo"):
        return _INTERMEZZO
    return _AREA


@pytest.mark.skipif(os.environ.get("METNOS_SITES_SIM") != "1",
                    reason="prova opt-in con Chromium reale")
def test_la_catena_di_ostacoli_resta_attraversabile(monkeypatch) -> None:
    from playwright.async_api import async_playwright

    payload = {"username": "utente-finto", "password": "segreto-finto",
               "credential_origins": [PORTALE, ACCESSO],
               "session_cookie_names": ["finta-sessione"]}
    monkeypatch.setattr(ci, "_load_site_credentials",
                        lambda _d: (payload, "portale.example.test"))
    monkeypatch.setattr(ci.credentials, "fingerprint", lambda _d: "finta")
    monkeypatch.setattr(sb.sites_audit, "record", lambda *_a, **_kw: None)

    async def esegui():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context()

            async def servi(route):
                url = route.request.url
                for origine in (PORTALE, ACCESSO):
                    if not url.startswith(origine):
                        continue
                    percorso = url[len(origine):].split("?")[0]
                    intestazioni = {"Content-Type": "text/html"}
                    # Il cookie di sessione nasce SOLO dall'invio, come su un
                    # sito vero: uno gia' presente sulla pagina di accesso non
                    # proverebbe niente, e il prodotto giustamente lo ignora.
                    if percorso.startswith(("/intermezzo", "/area",
                                            "/documenti")):
                        intestazioni["Set-Cookie"] = (
                            "finta-sessione=ok; Path=/; Secure")
                    await route.fulfill(body=_pagina(percorso),
                                        headers=intestazioni)
                    return
                await route.abort()

            await context.route("**/*", servi)
            page = await context.new_page()
            await page.goto(PORTALE + "/")
            sid = "replica-accesso"
            # Le stesse chiavi che il broker crea aprendo una sessione vera:
            # una sessione finta a meta' fa fallire il codice per la fixture,
            # non per il difetto in prova.
            entry = {"owner": "prova", "domain": "portale.example.test",
                     "label": "", "open_host": "portale.example.test",
                     "entry_url": page.url,
                     "page": page, "context": context, "lock": asyncio.Lock(),
                     "created": time.time(), "last_used": time.time(),
                     "gate_pending": False, "factor_pending": False,
                     "authenticated": False, "web_content_ingested": True,
                     "allowlist": {"portale.example.test",
                                   "accesso.example.test"},
                     "pending_actions": {}, "completed_approvals": {},
                     "approved_actions": set(), "secret_pending": False,
                     "blocked_requests": {}, "reveal_attempts": set(),
                     "action_replans": {}, "goal_flows": {},
                     "task_mandate": None, "owner_user_id": "prova",
                     "credential_mandate": None, "credential_mode": "default",
                     "observed_reason": None}
            monkeypatch.setitem(sb._sessions, sid, entry)
            try:
                inizio = time.monotonic()
                esito = await asyncio.wait_for(
                    sb.op_login(session_id=sid, owner="prova"), TETTO_S)
                durata = time.monotonic() - inizio
                if not esito.get("logged_in"):
                    # La fotografia del guasto sopravvive alla directory
                    # temporanea: senza, si ricomincia da capo ogni volta.
                    conserva = os.environ.get("METNOS_REPLICA_SHOT")
                    if conserva:
                        await page.screenshot(path=conserva)
                        print("stato finale:", await page.evaluate(
                            "() => ({url: location.href,"
                            " strati: document.querySelectorAll("
                            "  '.popup-overlay,.consenso').length,"
                            " testo: document.body.innerText.slice(0, 200)})"))
                assert esito.get("logged_in"), esito
                # Nessuno degli strati deve essere rimasto in piedi.
                assert await page.evaluate(
                    "() => !document.querySelector('.popup-overlay')"
                    " && !document.querySelector('.consenso')")
                assert durata < TETTO_S, f"attraversamento lento: {durata:.1f}s"
            except asyncio.TimeoutError:
                pytest.fail(
                    f"l'attraversamento si e' piantato oltre {TETTO_S:.0f}s: "
                    "e' il guasto osservato in esercizio, ora riproducibile")
            finally:
                sb._sessions.pop(sid, None)
                await browser.close()

    asyncio.run(esegui())
