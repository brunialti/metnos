---
id: 0147
title: Bench Qwen 3.5 35B-A3B vs Gemma 4 26B+drafter come fast tier — risultati e plan esaustivo
date: 2026-05-18
status: accepted
area: runtime | llm | bench
related:
  - 0044  # default qwen3:8b (SUPERSEDED-BY 0146)
  - 0106  # bench fast/middle deferred (qwen3:8b)
  - 0146  # consolidamento Gemma single-model
---

## Context

ADR 0146 (18/5/2026) ha consolidato i tier locali su un singolo
llama-server Gemma 4 26B con drafter E2B. Resta aperta la domanda
sollevata dall'utente: "i fine-tuning di Qwen 3.5 piccoli giustificano
un secondo server come fast tier?".

ADR 0106 (7/5/2026) aveva bencato `qwen3:8b` (Qwen 3 vecchio, agosto 2025)
e ottenuto:
- intent_extractor concordanza fast↔middle = 58%
- vaglio concordanza = 62%
- speedup fast vs middle = 0.72×
- verdict PROMOTE_FAIL

Da allora (marzo 2026) è uscita la Qwen 3.5 small series con
miglioramenti significativi su function calling (BFCL-V4 score
9B: 66.1 vs Qwen 3-30B: 42.4 — +56% relative). Modelli candidati per
una rivalutazione: Qwen 3.5 4B, 9B, 35B-A3B (MoE 3B active).

Sui dischi di `.33` era già presente `Qwen3.5-35B-A3B-Q4_K_M.gguf`
(21 GB, scaricato 27/3/2026 ma mai testato come fast tier Metnos).
Bench condotto il 18/5/2026 senza scaricare nuovi modelli.

## Bench setup (eseguito 18/5/2026)

- **Baseline (middle)**: `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf` + drafter
  `gemma-4-E2B-it-Q4_K_M.gguf` su `:8080` (production llama-server,
  speculative decoding `--spec-draft-n-max 4 --spec-draft-p-min 0.75`)
- **Candidato (fast)**: `Qwen3.5-35B-A3B-Q4_K_M.gguf` su `:8082`
  (llama-server temporaneo, parametri base `-c 32768 --cache-type-k f16
  --cache-type-v f16 -b 4096 -ub 256 --threads 8`, senza drafter perché
  non disponibile per Qwen 3.5)
- **Corpus**: `~/.local/share/metnos/turns/*.jsonl` ultimi 7 giorni,
  filtri len(query)∈(5,250), dedup esatto
- **Tool**: `runtime/bench_intent_vaglio_tier.py` con env
  `METNOS_LLM_TIERS_CONFIG=/tmp/llm_tiers_bench.toml`
