# V26.4 — final independent relation-graph review

Date: 2026-08-08. Verdict: **PASS for the relation-graph contract**.

This review is offline and independent of candidate output. It made zero
model/server calls and read zero V26.4 inference outputs. It does not by itself
authorize inference: the separate external gate lock is still absent and the
runner correctly rejects execution without it.

## Frozen bytes reviewed

- runner: `ea3fc46b50ce25a72652214b380e251f092ada87420a3e056b67eda60b24057d`;
- typed registry: `448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f`;
- schema: `06452e75f0a1ec7a42c9bf87dfae3da4e1528fc07b02d9276272a1e9ff0c7fec`;
- prompt: `2e60f19500b6d6193d53f483707533ca39ec1fee6a0256b18e6a96994f53406f`;
- frozen mutation bundle:
  `ff2c02177cab32eb8d84e6d0f714442f3161a9371f85189b6ae55589bbf34922`;
- contamination audit:
  `6845ef5ab1a3b7f7871909a285b070c292cfe97b2cf923471ebfeee99af78ff9`;
- author freeze:
  `83d3b4441d83d8f90e5eb591f9eeaf052885150e6806cd3ceefd046d948ccde6`;
- independent probe script:
  `9116a851241c5d1c03385a81a2eff23bb830599ea57e46abb2f6ddd1fd15e29a`;
- independent probe result:
  `7b0ff821783d9f5d071b4da0ccaa7f562f57afd47136cfc73c863a12663fb62c`.

`verify_freeze()` passes on these bytes. `verify_external_gate()` fails closed
with `external independent gate lock is missing`. The author marker has
`inference_allowed=false` and cannot satisfy the external-lock schema.

## Independent result

The final independent probe is
`/tmp/metnos_v264_independent_graph_probe.py`; its frozen result is
`/tmp/metnos_v264_independent_graph_probe_result.json`.

- 125/125 checks pass;
- 19 valid graph controls pass;
- 68 concrete invalid graph mutations are rejected;
- 19 direct/dependency/safety classifier controls pass;
- 4 semantic-omission mutations are correctly assigned to a frozen oracle,
  rather than falsely claimed as generic-validator checks;
- zero invalid/`NOT_EVALUATED` result receives correctness credit.

The candidate's own separate frozen suite is 69/69 and proves that all 34
typed-oracle expectations are representable, including direct binding,
dependency state, safety, evidence and coverage projections. This is an
offline representability check, not a model-accuracy result.

## Findings by required dimension

### Multi-clause, multi-action and multi-domain

PASS at the graph/validator level. Positive controls cover:

- two independent queryable domains, each retaining its own requested output;
- one current-location producer fanning out to a query, an outbound action and
  a movement action;
- a direct current-location answer that is also consumed by a later action;
- a quoted action plus a real main request, without executing or resolving the
  quoted dependency;
- an ambiguity whose alternatives both preserve an unaffected second action;
- mixed supported and unsupported clauses without dropping supported atoms.

Clause ids are unique, consecutive and source ordered; one projected primary
atom is allowed per semantic clause; auxiliary atoms must belong to a projected
clause. Alternative footprints must match, so an alternative cannot become
valid by dropping a clause.

### Relation arity, order and type

PASS. Relation membership and exact arity are registry-driven. Argument roles
and canonical types are not repeated in model output, eliminating a class of
contradictions. Bound references are checked against accepted reference types.
Derived inputs are accepted only through an exact registry `accepted_outputs`
edge; its coercion is uniquely derived and cannot be emitted or silently
changed by the model. Dependency outputs are restricted to the registry's
declared output slot, and action relations cannot masquerade as producers.

### Dependency DAG

PASS. Atom ids are consecutive and topological. A dependency edge must name a
strictly lower atom id and an actual output binding (or a prior projection
unknown). Missing, self, future, non-output and cross-alternative references
are rejected. Every auxiliary output must be consumed; fan-out is valid.
Depth is bounded at four.

