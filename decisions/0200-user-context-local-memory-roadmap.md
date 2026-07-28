---
id: 0200
title: Conoscenza utente locale fuori dal planner e apprendimento automatico controllabile
date: 2026-07-26
status: accepted   # ratificata da Roberto il 28/7/2026; calendario di F0 deciso separatamente
area: runtime
related:
  - 0182
  - 0185
  - 0187
  - 0193
  - 0196
  - 0199
complements: []
modifies: []
supersedes: []
---

# 0200 - Conoscenza utente locale fuori dal planner e apprendimento automatico controllabile

## Contesto

Metnos possiede già preferenze W2 in `runtime/users.py`, cache L0/L1
condivise, un registro runtime per argomenti server-owned, trasformazioni degli
argomenti dopo il piano, `project_paths.json`, esiti semantici dei turni e il
ciclo W1 che osserva piani riusciti e lacune. Non possiede invece uno store di
memoria personale, un principale canonico utilizzabile dal sottosistema o una
superficie conversazionale per ricordare, ispezionare, correggere e dimenticare.

La prima versione di RM-0001 proteggeva cache e planner, ma era troppo debole
rispetto al risultato di prodotto: poteva cambiare soprattutto presentazione e
disambiguazione post-piano. Una review avversariale del 26 luglio ha inoltre
mostrato che il disegno lasciava senza produttore `authz_revision` e gli
intervalli delle clausole, conservava ripieghi identitari verso `host`, non
chiudeva la corsa fra cancellazione e compilatore, teneva il journal di oblio
nello stesso database da ripristinare e non definiva una prova causale della
memoria utente equivalente a quella prevista per gli hint degli executor.

Le sette lenti Fable non hanno completato la propria refutazione automatica.
Ogni rilievo usato in questa decisione è stato pertanto verificato direttamente
contro RM-0001 e il codice corrente. La roadmap aggiornata è la specifica
completa; questa ADR congela le scelte che non devono essere riaperte durante
l'implementazione senza nuova evidenza.

## Decisione

### Perimetro unico: conoscenza dell'utente

RM-0001 riguarda soltanto preferenze, default operativi, riferimenti, fatti,
decisioni, episodi attestati e routine personali. Memoria dell'esperienza
interna degli executor, suggerimenti di strategia, Leiden, comunità, MCP e
produzione di capacità escono dal documento. Non sono vietati per sempre:
richiedono un caso reale e una decisione separata.

Questo taglio evita due prodotti uniti soltanto dall'uso di SQLite e di un
embedder. Elimina inoltre un secondo canale di proposte concorrente con W1 e
ChangeIntent.

### Principale canonico prima dello store

Ogni operazione futura usa un `PrincipalContext` immutabile creato dai confini
autenticati. La stringa `actor`, il ruolo HTTP ottenuto dalla sola rete locale,
un campo del corpo richiesta e il ripiego `host` non sono prove di identità.
Binding, ruolo e associazione dispositivo alimentano una `authz_revision`
monotona; dialoghi, task e riprese la ricontrollano al consumo.

`TutorPrincipal` diventerà una vista ristretta del principale canonico. Non
verrà creato un secondo albero di identità per la memoria.

### Tre store logici, due database principali

W2 in `users.db` resta autorevole per preferenze di risposta e default
operativi tipizzati. Viene esteso con revisioni, origine completa e scope, non
copiato nel database della memoria.

`user_memory.sqlite` conserva fatti, decisioni, riferimenti, episodi, routine,
evidenze, relazioni e indici. Un journal append-only separato conserva le
cancellazioni senza claim. Non esiste una transazione distribuita fra i due
database: un `UserContextSnapshot` porta la coppia di revisioni e l'epoch
autorevole del journal.

Se un ripristino dispone del journal corrente, lo riapplica prima di rendere lo
store leggibile. Se dispone soltanto di un backup vecchio e non può dimostrare
la continuità del journal, mette in quarantena tutte le memorie ripristinate.
La perdita di disponibilità è preferita alla ricomparsa silenziosa di dati
dimenticati.

