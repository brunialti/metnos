# Piano avversariale indipendente pre-scritto — punto 5

Data: 2026-08-12  
Autore: Revisore AI B, indipendente e non umano  
Stato: piano chiuso di 48 controlli; nessun artefatto del nuovo candidato è
stato letto per prepararlo

## Perimetro e autorità

Il piano riguarda soltanto la fetta di laboratorio autorizzata:

- radice tipizzata `unrepresentable`;
- radice tipizzata `system_control`, inizialmente con il solo controllo
  registrato equivalente a `undo_last_turn`;
- `operation_graph` con regioni possedute dalla barriera di approvazione e
  continuazioni tipizzate immutabili.

Le autorità sono il contratto `metnos.intent-shadow/0.1`, il registro ombra
con payload
`7a4f2916af8963d8d16fb79cb355d23e9739163f2241a84fa28bc6c070ed4a0a`,
l'oracolo canonico con payload
`2cdec2776fa48594db8729ec1aa17a635956a61f64b2ce7ad00aa32f8cc0427f`,
il checkpoint e il §24 dell'handover. Gli output storici del modello non sono
gold. La validità strutturale non attribuisce accuratezza.

Il percorso del candidato, quando la build sarà dichiarata chiusa, viene
indicato sotto come `CANDIDATE_ROOT`. Prima di allora questo piano non presume
nomi di file o simboli del candidato. I simboli canonici utili come riferimento
sono `verify_oracle.py::{read_json,validate_expected,validate_body,
validate_data_edges}` e `verify_registry.py::{build_expected_registry,
validate_registry}`; non sono autorizzati come scorciatoia per leggere il gold
durante l'analisi.

Ogni mutazione usa copie in `/tmp` o valori in memoria. Nessun controllo
scrive produzione, banco, oracle, registro o freeze canonici; nessun controllo
chiama GPU, rete o servizi.

## A. Contaminazione, gold e provenienza

1. **P5-01 — Nessuna query intera incorporata.** Cercare nei sorgenti,
   schema, prompt e artefatti generati del candidato i byte UTF-8 esatti delle
   120 query e dei 4 controlli nuovi, includendo LF e versioni NFC/NFD. **PASS:**
   zero corrispondenze; le query possono comparire soltanto nella fixture di
   ingresso congelata e nell'output grezzo della futura misura.

2. **P5-02 — Nessun lookup per impronta o posizione.** Cercare i 124 SHA-256,
   gli ID `frozen_sample.*`, gli ordinali dei quattro controlli e tabelle con
   cardinalità sospette 120/124. Provare inoltre query mutate semanticamente
   equivalenti con hash diverso. **PASS:** nessuna branca cambia perché
   riconosce hash, indice, lunghezza o posizione del caso.

3. **P5-03 — Nessun lookup occultato.** Cercare copie base64, esadecimali,
   sequenze di codepoint, payload compressi e costruzioni AST che assemblano
   dizionari query→risposta. Intercettare durante i test `hashlib`, decoder e
   apertura di blob non dichiarati. **PASS:** zero materiale capace di
   ricostruire le 124 query o i loro `expected`.

4. **P5-04 — Contaminazione parziale controllata.** Calcolare overlap di
   n-grammi di almeno quattro token fra prompt/schema e le 124 query, con
   normalizzazione Unicode dichiarata e un'eventuale allowlist limitata a
   identificatori tecnici del registro. **PASS:** zero overlap non spiegato da
   chiavi tecniche registrate; l'allowlist è pubblicata e non contiene frasi.

5. **P5-05 — Analyzer e runner non aprono gold.** Eseguire con un filesystem
   spy che fallisce su oracle, review, adjudication, controlli con expected,
   output c10/c11 e output con riparo. **PASS:** analizzatore, normalizzatore,
   validatore, proiettore e runner completano i test senza alcuna apertura; il
   solo valutatore offline può aprire l'oracolo dopo il gate pre-gold.