The frozen registry currently permits no natural chain deeper than one. The
generic depth-four acceptance, depth-five rejection and diamond-DAG logic were
therefore exercised under an in-memory typed registry extension, restored
before every frozen-registry check. This tests the generic algorithm without
claiming that such a chain exists in the present ontology.

### Unknown multiplicity and speech act

PASS. A main action is imperative and has no unresolved unknown. A queryable
open question or result-bearing imperative has exactly one unknown; polar
questions and assertions have none. Counts are per projected clause, not
global, so two independent questions legitimately have two outputs.

The preliminary mutation proposal treated an unconsumed embedded unknown as
invalid. The frozen typed oracle shows that this was too strict: quoted and
conditional content may preserve one internal unknown without making it a
current request. The final validator and probe correctly accept that case.

### Role, scope and safety

PASS. Clause role and speech act use the frozen compatibility matrix. Quoted,
conditional and relative material cannot become a current request. Operational
current-location dependency and effectful safety obligations consider only
`main_request` projections. A scoped send/share is retained semantically but
does not request location resolution or outbound consent. A real main send or
share still produces the required consent obligation.

### Proof/reference compatibility

PASS. Proof variants are closed and claim-specific. Explicit spans are
positive and inside the owning clause; morphology anchors are inside it; other
proof kinds cannot carry fake spans. Reference-specific proof families narrow
the generic family. A bound reference cannot use an orphan
`relation_composition` proof. Output bindings and dependency edges require the
composition family. Missing, `none`, cross-clause span and wrong-family proofs
are rejected.

### Coverage branches and alternatives

PASS. `supported`, `typed_ambiguity` and `unsupported` are closed disjoint
branches. Unsupported clauses have no pseudo-atoms. A fully unsupported input
has clauses and no relation atoms; a mixed request preserves supported atoms in
the supported/ambiguity branch. Typed ambiguity requires 2..4 complete,
materially distinct alternatives. Distinctness uses the full graph, including
auxiliary producer semantics, while oracle comparison separately projects the
decision-bearing atoms.

### Direct current-location binding

PASS. The classifier returns atom ids, not only a global boolean. It requires
exactly the supported main open question
`spatial.located_at(bound actor.current, unknown position, bound time.current)`.
It rejects identity, third party, historical, polar and scoped variants. A
dependency-only current-location producer is not direct. A direct output may
also feed a later action: in that case the graph exposes both the direct target
and the dependency state.

## Issues found and corrected before freeze

The review found, and the final bytes corrected, all of these pre-freeze
problems:

1. mismatched output-allowlist field names;
2. output allowed in the wrong relation/slot;
3. missing action/queryable unknown rules;
4. incomplete clause-id and mixed-coverage checks;
5. inability to reuse a projection output in a later action;
6. loss of dependency information when a direct clause coexisted;
7. missing total resource bounds and registry self-validation;
8. an uncalled registry validator;
9. alternative distinctness that initially ignored auxiliary semantics;
10. dependency and safety classifiers that initially included quoted scope;
11. a proof vocabulary/coercion contract that was not fully closed.

No known relation-graph blocker remains in the frozen bytes.

## Deliberate limits and remaining gates

Four adversarial properties cannot be proven from an arbitrary graph alone:
omitting a source action, merging two source domains into the wrong relation,
dropping unaffected work not represented anywhere, and replacing a mixed
request with a whole-request abstention. They require comparison with a frozen
source-semantic oracle. The 34-case oracle covers its focused boundary; the
principal 109 suite and the later redacted historical multi-action/multi-domain
holdout remain mandatory.

Therefore this verdict means:

- the V26.4 graph schema, validator and classifiers are suitable for the next
  independently locked 34-case native run;
- it does **not** claim live accuracy, 109-case parity, historical generality,
  latency acceptance or runtime-cutover eligibility;
- no native run may start until the separate oracle review, verifier and
  external gate lock are present and hash-bound to this frozen bundle.
