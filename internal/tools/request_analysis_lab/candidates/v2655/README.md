# V26.5.5 exact JSON-budget checkpoint

Status: **INDEPENDENT STATIC PASS; UNFROZEN; NOT LIVE-AUTHORIZED**.

V26.5.5 is a facade-only offline successor to V26.5.4. Its single behavioral
delta is an exact cumulative byte budget for the future canonical JSON request.
No V26.5.4 or earlier byte was modified. No server, model, network endpoint,
freeze, gate, or acceptance review was used or created.

## Exact pre-encoding budget

`metnos_v2655_facade.py` still exports only:

```python
evaluate(original_request, frame)
```

While it snapshots exact built-in JSON values, the facade counts the precise
UTF-8 bytes that `json.dumps(..., ensure_ascii=False, separators=(",", ":"),
sort_keys=True)` will later emit. The cumulative count includes:

- envelope and nested braces/brackets, commas, and colons;
- every occurrence of every value string, including shared Python string
  objects;
- all object keys;
- the complete `original_request` and fixed protocol version;
- JSON escapes for quotes, backslashes, control characters, and short escapes;
- the 1/2/3/4-byte UTF-8 width of every Unicode scalar;
- exact null, boolean, bounded integer, and finite-float spellings.

The counter stops at the first byte above 1,500,000, before `json.dumps` or
UTF-8 encoding can allocate the oversized serialized request. It rejects
Unicode surrogates, non-finite floats, subclassed containers/scalars, cycles,
excess depth/nodes, and the pre-existing per-string bound. For accepted input,
the single bounded encoding must have exactly the counted length or the facade
fails closed with an internal budget mismatch.

The snapshot no longer creates temporary tuples of whole lists/dictionaries.
The author over-boundary case represented 1,500,001 encoded bytes using shared
strings but reached only 16,740 tracked bytes of additional peak allocation
before rejection; `json.dumps` was replaced with a poison function to prove it
was not reached.

## Unchanged one-shot worker and semantics

There is deliberately no duplicate worker or manifest in this directory.
The V26.5.5 facade reads and pins the one durable V26.5.4 identities directly:

| Dependency | SHA-256 | Bytes |
|---|---:|---:|
| `../v2654/metnos_v2654_worker.py` | `c57c8ececdfffdc2380c7e318e1c9fe8070d074fadf641e0c458367704c20e22` | 19601 |
| `../v2654/metnos_v2654_runtime_manifest.json` | `88d7b0096ab2e0c76164c0c3c98705e68cadf06ef2bd5698d353f2139f552391` | 1426 |

This preserves the worker, internal protocol, adapter, validator, schema,
prompt, registry, audit hook, isolated interpreter launch, sealed memfd, and
semantic behavior byte-for-byte. A direct durable reference avoids an unused
manifest copy or two nominal worker identities that could drift.

## Offline author result

Replay from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B \
  internal/tools/request_analysis_lab/candidates/v2655/metnos_v2655_selftest.py
```

The archived PASS run reproduced:

- V26.5.4 author groups: **24/24**;
- compact mutation suite: **106/106**;
- contamination audit: **15/15** over the frozen 213-query corpus;
- V26.5.5 candidate semantics: **6/6** positive and **16/16** native negative,
  including multi-action, multi-domain, projection reuse, typed ambiguity, and
  mixed supported/unsupported coverage.

Focused budget results:

| Case | Canonical bytes | Outcome |
|---|---:|---|
| shared Unicode/escaped strings, exact under | 1,500,000 | passed facade; schema-invalid model frame as expected |
| same frame plus one ASCII byte | 1,500,001 | rejected before dump |
| escaped object keys, under | 1,499,893 | passed facade; schema-invalid as expected |
| escaped object keys, over | 1,500,475 | rejected before dump |

Short probes also covered original-request accounting, quotes, backslashes,
short and `\u00xx` controls, 2/3/4-byte scalars, finite float representations,
surrogates in request/value/key, NaN/infinity, subclasses, and cycles.

Seven complete ordinary one-shot evaluations measured median 88.832 ms,
minimum 83.102 ms, and maximum 93.333 ms on this host. This is a checkpoint
measurement, not a cross-host performance promise.

Archived result:
`metnos_v2655_author_selftest_result.json`, SHA-256
`8bfcb165cb2bfeb77102ec0b39e83b3a2729a87d167bea338e14f6c9bf4eab34`.

## Independent static review

The offline facade-delta review is **STATIC PASS**. Fresh runs reproduced
24/24, 106/106, 15/15, and candidate semantics 6/6 + 16/16. Independent
differential probes found zero mismatches across 1,004 canonical envelopes and
10,011 finite floats. Exact 1,500,000/1,500,001-byte boundaries, escaped keys,
UTF-8/JSON escapes, surrogates, aliases, cycles, subclasses, and bounded
pre-dump memory all behaved as declared.

Worker and manifest V26.5.4 remain byte-identical. See
`metnos_v2655_independent_static_review.md` and its JSON companion. This pass
does not create a freeze or authorize a gate, network, model, or live run.

## Threat boundary and next gate

The honest V26.5.4 threat model remains unchanged. The kernel, filesystem,
facade process, same-UID debugging boundary, `/usr/bin/python3`, and external
`jsonschema`/`regex` installations remain trust roots. This checkpoint does not
claim to resist arbitrary hostile code already running inside the facade
process. An independent static review must assess the exact counter and delta
before any future freeze; no live gate may be derived from this author PASS.

## Checkpoint artifact hashes

| File | SHA-256 | Bytes |
|---|---:|---:|
| `metnos_v2655_facade.py` | `fc34ffefc8c77c51a959a1fea5625ea896242afcfff0ed2a41e7f89c14d48d63` | 15053 |
| `metnos_v2655_selftest.py` | `91bb542d9f7e0c11e37f352c22a5df2d724e03f51d4bec6dd5429f1086e68426` | 14430 |
| `metnos_v2655_author_selftest_result.json` | `8bfcb165cb2bfeb77102ec0b39e83b3a2729a87d167bea338e14f6c9bf4eab34` | 2299 |

The README and review hashes are intentionally not self-referential. This
inventory is an unfrozen reviewed checkpoint, not a freeze manifest.
