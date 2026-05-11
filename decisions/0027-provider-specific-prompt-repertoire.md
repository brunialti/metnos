---
id: 0027
title: Provider-specific prompt repertoire (short, prescriptive hints)
date: 2026-04-26
status: accepted
area: runtime
related:
  - 0025
  - 0026
---

## Context

On the evening of 26 April 2026 the three-way benchmark
(`extract_email_addresses` synthesis on Gemma 4 26B, Claude Sonnet
4.6, qwen3:8b) revealed that the same system prompt produces
incoherent results across providers. With the same prompt:

- Gemma 4 26B Q4 produced an AST-failing output because of recurring
  over-escape on raw strings (`r'\\\\w+'` in JSON tool-calls becoming
  `r'\\w+'` in Python, matching literal `\w` instead of word chars).
- Claude Sonnet 4.6 produced a smoke-passing output but with a
  too-aggressive regex (lookbehind/lookahead) that matched 2/3 emails
  in the test text.
- qwen3:8b filled the structural fields but left `python_code` empty
  (the structural quality-floor failure of ADR 0026).

Adapting the prompt per provider rescued two of the three: with a
provider-specific hint Gemma 4 produced 3/3 emails in 32 seconds and
Claude Sonnet produced 3/3 in 9 seconds (half of the previous
duration). qwen3:8b remained below the floor regardless of hint.

The discovery was that prompt sensitivity is not noise — it is
*systematic per provider*. Different model families have different
idiosyncrasies that a single universal prompt cannot satisfy. The
question became: how do we encode and grow this knowledge over time
without ad-hoc patches scattered through the codebase?

## Decision

A *repertoire* of provider-specific prompt hints lives in the
runtime, structured for growth.

**Form: short and prescriptive, not exemplified.** The empirical
finding (verified on Gemma 4 and Claude) is that short prescriptive
forms work as well as long forms with examples and rationale. Brevity
saves tokens in the prompt (more margin for output) and keeps the
repertoire maintainable.

What goes in:
- One imperative-constraint line per behavior to enforce.
- Verb (Compile, Use, None).
- Neutral form: "Constraints: X. Y. Z."

What does not go in:
- Long-form examples (CORRECT/WRONG with code).
- Rationale ("Why" — the reason for the rule).
- Discursive notes.

The "why" lives in this ADR (and the accompanying memory), *not* in
the prompt. The prompt carries the rule; the documentation carries
the explanation.

**Concrete hints (current revision):**

```python
"anthropic": "\n\nVincoli: codice fedele alla spec. Regex semplice. Niente lookbehind/lookahead.",
"llamacpp":  "\n\nVincoli: raw string r'...' con UN backslash. Niente triple-quote docstring.",
"ollama":    "\n\nVincoli: compila python_code per intero (def invoke + def main). Mai vuoto.",
```

(The Italian is intentional: the prompts are addressed to the LLM,
which handles both languages, and the corpus that surrounds Metnos
is bilingual; the constraint lines are kept in the language Roberto
authored them in.)

**Storage today, direction tomorrow.** Today the hints live as a
static dictionary in `runtime/llm_router.py` (`PROVIDER_CODE_GEN_HINTS`).
This is enough to ship the v1.1 repertoire, but lacks versioning
(Gemma 4 vs Gemma 5 will differ), use-case differentiation (code-gen
vs reasoning vs extraction), and user editability. The direction
of evolution is a TOML config (`~/.config/metnos/prompts.toml`)
loaded by the router, with entries keyed on `(provider,
model_pattern, use_case)`. Migration is a v1.2 task.

**Discipline of growth.** Every time a systematic behavior of a
model that affects synt.generate is discovered, an entry is added to
the repertoire and a memory note records the bug observed and the
discovery date. The repertoire is a *living page*; new models
contribute, deprecated models are kept for history.

## Alternatives considered

**Universal prompt for all providers.** Pro: simplest. Con: two of
three providers fail on the same prompt; the failures are systematic,
not random; ignoring the per-provider differences leaves the synt
fragile. Rejected.

**Train per-provider full system prompts.** Pro: maximum
specialization. Con: prompts diverge in unrelated ways; maintenance
becomes per-provider replication of universal logic; new universal
rules require N edits. Rejected.

**Long-form hints with examples and rationale.** Pro: more legible
for new readers. Con: empirical test showed equivalent effectiveness
to short form; the extra tokens consume the model's output budget;
the hint becomes harder to maintain. Rejected.

**No repertoire; just bigger models everywhere.** Pro: avoid the
problem by overpaying. Con: even Claude Sonnet 4.6 produced a 2/3
match without the hint; the issue is not strictly capability, it is
specification. Rejected.

## Consequences

The synt's `generate` quality jumps measurably with hints active
(3/3 vs ≤2/3 on the canonical benchmark, half the latency on Claude).
The improvement is per-provider; the hints are stored in the router
so each call to `chat_with_tools(tier=..., provider=...)` injects
the right hint automatically.

The repertoire becomes a knowledge artifact that survives
Roberto/Claude personnel changes: a future contributor reading the
TOML file (or the router code today) sees the accumulated empirical
findings. Each entry has a date, a bug summary, and a model pattern.
The corpus gains a `prompt_repertoire.html` candidate page (planned)
that mirrors the same content as living documentation.

A specific implication for the wise-tier quality floor (ADR 0026):
qwen3:8b is *structurally* below the floor — no hint resolves it.
The repertoire confirms the floor empirically. Gemma 4 26B Q4 with
the right hint is at the floor; Claude Sonnet 4.6 with the right
hint is comfortably above.

When a new model lands (Gemma 5, Qwen 4, etc.), the rule is: probe
it, find the systematic deviations, add a hint, and update both the
repertoire and a memory note. The discipline keeps the synt's quality
robust against model churn.
