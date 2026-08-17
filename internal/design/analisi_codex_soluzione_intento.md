# Analisi indipendente — esiste una soluzione per l'intento senza lessici di instradamento?

Data: **11 agosto 2026**.

Perimetro: analisi dei 46 casi classificati come «nuovo peggiore» in
`internal/tools/request_analysis_lab/misure_11_8/confronto_intento.json`, del
referto del confronto e dei contratti correnti. Nessuna modifica al runtime,
nessuna nuova inferenza, nessun commit.

## 1. Risposta breve

**Esiste una soluzione architetturale credibile, ma non esiste ancora una
soluzione dimostrata.** Non è una nuova formulazione del prompt e non è una
collezione più elegante di sinonimi. È questa separazione:

1. il modello produce un grafo semantico compatto, capace di rappresentare
   modalità dialogica, relazioni, più pazienti, annidamento, controllo,
   aggregazione, sorgenti e ciclo di vita;
2. un registro tecnico piccolo e revisionato descrive quelle relazioni;
3. un registro di proiezione, compilato dai manifest firmati, risolve il grafo
   in executor, prerequisiti e collegamenti;
4. validatore e proiettore sono deterministici e non leggono mai il testo
   dell'utente, lemmi, glosse, `affinity` o dati localizzati.

Questa soluzione è conforme all'i18n per costruzione: il linguaggio naturale è
interpretato dal modello; il codice ragiona soltanto su identificatori, tipi e
contratti tecnici. La direzione è anche sostenuta dall'unico risultato positivo
della serie: `riparo_ruolo.py` corregge una proprietà totale fuori dal prompt,
guadagna tre casi e non ne rompe nessuno.

Il candidato V23lite attuale, invece, **non può essere riparato fino alla
parità con soli ritocchi deterministici sul suo frame**. In almeno 31 dei 46
casi l'informazione necessaria è assente o è già semanticamente sbagliata. Un
post-processore senza parole non può ricostruirla onestamente.

Quindi i due significati di «esiste» vanno distinti:

- **sì**, esiste un meccanismo generale che può raggiungere l'obiettivo senza
  reintrodurre liste linguistiche;
- **no**, oggi non c'è evidenza sufficiente per affermare che quel meccanismo
  raggiunga la qualità richiesta sul modello locale e sul catalogo intero.
  Oggi le voci cancellabili restano zero.

## 2. Metodo e criterio di conteggio

Ho ricostruito ogni caso dal frame grezzo, non soltanto dalla motivazione del
referto. Per avere un denominatore verificabile, assegno ogni caso a **una causa
primaria**: la prima lacuna che impedisce alla rotta nuova di essere interamente
corretta. La partizione somma esattamente a 46.

Alcuni casi hanno anche difetti secondari. Per esempio l'indice 38 sbaglia sia
la sorgente (`find/entries` inventato) sia il ciclo di vita del foglio; l'indice
64 sbaglia il dominio della sessione web e duplica creazione e scrittura. I
conteggi trasversali sono dichiarati separatamente e non modificano il totale.

| causa primaria | casi | indici |
|---|---:|---|
| tipo di risorsa, dominio o sorgente sbagliati | **17** | 0, 36, 38, 39, 45, 60, 63, 72, 76, 82, 91, 93, 98, 100, 110, 114, 115 |
| sessione web e prerequisiti non rappresentati | **8** | 31, 42, 50, 55, 64, 65, 96, 104 |
| ciclo di vita di un artefatto nuovo | **4** | 49, 54, 67, 73 |
| approvazione e ramo condizionale persi | **4** | 30, 59, 69, 77 |
| cardinalità o annidamento non esprimibili | **5** | 32, 56, 58, 79, 95 |
| modalità dialogica o controllo di sistema assenti | **4** | 25, 48, 62, 66 |
| operatore, aggregazione o presentazione confusi | **4** | 24, 53, 84, 90 |
| **totale** | **46** | |

