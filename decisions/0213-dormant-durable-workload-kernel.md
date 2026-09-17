---
id: 0213
title: Dormant durable-workload kernel before any public surface
date: 2026-08-20
status: accepted
area: runtime | storage | scheduling | safety
related:
  - 0186
  - 0190
  - 0193
  - 0196
  - 0204
  - 0207
---

> Modified by ADR 0214: after F13 certification, a compatible finalized long
> executor invocation is admitted automatically; `start_lre` and registered
> plans remain compatibility and optimized-DAG entries, not admission gates.

## Context

Metnos can execute one admitted invocation locally or remotely, constrain it
through the central scheduler, and preserve several specialist background
activities. It cannot yet represent an arbitrary corpus as immutable sources,
units, attempts, results and artifacts whose progress survives a process or
machine restart. In particular, the image-index build writes the final index
atomically but does not commit each image in a generic fenced store; its
systemd runner therefore is not evidence of exactly-once unit completion.

RM-0004 separates this missing authority into phases. The product owner
authorized F0 through F2 on 20 August 2026. The authorization accepts the safe
defaults below; it does not authorize a public noun, HTTP route, executor,
worker unit, model alias or unattended external mutation.

The F0 measurement used only generated data on the development host. Eight
generated 1600x1200 images took 911 ms through Tesseract at concurrency one,
456 ms at two and 230 ms at four. Hashing 64 generated MiB took 31, 17, 9 and
6 ms at concurrency one, two, four and eight respectively. A synthetic set of
64 independent ten-millisecond waits completed in 81 ms with eight threads.
These are diagnostic measurements, not service objectives. The configured
local VLM endpoint refused the health connection, so no VLM throughput number
exists. That absence deliberately yields a concurrency ceiling of one instead
of an estimate derived from CPU count. The complete, machine-readable record
is `tests/fixtures/durable_workloads/f0-capacity-baseline-v1.json`.

The executor census also found a semantic distinction that the design must
preserve. `find_files` has a verified read-only parallel policy;
`read_files_ocr`, `describe_images` and `describe_entries` do not yet declare a
durable effect contract; `extract_entries` consumes interactive `from_step`
references rather than durable dependencies; `create_images_indices` is a
specialist mutating build; and `write_files_doc` is a remote append without an
accepted durable idempotency key. None becomes retry-safe merely by appearing
in a plan.

## Decision

### Scope and identity

The internal Python package is `runtime.durable_workloads`. The SQLite store is
`PATH_USER_STATE/durable_workloads/state.sqlite3`; future content blobs live
below `PATH_USER_DATA/durable_workloads/`, partitioned by an opaque owner
digest. No table, module or API uses `tasks`, because that word remains owned
by recurring tasks. A public object name is deferred. Until a separate naming
decision is accepted, there is no vocabulary token, planner affinity, public
route, control executor, Tutor capability or published documentation for this
kernel.

F1 and F2 are dormant foundations. They may open a database only when called
explicitly and may not be imported by HTTP, the agent runtime, a scheduler or
an executor. Their presence therefore changes no current request path.

### Future process topology

When a later phase activates execution, it will use one coordinator process in
`metnos-durable-worker.service`, separate from the HTTP process. The unit will
be `PartOf=metnos.target` and install its `WantedBy=metnos.target` link only
when the feature is enabled. Its initial process count is one. The intended
service policy is `Restart=on-failure`, a five-second restart delay,
`KillMode=mixed`, a bounded cooperative stop followed by control-group
termination, and no new claims after stop begins. The worker is required for
feature readiness only when the feature is enabled; HTTP otherwise reports the
durable function as unavailable while ordinary chat remains healthy.

No systemd unit is created in F0-F2. These settings are a frozen input to the
lifecycle phase, not permission to integrate the dormant store into the
current stack.

### Consistency and effects

Execution is at least once. A `(owner, revision, stage, unit_key)` has at most
one committed result, selected later by a monotonic fence and compare-and-set.
Metnos does not claim exactly once across SQLite, a filesystem, a provider and
a remote device.

The closed durable-effect vocabulary is:

- `pure`: no externally visible mutation; automatic retry may be admitted;
- `idempotent`: the target accepts a stable native key and repeated equal
  requests are observably the same;
