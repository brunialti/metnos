# V26.2 minimal proof — static audit

Status: frozen pre-inference; independent review pending; no V26.2 model call has been made.

## Why this candidate exists

The frozen V25.3 first-attempt diagnostic showed that the binding gate needs only
`role`, `interpretation`, `speech_act`, `relation`, `subject_ref`,
`grammatical_person`, `time_scope`, and non-empty relation/subject/time evidence.
That post-hoc diagnostic classifies the 34 controls 34/34, with 9/9 positive hits
and zero negative leakage, but its original validator accepts 0/34 first attempts.
It is diagnostic evidence only and receives no acceptance credit.

The diagnostic's 34/34 is binding classification, not complete tuple agreement.
Against the frozen minimal semantic fixture, the legacy first-attempt frames are
24/34 exact. V26.2 therefore reports `binding_exact` and `signature_exact`
separately and never substitutes one for the other.

## Frozen contract

- Removed as redundant: `answer_mode`, `focus_role`, `focus_type`,
  `subject_type`, and `deixis_center`.
- Preserved: clause/predicate segment bounds and the seven-field minimal semantic
  signature.
- Evidence is an object with exactly the required keys `relation`, `subject`, and
  `time`. Each value is a closed tagged union selected by `kind`.
- Evidence variants carry only the fields needed by their type: explicit span,
  predicate morphology, clause semantics, zero-span context, tense morphology,
  or explicit absence (`none`). There is no free-text evidence channel.
- Unicode segmentation is the frozen V25.3 UAX #29 implementation; it uses no
  language or script table.
- The model sees no product action, capability, route, object taxonomy, gold,
  overlay, or source-language trigger list.

## Execution and scoring

- Exactly one native model call per case, fixed seed 92, temperature 0.
- No corrective retry and no validation feedback loop.
- Transport failure, schema failure, and semantic ambiguity are `NOT_EVALUATED`.
- Unsupported but structurally valid negative controls remain evaluable.
- Binding requires exact agreement with all seven signature fields plus non-`none`
  evidence for relation, subject, and time.
- Native acceptance requires 34 evaluable, 34 binding exact, 9/9 positive hits,
  zero negative leakage, zero ambiguity, and separately reports semantic signature
  exactness. One-call route integration remains blocked.

## Static results

- Mutation tests: 27/27 passed.
- Prompt surface/mixed lines: 0.
- Whole-query and n-gram overlap (n >= 3): 0 on frozen 109, 34, and 70 corpora.
- Prompt maximum-objective static eligibility: true.
- Runtime gold isolation: passed.
- Action ontology and product route in model contract: absent.

## Frozen artifacts

- Runner: `/tmp/metnos_v262_minimal_phase1_runner.py`
- Freeze: `/tmp/metnos_v262_minimal_phase1.freeze.json`
- Schema: `/tmp/metnos_v262_minimal_phase1.schema.json`
- Prompt: `/tmp/metnos_v262_minimal_phase1.prompt.txt`
- Mutations: `/tmp/metnos_v262_minimal_phase1_mutations_frozen.json`
- Opaque fixture: `/tmp/metnos_v262_minimal_phase1_controls_frozen.json`
- Gate: `/tmp/metnos_v262_minimal_phase1_preinference_gate.json`
- Contamination audit: `/tmp/metnos_prompt_contamination_audit.json`

The pre-inference gate is intentionally closed until independent review. The
review may open the gate but must not alter the frozen runner, schema, prompt,
fixture, mutations, or freeze lock.
