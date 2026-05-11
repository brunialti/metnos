---
id: 0043
title: Data piping between ReAct steps via `{{stepN.field}}` syntax
date: 2026-04-26
status: accepted
area: runtime
related:
  - 0042
---

## Context

The 12th cycle of the POC of v1.1 (26 April 2026) exposed a
fundamental gap. A user request asked Metnos to fetch a URL and
save the response to a file: `web_fetch` then `fs_write`. The
planner correctly invoked `web_fetch` at step 1. At step 2, it
emitted a tool-call to `fs_write` with `"content":
"$(web_fetch({...})['content'])"` — a shell-style placeholder that
the runtime did not interpret. The file was written with the
literal placeholder string, not with the body of the HTTP response.

The failure is structural for any multistep ReAct flow with
dataflow. Without a mechanism for the planner to refer to a
previous step's output, ReAct can only handle queries with
*independent* steps (`time_read` then `final_answer`), not real
pipelines (`fetch → save`, `read → summarize`, `search → write`).

Five alternatives surfaced. Picking among them was an architectural
decision that had to land before `agent_runtime.html` v1.1 could be
written.

## Decision

Reference syntax `{{stepN.field}}` resolved by the runtime.

**Pattern.** `{{stepN.field}}` or `{{stepN.field.subfield...}}`,
with `{{...}}` as the *unique* value of an argument (not
interpolated inside a longer string). The N is 1-indexed (step 1 =
the first invoked executor of the turn). Field lookup is a dotted
walk via `get` on each level of the dict; missing step or missing
field returns an explicit error.

**Runtime mechanic.** `agent_runtime.resolve_references(args,
history)` runs *before* validation, sandbox, and vaglio on every
tool-call. It substitutes references with the corresponding values
from `history[N-1].observation`. Reference errors are returned as
an observation `{ok: false, error: "..."}` so the LLM sees them
in the next step and can self-correct.

**Empirical validation.** POC cycle 12–13 of 26 April. Without data
piping, the LLM invented a shell-style placeholder syntax that did
not resolve. With `{{stepN.field}}` and five lines of prompt
explanation, Qwen 3:8b `think=false` produced
`"content": "{{step1.content}}"` zero-shot; the runtime resolved
the reference; the file was downloaded (278 bytes) and saved
correctly. The decision is documented in
`metnos_design_decisions_post_poc.md` together with native tool-use
(ADR 0042) and the local-LLM default (ADR 0044).

The interaction with native tool-use (ADR 0042) is graceful: the
reference syntax lives inside the `arguments` of the tool-call.
The runtime resolves before invocation. The mechanism is
identical to its pre-tool-use form, but the surface area
(validation, parsing) is smaller.

The other four alternatives are deliberately excluded for v1.1.
They may surface later if real pipelines force them; today,
`{{stepN.field}}` covers the canonical cases.

## Alternatives considered

**(B) The LLM regenerates the content in the text.** The prompt
instructs: "if you need to pass the output of a previous step, copy
it literally into your args". Pro: zero mechanism in the runtime.
Con: token cost balloons (the HTTP body is in the context, then
duplicated in the args); errors accumulate as content length grows;
fails for binary content. Rejected.

**(C) Named variables with explicit declaration.** The LLM declares
"save the output of this step as `var X`" and uses it in later
steps as `"content": {"$var": "X"}`. Pro: more rigorous than
positional references. Con: extends the schema, adds prompt complexity,
forces the LLM to learn two new concepts (declare, reference).
Rejected.

**(D) Chain primitives builtin.** A new builtin `pipe` that
chains: `pipe([web_fetch(...), fs_write(content=$prev.content,
path=...)])`. Pro: no parsing inside `args`; the LLM builds a typed
plan. Con: introduces a "language of composite plans" the planner
has to learn; the planner's grammar grows. Rejected for v1.1.

**(E) Single-step only for v1.1.** Defer multistep until v1.2.
Pro: avoids the question. Con: surfaces nothing; the cases that
need data piping pile up; the POC stops being meaningful for
realistic queries. Rejected.

**(A) Reference syntax `{{stepN.field}}` (chosen).** Pro: minimal
implementation cost (~30 lines of template substitution); covers
the canonical cases zero-shot on `qwen3:8b`; survives native
tool-use unchanged. Con: parser for the reference syntax,
error-handling for missing fields. Cost paid; benefit clearly
worth it.

## Consequences

The runtime gains `resolve_references(args, history) -> args` plus
a structured error returned as observation when references fail.
The planner's prompt template gains a short section that teaches
the syntax. `agent_runtime.html` v1.1 documents the resolver
between validation and sandbox.

A specific subtlety: the rule "only `{{...}}` as the unique value
of an arg" is a v1.1 limit. Interpolation inside a longer string
("the response was: {{step1.content}}") is not supported. If a
real case forces it, the resolver extends; today the limit
simplifies parsing and error reporting.

The observation contract gains weight: previous steps' observations
must be referenceable in a structured way. This shapes how
executors return their results — top-level fields with stable names
(`content`, `path`, `count`), not opaque blobs. The principle
inherits from ADR 0040 (medium-LLM-readable manifests): the LLM
should reference field names that match the manifest's
documentation.

The mechanism applies in both directions: the LLM passes
`{{step1.content}}` and the runtime resolves; the runtime emits an
error like `unknown field 'contnt' on step 1` and the LLM sees the
typo, fixes it next step. The loop closes itself.