- `reconcilable`: an authoritative read distinguishes not-applied,
  applied-same, applied-conflict and unknown before retry;
- `manual_only`: the outcome may be ambiguous and automatic retry is forbidden.

The first executable version will admit only pure work, read-only executor
calls and publication into Metnos's private artifact store. Existing manifest
fields such as `revertible`, executor names and transport do not imply one of
these profiles. External writes remain excluded until a signed durable
contract supplies idempotency or reconciliation and collision tests.

### Execution context and central scheduler

A later, optional scheduler input has the following exact logical fields:

```text
ExecutionContext(
  owner_user_id,
  workload_id,
  revision_id,
  stage_id,
  unit_key,
  attempt_id,
  priority,
  resource_claims,
  deadline_at
)
```

The context carries correlation, fairness and budget facts; it grants no
authority. The coordinator does not create a private executor pool. It offers
work through the central policy, whose effective limits may only reduce the
plan's caps. Multi-resource keys are derived from admitted identities,
acquired in canonical lexical order and released in reverse order. Initial
restrictive ceilings are OCR two, VLM one, each LLM binding one and local I/O
two, with an initial coordinator claim window of one. Representative
interactive/VLM coexistence measurements are mandatory before worker
activation.

### Plan, model binding and publication

The canonical plan identifier is `metnos.durable-plan/1`. Its stage kinds are
exactly `inventory`, `map`, `reduce`, `validate` and `publish`. A runner is a
closed `internal`, `executor` or registered logical `workload` reference.
Stage inputs use structured references such as `source.path` and
`dependency.result`; executable templates, expressions, SQL, shell and Python
are not plan data. Unknown fields are rejected. Budgets, retry policy,
cardinality, invalidation inputs, required artifacts and terminal policy are
part of the immutable canonical digest.

The plan stores a logical LLM workload, never a tier alias or provider model.
The current registry may resolve workloads to `fast`, `middle`, `wise`,
`creative` or `frontier`. `precise` is not a tier key and no alias is added.
`creative` remains a real tier with its existing binding fallback, while
workloads such as `tutor.compose` keep their own registered tier. The future
image preset reserves internal workload names `durable.images.answer` and
`durable.images.reduce`; reserving the strings does not register or activate
them. Admission must fail until they exist in the verified workload registry.

V1 publication means a downloadable, owner-scoped artifact in Metnos's
private store. It is not a write to a user path or provider. The plan-v1
schema therefore accepts only `publication="internal_store"`. External
publication requires a later schema and signed durable effect contract.

The normative F0 schemas are:

- `plan-v1.schema.json` for immutable plans;
- `error-v1.schema.json` for redacted structured errors;
- `event-v1.schema.json` for monotonic persistent event envelopes;
- `image-preset-output-v1.schema.json` for per-source evidence, aggregation
  and explicit coverage.

Each has a valid and intentionally invalid example under
`tests/fixtures/durable_workloads/examples/`. The executor census is frozen in
`executor-census-v1.json`.

### Transaction boundaries

State and the event that reports it are one SQLite transaction. Calls to an
executor, model, filesystem blob writer, provider or channel are never made
while that transaction is open.

```text
submit/admit/control/evaluate
        |
        v
 BEGIN IMMEDIATE
   owner-scoped reread + CAS
   relational state change
   monotonic event id allocation
   event insert
   terminal transition only: outbox insert
 COMMIT
        |
        v
 external observation or delivery

future attempt
  claim transaction -> invoke outside DB -> validate/stage outside DB
                    -> fenced result transaction -> acknowledge

future artifact
  write+fsync temp -> digest+rename -> DB commit -> reconcile prepared rows
```

A failed event insert rolls back the state change. A terminal state is derived
inside the repository after all nine completion checks; no caller supplies a
`success` boolean. SQLite and filesystem publication remain a
prepare-publish-reconcile protocol rather than a fictional shared
transaction.

### Retry and reconciliation matrix

