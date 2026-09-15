# Protected source access and empty-selection presentation

Date: 2026-09-15. Candidate worktree: `rm0008-reboot`.

The tests below describe the original candidate work and are local/synthetic
unless explicitly identified otherwise. The implementation is now installed
in release 46; deployment and production mail/photo outcomes belong to
[the release ledger](rm0008-release-20260915.md), not these earlier tests.

## Capability-aware protected-path guard

### Cause

The guard treated every path argument of a `create_*` executor as a write.
Consequently, the image index builder's source under the host's protected
`/var` tree was rejected even though its signed contract declares
`fs:read` on `arg:base_path`, with output restricted to `metnos:cache`.
Both the engine and prerequisite admission already had the verified catalog
entry, but neither passed it to the guard.

### Correction and boundaries

- `runtime/vaglio.py`: the standard guard accepts an explicit keyword-only
  catalog entry, separate from invocation arguments and ordinary context.
  Matching active/non-dormant entries with a non-placeholder signer can expose
  exact typed `fs:read` argument bindings. Signature verification remains the
  loader's responsibility; this is not a substitute verifier.
- Read-only exclusions apply by top-level argument identity, never by the
  value of a path. A destination containing the same value remains checked.
  Conflicting or ambiguous filesystem-write authority grants no exclusion.
  Filesystem-write capabilities are checked even when the tool name looks
  read-only. Effective conditions use resolved arguments/schema defaults.
- Forbidden credential paths are checked before read-only exclusions. Explicit
  filesystem bindings are scanned even when their field is named `content`;
  filesystem symlinks and traversal are resolved for that scan. Ordinary prose
  remains content, preserving the existing no-false-access behavior.
- `check_executor_guard` passes authority only to the standard guard. Explicit
  injected two-argument callbacks remain compatible; an internal `TypeError`
  never triggers a second, less-restricted invocation.
- `runtime/engine/executor.py`: both parallel preflight and ordinary invocation
  supply the selected catalog entry. Guard exceptions deny execution instead
  of silently allowing it. Missing protected-path policy also denies access.
- `runtime/executor_prerequisites.py`: admission passes the exact target loaded
  with `verify=True` through the same adapter. No caller/result-supplied
  capability or recommended action is accepted as authority.

No executor name was disguised, no `/var` exception was added, and no sandbox
authority was widened. `runtime/agent_runtime.py` needed no guard wiring change.

Tests: `test_vaglio_capability_paths.py`, `test_engine_vaglio_guard.py`, and
`test_executor_prerequisites.py`, together with existing content/access,
safe-verb, engine, parallel and image-plan tests. Cases cover protected source
reads, arrays, unsigned/unavailable entries, caller injection, condition/default
handling, invalid bindings, secret paths, symlinks/traversal, mixed source and
destination bindings, unbounded writes and failing legacy callbacks.

## Empty selection must not become a duplicate work count

### Evidence and cause

The parent task's real isolated mail turn `9c6d418deaed4a9d` observed 14 source
rows, 14 classified rows and zero filtered rows. Its filter result contained
`ok=true`, `entries=[]` and `metadata`. The final text incorrectly reported
28 processed items. Only this redacted shape/count evidence is used here; no
mail content is reproduced.

The failure has two deterministic stages:

1. The list output policy correctly targets the terminal filter through
   `${step3.@table}`. The renderer converted an empty table into the bare
   number `0`; the engine accepted this as a complete template rendering.
2. `TurnLog.write` detected the bare number as a degenerate answer and used
   `effect_counts.items`, which sums intermediate read/transform work. Reading
   and classifying the same 14 rows therefore became a misleading result count
   of 28. An older upstream presentation could also hide the terminal filter.

This is not a reason to reinsert a model summarizer or alter classification.

### Correction and boundaries

- `runtime/pipeline_effects.py`: `terminal_collection_output` reads the most
  recent actual collection across engine/persisted log shapes. It does not sum
  intermediate rows. Scalar results, errors and side effects form terminal
  boundaries. Explicit count-only results remain countable without materialized
  rows. Existing effect/work accounting and mutation counts are unchanged.
- `runtime/engine/executor.py`: an empty `@table` is rendered as an honest
  localized result for its referenced step. An explicit `@count` remains numeric;
  nonempty tables, count-only totals, source notes, scalar reductions and
  executor-owned effect receipts retain their existing behavior.
- When a processor's structured `count_in > 0` and `count_out == 0` attest an
  empty selection, the renderer reuses the existing IT/EN `MSG_PROCESSOR_EMPTY`.
  A genuinely empty source retains `MSG_NO_RESULTS`. No query/domain heuristic,
  invented relevance explanation, new translation or production seed edit was
  introduced.
- `runtime/agent_runtime.py`: only the degenerate-final fallback and its helper
  import changed for this correction. It uses terminal output, not the sum of
  work, and does not revive an earlier reader's presentation. Successful and
  partial mutation accounting remains authoritative.

The Italian selection message explains that no items matched the requested
criteria; the English message conveys the same result. The existing message
includes the processor name. The implementation deliberately does not assert
that a particular person's mail is unimportant: classification quality and
source completeness remain separate concerns.

### Verification

Four explicitly simulated complete code paths covered Italian/English and
messages/files. They used the actual output policy, engine argument piping,
`filter_entries`, finalizer and `TurnLog.write`, with synthetic read/classifier
outputs and no model calls. Each processed 14 source rows and returned zero
selected rows. Internal work telemetry stayed at 28; the final answer described
the empty selection and did not present that work total as distinct results.

Additional regressions cover a truly empty source, explicit zero counts,
count-only positive totals without rows, referenced versus unrelated later
steps, source notes, scalar/failure/mutation boundaries, upstream stale hints,
nonempty repeated transforms (14 output rows rather than 28), and successful
mutation receipts.

Final groups, using `/opt/metnos/.venv/bin/python`:

- **309 tests plus 6 subtests passed in 5.40 s**: finalizer, empty entries,
  degenerate-final honesty, output policy, final-message invariants, core/from-step/
  parallel engine behavior, false-mutation protection, scheduled-push honesty
  and planner honesty.
- **112 tests passed in 0.44 s**: capability path guard, existing content/access
  and safe-verb guards, engine guard wiring, prerequisites and image-index plan.

These final groups are non-overlapping: **421 tests plus 6 subtests**.
An earlier extra structure-planning suite failed at
`test_guard_pipeline_contract::test_two_enforce_guards_compose_no_double_producer`
after 15 passing tests: the compound framework omitted the messages producer
before guard/invocation. That separate failure is not counted as a passing suite
and was reported to the parent task; its code is outside this correction.

The exact real mail turn was not reissued after this fix. A subsequent real
HTTP verification must remain separate from these simulated code-path tests.
