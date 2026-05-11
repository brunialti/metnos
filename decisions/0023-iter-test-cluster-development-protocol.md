---
id: 0023
title: Development protocol — iterate, update DB tests, validate cluster
date: 2026-04-26
status: accepted
area: process
related:
  - 0029
---

## Context

By 26 April 2026 the project had 12 modules in the SQLite-backed test
framework (ADR 0029) with explicit dependency edges between them,
producing automatic clusters of "module + 1-hop neighbors". The pool
held 93 test cases at first run, all green. The framework existed; a
discipline for *using* it during development did not.

Without a discipline, two failure modes recurred. First, modifications
broke modules that depended on the modified one (e.g. agent_runtime
broke when prefilter was changed; every executor broke when sign was
changed) and the breakage was not noticed until much later. Second,
when a test failed, the temptation to "see if the next iteration
fixes it" was real — but each unaddressed failure compounded.

The protocol was set on 26 April 2026 to make the test framework
operationally load-bearing instead of cosmetically present.

## Decision

Five-step protocol applied to every module modification, regardless
of size.

**1. Update test cases in the DB.** When the modification adds or
changes a method or behavior, add or update the cases that exercise
it. If `foo()` is added, add cases for happy / edge / failure. If a
behavior is changed, update the case that verified it (and add a
case for the new behavior if different).

**2. Run module tests** with `runner.py module <name>`.

**3. Run cluster tests** with `runner.py cluster <name>` to catch
regressions in modules that depend on the modified one or that the
modified one depends on.

**4. Iterate until green.** If something fails, do not stop at the
first attempt. Diagnose, fix, re-run. Each iteration is both a
validation and a learning signal about the model.

**5. When the cluster is green, the modification is stable.** Move
to the next module or the next feature. If implementing a chain of
components (e.g. adaptive K, then scratchpad in sequence), the green
cluster of each step authorizes the next.

A few specific rules to avoid edge-case scumming:

- For small modifications, run at least the module-level tests. The
  difference between "I changed three lines" and "I changed three
  lines and validated" is a few seconds of run time, but catches
  regressions that would surface much later.
- When a modification exposes a coverage gap (no test exercises the
  edge case just discovered), *add the test that captures the bug
  first*, then the fix. This way the bug cannot re-emerge silently.
- A failing test that resists the first fix attempt: keep iterating,
  do not disable. Disabling a test means accepting the bug.
- A task is not "done" until the cluster of the modified module is
  green.

If multiple modules are touched in the same session, the closing
move is `runner.py all` for global confirmation.

The protocol is paired with ADR 0024 (test failure means fix the
code, not the test): together they enforce the loop-discipline that
turns the test framework into a real safety net.

## Alternatives considered

**Run all tests on every change.** Pro: maximum safety. Con: full
suite takes ~50–90 seconds; running it for every three-line change
becomes friction that erodes the discipline. Cluster scope (~30s)
is the tradeoff that catches 90% of regressions in 30% of the time.
Rejected.

**Run only the modified module's tests, not the cluster.** Pro:
fastest. Con: misses the regression class that motivated the cluster
in the first place — modules that depend on the modified one or that
the modified one depends on. Rejected.

**Manual judgment per change** (the developer decides whether to
run tests). Pro: maximum flexibility. Con: the default human bias
under time pressure is "skip the test, ship the change"; the
discipline collapses. Rejected.

**No incremental updates to the test DB during development**,
periodic refreshes. Pro: less per-session overhead. Con: tests drift
from the code; new behaviors go untested; the framework's coverage
degrades silently. Rejected.

## Consequences

The framework usage in practice on 26 April: ~70 latency tests at
20–100 ms each, ~17 birth tests at 80–100 ms each (filesystem) or
1.4–1.7 s each (network for `web_fetch`), ~9 cluster integration
tests at 200 ms – 5 s each, ~10 e2e tests at 1.4–5.8 s each. Total
all-93 in 50–90 s. The cluster-scope runs are the inner loop of
development; the all-run is the closing move of a session.

A pattern that emerged from the protocol: modifications often
require *two* fixes, one to the code and one to the manifest. For
example, a missing affinity term in `fs_read` was caught when the
cluster test exposed that the planner could not select it for a
specific query. The framework's coverage made the manifest gap
visible, which a code-only test would have missed.

The protocol scales by adding new modules to the DB
(`seed_modules.py`) when they appear and adding cases
(`populate_cases.py`) when behaviors change. The seed scripts are
the canonical place; ad-hoc tests outside the DB do not count
toward the cluster.

A future improvement queued: `runner.py changed`, which auto-detects
modified modules via mtime/git and runs module + cluster
automatically. v1.2 work item.
