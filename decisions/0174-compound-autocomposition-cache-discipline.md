---
id: 0174
title: Auto-composizione compound robusta — disciplina cache + normalizzazione clausole store
date: 2026-06-18
status: accepted
area: runtime
related:
  - 0129  # implicit actions (detector deterministico cross-domain)
  - 0146  # consolidamento routing / DEFAULT_TIERS
  - 0164  # cache di apprendimento (fastpath L0 / autopath L1)
  - 0165  # backend-resolver-uniforme
  - 0172  # github-issues promotion design (flusso issue)
complements:
  - 0171  # intent→NLU entities (§J): alternativa più strutturale a D2, accantonata qui
---

<!-- ESTENDE la disciplina di routing deterministico (§11 CLAUDE.md) ai COMPOUND
serviti dalla cache. Additiva: nessuna invalidazione della cache esistente (sig
mono byte-identica); i guard erano già committati (6f853f2/bd9b054/ed42f36) ma
INERTI live finché la cache li scavalcava. Questa ADR li rende efficaci. -->

## Context

Il flusso compound «trova le issue su github E salvale nello store X»
(detect/publish, ADR 0172) **non si auto-componeva in modo affidabile live**: il
piano eseguito era spesso `find_issues_github → find_entries → filter`
(read-only), con la clausola `{write, entries}` **droppata** — nessuna scrittura
nello store. Analisi cross-layer (4 letture parallele, 17/6) + verifica empirica
sul codice e sul server di prod (18/6).

