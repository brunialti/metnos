# Revisione avversariale indipendente — ciclo 5 finale

Data: 2026-08-12  
Revisore: AI B, indipendente e non umano  
Artefatti canonici modificati dal revisore: nessuno

## Verdetto

**PASS prudente: 32/32 controlli del piano e batteria aggiuntiva mirata
superati.**

- verificatore base: `error_count=0`;
- suite ufficiale: 107/107 negativi respinti;
- controlli positivi ufficiali: 6/6 riordini accettati;
- replay indipendente: 30/30 negativi respinti e 6/6 riordini accettati;
- nessuna nuova falla rilevata nelle superfici cambiate per D-04;
- oracle, banco, freeze e produzione non sono stati modificati.

Questo PASS chiude le falle concrete D-01–D-04 rispetto al piano e alle
mutazioni eseguite. Non dimostra accuratezza universale, assenza matematica di
ogni bypass o validità oltre il campione e il registro congelati.

## Verifiche canoniche

1. **Base — PASS.** `verify_oracle.py` restituisce 120 casi, 102 accordi, 18
   adjudication, 34 controlli esistenti, 4 nuovi, totale 38 e 23 fonti, senza
   errori.
2. **Suite negativa — PASS.** `test_oracle_mutations.py` respinge 107/107
   mutanti e termina con zero errori.
3. **Riordini positivi — PASS.** Le sei liste delle fonti sono dichiaratamente
   order-insensitive: invertirle, risigillare fonte e freeze e rieseguire il
   verificatore dà 6/6 `accepted`, con risultati deterministici.
4. **Registro — PASS.** `verify_registry.py`: 7/7 mutazioni respinte, payload
   `7a4f2916af8963d8d16fb79cb355d23e9739163f2241a84fa28bc6c070ed4a0a`.
5. **Audit fonti — PASS.** `audit_oracle_sources.py --check`: zero errori.
6. **Output sigillati — PASS.** `verifica_colonna_semantica.py` verifica sei
   impronte sigillate, 120 richieste, campione
   `36aba12b9ec0f366569498bba0fa4dd876f4d2354f736058791182ca41e37af4`
   e zero derive tra c10/c11 per i campi controllati. Tali output non sono usati
   come verità semantica dell'oracolo.

## Identità e freeze

- oracle file SHA-256:
  `3e0d1155fad7b2920b3b04bb6e617747849c8fe4a1abfea5920f1b51dba6a123`;
- oracle payload SHA-256:
  `2cdec2776fa48594db8729ec1aa17a635956a61f64b2ce7ad00aa32f8cc0427f`;
- freeze file SHA-256:
  `68b27604460955b73eec217224fc8afcdac065ed49ceb7512d062c721329c679`;
- freeze lock:
  `cb99a86de4a0d0915adce656bd279cb7e935498a0beacbe45203af4f0bf61bbf`;
- file congelati: 23, tutti presenti, 23/23 hash coerenti;
- documenti JSON riletti dal parser rigoroso: 13, includendo il freeze.

File e payload dell'oracolo coincidono con tutti i cicli precedenti. Rispetto
al ciclo 4 sono cambiate soltanto le due impronte attese:

- `verify_oracle.py` →
  `8fc61e29057a1b04b0386a9af44b9051c27f0a4ebda2fc84331be4cea4774687`;
- `test_oracle_mutations.py` →
  `9bb1e1fb27aa877679a0a4c275294684e61ee486148d922a2085515082f816c5`.

Il freeze non include se stesso. Verifier e suite non incorporano hash o lock
del freeze corrente. I nuovi set-hash delle sei liste creano la dipendenza
lineare fonti → verifier → freeze → lock, senza arco di ritorno e quindi senza
dipendenza circolare.

## Chiusura D-04

Le sei liste protette sono:

1. reviewer A → `cases`;
2. reviewer B → `cases`;
3. adjudication A → `cases`;
4. proposta controlli A → `controls`;
5. proposta controlli B → `controls`;
6. baseline dei 34 controlli → `cases`.

Il verificatore ora, prima di costruire mappe per indice o ID:

- richiede root e lista del tipo JSON esatto;
- impone cardinalità e insieme di identità attesi;
- rifiuta record non-object, identità mancanti, bool o tipo errato;
- rifiuta duplicati, omissioni, sostituzioni e record estranei;
- lega query e hash dei casi al banco congelato;
- lega ogni lista al proprio hash canonico dell'insieme di record;
- costruisce la mappa soltanto se l'intera lista è valida;
- per la baseline richiede 34 ID, query e hash query unici;
- verifica i conteggi derivati 120/120, 102+18, 34+4 e totale 38.

Gli hash canonici order-insensitive correnti sono:

- reviewer A cases:
  `ea4829c629b84ec54747e8825d53f77f5ce20da6d60a6b76a86f4783c77a7980`;
- reviewer B cases:
  `6e87c3be0fae4bab654aa0c412cff2539a2851c182132a0968fd16e05fdd8b9c`;
- adjudication cases:
  `3b943f582f255a74d426d5cde67274303fe9f8903c88b88950148346742d9d60`;
- proposta A controls:
  `288cf11d914024cf8f31beb3caa75458e446c75dab68da583b1390056babb10e`;
- proposta B controls:
  `8c5f7d3e79c0ae2f77946f2e93b320d712acfdc56a614b9fc56232283814d18c`;
