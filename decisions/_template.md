---
id: NNNN
title: Short but expressive title
date: YYYY-MM-DD
status: proposed | accepted | superseded | deprecated
area: runtime | executor | naming | undo | messages | synt | ...
related:
  - NNNN  # related ADRs (loose connection, same area)
complements:
  - NNNN  # ADRs ESTESI da questa: aggiungono dettagli senza invalidare
modifies:
  - NNNN  # ADRs MODIFICATI da questa: una decisione e' cambiata, ma il
          #   resto della ADR originale resta valido. Lascia status di
          #   quella ADR a 'accepted' e nota nel suo testo "modificata da NNNN"
supersedes:
  - NNNN  # ADRs SUPERATI completamente: la nuova decisione rimpiazza
          #   l'intera ADR originale. Cambia lo status di quella ADR a
          #   'superseded' e cita questa come successore
---

<!-- Convenzione tracciamento revisioni:
- complements:  l'ADR target resta valido al 100%, questa aggiunge dettagli
- modifies:     l'ADR target resta valido salvo i punti citati qui — annota
                  "modificata da NNNN" nell'ADR target
- supersedes:   l'ADR target e' obsoleto — cambia il suo status a 'superseded'
                  e linka a questa ADR -->


## Context

What was the state of the project when the decision had to be made? What
problem surfaced, and why did we have to pick now rather than later? Cite
concrete numbers (executors in the pool, green tests, Telegram runs, latency).

## Decision

What we chose, in prose. Code lines (`runtime/foo.py:42`), module names, and
new fields belong here, but the *why* comes before the *how*.

## Alternatives considered

One per option, brief. Real pros and cons. Don't write strawman alternatives:
write the ones that might re-open the question months later.

## Consequences

What changes in the code and in the way of working. What becomes easier, what
becomes more expensive, which doors close and which open. Any work items
spawned.
