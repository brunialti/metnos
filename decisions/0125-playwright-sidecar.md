---
id: 0125
title: Playwright sidecar per JS-rendering di SPA (Phase 1)
date: 2026-05-12
status: accepted
area: executor
related:
  - 0081  # Web crawler multi-tier
  - 0098  # Web crawl parallel + strategy
  - 0099  # Runtime perf optimizations
  - 0101  # Crawler error_class + soft-fail
extends:
  - 0101
---

## Context

Metnos ha ottimi crawler deterministici (`find_urls` + `read_urls_html` +
`read_urls_pdf`, ADR 0081/0098/0099/0101) ma NON esegue JavaScript. Il
20-30% del web moderno (dashboard, federazioni sportive, intranet, SPA
React/Vue/Next) idrata il contenuto dopo il caricamento dell'HTML, lasciando
allo scraper solo uno scheletro (`<div id="root"></div>` + bundle JS).

`read_urls_html._fetch_one` rileva gia' la SPA tramite tre signal
(testo/HTML ratio < 0.05, `<div id="root">`, `<noscript>` warning,
≥5 script + ratio basso) e marca `error_class="js_rendered"` (ADR 0101).
Oggi il PLANNER reagisce con `final_answer` onesto "la pagina e' una SPA,
non posso leggere": l'utente perde la possibilita' concreta di leggere
siti come `federvolley.it/v3-results.html`, dashboard moderne, intranet
SPA.

Stima effort 2-3 giorni nel TODO HIGH (memory
`metnos_todo_high_phase7_jsrender.md`, 7/5/2026). Phase 1 = sidecar
funzionante + integrazione minima `read_urls_html` + opt-in via arg
`js_render=true` + 1 hint planner + test base (no live browser in CI).

## Decision

Sidecar HTTP locale su porta `8771` (separata da 8770 HTTP API e 8765
pairing), basato su **Playwright headless Chromium**. Architettura
**opt-in**: il default di `read_urls_html` resta unchanged (throughput
preservato per i turn normali); il PLANNER chiede esplicitamente
`js_render=true` quando un primo step ha rilevato `error_class="js_rendered"`.

### Componenti

```
runtime/playwright_sidecar/
    __init__.py
    server.py        # aiohttp, single-browser Chromium, async
    client.py        # urllib sync, fail-loud (§2.8)
    requirements.txt # playwright>=1.40 + aiohttp
    install.sh       # setup manuale (~300MB chromium download)
    README.md        # usage + debugging
systemd/metnos-playwright.service
    # user unit, Restart=always, MemoryMax=1G, NON enable di default
executors/read_urls_html/read_urls_html.py
    # nuovo arg `js_render: bool = False`
    # stage 3 post-fetch: se js_render=True E sidecar UP,
    # entries con error_class=js_rendered + failed con la stessa
    # error_class vengono ri-richieste al sidecar.
    # Telemetria: js_render_count, js_render_attempted,
    # js_render_sidecar_available.
runtime/prompts/{it,en}/planner/sections/web.j2
    # nuova regola (js_rendered_retry) stile §6: DEVI / NON DEVI / OK / ERRORE.
    # Aggiornata (url_failed_soft_fail) per js_rendered: prima retry, poi soft-fail.
```

### Flusso opt-in

```
PLANNER step1: read_urls_html(urls=["https://spa.example/x"])
  → entries[0].error_class = "js_rendered" (SPA detected)
PLANNER step2: read_urls_html(urls=["https://spa.example/x"], js_render=true)
  → executor: client.is_up() → True
  → POST http://127.0.0.1:8771/render {url, wait_ms=2000}
  → sidecar: page.goto + wait + content + inner_text("body")
  → response: {ok=true, body_text, body_html, title, final_url, render_ms}
  → executor: aggiorna entry, drop error_class, set js_rendered_via_sidecar=true
PLANNER step3: describe_entries(from_step=2) → final_answer
```

Se sidecar DOWN: `is_up()` ritorna False (timeout 1s, conservativo), entry
resta col marker `error_class="js_rendered"` invariato + telemetria
`js_render_sidecar_available=false`. Il PLANNER riconosce il segnale e
fa final_answer onesto via la regola aggiornata `url_failed_soft_fail`.

### Determinismo + costi

- **Client + integrazione executor**: completamente deterministici §7.9
  (probe HTTP + dispatch dict). Niente LLM, niente retry implicito.
