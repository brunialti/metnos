---
id: 0216
title: Exact container and membership undo receipts
date: 2026-08-23
status: accepted
area: runtime | executors | undo
related:
  - 0060
  - 0183
  - 0215
---

## Context

The signed executor catalog distinguished undoable mutations, non-undoable
mutations and operations to which undo does not apply. A semantic audit found
two ordinary mutations whose providers already expose enough identity for an
exact inverse, but whose manifests still denied undo. Local directory deletion
destroyed a tree directly, while Drive deletion already moved the exact folder
ID to trash. Gmail label updates sent requested additions and removals but did
not retain the state observed before the update. Reversing the requested
arguments would be wrong when a label was already present or absent.

The same audit found other executors with mixed branches or sensitive prior
state. Those cases cannot be solved by extending an executor-name switch in
the undo broker.

## Decision

Metnos represents reversible container removal as an exact relocation receipt.
The common filesystem helper atomically renames a directory into the canonical
per-turn undo store and returns the opaque destination. It refuses a history
store nested in the target and refuses cross-filesystem fallback: copy followed
by recursive removal has no single crash-safe commit boundary. The existing
`restore_archived_directory` pattern restores the exact tree through the same
relocation primitive as `swap_src_dst`. Remote translation enables the
already-declared directory mode of the generic `move_files` executor. Google
Drive retains its existing exact-ID trash/restore receipt under the same signed
manifest ceiling.

Metnos represents reversible membership changes as provider-neutral before,
after, added and removed sets. The forward provider reads the exact resource,
performs the mutation and records the effective delta returned by the provider.
The reverse adds only members actually removed by the forward operation and
removes only members actually added. It does not infer an inverse from the
requested arguments and does not replace the entire current set. Gmail labels
are the first consumer of this generic receipt.

No executor, provider, label, path, package or natural-language value is added
to the undo broker. `open_sites`, `set_signatures`, `create_processes` and
`login_urls` remain unchanged until their branch/sensitive-state specifications
are explicitly approved. `set_credentials` and `set_persons` remain
non-undoable by product decision: deletion of credentials or biometric data
requires an explicit user request.

## Alternatives considered

Copying a directory tree and then calling recursive delete was rejected because
a crash can leave an incomplete backup or a partially removed source. Archiving
as a compressed blob was rejected for the device path because the remote undo
surface has no corresponding exact extraction primitive.

Reversing Gmail's requested `add` and `remove` arguments was rejected because
it would remove labels that existed before the turn or add labels that were
already absent. Restoring the entire prior label set was rejected because it
would overwrite unrelated concurrent changes.

Making every audited executor globally revertible was rejected because a
manifest boolean cannot honestly describe mixed no-effect, reversible and
policy-irreversible branches without a general per-execution receipt contract.

## Consequences

`delete_dirs` is reversible only when the local store can make an atomic rename
on the same filesystem; otherwise deletion fails before touching the target.
The undo store retains the tree until the normal history lifecycle removes it.
Gmail label mutation performs one additional exact read per message before the
write and stores label identifiers, never message content. Both executors use
standard receipts and fail honestly on malformed or unavailable inverse state.

The remaining design work is recorded in
`internal/design/undo-redesign-spec-20260823.md` and is intentionally not
implemented by this ADR.
