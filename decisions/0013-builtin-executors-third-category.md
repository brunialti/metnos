---
id: 0013
title: Builtin executors as a third category, runtime-shipped and non-synthesizable
date: 2026-04-25
status: accepted
area: executor
related:
  - 0009
---

## Context

The flat executor taxonomy of ADR 0009 had only two practical origins
in the early design: `synthesized` (what the synt produces) and
`seed` (what ships with the installation). Late on 25 April 2026 a
third class became necessary, prompted by an attempt to model
"agreed routine actions" as a kind of executor. Roberto's correction
was that scheduling, like signing or audit logging, is not an
ordinary capability the synt could ever invent: it is part of the
runtime fabric. Trying to express it as a synthesized executor would
either compromise the synt (giving it special powers) or compromise
the runtime (asking it to negotiate with the catalog for its own
ticking heartbeat).

The category had to be drawn cleanly so that future runtime-level
capabilities (a clock abstraction, a mnestoma query interface, an
audit reader) had a home that was *not* the seed pool and *not* the
synthesized pool.

## Decision

A third category: **`builtin` executors**. They are part of the
runtime distribution, signed as part of the release, and not
synthesizable by the synt. They sit alongside `seed` and
`synthesized` under the same ontological type (ADR 0009), with their
own policy: never aged, never fused, no creation gate (they are there
at install time).

The first builtin proposed and the model for the category is
`scheduler`. It invokes a target executor (or chain) at a defined
cadence, and replaces the wrong notion of "agreed routine actions as
a design category". Inputs cover: target_executor, args, schedule
(cron-like or NL "every morning at 8"), delivery_channel, count
(max firings or `None` for infinite), expiry, on_failure,
max_consecutive_failures, paused. Twin operations also builtin:
`scheduler.list`, `scheduler.cancel`, `scheduler.modify`. Persistent
state lives in `workspace/.runtime/scheduler.sqlite` (runtime data,
not config). Special capability: `system:scheduler` (access to the
gateway's async loop, write to the schedule store).

The criterion for "is this a builtin or a seed?" is structural:
- a builtin needs runtime-internal access (the gateway loop, the
  audit writer's secret format, the mnestoma's primary key) that no
  ordinary executor has;
- a builtin's policy is "never deprecated, never fused" because it is
  part of the release, not of the installation;
- a builtin survives the ager untouched.

The category is documented in `executor.html` v1.1 chapter 2, and a
project-level registry of proposed builtins lives in
`metnos_builtin_executors_proposals.md` (memory). Candidate ideas
under evaluation but not yet promoted: `clock` (tested-time
abstraction), `mnest_query` (mnestoma reader with audit), `audit_query`
(JSONL reader with filters), channel-agnostic `notify`.

## Alternatives considered

**Make scheduler a synthesized executor.** Pro: maximum elegance
("everything is an executor"). Con: the synt would need to know how
to write into the gateway's async loop, which requires either
exposing the loop as a generic capability (huge attack surface) or
hardcoding scheduler in the synt's knowledge (anti-modular).
Rejected.

**Embed scheduler in the runtime as a non-executor module.** Pro: no
new category. Con: the user-facing planner cannot invoke "schedule
this every morning" as a tool; the planner must be specially aware
that scheduling exists and goes through a different code path.
Pollutes the planner. Rejected.

**Special-case scheduler in the seed.** Pro: zero new concepts.
Con: aging policy of seeds presumes they can be deprecated /
generalized; scheduler is an axiom, not a candidate for fusion.
Mixing the two policies in one category produces special cases.
Rejected.

## Consequences

A clear distinction is now drawn between three lifecycles:
`builtin` (release-bound, axiomatic), `seed` (installed at
bootstrap, synthesisable into evolutions, agable), `synthesized`
(born from proto-mnest pressure or trace fusion, synt-managed). All
three share the same ontological type, manifest schema, signature,
sandbox profile.

The proposal registry (`metnos_builtin_executors_proposals.md`) is
the place where each future runtime-level capability is queued for
evaluation before promotion. This avoids the temptation to invent
runtime-level capabilities ad-hoc and discover later that two
components want to do the same thing in slightly different ways.

Open questions deferred to implementation:
- jitter on scheduler firings (for synchronized cron storms);
- predicate-conditioned firing (skip if X holds);
- the boundary of `clock` / `mnest_query` / `audit_query` / `notify`
  — which of these are real builtins vs interfaces internal to the
  gateway with no executor face.
