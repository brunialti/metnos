# Candidate intent IR 0.2 — laboratorio offline

Stato: prototipo nuovo, non collegato a runtime o produzione.

## Esito cercato

Il modello emette un oggetto molto piccolo. Può scegliere soltanto:

- un grafo con `kind=operation_graph` e `steps`;
- un controllo con `kind=system_control`;
- una rinuncia tipizzata con `kind=unrepresentable`.

Un passo operativo contiene `route` e, solo quando serve, `from`. Il modello
non può emettere porte, percorsi, ordinali, esiti o continuazioni. Questi dati
sono derivati dal compilatore usando il registro congelato.

La barriera compatta contiene `barrier` e `body`. Il compilatore assegna il
corpo all'esito `approved` e crea sempre l'esito `rejected` vuoto. Le azioni
interne non diventano visibili fuori dalla barriera.

## Componenti

- `registry_projection.py`: proietta il registro ombra e le descrizioni dei
  manifest. Conserva italiano e inglese, più `SCOPO` e `NON` quando presenti.
- `projection.py`: genera schema JSON e prompt senza richieste del banco.
- `language_tag.py`: normalizza strutturalmente BCP47 senza allowlist di lingue;
  ogni tag valido usa lo stesso prompt neutro, mentre un tag malformato viene
  rifiutato prima dell'invio.
- `validator.py`: chiude ricorsivamente forme, tipi e autorità.
- `compiler.py`: assegna ordinali e percorsi, deriva le sole porte univoche,
  materializza gli esiti e costruisce continuazioni legate alle impronte.
- `structured_client.py`: costruisce una richiesta `response_format=json_schema`
  rigorosa. Il client costruisce internamente il solo trasporto finto sigillato;
  oggetti esterni, imitazioni e sottoclassi vengono rifiutati prima di `send`.
- `critic.py`: dichiara soltanto l'interfaccia di un futuro secondo controllo.
  Non effettua chiamate. Il tetto è due chiamate e l'obiettivo medio è 1,20.
- `api.py`: espone `analyze` e `compile_ir`. In laboratorio `analyze` riceve
  soltanto la coda di byte finti; non accetta un oggetto di trasporto esterno.
- `tests/oracle_adapter.py`: unica conversione dall'oracolo, confinata ai test.

Non esiste un meccanismo di riparazione dell'uscita grezza del candidato 0.1.
JSON rotto o IR non valido falliscono chiusi.

Il core, lo schema e il compilatore sono neutrali rispetto alla lingua e
Unicode-safe. La lingua della richiesta è sempre esplicita nel prompt tramite
il tag BCP47 normalizzato: non esistono branch `it/en`, fallback impliciti o
una lista chiusa di lingue supportate. Le descrizioni localizzate restano dati
autoritative del registro, non eccezioni nella logica.

`kind` è il discriminante logico, ma l'ordine delle chiavi JSON non cambia la
validità. La serializzazione canonica model-facing presenta `kind` per primo;
schema e validatore accettano anche un oggetto equivalente che lo presenta per
ultimo, come richiede la semantica non ordinata degli oggetti JSON.

## Artefatti congelati

- `intent_ir_registry_projection_v0_2.json`;
- `intent_ir_v0_2.schema.json`;
- `intent_ir_v0_2.prompt.txt`;
- `candidate_v0_2.freeze.json`.

Gli artefatti sono rigenerabili con `build_artifacts.py`. Il controllo confronta
byte, insieme dei file, impronte e legame con registro e manifest.

## Prove

La suite offline copre:

- giro completo dei 120 casi congelati e dei 4 controlli nuovi;
- 3 barriere, 2 undo, 34 rinunce del pannello originale;
- radici, rotte, dipendenze, self/forward/ciclo e visibilità dei rami;
- D-01 chiavi duplicate, D-02 numeri non finiti, D-03 tipi e oggetti chiusi,
  D-04 liste di autorità e fonti;
- registro sintetico con rotta rinominata;
- assenza di richieste e impronte del banco negli artefatti model-facing;
- assenza di testi, frammenti, ID, indici e impronte del banco nella logica;
- tag regionali, script, private-use e rifiuto pre-send dei tag malformati;
- risposta strutturata finta, determinismo, impronte e tempo del compilatore;
- rifiuto di trasporti impostori e sottoclassi, senza invocare il loro `send`;
- equivalenza fra `kind` primo e ultimo, con serializzazione canonica `kind`-first;
- impossibilità di esprimere porte arbitrarie osservate nello storico sigillato.

La suite legge lo storico soltanto per una regressione aggregata sulle forme
degli archi. Non legge l'oracolo per scegliere una risposta e non entra in
`analyze` o nel compilatore.

## Stato dopo audit indipendente

Il ciclo 2 dell'audit indipendente è **PASS**: 30/30 prove ufficiali e 36/36
controlli complessivi, senza difetti aperti nel perimetro offline. Il round-trip
resta 124/124; la prova indipendente di 500 compilazioni impiega circa 4,007 ms
in media sul piccolo grafo di test. Il freeze verificato dall'audit ha SHA-256
`7b68f9f62965ea24cf830ecc8c717fcdf6d6116ce3fa412082ed1f2287b4e992`.

Questa evidenza riguarda struttura, determinismo e sicurezza del laboratorio.
Non misura l'accuratezza semantica live e non autorizza GPU, rete, endpoint,
servizi o chiamate a modelli. La sola aggiunta documentale di questa sezione
richiede un risigillo successivo, distinto dal freeze già auditato.

## Confini rispettati

- zero GPU, rete, endpoint e servizi;
- zero modifiche a runtime, produzione, banco e candidato 0.1;
- zero commit;
- nessuna misura di accuratezza semantica: questa fase prova struttura,
  sicurezza e riproducibilità.

Questo prototipo usa dizionari soltanto come rappresentazione di JSON nel
laboratorio. Un eventuale porting in Metnos dovrà usare oggetti tipizzati e
immutabili nel core; non è autorizzato copiare questi dizionari in produzione.
