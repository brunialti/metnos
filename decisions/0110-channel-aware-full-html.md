---
id: 0110
title: Channel-aware full HTML rendering — to_safe_html_full per HTTP browser, to_safe_html (subset Telegram) invariato
date: 2026-05-07
status: accepted
area: runtime, http, channels, telegram, ui
related:
  - 0078  # HTTP API Phase 1
  - 0095  # output formatter deterministico
complements:
  - 0109  # channel-aware HTML rendering (HTTP chat HTML-mode + Telegram split)
---

## Context

ADR 0109 ha portato HTTP chat e Telegram alla parita' minima sul
rendering: entrambi i canali ora ricevono HTML safe via
`runtime/html_sanitizer.py::to_safe_html`, prodotto sul subset
Telegram (`<b>`, `<i>`, `<code>`, `<pre>`, `<a>`). Le tabelle markdown
prodotte da `runtime/output_format` (ADR 0095) appaiono come blocchi
`<pre>` monospace su entrambi i canali.

Limite emerso: HTTP chat e' un browser, supporta nativamente HTML
completo (`<table>`, `<h2>`, `<ul>`, `<blockquote>`, `<hr>`).
Renderizzare le tabelle Metnos come `<table>` veri con `<thead>` e
`<tbody>` e' significativamente piu' leggibile del `<pre>` monospace,
specie quando i record hanno colonne di larghezza variabile (top-N
processi, lifecycle summary, entries dei find_*). Telegram NON
supporta `<table>`, quindi un singolo formato comune significa
livellare al minimo comune denominatore — spreco sul canale browser.

Decisione di Roberto: due funzioni distinte, dispatch per canale.
HTTP browser deve usare HTML pieno; Telegram resta sul subset.

## Decision

Aggiunta funzione `to_safe_html_full(md)` in `runtime/html_sanitizer.py`
accanto a `to_safe_html`. Nessun shim, nessuna sostituzione: le due
funzioni co-esistono. Whitelist tag emessi (estesa rispetto al subset
Telegram di ADR 0109):

```
b, strong, i, em, u, code, pre, a,
h1, h2, h3, h4, h5, h6,
ul, ol, li, blockquote, hr,
table, thead, tbody, tr, th, td,
p, br
```

Mapping markdown → HTML pieno:
- heading `# ## ###` (1-6) → `<h1>` ... `<h6>` (livello = numero `#`);
- hr `---` su riga sola → `<hr>`;
- blockquote `> ...` (consecutivi merged) → `<blockquote>...</blockquote>`;
- bullet list `*` o `-` (consecutivi merged) → `<ul><li>...</li></ul>`;
- numbered list `1. 2. ...` (consecutivi merged) → `<ol><li>...</li></ol>`;
- table markdown → `<table><thead><tr><th>...</th></tr></thead><tbody><tr><td>...</td></tr></tbody></table>`
  con allineamento da separator (`:---` left default, `---:` right,
  `:---:` center) reso come `style="text-align:..."` su `<th>/<td>`;
- code block triple backtick → `<pre><code>...</code></pre>`;
- testo plain → `<p>...</p>` (paragrafi separati da blank line).

Inline su righe non-codice (identico a `to_safe_html`):
`**bold**`/`__bold__` → `<b>`, `_italic_` (boundary word) → `<i>`,
`` `code` `` → `<code>`, `[text](url)` → `<a href="url">text</a>`.

Sicurezza identica a `to_safe_html`: `<`, `>`, `&` escapati a entita'
PRIMA di iniettare qualunque tag; nessun tag attivo nella whitelist
(no `<script>`, `<iframe>`, `<img>`, `<style>`, `<form>`, `<input>`,
attributi `on*`/`style` arbitrari).

