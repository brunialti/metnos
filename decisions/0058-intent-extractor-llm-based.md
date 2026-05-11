---
id: 0058
title: Intent extractor LLM-based per il prefilter (azione→concetto→verbo canonico)
date: 2026-04-29
status: accepted
area: prefilter, planner
related:
  - 0050  # prefilter bag-of-words
  - 0056  # tool routing
modifies:
  - 0050  # estende il prefilter con intent LLM-based primario
---

## Context

Il prefilter v1 era bag-of-words sull'`affinity` declaration nel manifest:
hard_match*2 + soft_match*1 (description). Funzionava sui casi ovvi ma falliva
sulle query con varianti linguistiche non lessicalizzate.

Caso live 29/4/2026 sera: query "sposta in Posta indesiderata le mail di
pubblicita di oggi knowcastle" rankava `read_messages` come top-1 perche'
la sua affinity (21 tag) include `posta`, `oggi`, `find`, `trova`, `cerca`
— sovrabbondante per il suo dominio specifico. `move_messages` finiva 4°
nonostante "sposta+mail" perfetto match. Il planner ne risentiva: anche con
"mostra che `request_new_executor` esiste" non riusciva a uscire dal vincolo
imposto dai top-K.

Tentativi intermedi:
- Lexicon manuale `_VERB_TO_CANONICAL` con coniugazioni IT/EN: 80-90% accuracy
  ma fragile su forme non listate ("archivia", "svuota cestino", "metti in
  spam") e cross-lingue.
- Roberto 29/4: "ideale e' dalla richiesta estrarre l'azione->concetto->
  matchare concetto su verbi->max 3 candidates".

## Decision

Pipeline a 3 stadi:

  **Stadio 1: Intent extraction (LLM)**
  Modulo `runtime/intent_extractor.py`. Una chiamata LLM al tier `middle`
  (gemma 4 26B, think=False, max_tokens=80, ~370ms) con prompt minimo che
  mappa la query sul vocabolario chiuso (20 verbi × 11 oggetti). Esempi
  in-context per disambiguazione (mostra/render vs leggi/read, scarta/filter
  vs cancella/delete, arricchisci/get vs riassumi/describe).
  Bypass deterministico per pattern undo ("annulla/undo/ripristina/rollback")
  → ritorna `None` per non forzare un mapping errato (LLM mappa "annulla" →
  delete spesso). Fallback al lexicon quando intent fallisce.

  **Stadio 2: Filtraggio per verbo+oggetto**
  `prefilter.rank_with_intent(query, catalog, intent, k=3)`:
  - Filtra catalog per `name.startswith(verb_)`.
  - Boost +6 se l'oggetto canonico e' nei `name_parts` (es. messages in
    move_messages).
  - Per verbi DESTRUCTIVE (move/delete/send/write/extract/create) iniett
    UN precursor (`read_*` per lo stesso oggetto) — il planner ha bisogno
    di leggere prima di agire.
  - Cap a `k=3` (richiesta esplicita Roberto: max 3 candidates).

  **Stadio 3: Universal helpers injection (in agent_runtime)**
  Sempre disponibili nei `tools_for_step`:
  - `classify_entries`, `filter_entries` (data manipulation cross-pipeline)
  - `undo_last_turn` (utente puo' annullare in qualunque momento)
  - `describe_entries` SOLO per verbi non-action (read/list/find/describe).
    Per verbi d'azione (move/delete/send/...) lo escludiamo perche'
    magnetic-tool: il planner pensa di "completare" facendo describe invece
    del verbo richiesto.

## Test

Validation: 100 query realistiche (IT/EN, conjugazioni, idiomi):
- Lexicon-only baseline: ~65/100 (varianti non listate falliscono)
- Intent extractor v1 (no disambiguazioni): 92/100
- Intent extractor v2 (disambiguazioni mostra→render, scarta→filter,
  arricchisci→get, riassumi→describe): 100/100 in 37s (~370ms/query).

E2e live convergence dopo l'intent extractor:
- knowcastle move_messages: 17 mail spostate correttamente
- tiscali: 7 mail spostate + folder "Posta indesiderata" creata + subscribed
- undo_last_turn: 7 mail ripristinate (dopo fix schema mismatch)

## Consequences

### Positive
- Robustezza linguistica: gemma riconosce "archivia", "svuota cestino",
  "metti nello spam" senza dover estendere lexicon.
- Scalabilita': zero manutenzione del dizionario verbi.
- Top-3 candidates ridotto: il planner ha set focalizzato, meno tool
  magnetic interference.
- Cross-language: gemma mappa IT/EN equivalenti automaticamente.

### Negative
- Latenza: +370ms per turno (vs 0ms del lexicon puro). Accettabile.
- Dipendenza dal tier middle: se gemma 4 e' irraggiungibile, fallback al
  lexicon (graceful degradation, accuracy ~65%).
- Non-determinismo: stessa query → stessa risposta in pratica (temperature=0,
  think=False) ma teoricamente possibili variazioni.

### Mitigazioni
- `_UNDO_PATTERNS` deterministici (annulla/undo/ripristina/rollback) bypassano
  l'LLM per garantire predicibilita' sulle azioni piu' delicate.
- Cap top-3 limita anche fallout di errori intent.

## Status

`accepted`. Implementato e in produzione dal 29/4/2026 sera. Test 100/100
convergente.
