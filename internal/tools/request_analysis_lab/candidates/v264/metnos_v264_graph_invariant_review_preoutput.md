# V26.4 — invariant and adversarial mutation review (pre-output)

Date: 2026-08-08. Status: independent, pre-inference, pre-candidate-output.
No model/server call was made and no V26.4 output was inspected.

## Scope and inputs

This review designs the fail-closed contract for a normalized relation graph
before the candidate exists. It covers clauses plus ordered atoms, the frozen
technical relation/reference registry, bindings `bound`, `unknown` and
`from_atom_output`, typed proof compatibility, coverage branches
`supported`/`typed_ambiguity`/`unsupported`, and a prior-id DAG.

Inputs read:

- `/tmp/metnos_v264_typed_registry.json`, SHA-256
  `ab198a54c028c37fde80e7a269d9578848cfebf670c9b8d4c7ff9cf0d39f9522`;
- frozen typed oracle overlay, SHA-256
  `e62d0605622e5f2c71e329e63448d29ad27fbfd3dafc64b880d4240cda9846af`;
- V26.3 independent rejection review, SHA-256
  `095259731746ddff6bf083db8f1551805345d4bfd2d425f1a7f906a2b0a36c65`.

This is a proposed acceptance contract, not acceptance credit. Once the
V26.4 draft appears, every item must be mapped to its concrete field path and
executed against its real validator.

## Required representation property

The graph must distinguish three things without duplicating grammatical
labels:

1. the primary atom of each semantic clause;
2. auxiliary producer atoms needed only to supply typed inputs to later atoms;
3. an atom output that is requested, consumed as a dependency, or both.

The cleanest derivation is: each supported clause has exactly one primary
atom; any preceding atoms owned by that clause are auxiliary producers. A
primary queryable atom under `open_question`, or a result-bearing imperative,
has one requested unknown. An auxiliary producer has one unknown output and
that output must be consumed later. A primary action atom has no unknown
argument. A prior primary output may also be consumed by later clauses, so
fan-out is valid. This avoids reintroducing `focus`, `answer_type`, or
`grammatical_person`.

If the candidate uses another representation, it must preserve these
distinctions explicitly or derive them unambiguously. Merely counting all
`unknown` bindings globally is incorrect for multi-action graphs.

## Invariants

### A. Envelope, clauses and coverage

- **A01 — closed variants.** Every object is closed. Fields from another
  coverage/binding/proof variant are rejected, not ignored.
- **A02 — local coverage.** Coverage is representable per clause (or by an
  equivalent complete alternative graph). This is required for a request that
  has one supported clause and one unsupported clause.
- **A03 — supported has atoms.** A supported clause owns at least one atom and
  exactly one primary atom.
- **A04 — unsupported has no atom.** An unsupported clause owns no relation
  atom. There is no `relation=none` pseudo-atom.
- **A05 — typed ambiguity is explicit.** It has 2..4 complete alternatives,
  never an opaque label or a single alternative.
- **A06 — materially distinct alternatives.** Alternatives differ in at least
  one decision-bearing fact after canonical removal of ids, proof ordering and
  equivalent proof choice. Proof-only and id-only duplicates are invalid.
- **A07 — alternative isolation.** Atom, clause and proof references cannot
  cross alternatives. Every alternative independently satisfies all graph
  invariants and safety obligations.
- **A08 — unaffected work preserved.** In a multi-clause ambiguity, each
  alternative preserves every unaffected supported clause/action. An
  alternative cannot gain validity by dropping a second action.
- **A09 — ordered clause ids.** Clause ids are unique, positive and follow the
  declared stable order. Source spans are valid. Nested quotation, condition
  and relative spans may overlap; a blanket no-overlap rule would be wrong.
- **A10 — total boundedness.** Alternatives, atoms, dependency depth and
  unsupported clauses obey the frozen limits. Total clauses and proofs also
  need explicit finite bounds; otherwise the current registry is incomplete
  as a resource bound.
- **A11 — no duplicated syntax fields.** `focus`, `focus_role`,
  `answer_type`, `subject_ref` as a parallel scalar, and
  `grammatical_person` are absent from the schema and rejected as additional
  properties. Semantic actor identity exists only in typed arguments.

### B. Clause role and speech act

- **B01 — registry matrix.** `main_request` permits only
  `open_question|polar_question|imperative`; `main_assertion` permits only
  `assertion`; `quoted_content|condition_content|relative_modifier` permit only
  `none`.
- **B02 — scope wins.** A quoted, conditional or relative atom cannot become a
  current request because its local grammar resembles a question/imperative.
- **B03 — action safety.** A primary `action_relation` in `main_request` must
  be imperative. An action relation may occur under quoted/condition/relative
  scope only with speech act `none`. The frozen registry does not authorize an
  action atom as an open or polar query.
