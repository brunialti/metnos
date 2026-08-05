---
id: 0206
title: Copertura semantica fail-closed per le osservazioni live del Tutor
date: 2026-07-30
status: accepted
area: tutor | routing | live-observation | safety
related: [0197, 0198, 0202, 0203]
---

# 0206 - Copertura semantica delle osservazioni live del Tutor

## Problema osservato

Nei turni `522cf3976d2742ac` e `3d1c9171cc014c1e` una richiesta di leggere la
posta fu classificata correttamente come osservazione reale, ma il selettore
delle viste scelse per vicinanza lo stato dei servizi. Il Tutor rispose quindi
che la fonte non conteneva la posta, invece di lasciare la richiesta al normale
executor `read_messages`.

Il difetto non era nella distinzione fra spiegazione e osservazione. La
categoria `OBSERVE` comprende necessariamente sia le poche viste tipizzate del
Tutor sia le normali operazioni di lettura del motore. Il singolo selettore LLM
stava trasformando una somiglianza tematica in autorità.

## Decisione

Una vista live è ammessa soltanto quando superano, nell'ordine, controlli
indipendenti e fail-closed:

1. il classificatore del modo decide che la richiesta è `OBSERVE`;
2. il selettore chiuso propone una sola vista visibile all'utente;
3. un secondo passaggio, privo delle altre viste e del contesto di
   conversazione, verifica che il contratto proposto copra tutti i fatti
   richiesti e nessuna esclusione;
4. un confronto vettoriale contro tutte le viste visibili richiede che quella
   proposta sia il primario semantico non ambiguo.

Il quarto controllo usa i vettori firmati della generazione Tutor già ammessa.
Per evitare che un divieto diventi accidentalmente un segnale positivo, il
testo incorporato nel vettore contiene soltanto titolo e copertura; le
esclusioni restano nel contratto consegnato ai due classificatori e nella
fonte usata per la risposta.

La soglia non contiene frasi, domini o identificatori di vista. Deriva dai due
parametri globali già calibrati sul corpus Tutor: la vista deve superare la
soglia documentale di una banda di pertinenza e precedere la seconda vista di
almeno un terzo della stessa banda. Un errore tecnico, una copertura
insufficiente, un primario diverso o un margine ambiguo restituiscono `None`:
la richiesta prosegue nel motore operativo e il Tutor non produce una risposta
di ripiego.

## Confini temporali e contesa

La finestra breve di ammissione comprende soltanto il lavoro che può attribuire
autorità al Tutor: classificazione del modo, separazione di un'eventuale
richiesta mista, scelta della vista e verifica della copertura. La sonda di sola
lettura e la composizione avvengono dopo l'ammissione e usano il termine
complessivo del turno. In questo modo una coda del modello non trasforma il
percorso corretto di una vista live in un errore terminale.

Il semaforo HTTP del Tutor è un limite di un pre-gate facoltativo, non capacità
del motore operativo. Quando è saturo, il turno prosegue verso dialoghi,
pianificatore ed executor; soltanto l'esaurimento del pool centrale dei turni
può produrre una risposta di capacità esaurita. Se una sonda o il compositore
live non restituiscono una risposta valida, il Tutor si ritira: nessun effetto è
stato eseguito e il normale percorso resta sicuro.

La finestra di commit è richiesta soltanto quando viene creato il consenso
persistente per una richiesta mista. Una spiegazione già composta non viene
scartata perché il termine residuo è breve.

## Gerarchia dei manifesti

Un argomento di manifest è una foglia del contratto di un executor, non una
capacità autonoma. Il catalogo incorpora quindi ogni foglia insieme alla
proiezione semantica del proprio manifest e conserva separatamente il testo
firmato che può essere mostrato al compositore. Se una foglia generica precede
un manifest completo nella stessa banda globale di pertinenza, il manifest
diventa la fonte primaria e le sue foglie pertinenti hanno precedenza nel
budget di contesto. Le foglie di famiglie prive di un manifest completo nella
stessa banda vengono escluse come rumore.

La regola dipende soltanto dalla struttura delle fonti
(`executor_manifest`/`executor_manifest_argument`) e dalla banda globale. Non
contiene nomi di executor, domini, parole della domanda o eccezioni per singoli
casi. Se nessun manifest completo supera la banda, una domanda realmente
rivolta a un parametro può ancora mantenere la foglia come primaria; una
pagina manuale vicina non la sostituisce per il solo fatto di non essere una
foglia.

## Contesto e lingua

La domanda precedente arriva al recupero come campo tipizzato del confine di
canale. Non viene più ricostruita analizzando il testo già reso del contesto.
Il contesto resta utile per risolvere un seguito, ma non partecipa mai al gate
di autorità delle osservazioni live.

L'ordine di ripiego linguistico è unico per selezione e risposta. Una nuova
lingua può usare il contratto inglese di una vista quando non ne esiste ancora
uno nativo, mentre il compositore riceve sempre la lingua corrente. La fonte di
ripiego non decide quindi la lingua della risposta.

## Conseguenze

- `OBSERVE` non concede di per sé alcuna sonda o fonte.
- Aggiungere una vista modifica il confronto in modo automatico; non servono
  regole per le parole della nuova area.
- Il contesto del turno precedente, le associazioni F4 e il profilo utente non
  partecipano al gate di autorità.
- Le viste legittime, come lo stato corrente dei servizi, restano disponibili;
  una normale lettura di file, messaggi, account o dispositivi non coperta per
  intero torna al planner.
- La saturazione del Tutor non nega capacità ai turni operativi e non consuma
  un dialogo già aperto.
- I frammenti di argomento non possono sottrarre il primato al contratto
  completo di un executor pertinente.
- I prompt italiano e inglese descrivono lo stesso secondo passaggio; una
  nuova lingua eredita il medesimo vocabolario chiuso e gli stessi controlli
  strutturali.

## Certificazione

Il corpus attraversa ora il confine fidato per i casi `handoff`: non si limita
a osservare l'esito prodotto dal servizio, ma verifica che esista il dialogo di
consenso e che la clausola operativa persistita coincida byte per byte con
quella scritta dall'utente. Una vista live viene inoltre provata con una capsula
tipizzata iniettata, così il percorso `OBSERVE` è ripetibile senza dipendere
dallo stato dell'istanza.

La richiesta «Come si archiviano le email? Archivia anche quelle di ieri»
seleziona come primaria l'operazione completa `move_messages`, risponde alla
prima clausola e conserva letteralmente «Archivia anche quelle di ieri» nel
consenso monouso. Non propone archivi esterni non attestati dalle fonti.

La compilazione del catalogo registra inoltre l'identità degli ingressi prima
e dopo il lavoro: se manifesti, registri o documenti cambiano durante la
costruzione, la generazione mista viene rifiutata e resta ammessa l'ultima
generazione coerente.

## Prova in esercizio

Dopo il deploy, la richiesta «Controlla se c'è posta metnos» ha prodotto il
turno `124e34ed64ed407e`: nessun `tutor_esito`, executor
`read_messages`, risposta operativa. Il controllo positivo «Elenca i servizi
attivi di Metnos» continua invece a selezionare `SERVICES_STATUS`.