- **Sidecar (browser)**: non deterministico per natura (JS exec, layout
  asincrono). Isolato dietro HTTP boundary: il caller vede solo
  `{ok, body_text, body_html, ...}`.
- **RAM**: ~200MB idle (browser persistente single-instance §7.4),
  cap MemoryMax=1G nel systemd unit.
- **Latency**: ~500ms-2s per page (browser startup +
  page.goto wait_until=load + wait_ms 2000ms + extraction).
- **Throughput**: deliberatamente non parallelizzato lato sidecar
  (single-instance §7.4). Phase 2 valutera' pool se necessario.

## Alternatives considered

### A) Selenium + Firefox/Chrome
Scartata. Selenium 4 e' meno performante di Playwright in headless,
piu' verboso, e l'ecosistema async aiohttp si integra peggio. Playwright
ha tooling primo-class per `wait_until="load"`, intercept di network,
viewport esplicito.

### B) Server JS-rendering esterno (Splash, Browserless.io)
Scartata. (a) Splash e' Twisted+QtWebKit, deprecato e mal mantenuto;
(b) Browserless.io e' SaaS - viola §10.3 (open-source self-hosted come
default). Soluzione locale e' obbligata.

### C) Niente sidecar, "non leggere SPA" come accept
Scartata. Roberto promosso a TODO HIGH (memory 7/5/2026): la frequenza
di SPA cresce, casi reali concreti (federvolley, dashboard) bloccati.
Il design opt-in mitiga il costo: default off, costi pagati solo quando
serve.

### D) Sidecar always-on con auto-fallback implicito
Scartata. Avrebbe:
- aggiunto ~500ms-2s di latenza ai turn normali ANCHE quando non serve;
- mascherato bug nel detection js_rendered (false positive avrebbero
  rallentato silenziosamente turni leciti);
- forzato installazione Chromium come dipendenza di Metnos (~300MB).
Opt-in esplicito mantiene il sidecar come strumento opzionale.

### E) `pyppeteer` (Puppeteer fork Python)
Scartata. Progetto unmaintained, ultima release 2023. Playwright e'
attivamente mantenuto da Microsoft + community.

### F) Single-instance vs pool di browser/context
Scelto single-instance §7.4. Le pagine SPA sono lente per natura (browser
startup + JS hydration); parallelizzare moltiplicherebbe l'uso RAM senza
necessariamente moltiplicare il throughput utente percepito. Phase 2 valuta
context-pool se vedremo bottleneck su query reali.

## Consequences

### Cosa cambia

- `executors/read_urls_html`: +1 arg (`js_render: bool`), +1 stage post-fetch
  (~90 LOC), +5 campi telemetria opt-in. Re-firmato (§7.10).
- `runtime/playwright_sidecar/`: nuova cartella (~350 LOC).
- `runtime/prompts/{it,en}/planner/sections/web.j2`: +1 regola
  `(js_rendered_retry)` (~22 righe per lingua) + aggiornamento mini di
  `url_failed_soft_fail` su `js_rendered` (prima retry, poi soft-fail).
- `systemd/metnos-playwright.service`: nuova user unit, NON enable di
  default. Roberto attiva manualmente.

### Cosa diventa piu' facile

- Lettura siti SPA (federvolley, intranet moderne, dashboard React/Vue).
- Debug: i marker `js_rendered_via_sidecar`/`js_render_error_class` rendono
  trasparente l'origine del rendering nel turn JSONL.

### Cosa diventa piu' costoso

- ~300MB download di Chromium (eseguito da Roberto on-demand).
- ~200MB RAM per sidecar idle se attivato.
- ~500ms-2s di latency per pagina renderizzata.

### Porte aperte / chiuse

Aperta: Phase 2 — sidecar context-pool (parallelismo controllato), gestione
cookie/auth Playwright (oggi solo via `auth_cookies_file` plain), screenshot
capability per fallback OCR su pagine impossibili.

Chiusa: nessun rollback del default off — il rischio di latenza/RAM su
turn ordinari ha la precedenza sul comfort di "tutto funziona out-of-the-box".

### Work items spawnati

- Phase 2 (deferred): context-pool, screenshot, Playwright auth flow.
- Bench live su federvolley + 5-10 SPA reali (richiesto Roberto sblocca
  il sidecar).
- Eventuale integrazione con `find_urls` (BFS che attraversa pagine
  parzialmente SPA).
- CLAUDE.md §10.6 — voce ADR 0125 quando Roberto promuove sidecar a default.
