---
id: 0014
title: Executor granularity — verb-noun, three uses, five-to-seven hops
date: 2026-04-25
status: accepted
area: executor
related:
  - 0009
  - 0010
  - 0045
---

## Context

On 25 April 2026 the question of granularity surfaced as a tension
that could no longer be deferred. Roberto's framing was sharp:
*"functions too elementary make it hard for the LLM to use them for a
complex request; functions too large risk being too specialized"*.
The first failure mode (too elementary, like `byte_read`,
`string_concat`) forces the planner to assemble long fragile chains
and floods the mnestoma with noisy edges. The second failure mode
(too large, like `archive_invoice_for_company_X_v3`) produces rigid
non-reusable executors and a combinatorial explosion of variants.

The decision was needed to align the seed pool (then ~22 candidates
under design) and to give the synt a discipline before generation
became real and the pool started growing autonomously. A wrong
granularity propagates: every executor born from a wrongly-sized
proto-mnest extends the error.

## Decision

The target equilibrium is *verb-noun at the level of a human task*.
Names are NL-readable in the user's planning sentences (`read_file`,
`extract_invoice_number`, `summarise_message`, `archive_pdf`,
`notify`); their inputs and outputs are human-comprehensible (paths,
queries, lists), not byte streams or opaque structs; their side
effects are explicit and minimal in the manifest.

Six heuristics define "right size" and become the standard a new
executor must pass before being added to the pool, whether
hand-written or synthesized:

1. **Naming**: verb-noun, NL-legible. No `do_X_v3`, no cryptic
   abbreviations.
2. **Inputs/outputs**: human-comprehensible.
3. **Side effects**: explicit, minimal, declared in the manifest.
4. **Reusability**: ≥ 3 imaginable use cases at design time.
5. **Synthesis test**: an LLM with the catalog plus docs should plan
   a non-trivial task in ≤ 5–7 hops. If more is needed, the missing
   executor should be synthesized.
6. **Duplication test**: a new specialized executor must fail the
   similarity check against the existing pool (the R score's
   `similarity_penalty`). Otherwise it is redundant; either compose
   from existing or generalize an existing one.

The discipline is not just words. The synt's R score (chapter 7 of
synt.html) already includes `similarity_penalty` and
`coverage_bonus`; combined with a hard cap of 5 hops in compose
chains (chapter 4 of synt.html), the pipeline auto-detects both
extremes and degrades gracefully (compose-fail-then-generate when
elementary, dedupe/generalize on the introvertive cycle when
specialized).

The heuristics were left informally documented in this memory until
27 April, when the first naming convention work (ADR 0045) lifted
them into the canonical naming rules. Together with the cascade
(ADR 0010) and the diary workflow (ADR 0030) they form the
self-discipline that keeps the catalog readable.

## Alternatives considered

**Codify the heuristics as hard rules in the manifest.** Pro: the
runtime can refuse to register an executor that violates them.
Con: heuristics 4 (≥3 use cases) and 5 (planner can plan in ≤7 hops)
are not mechanically checkable without running expensive tests; turning
them into hard gates either weakens them (downgrade to syntactic
checks) or blocks legitimate edge cases. Kept as review heuristics,
not as runtime gates. Rejected as a hard rule.

**Single granularity (everything atomic).** Pro: maximum reusability.
Con: the planner must compose long chains for every realistic task;
the LLM's plan-quality decays with chain length; mnestoma becomes a
soup. Rejected.

**Single granularity (everything macro).** Pro: each request is one
hop. Con: the catalog explodes with near-identical executors; the
synt cannot generalize; the planner cannot decide between three
specialized siblings without a complex disambiguation prompt.
Rejected.

**Defer the discussion.** Pro: more data first. Con: every executor
written or synthesized in the meantime cements a granularity that
becomes hard to refactor. The seed pool was about to be coded; the
discipline had to land before the seeds did. Rejected as deferral.

## Consequences

The seed pool is sized at this granularity (ADR 0015): ~22 executors
covering FS, shell, network, LLM, mail, channel, parse, time. Median
complexity ~80 lines + 5 birth tests, 1–3 capabilities, 30s timeout,
3–5 structured error classes.

Two follow-up decisions land cleanly on this base. The naming
convention (ADR 0045) lifts the verb-noun rule into a closed
vocabulary (16 actions × 11 objects), making the heuristic machine
checkable in the synt. Vectorial executors (ADR 0041) interact with
heuristic 5: a vectorial step counts as one hop regardless of
collection size, which is what allows the planner to act on 98 photos
without exceeding `cap_steps`.

The heuristics also create work for the introvertive synt cascade
(ADR 0010): when N specialized executors with similar shape have
recurrent traces, generalize them into a parametric one. This is
where heuristic 6 (similarity penalty) earns its keep — it routes
proposed duplicates back to a generalize step instead of a generate.

Open until first real synthesis sessions: the precise threshold of
recurrence that activates `generalize`, the exact place to display
the heuristic check (synt.html chapter 4 vs executor.html chapter 2),
the calibration of the 5–7 hop cap when chains grow legitimately
(e.g. multi-stage invoice processing).
