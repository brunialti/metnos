# Verifica funzionale indipendente B — punto 5, ciclo 1

Data: 12 agosto 2026  
Perimetro: candidato `candidate_v0_1`, contratto, registro, oracle, README,
checkpoint e handover pubblici. Le directory dei revisori precedenti non sono
state lette. Nessun file candidato o canonico è stato modificato.

## Esito

**FAIL / BLOCCO: 45 PASS, 3 FAIL su 48 controlli.**

Le suite fornite sono riproducibili e verdi: builder `error_count=0`, scenari
7/7 con round-trip 124/124, mutazioni 57/57 respinte e 7/7 positive accettate,
verificatore candidato `error_count=0`, dry-run 158/158. Sono verdi anche le
suite canoniche del registro e dell'oracolo. Questi risultati non coprono i tre
difetti bloccanti riprodotti sotto.

## Matrice 48/48

### A. Separazione delle fonti e derivazione dal registro

| N. | Esito | Controllo verificabile | Evidenza |
|---:|:---:|---|---|
| 1 | PASS | Richieste e risposte attese sono artefatti distinti. | La suite runner ha `gold_fields_present=false`; gli `expected` sono letti soltanto dal builder e dall'evaluator. |
| 2 | PASS | Prompt, schema e core non contengono una tabella o confronti testuali con le 120 richieste. | Audit del verificatore: nessuna query intera né sovrapposizione di almeno quattro token su 120+4+34. |
| 3 | PASS | I 34 controlli storici restano separati. | `legacy_panel_v0_1.json` ha 34 casi, autorità Phase-1 e conversione automatica falsa. |
| 4 | PASS | Lo schema deriva dal registro. | `build_schema(registry)` viene rimaterializzato e confrontato byte per byte dal verificatore. |
| 5 | PASS | Il prompt deriva dal registro. | `build_prompt(registry)` viene rimaterializzato e confrontato byte per byte dal verificatore. |
| 6 | PASS | Il core segue un registro sintetico rinominato. | Lo scenario rinomina controllo, barriera, esiti, rotta e ragione; i vecchi nomi spariscono e i tre documenti passano. |

### B. Fasi, mutabilità e isolamento dell'oracolo

| N. | Esito | Controllo verificabile | Evidenza |
|---:|:---:|---|---|
| 7 | PASS | Il normalizzatore applica soltanto trasformazioni di forma. | Deriva percorsi/ordinali, porte univoche, casi omessi e impronte; non contiene riscritture di rotta. |
| 8 | PASS | La normalizzazione non muta l'input. | Il round-trip dei 124 confronta ogni documento con una copia profonda immutata. |
| 9 | PASS | Il validatore non riceve la richiesta. | `validate_model_document(document, registry)` e `validate_normalized_document(document, registry)` non hanno parametro query. |
| 10 | PASS | Il validatore non legge risposte attese o oracle. | Import e percorsi del modulo sono limitati a registro e tipi; nessun gold/oracle. |
| 11 | PASS | Il runner simulato non apre l'oracolo. | Il runner non importa né nomina l'oracolo canonico o l'overlay Phase-1. |
| 12 | PASS | Il runner non importa evaluator o builder. | Guardia statica e lettura del sorgente confermano l'assenza dei due import. |
| 13 | PASS | L'evaluator apre il gold dopo avere caricato e validato un batch completo salvato. | `evaluate_saved_batch` termina `_validate_saved_batch` e solo dopo chiama `strict_json_file(ORACLE_PATH)`. Un batch incompleto è respinto. |
| 14 | PASS | L'evaluator riproduce ogni estrazione dai byte raw salvati. | Decodifica base64, riesegue `extract_raw_json` e confronta la riproduzione prima del gold. |
| 15 | **FAIL** | Il batch salvato è verificato con tipi JSON esatti. | Il confronto Python tra dizionari considera `true == 1`: mutare `validation_result.valid` da `true` a `1` lascia il batch accettato e valutato 120/120. Vedi B-02. |

### C. Radici, controlli e regioni

| N. | Esito | Controllo verificabile | Evidenza |
|---:|:---:|---|---|
| 16 | PASS | Le tre radici sono chiuse ed esclusive. | `operation_graph`, `system_control` e `unrepresentable` sono varianti chiuse; radici miste/ignote sono respinte. |
| 17 | PASS | `unrepresentable` è distinto dagli errori tecnici. | JSON rotto/non finito produce `technical_invalid`; ragione registrata produce `valid_unrepresentable`; ragione ignota produce `document_invalid`. |
| 18 | **FAIL** | Ogni input JSON non valido è restituito come invalidità controllata, senza eccezioni fuori contratto. | Un `outcome` JSON di tipo lista solleva `TypeError`; interi oltre il limite Python e surrogate Unicode possono sollevare `ValueError`/`UnicodeEncodeError`. Vedi B-01. |
| 19 | PASS | `undo_last_turn` è un controllo di sistema, non un'operazione. | Proviene da `system_controls`; non compare nelle 80 rotte e rifiuta `inputs` nel registro corrente. |
| 20 | PASS | `get/approval` è una barriera con rami propri. | Proviene da `barriers`, con esiti ordinati `approved` e `rejected`. |
| 21 | PASS | Ogni ramo possiede il proprio corpo. | I corpi restano annidati in `OutcomeCase`; gli ordinali di ramo non entrano nella regione esterna. |
| 22 | PASS | Esiti omessi e casi emessi rispettano il contratto. | Un esito omesso viene materializzato vuoto; un caso emesso con corpo vuoto è respinto. |

