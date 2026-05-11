---
id: 0097
title: Unified lifecycle summary (no fusion of agers)
date: 2026-05-07
status: accepted
area: runtime, lifecycle, ux
related:
  - 0072  # adaptive re-rank
  - 0095  # output formatter
  - 0096  # proposals cleanup
complements:
  - 0095
  - 0096
---


## Context

Il sistema ha QUATTRO ager indipendenti che girano la notte:

| Task | Schedule | Storage audit | Cosa fa |
|------|----------|---------------|---------|
| `apply_ager` | daily@04:00 | mnests events table | Decay + demote + proto purge |
| `apply_executor_ager` | daily@03:30 | `aging/executor_ager_*.jsonl` | active→deprecated→archived |
| `introvertiva_propose` | daily@05:00 | `introvertiva/candidates_*_<ts>.jsonl` | Cascata DEDUPE/GENERALIZE/SPECIALIZE |
| `introvertiva_apply` | daily@05:30 | `introvertiva/auto_applied_*.jsonl` | Auto-apply specialize alta confidenza |
| `proposals_cleanup` | daily@06:00 | `lifecycle/proposals_cleanup_*.jsonl` (ADR 0096) | Archive + dedup + legacy_orphan |

Roberto, 7/5/2026: chiedeva pros/cons di fonderli in un unico
`lifecycle.py`.

### Pros (a favore della fusione)
- Modello mentale unico ("tutto cio' che invecchia vive qui").
- Audit unico, vista debug consolidata.
- 1 task vs 5, ordering esplicito (oggi implicito nei numerini cron).
- Config centralizzata (`max_age_days`, `retention_days`, `n_snapshots`).

### Cons (contro la fusione)
- Oggi 5 funzioni piccole testabili in isolamento → un mega-modulo da
  ~600 LOC con piu' responsabilita' (viola §7.2 semplicita').
- Storage diversi: SQLite mnestoma + SQLite executor_stats + JSONL
  multipli. La "fusione" e' colocation, non vera unificazione.
- Cost function nativamente diversa: decay esponenziale (mnest peso),
  mtime (file), dominanza (specialize). API comune o e' sottile (no
  guadagno) o spessa (perde specificita').
- Coupling: bug in un decay puo' rompere tutti gli altri. Oggi falliscono
  in isolamento.
- Reversibilita': tornare indietro da una fusione e' refactor; tornare
  indietro da una scelta di NON fondere e' niente.

## Decision

NON fondere. Manteniamo i 5 ager separati. Aggiungiamo solo il livello
mancante: una **vista aggregata read-only** (`lifecycle_summary`) che
legge gli audit esistenti e produce UN report markdown unico.

### Modulo `runtime/lifecycle_summary.py`

API:
- `collect_summary(window_hours=24) -> dict` — legge gli ultimi audit di
  ognuno dei 4 ager (executor_ager, introvertiva_propose,
  introvertiva_apply, proposals_cleanup) nella finestra. Ogni sezione
  ha `path`/`ts`/`data`/`note`.
- `format_summary(summary) -> str` — markdown via `output_format` (ADR 0095).
  TL;DR aggregato + sezione per ogni ager.
- `run_summary(window_hours=24) -> dict` — tutto in uno; persiste in
  `~/.local/share/metnos/lifecycle/lifecycle_summary_<ts>.jsonl`.

CLI: `python -m runtime.lifecycle_summary [--window-hours N] [--json]`.

### Convenzioni leggere condivise

ADR 0096 ora persiste il proprio report (era omesso). Path dedicato:
`~/.local/share/metnos/lifecycle/proposals_cleanup_<ts>.jsonl`. Gli altri
ager mantengono i loro path storici (modificarli rompe consumer
esterni).

### Scheduler

Registrato come `lifecycle_summary` daily@06:30 (dopo proposals_cleanup
06:00). 7° task builtin (era 6°: apply_ager 04:00, synt_suggest 04:30,
introvertiva_propose 05:00, introvertiva_apply 05:30, apply_executor_ager
03:30, proposals_cleanup 06:00).

## Consequences

Positive:
- Vista unificata senza fusione. Costo di lettura: 4 file.
- Roberto puo' chiedere "stato lifecycle" e ottenere una pagina sola.
- Aggiungere nuovi ager (futuri) e' una entry nel `collect_summary` —
  niente refactor della logica.
- ZERO accoppiamento dei moduli ager esistenti.

Open / future:
- Se in futuro emergesse un caso d'uso che richiede transazione atomica
  (es. decay mnest + dedup candidati che li referenziano + archive
  proposal in un'unica transazione consistente), allora fondere — ma oggi
  non c'e' segnale che lo giustifichi.
- Notifica push del summary su Telegram a fine batch — TBD,
  trivialmente cablabile via `send_messages` se Roberto la vuole.
- Voce "lifecycle" nella dashboard admin HTTP — TBD.