| Durable profile | Known result after crash | Automatic action | Conflicting/unknown observation | V1 admission |
|---|---|---|---|---|
| `pure` | no committed result | retry with a new fence | contract violation if equal inputs yield incompatible committed digests | yes |
| `idempotent` | native key and equal payload digest | repeat the same keyed request | `needs_attention` on key/payload conflict | internal artifact store only |
| `reconcilable` | authoritative read says `not_applied` or `applied_same` | retry only for `not_applied`; commit observed equal result for `applied_same` | `needs_attention` for `applied_conflict` or `unknown` | no external target in v1 |
| `manual_only` | any uncertain outcome | never retry automatically | record redacted evidence and request a decision | no |

Transport deduplication is not effect idempotency. Undo is a compensation and
does not upgrade a profile. Timeout before an external response is uncertain
unless the contract proves otherwise.

### Persistent foundation

The metadata database is SQLite with foreign keys enabled, WAL, an explicit
busy timeout, UTC timestamps, numbered additive migrations and fail-closed
future-schema detection. The parent directory and database use modes 0700 and
0600. Modules perform no import-time opening and retain no singleton
connection. Every owner-bound primary or foreign key includes
`owner_user_id`; public repository methods require it and query by owner and
identifier together.

The first schema represents workloads, revisions, stages, sources, units,
attempts, results, result dependencies, artifacts, publications, events,
outbox and scheduler credits. Supporting stage dependencies, idempotent
commands and attention resolutions are relational as well. Plans and
snapshots may be bounded canonical JSON; mutable state, isolation keys,
versions, fences and completion facts remain relational.

F2 uses explicit workload and unit transition tables, optimistic workload
versions, idempotent request/command keys, event-and-state transactions,
derived unit counters and selective owner purge. It expresses state only; it
does not claim, lease, execute or commit a worker result. Those invariants
remain the F3 gate.

## Alternatives considered

**Extend scheduler v2 jobs.** Rejected because a scheduled callback is a wake
up, not a persistent unit/result graph, and overloading `tasks` would merge two
public meanings.

**Run the coordinator inside HTTP.** Rejected for activation because HTTP
restart and request-loop pressure would also interrupt claims and heartbeats.
The dormant F1-F2 package is still importable library code and does not create
the future service.

**Retry every reversible or remote executor.** Rejected because compensation
and transport deduplication do not prove the external effect was applied once.

**Name a public `jobs` or `workloads` object now.** Rejected because the
vocabulary and natural control grammar deserve a separate product decision
after a vertical slice is demonstrated.

**Map `precise` to `creative`.** Rejected because tier and workload are
different axes and current registered workloads intentionally bind
differently.

**Use the existing image-index builder as the generic engine.** Rejected
because its final atomic file does not provide owner-scoped generic units,
attempt fencing, result provenance or nine-part completion proof.

## Consequences

F0 gives lower-level implementation agents closed schemas and safe defaults;
F1-F2 can be reviewed without guessing about public naming, model aliases or
external side effects. The dormant database adds code and tests but no live
process or user-visible behavior. The design pays an up-front cost in schema,
owner-scoped composite keys and transaction discipline so later recovery can
be tested rather than narrated.

The synthetic measurements do not justify production capacity. F6/F12 must
repeat representative OCR, VLM, remote-device and interactive coexistence
tests before enabling the worker. F3 must prove lease expiry and fencing with
real concurrent processes. F4 must prove filesystem reconciliation. F11 must
adapt and sign the image-preset contracts. Public naming, routes, UI, Tutor
material and external publication remain closed until their later gates.

## Amendment — F13 controlled activation boundary (2026-08-22)

After F3-F12 had passed their recorded gates, the product owner explicitly
authorized F13. This amendment changes the activation boundary; it does not
weaken the consistency and effect rules above.

`LRE` (Long Run Engine) is the invariant internal architecture name and is not
translated. The exact system executor `start_lre` is admitted as the sole
planner-visible submission entry for the first controlled release. It accepts
only a plan identifier already present in the closed runtime registry and
absolute local source roots. This exact-name exception does not add `jobs`,
`workloads`, `lre`, or any equivalent object to the public naming grammar, and
it gives Synt no authority to invent a plan, executor, runner, or profile.

New submissions remain disabled by default. The final transition from draft to
an executable state shares a bounded cross-process lock with feature
configuration changes and rechecks both configuration and worker readiness.
The accepted channel event is pseudonymized before it enters persistent dialog
state; the resulting stable identity is used as the owner-scoped submission
key. Redelivery of the same Telegram update, or of an HTTP request carrying
the same `Idempotency-Key`, therefore converges on the same workload even when
the internal turn identifier changes. A changed payload under the same key
fails closed.

