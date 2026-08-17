# Verifica funzionale indipendente B — punto 5, ciclo 2

Data: 12 agosto 2026  
Perimetro: candidato aggiornato `candidate_v0_1`, contratto, registro, oracle,
README, checkpoint e handover pubblici. È stato riaperto il referto del ciclo 1.
Le directory `reviews/reviewer_a` e `reviews/reviewer_b` non sono state lette.
Nessun file candidato o canonico è stato modificato.

## Esito

**PASS: 48 PASS, 0 FAIL su 48 controlli.**

I tre difetti bloccanti del ciclo 1 sono chiusi e riprodotti come PASS con
fixture locali temporanee. Non sono emersi difetti nuovi, bloccanti o non
bloccanti. L'esito vale soltanto per la fetta deterministica offline: non
completa il punto 5 e non autorizza inferenza o misura GPU.

## Suite rieseguite

| Prova | Esito osservato |
|---|---|
| Builder query-suite `--check` | PASS, `error_count=0`, conteggi 120 + 4 + 34 |
| Suite funzionale candidato | PASS, 7/7 scenari, round-trip 124/124, `gpu_calls=0` |
| Suite mutazioni candidato | PASS, 89/89 negative respinte, 7/7 positive accettate, `gpu_calls=0` |
| Verificatore candidato | PASS, `error_count=0`, GPU assente |
| Dry-run `--include-legacy` | PASS, 158 record, pannelli 120 + 4 + 34, flag GPU/rete falsi |
| Verificatore registro | PASS, `error_count=0`, 7/7 mutazioni intercettate |
| Audit sorgenti oracle | PASS, `error_count=0` |
| Verificatore oracle | PASS, `error_count=0`, 120 casi + 4 nuovi controlli, freeze di 23 file |
| Suite mutazioni oracle | PASS, 107/107 negative respinte e 6/6 positive accettate; identità oracle invariata |
| Lint sintattico locale | PASS, parsing AST di 17 file Python |
| Determinismo dry-run | PASS, due esecuzioni con SHA-256 identico `c09298d13fe9a0112aa26cb8f177e886f2c38dcf30dbb9518f47fdac44e6b514` |

Tutte le prove sono state eseguite con `python3 -B`, senza rete, GPU, servizi,
produzione o banco congelato.

## Chiusura specifica dei tre FAIL del ciclo 1

| Difetto ciclo 1 | Esito ciclo 2 | Evidenza indipendente minima |
|---|:---:|---|
| B-01 — eccezioni fuori contratto su JSON ostile | PASS | 11/11: `outcome` array/object/number/bool/null danno `document_invalid`; interi positivi/negativi di 5000 cifre, surrogate in chiave/valore, ricorsione del decoder e superamento profondità danno `technical_invalid`; nessuna eccezione esce da `extract_raw_json`. |
| B-02 — tipi permissivi nel batch salvato | PASS | 8/8 alterazioni exact-type/closed (`true→1`, `false→0`, ordinale `int→bool`, SHA `str→bool`, chiave extra/mancante e conteggio `int→bool`) sono respinte; una sentinella conferma zero accessi al gate oracle. |
| B-03 — oracle non legato al sigillo | PASS | Oracle modificato con freeze canonico, freeze assente, lock alterato e oracle modificato con risigillo locale coerente sono tutti respinti; una sentinella conferma zero costruzioni della mappa gold/punteggio. Il controllo canonico intatto valuta 124/124 exact. |

Punti di codice precisi: il limite interi e i controlli Unicode/ricorsione sono
in `intent_shadow_io.py:27-45,91-95,105-215`; il validatore inserisce negli
insiemi soltanto outcome stringa in `intent_shadow_validate.py:165-191`; il
confine totale dell'extractor è in `intent_shadow_extract.py:65-140`.
L'evaluator applica tipi esatti e strutture chiuse in
`intent_shadow_evaluate.py:48-444`, confronta il replay come JSON canonico a
`:438-440`, verifica le identità dei verificatori e il checkpoint a `:464-499`,
verifica oracle/freeze/lock/fonti a `:502-522` e apre tale gold soltanto dopo il
batch completo a `:589-603`.

## Matrice 48/48

### A. Separazione delle fonti e derivazione dal registro

| N. | Esito | Controllo verificabile | Evidenza ciclo 2 |
|---:|:---:|---|---|
| 1 | PASS | Richieste e risposte attese sono artefatti distinti. | La suite query-only dichiara assenza di gold; `expected` resta nell'oracle e non nel runner. |
| 2 | PASS | Prompt, schema e core non contengono tabelle o confronti testuali con le 120 richieste. | L'audit contaminazione del verificatore passa su 120 + 4 + 34 testi. |
| 3 | PASS | I 34 controlli storici restano separati. | `legacy_panel_v0_1.json` contiene 34 casi, autorità Phase-1 e nessuna conversione automatica. |
| 4 | PASS | Lo schema deriva dal registro. | Il builder lo rimaterializza e il verificatore confronta hash e byte derivati. |
| 5 | PASS | Il prompt deriva dal registro. | Il builder lo rimaterializza e il verificatore confronta hash e byte derivati. |
| 6 | PASS | Il core segue un registro sintetico rinominato. | Lo scenario dedicato rinomina controllo, barriera, esiti, rotta e ragione e passa senza i vecchi nomi. |

