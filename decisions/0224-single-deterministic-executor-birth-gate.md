---
id: 0224
title: Single deterministic birth gate with a synthesized-executor review branch
date: 2026-08-25
status: accepted
area: executor | synt | signing | policy
related:
  - 0114
  - 0196
complements:
  - 0223
  - 0220
---

## Context

The service-startup coupling is modified by ADR 0225. Executor admission and
publication authority described here remain required.

RM-0007 created an authenticated, immutable and atomic publisher, but several
trusted producers could still reach that publisher directly. Human-authored
executors, Synt candidates, imported skills, specializations and maintenance
updates performed overlapping but different admission steps.

The audit also found that a failed semantic verifier could be treated as a
warning, that some unavailable deterministic checks were interpreted as a
pass, and that the promoter trusted the broad state `synthesized` instead of
evidence tied to the exact candidate. Tests proposed by the code-generating
model could test the same misconception and their shell setup ran outside the
runtime sandbox.

Publication integrity is therefore necessary but insufficient. An immutable
generation can faithfully preserve a badly admitted executor. The remaining
problem is to establish one admission authority before publication without
turning the publisher into a package manager or asking local models to
implement security policy.

## Decision

Every executor which is about to become `active` crosses one public birth
boundary, independent of its origin. Generators create only quarantined
candidates. They cannot select a live lifecycle, grant authority, sign or
publish.

The birth boundary snapshots the complete candidate and binds every applicable
check to a deterministic candidate digest. It runs structural, policy,
localization, routing and test-shape checks for every origin. `failed` and
`unavailable` both prevent activation.

Certification is binary: every applicable mandatory check and test must pass.
Origin and component labels help diagnosis but never permit a partial pass or
reduce the effect of an error.

Semantic alignment is required for every model-authored revision and every
imported or otherwise untrusted candidate; directly trusted human revisions
do not acquire this model-mediated check merely by crossing Birth. A separate logical workload,
`executor.birth.semantic_review`, has a minimum tier of `wise` and may route
to frontier. It reads purpose, contract and code and emits a typed opinion plus
independent test cases. It has no signing or publication authority. Its
`misaligned` and `uncertain` outcomes block automatic progress.

The runtime executes accepted test cases in a real sandbox, with no real
network, credentials or personal data and with declarative fixtures instead
of model-authored shell setup. Tests written only by the code-generating model
are never sufficient evidence.

For a synthesized executor, human approval bound to the exact candidate digest
is mandatory for new code, code changes, operational-contract changes and new
or increased authority. Builtin, core and directly human-authored executors
retain their existing review and trust boundaries. Imported candidates receive
the Birth semantic review required for untrusted input, but none of these
origins enters the Synt pre-exercise, failure-review or repair cycle.

An eligible, non-mutating synthesized executor may enter a bounded pre-exercise
state after deterministic admission, semantic review and isolated tests. A
negative user assessment hides the exact generation immediately and triggers a
mandatory frontier review of the failed query, arguments, output, contract and
code. A failed or unavailable review leaves it quarantined. A repair is always
a new candidate and crosses the complete gate again.

Once admitted, the existing RM-0007 publisher signs and commits the immutable
generation. A birth report records evidence and the active generation but is
not a second cryptographic authority: the loader continues to trust the signed
manifest and authenticated generation.

Low-level publication APIs remain implementation details. A static boundary
test rejects operational producers which bypass the birth module. Candidate
storage, retirement, rollback to an already admitted generation and the
one-time legacy cutover retain their dedicated, non-birth boundaries.

The complete candidate specification is RM-0008,
`internal/roadmap/RM-0008-porta-unica-nascita-executor.md`. It remains under
versioned adversarial review. Roberto approved the converged specification and
authorized implementation on 25 August 2026. F1 also fixed the three identity
domains, canonical framing, closed admission context and dedicated candidate
staging envelope before their code was introduced.

## Alternatives considered

### Teach every generator the complete publication procedure

