---
id: 0039
title: Robust executors at the NL/deterministic boundary
date: 2026-04-27
status: accepted
area: executor
related:
  - 0018
  - 0040
---

## Context

By the evening of 27 April 2026 several live runs had failed in
ways that traced back to the same structural cause: the LLM passes
arguments that are *almost* valid but not quite. Examples accumulated:

- `pattern="*.jpg,*.jpeg"` as a single comma-joined string instead
  of a list of two patterns;
- `tail_bytes=0` as a placeholder meaning "not specified" instead
  of as the literal value zero;
- `*.JPG` on a case-sensitive filesystem when the user meant any
  case;
- a path with `~` in front, expecting the executor to expand the
  home directory;
- `case_sensitive: "yes"` (a string) instead of the boolean `true`.

Each case has a tempting narrow patch: add a regex, a
case-insensitive default, an `if name == ...`. Roberto's framing
was that the right place for the fix is *the executor*, not the
prompt that invokes it: *"definitive solutions and not patches"*.
Each new model will produce a new way of being slightly wrong; if
every wrongness is patched at the prompt level, the maintenance
burden grows model by model. If the executor handles the variants,
the prompt stays clean and the system stays robust.

The principle is the structural form of ADR 0018 (general fixes
over patches): executors are the boundary where the natural-language
imperfections of the LLM meet a deterministic system. The boundary
is responsible for the translation, not the LLM.

## Decision

Six robustness patterns every new executor must cover; existing
executors are adapted to match.

**1. Zero / false / "" as placeholder of "not specified".** Treat
them as `None`, not as real values. (Example: `tail_bytes=0`,
`max_bytes=0`.)

**2. Compound patterns in a single string.** Split on comma, pipe,
simple brace-expansion. `"*.jpg,*.jpeg"` → `["*.jpg", "*.jpeg"]`.
Reuse a shared helper, do not reimplement per executor.

**3. Case-insensitive default for filesystem searches.** Linux is
case-sensitive but in natural language `.JPG` and `.jpg` are "the
same thing". The manifest may offer `case_sensitive: bool` as an
override.

**4. Normalized paths.** Expand `~`, handle trailing slashes,
accept relatives and absolutes, normalize redundant separators.

**5. Plural args by principle.** An executor that frequently
accepts a plural concept should accept both singular and list forms
(`pattern` and `patterns`, `path` and `paths`). The planner learns
to use the plural from the manifest.

**6. Auto-correcting errors.** If the passed arg is clearly *close*
to a valid value, normalize it instead of failing. `"yes"` /
`"true"` for booleans declared as boolean. Numeric strings for
declared integers. Etc.

**Where the helpers live.** A single `runtime/executor_helpers.py`
module exposes the common normalizers — `parse_compound_pattern(s)
-> list[str]`, `coalesce_zero_to_none(v)`, `normalize_path(p)`,
`coerce_bool(v)`, etc. Executors import them. No private
reimplementations per executor.

**Discipline.** When writing a new executor, check the six points
before signing the manifest. When a live run exposes a new pattern
of LLM weirdness, the fix lives in the executor (or the helpers
module), not in the system prompt.

The pairing with ADR 0040 (manifest readability for medium-tier
LLMs) is direct: a manifest that documents the supported variants
of an arg trains the LLM to use them, which closes the loop. The
executor is permissive; the manifest tells the LLM what is
permissible.

## Alternatives considered

**Strict args, fix in prompt.** Pro: minimal executor code. Con:
each new model brings new ways to be wrong; the prompt grows
unboundedly; the system depends on perfect LLM compliance.
Rejected.

**Per-executor variant handling.** Pro: each executor knows its own
domain. Con: the same patterns (compound strings, zero-as-placeholder)
recur across executors; per-executor handling produces inconsistent
behavior and code duplication. Rejected.

**Fail loudly on every imperfection.** Pro: surfaces problems
immediately. Con: the user sees system errors for what are obvious
LLM near-misses; the experience is "Metnos refuses what should
work"; the planner could retry but burns budget. Rejected.

## Consequences

The seed pool gains the six patterns universally. New executors
implement them; existing ones retrofit (the diary in ADR 0030
tracks which retrofits are pending).

`runtime/executor_helpers.py` is the new central normalizer module.
It is where future patterns land too: when a seventh structural
imperfection is identified, a new helper joins the module rather
than spreading across executors.

The interaction with the daily reality-check discipline is
constructive. Each new live failure is *evidence* of a missing
robustness pattern; the fix updates the helpers module; the next
live run benefits. The cycle is the system's own immune response
to LLM idiosyncrasies.

A concrete pattern visible from the executor diary (ADR 0030):
several executors that take patterns now accept both singular and
plural forms. The planner has begun spontaneously using the plural
forms (per the manifest's documentation, ADR 0040), which means
fewer compound-string parses are needed at runtime — the manifest
shapes the input distribution upstream.
