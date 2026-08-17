# V26.5.6.1 compact K1/34 author checkpoint

Status: **INDEPENDENT STATIC BLOCK; inference=false; transport=false; no
preflight gate; no external gate; no live run**.

This is the runner-only/offline-evaluator successor to the independently
blocked V26.5.6 bundle. It addresses the five static blockers from that review,
but its independent V26.5.6.1 infrastructure review is STATIC BLOCK and
therefore authorizes neither the GET preflight nor model inference.

## Frozen semantic boundary

The compact prompt, compact schema, typed registry, compact adapter, V26.5.5
facade, V26.5.4 worker and worker manifest are byte-identical to their pinned
predecessors. V26.5.6.1 changes only runner/evaluator infrastructure and
references all semantic bytes by SHA-256 in the author freeze.

The registry still has only 10 Phase-1 relations. K1/34 is consequently a
focused infrastructure/semantic probe, not evidence for the full 109 cases,
not a RequestAnalysis replacement and not a cutover authorization. General
coverage still requires a catalog-derived registry and compatible independent
oracle.

## Five closed author blockers

1. Runner and evaluator CLIs catch expected technical exceptions at their
   outer boundary and emit one hash/text-free JSON diagnostic line. They emit
   no traceback and do not depend on `sys.excepthook` or apport.
2. `--preflight` can produce an exclusive atomic `*_preflight.json`, but only
   after a distinct preliminary independent gate. That gate is absent here,
   so the author checkpoint permits no transport.
3. Before its first gold read, the offline evaluator closes the exact result
   shape for every accepted status, reconstructs the pinned UAX segmentation,
   and revalidates every expanded frame with the pinned registry and full
   validator. Empty-frame and extra-field mutations fail with zero gold
   reads.
4. The native HTTP director has no environment proxy handler and denies all
   redirects. Endpoints must be literal IPv4/IPv6 loopback HTTP addresses with
   an explicit port. Actual opener calls are counted; proxy destination,
   redirect and two-open fakes fail closed.
5. Both live and preflight entry points open the output parent by an
   `openat`/`O_NOFOLLOW` chain before any gate or transport operation. The
   stable directory descriptor is retained through temporary write, hard-link
   publication and directory `fsync`.

The compact model batch additionally has an exact pre-encoding cap of
7,609,728 bytes. It remains query-free, has `accuracy_claimed=false`, and is
never evaluated against gold in the live runner. The separate evaluator reads
a bounded batch and writes a distinct `*_evaluation.json` offline.

## Offline author verification

Run only:

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v26561/metnos_v26561_offline_selftest.py
```

Result: **34 asserted checks PASS** — the V26.5.6 21/21 suite plus 13 new
V26.5.6.1 checks. The complete in-memory path used one fake GET and 34 fake
POSTs (35 counted fake opens); actual network calls and model calls were zero.
The complete valid fake batch was 78,339 bytes including its final newline.
The suite also replays genuine valid, model-JSON-invalid and schema-invalid
result branches through the new pre-gold validator.

No `__pycache__` or `.pyc` is present. The measured test time on this host was
13.841 s; it is process/validation overhead, not a model-latency estimate.

## Byte-stable author artifacts

| Artifact | SHA-256 |
|---|---|
| `metnos_v26561_k1_runner.py` | `443fd60f41fd664401c30733b9677b9310eded6f02d8a6039ce767eb9bf0cd16` |
| `metnos_v26561_offline_evaluator.py` | `6e2486fa37f7d083fdd92688af6d9421df5f15aa8136868ecb8836e07bc53852` |
| `metnos_v26561_offline_selftest.py` | `1987d66da6d97b236d75dbd200c8617ab84f0cf1dd48c2598025d3dd52b401f7` |
| `metnos_v26561_author.freeze.json` | `f39140cc94e328e465e60e4c297ea8a77f4af57d810ce65c54b43693599ae316` |
| `metnos_v26561_author_pre_gate.json` | `2c77346f419fc0fa56717e15cc4f57f3a8b96874dde2e8a04b80d4efbfdd6ba2` |
| `metnos_v26561_author_selftest_result.json` | `71085e7bb5bdb2c2776140a64b487ceffcf8285c7842e1b489d80dc3718ad7bd` |

README is not part of its own hash table. La review indipendente è **STATIC
BLOCK**: i verifier operativi richiedono contraddittoriamente che i gate da
leggere siano assenti; quattro mismatch diagnostica/preflight/summary aprono
ancora gold; l'evaluator non impone il contesto interprete isolato né
ricalcola la membership dependency prima del nuovo exec. Report MD/JSON:
`metnos_v26561_independent_static_review.*`. Non creare alcun gate V26.5.6.1.