- **B04 — queryable speech.** A primary `queryable_relation` can represent an
  open/polar request, assertion, embedded content, or a result-bearing
  imperative. Its unknown rules still apply.
- **B05 — one primary per clause.** Coordinated independent requests become
  separate semantic clauses. Two unrelated primary atoms hidden in one clause
  are invalid; auxiliary producers are not primary atoms.

### C. Registry arity, order and types

- **C01 — frozen member.** Every emitted relation is a member of the exact
  frozen registry. Namespaces are not guessed or extended at runtime.
- **C02 — exact arity.** Argument count equals the relation signature.
- **C03 — exact order and roles.** At index `i`, role equals slot `i`; checking
  only a role set is insufficient.
- **C04 — unknown type.** An unknown output has the canonical slot type.
- **C05 — bound reference.** A `bound` binding names one frozen reference;
  its registry type must be in that slot's `accepted_reference_types`.
- **C06 — derived output.** A `from_atom_output` source output type must be in
  that slot's `accepted_output_types`. If the list is absent, derived input is
  forbidden.
- **C07 — no silent coercion.** The two declared spatial-position adapters for
  `acl.share.resource` and `communication.send.content` are allowlisted edges,
  not permission to rewrite the source output type to the consumer slot type.
  Any supplied duplicate type must equal the value derived from the registry;
  preferably it is omitted.
- **C08 — exclusive binding variants.** `bound` carries only a reference,
  `unknown` carries no ref/source, and `from_atom_output` carries only a source
  atom/output selector. Hybrid variants are invalid.
- **C09 — no duplicate semantic truth.** Model-supplied role/type metadata
  that is derivable from relation+slot+registry must either be absent or be
  validated for exact equality.

### D. Unknowns and requested outputs

- **D01 — open question.** Each primary open-question queryable atom has
  exactly one requested unknown argument. A polar question has none.
- **D02 — primary action.** A supported primary action atom has no unknown
  argument; an unresolved effectful argument becomes a fail-closed typed
  ambiguity/clarification, not an executable supported atom.
- **D03 — result-bearing imperative.** A primary queryable imperative may have
  exactly one requested unknown (for example a selected set). It must not be
  rejected by a blanket `imperative => zero unknown` rule.
- **D04 — assertion/embedded terminal outputs.** Assertions and speech `none`
  have no unconsumed requested unknown. An embedded/auxiliary unknown is valid
  only when a later atom consumes it.
- **D05 — auxiliary producer.** A non-primary producer has exactly one unknown
  output and it is consumed by at least one later atom. Dead auxiliary atoms
  are invalid.
- **D06 — fan-out.** One valid output may feed multiple later atoms. Consumption
  multiplicity is not restricted to one.
- **D07 — requested and consumed.** A primary open-question output may also be
  consumed by a later clause. Consumption does not erase its requested status.
- **D08 — per-clause, not global.** Two independent open questions in one
  multi-query legitimately have two requested unknowns. A global singleton
  rule is invalid.

### E. Dependency DAG

- **E01 — source exists locally.** Every `from_atom_output` resolves to an atom
  in the same supported graph/alternative.
- **E02 — strict prior id.** `source_atom_id < consumer_atom_id`; self and
  future references are invalid.
- **E03 — array/id coherence.** Atom array order, unique ids and the causal
  order agree. Reordering bytes without ids, or ids without bytes, is rejected.
- **E04 — source output exists.** The selected source role/index exists and is
  an `unknown` output of that source atom. A bound input is not an output.
- **E05 — selector agreement.** If both role and index are present, both point
  to the same slot. Prefer one canonical selector to avoid contradiction.
- **E06 — acyclic.** The entire dependency graph is acyclic. Strict prior ids
  imply this, but a separate traversal must enforce maximum depth and detect
  malformed graphs independently.
- **E07 — bounded depth.** Longest dependency path is at most 4, including
  cross-clause edges.
- **E08 — no cross-alternative edge.** Equal numeric ids in another alternative
  do not resolve a reference.
- **E09 — no hidden dependency ref.** A frozen bound reference cannot be used
  as a substitute for a required derived output, and vice versa.
- **E10 — deterministic direct/dependency distinction.** A current-location
  producer consumed by another primary atom is a dependency, not a direct
  current-location answer, unless it is independently the primary output of
  its own open-question clause.

### F. Proof compatibility and grounding

- **F01 — proof per decision fact.** Every supported clause role, speech act,
  relation and binding has at least one permitted non-null proof. Time is an
  ordinary typed argument, not a detached scalar.
- **F02 — claim-family matrix.** Proof family is allowed for its exact fact
  class. `tense_morphology` cannot prove a relation; an interrogative
  construction cannot by itself prove a bound recipient.
- **F03 — reference-specific proof.** When a reference entry declares
  `proof_families`, the chosen bound proof is in that intersection. Generic
  argument permission cannot widen it.
