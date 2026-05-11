---
id: 0073
title: Prefilter token-based confirmed as primary — bench vs embedding (paraphrase-multilingual-MiniLM-L12-v2 384d)
date: 2026-05-04
status: accepted
area: runtime, prefilter
related:
  - 0058  # intent extractor LLM-based
  - 0072  # adaptive re-ranking intra-turn
complements:
  - 0072
---

## Context

Roberto ha chiesto di confrontare il prefilter attuale (`rank_adaptive`,
token-based con MAPPING bilingue IT+EN) con un ranker basato su embedding
delle descrizioni e della query, "sia in termini di qualità sia di latenza,
con 1000 esempi". L'esistenza di un motore di embedding già pronto su
suprastructure (`suprastructure.embedding.onnx_embedding.EmbeddingService`,
ONNX Runtime, paraphrase-multilingual-MiniLM-L12-v2 384d, output L2-normalized)
ha permesso il confronto senza alcun download: il modello scaricato per
giorgio2 (`/opt/giorgio2/models/onnx/`) è caricabile in-process tramite
`EmbeddingService(model_dir=...)`.

## Setup del bench

- **Query**: 298 query reali estratte da `~/.local/share/metnos/turns/*.jsonl`
  (turni dal 26/4 al 3/5/2026, deduplicate per `user_query`). Filtro: almeno
  un `chosen_tool` osservato nel turno e presente nel catalog corrente.
- **Ground truth**: l'insieme `{chosen_tool}` osservati nei `steps[*]` del
  turno (esclusa `final_answer`). Non è un GT teorico: è cosa il PLANNER ha
  effettivamente scelto sotto policy reale (con vaglio + ricorsione +
  prefilter precursor §10.6.3 attivi).
- **Catalog**: 40 executor (snapshot 4/5/2026, `loader.load_catalog(verify=True)`).
- **Embedding text per executor**:
  `name + ": " + description + " [" + " ".join(affinity) + "]"`.
- **Init costs** (una sola volta): caricamento modello + warmup ~750 ms;
  embedding del catalog (40 executor) ~600 ms; ~1.4 s totali per inizializzazione.

Script: `runtime/bench_embedding_vs_token.py`. Output JSON:
`runtime/bench_embedding_vs_token.result.json`.

## Risultati

### Recall@K — esiste almeno un GT nel topK

| K  | token  | embedding |  Δ      |
|----|--------|-----------|---------|
| 5  | 92.3%  | 75.8%     | +16.5   |
| 8  | 93.6%  | 85.9%     |  +7.7   |
| 10 | 94.6%  | 88.3%     |  +6.3   |

### Recall_full@K — l'intero GT è nel topK

| K  | token  | embedding |  Δ     |
|----|--------|-----------|--------|
| 5  | 65.4%  | 51.7%     | +13.7  |
| 8  | 68.5%  | 63.4%     |  +5.1  |
| 10 | 69.5%  | 68.8%     |  +0.7  |

### Latenza per query (decoupling init)

| ranker     | mean    | p50    | p95    |
|------------|---------|--------|--------|
| token      | 0.77 ms | 0.77   | 0.82   |
| embedding  | 4.20 ms | 3.82   | 6.38   |

Embedding è ~5× più lento per chiamata (ma in valore assoluto resta
trascurabile rispetto al budget LLM ~1-3 s/step).

### Overlap top-5

Overlap medio tra le due classifiche top-5: **1.84 / 5**. Non sono
ranker equivalenti — selezionano executor sostanzialmente diversi su
~⅔ dei posti.

## Analisi

**Perché il token-based vince così nettamente su Metnos**:

1. *Naming compositivo* (cfr. ADR 0045): i nomi degli executor sono
   `azione_oggetto[_qualifier]`. Le query reali contengono *letteralmente*
   il verbo + l'oggetto del nome ("leggi i file", "sposta le mail"). Match
   lessicale è quasi tautologico.
2. *MAPPING bilingue IT+EN* (cfr. ADR 0048): i sinonimi italiani (`leggi →
   read`, `cancella → delete`) sono già hardcoded in `vocab.py` e il
   prefilter li applica in pesatura. Il vantaggio "semantic" che giustifica
   l'embedding è coperto a monte.
3. *Description tecniche e brevi*: lo spazio dove un embedding può
   aggiungere valore (parafrasi, sinonimi obliqui) è limitato dalla forma
   stessa delle description Metnos.
4. *Query reali sono dirette*: path letterali, verbi imperativi, IT/EN
   canonico. Terreno ideale per token matching, terreno avaro per
   embedding.

**Dove l'embedding ha vinto** (1 caso su 8 esempi mostrati): query con
verbi azione *secondari* impliciti — "scarica URL e salva in /tmp" →
embedding pesca `write_files`, token pesca `get_urls` (entrambi corretti
per step distinti della catena).

**Dove il token vince forte**: ogni query con verbo+oggetto canonico
("leggi /tmp/x.txt", "comprimi", "sposta in Posta indesiderata") va
diretta al match lessicale. Nessuna ambiguità da risolvere.

## Decision

**Il prefilter token-based (`rank_adaptive`) resta il ranker primario.**
Nessuna sostituzione con embedding-based.

L'embedding può rientrare come **complemento opzionale** intra-turno
attraverso `runtime/adaptive_rerank.py` (ADR 0072), che è ranker-agnostic
per scelta deliberata. Se in futuro emergessero classi di query oblique
dove l'embedding-rank batte il token-rank (ad oggi non osservate), si
potrà swap-in il ranker dentro `re_rank_for_step` senza modifiche al
PLANNER né al catalog.

## Reusable

Il modello ONNX di giorgio2 (`/opt/giorgio2/models/onnx/model.onnx`,
`tokenizer.json`) è caricabile in-process via
`EmbeddingService(model_dir=...)` in ~750 ms, **senza scaricare nulla**.
Riusabile per altri benchmark/feature semantiche:
- dedup di proposals introvertive (similarity tra `target_intent`)
- routing introvertivo (matching tra mnest e nuovi intent)
- memoria long-term (semantic search su audit log)

## References

- `runtime/bench_embedding_vs_token.py`
- `runtime/bench_embedding_vs_token.result.json`
- ADR 0045 (closed naming vocabulary)
- ADR 0048 (Stage 1 bilingual MAPPING)
- ADR 0058 (intent extractor LLM-based)
- ADR 0072 (adaptive re-ranking intra-turn — ranker-agnostic)
- `suprastructure.embedding.onnx_embedding.EmbeddingService`
