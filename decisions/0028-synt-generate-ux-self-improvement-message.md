---
id: 0028
title: Synt generate UX — explicit self-improvement message on first run
date: 2026-04-26
status: accepted
area: ux
related:
  - 0010
  - 0025
---

## Context

When the synt's reactive cascade falls into `generate` (ADR 0010),
the user-facing latency is on the order of tens of seconds: the wise
tier (ADR 0025) runs the spec + skeleton stage(s) over a frontier
model. On hardware running Gemma 4 26B, the canonical synthesis
benchmark produced `format_json` end-to-end in 65 seconds; Claude
Sonnet 4.6 was around 9–18 seconds; OpenAI GPT-5 around 35 seconds.
On any path, the user spends seconds where they expect milliseconds.

Without context, the user perceives the system as slow without
knowing why. The next request that re-uses the freshly synthesized
executor will be sub-millisecond — the cost is amortized — but only
*if* the user remains for the second request. A first run that
appears slow without explanation makes the user doubt the framing
of the system as reactive.

The decision was made on the night of 26 April 2026 to give the
generate path an explicit self-improvement message rather than
treating the latency as an apologetic pause.

## Decision

When `react()` falls into `generate`, after `compose` has failed
and before `generate` starts, Metnos emits a natural-language
message to the channel:

> "I am building a new component to extend my capabilities:
> `<name>`. The first time it takes ~30–60s; subsequent similar
> requests will be immediate."

(Italian rendering on Italian channels, unchanged in shape.)

Two notes on framing.

**Self-improvement, not delay.** The message says the system is
*growing its capability* — not "please wait while I think". This
matches the non-renunciation telos (ADR 0010, *coltivazione degli
strumenti*) and the feasibility frame of one-author-plus-AI (ADR
0019): the system honestly reports that it is building a new piece
of itself.

**Concrete numbers.** The message gives a time estimate ("~30–60s")
that matches the empirical wise-tier latency. Vague language
("a moment", "a bit") is less useful: the user calibrates to the
number, not to the apologetic tone.

The end-of-turn report says: *"I created `<name>`, it will be used
directly for similar requests in the future."*

The natural place for the message is `SynthProposal.rationale` (or a
new `user_message` field on the proposal); the channel module
presents it to the user. The wording lives in the central message
repository (ADR 0004) under `MSG_SYNT_GENERATE_FIRST_RUN`, allowing
i18n.

## Alternatives considered

**Silent latency.** Pro: minimal UI surface. Con: the user
experiences the latency as a system fault; without context, they
disengage. The cost compounds over future synth events. Rejected.

**Generic "thinking..." status.** Pro: trivial implementation.
Con: indistinguishable from any planner thinking step; loses the
self-improvement framing; users can't tell the difference between
a 5-second normal step and a 60-second synthesis. Rejected.

**Progress bar with stage indicators.** Pro: maximum granularity.
Con: heavy UI surface for marginal value; the user wants to know
"is this normal?" not "we are at 47% of stage 3 of 5". Adopted only
in the to-be-built progress prompt (`metnos_progress_prompt_todo`)
for *all* long operations; the synth message is the framing
sentence on top.

**Hide the generation entirely** (the user never knows the executor
was synthesized). Pro: maximum surface simplicity. Con: violates
the audit and transparency principle; the user cannot verify what
was added to the catalog. Rejected.

## Consequences

The `synt.html` chapter 7 (cascade + telos) is the natural place to
incorporate the message as a contract: the cascade emits not only
the proposal, but the user-visible string that explains it. The
non-renunciation telos finds its concrete UX form: "I am not
refusing, I am building."

The pattern interacts with ADR 0019 (single-author + AI feasibility):
the user's mental model becomes "Metnos is a system that grows".
This is not just messaging; it is the truthful description of the
system's behavior.

Open: the threshold above which the message fires. ~3 seconds is
short enough that a progress message looks paranoid; ~30 seconds is
long enough that explicit framing is clearly net-positive. The
default is "always when entering generate, regardless of latency",
because the framing is valuable even on a fast wise tier.

The pairing with the deferred `progress_prompt` work (separate to-do
in `metnos_progress_prompt_todo`) is direct: when the future
"I am working..." prompt exists, the synth message is one of its
canonical instances, with the self-improvement framing on top of the
generic "I am working" template.
