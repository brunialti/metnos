---
id: 0094
title: Deterministic fast path for trivial queries
date: 2026-05-07
status: accepted
area: runtime
related:
  - 0076  # synth_request short-circuit (same ideological pattern: catch-all runtime-side before LLM cascade)
  - 0077  # introvertiva quality filters (deterministic > LLM)
complements:
  - 0076
---


## Context

Live turn `21fbd601` ("che ore sono") clocked **53.4 s**. Trace: PLANNER
(Gemma 4 26B think=true) loads, picks `get_now`, receives the observation,
formulates the final_answer. The whole LLM-loop is overkill: the query is
a 1:1 deterministic mapping with no arguments and no ambiguity. CLAUDE.md
§7.9 (Determinismo > LLM se equipotente) is the paradigmatic case.

The same pattern applies to ~12 IT + 12 EN trivial queries on time/date.
At ~50 s per turn × even 5 such queries/day across the user base, the
overhead is significant on top of being entirely avoidable.

The catch-all template is already in place: ADR 0076 short-circuits the
synth cascade when an executor name is already in catalog. Same ideology,
applied earlier in the pipeline (before PLANNER, not after).


## Decision

Add `runtime/fast_path.py` (~210 LOC) — a deterministic short-circuit
runtime-side that intercepts trivial queries BEFORE the PLANNER LLM is
invoked, calls the executor directly, and formats the final_message via
deterministic templates. Zero LLM calls in the critical path.

Three components:

1. **Closed pattern table** (`_FAST_PATTERNS`): list of `FastPattern`
   dataclasses, each binding a tuple of normalized exact-match strings
   (IT + EN) to an executor name, literal args, and bilingual final
   message templates. O(1) lookup via pre-built `_PATTERN_INDEX` dict.

2. **Normalizer** (`_normalize`): lowercase, ASCII-fold curly apostrophes
   (mobile autocorrect), strip trailing punctuation `.?!,;:`, collapse
   internal whitespace. NO stemming, NO synonym expansion, NO regex —
   variants live in the table.

3. **Renderer** (`_render_template`): parses `metadata.iso8601` from the
   `get_now` observation, extracts components (hh:mm, weekday, day, month,
   year), substitutes into the template. IT and EN month/weekday tables
   inline; no `locale` dependency.

Integration in `agent_runtime.py::run_turn`: between `turn_id` setup and
`ModeRouter`, after credentials extraction (so redacted query is matched).
Skipped when `reference_images` are attached (multimodal intent always
exceeds a literal time-question pattern). On match: builds a `StepLog`
with `fast_path=True` marker, invokes via existing `invoke_executor`
(reuses sandbox + PYTHONPATH wiring), renders the message, writes the
turn log, returns. On executor failure: silently falls through to the
normal PLANNER flow — fast path is opt-in, never a hard cap on the
turn's success.

Initial coverage: only `get_now` (24 patterns: 12 time + 12 date, IT + EN).
Other 1:1 mappings (`get_location`, `get_processes`, ...) deliberately
excluded: location involves provider availability and credentials,
processes involve filtering — both have ambiguity surfaces. Time/date
are the only patterns where the user's intent is mapped 1:1 to an
executor with no NL argument.


## Alternatives considered

- **Regex-based intent classifier with confidence threshold**. Rejected:
  any "confidence" smaller than 1.0 reintroduces probabilistic behaviour
  and the §2.4 robustness contract becomes a tuning problem. Exact match
  on a closed table is the simplest object that does the job.

- **Embed fast path inside `prefilter.rank_with_intent`**. Rejected: the
  prefilter still needs the PLANNER call afterward (it ranks; it doesn't
  decide). Short-circuiting at prefilter level would mean smuggling
  execution into a ranker, which violates §7.2.

- **Flag in manifest TOML** (`[fast_path] patterns = [...]`). Rejected
  for now: only one executor (`get_now`) is currently a candidate, and
  the patterns are bilingual + need template strings — pushing this into
  TOML adds schema + parser work without payoff. If/when a third
  executor joins (synonym chain), promote to manifest field.

- **Special-case in `format_simple_answer`**. Rejected: that helper runs
  AFTER the executor is invoked, so it would skip the LLM final-answer
  formulation but NOT the PLANNER step that picks the executor. We need
  to skip both.


## Consequences

What gets cheaper:
- "che ora e", "what time is it", and 22 sibling queries: ~50 s →
  ~17 ms (≈3000× speedup; verified end-to-end with subprocess invoke).
  Pure lookup is ~1.4 µs.
- No more wasted PLANNER+LLM cost (~2400 input tokens) on these queries.
- Deterministic, audit-friendly: turn logs carry `fast_path: True` on
  step 1 → easy to query "which fraction of turns short-circuited".

What gets more expensive (almost nothing):
- 30 LOC table to maintain. Each new pattern pair = 1 line.
- One conditional in `run_turn`. ~1.4 µs of overhead on every turn that
  does NOT match (negligible).

Doors closed:
- We will NOT push fast path coverage into ambiguous territory. Every
  pattern in the table must be a 1:1 NL-to-executor mapping with no
  argument extraction. As soon as the planner has a real choice to make
  (which timezone? which file pattern? which user?), the request goes
  to the PLANNER.

Doors opened:
- Pattern for `get_inputs` short-circuits ("annulla l'ultimo" → already
  has a deterministic bypass in `intent_extractor`, but could be
  refactored to use this same machinery for consistency).
- Eventually: per-channel fast-path overrides (Telegram could ship a
  fixed-button "what time is it" that hits the executor directly bypassing
  even normalisation).

Work spawned:
- None blocking. If a third executor wants in, promote the table to
  manifest fields (TOML `[fast_path]` block) and shrink `fast_path.py`
  to a loader.

Tests: `runtime/tests/test_fast_path.py` — 29 cases (positive matches IT+EN,
robustness to case/punctuation/apostrophes/whitespace, negative matches,
template rendering, error fallthrough, normalizer unit tests). Regression
suite: 594 passed (8 pre-existing accepted failures unchanged). Smoke
battery: still passes; queries `che ora e?` and `che data e oggi` now
short-circuit (fast_path step instead of PLANNER step) and the smoke
matcher (`tool_re=^get_now$`) keeps matching.
