---
id: 0045
title: Executor naming convention — verb_object[_qualifier] with closed vocabulary
date: 2026-04-28
status: accepted
area: naming
related:
  - 0014
  - 0041
---

## Context

By 28 April 2026 the seed pool plus reality-check additions had
grown to ~32 executors with naming patterns inherited from
historical conventions. Some used Linux/CS prefixes (`fs_read`,
`web_fetch`), some action-first (`get_now`, `move_file`), some
were synthesised from the synt with their own choices
(`format_json`). The inconsistency was small enough to be
tolerable in the POC but big enough to cause two problems looking
forward.

First, the *composer* (LLM at the moment of tool selection) does
not benefit from a uniform structure when each name is shaped
differently. The composer's job is description-driven; structural
discipline buys little there. Second, the *synt* (LLM at the
moment of generating a new executor) benefits enormously from a
closed vocabulary, because it prevents the proliferation of
synonymous names (`get_files` vs `read_files` vs `load_files`)
that fragments the catalog over time.

The decision was to give the synt a vocabulary, not to over-
constrain the composer. Roberto's framing on 28 April: structure
serves the composer (description does the heavy lifting),
vocabulary serves the synt.

## Decision

Schema `<action>_<object>[_<qualifier>]`, where action and object
come from closed lists.

**Closed vocabulary — actions (16, by semantic category):**
- I/O fs (4): `read`, `write`, `move`, `delete`
- Discovery (2): `find`, `list`
- In-memory list transformations (3): `filter`, `sort`, `group`
- Metadata (2): `get`, `set`
- Network (2): `fetch`, `send`
- Statistics (1): `describe` (mean / median / std / min / max / n
  + percentiles on a numeric list; use `fields[]` to limit the
  computation)
- Formatted output (1): `render` (produces report/view in a target
  format; `render_files_pdf`, `render_messages_html`)
- Decomposition (1): `extract` (decomposes a container into
  components on disk; zip→files, pdf→pages)

**Not a verb: `count`.** Discovery actions (`find` / `list` /
`filter`) return `count` as a result field. "How many X" on files
goes through `get_files_metadata(fields=["lines_count",
"words_count", "byte_size"])`. Statistics on numbers go through
`describe_numbers(fields=["n", ...])`.

**Closed vocabulary — objects (11, always plural):**
`files`, `dirs`, `packages`, `messages`, `events`, `contacts`,
`places`, `processes`, `urls`, `lines`, `numbers`.

**Optional qualifier**: free-form, but only for *real format /
parser variants* (`_zip`, `_pdf`, `_xml`, `_html`, `_text`).
NEVER for runtime options — those are args.

**Strict rules:**
1. Verb first, infinitive imperative. NO `file_read`, YES
   `read_files`.
2. Always plural (consistent with vectorial-by-default of ADR
   0041).
3. No abbreviations: `packages` not `pkg`, `messages` not `msg`.
4. No domain prefix: NO `fs_read`, YES `read_files`; NO
   `web_fetch`, YES `fetch_urls`.
5. Qualifier = real variant, not runtime option.

**Rare exceptions** (singular for intrinsically unique
operations): `get_now` (time is one).

**Synt rule.** The synt picks `action ∈ vocabolario_azioni`,
`object ∈ vocabolario_oggetti`. If no combination fits the use
case, *escalation to Roberto* — the synt does not invent new
verbs or objects.

**Refactor of existing seeds** (9 implemented at 27 April), to
be done in a dedicated session:
- `fs_read` → `read_files`
- `fs_write` → `write_files`
- `move_file` → `move_files` (already done with vectorial
  refactor of ADR 0041)
- `create_dir` → `create_dirs` (already done)
- `find_file` → `find_files`
- `list_dir` → `list_dirs`
- `time_read` → `get_now` (singular exception)
- `web_fetch` → `fetch_urls`
- `pkg_search` / `pkg_install` / `pkg_uninstall` /
  `pkg_list_installed` → `search_packages` / `install_packages` /
  `remove_packages` / `list_installed_packages`
- `geo_poi_search` → `search_places`
- `get_file_dates` → deprecated, replaced by `get_files_metadata`
  (one executor with `fields[]`, see ADR 0005)

Backward compatibility is not preserved (ADR 0031).

## Alternatives considered

**Free naming (description-driven only).** Pro: maximum freedom
for the synt. Con: catalog fragmentation; near-duplicates with
different verbs (`fetch_urls` and `download_urls` and
`get_urls`); the introvertive cascade (ADR 0010) cannot
recognize them as candidates for `dedupe`. Rejected.

**Strict `verb_object` with no qualifier.** Pro: even simpler.
Con: format variants are real (`extract_files_zip` differs from
`extract_files_pdf` in code path, capability, error class);
forcing them into the same name dilutes the synt's similarity
penalty. Rejected.

**Action vocabulary with arbitrary objects.** Pro: easier for
the synt to find a match. Con: the *object* drift is what fragments
the catalog (does mail go through `messages` or `mails` or
`emails`?). Closing the object list is what stabilizes
similarity scoring. Rejected.

**Open vocabulary with periodic curation.** Pro: discovers new
needs organically. Con: every new verb is debt until reviewed;
the synt produces inconsistent code in the meantime. Rejected.

## Consequences

The synt's prompt for code generation gains the closed
vocabulary as a *constraint*, not a suggestion. When the synt
proposes a name outside the vocabulary, the spec stage flags it,
the proposal is rejected, and the situation escalates to Roberto.
The escalation is the discovery channel for missing verbs:
when "I needed a verb that does not exist" recurs, that is data
on a real gap.

A side effect: name length grows slightly. `fs_read` becomes
`read_files`, four characters longer. The cost is one line in
prompts; the benefit is a consistent catalog the synt can navigate
without inventing.

The interaction with ADR 0014 (granularity) is direct: the closed
vocabulary *operationalizes* the verb-noun heuristic. With 16
actions × 11 objects = 176 nominal slots; the catalog occupies a
fraction of these, with each occupied slot being a real
capability. Counting empty slots becomes a way to see what the
system *cannot* do yet — a structural form of proto-mnest
detection (ADR 0009).

The pairing with ADR 0041 (vectorial by default) shapes
plurality: every object name is plural because every executor
takes a list. The two decisions reinforce each other: the
naming says "files", the executor accepts a list of files, the
manifest documents the list as canonical, the planner emits
lists as the natural form.
