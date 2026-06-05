---
id: 0142
title: consult_frontier system verb + agentic frontier primitive (modes A and B)
date: 2026-05-17
status: accepted
area: runtime | executor | naming | llm
related:
  - 0088  # admin HMAC consent
  - 0123  # skill importer agentskills.io
  - 0134  # semantic affinity fallback BGE-M3
  - 0140  # sandbox audit + scaling skill
  - 0141  # GitHub provider first-party skill (first mode-B consumer)
---

## Context

By 17/5/2026 Metnos relies on a 3-tier local LLM stack (fast =
`qwen3:8b`, middle/wise = Gemma 4 26B) for synth and planner. Frontier
models (Anthropic Sonnet/Opus, OpenAI GPT-5) are reserved as
single-call fallbacks for wise-tier saturation. There is no primitive
for **delegating a self-contained reasoning task** to a frontier model
in a controlled way.

Three concrete needs surfaced together:

1. **GitHub auto-reply (ADR 0141)**. Before posting an answer to an
   open issue, Metnos needs the answer to be *grounded* in the actual
   repository: file contents, dir structure, related code. The size of
   a real repo (hundreds of thousands of LOC) rules out prefetching
   "the relevant subset" into a single prompt — *if Metnos already
   knew which files were relevant, Metnos would already know the
   answer*. The selection of relevant context is itself a reasoning
   task and belongs to the frontier model.

2. **Email triage and report synthesis**. Multi-thread analysis with
   cross-references benefits from frontier reasoning even without
   tool use. A single big prompt + structured output is enough.

3. **Code review delegation**. A frontier model can review a diff with
   awareness of the broader codebase if it can navigate the repo.

These needs share a common shape: a *role* (what the frontier is
asked to do), an *output specification* (what shape the answer must
take), a *local context* (data Metnos already has and wants to inject
verbatim), and optionally a *remote context handle* (a set of tools
the frontier can call to explore further). Building three specialized
executors would violate §7.2 (simplest sufficient form) and §7.3
(general solution over hardcoded). One executor with two operating
modes covers the entire space.

Two architectural choices had to be made up front:

- **Where in the §2.2 grammar does this verb live?** The action
  ("consult", "delegate to a higher reasoning tier") is not a
  user-domain operation on a user-domain object. It is a runtime
  capability, exactly like `admin` (shell escalation) and `undo`
  (turn rewind). It belongs to a small class of meta-verbs reserved
  to the runtime.
- **Is `remote_context` a prefetch or an agentic tool exposure?**
  The first design draft had it as prefetched bytes. We replaced it
  with tool exposure once it became clear that the selection of
  relevant remote context is the very task the frontier is being
  hired for.

## Decision

### (A) `consult` becomes a system verb category

§2.2 already enumerates two system verbs (`admin`, `undo`) outside the
23 user-action vocabulary. **`consult` joins them as a third**,
documented in `runtime/vocab.SYSTEM_VERBS` and rejected by the synth
NAMING stage like the other two. The §2.2 entry for SYSTEM_VERBS reads:

> System verbs are runtime meta-actions. They do not produce
> user-domain entities. They are reserved to builtin executors
> (`undo_last_turn`, `admin`, `consult_frontier`); the synth pipeline
> rejects executor names that begin with one of them.

Phase 1 ships exactly one executor in this category:
`consult_frontier`. The category is opened (not just the executor) to
make room for future siblings (e.g. `consult_local_wise` if we ever
want a controlled wise-tier loop without frontier billing).

### (B) Single builtin executor with two modes

Path: `/opt/myclaw/executors/consult_frontier/`
(manifest + `consult_frontier.py`).

Signature:

```
consult_frontier(
  role: str,                  # what the frontier is asked to do
  output_spec: dict,          # JSON schema the answer must satisfy
  local_context: dict,        # data injected verbatim into the prompt
  remote_context: list|None,  # mode B: tool exposure for exploration
  tools_allowed: list[str],   # whitelist of tool names exposable
  tier: "fast"|"middle"|"wise" = "wise",
  max_tool_iters: int = 30,
  max_remote_bytes: int = 500_000,
  cache_ttl_s: int = 0,
) -> {"answer": <conforms to output_spec>, "meta": {...}}
```

