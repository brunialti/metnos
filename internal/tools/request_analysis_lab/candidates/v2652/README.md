# V26.5.2 — manifest policy hardening

Stato: **SOLO OFFLINE, STATIC BLOCK, NON FROZEN**.

Questo successore conserva invariati prompt, schema, adapter, registry,
validator, fixture e corpus V26.5.1. Respinge le mutazioni dichiarate di
`role/import_allowed`, ma la review indipendente ha falsificato il confine
diretto dell'accessor: una mappa post-verifica forgiata o promossa può ancora
portare sorgenti non autorizzati a `compile/exec`.

Il core e il verifier richiedono:

- campi manifest chiusi e hash SHA-256 validi;
- `import_allowed` booleano esatto per ogni entry;
- ruolo/permesso esatti per core, adapter, validator, registry, schema,
  prompt, fixture, auditor e verifier;
- accessor separato, risultato però non fail-closed contro mappe forgiate,
  elevation post-verifica e path assoluti;
- registry/schema/prompt/fixture restano dati non importabili nel manifest
  corrente, ma lo schema può essere promosso dal caller dell'accessor;
- manifest e report saranno pin-nati dalla futura radice esterna
  freeze/gate; non possono auto-pin-narsi senza circolarità.

Risultati offline:

- pre-import verifier: 43/43, 38 artefatti;
- candidate core: 114/114;
- dei 114: 96 replay V26.5.1, 14 mutazioni manifest respinte da core e
  verifier, 4 probe diretti dell'accessor respinti;
- replay del risultato core byte-identico;
- zero rete, modello, trasporto e letture di `.pyc` del repository.

La review trova due blocker causali:

- `_artifact_bytes/_source_module` accettano `entries` e ruolo atteso dal
  caller; evidence promosso e path assoluto non-manifest vengono eseguiti, e
  uno schema promosso raggiunge `compile`;
- `CandidateCore.evaluate_segments` resta pubblico e accetta segmenti solo-id
  senza query originale, testo o offset.

Il marker byte del verifier è corretto sui byte correnti, ma non è una prova
generale: nomi di moduli costruiti dinamicamente ne eludono la scansione.

Hash:

- manifest: `c0761aef08a120843a33dfc86756eed3caf823f0b1ffd26d7a0bcc140b31d56a`;
- core: `e62a1c3b24314736d8c5e8fcc0a52501bfe385bf2a8c17b65f423dabbf3a1ec3`;
- core result: `5cbcf034f43a0dca9807d1071e9d94d95555d40f9d2d8102e84940fd290eb9bc`;
- verifier: `3453a1eb3e8c81c454e31e723d7c900cc3aaf27859a448942858190b134d9cef`;
- verifier result: `057d9579b6552015981f1ac67d2b324f94897e5865bcda7e73653e705a4bbcc6`;
- review BLOCK V26.5.1 MD/JSON: `5ee95bea686bcf2ea3b37acd12c526583c585e9710ccdee8d6ac284508284e31` /
  `d445a84e747868b0be7d13211d350b5d7d8099162eef694e1bc89e413c9be304`;
- review STATIC BLOCK V26.5.2 MD/JSON:
  `6046dbe908ee9026f5c35a75401414a6daa48667bc56e9e80e05e7af5c5b7f7a` /
  `24839bac5499f9b2285fc16d7e395e03b87fc4b66723e411e09c36aa87283e59`.

Nessun freeze, gate, rete, inferenza o wrapper è autorizzato. Prossimo passo
unico: V26.5.3 offline, senza accessor che accetti mappe dal caller e senza API
pubblica `evaluate_segments`, poi nuova review indipendente.
