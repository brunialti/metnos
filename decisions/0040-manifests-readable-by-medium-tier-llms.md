---
id: 0040
title: Manifests written for medium-tier LLM readability
date: 2026-04-27
status: accepted
area: executor
related:
  - 0025
  - 0039
---

## Context

The manifest of an executor is not just developer documentation —
it is the *prompt-to-LLM* of that tool. The planner uses the
manifest's `description` and the `args.properties.*.description`
fields to decide whether to invoke the tool and with which
arguments. If a medium-tier LLM (the project's `fast` tier,
`qwen3:8b` per ADR 0044) does not understand the manifest, the
executor is invoked badly even if the underlying code is correct.

By 27 April 2026 the live reality check had exposed concrete
failures of that mode. `qwen3:8b` was passing wrong patterns
(`*.jpg,*.jpeg` as a single string), wrong case-sensitivity (`.JPG`
vs `.jpg`), and ignoring plural-args support. Each of these traced
to a manifest description that assumed a wiser reader than the
one actually doing the reading.

The decision was needed to align the manifest authoring discipline
with the LLM the planner actually uses. Writing for a wise-tier
reader produces brittleness; writing for a medium-tier reader
produces robustness.

## Decision

Manifests pass an "LLM-medium readable" review before the
signature.

**`description` of the tool.** One sentence stating the principal
responsibility ("lists files in a directory", not "enumerator of
filesystem entries"). One sentence for limits and exceptions.
Operative tone, plain language. The planner reads this sentence
when deciding whether to call the tool; if it does not register
in five seconds of LLM attention, the description is too dense.

**`args.properties.X.description`.** What the arg represents in
ordinary language. Examples in quotes when the format is not
obvious: `"Es. '*.jpg', 'README*'"`. When an arg accepts
variants (string or list, string-with-commas), enumerate the
supported variants. Defaults stated in plain text in the
description, not only as `default = ...` (the LLM reads the
description, not the schema metadata).

**`enum` values use speaking names** (`'image'`, `'video'`), not
cryptic codes.

**No Python jargon.** "Standard library only" instead of "stdlib
pure". "List of fields with type and description" instead of "JSON
Schema subset". The reader is not a Python developer.

**Tool description as operational overview.** Say what the tool
*does*, what it *does not do*, and what should be used after it
("does not filter; for filtering use `filter_entries`").

The discipline pairs with ADR 0039 (robust executors) — the manifest
documents the variants the executor accepts. Together they form a
contract: the manifest tells the LLM the wide form of accepted
inputs; the executor implements the wide form. The medium-tier LLM
sees the wide form and uses it.

## Alternatives considered

**Write manifests for a wise-tier reader.** Pro: uses denser,
more elegant prose. Con: the planner is the medium-tier LLM, not
a wise-tier reader; the prose fails to register; the executor
gets called wrong. Rejected.

**Two manifest registers, one for the planner and one for humans.**
Pro: each audience optimal. Con: keeping two in sync is the work
of which the project has none to spare; humans can read the
medium-LLM register fine. Rejected.

**Explanation in code comments, terse manifest.** Pro: cleaner
manifest. Con: the planner does not read code comments; the
manifest is the prompt; the comments are invisible to the
decision-maker. Rejected.

## Consequences

Manifest authoring (delegated to Claude per ADR 0020) follows the
medium-LLM readability rule. Roberto's review explicitly checks for
plain language, examples in quotes, plural variants documented,
defaults in prose.

Concrete change in the seed pool: `pattern` args document
"single pattern, comma-separated multiple patterns, or a list" in
prose, not just "string | list[string]" in the schema. The
planner picks up the variants from the description, which means it
*emits* the right form more often, which reduces the load on the
robust-executor helpers (ADR 0039).

The interaction with ADR 0042 (native tool-use) is direct: the
manifest's args schema becomes the `parameters` of the tool passed
to the LLM. The schema's descriptions ARE the prompts the LLM sees.
A clear manifest description is a clear tool-call prompt.

A specific quality bar — when in doubt: if a human takes more than
30 seconds to understand an arg description, the medium-tier LLM
will fail. Rewrite.
