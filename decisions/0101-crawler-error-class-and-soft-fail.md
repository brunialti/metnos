---
id: 0101
title: Crawler error_class deterministico + soft-fail PLANNER (Z.ter/Z.quater)
date: 2026-05-07
status: accepted
area: executor, planner, web
related:
  - 0081  # crawler tier policy
  - 0098  # web crawl parallel + strategy + (Z)/(Z.bis)
  - 0099  # runtime perf (seed-step URL injection)
complements:
  - 0098
---


## Context

Turn live 7/5/2026: l'utente fornisce URL e chiede contenuto. Quando il
sito blocca il crawler (HTTP 403/429), risponde 5xx, va in timeout, o
ritorna risorsa non-HTML, il PLANNER non aveva un modo deterministico
per riconoscere la classe di errore: `failed[]` esponeva solo `error: str`
(es. `"http error 403: Forbidden"`). Il LLM medio sotto pressione cadeva
in due anti-pattern:

1. **Chasing**: rifaceva `find_urls(seed_urls=[...])` o `read_urls_pdf`
   sullo stesso URL gia' fallito, sperando che la discovery laterale
   sbloccasse l'accesso. Non sblocca: 403 e' lato server.
2. **Silent fail**: rispondeva «non ho potuto leggere» senza
   classificare l'errore ne' suggerire alternative concrete (cache
   Google, archive.org, PDF/screenshot manuale).

In parallelo, su pagine servite gzip-compresse da edge cache (Varnish
su python.org), il body decodificato era binario garbage: nessuna
classificazione esplicita, body_text=0 char, no signal di JS-rendering.
Il PLANNER concludeva con messaggi ambigui sul "perche'" la pagina
era illeggibile.


## Decision

Tre interventi concorrenti.

### (a) `error_class` deterministico in `read_urls_html`

`_fetch_one` ritorna `(None, {"error": str, "error_class": str})` invece
di `(None, str)`. La classe e' mappata deterministicamente (no LLM,
§7.9):

| Causa                                 | error_class      |
|---------------------------------------|------------------|
| HTTP 403                              | `forbidden`      |
| HTTP 429                              | `rate_limited`   |
| HTTP 404                              | `not_found`      |
| HTTP 5xx                              | `server_error`   |
| TimeoutError / "timed out"            | `timeout`        |
| Content-Type != text/html             | `non_html`       |
| URLError altro (DNS, conn refused)    | `network`        |
| Detection JS-rendering post-parse     | `js_rendered`    |
| HTTPError altro / parse error / catch | `unknown`        |

`failed[]` propaga sia `error: str` (backward-friendly) sia `error_class`.
Quando `js_rendered=true` su un entry letto con successo ma scheletrico,
l'entry stessa espone `error_class: "js_rendered"` per coerenza.

In aggiunta, `read_urls_html` ora invia `Accept-Encoding: gzip, deflate,
identity` e decomprime body via `Content-Encoding` (Varnish-style edge
cache gzip-pa anche senza header esplicito del client). Risolve il caso
python.org/about (body letto = 0 char prima del fix).

### (b) Regole `(Z.ter)` e `(Z.quater)` nel PLANNER

`runtime/prompts/it/planner.j2`, dopo `(Z.bis)`:

- **(Z.ter) URL FALLITO IN HISTORY**: quando uno step di lettura URL ha
  ritornato `ok=false` o `entries` vuote con `failed[]` non vuoto, il
  PLANNER DEVE formulare `final_answer` onesto che dichiara la
  `error_class` e suggerisce alternativa concreta (cache Google /
  archive.org per `forbidden`/`rate_limited`; retry per `timeout`/`network`;
  `read_urls_pdf` per `non_html`; etc.). NON DEVE rifare lo stesso URL.
- **(Z.quater) URL PARZIALMENTE FALLITO**: quando `entries[]` non vuoto
  E `failed[]` non vuoto, il PLANNER DEVE usare gli entries successful
  per la final_answer e dichiarare esplicitamente quali URL sono falliti
  con la loro `error_class`. NON DEVE rispondere con un NO globale.

Forma prescrittiva DEVI / NON DEVI / OK / ERRORE (§6).

### (c) Test deterministici

`runtime/tests/test_read_urls_html.py` aggiunge 7 test che mockano server
HTTP locale e verificano la mappatura status → error_class:

- `test_failed_403_has_error_class_forbidden`
- `test_failed_429_has_error_class_rate_limited`
- `test_failed_404_has_error_class_not_found`
- `test_failed_5xx_has_error_class_server_error`
- `test_failed_non_html_has_error_class_non_html`
- `test_failed_network_has_error_class_network` (URL `*.invalid`)
- `test_partial_success_keeps_entries_and_failed`

18/18 PASS (11 baseline + 7 nuovi).


## Consequences

**Pro:**
- Soft-fail onesto: l'utente sa SEMPRE perche' la pagina non e' stata
  letta e cosa puo' fare per recuperare. Niente piu' messaggi ambigui.
- Zero LLM nella classificazione (§7.9): regex/match su HTTPError.code.
- Backward-compatible: `failed[].error: str` resta. Aggiunto `error_class`
  accanto. Niente shim (§7.1, dev pre-1.0).
- gzip handling collaterale: pagine cachate via Varnish ora leggibili.
- Convergenza programmatica verificata su 3 query reali: python.org/about
  (read+describe+final, 105s, body 2356 char), Wikipedia/Metis
  (read+read+describe+final, 95s), partial fail example.com+invalid
  (read+final, 40s, classe 404 + network correttamente dichiarate).

**Contro:**
- Vocabolario fisso di 9 classi: nuove cause concrete = nuova entry
  esplicita nella mappa (es. SSL handshake fail oggi cade in `network`,
  potrebbe avere classe propria in futuro).
- Il PLANNER deve ricordarsi di consultare `failed[*].error_class`: se
  ignora, rivuole rifare. Mitigato da regola (Z.ter) prescrittiva.

**Estensioni naturali:**
- Stessa mappatura applicabile a `read_urls_pdf` e `get_urls_text` (oggi
  espongono solo `error: str`). Replica del pattern `_classify_http_error`
  + `_classify_url_error`.
- Auto-degrade tier post-`rate_limited` (TODO ADR 0098): se 429 ricorrente,
  inserire host in `~/.config/metnos/blocked_origins.json`.


## Test

- `runtime/tests/test_read_urls_html.py` 18/18 PASS.
- `runtime/tests/test_prompt_loader.py` 14/14 PASS (planner.j2 byte-valid).
- Regression `pytest runtime/tests/` 663/671 PASS, 8 fail pre-esistenti
  (gallery×3, http_server×3, pipeline_smoke, users_smoke_e2e). ZERO
  nuove regressioni.
- Smoke invariants 55/55 catalog OK.
