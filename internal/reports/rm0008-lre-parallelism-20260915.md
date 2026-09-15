# Supervised LRE parallelism and recoverable model reservations

## Scope and outcome

This report describes the original isolated candidate tests; all model usage
in those tests is synthetic. The implementation is now installed in release
46, including a correction for unnecessary writer locking on current-store
opening. Production deployment and real-model outcomes are recorded separately
in [the release ledger](rm0008-release-20260915.md).

The supervised service now uses available central scheduler lanes without an
independent pool, refills useful work without the idle polling delay, and
reuses thread-owned bindings during a bounded scheduling slice. Model units
can overlap only when their frozen token reservations fit atomically and all
existing resource, policy, identity, lease and plan limits permit it.

## Root causes

1. Parallel service lanes already existed, but `METNOS_DURABLE_WORKERS`
   defaulted to one. Both the legacy durable service template and the
   closed-build target recipe omitted the central scheduler opt-in used by
   HTTP. Changing the template alone would not affect the signed deployment.
2. Every lane executed one unit, closed its store and bridge, then waited for
   the next idle poll before reuse. Short work left capacity idle; recreating
   runtime bindings also reset their maintenance interval.
3. Storage explicitly excluded any second LLM/VLM unit in the same revision.
   The exclusion protected a real budget invariant: the previous preflight
   observed total remaining tokens but did not subtract concurrent promises.

No global execution lock was removed. Workers execute outside their SQLite
transactions. The store still performs atomic claim/fence operations and
enforces the frozen plan's `max_concurrency`; actual invocation still enters
the existing scheduler's resource, executor and identity gates.

## Candidate implementation

- `runtime/durable_workloads/service.py`: automatic lane count derives from
  the existing central capacity and existing ceiling. Explicit serial
  configuration and the central parallelism gate remain authoritative.
  Thread-owned bindings are reused until idle, error, stop, or the existing
  polling interval's scheduling slice ends; no fenced unit is interrupted.
  Progress wakes the supervisor. Generation-tagged completion notifications
  avoid losing a refill when an idle probe overlaps another lane's progress,
  without spinning when all work is idle. Shutdown, heartbeat and fencing
  behavior remain intact.
- `runtime/durable_workloads/storage.py`: an active fenced lease reserves the
  token bound from its immutable admitted catalog. The common SQL projection
  computes `R = max_calls * (max_input_tokens + max_output_tokens)` only for
  a unique, correctly bound, bounded zero-cost contract. Claim selection
  requires `used + outstanding reservations + candidate R <= budget` in the
  same immediate transaction that inserts the lease. There is no new schema,
  mutable model configuration lookup, process-local reservation counter or
  separate reservation lifecycle.
- Complete usage is persisted and added to revision totals atomically by the
  existing usage API; that attempt then has no outstanding reservation. The
  preflight subtracts other attempts' reservations, not its own, and checks
  the current fence, lease state, expiry, clock and accounting completeness.
  Expiry alone does not release an unaccounted reservation. Reconciliation
  preserves the existing fail-closed unknown-usage rule after the model
  boundary was entered.
- Missing, malformed, duplicate, metered or unbounded model contracts retain
  serial admission; no paid-model authority was added. A temporarily full
  reservation set does not prevent a smaller stage or another workload from
  being selected. An impossible reservation with no active model work enters
  `needs_attention` without creating an attempt or calling a provider, after
  runnable candidates have been preferred. Reported observed tokens remain
  actual usage, separate from the required reservation.
- The same storage file adds `find_active_submission(owner_user_id,
  scope_digest)`: canonical SHA-256 validation, exact owner/scoped payload
  match, nonterminal workloads only. Submission locking and wiring are owned
  by the parent task.
- `runtime/executor_birth_service_catalog.py` changes only the durable target
  recipe to declare `METNOS_EXECUTOR_PARALLEL=1`.
  `install/units/metnos-durable-worker.service.tmpl` matches that opt-in.
  `install/INSTALL_NOTES.md` documents the installation/runtime contract.
  These source changes require the normal coherent release process; they do
  not authorize modifying live signed units or adding drop-ins.

## Evidence

New service tests use file-backed SQLite, actual workers, the actual execution
bridge and central scheduler, with synthetic deterministic work. They prove:

- three independent units overlap when allowed;
- plan limits, CPU capacity and serial executor policy each constrain overlap;
- a fast lane finishes five units while its slow peer is still blocked, both
  with binding reuse and with a forced one-unit scheduling slice;
- an idle probe does not cause a busy loop, and an overtaken probe wakes;
- cooperative shutdown commits two active units; restart executes only the
  other four, with six total attempts and six results;
- serial useful work also avoids the idle polling interval.

One observational benchmark ran the same 24 synthetic units, each containing
25 ms of simulated work, through the service and SQLite:

| Lanes | Peak overlap | Elapsed |
|---|---:|---:|
| 1 | 1 | 0.722748 s |
| 3 | 3 | 0.268606 s |

The observed speedup was 2.69x. This is not an inference about real model
throughput. Tests assert results and concurrency, not a flaky speed ratio.
Raw pytest properties are in `/tmp/rm0008-lre-parallel-observation.xml`.

Reservation tests race eight independent SQLite connections against a budget
of exactly two reservations: exactly two distinct units are leased, for both
LLM and VLM stages. They also cover reopen, idempotent accounting, old fences,
pre-boundary expiry, post-boundary unknown usage, invalid historical
contracts, smaller-stage/workload fairness and permanent budget exhaustion.
Actual supervised bridge tests use synthetic model usage and verify host caps
of one and two, as well as an explicit LLM class-zero restriction. No model
endpoint is contacted; all six unit results have complete exact usage.

## Current limits intentionally preserved

The host's default LLM and VLM capacities remain one each. Folder work and
image analysis may overlap when their separate resource claims and dependencies
permit it, but this does not promise multiple calls to the same model.
`METNOS_LLM_PARALLELISM_CLASS` defaults to zero and applies to the `llm`
resource class. It constrains folder-classification workloads, not the
index-builder executor's `local_io` policy. No default override or special
LRE scheduler receipt was added.

Mutating/path executors also need a real independent output identity. The
current generic selector recognizes `dest`, `path`, `output_path`, or one
`paths` item; an index invocation containing only `base_path`, generation and
entries has no such identity and remains serial. Using an LRE unit key as an
implicit write authority would be unsafe. Any future partition-level identity
must correspond to the actual partition written and be verified by the
executor's contract. Existing model slots and unknown identities remain
binding even if the service has spare lanes.

## Tests and release handoff

New files:

- `tests/runtime/durable_workloads/test_service_parallel_progress.py`
- `tests/runtime/durable_workloads/test_model_reservations.py`

Updated regressions:

- `tests/runtime/durable_workloads/test_service.py`
- `tests/runtime/durable_workloads/test_execution_bridge.py`
- `tests/runtime/durable_workloads/test_durable_workload_storage.py`
- `tests/runtime/infra/test_stack_units.py`
- `tests/portable/test_executor_birth_service_catalog.py`

The initial broad check passed **513 tests in 55.82 s**. After adding the final
model-cap and lease-time checks, the focused new suites passed **38 tests in
3.68 s**. The final explicit service/storage/scheduler/installer/catalog scope
passed **308 tests in 12.45 s**. `git diff --check` was clean.

Two intervening whole-directory reruns encountered concurrent work outside
this implementation and are not reported as passes:

- **187 passed, one failed**: the new `test_image_indexing_plan.py` fixture
  omitted `metnos.inventory-seal/1`. The parent subsequently reported this
  fixed, with 11 indexing-plan tests passing.
- **200 passed, one failed**: `test_image_search_contract.py` still asserted
  the earlier intrinsically-long timeout after its manifest changed in a
  parallel task. This was handed back to that task for alignment.

The final 308-test selection names the owned service, reservation, storage,
fencing, execution and binding tests explicitly, plus both scheduler suites,
installer documentation/credentials, phase-5 integration, service units and
the signed service catalog. It does not conceal or certify the two changing
index/search contracts above.

The broad command covers the durable workload suite, both executor-scheduler
suites, installer documentation/credential checks, phase-5 integration,
service-unit invariants and the portable signed service catalog. These are
simulated/integration checks, not authenticated user-query E2E evidence for
the complete new indexing pipeline. That pipeline and its real end-to-end
release gate remain the parent task's responsibility.
