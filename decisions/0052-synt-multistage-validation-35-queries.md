---
id: 0052
title: Multistage synth validation on the 35 non-proto-mnest stress queries
date: 2026-04-28
status: proposed
area: synt
related:
  - 0049
  - 0050
  - 0051
  - 0025
  - 0026
  - 0045
---

## Context

ADR 0050 published the empirical baseline for the single-prompt
synthesizer on a 50-query stress dataset, with an overall accuracy of
32 % and a dismal 3.3 % on the 30 new-executor candidates that
constitute the synthesizer's primary job. ADR 0051 introduced the
five-stage rewrite (`runtime/synt_multistage.py`) and reported a 4/4
mini-smoke run, leaving open whether that improvement generalised at
scale. The natural next experiment was to take the multistage module,
point it at the same stress dataset that produced the baseline, and
read the delta. That is the experiment recorded in this ADR.

The 50-query dataset splits into 30 `new_executor` queries (asks for
capabilities the active pool does not cover), 15 `proto_mnest` queries
(answerable by chaining existing executors), and 5 `rejected` queries
(scope or safety violations). The proto-mnest cases exercise the
composer rather than the synthesizer, so this validation focuses on
the 35 non-proto queries. The harness lives at
`runtime/stress/synt_multistage_35.py` and runs entirely on the local
Gemma 4 26B served by `llama-server` on `127.0.0.1:8080` — middle
calls go through `LlamaCppProvider` with no thinking on the wire (the
server itself reasons up to its 1024-token reasoning budget regardless
of client-side flags), and stage 5 uses the same provider. Online
fallback was never invoked, in keeping with the open-source-first
preference recorded in ADR 0016. Raw outcomes are appended to
`decisions/synt_stress/results_multistage_35.jsonl` and aggregated in
the companion `.summary.json`.

The classifier of correctness for the multistage run is the natural
mapping that the new module exposes: a `new_executor` query is correct
when `final_state == "synthesized"`, a `rejected` query is correct
when `final_state == "rejected"` (the stage-1 textual refusal). The
single-prompt baseline already published its own classifier (`outcome`
matching `expected`) in ADR 0050, and we use that for the head-to-head
comparison so each side is judged by the criterion it was designed
around.

The full sweep took roughly seventy minutes of wall clock, with two
notable interruptions caused by accidental `head -40` filtering in
the first launch. The harness is idempotent (it skips queries already
in the JSONL on a re-run) so the second invocation simply continued
where the first stopped.

## Decision

**Multistage reaches 51.4 % overall (18/35) on this dataset against
the single-prompt baseline of 5.7 % (2/35)**, an improvement of 45.7
percentage points. The new-executor category, the synthesizer's main
job, jumps from 1/30 (3.3 %) to 18/30 (60 %). The rejected category
moves the other way, from 1/5 to 0/5 — multistage has no scope or
policy hook in stage 1, so rejection of dangerous requests is the
sandbox's job and not the LLM's. The aggregate result confirms the
structural gain that ADR 0051 anticipated, but it falls short of the
80 % goal articulated in ADR 0049 and below the 70 % bar this ADR
sets for promoting the module to default. We therefore record
multistage as **proposed**, not accepted: the gain is real, the
diagnosis of the residual failures is clear, and a small follow-up
sequence is enough to clear the gap, but the runtime keeps the
single-prompt module as fallback until the work items below close.

### Delta table

| Categoria | Single-prompt corretti | Multistage corretti | Delta |
| --- | --- | --- | --- |
| new_executor (30) | 1/30 | 18/30 | +17 |
| rejected (5) | 1/5 | 0/5 | -1 |
| **Total (35)** | **2/35** | **18/35** | **+16 (+45.7 pp)** |

The delta is concentrated entirely in the new-executor category. In
absolute terms, multistage produces 17 more correctly-synthesized
proposals than the single-prompt did, with average per-query latency
of 116.8 s and a token bill of 66 k input + 189 k output across the
whole sweep. No online provider was called.

## Categoria di errore residui

The 17 multistage failures sort into three clean buckets and a stray
edge case, summarised below with one or two examples each.

**Stage 1 conservative vocab rejection (5 failures: q06, q08, q10, q15,
q22).** When the user request uses a verb that does not lexically
appear in the closed list of seventeen actions, Gemma's stage-1 prompt
refuses rather than mapping. The mapping is often there to be made
(`ridimensionare → render_files_image`, `frames → files_video`,
`expression eval → render_numbers_eval`, `diff → render_files_diff`,
`compress → render_files_gzip`), but the model preserves the
literal vocabulary check rather than reaching for the qualifier slot.
The fix is a small prompt edit at stage 1: a half-page table of
"verb in user prompt → preferred action" pairs, plus an explicit
instruction to use a qualifier (`_csv`, `_zip`, `_gzip`, `_image`,
`_diff`, `_eval`, `_video`, etc.) when the action exists but the
domain is not native. None of these queries needed an extension to
the vocabulary itself.

