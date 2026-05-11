---
id: 0064
title: Literal `paths` argument for path-only entries consumers (atomic case)
date: 2026-05-01
status: accepted
area: executor
related:
  - 0061  # test runner vector matchers
---

<!-- Raffinamento di CLAUDE.md §4.2 (Caso degenere N=1 con literal letterale).
§4.2 prescriveva il comportamento al PLANNER ("DEVI passare valori letterali
come typed list arg"); questa ADR garantisce che il typed list arg ESISTA
sui consumer di entries con record path-only. -->

## Context

CLAUDE.md §4.2 prescrive: per il caso degenere N=1 con literal letterale,
il PLANNER DEVE passare il valore letterale come typed list arg
(es. `paths`, `urls`) inline; NON DEVI usare `from_step=0` o
`from_step=1` quando nessuno step precedente ha prodotto una lista.

Il fix UC17 (planner_facing_schema, 30/4/2026) ha trasformato lo schema
dei consumer di `entries` in modo che il PLANNER veda solo
`from_step: integer ≥ 1` come arg richiesto. Per i consumer con
manifest gia' scritto in stile from_step (es. `get_files_metadata`),
il manifest stesso espone `from_step` required senza alternativa.

UC67 (1/5/2026): query atomica `"metadata del file /tmp/uc_test_a.txt"`.
Nessuno step precedente. Il PLANNER (Gemma 4 26B middle) ha provato
`get_files_metadata(from_step=0, fields=[...])` 3 volte di seguito →
loop_break. Lo schema `from_step.minimum=1` non e' stato rispettato dal
decoding (Gemma ignora `minimum` JSON-Schema), il validate_args ha
correttamente rifiutato `from_step=0`, e il PLANNER non aveva alcun
typed list arg literal con cui formulare la chiamata.

## Decision

I consumer di `entries` il cui SCHEMA DEI RECORD e' path-only (cioe'
`entries: [{path: str}]` senza altre chiavi obbligatorie per record)
DEVONO esporre `paths: list[str]` come arg literal alternativo a
`from_step`. Manifest pattern:

```toml
[args]
type     = "object"
required = []   # nessuno required: PLANNER picka from_step OPPURE paths

[args.properties.from_step]
type        = "integer"
description = "Step precedente che ha prodotto la lista. ALTERNATIVA: usa `paths` per il caso atomico (literal letterale, nessuno step precedente)."
minimum     = 1

[args.properties.paths]
type        = "array"
description = "Lista di path letterali. Usa per caso atomico (utente cita 1+ path nella query). Non combinare con from_step."
items       = { type = "string" }
```

L'`invoke()` accetta entrambi: se `entries` non e' fornito ma `paths`
si', costruisce `entries=[{path:p} for p in paths]`. Validazione
runtime garantisce esattamente uno dei due (entries da from_step
risolto, oppure entries derivato da paths).

Applicato 1/5/2026 a `get_files_metadata` v0.4.0. Eligible per stesso
upgrade (path-only consumers): `get_file_dates`. Non eligible:
`sort_entries` (record schema variabile per `by`), `filter_entries`
(record schema variabile per `where_field`), `compute_entries` (record
schema variabile per `key`), `move_files` (richiede src+dst non solo
path), `describe_entries` (record schema generico).

## Alternatives considered

* **Forzare `from_step` required + insegnare al PLANNER a step1 fittizio**:
  obbliga il PLANNER ad emettere un `find_files(paths=[...])` o simile
  prima del consumer per il caso atomico. Verbose, lento, viola §7.2
  (semplicita'): un'azione utente atomica genera 2 step LLM invece di 1.
* **Estendere `planner_facing_schema` per generare `paths` automaticamente
  dai consumer di entries path-only**: trasforma piu' di una entry del
  manifest, complica la trasformazione, perde leggibilita' del manifest
  (la fonte canonica di "cosa fa il tool"). Esplicitare nel manifest
  e' meglio.
* **Aggiungere `entries` letterale al PLANNER**: il PLANNER avrebbe
  potuto inventare la struttura dict (`entries=[{"path": "/tmp/a.txt"}]`).
  Sotto schema-guided decoding e' stato proprio cio' che si voleva
  evitare con UC17 (ADR planner_facing_schema). Mantenere `entries`
  invisibile al PLANNER e dare un typed list arg leggibile e' coerente.

## Consequences

* `get_files_metadata` regge il caso atomico literal in 1 step (vs
  loop_break in 3 step nel iter 1 della battery 50/UC).
* Il pattern e' replicabile a futuri consumer di entries path-only.
* Manifest restano leggibili al LLM medium (CLAUDE.md §2.5): la
  description del nuovo arg `paths` cita esplicitamente quando usarlo.
* I test manifest esistenti (entries-form) restano verdi (cambio
  additivo, non sottrattivo).