This amendment does not certify F13. Installation still requires the user's
installer consent; a non-sensitive pilot must demonstrate a real restart and
no duplicate committed work. Public documentation and Tutor may describe the
submission path only after that deployed proof, and RM-0004 remains `active`
until every F13 exit gate is evidenced.

## Amendment — F13 certification and release (2026-08-22)

The product owner accepted the installer disclosure, and the supervised user
unit was installed without replacing the existing HTTP service baseline. The
feature was exercised through an explicit off/on/off sequence and was left
disabled after certification; disabling admission does not remove historical
workloads or stop owner-scoped reads.

The non-sensitive pilot processed four synthetic images. A controlled worker
restart occurred after eight committed units and one active attempt. The new
process resumed the same workload and completed 23 units with 23 attempts, 23
distinct committed result digests, and three validated artifacts. Replaying
the accepted HTTP delivery converged on the same workload and revision and did
not change those counts. A preceding failed workload remains recorded as a
failure, demonstrating that an invalid model result cannot become a false
completion.

The signed `start_lre` contract, public bilingual System guide, rebuilt Tutor
catalog, personal-information gate, incremental public release and Cloudflare
deployment all passed their recorded checks. Detailed private evidence is in
`internal/reports/rm0004-f13-verification-20260822.md`. This amendment certifies
F13 and permits RM-0004 to move to `implemented`; it does not change the
default-disabled admission policy or promise exactly-once execution of
arbitrary external effects.

## Amendment — quiescent supervision and honest progress (2026-09-16)

A live supervisor is not evidence of advancing work. Empty or attention-only
queues must not repeatedly launch every hardware-derived lane. A bounded,
read-only demand hint sizes lane submissions against the frozen per-workload
concurrency and the central scheduler's host resource ceilings. Worker
capabilities are per-invocation limits, not host-wide capacity. The authoritative
claim, reservations, owner checks and fencing remain unchanged. Negative hints
are cached only for a quiescent database, invalidated by SQLite data_version
and connection total_changes. Active controls and outstanding leases retain
a maintenance lane, including leases on attention-only workloads.

The primary store migrates and checks integrity at startup. Worker connections
use its existing independently validated open_peer path rather than repeating
full migration checks. Disabled supervision refreshes feature_disabled health;
this exemption does not refresh fatal or overdue-execution states. Retention
and authorization reconciliation run independently of execution, with a shared
factory cadence so rebuilding lane bindings cannot multiply those operations.

Only a refusal proven to precede executor transport can certify no child model
call. A missing child envelope after dispatch remains unknown usage and blocks
automatic progress. Accounting-incomplete errors carry a distinct message and
retain the bounded underlying error code where available. Historical unknown
usage is not cleared, budgets are not increased, and failures are not replayed
by this change.

Console progress counts committed results only; errors, attention and skips
remain separate. The last committed timestamp comes from actual result records,
including adopted results, never generic updated_at. Presence, disabled state,
work state and stale observations are separate. Read APIs remain owner-scoped.
This amendment records candidate behavior, not deployment certification; live
release and end-to-end evidence must be recorded separately.

## Amendment — feature indicator separate from process health (2026-09-16)

The Services card previously reused canonical process status for its dot.
A deliberately disabled, healthy LRE worker therefore appeared green. The
card now presents feature availability separately: confirmed disabled is gray;
green requires valid configuration, matching observed enablement, healthy
readiness and a running process; transitions or unverified observations are
amber; service failure or invalid configuration are red. Text accompanies
color and existing IT/EN message keys supply every displayed explanation.
Process status, health and PID remain available under technical details.

Canonical status, watchdog behavior, desired state and enable/disable controls
are unchanged. A view must not turn an intentionally disabled supervisor into
a runtime failure merely to change its color. Rendered IT/EN regressions cover
disabled, enabled, mismatched, invalid, failed, missing and unprobed states;
unknown information never yields green. UI registry and public documentation
describe this implemented distinction; no experimental Tutor routing,
composition or coverage change is included.

## Amendment — persisted start, known-unit percentage and cautious ETA (2026-09-16)

