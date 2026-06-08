# Playwright sidecar (ADR 0125, Phase 1)

JS-rendering opt-in per `read_urls_html`. Sidecar HTTP locale su porta `8771`
che renderizza pagine SPA (single-page application JavaScript-rendered)
tramite Chromium headless. Senza il sidecar, le SPA come `federvolley.it/v3-results.html`
restano scheletri HTML vuoti per il crawler di Metnos.

## Setup (manuale)

```bash
./install.sh                       # ~300MB download (chromium)
# oppure:
/opt/suprastructure/.venv/bin/python -m pip install playwright>=1.40 aiohttp
/opt/suprastructure/.venv/bin/python -m playwright install chromium
```

## Avvio

Manuale (foreground):
```bash
/opt/suprastructure/.venv/bin/python -m playwright_sidecar.server \
    --host 127.0.0.1 --port 8771
```

Via systemd-user (non enable di default):
```bash
cp /opt/metnos/systemd/metnos-playwright.service \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start metnos-playwright.service
systemctl --user status metnos-playwright.service
```

## Uso da `read_urls_html`

```python
# Senza JS-render (default, throughput normale):
read_urls_html(urls=["https://x.example/spa"])
# → entries[0].error_class == "js_rendered" (SPA detected)

# Con JS-render opt-in (sidecar deve essere UP):
read_urls_html(urls=["https://x.example/spa"], js_render=True)
# → entries[0].body_text popolato dal DOM materializzato
# → entries[0].js_rendered_via_sidecar = True
# → result.js_render_count = 1, js_render_sidecar_available = True
```

Il PLANNER ha la regola `(js_rendered_retry)` in
`runtime/prompts/<lang>/planner/sections/web.j2`: dopo un primo step che
ritorna `error_class="js_rendered"`, ri-invoca `read_urls_html` con
`js_render=true`.

## Endpoint HTTP

`GET /health`
```json
{"ok": true, "browser": "chromium", "version": "120.0.6099.71"}
```

`POST /render`
```json
{"url": "https://x.example/spa", "wait_ms": 2000,
 "viewport": {"w": 1280, "h": 800}}
```
Risposta success:
```json
{"ok": true, "body_text": "...", "body_html": "<html>...</html>",
 "title": "...", "final_url": "...", "render_ms": 1234}
```
Risposta failure (HTTP 200 con `ok=false`):
```json
{"ok": false, "error": "timeout after 30s on goto",
 "error_class": "timeout"}
```

## Debugging

- Sidecar non parte: `journalctl --user -u metnos-playwright.service -n 50`
- Chromium non trovato: `playwright install chromium` (richiede ~300MB).
- Client `is_up()` False ma porta libera: verifica `curl -fsS http://127.0.0.1:8771/health`.
- Pagina sempre timeout: aumenta `wait_ms` (max 15000) o il timeout client.
- RAM crescita: il sidecar mantiene UN browser persistente; restart se >500MB.

## Vincoli (the design guide §7)

- §7.1 no shim: se `playwright` non e' installato, `server.main()` exit 1.
- §7.4 single-instance: un solo browser per processo, no pool. Throughput
  e' deliberatamente limitato (opt-in per pagine SPA, non default).
- §7.9 deterministico: client + integrazione `read_urls_html` sono
  deterministici. Il browser stesso e' isolato dietro HTTP boundary.
- Niente abilitazione automatica: Roberto enable manualmente.