### D. Ordine, regioni, archi e porte

| N. | Esito | Controllo verificabile | Evidenza |
|---:|:---:|---|---|
| 23 | PASS | Gli ordinali seguono l'ordine di pre-visita del documento. | Normalizzatore e validatore tipizzato condividono il contatore sequenziale e ne verificano la corrispondenza. |
| 24 | PASS | Gli archi puntano solo a operazioni precedenti. | Mutazioni forward e self-edge sono respinte. |
| 25 | PASS | La sorgente deve dominare la destinazione nella regione corretta. | La visibilità è copiata all'ingresso dei rami e non incorpora produttori locali di altri percorsi. |
| 26 | PASS | Un produttore esterno precedente può alimentare un ramo. | `current_visible` viene copiato in ciascun caso, conservando le sole sorgenti dominanti esterne. |
| 27 | PASS | Archi fra rami fratelli o da ramo verso l'esterno sono respinti. | Entrambe le mutazioni dedicate sono intercettate. |
| 28 | PASS | Le porte sono derivate soltanto quando univoche. | Il registro corrente ha una porta `primary`/`result` per rotta; registro sintetico multiporta senza nomi espliciti è respinto. |
| 29 | PASS | Porte ignote e archi duplicati sono respinti. | Mutazioni input/output errati e duplicazione canonica sono tutte intercettate. |

### E. Continuazioni immutabili

| N. | Esito | Controllo verificabile | Evidenza |
|---:|:---:|---|---|
| 30 | PASS | La continuazione lega versione, registro, radice, percorso, esito e corpo. | Tutti i campi entrano nel payload canonico di `continuation_sha256`. |
| 31 | PASS | Ogni alterazione della continuazione fallisce chiusa. | Mutazioni di hash, registro, radice, percorso, esito e corpo sono respinte. |
| 32 | PASS | La continuazione non viene eseguita o rianalizzata. | Il candidato espone solo costruzione e verifica; nessun trasporto reale o richiamo analizzatore. |

### F. JSON rigoroso e guardrail tecnici

| N. | Esito | Controllo verificabile | Evidenza |
|---:|:---:|---|---|
| 33 | PASS | Le chiavi JSON duplicate sono respinte. | `object_pairs_hook` solleva `JSON_DUPLICATE_KEY`; coperto anche su freeze. |
| 34 | PASS | `NaN`, infiniti e overflow float sono respinti. | Tutte le mutazioni D-02 producono invalidità tecnica. |
| 35 | PASS | Gli interi di arco richiedono tipo esatto. | `false` e `0.0` non sono accettati come ordinale `0`. |
| 36 | PASS | Radici, nodi, casi, archi e freeze hanno campi chiusi. | Chiavi extra/mancanti e set locali/autorità alterati sono respinti. |
| 37 | PASS | Byte, profondità, nodi e stringhe oltre limite restano errori tecnici. | Le quattro sonde ufficiali restituiscono invalidità e non `unrepresentable`. Il FAIL 18 riguarda forme non coperte da tali sonde. |

### G. Freeze, hash e determinismo

| N. | Esito | Controllo verificabile | Evidenza |
|---:|:---:|---|---|
| 38 | PASS | Il registro verifica formato, classi e propria impronta payload. | Verificatore canonico `error_count=0`, 7/7 mutazioni intercettate. |
| 39 | PASS | Il freeze candidato lega l'insieme esatto dei file locali e delle autorità. | Hash e insiemi completi sono ricalcolati; omissioni, aggiunte e sostituzioni falliscono. |
| 40 | PASS | Hash di schema e prompt coincidono con i byte derivati. | Verificatore candidato `error_count=0`; confronto materiale byte per byte. |
| 41 | **FAIL** | L'evaluator impone l'identità congelata dell'oracolo che usa. | Apre `ORACLE_PATH` e ne riporta il digest, ma non lo confronta con il freeze: un oracle locale temporaneo con un `expected` valido modificato viene accettato e valutato (119/120). Vedi B-03. |
| 42 | PASS | A input invariato il comportamento è deterministico. | Due dry-run completi sono identici, con SHA canonico uguale; schema e prompt rigenerati coincidono. |

### H. Copertura dei pannelli e risultati

