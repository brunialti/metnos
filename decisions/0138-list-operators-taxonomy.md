---
id: 0138
title: Operatori liste — filter_lists set ops + tassonomia (filter_entries / compute_entries)
date: 2026-05-15
status: accepted
area: executor | naming | runtime
related:
  - 0117  # unified image enrichment index (uses entries pipelines)
  - 0127  # vocab _empty qualifier (cross-domain entries)
complements:
  - 0127
---

## Context

Le pipeline ReAct producono frequentemente DUE liste di `entries` nello
stesso turno, da combinare logicamente. Esempi reali:

- «C'e' un evento HLT che si sovrappone con uno MNM nei prossimi 3
  mesi?» → A = filter HLT, B = filter MNM, A ∩temporal B.
- «Quali file sono in entrambe le cartelle X e Y?» → A = list X,
  B = list Y, A ∩ B su `name`.
- «Quali task sono stati lanciati ma non sono finiti?» → A =
  read_tasks_history(state="started"), B = read_tasks_history(state=
  "finished"), A \\ B su `task_id`.

Pre-15/5 il catalog NON aveva un primitivo per set ops bi-list. Il
PLANNER tentava di simulare con `filter_entries` ripetuti dentro un
loop o con string-matching ad hoc → degeneration (loop_break),
risultati falsi (test 4 HLT/MNM ritornava 0 entries con AND simulato
via 2 filter_entries paralleli, perche' la 2ª lista non veniva mai
incrociata).

Stessa lacuna sui filtri MONO-LISTA: `filter_entries` accettava solo
`where_in/where_not_in` (match esatto in lista). NON `starts_with`,
`contains`, `glob`, `regex`. Query «appuntamenti che iniziano con HLT»
→ il PLANNER passava `where_in=["HLT*"]` con wildcard pensando glob,
match esatto restituiva 0.

## Decision

Tassonomia chiusa degli operatori su liste, allineata ai verbi §2.2
canonici per ASSE (filter = riduzione | compute = calcolo) ×
CARDINALITA' (1 lista mono | 2 liste bi):

| Verbo            | Input    | Output    | Esempi op                                  |
|------------------|----------|-----------|--------------------------------------------|
| `filter_entries` | 1 lista  | lista     | where_in, where_starts_with, where_glob   |
| `filter_lists`   | 2 liste  | lista     | intersect, union, difference, symdiff, overlap |
| `compute_entries`| 1 lista  | scalare   | sum, prod, avg, min, max, count, count_distinct |
| `compute_lists`* | 2 liste  | scalare   | jaccard, cosine, distance (riservato futuro) |

(*) `compute_lists` riservato ma NON implementato in 0138 — verra'
introdotto quando emerga necessita' concreta di metriche numeriche
cross-list. La presenza nella tabella documenta la regola di naming
per il futuro.

### filter_entries esteso (commit `ef0c776`)

4 nuovi operatori di stringa case-insensitive applicabili a `where_field`
arbitrario (oltre name/path):
- `where_starts_with: list[str]`
- `where_contains: list[str]`
- `where_glob: list[str]` (fnmatch `*`/`?`)
- `where_regex: list[str]`

Tolleranza wildcard su `where_in`/`where_not_in` (commit `ef0c776`,
ADR 0138 §G): se un valore contiene `*` o `?`, applica `fnmatch.
fnmatchcase` invece di match esatto. Backward compatible.

### filter_lists nuovo executor (commit `8e95fe1`)

Path: `executors/filter_lists/{filter_lists.py, manifest.toml}`.
`~226 LOC` + 13 unit test.

Operazioni (`op` enum):
- `intersect`: A ∩ B su `on_keys`. Dedup automatico.
- `union`: A ∪ B su `on_keys`. Dedup.
- `difference`: A \\ B (entries di A non in B).
- `symdiff`: (A \\ B) ∪ (B \\ A).
- `overlap`: AND TEMPORALE auto-detect start/end/taken_at_iso/mtime/
  fired_at. Shortcut: no on_keys. Per ogni entry di A, cerca in B
  intersezioni temporali → `_overlap_with` field.

Args canonici (manifest):
- `op: str` (enum)
- `on_keys: list[str]` (richiesto per ops ≠ overlap)
- `with_step: int` (parallelo a from_step: indica step→entries_b)

Output: `{ok, op, entries: list[dict], metadata: {count_a,
count_b, count_out, on_keys}}`. Type schema entries `list[dict]`.

### Wiring runtime (commit `4059593`)

`agent_runtime._resolve_from_step` Layer 5: arg `with_step=N` espande
in `entries_b` (parallelo a `from_step=N` → `entries`). Permette al
PLANNER di referenziare 2 step diversi naturalmente nello stesso
tool call:

```python
filter_lists(op="overlap", from_step=A, with_step=B)
```

`_UNIVERSAL_HELPERS` e `_FROM_STEP_HELPERS` aggiornati con
`filter_lists` (universal injection nel pool da step 2+; gating al
primo step §4.2 dove non c'e' nessuna lista precedente).

## Alternatives considered

**(a) Un executor per ogni operazione** (`intersect_entries`,
`union_entries`, `difference_entries`, ...): naming compositivo
violato (verbo `intersect` non in §2.2). Esplosione del pool top-K.
Anti-pattern §7.3 hardcoded.

**(b) Estendere `filter_entries` con `with_step`+`op`**: confonde
1-lista e 2-liste. La firma del manifest perde simmetria con
`compute_entries` (mono). Scelto contro.

**(c) Rinominare l'executor `compute_lists`** (nome originale del
commit `4059593`): Roberto 16:50 «se piu' comprensibile filter_lists,
se poi servono operatori mat su liste compute_lists». Verb-axis
(filter = riduzione di lista a lista; compute = produzione di scalare
da lista). Vincente.

## Consequences

- Query «HLT overlap MNM» risolta in 6 step deterministici (test 4 PASS).
- `compute_entries` resta per aggregati scalari (count, sum, avg).
  Non duplicato §7.2.
- Pipeline pattern documentata nelle rule planner: `calendar.yaml ::
  events_overlap_intersect` mostra il template `read → filter_A →
  filter_B → filter_lists(op=overlap) → final_answer`.
- §2.4 (Convention args `array of string`): valori opachi del dominio
  (summary, label) tollerano wildcard via fnmatch; valori chiusi
  (slug, scope, time canonical) restano match esatto. Documenta
  l'asimmetria emersa dal bug live.
- `_FROM_STEP_HELPERS` (ADR 0135 §C) esteso a 7 entries: filter,
  sort, compute, classify, group, describe + filter_lists. Tutti
  esclusi dal pool al primo step §4.2.

## Notes

- L'arg `overlap_step` di `filter_entries` (ALIAS-SHORTCUT
  introdotto in `4059593`) resta come scorciatoia mono-call quando
  l'utente vuole filtrare+overlap in un solo step. `filter_lists`
  rimane la primitive canonica per bi-list ops puri.
- Il pattern di naming generalizza: futuri operatori cross-list
  (es. metrica di similarita') vanno sotto `compute_lists` non
  `filter_lists` (output scalare, non lista). La tabella sopra e'
  la guida normativa.
