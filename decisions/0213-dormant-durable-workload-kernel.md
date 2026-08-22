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
