---
id: 0030
title: Executor diary as the long-term capability registry
date: 2026-04-26
status: accepted
area: process
related:
  - 0014
  - 0015
---

## Context

By 26 April 2026 the seed pool (ADR 0015) had 22 candidates and the
implementation was about to begin in earnest. Each candidate carried
a small biography — when it was first proposed, why, what use case
motivated it, what reservations had surfaced. Without a place to
record this, motivations would scatter across many memory files
(walk-through simulations, design dialogues, reality checks) and the
*pattern of demand* — which executors keep emerging, in which
contexts, with which qualifications — would remain invisible.

The decision was needed because two future moves depended on it.
First, the synt would propose new executors based on proto-mnest
clusters; before it did, somebody had to know whether the proposal
was new or a re-emergence of an old one. Second, periodic study of
the diary would identify the *clusters of motivation* that signal a
missing system function (e.g. "five executors needed because the
user wants to organize the inbox" → there is a meta-pattern of
inbox organization the system does not yet name).

## Decision

A living registry — `metnos_executor_diary.md` — holds one entry
per executor, hand-written, accumulating motivations rather than
overwriting them. The associated workflow is mandatory before any
new executor is proposed.

**Entry shape.**
```
## <name>

**Status:** desired | in_proof | implemented | abandoned
**Capability:** <e.g. mail:send>
**Target_kind:** exact | path_glob | host | none

**Description:** one line.

**Motivations:**
- [YYYY-MM-DD, context] reason this executor would be useful

**Encounter history:**
- [YYYY-MM-DD] where/how it was mentioned

**Implementation:** (empty until implemented)
```

**Workflow before proposing a new executor** (during POC, test
writing, design conversation, walk-through simulation):

1. **Consult the diary first.** Search by name and capability. If a
   match exists, jump to step 2; if not, step 4.
2. **Verify recorded motivations.** Compare the current use case to
   the entry's motivations.
3. **If the current motivations are not the same:** update the
   entry. Add to "Motivations" the new reason, dated, with context
   ("26/4/2026: emerged during POC test framework, e2e tests tried
   to send mail"). Do not replace earlier motivations; accumulate.
   Each motivation is data on real system needs.
4. **If the executor is not in the diary:** add a new entry with
   proposed name, status `desired`, proposed capability, target_kind,
   short description, initial motivation with date and discovery
   context.
5. **When implemented:** status → `in_proof` (during POC) or
   `implemented`. Add an Implementation section: date, phase /
   session, path in the project.
6. **When abandoned:** status → `abandoned` with rejection
   motivation and date.

**Periodic study.** Every 2–4 weeks of active development, re-read
the diary to:
- identify clusters of motivation that signal a missing system
  function;
- find executors `desired` for a long time that keep re-emerging,
  candidates for the next seed pool extension;
- find stale entries (motivations no longer current) — update or
  abandon.

**What does NOT go in the diary.**
- Internal runtime functions (loader, prefilter, vaglio: modules,
  not executors).
- Builtins without an invocable tool face.
- Minor variations of an existing executor (a new flag is not a new
  executor; if the change is in capability or target, it is).

The pattern interacts with ADR 0014 (granularity) and ADR 0015 (seed
pool). The granularity heuristics tell you whether a proposed
executor is right-sized; the diary tells you whether anyone has
already proposed it. Together they keep the catalog clean.

## Alternatives considered

**No diary; rely on memory and search.** Pro: no overhead. Con:
motivations scatter; clusters become invisible; the pattern of
demand cannot be studied; the same executor gets re-proposed under
different names. Rejected.

**Single-line table only** (no history per entry). Pro: maximum
density. Con: history is the data; without it the diary is just a
list of names, indistinguishable from the manifest catalog. Rejected.

**Auto-generated from code + manifests.** Pro: never out of sync.
Con: motivations live outside the code (they live in dialogues,
walk-throughs, reality checks); auto-generation can capture the
catalog state, not the demand history. Hybrid possible later;
authoring stays human-driven. Rejected as a sole approach.

**Inline motivations in the manifest.** Pro: co-located with the
spec. Con: motivations include rejected and `desired` items that
have no manifest yet; the manifest is also the prompt-to-LLM (ADR
0040), and adding motivation history would dilute it. Rejected.

## Consequences

The diary is the authoritative answer to "do we already want this?"
before any new executor proposal. The decision pre-empts a class of
quiet duplication that would otherwise compound as the catalog
grows.

The periodic study has already proven its worth: the
inbox-organization cluster (mail_read + parse_pdf + llm_extract +
calendar_create_event + mail_label + channel_out) is six entries
that share the same motivation root, signaling that "the system
helps me keep digital order" is a meta-pattern Metnos should
recognize as a coherent capability bundle.

Trade-off: maintaining the diary is manual discipline. The cost is
real but bounded — each new entry is a few minutes, each periodic
re-read is half an hour. The discipline pays off when the catalog
crosses ~30 entries (roughly the v1.1 milestone) and the question
"why do we have this executor?" gets asked.

The diary lives in the memory store
(`~/.claude/projects/-opt-myclaw/memory/metnos_executor_diary.md`),
not in the public corpus, because it carries decision history that
includes rejected ideas and motivations not all suitable for public
documentation. Public-facing executor descriptions are in the
manifests (ADR 0040).