Determinismo §7.9: parser regex + minor state machine line-based, no
LLM. Niente librerie markdown esterne (§7.2 semplicita'): ~250 LOC
inline nel modulo.

### Matrice canale ↔ funzione

| canale          | funzione di rendering          | uso                     |
|-----------------|--------------------------------|-------------------------|
| Telegram bot    | `to_safe_html` (subset)        | parse_mode=HTML         |
| HTTP chat       | `to_safe_html_full` (browser)  | `final_message_html`    |
| metnos-cli      | `final_message` plain          | textContent             |
| voce (futuro)   | `to_plain_text` o adapter      | TTS                     |

### Implementation

**File toccati**:
- `runtime/html_sanitizer.py` — nuova funzione `to_safe_html_full` +
  helper privati (`_md_tables_to_html`, `_apply_inline`, regex per
  heading/bullet/numbered/blockquote/hr/table-line/codefence). Niente
  modifica a `to_safe_html` esistente (resta invariato per Telegram).
- `runtime/http_routes_agent.py` — `_safe_final_html` ora chiama
  `to_safe_html_full` invece di `to_safe_html`. Fallback non-silente
  (§2.8) su eccezione: log warning + `html.escape(md)` come ultima
  difesa (mai HTML iniettabile).
- `runtime/channels/telegram_format.py` — INVARIATO. Continua ad
  usare `to_safe_html` via `format_for_telegram`.

**Caso speciale blockquote**: il marker `>` viene escapato a `&gt;`
dal passaggio iniziale `_html.escape(s, quote=False)`. Il regex
`_BLOCKQUOTE_RE` matcha quindi `^&gt;\s+(.+)$`, NON `^>\s+...`.

**Placeholder per blocchi pre-processati**: tabelle e code block
vengono trasformati prima del loop line-based e marcati con
placeholder ASCII NUL (`\x00CODEBLOCK<idx>\x00`, `\x00TABLE<idx>\x00`)
per non essere wrappati in `<p>` dal loop. Restore alla fine.

## Alternatives considered

**(a) Una sola funzione, downgrade per Telegram**: produrre HTML pieno
e poi mappare `<table>` → `<pre>` solo per Telegram. Rifiutata: il
downgrade richiede un parser HTML→HTML (regex fragili o lib esterna),
piu' complesso del fork esplicito. Inoltre il bug XSS si raddoppia
(due output da validare).

**(b) Libreria markdown esterna** (`markdown`, `mistune`,
`markdown-it-py`): aggiunge dipendenza ~30k LOC, output non
controllato (potenzialmente tag fuori whitelist), config sanitization
da costruire comunque. Rifiutata per §7.2 (semplicita') e §7.9
(determinismo: il nostro parser e' line-based, leggibile, testabile).

**(c) Rendering client-side** (markdown.js nel browser): il browser
fa il parse, il server invia markdown grezzo. Rifiutata: sposta la
sanitization sul client (rischio XSS via DOMPurify config errato),
rompe la simmetria con Telegram (server-side anyway), aumenta il
JS bundle del template chat.html.

**(d) Stessa funzione con parametro `mode={"telegram","browser"}`**:
collassa due funzioni in una con branch interno. Rifiutata: la firma
e i call site sono piu' chiari con due funzioni esplicite; il branch
interno aumenta la complessita' ciclomatica della singola funzione e
diluisce la responsabilita' (Telegram-subset e browser-full hanno
whitelist diverse, mapping diverso, paragrafo handling diverso).

## Consequences

**Pro**:
- HTTP chat ora rende tabelle Metnos (ADR 0095) come `<table>` veri
  con `<thead>/<tbody>` e allineamento. Lifecycle summary, top-N
  processi, entries find_* appaiono come tabelle leggibili.
- Heading multilivello (`# ##`) producono `<h1>`/`<h2>` reali; il
  CSS del browser puo' modulare la dimensione (al posto del solo
  `<b>` del subset Telegram).
- Liste markdown (bullet e numbered) rese come `<ul>`/`<ol>` nativi.
- Telegram pipeline INVARIATA: zero rischio regressione su Telegram.

**Con**:
- Due percorsi di rendering = due test suite (mitigato: 28 test
  `test_to_safe_html_full.py` + 9 test `test_html_chat_rendering.py`
  aggiornati).
- Estensione futura (es. nuovo tag come `<details>`) richiede
  decisione su ENTRAMBE le funzioni se serve cross-channel.
- Whitelist piu' ampia su HTTP = superficie XSS leggermente maggiore;
  mitigato dal fatto che la whitelist e' chiusa e nessun tag attivo
  e' incluso (no `<script>`, no event handlers, no `<iframe>`,
  `style` attribute solo `text-align` controllato dal nostro codice).

**Lavori derivati**:
- (Opt-in) `to_safe_html_full` riutilizzabile su future view
  (admin/proposals HTML, dashboards) che oggi escapano manualmente.
- Possibile rimozione di `_md_tables_to_pre` in `to_safe_html` se
  Telegram in futuro supportasse `<table>` (improbabile).

## Test

Suite nuova `runtime/tests/test_to_safe_html_full.py`: 28 casi
deterministici copertura: empty/None, table (con allineamento),
heading h1/h2/h3 + heading-with-inline, bullet `*` e `-`, numbered,
blockquote single + multi-merged, hr, paragrafi (singolo + bold-in-p
+ due paragrafi), code block triple backtick (con e senza lang
hint), inline (code/link/italic/snake_case-preserve), security
(`<script>`, `<img onerror>`, `&` escape), combined heading + list
+ table.

Suite aggiornata `runtime/tests/test_html_chat_rendering.py`: 9 casi
allineati su `to_safe_html_full` (table → `<table>` invece di `<pre>`,
nuovo test `heading_renders_as_h1`, code block ora `<pre><code>`).

Run: `pytest runtime/tests/test_to_safe_html_full.py
runtime/tests/test_html_chat_rendering.py -xvs` → 37/37 PASS.

Regression `pytest runtime/tests/` (esclusi smoke + telegram_pairing):
770 PASS / 1 FAIL pre-esistente (`test_pipeline_smoke` find_urls
topic ranking, gia' noto da ADR 0109, non correlato).

`python -m runtime.smoke --invariants-only`: 55/55 catalog OK,
10 consumer/precursor checks OK.
