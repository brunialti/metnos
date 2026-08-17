# V26.3 — causal delta and frozen-candidate review

Status: offline analysis only; zero server/model calls; no acceptance credit.

## Decision

Freeze and test V26.3 natively. The observed failure does not justify adding source-language lists or free rewriting. It justifies restoring the complete technical relation contract that V26.2 removed while retaining the minimal semantic output.

This is a justified experiment, not a predicted pass. V25.3 and V26.2 also differ in output scaffolding, so only the frozen native run can test the recovery.

## Measured delta

| Field | V25.3 first exact | V26.2 exact | Correct→wrong | Wrong→correct |
|---|---:|---:|---:|---:|
| `role` | 33/34 | 30/34 | 3 | 0 |
| `interpretation` | 33/34 | 19/34 | 15 | 1 |
| `speech_act` | 32/34 | 30/34 | 2 | 0 |
| `relation` | 32/34 | 17/34 | 15 | 0 |
| `subject_ref` | 27/34 | 27/34 | 2 | 2 |
| `grammatical_person` | 27/34 | 28/34 | 1 | 2 |
| `time_scope` | 30/34 | 29/34 | 3 | 2 |

Full seven-field tuple: V25.3 first attempt 24/34; V26.2 5/34.

Binary current-location gate: V25.3 first attempt 34/34. V26.2 has only 19/34 honestly supported frames; unsupported and ambiguous are abstentions, not successful negatives.

The largest regressions are relation and coverage state. V26.2 documented zero relation signatures with arity, while V25.3 and V26.3 document nine. The one validator-invalid V26.2 record is a separate duplicated-anchor problem.

## Causal split

- Prompt change: expected to recover relation, supported/ambiguous boundaries, role and speech act.
- Symbolic proof references: expected only to remove duplicated local anchors, token cost and structural invalids.
- Minimal schema: remains a live uncertainty because V25.3 exposed redundant focus/type fields. If V26.3 still loses relation choice, those fields were acting as model scaffolding even though they were not needed by the consumer.

## Candidate contract

V26.3 retains role, interpretation, speech act, relation, subject reference, grammatical person, time scope, and three typed proofs. It retains UAX #29 segmentation, one call, temperature zero, seed 92 and zero retries. Grammatical person is diagnostic output but is not duplicated in the route predicate because current_actor already carries the useful identity claim.

The prompt defines every retained enum and every relation signature symmetrically. It includes no source-language trigger, synonym list or example. The closed-world rule makes ambiguity and unsupported mechanically distinct.

## Static gates

- Mutation tests: 37/37;
- surface/mixed prompt lines: 0;
- whole-query and contiguous >=3-token overlap: zero on 109 + 34 + 70 datasets;
- inference calls before freeze: zero;
- native run required; post-hoc projection receives no credit.

## Alternative order

1. Run A, the frozen minimal atom with complete technical contract.
2. If errors are primarily clause/scope errors, try B: syntax facts and semantic projection as two sections of the same JSON response and same model call.
3. If ambiguity remains self-declared or a single relation is forced, try C: relation candidate algebra whose cardinality determines coverage.
4. If relation choice remains unstable across domains, move to D: typed fact table plus deterministic catalog unifier.

Do not run B, C and D opportunistically on the same 34-case design set. Freeze one hypothesis at a time, preserve failed artifacts and report coverage separately from accuracy.

## Frozen artifacts

- `/tmp/metnos_v263_phase1_runner.py` — SHA-256 `57580bfc8ee7576b1486bb6f9990d90926d0bc82aa8c4006c7058d8f6d8ea7ac`
- `/tmp/metnos_v263_phase1.prompt.txt` — SHA-256 `7ae33768167dd4803903008e1e1a690c99d6da7a4e718404be6d194526e74146`
- `/tmp/metnos_v263_phase1.schema.json` — SHA-256 `163aeea62eba817c3390d8b2ff7610bf82bb1b31ffd5f914274754ca4034d360`
- `/tmp/metnos_v263_phase1_controls_frozen.json` — SHA-256 `9196cd736eee25aeabb5b1c1b4803c8b63e71f8c32f63034bf3bfe336f4c32e8`
- `/tmp/metnos_v263_phase1_mutations_frozen.json` — SHA-256 `6c5608092b417d5d52a2643e93ffb8f2c34b448ae40ae2c0c5400a00cff2babc`
- `/tmp/metnos_v263_phase1_contamination_audit.json` — SHA-256 `4a3de10ed31eaa8e10049bc255632bd320df6bae366c1533e6395ac515743769`
- `/tmp/metnos_v263_phase1.freeze.json` — SHA-256 `80c1b4e70f1ef5cb75d1ddea1458f3a4934598729a03c3c552c562fe8eeaeb0c`
- `/tmp/metnos_v263_phase1_preinference_gate.json` — SHA-256 `2f7ddffd6cb37f0e7b380adcb9935dba6a3cddc4cfa990b0dec38701aa8ddde8`

Native gate: 34/34 valid and supported, 34/34 binding, 9/9 positives, zero negative leakage. Full semantic tuple is reported separately: the prior evidence establishes only 24/34 for V25.3 first attempt, not 34/34.