### B. Fasi, mutabilità e isolamento dell'oracolo

| N. | Esito | Controllo verificabile | Evidenza ciclo 2 |
|---:|:---:|---|---|
| 7 | PASS | Il normalizzatore applica soltanto trasformazioni di forma. | Deriva percorsi, ordinali, porte univoche, casi omessi e hash; non riscrive rotte. |
| 8 | PASS | La normalizzazione non muta l'input. | Il round-trip 124/124 verifica anche la copia profonda originale. |
| 9 | PASS | Il validatore non riceve la richiesta. | Le API di validazione ricevono soltanto documento e registro. |
| 10 | PASS | Il validatore non legge risposte attese o oracle. | Modulo e import non contengono percorsi gold/oracle. |
| 11 | PASS | Il runner simulato non apre l'oracolo. | Guardia statica e codice runner confermano assenza di oracle/overlay. |
| 12 | PASS | Il runner non importa evaluator o builder. | Guardia statica del verificatore passa. |
| 13 | PASS | L'evaluator apre il gold soltanto dopo un batch completo salvato e validato. | Otto batch alterati sono respinti con zero chiamate al gate oracle; l'ordine è esplicito a `intent_shadow_evaluate.py:589-603`. |
| 14 | PASS | L'evaluator riproduce ogni estrazione dai byte raw salvati. | Base64 viene decodificato, l'estrazione rieseguita e i byte JSON canonici confrontati prima del gold. |
| 15 | PASS | Il batch salvato è chiuso e verificato con tipi JSON esatti. | Validatori ricorsivi exact-type/closed; 17 mutazioni ufficiali dedicate e 8 sonde indipendenti sono respinte prima del gold. |

### C. Radici, controlli e regioni

| N. | Esito | Controllo verificabile | Evidenza ciclo 2 |
|---:|:---:|---|---|
| 16 | PASS | Le tre radici sono chiuse ed esclusive. | `operation_graph`, `system_control`, `unrepresentable`; radici miste, extra o ignote sono respinte. |
| 17 | PASS | `unrepresentable` è distinto dagli errori tecnici. | Ragione registrata è valida; JSON rotto/oltre limite è tecnico; ragione ignota è documento invalido. |
| 18 | PASS | JSON ostile o non valido entro l'interfaccia raw resta un esito controllato. | Batteria indipendente 11/11 e mutazioni ufficiali coprono outcome di tipo errato, interi enormi, surrogate, UTF-8, ricorsione e profondità. |
| 19 | PASS | `undo_last_turn` è un controllo di sistema. | È in `system_controls`, non nelle 80 operazioni, e rifiuta input non dichiarati. |
| 20 | PASS | `get/approval` è una barriera con rami propri. | È in `barriers` con esiti ordinati `approved`, `rejected`. |
| 21 | PASS | Ogni ramo possiede il proprio corpo. | I corpi restano in `OutcomeCase`; gli ordinali locali non diventano visibili fuori dal ramo. |
| 22 | PASS | Esiti omessi ed emessi rispettano il contratto. | L'omesso viene materializzato vuoto; un caso emesso vuoto viene respinto. |

### D. Ordine, regioni, archi e porte

| N. | Esito | Controllo verificabile | Evidenza ciclo 2 |
|---:|:---:|---|---|
| 23 | PASS | Gli ordinali seguono la pre-visita del documento. | Normalizzatore e validatore condividono il contatore sequenziale e ne verificano la coerenza. |
| 24 | PASS | Gli archi puntano soltanto a operazioni precedenti. | Forward-edge e self-edge sono respinti. |
| 25 | PASS | La sorgente domina la destinazione nella regione corretta. | La visibilità entra nei rami per copia e non acquisisce produttori di altri percorsi. |
| 26 | PASS | Un produttore esterno precedente può alimentare un ramo. | Le sorgenti dominanti esterne restano visibili nei casi. |
| 27 | PASS | Archi tra rami fratelli o dal ramo verso l'esterno sono respinti. | Entrambe le mutazioni dedicate passano come rifiuti. |
| 28 | PASS | Le porte sono derivate soltanto quando univoche. | Il registro sintetico multiporta richiede nomi espliciti e altrimenti fallisce chiuso. |
| 29 | PASS | Porte ignote e archi duplicati sono respinti. | Le tre mutazioni dedicate sono intercettate. |

### E. Continuazioni immutabili