Tre dati trasversali sono particolarmente importanti:

- **6 regressioni di sicurezza o controllo**: due undo trasformati in mutazioni
  e quattro approvazioni eliminate;
- **3 perdite identiche di un secondo bersaglio**: file e directory diventano
  soltanto file;
- **5 frame contengono già la firma completa di un foglio nuovo**
  (`new_resource`, sink `files`, qualificatore `spreadsheet`) ma producono
  `write/files`: indici 38, 49, 54, 67 e 73.

## 3. Causa 1 — tipo di risorsa, dominio o sorgente sbagliati: 17

### Evidenza

Questa è la famiglia maggiore. Comprende:

- contenuto visivo scambiato con registro delle persone (0);
- preferenze scambiate con `entries` o file (36, 72, 98);
- risorse web/GitHub scambiate con file locali o record generici (39, 63, 76,
  82, 91, 100);
- Google Photos scambiato con filesystem generico (45, 114);
- salute macchina, credenziali, calendario contenitore e posizione personale
  proiettati rispettivamente su file, file, eventi e persone (60, 93, 110,
  115);
- sorgente non dichiarata inventata come store `entries` (38).

Il difetto nasce già nei `semantic_heads`, non soltanto nell'ultima coppia
verbo/oggetto. Negli indici 0, 45, 60, 93, 110, 114 e 115 il `patient_object`
è sbagliato o assente; in 36 e 72 non esiste alcun patient e la seconda fase
inventa comunque un dominio; in 63, 76, 82 e 91 il corpus remoto è già stato
ridotto a `entries`.

### Quanto è riparabile dal frame attuale

Soltanto **due casi sono già dimostrati riparabili** senza rileggere il testo:
39 e 100. Entrambi hanno un carrier `urls` autorevole che contraddice il
patient `files`. Ho riprodotto in sola lettura il proiettore congelato V24.1
sui 46 frame: corregge entrambi a `read/urls`.

Lo stesso replay mostra il limite. Non corregge gli altri 15; trasforma inoltre
il primo anello corretto dell'indice 24 da `find/issues` a `find/urls`, perché
la regola generica di autorità del carrier è troppo larga. Agli indici 76 e 82
sposta `entries` verso `urls`, ma la rotta resta sbagliata: il dominio richiesto
è `issues`. Un proiettore generico può usare soltanto ciò che il frame prova.

L'indice 38 è anche realmente sottospecificato: la frase non dichiara dove
cercare le fatture. Inventare `entries`, mail o file sarebbe una congettura. Un
sistema corretto deve conservare l'incertezza e chiedere chiarimento, non
ottenere un punto scegliendo una sorgente arbitraria.

### Meccanismo proposto

Il modello non deve emettere direttamente sia `patient_object` sia la rotta di
prodotto. Deve scegliere una **relazione semantica tipizzata** e i suoi
argomenti; azione, ruoli, tipi d'uscita e route vengono derivati dal registro.
Servono almeno:

- tipo del patient distinto da contenitore, rappresentazione e fornitore;
- tipo della sorgente e sua autorità (`source_owner`, locator, contesto);
- legame di domanda per capacità speciali, come posizione corrente o salute
  macchina;
- distinzione contenitore/record (`calendars`/`events`) e
  registro/corpus (`persons`/`images`);
- stato esplicito `unknown` quando la sorgente manca.

Questi non sono sinonimi. Sono ID e compatibilità tecniche revisionate a
partire dagli executor attivi. Il proiettore applica soltanto contratti
provati; una collisione irrisolta produce chiarimento o arresto prudente.

### Giudizio

**Riparabile architetturalmente, non riparabile in generale sul frame V23lite
attuale.** Quindici casi richiedono che l'analizzatore emetta una distinzione
semantica nuova e corretta. Il proiettore non può inventarla.

## 4. Causa 2 — sessione web e prerequisiti non rappresentati: 8

