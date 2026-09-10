"""Una scheda che non si apre non e' un arrivo (10/9/2026).

Turno reale sul portale di un pedaggio: la pagina «movimenti e fatture» ha due
schede, e la ricerca a obiettivo ha cliccato quella giusta — «FATTURE» — ma il
clic non ha aperto niente. La tabella dei movimenti e' rimasta li'. La ricerca
ha contato il clic come un passo, ha dichiarato l'arrivo e ha letto quel che
c'era: i movimenti, spacciati per fatture. Roberto se n'e' accorto guardando la
risposta: «si e' confuso con i movimenti !!!! non e' arrivato alle fatture».

Il difetto era di forma: la prova del contenuto era legata al solo caso della
CONTINUAZIONE (un «mostra altri»), non alla NAVIGAZIONE. Ma una scheda, un
filtro, una fisarmonica cambiano il contenuto senza cambiare l'indirizzo:
restare sullo stesso posto non dice se il clic ha fatto qualcosa.

La regola provata qui: quando il posto non cambia, il clic deve dimostrare di
aver cambiato il CONTENUTO. Se non lo dimostra, non e' un passo (torna nel
budget, pesa sugli sterili) e soprattutto non e' un arrivo — il giro dopo si
prova un'altra strada verso la stessa cosa, invece di leggere la scheda
sbagliata credendo di essere sull'altra.

Struttura e geometria, mai testi o marchio di nessuno.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from playwright_sidecar import session_broker as sb

AREA = "https://area.example.test"

# La scheda «Fatture» e' un pulsante SENZA ascoltatore: cliccarla non fa
# niente, l'indirizzo non cambia e la tabella dei movimenti resta li'. E' cio'
# che la ricerca ha incontrato dal vivo. La strada che funziona esiste ma porta
# un nome piu' lungo, quindi vale MENO della scheda: si prende per prima la
# scheda, e la seconda strada solo se la prima non conta come arrivo. La
# discesa di confidenza e' anche una deriva — che qui non deve fermare la
# ricerca, perche' il passo precedente non e' avvenuto.
_DOCUMENTI = """<!doctype html><meta charset="utf-8"><body>
  <h1>Documenti</h1>
  <nav>
    <button type="button" id="movimenti" class="attiva"
            aria-selected="true">Movimenti</button>
    <button type="button" id="rotta" aria-selected="false">Fatture</button>
  </nav>
  <table><tbody>
    <tr><td>Movimento del 2 settembre</td><td>12,40</td></tr>
    <tr><td>Movimento del 9 settembre</td><td>3,80</td></tr>
  </tbody></table>
  <p><a href="/documenti/fatture">Apri l'elenco completo delle fatture
     del periodo</a></p>
  <script>
    // La scheda si SELEZIONA e fa comparire la sua barra di strumenti - una
    // icona senza testo - ma la tabella sotto resta quella dei movimenti.
    // E' il caso che conta: i CONTROLLI cambiano, quindi la vecchia prova di
    // movimento (il posto piu' i controlli) dichiara progresso; il CONTENUTO
    // no, e l'unica prova che la scheda si sia aperta e' quella.
    document.getElementById('rotta').addEventListener('click', () => {
      document.getElementById('movimenti').setAttribute('aria-selected', 'false');
      document.getElementById('movimenti').classList.remove('attiva');
      document.getElementById('rotta').setAttribute('aria-selected', 'true');
      document.getElementById('rotta').classList.add('attiva');
      if (document.getElementById('scarica')) return;
      const b = document.createElement('button');
      b.id = 'scarica';
      b.type = 'button';
      b.setAttribute('aria-label', 'Scarica');
      document.querySelector('nav').appendChild(b);
    });
  </script>
</body>"""

_FATTURE = """<!doctype html><meta charset="utf-8"><body>
  <h1>Documenti</h1>
  <table><tbody>
    <tr><td>Fattura 2026-002</td><td>30 giugno 2026</td></tr>
    <tr><td>Fattura 2026-001</td><td>31 marzo 2026</td></tr>
  </tbody></table>