| N. | Esito | Controllo verificabile | Evidenza |
|---:|:---:|---|---|
| 43 | PASS | Tutti i 124 `expected` tipizzati fanno round-trip. | 87 grafi, 2 controlli e 35 astensioni: 124/124 validati, normalizzati e proiettati. |
| 44 | PASS | I pannelli tipizzati sono esattamente 120+4. | Conteggi, ordinali, ID e hash query sono chiusi e unici. |
| 45 | PASS | Il pannello storico è esattamente 34. | ID, ordine, testi/hash e legame con l'oracolo Phase-1 sono verificati dal builder. |
| 46 | PASS | Il dry-run produce 158 record senza GPU o rete. | 120+4+34 record, tutti `dry_run_no_transport`; flag GPU e rete falsi e nessun import di trasporto. |
| 47 | PASS | Le metriche tipizzate restano colonne distinte. | Validità tecnica/documentale, radice, semantica, grafo, rotta, archi, barriera, controllo e astensione sono contate separatamente. |
| 48 | PASS | I tre pannelli non si compensano. | L'evaluator tipizzato accetta solo 120+4; dichiara i 34 non valutati, autorità Phase-1 e `cross_panel_compensation=false`. |

## Difetti

### Bloccanti

**B-01 — input JSON ostile può uscire dal contratto di invalidità.**

- Punto preciso: `intent_shadow_validate.py:165-187` aggiunge anche un outcome
  non-stringa e poi chiama `set(emitted)`; una lista/dizionario JSON è
  non-hashable. Inoltre `intent_shadow_io.py:140-152` traduce soltanto
  `JSONDecodeError`, non gli errori del decoder per interi enormi o ricorsione;
  la canonicalizzazione successiva non intercetta surrogate Unicode.
- Esempio minimo innocuo: usare come output raw
  `{"kind":"operation_graph","body":[{"kind":"barrier","barrier":"get/approval","cases":[{"outcome":[],"body":[{"kind":"operation","route":"set/issues"}]}]}]}`.
  `extract_raw_json` solleva `TypeError: unhashable type: 'list'` invece di
  restituire `document_invalid`/`technical_invalid`.
- Impatto: un solo record può interrompere l'intero batch; gli esiti tecnici
  non restano contabilizzati separatamente.
- Correzione suggerita, non applicata: dopo l'errore di tipo dell'outcome non
  inserirlo nelle operazioni di insieme/appartenenza; aggiungere parsing
  interi limitato, controllo degli scalar Unicode e traduzione mirata di
  `ValueError`/`RecursionError`/`UnicodeEncodeError` in `StrictJsonError`.
  Aggiungere mutazioni end-to-end per tutte queste forme.

**B-02 — il replay dell'evaluator non impone i tipi esatti del batch.**

- Punto preciso: `intent_shadow_evaluate.py:105-117` usa uguaglianza Python
  fra dizionari; in Python `True == 1` e `False == 0`.
- Esempio minimo innocuo: in un batch fake valido sostituire soltanto
  `records[0].extraction.validation_result.valid=true` con `1`. Il batch viene
  accettato e l'evaluator restituisce `ok`, 120/120 exact.
- Impatto: D-03 non è applicato all'artefatto che autorizza l'apertura del
  gold; prove salvate con tipi falsificati possono superare il replay.
- Correzione suggerita, non applicata: validare ricorsivamente schema chiuso e
  tipo esatto dell'envelope salvato, quindi confrontare anche i byte JSON
  canonici della riproduzione con quelli osservati. Aggiungere un censimento
  `bool -> 0/1` per ogni booleano annidato prima del gold.

**B-03 — l'evaluator non verifica il freeze dell'oracolo.**

- Punto preciso: `intent_shadow_evaluate.py:215-219` apre l'oracolo e controlla
  soltanto identità/conteggi; `:321-323` calcola il digest per il referto ma non
  lo confronta. Il freeze candidato lo pinna, ma l'evaluator non lo legge.
- Esempio minimo innocuo: puntare `ORACLE_PATH` a una copia temporanea in cui
  una ragione `unrepresentable` è sostituita con un'altra ragione registrata.
  Il batch intatto viene valutato con successo, 119/120, usando il nuovo hash.
- Impatto: la valutazione può usare un gold diverso da quello congelato senza
  fallire; il numero resta formalmente `ok` ma non appartiene al checkpoint.
- Correzione suggerita, non applicata: prima di costruire `_expected_map`,
  verificare l'hash esatto dell'oracolo e del suo freeze contro un'autorità
  pin-nata e applicare la validazione canonica completa. Un mismatch deve
  fallire prima di qualunque punteggio.

### Non bloccanti

Nessuno. I limiti dichiarati su assenza di GPU/live, mancata esecuzione delle
continuazioni e autorità separata Phase-1 sono coerenti col perimetro e non sono
stati trasformati in difetti.

## Chiusura

Il candidato soddisfa la struttura funzionale principale, ma non è pronto per
il successivo checkpoint: B-01 compromette la totalità della condotta e B-02/B-03
compromettono il confine pre-gold. Le correzioni devono essere applicate in un
nuovo ciclo, risigillate e coperte da mutazioni dedicate; questa revisione non
le ha applicate.
