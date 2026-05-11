---
id: 0096
title: Proposals cleanup — decay, dedup, legacy auto-decay
date: 2026-05-07
status: accepted
area: runtime, introvertiva, lifecycle
related:
  - 0072  # adaptive re-rank
  - 0076  # synth_request short-circuit
  - 0077  # introvertiva quality filters
complements:
  - 0076
  - 0077
---


## Context

Live state 7/5/2026 (Roberto): «77 proposal su cui decidere sono troppe per
un utente. Si rischia di perdere candidati per overload.»

I 77 file in `~/.local/share/metnos/synt_proposals/` sono in realta' un
mix:
- **Run logs synthesis**: ogni invocazione di `request_new_executor`
  scrive un file con tutti gli stage. Pre-ADR 0076 (4/5), il loop
  ricorrente per `list_processes`/`compress_files_gz`/etc. produceva 5-6
  attempt con stesso `name`. ADR 0076 ha smesso di crearne nuovi, ma
  l'accumulo storico resta.
- **Falliti storici**: stage 5 SyntaxError, stage 3 JSON parse failed.
  Utili per il prossimo audit, ma >30gg sono history fredda.

I 25 file in `~/.local/share/metnos/introvertiva/candidates_*.jsonl`
sono diversi: candidati prodotti da audit ricorrenti su mnestoma. Stessi
candidati ricorrono ad ogni run (ad es. `legacy_orphan: fetch_urls ->
write_files` appare in 6+ file consecutivi perche' il problema persiste
finche' l'utente non agisce).

Caso paradigmatico individuato in audit: mnest `fetch_urls -> write_files`
weight 0.876, uses 49. `fetch_urls` rimosso dal vocabolario il 3/5/2026
(ridotto a `get_urls`). Il mnest e' un riferimento morto. Non e' una
proposta utente da valutare; e' rumore deterministicamente identificabile.

Roberto: «se sono simili le proposte sono uguali. Una funzione di costo
(anche approssimata) sceglie quella piu' performante.»

## Decision

Nuovo modulo `runtime/proposals_cleanup.py`: orchestratore di tre
operazioni deterministiche complementari.

### 1. `archive_aged_synth_proposals(max_age_days=30)`

Sposta da `synt_proposals/` a `synt_proposals/_archived/<YYYY>/<MM>/`:
- proposal con `final_state="synthesized"` AND `name` gia' nel catalog
  attuale → storia ridondante (l'executor c'e', il run log non serve);
- proposal con `mtime` piu' vecchio di `max_age_days` → cold storage.

NIENTE delete. Move atomic. L'archive resta esplorabile.

### 2. `dedupe_introvertiva_candidates(retention_days=7)`

Per ogni `candidates_*.jsonl` recente:
- raggruppa record per `(kind, src_executor, dst_executor, proposed_name)`;
- entro un gruppo, scegli il **vincitore** con cost function:
  `uses` massimo, tiebreak `weight` massimo;
- riscrive il file solo coi vincitori.

File piu' vecchi di `retention_days` archiviati come per le proposal.

### 3. `auto_decay_legacy_orphan_mnests()`

Mnest con `state='active'` ma `src_executor` o `dst_executor` non in
`load_catalog()` → marcati `state='superseded'`. La motivazione viene
scritta in `tags` (lo schema attuale di `mnests` non ha colonne dedicate
`superseded_at`/`reason`). Niente DELETE: per audit trail.

Caso d'uso primario: bonifica mnest con verbi rimossi/rinominati
(`fetch_urls`→`get_urls`, `find_file`→`find_files`, ...). Non richiedono
review umana.

### Cost function — perche' `uses` come metrica

`uses` e' il proxy diretto di "quanto questo pattern viene effettivamente
osservato". Su candidati equivalenti (signature identica), il record con
`uses=49` rappresenta una stima piu' robusta del segnale rispetto a
`uses=10`. Non e' una metrica di costo computazionale ma di evidenza
empirica. Tiebreak su `weight` perche' il decay temporale vi confluisce.

## Operations

CLI standalone: `python runtime/proposals_cleanup.py [--dry-run]`.
Idempotente. Output JSON con `archived`, `deduped_files`,
`removed_records`, `decayed`, `errors`.

Scheduling consigliato: settimanale (`apply_ager` ne sarebbe il vicino
piu' naturale). Integrazione nel scheduler builtin: TBD — questo ADR
introduce solo lo strumento.

### Effetto misurato (7/5/2026 prima applicazione)
- synth_proposals: 77 → 21 (archived 56). Tutti i 6 doppioni
  `compress_files_gz`, gli 11 `move_messages`, etc. nell'archive.
- introvertiva: 18 → 9 file (n=3 snapshot per kind), 108 → 42 record,
  signature uniche 33 → 27. `fetch_urls` ricorrente collassato.
- mnestoma: 1 mnest legacy_orphan auto-decaded (`fetch_urls ->
  write_files` w=0.876).

### Quarta fase: keep_latest_n_per_kind
Aggiunta dopo prima applicazione. Ogni run di
`task_introvertiva_propose` emette UNO snapshot completo dei
candidati attivi: tre snapshot consecutivi bastano per stabilita'/trend
(da 6 file × 3 kind = 18 → 3 × 3 = 9). Snapshot piu' vecchi vanno
in `_archived/`.

### Scheduler integration
Registrato come `proposals_cleanup` in `scheduler.make_default_scheduler`,
schedule `daily@06:00` (dopo apply_ager 04:00, synt_suggest 04:30,
introvertiva_propose 05:00, introvertiva_apply 05:30, apply_executor_ager
03:30). Idempotente: senza materiale stale e' no-op.

### CLI batch review
`runtime/admin/proposals_cli.py` (mod `python -m admin.proposals_cli`):
- `summary` — overview compatto: count per stato/kind + executor con piu'
  proposal + top signature per `uses`.
- `list-synth [--state synthesized|abandoned|failed]` — lista proposal
  synth attive.
- `list-candidates [--kind legacy_orphan|generalize|specialize|dedupe]`.
- `show-synth <id>` — dettaglio JSON.
- `cleanup [--dry-run]` — esegue ADR 0096 cleanup esplicitamente.

Output via `output_format` (ADR 0095): tabelle markdown deterministiche.

## Consequences

Positive:
- Backlog review utente ridotto da O(N runs) a O(distinct signatures).
- Eliminazione automatica dei dead reference (no review utile).
- Audit trail preservato (archive non-distruttivo).
- 4 punti del feedback Roberto coperti: (a) auto-promote per casi
  inequivocabili (legacy_orphan); (b) batch dedup riduce coda;
  (c) decay; (d) cost function (uses) per la scelta del vincitore.

Open:
- Integrazione automatica nel scheduler (oggi e' manuale).
- `apply_ager` di mnestoma e questo modulo NON sono coordinati: il primo
  decade per inattivita', il secondo per orphan. Possibile unificazione
  futura sotto un unico `lifecycle.py`.
- CLI di review batch interattivo (per i candidati che restano dopo
  dedup) — nice-to-have, non bloccante.
- Per `synt_proposals`, gen-time dedup (skip write se proposal recente
  con stesso `name` esiste) e' tecnicamente ortogonale: ADR 0076
  short-circuit gia' previene la duplicazione per executor in catalog;
  per executor non-in-catalog il write resta utile (audit trail).