</body>"""

TETTO_S = 60.0


def test_un_passo_a_vuoto_torna_nel_budget_e_pesa_sugli_sterili() -> None:
    """La contabilita' del ritiro, senza browser: e' una sola regola.

    Il passo torna nel budget dell'esplorazione — non e' stato speso per
    niente — ma incrementa il tetto degli sterili, perche' nemmeno il nulla
    puo' ripetersi all'infinito.
    """
    flow: dict = {"steps": 2, "sterile": 0}
    sb._ritira_il_passo(flow)
    assert flow == {"steps": 1, "sterile": 1}

    # Il ritiro non scende sotto zero: un passo non contato non si toglie due
    # volte per il fatto di essere stato inutile.
    magro: dict = {"steps": 0, "sterile": 2}
    sb._ritira_il_passo(magro)
    assert magro == {"steps": 0, "sterile": 3}


@pytest.mark.skipif(os.environ.get("METNOS_SITES_SIM") != "1",
                    reason="prova opt-in con Chromium reale")
def test_la_ricerca_non_legge_la_scheda_che_non_si_e_aperta(monkeypatch) -> None:
    from playwright.async_api import async_playwright

    visti: list[str] = []
    monkeypatch.setattr(sb.sites_audit, "record",
                        lambda evento, *a, **kw: visti.append(evento))

    async def esegui():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context()

            async def servi(route):
                url = route.request.url
                if not url.startswith(AREA):
                    await route.abort()
                    return
                percorso = url[len(AREA):].split("?")[0]
                corpo = (_FATTURE if percorso.startswith("/documenti/fatture")
                         else _DOCUMENTI)
                await route.fulfill(body=corpo,
                                    headers={"Content-Type": "text/html"})

            await context.route("**/*", servi)
            page = await context.new_page()
            await page.goto(AREA + "/documenti")
            sid = "scheda-inerte"
            entry = {"owner": "prova", "domain": "area.example.test",
                     "label": "", "open_host": "area.example.test",
                     "entry_url": page.url,
                     "page": page, "context": context, "lock": asyncio.Lock(),
                     "created": time.time(), "last_used": time.time(),
                     "gate_pending": False, "factor_pending": False,
                     "authenticated": False, "web_content_ingested": False,
                     "allowlist": {"area.example.test"},
                     "pending_actions": {}, "completed_approvals": {},
                     "approved_actions": set(), "secret_pending": False,
                     "blocked_requests": {}, "reveal_attempts": set(),
                     "action_replans": {}, "goal_flows": {},
                     "task_mandate": None, "owner_user_id": "prova",
                     "credential_mandate": None, "credential_mode": "default",
                     "observed_reason": None}
            monkeypatch.setitem(sb._sessions, sid, entry)
            try:
                await asyncio.wait_for(_cerca(sid, "le fatture"), TETTO_S)
                testo = await page.locator("body").inner_text()
                assert "Fattura 2026-001" in testo, (
                    f"la ricerca si e' fermata qui: {page.url}\n{testo}")
                assert "goal_facet_unchanged" in visti, (
                    "il clic a vuoto non e' stato riconosciuto: la prova non "
                    "sta misurando il difetto")
            except asyncio.TimeoutError:
                pytest.fail(f"la ricerca non si e' chiusa entro {TETTO_S:.0f}s")
            finally:
                sb._sessions.pop(sid, None)
                await browser.close()

    asyncio.run(esegui())


async def _cerca(sid: str, obiettivo: str) -> None:
    """Guida la ricerca come fa l'executor: un giro, un'eventuale conferma."""
    token = None
    for _ in range(12):
        esito = await sb.op_act(session_id=sid, owner="prova",
                                action=obiettivo, goal_query=obiettivo,
                                approval_token=token)
        token = esito.get("approval_token") if esito.get(
            "approval_required") else None
        if token:
            continue
        if not esito.get("ok"):
            raise AssertionError(f"la ricerca e' fallita: {esito}")
        # La ricerca si chiude con un'osservazione: non c'e' piu' niente da
        # cliccare, o il posto e' quello giusto.
        if esito.get("primitive") == "observe":
            return
    raise AssertionError("la ricerca non si e' chiusa in dodici giri")
