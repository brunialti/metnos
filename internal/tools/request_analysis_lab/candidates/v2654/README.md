# V26.5.4 one-shot worker checkpoint

Status: **INDEPENDENT STATIC BLOCK; UNFROZEN; NOT AUTHORIZED**.

This directory is a new offline successor to V26.5.3. It closes the specific
same-process authority leak by moving verified semantic-source execution into a
fresh, one-request worker. No model, server, or network endpoint was called.
No freeze, acceptance review, external gate, or live authorization was created.

## Boundary and API

`metnos_v2654_facade.py` exports exactly one supported API:

```python
evaluate(original_request, frame)
```

The facade accepts only an exact built-in JSON snapshot. It rereads the fixed
worker with repository-relative `openat` traversal, `O_NOFOLLOW`, regular-file
and size checks, SHA-256, and before/after `fstat` identity checks. Verified
worker bytes flow directly into a sealed `memfd`; the facade starts exactly:

```text
/usr/bin/python3 -I -B /proc/self/fd/<sealed-fd>
```

The child receives a minimal explicit environment and one closed JSON request
on stdin, emits one closed JSON response on stdout, and exits. Extra authority
fields such as source, path, entry, module, injection, or id-only segments are
not protocol members.

The worker is deliberately non-importable. Its only execution entry is the
zero-argument `run_once()`. Adapter and validator bytes are selected only by
fixed local identities. There is one lexical `compile` call and one lexical
`exec` call, both inside `run_once`; neither source nor an injection map is a
function parameter or module attribute. The registry assignment is fixed to
the freshly verified registry bytes.

The compact frame is checked against the pinned JSON Schema before either
verified Python source is compiled. Source ASTs must match exact import
surfaces and reject reflective globals/builtins/dunder/subscript paths. A
worker audit hook denies network, subprocess, shell, ctypes, exec, fork, and
forkpty events. The worker has no model or transport client.

## Semantic bytes reused unchanged

The durable runtime manifest is
`metnos_v2654_runtime_manifest.json`, SHA-256
`88d7b0096ab2e0c76164c0c3c98705e68cadf06ef2bd5698d353f2139f552391`.
It pins these existing bytes without copying or modifying them:

| Identity | Durable source | SHA-256 | Bytes |
|---|---|---:|---:|
| adapter | `../v265/metnos_v265_compact_adapter.py` | `3375207bd7bbf6d99098d0ec2066d0c9f3eb6b76b34f1ccd450abaf89abecef1` | 5600 |
| validator | `../v2653/metnos_v2653_injected_validator.py` | `66760987f5d2335c79114632affa5c04ffecd9ffe5f8e1a7adec7bc50b4b793d` | 31627 |
| registry | `../v2641/metnos_v2641_typed_registry.json` | `448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f` | 9456 |
| compact schema | `../v265/metnos_v265_compact.schema.json` | `76cd30411704c780e5866fd165f945bd3e773f0dab1b359e875d41f6eb54a921` | 13751 |
| compact prompt | `../v265/metnos_v265_compact.prompt.txt` | `893db06f910acd48f8929251a89afaaaf630632b90589e0863948792643a7418` | 8154 |

No V26.5.3 or earlier file was edited by this checkpoint.

## Author self-test

Replay from the repository root; it requires no pre-existing `/tmp` artifact:

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B \
  internal/tools/request_analysis_lab/candidates/v2654/metnos_v2654_selftest.py
```

The PASS run covered 24 test groups:

- all 6 positive controls and all 16 native negatives from the durable V26.5.1
  fixture, using one real 80-segment synthetic request;
- multi-action/fan-out, projection reuse, multi-domain, typed ambiguity, and
  mixed supported/unsupported coverage;
- caller builtins/module/environment monkeypatches, closed extra-field and
  id-only protocols, exact-JSON rejection, and schema-before-compile;
- singular lexical sinks, fixed registry injection, source AST guards,
  transport import allowlists, and the worker audit-hook deny set;
- worker import refusal plus forged-worker-hash and symlink negatives.

The same run measured seven complete fresh-process evaluations: median
88.263 ms, minimum 84.453 ms, maximum 89.597 ms. These are local checkpoint
measurements, not a latency claim for another host. The forged-hash test creates
and removes its own ephemeral scratch directory; replay does not consume any
external temporary artifact.

Result: `metnos_v2654_author_selftest_result.json`, SHA-256
`475cd6332be2d50b2805952455ca4383e16a5492e7b15d7573930323b49cef14`.

## Independent reliability review

The offline review reproduced **24/24**, **106/106**, and **15/15**, but found
one blocking memory-bound defect. The facade snapshots the whole frame and
materializes `json.dumps(envelope).encode("utf-8")` before applying its
1,500,000-byte request limit. Per-string and node limits do not impose a
cumulative JSON/UTF-8 allocation budget, and object keys are not counted.

Verdict: **STATIC BLOCK**. The minimal successor must count exact canonical
JSON/UTF-8 bytes incrementally during the snapshot, including keys, escaping,
envelope constants, and `original_request`, and reject before `json.dumps`.
See `metnos_v2654_independent_static_review.md` and its JSON companion.

## Honest threat model

This design isolates untrusted request/frame JSON and prevents repository-file
substitution from reaching the semantic `compile`/`exec` sinks after a pin or
TOCTOU check fails. A normal caller cannot pass source, paths, module objects,
entry maps, or injections through the facade or worker protocol. A fresh
isolated interpreter also prevents ordinary caller `builtins`, `sys.modules`,
`PYTHONPATH`, and `PYTHONINSPECT` monkeypatches from crossing the boundary.

Python module privacy is not a security boundary. V26.5.4 therefore does **not**
claim resistance to arbitrary hostile code already executing inside the
trusted facade process: such code can rewrite function globals/code, replace
`os`/`subprocess`, inspect memory, or attach a same-UID debugger. The kernel,
filesystem, `/proc`, `/usr/bin/python3` (Python 3.12.3), and the facade bytes are
trust roots.

The `jsonschema` 4.10.3 system installation and `regex` 2026.3.32 user-site
installation are version- and origin-pinned at runtime but their full package
byte trees and the interpreter binary are not frozen here. They remain an
explicit external trusted-environment dependency. Any future freeze must bind
that environment (or vendor and hash it), bind facade/worker/manifest hashes,
and pass an independent static review before any gate or run can exist.

## Checkpoint artifact hashes

| File | SHA-256 | Bytes |
|---|---:|---:|
| `metnos_v2654_facade.py` | `cfb7705e1a3e47fbeea3ca4a9346c8fc6c533bed50ab14360498ace1dbe45692` | 12481 |
| `metnos_v2654_worker.py` | `c57c8ececdfffdc2380c7e318e1c9fe8070d074fadf641e0c458367704c20e22` | 19601 |
| `metnos_v2654_runtime_manifest.json` | `88d7b0096ab2e0c76164c0c3c98705e68cadf06ef2bd5698d353f2139f552391` | 1426 |
| `metnos_v2654_selftest.py` | `bb0fcfb1df49d194b5d778fc968854e4163b4f60e03302ec0cf9c6835dac5082` | 19690 |
| `metnos_v2654_author_selftest_result.json` | `475cd6332be2d50b2805952455ca4383e16a5492e7b15d7573930323b49cef14` | 1702 |

The README and review hashes are intentionally not self-referential. This is
a blocked checkpoint inventory, not a freeze manifest.