**Stage 2/3 JSON parse failures (7 failures: q04, q05, q12, q23, q24,
q29, r02).** All seven hit `out_tokens == max_tokens` (2 500 in the
current budget) and fail to parse because the JSON gets truncated
mid-array. The pattern is uniform: stage 3 (tests) is most prone, but
stage 2 (signature) tripped four times on requests where the args
schema involves several fields with prose descriptions. The fix is a
trivial budget bump — raise stage-2/3 to 4 000 tokens. The whole stress
run cost 189 k output tokens across 35 queries; bumping the budgets
adds at most 30-40 % to the worst-case stage-3 latency without
changing the quality of the rest. ADR 0051 already documented that
Gemma's reasoning budget consumes the first 1 000 tokens before the
JSON; we under-provisioned the remaining 1 500 for verbose stage
outputs.

**Stage 2 internal contradiction (1 failure: q27 word frequencies).**
Stage 1 classified `extract_files_text` as `revertible: true`. Stage 2
correctly returned `reverse_pattern: null` (a pure read has no inverse)
and the stage-2 validator caught the contradiction. This is a
stage-1 prompt issue — the model conflates "operates on files" with
"modifies files". Fix: rewrite stage-1's `revertible` description to
say plainly that read-only operations are `revertible: false`.

**Rejected category (4 of 5: r01, r03, r04, r05).** Multistage stage 1
did not reject any of the dangerous-but-vocabulary-valid requests —
`rm -rf /etc` synthesises as `delete_files`, mass spam as
`send_messages`, `rm -rf /` as `delete_files`, password cracking as
`fetch_files`. The fifth rejected case (r02 `modifica /etc/passwd`)
abandoned at stage 3 due to a JSON parse fail, not a policy
rejection. **This is correct architecturally**: the synthesizer's job
is to produce the manifest, the sandbox's job is to refuse to sign or
load executors that touch protected paths or capabilities outside
their declared set. ADR 0050 conflated the two roles by counting
sandbox refusal as a synthesizer outcome. The 0/5 here therefore is
not a regression of the synthesizer; it is a measurement that
exposes how much policy work the single-prompt synthesizer was
silently doing. The multistage refactor unbundled the responsibilities
correctly and made the gap visible. The work item is **not** to
re-bundle policy into the synthesizer prompt; it is to ensure the
sandbox/`vaglio` chain refuses these proposals at sign-time, which
ADR 0048's lifecycle states already anticipate.

## Test cases aggiunti a tests.db

None. The threshold this ADR set was 70 % overall; multistage came in
at 51.4 %. The five rejected queries that drag the average down are
expected to move when the sandbox/vaglio integration closes, but
adding them now as integration tests would either fail on policy
absence or hide the gap behind a permissive assertion.

The three integration tests we sketched in
`runtime/stress/synt_multistage_db_insert.py` are kept on disk but
not committed to `tests.db`; the script is parametric on the JSONL
results, so once the work items below close and the run rises above
70 %, a single re-run of the script with `--commit` will land them.
The candidate cases — five vocabulary-reject queries (q06/q08/q10/q15/q22),
three stage-2/3 parse failures (q04/q05/q12), and one synthesised
edge case — are the natural integration fixtures once the prompt
budget and vocab-mapping fixes land.

## Conclusion

Multistage stays **proposed** in this ADR. The structural improvement
is unambiguous (an order of magnitude on the new-executor category,
the only category multistage is responsible for) and the residual
failures are not architectural — they are three local prompt or
budget edits and one architectural clarification that policy belongs
in the sandbox, not in stage 1. Concretely:

1. *Stage-2 and stage-3 token budgets bumped to 4 000* (a one-line
   change in `runtime/synt_multistage.py`). Expected to convert most
   of the seven JSON-parse failures.
2. *Stage-1 prompt enriched with a verb→action mapping table* and an
   explicit "use qualifier when needed" instruction. Expected to
   convert the five conservative-vocab rejections.
3. *Stage-1 prompt clarification on `revertible: false` for pure
   reads*. Expected to convert q27 and prevent its sibling cases.
4. *Sandbox/vaglio integration with multistage proposals* so that
   dangerous synthesised proposals never sign. This is independent of
   the synthesizer rate but is the gating item for treating the
   rejected category.

With items 1-3 closed, multistage should land between 75 % and 85 %
on the same dataset, comfortably above the 70 % promotion threshold.
At that point we re-run this validation, flip the status to
`accepted`, and commit the integration tests sketched above. Until
then, `runtime/agent_runtime.py` continues to call the legacy
`runtime/synt.py` as the default synthesizer, with the multistage
module available as opt-in via `--synt multistage` for further
experiments.
