# ADR 0168 — Circuit-breaker dello scheduler: task ricorrente che fallisce N volte → notifica owner con scelta

**Date**: 2026-06-02
**Status**: accepted
**Related**: ADR 0112 (scheduler v2 asyncio co-host), ADR 0090 (engine UI dichiarativo / inline keyboard), ADR 0078 (HTTP API admin), §2.8 (no silent failure), §7.3 (universalità), §7.9 (codice deterministico > LLM), §8.6 (no daemon restart during turn)
**Complements**: il "monitor = esecuzione schedulata di query utente multi-azione/multi-dominio" (sessione 2/6): senza circuit-breaker una query schedulata rotta ri-spara all'infinito.

## Context

Analisi (sessione 2/6): per una **query schedulata ricorrente** (il modello del monitor GitHub: `recurring_tasks._run_user_query_callback` → `run_turn` → push canale) NON esisteva alcun meccanismo che rilevasse un fallimento sistematico e fermasse la ripetizione. Tracciati i quattro livelli:

1. **Scheduler** (`scheduler_v2/daemon.py` + `storage.py`): dopo ogni fire registra `last_status`/`last_error`/`total_failures` (cumulativo, mai resettato), ma per un task ricorrente `enabled=0` scattava SOLO all'esaurimento di `remaining_runs` (limite `times`), mai per fallimento. Nessuna colonna `consecutive_failures`, nessun watchdog. **Un task ricorrente che fallisce ri-spara ogni intervallo all'infinito.**
2. **Learning cross-turn** (`turn_feedback.py`): `count_consecutive_errors_for_query` + demote del path fallito esistono, ma leggono `turn_feedback.jsonl` scritto SOLO dal bottone ✓/✗ via HTTP. Un turno schedulato **non ha utente** che preme ✗ → non si attiva.
3. **Within-turn** (ReAct §4.4): `cap_steps`/`cap_same_executor` proteggono il singolo fire, non la ricorrenza.
4. **Escalation Strato 3** (≥3 ✗): guidata dal contatore di feedback (che i turni schedulati non alimentano) e il suo dialog non era consegnabile headless.

L'unica protezione reale era l'**idempotenza** (es. `filter_lists op=delta` per il monitor): rende innocua la ripetizione di una query *funzionante*, ma non aiuta una query che fallisce **sistematicamente** (PAT scaduto, repo rimosso, pipeline malformata).

## Decision

**Circuit-breaker al livello scheduler** (deterministico, universale per OGNI task ricorrente, non solo il monitor) + **notifica con scelta all'owner** che ha creato il task — consegnabile perché il task record ha `channel`+`chat_id` (a differenza del gate headless).

1. **Streak** — nuova colonna `schedule_entries.consecutive_failures` (migrazione additiva idempotente in `storage._migrate_additive`). `record_outcome`: `success` → azzera; `error`/`timeout` → `+1`. Distinta da `total_failures` (cumulativo).

2. **Soglia + disable** — in `daemon._fire_entry`: se il task è `recurring` e `status != success` e `consecutive_failures+1 >= _CIRCUIT_BREAK_AFTER` (default 3, env `METNOS_SCHED_CIRCUIT_BREAK_AFTER`, allineato all'escalation Strato 3) → `disable=True` + invoca il hook `on_circuit_break(entry, error)`. Il daemon resta **channel-agnostico**: il hook è `None` di default (solo log).

3. **Notifica 3-opzioni** — `builtin_callbacks.install_default_callbacks` cabla `scheduler.on_circuit_break = recurring_tasks._notify_circuit_break`, che manda all'owner (telegram + chat_id) l'errore + inline keyboard `[▶️ Continua] [⏸️ Sospendi] [🗑️ Cancella]`. callback_data `sched:<azione>:<entry_name>`.

4. **Dispatch scelta** — `channels/daemon.py::_handle_scheduler_callback` (prefisso `sched:`, accanto a `dlg:`/`promoter:`/`approve:`): `cont`→`client.resume_job` (enable + azzera streak + ricalcola `next_fire_at`); `susp`→`toggle_job(off)` (ripristinabile); `canc`→`cancel_job` + `cancel_user_task` (rimuove la schedulazione). Cross-processo via DB scheduler condiviso (WAL); il daemon HTTP recepisce al prossimo poll (≤60s).

5. **Invariante** — `client.toggle_job(enabled=True)` azzera `consecutive_failures` (abilitare = ripartenza pulita: evita che il breaker riscatti dopo un solo fallimento residuo).

Determinismo §7.9: nessun LLM. §2.8: la notifica espone l'errore reale, non un esito fittizio.

## Consequences

- Una query schedulata rotta si auto-disabilita dopo 3 fallimenti e l'owner decide; niente loop infinito né rumore.
- Test: `scheduler_v2/tests/test_circuit_breaker.py` (10) + `tests/test_scheduler_circuit_break_callback.py` (5).
- File runtime (no re-sign executor); live al restart del daemon (§8.6: non durante un turno utente attivo).
- I 9 test scheduler stale del consolidamento builtin (ADR 0167) sono stati allineati derivandoli dalla fonte di verità (`_BUILTIN_JOBS`), non da liste hardcoded.
