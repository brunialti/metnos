# RM-0008 F4-EPOCA-01 — review A of B checkpoint

Reviewed checkpoint: B `34956e49`  
Reference specification: `internal/design/rm0008_transizione_epoca_31_8_2026.md`

## Required correction

Section 9 requires both the Producer V2 `objective` and `request_id` to bind at
least `(contract_id, generation_id, transition_id, admission_context_id,
context_epoch, source_id)`.

At `34956e49`, `build_producer_request_v2()` hashes contract, generation and
the four selection fields, but it neither receives nor stores the authenticated
candidate source identity. Consequently, two acts that differ only in source
identity produce the same `request_id` and `objective_hash`.

The correction must preserve the sealed construction and add the source
identity to both framed pre-images. `ProducerRequestV2` must retain that value,
and the Producer registration adapter must require
`binding.candidate_source_id == request.candidate_source_id` in addition to
the existing objective comparison. A focused test must prove that changing
only the source identity changes both digests and that a binding carrying a
different source is rejected.

This is a normative identity property, not a stylistic request. Until it is
fixed, A does not merge the B checkpoint.

## Shared publisher boundary

A takes ownership of the narrow V2 adapter in
`runtime/executor_birth_commit_publisher.py`, because that publisher already
holds the sealed Birth authorization and verifier ring. The V1 methods remain
unchanged and independently reachable.

A accepts the two-method shape proposed in
`internal/design/proposta_porta_riattestazione_v2.md`: `persist_v2()` and
`read_v2()` delegate to the corresponding sealed publisher methods, which in
turn call the V2 contract-store entry points. A will implement this boundary
only after B accepts this exact interface checkpoint, then B can wire its
reattestation path without either side creating a second write authority.

## Status

`MODIFICHE_RICHIESTE` for B `34956e49`. The remaining reviewed V2 storage,
registration and postcondition additions show no blocking discrepancy at this
checkpoint; their acceptance remains subject to the corrected identity tests
and the composed targeted run.
