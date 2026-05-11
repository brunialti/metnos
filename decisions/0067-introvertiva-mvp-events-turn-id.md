---
id: 0067
title: Introvertiva MVP — events.turn_id refactor + candidates_generalize/specialize/dedupe
date: 2026-05-01
status: accepted
area: introvertiva
related:
  - 0064  # literal paths arg
---

<!-- Apertura del modulo introvertiva. CLAUDE.md non aveva sezione su
introversione (concetto vivo solo nelle memorie progetto). Questa ADR
formalizza il design MVP + il refactor prerequisito sul DB events. -->

## Context

L'introvertiva (cascata di sintesi DA DENTRO il sistema, non triggerata da
query utente) era TOP backlog HIGH dal 30/4/2026 sera ma senza modulo
programmatico. La bonifica fossili 30/4 era stata fatta a mano con audit
log JSONL, da replicare algoritmicamente.

Tre operazioni canoniche definite (`mykleos_synthesis_strategies.md`):
* DEDUPE — ritira/consolida doppioni del catalog/mnest.
* GENERALIZE — promuove pattern ricorrente di catena a executor macro.
* SPECIALIZE — estrae varianti mirate da analisi args ricorrenti.

Roberto 1/5/2026 sera autorizza partire con MVP + refactor minimo events.

## Decision

### Refactor events (prerequisito generalize)

`events` table in `mnest.sqlite`: aggiunta colonna `turn_id TEXT` +
indice partial `idx_events_turn_id WHERE turn_id IS NOT NULL`. Migration
idempotente in `Mnestoma.__init__` con backfill best-effort dal vecchio
slot `reason` (per kind=reinforce, pattern hex 16/32 char).

Schema events post-refactor:
```
id INTEGER PK, mnest_id TEXT, ts TEXT, kind TEXT,
delta REAL, new_state TEXT, reason TEXT, turn_id TEXT
```

INSERT sites kind=reinforce (4) migrati da `reason=turn_id` a
`turn_id=turn_id`. INSERT sites kind=rename/merge/decay/deprecate (4)
restano con `reason=descrizione` (turn_id=NULL — sono manutenzione,
non passi turno).

Backfill 1/5: 79/79 reinforce events ora con turn_id popolato. Top
turn_id ha 3 step ricostruibili.

### Modulo `runtime/introvertiva.py`

Tre funzioni indipendenti, callable da CLI o cron:

* `candidates_dedupe()` — segnala mnest legacy (src/dst non in catalog).
  MVP: solo identificazione. Replay completo della bonifica 30/4 (con
  manifest superseded_by mapping) richiede manifest pervasivo, fase 2.

* `candidates_generalize(min_chain_len=3, min_uses=3,
  min_distinct_intents=2, min_avg_weight=0.5, limit=20)` — clustering
  catene da `~/.local/share/metnos/turns/<YYYY-MM-DD>.jsonl`:
  1. Carica turni, estrae `tuple(chosen_tool for s in steps if tool)`.
  2. Filtra catene `len >= min_chain_len`.
  3. Per ogni catena: conta uses, intent diversity, avg_weight (mean
     weight dei mnest delle transizioni).
  4. Ranking by `score = uses * avg_weight`.

* `candidates_specialize(min_uses=3, min_arg_dominance=0.6,
  only_active_catalog=True, limit=20)` — analisi args ricorrenti:
  1. Per ogni step (tool, raw_args), conta valore di ogni arg (skip
     `from_step` plumbing).
  2. Per ogni (tool, arg): se un valore copre >= min_arg_dominance del
     totale → candidato.
  3. Filtro `only_active_catalog`: scarta tool che non sono piu' nel
     catalog (pulisce noise da fs_write/web_fetch legacy).

Audit log JSONL append-only in `~/.local/share/metnos/introvertiva/
candidates_<op>_<ts>.jsonl`. Reversibile + ispezionabile.

CLI: `python3 introvertiva.py {dedupe|generalize|specialize|all}`.

## Alternatives considered

* **Cluster su events anziche' turns/jsonl**: events ha solo singole
  transizioni (mnest_id), non sequenze. Pre-refactor mancava turn_id;
  post-refactor sarebbe possibile fare `SELECT * FROM events JOIN ...
  ORDER BY turn_id, ts` ma il join e' meno informativo dei turns/jsonl
  (mancano user_query, candidates, expandable_caps). Turns/jsonl resta
  fonte canonica per generalize.
* **Promozione automatica al primo run**: rischio creare macro
  fallaci (bug del PLANNER mascherato da pattern, vedi insight sotto).
  MVP fa SOLO identificazione; promozione richiede smoke replay
  (catena vs macro identico output) + manual review.
* **Dedupe automatico**: senza manifest superseded_by canonico, il
  mapping legacy→corrente e' empirico. Rischio cancellare mnest legittimi.
  MVP fa SOLO segnalazione di mnest con src/dst orphan.

## Consequences

### Insight inattesi al primo run (1/5/2026 sera)

* **Generalize top candidate** = `find_dirs → sort_entries → sort_entries`
  (uses=7, distinct_intents=4, score=4.2). Sequenza con sort_entries
  duplicato consecutivo. NON e' un macro candidato: e' **bug del PLANNER**
  (doppia sort) mascherato da pattern frequente. L'introvertiva qui
  mostra dove il prompt PLANNER va sistemato — `sort_entries(top=K)`
  in 1 step risolve, eliminando il pattern "ridondante".
* **Specialize top candidates** = `actor=host`, `timezone=Europe/Rome`,
  `desc=true` — dominanze naturali (single-user deployment, geo
  configurato), NON specializzazioni utili. Il signal vero da
  capitalizzare: `sort_entries(desc=true) 100%` → cambiare il default
  del manifest da `desc=false` a `desc=true`.

### Implicazioni

* Il MVP e' un OSSERVATORIO del comportamento aggregato del PLANNER, non
  solo strumento di crescita catalog. Trova bug di prompt + dominanze
  naturali oltre a candidati legittimi.
* Future iterazioni: filtri "skip se dominant_value == manifest_default"
  (specialize) e "skip se catena ha pattern X→X consecutivi" (generalize)
  per ridurre noise.
* Refactor events.turn_id e' anche ABILITANTE per future analisi
  cross-turno (es. retry pattern, intent drift).
