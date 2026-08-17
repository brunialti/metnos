# V26.4.1 — independent runner-infrastructure review

Review date: 2026-08-08  
Scope: frozen V26.4.1 archive only  
Verdict: **STATIC PASS — NETWORK AND LIVE GATES REMAIN CLOSED**

This is an independent, offline review of the byte-stable bundle under
`internal/tools/request_analysis_lab/candidates/v2641/`. It authorizes neither
the standalone transport preflight nor model inference. No server was contacted,
no candidate output was read, and no operational gate or result was created.

## Frozen bytes reviewed

| Artifact | SHA-256 |
|---|---|
| freeze | `6f98647d6a9ea3b46ec521dca50e9f5bb00352d9b9360047219bd81976cd6eff` |
| runner | `6f215b04e6dd543b6de5193199957cb653599164f107177f7465fbef7dc95459` |
| infrastructure mutation probe | `9db8cb869160bc89a8655d3d2e1a003532a0d9bb605cf691b26f72ae8797ca0e` |
| infrastructure probe result | `f15fd3788f0763ebae05840be72f898de5ae41ad6725922b054c19f96b20d7fb` |
| mutations | `8c050ed42142b845089ab0fa35346189672088d31d3e71422971bd1076d74229` |
| contamination audit | `4d56e736c666dae096cfebdf5618daf4125c538c2c11cf216531fb2c196b3349` |
| author review | `ff1832953c591eda9449a21898d874d0579140119214272b3f472cf045d10e53` |
| author pre-gate | `b7a6a219d57b1fd866bd8407d3178d093ac944c5509c118461d565c50290007a` |

The archived runner and freeze are byte-identical to the declared stable
copies. `verify_freeze()` succeeds against the archive.

## Semantic immutability

V26.4.1 is a runner-only delta. The following semantic artifacts are
byte-identical to V26.4:

| Artifact | V26.4/V26.4.1 SHA-256 |
|---|---|
| typed registry | `448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f` |
| JSON schema | `06452e75f0a1ec7a42c9bf87dfae3da4e1528fc07b02d9276272a1e9ff0c7fec` |
| prompt | `2e60f19500b6d6193d53f483707533ca39ec1fee6a0256b18e6a96994f53406f` |
| frozen controls | `1dfbc5d2a7a83308a0f414d1b2c0b395f096d034471930fbaa886627aa79d954` |

The freeze and probe also establish exact equality for the source bundles:

| Source bundle | SHA-256 |
|---|---|
| Unicode segmenter | `4f21209070c9e4f97ed4dcbdb89cb400bcfbd11185f86407b15457e3b9524461` |
| schema/prompt generator | `ddb5cb1db75520ed275bc1686b7ebb26f3203009fb2ef80f919200d74752e0f2` |
| validator | `f7bd8511c3a2cc0527ecfcf01299fa728f931f222a5b8ac77aaee69a798f1ca8` |
| classifier | `979d75c6c932bba3a3df68c8eb4fdbf83fe3511b2acf3412abbb1a06b02bf99d` |
| evaluator | `f06b91064d16b8a31c3695d89b0fd127b1f8b7d720027d403f78392351bea83a` |
| request-body probe | `093ef172b58a81de826bbde0c99c013b68333ab6a2c012df0302a608b22063a3` |
| canonical registry | `77c0d5f6c398243eb343e469506a140f1307253ee17a360776ee65c1dc8be40c` |

In particular, the parent `evaluate_records` implementation is unchanged.
The V26.4.1 wrapper only requires a complete 34-case run and removes the old
synthetic `model_calls` field before publication. There is no semantic retry.

## Transport and batch semantics

The runner performs an inline transport preflight in the same process before
the first inference POST. It is a single `GET /v1/models`, without a request
body and without retry. Failure blocks every POST. A successful check must be
at most five seconds old immediately before the inference loop.

Fail-fast is restricted to infrastructure and protocol-envelope failures:

- socket/open/read failures, including `EPERM`;
- HTTP non-2xx responses;
- malformed response JSON document;
- malformed chat response envelope.

