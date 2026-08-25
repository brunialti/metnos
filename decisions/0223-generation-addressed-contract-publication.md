---
id: 0223
title: Immutable publication of localized executor contracts
date: 2026-08-25
status: accepted
area: executor | signing | localization | loader
related:
  - 0193
  - 0219
complements:
  - 0220
---

## Context

At the time of this decision, RM-0005 provided the localization registry,
candidate review, contract promotion and explicit instance-language
activation. Its legacy publisher changed the live manifest and invoked
`sign_executor()`, which recalculated the digest from whatever code was then
present. A localization could therefore adopt an unrelated technical change.
Manifest, signature and language state were written separately, and exception
rollback did not cover a process crash.

Verification and use were also disconnected. `verify_executor()` verified one
read of the manifest and reopened the path to parse it; the loader performed
another independent read. Candidate evidence was not compared with the current
base at commit, and concurrent promoters had neither a shared writer lock nor
a conditional update.

These defects block publication-time enforcement of RM-0002. They do not by
themselves require a new executor package manager, an atomic technical deploy
or a new runtime-release format.

## Decision

The first implementation is limited to safe publication of the contract
revisions required by localization and their minimal retirement/reactivation
lifecycle.

For each installed contract, Metnos stores immutable revisions. An active
generation contains exactly:

```text
manifest.toml
manifest.toml.sig
manifest.lang_state.json
```

The logical generation identifier is a length-delimited SHA-256 digest of
those three files in a fixed order and has the form `sha256:<64hex>`. Its
directory name is only `<64hex>`, so it is portable to NTFS. A single regular
file named `current` contains the logical identifier and is atomically replaced
only after the complete generation has been written, flushed and verified.

An existing generation directory is never replaced. It must contain exactly
the three regular files, with no extras, symlinks or reparse points. Identical
bytes are reused and the directory durability barrier is repeated; any other
state is `generation_corrupt`.

`current` may instead select a signed retirement tombstone containing exactly
`retirement.json` and `retirement.json.sig`. The canonical JSON binds schema
and version, `ContractId`, the previous active generation, actor and reason.
Its Ed25519 signature covers the domain prefix
`metnos.contract-retirement/v1\0` followed by those JSON bytes; its revision
ID is the same length-delimited construction over the two tombstone files.
The two closed payload shapes make the revision kind unambiguous without a
general envelope or code package.

Each contract directory also contains one immutable `binding.json` outside the
revisions. It holds only schema version and canonical `ContractId`; the
directory name must equal the SHA-256 storage key of that ID. Source path and
allowed code roots are reconstructed from the versioned origin map. No
absolute path, manifest metadata or global binding index is persisted, and the
active generation remains exactly three files. Operational skill enablement
remains an external policy keyed by the structural identity; it is not read
from the authoring manifest. A `DISABLED` installed source remains publishable
while policy hides it; a source classified `RETIRED` is rejected by ordinary
publishers.

Localization state has one canonical v1 JSON representation: schema version,
a `selectors` object, normalized language tags, sorted keys, compact UTF-8 and
one final newline. Argument-description selectors follow the complete nested
JSON-Schema path, for example
`args.properties.messages.items.properties.body.description`; they are
enumerated through the standard JSON-Schema child constructs rather than
limited to first-level arguments. Structural position distinguishes the
`description` keyword from a property itself named `description`. The
legacy first-level form `args.<name>.description` is migrated once and
thereafter rejected. The migration rebuilds the resource set from each parsed
manifest and returns deterministic evidence for every added, normalized,
dropped or provenance-cleared entry. It preserves source provenance only when the old
version hash matches the current text; otherwise it records the current hash
and clears provenance that can no longer be proven. Collisions fail, and the
complete result passes strict v1 validation. Signing, synthesis and migration
tools then share the same canonical encoder.

The existing manifest signature remains the sole cryptographic authorization
of an active contract and its declared code digest. The generation hash
prevents accidental mixing of manifest, signature and localization state. A
retirement has no manifest and is signed by the same trust authority in its
distinct domain. No second envelope signature or receipt authority is
introduced in version 1.