List and detail expose a closed progress projection for the active revision,
computed by one owner-scoped aggregate per bounded page (maximum 200 IDs,
HTTP maximum 100). No new store, schema, writes, model calls or per-job query
loop is introduced. `execution_started_at` in persisted attempt metrics is
the execution start; admission, workload creation and lease acquisition are
not substitutes. Missing or invalid evidence remains unavailable.

Percentage is committed units divided by all currently known units, floored
to one decimal. It is neither elapsed time nor source coverage and may fall
as dynamic units appear. Until successful workload completion it is capped
at 99.9%, preserving the distinction from final publication and validation.

An indicative ETA is produced only while running, with sealed inventory,
complete accounting, fully materialized phases, no uncertain unit/retry and
no required final artifact. Exactly one processing phase after the mandatory
inventory is supported: heterogeneous multi-phase plans, including photo
indexing, deliberately show `n.a.`. At least three first-attempt successful
results must span ten seconds. Their completion cadence estimates remaining
units from the last persisted completion, not from poll time. The latest
result must be within the smaller of 120 seconds and stage timeout; an overdue
prediction, reversed clock or prediction beyond seven days is unavailable.
This is an observation-based indication, not a deadline or throughput promise.

The browser additionally suppresses ETA unless engine health is ready and the
observation is at most 30 seconds old; disconnection clears displayed estimates.
Each unavailable field displays the requested exact `n.a.`, via bilingual
catalog entries. Aggregate DTO, isolated real-store scenarios, executable
browser behavior and IT/EN seed checks guard the semantics. Live deployment
and production acceptance remain separate from these implementation tests.

## 16 September 2026 — contention recovery and readable workload details

The photo-analysis incident exposed two independent failure mechanisms.
Concurrent SQLite writer contention from several lanes was counted as several
consecutive supervisory failures, stopping healthy sibling attempts. Native
SQLite BUSY/LOCKED errors now pause new admissions with interruptible bounded
backoff (0.25 seconds exponentially capped at 5 seconds; eight observations or
60 seconds exhaust the episode). A simultaneous burst counts as one failed
cycle. Other errors retain the three-cycle limit. No interrupted invocation
is replayed by this handling: persistent fences, usage accounting and recovery
remain authoritative. `test_service_contention.py` includes a real writer lock.

Conservative read-only negative probes avoid taking the SQLite writer for
expired-lease recovery or historical result adoption when no candidate exists.
All original authoritative selections and checks remain inside the transaction;
new work arriving after a negative probe is observed on a later cycle. The
recovery probe includes due retries, older revisions and both clock-regression
signals. Tests cover a real external writer and state changes after a positive
probe. The optional 31-lane/966-unit stress reproduced starvation without
artificial sleeps; after the probes it completed without BUSY on the tested
host. This does not promise contention-free operation under arbitrary load.

Separately, ordinary invocation statistics invalidated the catalog via the
aging database's mtime. The loader now authenticates and applies the same
immutable semantic lifecycle snapshot; count-only changes do not invalidate,
but archived/deprecated transitions and removals do, including WAL changes.
Malformed or unreadable lifecycle state fails closed. This does not bypass
signatures, generation checks, ownership or skill visibility. See ADR 0099
and `test_loader_lifecycle_signature.py`. Executor loader failures retain a
closed `loader_cause` diagnostic without arbitrary exception text or a new
automatic retry grant. Historical errors without this field cannot be
retroactively assigned a definite internal cause.

The console presents an activity title, admitted owner-only folder when known,
observed active phase and compact timing metrics. The pure display projector
is injected at the composition boundary; universal storage/control do not
import the photo domain or expose arbitrary plan arguments. Current admitted
revisions are read in a bounded owner-scoped aggregate, also for existing jobs.
Dynamic paths render as text, never markup or links. Explanations, events and
technical details are collapsible and retain their expansion during refresh.
The UI preserves i18n, keyboard access, state-specific actions and stale-data
handling. Attention remains visible independently of service readiness.

Parallelism separates running units, assigned units and admitted workload
limit; it does not claim thread/process counts or the instance's actual free
capacity. Initial photo discovery shows n.a. instead of treating its lone
unit as a complete work estimate. Missing ETA states why; the estimator remains
deliberately limited to homogeneous single-phase work. Discovery partial files
are not a resumable scan cursor; committed phases/groups remain recoverable.
Tests: `test_description.py`, `test_progress.py`, owner-scoped control/API and
isolated Chromium IT/EN desktop/mobile in `test_durable_console_behavior.py`.

