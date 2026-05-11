---
id: 0042
title: Native tool-use as the planner default
date: 2026-04-26
status: accepted
area: runtime
related:
  - 0025
  - 0043
---

## Context

The early POC of v1.1 had the planner emit JSON in the LLM's text
output and parse that JSON back into a tool invocation. The pattern
is widespread in the indie LLM-tooling space — write a system prompt
that says "respond only with JSON in this shape", parse the
response, validate. It works approximately. It also fails
approximately: the LLM puts a sentence before the JSON, forgets the
fenced code block, emits malformed JSON when the prompt is long,
chooses a slightly wrong field name. Each failure is a class of
edge case the parser must defend against.

On 26 April 2026 a probe of the Ollama API revealed that Qwen 2.5
and Qwen 3 (and Llama 3.1+, Mistral instruct, Gemma 3) all support
native tool-use through the `/api/chat` endpoint with a `tools`
parameter (a list of JSON-Schema function definitions). When the
model decides to invoke a tool, it returns `message.tool_calls`
structured: `{"id": "call_xxx", "function": {"name": ..., "arguments":
{...}}}`. No prompt template, no parsing, no fenced code blocks.

Anthropic's API supports the same pattern via `tool_use` blocks.
OpenAI supports it via function calling. The pattern is industry-
wide and reliable. The empirical comparison: latency identical to
prompt-based JSON (~800 ms on Qwen 3:8b `think=false`); failure
modes of the prompt-based approach (text-before-JSON, malformed
JSON, missing fence) eliminated entirely.

The decision was needed before `agent_runtime.html` v1.1 was
written, because the planner's loop shape depends on which API
flavor it uses.

## Decision

Native tool-use is the default for the LLM provider abstraction.

**LLMProvider exposes `chat_with_tools(system, user, tools=[...],
history=[...])`** rather than `chat(system, user) -> text`. The
return type carries `text` (when the model produces a final
answer) or `tool_calls` (when it requests an invocation), not raw
text to be parsed.

**`agent_runtime` does not parse text to extract a plan.** It
receives `tool_calls` directly, validates against the executor's
schema, executes. The loop becomes: call the LLM with `tools` +
`history`; if `tool_calls`, run each, append the observation, loop;
if `text`, that is the final answer.

**The manifest's args JSON Schema is the `parameters` of the
tool** passed to the LLM. Zero translation: what the manifest
declares is what the LLM sees, character-for-character.

**Same pattern across providers.** Ollama (local), Anthropic,
OpenAI all use the same mental shape. The runtime code does not
fork per provider.

**Fallback for non-supporting providers.** Some older or smaller
local providers may not support tool-use. The prompt-based JSON
parser remains as a fallback, but it is no longer the default.
Activated only when the provider declares no tool-use support.

The decision is one of three ratifications closed on 26 April
2026 (the others: data piping `{{stepN.field}}` in ADR 0043, and
the local-LLM default in ADR 0044). All three are documented in
`metnos_design_decisions_post_poc.md` and rolled into
`agent_runtime.html` v1.1.

## Alternatives considered

**Keep prompt-based JSON as default.** Pro: works on any LLM,
including those that do not support tool-use. Con: fragile against
the well-known failure modes; requires defensive parsing; produces
intermittent errors that look like model variance but are actually
a parser issue. Rejected.

**Tool-use only when the provider supports it; no fallback.** Pro:
simplest code path. Con: cuts off provider portability; some local
models (DeepSeek-R1 thinking-only, future small models) may not
support tool-use. Keep the fallback, but explicit opt-in. Rejected
the no-fallback variant.

**Hybrid — tool-use for the planner, prompt-based for the synt or
other components.** Pro: per-component flexibility. Con: introduces
two patterns to maintain, two failure modes to debug; the empirical
finding is that tool-use is uniformly better. Rejected.

## Consequences

Approximately 100 lines of parser code are removed from
`agent_runtime`. About 50 lines of tool-call validation and
execution are added. Net code reduction.

The reopened decisions documented in the POC memory:

- **D1 (loop shape)**: invariant in direction (mode-parametrized,
  multistep local, single-shot online), but rewritten in *how* —
  no JSON parser, structured tool-calls.
- **B1.2 (args = JSON Schema)**: confirmed; the manifest's schema
  is the tool's `parameters` directly, zero translation.

A whole class of bugs disappears: the LLM putting text before the
JSON, forgetting the fence, producing malformed JSON, choosing
slightly wrong field names. None of these are possible with native
tool-use.

The data piping decision (ADR 0043) survives unchanged: the
`{{stepN.field}}` syntax now lives inside the `arguments` of the
tool-call. The runtime resolves before invocation. Behavior
identical to before; surface area reduced.

Provider compatibility queue:
- Ollama supports natively for: Qwen 2.5, Qwen 3, Llama 3.1+,
  Mistral instruct, Gemma 3. Tested.
- Anthropic API: native (`tool_use` blocks). Tested.
- OpenAI API: native (function calling). Compatible by inspection.
- LiteLLM: cross-provider translation layer.
- Providers that do not support `tool_calls`: degrade to
  prompt-based parser via the fallback path.

The doc `agent_runtime.html` v1.1 is written with native tool-use
as the principal architecture, not as an option. The fallback is
mentioned but separated.