**Mode A — single-call (`remote_context=None`)**. A monolithic prompt
is assembled from `role` + `output_spec` + `local_context`. One LLM
call, structured JSON output validated against `output_spec`. Tier
can be any (including local Gemma for cost-free runs).

**Mode B — agentic (`remote_context` non-empty)**. The frontier is
given a tool-use loop over the names listed in `tools_allowed`
(intersected with `remote_context` definitions). The loop:

1. Frontier emits a tool call (Anthropic/OpenAI structured tool API).
2. Metnos dispatches it through a local sandboxed dispatcher.
3. Result is appended; loop continues.
4. Loop terminates on: (a) frontier emits a final answer, (b)
   `max_tool_iters` reached, (c) cumulative tool output exceeds
   `max_remote_bytes`.

Tier in mode B must be `middle` or higher *and* point to a provider
with structured tool API (Anthropic / OpenAI). The executor
auto-bumps `tier="fast"` to `middle` in mode B and emits a warning
into the meta block (no error: planner-driven misconfiguration should
not abort the turn).

### (C) Read-only tool exposure invariant

The dispatcher refuses to expose any tool whose `target_kind` implies
mutation:

```
DENY = {
  "create_*", "set_*", "send_*", "move_*", "delete_*",
  "write_*", "change_*", "share_*",
}
```

This is checked at executor entry (deterministic §7.9) against the
manifest of each tool listed in `tools_allowed`. A request to expose
`send_messages_github` to a frontier session fails fast with
`error_class="forbidden_mutation_in_consult"`. Write operations
remain human-gated: the frontier produces a *proposal* in
`output_spec`, Metnos turns it into an approval card, Roberto
confirms, *then* Metnos performs the mutation through the regular
executor path.

### (D) Path sandbox for filesystem tools

Tools that read local filesystem (e.g. `read_files_text`,
`list_dirs`) are gated by a path whitelist
(`consult_frontier._path_allowed`):

- `/opt/myclaw/**` (product code, read-only)
- `/home/user/**` (user files)
- `/tmp/**`

Anything outside (e.g. `/etc`, `/root`, `~/.ssh`,
`~/.config/metnos`) is rejected. Symlink resolution is applied
before the whitelist check.

### (E) Tier configuration + fallback chain

Tiers and their endpoint mapping live in
`~/.config/metnos/llm_tiers.toml`:

```
[tiers.fast]
provider = "anthropic"
model = "claude-haiku-4-5"

[tiers.middle]
provider = "anthropic"
model = "claude-sonnet-4-7"

[tiers.wise]
provider = "anthropic"
model = "claude-opus-4-7"
fallback = ["openai:gpt-5"]
```

`runtime/llm_router.py::LLMRouter.fallback_chain(tier)` returns the
ordered list of (provider, model) pairs to try on failure. This
formalizes a tier-→model mapping that was implicit in several places
across the codebase (resolves the long-standing item #40 in the
runtime backlog).

### (F) Disk cache

Optional caching per call: `~/.cache/metnos/consult_frontier/<sha>.json`
where `sha = sha256(role + output_spec + local_context +
remote_context_definitions + tier)`. Hit means the entire `answer +
meta` is replayed without touching the frontier API. Default
`cache_ttl_s=0` (disabled). Useful for batch / replay scenarios; not
useful for live agentic flows. Invalidation is TTL-based only — no
semantic invalidation. Manual invalidation = delete the file.

### (G) Cost instrumentation

Before the call: `consult_frontier` emits an estimated cost into
`meta.cost_estimate_usd` based on the public pricing table at
`runtime/llm_pricing.py` (input/output tokens × tier rate).
After the call: actual usage from provider API populates
`meta.cost_actual_usd`. Mode B reports per-iteration cost in
`meta.tool_iters[]`. The estimate is informative, never a gate
(no auto-abort on budget).

## Alternatives considered

**(a) Prefetch context approach** (the design draft we started from).
The executor would accept `remote_context` as already-fetched bytes
or text. Rejected by the central observation: deciding *what* to
prefetch is the same reasoning task we are trying to delegate.
Prefetch puts a low-quality heuristic in front of the frontier,
which is exactly the wrong split of labor.

