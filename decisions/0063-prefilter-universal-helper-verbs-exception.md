---
id: 0063
title: Prefilter — universal-helper verbs eccezione al return-None on empty primary
date: 2026-05-01
status: accepted
area: runtime
related:
  - 0058  # intent extractor LLM-based
---

<!-- Raffinamento di CLAUDE.md §10.6.3 (Prefilter precursor universale per
consumer). 10.6.3 non aveva ADR formale — questa ADR formalizza l'eccezione
universal-helper-verbs in rank_with_intent. -->


## Context

`rank_with_intent(query, catalog, intent)` filtra il catalog per
`name.startswith(verb_)`. Se nessun executor matcha (es. intent
`group/dirs` senza alcun `group_*` in catalog), ritorna `None` e il
caller fa fallback a bag-of-words su tutta la lessicografia (logica
introdotta 30/4 per UC3 "Trova ... raggruppa ...").

Il check si rompe per i universal-helper verbs (`classify`, `filter`,
`sort`, `describe`, `compute`): questi tool sono iniettati in-process
da `agent_runtime.py` (`_UNIVERSAL_HELPERS`) e NON hanno manifest in
`/opt/myclaw/executors/`. Per questo `classify_entries` non appare nel
catalog. Conseguenza: per intent `verb=classify, object=messages` la
funzione ritorna `None` → il prefilter perde la chance di iniettare
`read_messages` come precursor → il caller cade in BoW sull'intera
catena.

Smoke invariant `[invariant precursor] verb=classify object=messages`
(ADR 0042) cattura esattamente questa condizione: ha fallito 2026-05-01.

## Decision

In `rank_with_intent`, definire un set chiuso `_UNIVERSAL_HELPER_VERBS =
("classify", "filter", "sort", "describe", "compute")`. Quando `primary`
e' vuoto E il verb e' in questo set, NON ritornare `None`: procedi al
blocco di precursor injection. Il caller comporra' poi il pacchetto
finale `read_messages + classify_entries` via il path universal-helper
in `agent_runtime`.

Per i verbi consumer ordinari (move, delete, send, write, ...) la
return-None resta intatta: se non c'e' alcun executor con quel verbo
nel catalog, l'intent e' debole e il fallback BoW e' la strategia
corretta.

## Alternatives considered

* **Inserire dummy entries per i universal-helper nel catalog**: viola
  §7.2 (semplicita') e §10.6.4 (no synth ridondanti). I universal helper
  sono part del runtime, non del catalog manifest, by design.
* **Far evolvere lo smoke invariant per skippare i universal-helper
  verbs**: nasconde il problema invece di risolverlo — il prefilter
  resterebbe incapace di iniettare precursor per `classify_messages`,
  e il PLANNER perderebbe `read_messages` dai candidati.
* **Aggiungere `classify`, `filter`, `sort`, `describe`, `compute` a
  `_PRODUCER_VERBS`**: rompe la semantica del set
  (producer = verbo che genera output da nulla, non da una lista).

## Consequences

* Smoke invariants verde: 10 consumer/precursor checks superati.
* Per ogni universal-helper verb il prefilter inietta correttamente
  i precursor producer dell'object, anche se l'helper stesso non vive
  nel catalog.
* L'eccezione e' un set CHIUSO esplicito: nuovi universal-helper
  introdotti in futuro vanno aggiunti sia in `agent_runtime._UNIVERSAL_HELPERS`
  che in `prefilter._UNIVERSAL_HELPER_VERBS`. Da fattorizzare in un'unica
  fonte (futuro refactor).
