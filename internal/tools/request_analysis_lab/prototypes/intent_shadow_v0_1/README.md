# Intent shadow prototype 0.1 — frozen registry and oracle

This directory contains the point-3 frozen registry and the completed point-4
canonical oracle for the standalone intent prototype. It is laboratory-only
and has no production import path.

Files:

- `intent_shadow_registry_v0_1.json`: reviewed shadow registry generated from
  pinned sources;
- `intent_shadow_registry_v0_1.freeze.json`: hashes of registry, verifier and
  normative contract;
- `verify_registry.py`: deterministic builder, verifier and mutation checks.
- `audit_oracle_sources.py`: deterministic check of whether the frozen sample
  has enough independent gold to build the complete oracle;
- `intent_shadow_oracle_source_audit_v0_1.json`: frozen result of that source
  audit. It records the pre-adjudication coverage gap; it is not an oracle;
- `intent_shadow_oracle_v0_1.json`: canonical oracle for all 120 frozen sample
  requests plus exactly 4 new controls;
- `intent_shadow_oracle_v0_1.freeze.json`: hashes of the oracle and its 23
  authoritative sources;
- `verify_oracle.py`: deterministic, fail-closed verifier for bindings,
  schemas, registry values, controls and freeze integrity;
- `test_oracle_mutations.py`: negative mutation suite and positive checks for
  semantically neutral source-list reordering.

Verify without model or GPU calls:

```bash
python3 -B internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/verify_registry.py
python3 -B internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/audit_oracle_sources.py --check
python3 -B internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/verify_oracle.py
python3 -B internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/test_oracle_mutations.py
```

Success requires `error_count: 0` and process exit code 0. Warnings remain
separate from errors and describe known limits rather than silently claiming
coverage that the sources do not provide.

The registry uses explicit classification. It does not infer controls or
barriers from names, capabilities, `SYSTEM_VERBS`, universal helpers, or the
verb-unique loader registry. In this first slice only `undo_last_turn` is a
system-control root and only `get/approval` is a barrier.

Do not hand-edit the generated registry or freeze file. Change the verifier's
builder, rerun `--emit-registry`, review the diff, rerun `--emit-lock`, and then
require the default verifier to return zero errors.

The oracle is complete and canonically frozen: 120 cases are split into 102
exact reviewer agreements and 18 authorized adjudications; root counts are 84
operation graphs, 2 system controls and 34 unrepresentable requests. The 34
existing controls plus 4 new, pairwise distinct controls total 38.

The review was double, independent and blind until both deliveries, but it was
performed by two AI reviewers and was not human review. Agreement among the
old c10/c11/control model arms was never converted into gold. The result is
limited to the 120 cases, 38 controls and shadow registry 0.1; the freeze is a
deterministic integrity seal, not an external signature or proof of universal
accuracy.

The final verifier returns `error_count: 0`. The official suite rejects
107/107 negative mutations and accepts 6/6 source-list reorderings that are
semantically neutral. An independent replay rejects 30/30 targeted mutations
and accepts 6/6 reorderings; the final adversarial plan passes 32/32 checks.
D-01 rejects duplicate JSON keys, D-02 rejects non-finite JSON numbers, D-03
uses exact JSON types and recursively closed metadata, and D-04 binds all six
authority/baseline lists as exact, unique, order-insensitive sets. Oracle bytes
did not change during D-01 through D-04.

Approved semantic sentinels remain: a compound with an indispensable
outside-registry clause fails closed as a whole; case 38 is temporarily
outside the registry pending a future text/PDF invoice pipeline; case 84 is a
single `get/images` view of materialized unified indices, not a persistent
corpus registry; case 113 treats structured-field selection and rename as a
projection in `read/events -> create/files`. A future persistent corpus
registry and the future `unavailable_not_deleted` status are not part of v0.1
expected values. The frozen catalog snapshot contains 96 executors and remains
explicitly stale against 83 current manifests; it is not silently replaced.
