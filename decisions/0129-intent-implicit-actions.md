---
id: 0129
title: Pattern intent-implicit per azioni mutating sottintese (cross-domain)
date: 2026-05-14
status: accepted
area: vocab | intent_extractor | planner | calendar
related:
  - 0045  # naming convention closed vocabulary
  - 0090  # get_inputs UI dichiarativo
  - 0127  # qualifier `_empty` (propose-and-fire)
complements:
  - 0127  # estende il pattern propose-and-fire con un quinto step potenziale
---

## Context

Query reali multi-azione mostrano ricorrentemente uno schema dove un
sostantivo di dominio (es. «appuntamento», «backup», «riassunto») COMPARE
nella query MA nessun verbo mutating esplicito copre quel sostantivo.
Esempio canonico (turn live 14/5/2026, conv c_mp1adlnc_7w04y4):

> «proponi per la prossima settimana 3 possibili orari per un appuntamento di
> una ora la mattina, dopo la scelta mandami una email con la scelta»

Verbi rilevati: `proponi` → `describe` (P5 suggestion semantics), `mandami` →
`send`. Mutating verb per `events` (default `create`) NON presente.
Risultato: il PLANNER ha eseguito `find_events_empty → get_inputs(choice) →
send_messages` senza fissare l'evento in calendar. L'email conferma uno
stato che non esiste.

**Generalizzabilita'**: lo stesso schema si presenta cross-domain — sempre
con pattern «pipeline multi-azione + un verbo mutating mancante per un
oggetto nominato»:
- «proponi backup mensile e notifica via email» → `create_files`+compress implicito
- «mandami riassunto del documento X» → `write_files_text` implicito (genera il riassunto su disco)
- «manda email a Mario sull'incontro di domani» → `create_events`? (qui il
  sostantivo e' contesto del messaggio, non target — caso ambiguo)

Servono:
1. **Detection deterministica** (no LLM): identificare il pattern
   noun→object senza verbo mutating per quel object.
2. **Risoluzione con strategia esplicita**: auto-execute / ask / skip,
   classificata da heuristica deterministica.
3. **Hint strutturato al PLANNER LLM**: il decisional layer (a/b/c) deve
   essere FUORI dal LLM — Gemma 4 26B medium e' bravo a OBBEDIRE a hint
   strutturati, fragile a DECIDERE su classificazioni ambigue.

## Decision

Aggiungere un layer `implicit_actions` deterministico §7.9 al pipeline
intent. Tre componenti:

### 1. `vocab.OBJECT_DEFAULT_MUTATING_VERB`

Tabella chiusa (17 OBJECTS §2.2 → verbo mutating canonico o `None`):

```
files       → "write"          messages   → "send"
dirs        → "create"         events     → "create"
contacts    → "set"            images     → "create"
signatures  → "set"            texts      → "write"
proposals   → "set"            credentials → "set"
packages/places/processes/urls/numbers/inputs/entries → None  (read-only)
```

`None` = nessun mutating canonico (oggetti read-only-by-construction o
domini fuori vocab §2.2). Tabella estesa solo per nuovi OBJECTS (gia'
escalation a Roberto §2.2).

### 2. `vocab.detect_implicit_actions(query, explicit_verbs?)`

Detection deterministica:
1. Tokenize query, identifica TUTTI i sostantivi che mappano a un OBJECT
   via `canonical_object()` (gia' table-driven da `_OBJECT_SYNONYMS_{IT,EN}`).
2. Identifica i verbi canonici esistenti nella query (`prefilter.detect_canonical_verbs_all`).
3. **Condizione necessaria**: la query DEVE avere almeno un verbo mutating
   ESPLICITO (in `DESTRUCTIVE_VERBS`). Altrimenti la query e' read-only
   single-purpose → no implicit. Evita falsi positivi tipo «cerca file pdf»
   che farebbe emettere `write_files` ask.
4. Per ogni `noun → object` con default mutating presente E non gia'
   coperto dai verbi mutating della query → emit entry.

**Heuristica confidence** (additiva):
- Base: 0.70 (single noun→object match)
- +0.10 se il verbo principale e' producer (`find/get/list/read`) — chiaro
  che l'oggetto e' destinatario di un'azione mutating successiva.
- +0.05 se il default mutating e' reversible (`create/set/move` — coperti
  da reverse_patterns §2.3).
- Skip se default mutating e' `None`.

**Mapping a strategy**:
- `auto`:  `confidence >= 0.85`
- `ask`:   `0.60 <= confidence < 0.85`
- `skip`:  `confidence < 0.60` (entry NON emessa)

Output: lista `[{verb, object, noun_token, verb_canonical, confidence,
strategy, rationale}]`.

### 3. Enrichment intent_extractor

