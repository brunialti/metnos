---
id: 0080
title: Telemetria fine sui StepLog — 5 sotto-componenti del else_ms
date: 2026-05-04
status: accepted
area: runtime, observability
related:
  - 0058  # intent extractor LLM-based
  - 0072  # adaptive re-ranking intra-turn
  - 0073  # prefilter token-based stays
  - 0078  # http api phase 1 (admin dashboards)
---

## Context

`bench_latency_breakdown.py` aggregava il tempo di un turno in due
secchi: `planner_llm_ms` (somma `step.llm_latency_ms`) e `else_ms` (resto).
`else_ms` mescolava 6+ contributi eterogenei (intent extractor, vaglio,
exec puro, prefilter, adaptive re-rank, IO/scratchpad/audit/channel),
rendendo impossibile diagnosticare quale sotto-componente domina la
latenza per turni lenti.

Caso di studio 4/5/2026: turno «sposta in spam le mail di pubblicita
oggi» con `else_t = 248 s` su 9 step. Senza breakdown non e' possibile
distinguere fra «IMAP lento», «vaglio bloccante», «exec subprocess
overhead» o «scratchpad I/O su entries lunghe».

## Decision

`runtime/agent_runtime.py::StepLog` (dataclass) ottiene 5 nuovi campi
(default `None` per compat con turn JSONL storici):

| campo | semantica | tipico step |
|-------|-----------|-------------|
| `intent_ms` | `intent_extractor` LLM call (tier middle, ~370 ms) | step 1 |
| `prefilter_ms` | `rank_adaptive` puro (token rank + cap adattivo) | step 1 |
| `vaglio_ms` | `judge()` (vaglio LLM o stub) | ogni step exec |
| `exec_ms` | `invoke_executor()` puro (subprocess + IO) | ogni step exec |
| `rerank_ms` | `re_rank_for_step()` (post-step ok) | step exec ok |

Misura: `time.perf_counter()` al confine, `int((t1-t0)*1000)`. I cinque
wrapper sono: `_intent_llm` chiusura (intent_ms accumulato), prefilter
totale - intent (`prefilter_ms`), `judge()` call, `invoke_executor()`
call, `re_rank_for_step()` call.

Solo step 1 popola `intent_ms` e `prefilter_ms` (sono setup di turno,
una volta sola). Step >= 2 lasciano None: il bench somma 0.

`bench_latency_breakdown.py` legge i 5 campi via `_safe_int()` (None →
0) e mostra un blocco aggiuntivo "BREAKDOWN FINE (ADR 0080)" con il
conteggio dei turni in cui i campi sono popolati. Calcola `residual_ms
= else_ms - somma_fine`: il residuo cattura quel che resta (catalog
load, scratchpad, audit, channel round-trip) e fornisce un metro per
identificare overhead nascosti.

## Consequences

- **Compatibilita' totale**: i campi sono opzionali (`int | None`) con
  default `None`, niente rottura di serializzazione del jsonl storico.
  `dataclasses.asdict()` produce `null` per None, json.dumps lo
  rispetta.
- **Test live verificato 4/5/2026**: turno `che ora e?` produce
  `step1: intent_ms=405, prefilter_ms=2, vaglio_ms=0, exec_ms=20,
  rerank_ms=1, llm_latency_ms=17697`; step2 (final_answer) ha tutti
  i 5 campi None.
- **Bench output**: `bench_latency_breakdown.py` mostra ora 7 righe
  invece di 3 nella sezione AGGREGATI. Il `residual_ms` rivela quanto
  rimane non attribuito (~6 s su turni lunghi: scratchpad SQLite +
  catalog load + Telegram I/O).
- **Visibility per la dashboard HTTP** (ADR 0078): `/admin/turns`
  potra' esporre i 5 campi come breakdown grafico via uPlot in iter
  successive.
- **Niente nuovi LLM call**: la telemetria e' puro instrumentation,
  zero overhead semantico (perf_counter ~ns).

## References

- `runtime/agent_runtime.py` (`StepLog`, `run_turn` wrappers).
- `runtime/bench_latency_breakdown.py` (lettura campi + sezione
  BREAKDOWN FINE).
- ADR 0058 (intent extractor — quota intent_ms misurata qui).
- ADR 0072 (adaptive rerank — quota rerank_ms misurata qui).
- ADR 0073 (prefilter token — quota prefilter_ms misurata qui).