By contrast, malformed JSON inside `message.content` and validator-invalid
frames are model results. They are counted as `evaluated_invalid`, contribute
as failures, and do not stop the remaining controls. This preserves the
distinction between an unmeasured batch and a measured model error.

The counters follow the actual progress boundary:

| Counter | Increment boundary |
|---|---|
| `socket_attempts` | one transport attempt starts |
| `http_responses` | HTTP status is observed |
| `server_accepted_requests` | a 2xx status is observed, before body read |
| `response_json_documents_decoded` | the response body is decoded as JSON |
| `decoded_chat_responses` | the chat envelope and string content are valid |
| `decoded_frames` | model content is decoded as a JSON object |
| `evaluated_cases` | a valid or invalid model frame is evaluated |

Thus a read failure after a 2xx retains `server_accepted_requests=1`, while a
content JSON error retains all earlier counters and becomes an evaluated model
failure. A transport/protocol abort yields `NOT_EVALUATED`, publishes no
accuracy claim, and exits non-zero. A full 34-case run containing model-invalid
records remains an evaluated run.

## Diagnostics, privacy, and publication

The exception chain preserves `URLError`, its `PermissionError` cause, and
`errno=1`, rather than collapsing an `EPERM` into an apparent model error.
HTTP, response-JSON, chat-envelope, and content-JSON failures remain distinct.

Diagnostic bodies are bounded. Stored excerpts are short and redact the exact
query, its JSON-escaped form, authorization data, tokens, secrets, cookies,
passwords, email addresses, and long credential-like strings. Hash metadata
distinguishes a complete captured body from a truncated one. Validator-invalid
frames are represented by a hash and error codes, not persisted verbatim.

Result publication uses a same-directory exclusive temporary file, flush and
file `fsync`, no-clobber hard-link publication, directory `fsync`, and temporary
file cleanup. Existing results cannot be silently replaced. Preflight or live
errors exit non-zero; a completed evaluated batch may exit zero even when some
frames are model-invalid, which is the correct benchmark meaning.

## External preflight and live-lock contracts

The frozen runner structurally requires two distinct controls:

1. A separately reviewed transport-preflight gate authorizing exactly one GET,
   zero request body, zero retry, and zero inference.
2. A later live lock binding the exact freeze, runner, independent review,
   verifier, external preflight result and result hash. The result must be PASS,
   match endpoint/method/body constraints, be no older than fifteen minutes,
   and truthfully record one network/preflight call and zero inference calls
   before the lock.

The runner still repeats the same-process inline GET after those external
checks. Hash, endpoint, recency, extra-key, call-count, body, method, and prior
inference mutations are rejected by the offline probe.

Neither an external preflight gate nor a live gate exists in this reviewed
bundle. The author pre-gate explicitly has both transport-preflight and
inference permission set to false. This review does not change those values.

## Independent offline evidence

- archived runner self-test: **69/69**;
- archived infrastructure mutation probe: **72/72**;
- `verify_freeze()`: **PASS**;
- semantic artifacts and all seven semantic source bundles: **byte-identical**
  to V26.4;
- operational preflight lock/result, live lock, and K1 result: **absent**;
- network calls: **0**;
- candidate outputs read: **0**.

The inherited graph probe (125/125), oracle representability checks (34/34),
and zero-overlap contamination audit remain hash-bound evidence in the freeze;
they were not reinterpreted as live model performance here.

## Verdict and residual limits

**PASS for the frozen runner-only infrastructure delta.** The reviewed code
meets the required static properties: semantic byte preservation, inline
same-process preflight, precise and redacted failure taxonomy, truthful phase
counters, transport-only fail-fast, continuation on model-invalid frames,
non-zero infrastructure failure exit, no semantic retry, and atomic no-clobber
output.

This is not evidence of endpoint reachability, model accuracy, latency, or
runtime-cutover eligibility. Before any network access, a separate independent
verifier must hash-bind this report and the frozen bundle into a one-shot
transport-preflight gate. A subsequent live lock may be considered only after a
fresh successful preflight; it must also preserve the runner's mandatory inline
check. Until then, the correct operational state is **LIVE BLOCKED**.
