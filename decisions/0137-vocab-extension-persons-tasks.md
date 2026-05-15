---
id: 0137
title: Vocab extension persons + tasks (OBJECTS 17→19)
date: 2026-05-15
status: accepted
area: naming | vocab
related:
  - 0113  # named persons registry (introduces set_persons/find_persons_indices)
  - 0112  # scheduler v2 (introduces recurring tasks)
---

## Context

§2.2 vocab dichiara 17 OBJECTS canonici (`files, dirs, packages,
messages, events, contacts, places, processes, urls, numbers, images,
signatures, texts, proposals, inputs, credentials, entries`). Due
entita' di prima classe non comparivano:

1. **persons** (ADR 0113, 11/5/2026): registro nominale di persone
   enrolled (volti ArcFace, slug case-accent-insensitive). 4 executor
   canonici esistenti (`set_persons, get_persons, find_persons_indices,
   delete_persons`).
2. **tasks** (ADR 0112, scheduler v2): task ricorrenti / promemoria
   / timer schedulati. 6+1 executor builtin runtime (post rename
   `cancel_scheduled_task`→`delete_tasks_scheduled`→`delete_tasks` di
   stamattina): `list_tasks, read_tasks, create_tasks, delete_tasks,
   set_tasks, read_tasks_history`. Distinti da `events` (calendario
   utente) e `processes` (OS).

Bug live: query "quali persone enrolled" → routing fallisce su
`find_persons_indices` invece di `get_persons` perche' `canonical_object("persona")`
ritornava None (no synonym `persona`→`persons`). Stesso pattern per
"task ricorrenti" → senza vocab tasks, la sezione planner
`scheduled_tasks` non viene gated correttamente.

## Decision

`vocab.py`:

1. **OBJECTS** 17 → 19: aggiunti `persons` (19°) e `tasks` (18°).
2. **OBJECT_DEFAULT_MUTATING_VERB**:
   - `persons`: `"set"` (set_persons = enroll)
   - `tasks`: `"create"` (create_tasks)
3. **_OBJECT_TO_SECTIONS**:
   - `persons`: `("photos",)` (compositive con images)
   - `tasks`: `("scheduled_tasks",)`
4. **_OBJECT_SYNONYMS_IT**:
   - persons: persona, persone, enrollato/a/i/e, registrato/a/i/e,
     volto/i, viso/i, enrolled
   - tasks: task, promemoria, timer, ricorrente/i, schedulato/i
5. **_OBJECT_SYNONYMS_EN**:
   - persons: person, persons, people, enrolled, registered, face, faces
   - tasks: task, tasks, reminder, timer, scheduled, recurring

`prefilter._OBJECT_PRIMARY_TOOLS`:
- `persons`: tuple (`get_persons, set_persons, find_persons_indices,
  delete_persons`)
- `tasks`: tuple (`list_tasks, read_tasks, create_tasks, delete_tasks,
  set_tasks, read_tasks_history`)

## Consequences

- Query "quali persone enrolled" → routing `get_persons → final_answer`
  in 2 step ("Le persone enrolled sono 4: Iacopo, Matteo, Roberto,
  Silvia").
- Query "mostrami i task ricorrenti" → `list_tasks → final_answer`.
- Sezione planner `photos.yaml` gated per object=persons (compositivo
  con images: "Matteo al mare").
- Sezione `scheduled_tasks.yaml` gated per object=tasks (rimosso
  `_OPT_IN_SECTIONS = {"scheduled_tasks"}` opt-in — non piu' necessario
  con vocab proper).
- Battery e2e 22/22 PASS (era 19/22 senza vocab persons).

## Notes

- `tasks` come 18° object puro (decisione A in alternativa a `tasks +
  qualifier _scheduled` o fusione con `events`): scelta semantica chiara,
  scheduler tasks sono entita' di prima classe distinte.
- Rinominati i 6 builtin del scheduler v2 al naming canonical §2.2
  (rimosso suffix ridondante `_scheduled`): `schedule_recurring →
  create_tasks`, `list_scheduled_tasks → list_tasks`,
  `delete_tasks_scheduled → delete_tasks`, `show_scheduled_task →
  read_tasks`, `toggle_scheduled_task → set_tasks` (con `fire_now=true`
  opzione per accorpare ex `run_scheduled_task_now`),
  `scheduled_task_history → read_tasks_history`.
- Migration v1 audit log `source_command` lasciato col vecchio nome
  per coerenza storica (audit trail).
