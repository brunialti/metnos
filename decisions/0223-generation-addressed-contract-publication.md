---
id: 0223
title: Immutable publication of localized executor contracts
date: 2026-08-25
status: proposed
area: executor | signing | localization | loader
related:
  - 0193
  - 0219
complements:
  - 0220
---

## Context

RM-0005 provides the localization registry, candidate review, contract
promotion and explicit instance-language activation. Its publisher currently
changes the live manifest and invokes `sign_executor()`, which recalculates the
digest from whatever code is then present. A localization can therefore adopt
an unrelated technical change. Manifest, signature and language state are
written separately, and exception rollback does not cover a process crash.

Verification and use are also disconnected. `verify_executor()` verifies one
read of the manifest and reopens the path to parse it; the loader performs
another independent read. Candidate evidence is not compared with the current
base at commit, and concurrent promoters have neither a shared writer lock nor
a conditional update.

These defects block publication-time enforcement of RM-0002. They do not by
themselves require a new executor package manager, an atomic technical deploy
or a new runtime-release format.

## Decision

The first implementation is limited to safe publication of localized contract
variants.

For each admitted contract, Metnos stores immutable generations containing
exactly:

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

Each contract directory also contains one immutable `binding.json` outside the
generation. It holds only schema version and canonical `ContractId`; the
directory name must equal the SHA-256 storage key of that ID. Source path and
allowed code roots are reconstructed from the versioned origin map. No
absolute path, manifest metadata or global binding index is persisted, and the
generation remains exactly three files. Operational skill enablement remains
an external policy keyed by the structural identity; it is not read from the
authoring manifest.

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
of the contract and its declared code digest. The generation hash prevents
accidental mixing of manifest, signature and localization state. No second
envelope signature or receipt authority is introduced in version 1.

### Verified bytes

Verification returns an immutable value holding manifest bytes, the object
parsed from those bytes, signature bytes, localization-state bytes, signer and
declared and verified code digests. The loader constructs the executor from
the `VerifiedManifest.parsed` object in that value and does not reopen
`manifest.toml`.

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

All cooperating writers for one contract use the same permanent lock file and
an in-process mutex acquired in a fixed order. Linux uses `flock`; Windows
locks the first byte of a permanent, at-least-one-byte regular file, seeking to
zero and acquiring and releasing through the same handle. Acquisition retries
are bounded by a monotonic deadline. The publisher also requires an expected
generation: the lock serializes writers, while the comparison rejects a
candidate prepared from an obsolete base.

The public publisher owns that lock. `sign.py publish` prepares an immutable
technical draft and calls one technical-publication transaction; it does not
run offline signing first, acquire its own lock or call a second public
publisher under the lock. The transaction revalidates base, draft and code,
updates the digest in memory, signs, publishes and reconciles authoring before
releasing the single lock.

Translation and semantic review occur before lock acquisition. Under the
lock, the publisher performs only deterministic verification, structural
comparison, linting, byte signing and filesystem publication.

`current` is fully verified before conflict or idempotency is evaluated. A
retry is successful only when the current verified generation equals the
complete desired three-file postcondition; the presence of requested patches
alone is insufficient.

The only per-contract visible commit operation is replacement of `current`.
A crash before that replacement leaves the previous generation current; a crash after it
leaves the complete new generation current. Complete unreferenced generations
and incomplete temporary directories are reported but not automatically
deleted in version 1.

Pointer temporary files are created on the same volume, flushed and closed
before `os.replace()`. Windows sharing violations are retried for a finite
period and the previous pointer is never deleted first. NTFS certification
covers process crashes and atomic visibility; because the standard library
cannot flush a directory on Windows, survival of the latest pointer across
sudden power loss is not promised.

The one-time cutover boundary is stricter. On Linux, M4 writes and `fsync`s a
temporary marker, renames it, then `fsync`s the parent directory; it repeats
the parent-directory barrier after renaming the complete shadow root. On
Windows, it creates the final marker with
`CreateFileW(FILE_FLAG_WRITE_THROUGH)`, writes `v1\n`, calls
`FlushFileBuffers()` on that handle, and closes it; it then moves the complete
same-volume shadow directory to an absent production name with
`MoveFileExW(MOVEFILE_WRITE_THROUGH)`. Failure of any native call aborts before
cutover is acknowledged. This does not strengthen every ordinary `current`
update into a power-loss transaction.

Rollback verifies an existing generation and atomically repoints `current`
under the same lock, recording actor and reason in the ordinary audit log. It
does not edit or duplicate the selected generation. It reconciles authoring
under the lock and workflow state from a fresh read after releasing the lock.

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
is permitted only while both marker and production root are absent. Production
root present means store-only even if the marker is lost; marker present with a
missing or incomplete root fails closed and resumes cutover. Thus loss of
`ACTIVE` cannot silently reactivate legacy after the global swap.

Cutover runs in a short quiescent maintenance window. It blocks new turns,
schedulers, publishers, reloads and watchers and drains already admitted
turns. The sole migration writer regenerates the structural inventory and
reverifies every binding, signature and code digest. It then durably creates
the marker, atomically renames the complete shadow root to the production root
on the same volume, performs a full store-only load and a controlled global
restart/swap, and only then reopens inputs. A crash before the marker leaves
legacy; a crash after it is fail-closed or store-only, never hybrid. No global
lock is added to ordinary publication.

A genuinely new contract may create its first binding and generation with no
expected generation only when no publication history exists; until then the
post-cutover loader does not see it. Missing `current` with existing history is
corruption, not initialization.

## Explicit residual risk

Version 1 guarantees that localization does not authorize changed code. It
does not guarantee that code bytes reopened later by the current runner are
identical to those verified at catalog load. If external code changes, digest
verification rejects the contract when it is checked again, but a
verification-to-invocation window remains.

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
publisher must share the same small contract-store API. The legacy aligner is
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

Certification includes real multiprocess Linux and Windows/NTFS tests, not
only mocks: byte lock handling, finite timeout, process-crash boundaries,
existing-generation reuse, pointer sharing retries, binding-only loader,
single-lock technical publication and global-boundary fail-closed behavior.

Disk use grows because generations are retained. Version 1 accepts that cost
and reports orphans instead of adding premature garbage collection.

The external adversarial review has been resolved in the normative RM-0007
specification, but this ADR remains `proposed` until the M4 cutover and its
certification succeed. RM-0002 may proceed through its independent early
phases, but its new publication-time blocks wait for the implemented boundary.
Localization of `affinity` remains outside this ADR.