### Planner e cache restano condivisi

Claim, profilo, `user_id` e revisioni non entrano nel planner, nel prompt di
sistema, nel vaglio o nelle cache L0/L1. Preferenze e riferimenti riempiono
argomenti soltanto dopo il piano e soltanto quando una dichiarazione firmata del
dominio autorizza quella personalizzazione. Un valore esplicito nel turno
prevale; capability e consenso sono calcolati sul valore finale.

Il precedente è `_RUNTIME_ARG_SOURCES`: verrà esteso a provider che ricevono un
contesto di invocazione ristretto. Il precedente di dominio è
`backend_resolver`, ma la nuova forma non contiene casi per calendario,
cartella o provider nel runtime centrale. Ogni manifest firmato dichiara
argomento, chiave semantica, scope e provider dei valori validi.

### Riferimenti e routine

Un ReferenceSlot sceglie soltanto fra candidati prodotti dal provider nel
turno. Il primo provider è `runtime/project_paths.json`; il caso Atlas non è
più differito. Ogni riferimento a un identificatore riutilizzabile porta
un'ancora di continuità e torna a chiedere soltanto se il candidato è sparito,
ha cambiato identità o non è più univoco.

Le routine personali sono l'unico effetto prima della cache. Una richiesta
ellittica può diventare una sequenza canonica di verbi, oggetti e nomi di slot
prima di L0, ma la forma non contiene valori personali. Cartelle, account,
calendari e destinatari vengono risolti dopo il piano. Se la scelta richiede
prosa del profilo o non produce un solo candidato, il sistema si astiene.

Questa scelta rende possibili richieste come «prepara le solite cose» senza
rendere per-utente la cache dei piani. Le osservazioni di turni riusciti vengono
riusate da W1; non nasce un secondo osservatore globale.

### Automatico significa nessuna approvazione per elemento

Dopo la promozione della fase dedicata, l'apprendimento implicito non sensibile
è attivo per il proprietario verificato. L'utente riceve una sola informazione
all'attivazione e controlla il sistema con domande, correzioni, oblio e un
interruttore generale. Gli ospiti restano spenti per impostazione predefinita.

La raccolta e l'applicazione sono distinte. Una singola osservazione implicita
può essere visibile ma non influente; evidenze indipendenti e coerenti possono
promuovere un fatto a lettura o una preferenza chiusa a default. Nessun claim
libero diventa autorità o argomento operativo.

Per categorie sensibili, segreti, contenuto esterno, testo dell'assistente,
citazioni, allegati e output tool la scrittura è vietata. Una protezione per i
minori verrà dichiarata soltanto quando il registro utenti avrà un marcatore
verificabile e una regola su chi può impostarlo.

### Evidenza e oblio

La prosa prodotta dal compilatore non è evidenza. Ogni claim cita la clausola
dell'utente o un evento runtime attestato. Gli episodi conservano forma delle
azioni, esito, conteggi e puntatore al TurnLog; non una seconda narrazione LLM.

Ogni cancellazione è un protocollo write-ahead sotto lock del principale. Il
coordinatore risolve prima gli ID opachi, alloca l'epoch, appende e sincronizza
`PREPARED` nel journal separato, esegue cancellazioni idempotenti negli store,
poi appende `COMMITTED`. Un `PREPARED` incompleto viene rieseguito all'avvio o
prima del restore. In questo modo anche il crash fra commit di `users.db`,
commit di `user_memory.sqlite` e append finale non può far risorgere il dato.

Il nuovo epoch invalida tutti gli eventi non completati del principale
osservati prima di esso. Il compilatore lo ricontrolla nella transazione finale.
Questa invalidazione ampia è intenzionale: su un corpus personale piccolo è
più semplice e sicura di una tombstone semantica o di hash a bassa entropia.