Rejected. It duplicates policy and makes safety depend on prompt quality and
model capability. A model could also mistake a claimed check for evidence.

### Put every check directly inside the immutable publisher

Rejected. Candidate review may be slow and may require human input, while the
publisher must keep a small atomic commit boundary. Admission and commit are
one public operation but remain separate internal responsibilities, joined by
the exact candidate digest.

### Keep automatic promotion and rely on a grace-period rollback

Rejected. Rollback observes failures after authority has already been granted
and cannot reveal quiet semantic errors. Grace remains useful operationally,
but it is not admission evidence.

### Require a frontier model for every executor

Rejected. Model review applies only to synthesized executors. Their normal
semantic review requires an independent high tier, not a vendor or one fixed
model. Frontier remains available for complexity and is mandatory for
post-failure review; lower tiers may not be substituted silently.

## Implementation status — increment 2A

The handle-bound filesystem primitive of increment 2A is implemented and passes
its own acceptance base in full: 235 cells across six activities — portable and
concurrency on Ubuntu and Windows, the manifest, and the real-identity Windows
ACL suite — plus the two ordinary test suites, all green on the same public
commit. The base is frozen and its pre-fix photograph is regenerated by a fixed
procedure whenever the base itself changes.

## Implementation status — group 2 complete (increments 2B to 2F)

Group 2 prepares an **inactive** authority set. It does not start the Birth
runtime and it migrates no caller.

- **2B** — durable provisioning transaction: immutable header, closed-schema
  checkpoints with a domain-bound digest, byte-ordered payload inventory. No
  authoritative name is born final: every object is written under a pending
  name, re-read and promoted by a rename without replacement. The author root
  is acquired from the fixed previous names alone, and only the default private
  key travels.
- **2C** — Admission and one store per producer capability are generated inside
  the transaction; the two public operator registries are copied byte for byte;
  a separation check compares the raw public bytes of every role.
- **2D** — eleven context components from a code-owned catalogue, each carrying
  an honest `enforcement_state`; `material-v1.json` becomes durable before
  `set.json`, whose `set_id` is the domain digest of the document without that
  field.
- **2E** — the core may hand over `BirthCommitFactsV1` and nothing else; the
  publisher seals author key, ring, Admission key, issuer, verifier, epoch
  resolver and the single store primitive. The link is crossed against the real
  primitive on an isolated root.
- **2F** — the three finals are published by renames without replacement, the
  marker is written after the full re-read, and only the agreeing transaction is
  removed. Two installer entries with no parameters are wired into Phase 3.

**Declared and not proven** (group 2 does not claim them): the Windows boundary
of the provisioner beyond the publication of the finals; a real process kill
after each individual write step; two concurrent provisioners. The exit table of
the group 2 report carries the detail.

## Implementation notes — increment 2A on Windows

The handle-bound filesystem primitive behaves the same on both platforms, but
three properties were settled while certifying it against the frozen acceptance
base and are recorded here so they are not rediscovered:

- **The anchor of a chain is opened by path; every descendant is opened
  relative to it.** The prohibition on the Win32 wrapper is a prohibition on
  falling back to it after a native refusal, not on using it for the anchor.
- **The provisioning lock is created and reopened through the relative native
  entry, without the right to remove.** Keeping that right in the handle that
  stays open while the lock is held makes every later read of the container
  collide with it.
- **What stays open after a container is created is a reader, not a creator**,
  for the same reason; and the owner of a store may write its own global lock,
  a property derived from the closed catalogue so that the writer and the
  verifier of a profile cannot disagree.

Two acceptance cells of the frozen base require exactly one native open per
created object, which the reader above contradicts; a third requires a single
status conversion where a refused move must also look at its destination. Both
conflicts are with mandatory cells, and the mandatory ones prevail. The
measurements and the minimal base change each would need are recorded in
`internal/reports/rm0008-gruppo2-analisi-implementazione.md`, sections 17.54,
17.60 and 17.65.

## Operational verification — canonical systemd observations