## 16 September 2026 — phase estimate is not a whole-job promise

`progress.current_phase` exposes a persisted stage key, estimated end and a
closed unavailable-reason code, separately from the original whole-job ETA.
Only one actively leased/running phase qualifies. Its inventory must be sealed
and its own materialization complete; at least three final successful
completions from that phase are required. A future phase, incomplete later
materialization or heterogeneous earlier completions never enter its rate.
Any current-revision uncertain unit, unresolved retry, explicitly unknown usage or terminal
model attempt without complete usage invalidates the estimate. An in-flight
model call need not have its final consumption yet: `usage_complete` includes
these live calls and is therefore not the phase-estimate gate. Its stronger
completion/accounting semantics remain unchanged. `needs_attention` has
priority over all lesser unavailability reasons.

The rate is `(last_success - first_success) / (success_count - 1)`; remaining
phase units use that cadence, anchored at `last_success`, never the poll time.
The sample span must be at least ten seconds. Freshness is bounded by
`min(1800 seconds, phase timeout, max(120 seconds, 2 * cadence))`, accommodating
multi-minute blocks without treating an indefinitely quiet phase as progress.
Overdue, future-clock and forecasts beyond seven days produce no ETA. This is
an indicative throughput estimate, not a claim that individual blocks have
equal costs or that later phases finish at the same time. No schema or scheduler
change is required; the bounded owner-scoped page query aggregates only the
current admitted revision. The original cautious single-phase whole-job ETA
remains available between claims without inventing an active phase.

The main console metric explicitly names the current phase in Italian and
English. The distinct whole-job estimate remains in collapsed technical
details; unavailable estimates explain why, and readiness/freshness protections
still suppress display. Existing percentage remains a count of known committed
units and is never used to extrapolate this forecast. Tests in `test_progress.py`
cover separate phases, sample contamination, concurrent phases, materialization,
attention/retries/usage, slow blocks, stale and overdue forecasts, and fixed
polling anchors; `test_durable_console_behavior.py` covers scope separation,
IT/EN catalog and isolated real-browser presentation.

## 16 September 2026 — multidimensional robustness audit

The audit corrects demonstrated defects without weakening ownership, accounting,
result fencing, strict photo coverage or resource limits. It does not certify an
installed release or authorize automatic restart of incompatible workloads.

- `progress_many` aggregates selected-revision attempts once per phase and
  derives whole-job totals from those facts. Indexed unit selection and explicit
  join ordering avoid per-unit scans of owner history even without SQLite
  statistics. Materialization searches the closed set of nonterminal states
  instead of rescanning every completed parent; all nine states retain their
  original semantics. Regression gates measure SQLite VM instructions, not
  machine-dependent elapsed time.
- List/detail projections share a deferred read transaction, producing a coherent
  WAL snapshot without reserving the writer. Initial read failure is not an
  empty job list; refresh failure retains prior rows but marks them stale. The
  new messages use the canonical IT/EN catalog.
- Recovery candidates must have actionable transitions. A draining pause or
  cancellation cannot consume the entire limited reconciliation batch forever.
  Cancellation remains dominant even when its wall-time budget has expired.
- An overdue adapter prevents new lane admission while it is alive. Once all
  outstanding futures return, the supervisor reaps outcomes and reconciles
  before admitting more work. This does not kill an uncooperative Python thread.
- `commit_result` reads its clock after acquiring the write transaction. The
  worker supplies a callback, not a timestamp captured before possible writer
  contention. Explicit timestamps remain only a deterministic testing seam.
- Artifacts, inventories and photo parts reject special files after nonblocking
  descriptor open. Snapshot hashing reads at most the frozen size plus one
  detection byte. Source authority is checked again after local copying or remote
  attestation; revoked private copies are not delivered to the executor.
- Direct invocation checks nested schema authority/secret annotations using a
  bounded walk. Unresolved references fail closed. Semantic schema normalization
  distinguishes schema annotations from property names and literal data.
