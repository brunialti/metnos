---
id: 0226
title: Forward exit from a crossing that can never be attested
date: 2026-09-09
status: accepted
area: runtime | installation | ownership chain
modifies:
  - 0224
---

Implemented and tested; not yet exercised on the production chain.
Extends the Executor Birth admission chain of ADR 0224 with one terminal
outcome it lacked. It does not change what admission proves.

## Context

The first real successor release published its head and then failed its final
administrative attestation. The signed deployment descriptor named the managed
product interpreter as the administrative Python, because the release builder
derived it from `sys.executable` and the build had been run with that
interpreter. The descriptor also drives `@administrative_python@`, the
interpreter that runs the root birth helper: the verifier was right to refuse.

The descriptor is signed and immutable, so that crossing can never be attested.
The chain, however, opens release N+1 only after N reaches `PREFLIGHT_VERIFIED`
at sequence 6, and the coordinator record grammar assigns exactly one state per
sequence, so a refusal cannot be written into the journal. A single wrong field
in one signed release therefore blocked every later release permanently. The
machine kept running; it could no longer be updated.

## Decision

- The release builder derives the administrative interpreter from the fixed
  operating-system link, never from the interpreter that runs the build. The
  managed interpreter remains the service interpreter and is verified as a
  signed external target of the service catalog.
- The transition refuses, before reserving the edge and before stopping any
  service, a descriptor whose administrative interpreter is not the locally
  captured fixed one.
- A crossing that published its head and is proved unattestable is recorded by
  one immutable control document beside the transaction, which keeps its
  truthful last record at `HEAD_REQUIRED`. Nothing signed is rewritten.
- The abandonment reason comes from a closed catalogue, today
  `administrative_tcb_path_unsatisfiable`. It is proved inside the lock by the
  preflight itself, which re-authenticates the same signed history the
  attestation uses. Declared administrative paths must differ from the observed
  ones **and** every prerequisite hash must still agree: differing hashes mean
  the machine changed, which is not permanent and is not an abandonment.
- The proof names the crossing it examined; the writer refuses a proof taken
  about another request, because the prover selects through the required head
  while the writer reads the journal.
- The four independent readers of the predecessor rule — the release edge, the
  successor claim, the coordinator graph resolution and the preflight snapshot
  authentication — admit an abandoned predecessor on the same terms. Their
  agreement is itself asserted by a test that asks the two independent decoders
  the same question about the same bytes and requires the identical verdict.
- Startup selection is unchanged. An abandoned epoch keeps serving until its
  successor is attested; an abandonment removes nothing.

## Consequences

A mistake in a signed release is recoverable by moving forward instead of
rewriting history, and the failed crossing stays permanently on the record as
failed. The cost is one new control document and one new closed vocabulary in
the most delicate subsystem; the vocabulary must stay closed, and every new
reason must be a contradiction the machine can prove for itself.
