---
id: 0003
title: Executor lifecycle (active / deprecated / archived) with procedural visibility
date: 2026-04-28
status: accepted
area: executor
related:
  - 0002
---

## Context

On April 28, 2026, after we implemented `get_files_metadata` as a
replacement for `get_file_dates`, we had to decide what to do with the
old one. Leave it in the pool? Delete it from disk? Mark it "deprecated"?

Roberto framed the problem structurally: deprecation has to be considered
from three different perspectives, and they don't coincide.

LLM composer side: if an executor is deprecated, **it must not be passed
to the model as an option**. Showing it is useless — worse, the model
would use it and produce ambiguous outcomes. Exclusion must be procedural,
not a textual hint to the model.

System side: sometimes it's still needed. For example, to revert
historical ops in the undo log — a `done` of `get_file_dates` recorded
yesterday requires that today the module still be loadable so we can
call its `reverse()`. Deleting from disk breaks history.

Synt side: to propose migrations or fusions, the synt must know which
executors exist as deprecated and which replaces them. But if deprecated
ones grow, passing full manifests bloats the context.

## Decision

Three lifecycle states in the manifest, default `active`:

- `active`: in the pool, visible to composer, visible to synt.
- `deprecated`: NOT visible to composer (loader filtering). Visible to
  synt only as **compact index** `[{name, superseded_by, version}]`, not
  full manifests. The file stays on disk and signed; the runtime can
  load it explicitly to call its `reverse()` for historical ops.
- `archived`: not visible to the synt either, by default. Only explicit
  access (e.g., reconstructing a historical environment).

Added to the manifest the optional `superseded_by = "<name>"` field for
deprecated entries: the synt reads it for automatic migration plans.

`loader.load_catalog()` remains the single source; on top of it
`filter_for_visibility(catalog, visibility)` with three profiles:
`VISIBILITY_COMPOSER` (default, only `active`), `VISIBILITY_SYNT` (same
as composer; the caller for migrations calls `deprecated_index(catalog)`
instead, a separate function), `VISIBILITY_ALL` (everything, for
historical undo and audit).

`agent_runtime.py:389` now invokes
`filter_for_visibility(load_catalog(), VISIBILITY_COMPOSER)`. The composer
never sees `get_file_dates`.

Contextual cleanup: 5 pre-seed stubs (`pkg_install`, `pkg_list_installed`,
`pkg_search`, `pkg_uninstall`, `geo_poi_search`) were dirs without code
or signature — the loader was already discarding them with "verify
failed", but they cluttered `ls executors/`. Hard removal: never were
active, nothing to preserve. 13 dirs remain: 12 active + 1 deprecated
(`get_file_dates`).

## Alternatives considered

**Hard removal of the deprecated**: delete dir and file. Pro: zero
clutter. Con: historical undo impossible for ops done by `get_file_dates`,
audit trail broken, no automatic synt migration. Rejected.

**Physical archive directory** (`executors/_archive/get_file_dates/`):
total isolation, `ls executors/` shows only active. Pro: visual cleanup.
Con: "move" mechanics to manage, requires loader update to discover the
archive dir. For 1 deprecated the cost/benefit ratio is negative.
Adopted as **transition threshold**: when deprecated count >5, we
re-evaluate the physical move.

**`enabled = false` flag** instead of lifecycle. Pro: simpler (boolean).
Con: doesn't distinguish "temporarily disabled" from "deprecated with
successor". The distinction is meaningful for the synt. Rejected.

**Visibility decided in the prompt** (text line to composer "do not use
get_file_dates"). Pro: zero code. Con: delegated to the model, fragile,
token waste, contradicts "procedural exclusion" principle. Rejected, and
explicitly forbidden by Roberto.

## Consequences

The manifest schema gets two new fields (`lifecycle`, `superseded_by`),
both optional with sensible defaults. Existing executors require no
changes unless one wants to deprecate them.

The loader grows by ~30 lines for `filter_for_visibility` and
`deprecated_index`. Negligible cost.

The lifecycle pattern is now a general tool: we'll use the same mechanism
for multiple versions of the same executor (`fs_write` v1 deprecated, v2
active) or for periodic pool cleanup. Opens the door to a "deprecation
date" and possibly auto-promotion to `archived` after N days — not
implemented today.

`get_files_metadata` is now the canonical replacement for `get_file_dates`.
The next live Telegram run won't see the old one among the options.
