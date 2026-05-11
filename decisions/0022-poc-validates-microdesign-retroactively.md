---
id: 0022
title: A validated POC retroactively approves the corresponding microdesign
date: 2026-04-26
status: accepted
area: process
related:
  - 0021
  - 0032
---

## Context

The early discipline for the corpus was *document first, code after
approval*. That rule had served well for high-level architecture
(philosophical choices, constitutional principles, telos), where
rushing into code without the conceptual frame produces incoherent
software. But it became awkward at low levels — microdesign of single
modules, concrete schema of components, interface contracts — where
the speculative document often gets contradicted once code exists.

By the end of the POC v1.1 cycle (26 April 2026) several microdesign
documents had drifted: the speculative `agent_runtime.html` described
a tool-call flow that did not match the working code, the
`scratchpad.html` shape was different from the implemented one. The
choice was either to keep documents authoritative (and rewrite the
code to match speculation) or to let working code authoritative the
documents (and rewrite the documents).

Roberto's framing on 26 April 2026 was that the second is more
honest. A microdesign written *before* the POC is a guess; a
microdesign written *after* a validated POC is a specification of
what works. The first carries less weight than the second.

## Decision

A bidirectional workflow, level-conditional.

**At low level (microdesign of concrete modules, interface contracts,
operational schemas), a validated POC retroactively approves the
corresponding microdesign.** When the test framework is green and
the architectural tensions on that module have been resolved, the
microdesign document is updated to reflect what the POC actually
does. That document then becomes APPROVED, not draft.

**Validated** means: the module's tests pass plus the cluster's
tests pass, no architectural tension remains open, and the relevant
decisions have been ratified (see the post-POC ratification pattern
in `metnos_design_decisions_post_poc.md` → ADRs 0042, 0043, 0044).

**Level matters.** The bidirectional rule applies to microdesigns of
concrete modules (executor, agent_runtime, prefilter, vaglio,
sandbox, scratchpad, router, etc.) and to operational schemas
(manifest, config, policy registry, capability registry). It does
*not* apply to higher-level documents — telos, constitution,
perspectives of judgement, dialogues, the introductory chapters 1–3
of Architettura, the four Laws. Those remain doc-first.

**Language requirement.** Microdesigns approved through POC must be
rewritten in much simpler, more concrete language than the
speculative version. Concrete terms, examples lifted from the POC
code, visual schemas where they help. No theory unless the code has
already pushed it. Each POC-approved microdesign declares at the
top: validation date, POC reference (session/commit), and the test
cases that confirm it.

The rule pairs with ADR 0021 (POC as architectural cycle): the POC
is what produces the validation, the validation is what authorizes
the document.

## Alternatives considered

**Strict doc-first at all levels.** Pro: consistency, the doc is
always authoritative. Con: low-level docs are guesses until tested;
treating a guess as authoritative produces self-deception. Code
that contradicts an authoritative doc is judged "buggy" when the
honest reading is "the doc was wrong". Rejected.

**Strict code-first at all levels.** Pro: simplicity. Con: high-level
choices (telos, constitution) cannot be settled by code; they
require human deliberation and can only be expressed as documents.
Rejected.

**Neither — abandon the doc/code synchronization rule.** Pro: zero
overhead. Con: the corpus drifts; the user model degrades; the
project becomes navigable only by reading code. The corpus is the
project's identity; abandoning its discipline is abandoning the
project's voice. Rejected.

## Consequences

Several v1.1 microdesigns were promoted to APPROVED through this
rule after the POC of 26 April: `executor.html`, `agent_runtime.html`,
`scratchpad.html`, `vaglio.html`, `policy.html`. The promotion
happened in the same session as the POC closure, not in a follow-up
batch.

The doc-alignment discipline (ADR 0032) is the natural companion:
when a POC cycle closes, the doc update is treated as part of the
cycle, not as a deferred backlog item. The same session that turns
the test green also turns the doc into APPROVED.

The language simplification — from speculative to operational — has
a side effect that turns out to be valuable: the POC-approved
microdesigns are easier to read, easier to translate (ADR 0048), and
easier to use as input for new contributors / future Claude sessions.
The "concreteness via POC" carries forward into the corpus's voice.

A trade-off: when an APPROVED microdesign needs a substantive change
(architecture evolves, the POC ages), it must be re-cycled through
the same procedure (write/break/re-discuss/re-write/re-validate),
not edited as a stable document. This is correct but expensive,
and it argues for keeping POC cycles small enough to be re-runnable.
