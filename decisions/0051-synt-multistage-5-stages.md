---
id: 0051
title: Multistage synth pipeline (5 stages, procedural → creative)
date: 2026-04-28
status: accepted
area: synt
related:
  - 0049
  - 0050
  - 0042
  - 0045
---

## Context

ADR 0050 documented the failure of the single-prompt synthesizer on the
50-query stress test: the planner-LLM (Gemma 4 26B locally, with Claude
Sonnet 4.5 as the strongest fallback) succeeded on only 1 of 30
new-executor candidates. The bottleneck was not model capacity — even
Claude reached a plateau around 40 % on the same prompt — but prompt
overload. The single template tried to demand a name from a closed
vocabulary, an args schema, capabilities, a reverse pattern, a fluent
description, a battery of birth tests, and a complete Python module, all
in one round-trip with roughly 8 000 tokens of context and a tool-call
formatted output.

Roberto framed the right question on the afternoon of 28 April: a fresh
executor is created rarely, while the composer runs continuously. The
synthesizer can spend several round-trips, the composer cannot. So we
should split the synthesizer into smaller stages whose individual
prompts are short and focused, while the composer remains a single
zero-shot decision.

## Decision

A new module `runtime/synt_multistage.py` implements the synthesizer as
five sequential stages, ordered from the most procedural task to the
most creative one. The skeleton of the future executor is filled in
progressively, and each stage prompt receives only the slice of that
skeleton that its own task needs.

**Stage 1 — naming and classification (procedural).** Receives only the
user request and the closed vocabulary (17 actions × 11 nouns). Emits a
JSON with `name`, `action`, `object`, optional qualifier, plus the
classifications `revertible`, `critical`, `target_kind`. If no
combination of the closed vocabulary can describe the request, this
stage explicitly rejects with a textual reason. Tier middle (Gemma 4
26B, `think=false`).

**Stage 2 — signature (procedural).** Receives the user request plus the
naming output. Emits the JSON args schema, the `capabilities` list, and
the `reverse_pattern` chosen from the closed catalogue defined in
`runtime/reverse_patterns.py` when `revertible=true`. Tier middle.

**Stage 3 — birth tests (procedural).** Receives the user request and
the signature. Emits four to six birth tests in a fixed JSON shape
(`name`, `setup`, `input`, `expect`, `teardown`). Tier middle.

**Stage 4 — description and affinity (creative).** Receives the user
request plus the slice of the skeleton that already has args,
capabilities, revert info, and the count of tests. Emits the
LLM-readable manifest description (single line, no newlines, two to
five sentences) and the affinity keyword list. Tier middle to wise; in
practice middle is sufficient for our test set.

**Stage 5 — code (creative + procedural).** Receives the full skeleton
and the user request. Emits the `<name>.py` Python file in plain text
(no fences). This is the only stage that runs on the wise tier with
reasoning enabled.

The closed vocabulary appears only in the stage 1 prompt. After stage 1,
the name has already been chosen and there is no benefit to repeating
the vocabulary in later prompts. Each stage prompt is built to ignore
information that is not strictly necessary for that stage; the goal is a
narrow, focused task that the LLM can execute deterministically.

A central insight from tuning is that Gemma 4 26B started with
`--reasoning-budget=1024` quietly consumes about a thousand tokens in
its internal thinking before emitting the final JSON. The first run
truncated at 600 tokens of output and three of four queries failed with
a JSON-parse error, even though the model had produced a perfect
classification a moment later when given enough head-room. Stage budgets
were therefore raised to 1 800 (stage 1), 2 500 (stages 2 and 3), 2 200
(stage 4), and 5 000 (stage 5). With this budget the same four queries
gave four correct outcomes: three syntheses (`extract_files_zip`,
`delete_files`, `describe_numbers`) and one legitimate rejection of
"ridimensionare immagini" because both the verb and the object fall
outside the closed vocabulary.

## Alternatives considered

**Keep the single-prompt synthesizer and tune the prompt further.** This
was the implicit alternative when Roberto suggested incremental fixes to
ADR 0049's five gaps. Sweeping prompt parameters could plausibly reach
40 to 50 percent on the stress test, but probably not the 80 percent
target. The same five-fold budget can buy four extra round-trips on a
focused stage, and the empirical jump from one in four to four in four
on the small subset shows that the structural change matters more than
the prompt-engineering tweaks.

**Move the wise tier to Claude online.** ADR 0050 showed that Claude
Sonnet 4.5 reached only forty percent on the same prompt: the limit was
structural, not capacity. Adding online cost without a structural fix
was not worth the spend, and Roberto stated explicitly that online is
the last resort, not the default. Multistage runs entirely on the local
Gemma 4 26B, so the cost question disappears.

**Decision-tree synthesizer (a state machine that calls one of N
specialised generators based on the request shape).** Could be more
deterministic but requires a much heavier framework. The five stages
already give 80 percent of that benefit with prompts that are easy to
iterate.

## Consequences

The new module is parallel to the legacy `synt.py`. The legacy single
prompt remains in place for back-compat and for the cases where
multistage might fail; the new module will become the default once the
validation set grows from four to ten queries and the integration with
the lifecycle states (`proposed → synthesized → active`) is complete.

The five-stage design also establishes the budget shape that the next
synthesizer iterations will inherit: every stage consumes about 1 000
tokens of internal reasoning plus a few hundred tokens of structured
output, so the per-stage budget must always be at least 1 800 tokens.
This is a constraint specific to the current Gemma 4 26B configuration
with `--reasoning-budget=1024`; if we move to a model with no internal
reasoning the budgets can shrink, but for now they are the floor.

A side effect of having a focused stage 1 is that the synthesizer can
reject a request without consuming any of the more expensive stages.
Q1 took 23 seconds and 457 input tokens and stopped there. The stress
test's five rejections from ADR 0050 should now be cheap.

A piece of work this opens: integrating the multistage output with the
`lifecycle` states defined in ADR 0048. Stage 1 should default the new
manifest to `lifecycle="synthesized"`; the human-approval gate (or the
auto-sign on green tests) decides the promotion to `active`. That part
is not yet wired; it will be the next step before declaring multistage
the default synthesizer.
