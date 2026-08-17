# Audit offline indipendente candidate v0.2 — ciclo 2

Data: 2026-08-13  
Perimetro: gli stessi 36 controlli finiti del ciclo 1 su `candidate_v0_2/`.
Sola lettura del codice, degli artefatti e dei test; nessuna GPU, rete, chiamata
a servizi, modifica al candidato o commit. `self_review.md` non è stato usato
come autorità. L'unica scrittura è questo referto.

## Esito

**PASS — 36/36 controlli.**

- test ufficiali: **30/30 PASS**;
- controlli aggregati indipendenti: **6/6 PASS**;
- difetti bloccanti aperti: **0**;
- difetti non bloccanti trovati: **0**.

I due difetti bloccanti del ciclo 1 sono chiusi. Il candidato è pronto come
base offline per **progettare** una futura misura. Questo verdetto non
autorizza la misura, chiamate a modelli, GPU, rete o servizi.

## Chiusura blocker 1 — trasporto finto interno e sigillato

**PASS.** `analyze` non riceve più un oggetto di trasporto: riceve soltanto una
coda di byte finti. `StructuredClient.from_fake_responses` costruisce
internamente `FakeStructuredTransport`; il costruttore richiede il sigillo del
modulo e il client accetta soltanto il tipo esatto. Entrambe le classi
rifiutano sottoclassi.

La riproduzione del ciclo 1 ora dà:

- trasporto impostore con `offline_only=True`: respinto;
- `send` dell'impostore: **non chiamato**;
- sottoclasse di `FakeStructuredTransport`: respinta;
- costruzione con sigillo errato: respinta;
- trasporto finto creato dalla factory: accettato e funzionante in memoria.

La regressione ufficiale verifica espressamente il rifiuto prima di `send`; la
sonda indipendente riproduce lo stesso risultato.

## Chiusura blocker 2 — ordine di `kind`

**PASS.** Il validatore non richiede più che `kind` sia la prima chiave. Per
tutte e tre le radici sono stati provati `kind` primo e `kind` ultimo:

- entrambi conformi allo schema JSON draft 2020-12;
- entrambi validi per `compile_ir`;
- stesso documento tipizzato;
- stessa impronta compilata;
- `canonical_ir_json_bytes` presenta sempre `kind` per primo.

La validità segue quindi la semantica non ordinata degli oggetti JSON, mentre
la convenzione `kind`-first resta una scelta deterministica di presentazione.

## Risultati dei 36 controlli

### Suite ufficiale — 30/30

Le 30 prove passano senza errori. Coprono:

- artefatti e freeze correnti;
- descrizioni derivate e materiale italiano/inglese;
- assenza di campi derivati nello schema model-facing;
- isolamento da runtime, rete e oracolo nel codice attivo;
- trasporto finto e rifiuto di impostore, sottoclasse e sigillo errato;
- budget dichiarato del critic;
- D-01, D-02, D-03 e D-04;
- equivalenza `kind` primo/ultimo e serializzazione `kind`-first;
- autorità di root, route, controllo, barriera e reason;
- self/forward/ciclo e scoping dei rami;
- porte ambigue fail closed;
- impossibilità per il modello di emettere campi derivati;
- roundtrip 124, barriere e continuazioni;
- richiesta JSON Schema rigorosa e trasporto finto;
- critic soltanto come interfaccia;
- registro sintetico rinominato;
- anti-contaminazione;
- audit storico degli archi;
- determinismo e tempo del compilatore.

### Controlli aggregati indipendenti — 6/6

| N. | Controllo | Esito |
|---:|---|---|
| 31 | trasporto finto interno/sigillato, rifiuto pre-`send` | PASS |
| 32 | parità schema/validatore per ordine di `kind` e serializer | PASS |
| 33 | roundtrip, compilatore, continuazioni e multi-branch fail closed | PASS |
| 34 | contratto model-facing, D-01—D-04, client e critic | PASS |
| 35 | registro, lingue, anti-leakage e audit edge | PASS |
| 36 | freeze, determinismo e prestazioni | PASS |

## IR, validatore e compilatore

- radici esclusive: `operation_graph`, `system_control`, `unrepresentable`;
- IR model-facing minimo: `kind`, `steps`, `route`, `from`, `barrier`, `body`,
  `control`, `reason`;
- nessun `input`, `output`, `path`, `node_path`, `ordinal`, `outcome` o
  `continuation` nello schema model-facing;
