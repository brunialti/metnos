---
id: 0054
title: Telos of non-renunciation cabled — request_new_executor builtin + auto sign+install + catalog reload
date: 2026-04-28
status: accepted
area: agent_runtime
related:
  - 0010
  - 0052
  - 0053
---

## Context

Live test of QuickTour scene 7 ("synt-on-the-fly") on Telegram showed
that the telos of non-renunciation (ADR 0010) was *not* cabled into
the runtime in a way the user could observe. When asked
"comprimi /tmp/cfg.json con gzip" — a task no seed executor covered —
Gemma simply replied "non dispongo di un tool per la compressione",
ending the turn as a graceful refusal instead of triggering the synt
cascade.

Inspection of `agent_runtime.py` confirmed the gap: the synt-compose
hook (`_try_synt_compose`) only fires when the LLM emits a `tool_call`
with a name not in the catalogue. With native tool-use enabled, Gemma
is constrained to tools it sees; it does not invent names. Without an
explicit affordance to *request* synthesis, the planner had no path
beyond refusal.

## Decision

Three coupled changes, implemented as one feature because each is
useless without the others:

* **Builtin tool `request_new_executor(expected_name, intent)`**, in
  `runtime/synth_request.py`, modelled on `scratchpad_read`: a tool
  that lives in the runtime, has no manifest on disk, is appended to
  every step's tool set. The system prompt instructs the planner to
  call it whenever no listed tool covers the request. The `expected_name`
  follows the closed naming convention (`{action}_{object}[_qualifier]`,
  see ADR 0049); `intent` is one or two sentences describing what the
  executor should do.

* **Auto sign + install** of the synthesised executor on success.
  `synth_request.handle_synth_request` writes the manifest and the
  Python file to `~/.local/share/metnos/executors/<name>/`, runs
  `sign_executor` from `runtime/sign.py`, and the loader picks it up
  on the next `load_catalog()` call. Idempotent: the directory is
  overwritten on subsequent runs of the same name.

* **Catalog reload mid-turn.** After a successful
  `request_new_executor`, the run_turn loop reloads the catalogue,
  re-renders the tool set, and adds the freshly-installed executor to
  `base_tools` for the next step. The planner sees the new tool on
  the very next iteration and can call it on the original arguments,
  closing the user's task in a single turn end-to-end.

The synthesis itself remains the multistage pipeline from ADR 0051
(naming → signature → tests → description → code), now reachable from
a real ReAct turn rather than only from CLI.

## Consequences

Live confirmation on Telegram (28 April 2026, 21:55-21:58):
"comprimi /tmp/cfg.json con gzip" produced, in 148 seconds wall time
inside a single turn:

1. progress bar (5 dots) advancing through the 5 multistage stages;
2. `compress_files_gz` synthesised, signed, installed;
3. catalogue reloaded mid-turn, the planner picked up the new tool;
4. final answer: "Ho compresso il file /tmp/cfg.json in /tmp/cfg.json.gz" —
   verified on disk (374 bytes → 428 bytes, valid gzip).

The QuickTour scene 7 now carries the live verification badge in
both IT and EN.

## Alternatives considered

* **Letting the LLM invent the tool name** (and intercepting unknown
  names as proto-mnest) — rejected because native tool-use providers
  validate the name against the offered set. The model conforms to
  the constraint and chooses a fallback tool instead of inventing.
* **Manual approval card before install** — rejected for the live
  flow. The synthesis already produces signed code with green birth
  tests; the human gate moves to the *runtime* of the new executor
  (subject to vaglio/policy as any other call). Approval cards remain
  for the introvertive cascade (merge, generalise).
* **Persisting synthesised executors in `/opt/myclaw/executors/`**
  alongside seed executors — rejected to keep authored vs synthesised
  visually separable on disk and to avoid polluting the project tree
  with machine-generated code. The loader scans both directories.

## Open

* Schema lifecycle: synthesised executors are created with
  `lifecycle = "active"` on first install. Demotion to `deprecated`
  on disuse, and TTL-based archival, follow the same flow as seeds —
  but the introvertive cascade has not yet been exercised against a
  pool that contains synthesised entries.
* The synthesised executor for "compress_files_gz" used `tarfile`
  with `w:gz` mode even on single files, producing a valid `.gz`
  whose payload includes a tar PaxHeader before the original bytes.
  Semantically acceptable, but would benefit from a stage-5 prompt
  hint distinguishing "single-file gzip" from "multi-file tar.gz".
  Filed as a stage-5 prompt iteration, not blocking.
