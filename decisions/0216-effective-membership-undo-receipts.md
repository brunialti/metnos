---
id: 0216
title: Effective membership undo receipts
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
mutations and operations to which undo does not apply. Gmail label updates sent
requested additions and removals but did not retain the state observed before
the update. Reversing the requested arguments would be wrong when a label was
already present or absent.

The same audit found mutations involving mixed branches, operating-system
identity or sensitive prior state. Those cases cannot be solved by extending
an executor-name switch in the undo broker.

## Decision

Metnos represents reversible membership changes as provider-neutral before,
after, added and removed sets. The forward provider reads the exact resource,
performs the mutation and records the effective delta returned by the provider.
The reverse adds only members actually removed by the forward operation and
removes only members actually added. It does not infer an inverse from the
requested arguments and does not replace the entire current set. Gmail labels
are the first consumer of this generic receipt.

The signed executor contract declares both read and write authority because the
pre-state read is part of the mutation. No executor, provider, label, path,
package or natural-language value is added to the undo broker.

`open_sites`, `delete_dirs`, `set_signatures`, `run_processes` and
`login_urls` remain unchanged until their branch, storage or sensitive-state
specifications are explicitly approved. `set_credentials` and `set_persons`
remain non-undoable by product decision: deletion of credentials or biometric
data requires an explicit user request.

## Alternatives considered

Reversing Gmail's requested `add` and `remove` arguments was rejected because
it would remove labels that existed before the turn or add labels that were
already absent. Restoring the entire prior label set was rejected because it
would overwrite unrelated concurrent changes.

Making every audited executor globally revertible was rejected because a
manifest boolean cannot honestly describe mixed no-effect, reversible and
policy-irreversible branches without a general per-execution receipt contract.

An atomic relocation prototype for local directory deletion passed direct
tests but failed through the real sandbox: the target and central history store
are separate mounts, so `rename` returns `EXDEV` even on the same physical
filesystem. Copy followed by recursive deletion was rejected because it lacks
a single crash-safe commit boundary. The prototype was removed before public
deployment and the problem returned to design.

## Consequences

Gmail label mutation performs one additional exact read per message before the
write and stores label identifiers, never message content. Its reverse applies
only the effective membership delta and fails honestly on a malformed receipt
or provider error.

The remaining design work is recorded in
`internal/design/undo-redesign-spec-20260823.md` and is intentionally not
implemented by this ADR.