### Evidenza

Negli indici 31, 42, 50, 55, 64, 65, 96 e 104 la richiesta opera dentro una
sessione con stato, ma il frame conserva pezzi incompatibili:

- `login/urls` invece di `login/sites`;
- `open/urls` invece di `open/sites`;
- `act/files` o `find/entries` dopo login;
- prenotazioni lette come eventi di calendario nonostante un arco dal login;
- lettura post-login con oggetto `none` e conseguente frame invalido.

Gli archi sono quasi sempre presenti. Il problema è che la seconda fase tratta
ogni oggetto localmente e non usa il tipo d'uscita del produttore. L'indice 31
aggiunge una duplicazione strutturale: head e predicato discordano sullo stesso
arco, e il validatore respinge tutto.

### Meccanismo proposto

Questa famiglia è il miglior candidato a un riparo interamente deterministico:

1. il contratto di `open_sites` dichiara che produce `session_id` di tipo
   sessione web;
2. `login_sites`, `act_sites` e `read_sites` dichiarano quel tipo come input;
3. il compilatore costruisce i prerequisiti e la compatibilità
   produttore-consumatore;
4. un nodo dipendente da una sessione non può diventare `urls`, `files`,
   `entries` o `events` senza una sorgente indipendente esplicita;
5. `input_from_predicate_id` è derivato una volta sola dal grafo, non chiesto
   due volte al modello.

Il risolutore non cerca parole come “accedi” o “sito”. Usa relazione, tipo
dell'arco e contratti di I/O firmati. Può anche inserire `open_sites` come
prerequisito di `login_sites` senza considerarlo una seconda intenzione
linguistica.

### Giudizio

**Riparabile con alta plausibilità dal contenuto strutturale già presente**, ma
non ancora dimostrato sui 120. Sono otto casi candidati, non otto punti già
guadagnati: manca un registro revisionato e la prova di assenza di regressioni
su richieste che cambiano davvero dominio dopo aver aperto un sito.

## 5. Causa 3 — ciclo di vita di un artefatto nuovo: 4

### Evidenza

Negli indici 49, 54, 67 e 73 il primo anello è corretto. Il secondo contiene
già tutti i fatti necessari:

- `resource_scope = new_resource`;
- sink `primary_output:files`;
- `object_qualifier = spreadsheet`;
- dipendenza dal risultato precedente.

Nonostante ciò la rotta è `write/files`; il catalogo richiede la creazione del
nuovo foglio. L'indice 38 ha lo stesso difetto, ma resta nella prima famiglia
perché inventa anche la sorgente. L'indice 64 ha una variante: emette prima
`create/files` e subito dopo un `write/files` ridondante sullo stesso artefatto.

### Meccanismo proposto

Il ciclo di vita appartiene al contratto dell'artefatto:

- relazione `create_artifact` + `new_resource` + tipo d'uscita qualificato
  selezionano la proiezione di creazione;
- una relazione di popolamento sul medesimo artefatto può diventare argomento
  della creazione o aggiornamento successivo soltanto se il contratto lo
  richiede;
- una coppia create/write sullo stesso sink e sullo stesso arco viene
  normalizzata secondo ownership e durata dichiarate, non secondo una lista di
  verbi scritta nel proiettore.

Sul campione, la firma strutturale `write/files_spreadsheet + new_resource`
compare soltanto nei cinque casi regressivi 38, 49, 54, 67 e 73. È un ottimo
candidato a una misura isolata: il meccanismo è totale e non ha bisogno del
testo.

### Giudizio

**Riparabile deterministicamente sul frame attuale.** Quattro casi diventano
interamente corretti; il quinto, 38, migliora ma resta irrisolto per la
sorgente mancante. Il guadagno netto deve comunque essere misurato, non dedotto
dal solo campione osservato.

## 6. Causa 4 — approvazione e ramo condizionale persi: 4

### Evidenza

