---
id: 0017
title: Simplicity as a binding project constraint, not aesthetic preference
date: 2026-04-26
status: accepted
area: process
related:
  - 0009
---

## Context

During the executor design dialogue (ADR 0009) Roberto stated the
rule explicitly: *"if Metnos becomes too complex the user will not
use it"*. The follow-up clarification — *"power and complexity do not
always go together. I do not want to give up any of the discoveries,
any of the mechanisms, any of the concepts. But they must be clear,
linear"* — turned the rule from a "remove things" request into a
binding integration discipline.

The decision was reaffirmed on 26 April 2026 in the wider context of
day-to-day implementation choices, not just the high-level dialogue.
Wherever two approaches achieve the same result, the simpler one
wins. Layers, factories, configuration flags, hypothetical
extensibility, premature abstraction — these are debt unless their
need is concrete.

The reason this rises to ADR status (rather than living as a tonal
preference) is that it is *binding*. A complex design is a *failed*
design even when conceptually elegant. The proof point is the user:
if they cannot build a working mental model, they will not use the
system, regardless of how impressive its internals.

## Decision

Simplicity is a project-level constraint with three operational
forms.

**At equal output, choose the shortest, most linear, most modular
solution.** When evaluating two approaches with equivalent end
results, take the smaller one. The smaller one will be cheaper to
maintain, easier to explain in a doc, and faster to validate.

**No preventive abstraction.** No layers, factories, configuration
flags, or hypothetical extensibility introduced for a future case
that has not yet appeared. Add abstraction when the second concrete
case arrives, not before.

**Modularity means clear boundaries between independent parts, not
proliferation of modules.** A handful of well-bounded modules beats
many small ones whose seams blur because they were drawn for an
imagined symmetry rather than a real responsibility.

The principle connects directly to several other decisions:
- the seed-pool sizing (ADR 0015) is calibrated at median ~80 lines
  per executor, an explicit cap on per-unit complexity;
- the synt design (ADR 0010) prefers compose over generate
  precisely because compose is the simpler default;
- the routing policy (ADR 0034) caps the design at three deterministic
  levels with linear scoring, refusing the temptation of ML-based
  optimizers;
- the simplicity reading is the test the dialogue (ADR 0009) is
  meant to pass: if the doc cannot be told as a story, the design is
  too complex.

When a richer architecture starts to be proposed, the discipline is
to stop and propose the minimum version first. The richer one comes
later, motivated by an actual constraint the minimum cannot meet.

## Alternatives considered

**Treat simplicity as a tonal preference, not a hard rule.** Pro:
allows occasional flexibility for "this case is unusual". Con: every
case becomes "unusual" by argument, which is how complexity wins by
default in software. Rejected.

**Cap complexity by quantitative metrics (cyclomatic complexity,
lines of code).** Pro: machine-checkable. Con: the spirit of the
rule is "the user can build a mental model", which does not reduce
to LoC. A 200-line linear function is simpler than a 20-line one
with three layers of indirection. Rejected as a sole rule, kept as a
prompt to audit when LoC grows.

**Defer the question to release 1.0.** Pro: focus on shipping.
Con: complexity accreted before 1.0 hardens by the time 1.0 ships;
the cleanup is then exponentially more expensive. Rejected.

## Consequences

Several other ADRs cite this one as their parent: granularity (ADR
0014), routing (ADR 0034), no-backward-compat (ADR 0031), the rule
that the synt does not invent new vocabulary (ADR 0045). They all
rely on the implicit "if you start to feel like you are
over-engineering, stop and propose the minimum first".

The discipline shapes daily coding. When two approaches present
themselves, the explicit question is: "which would I document in
fewer words?" The shorter doc usually identifies the right code.
When asking the question is awkward (because each option has a
small advantage somewhere), the tiebreaker is what the user will
think reading the manifest of the executor that exposes this code:
the user is the witness, and the user does not read manuals.

Limit: dataflow that genuinely needs structure (the routing 3-level
schema, the cascade of synt strategies, the three-tier LLM
architecture) is allowed to be structured. The principle is not
"flat above all", it is "clear and linear". Three deterministic
levels is clear; ten deterministic levels is not.
