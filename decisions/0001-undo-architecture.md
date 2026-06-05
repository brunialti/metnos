---
id: 0001
title: Undo as a generic runtime hook with executor-supplied reverse()
date: 2026-04-28
status: accepted
area: undo
related:
  - 0002
---

## Context

On the morning of April 28, 2026, after Metnos had executed its first "real"
run that mutated the user's filesystem — sorting the 98 photos in
`/home/user/images/` into per-year folders by EXIF date — we hit the
absence of any undo capability from Telegram. The only recovery path was a
manual bash command (`cd`, `for`, `mv`, `rmdir`) that I had to run myself.
This is a product problem, not a feature gap: a personal assistant that
modifies user files *without an undo* is not usable as a daily driver.
Roberto confirmed that undo was always in the spec (memory
`metnos_session_resume_28apr_v4`, finding 3) but had been parked under
"phase 5" without a concrete design.

Two constraints framed the work. First, undo must be **generic**: no
per-executor hardcoded logic that the runtime needs to know about. Second,
it must be **crash-safe**: a `kill -9` mid-critical-op must not leave a
state Metnos can't reason about.

## Decision

Three coupled mechanisms:

**Append-only on-disk log** (`runtime/undo.py`, file
`~/.local/share/metnos/undo.jsonl`). Whenever the runtime invokes an
executor whose manifest declares `revertible = true`, it writes a `pending`
record (args, plan, turn_id), then a `done` record (results) if the call
succeeds. All writes use `fsync`+`flock`. A `kill -9` mid-call leaves a
`pending` with no `done` that `find_crashed()` surfaces at boot.

**`reverse(plan, results)` exposed by the executor itself.** The runtime
doesn't know what the op does; it only knows that a reversible executor
exposes a Python function with that signature. For `move_files`, reverse
is straightforward (swap src↔dst from result pairs). For `create_dirs` we
had to add a `created: bool` field on every result entry, because the
reverse must remove ONLY directories actually created — not pre-existing
ones (with `exist_ok=True` create_dirs accepted both cases without
distinguishing). For `fs_write` reverse is deferred to a second iteration:
it requires a backup blob ≤10 MB of the pre-write content, and the blob
storage is not necessary for the undo MVP.

**Per-turn granularity.** When Roberto asks "undo the last command", Metnos
takes the latest turn_id with at least one revertible `done` not yet
`undone`, and in reverse order of execution calls each one's reverse. Op
that are inherently irreversible in that turn (mail sends, etc.) are
skipped and reported. The `undo_last_turn` executor (manifest + code +
sign, like any other) implements this with dynamic import of the relevant
executor modules.

Runtime hook: in `agent_runtime.py:670` (around `invoke_executor`), before
launching the subprocess we read `Executor.revertible` from the catalog;
if `True`, we write `pending`, run, write `done` if ok.

## Alternatives considered

**Reverse hardcoded in the runtime.** A runtime function that knows
per-executor name what "reverse" means. Pro: no API for the executor.
Con: the runtime knows executors by name, anti-modular; every new
critical executor requires a runtime patch; bad for the synt that wants
to propose new executors autonomously. Rejected.

**Filesystem snapshot before every critical op.** Pro: universal undo,
no reverse function to write. Con: expensive (gigabytes per turn), does
not generalize to non-filesystem ops (mail, network). Rejected.

**Per-op undo rather than per-turn.** Pro: finer granularity. Con: the
user mental model is the turn ("undo what you just did"); asking op by op
confuses. Rejected.

**Auto-rollback at boot for orphan `pending` records.** Pro: automatic
cleanup. Con: dangerous — if Metnos crashes during an op that wasn't
revertible-by-design (e.g. a mail_send mid-flight), an auto-rollback
could harm. Decision: surface it only, let the user decide. Auto-rollback
rejected, surfacing kept.

## Consequences

`Executor` now has a `revertible: bool` field (default false), and
critical-reversible executors must expose `reverse(plan, results) -> dict`.
As of 28/4/2026 two do: `move_files` and `create_dirs`. A new executor
`remove_dirs` was added (useful both as create_dirs reverse and as
standalone capability, with safe default: removes only empty dirs,
`force=true` for rmtree) and `undo_last_turn` (8/8 + 3/3 birth tests
green, E2E integration test confirmed).

The drift between hand-written and synt-generated executors now includes
the `revertible` flag and the reverse function. This is explicit debt
(see ADR 0002): until the synt can write reverse correctly (a hard
problem), synt-generated executors are `revertible=false` by default.

Pending for the next iteration: `fs_write` revertible with blob backup,
boot recovery (surface orphan `pending` at runtime startup), reverse
pattern catalog (see ADR 0002).