**Ampiezza della cancellazione (ratificata il 28 luglio 2026).** Un «dimentica»
su un valore **tipizzato** risolve il bersaglio **per chiave**: muore ogni riga
con quella chiave e quell'ambito, in qualunque contenitore dichiarato —
preferenza, default operativo, riferimento, routine — e in qualunque stato,
quindi anche un gemello già compilato prima della richiesta. Su un **claim
libero** si cancella l'identificativo indicato e ogni claim dello stesso
principale con digest identico; ciò che sopravvive non resta nascosto, perché
l'inventario consegnato subito dopo lo elenca. L'estensione a nuovi contenitori
non è affidata alla memoria di chi implementa: ogni tabella chiavata per
`(chiave, ambito)` si dichiara in un registro, e una prova d'invariante fallisce
se una fase ne aggiunge una senza dichiararla.

**Aggregazione deterministica prima del modello (ratificata il 28 luglio 2026).**
Una domanda aggregata sul profilo risponde con un elenco citato dei record
selezionati, non con prosa generata. È l'applicazione di §7.9: l'inventario
deterministico copre già quasi tutto il caso d'uso, e il compositore locale
sarebbe l'unico modello non necessario del nucleo. Rientra soltanto dopo almeno
tre casi documentati del corpus congelato in cui le fonti recuperate contengono
la risposta ma l'elenco entro budget produce astensione o è giudicato incompleto
da due annotatori indipendenti — cioè il difetto è nella presentazione e non nel
recupero — con ADR separata e misura di danno preregistrata.

### Controllo dalla chat, non executor memories

Una `UserContextBoundary` comune a HTTP e Telegram gestisce comandi diretti e
domande sul profilo prima del planner. Usa il `detection_lexicon` e gli
intervalli prodotti dal segmentatore compound comune. Non introduce l'oggetto
`memories` nel vocabolario e non espone quattro executor in-process con piena
autorità filesystem.

Le pagine amministrative restano una vista supplementare. «Che cosa sai di
me?», correzione e oblio devono funzionare dalla chat.

### Prova causale a tre bracci

Ogni fase che influenza risposta, argomenti o comprensione viene confrontata
sullo stesso replay con: memoria spenta, baseline lineare ultimo-valore-per-slot
e impianto completo. La misura primaria è il numero di interazioni necessarie
per l'esito corretto rispetto a un referente atteso indipendente. Il sistema
completo deve battere anche la baseline lineare.

Il corpus congela prima la partizione rilevante/irrilevante e include trappole
con riferimenti cambiati. Eventi a tolleranza zero dichiarano il denominatore
reale e il limite superiore `3/N`; una misura pari a zero non viene presentata
come prova di rischio nullo.

## Alternative considerate

### Profilo libero nel planner

Avrebbe dato al modello la superficie più ampia, ma rende gli hit L0/L1
dipendenti dall'utente oppure ignora il profilo proprio sugli hit. Aggiunge
inoltre prosa non attendibile al punto che sceglie capacità e forma del piano.
È respinto.

### Solo personalizzazione della risposta

Preserva perfettamente il routing, ma non consente default operativi,
riferimenti utili, continuità episodica o richieste ellittiche. Era il difetto
principale della precedente RM-0001. È respinto come soluzione completa e
conservato come prima capacità progressiva.

### Riscrittura completa della query con dati personali

Potrebbe cambiare ciò che Metnos comprende, ma porterebbe path, account o
destinatari nelle chiavi e nei framework della cache. È respinta. Resta soltanto
la riscrittura povera della forma di routine, senza valori.

### Un unico database per tutto

Semplificherebbe alcune transazioni, ma duplicarebbe W2 o mescolerebbe cicli di
vita diversi. Si mantengono due database con snapshot di revisioni e un journal
separato per l'oblio.

### Conferme per ogni inferenza

