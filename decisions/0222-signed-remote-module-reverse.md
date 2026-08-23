---
id: 0222
title: Signed remote module reverse and preserved dialog turn identity
date: 2026-08-23
status: accepted
area: remote protocol | undo | orchestration
related:
  - 0001
  - 0217
  - 0221
modifies:
  - 0183
---

## Context

The live packaged-app test produced an exact reversible receipt, yet
`undo_last_turn` rejected it with `module.reverse non eseguibile su un device
remoto`. ADR 0183 had made the closed filesystem reverse patterns remote by
translating them into ordinary executor calls, but a custom reverse function
still had no authenticated device entrypoint. Running that function on the
server would apply an inverse on the wrong host; inventing a normal forward
argument for each executor would duplicate domain semantics and create
executor-specific protocol exceptions.

The same live flow exposed a second correlation defect. Completion of a
consent dialog retained `origin_turn_id` as UI metadata but did not put it in
the callback consumed by `gate_dispatch`. The resumed mutation was therefore
journalled with an empty turn ID. Historical empty IDs were then grouped as if
they were one turn, producing a false overall failure even after the exact
process had been stopped.

## Decision

The signed remote invocation has a closed `operation` field with two values:
historical `invoke` and `reverse`. `invoke` remains omitted on the wire for
byte-compatible signatures. A client advertises support positively through
the authenticated profile capability `executor_reverse_v1`; the server never
infers support from an OS name or version number. A reverse invocation is
queued only when the capability is present, its arguments are exactly
`{plan, results}`, and the current manifest declares both `revertible=true`
and `module.reverse`.

The client binds `operation` into the server signature, verifies the downloaded
manifest again, and exposes the selected operation to the sandbox only through
a runtime-owned environment value. The common `run_stdio` boundary then calls
either `invoke(args)` or the same signed module's `reverse(plan, results)`.
Consequently every executor using the standard boundary receives remote custom
undo without an executor-name registry, a domain switch or a second code
bundle. Closed filesystem patterns continue to use ADR 0183's ordinary
executor-call translation.

Transport completion and semantic restoration are distinct. The signed result
payload passes through unchanged, and each custom reverse must attest its own
postcondition. `run_processes.reverse` therefore requires `restored=true` from
the client provider; an outer `ok=true`, a vanished launcher PID or an empty
candidate set cannot close the undo record as successful.

Dialog creation copies a non-empty `origin_turn_id` into the persisted callback
unless that callback deliberately carries another turn ID. All completion
paths therefore send the originating identity to the common executor dispatch.
As a conservative migration rule, legacy operations with an empty turn ID are
isolated per operation instead of being grouped together.

## Alternatives considered

A generic `reverse_executor` bundle was rejected because it would need to load
or trust another executor's code outside the bundle selected by the signed
invocation. Adding `stop` or `undo` arguments to each forward executor was
rejected because planner-visible schemas are not internal entrypoint selection
and would require per-domain conventions. Dispatching custom reverse locally
on the server was rejected because the receipt describes state on the device.
Gating the new field by a minimum client version was rejected in favour of
explicit protocol capability negotiation.

## Consequences

`module.reverse` now has the same-device guarantee as the closed reverse
patterns. Older clients fail before queue admission and cannot silently treat
a reverse payload as a forward call. The initial 0.2.58 round trip proved the
protocol but exposed an insufficient application postcondition: a packaged app
could hand execution away from its activation PID while the window remained
open. Client 0.2.62 and the revised executor require a verified, post-boundary
package-process cohort plus a positive restoration attestation. Live turn
`5b1cc75b42aa4aeb` and reverse invocation
`inv-18ce87c4bcce8dcbde1f61c5` closed the application and the undo record.
New consent continuations retain one coherent turn boundary; malformed legacy
empty records cannot make one undo reach unrelated historical effects.