- `from` accetta soltanto interi esatti, non negativi, unici e riferiti a
  operazioni precedenti che dominano il punto d'uso;
- self, forward, cicli e fuga di una produzione dal ramo sono respinti;
- una sorgente esterna resta visibile dentro la barriera;
- ordinali e percorsi sono derivati deterministicamente;
- tutte le **80/80** operazioni congelate hanno una sola porta `result` in
  uscita e `primary` in ingresso; il compilatore deriva quindi l'arco
  univocamente;
- un registro sintetico con porte ambigue fallisce chiuso;
- la barriera compatta produce `approved` con corpo e `rejected` vuoto;
- la continuazione è legata a versione, registro, radice, percorso barriera,
  outcome e corpo; una modifica del registro la invalida;
- un significato con più rami non vuoti viene respinto, non appiattito;
- `system_control` e `unrepresentable` compilano senza campi estranei.

## D-01—D-04 e client strutturato

- D-01: chiavi duplicate respinte;
- D-02: numeri non finiti, overflow e interi oltre limite respinti;
- D-03: tipi JSON esatti e oggetti chiusi ricorsivamente;
- D-04: `from` unico e order-insensitive; liste di autorità canoniche e
  proiezione legata alla fonte;
- richiesta: `response_format.type=json_schema`, nome stabile, `strict=true`,
  schema completo e `temperature=0`;
- `grammar` e `tools` assenti;
- il critic resta una sola interfaccia senza implementazione, oracolo o rete;
- massimo dichiarato due chiamate e obiettivo medio 1,20. Quest'ultimo resta un
  obiettivo da misurare in futuro, non un risultato provato offline.

## Registro, descrizioni e anti-contaminazione

- la proiezione coincide con quella ricostruita dal registro congelato e dalle
  fonti manifest/snapshot;
- descrizioni: **80/80** route con italiano, **61/80** anche con inglese;
- capitoli `SCOPO` e `NON`: 73 route in italiano e 61 in inglese;
- il registro sintetico rinominato governa schema, prompt e validatore;
- leak di query, impronte query o identificativi dai 120 casi, 4 controlli
  tipizzati e 34 controlli Phase-1 nei tre artefatti model-facing: **0**;
- nessun indice di caso è hardcoded nel codice attivo.

Il catalogo conserva entrambe le lingue quando la fonte le offre; non è però
bilingue completo, perché 19 route storiche hanno soltanto materiale italiano.
È un limite visibile delle fonti congelate, non un nuovo difetto del ciclo 2.

## Roundtrip e audit storico edge

- roundtrip semantico: **124/124**;
- barriere: **3**;
- controlli di sistema undo: **2**;
- astensioni: **34** nei 120 casi originari, **35** includendo i 4 controlli;
- audit storico data-driven: **156** issue `DATA_EDGE_*` e **156** archi
  espliciti corrispondenti in **66** record B, sopra la soglia richiesta 139;
- dettaglio storico invariato: 58 `DATA_EDGE_DOMINANCE` e 98
  `DATA_EDGE_OUTPUT_PORT`;
- le porte arbitrarie osservate nello storico non sono esprimibili nel nuovo
  schema e le chiavi ostili `input`/`output` vengono respinte.

## Freeze, determinismo e prestazioni

- artefatti rigenerati attesi e file presenti: byte-identici;
- insieme e impronte dei file, contratto, registro, schema, prompt e payload
  freeze: coerenti;
- SHA-256 del freeze ciclo 2:
  `7b68f9f62965ea24cf830ecc8c717fcdf6d6116ce3fa412082ed1f2287b4e992`;
- determinismo: una sola impronta in **40** compilazioni;
- prestazione indipendente: **500/500** compilazioni valide, circa **4,007 ms**
  medi sul piccolo grafo di prova, sotto il limite testato di 20 ms.

La misura temporale è locale e non è una garanzia universale. Il freeze resta
correttamente marcato `offline_candidate_not_runtime`.

## Conclusione e prontezza

**PASS, 36/36.** I due blocker del ciclo 1 sono chiusi e non emergono altri
difetti nel perimetro finito. Il candidato v0.2 può essere usato come base per
progettare un protocollo futuro separato, con nuova revisione e autorizzazione
prima di qualsiasi esecuzione. Questo audit non autorizza né una misura né
l'uso di GPU, rete, endpoint, servizi o modelli.