6. **P5-06 — I 34 controlli legacy non diventano verità tipizzata.** Mutare
   `tuple` ed `expect_binding` in `question_focus_controls_v1.json` lasciando
   invariata la query. **PASS:** l'uscita del candidato non cambia e nessun
   adattatore li converte in `operation_graph`, `system_control` o
   `unrepresentable`. Se usati, i 34 restano un pannello legacy separato; il
   loro inserimento nella futura misura richiede un manifest sperimentale
   esplicito, non una conversione automatica.

7. **P5-07 — I quattro controlli nuovi non sono eccezioni.** Rimuovere dal
   processo ogni ID/ordinal del controllo, permutare l'ordine e sostituire il
   testo con parafrasi non presenti nelle fonti. **PASS:** il percorso resta lo
   stesso e non esistono branch dedicati ai quattro casi.

8. **P5-08 — Provenienza chiusa.** Generare l'inventario delle dipendenze
   caricate e confrontarlo con il freeze del candidato. **PASS:** ogni sorgente
   è dichiarata e improntata; nessun import, file temporaneo o variabile
   d'ambiente può introdurre gold o codice non congelato.

## B. Separazione delle fasi e tassonomia degli esiti

9. **P5-09 — Output grezzo immutabile.** Conservare byte e digest prima della
   decodifica; rendere ricorsivamente immutabile una copia sentinella del JSON.
   **PASS:** normalizzazione, validazione e proiezione non cambiano né byte né
   oggetto emesso.

10. **P5-10 — Normalizzazione con allowlist esatta.** Confrontare ricorsivamente
    grezzo e forma normale. Sono ammessi soltanto ID derivati dalla posizione,
    casi vuoti omessi materializzati dal registro, porte univoche derivate e
    impronte canoniche. **PASS:** ogni altra differenza è errore e nessuna fase
    lavora in place.

11. **P5-11 — Il normalizzatore non ripara significato.** Mutare una rotta, un
    controllo, una ragione, una barriera o un esito con alias plausibile,
    traduzione, typo e valore fuori registro; togliere `kind`; spostare
    un'azione dentro o fuori una barriera. **PASS:** tutti i casi restano
    invalidi; nessun alias viene sostituito e nessun nodo viene aggiunto,
    rimosso o riordinato.

12. **P5-12 — Validatore cieco alla query.** Eseguire lo stesso documento con
    query differenti e la stessa query con documenti differenti, bloccando
    accesso a query e gold. **PASS:** la validità dipende soltanto da documento,
    contratto e registro; firme e closure del validatore non accettano testo,
    indice, hash query o expected.

13. **P5-13 — Proiettore cieco alla query.** Ripetere l'attacco P5-12 sul
    proiettore e sull'adattatore di valutazione. **PASS:** il proiettore produce
    solo tipi derivati dalla forma normale; l'adattatore può confrontare col
    gold ma non può correggere o ricostruire il documento.

14. **P5-14 — Esiti tecnici separati.** Iniettare timeout, output vuoto, JSON
    rotto, schema invalido, limite superato, errore di normalizzazione e
    proiezione fallita. **PASS:** nessuno diventa `unrepresentable`; trasporto,
    decodifica, validazione e proiezione hanno stati e contatori distinti.

## C. Radici, accuratezza e mutazioni

15. **P5-15 — Tre radici valide ed esclusive.** Validare un esempio minimo
    generato dal registro per ciascuna radice. **PASS:** esattamente
    `OperationGraph`, `SystemControl` e `Unrepresentable`, senza lista vuota o
    coppia `verb/object` sostitutiva.

16. **P5-16 — Radici miste respinte.** Aggiungere a ogni radice campi delle
    altre due e costruire una richiesta con controllo più operazione.
    **PASS:** ogni combinazione è rifiutata; non viene appiattita o divisa in
    modo implicito.

17. **P5-17 — Oggetti chiusi alla radice.** Provare `kind` assente, ignoto,
    booleano o numero; campo extra; body mancante/vuoto; root array/null.
    **PASS:** tutti respinti con codice stabile e nessuna eccezione non gestita.

18. **P5-18 — Ragioni guidate dal registro.** Per ogni reason registrata
    costruire una radice valida; provare reason vuota, libera, tradotta, alias e
    fuori registro. **PASS:** sono accettate esattamente le chiavi del registro
    iniettato; nessuna ragione è codificata nel validatore.