- **N**: 25 query (soglia minima dichiarata dal bench tool; sopra
  l'INCONCLUSIVE-cutoff a 20)
- **Cleanup post-bench**: Qwen server stoppato, file temp rimossi.
  Production `.33` lasciata invariata.

## Risultati

### Intent extractor

| Metric | Valore | Soglia | Stato |
|---|---|---|---|
| Concordanza verb+object Qwen↔Gemma | **68.0%** (17/25) | ≥90% | **FAIL** |
| Latenza media middle (Gemma) | 386.5 ms | — | baseline |
| Latenza media fast (Qwen) | 596.9 ms | — | — |
| Speedup fast / middle | **0.65×** | ≥1.0× | Qwen più LENTO |
| Verdict | PROMOTE_FAIL | — | — |

Pattern di disagreement: query multi-intent (es. "trova foto X e poi
mandami una mail con elenco"). Qwen 3.5 si ferma sul primo intent
(`find images`), Gemma sceglie il verbo d'azione finale
(`send messages`) — comportamento canonico Metnos ADR 0095. Le query
single-intent vanno concordi.

### Vaglio LLM judge

| Metric | Valore | Soglia | Stato |
|---|---|---|---|
| Concordanza approve/deny score≥0.5 | **92.0%** (23/25) | ≥95% | **FAIL** (per 3 pt) |
| Latenza media middle (Gemma) | 1374.3 ms | — | baseline |
| Latenza media fast (Qwen) | 992.4 ms | — | — |
| Speedup fast / middle | **1.38×** | ≥1.0× | Qwen più VELOCE |
| Verdict | PROMOTE_FAIL | — | — |

Pattern interessante: Qwen 3.5 è **vicinissimo alla soglia** (92% vs
95%), e qui sì è più veloce di Gemma (1.38×). Insight: vaglio è
giudizio binario procedurale (score>0.5 = approve), task per cui un
modello dedicato a procedural calls può funzionare meglio del 26B
generalista. Manca poco. **Ipotesi**: con un prompt vaglio
ri-targettato per Qwen 3.5 (più strutturato, esempi specifici), si
potrebbe chiudere il gap.

## Confronto con ADR 0106 (Qwen 3 vecchio)

| Ruolo | ADR 0106 (Qwen 3:8b, 7/5/2026) | ADR 0147 (Qwen 3.5 35B-A3B, 18/5/2026) | Δ |
|---|---|---|---|
| intent concordanza | 58% | 68% | +10 pp |
| intent speedup | 0.72× | 0.65× | −0.07× (peggio) |
| vaglio concordanza | 62% | **92%** | **+30 pp** |
| vaglio speedup | 1.5× | 1.38× | −0.12× |

Il salto Qwen 3 → Qwen 3.5 ha portato **+30 punti percentuali su
vaglio**. Su intent il guadagno è c'è ma minore. La velocità è
leggermente peggiore probabilmente per la dimensione superiore
(35B-A3B vs 8B dense, anche se solo 3B parametri attivi). Inoltre il
candidato testato non ha drafter — il confronto a parità di feature
sarebbe Qwen 3.5 con drafter compatibile (oggi non esiste pubblicato).

## Decision

1. **Mantenere ADR 0146**: fast/middle/wise rimangono unificati su
   Gemma 4 26B + drafter E2B. Verdict bench è PROMOTE_FAIL su entrambi.
2. **Non scaricare/testare Qwen 3.5 9B o 4B**: i 35B-A3B sono già il
   meglio della famiglia per coverage; un 4B dense raggiungerebbe la
   stessa concordanza vaglio? Improbabile su intent.
3. **Re-bench periodico**: quando Google rilascia un Gemma 4 drafter
   per Qwen, o quando esce Qwen 4 con MoE+spec-decoding nativi,
   ripetere il bench. Tool e DB pronti.
4. **Memoria empirica**: tutti i risultati bench LLM vengono ora
   storati in `~/.local/share/metnos/llm_bench.sqlite` (schema
   `bench_runs` + `bench_samples`) — fonte unica per evitare di
   rifare audit forensici al prossimo dubbio.

## Storage results (centralizzato)

Database: `$METNOS_USER_DATA/llm_bench.sqlite`

```sql
CREATE TABLE bench_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    finished_at TEXT,
    kind TEXT NOT NULL,                       -- 'intent' | 'vaglio' | ...
    n INTEGER NOT NULL,
    baseline_model TEXT NOT NULL,
    candidate_model TEXT NOT NULL,
    baseline_endpoint TEXT,
    candidate_endpoint TEXT,
    agreed INTEGER,
    concordance_pct REAL,
    threshold_pct REAL,
    baseline_avg_ms REAL,
    candidate_avg_ms REAL,
    speedup_x REAL,
    verdict TEXT,                              -- PROMOTE_OK/FAIL/INCONCLUSIVE
    notes TEXT,
    adr_ref TEXT
);
CREATE TABLE bench_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES bench_runs(id),
    idx INTEGER NOT NULL,
    query TEXT NOT NULL,
    baseline_json TEXT,
    candidate_json TEXT,
    agree INTEGER NOT NULL,
    baseline_ms REAL,
    candidate_ms REAL
);
```

Inserted today:
- run_id=1 intent  Qwen3.5-35B-A3B  17/25  68.0%  0.65×  FAIL  ref=0147
- run_id=2 vaglio  Qwen3.5-35B-A3B  23/25  92.0%  1.38×  FAIL  ref=0147

(I per-sample JSON non sono stati storati in bench_samples in questa
prima passata per minimizzare side-effect; estensione del bench tool
per fare INSERT diretto al volo è la prossima iterazione.)

## Plan esaustivo (per quando torna ragionevole)

Quando si vuole rifare un bench completo (es. uscita Qwen 4 / drafter
per Qwen 3.5 / Gemma 5):

1. **Modelli candidati**:
   - baseline: Gemma 4 26B + drafter E2B (current production)
   - candidates: Qwen 3.5 4B, 9B, 35B-A3B; eventuali fine-tune
     (Hermes-Llama, NousResearch Hermes-3-Llama-3.1-8B)
2. **Ruoli testati**:
   - intent_extractor (procedural NLU, ADR 0058)
   - vaglio LLM judge (safety binary classification, ADR 0107)
   - classify_entries (multi-class procedural)
   - describe_entries (summarization, requires italian quality check)
   - intent_implicit_actions (multi-action detection, ADR 0129)
3. **Corpus**: stratificato per sezione planner
   (mail/calendar/photos/web/system), N≥200 per significatività
   statistica
4. **Metriche**:
   - concordanza (verb, object) per intent; (approve/deny) per vaglio
   - latency p50, p95, p99
   - output token count
   - failure modes: tool-call mancante, JSON malformato, args fuori
     schema, thinking-leak, italian quality regression
   - cost-equivalent (rate Anthropic/OpenAI per parità di compute)
5. **Soglie**:
   - intent ≥90% concordanza + speedup ≥1.5× (cumulativo)
   - vaglio ≥95% concordanza + speedup ≥1.2×
   - Sotto soglia su uno dei due → no promote
6. **Output**: insert in `bench_runs` + `bench_samples` con
   `adr_ref` corretto. Riassunto in markdown nella stessa ADR.

## Side note — fine-tuning ricerca

WebSearch 18/5/2026 (sources sotto):

- Qwen 3.5 small series: Qwen 3.5-4B (TAU2-Bench 79.9), 9B (BFCL-V4
  66.1, GPQA Diamond 81.7 beating GPT-OSS-120B). Architettura Gated
  DeltaNet hybrid, function calling viable per agent framework senza
  cloud dependency.
- Gemma 4 MTP drafter: 3× speedup confirmed on NVIDIA, 2.2× su Apple
  Silicon. Drafter è 4-layer model orders of magnitude smaller del
  target.
- Hermes-3-Llama-3.1-8B: function calling robusto su base Llama;
  italian quality non documentato.
- Fine-tuning Gemma 4: Unsloth supporta E2B/E4B/26B-A4B/31B con QLoRA
  (16 GB VRAM minimum). Crescente fine-tune community per agentic tool
  use.

Implicazione operativa: nessuna delle release recenti cambia la
conclusione bench. Gemma 4 + drafter resta competitivo o superiore
per il workload Metnos. Re-evaluation diventa interessante quando:
- Qwen rilascia un drafter speculative-decoding-ready
- Gemma 5 esce con miglioramento di base (target ~settembre 2026?)
- Nuova fine-tune italiana di un modello piccolo diventa pubblica

## Consequences

+ ADR 0146 confermato empiricamente.
+ Database `llm_bench.sqlite` come fonte unica per future evaluation —
  niente più re-audit forensici.
+ Plan esaustivo documentato + riusabile.
- 25 query è poco per significatività statistica robusta; il
  re-bench completo (N≥200, p95 latency, failure mode) resta TODO
  quando lo scenario lo richiede.
