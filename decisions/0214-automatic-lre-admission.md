---
id: 0214
title: Automatic LRE admission from finalized executor plans
date: 2026-08-22
status: proposed
area: runtime | scheduling | storage | messages
related:
  - 0193
  - 0196
  - 0207
modifies:
  - 0213
---

## Context

F13 made LRE executable, recoverable and visible through a controlled entry.
Its only planner-facing admission path, `start_lre`, still requires a named
registered plan and local source roots. That restriction was useful for the
first deployed proof, but it is the wrong product boundary: users should ask
for an outcome, while Metnos decides whether the finalized work belongs in the
interactive turn or in LRE. Requiring one profile per executor would duplicate
the verified catalog, drift from executor schemas and make a universal engine
behave like a collection of domain-specific workflows.

The current generic kernel can already invoke an admitted executor locally or
remotely through the central scheduler. Two pieces are missing for a direct
invocation: immutable literal arguments and an immutable placement when the
plan has no source from which to derive a device. The current dispatcher also
has four paths that can lead to execution—L0, L1, L3 and recovery—so admission
must happen after their common framework finalization and before every
`Executor.run()` call. An integration that checks only newly proposed plans
would let cached or recovered long actions bypass LRE.

The product owner decided on 22 August 2026 that a compatible action which is
intrinsically long must be served automatically by LRE. The profile must not be
a gate and may be produced mechanically at runtime. The same decision requires
simple, domain-neutral code, explicit failures and no silent fallback to an
inline long invocation.

## Decision

Metnos will add a generic direct-invocation compiler to LRE. A deterministic,
verified executor is intrinsically long when its declared timeout is at least
600 seconds. The threshold is a conservative first operational boundary, not
a theoretical limit and not a language heuristic. Executor names, domains and
phrases do not participate in the decision.

The compiler consumes the exact executor object and finalized arguments from
the normal planner. It emits the existing sealed empty-inventory stage and one
singleton `validate` stage. Each argument is stored as a bounded canonical JSON
`literal` reference and checked against the executor contract's single frozen
JSON type. The result uses a generic `metnos.executor-result/1` envelope with a
required boolean `ok`. No expression, query replay, placeholder or executable
template is persisted.

An optional closed `placement` object on the stage freezes either `server` or
one already-resolved device name. Schema version 7 adds only an owner-scoped
`stage_placements` relation. A stored placement overrides source-derived
placement; an absent row preserves the previous behavior. The normal executor
invocation boundary still revalidates device ownership, availability,
platform and manifest. Failure never falls back to another machine.

The runtime registry is populated once per process from the already verified
catalog. It admits only active, signed, non-dormant, non-in-process,
deterministic executors with an explicitly declared effect policy and a closed,
single-type argument schema. `read_only` maps to durable `pure` with at most
three attempts. `create_only`, `reversible` and `mutating` map to
`manual_only` with one attempt. `unknown`, `interactive`, undeclared policies
and intelligent executors without complete frozen model bindings fail closed.
This is one catalog-derived runner registration, not one user-facing profile
per executor.

`engine.dispatch.run_turn()` receives an optional callback and remains unaware
of LRE modules. One helper calls it after framework finalization and before
execution in L0, L1, L3 and recovery. The callback has three outcomes: `None`
means no long action; a successful receipt terminates the turn; a localized
rejection also terminates the turn. Once any long step is recognized, internal
admission failure cannot become `None`. A preceding approval gate may execute
alone, because the approved resume is classified again. The first version
admits one independent long executor step; unresolved placeholders or a
multi-step graph are rejected honestly until the full framework can be
represented without opaque data.

The existing registered image plan and `start_lre` remain supported as an
optimized compatibility entry for a known DAG. They are no longer described
or treated as the normal activation mechanism. RM-0004 F14 is the normative,
file-level implementation contract and acceptance matrix for this decision.

## Alternatives considered

**Create one profile for every long executor.** This would be easy to bolt onto
F13, but it would create a second catalog of names, timeouts, schemas and
effects. New executors would remain uncovered until a profile was manually
added, which directly conflicts with automatic universal behavior.

**Ask the user whether to enable LRE for each request.** This exposes an
internal architecture choice, makes identical requests behave differently and
does not solve restart safety. LRE should be transparent while its status and
history remain inspectable.

**Move every executor whose timeout is large, including intelligent and
mutating ones.** Timeout alone does not prove that model bindings are frozen or
that an external effect is safe to repeat. Broad optimistic admission would
produce false recovery guarantees. The selected rule is automatic only inside
contracts whose uncertainty can be represented honestly.

**Persist the whole framework as an opaque payload.** This would cover compound
plans faster, but would bypass the typed graph, dependency validation,
invalidation digests and observability that justify LRE. Unsupported compound
plans are rejected instead of becoming an unreviewable special runner.

**Run the long executor inline when admission fails.** This keeps the old turn
alive, but defeats durability precisely when the service is unavailable or a
contract is unsafe. The selected trivalent result makes that failure explicit
and prevents duplicate execution.

## Consequences

Long, compatible single-executor actions become automatic and require no LRE
vocabulary or profile knowledge from the user. New deterministic executors
enter coverage by declaring truthful standard metadata, not by adding a domain
branch. Cached and recovered frameworks receive the same admission semantics
as fresh proposals.

The durable plan may now contain owner-scoped user arguments, so plans and logs
must remain redacted at presentation boundaries and credentials must never be
materialized as literals. Schema migration 7 adds placement rows but does not
rewrite existing stages. The registry costs one verified catalog pass per
process and no per-turn catalog reload.

The first automatic path deliberately excludes compound long plans and
intelligent executors whose complete model use cannot be frozen. It also
resumes only at unit boundaries: a monolithic executor that writes a progress
file but commits no batch remains monolithic. Those limitations are visible
contract gaps, not reasons to add executor-specific code or claim checkpoints
that do not exist.

This ADR becomes `accepted` only after every F14 acceptance test passes and the
public bilingual guide, Tutor build and deployed service describe automatic
activation without presenting profiles as a prerequisite.