- Frozen contracts include complete capability declarations, including `when`
  and `hint`, not just capability names. Existing nonempty-capability digests
  change intentionally: do not rewrite stored contracts or automatically retry
  them against the new build. Use the canonical compatibility/revision workflow.
- A failed Telegram send is retryable only when `delivery_ambiguous` is explicitly
  boolean false; omitted or malformed evidence is not proof of non-delivery.
- Schema validation checks required trigger definitions against the original
  migration statements, rejecting removed or replaced immutable/transition
  guards. One migration sequence replaces six duplicated upgrade branches;
  transactional checkpoints and rollback semantics remain unchanged.

Image publication fingerprints and bounded filename classification are detailed
in ADR 0117. Redundant pre-transport accounting and unreachable repeated response
checks were removed, without broad unrelated refactoring.

Tests: `test_progress.py`, `test_materialization_read_bounds.py`,
`test_service_recovery_fairness.py`, `test_commit_deadline_after_contention.py`,
`test_service_parallel_progress.py`, `test_control.py`, `test_schema.py`,
`test_security_boundaries.py`, `test_source_authority.py`,
`test_direct_invocation.py`, `test_admission_compiler.py`, `test_outbox.py`,
and console/API tests. Run `test_contention_scale.py` with
`METNOS_TEST_CONTENTION_SCALE=1` to include the larger contention cases.

Open boundaries: WAL `synchronous=NORMAL` is not power-loss durability
certification; artifact retention cutoff is not automatic deletion of referenced
blobs; strict photo decoding failure still stops the job. Generation attestation
factory wiring remains a lifecycle integration item, not a demonstrated bypass
of the verified loader and frozen-contract checks.

## 16 September 2026 — generic item error receipts and persistent final trace

The user's subsequent decision supersedes the strict photo-decoding stop noted
above: domain-specific handling belongs to the producer, while LRE records a
general-purpose outcome. This implementation is a development candidate, not
evidence of an installed release or successful real-archive indexing.

An approved output schema may explicitly declare the reserved `domain_outcome`
field: version 1 and `error_counts`, a closed bounded map of stable identifiers
to positive integer counts. At most twenty codes and one million affected items
are accepted per receipt; booleans, arbitrary text, nested detail and malformed
or undeclared fields are rejected. An open output schema alone does not approve
this field. Identifiers are not translation keys. The kernel contains no decoder,
photo marker, retry policy or content interpretation.

Only the originating unit emits the receipt. Each original item has exactly one
primary error code; counts are affected items, not exceptions or attempts.
Reducers and publishers may preserve their own domain summaries but must not
re-emit the same generic outcome. Item identity and primary-code selection belong
to the approved producer: the aggregate-only kernel cannot infer or enforce
cross-phase item identity. The photo adapter reports only during analysis.

`ValidatedResult` verifies the reserved shape; the receipt is bound to the result
digest. Fresh commit and semantic reuse share the same projection into the
existing `terminal_detail_json`, atomically with the committed unit. Replay does
not increment a counter. There is no database migration and no counter updated
independently from the accepted result. Failed/unconfirmed attempts do not enter
this projection.

The owner/current-revision execution summary exposes `domain_errors` with
`nitems`, `categories` (`error_code`, `count`) and `truncated`. SQL aggregates only
committed units, computes the complete total before the twenty-category display
limit and does not load result bodies. The console displays the localized item
count and stable category codes independently of technical attempt errors.
Completed jobs retain the trace after database reopening and page reload, while
their history is retained. Historical jobs without these receipts show no domain
items; the system does not invent or backfill counts from old error messages.

Current `error_categories` remain the active unit/materialization problems;
successful retry legitimately clears them. A separate `attempt_errors` projection
retains `nattempts`, categories and truncation from persisted structured attempt
errors in the same owner/revision. Its total is computed before the category
limit. Only stable codes are exposed, never free-form messages, source paths or
exception text. The console's collapsible history remains available even for a
successful terminal workload. Recovered technical errors do not inflate item
counts or change a clean `completed` state into `completed_with_errors`.

Committed negative outcomes select `completed_with_errors` only after the usual
source coverage, materialization, dependencies, artifacts and usage checks pass.
They neither mark units failed/partial nor authorize missing output, unknown
usage or unverified sources. This is not a blanket catch-and-continue policy.
Normal restart persistence is tested; power-loss durability is not certified.

