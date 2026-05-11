---
id: 0049
title: Multidimensional analysis of the synt synthesizer (post-28/4 reality check)
date: 2026-04-28
status: proposed
area: synt
related:
  - 0001
  - 0002
  - 0003
  - 0025
  - 0026
  - 0039
  - 0040
  - 0041
  - 0045
---

## Context

The April 28 live Telegram session pushed the rest of the runtime forward
in a way the synt has not yet caught up with. We added the
revertible-with-reverse-pattern contract (ADR 0001), a closed action/object
vocabulary (ADR 0002), a five-state lifecycle with `superseded_by`
(ADR 0003), an OS-aware `platform_policy` consulted by every critical
executor, the message repertoire `runtime/messages.py`, and the standing
robustness rules in `feedback_robust_executors`. Meanwhile the Gemma 4 26B
local planner failed multiple turns in characteristic ways: it skipped
`filter_entries`, invented Jinja-like placeholders, and treated a
directory as a file. The same model is what the synt wires as `tier=wise`
for both `propose_executor` and `propose_birth_tests`. The question is no
longer whether the synt can produce *some* executor (it can, since the
26 April POC). It is whether it can today produce a *critical-revertible*
executor like `delete_files` safely, with the contracts listed above.
This analysis maps the gap and orders the work items needed before we
let the synt write a destructive executor end to end.

## Decision

We assess the synt across nine dimensions and surface five high-priority
work items plus a hard-blocker list for the `delete_files`
synth-generation case. The assessment uses the current `runtime/synt.py`
(1690 lines) as ground truth and is calibrated against the live findings
of 28/4 and the standing memories.

**D1. Tier choice — score 4/5.** The synt correctly routes both
`propose_executor` (`runtime/synt.py:625`) and `propose_birth_tests`
(`runtime/synt.py:856`) to `tier="wise"`, honoring ADR 0026
(no degradation). The default wise points at Gemma 4 26B
(`runtime/llm_router.py` config). The probe stage is collapsed inside
generate so there is no separate middle-tier call. Action: keep wise for
generate; consider a middle-tier *spec critique* pre-pass to catch
malformed specs before paying the full Gemma round.

**D2. Manifest quality — score 2/5.** `_render_manifest_toml`
(`runtime/synt.py:1067-1106`) emits only `name/version/author/description/
affinity` plus `[code]` and `[args.*]`. It does not generate `lifecycle`,
`revertible`, `reverse_pattern`, `superseded_by`, `target_kind`, or
`capabilities`. The closed naming vocabulary from ADR 0002 is absent from
the system prompt (`GENERATE_SYSTEM_PROMPT`, `runtime/synt.py:86-101`):
the synt asks for "snake_case verb_noun" with no list of allowed actions
or objects. Result: a synt-generated executor today is born `active` by
default with no undo and no reverse pattern.

**D3. Code quality / robustness — score 2/5.** The prompt names neither
`runtime/messages.py`, `runtime/platform_policy.py`, nor the (still
missing) `runtime/executor_helpers.py`. Imports are validated against
`STDLIB_WHITELIST` (`runtime/synt.py:72-80`) but Metnos's own runtime
modules are not whitelisted, so an executor that does `from messages
import get` would currently fail the import check. The vectorial-by-default
rule (memory `feedback_executors_vectorial_by_default`) is not encoded
either. The synt produces self-contained pure-stdlib snippets, which is
fine for a `count_words` toy but wrong for any executor that touches the
filesystem.

**D4. Test coverage — score 3/5.** Birth tests are generated and executed
(`_run_birth_tests`, `runtime/synt.py:821-900`) with a 3-5 case minimum
and the propose_birth_tests tool schema is sound. There is no explicit
edge case requirement (empty list, scope violation, dangerous arg). The
runner imposes `all_passed` and at least 3 tests, which is the right
floor; the gap is in *coverage prescription* inside the prompt.

**D5. Safety — score 2/5.** `derive_sandbox_profile`
(`runtime/synt.py:1489-1545`) flags net/subprocess/write_files/dangerous
calls and rejects on `dangerous=True`. There is no critical-class
recognition: the synt cannot today decide that a candidate is
"destructive" and trigger the extra checks (capability set, revertibility,
platform_policy guard, dry-run mode). Anything that calls `os.remove`,
`shutil.rmtree`, `Path.unlink`, or write-mode `open()` reaches sandbox
profile but no separate capability ladder.

