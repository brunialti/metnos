# V26.5.6.4 compact K1/34 author checkpoint

Status: **live K1/34 ESEGUITO e fallito semanticamente: 0/34 frame validi**.
Prima del live: STATIC PASS indipendente, preflight PASS, external gate
verificato.

## Esito del live e causa (post mortem offline, 9/8/2026)

Trasporto perfetto, semantica a zero: 34/34 HTTP 200, retry 0, errori 0,
p50 6,109 s e p95 9,447 s, ma **frame validi 0/34**. La condotta e'
schema -> adapter -> validator e si ferma al primo errore: **34/34 hanno
superato lo schema JSON**, 33 sono morti nell'adapter (32 `two semantic clauses
claim the same source span` + 1 `dependency has no projection with the same
clause span`) e **un solo caso ha raggiunto il validator** (`proof_family` +
`reference_type`).

Causa: la forma compatta ha tolto `clause_id` e promosso lo **span sorgente a
chiave primaria dell'identita' di clausola**. La decodifica pretende quindi span
iniettivo su proiezioni + clausole non supportate, e uguaglianza *esatta* di
span fra dipendenza e proiezione. Nessuno dei due invarianti e' esprimibile
nello schema JSON, quindi la decodifica vincolata non puo' imporli: vivono solo
nella prosa del prompt e abortiscono prima di ogni validazione semantica.

Non e' un limite di rappresentazione del gold: la fixture congelata attende al
massimo **una** proiezione per grafo (31 `supported` + 3 `typed_ambiguity`), e
il frame in forma gold passa schema e adapter. Il modello ha emesso ancore
semantiche in eccesso.

Il gate esterno esigeva `output_must_be_absent`: ora l'output esiste, quindi
**quel gate non riautorizza nulla**. `negative_usable_leakage = 0` e' un
passaggio **vacuo** (tutti i `predicted_*` sono `null`), non prova di rifiuto
corretto.

I frame grezzi **non sono ricostruibili**: `expanded_frame` viene persistito
solo sui casi validi, e `raw_frame_sha256`/`model_content` erano stati rimossi
per progetto. Quale coppia di ancore si sia sovrapposta resta indeterminato.

E' la **seconda volta**: nel live V26.4.1 venti casi su 34 fallivano solo su
`clause_ids` perche' schema e prompt non dichiaravano un invariante preteso dal
validator. V26.5 ha risposto **togliendo** `clause_id` e derivandolo dall'ordine
degli span: cosi' l'errore da locale e parziale (20/34) e' diventato globale e
totale (34/34). Derivare un identificatore non elimina il vincolo, lo sposta
dove lo schema non arriva.

Report completo, metriche e successore minimo: `metnos_v26564_live_postmortem.md`
e `.json`. Sonda offline riproducibile (zero rete, zero modello):

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v26564/metnos_v26564_postmortem_encoding_probe.py
```

| Artifact del live e del post mortem | SHA-256 |
|---|---|
| `metnos_v26564_live_k1_34.json` | `9c15cc295e9465d987c2fd6bcb86ed5d66ed7b28a00a160065b3f7e9de90fc04` |
| `metnos_v26564_live_k1_34_evaluation.json` | `c2602051b1b62daf1b93ec9a0693158c7a341cfe175131c09c43845d8b184522` |
| `metnos_v26564_live_postmortem.md` | `6089ab1672c49b1ac7143fbf1ca6f4bea076faefd446dd907366fd52aecd0a3b` |
| `metnos_v26564_live_postmortem.json` | `3dc12cc98084fa3467611d1c2390eeba4ff744a32b36ce62dda5da95e366bcad` |
| `metnos_v26564_postmortem_encoding_probe.py` | `9fd93cec37ee704c2ac848b2d91cfd68336e4c1a4ca735bd7b2e9a15a3e0ca93` |

I due output del live sono archiviati **byte-identici** agli originali in
`/tmp`, che restano intatti; i sei artifact congelati d'autore, i gate e i
verifier non sono stati toccati.

This byte-distinct successor addresses the complete independent V26.5.6.3
STATIC BLOCK report. Before any gold read, a recursive census assigns an exact
expected Python/JSON type to every scalar leaf in the complete batch; an
unclassified leaf fails closed. Expanded-frame scalars use the frozen technical
vocabulary and then still pass the complete jsonschema and registry validator.

The four nested boolean bypasses now require exact booleans, all four preflight
timestamps require exact nonnegative integers, and canonical IPv4/IPv6
loopback ports are restricted to `1..65535`. Future gate comparisons use
recursive type-exact equality. `raw_frame_sha256` and persisted
`model_content` bytes/digest were removed, like response-body evidence, because
the offline evaluator cannot reconstruct them. Request-body bytes/SHA remain
and are reconstructed exactly from pinned inputs.

Offline self-test: **85/85 PASS** (66 inherited V26.5.6.3 checks plus 19
current checks), zero network/model calls. It mutates all 148 observed normalized
scalar paths; valid and invalid batches contain respectively 2,028 and 1,994
scalar leaves. Every predecessor bypass, negative timestamp shift, port-zero
case and removed-observation reinjection fails before gold. Fake transport uses
one in-memory GET and 34 in-memory POSTs. No bytecode cache exists. At the
author checkpoint both operational gates were absent; the later independent
preflight-only gate is documented below.

