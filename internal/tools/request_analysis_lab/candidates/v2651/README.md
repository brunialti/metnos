# V26.5.1 — durable replay successor

Stato: **STATIC BLOCK — POLICY IMPORT DEL MANIFEST, NON FROZEN**.

Questa versione nasce dopo il BLOCK indipendente di V26.5. I byte V26.5 non
vengono modificati. Il primo checkpoint introduce:

- un manifest di dipendenze con soli path relativi e durevoli;
- un verifier stdlib-only che controlla tutti gli hash prima di qualunque
  futuro import dinamico;
- separazione esplicita fra input runtime-safe, evidence e sorgenti storiche
  non importabili.

Il secondo checkpoint aggiunge il validator self-contained. Un generatore AST
estrae meccanicamente dal runner storico le sole 23 funzioni necessarie a
schema, registry audit e validazione; il modulo risultante usa il registry
durevole, ne controlla l'hash prima del caricamento e non contiene prompt,
trasporto, retry o path temporanei. Il test offline è 11/11: due grafi validi
(diretto e dependency+consumer) e tre errori fail-closed, oltre a schema,
registry, import e isolamento.

Il terzo checkpoint materializza la suite compact-native. Le 68 mutazioni
storiche sono classificate una per una: 56 `retained`, 3 `obsolete` perché
`clause_id` è derivato, 9 `replacement` sul nuovo `source_ordinal` o sui campi
rimossi. La suite usa soltanto schema compatto, adapter canonico e validator
self-contained: 68/68 casi storici, 16/16 negativi nativi e 6/6 positivi,
totale 106/106. I positivi includono fan-out multi-azione, due domini, riuso
del risultato, ambiguità e richiesta mista. Suite, generatore fixture e
self-test compilano i byte verificati: zero letture di `.pyc` del repository.

Il quarto checkpoint rende durevole l'audit anti-contaminazione esteso. Un
generatore verificato ricostruisce dalle fonti canoniche un corpus di sole
query: 109 benchmark storici, 34 controlli mirati e 70 casi avversariali, per
213 totali. Il corpus non contiene gold, expected o output e ha ruolo
`audit_dataset`: non può entrare in prompt, runtime o proiettore. L'audit
riproduce byte per byte il risultato storico e passa 15/15 controlli: oltre ai
conteggi pinna e ricalcola contenuto e ordine delle tre sequenze; zero righe
linguistiche/miste e zero query o sequenze di almeno tre token condivise col
prompt. I moduli verificati sono compilati dai byte sorgente, senza `.pyc`
repository. Nessuna rete, modello o output del candidato è stato letto.

Il quinto checkpoint aggiunge il core candidato durevole. Verifica l'intero
manifest prima di eseguire moduli compilati dai sorgenti, pinna Unicode,
`regex` e `jsonschema`, conserva la richiesta intatta, costruisce il request
body e valida schema -> adapter -> validator. Self-test 96/96, inclusi
multi-azione e multi-dominio; replay byte-identico, zero letture di `.pyc`
del repository,
rete, trasporto o chiamate modello. Le quattro fonti canoniche delle 213 query
sono ora incluse nel manifest.

Questo chiude localmente i difetti tecnici storici B1/B2, ma non autorizza un
freeze. La review indipendente del delta corrente ha trovato un nuovo blocker:
core e verifier accettano un adapter dichiarato
`historical_source_only/import_allowed=false` e il core lo esegue comunque.
Serve V26.5.2, nuova review e soltanto dopo un wrapper separato. Nessun gate,
endpoint o run è autorizzato.

Checkpoint manifest: 34 artefatti, 38/38 controlli PASS, zero import dinamici
e zero rete. Gli input futuri sono privi di riferimenti temporanei;
runner/probe e auditor storici sono marcati `historical_source_only` e
`import_allowed=false`.

Hash checkpoint:

- manifest: `3f9c7f7de58bdc01d65a2ffa3048c1635b2afbad6d42e775e7e6615b2f8d0f8b`;
- pre-import verifier/result: `ba19c51e44014b0443914497915e1d531fe8e101a0180ba54fb583d895a703e4` / `119c9219d32fef1a9fbf48f667255033311397b23b3ba7127d6f4804502d5a52`;
- candidate core/result: `e212b78554dbf2d33fc5d966a7074655a9b0de0eab41c1d8f6cc15789cb5d227` / `d1476a1660ed81dd52cf3ef7b24ab9a679d107d62b54e2bb083d123efd696746`;
- validator: `b19b3aa781ab3ac44b973db2aed3f6da889c29703addfbdf8251a5c60ae97656`;
- generator: `e5a56091cedbfe4d9a3cdeb9358bf2bbc6a23c708817fbd24e254a636870bc45`;
- self-test result: `1b2021d9f50efa6e7867fe872c5eb4a378e9f714ba9a3d74f52ae35de0fcbda3`;
- mutation matrix: `69103b64374527feb0d40bc709043066e030fa81101651f724e52e79d5a9c1cc`;
- compact fixture: `8ce526f2384c4e1a9e1103c97ee3eb0af3f4bff1302cae621d61f5fe792d023e`;
- mutation suite: `c5f0d940d3feac82162d408e10663cbc8fa2b6db20450cc29c32b4554cddf4cb`;
- suite result: `3306d299b18726da1b903643d9c85763c05ec57680259c2ab05ddf0aa8bdb68f`;
- corpus 213 query-only: `237aae0006fd6926fc20e5f31aa50a4aa0bd8a2d10e7162a9ebe8424c6558149`;
- audit result: `50e1c5931fe60bfb1007bf5624b1f836214c43fab98194350b66921549487c92`;
- review indipendente pre-hardening: `014cfc55e651f363daf0898a05c01781f317a66a0cd57d90f5028c4bdd05fed9`;
- core environment: Unicode `15.0.0`, `regex 2026.3.32`, `jsonschema 4.10.3`.

La review pre-hardening è PASS, ma non copre i byte correnti.

## Review indipendente del delta corrente

Verdetto: **STATIC BLOCK**. Tutti i risultati dichiarati sono stati riprodotti
byte-esatti — manifest 38/38, validator 11/11, core 96/96, mutazioni 106/106 e
audit 15/15 — e non è stata trovata una regressione nello schema/adapter/grafo.
Il blocker è il mancato enforcement di `role/import_allowed` prima di
`compile/exec`.

- report JSON:
  `metnos_v2651_independent_delta_review.json`,
  SHA-256
  `d445a84e747868b0be7d13211d350b5d7d8099162eef694e1bc89e413c9be304`;
- report MD:
  `metnos_v2651_independent_delta_review.md`,
  SHA-256
  `5ee95bea686bcf2ea3b37acd12c526583c585e9710ccdee8d6ac284508284e31`.

Il self-pin di manifest e report resta intenzionalmente compito di un futuro
wrapper/freeze esterno; non è il blocker. Anche le cache `.pyc` ignorate non
sono state lette. Prossimo passo unico: V26.5.2 applica la policy per consumer,
aggiunge le mutazioni corrispondenti e riceve una nuova review. Fino ad allora:
zero freeze, gate, rete o inferenza.
