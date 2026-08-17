# Audit offline indipendente candidate v0.2 — ciclo 1

Data: 2026-08-13  
Perimetro: `candidate_v0_2/`, sola lettura del codice, degli artefatti e dei
test. Nessuna GPU, rete, chiamata a servizi, modifica al candidato o commit.
`self_review.md` non è stato usato come autorità. L'unica scrittura è questo
referto.

## Esito

**FAIL — due difetti bloccanti.**

Il disegno del piccolo IR, il compilatore, gli artefatti congelati e le prove
strutturali sono in gran parte solidi. Tuttavia:

1. `StructuredClient` non impone davvero il trasporto finto in memoria;
2. lo schema JSON rigoroso e il validatore accettano insiemi diversi di JSON.

Il candidato non è quindi ancora pronto come base congelata per progettare una
futura misura. Correggere i due difetti e ripetere l'audit non autorizza né
implica una misura.

## Conteggio dei controlli

Totale: **36/36**.

- suite ufficiale: **28/28 PASS**;
- verifiche indipendenti statiche e aggregate: **6/6 PASS**;
- sonde mirate aggiunte dall'audit: **2/2 FAIL**, corrispondenti ai due difetti
  bloccanti sotto;
- difetti non bloccanti: **0**.

Non sono state aggiunte prove oltre quelle necessarie a confermare i difetti.

## Difetto bloccante 1 — il trasporto non è realmente fake-only

`structured_client.py:75-79` accetta qualunque oggetto che esponga
`offline_only = True`. Il protocollo Python non è una barriera a runtime e
l'attributo è autodichiarato dal chiamante. Una futura classe con rete può
quindi passare il controllo senza essere `FakeStructuredTransport`.

Riproduzione minima:

```python
class ImpostorTransport:
    offline_only = True

    def send(self, request):
        return {"choices": [{"message": {"content":
            '{"kind":"system_control","control":"undo_last_turn"}'
        }}]}

StructuredClient(ImpostorTransport())  # accettato oggi
```

La sonda dell'audit conferma `impostor_transport_accepted=true`. Non è stata
effettuata rete: la classe usata era interamente in memoria. Il difetto viola
il contratto dichiarato «accetta soltanto un trasporto finto in memoria» e
rende la proprietà offline dipendente dalla buona fede del chiamante.

Correzione minima suggerita:

- se questo modulo deve restare strettamente di laboratorio, richiedere
  `type(transport) is FakeStructuredTransport` oppure un token/costruttore
  privato non autodichiarabile;
- se invece si vuole un'interfaccia estendibile, togliere la pretesa
  «fake-only» e spostare il confine offline in un wrapper separato e
  verificabile. Questa è una scelta di contratto, non un semplice cambio di
  commento.

Test di regressione minimo:

- una classe impostore con `offline_only=True` e metodo `send` deve essere
  respinta prima di invocare `send`;
- `FakeStructuredTransport` deve continuare a essere accettato.

## Difetto bloccante 2 — schema strict e validatore divergono

Il client invia JSON Schema draft 2020-12 con
`response_format.type=json_schema` e `strict=true`. Lo schema discrimina le
radici con `oneOf` e `kind`, ma JSON non attribuisce significato all'ordine
delle chiavi. Il validatore aggiunge invece una regola non espressa nello
schema: `validator.py:154-155` richiede che `kind` sia la prima chiave.

Riproduzione minima:

```json
{"steps":[{"route":"change/files"}],"kind":"operation_graph"}
```

- lo schema congelato lo accetta;
- `compile_ir` lo rifiuta con `ROOT_DISCRIMINANT_FIRST`.

Una risposta garantita conforme al contratto strutturato può dunque fallire
nel validatore locale. `strict=true` non può garantire un ordine delle chiavi
che JSON Schema non definisce.

Correzione minima suggerita:

- rimuovere il requisito di ordine da `validate_ir`; usare esclusivamente il
  valore di `kind` come discriminante;
- se l'ordine serve soltanto per presentazione o hashing, produrlo nella
  serializzazione canonica, non usarlo come validità semantica.

Test di regressione minimo:

- per ogni radice, permutazioni delle chiavi accettate dallo schema devono
  produrre lo stesso documento e la stessa impronta;
- aggiungere un test di parità schema-validatore: ogni esempio positivo
  conforme allo schema deve essere accettato dal validatore, e le mutazioni
  negative condivise devono essere respinte da entrambi.

## Controlli superati

### IR e schema model-facing

- tre radici esclusive: `operation_graph`, `system_control` e
  `unrepresentable`;
- IR minimo: operazione `route` più `from` opzionale; barriera `barrier` più
  `body`;
- nessun `input`, `output`, `path`, `node_path`, `ordinal`, `outcome` o
  `continuation` nello schema model-facing; chiavi extra respinte
  ricorsivamente;
- `from` è un array non vuoto di interi esatti, non negativi e unici; il
  validatore lo canonicalizza come insieme ordinato;
- riferimenti self/forward e cicli sono respinti; una sorgente esterna domina
  il corpo della barriera, mentre una produzione del ramo non evade dalla
  barriera.

### Compilatore e continuazioni