Gli indici 30, 59, 69 e 77 perdono `get/approval` e/o l'aggiornamento della
issue. Il frame piatto rappresenta la proposizione condizionale come record
`condition`, poi `riparo_ruolo.py` azzera correttamente verbo e oggetto perché
un record non-request non deve essere eseguibile. Il contenuto del ramo, però,
non ha un altro posto dove vivere.

Il risultato non è soltanto meno preciso. In 69 la richiesta di consenso
diventa `send/messages`; in 77 l'approvazione scompare e l'invio è duplicato.
È una perdita di una barriera di sicurezza.

### Meccanismo proposto

L'approvazione non è una rotta lineare fra due rotte. Serve una relazione di
controllo con struttura simile a:

```text
decisione umana
  ramo accettato -> sottografo tipizzato
  ramo rifiutato -> nessun effetto
```

Il registro dichiara `get_approval` come barriera di consenso e l'azione
condizionata come sottografo, con un arco di controllo diverso dagli archi di
dati. Se l'analizzatore segnala una condizione di approvazione ma non produce
un ramo valido, il validatore deve arrestare l'esecuzione; non può convertirla
in un messaggio né lasciar passare la mutazione.

Le due query che nominano identificatori tecnici possono inoltre essere
verificate contro l'inventario degli executor. Un identificatore canonico
esatto è dato tecnico, non una parola di una lingua; non deve però diventare un
ripiego per le richieste naturali 69 e 77.

### Giudizio

**Riparabile soltanto con un grafo di controllo nuovo.** Il frame attuale non
conserva abbastanza informazione per ricostruire onestamente i quattro casi.

## 7. Causa 5 — cardinalità o annidamento non esprimibili: 5

### Evidenza

Negli indici 32, 56 e 58 un solo predicato ha due pazienti coordinati, file e
directory. Lo schema ammette un solo `patient_object`; il modello sceglie file
e perde sistematicamente directory. Il carrier `dirs` indica il contenitore in
cui cercare, non prova che il contenitore stesso debba essere cancellato: non è
lecito trasformarlo automaticamente in secondo bersaglio.

L'indice 95 mostra un limite diverso dello stesso schema piatto. Le operazioni
che definiscono il corpo di un task sono marcate `description`; alcune
operazioni finali restano invece richieste immediate. Il sistema non distingue
«descrizione non eseguibile» da «sottografo differito che è il contenuto del
task».

L'indice 79 enumera molti predicati, ma perde estrazione e scrittura del
rapporto, inventa passaggi intermedi e non distingue correttamente i due
artefatti finali. Qui non manca spazio nello schema: manca una rappresentazione
di prodotti, campi richiesti, raggruppamento e artefatti come sottografo
coerente.

### Meccanismo proposto

- `patients[]` o una proiezione coordinata per un solo predicato, con un tipo e
  una prova separati per ogni paziente;
- clausole annidate con `execution_scope = immediate | deferred_task_body`;
- artefatti finali come nodi con tipo, ownership e dipendenze, non come sink
  accessori del predicato precedente;
- controllo deterministico di copertura, cardinalità e compatibilità degli
  archi dopo l'inferenza.

Lo schema può impedire rappresentazioni incoerenti, ma non può sapere da solo
quanti bersagli sono nominati nel testo. La completezza resta una responsabilità
semantica del modello e va misurata con un oracolo per clausola.

### Giudizio

**Riparabile con una rappresentazione nuova; non riparabile dal frame
attuale.** I tre casi file+directory sono una prova diretta di impossibilità
della cardinalità corrente. Gli indici 79 e 95 restano i più difficili anche
dopo il cambio di schema.

## 8. Causa 6 — modalità dialogica o controllo di sistema assenti: 4

### Evidenza

- 25 e 66: `undo` è fuori da `ACTIONS` per progetto. Il modello è costretto a
  scegliere un verbo ordinario e produce `change/files` o `delete/entries`.
