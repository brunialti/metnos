---
id: 0019
title: Feasibility frame — one author plus increasingly capable AI assistants
date: 2026-04-26
status: accepted
area: process
---

## Context

On 26 April 2026 a feasibility audit framed Metnos as "single-person,
no team, no budget" — the traditional indie-developer frame —
and used that to limit what was reasonable to build. Roberto's
correction was explicit and reframed the audit: the right frame is
*one person plus AI assistants that are getting more capable every
month*. The difference is not of degree but of order. A project
"too ambitious for one person" today may be reasonable in 6–12
months, and Metnos is designed for *the present that becomes the
future*, not for a static present.

The audit had ignored a structural fact about software production
in 2026: the activities that scale well with AI assistance — writing
idiomatic code in mainstream languages, integrating standard OSS
libraries, translation, boilerplate tests, documentation, naming
migrations, mechanical refactors, glue code — are most of the lines
of code in any project. The activities that do not scale —
architectural decisions, trade-off evaluation, debugging of
distributed probabilistic systems, ethical/constitutional choices,
long-term curation — are the genuine bottleneck.

A wrong feasibility frame produces wrong design choices. If "one
person cannot build X" is treated as a hard constraint, ambitious-
but-correct designs get truncated to fit a model of effort that is
already obsolete.

## Decision

When evaluating whether a component, an architecture, or a design
choice for Metnos is realistic, the mental model is *one person
working with Claude / Codex / specialized agents that execute
well-specified tasks*, not *one person typing every line of code by
hand*.

Operationally:

- For activities that scale with AI assistance — code, integration,
  tests, docs, refactors — do not invoke "one person cannot do this"
  as a constraint. The argument is "one person *has trouble with
  this*" only when the activity requires *sustained human judgment*
  or *continuous large-scale maintenance*.
- For activities that do not scale — design decisions, trade-off
  evaluation, calibration of probabilistic LLM-in-the-loop systems,
  ethical/constitutional choices, hands-on debugging of distributed
  state — the bottleneck is genuine and the analysis must focus
  there.
- The real budget is not "person-hours of code"; it is *person-decisions
  to close design ambiguities + continuous curation*. Concentrate
  the feasibility analysis on the second.
- Acknowledge the trajectory: what takes 100 hours of an AI agent
  today may take 10 hours in six months. Treat "not buildable now"
  as "buildable soon" unless the constraint is explicitly the
  scaling-resistant kind.

The principle has visible consequences in companion ADRs. The
manifest-authoring delegation (ADR 0020) is the practical form of
"do not waste Roberto's hours on derivative artifacts when the
decisions are already closed". The no-backward-compat rule (ADR
0031) is the form of "AI-assisted refactor is cheap, hand-rolled
back-compat shims are not worth the price". The simplicity rule
(ADR 0017) is also informed by this frame: AI assistants cannot
yet reason well in deeply complex codebases, so simplicity preserves
the leverage.

## Alternatives considered

**Stick with the traditional indie frame.** Pro: pessimistic, hard
to overshoot. Con: systematically underestimates what is achievable;
designs end up too modest, too narrow, too defensive against work
that AI does cheaply. Rejected.

**Assume AI does everything (full automation).** Pro: maximum
optimism. Con: ignores that judgment, ethics, and long-term curation
do not scale; produces designs whose maintenance is impossible.
Rejected.

**No explicit frame; case-by-case.** Pro: flexibility. Con: the
default human bias is the indie frame, so case-by-case quietly
collapses to the wrong default. The frame must be explicit to be
operative. Rejected.

## Consequences

Several decisions become more permissive than they would in the
indie frame: the bilingual canonical corpus (ADR 0048) is feasible
because translation scales; the Rust client (ADR 0046) is feasible
because cross-compile and library integration scale. Several
decisions become more restrictive: ADR 0017 (simplicity) is
defended harder, because AI-assisted complex code remains
expensive to debug.

The frame also informs the `reality_check` discipline (live runs on
real Telegram with real input). Daily contact with the system is
where the non-scaling activities (judgment, curation) get exercised.
The output of those sessions — surprises, broken assumptions, design
gaps — is the bottleneck input the AI cannot supply on its own.

A practical heuristic when arguing about the realism of a design:
if a Claude session can write the code from a clear specification,
the constraint is "do we have a clear specification?" not "do we
have time to type the code?". Bring the conversation back to the
specification.
