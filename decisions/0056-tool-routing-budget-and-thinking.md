---
id: 0056
title: Tool routing budget and reasoning budget — k_max=8 + think=True + 512
date: 2026-04-28
status: accepted
area: agent_runtime
related:
  - 0050
  - 0051
  - 0053
  - 0054
  - 0055
---

## Context

Live test of the QuickTour scene 1 ("riassumi le mail di oggi") on the
Telegram channel surfaced a hard pattern in Gemma 4 26B Q4_K_M used as
planner with native tool-use:

> When the planner is presented with a candidate set above a certain
> size, Gemma's reasoning text correctly identifies the right tool
> (e.g. "I should call `read_messages` after `get_now`"), but the
> emitted `tool_calls` blob lands on a generic "magnet" tool with
> placeholder args (typically `get_files_metadata` with
> `entries=[]`). The reasoning and the action diverge.

Two independent observations narrowed the cause:

1. **Curl probe with the full system prompt and 22 candidate tools**
   reproducibly produced the broken `get_files_metadata` tool_call (3/3
   trials, `enable_thinking` either on or off).
2. **Curl probe with the same system prompt and 5-8 candidate tools**
   (after we tightened the prefilter) produced the correct
   `read_messages` tool_call deterministically — across reasoning
   budgets 256, 512, 768, and 1024.

The difference was not the model, the prompt, the language, or the
specific tool description. It was the **size of the candidate set**.
Gemma's tool-use head pattern-matches against the names in the
catalogue, and the magnet effect collapses as the catalogue shrinks.

In the same session we also observed that Qwen-class thinking models
(referenced by Roberto) require a minimum reasoning budget of about
512 tokens for tool-use to work: smaller budgets truncate the chain of
thought before the model can decide which tool to call. Gemma was
hard-coded at 1024 for the synthesis pipeline, but it turned out to be
overkill for the planner; 512 is the sweet spot for Gemma too.

## Decision

Three coupled changes, implemented together because they only work as
a unit:

* **Prefilter cap to `k_max=8`**, with adaptive K and a *relative*
  cutoff: keep only candidates with `score >= top_score // 2`, then cap
  at `k_max=8`. With the meta tool `request_new_executor` always
  appended (telos of non-renunciation, see ADR 0054), the planner sees
  at most **9 tools** per step. Pool size grows freely (today 25 seed,
  tomorrow 100+); the planner only ever sees the relevant slice.

* **`think=True` is the default for the planner**, with
  `reasoning_budget=512`. The reasoning is short enough not to drown
  the tool-call decision and long enough to let the model compose
  ReAct sequences (e.g. "first `get_now`, then `find_messages` with
  the date returned").

* **`reasoning_budget` parametrised** in `LlamaCppProvider.chat_with_tools`
  (default 512, used to be hard-coded 1024). The synthesis pipeline
  keeps 1024 for stage 1 NAMING (where higher budget pays off — see
  ADR 0053). Other providers (Qwen, future thinking models) can pass
  their own budget through the same surface.

## Consequences

Live confirmation on Telegram, after the three changes were composed:
the test query that had been failing the entire session
(`"riassumi le mail metnos di oggi"`) chose `read_messages(account=metnos_system, max_results=10)`
on the first step, on every retry. The same change unlocked downstream
issues we had been treating as planner bugs (e.g. invented dates in
`since` parameters): once the magnetic placeholder stopped winning,
the model used the contextual reasoning it already had.

The lesson generalises:

> **The bottleneck of an LLM tool-use pipeline is not usually the
> model. It is the conditions in which you put it.** A noisy candidate
> set drowns the description-based selection. A reasoning budget too
> long invites pattern-matching shortcuts; one too short truncates the
> compositional chain. The right shape for the prompt is the shape
> that lets the model do its job, not the shape that contains the most
> information.

## Alternatives considered

* **Embeddings on tool descriptions** (Roberto's long-term suggestion).
  Rejected for tonight: requires either a pip dependency
  (sentence-transformers) or running a second llama-server with a
  small embedding model. The token-affinity prefilter at `k_max=8` is
  good enough until the seed pool grows beyond ~50 executors. Embedding
  routing stays on the roadmap as the next-tier upgrade.
* **Hard blacklist in the system prompt** ("for email queries, NEVER
  use `get_files_metadata`"). Tested and *does not work*: Gemma
  ignores the constraint when the tool is in the candidate set. The
  fix has to operate on the candidate set, not on the prompt.
* **Context-aware `_drop` of magnetic tools** by keyword on the user
  query ("if user says mail/email, drop `get_files_metadata` etc.").
  Tested as a stopgap, dropped: it is hardcoding of domain knowledge
  in the runtime. The relative cutoff achieves the same effect without
  any domain-specific knowledge.
* **Disabling thinking entirely on the planner**. Tested earlier in the
  session as a workaround. It removed the magnet effect on small
  catalogues but degraded multi-step ReAct (the model could not plan
  `get_now → find_messages` without the reasoning chain). Rejected in
  favour of the joint fix.

## Open

* When the seed pool crosses ~50 active executors, the relative cutoff
  may admit too many candidates with mid scores; that's the moment to
  switch to embedding-based routing. Trigger: track average `K` over a
  week of real traffic; if it consistently hits `k_max`, plan the
  upgrade.
* Per-provider reasoning budget defaults will need to live in
  `provider_config.toml` (Anthropic ignores it, OpenAI uses other
  knobs, Qwen min 512, Gemma 512 for tool-use / 1024 for code synth).