19. **P5-19 — Composto fail-closed senza sottografo parziale.** Usare un
    composto sintetico con una clausola rappresentabile e una indispensabile
    fuori registro; fornire come mutante il solo nodo rappresentabile.
    **PASS:** l'oracolo valuta corretto soltanto l'intero
    `unrepresentable/outside_registry`; il sottografo parziale è inaccuratezza
    anche se sicuro e formalmente valido.

20. **P5-20 — Controllo guidato dal registro e input chiusi.** Eseguire tutti i
    controlli di radice registrati; provare controllo ordinario, ignoto,
    input runtime-owned, `inputs={}` non ammesso e campo operation aggiunto.
    **PASS:** passa soltanto il contratto esatto e senza input model-facing non
    dichiarati.

21. **P5-21 — Nessun hardcoding di undo.** Rinominare il solo controllo in un
    registro sintetico coerente, rigenerare schema/prompt e ripetere il flusso.
    **PASS:** il nuovo ID funziona, quello storico fallisce e il codice core non
    cambia.

22. **P5-22 — Sicurezza non equivale ad accuratezza.** Valutare stop prudente
    al posto di una rotta rappresentabile, rotta generale col significato
    sbagliato e operazione di lettura sbagliata. **PASS:** sono errori di
    accuratezza; un eventuale indicatore di sicurezza non attribuisce credito
    alla rotta.

23. **P5-23 — Mutazione soltanto per effetto reale.** Valutare ricerca/lettura
    sbagliata, stop, azione di scrittura falsa e azione protetta resa
    incondizionata. **PASS:** soltanto le ultime due contano come mutazione non
    richiesta; errore di accuratezza e mutazione restano colonne indipendenti.

## D. Grafo dati, barriera e continuazione

24. **P5-24 — Nodi e route chiusi.** Provare node kind ignoto, operation con
    campi extra, route ignota e barriera usata come operation. **PASS:** tutti
    respinti; ogni route valida proviene dal registro iniettato.

25. **P5-25 — Archi mancanti, self e forward.** Su grafi di due o tre
    operazioni provare `from` inesistente, uguale al destinatario, futuro,
    negativo e booleano. **PASS:** tutti respinti prima della proiezione.

26. **P5-26 — Archi fra fratelli e verso l'esterno.** Creare due case della
    stessa barriera; collegare il secondo al producer del primo e un nodo
    esterno a un producer interno. **PASS:** entrambi respinti come sorgenti non
    dominanti.

27. **P5-27 — Dominanza lecita.** Collegare un producer precedente alla
    barriera a nodi in uno o più case annidati. **PASS:** gli archi validano e
    conservano lo stesso ordinale canonico in ogni percorso.

28. **P5-28 — Porte e duplicati.** Provare porta input/output ignota, porta
    omessa quando ambigua e arco duplicato; nel positivo omettere le sole porte
    univoche. **PASS:** negativi respinti e porte univoche materializzate senza
    cambiare route o source.

29. **P5-29 — Cicli nel grafo unito.** Costruire cicli dati, controllo e misti,
    inclusi cicli ottenuti dopo normalizzazione di nodi annidati. **PASS:** ogni
    ciclo viene respinto e nessun ordinamento topologico lo “ripara”.

30. **P5-30 — Contratto della barriera.** Provare barriera ignota, cases vuoti,
    outcome ignoto/duplicato/fuori ordine, body emesso vuoto e campi runtime
    (`prompt`, token, `dialog_id`). **PASS:** tutti respinti. L'outcome omesso
    può diventare soltanto il case vuoto previsto dal registro.

31. **P5-31 — Possesso reale del bersaglio.** Duplicare fuori dalla barriera
    una stessa azione protetta, oppure lasciare una barriera senza alcuna
    continuazione non vuota. **PASS:** entrambi respinti; senza l'outcome
    richiesto nessun nodo protetto è raggiungibile.

32. **P5-32 — Barriere annidate.** Provare una barriera dentro un case, con
    riferimenti validi al producer dominante esterno; poi tentare riferimenti
    da un nipote a un ramo cugino. **PASS:** il positivo valida, i cugini sono
    respinti e i `barrier_path` sono deterministici.

