---
id: 0068
title: Recurring tasks — callback registry per chiave string (restart safety)
date: 2026-05-01
status: accepted
area: scheduler
related:
  - 0067  # introvertiva MVP
---

<!-- Iter 2 scheduler: applica lezione F1 da giorgio2/core/scheduler.py
(letto 1/5/2026 sera). Disaccoppia persistenza recurring task da
implementazione della closure callback. -->

## Context

L'iter 1 di `runtime/recurring_tasks.py` (1/5/2026 sera) salvava task user
con (`name`, `schedule`, `query`, `actor`, `channel`, ...). Al boot del
daemon scheduler, `bootstrap_into_scheduler()` ricreava per ogni record
una closure tramite `_make_task_fn(record)` e la registrava in
`scheduler.tasks` dict in-memory.

Lettura del scheduler giorgio2 (lezione F1, vedi
`reference_giorgio2_scheduler_patterns.md`) ha evidenziato la fragility:
se cambia la signature di `_make_task_fn` (es. aggiungo un kwarg
opzionale, ristrutturo i record schema), le closure ricreate al
prossimo boot potrebbero comportarsi diversamente da quelle create
all'iter precedente. La closure NON e' versionata; il record DB si.

Pattern alternativo (giorgio): salvare un `callback_key` string in DB.
Registry `{key → callable}` registrato in code al boot. Al fire,
`dispatch_callback(record.callback_key, record)` risolve dal registry.
Refactor della callback NON tocca il DB; aggiunta nuove callback non
richiede migration.

## Decision

Tre cambiamenti in `runtime/recurring_tasks.py`:

1. **Schema**: aggiunta colonna `callback_key TEXT NOT NULL DEFAULT
   'run_user_query'` a `recurring_tasks`. Migration idempotente in
   `_open()` per DB pre-1/5/2026 sera.

2. **Registry**: `_CALLBACKS: dict[str, callable] = {}`. API:
   - `register_callback(key, fn)` — idempotente.
   - `dispatch_callback(key, record)` — risolve + invoca; KeyError se
     key non registrata (errore esplicito vs silent nothing).

3. **Refactor `_make_task_fn`**: ora wrapper minimale che dispatcha via
   `record["callback_key"]`. La logica vera (run_turn + push canale)
   vive in `_run_user_query_callback(record)` registrata come
   `run_user_query` al import-time.

```python
register_callback("run_user_query", _run_user_query_callback)

def _make_task_fn(record):
    def _fire():
        return dispatch_callback(record.get("callback_key", "run_user_query"), record)
    return _fire
```

## Alternatives considered

* **Closure + version field nel DB**: salvare `callback_version=1` per
  ogni task; al boot rifiutare se version != current. Conserva
  flessibilita' closure ma richiede migration ad ogni cambio. Piu'
  brittle del registry (chiave string e' stabile per definizione).
* **Pickling closure in DB**: anti-pattern ben noto (vedi
  reference_giorgio2 N caveat). Rompe al primo refactor della funzione
  o cambio versione Python. Mai.
* **Eseguire literal Python da DB**: salvare `callback_code = "lambda r:
  ..."` ed `eval`. Security disaster + difficile da auditare. Mai.

## Consequences

* **Restart safety**: refactor della callback `_run_user_query_callback`
  o aggiunta di nuove callback NON richiede modifica DB ne' migration
  task user esistenti.
* **Plugin extensibility**: future callback diverse possono registrarsi
  con altre key (es. `register_callback("ask_parse_broadcast",
  _ask_parse_broadcast_callback)` per pattern shibot S2). I task user
  scelgono la callback via field `callback_key`.
* **Dispatch error esplicito**: KeyError se key non registrata,
  visibile in scheduler.runs.output. No silent failure.
* **Test coverage**: smoke test `test_introvertiva.py` non coperto qui
  (e' su introvertiva); test recurring_tasks resta da scrivere
  (tracked in TODO scheduler iter 2 punto residuo).

## Roadmap residua iter 2 scheduler

Implementati 1/5/2026 sera:
* (1) callback registry per chiave — questa ADR.

Da fare:
* (2) timer one-shot persistente.
* (3) weekdays + expires_at + remaining_runs.
* (4) source_command.
* (5) config JSON dichiarativo per task di sistema (lezione shibot S1).
* (6) ask + parse + condition + broadcast (lezione shibot S2).
* (7) asyncio refactor — RINVIATO.