The closed distribution binds configured service behavior, not differences in
how the manager renders the same behavior before and after activation. Real
systemd 255.4 testing exposed both inverse timer links (`TriggeredBy` and
implicit `After`) and an unconfigured watchdog changing from `infinity` to `0`.
The verifier normalizes only the inverse links derived from the same signed
catalog timer target, in both the property projection and residual dependencies,
and those two disabled defaults. Explicit dependencies and watchdog settings,
foreign triggers, timer target changes and dependency origins remain checked.
This corrects the comparison without changing the admission or activation
boundary. Passing portable cases does not substitute for the real systemd cell,
the complete isolated transition or the final functional check in exercise.

## Operational configuration — HTTP private settings

The signed HTTP recipe selects engine `v3`, preserving the production contract;
its launch environment remains closed and arbitrary unit drop-ins are rejected.
Instance choices reuse the existing private `runtime.toml`: `default_account`
in `[mail]` is a nonempty string, defaulting to `metnos_system`, and
`nightly_enabled` in `[telos]` is a boolean, defaulting to `false`. Their named
environment overrides take precedence; `METNOS_TELOS_NIGHTLY` retains the exact
opt-in value `1`.
HTTP resolves `METNOS_DEFAULT_MAIL_ACCOUNT` after its Birth check and before
workers start, so the sandbox and child executor use the same selected name
without receiving the private file; an explicit invocation account still wins.
Empty mail overrides and incorrectly typed file settings are rejected, not
silently replaced. These are application data, not new launch authority or
personal values in signed source.

## Read-side author trust after the legacy transition

The transition inventory and its cold loader audit receive the same public
author ring from the existing authenticated read port, including the final
unchanged-inventory check. The installed runtime bundle retains this immutable
public ring; separate administrative readers authenticate the required context
through the ownership chain. STORE_ONLY readers never fall back to the retired
ambient key directory. Missing or invalid authority remains an explicit refusal.
This repairs the shared reader connection without introducing a second key
store, changing publication authority, or weakening signature verification.

Code-dependency projections select their signer files from that authenticated
ring. In Linux executor processes, the parent exposes only those public files
in a private read-only top-level mount, empty when no dependency is declared.
The child projection reader uses only that mount, without a configuration-path
fallback. Nested user namespaces are disabled to prevent replacement of the
mount; ordinary temporary storage and explicitly granted writable mounts are
unchanged. This is a filesystem boundary, not a claim of isolation from Python
code that modifies its own process. No signing key or publication authority is
passed to the child.

## Transition composition at the certificate boundary

The administrative installer receives the authenticated-record type required
by G6, built from the same payload and signature as the verified installed
distribution. Candidate preparation authenticates these bytes and matches them
to the durable `RECEIPTS_COMPLETE` record; it cannot require the build archive
whose publication follows certification. An existing conflicting archive is
rejected, not replaced or treated as an alternative head.

Dominant startup's catalog binding identifies the current contract receipts,
not the separately signed service catalog. The coordinator rereads the receipt
proof under the existing locks, while the service catalog is recaptured and
compared independently. Integration tests cross the actual G6 type check,
dominant-startup completion and certificate-ready validation, including negative
cases for invalid signatures and changed catalogs. These corrections preserve
the publication order and authority boundaries; they introduce no new recovery
path and do not replace the full isolated transition test.

The autonomous service-source fingerprint projects home-relative
`ReadWritePaths` onto the signed service home, just as it projects the target
environment. The prior fingerprint accidentally retained one test home's
literal writable paths and rejected catalogs generated for another valid home.
The corrected single fingerprint preserves exact relative paths and fixed
runtime paths; it grants no additional write access. Differential tests vary
the home and administrative interpreter and reject fully rehashed writable-path
expansions. The isolated G6 recipe remains independently constrained.

## Consequences

- Local models may remain useful even when imperfect: poor output is rejected
  or held for review rather than activated.
