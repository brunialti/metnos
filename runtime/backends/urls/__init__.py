"""runtime/backends/web — builtin web/HTTP backends.

Architettura (decisione 13/5/2026, Q1 canonical+args):
- Ogni file `<channel>_<provider>.py` espone le funzioni dei verbi web
  (read_html/read_pdf/find/login) per UN provider builtin.
- Gli executor `read_urls_html.py`/`read_urls_pdf.py`/`find_urls.py`/
  `login_session.py` dispatchano esplicitamente via `_HANDLERS` table
  cablato (no registry magico, no decorator).

Backend builtin disponibili:
- `httpx_default`: HTTP via stdlib urllib + helper centralizzati
  (`host_throttle`, `host_health`, `http_cache`). Niente JS-render.
- `playwright_stub`: stub esplicito per JS-render via sidecar Playwright.
  Lo stub ritorna error_class="not_implemented" finche' il sidecar non
  viene avviato esplicitamente (`runtime/playwright_sidecar/`).

Predisposizione plugin esterni (ADR pending):
- Quando si aggiungeranno plugin esterni in
  `~/.local/share/metnos/plugins/web-*/`, il dispatcher dell'executor
  arricchira' `_HANDLERS` via plugin loader (scan dei plugin manifest +
  import dinamico + trust gate). NIENTE registry magico ora; il pattern
  resta semplice e Pythonico.
- Vincoli previsti per il loader plugin:
  1. Manifest dichiara contract_version + (verb, channel, client) tuples
  2. Trust gate: enabled=true + consent_token in cred store
  3. Precedenza: builtin > plugin (no override builtin senza force flag)

Funzioni per backend:
- `read_html(args: dict) -> dict`: fetch+parse pagine HTML (vettoriale).
- `read_pdf(args: dict) -> dict`: fetch+parse PDF (vettoriale).
- `find(args: dict) -> dict`: discovery+ranking URL (BFS multi-tier).
- `login(args: dict) -> dict`: login form web + persist cookie jar.

Contratto comune di ritorno: dict con `ok: bool` + campi verbo-specifici
(entries / results / failed / ok_count / fail_count / ...). Soft-fail
per-URL tramite `failed=[{url, error, error_class}]` con error_class
deterministica (ADR 0101): forbidden|rate_limited|not_found|server_error|
timeout|non_html|network|js_rendered|unknown.
"""