- **F04 — explicit bounds.** Explicit segment spans are positive, in the
  supplied segment set, ordered and inside the fact's clause. Non-explicit
  proof variants do not carry fake span fields.
- **F05 — relation composition.** `from_atom_output` requires a
  `relation_composition` proof tied to the same source atom/output. Such a
  proof is forbidden on an unrelated `bound` input.
- **F06 — producer proof class.** A dependency relation/output uses the
  dedicated `dependency_relation`/`dependency_output` proof policy. A terminal
  requested unknown uses `unknown_argument`. If an output is both requested
  and consumed, it must satisfy the requested-output proof and the consumer
  edge independently.
- **F07 — no orphan or cross-fact proof.** Every proof points to one emitted
  fact; its atom, argument and clause selectors match that fact. Proofs cannot
  borrow evidence from another alternative or unrelated clause.
- **F08 — supported means grounded.** Missing, `none`, unknown proof kind, or
  incompatible proof makes a supported graph invalid and not creditable as a
  negative route decision.
- **F09 — quoted proof vocabulary.** `actor.quoted` currently names
  `quotation_context`, which is not listed in the global fact-family tables.
  The concrete proof union/validator must include it intentionally or the
  registry must be corrected before freeze.
- **F10 — no semantic retry repair.** A validator failure cannot be repaired by
  changing relation, role or arguments to agree with an earlier claim.

### G. Direct current-location binding

The direct predicate must return atom identities (not merely one boolean) and
match exactly a supported primary atom with:

```
clause_role = main_request
speech_act  = open_question
relation    = spatial.located_at
arg[0]      = bound(actor.current)
arg[1]      = unknown(spatial.position), requested by this clause
arg[2]      = bound(time.current)
all required proofs valid
```

It must reject identity, workflow/document position, filesystem/runtime
location, an explicit third party, current-context place, historical time,
polar/asserted/quoted/conditional/relative scope, and a current-location atom
that exists only as an auxiliary producer. A separate open current-location
clause remains direct even if its output also feeds a later action.

## Positive structural controls that must remain valid

These controls are as important as negative mutations; otherwise a validator
can reach 100% mutation kill by rejecting everything.

1. One exact direct current-location graph.
2. One polar `spatial.located_at` graph with all arguments bound and no unknown.
3. One queryable imperative `spatial.near` primary with one requested candidate
   unknown and a prior current-location producer for its anchor.
4. A fan-out DAG: one current-location output feeds `spatial.near`,
   `communication.send`, and `movement.destination` in later atoms.
5. Two independent open questions in different domains, each with one primary
   unknown; global unknown count is two.
6. A direct current-location open question whose output also feeds a later
   outbound action; direct target count remains one and the action stays
   separate.
7. Quoted or conditional action semantics (`speech_act=none`) plus a separate
   real main request.
8. A two-alternative typed ambiguity where both alternatives preserve an
   unaffected second action.
9. A diamond DAG and a depth-exactly-four DAG.
10. A mixed supported+unsupported multi-clause request. If this cannot be
    represented without deleting the supported atoms, V26.4 is not ready for
    the required historical multi-domain holdout.

## Adversarial suite and acceptance

The machine-readable proposal is
`/tmp/metnos_v264_graph_mutation_proposal.json`. It separates:

- validator mutations: must be rejected locally with a stable error code;
- classifier mutations: structurally valid graphs whose direct/dependency
  predicate must change deterministically;
- oracle mutations: omissions or wrong semantic choices that require a frozen
  expected graph, not a generic schema;
- static/freeze mutations: registry, prompt, schema, validator and gate-chain
  hash changes.

Required pre-run result:

- all positive controls accepted;
- 100% validator mutation kill, with no `NOT_EVALUATED` frame counted as a
  correct negative;
- 100% direct/dependency classifier expectations;
- every typed ambiguity pair materially distinct;
- repeated validation and canonicalization bit-identical;
- exact frozen hashes for registry, schema, prompt, validator, segmenter,
  runtime versions, controls, mutation suite, oracle, independent review and
  external run lock.

## Preliminary blockers in the registry/draft contract

1. `quotation_context` is reference-specific but absent from the global proof
   matrices. Resolve before schema freeze.
2. The spatial-position adapters into share/send are valid only as explicit
   registry edges. The graph must retain the producer output type and prevent
   arbitrary coercion.
3. No explicit total-clause or proof-count bound appears in the registry.
4. The exact representation of primary versus auxiliary atoms is not yet
   visible. Without it, unknown multiplicity, dead dependency detection and
   direct binding cannot be validated for multi-action inputs.
5. A top-level-only three-way coverage branch is insufficient for mixed
   supported/unsupported multi-domain input unless each alternative preserves
   clause-local coverage. This must be demonstrated by a positive control.

These are pre-output findings. They may already be addressed by the candidate
draft; they must be rechecked against its actual bytes.
