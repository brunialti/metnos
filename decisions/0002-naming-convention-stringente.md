---
id: 0002
title: Strict executor naming convention with closed vocabulary
date: 2026-04-28
status: accepted
area: naming
related:
  - 0001
  - 0003
---

## Context

Until the morning of April 28, 2026, the executor naming convention was a
loose guideline: "verb followed by plural object" (memory
`feedback_executor_naming_convention`, initial version). With 13 executors
in the pool — part inherited from earlier phases (`fs_read`, `fs_write`,
`web_fetch`, `time_read`, `pkg_*`), part added under the recent guideline
(`move_files`, `create_dirs`, `find_file`, `list_dir`, `filter_entries`,
`get_file_dates`) — the pool was mixed: domain prefixes instead of verbs in
front, some singulars, inconsistent abbreviations. The problem would have
exploded with the synt: it would need to propose new names, and without a
formal convention every run would generate different names.

Roberto framed the question: how do we balance naming discipline with the
synt's creative freedom? Constrain the vocabulary too tightly and the synt
can't cover an emergent use case. Don't constrain it and the pool fills
with synonyms (`search_files` vs `find_files`, `delete_files` vs
`remove_files`).

## Decision

Two layers:

**Fixed structure** `{action}_{plural_object}[_{qualifier}]`. This is enough
for the composer: the planner LLM at choice time has the name + description,
and the name is already self-explanatory (verb in front, object after). No
extra prompt to the composer is needed for the convention.

**Closed vocabulary** for the synt. 16 actions in 8 semantic categories
(`read`, `write`, `move`, `delete` for fs I/O; `find`, `list` for
discovery; `filter`, `sort`, `group` for list transformation; `get`, `set`
for metadata; `fetch`, `send` for network; `describe` for statistics;
`render` for formatted output; `extract` for container decomposition).
11 objects all plural (`files`, `dirs`, `packages`, `messages`, `events`,
`contacts`, `places`, `processes`, `urls`, `lines`, `numbers`).
The qualifier is free BUT only for real format/parsing variants (`_zip`,
`_pdf`, `_xml`, `_html`, `_text`); never for runtime options (those are
args).

**Synt rule:** picks action and object from the vocabulary; if no
combination fits, **escalation to Roberto**, no inventing new verbs or
objects. No synonyms: `search_packages` not `find_packages`,
`remove_packages` not `delete_packages`. The exclusivity rule rests on
action choice: each verb has a single canonical meaning.

`count` was deliberately excluded (same day, after discussion with Roberto):
discovery (`find/list/filter`) already returns `count` as a result field;
file counts (n_lines, n_words) are asked via
`get_files_metadata(fields=["lines_count","words_count","byte_size"])`;
number statistics via `describe_numbers(fields=["n", ...])`. `count` as a
verb was pure redundancy.

`extract` was initially miscategorized (under "computation/output");
Roberto spotted the inconsistency and triggered the creation of a proper
"decomposition" category — an example that the vocabulary is not just a
list but also a categorical map that helps the synt pick.

## Alternatives considered

**Free naming**, canonical description in the manifest. Pro: no constraint.
Con: pool fills with synonyms, composer struggles to discriminate.
Rejected.

**Open vocabulary**, list of examples and guidelines. Pro: infinite
coverage. Con: every synt run produces different names for similar cases,
compositional search is hard. Rejected.

**Very small vocabulary** (5-6 verbs). Pro: minimalism. Con: forces
contrived qualifiers (`get_files_special_for_X`); the synt can't find a
combination and escalates too often. Roberto set the constraint "not too
big" — the equilibrium turned out to be 16, in 8 categories.

**Per-domain vocabulary** (filesystem-specific verbs, network-specific
verbs). Pro: granularity. Con: the LLM picks domain before action, but
many executors are multi-domain (e.g., `filter_entries` operates on fs
entries but also on email entries). Rejected.

## Consequences

The synt prompt receives the vocabulary as ~25 tokens; no significant
overhead. The existing seed pool enters a **refactor debt**: `fs_read` →
`read_files`, `fs_write` → `write_files`, `web_fetch` → `fetch_urls`,
`time_read` → `get_now` (singular exception allowed for inherently unique
ops), `pkg_*` → `*_packages`, `geo_poi_search` → `search_places`,
`find_file` → `find_files`, `list_dir` → `list_dirs`. The refactor is
scheduled as a dedicated session (memory
`feedback_executor_naming_convention`); no backward-compat shim
(`feedback_no_backward_compat_in_dev`).

`get_file_dates` was deprecated the same day (see ADR 0003) in favor of
`get_files_metadata`, executing de facto the first step of the refactor.

The synt must be updated to **know the vocabulary** and to escalate when
no combination fits. Pending work.
