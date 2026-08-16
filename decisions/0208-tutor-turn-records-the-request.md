---
id: 0208
title: A Tutor turn records the request, like an engine turn
date: 2026-08-16
status: accepted
area: tutor | observability | privacy
related:
  - 0197
  - 0198
  - 0202
  - 0203
---

## Context

Tutor is an early exit placed *before* the engine: when its gate answers, the
request never reaches the planner. An admission it should not have made is
therefore a silent diversion — the user asked for an operation, received an
explanation, and the turn record shows no failure.

Until now the Tutor turn record deliberately omitted the request. The module
carried the rule in its own header ("deliberately no raw query in Tutor
telemetry"), and `user_query` was written as an empty string; the request
survived only as a normalized hash in `tutor_query_hash`.

The practical effect was that the gate could not be audited. Measured on the
turn history on 2026-08-16: **167 Tutor turns, 21 of them a declared gap, 13 of
those with `gap_reason=no_source`** — turns Tutor closed with no admissible
source at all, and with the engine never consulted. Nobody could tell *which*
requests those were. The defect they were hiding (see below) was found by
reproducing suspect phrasings by hand, not by reading the record.

Two facts framed the choice. First, the same store — one JSONL file per day
under `PATH_TURNS` — already holds the request of every *engine* turn of the
same user, in clear. Tutor was the only writer applying a stricter rule to the
same field of the same file. Second, a memory note dating from 2026-08-11 had
already made observability the stated precondition for touching the gate at
all: without it, no change could be measured before or after.

## Decision

A Tutor turn writes `user_query` with the request, in the same form and the
same field an engine turn uses (`runtime/tutor/telemetry.py::_prepare`). The
value is `TutorRequest.query_redacted`, which is the text that reached the
boundary after the sensitive-input refusal — the same text the engine would
have stored had the turn gone to the planner.

Three boundaries are unchanged and are part of this decision:

- **The F4 ledger keeps its rule.** ADR 0202 states that the learning ledger
  never keeps the query in clear, only a normalized hash, a vector when
  needed, and closed metadata. `record_turn_hashed` is untouched;
  `tutor_query_hash` remains in the turn record because it is the ledger key,
  not a substitute for the request.
- **A secret-shaped request still never reaches Tutor.** `contains_sensitive_input`
  refuses at the boundary before a `TutorRequest` exists, so no credential
  material can arrive at this field.
- **Retention is the retention of a turn, no better and no worse.** The Tutor
  record is now exactly as durable, and exactly as removable, as the engine
  record next to it. Whether the turn store should be pruned or purged per
  owner is a separate, pre-existing question that this ADR does not settle and
  does not worsen.

## Alternatives considered

**Keep the hash and correlate offline.** The hash is stable, so a suspected
phrasing can be hashed and matched against the record. This is what was
available, and it is why the defect stayed open: it answers "did the user ask
*this*?" but never "what did the user ask?". An audit needs the second
question, and a diversion nobody has guessed cannot be hashed.

**Record the request only for the outcomes that ended badly** (gap, technical
unavailability). Cheaper in exposure and it would have surfaced the 13 turns.
Rejected because it makes the record self-selecting: a wrong admission that
produced a confident, well-composed, and irrelevant answer is exactly the case
that would keep hiding, and `esito=fondata` is not evidence of a correct
admission.

**A separate audit store, retained briefly.** Cleanest in principle, and it
would have kept the minimization intact. Rejected as a second store holding
the same class of data as the first, with its own path, permissions, rotation,
and deletion path to keep correct — a durable maintenance cost to preserve an
asymmetry with no measured benefit.

## Consequences

The gate becomes auditable after the fact: `mode == "tutor"` rows now carry the
request together with `tutor_esito`, `tutor_gap_reason`, `tutor_source_ids` and
the score band, so an admission can be judged from the record rather than
reproduced by hand.

That audit immediately paid for itself. The same measurement supported the
companion change in `runtime/tutor/service.py`: with `gap_reason=no_source`
Tutor now returns `None` and hands the turn back to the engine, instead of
closing it with "I have no guide for this". A gap remains a declared outcome
where it is *earned* — the composer holds sources and still cannot answer.
«Dov'è il Duomo di Milano», one of the 13, is answered correctly by the engine.

The exposure delta is a Tutor request in a file that already contains the same
user's engine requests. No new store, no new path, no new permission.
`test_tutor_telemetry_records_the_request` pins the contract, including the
hash that must survive alongside it.

Reversal is one field in `_prepare`. The decision is recorded here so that
returning to minimization is a deliberate act with a known cost — losing the
ability to see what Tutor took — rather than a silent regression.
