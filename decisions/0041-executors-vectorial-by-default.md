---
id: 0041
title: Executors vectorial by construction; single item is the degenerate case
date: 2026-04-27
status: accepted
area: executor
related:
  - 0014
---

## Context

On the evening of 27 April 2026 a live test exposed a structural
issue. Roberto had asked Metnos to sort 98 photos by year (turn
`abb3a4d1`). The available `move_file` was single-item. The planner
(Gemma 4 26B) saw the situation and *did not call any tool*: it
produced a final-message asking Roberto whether to proceed. The
chain "98 individual `move_file` calls" exceeded the planner's
`cap_steps`, so the planner rationally chose to give up.

The fix in the moment was a `move_files_batch` work item. But on
reflection the broader question was clearer: *every* executor
should accept a list as its natural input, not just `move_file`.
Operations on homogeneous collections are common; planning a step
per item floods the ReAct loop.

The decision was made on the same evening to flip the default. Not
"single item with optional batch sibling" but "vectorial by
construction; N=1 is the degenerate case".

## Decision

Every executor accepts input as a list. The N=1 case is degenerate;
N=0 is permitted (a no-op with structured report). No `*_batch`
sibling executors — the executor *is* the batch version.

**Why this is the right boundary.** Iteration over a homogeneous
collection lives *inside* the executor. Iteration that depends on
results step-by-step (conditional loops, branching on previous
observation) belongs naturally to the planner / composer (the
ReAct nature). The split is: vectorial-by-default for homogeneous
operations on collections; conditional iteration in the planner.

The planner works well when it can pass the output of a vectorial
producer (`list_dir`, `find_file`, `filter_entries`) directly to a
vectorial consumer (`move_files`, `read_files`) without `for` loops
in the plan.

**Operational rules:**
- Every new executor: natural input is a list (`paths`, `entries`,
  `urls`). Output is a list or an aggregate
  (`{ok, ok_count, fail_count, results, failed}`) for best-effort
  actions.
- Refactor the existing single-item seeds (`move_file`, `create_dir`,
  `fs_read`, `fs_write`, `web_fetch`, ...): drop the single-item
  signature. Enabled by ADR 0031 (no backward compat in dev).
- Naming: plural when it helps (`move_files`, `read_files`); or
  keep singular with the convention "always a list" stated
  explicitly in the manifest description (ADR 0040).
- Special cases (e.g. `time_read` with no input, executors with an
  intrinsically scalar input that never benefits from a list):
  evaluate; default is "yes, accept a list" if it usefully bundles
  multiple requests in one round.

The `*_batch` proposal is therefore *not* a separate executor;
it is what every executor already does. The work item
"move_files_batch" of `metnos_session_resume_28apr_v3` is dropped:
its content is "make `move_file` accept a list", already part of
this rule.

## Alternatives considered

**Keep single-item, add `*_batch` siblings.** Pro: explicit; the
planner can choose. Con: doubles the catalog; the planner has to
discriminate "do I have one or many?" before picking; for typical
N>1 cases the planner picks wrong (uses single-item, hits cap).
Rejected.

**Keep single-item, let the planner loop.** Pro: clean per-step
semantics. Con: 98-photo sorts exceed `cap_steps` cleanly; the
planner correctly gives up; user experience is "Metnos cannot
handle moderate collections". Rejected.

**Make this a design rule for *some* executors, not all.** Pro:
flexibility for cases where vectorial does not fit. Con: the rule
becomes case-by-case; the planner can no longer assume "any
executor takes a list"; it has to read each manifest. The benefit
of a uniform rule is the planner's *expectation*. Rejected as an
exception-allowing rule.

## Consequences

Refactor work in the seed pool: `move_file` → `move_files`,
`create_dir` → `create_dirs`, `fs_read` → `read_files`, `fs_write`
→ `write_files`, `web_fetch` → `fetch_urls` (all with vectorial
signatures). Some of these renames also align with ADR 0045 (naming
convention) — done in the same refactor batch.

Output shape of best-effort vectorial operations becomes uniform:
`{ok, ok_count, fail_count, results, failed}`. The planner learns
this shape once; it applies to every vectorial executor.

Synt benefit: code generated for new executors does not have to
handle higher-level iteration, only the per-item logic plus a
standard wrapper. The wise-tier prompt for `synt.generate`
(ADR 0027) already includes "make the function accept a list of
inputs"; the convention is now structural rather than per-prompt.

A concrete example: a sort-photos request planned by Gemma 4 26B
becomes a 3-step plan (`list_dir` → `filter_entries` →
`move_files`) regardless of how many photos there are. 98 photos,
1 step of `move_files`. The cap pressure dissolves.

The pattern interacts with ADR 0014 (granularity heuristics): a
vectorial step counts as one hop regardless of collection size,
which is what allows the 5–7 hop budget to handle real workloads.
The vectorial decision is what makes the granularity heuristic
viable in practice.