33. **P5-33 — Continuation completa e riproducibile.** Per ogni case non vuoto
    verificare esattamente `contract_version`, `registry_sha256`,
    `root_document_sha256`, `barrier_path`, `outcome_ref`, `typed_body` e
    `continuation_sha256`. **PASS:** due costruzioni producono oggetti e hash
    identici; nessun campo runtime entra nell'impronta.

34. **P5-34 — Continuation immutabile e anti-tamper.** Dopo la decisione mutare
    body, ordine, route, outcome, path, root hash o registry hash; mutare anche
    l'oggetto sorgente dopo la proiezione e installare uno spy
    sull'analizzatore. **PASS:** ogni tamper fallisce chiuso, la continuation
    già creata non cambia e la ripresa non richiama analizzatore né query
    originale.

## E. Derivazione, parser, limiti e freeze

35. **P5-35 — Schema e prompt derivati.** Rigenerare entrambi due volte dal
    medesimo registro e confrontare byte/hash con gli artefatti congelati;
    cambiare poi una chiave del registro. **PASS:** stesso registro dà byte
    identici; il registro mutato cambia gli artefatti o viene respinto dal pin,
    senza copie manuali divergenti.

36. **P5-36 — Registro sintetico interamente rinominato.** Rinominare
    controllo, barriera, outcome, reason e almeno due route, conservando forma e
    porte; rigenerare schema/prompt e coprire tutte le radici, una barriera e
    una continuation. **PASS:** flusso end-to-end verde coi nuovi ID e rosso coi
    vecchi. È il controllo principale contro hardcoding di
    undo/approval/reasons.

37. **P5-37 — Nessuna classificazione dedotta.** In un registro sintetico usare
    nomi fuorvianti e capability non indicative. **PASS:** classe di radice,
    operation e barrier seguono soltanto l'annotazione tipizzata del registro,
    mai nome, prefisso, helper o somiglianza.

38. **P5-38 — D-01, chiavi JSON duplicate.** Inserire duplicati concordi e
    contraddittori a root, nodo, edge, case, continuation, registro, batch e
    freeze. **PASS:** ogni lettura JSON li respinge prima di qualunque
    normalizzazione o gold read.

39. **P5-39 — D-02, numeri non finiti.** Inserire `NaN`, `Infinity`,
    `-Infinity` e `1e999`, anche annidati e nei contatori. **PASS:** tutti
    respinti; serializzazione canonica usa `allow_nan=False`.

40. **P5-40 — D-03, tipi esatti e oggetti chiusi.** Mutare bool↔int,
    int↔float/string, null, chiavi extra/mancanti e strutture inattese a ogni
    livello. **PASS:** matrice ricorsiva interamente respinta con messaggi
    deterministici; nessun `isinstance(True, int)` crea un falso zero.

41. **P5-41 — D-04, liste canoniche.** Per liste dichiarate insiemi provare
    duplicato, omissione, aggiunta e sostituzione e accettare il solo riordino;
    per body, outcomes e `data_from`, dove l'ordine è semantico, provare il
    riordino come negativo. **PASS:** unicità/cardinalità sono obbligatorie e la
    politica d'ordine è esplicita per ogni lista.

42. **P5-42 — Limiti tecnici prima dell'allocazione.** Per byte grezzi,
    profondità, nodi, cases, archi, stringhe e dimensione batch provare
    limite−1, limite e limite+1, valori negativi/enormi e nesting ostile.
    **PASS:** i limiti dichiarati sono imposti prima di materializzazioni
    costose; il superamento è errore tecnico, mai `unrepresentable`, e non
    modifica contatori semantici.

43. **P5-43 — Freeze completo e senza circoli.** Verificare hash di codice,
    schema, prompt, registro, normalizzatore, validatore, proiettore, runner,
    valutatore, fixture e test; mutarne uno alla volta e provare path alias,
    `..` e symlink in `/tmp`. **PASS:** ogni deriva è respinta, i path risolvono
    dentro la radice autorizzata e la catena fonti→freeze→lock non ha ritorni.

