---
id: 0175
title: Engine v3 swappable — provider-gating + deterministic reorder (proposer compound redesign)
date: 2026-06-18
status: accepted
area: runtime
related:
  - 0164  # engine v2 a 4 layer (dispatch/proposer/recovery/terminator)
  - 0174  # auto-composizione compound + disciplina cache
  - 0136  # provider suffix (_google_workspace/_github), marker dormancy
  - 0146  # LLM tier consolidation
complements:
  - 0164
---

## Context

Obiettivo VITALE (Roberto, 18/6/2026): il proposer deve comporre in modo
UNIVERSALE query di complessità pari a FASE 3 del flusso issue-publish — 4+
clausole con scelta del PROVIDER corretta, ORDINE corretto, ARGS corretti.
Sintomo FASE 3: «leggi store → approva → posta commento github → aggiorna store»
componeva male — post = `send_messages` GENERICO (non `send_messages_github`),
ordine write-prima-di-send, `status=posted` non riempito.

Root cause (analisi 3 agenti, blueprint `project_proposer_compound_redesign`):
tre layer LLM-liberi collassano sui compound 4+. **GAP-B**: la
provider-disambiguation (`tool_grammar.filter_pool_for_grammar`) è ORFANA in
engine v2 — chiamata solo dal planner legacy (`agent_runtime`, gated
`METNOS_GRAMMAR`, morto in v2) → il provider non era mai gateato sui compound.
**GAP-C**: ordine = hint testuale non vincolante; args ~100% LLM.

Vincolo di processo: le modifiche sono molte e il sistema è in produzione su un
obiettivo vitale → serve un engine **swappable**, ripristinabile in ogni momento.

## Decision

**Engine v3 = variante SWAPPABLE, non una modifica in-place di v2.** Nuovo valore
`METNOS_ENGINE=v3` → `MetisV3Proposer` (sottoclasse di `MetisProposer`: riusa il
wrapper multi-candidate/rank/cache) che swappa il SOLO core di generazione via il
seam `_make_simple` → `SimpleProposerV3`. L'unico punto condiviso è
`engine.is_v3()`, consultato dai guard deterministici di `dispatch` per attivare
comportamento **solo additivo** in v3. v2 (`MetisProposer`/`SimpleProposer`)
resta byte-intatto: rollback = `METNOS_ENGINE=metis` (drop-in
`proposer-hardening.conf`).

- **P1 — provider-gating (GAP-B).** `tool_grammar.provider_gate_names` (sibling
  names-based del solo blocco provider di `filter_pool_for_grammar`, stessa SoT
  `detection_lexicon provider.markers`), applicato in
  `SimpleProposerV3._effective_pool` ANCHE sui compound: marker presente →
  esclude il canonico generico se esiste la variante provider nel pool (UN solo
  provider/clausola); marker assente → esclude la variante. L'LLM non PUÒ più
  emettere il generico (grammar GBNF stretta sul pool gateato). +
  `derive_tool_name(query=...)` provider-aware (opt-in, v2 invariato).
- **P2 — ordine (GAP-C).** `_enforce_missing_clauses` usa il derive
  provider-aware (v3-gated) → enforce appende la variante provider, non il
  generico. `dispatch._conform_to_intent_order` (v3-gated) riordina gli
  step-executor nell'ordine di `intent.actions`, robusto agli helper SOFT
  (transform), rimappa `from_step`/`${stepN}`, abort conservativo su forward-dep.
- **P3 — bug §2.8.** `handle_write_entries` IGNORAVA `set_fields`/`fields` → lo
  stato non veniva MAI aggiornato pur con `ok:True` (FASE 3 «aggiorna a posted» =
  silent failure). Fix: merge `set_fields` in ogni entry pre-upsert. Universale
  (non v3-gated).

**Scoperta empirica (banco `tests/benchmarks/compound_dryrun.py`, a secco no-side-effect).**
Stress di 4 compound SANE diverse (3-4 clausole, domini misti) — tutte ✓
deterministiche su v3 **e già su metis**. La tesi del blueprint «l'LLM collassa
su 4+» è troppo forte: collassa sui CONFONDENTI (provider-ambiguity,
intent-misroute), non sulle query sane. Il valore di v3 è il **DETERMINISMO** del
provider e dell'ordine (era scelta LLM/fortuna, §7.9), non una capability nuova.
I fallimenti residui osservati sono **INTENT-level**, non del proposer
(«approvazione»→get_persons; «db locale»→issues live): risolti nel prompt
`intent_extractor.j2`, non nell'engine.

## Alternatives considered

- **Modificare v2 in place (blueprint letterale).** Scartato: nessun rollback su
  un obiettivo vitale in produzione; ogni regressione del routing colpirebbe
  prod. La forma swappable costa una sottoclasse + un gate `is_v3()`, ripagata
  dalla reversibilità totale.
- **provider-gating dentro `build_routing_pool` (blueprint P1).** Scartato in
  favore del proposer: `build_routing_pool` è una funzione PURA condivisa col
  guard `routing_subset_bench` (fidelity §11) e va tenuta neutra; il gating vive
  meglio in `SimpleProposerV3._effective_pool` (più localizzato, v3-only).
- **Binding skeleton completo + typed tool-graph (blueprint P2/P4).** DEFERRED:
  i dati mostrano che metis regge già le compound sane (4 casi ✓), quindi
  costruire la struttura+from_step in modo deterministico è over-engineering ad
  alto costo/rischio per le query sane; servirebbe solo per i casi rotti, che
  sono intent-level. Riaprire se emergono compound SANE fallenti.

## Consequences

- v3 è **opt-in** (`METNOS_ENGINE=v3`); produzione resta `metis` finché non
  promosso. Rollback istantaneo. Nessun `if v3:` sparso: solo `is_v3()` nei guard.
- P3 `set_fields` è universale (bug fix) → beneficia anche v2.
- Validazione: suite **2732/0**, routing v3 **29/29**, intent gold **25/25**,
  stress 4 compound sane ✓ deterministiche, v2 verificato intatto (reorder skip
  sotto metis). Banco di prova permanente: `tests/benchmarks/compound_dryrun.py`.
- Intent fixes (commit separato): `it/`+`en/intent_extractor.j2` — STORE rule
  estesa a «db locale» (record store = entries qualunque il tipo) + regola
  approval ({get,approval} non {get,persons}).
- Aperti: promozione v3→prod dopo e2e; FASE 3 e2e LIVE (commento reale su github)
  = richiede autorizzazione; binding skeleton/P4 solo se i dati lo giustificano.