**(b) One LLM call per fixed prompt** (no agentic loop). Acceptable
for mode A but kills mode B's value proposition. Two options would
have meant exposing them as two different executors, which fragments
the API surface and forces callers to know in advance which mode
they need. The unified signature with `remote_context` as the
discriminator is more honest.

**(c) Multiple specialized executors** (`analyze_issues_github`,
`summarize_email`, `review_pulls_github`, ...). Rejected — DRY
violation (§7.2) and naming pressure (`analyze` is not a §2.2 verb,
neither is `summarize`). A single `consult_frontier` parametrized by
`role` + `output_spec` carries the variability cleanly.

**(d) Expose to the planner as a regular tool**. Rejected. The
planner's grammar pool is constrained (GBNF ADR 0133); a meta-verb
with a free-form `role` argument and a recursive `output_spec`
breaks the pool's discriminated union shape. `consult_frontier` is
called by *executors* (e.g. `runtime/jobs/github_dedup.py`,
auto-reply pipeline) and by the runtime, never by the planner.

**(e) Lean on `admin` for frontier delegation**. Rejected. `admin`
is for shell-level privilege escalation with HMAC consent (ADR
0088); it solves a different problem (privilege) and would force
every frontier call through a consent prompt, which is the opposite
of what mode A is for (silent batch analysis).

## Consequences

- **General-purpose primitive across domains**. One executor serves
  GitHub auto-reply, email triage, report synthesis, review
  delegation. New callers add an entry to their pipeline, not a new
  executor.
- **Controlled exposure surface**. Tool whitelist + path sandbox +
  read-only invariant + max_tool_iters + max_remote_bytes give a
  bounded blast radius. Audit (ADR 0140) records every tool call
  inside the loop.
- **Tier router formalized**. `runtime/llm_router.py` becomes the
  one source of truth for "what model does `wise` mean today?". The
  config file lives outside the repo (per-host) which makes
  multi-host deployments (`.33`, future siblings) cleaner.
- **Real cost**. Sonnet at mode B with 20 tool iterations on a
  medium GitHub issue runs around $0.20–$0.80 per call (input is
  dominated by tool results). Opus is 5x that. Roberto's daily issue
  volume is small (estimated single-digit auto-replies per day);
  monthly budget envelope ~$30–$80. Worth instrumenting from day
  one (per-call estimate + per-month aggregate in
  `runtime/llm_cost_ledger.py`, to be written separately).
- **Mode B requires non-local provider**. Gemma 4 26B local has no
  structured tool-use API equivalent to Anthropic's `tools`
  parameter; mode B is therefore not available with the locally
  hosted wise tier. This is acceptable for the launch use cases
  (GitHub auto-reply is opt-in, runs on demand, and benefits from
  the higher-quality reasoning anyway).
- **Cache is TTL-only**. No semantic invalidation. If the underlying
  repo changes, `cache_ttl_s=0` (default) is the safe choice.
  Manual invalidation is delete-the-file. Acceptable for the launch
  scope; revisit if a high-frequency batch caller appears.
- **Door opened**: the SYSTEM_VERBS category is now three. Future
  meta-verbs (e.g. `consult_local_wise`, `synthesize_executor` as a
  formal verb instead of an inline pipeline) have a clear home.
- **Door closed for now**: no agentic loops over *write* tools, no
  matter how clever the supervisor pattern. Writes go through
  approval cards. Reopening that door requires its own ADR.

## Implementation references

- Executor:
  `/opt/myclaw/executors/consult_frontier/`
  - `manifest.toml`
  - `consult_frontier.py`
- Vocab category:
  `runtime/vocab.py::SYSTEM_VERBS`
- Tier router:
  `runtime/llm_router.py::LLMRouter.fallback_chain`
- Tier config (per-host, outside repo):
  `~/.config/metnos/llm_tiers.toml`
- Pricing table:
  `runtime/llm_pricing.py`
- Path sandbox helper (inline in executor):
  `consult_frontier.py::_path_allowed`
- Read-only mutation gate (inline in executor):
  `consult_frontier.py::DENY` + `_check_tool_readonly`
- Cache directory:
  `~/.cache/metnos/consult_frontier/<sha256>.json`
- First mode-B consumer:
  `runtime/jobs/github_dedup.py` and the auto-reply pipeline of
  ADR 0141
