---
id: 0104
title: Report runtime user-facing i18n compliance (estensione ADR 0092)
date: 2026-05-07
status: accepted
area: runtime, multilang
related:
  - 0092  # prompt-as-data + multilang foundation
  - 0095  # output_format deterministico
  - 0096  # proposals_cleanup
extends:
  - 0092
---


## Context

ADR 0092 ha portato i prompt LLM e i descrittori manifest sotto disciplina
multilang (storage `runtime/prompts/<lang>/<role>.j2`, `[description].<lang>`,
`i18n.sqlite`). Sono pero' restati fuori dal perimetro tre famiglie di
testi user-facing emessi dal RUNTIME (non dagli executor sintetizzati ne'
dai prompt LLM):

1. **`agent_runtime`** — chiusure deterministiche di turno: auto-final dopo
   ok_count, footer elapsed/orario, undo summary, hallucination notice,
   default `truncated_what`.
2. **`runtime/lifecycle_summary`** — markdown del summary aging notturno
   (titoli sezione, header tabelle "Esito"/"Kind"/"Operazione"/"N",
   note `nessun audit recente`, etichette TL;DR).
3. **`runtime/output_format`** — prefisso TL;DR `_Riepilogo: …_`.
4. **`runtime/orchestration`** — health block (`Stato server`, `Carico`,
   `RAM`, `Dischi`, `Servizi`), documents block, entries block (header
   tabella `Processo`, `Top N su M`, `…(altre N omesse)`), risultati di
   cap-expand resume.
5. **`executors/find_images_indices`** — `final_message_hint` di discover
   automatica e di ranking sopra/sotto soglia.

Tutti questi testi venivano scritti con f-string italiana hardcoded,
ignorando `config.DEFAULT_LANG` (env `METNOS_LANG`). Un sistema con
`METNOS_LANG=en` mostrava mix italiano + inglese al confine
runtime↔executor↔report.


## Decision

**Tutti i report runtime user-facing devono passare attraverso
`messages.get(key, **vars)`** che si appoggia a `~/.local/share/metnos/i18n.sqlite`
(vedi `runtime/i18n.py`). Disciplina identica a manifest description e
prompts (ADR 0092): single source of truth con fallback chain
`current_lang → en → it → <missing:CODE>`.

### Chiavi i18n aggiunte (55, IT+EN)

Famiglia `MSG_AUTO_FINAL_*` / `MSG_ELAPSED_TAG` / `MSG_TRUNCATED_DEFAULT_WHAT` /
`MSG_UNDO_AUTO_FINAL` / `MSG_HALLUCINATION_NOTICE` (agent_runtime).
Famiglia `MSG_LIFECYCLE_*` (lifecycle_summary, 21 chiavi: titoli, sezioni,
header tabelle, righe, note).
`MSG_TLDR_PREFIX` (output_format).
Famiglia `MSG_HEALTH_*` / `MSG_DOCS_DISCOVERED` / `MSG_OMITTED_OTHERS*` /
`MSG_TOP_OF` / `MSG_PROCESS_HEADER_NAME` / `MSG_CAP_EXPAND_*` (orchestration).
Famiglia `MSG_IMG_*` (find_images_indices, 6 chiavi: discover/threshold
final_message_hint).

Lingue popolate: IT + EN, `needs_translation=0` per entrambe (lookup hit
diretto, no fallback chain).

### Pattern di chiamata

```python
from messages import get as msg
log.final_message = msg("MSG_UNDO_AUTO_FINAL",
                         executor=target_executor, count=target_count)
```

In moduli che gia' avevano `from messages import get as msg` (es.
`agent_runtime`) si riusa l'alias. Negli altri (`lifecycle_summary`,
`orchestration`, `output_format`, `find_images_indices`) import lazy o
diretto come `_msg` per chiarezza.

### Pattern import per executor

`executors/find_images_indices/find_images_indices.py` aggiunge `_RUNTIME`
al `sys.path` (gia' presente per altri import) e chiama
`from messages import get as _msg`. Manifest re-firmato.

### Determinismo (CLAUDE.md §7.9)

Niente LLM nella catena di rendering: la disciplina `messages.get` e' un
lookup sqlite + `.format(**kwargs)`. La traduzione lazy via daemon
introvertivo (`i18n_translator`, ADR 0092 §10.6.19) resta opt-in: per le
55 chiavi nuove abbiamo provveduto direttamente a IT+EN, niente
`needs_translation=1`.


## Consequences

**Positive**

- Sistema con `METNOS_LANG=en` rende coerentemente in inglese: lifecycle
  summary, footer turno, auto-final, health block, find_images_indices.
- Aggiungere una terza lingua = `metnos-prompts add-language <code>` +
  popolare le 55 chiavi (workflow ADR 0092).
- Niente mix linguistico al confine runtime↔executor↔report.

**Trade-off**

- 55 chiavi addizionali da mantenere in `i18n.sqlite` quando il testo
  cambia: edit via `i18n.set(key, lang, text)` (auto-invalida le altre
  lingue per latest-wins).
- Cost: una lookup sqlite per stringa user-facing. Trascurabile per
  report che girano 1×/turno o 1×/notte.

**Backward compatibility**

Niente shim (CLAUDE.md §7.1). Le f-string italiane hardcoded sono state
rimpiazzate. Tutti i test verdi (691 PASS), 8 fail pre-esistenti
invariati (gallery, http_server dialog, pipeline_smoke topic, users e2e).


## Test

- `tests/runtime/infra/test_lifecycle_summary.py` — 8 PASS.
- `tests/runtime/engine/test_output_format.py` — 17 PASS.
- `tests/runtime/engine/test_orchestration.py` — 17 PASS.
- `tests/runtime/engine/test_auto_final_on_duplicate.py` — 22 PASS.
- Spot-check `METNOS_LANG=en` su lifecycle_summary, output_format,
  orchestration: tutti 3 verdi (Lifecycle Summary EN, _Summary:_ prefix,
  Server status/Load/RAM/Disks/Services).
- Smoke `runtime.smoke --invariants-only`: 55/55 catalog OK.

## Riferimenti

- `runtime/i18n.py` (DB sqlite + fallback chain).
- `runtime/messages.py` (facade `get(key, **kwargs)`).
- ADR 0092 (foundation multilang).
- ADR 0095 (output_format deterministico).
- CLAUDE.md §10.6.19 (multilang norm).
