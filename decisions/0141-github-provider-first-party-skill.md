---
id: 0141
title: GitHub provider as a first-party skill (issues, pulls, workflows, reviews)
date: 2026-05-17
status: accepted
area: skill | executor | naming | runtime
related:
  - 0123  # skill importer agentskills.io
  - 0130  # backend tree aligned to OBJECTS
  - 0131  # single credentials store
  - 0132  # plugin loader external
  - 0134  # semantic affinity fallback BGE-M3
  - 0136  # skill dormancy + provider qualifier
  - 0140  # sandbox audit + scaling skill
  - 0142  # consult_frontier system verb (mode B consumer)
complements:
  - 0123
  - 0136
---

## Context

By 17/5/2026 Roberto's workflow on Metnos asks for a first-class GitHub
integration with four recurring needs:

1. **Issue/PR ingestion**: watch repos he cares about, ingest new
   issues/PR every ~30 minutes, expose them to the planner as
   first-class entities (search/read/filter, not opaque webhooks).
2. **Deduplication**: before opening or answering an issue, detect
   pre-existing duplicates with both deterministic signals (title
   normalization, repo + author + 24h window) and semantic similarity
   (BGE-M3 cosine on title + body, ADR 0134) so Roberto does not
   re-discuss the same bug twice.
3. **Auto-reply on triage-ready issues**: drafts answers grounded in
   the actual repo (file contents, dir listings, code search), with a
   frontier model exploring the codebase on demand, then a human-gated
   approval card before the comment is posted.
4. **Analysis**: cross-issue summaries, review delegation, monthly
   activity reports.

ADR 0123 already provides a skill importer pipeline for
`agentskills.io` packages. We surveyed the public catalog: there is no
canonical GitHub skill that covers Roberto's full scope (most existing
ones wrap `gh` CLI partially, lack workflow support, do not separate
issues from pull requests semantically, and have no review-event
modeling). Importing one would mean importing a thin wrapper and then
extending it manually — losing the discipline of the §2.2 vocabulary
and forcing ad-hoc fix-ups every release of the upstream skill.

At the same time, building GitHub as a *builtin* tree of handcrafted
executors would duplicate infrastructure that the skill engine already
owns: signed manifests, dormancy detection (ADR 0136), credentials
metadata-only contract, sandbox audit (ADR 0140), provider-qualifier
filtering of the GBNF tool pool (ADR 0136), and the generic
`skill_wrapper` helper (ADR 0123).

The decision is therefore not "skill vs builtin"; it is "how to package
a first-party skill that we own end-to-end while reusing the skill
engine as the substrate".

## Decision

GitHub is shipped as a **first-party skill** living at
`~/.local/share/metnos/skills/github/` with the same on-disk shape as
imported skills (`SKILL.md` + `scripts/` + `references/`) but with a
distinguishing `[provenance]` block stating
`imported_from = "metnos:builtin/github"`. The `metnos:builtin/...`
prefix is the marker that this skill ships with the product and is
maintained in-tree, not pulled from `agentskills.io`.

### (A) Skill payload

- `~/.local/share/metnos/skills/github/SKILL.md` — agentskills.io
  frontmatter + Markdown instructional body (English).
- `~/.local/share/metnos/skills/github/scripts/github_api.py` — single
  Python entry point with subcommands, called by every executor wrapper.
  Subcommands return JSON on stdout, errors on stderr with
  `error_class` taxonomy (`missing_credentials`, `not_found`,
  `rate_limited`, `forbidden`, `server_error`, `network`, `validation`).
- `~/.local/share/metnos/skills/github/references/` — short Markdown
  references for the frontier LLM when in `consult_frontier` mode B
  (ADR 0142): API conventions, repo conventions, naming policies.

### (B) Pipeline reuse from ADR 0123

The first-party skill is admitted through the same 5-stage importer
pipeline as third-party skills:

1. Parser (`SKILL.md` frontmatter + scripts inventory).
2. Translator (action+domain → §2.2 verb_object via
   `runtime/skill_vocab_map.json::contextual`).
3. Codegen (Jinja templates emit a manifest + a thin wrapper per
   executor, all calling `runtime/skill_wrapper_github.py`).
4. LLM stage 4 description generation (English + Italian per ADR 0092).
5. Admission (4-layer policy of ADR 0114).

