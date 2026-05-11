---
id: 0031
title: No backward compatibility before 1.0; break freely when redesign is better
date: 2026-04-27
status: accepted
area: process
related:
  - 0019
---

## Context

By 27 April 2026 the project had refactored several modules
(`vaglio.py`, `agent_runtime.py`, the `channels/*` subsystem) and
was about to refactor the executor naming convention (ADR 0045) and
the move from single-item to vectorial executors (ADR 0041). Each
refactor opened the same question: do we keep a backward-compatible
path?

The traditional answer in mature projects is yes — write a shim, mark
the old path deprecated, give downstream a transition window. That
discipline pays off when there is downstream: external users,
integrations, persisted artifacts that the change might break.

Metnos is pre-1.0, single-user, and has no downstream that is not
the project itself. Every shim costs code, semantic confusion (two
ways to do the same thing), and tests that defend old behaviors
nobody depends on. Continuing to pay that cost would slow refactors
that were obviously improving the design.

## Decision

While Metnos is pre-1.0, backward compatibility is not a constraint.

Operationally:

- When refactoring a module (`vaglio.py`, `agent_runtime.py`,
  `channels/*`, executor schema, etc.), rename fields, change
  signatures, remove parameters legacy without writing compatibility
  shims.
- Existing tests are updated to the new design (they are not kept
  as "legacy tests"). A test that defended an obsolete behavior is
  rewritten or deleted.
- "Moderate" defaults that exist only for compatibility go away.
- **Exception**: persisted data (SQLite databases, state files)
  requires explicit migration or reset. Even there, if "reset" is
  simpler, it is allowed.
- The rule does not extend to artifacts published to the world (the
  site, deployed docs); for those, controlled-breakage discipline
  remains.

Validity: until Metnos reaches a stable release (likely 1.0 after
phase 4+).

## Alternatives considered

**Treat backward compat as discipline from day one.** Pro: builds
the muscle for post-1.0; no surprises later. Con: pure debt during
pre-1.0 — code duplication, slower refactors, premature stability
that hardens decisions still being explored. Rejected.

**No backward compat ever, even after 1.0.** Pro: maximum freedom
forever. Con: post-1.0 there will be users (other people, possibly
external, depending on how Metnos evolves); breaking them silently
is a known failure mode of small projects. Rejected.

**Per-feature compat decisions.** Pro: nuanced. Con: each refactor
re-asks the question; the default human bias under time pressure is
"add the shim", which is exactly the cost we are trying to avoid.
A blanket rule cuts the question. Rejected.

## Consequences

Refactors that would otherwise be costly become routine. The naming
convention (ADR 0045) changes ~15 executor names atomically with no
shim layer. The vectorial-by-default change (ADR 0041) replaces the
single-item signatures of `move_file`, `create_dir`, etc. without
keeping the old signatures around. Both refactors take hours instead
of days because there is no compatibility surface to maintain.

The discipline has a companion: since refactors are cheap, they
happen more often, which keeps the design clean. ADRs 0017
(simplicity) and 0019 (single-author + AI feasibility) interact:
AI-assisted refactor of mainstream Python is cheap precisely because
there is no shim layer to coordinate; the savings compound.

Trade-off: a future contributor returning to the codebase mid-
refactor sees the broken state. The mitigation is that refactors
are scoped to single sessions when possible, and the
docs-always-aligned discipline (ADR 0032) ensures the canonical
documents reflect the new design before the session closes.

When does the rule expire? At 1.0, defined as the first stable
release with users beyond Roberto. After that, breaking changes go
through a minor-version contract. Until then, the project flies
without a compatibility net, by design.