- 48: un nome di deposito nudo non contiene una richiesta; il modello inventa
  un predicato `read` non ancorato a un verbo sorgente.
- 62: «cosa sai fare» appartiene al Tutor, non al motore. Forzarlo nello schema
  azione/oggetto produce `describe/none` e invalidità.

Sono errori di categoria prima ancora che di route.

### Meccanismo proposto

La radice dell'analisi deve essere una unione discriminata:

- grafo di operazioni;
- controllo di sistema registrato, incluso undo;
- richiesta informativa/Tutor;
- riferimento o frammento non eseguibile;
- non supportato o ambiguo.

I controlli sono generati dall'inventario dei builtin, non aggiunti ad
`ACTIONS` e non riconosciuti con parole. Un esito ambiguo non deve mai ricadere
su una mutazione. Per undo e altri controlli sensibili la policy deve essere
prudente: il ramo operativo ordinario resta vietato finché il controllo non è
riconosciuto con sufficiente evidenza.

### Giudizio

**Riparabile solo prima della proiezione verbo/oggetto.** Nessun
post-processore del frame corrente può distinguere in modo generale undo,
frammento e Tutor senza rileggere linguisticamente la richiesta.

## 9. Causa 7 — operatore, aggregazione o presentazione confusi: 4

### Evidenza

- 24: il conteggio delle righe appena inserite diventa
  `describe/entries`; manca il tipo `cardinality`.
- 53: l'enumerazione del contenitore task diventa `find/tasks`; anche lo scope
  è stato classificato come criterio di ricerca.
- 84: corpus con indice immagini e numero di foto diventano
  `find/entries -> describe/images`; mancano sia il dominio dell'indice sia
  l'aggregazione.
- 90: «dammi i primi 5» viene emesso come un secondo `get/processes`, benché
  abbia scope `held_result` e un arco dal primo identico snapshot.

### Meccanismo proposto

Il registro semantico deve distinguere:

- enumerazione di contenitore, ricerca per criterio e lettura di elemento noto;
- aggregazioni tipizzate, almeno cardinalità e selezione dei primi N;
- modificatore di presentazione da nuova acquisizione;
- dominio degli indici come tipo tecnico, non come `entries` universale.

L'indice 90 è già riparabile con una normalizzazione totale: stesso
action/object, `held_result` e arco dal nodo precedente implicano che il secondo
nodo non è una nuova acquisizione. Sul campione questa firma compare una sola
volta. Gli altri tre casi richiedono claim semantici che il frame non contiene.

### Giudizio

**Un caso riparabile ora, tre soltanto con output semantico più ricco.**

## 10. Bilancio di riparabilità dei 46

La distinzione più utile non è «facile/difficile», ma «l'informazione esiste
già/non esiste».

| stato | casi | natura |
|---|---:|---|
| già dimostrati dal proiettore congelato V24.1 | **2** | carrier URL autorevole: 39, 100 |
| struttura sufficiente, meccanismo nuovo da implementare e misurare | **13** | 8 sessioni web, 4 cicli di vita, 1 duplicato |
| almeno una distinzione necessaria assente o sbagliata nel frame | **31** | nuova inferenza/nuovo schema obbligatori |
| **totale** | **46** | |

Il numero 15 non è una previsione di punteggio. È un limite di lavoro sul frame
esistente: due recuperi osservati e tredici candidati strutturali. I restanti
31 non possono essere corretti onestamente da una cascata deterministica che
non legga il testo. Aggiungere regole per gli indici specifici sarebbe soltanto
un dizionario mascherato.

## 11. La soluzione proposta, in forma completa

### 11.1 Inventario e contratti prima del modello

Il primo blocco non è l'inferenza ma il catalogo. Oggi il censimento rileva
**0 `intent_contract` su 103 manifest** e disaccordo fra snapshot e checkout.
Finché questo resta vero, nessun proiettore catalog-derived può essere autorità
di produzione.