### Verified bytes

Verification returns an immutable value holding manifest bytes, the object
parsed from those bytes, signature bytes, localization-state bytes, signer and
declared and verified code digests. The loader constructs the executor from
the `VerifiedManifest.parsed` object in that value and does not reopen
`manifest.toml`.

The complete read type is
`ContractRevision = VerifiedManifest | ContractRetirement`.
`current_contract()` authenticates either member. `current_manifest()` exposes
only an active generation and reports `contract_retired` for a tombstone; no
ordinary technical or signed-source publication may implicitly cross that
state.

Retirement removes execution authority but does not release the public name.
Catalog admission authenticates the tombstone's previous generation and keeps
its executor name reserved to the same `ContractId`, just as it does for an
installed contract hidden by `DISABLED`. This prevents another structural
identity from acquiring the name-derived localization resources retained in
historical registry rows.

Signing and signature verification gain pure byte APIs that receive already
loaded private/public key objects, not key names or paths. The localization path
preserves the existing code digest and never calls the authoring helper that
recalculates it.

Code paths are always resolved from the source manifest directory recorded by
the shared inventory, never from the generation directory. After resolution,
every path must be contained in one of the code roots admitted for that source.
This intentionally supports ordinary relative paths such as `../../...`
without granting arbitrary traversal.

After cutover the loader enumerates contract bindings, reconstructs structural
`ManifestRef` values whose authoring observations (`name`, lifecycle and
manifest hash) are absent, and calls `current_manifest(ref, ...)`. The inventory no
longer parses authoring manifests for live name, lifecycle or content; those
come only from `VerifiedManifest.parsed`.

Technical publication treats its expected old generation as an authenticated
CAS base: its stored bytes, signature, language state and generation digest
must be valid, but its old declared code digest is not compared with code that
is intentionally being updated. The candidate alone must match current code.
Live loading and localization publication continue to require full code-digest
verification.

### Linguistic authority

A localization publication:

- starts from the expected current generation and verifies its signature and
  code digest;
- may change only registered selectors in the declared target language;
- verifies source, previous target and candidate hashes;
- proves structurally that all other manifest values are unchanged;
- reruns the real executor standard and target-language linter;
- signs and immediately verifies the prepared manifest in memory;
- publishes manifest, signature and language state as one generation.

M3 exercises this path only in isolated stores or behind a production flag
that defaults off and is not a user preference. Production calls fail closed
until M4. It does not mirror authoring or change the production
registry while legacy files remain authoritative. M4 enables the live call and,
only as part of the cutover, reconciles the three authoring files under the
same writer lock. Registry reconciliation occurs after lock release from a
fresh, verified read of `current`, including the idempotent retry path.

The localization registry is workflow state. It does not grant filesystem
paths or signing authority. If publication succeeds but registry reconciliation
or language activation fails, the generation remains valid but dormant and a
retry reconciles it from the hashes in its language state.

### Concurrency and commit

Every cooperating operation that can change catalog membership, a visible
name or shared authoring files first acquires a small global catalog-admission
lock. A publisher then takes the contract's in-process mutex and permanent
writer lock; a visibility-policy change takes its policy lock instead. The
roles stay separate: the global lock makes the catalog-wide name and
membership decision stable, while the per-contract lock and expected
generation serialize `current` and reject a candidate prepared from an
obsolete base. Store-only catalog loading uses the same global boundary and
therefore sees one stable membership snapshot.

The fixed order is production catalog → optional shadow catalog → contract
writer locks in canonical `ContractId` order. Lifecycle and managed service
operations use production catalog → reconcile. No cooperative path acquires
the reverse order. The catalog lock is reentrant in one thread; after `fork`,
in-memory ownership bookkeeping is discarded so the child must contend on the
real file lock.

Linux uses `flock`; Windows locks the first byte of a permanent,
at-least-one-byte regular file, seeking to zero and acquiring and releasing
through the same handle. Acquisition retries are bounded by a monotonic
deadline.

