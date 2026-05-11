---
id: 0109
title: Channel-aware HTML rendering (HTTP chat HTML-mode + Telegram split-safe stack tracking)
date: 2026-05-07
status: accepted
area: runtime, http, channels, telegram, ui
related:
  - 0078  # HTTP API Phase 1
  - 0095  # output formatter deterministico
  - 0102  # thinking-leak scrubber + Z.bis/Z.quater
---

## Context

Bug osservato 7/5/2026 sera. Due problemi reali nel rendering del
`final_message` cross-channel:

1. **HTTP chat** (`runtime/templates/chat.html`) appende il
   `final_message` come `textContent` sul `<div class="msg bot">`,
   quindi `**bold**`, link `[text](url)`, blocchi ``` ``` ``` e tabelle
   markdown `| col1 | col2 |` arrivano all'utente come **testo grezzo
   con asterischi e barre verticali visibili**. Il sanitizer
   `runtime/html_sanitizer.py::to_safe_html` esiste gia' ed e' usato
   da Telegram, ma la pipeline HTTP non lo invoca.
2. **Telegram** ha un hard limit di 4096 char per messaggio. Lo split
   esistente (`channels/telegram_format.py::chunk_html`) gestisce gia'
   chiusura/riapertura di tag aperti al boundary, ma:
   - default `max_len=4096` (zero margine per close+reopen sequence);
   - non preserva attributi (`<a href="...">` riaperto come `<a>` —
     link rotto);
   - se nessun newline e' disponibile entro il boundary, fa hard split
     in mezzo a una parola.

## Decision

Due interventi disgiunti, deterministici (§7.9), uniti dal tema
"channel-aware safe HTML at the boundary".

### Patch 1 — HTTP chat HTML-mode

- `runtime/http_routes_agent.py`: nuovo helper `_safe_final_html(md)`
  che incapsula `to_safe_html` con fallback non-silente (§2.8) su
  eccezione interna (log + return ""). Aggiunge il campo
  `final_message_html` accanto a `final_message` in 4 punti che
  ritornano `web.json_response`/`progress._emit("final", ...)`:
  - `_turn_json` (`/agent/turn` JSON);
  - `_turn_sse` event `final`;
  - cap-pending immediate response JSON;
  - cap-pending immediate response SSE.
- `runtime/templates/chat.html`: la funzione `add(cls, text, meta,
  persist, attachments, galleryInfo, htmlMode)` ora accetta un 7°
  parametro opzionale `htmlMode`. Se `true`, usa `innerHTML` invece
  di `textContent`. La sicurezza poggia interamente su `to_safe_html`
  che escapa server-side `<`, `>`, `&` PRIMA di iniettare la
  whitelist `<b>/<i>/<code>/<pre>/<a>`. Il flag e' persistito
  in `pushHistory` per preservare il rendering al reload.
- `final_message` plain rimane nel payload per backward compat
  (CLI client Rust, scripts curl, metnos-cli).

### Patch 2 — Telegram split-safe HTML

- `runtime/channels/telegram_format.py`:
  - `_DEFAULT_CHUNK = 4000` (era 4096) → ~96 char di margine per close
    + reopen sequence sul boundary (caso peggiore: 4-5 tag annidati con
    `<a href="...">` lungo).
  - `_scan_open_tags` ritorna ora `list[tuple[str, str]]` con
    `(name, attrs)`, dove `attrs` e' la stringa attributo originale
    (incl. spazio iniziale). `_open_tags` riapre con attributi
    identici, preservando `href`, `class` ecc.
  - Boundary fallback: newline preferito, poi space, poi hard split.
  - Tag void (`<br>`, `<hr>`) gia' fuori da `PAIRED_TAGS` → ignorati.
  - `<pre>` lungo internamente lungo: chiuso e riaperto come ogni
    altro paired tag (split valido HTML, contenuto preservato).
- `runtime/channels/telegram.py::send`: rate-limit minimo di 50 ms
  fra chunk consecutivi (Telegram limita ~30 msg/s/bot privato, 50 ms
  = 20 msg/s safe).

## Implementation

### File toccati

- `runtime/http_routes_agent.py` — import `to_safe_html`, helper
  `_safe_final_html`, 4 nuovi `final_message_html` nei response
  payload.
- `runtime/templates/chat.html` — `add()` parametro `htmlMode`,
  branch finale che preferisce `data.final_message_html` se presente,
  regex strip `<i>elapsed: ...</i>` per HTML mode, persistenza
  `htmlMode` in `pushHistory` + `restoreHistory`.
- `runtime/channels/telegram_format.py` — `_TAG_FULL_RE` cattura
  attributi, `_scan_open_tags` ritorna tuple, `_open_tags` riapre con
  attrs, `_DEFAULT_CHUNK=4000`, fallback space-split.
- `runtime/channels/telegram.py` — `time.sleep(0.05)` fra chunks.

### Test (24 nuovi)

- `runtime/tests/test_chunk_html_splitsafe.py` (8 casi): short→1
  chunk, `<b>` aperto close+reopen, `<pre>` lungo splittato dentro,
  4 tag annidati LIFO/FIFO, void tag non in stack, `<a href>` con
  attributi preservati, fallback space-split, max_len rispettato.
- `runtime/tests/test_html_chat_rendering.py` (8 casi):
  `_safe_final_html` con bold/table/empty/None/HTML escape/equivalente
  a `to_safe_html`/link/code-block; smoke import.

Run: `/opt/suprastructure/.venv/bin/python -m pytest
runtime/tests/test_chunk_html_splitsafe.py
runtime/tests/test_html_chat_rendering.py -xvs` — 16/16 PASS.

## Consequences

- **Pro**: HTTP chat ora renderizza gli stessi `<b>`, `<i>`, `<code>`,
  `<pre>`, `<a>` di Telegram (parita' cross-channel). Tabelle markdown
  prodotte da `runtime/output_format` (ADR 0095) appaiono come blocchi
  monospace allineati invece di righe `|...|` grezze. Telegram split
  ora preserva link funzionanti su risposte molto lunghe.
- **Pro**: pattern channel-agnostic riusabile. Future view (voice,
  metnos-cli) possono attingere a `final_message` plain o convertirlo
  con `to_safe_html` a richiesta.
- **Pro**: backward compat CLI client mantenuta — `final_message`
  resta nel payload.
- **Con**: bug XSS lato client se in futuro qualcuno estende
  `to_safe_html` con tag attivi (es. `<img onerror>`). Mitigazione: la
  whitelist e' deliberatamente piccola (`b, i, code, pre, a`), nessun
  tag attivo possibile; tutti gli altri caratteri `<>&` sono
  escapati a entita' all'inizio di `to_safe_html`.
- **Con**: rate-limit Telegram 50 ms aumenta latenza apparente di
  ~50 ms per chunk extra. Su una risposta da 8000 char (2 chunk) =
  +50 ms, accettabile.

## Test

- 16/16 PASS (test_chunk_html_splitsafe + test_html_chat_rendering).
- Regression `pytest runtime/tests/` (esclusi smoke + telegram_pairing)
  741 PASS / 1 FAIL pre-esistente (`test_pipeline_smoke` find_urls
  topic ranking, non correlato).
- `python -m runtime.smoke --invariants-only`: 55/55 catalog OK,
  10 consumer/precursor checks OK.