| N. | Esito | Controllo verificabile | Evidenza ciclo 2 |
|---:|:---:|---|---|
| 30 | PASS | La continuazione lega versione, registro, radice, percorso, esito e corpo. | Tutti i campi entrano nel payload canonico di `continuation_sha256`. |
| 31 | PASS | Ogni alterazione della continuazione fallisce chiusa. | Hash, registro, radice, percorso, esito e corpo alterati sono respinti. |
| 32 | PASS | La continuazione non viene eseguita o rianalizzata. | Il candidato espone soltanto costruzione e verifica, senza trasporto o seconda analisi. |

### F. JSON rigoroso e guardrail tecnici

| N. | Esito | Controllo verificabile | Evidenza ciclo 2 |
|---:|:---:|---|---|
| 33 | PASS | Le chiavi JSON duplicate sono respinte. | `JSON_DUPLICATE_KEY`; copertura anche su freeze e oracle. |
| 34 | PASS | `NaN`, infiniti e overflow float sono respinti. | Le mutazioni producono invalidità tecnica/fail-closed. |
| 35 | PASS | Gli interi richiedono tipo esatto. | `false`, `true` e float non passano come ordinali o conteggi interi. |
| 36 | PASS | Radici, nodi, casi, archi, batch e freeze hanno campi chiusi. | Extra, omissioni, aggiunte e sostituzioni sono respinti nelle suite ufficiali e mirate. |
| 37 | PASS | Byte, profondità, nodi, stringhe e interi oltre limite restano errori tecnici. | Sonde ufficiali e indipendenti restituiscono `technical_invalid`, mai `unrepresentable`. |

### G. Freeze, hash e determinismo

| N. | Esito | Controllo verificabile | Evidenza ciclo 2 |
|---:|:---:|---|---|
| 38 | PASS | Il registro verifica formato, classi e impronta payload. | Verificatore `error_count=0`, 7/7 mutazioni intercettate. |
| 39 | PASS | Il freeze candidato lega l'insieme esatto dei file locali e delle autorità. | Verificatore `error_count=0`; omissioni, aggiunte e sostituzioni falliscono. |
| 40 | PASS | Hash di schema e prompt coincidono con i byte derivati. | Builder e verificatore sono verdi; freeze coerente. |
| 41 | PASS | L'evaluator impone l'identità congelata dell'oracolo prima del punteggio. | Verifica checkpoint candidato, identità del verificatore canonico, oracle, freeze, lock e fonti; 4/4 varianti indipendenti respinte prima di `_expected_map`. |
| 42 | PASS | A input invariato il comportamento è deterministico. | Due dry-run completi hanno lo stesso SHA-256; builder e verificatori sono deterministici. |

### H. Copertura dei pannelli e risultati

| N. | Esito | Controllo verificabile | Evidenza ciclo 2 |
|---:|:---:|---|---|
| 43 | PASS | Tutti i 124 `expected` tipizzati fanno round-trip. | 124/124 validati, normalizzati e proiettati. |
| 44 | PASS | I pannelli tipizzati sono esattamente 120 + 4. | Conteggi, ordinali, ID e hash query chiusi e unici. |
| 45 | PASS | Il pannello storico è esattamente 34. | Builder e dry-run confermano 34 casi legati all'autorità Phase-1. |
| 46 | PASS | Il dry-run produce 158 record senza GPU o rete. | 158 record; `gpu_mode_present=false`, `network_transport_present=false`. |
| 47 | PASS | Le metriche funzionali restano colonne distinte. | Validità, radice, semantica, grafo, rotte, archi, barriera, controllo e astensione hanno conteggi separati. |
| 48 | PASS | I tre pannelli non si compensano. | L'evaluator tipizzato usa soltanto 120 + 4; i 34 sono dichiarati separati, non valutati qui e `cross_panel_compensation=false`. |

## Difetti e limiti

### Difetti bloccanti

Nessuno.

### Difetti non bloccanti

Nessuno emerso nel perimetro verificato. Non resta una correzione funzionale
da suggerire o applicare per B-01, B-02 o B-03.

### Limiti dichiarati, non difetti

- verifica solo deterministica/offline: nessuna accuratezza di modello, GPU,
  latenza o token è stata misurata;
- i guardrail tecnici sono parametri di test, non i limiti finali del futuro
  protocollo pre-GPU;
- continuazioni e dialogo di approvazione non vengono eseguiti;
- il pannello legacy resta affidato al proprio oracle Phase-1 e non viene
  rivalutato semanticamente dal candidato;
- il registro è uno snapshot circoscritto e l'oracolo conserva i limiti
  dichiarati della doppia revisione AI; non viene rivendicata revisione umana
  o copertura universale attuale.

## Chiusura

Nel perimetro deterministico autorizzato, il candidato aggiornato supera tutti
i 48 controlli e chiude B-01, B-02 e B-03. Il passaggio successivo resta quello
documentato nel checkpoint/handover: definire e autorizzare separatamente il
protocollo pre-GPU; questa revisione non lo anticipa.