The public publisher owns the catalog→writer boundary. `sign.py publish`
prepares an immutable technical draft and calls one technical-publication
transaction; it does not run offline signing first, pre-acquire the writer lock
or call a second public publisher under it. The transaction revalidates base,
draft and code, updates the digest in memory, signs, publishes and reconciles
authoring before releasing the writer and global locks.

Translation and semantic review occur before lock acquisition. Under the
lock, the publisher performs only deterministic verification, structural
comparison, linting, byte signing and filesystem publication.

`current` is fully verified before conflict or idempotency is evaluated. A
retry is successful only when the current verified generation equals the
complete desired three-file postcondition; the presence of requested patches
alone is insufficient.

The only per-contract visible commit operation is replacement of `current`.
A crash before that replacement leaves the previous generation current; a crash after it
leaves the complete new generation current. Under the same writer lock, or
during quiescent cutover, a reserved `.generation-<suffix>` left by a crash is
removed only when it is a plain directory whose regular files form a subset of
exactly one allowed payload family: active generation or retirement. Empty
reserved staging is also recoverable. Links, nested directories, unknown names
and mixed families fail closed and remain untouched. Complete unreferenced
revisions are only diagnosed; version 1 adds no garbage collector.

The same recovery boundary recognizes only the closed, process-generated
temporary-name grammar for sibling `binding.json` and `current` files. A
temporary binding must contain the exact canonical binding; a temporary
pointer must name an already present authenticated revision. Recovery validates
all direct temporaries and all `.generation-*` directories before deleting the
first one. It therefore cannot half-clean a contract and then discover hostile
debris, and it never promotes a temporary file into authority.

Pointer temporary files are created on the same volume, flushed and closed
before `os.replace()`. Windows sharing violations are retried for a finite
period and the previous pointer is never deleted first. NTFS certification
covers process crashes and atomic visibility; because the standard library
cannot flush a directory on Windows, survival of the latest pointer across
sudden power loss is not promised.

The one-time cutover belongs to the managed Metnos server and is supported on
Linux with systemd only. M4 writes and `fsync`s a temporary marker, renames it,
then `fsync`s the parent directory; it repeats the parent-directory barrier
after renaming the complete shadow root. Windows remains a remote-client
platform. Its NTFS certification covers the portable per-contract store
primitives above, not a second server installer or a Windows catalog cutover.

Technical rollback authenticates the current revision as its signed,
structurally valid CAS base without comparing that revision's declared digest
with authoring code that the caller has already restored. It then verifies the
target generation against the restored authoring code and atomically repoints
`current` under the same lock, recording actor and reason in the ordinary audit
log. It neither copies nor edits code or revisions. It reconciles authoring
under the lock and workflow state from a fresh read after releasing the lock.
If the CAS base is a tombstone, rollback may explicitly restore an existing
generation through that same audited boundary; it cannot import a new source
or create a new generation.

Retirement is a separate conditional writer. It verifies the expected active
generation while source and code still exist, writes and authenticates the
domain-separated tombstone, records an idempotent audit authorization and
makes only that tombstone current. Registry rows for the exact `ContractId`
are invalidated after the commit. Reactivation is equally explicit and
audited: it uses the expected tombstone as CAS base, its authenticated previous
generation as linguistic/schema policy base, verifies new authoring code and
publishes a new active generation. Reinstalling or ordinarily publishing a
source cannot reactivate a retired contract.

### Authoring sources

Repository manifests, generated executors and imported packages remain
authoring sources. Existing technical tools may create, test and sign those
files. After cutover, a local source becomes live only through
`publish_technical_update()`; an already signed import uses
`publish_signed_source()` with the same policy checks.

The canonical authoring command is
`python3 runtime/sign.py publish executors/<name>`: it signs, verifies and
publishes inside the single transaction described above. `sign` alone remains
an offline operation and explicitly reports that the source is not live. Every
operational signing callsite is migrated at cutover.

