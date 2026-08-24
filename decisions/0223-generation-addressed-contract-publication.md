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

## Proposed decision

The first implementation is limited to safe publication of localized contract
variants.

For each admitted contract, Metnos stores immutable generations containing
exactly:

```text
manifest.toml
manifest.toml.sig
manifest.lang_state.json
```

The generation identifier is a length-delimited SHA-256 digest of those three
files in a fixed order. A single regular file named `current` contains that
identifier and is atomically replaced only after the complete generation has
been written, flushed and verified.

The existing manifest signature remains the sole cryptographic authorization
of the contract and its declared code digest. The generation hash prevents
accidental mixing of manifest, signature and localization state. No second
envelope signature or receipt authority is introduced in version 1.

### Verified bytes

Verification returns an immutable value holding manifest bytes, the object
parsed from those bytes, signature bytes, localization-state bytes, signer and
declared and verified code digests. The loader constructs the executor from
the parsed object in that value and does not reopen `manifest.toml`.

Signing and signature verification gain pure byte APIs. The localization path
preserves the existing code digest and never calls the authoring helper that
recalculates it.

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

The localization registry is workflow state. It does not grant filesystem
paths or signing authority. If publication succeeds but registry reconciliation
or language activation fails, the generation remains valid but dormant and a
retry reconciles it from the hashes in its language state.

### Concurrency and commit

All cooperating writers for one contract use the same permanent lock file.
Linux uses `flock`; Windows uses the standard-library byte-range lock on a
regular file. Lock acquisition has a finite timeout. The publisher also
requires an expected generation: the lock serializes writers, while the
comparison rejects a candidate prepared from an obsolete base.

Translation and semantic review occur before lock acquisition. Under the
lock, the publisher performs only deterministic verification, structural
comparison, linting, byte signing and filesystem publication.

The only visible commit operation is replacement of `current`. A crash before
that replacement leaves the previous generation current; a crash after it
leaves the complete new generation current. Complete unreferenced generations
and incomplete temporary directories are reported but not automatically
deleted in version 1.

Rollback verifies an existing generation and atomically repoints `current`
under the same lock, recording actor and reason in the ordinary audit log. It
does not edit or duplicate the selected generation.

### Authoring sources

Repository manifests, generated executors and imported packages remain
authoring sources. Existing technical tools may create, test and sign those
files. After cutover, a source becomes live only through a small
`publish_signed_source()` operation that verifies its signature, schema and
code digest and imports the three contract files as a generation.

This keeps Git-publishable sources and avoids redesigning technical code
deployment. It also avoids an indiscriminate ban on file writes: only direct
writes into the generation store and live reads from migrated authoring
sources are forbidden.

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
idempotent receipts. It was not selected for the candidate because it adds a
new runtime authority and migration to a file-oriented public contract format.
The external review is asked to challenge this choice, particularly on Windows
portability and crash durability.

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
retired or becomes a caller of the localization publisher. Contracts migrate
one at a time: a migrated contract is never read from both layouts and is
never silently restored from its authoring source.

Disk use grows because generations are retained. Version 1 accepts that cost
and reports orphans instead of adding premature garbage collection.

This ADR remains `proposed` until the requested external adversarial review is
resolved. RM-0007 contains the candidate implementation and certification
plan. RM-0002 may proceed through its independent early phases, but its new
publication-time blocks wait for this boundary.
