---
id: 0140
title: Prefilter modulare a strategie intercambiabili + scaling validato a 1000 tool
date: 2026-05-17
status: accepted
area: runtime | prefilter
related:
  - 0072  # adaptive re-rank intra-turno
  - 0075  # primary tools per object
  - 0080  # telemetria fine StepLog
  - 0134  # semantic affinity fallback BGE-M3
  - 0136  # skill dormancy + provider qualifier
---

## Context

Al 17/5/2026 il prefilter di Metnos (`runtime/prefilter.py::rank_adaptive`)
e' un singolo algoritmo token-based monolitico con boost canonical
verb/object, primary tools per object (ADR 0075), e fallback semantic
BGE-M3 attivato sotto soglia di confidence (ADR 0134). Il catalog reale
ha 84 executor (handcrafted + 17 importati dalla skill `google-workspace`).

Due tensioni stavano emergendo:

1. **Scaling skill**: la roadmap (`/it/internal/scaling_skill_roadmap`)
   prevedeva di crescere a 5-10 skill third-party (es. Outlook, Slack,
   Notion). Pool atteso 200-300 tool. Letteratura ToolBench/ToolLLM
   (Qin et al. 2024, arXiv:2307.16789) e RestGPT (Song et al. 2023,
   arXiv:2305.18752) riporta degrado Recall@5 del 50-70% nello stesso
   intervallo per pool eterogenei senza vocabolario chiuso. Non avevamo
   evidenza Metnos-specifica per decidere se serve un refactor.

2. **Sperimentazione algoritmi alternativi bloccata**: per provare
   Tool RAG / SQLite FTS5 / Bloom / verb-first dispatch / RRF ensemble
   serviva ogni volta hardcodare modifiche al `prefilter.py` esistente,
   con rischio di regressione su comportamento di produzione.

Il bench precedente embedding-vs-token (ADR 0075, 4/5/2026) aveva
mostrato token-based 92.3% Recall@5 sul pool corrente, ma su 50-70
tool — non conclusivo per scaling.

## Decision

Introdotto un nuovo package `runtime/prefilter_strategies/` con
**architettura plugin** che disaccoppia il dispatcher dalle strategie:

- **Interfaccia**: `PrefilterStrategy.rank(query, catalog, k_min, k_max,
  llm_call, prefer_intent) -> (candidates, route_info)`. Stessa
  signature di `rank_adaptive` per drop-in replacement.
- **Registry**: dispatch table `name → factory`, auto-registrazione al
  primo import.
- **Selettore runtime**: env `METNOS_PREFILTER` (default `legacy` =
  comportamento storico invariato).
- **Compare mode**: `METNOS_PREFILTER=compare:a,b` esegue entrambi A e
  B, ritorna A, logga B come confronto A/B.
- **Telemetria automatica**: opt-in `METNOS_PREFILTER_TELEMETRY=1`
  scrive JSONL su `~/.local/share/metnos/prefilter_telemetry.jsonl`
  (strategy, query_hash, latency, top3, confidence).
- **CLI**: `runtime/prefilter_stats.py` per summary e A/B compare.
- **Bench runner**: `runtime/bench_prefilter_strategies.py` su corpus
  500 query reali estratte dai turn log, con metrica tokens contesto LLM
  (`mean_pool_tokens`, `recall_per_ktok`) + scaling test con padding
  distractor sintetici a pool 84/250/500/1000.

Implementate 14 strategie comparabili (caratteri/righe approssimative):

| Strategia | LOC | Famiglia |
|---|---|---|
| `token_flat` (= legacy) | wrap | baseline token BM25-like |
| `token_flat_v2` | ~100 | + provider penalty + name-exact boost |
| `selective_semantic` | ~80 | token + BGE-M3 fallback |
| `selective_semantic_v2` | ~130 | + threshold dinamico top1-top2 margin |
| `verb_first` | ~80 | dispatch §2.2 verb → token-rank sui survivors |
| `trie` | ~140 | navigation depth-first verb→obj→qualifier |
| `trie_v2` | ~180 | + multi-verb path union (peggiorato in pratica) |
| `hybrid_cascade` | ~110 | trie + bypass rank a depth>=2 |
| `constraint` | ~140 | SAT-style filter (verb_in, object_in, provider_in) |
| `fts5` | ~150 | SQLite FTS5 inverted index + BM25 |
| `bloom` | ~120 | Bloom pre-screen + token-rank sui survivors |
| `rrf_ensemble` | ~80 | Reciprocal Rank Fusion top-3 strategie |
| `length_adaptive` | ~70 | dispatch per lunghezza query (short→trie, ...) |
| `cached_token_flat` | ~90 | LRU cache su query hash |

**Default produzione**: `token_flat_v2` (via systemd drop-in
`~/.config/systemd/user/metnos-http.service.d/prefilter.conf`).
- Recall@5 = 0.720 (vs 0.717 baseline)
- Recall@1 = 0.488 (vs 0.484 baseline)
- mean pool tokens = 4096 (vs 4347 baseline) → **&minus;5.8% prompt LLM**
- latency mean 7.4 ms (vs 6.6 ms baseline) → +0.8 ms negligible