Technical evolution may change code, schema and surfaces. Existing
selector/language values and provenance remain byte-identical; changing an
existing text uses the localization publisher. New selectors or languages are
allowed only with complete coverage and the same RM-0002 local and parity
checks. Ordinary publication forbids removals. A schema removal requires an
exact canonical selector list plus actor and reason, must match the structural
diff, and is audited. The publisher never invents or silently merges text.

This keeps Git-publishable sources and avoids redesigning technical code
deployment. It also avoids an indiscriminate ban on file writes: only direct
writes into the generation store and live reads from authoring sources after
the global cutover are forbidden.

### Global cutover

Shadow generations live under a root that the loader never inspects. A fixed
sibling marker `contract-publications.ACTIVE` holding `v1\n` is a durable,
one-way bootstrap boundary, not a content selector or second authority. Legacy
is permitted only while both marker and production container are absent. A
complete, non-empty top-level container/root without the marker means
store-only; the same root with the valid marker means active. Marker or
container present with a missing, empty, incomplete or foreign top-level root
means `RECOVERY_REQUIRED`, never legacy, active or store-only. Per-contract
revision validity is checked separately. Top-level completeness means that the
container holds only `v1`, that `v1` is a non-empty plain directory, and that
each child is a plain canonical contract-key directory. This lets a locked
retry finish a new binding interrupted before `current` without calling an
empty or malformed global root active. Loss of `ACTIVE` cannot silently
reactivate legacy after the global swap. An invalid marker is a distinct
fail-closed `active_marker_invalid` error; it is never treated as an absent
marker. A production-container path that is itself a link or not a directory
similarly raises `production_store_invalid`; `RECOVERY_REQUIRED` covers a
container missing after the marker, or a plain container/root that is empty,
incomplete or internally foreign.

Cutover runs in a short quiescent maintenance window. It blocks new turns,
schedulers, publishers, reloads and watchers and drains already admitted
turns. The managed guard holds production catalog admission and then service
reconcile through the first cold store-only load. Activation reacquires the
production lock reentrantly, then takes the shadow lock while the shadow can
still be consumed and the per-contract writer locks in canonical order. This
is the same production→shadow→writer / production→reconcile order used by the
ordinary boundaries; ordinary productive publication also takes production
admission before its contract writer.

The prepared administrative boundary keeps production admission from its
last preflight through registry reconciliation. Before writing the marker it
checks the report schema and exact contract count, a clean and identical
authoring inventory, the exact manifest/signature/language-state bytes and
generation IDs in both source and shadow, and registry ownership conflicts in
read-only mode, including historical and stale rows. A missing registry is not
created by preflight. Only then does the boundary durably create the marker,
atomically rename the complete shadow container to production on the same
volume, reread production and reconcile the registry. The surrounding managed
cutover, still holding production→reconcile, then performs the first store-only
load. It releases that filesystem/lifecycle lock only after the load is green
and while services are still stopped. The operator then starts the target and
checks its readiness immediately. Individual units become reachable according
to normal systemd startup semantics: version 1 does not add a second,
target-wide maintenance gate for the interval between unit start and the final
readiness report. This does not expose a partial contract catalog, because the
authenticated cold load precedes every service start.
The sequence is indivisible with respect to cooperating catalog writers; it is
not presented as a cross-filesystem/SQLite rollback transaction. If registry
mutation fails after the swap, the already authenticated production revision
remains valid and the retry rule below repairs workflow state.

After the global move, a retry no longer depends on the consumed shadow or on
the report's pre-cutover generation IDs. It inventories and authenticates the
current production revisions, including retirement tombstones, repairs only a
missing valid marker when production is store-only, and reconciles registry
state from a fresh read. Thus a legitimate publication or retirement after
the swap cannot be overwritten by stale shadow evidence. A crash before the
marker leaves legacy; a crash after it is fail-closed or store-only, never
hybrid.

A genuinely new contract may create its first binding and generation with no
expected generation when `current` is absent and, after reserved staging
recovery, no authoritative history exists or the only history is the exact
candidate generation left between rename and pointer. A binding-only retry is
therefore recoverable. Any other history without `current` is corruption, not
initialization; until the pointer exists the post-cutover loader does not expose
the contract.

