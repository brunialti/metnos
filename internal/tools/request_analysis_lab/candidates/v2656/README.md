# V26.5.6 compact K1/34 author checkpoint

Status: **INDEPENDENT STATIC BLOCK; author inference=false; no external gate;
no live run**.

V26.5.6 connects the unchanged V26.5.5 one-shot facade to the compact V26.5
prompt/schema. It is infrastructure for the frozen Phase-1 K1/34 only. It is
not a full RequestAnalysis replacement and it does not certify the general
109-case suite.

## Closed live path

`metnos_v2656_k1_runner.py` uses one POST per case, seed 92, temperature 0,
zero retry, Unicode UAX #29 segmentation, the compact strict JSON schema, and
the exact V26.5.5 `evaluate(original_request, frame)` boundary. A GET
`/v1/models` preflight in the same process must pass before the first POST.
Transport/HTTP/outer-protocol failures stop the batch; model JSON or semantic
invalidity remains an evaluated model result and the remaining cases run.

The runner never opens `source_controls34`, fixture, oracle, or evaluator—not
before case 34 and not afterward. It writes only a query-free model-batch
artifact with expanded frames, exact phase counters, hashes, type/errno
diagnostics, and `accuracy_claimed=false`. It persists no response, exception,
or model-content text. A normal unexpected Python exception on a case returns
the already completed records so the caller can still write the partial batch
atomically.

The output writer is exclusive/no-clobber and atomic. A future native run also
requires a separate external lock binding the runner/freeze/author gate,
endpoint and output, a recent endpoint-exact GET preflight, interpreter
context, independent review, and gate verification. That lock is absent.

## Separate evaluation

`metnos_v2656_offline_evaluator.py` is a different offline command. It reads a
model batch once with a bounded no-follow descriptor, validates the exact 34
records, unique IDs, counter reconciliation, query absence, runner/freeze/gate
hashes and status closure before opening any gold. It then loads the pinned
Phase-1 fixture/oracle/evaluator and writes only a distinct
`*_evaluation.json` artifact containing the input batch SHA. Failure exits
nonzero without changing the batch or creating an evaluation file.

Illustrative replay after an independently authorized model batch exists:

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v2656/metnos_v2656_offline_evaluator.py \
  --batch /absolute/path/model_batch.json \
  --output /absolute/path/model_batch_evaluation.json
```

This offline evaluator authorizes neither live inference nor cutover.

## Author verification

The archived author self-test is **21/21 PASS**. It used an injected in-memory
transport only: 1 fake GET and 34 fake POSTs; actual network and model calls
were zero. It covers:

- deterministic compact request bytes and identical UAX segmentation;
- exact counters for 34 valid results and a mixed batch with model-JSON and
  schema-invalid frames that continues to case 34;
- zero postbatch/gold reads throughout the live runner;
- query-free, text-free diagnostics, including a facade exception containing
  a synthetic query fragment;
- fail-fast transport/outer protocol and recoverable partial output after an
  unexpected local case exception;
- accepted-response accounting on oversize/read failure;
- stale/wrong-endpoint/wrong-interpreter preflight rejection;
- raw frame handoff, multi-action, multi-domain and typed ambiguity replay;
- bounded no-follow gate references, single-snapshot swap, symlink and
  oversize rejection;
- separate evaluator success, pre-gold malformed-batch rejection, oracle
  failure recovery and exclusive output.

The complete fake 34 batch took 3.561 s; median per-case time was 103.710 ms.
These are local process/validation measurements, not model-latency promises.

Replay:

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v2656/metnos_v2656_offline_selftest.py
```

## Dependency and threat boundary

The freeze pins runner, V26.5.5 facade, V26.5.4 worker/manifest, compact
adapter/schema/prompt, validator, registry, query-only controls, evaluator,
fixture/oracle, V26.5.5 review, `/usr/bin/python3`, and a 51-module actual
non-stdlib inventory. Full file trees are pinned for jsonschema 4.10.3 and
regex 2026.3.32, including origins and transitive modules observed by the
representative worker/evaluator probe.

The OS/kernel, Python standard-library tree, same-UID process/debugger boundary
and filesystem remain explicit trust roots. There is no claim against kernel
or already-compromised same-UID code.

The typed registry contains only 10 relations: location/identity, filesystem,
runtime host, proximity, share/send, movement, workflow and document position.
Therefore even a future perfect K1/34 result would remain Phase-1 evidence. A
successor needs a catalog-derived registry with general coverage and a
compatible independent oracle before any 109-case or legacy replacement gate.

## Byte-stable author artifacts

| Artifact | SHA-256 |
|---|---|
| `metnos_v2656_k1_runner.py` | `ffb55a27da2167dc1ecb3cf878bf4f949380b81ffff1890dbe7a5f7ee4adf225` |
| `metnos_v2656_offline_evaluator.py` | `d2f027b00bc634bb53e85a4dfe622dc9f25d997d6d6490a3a37f798d22165803` |
| `metnos_v2656_offline_selftest.py` | `7b3191542cace8c827e966e5cf787fecd99a821be58583845999ade1818abd3c` |
| `metnos_v2656_author.freeze.json` | `d17cddcb1f4aae770f9288294c17a2b49cb7321916dfa61bd33ba954cfe813f5` |
| `metnos_v2656_author_pre_gate.json` | `3a92d1b07c1c8d7eb78fc3441dd62ac3bbec0589dd839aabb04944cf3446aa1e` |
| `metnos_v2656_author_selftest_result.json` | `4946be03f3c27843d908cd1423f5017959f710019448cfd1b7456ad67f3ebd77` |
| `metnos_v2656_phase1_evaluator.py` | `fdee11190fe913f26069ff7627a62d7b143b28c911c2ea22a6afee91632126c7` |
| `metnos_v2656_runtime_controls34.json` | `23e6e39df4d697a36c81fd911a0f3aa17e3ccaeeb126fcb2bcd2bcd5b3a191e1` |
| `metnos_v2656_python_dependency_tree.json` | `248b6a6877ad98d361890b50eb91664e3fec477ab6a8376f5dd48c0cb3e9b156` |

README is not part of its own hash table. La review infrastrutturale
indipendente è **STATIC BLOCK**: CLI senza boundary sugli errori attesi e senza
producer `--preflight`, `urlopen` con proxy/redirect impliciti e contatori non
fedeli, validazione full-frame mancante prima delle aperture gold, writer
runner non ancorato a dirfd no-follow. Il limite del batch corrente è invece
staticamente sotto 7.609.728 byte serializzati. Dettagli e probe fake-only in
`metnos_v2656_independent_static_review.md` e `.json`. Nessun transport smoke,
external gate, inference o acceptance run è autorizzato.