Ogni capacità attiva deve dichiarare, con revisione tecnica e firma:

- relazione ed effetto implementati;
- ruoli e tipi di paziente, sorgente, destinazione e destinatario;
- tipo, cardinalità, titolarità e durata dell'uscita;
- prerequisiti e compatibilità produttore-consumatore;
- ciclo di vita dell'artefatto;
- consenso, criticità ed effetti;
- regola di proiezione e stato di revisione.

Il compilatore produce un registro semantico deduplicato, un registro di
proiezione e una vista compatta hashata per l'analizzatore. Nessuna descrizione
localizzata entra nel proiettore o nel validatore.

### 11.2 Un solo grafo, nessuna rotta duplicata dal modello

V23lite fa emettere al modello prima i `semantic_heads` e poi anche i
`predicates` di prodotto. Questa duplicazione consente contraddizioni e obbliga
il prompt a insegnare il catalogo. Va eliminata.

Il modello deve emettere soltanto:

- modalità radice;
- clausole e loro ambito immediato/differito;
- relazione semantica per ordinale di registro;
- argomenti tipizzati, inclusi più pazienti;
- archi di dati e di controllo;
- ciclo di vita, aggregazione e prove sorgente;
- `unknown` espliciti.

Verbo, oggetto, executor, fornitore, prerequisiti e campi derivabili non devono
essere generati. Il proiettore li ricava dal registro e rifiuta ogni ambiguità.

### 11.3 Ripari deterministici ammessi

Sono ammessi perché totali e indipendenti dalla lingua:

- derivare ID, archi duplicati e campi di ruolo invece di farli ripetere al
  modello;
- azzerare l'eseguibilità dei ruoli non-request;
- controllare arità, cardinalità, ordine, tipi e compatibilità;
- proiettare relazione -> executor tramite contratti revisionati;
- inserire prerequisiti da firme I/O;
- normalizzare create/write tramite ciclo di vita e titolarità;
- deduplicare una presentazione dipendente da un'acquisizione identica;
- imporre approvazione e arresto prudente tramite archi di controllo;
- chiedere chiarimento quando un argomento obbligatorio resta `unknown`.

Non sono ammessi matcher sulla query, sinonimi, suffissi, stopword, nomi di
dominio usati come indizi semantici o eccezioni per singolo test.

### 11.4 Perché non è «altro testo nel prompt»

Le otto prove negative riguardano la prosa che cerca di persuadere il modello
a rispettare confini. Qui i confini vengono:

- rappresentati nella forma dell'output;
- derivati da un registro tecnico;
- verificati dopo l'inferenza;
- applicati da codice che non vede il testo.

Il prompt può restare minimo e definizionale. Aggiungere un'altra spiegazione
su preferences, sessioni, fogli o undo non fa parte della proposta.

## 12. Cosa non è riparabile e perché

### 12.1 Non è riparabile il significato già perso

`find/persons` all'indice 0, `write/files` per Google Photos, `create/events`
per un calendario e `get/persons` per la posizione sono tutti frame validi.
Dal solo frame non esiste una prova tecnica che autorizzi la sostituzione.
Correggerli richiede una nuova decisione semantica del modello.

### 12.2 Non è riparabile una rappresentazione incapace

Un solo `patient_object` non può rappresentare due bersagli; una lista piatta
di ruoli non può rappresentare il corpo differito di un task o il ramo di
un'approvazione. Il validatore può respingere, non recuperare informazione mai
emessa.

### 12.3 Non è riparabile l'ambiguità reale con una scelta nascosta

La sorgente delle fatture nell'indice 38 non è detta; il deposito nudo
dell'indice 48 può essere un riferimento contestuale, non un comando. Senza
contesto sufficiente, il comportamento corretto è il chiarimento o la non
esecuzione. Una route inventata non è qualità.

### 12.4 Non è sufficiente vietare coppie illegali

