---
id: 0025
title: Three-tier LLM architecture (fast / middle / wise)
date: 2026-04-26
status: accepted
area: runtime
related:
  - 0026
  - 0027
  - 0044
---

## Context

The POC of v1.1 had used a single LLM provider (Ollama with
`qwen3:8b`) for all roles: planner, judge, summarizer. The empirical
limit appeared on the evening of 26 April 2026 during a probe of
`extract_email_addresses` synthesis — `qwen3:8b` produced a
`tool_call` with `name`, `description`, `purpose`, `affinity`,
`args_schema`, `output_summary` filled in but `python_code` left
empty. The model is good enough for planning over a small catalog;
it is not good enough for code generation. Pushing it to do both
collapses the synt's quality.

The opposite extreme — calling Claude Sonnet for every planning step
— would be 3× slower than the local fast path and would consume
budget for low-value calls. A single LLM cannot be both economical
for routine planning and capable enough for code generation.

The decision was made on the evening of 26 April 2026 to expose three
distinct LLM "tiers" with explicit roles, each independently
configurable.

## Decision

Three named tiers, each with a different role and capability profile.

**`fast & furious`** — small, fast, *always local*, always
available. The default for all non-critical calls (planner,
summarizer for scratchpad). Reference candidate: `qwen3:8b` with
`think=false`, ~700–1000 ms per call. Acts as the safety net: if no
other tier is configured or available, `fast` covers everything.

**`middle & trustable`** — intermediate reasoning capacity. Used by
the vaglio's judge (when real) and by the synt's compose stage.
Candidates: `gemma3:12b` or `qwen3:32b` locally on a machine with
16–32 GB RAM, or `claude-haiku-4-5` / `gpt-4o-mini` online.

**`slow & wise`** — maximum reasoning capacity, accepts higher
latency or money cost. Used by the synt's `generate` (stages 2/3/5:
spec, skeleton, birth-test). Candidates: Claude Sonnet/Opus, GPT-4
family, Gemini Pro online; or large local models (qwen3:32b with
`think=true`, gemma3:27b) on capable hardware. The wise tier carries
a quality floor (ADR 0026): below Gemma 4 26B level it cannot run
local; below that floor an external provider is required.

Configuration is three independent TOML sections
`[runtime.llm.fast]` / `[runtime.llm.middle]` / `[runtime.llm.wise]`
with provider, model, and tier-specific options.

**Tier selection per call.** Each runtime component declares its
preferred tier:
- `agent_runtime.planner` → fast
- `vaglio.judge` → middle (when real)
- `synt.compose` → middle
- `synt.generate` → wise
- `summarizer` → fast

Per-call override remains available
(`provider.chat_with_tools(..., tier='wise')`). Auto-routing
heuristics (let the system pick the tier) are deferred to v1.2 once
telemetry exists.

**Auto-fallback** is on by default for the `fast` direction (if
`middle` or `wise` are unreachable, fall back to a lower tier with a
warning). The exception is `wise` (ADR 0026): silent degradation of
synth/judge to `fast` would corrupt the executor pool, so `wise` is
**fail-fast** rather than degrading.

**Same-model specialization.** Even when two tiers point to the same
backend (e.g. all three default to `qwen3:8b` on a small machine),
the *system prompt* differs per tier. `fast` is action-oriented,
short, structured-output, no explicit reasoning. `middle` encourages
brief consideration of alternatives, `think=false` but "reflect
briefly". `wise` encourages deeper reflection, trade-off
consideration, explicit uncertainty, `think=true`. Same backend,
three roles.

**Cost tracking** extends with a `tier` field; the existing
`CostTracker` already knows `provider+model`, the new field exposes
per-tier monthly spend.

## Alternatives considered

**Single LLM for everything.** Pro: simplest configuration. Con:
either the small model is too weak for synth (current empirical
finding) or the large model is wasteful for routine planning.
Rejected.

**Two tiers (fast and wise).** Pro: minimal taxonomy. Con: the judge
and the compose stage both want intermediate capacity;
forcing them into either fast (too weak) or wise (overkill) wastes
on both sides. Rejected.

**Five tiers (nano, fast, middle, wise, frontier).** Pro: maximum
granularity. Con: every component has to choose among five; the
configuration matrix grows; each tier has narrower coverage. Three
tiers is the smallest set that captures the meaningful distinctions.
Rejected.

**Auto-routing based on query complexity from day one.** Pro: no
manual tier selection. Con: needs telemetry that does not exist yet;
the heuristic without data is worse than the explicit declaration.
Deferred to v1.2.

## Consequences

The runtime gains an `LLMRouter` that wraps the existing
`OllamaProvider` and adds the three-tier dispatch. Tests verify both
the everything-is-fast configuration (worst case) and the
mixed-provider routing (with stub middle).

Three companion decisions land cleanly:
- ADR 0026 fixes the wise-tier quality floor and the fail-fast rule;
- ADR 0027 exposes provider-specific prompt hints in the router;
- ADR 0044 sets the default for the `fast` tier (`qwen3:8b`,
  `think=false`, `num_predict=400`).

A user with a modest machine and no online budget gets a working
Metnos at the fast level (planner, summarizer); they cannot run
synt.generate. The error is explicit at boot ("wise tier not
configured"), not silent. Users who want synth must either bring
hardware (Gemma 4 26B class) or set up an external provider.

The same-prompt-different-tier mechanism turns out to be valuable
even on hardware that can run multiple models: the diversity of
voices on the same call (planner sees the situation as fast,
judge sees it as middle) helps catch a class of LLM mistakes that
a single voice would not.