Regression evidence: `test_domain_outcome.py` (shape, approval, replay, reuse,
owner/revision boundaries, missing coverage/accounting, retry count, terminal
reopen and totals beyond the category limit), `test_image_indexing_e2e.py`
(mixed photo corpus and real durable restart with synthetic model responses),
`test_durable_console_behavior.py` (Node and real isolated Chromium IT/EN).

## 16 September 2026 — bounded recovery without cancelling the remaining queue

User-approved policy: a declared retryable error uses only the admitted number
of automatic attempts and only for a safe effect profile. Exhausting those
attempts is not evidence that the cause is permanent: the unit and workload
enter `needs_attention`. Pending batches and committed results remain stored;
there is no implicit cancellation of their queue. The same rule covers an
exhausted recoverable lease. An explicit retry decision grants one attempt,
not a reset of the automatic allowance. A further failure requires review again.

Missing or opaque failure classification and uncaught adapter exceptions become
`executor_unknown`, with manual review and no automatic retry. Explicit invalid
input, permanent failures and contract violations remain non-retryable; a plan
cannot turn those classes into automatic retries. Unknown consumption, budgets,
clock regression, capability changes and ambiguous effects retain precedence.
No authority, source, accounting, fencing or frozen-contract check is bypassed.

The persisted error receipt reflects `retry=manual` when attention is required.
Historical categories may include `cause_code`, but only the bounded approved
runner code already saved by the execution bridge; arbitrary diagnostic text is
not projected. Counts remain attempts, distinct from `domain_errors.nitems`.
The kernel contains no image-specific decision. It does not reopen old terminal
jobs or rewrite their approved contracts.

Console details use a compact localized value/meaning table: completed batches
and total batches for the active phase, known total across materialized phases,
phase percentage, revision start and phase finish estimate. Phase numbering and
the existing conservative estimate remain authoritative; no time percentage is
invented. Missing data stays `n.a.`. HTTP chat turns make registered internal
page paths clickable, including saved LRE receipts, without changing their plain
text, Telegram formatting or access checks.

Regression evidence includes exhaustion/restart/manual-grant integration tests,
synthetic image end-to-end tests with one and three model failures, and isolated
Chromium IT/EN desktop/mobile checks. Installed release and real checks are
recorded separately in `internal/reports/lre-error-resilience-20260916.md`.

## 17 September 2026 — recovered attempts do not disable forecasts forever

The original telemetry predicate treated every `attempt_count > 1` as unresolved,
including committed units. One recovered error therefore disabled forecasts for
the remaining lifetime of the revision. Only uncommitted repeated attempts now
carry that uncertainty; the final successful attempt contributes one sample.
Completion intervals retain time spent on failures and retry waits between
successful completions. Historical errors are neither removed nor counted twice.

This changes only the read-side progress projection. Unknown model consumption
in any attempt, unresolved states, stale samples, sealed-inventory requirements,
and the stronger completion/accounting checks remain binding. No schema, plan,
budget, retry allowance, model setting or scheduler policy changes. Four real
failure/retry storage tests cover single and multiple phases, known model usage,
unchanged history, fixed polling anchors, and refusal when accounting is unknown.
Deployment status is recorded separately in the LRE parallelism report.

## 17 September 2026 — CPU admission and model lifecycle boundary (candidate)

Resource vectors are reserved atomically after executor and target exclusions.
A caller waiting for a model, network or disk must not retain CPU claims that
another admitted workload could use. The global admission bound and deadlines
remain unchanged; this does not claim general starvation freedom.

LRE checks the frozen model binding, effective capability and resource claim.
Virt owns provider selection and host lifecycle through `ModelResource`; the
kernel does not inspect model names, endpoints or launch commands. Local vision
uses the existing shared process startup. Additional lifecycle adapters belong
to Virt. Automatic multi-replica management is not implemented by this change.

Managed local children derive native thread ceilings from affinity, visible
cgroup-v2 quotas, logical CPU shares and item concurrency. The parent environment
and already loaded daemon pools are untouched. This is not exclusive CPU or
memory reservation. Candidate tests and deployment limits are recorded in
`internal/reports/lre-cpu-model-resources-20260917.md`.