**D6. Self-correction — score 1/5.** There is no refinement loop. If
birth tests fail or the convention check fails, the proposal is logged,
saved under `_failed_*`, and the request is marked abandoned with a
24h lock (`runtime/synt.py:723-748`). One-shot generation, zero retries.
This is the largest gap versus Voyager and SWE-Agent (see References).

**D7. Performance — score 4/5.** Wise call uses `max_tokens=6000` for
generate and `4500` for birth tests, with `for_code=True` on generate.
On Gemma 4 26B local this lands in the tens of seconds per stage, so a
full proposal takes roughly a minute end to end. Acceptable for
introspective synthesis, expensive for reactive cascade. No metric on
out_tokens cap budgets.

**D8. Reverse pattern — score 1/5.** `runtime/reverse_patterns.py` (165
lines, four named patterns) is not referenced by the synt at all. The
prompt does not mention reverse, the manifest renderer does not emit
`reverse_pattern`, the schema for `propose_executor` has no
`reverse_pattern` field, and there is no escalation path when a candidate
implies critical mutation. Today the synt is structurally incapable of
producing a revertible executor.

**D9. Lifecycle awareness — score 1/5.** The five-state lifecycle of ADR
0003 (`proposed → synthesized → active`) does not exist for the synt: the
manifest renderer omits the `lifecycle` field entirely, and the approval
path moves the proposal directly into `executors/<name>/` and signs it
(`approve_proposal`, `runtime/synt.py:1373-1441`). There is no
intermediate `synthesized` state, no shadow run, no superseded_by support.

## External references

Three patterns from prior art that map naturally onto these gaps. Voyager
([Wang et al., NeurIPS 2023](https://voyager.minedojo.org/)) keeps a
**skill library of executable code** and uses an *iterative prompting
mechanism* with environment feedback, execution errors, and
self-verification — exactly the refinement loop we are missing in D6.
SWE-Agent ([Yang et al., NeurIPS 2024](https://arxiv.org/abs/2405.15793))
introduces **agent-computer interfaces with guardrails for error recovery
and immediate feedback on edits**, which is the model for adding a
spec-critique middle-tier pass and a structured retry on birth-test
failure. Toolformer ([Schick et al., 2023](https://arxiv.org/abs/2302.04761))
filters generated tool calls by **utility — keep only those that improve
downstream prediction** — which translates into our reward formula being
weak (judge stub at 0.5, no actual utility signal from the planner that
would have used the executor); plugging real reward back from runtime
usage is a v1.2 work item.

## Conclusion: top-five work items and delete_files blockers

The five priorities are, in order: (1) extend the `propose_executor`
tool schema and the manifest renderer with `lifecycle`,
`target_kind`, `capabilities`, `revertible`, `reverse_pattern`,
`superseded_by`, defaulting `lifecycle="synthesized"` so a fresh
proposal never auto-installs as `active`; (2) inline the closed
action/object vocabulary from ADR 0002 into `GENERATE_SYSTEM_PROMPT`
and refuse a proposal whose name does not match the
`{action}_{plural_object}[_{qualifier}]` grammar; (3) add a refinement
loop on birth-test failure (max 2 retries, send the failing test stdout
back as feedback, escalate to abandoned only after both retries) — this
single change addresses Voyager-style self-verification and likely
turns most one-shot failures into success; (4) build
`runtime/executor_helpers.py` and add it plus `messages.py` and
`platform_policy.py` to the synt prompt and import whitelist, so
generated executors can speak the runtime's idioms; (5) introduce a
critical-class detector inside `derive_sandbox_profile` (any call to
`os.remove`, `shutil.rmtree`, `Path.unlink`, write-mode `open`) that
forces `revertible=true` and a `reverse_pattern` chosen from the closed
catalog of `runtime/reverse_patterns.py`.

Hard blockers for `delete_files` synth-generation today: the synt cannot
emit `revertible`, cannot pick a `reverse_pattern`, cannot import
`platform_policy` (so no protected-path guard), cannot land in the
`synthesized` lifecycle state for a shadow run, has no refinement loop
when birth tests miss the destructive edge cases, and offers no critical-
class capability ladder. Until these five blockers close, a synt-generated
`delete_files` would be a one-shot active-state executor that deletes
into protected paths with no undo. Out of scope today.