La legalità del catalogo può eliminare `login/urls` o `open/urls`, ma non dice
se `preferences` debba diventare file, entries o altro; né distingue issue
remote da record locali. Un filtro di legalità aumenta la sicurezza, non
risolve da solo la semantica.

### 12.5 Non è sufficiente il campione da 120

Il confronto misura soltanto cinque scorciatoie lessicali attivate e nessun
caso `system.status_query`. Inoltre almeno un accordo è noto essere sbagliato
in entrambi i rami: la domanda sulla data odierna diventa `get/numbers`, mentre
il percorso lessicale la corregge a `get/now`. Correggere i 46 disaccordi non
dimostrerebbe quindi la sostituibilità dei 1.030 slot censiti.

La prova di cancellabilità deve includere tutti i consumatori di instradamento,
non soltanto `extract_intent`, e deve distinguere le forme condivise con usi
non di instradamento. Per esempio `health.section_focus` può restare necessario
per scegliere una sezione della risposta anche dopo essere sparito dal
percorso dell'intento.

## 13. Criterio di prova prima di cancellare il lessico

Una soluzione può essere dichiarata raggiunta soltanto dopo questa sequenza:

1. inventario attivo riconciliato e contratti semantici revisionati per ogni
   capacità;
2. compilatore, validatore e proiettore con test senza testo naturale;
3. nuova analisi sui 120, con oracolo per tutti i casi e non soltanto sui 55
   disaccordi; i 65 accordi possono contenere errori comuni;
4. zero regressioni di sicurezza su undo, approvazione, negazione e rami
   condizionali;
5. prove multilingui e richieste composte, ripetute fuori dalla banda di
   rumore;
6. confronto in ombra sul traffico reale, includendo route, clausole,
   dipendenze, argomenti e chiarimenti;
7. censimento dei consumatori: il nuovo percorso non consulta più query grezza,
   `affinity` o `detection_lexicon` per instradare;
8. soltanto allora rimozione dei rami lessicali. Le voci condivise restano nel
   database se hanno ancora consumatori legittimi non di instradamento.

Va pubblicata anche la latenza. I 3.961 ms mediani del nuovo percorso contro
310 ms dell'attuale non rendono falsa la soluzione, ma impediscono di chiamarla
pronta per il prodotto. La vista compatta deve togliere dall'uscita tutti i
campi derivabili; non va tentato un nuovo taglio della prosa già misurato in
perdita.

## 14. Giudizio finale

**L'obiettivo è raggiungibile come progetto architetturale, ma non è
raggiungibile correggendo il prompt o aggiungendo una coda di ripari al frame
V23lite corrente.** Le cause non mostrano un bisogno inevitabile di dizionari:
mostrano soprattutto assenza di tipi tecnici, contratti di catalogo,
cardinalità, annidamento e modalità radice. Tutti questi possono stare in
schema, registro e proiettore senza parole della lingua sorgente.

Il rischio non eliminabile è a monte: nei 31 casi senza informazione sufficiente
il modello deve capire correttamente il significato. Non esiste un riparo
deterministico e indipendente dalla lingua che possa sostituire quella
comprensione.
La sola risposta onesta è costruire la rappresentazione che consenta al modello
di dirlo una volta, vincolarla con contratti tecnici e misurarla sul campione
congelato.

Perciò il verdetto operativo è:

- **non proseguire con modifiche di prompt**;
- **non portare V23lite attuale in produzione**;
- **non cancellare oggi alcuna voce o ramo lessicale**;
- **proseguire soltanto con catalogo revisionato + grafo più espressivo +
  proiettore deterministico**;
- considerare l'obiettivo raggiunto solo quando la nuova misura dimostra
  parità o miglioramento e identifica i consumatori di instradamento eliminati.

Questa non è una promessa che il prossimo candidato passerà. È però una strada
causalmente coerente con tutti i 46 regressi e con l'unico riparo che ha già
guadagnato senza rompere casi.
