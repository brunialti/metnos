---
id: 0106
title: Fast tier per intent_extractor + vaglio LLM judge — bench scaffolding
date: 2026-05-07
status: accepted
area: runtime, performance, llm
related:
  - 0073  # bench embedding-vs-token
  - 0099  # runtime perf optimizations
---

## Context

Oggi `intent_extractor` (in `prefilter.rank_adaptive`) e il giudice LLM
opt-in del `vaglio` (`_judge_score_llm`) usano entrambi
`LLMRouter.provider("middle")` (Gemma 4 26B think=true). Ipotesi: i
compiti sono procedurali abbastanza da girare bene su tier `fast`
(qwen3:8b think=false), che e' ~3-5x piu' veloce e ~10x meno energico.

Promote a `fast` aggressivamente sarebbe rischioso senza misura: il
ranking di Metnos dipende dall'intent, e un degrado del 10-15% di
precision fa cascata su tutto il pianificatore.

## Decision

**Promote DEFERRED** — non commettere lo switch in questa sessione.
Predisporre invece il **benchmark empirico** che misuri la concordanza
fast vs middle su corpus reale e renderlo una procedura ripetibile.

Soglie minime per autorizzare lo switch:

  - intent_extractor: concordanza `(verb, object)` fast↔middle >= 90%.
  - vaglio LLM judge: concordanza approve/deny (soglia 0.5) >= 95%.

Tutto sopra le soglie → flip della costante `tier="middle"` →
`tier="fast"` in `runtime/intent_extractor.py` chiamata e
`runtime/vaglio.py::_judge_score_llm`.

Sotto soglia → resta middle, archiviare i numeri come baseline futura.

## Implementation

`runtime/bench_intent_vaglio_tier.py` (~220 LOC):

  - `_sample_queries(n)` — pesca dai turn log JSONL recenti.
  - `_bench_intent(queries)` — confronto verb+object fast vs middle.
  - `_bench_vaglio(queries)` — confronto score-side vs soglia 0.5.
  - Output JSON con: n, agreed, concordance_pct, threshold_pct,
    promote_ok, latency averages, speedup_x, samples.
  - Esit code 0 = PROMOTE_OK; 1 = PROMOTE_FAIL; 2 = INCONCLUSIVE.

Esempi:

```bash
/opt/suprastructure/.venv/bin/python -m bench_intent_vaglio_tier \
    --kind=both --n=50
```

## Status onesto (§2.8 no silent failure)

Bench eseguito il 7/5/2026 sera (n=50, corpus reale ~/.local/share/metnos/turns/). Risultati:

| Ruolo | Concordanza fast↔middle | Soglia | Speedup fast vs middle | Verdict |
|---|---|---|---|---|
| intent_extractor | **58%** (29/50) | ≥90% | **0.72×** (fast piu' lento) | PROMOTE_FAIL |
| vaglio judge (score≥0.5) | **62%** (31/50) | ≥95% | 1.5× | PROMOTE_FAIL |

Verdict overall: `PROMOTE_FAIL`. Tier `middle` resta su entrambi i call site. Bench raw output preservato in `/tmp/bench_adr0106_output.log`.

**Insight inattesi**:
- **Fast non e' piu' veloce di middle su intent_extractor**: il llama-server di Gemma 4 26B ha `--cache-prompt` + speculative decoding + `num_predict=400` di default → su query brevi (intent extraction) il throughput e' competitivo con qwen3:8b ollama (cold-start e niente speculative).
- **Vaglio fast 38% disagreement** = false negative su safety (blocchi mancati) inaccettabile per un giudice di sicurezza, indipendentemente dallo speedup.
- **Intent fast 42% disagreement** = cascade error sul PLANNER (rank/dispatch errato).

## Consequences

  + Niente regressioni di precision rispetto a middle (numeri archivati come baseline).
  + Procedura empirica formalizzata e ripetibile (riusa lo script su tier futuri).
  - Persiste il costo middle (~700-900 ms intent + ~1500 ms judge LLM).
  + Iterazioni future possibili: provare qwen3:14b o llama4-mini come tier intermedio; prompt engineering specifico per fast (schema-only, niente CoT). Per ora niente.
