---
id: 0149
title: Query normalization as planner by-product + BGE matcher fast-path
date: 2026-05-18
status: proposed
area: runtime | planner | fast-path | mnestoma
related:
  - 0094  # fast_path deterministic short-circuit
  - 0146  # LLM tier consolidation Gemma
  - 0148  # supra-refactor audit
---

## Context

Le query utente raggiungono il planner in molte forme di superficie che
sono semanticamente equivalenti:

```
"che ora è?"   "che ore sono?"   "what time is it?"   "che ora era?"
"trova foto di Matteo"   "cerca foto di Matteo"   "find photos of Matteo"
```

Il `fast_path` corrente (ADR 0094) cattura solo match esatti su forma
normalizzata (lowercase, punctuation strip, whitespace collapse). Tutte
le varianti non normalizzabili a queste operazioni elementari finiscono
al planner Gemma 4 26B con costo p50 ~5 s, p90 ~47 s, p95 ~60 s.

## Constraints non negoziabili

1. **Linguistic understanding stays inside the LLM** (ADR 0094 +
   feedback memoria 18/5/2026). Nessun modulo Python per-lingua
   (no lemmatizer rules, no spaCy, no stanza).
2. **No nuovi modelli** — solo asset già su disco: Gemma 4 26B, Gemma
   4 E2B (drafter), BGE-M3 ONNX int8.
3. **No frontier hosted** per la normalizzazione (privacy POC).
4. **No nuovi servizi systemd** — riusare l'infra esistente.
5. **Reliability bar**: un normalizzatore inaffidabile è peggio di
   nessun normalizzatore. Wrong routing inaccettabile.

## Decision

Architettura a 3 livelli con **canonical_query come by-product del
planner stesso**:

```
NEW QUERY
   │
   ▼
[L0]  normalize deterministico (lower / punct / whitespace)   ~0 ms
   │
   ▼
[L1]  BGE-M3 cosine vs canonical_phrases table                ~25 ms
      hit (cosine ≥ 0.95 AND promoted_uses ≥ 5)
         → execute promoted tool, deterministic template
      altrimenti → fallthrough
   │
   ▼
[L2]  Gemma 4 26B planner (esistente)
      output schema esteso: {tool_call, canonical_query}      +~50 ms output
      mnestoma logs (canonical_query, tool, args_shape)
      promotion job → L1 dopo K=5 conferme coerenti
```

### Reliability per costruzione

Il rischio di drift fra "chi normalizza" e "chi sceglie il tool" è zero
**perché sono la stessa call**. Gemma 26B emette `canonical_query` e
`tool_call` nello stesso JSON output, con grammar constraint. Internal
consistency garantita.

I cani di guardia sono indipendenti:

- **Promozione conservativa**: K=5 uses con stesso tool + args_shape
  prima che una entry passi a L1 (mnestoma soglia esistente §5.6).
- **Threshold cosine**: 0.95+ per match L1; sotto soglia → planner.
- **Shadow mode**: prima dell'attivazione, ogni candidate promotion
  shadowed per K turni — match comparato vs planner reale, attivazione
  solo se concordanza > 95%.
- **Circuit breaker**: se L1 hit ma final-answer-rejection (user retry
  o `undo_last_turn`) > 5% sulla entry → demote.

## Output schema esteso

Il grammar JSON del planner (file `runtime/tool_grammar.py`, builder
`grammar_for_tools`) viene esteso con un campo top-level:

```json
{
  "tool_call": { ... },
  "canonical_query": "che ora essere"
}
```

Convenzioni per `canonical_query`:

- **Forma lemma** quando possibile: verbi all'infinito, sostantivi al
  singolare-non-marcato.
- **Lingua scelta dal modello** — il by-product non si vincola a IT/EN
  fisso. Il matcher BGE cosine è cross-lingual nativo.
- **Niente argomenti specifici** (path, URL, ID). Esempio:
  `"scarica https://x.com"` → `"scaricare url"`, non
  `"scaricare https://x.com"`.
- **Lunghezza ≤ 50 token** per stabilità embedding.

Prompt seed (planner): istruzione esplicita al modello di emettere
`canonical_query` come riduzione della query utente alla forma lemma
generale, senza argomenti.

## mnestoma integration

Il record mnest esistente (`pattern`, `uses`, `weight`, `decay`)
acquisisce un campo addizionale `canonical_query`. Il PRIMARY KEY della
tabella mnest passa da `(query_pattern, tool)` a
`(canonical_query, tool, args_shape)` quando `canonical_query` è
popolato. Retrocompat: i mnest pre-0149 senza canonical restano funzionali
ma non si promuovono a L1.

## Verification (vedi step 2v in task tracker)

1. **Emission test**: 30 query con expected canonical_query
   stringato. Run planner. Verify field popolato e cluster cosine
   con expected > 0.85.
2. **Consistency cluster test**: 5 surface variants × 6 intents (time,
   date, location, find_files, read_files, status). Intra-cluster
   cosine deve essere > 0.90; inter-cluster < 0.70.
3. **Smoke regression**: full smoke battery (18 turni) deve restare
   0 FAIL. Tool selection invariato.
4. **Shadow harness**: 7 giorni di traffico reale loggato, before
   L1 attivazione. Manual review dei top-20 canonical_query più
   frequenti.

## Costi

| Voce | Valore |
|---|---|
| Latency turno corrente | +50 ms output token (invisibile) |
| Latency turno cache-hit | ~25 ms (vs ~12 s baseline) |
| VRAM extra | 0 (BGE-M3 già caricato) |
| Servizi systemd extra | 0 |
| Spesa $$ esterna | 0 |
| Manutenzione | ricalibrazione threshold periodica |

## Effort

| Step | File | Effort |
|---|---|---|
| 2a Schema grammar | `runtime/tool_grammar.py` + prompt seeds | 30 min |
| 2b mnestoma logging | `runtime/mnestoma.py`, `runtime/agent_runtime.py` | 1 h |
| 2c BGE matcher (next session) | `runtime/fast_path.py` | 2 h |
| 2d Promotion job (next session) | `runtime/scheduler_v2/builtin_callbacks.py` | 2 h |
| 2v Verification | nuovi test scripts | 1-2 h |

Questa sessione: **2a + 2b + 2v parziale (emission test)**. Step 2c/2d
nella sessione successiva con i log mnestoma raccolti come dataset.

## Out of scope

- Lemmatizer rule-based Python (escluso ADR 0094 + memoria 18/5/2026).
- Modelli small dedicati come normalizer separato (E2B/Phi-3/Llama 1B):
  esclusi per reliability drift e vincolo "no nuovi modelli".
- Frontier API per normalizzazione (escluso da scelta utente 18/5/2026).
- E2B come "shadow validator" su borderline: rivalutabile in futura
  iterazione, **solo dopo** misurazioni reali su Strix Halo Vulkan.

## Open questions

- Threshold cosine ottimale (0.95? 0.92?) — calibrazione su corpus reale.
- K=5 promotion uses ottimale — bench a soglia diversa.
- Cross-lingual canonical: lasciare al modello scegliere la lingua o
  forzare EN per consistenza? Decisione differita a step 2c.