- ordinali e `node_path` sono derivati deterministicamente;
- ogni arco deriva `result -> primary` perché tutte le **80/80** operazioni del
  registro congelato hanno una sola porta di uscita `result` e una sola porta
  di ingresso `primary`;
- porte sintetiche ambigue falliscono chiuse;
- una barriera compatta diventa `approved` con corpo e `rejected` vuoto;
- le continuazioni sono legate a versione, impronta registro, impronta radice,
  percorso barriera, outcome e corpo; una modifica del registro le invalida;
- `system_control` e `unrepresentable` compilano e proiettano senza grafi o
  campi estranei;
- la conversione test-only rifiuta un significato multi-branch con ramo
  `rejected` non vuoto: fail closed confermato.

Limite esplicito, non difetto in questa fetta: il compilatore supporta soltanto
la barriera `approved/rejected` proiettata dal registro; non rappresenta
semantiche multi-branch generiche.

### Robustezza D-01—D-04

- D-01: chiavi duplicate respinte alla radice e nei passi;
- D-02: `NaN`, infinità, overflow float e interi oltre 4.300 cifre respinti;
- D-03: tipi JSON esatti e oggetti chiusi, inclusa distinzione `bool`/`int`;
- D-04: `from` unico e order-insensitive; liste di autorità canoniche, senza
  duplicati, e proiezione legata alla fonte esatta.

### Client strutturato e critic

- la richiesta contiene `response_format.type=json_schema`, nome stabile,
  `strict=true`, schema completo e `temperature=0`;
- `grammar` e `tools` non sono presenti nella richiesta costruita;
- il trasporto finto ufficiale conserva la richiesta e restituisce una coda
  deterministica senza I/O;
- il critic è soltanto un'interfaccia: nessuna implementazione o import di
  oracolo, runtime o rete; il risultato primario dichiara una sola chiamata e
  può materializzare soltanto una richiesta critic dopo un segnale meccanico;
- tetto dichiarato 2 chiamate e obiettivo medio 1,20. È un vincolo/obiettivo
  dichiarato, non una prestazione empirica dimostrata da questo prototipo.

### Registro, prompt e contaminazione

- la proiezione è derivata dal registro congelato e dalle descrizioni dei
  manifest/snapshot, con impronte delle fonti e confronto byte-riproducibile;
- **80/80** route hanno materiale descrittivo italiano; **61/80** hanno anche
  inglese. I restanti 19 derivano da fonti storiche soltanto italiane: il
  meccanismo conserva entrambe le lingue quando disponibili, ma il catalogo
  non è bilingue completo;
- capitoli `SCOPO` e `NON` risultano per 73 route in italiano e 61 in inglese;
- il registro sintetico rinominato è seguito da prompt, schema e validatore;
- nessuna query, impronta query o identificatore dei 120 casi, 4 controlli
  tipizzati o 34 controlli Phase-1 compare nei tre artefatti model-facing;
- nessun indice di caso è hardcoded nel codice attivo. La prova aggregata usa
  la sola soglia storica dichiarata `>=139`, non un elenco di indici.

### Roundtrip, audit edge e prestazioni

- roundtrip esatto: **124/124** expected, composti da 120 casi e 4 controlli;
- conteggi: **3** barriere, **2** controlli undo, **34** astensioni nei 120 casi
  originari e **35** includendo i 4 controlli;
- audit storico aggregato e data-driven: soglia richiesta **139** superata;
  il batch corrente contiene **156** issue `DATA_EDGE_*`, tutte su percorsi
  distinti, e **156** archi espliciti corrispondenti in **66** record B;
- dettaglio: 58 `DATA_EDGE_DOMINANCE` e 98 `DATA_EDGE_OUTPUT_PORT`;
- le forme con porte arbitrarie non sono esprimibili nel nuovo schema e le
  mutazioni con `input`/`output` sono respinte;
- determinismo: **1** sola impronta in 40 compilazioni;
- prestazione della sonda: **500/500** compilazioni valide, circa **4,0 ms**
  medi sulla macchina di audit, sotto il limite testato di 20 ms. È una misura
  locale del piccolo esempio, non una garanzia universale.

### Freeze

- i tre artefatti sono byte-identici alla rigenerazione;
- insieme e impronte dei file coincidono col freeze;
- legami a registro, schema e prompt e impronta del payload freeze sono validi;
- SHA-256 freeze:
  `8658c44ff11d6b7afe53d16578afcff1995bf5c54e5441d9c856420c907ffb47`;
- stato correttamente limitato a `offline_candidate_not_runtime`.

Nota: il freeze include anche `self_review.md`. Questo non lo rende autorità e
non è stato usato per il verdetto; significa soltanto che una sua modifica
richiede il risigillo dell'intero candidato.

## Prontezza per una misura futura

**Non pronto.** Il nucleo strutturale merita di essere mantenuto, ma prima di
progettare una misura futura servono entrambe le correzioni bloccanti, i test
di regressione indicati, rigenerazione di artefatti/freeze e nuovo audit
indipendente. Anche dopo un PASS, l'audit autorizzerebbe soltanto la fase di
progettazione del protocollo: non GPU, rete, chiamate a modelli o esecuzione di
una misura.