Distinguishing traits of the first-party skill:

- Provenance block (`imported_from = "metnos:builtin/github"`) is
  inserted by the codegen template, not by the importer CLI.
- Upgrade path: `metnos-skills upgrade github` is a no-op; updates ship
  with `git pull` of `/opt/myclaw` and re-run codegen.
- Manual maintenance is explicit and acceptable: this is a flagship
  integration, not a community contribution.

### (C) Phase 1 executors (13 total)

All bear the `_github` provider qualifier (4th family qualifier, ADR
0136). The provider marker `_PROVIDER_SUFFIX_MARKERS["_github"]`
(populated in `runtime/tool_grammar.py`) filters these out of the GBNF
pool unless the query contains a GitHub marker (`github`, `repo`,
`issue`, `pull request`, `PR`, `workflow`, `actions`, `commit`).

| Executor | Verb | Object | Notes |
|---|---|---|---|
| `find_issues_github` | find | issues | search with filters: repo, state, author, labels, since |
| `read_issues_github` | read | issues | id-based, returns body + comments + reactions |
| `create_issues_github` | create | issues | title + body, optional labels/assignees |
| `set_issues_github` | set | issues | upsert labels/state/assignees/milestone |
| `delete_issues_github` | delete | issues | maps to "close" (GitHub has no hard delete) |
| `find_pulls_github` | find | pulls | search across repos with filters |
| `read_pulls_github` | read | pulls | id-based, returns body + diff summary + reviews |
| `create_pulls_github` | create | pulls | head/base/title/body |
| `set_pulls_github` | set | pulls | upsert labels/state/assignees/draft |
| `change_pulls_github` | change | pulls | merge (squash/rebase/merge), close, reopen, request_review |
| `delete_pulls_github` | delete | pulls | maps to "close" |
| `send_messages_github` | send | messages | post issue/PR comment, optional `review_event` arg |
| `delete_messages_github` | delete | messages | delete comment (author-only) |
| `create_tasks_github` | create | tasks | trigger workflow_dispatch run |
| `read_tasks_github` | read | tasks | poll run status/logs |

(15 lines, 13 manifests — `read_tasks_github` and `create_tasks_github`
are the workflow pair; the table also lists their pair member for
clarity. The actual executor count is 13, with workflows treated as the
multi-backend `tasks` object per ADR 0130/0136 rather than a
specialized ad-hoc object.)

### (D) Vocabulary additions

Two new §2.2 OBJECTS are introduced:

- **`issues`** — a remote, mutable, threaded discussion entity with
  state (open/closed), labels, assignees, and reactions.
- **`pulls`** — distinct from `issues` because of three properties that
  break the §2.6 schema-stability invariant when collapsed: a diff is
  always present, a `merge` action exists, and structured reviews
  (approve/request_changes/comment) are first-class.

OBJECT count moves from 19 (ADR 0137) to **21**. Watchpoint:
`runtime/vocab.py` size now approaches the bench-validated scaling
limit (ADR 0140 watchpoint at 2x verbs / 3x objects+modifier). The
addition of two cohesive, well-discriminated objects does not consume
the headroom budget meaningfully — the watchpoint comment in
`runtime/vocab.py` is updated to reflect 21/57 instead of 19/57.

### (E) Reviews modeled as a `messages` sub-form

PR reviews are *not* a new OBJECT. They are emitted via
`send_messages_github` with an optional `review_event` argument
(`COMMENT | APPROVE | REQUEST_CHANGES | PENDING`). Rationale: a review
without an event is structurally indistinguishable from a comment, and
review events are a small enum (4 values) — promoting them to an
OBJECT would violate §7.2 (simplest sufficient form). The manifest
description spells out the boundary in prescriptive form (§6).

### (F) Workflows as `tasks` with provider qualifier

GitHub Actions runs are remote tasks: they have a name, an id, a
status, a start time, and a log. They map cleanly onto the existing
multi-backend `tasks` object (ADR 0137) just like scheduler v2 local
tasks. Two executors: `create_tasks_github` (workflow_dispatch) and
`read_tasks_github` (poll status). The pattern is identical to local
scheduler tasks; only the backend differs. This validates the §2.2
provider-qualifier discipline as a substitute for backend-specific
verbs.

### (G) Three extra subcommands for `consult_frontier` mode B

