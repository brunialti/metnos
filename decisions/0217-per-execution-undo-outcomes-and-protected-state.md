---
id: 0217
title: Per-execution undo outcomes and protected exact-state receipts
date: 2026-08-23
status: accepted
area: runtime | executors | undo | windows helper
related:
  - 0001
  - 0060
  - 0183
  - 0210
  - 0211
  - 0216
---

## Context

The executor audit found four operations whose undo applicability cannot be
described honestly by one unconditional boolean. `open_sites` may create a
browser session or reuse one that predates the turn. `set_signatures` may
perform an ordinary exact state transition or create a `forbidden` row that
Law 1 does not permit a generic undo to delete. `create_processes` may reuse a
running process, create a session process, or also modify startup persistence.
`login_urls` may reuse a cookie jar or replace secret session state.

Making those executors globally reversible would expose invalid inverses.
Keeping them globally non-reversible would hide exact inverses that are
available for individual executions. Solving each case in the broker would
couple the runtime to executor names and provider concepts.

## Decision

A signed executor manifest may declare `[undo] outcome = "per_execution"`
only together with `revertible = true` and a reverse pattern. Every completed
execution under that contract must emit `_undo.outcome` with exactly one of
`reversible`, `no_effect`, or `irreversible`. A reversible outcome carries the
executor-owned exact receipt. A no-effect or irreversible outcome closes the
journal operation without adding it to the reverse stack. It still forms the
latest-turn boundary, so a later `undo` cannot unexpectedly reach an older
turn. A missing or malformed conditional receipt fails closed as
irreversible. The runtime classifies the signed outcome generically and has no
executor-name branch.

Exact state transitions use before/after receipts and compare-and-swap at the
storage boundary. `set_signatures` restores a complete SQLite row only when
the current row still equals the recorded `after` state. A transition to
`forbidden` is irreversible, and a pre-existing forbidden row remains
immutable.

Sensitive prior state never enters `undo.jsonl`. `protected_undo` stores
encrypted blobs under an unpredictable handle, derives a domain-separated key
from the local admin key, binds the authenticated envelope to actor and
namespace, enforces private filesystem permissions, and uses the same
retention horizon as the undo journal. `login_urls` records only this handle
and state digests. Its reverse compares the current jar with the recorded
after digest before deleting a newly created jar or atomically restoring the
encrypted prior jar. Successful restoration discards the blob; retention
purges expired authenticated blobs.

Managed process undo uses kernel identity, not discovery by name. Helper
protocol 4 and client/helper release 0.2.56 add a signed typed stop request
that binds package ID, PID and Windows process creation time. The helper
resolves the trusted executable again, opens that exact process object,
compares image and creation time, and terminates only that object. An already
running session is `no_effect`; a newly created session process is reversible.
Persistent start remains irreversible until startup registrations receive an
equally strong independently verifiable identity. No path, command, arguments,
process name or task name is accepted by the stop protocol.

`open_sites` records only newly created session IDs and never closes reused
sessions. `delete_dirs` remains non-undoable because the local sandbox can
place the target and history store on separate mounts; its storage architecture
has not been selected. `set_credentials` and `set_persons` remain
non-undoable by product decision and are deleted only by an explicit user
request.

## Alternatives considered

An executor-name switch in the undo broker was rejected because it would make
every new mixed executor a runtime change and would duplicate provider
semantics outside the signed executor contract. Inferring no-effect from
fields such as `cached`, `reused` or diagnostic text was rejected because
those fields are not a general security contract.

Stopping processes by executable name, package ID, or PID alone was rejected:
all three can designate state that predates the turn, and a PID may be reused.
Treating persistent startup as reversible with only the current deterministic
task name was rejected because a replaced registration could be deleted by
mistake.

Writing cookie contents into the undo journal or a plaintext history file was
rejected because it duplicates live authentication secrets into audit state.
Restoring a previous row or cookie jar without comparing the current after
state was rejected because it would overwrite concurrent changes.

## Consequences

The public catalog retains the three user-facing classes—undoable, not
undoable and not applicable—but identifies conditional undo contracts within
the undoable class. The live census is 23 undoable, 13 not undoable and 49 not
applicable first-party executors.

Conditional executors must test reversible, no-effect, irreversible and
malformed-receipt outcomes. State-bearing reverses must test concurrent change
and second-undo behavior. Managed stop additionally requires the Windows
cross-build and real Windows validation before release claims are complete.
