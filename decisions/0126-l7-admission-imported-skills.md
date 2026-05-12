---
id: 0126
title: L7 admission anti-synth quando intent matcha imported skill
date: 2026-05-12
status: accepted
area: synt | runtime | loader
related:
  - 0114  # ADR layer 1-6 admission (vocab/affinity/test/efficacy/smoke/verify)
  - 0123  # skill importer agentskills.io (i bindings imported_from)
complements:
  - 0114  # estende come 5° gate preventivo, parallelo a affinity L2
---

## Context

ADR 0114 (8/5/2026) ha definito **4 layer di admission cumulativi** contro
synth difettosi: vocab semantic gate (L1, deferred), affinity overlap guard
(L2, jaccard 0.5 verso handcrafted), efficacy ager (L3), smoke battery
con expected_first_tool (L5), LLM semantic verifier (L6 stage 6 synt).
ADR 0123 (10/5/2026) ha introdotto lo **skill importer** che installa
executor con provenance `imported_from = agentskills.io/...` in
`~/.local/share/metnos/executors/_imports/<skill>/<name>/`. 24 sub-command
del google-workspace skill demo importati come executor di prima classe.

**Bug live 11/5/2026** (turn `e0038482`, ~16:18, query «che appuntamenti
ho domani»):
- Candidates ranked correttamente: `[get_now, delete_events, read_events,
  set_events, compute_entries]` — `read_events` (imported da
  google-workspace) era in posizione 3 del top-K.
- Step 1: PLANNER ha scelto **`request_new_executor(expected_name="read_appointments")`**
  invece di `read_events` che era visibile.
- Synt wise tier ha provato a creare un nuovo executor `read_appointments`
  o `read_calendar`, fallendo dopo 119-227s (status `rejected_semantic_drift`
  o `abandoned`) — wasted compute, frustration UX («Mi dispiace, non riesco
  ad accedere al calendario»).

Analisi corpus 57 synt_proposals storiche: **2 casi** di duplicate vs
imported che NESSUNO degli short-circuit esistenti (`already_in_catalog`,
`canonical_alias`) avrebbe intercettato:
- `read_appointments` → matches `read_events` (sinonimo cross-lang).
- `read_calendar` → matches `read_events` (sinonimo cross-lang).

I 4 layer admission esistenti non coprono questo caso perche':
- **L2** (affinity overlap) confronta synth-vs-synth e synth-vs-handcrafted,
  ma gli imported sono ESCLUSI esplicitamente (`_is_imported()` skip) per
  permettere overlap legittimo dei domini specializzati.
- **L3** (efficacy ager) richiede ≥100 invocations prima di demota — il
  synth duplicato non vivra' mai cosi' a lungo, viene rejected (`rejected_semantic_drift`)
  o abbandonato.
- **L5** (smoke routing) verifica routing query→tool ma non interviene
  preventivamente al synth time.
- **L6** (LLM verifier stage 6) interviene a sintesi fatta, dopo aver gia'
  bruciato 100+s.

Manca un **gate preventivo** a request_new_executor time: se l'intent
(verb, object) matcha gia' un imported skill executor, redirect immediato
SENZA cascade.

## Decision

**Layer 7 admission**: lookup tabellare deterministico (§7.9) in
`synth_request.handle_synth_request()`, dopo `already_in_catalog` e
`canonical_alias` short-circuit, prima della cascata `multistage_run_full`.

Componenti:

1. **`vocab.imported_bindings_index()`** — auto-discovery al boot di
   `~/.local/share/metnos/executors/_imports/<skill>/<exec>/manifest.toml`,
   parsa `name = <verb>_<object>[_qualifier]`, popola
   `dict[(verb, object), list[name]]`. Cache invalidata su mtime di
   `_imports/` (path-mtime signature O(N) directory scan).

2. **`vocab.canonical_object(token)`** — risolve sinonimi cross-lang
   verso OBJECTS canonici §2.2 via tabella `_OBJECT_SYNONYMS_IT` +
   `_OBJECT_SYNONYMS_EN`. Esempi: `appointments`/`appuntamenti`/`calendar`/
   `calendario`/`agenda` → `events`. Lista CHIUSA, escalation a Roberto
   per nuovi termini.

3. **`vocab.lookup_imported_for_intent(verb, object_token)`** —
   risolve `object_token` via `canonical_object()`, cerca in
   `imported_bindings_index()` per `(verb, canonical_object)`, ritorna
   lista nomi (vuota se nessun match).

4. **L7 gate in `synth_request.handle_synth_request`**: dopo
   `_find_canonical_alias` short-circuit, parsa `expected_name` come
   `<verb>_<object>[_qualifier]`. Se `lookup_imported_for_intent(verb,
   object)` ritorna >=1 hit, return observation:
   ```python
   {
       "ok": True, "synthesized": False,
       "redirected": True, "l7_admission": True,
       "name": imported_hits[0],
       "error": f"duplicates_imported_skill_{imported_hits[0]}",
       "imported_alternatives": imported_hits,
       "message": "L'intent (verb=..., object=...) e' gia' coperto dallo "
                  "skill imported `...`. NON DEVI rifare la sintesi. "
                  "CHIAMA `...` al prossimo step ..."
   }
   ```

5. **Best-effort safety**: errori nel lookup (es. `_imports/` corrotto)
   non bloccano la cascata legacy. Log a `DEBUG` e continua.