The skill's `scripts/github_api.py` exports three subcommands that are
**not** wired as executors visible to the planner. They exist only as
tools exposed to a frontier LLM during a `consult_frontier`
(ADR 0142) mode-B session for codebase exploration:

- `repos_read_file(owner, repo, path, ref)` — single file read with
  size cap (default 64 KB).
- `repos_list_dir(owner, repo, path, ref)` — directory listing with
  pagination.
- `code_search(owner, repo, query, language)` — GitHub code search API.

They are read-only by design (no `path_in {issues/pulls/comments}`).
Sandbox audit (ADR 0140) records every call.

### (H) Wiring map

- Skill scaffold: `~/.local/share/metnos/skills/github/`
- Skill-specific helper: `runtime/skill_wrapper_github.py` (extends
  the generic `runtime/skill_wrapper.py` of ADR 0123 with GitHub
  pagination, ETag caching, rate-limit retry, and PAT scope-aware
  error mapping)
- Credentials check function:
  `runtime/skill_credentials.py::_check_github_pat` (validates PAT
  presence, token format, expiry from the `last-validated-at` metadata,
  required scopes `repo`, `workflow`, `read:org`)
- Provider markers:
  `runtime/tool_grammar.py::_PROVIDER_SUFFIX_MARKERS["_github"]`
- Watcher: ~~`runtime/jobs/github_watcher.py`~~ **RITIRATO (ADR 0186)**: la manutenzione issue è una query NL schedulata (`run_user_query`), non un job bespoke
  (`every_30m`)
- QA / dedup store:
  `runtime/github_issue_qa_store.py` (SQLite + BGE-M3 1024-d embedding,
  same fast-path/fallback shape as ADR 0134)
- Watch state: `runtime/github_watch_state.py`
- Dedup job: `runtime/jobs/github_dedup.py` (4-AND safety: same repo
  + same author + opened within 24h + cosine ≥ 0.78; deterministic
  classifier first, semantic only above ambiguity threshold)

## Alternatives considered

