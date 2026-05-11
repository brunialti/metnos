---
id: 0076
title: synth_request — pre-call short-circuit per executor esistente o alias canonico (anti list_processes loop)
date: 2026-05-04
status: accepted
area: runtime, synt
related:
  - 0045  # closed naming vocabulary
  - 0058  # intent extractor LLM-based
  - 0066  # synth executors in user data dir
  - 0072  # adaptive re-ranking intra-turn
  - 0075  # prefilter primary tools per object
complements:
  - 0066
  - 0075
---

## Context

Caso live ricorrente (turn log 1/5–4/5/2026): il PLANNER chiamava
`request_new_executor(expected_name="list_processes", intent="...")` per
query come "Quante istanze di claude sono running". Ma:

- L'executor canonico per snapshot di stato e' `get_processes` (handcrafted,
  `/opt/myclaw/executors/get_processes/`).
- CLAUDE.md §2.2 e `vocab.py` prescrivono `get` per snapshot di stato del
  sistema, non `list` (`list` = container enumeration without content).
- `request_new_executor` lanciava una cascata synt da 5 stadi wise tier
  (Gemma 4 26B think=true, ~150 s wall) per RIGENERARE un executor che
  esisteva gia' come handcrafted, oppure per generare un duplicato dal
  nome diverso (`list_processes`) che avrebbe poi richiesto sweep di
  pulizia.

Il fix di prefilter (ADR 0075) ha attenuato il problema (`get_processes`
ora compare nel pool) ma non l'ha eliminato: il PLANNER puo' comunque
scegliere di chiamare `request_new_executor` con un `expected_name`
sbagliato (specialmente se la psicologia del modello accentua `list` su
"elenca/enumera" anche quando snapshot e' la semantica corretta).

Serve un fix DEFINITIVO **runtime-side**, indipendente dalla scelta del
ranker o dal prompt, che cattura la richiesta sbagliata prima che parta
la cascata synt.

## Decision

`runtime/synth_request.py::handle_synth_request` esegue **due short-circuit
deterministici** prima di invocare `multistage_run_full`:

### 1. `already_in_catalog`

Se `expected_name in catalog.executors`, ritorna immediatamente:

```json
{
  "ok": true,
  "synthesized": false,
  "already_in_catalog": true,
  "name": "<expected_name>",
  "expected_name": "<expected_name>",
  "message": "Executor `<name>` esiste gia' nel catalog. NON DEVI rifare la sintesi. CHIAMA `<name>` al prossimo step con gli args appropriati."
}
```

### 2. `redirected` (canonical alias)

Se `expected_name` non esiste ma esiste un alias `<producer_verb>_<object>[_qualifier]`
per lo stesso object con verbo producer diverso, ritorna immediatamente:

```json
{
  "ok": true,
  "synthesized": false,
  "redirected": true,
  "name": "<canonical>",
  "expected_name": "<expected_name>",
  "message": "L'executor canonico per questo intent e' `<canonical>`, non `<expected_name>`. NON DEVI rifare la sintesi. CHIAMA `<canonical>` al prossimo step."
}
```

Helper deterministico: `_find_canonical_alias(expected_name, catalog)`:
- Parsa `expected_name` come `verb_object[_qualifier]`.
- Se `verb` non e' producer (`get`/`find`/`read`/`list`), ritorna `None` —
  per non-producer (move/delete/send/write/compute/change/...) la
  richiesta e' azione, non lookup: nessun alias.
- Se `obj` non e' in `vocab.OBJECTS`, ritorna `None`.
- Per ogni producer verb diverso, prova candidate `pv_obj[_qualifier]`,
  poi fallback `pv_obj` senza qualifier. Primo match in catalog vince.

## Consequences

- **Latenza risparmiata**: ~150 s per chiamata short-circuited.
- **Zero duplicati**: nessun synth file rigenerato per nomi gia' esistenti
  o aliasable.
- **Nessuna dipendenza da LLM**: il fix e' deterministico (catalog
  membership + parsing). Coerente con la direttiva "codice deterministico
  > LLM se equipotente, equiefficace o se codice deterministico [sarebbe]
  troppo complesso" (4/5/2026).
- **Catch del caso `expected_name` mai visto ma alias-of**: anche se il
  prompt PLANNER non ha guida esplicita "get vs list", il fix runtime
  cattura il fallout. Defense in depth.
- **Test verificati 4/5/2026**:
  - `list_processes` → redirect to `get_processes`.
  - `find_processes` → redirect to `get_processes`.
  - `read_processes` → redirect to `get_processes`.
  - `list_messages` → redirect to `read_messages`.
  - `move_processes` → no redirect (non-producer verb, va a synt).
  - `compute_files` → already_in_catalog.
  - `get_processes` → already_in_catalog.

## References

- `runtime/synth_request.py` (`_find_canonical_alias`, `handle_synth_request`).
- `runtime/vocab.py` (`PRODUCER_VERBS`, `ACTIONS`, `OBJECTS`).
- ADR 0075 (prefilter primary tools — fix complementare).
- Memory `metnos_synt_spurious_root_cause_4may.md` (analisi causa root).