`extract_intent(query, llm_call)` ritorna dict con campo addizionale
`implicit_actions: list[dict]` quando non vuoto. Determinismo §7.9
(detection eseguita dopo l'LLM call, indipendente).

### 4. Hint invariante PLANNER prompt (`_core.j2` IT+EN)

Blocco prescriptive §6 nel `_core.j2` (5 righe):

```
IMPLICIT ACTIONS (ADR 0129):
DEVI: per ogni entry in `intent.implicit_actions` con strategy="auto",
      EMETTI lo step `<verb>(...)` dopo gli input-gathering e PRIMA del
      verb notify.
DEVI: per strategy="ask", EMETTI get_inputs(yes_no) PRIMA dello step
      mutating; esegui solo se conferma == true.
NON DEVI: ignorare implicit_actions o riordinarle prima di get_inputs scelta.
OK: «proponi appuntamento + email» → find_events_empty → get_inputs(choice) →
    get_inputs(yes_no confirm) → create_events → send_messages → final_answer.
ERRORE: skippare create_events quando strategy=="auto" o "ask"+confirm=true.
```

### 5. Iniezione runtime (`agent_runtime`)

Dopo `rank_adaptive`, se `route_info["intent"]["implicit_actions"]` non
vuoto, aggiunge al `planner_system` un blocco strutturato con le entry
(verb/object/strategy/confidence/noun_token). Il PLANNER LLM legge le
entry e applica la regola dell'invariante.

## Alternatives considered

**(a) Risoluzione lato LLM**: chiedere al PLANNER «c'e' un'azione mutating
implicita?». Contro: Gemma medium con think=false oscilla; non gestisce
classificazione (auto/ask/skip) consistentemente. Determinismo §7.9
preferibile.

**(b) Hardcoding per dominio**: «se sostantivo == "appuntamento" allora
emetti create_events». Contro: §7.3 esplicito anti-case-patch. Scala male
(nuovo dominio = nuovo case). La table `OBJECT_DEFAULT_MUTATING_VERB` e' un
LOOKUP, non case-patch: ogni nuovo OBJECT eredita lo stesso pattern senza
codice aggiuntivo.

**(c) Strategia unica `ask` per tutto**: chiedere sempre conferma.
Contro: UX rumoroso per casi univoci (es. il bench fittizio mostra che
auto con confidence alta riduce dialog inutili). Threshold conservativo
+ reversibility bonus permette di alzare la confidence verso auto.

**(d) Pattern noun-bound preposizionale**: per filtrare il caso «email
sull'appuntamento di domani» (sostantivo come contesto del messaggio, non
target). Contro: complica detection senza beneficio chiaro nel breve.
Strategia `ask` resta safe net — l'utente risponde NO e il flow prosegue
solo con send_messages. Affinamento futuro se UX rumoroso.

## Consequences

- **Pattern universale cross-domain**: stesso meccanismo per events, files,
  messages, dirs, ecc. Aggiungere nuovo OBJECT = entry in
  `OBJECT_DEFAULT_MUTATING_VERB`; il resto e' invariato.
- **PLANNER prompt invariante minimo** (5 righe): il decisional layer e'
  FUORI dal LLM, il PLANNER fa solo composizione step (suo core).
- **`local_ics.create` non-stub**: prerequisito per la strategia auto/ask
  su `events`. Writer iCal minimale (`BEGIN:VEVENT...END:VEVENT` append-only
  al file `~/.local/share/metnos/calendar.ics`, atomic write tmp+rename,
  `_undo: {ids: [<uid>], reverse_pattern: "delete_events_by_id"}` §2.3).
  `delete()` simmetrico (rimuove VEVENT per uid). `read()` parser tollerante.
- **Reversibility-aware**: confidence dipende dal `default_mutating` essere
  reversible (boost +0.05). Per azioni irreversibili (es. `send`) la
  confidence resta bassa → forza `ask`.
- **Threshold conservative**: `auto` richiede `>= 0.85` — bonus producer
  + reversible deve combaciare. Roberto puo' tunare in funzione del
  rumore osservato.
- **No regression**: `implicit_actions = []` per query single-action o
  read-only — comportamento PLANNER invariato per >90% dei casi (stime
  smoke + corpus reale).
- **Strategia `skip` e' un'opzione di default**: se la classificazione
  non raggiunge `0.60` di confidence, nessuna entry e' emessa, PLANNER
  segue il piano originale senza modifiche.

## Implementazione

- `runtime/vocab.py`: `OBJECT_DEFAULT_MUTATING_VERB` dict + `detect_implicit_actions()`.
- `runtime/intent_extractor.py`: arricchisce return con `implicit_actions`.
- `runtime/agent_runtime.py`: iniezione `planner_system` con blocco
  strutturato dopo `rank_adaptive`.
- `runtime/prompts/{it,en}/planner/_core.j2`: hint invariante 5 righe stile §6.
- `runtime/backends/calendar/local_ics.py`: `create()` + `delete()` non-stub
  (iCal writer minimale, no lib esterne).
- Test isolato: `detect_implicit_actions` su 6 query corpus reale (3 verde,
  1 falso positivo lieve mitigato da `ask`, 2 read-only no implicit).
- Test e2e: query «proponi appuntamento + email» → 6 step pipeline
  completa (find_events_empty → get_inputs choice → get_inputs yes_no →
  create_events → send_messages → final_answer).