## Scaling validato (finding centrale, originale)

Bench su pool 84/250/500/1000 (catalog reale + distractor sintetici da
combinazioni `verb × object × provider`) su 300 query reali:

| Strategia | pool 84 | pool 250 | pool 500 | pool 1000 | delta 84→1000 |
|---|---|---|---|---|---|
| `token_flat_v2` | 0.720 | 0.697 | 0.687 | 0.663 | **&minus;5.7 pp** |
| `selective_semantic` | 0.717 | 0.697 | 0.687 | 0.660 | &minus;5.7 pp |
| `token_flat` | 0.717 | 0.697 | 0.687 | 0.660 | &minus;5.7 pp |
| `length_adaptive` | 0.703 | 0.683 | 0.673 | 0.647 | &minus;5.6 pp |
| `constraint` | 0.693 | 0.667 | 0.650 | 0.630 | &minus;6.3 pp |
| `trie` | 0.667 | 0.613 | 0.627 | 0.593 | &minus;7.4 pp |
| `bloom` | 0.527 | 0.527 | 0.527 | 0.527 | 0 (degenere, bug) |

**Confronto letteratura**:
- ToolBench/ToolLLM (Qin et al. 2024): Recall@5 crolla 50-70% fra pool
  50 e 500 su REST APIs eterogenee senza vocabolario comune.
- Metnos: &minus;5.7 pp fra pool 84 e 1000 = **ordine di grandezza
  migliore**.

**Spiegazione del vantaggio**: il vocabolario chiuso §2.2 (23 azioni ×
19 oggetti × 4 famiglie qualifier) impone una grammatica strutturale
ai nomi degli executor. I token canonici della query (verb/object
detection deterministico) hanno un Signal-to-Noise Ratio molto piu'
alto rispetto a un corpus REST eterogeneo. Il rumore introdotto dai
distractor (combinazioni verb×object×provider sintetiche plausibili)
non sopravvive al filtro deterministico canonical.

**Implicazione di scaling**: estrapolando linearmente, Recall@5 < 50%
solo a pool >5000 tool. Per Metnos questo e' "infinito" pratico:
50-100 skill third-party stabili senza re-architecture del prefilter.

## Alternatives considered

**(a) Mantenere prefilter monolitico + sperimentare hardcoded**: bocciato.
Ogni esperimento richiedeva modifiche al modulo di produzione, rischio
regressione, no A/B comparativo metrico.

**(b) Sostituire direttamente con Tool RAG (BGE-M3 vettoriale puro)**:
proposto inizialmente nella roadmap (Fase A). Bocciato dopo bench:
Metnos ha gia' BGE-M3 come fallback (ADR 0134) e selective_semantic
non batte token_flat. Sostituzione completa avrebbe perso il signal
canonical senza guadagno.

**(c) Implementare solo 3-4 strategie**: bocciato. Lo sforzo dichiarato
era equivalente (l'architettura plugin e' il 70% del costo); l'arricchi-
mento a 14 strategie ha permesso di scoprire `hybrid_cascade` (vincente
nel bench v3) e `rrf_ensemble` (sorprendente flop con errori correlati).

## Consequences

- **Sperimentazione futura veloce**: nuove strategie = nuovo file +
  registrazione in `__init__.py`. Zero modifiche al dispatcher.
- **Scelta default change-friendly**: produzione cambia strategia con
  un drop-in systemd, niente restart code path.
- **Telemetria continua per A/B reale**: dati di produzione (non solo
  bench sintetico) confermano o smentiscono il vincitore del bench.
- **Roadmap scaling skill semplificata**: la Fase A originale ("Tool
  RAG come Fase 0 obbligatoria") declassata a "bench decision-gated";
  esito del bench valida vocabolario chiuso §2.2 come scaling enabler
  e rimanda re-architecture a soglia ben oltre 30+ skill third-party.

## Watchpoint futuro

Il vocab chiuso e' la fonte del vantaggio. Se in futuro raggiunge ~46
azioni o ~57 oggetti (2x/3x dell'attuale), il signal canonical si
diluisce e la soglia di degradazione potrebbe scendere a pool
1000-3000. Watchpoint memoria persistente
(`vocab-scaling-watchpoint`): rifare `python3 -m
bench_prefilter_strategies --pool-size 1000` quando vocab raddoppia;
soglia di alert Recall@5 < 0.55.

## References

- ToolLLM (Qin et al. 2023): https://arxiv.org/abs/2307.16789
- RestGPT (Song et al. 2023): https://arxiv.org/abs/2305.18752
- BGE-M3 (Chen et al. 2024): https://arxiv.org/abs/2402.03216
- Reciprocal Rank Fusion (Cormack, Clarke, Buettcher 2009):
  https://dl.acm.org/doi/10.1145/1571941.1572114
- ColBERTv2 (Khattab & Zaharia 2022): https://arxiv.org/abs/2112.01488
- Doc interni: `/it/internal/bench_prefilter_strategies`,
  `/it/internal/bench_prefilter_scaling`,
  `/it/internal/scaling_skill_roadmap`