## Explicit residual risk

Version 1 guarantees that localization does not authorize changed code. It
does not guarantee that code bytes reopened later by the current runner are
identical to those verified at catalog load. If external code changes, digest
verification rejects the contract when it is checked again, but a
verification-to-invocation window remains. Catalog caching is keyed by the
published revision, not authoring `mtime`: changing only an authoring code file
does not create a revision and is intentionally not a cache invalidation or a
deployment. Already loaded modules/processes are not made immutable by this
decision.

Closing that window requires a separate design covering process dependencies,
in-process builtins, remote bundles and release identity. Copying only the
declared files would be insufficient without first proving that they form the
complete execution closure. That work is tracked separately as
`EXEC-BIND-001` and is not silently included in this decision.

The threat boundary is otherwise unchanged: this design protects against
programming errors, cooperating-writer races, stale candidates and partial
publication. It does not protect keys and files from arbitrary code already
running as the same operating-system user.

## Alternatives considered

### Signed envelope and packaged code in every generation

This would provide a stronger code-to-execution binding, richer receipts and a
path toward technical deployment. It was removed from version 1 because it
also requires dependency closure, runner and remote-protocol changes, builtin
release identity and packaging migration. Those requirements are materially
larger than the linguistic-publication defect.

### SQLite as the current pointer

A small SQLite table could combine serialization, conditional update and
idempotent receipts. It was not selected because it adds a new runtime
authority and migration to a file-oriented public contract format without
removing the need to verify and synchronize filesystem payloads.

### Journal over the three live files

A journal would require every reader to understand intermediate states and
replay. It leaves more states than an immutable directory plus one pointer and
was rejected.

### Lock without generations

A lock serializes cooperating writers but does not give lock-free readers a
single complete state and cannot make three live-file replacements atomic. It
is necessary but insufficient.

### Continue exception rollback

Restoring old bytes in an exception handler cannot run after termination,
power loss or machine reset and does not join independent reads into one
snapshot. It remains useful only for temporary-directory cleanup.

## Consequences

The loader, localization pipeline, activation validator and authoring-source
publisher, retirement writers and installer must share the same small
contract-store and cutover boundary. The legacy aligner is
retired or becomes a caller of the localization publisher in M4. M3 remains
non-productive. Migration builds and checks contracts in a separate shadow,
then durably crosses `ACTIVE` and switches the complete catalog once; a running
layout is never hybrid and an invalid store never silently restores an
authoring source.

Before implementation, a regenerable census classifies every manifest,
signature and language-state reader or writer in runtime, installers and
scripts. Cutover is blocked by any unclassified live callsite. In the same M4
commit, the operational signing callsites and `CLAUDE.md` §7.10 change to the
fail-loud `sign.py publish` workflow; documenting it earlier or later would
make the invariant workflow false.

Certification includes real multiprocess Linux and Windows/NTFS tests for the
portable per-contract store, not only mocks: byte lock handling, finite
timeout, process-crash boundaries, existing-generation reuse and pointer
sharing retries. Binding-only loading, the global fail-closed boundary,
quiescence and managed restart are certified on the supported Linux/systemd
server installation.

Disk use grows because revisions are retained. Version 1 accepts that cost and
reports unreachable revisions instead of adding premature garbage collection.
The previous active generation whose identifier is authenticated by a current
tombstone is reachable audit and rollback evidence, not an orphan. Its payload
is verified when it is selected as a rollback/reactivation target. Reserved
structurally recognizable crash staging is transaction recovery, not garbage
collection; all other debris is left untouched and blocks the relevant
operation.

The external adversarial review was resolved in the normative RM-0007
specification. M4 then completed the callsite census, productive migration,
store-only load, controlled restart and two green post-cutover cycles on the
reference installation. The complete certification is recorded in
`internal/reports/rm0007-final-certification-20260825.md`; this decision is
therefore accepted. RM-0002 subsequently completed its publication-time gates.
Localization of `affinity` remains outside this ADR.
