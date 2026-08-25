---
id: 0224
title: Single deterministic birth gate with a synthesized-executor review branch
date: 2026-08-25
status: proposed
area: executor | synt | signing | policy
related:
  - 0114
  - 0196
complements:
  - 0223
  - 0220
---

## Context

RM-0007 created an authenticated, immutable and atomic publisher, but several
trusted producers could still reach that publisher directly. Human-authored
executors, Synt candidates, imported skills, specializations and maintenance
updates performed overlapping but different admission steps.

The audit also found that a failed semantic verifier could be treated as a
warning, that some unavailable deterministic checks were interpreted as a
pass, and that the promoter trusted the broad state `synthesized` instead of
evidence tied to the exact candidate. Tests proposed by the code-generating
model could test the same misconception and their shell setup ran outside the
runtime sandbox.

Publication integrity is therefore necessary but insufficient. An immutable
generation can faithfully preserve a badly admitted executor. The remaining
problem is to establish one admission authority before publication without
turning the publisher into a package manager or asking local models to
implement security policy.

## Decision

Every executor which is about to become `active` crosses one public birth
boundary, independent of its origin. Generators create only quarantined
candidates. They cannot select a live lifecycle, grant authority, sign or
publish.

The birth boundary snapshots the complete candidate and binds every applicable
check to a deterministic candidate digest. It runs structural, policy,
localization, routing and test-shape checks for every origin. `failed` and
`unavailable` both prevent activation.

Certification is binary: every applicable mandatory check and test must pass.
Origin and component labels help diagnosis but never permit a partial pass or
reduce the effect of an error.

Semantic alignment is required only for executors synthesized by Metnos. It is
the only model-mediated check in that branch. A separate logical workload,
`executor.birth.semantic_review`, has a minimum tier of `wise` and may route
to frontier. It reads purpose, contract and code and emits a typed opinion plus
independent test cases. It has no signing or publication authority. Its
`misaligned` and `uncertain` outcomes block automatic progress.

The runtime executes accepted test cases in a real sandbox, with no real
network, credentials or personal data and with declarative fixtures instead
of model-authored shell setup. Tests written only by the code-generating model
are never sufficient evidence.

For a synthesized executor, human approval bound to the exact candidate digest
is mandatory for new code, code changes, operational-contract changes and new
or increased authority. Builtin, core, imported and directly human-authored
executors retain their existing review and trust boundaries; they cross the
same deterministic gate but do not enter the Synt review cycle.

An eligible, non-mutating synthesized executor may enter a bounded pre-exercise
state after deterministic admission, semantic review and isolated tests. A
negative user assessment hides the exact generation immediately and triggers a
mandatory frontier review of the failed query, arguments, output, contract and
code. A failed or unavailable review leaves it quarantined. A repair is always
a new candidate and crosses the complete gate again.

Once admitted, the existing RM-0007 publisher signs and commits the immutable
generation. A birth report records evidence and the active generation but is
not a second cryptographic authority: the loader continues to trust the signed
manifest and authenticated generation.

Low-level publication APIs remain implementation details. A static boundary
test rejects operational producers which bypass the birth module. Candidate
storage, retirement, rollback to an already admitted generation and the
one-time legacy cutover retain their dedicated, non-birth boundaries.

The complete candidate specification is RM-0008,
`internal/roadmap/RM-0008-porta-unica-nascita-executor.md`. It remains under
analysis until adversarial review converges; this ADR does not authorize
implementation.

## Alternatives considered

### Teach every generator the complete publication procedure

Rejected. It duplicates policy and makes safety depend on prompt quality and
model capability. A model could also mistake a claimed check for evidence.

### Put every check directly inside the immutable publisher

Rejected. Candidate review may be slow and may require human input, while the
publisher must keep a small atomic commit boundary. Admission and commit are
one public operation but remain separate internal responsibilities, joined by
the exact candidate digest.

### Keep automatic promotion and rely on a grace-period rollback

Rejected. Rollback observes failures after authority has already been granted
and cannot reveal quiet semantic errors. Grace remains useful operationally,
but it is not admission evidence.

### Require a frontier model for every executor

Rejected. Model review applies only to synthesized executors. Their normal
semantic review requires an independent high tier, not a vendor or one fixed
model. Frontier remains available for complexity and is mandatory for
post-failure review; lower tiers may not be substituted silently.

## Consequences

- Local models may remain useful even when imperfect: poor output is rejected
  or held for review rather than activated.
- New synthesized code and new synthesized powers require one explicit human
  decision, but all mechanical work before and after it is automatic.
- Current fail-open Synt checks and direct publisher call sites must be
  removed or redirected.
- Model-authored shell fixtures are retired for generated candidates.
- Builtin, core, imported and directly human-authored executors are not
  subjected to Synt semantic review, pre-exercise or frontier failure review.
- The static RM-0007 boundary inventory expands to recognize exactly one
  operational birth owner.
- RM-0007 remains the commit mechanism and RM-0002 remains the linguistic
  validator; this decision coordinates them rather than duplicating them.
