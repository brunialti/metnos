# RM-0008 image indexing and relevance — candidate evidence

Status: the implementation is installed in release 46. The isolated
service-identity cold-start gate passed, but production release 45 exposed
an additional model-path defect. The correction and actual release-46 E2E
are tracked in [the release ledger](rm0008-release-20260915.md). This report
preserves the original candidate evidence and failed oracles; none is
relabeled as production evidence. The owner's photo index remains untouched.

## Original failure and separate causes

Turn `5485066430a24d27` asked `cerca foto di roberto con un computer`.
It was rejected with `match_source=lre`, `error_class=contract_unsupported`
before any executor step. This proves an admission/contract failure, not a
worker outage and not a reason to rebuild an existing index.

The subsequent disposable, signed HTTP/LRE fixture completed the old search
contract but failed the strict relevance oracle: of two photos of the same
synthetic person, both the computer and mountain were returned. Its preserved
artifact is `/tmp/rm0008-lre-candidate-bfx8v24_/result.json`. The expected result
was not weakened or padded with additional photos to make that case pass.

## Candidate design

The public build request is expanded through the registered
`images.index.v1` LRE plan. Discovery itself is background work, so submitting
a large archive does not synchronously hash every file in the chat process.

1. `discover` seals accepted local sources into immutable groups of at most 32.
2. `images.folder_classify` classifies the group's distinct folder labels.
3. `analyze` verifies and snapshots source bytes, then computes the existing
   EXIF, face, text and image features with one VLM call per changed photo.
4. `merge` produces child-reference manifests without copying vectors.
5. `publish` validates the accepted tree and expected count, streams a complete
   generation with bounded memory, and atomically replaces one active pointer.

The previous generation stays readable throughout failures and rebuilds.
Unchanged bytes are reused only after exact source verification; `force`
disables reuse. Content-addressed fragments, duplicate/path/hash checks,
generation compare-and-set and completed-publication reuse support retries.
No user index is deleted. The ordinary omitted-phase invocation cannot start
a monolithic process; the public read-only dry run remains deterministic.

Read-only model grants expose only declared local artifacts and a closed,
non-sensitive configuration projection. They do not expose an installation,
home directory, full model cache or account configuration. The derived index
is writable through the existing `metnos:cache` resource vocabulary. VLM
analysis cannot lazily start a process.

The host canonicalizes image-reader corpus arguments before entering the
sandbox. A physical alias therefore reaches the same logical index key with
an exact read-only directory grant, without mounting alias metadata or a
larger workspace. Folder cache keys include owner, language, prompt identity
and the resolved model-binding digest. Existing delivery keys are checked for
payload conflicts before same-scope coalescing; no new alias table or database
migration was introduced.

Local vision readiness now belongs to the host execution bridge after the
claimed unit has passed its execution fence. It verifies the frozen model
binding before and after the normal `virt.ensure_vlm_up` path and shares the
unit deadline. A process lock serializes instance startup across host lanes;
success is not latched permanently, so a later job can recover after idle
shutdown. Startup failure produces a runner/contract error with verified zero
model calls, never a fabricated result or usage record.

The existing launcher receives only the administrator-controlled artifact
projection from `/etc/metnos/vlm-startup.toml` or existing trusted startup
environment values. Ordinary model settings cannot choose shell commands or
arbitrary launcher variables. The helper resolves the selected binary's
sibling library directory in its child environment, preserving an existing
administrative library path, and uses the runtime's data/state directories
for logs and PID files. No installed binary or RUNPATH is rewritten.

## Relevance change and compatibility

The search retains the existing dense floor, vocabulary expansion and explicit
result-limit semantics. When sparse evidence exists, non-matching candidates
provide the adaptive background distribution instead of allowing the relevant
foreground to raise its own cutoff. Where the sample cannot support an
adaptive threshold, the existing expanded lexical evidence is required above
the dense floor. With no sparse evidence, the previous dense broad-query
behavior is retained. This does not force top-1 or introduce a new model.

