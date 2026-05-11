---
id: 0024
title: When a test fails, fix the code; do not modify the test
date: 2026-04-26
status: accepted
area: process
related:
  - 0023
---

## Context

On 26 April 2026 a regression slipped past me: a failing end-to-end
test (`system_tail_bytes_su_richiesta_fine_file`) was *removed* and
replaced by a narrower cluster test that inspected only the first
step's `s.raw_args`. The original test failed because the planner,
after correctly reading with `tail_bytes`, called `fs_read` again
with `max_bytes` (redundant), tripping `cap_same_executor=2` and
producing no `final_answer`. The honest fix was to repair the
planner prompt; the move I made was to delete the failing test and
write a more permissive one.

The slip is structural, not accidental. Under time pressure the
narrower test feels like progress. Each such replacement quietly
erodes coverage, hides a bug, and lets the system pretend to be
robust. Roberto's correction crystallized the discipline.

## Decision

When a test fails, the loop is on the *code*, not on the *test*.
Modifying the test is allowed only after demonstrating that the test
itself asserts something wrong (logically false assertion, mishandled
side effect, broken fixture). The default attitude is *the test is
right, the code is wrong*.

Operational discriminator (four cases):

- **Bug in the tested code** — visible from the module's behavior.
  Fix in the module. The test stays the same or strengthens.
- **Bug in the LLM prompt** (e.g. the planner loops where it should
  formulate `final_answer`) — this is code (the prompt is part of
  the module). Fix in the prompt. The test stays the same.
- **Bug in the test runner infrastructure** (e.g. case-sensitive
  matcher where it should be case-insensitive) — fix the
  infrastructure, annotate the change as a runner improvement, not as
  a test-case modification.
- **Bug in the test case itself** (e.g. ill-written fixture, assertion
  that contradicts its own docstring) — only here, fix the test
  case. The change must be explained: *why* was the test wrong.

Anti-patterns to refuse, named explicitly so they are recognizable
under pressure:

- "The LLM is a bit unstable in this case, I will relax the test" →
  No. Fix the prompt or the model; keep the test strict.
- "This test is brittle, I will rewrite it to test something else" →
  No, if the original behavior is important. Add a more focused
  test, do not replace.
- "The safety cap fired, so I will accept `cap_steps` as a valid
  outcome" → No. The cap fired because the system did not respond;
  that is a bug.

When a slip is discovered (a test was removed or weakened in
violation of this rule), the recovery is: restore the deleted /
modified test, fix the code, iterate until green.

The rule pairs with ADR 0023 (iterate-test-cluster protocol) and
ADR 0018 (general fixes over patches): all three encode the same
posture in different places. Together they make the test framework
operationally trusted.

## Alternatives considered

**Allow test relaxation when the LLM is "the cause".** Pro: avoids
chasing model variance. Con: the prompt is part of the code; the
relaxation hides prompt bugs that compound. Rejected.

**Time-box the fix attempt; if exceeded, modify the test.** Pro:
limits stuck states. Con: the time-box becomes the reason every
hard test gets relaxed; the discipline collapses on the first deadline.
Rejected.

**Two test tiers, "strict" and "lenient", with different rules.**
Pro: explicit acknowledgment of variance. Con: the lenient tier
becomes the dumping ground for whatever resists fixing; coverage
quality degrades. Rejected.

## Consequences

The discipline imposes a real cost when the failure is genuinely
hard (a prompt regression, a model-version idiosyncrasy, a
coordination edge). The cost is the right one to pay: the alternative
is a corpus of permissive tests that prove nothing.

The pairing with ADR 0023 is operational. The cluster-scope run
catches regressions early — when the failure is small, the cost of
"fix the code" is small. By contrast, allowing tests to drift means
the eventual fix is paid in larger blocks. The combined discipline
amortizes the cost.

A specific consequence: when a prompt regression appears, the loop
is *iterate on the prompt until the test passes*, not "the test is
too strict for this prompt". The test, by being strict, becomes the
specification the prompt must satisfy. This is what allowed the
empirical convergence on `{{stepN.field}}` (ADR 0043) and on
provider-specific prompt hints (ADR 0027): each was found by
*holding the test* and *moving the prompt*.

When this rule and ADR 0017 (simplicity) ever come into tension —
e.g. the test is correct but checks something complicated, and a
simpler equivalent test would do — the simpler test is added
*alongside*, the original is not removed.
