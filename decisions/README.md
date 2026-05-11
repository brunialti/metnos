# Architectural Decision Records (ADR)

Persistent log of architectural and implementation decisions for Metnos. One
ADR per file, numbered sequentially (`0001-...`, `0002-...`). The point is
simple: someone returning to the project months later must be able to read an
ADR and understand **what we chose, why, and what we ruled out**.

These are not dev notes or a changelog. They are the **reasoning** behind the
long-term choices.

## When to write an ADR

- The data model or the contract between modules changes.
- The way an executor is written changes (signature, manifest schema, lifecycle).
- The way Metnos talks to users changes (errors, warnings, messages).
- A new paradigm is introduced (undo log, audit trail, multi-tenant, etc.).
- We pick between non-obvious alternatives and someone might re-open the choice later.

DO NOT write ADRs for: bug fixes, local refactors, dependency bumps, variable
renames.

## Format

See `_template.md`. Sections are **discursive**, not bullet lists: a future
reader must reconstruct the context without going back to the original
conversation. Cite numbers (test count at the time of the choice), files and
line numbers (`runtime/agent_runtime.py:670`), absolute dates. Never use
relative dates ("yesterday", "last week"); they decay.

## Status

- **proposed**: discussed, not yet applied to the code.
- **accepted**: applied; current state of the project depends on it.
- **superseded**: a later ADR revises it; reference the new ADR.
- **deprecated**: no longer applies, kept for history.

## Index

Keep `0000-INDEX.md` updated whenever you add an ADR.