- New synthesized code and new synthesized powers require one explicit human
  decision, but all mechanical work before and after it is automatic.
- Current fail-open Synt checks and direct publisher call sites must be
  removed or redirected.
- Model-authored shell fixtures are retired for generated candidates.
- Imported candidates receive Birth semantic review; builtin, core and directly
  human-authored revisions do so only when model-authored. These origins are not
  subjected to Synt pre-exercise, frontier failure review or automatic repair.
- The static RM-0007 boundary inventory expands to recognize exactly one
  operational birth owner.
- RM-0007 remains the commit mechanism and RM-0002 remains the linguistic
  validator; this decision coordinates them rather than duplicating them.

## Configured watchdog before its first start (8 September 2026)

The real r13 clone loaded all 12 signed units but refused Playwright because
`WatchdogSec=45s` was compared with `WatchdogUSec=infinity`. In systemd 255,
the latter exposes `service_get_watchdog_usec()`, initially the runtime
sentinel; `service_start()` copies the configured timeout before execution.
See [the upstream implementation](https://github.com/systemd/systemd/blob/v255/src/core/service.c)
and [its D-Bus getter](https://github.com/systemd/systemd/blob/v255/src/core/dbus-service.c).

Only that initial sentinel is projected onto the exact signed setting, and
only with one complete manager observation showing inactive/dead, zero main
and control PIDs and zero main-start monotonic timestamp. Missing, duplicate
or contradictory evidence is refused. After start the measured timeout must
still match exactly. The lifecycle fields are evidence, not part of the stable
configuration hash; signed fragments, no-drop-in checks and reload checks are
unchanged. No service start is performed to manufacture a preflight result.

## Reader access and bounded startup analysis (8 September 2026)

The r14 isolated transition reached `PREFLIGHT_VERIFIED`, but activation failed.
The service reader tried to open the root-owned 0600 required-head writer lock;
Playwright's preflight exceeded its 90-second startup deadline. Neither the
signed transition records nor the earlier readiness probe proved activation.

The POSIX unprivileged reader checks the lock's exact type, ownership, mode,
link count and size without opening it. The marker remains checked by the
administrator and in the portable test store. The lock serializes writers;
it is not public signing authority. Making it readable would also permit an
unprivileged process to acquire flock. Signed heads, certificates and all
cold-chain checks remain mandatory; no permission or key is changed.

Profiling identified repeated pure boundary analysis as the dominant startup
cost. A one-entry, process-local cache now keys that analysis by the complete
ordered paths and immutable bytes, not timestamps or caller-provided digests.
Existing source/AST bounds remain enforced. Each observation still rereads
the live files, authenticates signatures, checks the full metadata A/B snapshot
and resolves local imports against the live filesystem. No persistent cache,
new trusted helper, timeout increase or result reuse is introduced.

After a signal termination, systemd reports a number plus a signal label,
including `RTMIN+n` or a numeric fallback; exited processes have a bare number.
The parser accepts these distinct shapes while retaining exact command, flag
and paired-observation checks. This follows the upstream
[systemctl formatter](https://github.com/systemd/systemd/blob/v255/src/systemctl/systemctl-show.c)
and [signal formatter](https://github.com/systemd/systemd/blob/v255/src/basic/signal-util.c).
Parsing a terminated process does not classify it as successfully started.

The actual r15 clone completed all seven durable records but still failed
activation deadlines. Profiling the unchanged installed preflight attributed
almost all work to source semantics: the boundary census and two equivalent
import syntax scans. Import syntax now has its own single-entry cache keyed
by exact ordered paths and bytes; live import resolution remains outside it.
The census avoids command parsing for non-process calls and path-taint scans
for calls that neither read nor write. All 466 observed boundary facts on the
754-file r15 corpus were identical in an offline before/after comparison.
The latter change reduced the uncached census from 14.799 to 11.871 seconds.
These measurements do not establish successful activation; the complete
candidate still requires a fresh end-to-end proof. No deadline, filesystem
permission, signing authority or mandatory check has been relaxed.

The r16 transition still exceeded stack readiness's deadline. A measurement
on its preserved clone found three complete boundary/import analyses per
service-catalog read. The runtime now reuses only immutable syntax facts for
one exact ordered source snapshot and analysis-budget tuple. Filesystem
enumeration, source reads, metadata, signatures and import resolution remain
outside both caches; the before/after distribution checks are unchanged.
Returned boundary lists are fresh copies, and no AST or persistent verdict
is retained. Warm-cache tests cover changed bytes, paths, added/removed files,
unreadable sources, tightened budgets and newly uncovered local imports.
On the same installed state, replacing only the pure analysis functions in
memory reduced a cold catalog read from 51.485 to 18.056 seconds, and the
next read to 1.464 seconds, with the same nine services. This read-only
measurement and the unchanged 466 boundary entries are predictive evidence,
not proof that the complete new candidate activates successfully.

The r17 clone passed HTTP, browser and all 122 executor checks, but the durable
worker failed before entering its module. Its signed recipe selected the
installation root while `durable_workloads.service` lives under `runtime`.
The recipe now selects that directory, as other bare runtime modules do.
A regression resolves every Python target from its signed directory without
importing a parent package or inheriting ambient source paths. The launcher
keeps its restricted path; no runtime fallback or readiness exception is added.

## Development prerequisite for F5: exact terminal replay (15 September 2026)

The schema-2 terminal reader now compares the contract encoded in a signed
publication with the request's exact contract before constructing the typed
publication. Previously construction substituted the request identity and
could hide a contradictory signed field. Replay also checks that committed
state corresponds to a publication and that the durable result hash matches
the authenticated terminal bytes; a signed recovery hint is not a completed
publication, and a rejected row cannot replay one as successful.

Four negative cases reproduced the omissions before repair. The affected
operational/Producer and bootstrap/recovery families pass 82 isolated tests.
This preserves existing signing domains, schemas and productive callers;
it does not establish the F5 admission threshold or enable F5. At this
checkpoint the repair is not installed, the source-review pin is unchanged,
and live HTTP, native Windows and independent review remain outstanding.

An independent internal review subsequently found that an admitted recovery
hint stored as a rejected terminal still passed the publication-presence
check. Replay now also requires the signed outcome and operational error to
match the terminal state and stored rejection code. It preserves admitted
and preexercise successes, all three rejection outcomes, and an operational
finalization error distinct from the original check error. Five new negative
cases reproduced the remaining omission; 20 affected cases pass, including
both interruption-recovery paths. The reviewer found no further blocker in
this bounded repair. Installed verification and source review remain open.

For G8 historical evidence, Roberto approved verification of signed receipts,
exact bindings and reread persistent state, with explicit disclosure that
temporary journals and source snapshots may no longer exist. A verified
signed hash is not evidence that those original bytes were reread. This
clarifies the evidence contract without enabling F5 or lowering its admission,
producer, cycle or defect thresholds.

## Incremental current-state verification and code retention (16 September 2026)

Reuse the existing signed, protected required-head pointer as the baseline
for ordinary runtime selection and the next transition. The new owner reader
authenticates that head, its distribution/cutover/context records and the
immediate predecessor edge. It verifies the current installed tree and rereads
the selector before returning. Its nominal result is a verified **window**,
not a claim that the entire history has just been audited. It introduces no
new key, persistent cache or service. A missing pointer still requires proof
of an initial state; corrupt installed state cannot fall back to bootstrap.

The full historical reader remains available for explicit reconciliation
and audit. Older executable trees are not historical authority. The existing
administrative release procedure now prunes obsolete code after successful
publication, normally keeping the current tree and one recovery predecessor.
Future, process-referenced, mounted and service-referenced trees are retained.
Existing deployment/startup locks and current attestation protect the plan;
an unsafe plan is deferred without undoing a completed release. Compact signed
history, Birth stores, user data, models and unresolved migration evidence
are outside the deletion scope. Recovery copies do not authorize head rollback.

The runtime changes are development code, not an installed F5 activation.
The standalone administrative startup/cutover verifier now captures the same
current edge plus an optional pending successor, then reuses its existing
signature, journal, claim, context and attestation validator. The first
retained edge relies on the protected signed baseline; subsequent edges must
match exactly. It checks at most three heads and transactions, without
enumerating historical directories. Snapshot changes and selected corruption
still fail. Initial installation and explicit audits retain the full reader.
The internal snapshot records its scope rather than claiming a lifetime audit.

The candidate passed the real installed HTTP startup attestation in read-only
mode, selecting two heads and two transactions in 0.662 seconds; no service
was restarted. This is not a general latency guarantee or deployment. The
release coordinator's own journal inventory remains a separate follow-up
before claiming bounded cost for the complete release cycle. The earlier
code cleanup and this proof do not certify F5/F6 completion.

## F5 isolated integration and execution waits (16 September 2026)

The approved development order is isolated integration, two complete focused
cycles, evidence-derived certification, then production activation. The
historical admission threshold, producers, migration and platform requirements
are unchanged. Unmodified mail/calendar/contact flows are not part of the
focused F5 profile; their existing regression evidence remains applicable.

The strict durable attempt path must reauthenticate the exact generation
after model-resource readiness and after scheduler admission. A valid check
before a wait is not permission to execute a generation quarantined during
that wait. The synchronous and submitted runtime wrappers provide one internal
pre-transport check within the admitted call. The durable owner supplies it;
requests and manifests cannot select it. Refusal preserves existing structured
errors and verifies zero model calls because transport was never entered.
The same sandbox and Birth boundaries still apply. Tests cover actual queue
contention and persisted epoch quarantine, with no transport on refusal.
This is candidate integration code, not an installed F5 activation.

The activation reader now accepts no caller document or key mapping. It reads
the dedicated public authority, a compact signed installation attestation and
the current authenticated F4 ownership window. The attestation binds the
installation, required head/build, qualification and migration, rather than
caller-supplied success counts. It does not reopen the full historical dossier.
This reader and its isolated filesystem tests do not issue a certificate or
enable production F5; evidence-derived issuance and productive lifecycle
composition remain unfinished.

## Exact-execution quarantine and resumable feedback (16 September 2026)

The approved maintenance capability `promoter:quarantine` uses the existing
per-capability authority preparation and the single sealed Birth core. It is
not promotion or rollback. Its absence does not disable F4; its productive
facade requires the fixed F5 activation. Models receive no publication key,
and the separate administrative certification key never publishes executors.

The signed objective binds the exact retained execution receipt. Before
publication, the owner authenticates the current generation and admission,
checks contract/candidate/name identities, and proves unchanged code, language
state and manifest fields except `lifecycle=quarantined`. It records reuse of
the prior authenticated admission rather than rerunning suspect code or
claiming new property/semantic passes. Store predecessor CAS prevents stale
feedback from changing a successor. A committed quarantine is distinct from
a rejected candidate report with the same outcome label.

The owner reconstructs an exact retry from authenticated original manifest
bytes and unchanged current code, accepting only the immediate quarantine
successor bound to that execution. No staging directory must survive a crash.
Producer expiry forbids a first claim but not authentication of an already
claimed request; quarantine recovery renews only its own existing lease.
The same terminal hint and independent persistent reread remain required.

The feedback composition orders publication/reread, epoch replacement/cache
invalidation and idempotent review enqueue. It reuses the existing catalog
lock and SQLite owners. Epoch retry verifies the recorded transition and
does not reset counters or append duplicate history. A failed queue leaves
quarantine intact; a repeated receipt schedules the same job. The instance
state paths are `birth/executor_epochs.sqlite` and `birth/failure_reviews.sqlite`;
productive entry refuses a missing migrated epoch store instead of creating
an empty replacement. Existing F4 startup does not enter this optional path.

Real isolated store tests cover signatures, publication, source preservation,
stale identities, expiration and interruption recovery, epoch/cache effects,
outbox retry and unaffected executors. These are fixture proofs, not historical
qualification. Live turn wiring, all cache layers, the review consumer,
installed migration, focused HTTP cycles and certificate issuance remain
required before productive F5 activation.
## Private undo fixtures follow the declared inverse (16 September 2026)

Release 54 correctly refused two incomplete property proofs: created paths
were being undone through a nonexistent module inverse, while web sessions
had no isolated positive case. The property runner now selects closed fixture
semantics from capability, argument schema and the exact reverse declaration,
never from an executor name. Created-path fixtures derive formats from declared
successful cases but replace host paths and shell setup with seeded private
files. They execute the actual central `delete_created_paths` implementation.
The pure filesystem registry imports admitted-module authority only when an
inverse actually dispatches another executor; authenticated dispatch is intact.

The session fixture is an observer-owned protocol model in a separate process
from candidate code, exposed through a bounded local socket inside the native
sandbox. It covers one new session and a mixed reused/new collection, preserves
pre-existing and other-owner state, and models owner-wide closure. It is selected
only for `module.reverse`, so a different declared inverse cannot be certified
by exercising a Python inverse that production would not use. This proves the
candidate's session receipt/inverse protocol, not a live Playwright deployment.

Read-only core support paths cannot be shadowed by candidate files. All original
hash-based oracles, case budgets and native isolation checks remain mandatory.
Regression mutants omit receipts or directory cleanup, delete existing state,
name the wrong session, return a no-op inverse or close all owner sessions;
each must fail. Native delegated tests separately execute valid file/session
round trips and two mutants without publishing or accessing production state.
The native run also exposed an inverse wrapper writing configuration beside
read-only candidate code; it now uses the private work directory, like the
ordinary stdio wrapper. No production authority or admission bypass was added.

## Read-only successor retirement extension (17 September 2026)

The exact-plan successor check rejected a signed catalog adding one repository
entry, after maintenance had already stopped services. The source recipe was
valid; the missing behavior was transition between the two reviewed recipes.

Keep every prior retirement step byte-for-field identical. Permit additional
repository scripts/modules only, with no destination aliasing or new unit
retirements. The successor remains an observer, never an initial neutralizer.
Every known predecessor file still needs its exact retired size/hash. Historical
absence is admissible only within the initial complete census's source roots,
excluding bytecode and cache omissions, and must be matched by live absence of
the original and all retirement/preservation artifacts. Owned, non-writable
directory handles are traversed without links and revalidated after the read.
No synthetic tombstone and no mutation of historical evidence is introduced.

The complete plan digest includes added entries. Each successor, including an
exact replay or the following release, re-proves the evidence under deployment,
startup and maintenance locks. Initial transition requirements are unchanged.
The development release tool runs the same observer before maintenance and
binds the immutable census ID to authenticated current startup materials; this
is only early refusal, never a completion capability or a substitute for the
locked product check.

Tests cover real retirement files, absent and already-retired additions, replay,
all prior-step mutations, aliasing, filesystem races, permissions, links,
historical-census omissions and unbound early-preview inputs. The real signed
previous/candidate catalogs and initial census also passed the patched observer
twice in a read-only diagnostic. This does not certify a newly built distribution
or a completed service restart; production remained on its prior release at
that diagnostic checkpoint. The later signed 63-to-64 transition passed on
17 September, with all six live job contracts and every saved result retained;
F5 remained inactive. Deployment evidence is recorded separately in
`internal/reports/lre-generic-parallelism-20260917.md`.

The development release tool also preserves repeated byte-identical unclaimed
builds in bounded, fresh archive siblings after validating the existing
archive's metadata and census. It never overwrites an earlier archive. The
administrative helper invariant starts after authenticated predecessor
recovery, while mutations by the builder still refuse the release. These
orchestration fixes do not change the signed runtime or weaken cutover checks.
