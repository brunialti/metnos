---
id: 0062
title: Sort_entries — top user-richiesto è truncation oggettiva con flag intentional
date: 2026-05-01
status: accepted
area: executor
related: []
---

<!-- Raffinamento del principio 2.7 della CLAUDE.md (truncation visibility
cross-executor). 2.7 non aveva ADR formale — questa ADR introduce
`truncated_intentional` per separare visibility (fatto) da notify policy
(scelta UX). -->


## Context

Principio 2.7 (truncation visibility cross-executor): ogni executor con
cap di output che taglia un set di risultati DEVE esporre `truncated:true`
+ `truncated_what` + `used` + `available_total`, e il runtime prepende
notice all'utente.

Il 2026-04-28 una decisione inline in `executors/sort_entries/sort_entries.py`
aveva escluso il caso `top` user-richiesto dalla regola: il commento
diceva "`top` e' una richiesta esplicita dell'utente, NON una saturazione
involontaria. Non dichiariamo truncated:true: il runtime non deve
prepended notify/cap_expand per cap richiesti." L'output di
`sort_entries(top=3, entries=[5 elementi])` aveva `count:3, total_input:5`
ma niente `truncated:true`.

Il manifest test `top_3_di_5` si aspettava `metadata_field_eq:
{count: 3, truncated: True}`. Il test incarnava il principio originale
2.7. Sweep 2026-05-01: il test era rosso da fine aprile. La decisione
del 28/4 e il principio 2.7 erano in tensione non risolta.

## Decision

Riconciliare 2.7 separando *visibility* da *notify policy*:

* L'output di `sort_entries(top=K, ...)` con `K < total_input` espone TUTTI
  e cinque i campi 2.7: `truncated:true`, `truncated_what:"entries"`,
  `used:K`, `available_total:total_input`. Visibility e' un fatto
  oggettivo: la lista restituita NON contiene tutto.
* Aggiunto un nuovo campo `truncated_intentional:true` per segnalare al
  runtime che il cap e' user-requested. Il runtime usera' questo flag per
  decidere se prepended cap-expand prompt (skipped quando intentional).

Il test ricompone: il matcher `metadata_field_eq` (con la fallback chain
del runner — ADR 0061) trova `truncated` in top-level e supera.

## Alternatives considered

* **Mantenere la decisione 28/4 e modificare il test**: viola §8.2
  ("test fail → fix code, not the test") e crea un test orfano del
  principio 2.7.
* **Aggiungere un cap `top_silent` separato**: introduce due varianti
  (`top` rumorosa, `top_silent` muta) per quello che a livello fattuale
  e' lo stesso taglio. Viola §7.2 (semplicita').
* **Sopprimere completamente il flag intentional**: torna alla 2.7 pura
  ma il runtime non saprebbe distinguere "ho dato top=3" da "ho hit cap
  involontario", emettendo cap-expand prompts indesiderati.

## Consequences

* `sort_entries` test verde senza modifica del test.
* Il runtime guadagna un segnale per discriminare cap intenzionali da
  saturazioni involontarie. Il prepended cap-expand prompt salta su
  intentional.
* Il principio 2.7 della CLAUDE.md va aggiornato per citare il flag
  `truncated_intentional` e la separazione visibility/notify.
* Altri executor con cap user-richiesto (es. `read_messages` con
  `max_results`) dovranno seguire lo stesso pattern. Refactor incrementale.