44. **P5-44 — Determinismo.** Ripetere la suite con ordine casi permutato dove
    non semantico, `PYTHONHASHSEED`, locale e fuso diversi; confrontare output,
    hash, ordine errori e contatori. **PASS:** gli artefatti semantici e i
    verdetti sono byte-identici oppure ogni metadato volatile è escluso e
    dichiarato; niente tempo o casualità influenza l'analisi.

## F. Runner, trasporto finto e valutatore

45. **P5-45 — Offline significa zero trasporto.** Negare socket, URL opener e
    router LLM ed eseguire tutta la suite deterministica. Poi usare un
    trasporto finto per successo, timeout, HTTP errore, JSON rotto, schema
    invalido e risposta troppo grande. **PASS:** offline apre zero connessioni;
    il finto produce esattamente una osservazione per caso e stati tecnici
    distinti, senza retry o fallback silenzioso.

46. **P5-46 — Batch sigillato prima del gold.** Il runner salva soltanto
    ingresso identificato, output grezzo, esiti tecnici e impronte; non apre
    oracle. Dopo il salvataggio mutare un byte, un record, un contatore e
    l'ordine. **PASS:** il valutatore verifica closure, unicità, cardinalità e
    hash prima della prima apertura gold; ogni mutante è respinto con zero gold
    read. Normalizzazione e proiezione non possono riscrivere il batch
    osservato.

47. **P5-47 — Contatori veri e colonne non compensabili.** Duplicare, omettere
    e contraddire record e summary. **PASS:** i contatori sono ricalcolati dai
    record e devono chiudere il denominatore. Il valutatore pubblica
    separatamente: rotta completa, astensione corretta, astensione indebita,
    errore tecnico, mutazione non richiesta, undo, consenso, negazione e rami;
    nessuna somma/media trasforma una regressione di sicurezza in PASS.

48. **P5-48 — Cancello finale pre-GPU e confronto fresco.** Eseguire i 47
    controlli precedenti, verificatori canonici di oracle/registro, test del
    candidato, mutation suite e controllo di integrità pre/post. Congelare
    prima della misura: candidato, controllo fresco, ordine dei due bracci,
    stesse 120 query, trattamento separato dei 4 controlli nuovi, seed, modello,
    budget, timeout, limiti e criterio di arresto. **PASS:** tutto è esplicito,
    hash-pinned e a errore zero; nessuna GPU è autorizzata se esiste un FAIL,
    uno skip, una scelta non fissata o un artefatto d'uscita precedente.

## Criterio complessivo di PASS prima della GPU

Il cancello è verde soltanto se:

- **48/48 controlli PASS**, senza skip, xfail o eccezioni assorbite;
- ogni mutante negativo elencato è respinto e ogni positivo dichiarato è
  accettato; i conteggi attesi sono costanti nel test, non ricavati dal numero
  di risultati prodotti;
- `verify_oracle.py`, `verify_registry.py` e l'audit delle fonti terminano con
  `error_count=0`, senza cambiare oracle, registro o freeze canonici;
- il test col registro sintetico rinominato è verde e gli ID vecchi sono
  respinti;
- contaminazione e aperture gold pre-gate sono zero;
- batch, contatori, continuation e freeze superano i mutanti risigillati
  applicabili;
- banco, produzione, servizi e processi GPU non cambiano;
- eventuale impiego dei 34 controlli legacy è dichiarato come pannello
  separato e non come oracle tipizzato; ogni scelta diversa viene fermata e
  sottoposta a Roberto;
- il protocollo del singolo confronto GPU è congelato prima di creare il suo
  output e non può essere modificato a posteriori.

## Limiti del piano

La matrice è intenzionalmente finita: 48 controlli, con le sole varianti
elencate. Non prova assenza matematica di ogni bypass né accuratezza fuori da
120 richieste, 4 controlli nuovi e registro 0.1. I limiti di dimensione e
durata sono proprietà tecniche del candidato: devono essere dichiarati e
provati prima della GPU, ma non possono cambiare il significato di
`unrepresentable`. Scadenza reale del consenso, esecuzione della continuation,
integrazione runtime e produzione restano fuori dal punto 5.