**(a) Import an existing GitHub skill from `agentskills.io`**.
Rejected. The catalog at 17/5/2026 lists 4 GitHub skills; the most
complete one (`gh-companion`) wraps `gh` CLI without structured tool
output, has no PR review or workflow support, and packages credentials
in plaintext under `~/.gh/`. Importing it would mean either accepting
the gap (drops 60% of Roberto's use cases) or forking it
post-import (loses the upgrade story that justifies the importer in
the first place). The work to write the skill from scratch is roughly
equal to the work to extend the imported one, with the benefit of
owning the manifest grammar cleanly.

**(b) `gh` CLI wrapper exposed through `admin`**. Rejected.
This forces every operation through the HMAC consent flow (ADR 0088),
which is correct for shell-level privilege escalation but wrong for
routine read/write that the planner should chain freely (e.g. *find
all open PRs in repo X with the bug label, then summarize each*).
Output is also unstructured: `gh issue list --json ...` exists but is
not consistent across subcommands, and the planner would still need a
parser layer that the skill engine already provides.

**(c) Builtin executors under `/opt/myclaw/executors/` (no skill
shell)**. Rejected. This duplicates the skill engine's substrate —
dormancy detection, credentials metadata contract, sandbox audit,
provider-qualifier grammar filter — which we built explicitly for
this kind of integration. The marginal cost of the `SKILL.md` + scripts
folder structure is ~20 lines of YAML; the gain is everything in
ADR 0123 / 0136 / 0140 for free.

**(d) Mix: builtin for read paths, skill for writes**. Rejected. Split
ownership across two substrates for the same provider is a maintenance
trap. One provider = one substrate.

## Consequences

- **Skill engine validated as multi-purpose substrate**. The same
  pipeline runs imported skills (Google Workspace) and first-party
  skills (GitHub). The provenance block is the only branching point.
- **Provider-qualifier discipline extended**. `_github` joins
  `_google_workspace` and `_metnos`; the GBNF pool filter
  (`_PROVIDER_SUFFIX_MARKERS`) keeps generic queries free of GitHub
  noise. Coherent with the §2.2 4-family qualifier model (ADR 0136).
- **Vocabulary discipline preserved**. `issues` and `pulls` are
  introduced with explicit semantic discrimination, not as a
  convenience. The 19→21 OBJECTS bump is the first since ADR 0137 and
  stays well below the scaling watchpoint of ADR 0140.
- **First consumer of `consult_frontier` mode B**. The dedup +
  auto-reply pipeline relies on the frontier LLM browsing the repo
  through `repos_read_file` / `repos_list_dir` / `code_search`. ADR
  0142 is the substrate; ADR 0141 is the first non-trivial application.
- **Cost**. Manual maintenance of 13 manifests + wrappers + helper +
  watcher + dedup job. Estimated ~600 LOC product-side, refreshed on
  GitHub API changes (rare; v3 REST is stable). Acceptable for a
  flagship integration.
- **Door closed for now**. We do not chase parity with `gh` CLI for
  exotic operations (security advisories, packages, releases, deploy
  keys). They are intentionally out of Phase 1.
- **Door opened**. Adding a second third-party-style first-party skill
  (e.g. Linear, Notion) becomes a known operation: copy the scaffold,
  fill the action map, register a `_check_<provider>_credentials`,
  add a `_PROVIDER_SUFFIX_MARKERS` entry.

## Implementation references

- Architecture doc:
  `docs/it/internal/github_provider_architecture.html`
- Skill scaffold: `~/.local/share/metnos/skills/github/`
  - `SKILL.md`
  - `scripts/github_api.py`
- Helper specific to GitHub:
  `runtime/skill_wrapper_github.py`
- Generic helper inherited from ADR 0123:
  `runtime/skill_wrapper.py`
- Credentials check:
  `runtime/skill_credentials.py::_check_github_pat`
- Provider markers:
  `runtime/tool_grammar.py::_PROVIDER_SUFFIX_MARKERS["_github"]`
- Watcher (scheduler v2 `every_30m`):
  `runtime/jobs/github_watcher.py`
- Dedup job:
  `runtime/jobs/github_dedup.py`
- Issue QA store (SQLite + BGE-M3):
  `runtime/github_issue_qa_store.py`
- Watch state persistence:
  `runtime/github_watch_state.py`
- Vocab additions:
  `runtime/vocab.py` (`issues`, `pulls`, `_OBJECT_TO_SECTIONS`,
  `_OBJECT_PRIMARY_TOOLS`, `OBJECT_DEFAULT_MUTATING_VERB`,
  synonyms IT+EN)

## Update 2026-06-25 — §(G) superseded: repo-file subcommands promoted to planner executors

Section **(G)** declared `repos_read_file` / `repos_list_dir` / `code_search`
as *backend-only* tools, exposed exclusively to the frontier LLM during a
`consult_frontier` mode-B session and **"not wired as executors visible to the
planner"**. That boundary was codified in `vocab.QUALIFIER_OBJECT_COMPAT` as
`"github": {issues, pulls, messages, tasks}` (comment "NON files/dirs").

Origin turn `6ec02267` — *"quanti file ci sono su github nel repo
brunialti/metnos"* — failed honestly (§2.8): the planner had no tool to count
repo files and fell back to local `find_files`. Roberto's decision (25/6) is to
make repo files reachable by the **planner/user directly**, not only by the
frontier auto-reply flow. §(G) is therefore superseded for the three
file-oriented subcommands:

- **Vocabulary**: `QUALIFIER_OBJECT_COMPAT["github"]` widened to include
  `files` and `dirs` (a repo *is* files and folders). The `_github` qualifier
  is now §2.2-valid on those objects.
- **New executors** (user-data, signed, `~/.local/share/metnos/executors/skills/github/`):
  `find_files_github` (count/search across the whole recursive tree),
  `read_files_github` (file content by path, vectorial over `paths`),
  `list_dirs_github` (one-level folder listing, vectorial, default root).
- **New skill subcommand** `scripts/github_api.py::cmd_repos_tree` —
  `git/trees/{ref}?recursive=1` with per-subtree descent fallback when GitHub
  truncates the single recursive call (100k server cap → our 300k ceiling),
  `truncated` propagated honestly (§2.7/§2.11).

Unchanged: the subcommands still exist for the frontier mode-B path (addition,
not removal); routing stays gated by `_PROVIDER_SUFFIX_MARKERS["_github"]`
(verified: local file/dir queries keep routing to local `find_files`/`list_dirs`,
routing subset gate 29/29). `code_search` is NOT promoted (deferred — content
search, distinct from tree enumeration).