Independent offline review: **STATIC PASS**. A separate probe reproduced
85/85 and freeze PASS, confirmed 8/8 semantic parent identities, independently
enumerated 148 normalized scalar paths (150 path/type pairs), rejected 594
wrong scalar types, and rejected 45 serialized branch/container mutations
before any GOLD read. Request proof is derived 34/34; ports 1 and 65535 pass
for canonical IPv4/IPv6 while 0 and 65536 fail in runner and evaluator.
Reports: `metnos_v26564_independent_static_review.md` and `.json`.

## Independent one-shot transport preflight gate

A separate post-review authorization and verifier now permit exactly one
transport preflight, not inference: `/usr/bin/python3 -I -B`, literal endpoint
`http://127.0.0.1:8080`, one `GET /v1/models`, zero request-body bytes, one
transport attempt, zero POST and zero inference calls. The exclusive output is
`/tmp/metnos_v26564_transport_preflight.json`; it is currently absent and must
remain absent until the command is deliberately run.

The offline verifier passed 75/75 mutations: 25 gate mutations were rejected
independently and by the frozen runner parser, 27 authorization mutations and
23 verification-result mutations failed closed. Its only request exercise was
an in-memory fake GET. Real network/server/model calls remained 0/0/0; no
candidate output was read or created. The external live gate remains absent.

```bash
/usr/bin/python3 -I -B \
  /opt/metnos/internal/tools/request_analysis_lab/candidates/v26564/metnos_v26564_k1_runner.py \
  --preflight \
  --endpoint http://127.0.0.1:8080 \
  --output /tmp/metnos_v26564_transport_preflight.json
```

| Preflight artifact | SHA-256 |
|---|---|
| `metnos_v26564_preflight_gate.lock.json` | `dd27373ee3b8342d134971fc4fd6bd804e8be9930d7412b6e8a73860148fa5ad` |
| `metnos_v26564_transport_preflight_gate_verifier.py` | `de70f1610c3e6619e10fe267d6ff46199ed8d008317de59a67ca3dd5f501bbaa` |
| `metnos_v26564_transport_preflight_authorization_review.json` | `18181b5798755d961c8b4641d69e69dd2b5cc771b6fa4fee08e6432042b47ee0` |
| `metnos_v26564_transport_preflight_gate_verification.json` | `d687b3b72a39f1bf609afef3b1b795080a16aa08f2ae93268084073a9b012a81` |

## External K1/34 gate — verified, not executed

The preflight is archived byte-identically as
`metnos_v26564_transport_preflight.json` (1,552 bytes, SHA-256
`7e85e2daca3bdd8b5daa9b2fe1d3e80eb76f566171ba98bd4038192c7b76735a`).
The gate binds endpoint 8080, K1/34, at most 34 model POSTs, zero retry, one
call per case and a mandatory inline GET. Offline verification passed 27/27
runner-parser mutations with zero network/inference. The exclusive live output `/tmp/metnos_v26564_live_k1_34.json` was produced
by that single authorized run and is archived here byte-identically.

```bash
/usr/bin/python3 -I -B \
  /opt/metnos/internal/tools/request_analysis_lab/candidates/v26564/metnos_v26564_k1_runner.py \
  --controls --endpoint http://127.0.0.1:8080 \
  --output /tmp/metnos_v26564_live_k1_34.json
```

| External gate artifact | SHA-256 |
|---|---|
| `metnos_v26564_external_gate.lock.json` | `f23972a2a1f1970c8b1a1612d4610bd34579e3fb6ab9c8cd2ec8422de552ea7d` |
| `metnos_v26564_external_live_gate_verifier.py` | `99bcefca0e4df4f40e9c85cc0f991d392f1ecbcbecdc67d992dfb7ba5bd979e0` |
| `metnos_v26564_external_live_authorization_review.json` | `516bb4e40250df8373486c9d3cde12d9498a15a833c0ce615b17809ad8cb9a8a` |
| `metnos_v26564_external_live_gate_verification.json` | `6b8a71c720f91c87321632a5758152717827aab94d2a3ccbe4625c8b458444f4` |

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v26564/metnos_v26564_offline_selftest.py
```

## Author artifacts

| Artifact | SHA-256 |
|---|---|
| `metnos_v26564_k1_runner.py` | `6fb5785d32b73f9d7dad4e917b211fdcdc12d9ca05088cc6d8ed9c4b167b146a` |
| `metnos_v26564_offline_evaluator.py` | `f2c4a900f3b1cc6d9341785f99f7386891dc490812e9330cb256401a0cb967a3` |
| `metnos_v26564_offline_selftest.py` | `ad9e4c0d401f2bdf6ff1bafad0df2d619e5d8aed42483fdeeecd0600602ca03e` |
| `metnos_v26564_author.freeze.json` | `d41a440551af5bfc430000e12bcdda2cbc89ca8c601b137d74b6e57f47f6cd56` |
| `metnos_v26564_author_pre_gate.json` | `365bc9637fb3c1d1d09812968b5ad85de0875b0b09fc3f21834c89fbca178236` |
| `metnos_v26564_author_selftest_result.json` | `dbccbbd61bff02501ed163337fe911e2524d5cb082afdea44762ba632a8a30c7` |

README, independent reports and post-review preflight artifacts are not part of
the author hash table. The frozen author artifacts above remained
byte-identical. The STATIC PASS alone authorized no transport; the distinct
gate above authorizes only its single GET preflight and no live inference.
The 10-relation registry remains Phase-1-only and cannot certify the general
109 cases or legacy replacement.
