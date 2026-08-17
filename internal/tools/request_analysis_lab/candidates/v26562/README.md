# V26.5.6.2 compact K1/34 author checkpoint

Status: **INDEPENDENT STATIC BLOCK; inference=false; transport=false; no
preflight gate; no external gate; no live run**.

This byte-distinct successor addresses the three blockers in the independent
V26.5.6.1 review. The independent V26.5.6.2 review confirms the gate split and
the evaluator execution-context/tree fix, but finds the pre-gold closure still
fail-open. Prompt, schema, typed registry, adapter, facade, worker and worker
manifest remain byte-identical to their frozen parents.

## Three author fixes

1. Immutable author-byte verification is now separate from the author-time
   policy that requires operational gates to be absent. Preflight and external
   gate verifiers call only the immutable-byte function, then open and fully
   validate the expected gate. The author `--verify-freeze` path still enforces
   that both gates are absent at this checkpoint.
2. Before the first gold read, the evaluator now types and reconciles the
   complete result diagnostic with record counters, the complete inline GET
   preflight with its counters, aggregate inference counters, total transport
   attempts and all four latency summaries recomputed from record latencies.
   Frame, diagnostic, boolean-counter, preflight and summary mutations all fail
   with zero gold reads.
3. The evaluator requires `/usr/bin/python3` 3.12.3 with `-I -B`, Unicode
   15.0.0 and the pinned executable before batch processing. It recomputes the
   complete jsonschema/regex package and metadata-tree membership and hashes
   the pinned files before importing regex or compiling/executing validator
   bytes. A forged internally self-consistent file list fails before compile.

Semantic and scope limitations are unchanged: the typed registry has only 10
relations, so K1/34 cannot certify the general 109 cases or authorize legacy
cutover.

## Offline author verification

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v26562/metnos_v26562_offline_selftest.py
```

Result: **50 asserted checks PASS** — 34 inherited V26.5.6.1 checks plus 16
current checks. The current suite proves operational gate validation reaches
present synthetic bytes without bypass, rejects an unisolated evaluator,
rejects forged dependency membership before compile/exec and exercises nine
pre-gold frame/counter/preflight/summary mutations with zero gold reads.

The complete model path used one fake GET and 34 fake POSTs entirely in
memory. Actual network and model calls were zero. No preflight, gate or live
artifact was created, and no `.pyc`/`__pycache__` exists in this directory.

## Byte-stable author artifacts

| Artifact | SHA-256 |
|---|---|
| `metnos_v26562_k1_runner.py` | `fe33701bcb5483b8881fef2bd56428434e626142e6128e3a39d3bdfc6a71832b` |
| `metnos_v26562_offline_evaluator.py` | `0265597e01677a4d0f5d0a23bad9f6683ed53c8f9c6d636ccbe2ef076594bc9b` |
| `metnos_v26562_offline_selftest.py` | `dc62849c891b5b7fec01378c6f20d9b154d258d84ad72126835e293dd1f69bc2` |
| `metnos_v26562_author.freeze.json` | `da2ccdf5f04f22916fec8bd066cad5f01ba2fccb005cb21437767b209b2a5e88` |
| `metnos_v26562_author_pre_gate.json` | `dd85c20617ba130c30ef9f413539e9c5b059d36dd81d4853e6fe2941212eb9d7` |
| `metnos_v26562_author_selftest_result.json` | `d41e70a835f5949dcdf20cdec4ff0c75a50f8f2ae51be53f8f8d543655abf8a2` |

README is not part of its own hash table. La review indipendente è **STATIC
BLOCK**: booleani JSON equivalenti a 0/1, copie summary non tipizzate, un
endpoint userinfo non-loopback e prove diagnostiche non derivate superano la
validazione, aprono `source_controls34` e producono `PHASE1_EVALUATED`.
Report MD/JSON: `metnos_v26562_independent_static_review.*`. Non creare alcun
gate V26.5.6.2.
