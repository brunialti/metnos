---
id: 0018
title: General fixes over symptomatic patches; daily test as discovery
date: 2026-04-28
status: accepted
area: process
related:
  - 0017
  - 0024
  - 0039
---

## Context

By 28 April 2026 the daily live runs on Telegram had become the
sharpest source of failure signal. They exposed cases the test
framework had not anticipated, because the input came from real
language and a real model with its own idiosyncrasies. Each failure
came with two possible responses: a narrow patch around the symptom
("if `name == 'X'` then skip"), or a broader fix that traced back to
the underlying class of error.

The temptation toward the narrow patch is real. Narrow patches are
fast, look like progress, and unblock the immediate run. The cost is
not visible immediately: tomorrow the same class of error returns
slightly differently, the patch does not catch it, and the system
appears robust at the test that exists while collapsing at any
neighbour.

Roberto's framing on 28 April 2026: *the purpose of the daily test is
to discover where the machine is fragile and to make it robust in
general; an app made of N narrow patches is a house of cards that
crumbles at the first new input*. Each failure is an opportunity for
architectural discovery, not a wart to be hidden.

## Decision

Two layered rules.

**No symptomatic patches.** When a live run fails, do not bypass the
error with a per-name skip, a per-string regex, or a per-case
branch. Trace the cause back to its class, and produce the
generalized fix.

The discriminator: before writing a fix, ask *"if a similar but
different input arrived tomorrow, would this fix hold?"*. If the
answer is no, the fix is not general enough.

**Detection of a class of errors as a safety net is allowed; as a
fix it is not.** Spotting "the LLM is producing malformed
placeholders" and emitting a structured error is acceptable. Spotting
"the LLM is producing malformed `filter_entries(kind=image)`
placeholders" specifically and silently skipping is not. The first
generalizes the *symptom* recognition; the second hard-codes a
specific case.

Hardcoded names of executors, models, paths, or error strings in the
runtime is the red signal. They belong in config, in manifests, in
catalogs, in the central message repository (ADR 0004). If a runtime
function knows about `move_files` by name, the rule is broken.

The principle is paired with ADR 0024 (test failures mean fix the
code, not the test) and ADR 0039 (executors are the NL/deterministic
boundary): both encode the same posture in different places. The
daily test, in this framing, is *probing* the frontier of the
system's behavior, not regressing against a fixed contract.

## Alternatives considered

**Narrow patches first, generalize later.** Pro: faster to unblock
each run. Con: the "later" generalization rarely happens; every patch
becomes load-bearing in some other code path; the generalization
cost compounds. Rejected.

**Mixed approach with a flag for "patch vs generalize".** Pro:
flexibility. Con: the flag becomes a permanent escape hatch; "patch"
wins by default because it is easier in the moment. Rejected.

**Symptom-detect and refuse the action** (instead of generalizing
the fix). Pro: minimal code. Con: the user request fails for what is
often a small distance from a valid input. Refusing without a
generalized recovery is the failure mode of legacy enterprise
software. Rejected.

## Consequences

The example that produced this ADR was concrete (28/4 live run "sort
photos by year and place"). Gemma invented a syntax
`{step1.entries|filter_entries(kind=image)}` — a pipe-syntax that the
runtime did not know. The narrow patch would have been a regex that
blocks the specific pipe expression. The general fix was a prompt
reinforcement with a negative example and a generalized check for
malformed placeholders (single brace, pipe, ternary), not just
"`filter_entries`". The specific case is incidental; the pattern
"the LLM invents a template engine" is general.

The rule shapes the runtime in three places.

In `runtime/agent_runtime.py`, the placeholder resolver
(`{{stepN.field}}` per ADR 0043) carries general validation, not a
list of bad cases.

In `runtime/executor_helpers.py` (ADR 0039), the helpers are general
pattern normalizers (compound patterns, zero-as-placeholder,
case-insensitive defaults), not per-executor fixers.

In `runtime/messages.py` (ADR 0004), the central repository allows
the error template "ERR_PLACEHOLDER_MALFORMED" to be reused across
many sites; nobody hardcodes the text.

Trade-off: the general fix is sometimes more expensive than the
narrow patch (a few hours instead of a few minutes). The discipline
is to treat that cost as unavoidable, not as a budget item to be
saved. A house of cards crashes once; a robust general fix runs for
years.