Determinismo §7.9: zero LLM, zero network. Solo lookup tabellare +
filesystem read-only del campo `name` dei manifest.

## Alternatives considered

**A. Estensione di `_find_canonical_alias`** per coprire anche i sinonimi
cross-lang.
- Pro: un solo punto di redirect, codice piu' compatto.
- Contro: `_find_canonical_alias` lavora su (producer_verb, object) per
  producer-equivalenti (`list_processes` → `get_processes`). Estenderlo
  a sinonimi cross-lang lo carica di logica nuova ortogonale (semantica
  multilingua vs ortogonalita' dei verbi producer). Separazione chiara
  = piu' leggibile, piu' testabile, debug piu' facile. Scartato.

**B. LLM-based intent matcher** per decidere se synth duplica imported.
- Pro: copre sinonimi sconosciuti, espressioni naturali ("la mia agenda
  di Google", "il mio calendario lavoro").
- Contro: violazione §7.9 (LLM > deterministico solo se equipotente).
  Una tabella di ~30 sinonimi IT+EN copre il 95% dei casi reali.
  ~200ms di latency aggiunti a ogni synth_request. Scartato.

**C. Static `vocab.IMPORTED_BINDINGS` constant** invece di auto-discovery.
- Pro: ZERO filesystem access al call time, lookup pure O(1).
- Contro: ogni nuovo `metnos-skills import` richiederebbe edit della
  costante + redeploy. Auto-discovery con cache mtime-invalidated e'
  cheap (~1ms per scan) e segue il pattern "skills imported on-the-fly"
  di ADR 0123. Scartato.

**D. Aggiungere il check al PLANNER prompt** ("DEVI usare imported
executor se intent matcha").
- Pro: zero codice runtime, soluzione "soft".
- Contro: il PLANNER LLM ignora frequentemente vincoli soft (bug live
  11/5 ha ignorato il top-K con `read_events` in posizione 3). Servono
  guardrail deterministici a livello runtime, non solo prompt. Comunque,
  il prompt PLANNER andra' rinforzato in parallelo (TODO Phase 3).

## Consequences

**Easier**:
- Bug live 11/5 non si ripresenta: synth `read_appointments` redirect
  immediato a `read_events`, risparmio 119-227s di cascade.
- Nuovi skill importati godono di L7 automaticamente: aggiungere
  `outlook` skill con `read_events_outlook` → next synth attempt su
  qualunque (verb=read, object=events) ottiene entrambe le alternative.
- Test convergence per il bug-live caso: 14 nuovi test in
  `runtime/tests/test_admission_layer7.py` (boot discovery, canonical
  object, L7 6-case decision tree, bug-live replica).

**Harder/more expensive**:
- Sinonimi cross-lang: nuove lingue (FR, ES, DE) richiedono edit di
  `_OBJECT_SYNONYMS_<LANG>` in `vocab.py`. Pattern ripetibile, ~30
  righe per lingua.
- Manutenzione tabella sinonimi quando emergono nuovi termini IT/EN.
  Escalation policy: Roberto rivede ogni nuovo entry.

**Risk surface**:
- False positive: un synth legittimo per dominio LOCAL (`read_files`)
  potrebbe essere redirect a un imported `read_files_google_workspace`
  se l'handcrafted `read_files` non e' in catalog. Mitigazione: il
  short-circuit `already_in_catalog` interviene PRIMA di L7 ed
  intercetta il caso `read_files` (canonical handcrafted). Verificato
  su corpus storico: 23 falsi candidati intercettati a monte da
  `already_in_catalog`, solo 2 veri positivi (`read_appointments`,
  `read_calendar`) intercettati nuovamente da L7.

**Doors closed**:
- Synth duplicato vs imported skill: la cascata 5-stage non si esegue
  piu' (con la sua spesa di 100-200s wise tier). Esiste l'observation
  di redirect onesta.

**Doors opened**:
- Pattern generalizzabile: lookup tabellare per `provider specialization`
  → next steps potenziali (lookup `lat/lon → places provider`, `mime
  → render engine`, etc).
- L7 si compone con futuri layer (L8+): es. "intent matcha N executor
  imported da skill diversi → dialog 'quale provider preferisci?'".

## Implementazione

File modificati:
- `runtime/vocab.py` (+216 righe): `imported_bindings_index`,
  `canonical_object`, `lookup_imported_for_intent`,
  `invalidate_imported_bindings_cache`, tabelle `_OBJECT_SYNONYMS_IT/EN`.
- `runtime/synth_request.py` (+33 righe): L7 gate dopo
  `_find_canonical_alias`, before `multistage_run_full`.
- `runtime/tests/test_admission_layer7.py` (NEW, 14 test).

Test stats: 14 PASS / 0 FAIL. Suite totale 1278 PASS / 20 FAIL
pre-esistenti (i18n IT vs EN, clip_embedding gated, sse_keepalive,
find_images_unified — nessuno tocca L7).

## Convergence verificata

Corpus retro-actively: 57 synt_proposals storici.
- 21 intercettati da `already_in_catalog` (pre-check esistente).
- 1 intercettato da `canonical_alias` (pre-check esistente).
- **2 NUOVI intercettati da L7** (`read_appointments`, `read_calendar`).
- 33 procederebbero a cascade legacy (out-of-vocab o domini non coperti).
