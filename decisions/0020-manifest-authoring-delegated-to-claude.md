---
id: 0020
title: Manifests and derived artifacts authored by Claude, reviewed by Roberto
date: 2026-04-26
status: accepted
area: process
related:
  - 0019
---

## Context

Each executor in Metnos ships with a TOML manifest — name,
description, args schema, capabilities, target_kind, profile, sandbox
hints, error classes, signature placeholder. Multiplied by the seed
pool (ADR 0015) that is 27 manifests at boot, growing as the synt
generates new ones. Each manifest takes on the order of 30 minutes
of careful prose to write well, more if the args schema is unusual.

After 26 April 2026 the design decisions of the executor schema
(v1.1) and the granularity heuristics (ADR 0014) were closed. Writing
the manifests for the seed pool was now a *derivative* activity: it
followed mechanically from the closed decisions, with only edge cases
requiring fresh thought. Asking Roberto to type each one was a poor
use of his time when the bottleneck was already the design closures
he had just delivered.

The rule is the practical instantiation of ADR 0019: derivative
artifacts that follow from closed decisions are appropriate AI
authoring; new decisions are not.

## Decision

Manifests of executors are authored by Claude on initiative,
applying the v1.1 schema. Roberto reviews; his corrections become
the canonical version.

Operating rules:

- When a session introduces a new executor, Claude writes the
  complete TOML manifest draft applying the canonical schema
  (`metnos_executor_schema_v1.md`).
- Roberto reviews and corrects; corrected version is canonical.
- If the schema does not cover a case (a missing test matcher, a
  capability not yet in the registry, an ambiguous hint scope),
  Claude raises the question rather than inventing by inertia.
- Claude does not digitally sign manifests — only Roberto holds the
  author key. Until the code is written and signed, `code.digest`
  remains a placeholder.
- The pattern extends to other artifacts derived from already-closed
  decisions: config templates, declarative test cases, policy
  schemas, documentation entries that follow a fixed template. For
  artifacts that require fresh design decisions, Claude stops and
  asks.

## Alternatives considered

**Roberto writes every manifest.** Pro: full ownership of every line.
Con: an obvious mismatch between the bottleneck (Roberto's hours of
*decision*) and the activity (typing schemas). Cost-on-Roberto for
zero design value. Rejected.

**Claude writes manifests with no review.** Pro: maximum throughput.
Con: a manifest is a contract; subtle errors propagate (a wrong
default, a missing capability) into runtime behavior. Review is the
quality gate. Rejected.

**Lazy manifests (write only when an executor is implemented).**
Pro: avoid premature commitment. Con: the manifest is also the
spec the implementation works against; without it, the
implementation invents its own contract. Rejected.

## Consequences

Throughput on derivative artifacts is multiplied without consuming
Roberto's design hours. The review pattern stays sharp because
review is faster than authoring; corrections become a learning
signal that improves Claude's drafts over time.

The escalation rule is load-bearing: when Claude encounters a case
the schema does not cover, raising it is non-negotiable. Inventing
by inertia is the failure mode that turns delegation into
unsupervised drift. The clearest signal that escalation is needed:
the draft would require explaining a new concept in the description
rather than naming an existing one.

Boundaries:
- Hand-written executor *code* is not delegated wholesale; the
  design hooks (capability declaration, profile, error classes)
  carry decisions that Roberto signs off on.
- Synt-generated executors run through the full synt pipeline (spec
  + skeleton + birth tests), not through this manifest-authoring
  pattern.
- Documentation that introduces concepts (canonical microdesigns,
  the dialogues, telos updates) is *not* delegated; those are
  decision artifacts, not derivative ones.

The delegation is reversible: when an executor schema migrates (e.g.
the v1.1 → v1.2 transition) the rule pauses until the new schema is
reviewed.
