---
id: 0215
title: Reference-flow certification and domain-neutral causal repairs
date: 2026-08-23
status: accepted
area: runtime | testing | language | recovery
related:
  - 0177
  - 0183
  - 0201
  - 0213
  - 0214
---

## Context

RM-0006 replaced a generic request for a large stress test with a logical
certification of observable outcomes. Unit and integration suites already
proved many individual mechanisms, but they did not show that a natural
request crosses routing, planning, authority, placement, consent, execution,
recovery, response and postcondition correctly as one product path. Counting
tests or executors would not answer that question.

The diagnostic cycles exposed three failure classes. A device marker could be
selected even when it occurred in a negated clause. A planner could schedule
the exact inverse executor after a creation but omit the dataflow reference to
the created identifiers. The explicit compatibility entry for a registered
LRE plan could lose turn identity across approval and resume. Fixing individual
Italian or English sentences, calendar calls, device names or fixture values
would have made the certification pass without repairing the product.

## Decision

Metnos adopts a frozen matrix of 24 reference flows, each expressed in Italian
and English, as an end-to-end logical certification. An external deterministic
coordinator drives only the normal HTTP turn boundary. It records redacted
observations and evaluates plans, placement, authority, consent, effects,
terminal state, response requirements and fixture postconditions against an
oracle frozen before the qualifying run. Device flows use the real Rust client
against an isolated Metnos server. Durable flows use the production compiler,
storage, leases, fencing and artifact store; only OCR and model output are
deterministic fixtures. Real providers remain outside the repeatable matrix and
are checked by at least four separate non-destructive probes.

The three causal corrections are domain-neutral. Syntax polarity lives in the
translatable detection lexicon and is evaluated by one
`detection_lexicon.polarity_state_at` clause rule. Negation, inhibition,
contrast, negative coordination, sequence, and command invocation are native,
human-reviewed safety resources. The result is tri-state: asserted, negated,
or unavailable. Safety consumers fail closed when the active language lacks a
ready native resource. Domain recognizers keep only their surface markers and
cannot introduce private lists of negators.

Target placement consumes an ordered stream of normalized mentions. A later
explicit correction replaces an earlier target, while a final revocation
prohibits remembered or default placement. Strong server, local, or named
device mentions outrank weak aliases; overlapping surfaces of the same target
prefer the strongest and longest form. The same check runs even when no device
is registered, so a negated target constraint cannot fall into a target-blind
path.

When a framework explicitly schedules an inverse executor but omits its
target, the execution boundary may derive arguments only from a preceding
committed result's standard `_undo.reverse_pattern` envelope. The generated
call must name the exact inverse executor, be unique and be projected through
the consumer's declared schema. An explicit target or `from_step` always wins.
No domain, provider, executor pair or natural-language phrase participates in
this repair.

The technical `start_lre` compatibility entry is deterministic only when the
loaded executor schema exposes exactly one profile repeated by the request and
the request contains at least one absolute source path. It always uses the
canonical approval gate. Turn and source-request identifiers are stored and
propagated through every resume branch so the approved work remains one
idempotent request. Ambiguous technical input returns to the ordinary planner;
it is never guessed from a phrase table.

RM-0006 is complete only after all 96 required cases pass in two consecutive
cycles on the same frozen matrix and effective code surface, the real probes
pass, and a verifier outside the Metnos processes inspects the summary,
failures and a success sample for every family. Diagnostic runs before the last
correction are not qualifying evidence.

The qualifying run passed 96/96 cases on technical revision
`201342f1269fd366270ee1523f4bca94dd59c393`; its append-only result registry has
SHA-256 `a7bc2edd8d64b5584a77331f78573a64becb95fa813dbc0e9f88586bcb409c6d`.

## Alternatives considered

**Add request-specific fast paths.** This would lower test latency but create a
new semantic branch for every troublesome wording. It was rejected because it
does not generalize across languages and bypasses the same planner and runtime
whose behavior the certification must measure.

**Infer inverse targets from executor names or result-shaped heuristics.** A
calendar-specific or file-specific mapping could repair the observed cases,
but it would let unrelated result fields authorize mutations. The sealed undo
envelope is the only existing producer contract that names both the inverse
operation and its identifiers.

**Keep polarity inside each recognizer.** Separate negation lists would drift
across device, placement and future domains and would contradict localisation
as data. A shared syntax concept and a single clause rule preserve one
algorithm while allowing new languages to supply resources.

**Replace real boundaries with mocks.** Fully mocked device and durable paths
would be faster, but could hide protocol, ownership, lease, fencing and artifact
contract differences. The selected fixture isolates state while retaining the
real product boundaries.

## Consequences

The qualifying certification completed 96 of 96 cases with no exclusions,
errors or failures. Five safe real-boundary probes also passed. The matrix,
suite, catalog, LLM configuration and executed product surface have independent
SHA-256 identities, and the append-only results registry has its own digest.

The certification remains a logical release gate, not a load, soak or model
quality benchmark. It does not implement RM-0005: Italian and English coverage
cannot prove bootstrap and automatic localisation of an arbitrary new instance
language. Future changes to routing, planning, authority, device placement,
dialog resume, undo or durable execution must run the affected regression and
the reference-flow gate in proportion to risk; an oracle change requires an
explicit review before it can qualify a new run.