- baseline cases:
  `a489852a519f4176599af648a253dd9834d7886e23cd0e801801b000efba83cc`.

Per tutte e sei, il digest resta identico dopo inversione della lista, mentre
cardinalità, identità e unicità restano obbligatorie.

## Replay indipendente mirato

È stata eseguita una sola batteria aggiuntiva, limitata alle superfici D-04.
Per ciascuna delle sei liste sono state prodotte, in `/tmp`, cinque varianti
negative risigillate:

1. duplicato in testa;
2. duplicato in coda;
3. record junk;
4. omissione di un record;
5. sostituzione dell'ultima identità con la prima.

Esito: **30/30 respinte** dal codice specifico
`SOURCE_CASE_LIST`, `SOURCE_CONTROL_LIST` o `BASE_CONTROL_LIST`, con la stessa
lista di errori su due esecuzioni. Questa matrice include i sette falsi zero
originali D-04 e li estende uniformemente a tutte e sei le liste.

Per ciascuna lista è stata inoltre invertita soltanto la sequenza, senza
cambiare i record: **6/6 accettate** e deterministiche. Il comportamento
conferma il contratto scelto: ordine non semantico, insieme/cardinalità/unicità
obbligatori.

La suite ufficiale aggiunge, oltre a queste famiglie, duplicati contraddittori,
record invalidi e duplicazione separata di ID/query nella baseline. Il suo
blocco D-04 comprende 38 negativi e 6 positivi.

## Baseline e coerenza delle fonti

- reviewer A: 120 casi unici;
- reviewer B: 120 casi unici;
- divergenze A/B: esattamente 18;
- adjudication: gli stessi e soli 18 indici;
- accordi esatti derivati: 102;
- baseline: 34 record, 34 ID, 34 query e 34 hash query unici;
- proposte A e B: quattro controlli unici ciascuna;
- selezione canonica: quattro controlli unici;
- totale autorizzato: 34+4=38;
- nessuna collisione query/hash tra i quattro nuovi, i 34 o le 120 richieste;
- provenienza: doppia revisione AI indipendente, non umana; l'accordo tra
  bracci non è gold.

Il catalog snapshot resta esplicitamente arretrato: 96 voci congelate contro
83 manifest correnti. Non viene sostituito silenziosamente.

## Nessuna regressione D-01–D-03

- **D-01, chiavi duplicate:** oracle e freeze con chiavi duplicate vengono
  respinti dal parser condiviso.
- **D-02, numeri non finiti:** `NaN` e overflow `1e999` in oracle e freeze
  vengono respinti; serializzazioni canoniche usano `allow_nan=False`.
- **D-03, tipi e oggetti aperti:** 47/47 mutanti metadata respinti; i 5/5
  mutanti schema del freeze sono respinti. Il confronto resta type-strict e
  ricorsivo.

Nell'AST del verificatore esiste una sola `json.loads`, dentro `read_json`; la
suite delega allo stesso parser.

## Piano avversariale 32/32

| Controlli | Esito | Evidenza sintetica |
|---|---|---|
| ATK-01–07 | PASS | Parser rigoroso; 120 casi; bijezione, testi, hash, ID, unicità e ordine canonico. |
| ATK-08–16 | PASS | Radici 84/2/34; grafi, route, reason, control, barrier, outcome e dipendenze conformi. |
| ATK-17–20 | PASS | Contratto, registro, freeze e audit fonti coerenti. |
| ATK-21–22 | PASS | Banco e output sigillati integri; nessun output di modello promosso a gold. |
| ATK-23–25 | PASS | Snapshot arretrato dichiarato, double-AI non umana e fonti 102+18 validate senza estrazione lossy. |
| ATK-26–29 | PASS | Baseline esatta 34, quattro aggiunte, totale 38, nessuna collisione o duplicazione semantica dichiarata. |
| ATK-30 | PASS | 107/107 negativi ufficiali più 30/30 nel replay indipendente. |
| ATK-31 | PASS | Mutazioni di binding, ordine e `data_from` respinte. |
| ATK-32 | PASS | Snapshot pre/post identico per 23 fonti, oracle, freeze e stato Git preesistente; nessun servizio o processo GPU avviato. |

I casi semantici sentinella restano invariati: 9/11 `find/persons`; 38
temporaneamente `unrepresentable/outside_registry`; 84 soltanto `get/images`
come vista degli indici materializzati; 113 `read/events -> create/files` con
`data_from: [{"from": 0}]`. Le note future restano metadata e non entrano negli
expected.

## Limiti dell'attestazione

- Il PASS riguarda i 32 controlli dichiarati, 107 mutazioni ufficiali e 36
  scenari indipendenti sulle superfici D-04; non è una prova formale.
- Gli hash di insieme si basano sulla resistenza alle collisioni di SHA-256.
- Il freeze è un sigillo deterministico revisionato, non una firma esterna
  contro chi possieda autorità per aggiornare fonti, verifier e lock insieme.
- Il risultato è limitato ai 120 casi, ai 38 controlli e al registro 0.1.
- Nessuna GPU, rete, inferenza, executor, servizio o commit è stato usato.

Conclusione: **nessun difetto bloccante o non bloccante nuovo rilevato nel ciclo
5; oracle canonico e verificatore superano il piano corrente 32/32.**