Riduce alcuni falsi positivi ma contraddice l'obiettivo automatico e trasforma
il controllo in un flusso di approvazioni. È respinta per conoscenza non
sensibile e reversibile. Consensi mutanti/outbound restano invece intatti,
perché appartengono all'autorità dell'azione, non alla memoria.

### Esperienza executor e Leiden nello stesso programma

Condividono poche primitive e non sono necessari ai casi utente. Leiden non ha
un consumatore operativo distinto dalle proposte già esistenti e la scala
osservata è minima. Entrambi escono da RM-0001; una riapertura richiede casi e
misure, non una soglia arbitraria di nodi.

### Adozione diretta di Swafra o del suo MCP

Il sito e il repository Swafra sono stati verificati al commit
`24dba18a4194aef0cb0d6d6c68cf46e6fcbf2da7`. Offrono un archivio documentale
locale con chunking Leiden, retrieval ibrido, grafo e sei tool MCP, ma non il
principale, la provenienza, la policy dei dati sensibili, il reconciler e il
protocollo di oblio richiesti da Metnos. L'interfaccia invita inoltre l'agente
a salvare proattivamente testo eterogeneo e a recuperarlo a inizio sessione:
un confine di fiducia incompatibile con questa decisione.

Il dettaglio verificabile dell'audit è conservato in
`internal/reports/rm0001-swafra-primary-audit-20260726.md`.

Il benchmark non cambia il verdetto. L'artefatto versionato come `@10` calcola
la metrica su 28-46 risultati per caso e tronca a dieci soltanto il campo
serializzato. Dai primi dieci conservati risultano 434/470 `recall_all` e
464/470 `recall_any`; il codice corrente non può produrre i conteggi
dell'artefatto e manca un'ablation che isoli Leiden. Il numero pubblicizzato
non è quindi prova sufficiente per adottare grafo o clustering.

Sono conservate come ipotesi F4 il funzionamento locale, il confronto di un
retrieval ibrido e la diversità per sorgente entro un `k` reale. Non si importa
il pacchetto, non si espone MCP e non si riapre Leiden senza evidenza Metnos.

## Conseguenze

RM-0001 passa da `active` a `ready`: i confini sono sufficienti per iniziare
F0, ma nessuna funzione descritta diventa corrente per effetto della decisione.
L'implementazione procede per F0-F6 e ogni fase ha un interruttore, una ADR,
una prova causale e un criterio di ripiego.

Il lavoro iniziale cresce rispetto a una semplice tabella memoria: bisogna
correggere la catena d'identità, produrre revisioni autorizzative, aggiungere
intervalli di clausola e progettare restore/quarantena. Questo costo è
necessario perché il sottosistema conserva dati tra sessioni e canali.

Il cammino complessivo si riduce: spariscono executor memories, store e
compiler dell'esperienza, harness Leiden, dense retrieval obbligatorio, MCP e
un secondo canale di proposte. Exact e FTS5 precedono qualunque derivatore.

La documentazione pubblica non cambia finché una fase non è implementata e
certificata. Il Tutor non deve presentare le capacità della roadmap come
funzioni correnti.

## Ratifica

Ratificata da Roberto il 28 luglio 2026, insieme alle due decisioni di merito
scritte sopra. La ratifica **non avvia F0**: fissa le scelte perché non vengano
riaperte durante l'implementazione, e il calendario resta una decisione separata.

RM-0001 porta da quella data uno strato di attuazione (§13) con contratti,
schemi, punti d'innesto verificati riga per riga, ordine di costruzione,
interruttori e prove per ciascuna fase, più sedici decisioni trasversali che
governano l'estensione dello schema, l'unicità dei vocabolari chiusi, la
monotonia degli interruttori e il divieto verificabile di auto-rinforzo. Quel
capitolo dichiara anche i quattro punti in cui il lavoro tocca il nucleo senza
essere protetto da un interruttore, con il rispettivo contenimento (§13.9): sono
correzioni a difetti esistenti, e vanno dimostrate invarianti, non nascoste.
