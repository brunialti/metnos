---
id: 0074
title: Scheduler — gate "no user activity since last run" sui task age-based
date: 2026-05-04
status: accepted
area: runtime, scheduler
related:
  - 0067  # introvertiva MVP
  - 0068  # recurring tasks callback registry
complements:
  - 0067
  - 0068
---

## Context

I task notturni del scheduler che misurano *inattivita'* di un'entita'
(executor, mnest, proposta) assumono implicitamente che l'utente *abbia
usato* il sistema fra un giro e l'altro. Senza questa assunzione,
"executor X non e' stato usato negli ultimi 30 giorni" diventa privo di
significato: forse Roberto era in vacanza, forse il daemon era spento,
forse nessuno ha proprio interagito con Metnos. Deprecare per inattivita'
in un'osservazione "vuota" e' un artefatto del calendario, non del
comportamento utente.

Task interessati:
- `apply_executor_ager` (`daily@03:30`) — active→deprecated dopo 30g di
  inattivita', deprecated→archived dopo altri 14g.
- `introvertiva_propose` (`daily@05:00`) — analizza mnest/event/turns
  per produrre dedupe/generalize/specialize.
- `introvertiva_apply` (`daily@05:30`) — auto-apply specialize a
  confidenza alta.

## Decision

Aggiungere in `scheduler.py` due helper:

- `_last_user_interaction_ts() -> float | None` — legge l'ultimo `ts_end`
  (fallback `ts_start`) dell'ultima riga del file `~/.local/share/metnos/turns/YYYY-MM-DD.jsonl`
  piu' recente. Ogni turno e' loggato anche se non triggera executor,
  quindi e' una sorgente di verita' affidabile per "quando ha parlato
  l'utente l'ultima volta".

- `_gate_user_activity(task_name) -> dict | None` — confronta
  `last_user_interaction_ts` con il `last_run_at` del task nel DB
  scheduler. Se l'ultima interazione utente precede l'ultima
  esecuzione del task, restituisce un dict-summary con
  `skipped="no_user_activity_since_last_run"`. Altrimenti `None` =
  "il task DEVE girare".

I 3 task lo invocano come prima istruzione del corpo. La prima
esecuzione (`last_run_at` NULL) gira sempre. Failure di lettura del DB o
del jsonl turni → `None` (failsafe, gira).

## Consequences

- **Coerenza con la realta'**: nessun executor deprecato per inattivita'
  durante periodi in cui l'utente non ha usato Metnos.
- **Niente effetto secondario sui task non age-based**: `apply_ager`
  (mnest), `synt_suggest`, recurring user tasks restano fuori dal gate
  perche' la loro semantica non dipende da "tempo di disuso".
- **Metnos in vacanza ≠ Metnos da pulire**. Il sistema preserva il pool
  finche' non ricomincia a essere usato.
- **Failsafe**: se una sorgente del gate non e' disponibile (DB, jsonl,
  conversioni timestamp), il task gira come prima — preferiamo run
  spurio a skip ingiusto.

## References

- `runtime/scheduler.py` (`_last_user_interaction_ts`, `_gate_user_activity`,
  `task_apply_executor_ager`, `task_introvertiva_propose`,
  `task_introvertiva_apply`).
- ADR 0067 (introvertiva MVP).
- ADR 0068 (recurring tasks callback registry).
