---
id: 0119
title: describe_entries preserva URL/path nel summary (post-process deterministico)
date: 2026-05-09
status: accepted
area: runtime, describe_entries, ux
related:
  - 0118  # llm rerank find_urls (general-purpose)
  - 0095  # output formatter deterministico
---

## Context

Turn live 9/5/2026 16:39 (`00edaa80`): query «cerca su www.atpromaistruzione.it
il file sull'organico di diritto della scuola nella provincia di roma» →
`find_urls` torna 4396 URL, `describe_entries(from_step=N)` riassume 20
entries in prosa pulita raggruppando per «Scuola dell'Infanzia / Primaria
/ Secondaria I/II / ATA». **Ma nessun URL nel summary**. L'utente: «avrebbe
dovuto darmi i link ai documenti trovati».

Causa diretta: il prompt template `describe_entries_by_importance.j2` ha la
regola esplicita **«Niente elenco letterale di subject/url/path»** (gia' in
prompts da release ADR 0095). E' giusto per email/file digest dove URL e
path sono rumore metadati; e' sbagliato per kind=`web_result` dove gli URL
SONO l'informazione primaria.

## Decision

**Post-process deterministico in `describe_entries.py`**: dopo la chiamata
LLM, se le `entries` hanno campo `url` (preferenziale) o `path` E il LLM
non ha citato la maggior parte dei top-5 nel summary, append automatic
di una sezione **Link diretti** (o **Direct links**, **Path**, **Paths**
per i18n) con elenco markdown.

Logica:
1. Estrai URL gia' citati nel summary via regex `https?://[^\s<>"']+`,
   strip trailing punctuation.
2. Conteggio coverage = (n citati / n top-5). Se >= 0.6 → skip (LLM ha
   gia' fatto il lavoro).
3. Altrimenti append max 10 link come `- [title](url)` o `- ` ``path`` ` —
   `name` per file kind.
4. Sanitize: `[`/`]` nei titoli → `(`/`)`, `\n` → space, fallback
   `(no title)` per entries senza titolo/nome.
5. fmt='html' → `<ul><li><a href=...>` con escape & < >.
6. fmt='json' / 'bullet_list' → skip (formati strutturati gestiti
   altrove).

General-purpose: agnostico al `kind`, agnostico al lingua del summary
LLM, no hardcoded URLs/domains. Lavora su qualsiasi entries con campo
url o path. La regola di prompt «niente elenco letterale» resta valida
(LLM produce sintesi pulita) — i link sono **append separato** con
heading dedicato, non mescolati al riassunto.

## Implementation

`runtime/describe_entries.py`:
- `_maybe_append_link_section(text, entries, fmt, kind) -> str`: nuovo
  helper modulo-level. ~80 LOC.
- Costanti `_LINK_SECTION_TITLE` / `_PATHS_SECTION_TITLE` per IT/EN.
  Cap `_MAX_LINKS_APPENDED = 10`.
- Wired in `handle_describe_entries` dopo `call_llm` e PRIMA della nota
  `MSG_DESCRIBE_TRUNCATED`.

`tests/runtime/entries/test_describe_entries_link_section.py`: 10 test
coprono append, skip-on-coverage, file kind path, no url/path,
fmt=json/bullet_list skip, html format, sanitize brackets, empty
entries, cap a 10, nontext input passthrough.

## Consequences

Pro:
- UX: query "cerca/trova/file/documento" ora ritornano LINK direttamente
  nel summary, non solo prosa categorizzata.
- General-purpose: lingua-agnostic (i18n via `DEFAULT_LANG`), kind-
  agnostic (rileva url/path automaticamente), no hardcoded.
- Deterministico: zero LLM extra, zero regex per-paese, append
  trasparente al chiamante.
- Robusto: skip se LLM gia' ha citato (no doppia stampa); skip se
  fmt strutturato; sanitize markdown; cap a 10 link.

Contro:
- +N righe in fondo al summary (max 10 + 1 heading). Trascurabile per
  Telegram (4096 char) e HTTP chat.
- Per kind=email/log_line/generic con url accidentale, il link section
  potrebbe apparire (ma e' raro perche' la heuristic 60% coverage
  funziona da gate).

## Status

`accepted` 2026-05-09. Suite 1118 PASS / 0 FAIL / 1 skip dopo merge.
Wired di default in produzione (no env var disable — comportamento
sempre attivo, governed dalle entries).