The subsequent real cold-start run exposed a separate vocabulary problem:
the shared physical ancestor token `data` was expanded from `computer` and
gave the mountain photo its only lexical match. Shared path-only terms now
cannot consume expansion slots without descriptive support. An expanded
term supported by a caption still cannot admit another photo solely through
its shared storage path. Literal path queries remain supported. Metamorphic
tests move the same metadata under different root vocabulary and retain the
strict two-photo oracle, including the real captions and a genuine caption
term that must remain eligible for expansion.

Real BGE CPU tests cover the original two-photo corpus, two relevant results,
laptop synonyms, Italian/English terms, a broad query and an independent
32-photo corpus with eight relevant photos. Synthetic controls also cover a
constant background and a mostly relevant collection. These are regression
evidence for the tested corpora, not a guarantee against every false negative.

A separate pre-existing embedding discrepancy was observed: the installed
BGE ONNX model exports both three-dimensional `token_embeddings` and
two-dimensional `sentence_embedding`, while `bge_embedding.py` takes output 0
and performs masked-mean pooling. The real original similarities were about
0.755185 for the computer and 0.599582 for the mountain; broad vocabulary
similarities can also be noisy. Pooling was deliberately left unchanged:
changing query vectors alone would silently break compatibility with existing
index vectors of the same dimension. Any correction needs a versioned model
identity and an explicitly verified index migration/rebuild.

## Completed verification

- Builder, schema, prompt, VLM and simulated LRE suite: 234 passed.
- Builder presentation contract: 7 passed. Standard manifest validation has
  zero issues; its code digest and language-state bytes match the candidate.
- Real local BGE relevance plus existing reader and simulated LRE controls:
  43 passed in the earlier relevance gate. The latest real BGE, reader and
  boundary gate passes 47 tests, including storage-root metamorphism. No
  generative LLM/VLM was used in these relevance suites.
- Host readiness, deadline, process-lock, launcher layout, bridge, worker
  service and simulated image pipeline gate: 75 passed. It covers cold to
  ready, ready to idle-stopped to a new job, two host lanes, a distinct
  process, failed startup, child-only projection and untrusted profile
  refusal. These lifecycle tests simulate the launcher/health transport.
- Boundary, simulated LRE and sandbox regression suite: 89 passed, 1 skipped.
  It checks physical/logical alias identity, model-sensitive folder cache,
  existing request-key conflicts and exact grants.
- Earlier local-model probes executed real BGE inside bwrap with declared
  read-only artifacts, producing a `(1, 1024)` vector without broad mounts.

The simulated pipeline uses the real registry, compiler, inventory, SQLite
store, execution bridge and worker, but synthetic model responses and an
in-process executor transport. It indexes 33 synthetic photos in two groups,
stops after one accepted analysis group, recreates the worker/store, resumes
without repeating that group, publishes, and searches the complete generation.
It attests 33 VLM usage records, one completed folder classification and zero
model calls in discovery/merge/publication. This is not a real-model or
sandbox end-to-end attestation.

## Real end-to-end gate

The prepared `/tmp/rm0008-image-indexing-real.py` harness uses two neutral-named,
drawn images with no precomputed captions or index. It snapshots candidate
source, signs the fixture contracts with a fresh fixture key, keeps loader
verification and bwrap enabled, and uses existing local model services and
read-only model artifacts. It does not seed realistic user data, start a
Telegram delivery daemon or use production signing keys.

The required gate is: first HTTP search, automatic prerequisite admission,
real LRE phases and model usage, complete atomic index, second HTTP search
through the physical alias, and isolated outbox inspection. A second gate
requires the production service UID, initially cold VLM, the administrative
startup profile and a real outbox dispatcher with a simulated sender. The
sender cannot contact Telegram; it records the acknowledgement and verifies
that a second delivery pass cannot repeat it.

