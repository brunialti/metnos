---
id: 0044
title: Default local LLM — qwen3:8b with think=false, num_predict=400
date: 2026-04-26
status: superseded
superseded-by: 0146
area: runtime
related:
  - 0025
  - 0042
  - 0106  # bench PROMOTE_FAIL on qwen3:8b vs Gemma
  - 0146  # consolidamento Gemma single-model
---

> **SUPERSEDED-BY ADR 0146** (18/5/2026). Il bench formale ADR 0106
> (7/5/2026) ha dimostrato che `qwen3:8b` non porta beneficio
> rispetto a Gemma 4 26B + drafter E2B su intent_extractor e vaglio
> (concordanza sotto soglia, speedup 0.72×). In produzione su `.33`
> ollama e' disabilitato; tutti i tier locali puntano allo stesso
> llama-server Gemma su `:8080`. La differenza fra fast/middle/wise
> e' nei parametri per-call. Questa ADR resta in archivio come
> storia della scelta originale.

## Context

The early POC of v1.1 had used `qwen2.5:7b-instruct` as the local
LLM (4.7 GB, available in Ollama). On 26 April 2026 the POC's
final cycles probed a more recent option, `qwen3:8b` (5.2 GB,
downloaded during the same session). The probes also explored the
`think` flag — Qwen 3's optional explicit-reasoning mode — and the
`num_predict` parameter that bounds output tokens.

The findings produced the runtime defaults for the `fast` tier
(ADR 0025). They are recorded here because changing the default
LLM is an architectural decision: it sets the latency budget, the
think semantics, and the failure modes of every planning step.

## Decision

The `[runtime.local]` (or `[runtime.llm.fast]` per the three-tier
naming of ADR 0025) section of the config defaults to:

```toml
provider = "ollama"
model    = "qwen3:8b"
endpoint = "http://localhost:11434"
think    = false
num_predict_default = 400
```

Replacing `qwen2.5:7b-instruct`, which was a generation behind.

**Why these specific values.**

- **`qwen3:8b`** vs `qwen2.5:7b-instruct`: marginal RAM cost (~5.2
  vs 4.7 GB), latency identical (~700–1000 ms with `think=false`),
  newer training, native tool-use confirmed working (ADR 0042).
- **`think=false`** as default: with native tool-use and a
  pre-filtered catalog, Qwen 3:8b is robust on planning queries
  zero-shot without thinking. `think=true` is reserved for synt
  and vaglio when those are real (different tier). Latency cost of
  `think=true` is ~6× — 4500 ms vs 700 ms, 159 vs 23 output
  tokens. Not worth paying for routine planning.
- **`num_predict=400`** as default: Qwen 3:8b `think=false`
  typically needs 50 tokens for a planning tool-call. With
  `think=true` the minimum jumps to 200 (under that, the thinking
  is truncated and no tool-call is emitted). For complex queries
  with multistep and big args, ~500 tokens per step. 400 is the
  safety margin that covers all `think=false` cases plus most
  `think=true` cases. Bigger does not change output (the LLM stops
  itself once the tool-call is complete).

**`think` as a per-call parameter, not just per-config.** The
synt and the vaglio can set `think=true` on demand without
reconfiguring the runtime. The LLMProvider API (per ADR 0025)
exposes `chat_with_tools(..., think=False, max_tokens=400)` with
defaults from config but per-call override allowed.

**Latency budget per turn end-to-end** (Qwen 3:8b `think=false`):
- 1 step (single-shot simple): 800–1200 ms
- 2 steps (call_tool + final_answer): 1500–2500 ms
- 3 steps (complex with data piping): 3000–5000 ms
- 5 steps cap (worst case): 6000–10000 ms

All acceptable for a personal assistant; under 10s in worst case.

**Memory footprint at runtime**: ~7 GB (model + KV cache). On a
30 GB RAM machine, ample headroom for executors plus Ollama
runtime.

The decision is one of three post-POC ratifications (with native
tool-use of ADR 0042 and data piping of ADR 0043).

## Alternatives considered

**Keep `qwen2.5:7b-instruct`.** Pro: known stable, already
downloaded. Con: a generation behind, native tool-use slightly
less robust on edge cases, marginal output quality differences.
Rejected.

**Default to `think=true`.** Pro: better quality on ambiguous
queries. Con: 6× slower for routine planning; the planning over a
small pre-filtered catalog rarely needs explicit reasoning;
worsens latency for the common case. Rejected. `think=true`
remains opt-in for synt / vaglio / specific cases.

**Smaller model (`qwen2.5:0.5b` nano).** Pro: faster, lighter. Con:
quality floor below tool-use reliability; structural-field-fill
rate drops; the planner becomes guess-driven. Rejected.

**Larger model as default (`qwen3:32b` or `gemma3:12b`).** Pro:
better reasoning. Con: ~25 GB for qwen3:32b, machine-dependent;
defeats the "fast" semantics of the tier (ADR 0025); routine
planning does not need it. Rejected for the fast tier; available
as middle/wise (ADR 0026).

**`num_predict=200`** (tighter). Pro: faster ceiling. Con: under
`think=true` the truncation cuts the tool-call. The 400 margin
makes `think` a per-call decision without re-tuning. Rejected.

## Consequences

The fast tier has a stable default that any user with Ollama
plus 8 GB of RAM headroom can run. No external provider, no API
key, no recurring cost. This honors ADR 0016 (OSS self-hosted as
default) and the `t.parsimonia` telos.

When the wise tier is also `qwen3:8b` (because the user has no
hardware for Gemma 4 26B and no online budget), ADR 0026 fails
the boot — `wise` cannot degrade to `fast`. The user has to
configure something. This is intentional and documented.

The decision is sensitive to model churn. When `qwen3.5:8b` lands
(or `qwen4:8b`), the probe-and-update pattern repeats: download,
benchmark on the existing test suite (ADR 0029), if equivalent
or better at the same RAM cost, update the default. This is
explicit work, not a passive upgrade.

A specific note on `num_predict`: in Ollama the parameter caps
the output tokens; the LLM stops itself before reaching it
typically. The 400 ceiling does not slow normal cases; it
prevents runaway generation in pathological prompts. Useful
safety, no perf cost.

When the runtime adds prompt-caching support (Ollama is improving
here), the latencies above can drop further. The default is
calibrated for the no-caching baseline; future improvements only
help.
