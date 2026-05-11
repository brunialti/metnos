---
id: 0010
title: Synthesis cascade with non-renunciation telos (compose before generate)
date: 2026-04-25
status: accepted
area: synt
related:
  - 0009
---

## Context

By 25 April 2026 the synt was a single function: when a request did
not fit the existing pool, it generated a new executor with a 7-stage
LLM pipeline (spec, skeleton, profile, birth-test, sign, approval,
install). Two related problems surfaced in conversation that day.

First, the principle "do not leave a user request unactuated, when
possible" had no formal home. It was implicitly carried by Law 2
(informed obedience) and by the `t.tempo` telos, but the *mechanism*
of effort — what actually exhausts before saying "I cannot" — was not
written anywhere. A user-facing system that gives up too easily
becomes useless; one that pretends to try and gives up silently is
worse.

Second, the synt jumped straight to generation when a planning step
failed. Generation costs ~1.30€ in frontier-LLM tokens (spec +
skeleton via wise tier) and tens of seconds. The pool already
contained many executors that could be *composed* into a chain to
satisfy the request without producing a new artifact. Skipping
composition was a parsimony failure (telos `t.parsimonia`) and a
bother failure (each new executor added to the catalog increases the
planner's load forever).

## Decision

Two layered moves, ratified the same day.

**Place the principle as a telos, not a Law.** The non-renunciation
becomes telos `t.coltivazione_strumenti` (or an extension of
`t.tempo`), explicitly *not* a fifth Law in the Constitution. The
reasoning: the four Laws are negative prohibitions ("do not do X");
teleology pushes positive ("come closer to Y"). The Vaglio's motto
*"the Constitution does not judge, teleology does"* depends on this
separation. Operationally: *the synt exhausts the cascade of
strategies within budget and constitution before emitting
`abandoned`*.

**Cascade of five strategies, orthogonalized on two axes.** The
original list (generation, composition, duplication, specialization,
generalization) mixed reactive and introvertive moves. Cleaned up:

- **Reactive** (during the user turn):
  - `compose` — search the existing pool for a chain that satisfies
    the proto-mnest. Zero frontier LLM, cost is the catalog scan.
  - `generate` — fall back to the 7-stage pipeline if compose fails.
- **Introvertive** (homeostasis cycle, run by the ager nightly):
  - `dedupe` — merge near-duplicates;
  - `generalize` — N specialized executors with similar shape become
    a parametric one;
  - `specialize` — only on hot paths with measurable benefit;
  - `decompose` — break a complex executor into reusable parts.

All introvertive moves are gated by batch human approval (the
mandatory stage 6 of synt is never bypassed).

The R score that ranks proposals (existing) gains a `strategy_cost`
component that rewards composition over generation.

The reactive cascade lands in `synt.html` v1.1 (canonical, written
the same day, 13 chapters, SVG figure of the cascade, contract types
`Strategy` / `ProposalState` / `SynthRequest` / `SynthProposal`).
Introvertive strategies provisionally live in `synt.html` chapter 5;
their final home (a separate `consolidator.html`?) is left open.

## Alternatives considered

**Codify the principle as Law 4.** Pro: maximum visibility, hard
constraint. Con: contaminates the prohibition-only nature of the
Constitution; would force the Vaglio to judge against a positive
prescription, breaking its motto. Rejected.

**Skip composition, always generate.** Pro: simplest pipeline.
Con: catalog explosion, parsimony failure, bother failure. Each new
executor rides the planner forever; redundant generation is the
single fastest way to make the system useless. Rejected.

**Composition as a separate component, not a synt stage.** Pro: clean
separation of concerns. Con: composition uses the same catalog scan
the synt would do for similarity check; reusing the synt's
infrastructure halves the code. Rejected.

**Embed introvertive strategies in the executor manifest** (each
executor declares "I am a generalization candidate of X, Y").
Pro: explicit. Con: pushes design metadata to a place that should be
authoritative about behavior, not about evolution; the ager already
handles cluster-level metadata. Rejected.

## Consequences

`synt.html` v1.1 is canonical, UNDER APPROVAL. The Architettura
chapter 9 gains the section *"La cascata della sintesi: prima
comporre, poi generare"* with the two reactive steps and the three
introvertive ones; chapter 11 adds the `t.coltivazione_strumenti`
telos to the example `TELOS.md`. The English canonical mirror
(`/en/architecture/synt.html`) is part of the next translation batch.

The non-renunciation telos has a downstream effect on UX (ADR
0028 / `feedback_synt_generate_ux`): when react falls into generate,
the user is told a new component is being built, latency is expected
the first time and immediate after. The framing is *self-improvement*,
not *delay*.

Open questions left for later sessions: the trigger of proto-mnest
birth during composition (today generic), the recurrence threshold
that activates `generalize`, and the exact home of the introvertive
cascade (synt vs a future `consolidator.html`).