**Modello causale verificato** (corregge l'analisi del 17/6 su due punti):

1. **L'intent NON è il collo di bottiglia.** L'estrattore intent gira già
   seed-pinnato (`llm_provider.chat`, `METNOS_LLM_SEED` default 42 — vale per
   OGNI call, inclusa la `fast`); la regola prompt `store→{write,entries}`
   (`intent_extractor.j2` v3) funziona. Misura: 8/8 run × 3 query →
   `[{find,issues},{write,entries}]` stabile. Il residuo di non-determinismo è
   lo **stato interno del llama-server condiviso** (logits ±0.1, stessa diagnosi
   del describe-determinism), risolvibile solo con processo monouso
   `llama-completion` (~+3-4s/turno — inaccettabile sull'hot-path). Quindi «D1 =
   seed-pin l'intent» era **già fatto**.

2. **Il bug decisivo è la cache che scavalca i guard.** I correttori
   deterministici di struttura — `_align_framework_objects` (ri-allinea i
   tool-fratelli all'oggetto dell'intent) e `_enforce_missing_clauses` (appende
   le clausole RICHIESTE scoperte) — giravano **solo sul path L3 proposer**. Gli
   hit `L0 fastpath` / `L1 autopath` (`engine/dispatch.py`) eseguivano il
   framework cachato applicando SOLO `_mutating_args_grounded` +
   `_apply_ordering_clause`, poi `return`. Un piano compound stale/read-only era
   ri-servito **bypassando i correttori** (`mode=''` = cache-served).

3. **Perché il proposer droppava la clausola write.** Con nessuno store
   registrato (prod: store vuoto), `_gate_store_skill` (`routing_pool.py`) toglie
   `write_entries`/`find_entries`/`delete_entries` dal **pool** (skill dormiente,
   §7.9) → il proposer non ha `write_entries` da proporre → framework read-only.
   L'enforce L3 lo **recupererebbe** (`write_entries` è nel `catalog_v2` via
   `_engine_v2_catalog_with_builtins`, aggiunto INCONDIZIONATAMENTE; quindi
   `derive_tool_name(write, entries)` → `write_entries`), **ma solo se l'enforce
   gira** — e la cache lo scavalcava.

4. **Collisioni di cache cross-compound.** `_compute_intent_sig`
   (`engine/autopath.py`) hashava **solo** il `verb|object` PRIMARIO, ignorando
   `actions[1:]`; il path-2 (intent_hash) non aveva object-boundary (il path-1
   sì). Due compound con la stessa PRIMA clausola ma seconda diversa collidono
   sotto lo stesso ihash → cross-serve dei piani.

5. **Affamamento residuo dell'enforce.** Se sotto contesa del server l'intent
   flippasse la clausola store a `{write, issues}`, `derive_tool_name(write,
   issues)` = None (nessun `write_issues`, ritirato ADR 0172) → enforce **non può
   appendere**. Il punto di leva non è rendere l'LLM più deterministico ma
   **canonicalizzare deterministicamente** la clausola store a `entries`.

## Decision

Tesi: l'intent LLM non è rendibile deterministico a basso costo → la robustezza
si ottiene con **canonicalizzazione deterministica + disciplina cache + guard
deterministici**, tutto §7.9. Cinque decisioni (D1–D5):

- **D1 — seed intent: nessuna azione.** È già pinnato. Il determinismo
  dell'intent passa da D2 (canonicalizzazione), non da ulteriore determinismo
  LLM. Documentato per non ri-aprire il punto.

- **D2-c — normalizzazione deterministica delle clausole store** (lessico).
  `dispatch._normalize_store_clauses(intent, query, catalog)`, chiamata PRIMA di
  pool/cache/proposer. Se la query referenzia uno store-sink interno
  (`detection_lexicon` concept `object.store_sink`, i18n IT+EN), ri-mappa a
  `entries` le clausole di `intent.actions` con un OGGETTO non-routabile.
  **Sicuro per costruzione (tool-existence guard)**: flippa SOLO se
  `derive_tool_name(verb, object)` = None MA `derive_tool_name(verb, entries)`
  esiste. Così `{find,issues}`→`find_issues_github` (routabile) non è MAI toccato
  e `{create,files}`→`create_files` (routabile) nemmeno; solo le clausole store
  orfane (`write_issues` inesistente) diventano `write_entries`.
  **Locus = dispatch, non `extract_intent`**: serve il `catalog` per il check
  tool-existence (che rende il flip incondizionatamente sicuro) e la posizione
  pre-cache rende la sig compound-aware coerente. `detection_lexicon` resta il
  meccanismo di rilevamento (i18n). DEVI tenere il flip vincolato a
  tool-existence; NON DEVI flippare per sola presenza dello store-sink.

- **D3 = D + B — disciplina cache.**
  - **D**: `_compute_intent_sig` **compound-aware** (hash di TUTTE le `actions`,
    non solo il primario) + **object-boundary anche su path-2** (gemello del
    path-1). Mono-azione: `actions` vuoto → base `verb|object` → ihash IDENTICO
    al precedente (ZERO invalidazione della cache esistente).
  - **B**: i guard deterministici (`_apply_deterministic_structure_guards` =
    align + enforce, NO LLM, idempotenti) girano anche sugli **hit L0/L1** prima
    dell'execute. Il re-propose LLM dei dropped resta **L3-only** (un hit
    incompleto oltre l'enforce cade come tale). Self-healing:
    `_maybe_record_fastpath`/`record_observation` registrano il piano corretto.

- **D4 — grammar-on-verbs: ACCANTONATA.** `enforce`+`align` danno GIÀ la garanzia
  di struttura deterministica post-proposer senza vincolare l'LLM. Un vincolo GBNF
  per-clausola amplificherebbe un `intent.actions` sbagliato (garbage-in più
  rigido). Riapribile solo se D2/D3 si rivelassero insufficienti.

- **D5 — skeleton: TENUTO.** Hint non-vincolante cablato in `intent._repropose_cover`
  (`dispatch.py`, re-propose dei dropped); byte-identico sul mono. Base di un
  eventuale D4.

## Consequences

- Routing deterministico end-to-end per i compound store, **anche dalla cache**.
  Il piano `find_issues_github → write_entries(from_step=N, store=X)` è garantito
  per struttura, non per fortuna dell'LLM.
- **Zero regressione sul mono**: sig back-compat (hash identico senza `actions`);
  `_normalize_store_clauses`/align/enforce sono no-op senza `intent.actions`.
- Costo: `align`+`enforce` su ogni hit cache (~ms, deterministico, idempotente,
  NESSUNA call LLM). Su mono è no-op immediato.
- La cache **guarisce**: D previene il cattivo caching cross-object; B corregge in
  servizio finché il champion stale non è rimpiazzato.
- Onestà (§2.8): se lo store non è registrato, `write_entries` è dormiente nel
  pool e fallirebbe in esecuzione — l'enforce lo appende comunque dal
  `catalog_v2`, l'esito runtime è un fallimento ONESTO, non un read-only
  silenzioso spacciato per successo.

## Alternatives considered

- **D2 via prompt rafforzato (a)**: già presente (v3) e insufficiente da solo
  sotto contesa del server; resta LLM. **NER entities §J / ADR 0171 (b)**:
  strutturale ma molto più lavoro e ancora LLM. Scelto (c) deterministico:
  immune al wobble, §7.9.
- **D3 «A» (vietare promozione L1 dei compound)**: sicura ma butta via la latency
  proprio sui piani più costosi da ripianificare. Scartata in favore di D+B.
- **D2 in `extract_intent`**: scartato — senza `catalog` il check tool-existence
  non è possibile e il flip diventa rischioso (romperebbe `create_files` e simili).

## Implementation

- `runtime/detection_lexicon_seed.py`: concept `object.store_sink` (phrases IT+EN).
- `runtime/engine/dispatch.py`: `_normalize_store_clauses`,
  `_apply_deterministic_structure_guards`; chiamate D2-c (top di `run_turn`) e
  D3-B (hit L0/L1); refactor L3 sul helper condiviso.
- `runtime/engine/autopath.py`: `_compute_intent_sig` compound-aware;
  object-boundary su path-2 in `lookup`.
- Moduli RUNTIME (non executor) → nessun re-sign §7.10.
