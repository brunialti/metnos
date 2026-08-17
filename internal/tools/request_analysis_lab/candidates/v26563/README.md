# V26.5.6.3 compact K1/34 author checkpoint

Status: **INDEPENDENT STATIC BLOCK; inference=false; transport=false; no preflight
gate; no external gate; no live run**.

This byte-distinct successor closes the eight pre-gold bypasses reported by
the independent V26.5.6.2 review. It requires exact JSON scalar types,
reconciles both summary counter maps, reconstructs the POST request bytes and
SHA-256 from the pinned query, Unicode segmentation, prompt and schema, checks
all record/summary latency relations, and accepts only the exact canonical
literal-loopback endpoint serialization (IPv4 or IPv6, explicit port, no
userinfo/path/query/fragment).

HTTP response bytes are intentionally discarded. The persisted batch contains
no response-body length or digest, and the evaluator rejects such a field:
without the original body it would be an uncheckable observation, not proof.
Only request-body evidence is independently reconstructed. Prompt, schema,
typed registry, adapter, facade, worker and worker manifest remain byte-identical
to their frozen parents.

Offline self-test: **66/66 PASS** (50 inherited V26.5.6.2 checks plus 16
current checks), with zero network and zero model calls. The mutation suite
replays all eight causal bypasses plus scalar, endpoint and latency boundaries;
every invalid batch is rejected before any gold identity is opened. The fake
complete path uses one in-memory GET and 34 in-memory POSTs. No bytecode cache
or operational gate exists.

Run only the offline verification:

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v26563/metnos_v26563_offline_selftest.py
```

## Author artifacts

| Artifact | SHA-256 |
|---|---|
| `metnos_v26563_k1_runner.py` | `8571df498a58a9de9cc6703845a156e4d7a0dd679107f753a090f2cb9e509465` |
| `metnos_v26563_offline_evaluator.py` | `5046d26effe17e2b39a8fac41c1efa4850f8b69784a499fc6ff32d72506df3ac` |
| `metnos_v26563_offline_selftest.py` | `865d05ca73fa2fdbcb41aac3f7b31805ee3de140ff9e0e0fb40055199a3055c0` |
| `metnos_v26563_author.freeze.json` | `f3a655d1786b651da509c91a4cf0f9679f6e192885d744ffb100486c6bd224cc` |
| `metnos_v26563_author_pre_gate.json` | `66e53e250bb64d005287ef29d79cd4330208680c4c7ad2da8e7874f52a4f40fa` |
| `metnos_v26563_author_selftest_result.json` | `1496cb597700ee8e6a9c7dfcde7e9ec79da857d96595302f4ad3b4c8fbae9afe` |

README is not part of its own hash table. La review indipendente è **STATIC
BLOCK**: quattro interi 0/1 in campi booleani annidati superano la closure,
aprono `source_controls34` e terminano `PHASE1_EVALUATED`. La porta 0 è inoltre
accettata dai due parser; `raw_frame_sha256` e `model_content` sono osservazioni
non autorevoli ma non etichettate come tali. Report MD/JSON:
`metnos_v26563_independent_static_review.*`. Non creare gate V26.5.6.3. Il
registro resta limitato a 10 relazioni e non certifica i 109 casi generali.
