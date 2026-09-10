"""Un ingresso che non muove niente non si ripete (10/9/2026).

Turno reale `ab0ebb39`: il portale era in manutenzione e ogni clic su «accedi»
riportava alla pagina di manutenzione. Il pilota ne ha fatti **quattro**,
identici — `url_before == url_after` tutte le volte — fino a esaurire il budget
d'ingresso, e solo allora ha dichiarato fallimento.

E' la stessa regola gia' scritta per la ricerca a obiettivo, che qui mancava:
il budget si spende sul progresso. Un clic che non fa comparire nessuna
superficie di accesso E lascia la pagina esattamente com'era non e' avvenuto;
ripeterlo non puo' finire diversamente.

La prova conta i clic, non solo l'esito: un esito giusto raggiunto dopo quattro
tentativi identici sarebbe lo stesso difetto.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from playwright_sidecar import credential_injection as ci
from playwright_sidecar import session_broker as sb

SITO = "https://fermo.example.test"

_HOME = f"""<!doctype html><meta charset="utf-8"><body>
  <h1>Servizi</h1>
  <a href="{SITO}/accedi">Accedi</a>
</body>"""

# La pagina di manutenzione ha a sua volta un ingresso, e porta a se stessa.
_MANUTENZIONE = f"""<!doctype html><meta charset="utf-8"><body>
  <h1>Pagina in manutenzione</h1>
  <a href="{SITO}/accedi">Accedi</a>
</body>"""

TETTO_S = 60.0


@pytest.mark.skipif(os.environ.get("METNOS_SITES_SIM") != "1",
                    reason="prova opt-in con Chromium reale")
def test_il_pilota_smette_quando_la_pagina_non_cambia(monkeypatch) -> None:
    from playwright.async_api import async_playwright

    payload = {"username": "utente-finto", "password": "segreto-finto",
               "credential_origins": [SITO],
               "session_cookie_names": ["finta-sessione"]}
    monkeypatch.setattr(ci, "_load_site_credentials",
                        lambda _d: (payload, "fermo.example.test"))
    monkeypatch.setattr(ci.credentials, "fingerprint", lambda _d: "finta")
    monkeypatch.setattr(sb.sites_audit, "record", lambda *_a, **_kw: None)

    ingressi: list[str] = []

    async def esegui():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context()

            async def servi(route):
                url = route.request.url
                if not url.startswith(SITO):
                    await route.abort()
                    return
                percorso = url[len(SITO):].split("?")[0]
                if percorso.startswith("/accedi"):
                    ingressi.append(percorso)
                    corpo = _MANUTENZIONE
                else:
                    corpo = _HOME
                await route.fulfill(body=corpo,
                                    headers={"Content-Type": "text/html"})

            await context.route("**/*", servi)
            page = await context.new_page()
            await page.goto(SITO + "/")
            sid = "ingresso-fermo"
            entry = {"owner": "prova", "domain": "fermo.example.test",
                     "label": "", "open_host": "fermo.example.test",
                     "entry_url": page.url,
                     "page": page, "context": context, "lock": asyncio.Lock(),
                     "created": time.time(), "last_used": time.time(),
                     "gate_pending": False, "factor_pending": False,
                     "authenticated": False, "web_content_ingested": True,
                     "allowlist": {"fermo.example.test"},
                     "pending_actions": {}, "completed_approvals": {},
                     "approved_actions": set(), "secret_pending": False,
                     "blocked_requests": {}, "reveal_attempts": set(),
                     "action_replans": {}, "goal_flows": {},
                     "task_mandate": None, "owner_user_id": "prova",
                     "credential_mandate": None, "credential_mode": "default",
                     "observed_reason": None}
            monkeypatch.setitem(sb._sessions, sid, entry)
            try:
                esito = await asyncio.wait_for(
                    sb.op_login(session_id=sid, owner="prova"), TETTO_S)
                assert esito.get("logged_in") is not True, esito
                assert esito.get("reason_code") == "login_entry_stalled", esito
                # Il primo clic porta dalla home alla manutenzione: e' un
                # movimento. Il secondo no, e li' si smette. Un terzo sarebbe
                # gia' il difetto.
                assert len(ingressi) <= 2, ingressi
            except asyncio.TimeoutError:
                pytest.fail(f"l'ingresso non si e' chiuso entro {TETTO_S:.0f}s")
            finally:
                sb._sessions.pop(sid, None)
                await browser.close()

    asyncio.run(esegui())
