---
id: 0021
title: POC as a cycle on architecture, not as a build sprint
date: 2026-04-26
status: accepted
area: process
related:
  - 0022
---

## Context

The proof-of-concept work for Metnos v1.1 began on 26 April 2026 with
a tendency to act like a build sprint: a fixed design at the top, a
linear march through implementation tasks, an attempt to close every
architectural ambiguity *before* writing the next line of code. The
counterpart failure mode — designs that get coded too late and
collapse on contact — was already familiar from prior projects.

The POC sits between two opposite risks. The first is the *ambitious
design never built*: too much architecture, too little code, the
design ossifies into something untested. The second is *the design
closed too early*: every ambiguity gets resolved on paper before the
runtime has a chance to push back, and the resolutions ossify before
they have evidence behind them.

Roberto's framing on 26 April 2026 was sharp: *the POC's purpose is
to cycle on architectural choices when problems surface at runtime;
it is not a sprint to implement a fixed design*. The POC is the
feedback loop that validates or invalidates design choices.

## Decision

Two operating rules during POC work.

**Close only what blocks the next concrete line.** When ambiguities
surface during POC coding, do not stop to close them all in advance.
Close only the ones that physically block the next line; leave the
others to be resolved by the runtime itself.

The discriminator: for each decision Claude is about to close
preemptively, ask *"will this block the next concrete step, or only
the next abstract step?"*. Only the first must be closed.

**A blow-up at runtime is the right signal to revisit.** When an
ambiguity explodes (a test fails, behavior is unexpected, a contract
breaks), *that* is the signal that the design choice deserves
revision. Not before. The POC of v1.1 is expected to produce 3–5
cycles of "write → break → re-discuss architecture → re-write". That
cadence is value, not noise.

The corollary is that decisions invented on the fly during POC
coding should be *surfaced as emerging tensions*. The right form is
a memory file (`metnos_poc_*_tension.md`) that explains the case,
the choices, and the suggested default — to let Roberto see them and
decide whether they merit a real architectural conversation.

The discipline pairs with ADR 0022 (POC validates microdesign
retroactively): when a cycle stabilizes (test framework green,
tensions resolved), the corresponding microdesign document is
promoted to APPROVED, in language that reflects the working code
rather than the speculative pre-POC plan.

## Alternatives considered

**Close everything in advance, code last.** Pro: clean separation
between design and code. Con: the decisions get made without
evidence; the runtime exposes assumptions the design did not; the
fix-up phase is then a quiet rewrite of what was supposed to be
"settled". Rejected.

**Code first, document never.** Pro: maximum velocity. Con: the
design vocabulary the project depends on (telos, vaglio, mnest,
scratchpad) cannot live in the code alone; without the documents
the dialogues become unanchored. Rejected.

**Strict TDD over POC** (write the test first, code the bare minimum
to pass it). Pro: classic discipline. Con: the POC's goal is to
*discover* what to test, not to validate against a known oracle.
TDD presumes the test is correct; the POC is exactly the place where
the test contract is being formed. Rejected as the primary
discipline; kept as a tool when the test is well understood.

## Consequences

The POC of v1.1 produced exactly the expected pattern: ~3–5 cycles
of write/break/re-discuss/re-write before the runtime stabilized at
4 implemented executors, complete runtime, 6/6 end-to-end query OK.
Each cycle exposed a real architectural decision that would have
been wrong in advance:

- the data-piping syntax `{{stepN.field}}` (ADR 0043) was not in the
  pre-POC plan; it emerged from a runtime failure;
- native tool-use (ADR 0042) was not the default in the pre-POC
  plan; it surfaced from the empirical test that prompt-based JSON
  was fragile;
- the wise-tier quality floor (ADR 0026) emerged from the actual
  failure of qwen3:8b on `extract_email_addresses`, not from
  speculation.

The discipline also normalizes the relationship between POC and
documentation. The microdesign documents (`agent_runtime.html`,
`scratchpad.html`, `synt.html`) get rewritten *after* the POC
stabilizes, in language that reflects the working code. Specifications
without working code remain *under approval*, not approved.

Trade-off: this rule means the corpus is occasionally out of sync
with the current code, between a POC cycle and the doc rewrite. The
mitigation is the doc-alignment discipline (ADR 0032): when a POC
cycle closes, the doc rewrite happens in the same session, not on a
later backlog.
