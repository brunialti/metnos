---
id: 0134
title: Semantic affinity fallback ibrido (BGE-M3 + hard match)
date: 2026-05-15
status: accepted
area: prefilter | runtime
related:
  - 0117  # unified image enrichment index (introduce BGEEmbeddingService)
complements:
  - 0086  # indici di dominio (pattern create/find/get/delete *_indices)
---

## Context

Il prefilter `runtime/prefilter.py::affinity_score` matcha la query
utente contro i tag `affinity` di ogni executor via bag-of-words hard
match (token overlap + verb/object boost). Bench storico
`bench_minilm_vs_bge.result.json` (88 query × 55 executor) confermava:
**embed-only NON batte token-only** sul recall@K medio.

Tuttavia 42/65 manifest dell'audit 15/5/2026 (`runtime/audit affinity`)
hanno anti-pattern §2.5 (typo deliberati come `sutuazione`,
declinazioni IT singolare+plurale + IT/EN paralleli, compound multi-root
ridondanti). Casi non coperti dal hard match:
- typo edit-distance 1-2 (`Sutuazione server` → `situazione`)
- sinonimi semantici (`stato`↔`situazione`↔`salute`)
- declinazioni irregolari (`riunione/riunioni` non risolto da regole
  -i/-o suffix)
- cross-lingua (`open the messages` ↔ `leggi le mail`)

Bonifica affinity (commit `fc8f5d2`) ha ripulito 21 manifest dai casi
piu' grossolani, ma resta debito linguistico irriducibile a tag finito.

## Decision

**Ibrido fast-path** in `runtime/prefilter.py::rank_adaptive` (BoW path):

1. **Hard match** resta primario (1-2ms). Se `top_score >= threshold`
   (default 8), nessun fallback attivato.
2. **BGE-M3 semantic fallback** quando `top_score < threshold`:
   - encode query (~13-17ms su Strix Halo, ONNX int8 fp32 output)
   - cosine vs cache embedding affinity (~10ms su 731 tag)
   - re-rank con `score_finale = hard + alpha * max_cosine` (default
     alpha=4.0)
3. **Cache su disco**: `~/.cache/metnos/affinity_emb/<sha16>.npz`
   contiene matrix (N_tags, 1024) + executor_names + reverse index.
   Chiave sha256 su (executor.name, tag) ordinati → invalidation
   automatica al cambio catalog (re-sign, nuovo synth, bonifica).
4. **Modulo dedicato** `runtime/affinity_semantic.py` (~190 LOC):
   API `build_or_load_cache(executors)` + `semantic_max_per_executor(query, cache)`.
   Lazy singleton `BGEEmbeddingService`; degrade silente se modello
   non disponibile.
5. **Env switch**:
   - `METNOS_SEMANTIC_MATCH=0`: opt-out fallback.
   - `METNOS_SEMANTIC_THRESHOLD=N`: soglia attivazione (default 8).
   - `METNOS_SEMANTIC_ALPHA=F`: peso bonus (default 4.0).

## Bench tuning (15/5/2026)

Corpus 301 query reali estratte da `~/.local/share/metnos/turns/*.jsonl`
(ground truth = `chosen_tool` primo step di turni riusciti, escludendo
@uploaded/fetch_urls deprecato). Grid threshold × alpha 6×5 = 30 combo
testate via `runtime/bench_semantic_tuning.py`. Risultati:

| threshold | fb_rate | top1   | top3   | top5   | avg_lat |
|-----------|---------|--------|--------|--------|---------|
| baseline  | 0%      | 51.8%  | 68.8%  | 72.4%  | 1.36ms  |
| 4         | 1.3%    | 51.8%  | 69.1%  | 72.8%  | 2.0ms   |
| 6         | 10.3%   | 51.8%  | 69.8%  | 73.1%  | 6.0ms   |
| **8**     | 14.0%   | 52.8%  | 69.8%  | 73.1%  | 5.9ms   |
| 10        | 20.9%   | 52.8%  | 69.8%  | 73.1%  | 10.0ms  |

Pareto best = threshold=8 (sblocca +1% top1, plateau dopo). Alpha
equivalente su recall in 2..6 (la differenziazione semantica avviene
nella SELEZIONE, non nel re-rank): default conservativo 4.

## Consequences

- Casi edge (typo, sinonimi, cross-lingua) coperti senza bonificare
  i restanti 21 manifest del catalogo.
- Latency aggiuntiva 25ms solo sul 14% delle query (cap_expand
  budget complessivo turno ben sotto soglia).
- Cache invalidation deterministica via hash → robusta a re-sign.
- 15/15 unit test in `runtime/tests/test_affinity_semantic.py`
  (cache, key invalidation, fallback con cache None, opt-out env).
- Bug fix collaterale: rimosso mapping `"del": "delete"` da
  `_VERB_TO_CANONICAL` (preposizione articolata IT "del sistema"
  generava verb-boost +10 a tutti i delete_*).

## Notes

- Bench storico embed-only vs token-only era condotto SENZA fast-path
  (sempre attivo BGE). Il valore di BGE-M3 e' sui casi edge, non
  sulla maggioranza che gia' funziona — il fast-path lo cattura.
- Estensibile a MiniLM 384d (gia' su `/opt/myclaw/models/embedding/`)
  se latency diventa problema critico, ma il bench mostra parita'
  approssimativa su recall.
