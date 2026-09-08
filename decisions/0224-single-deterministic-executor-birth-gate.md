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