The first preflight stopped before spawning HTTP/worker processes or making
any model call: the configured VLM endpoint `127.0.0.1:8081` refused its health
connection. Evidence is retained at
`/tmp/rm0008-image-indexing-real-j2u4b64s/result.json`. The same non-sensitive
binding was verified in the local VLM configuration. The existing
`virt.ensure_vlm_up` path uses the installed `scripts/vlm_server.sh` helper,
not a catalogued systemd VLM unit. A governed cold start must be distinguished
from an end-to-end test with an already available model; the sandbox must not
regain process-start authority to conceal that lifecycle requirement.

The first real cold-start pipeline ran as the fixture owner, not service UID
995. Evidence remains at
`/tmp/rm0008-image-indexing-real-hfix7gfu/result.json`:

- First HTTP turn `25521bd160fa41e1`: automatic admission in 7.051 seconds.
- Workload `wrk_054e22db46814f80b17389f6d2df5ed6`: all five stages succeeded.
  Analysis, including the normal host cold start, took about 9.1 seconds.
- Real usage: one folder LLM call and two VLM calls. Discovery, merge and
  publication attest zero calls; no phase reports missing usage.
- Four actual bwrap invocations were observed. All 127 observed active
  pointers resolved to a complete two-entry generation; no partial pointer
  or mixed-generation observation occurred.
- The physical-alias second HTTP search reached the same logical generation
  in 6.397 seconds total (executor 1.205 seconds). It incorrectly returned
  both photos, so the report correctly remains `success: false`.
- The computer score was 1.703696 and mountain score 0.761373. On the exact
  indexed descriptions the expanded query included `data`; the mountain
  had no literal computer match and its only expanded BM25 term came from
  that common path. The new reader regressions cover these exact captions.
- Admission and completion owner events were persisted once and sent to the
  owner's event stream. Telegram rows remained pending with zero attempts;
  no Telegram delivery daemon was started in that run.

The UID-995 cold-start gate passed using two newly drawn synthetic images
and real model execution, without production signing or deployment. The
fixture rejects pre-injected model/bin paths, so the normal host readiness
hook consumed the root-owned administrative profile. Evidence is retained at
`/tmp/rm0008-image-indexing-real-_xt9a45q/result.json`; the administrative run
is `run-g_b_ds9a`.

- The VLM initially refused its health connection. The fixture started the
  existing installed `llama-server` through the normal helper, recording PID
  `908933`, UID `995`, executable
  `/home/roberto/llama.cpp-0827-v030/build/bin/llama-server` and start ticks
  `5831710`.
- First HTTP turn `dd5a037bd9504572` admitted the automatic prerequisite in
  9.997 seconds. Workload `wrk_dfb11367103f4ff1b248a65fcb2cb02a` completed all
  five stages on their first attempt.
- Real usage remained one folder LLM call and two VLM calls, with verified
  zero-call discovery/merge/publication and no missing usage records.
- Four real bwrap invocations and 127 complete active-pointer observations
  were recorded, with zero pointer errors.
- Second HTTP turn `31128fb33fb84265`, through the physical alias, took
  6.286 seconds and passed the unchanged strict computer-only oracle. The
  mountain image was excluded without adding photos to the corpus.
- The real outbox dispatcher used the canonical owner from the HTTP turn,
  persisted two acknowledgements through an explicitly simulated sender,
  and claimed zero messages on a second pass. Admission and completion were
  each delivered once in this simulated transport. No Telegram client,
  credential or real recipient was used.
- A subsequent scoped administrative inspection (`run-fxtkxdt0`) found no
  live process whose working directory belonged to that fixture realm. The
  recorded model PID/start-tick identity was no longer alive. This check read
  only the fixture report and process metadata; it did not stop a process.

This verifies the service UID and current installed assets in an isolated
candidate realm. It is not a production deployment, a fresh-install test,
an index of the user's own photos, or proof of a real Telegram delivery.
Crash/resume across multiple groups remains covered by the separate
33-image simulated pipeline, not by this two-image real-model run.

Actual model capacity on this host is one. Units are independently resumable
and ready work can use distinct declared resources, but there is no evidence
of parallel inference beyond that capacity. A single frozen executor model
contract currently reserves a VLM slot even for its zero-call phases.
