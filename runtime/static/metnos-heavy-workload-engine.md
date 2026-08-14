# Richiesta a Metnos: motore generico per workload lunghi, persistenti e paralleli

## Mandato

Analizza l'architettura e il codice installati in `/opt/metnos`, quindi progetta,
implementa, integra e collauda un motore generico per eseguire carichi di lavoro
lunghi o molto grandi senza perdere il lavoro già svolto e senza duplicare gli
effetti dopo errori, interruzioni o riavvii.

Il risultato deve appartenere realmente all'architettura Metnos: richieste in
linguaggio naturale, piani tipizzati, executor ammessi, autorità minima,
policy, audit, postcondizioni osservabili e modelli configurati tramite tier.
Non creare un secondo agente separato, un workflow hard-coded per un solo caso,
un accesso shell generale o un sistema che aggiri scheduler, policy ed executor.

Questa richiesta è autocontenuta. Prima di modificare il codice, verifica le
implementazioni e gli ADR già presenti e riusa i meccanismi esistenti quando
forniscono davvero la semantica richiesta. Se nomi o dettagli suggeriti qui non
rispettano il vocabolario canonico del progetto, scegli nomi coerenti e documenta
la decisione.

## Obiettivo dal punto di vista dell'utente

L'utente deve poter formulare soltanto il risultato desiderato, per esempio:

> Process every image in `/home/user/questions`, extract and solve every
> question, and create `notes.md`, `solutions.md`, and `cheat-sheet.md`. Work in
> the background, retry recoverable failures, survive restarts, and do not
> declare completion until every source item is accounted for.

Metnos deve trasformare autonomamente la richiesta in un job durevole. L'utente
non deve scaricare skill, definire sub-agent, scrivere DAG, scegliere batch o
costruire manualmente un workflow. Deve tuttavia poter vedere il piano, i limiti,
il modello scelto, i progressi, gli errori e gli artefatti prodotti.

## Requisiti fondamentali

### 1. Motore generico, non soluzione speciale

Implementa un sottosistema generico per collezioni di unità indipendenti e per
successive fasi di aggregazione. Deve poter servire, tra gli altri, questi casi:

- immagini contenenti domande da estrarre e risolvere;
- directory di PDF o documenti da classificare, estrarre o riassumere;
- grandi collezioni di file da analizzare o trasformare;
- elaborazioni con una fase `map` per elemento e una o più fasi `reduce`;
- produzione finale di uno o più file, documenti o record strutturati.

Il caso delle immagini descritto più avanti deve essere un preset o un piano
compilato sopra il motore, non logica incorporata nel suo nucleo.

### 2. Job persistenti e ripresa dopo restart

Ogni richiesta lunga deve creare un job persistente con almeno:

- identità immutabile del proprietario e isolamento tra utenti;
- obiettivo originale e piano compilato/versionato;
- manifest degli input con path o identificatore, dimensione, hash e metadati;
- versione degli executor, dei prompt e dei backend impiegati;
- binding effettivo di tier, provider e modello per ogni fase LLM;
- stato del job, di ogni fase e di ogni unità di lavoro;
- numero di tentativi, errori strutturati e prossima data di retry;
- output intermedi, artefatti finali e relativa provenienza;
- timestamp, metriche e record di audit.

Usa uno storage transazionale appropriato, preferibilmente SQLite se coerente
con gli store esistenti. Le transizioni devono essere atomiche. Dopo un crash,
un arresto del servizio o il riavvio dell'host, il sistema deve recuperare i job
non terminali, riconciliare gli output già presenti e riprendere dalla prima
unità non completata, senza ricominciare l'intero lavoro.

### 3. Assenza di duplicazioni ed effetti “effectively once”

Non promettere un generico “exactly once” dove tecnicamente non è possibile.
Implementa elaborazione almeno-una-volta con commit idempotenti e deduplicazione,
in modo da ottenere effetti osservabili “effectively once” quando il backend lo
consente.

In particolare:

- assegna a job, fase e unità chiavi stabili e deterministiche;
- deriva la chiave dell'unità da identità/hash della sorgente, versione della
  fase e parametri semanticamente rilevanti;
- conserva separatamente `attempt`, `execution` e `committed result`;
- usa transazioni, vincoli unici e compare-and-set per impedire doppi commit;
- scrivi file prima in un temporaneo verificato e pubblicali con rename atomico;
- usa idempotency key native quando offerte da provider esterni;
- non ripetere automaticamente effetti esterni non idempotenti se non possono
  essere riconciliati con un'evidenza osservabile;
- in quel caso sospendi la sola unità interessata e chiedi una decisione,
  lasciando proseguire le unità indipendenti;
- una nuova esecuzione intenzionale dello stesso lavoro deve creare una revisione
  esplicita, mentre la ripetizione accidentale della richiesta deve poter
  riagganciare il job esistente.

Modifiche a input, prompt, executor, modello o parametri che cambiano il risultato
devono invalidare soltanto i checkpoint dipendenti, non l'intero job senza motivo.

### 4. Concorrenza e parallelismo governati

Le unità indipendenti devono poter essere eseguite in parallelo, ma tutto il
parallelismo deve passare attraverso lo scheduler centrale di Metnos e rispettare
le classi di parallelismo dichiarate dagli executor.

Sono richiesti:

- fan-out bounded e configurabile per CPU, I/O, GPU, provider e tier LLM;
- backpressure quando una risorsa è satura;
- fair use tra utenti e tra job;
- priorità e possibilità di limitare o mettere in pausa un job;
- batching adattivo senza cambiare la semantica dei risultati;
- nessun pool privato capace di superare il budget assegnato;
- cancellazione cooperativa e shutdown ordinato;
- lease persistenti con heartbeat, scadenza e fencing token, affinché due worker
  non possano committare la stessa unità dopo un restart o una race;
- retry con backoff e jitter per errori transitori, con limite configurabile;
- distinzione strutturata tra errore transitorio, permanente, input non valido,
  budget esaurito, policy negata e intervento umano necessario.

L'ordine di completamento parallelo non deve alterare l'ordine canonico degli
output finali. La ricomposizione deve essere stabile e basata sull'identità delle
unità, non sul momento in cui terminano.

### 5. Pipeline, fan-out/fan-in e riduzioni grandi

Il motore deve rappresentare almeno:

- acquisizione e congelamento dell'inventario;
- fasi deterministiche o agentiche per singola unità;
- dipendenze tra fasi;
- fan-out su unità indipendenti;
- fan-in e riduzioni gerarchiche;
- validazione di copertura e qualità;
- pubblicazione atomica degli artefatti.

Le riduzioni non devono richiedere di caricare l'intero corpus in un singolo
prompt. Implementa map/reduce gerarchico e checkpointato: digest o risultati
parziali bounded, riduzioni intermedie e riduzione finale. Ogni nodo della
riduzione deve essere riusabile se i suoi input e la sua configurazione non sono
cambiati.

La normale soglia massima di passi di un turno interattivo non deve diventare il
limite di cardinalità del job. Il turno crea, controlla o interroga il job; il
worker durevole elabora le unità entro il proprio mandato e i propri budget.

### 6. Compilazione da linguaggio naturale

Il planner deve poter riconoscere una richiesta pesante e compilare un job usando
soltanto fasi ammesse. Il piano persistito deve avere:

- scopo e condizione terminale espliciti;
- schema degli input e degli output di ogni fase;
- executor canonico utilizzato da ogni fase;
- regole di retry e invalidazione;
- budget di concorrenza, token, tempo e costo;
- criteri di copertura e qualità;
- artefatti finali richiesti.

L'LLM non deve poter inventare executor, ampliare l'autorità, modificare il
mandato durante l'esecuzione o trasformare un risultato parziale in successo.
Se manca una capacità, usa il normale processo Metnos di composizione o proposta,
test, firma e ammissione.

Prevedi executor o API canoniche per creare, leggere e controllare i job. Le
operazioni minime sono: avvio, stato, lista, pausa, ripresa, cancellazione,
ritentativo selettivo, revisione e recupero degli artefatti. Evita di sovraccaricare
il concetto di task pianificato ricorrente se la semantica è diversa.

### 7. BYOM e riproducibilità

Usa i tier astratti già configurabili in Metnos (`fast`, `middle`, `precise`,
`wise`, `creative`, `frontier`) e non codificare nomi di modelli nel motore.

Per ogni risultato LLM registra almeno tier richiesto, tier risolto, provider,
modello, parametri di generazione, versione del prompt, token, latenza e costo
quando disponibile. Una ripresa deve usare il binding congelato dal job oppure
richiedere/registrare una revisione esplicita se quel backend non è più
disponibile. Il cambio di modello non deve mescolare silenziosamente risultati
incompatibili nello stesso artefatto.

I risultati cacheabili devono essere indirizzati dal contenuto. Non riutilizzare
un risultato se sono cambiati sorgente, istruzione, schema, modello o versione
dell'executor in modo semanticamente rilevante.

### 8. Provenienza, completezza e veridicità dello stato

Ogni risultato deve essere riconducibile alla sorgente e all'esecuzione che lo ha
prodotto. Conserva almeno:

- source ID e hash;
- coordinate utili, come pagina, immagine o indice del record;
- testo estratto e relativo metodo, confidenza o diagnostica;
- output della fase e versione del suo contratto;
- errori e tentativi;
- dipendenze degli artefatti aggregati.

Gli stati terminali devono distinguere almeno `completed`, `completed_with_errors`,
`failed`, `cancelled` e `needs_attention`. Non dichiarare `completed` se:

- non è stato congelato un inventario completo;
- esistono input senza un esito terminale contabilizzato;
- è scattato un cap o una truncation non deliberatamente accettata;
- un artefatto richiesto manca o non supera la validazione;
- la pubblicazione finale non è stata committata.

Mostra sempre denominatore e numeratore reali: sorgenti scoperte, elaborate,
riuscite, fallite, saltate e ancora pendenti. Una percentuale non deve nascondere
elementi irrisolti.

### 9. UI, API e canali

Integra il motore nella UI web esistente con almeno:

- elenco dei job dell'utente;
- stato e progresso aggiornabile senza bloccare la chat;
- dettaglio delle fasi e delle unità fallite;
- modello/tier e budget effettivi;
- pulsanti pausa, riprendi, annulla e ritenta falliti;
- avvertimenti per policy, credenziali, costo o intervento umano;
- download degli artefatti finali;
- log sintetico e collegamento all'audit completo.

La UI deve restare responsiva anche durante job molto lunghi. Un refresh o logout
non deve interrompere l'elaborazione autorizzata.

Integra Telegram senza inviare un messaggio per ogni elemento. Sono sufficienti:
accettazione con job ID, aggiornamenti significativi/configurabili, richiesta di
intervento, completamento e consegna/link degli artefatti. Lo stato deve poter
essere interrogato in linguaggio naturale da web chat e Telegram.

Esponi API autenticate e owner-scoped per le stesse operazioni. Non permettere a
un utente di osservare o controllare job, input o artefatti di un altro.

### 10. Gestione operativa

Prevedi:

- migrazioni di schema versionate e reversibili quando possibile;
- startup recovery deterministico;
- retention configurabile di checkpoint, log e artefatti;
- garbage collection che non elimini dati referenziati da job attivi;
- health check con code, worker, lease scadute e job bloccati;
- metriche su throughput, latenza, retry, errori, token e costo;
- limiti per job e per utente;
- protezione da input che crescono mentre vengono scanditi;
- comportamento definito per file modificati o rimossi dopo il congelamento;
- possibilità di eseguire worker su server o device compatibili senza perdere
  identità, autorità e firma dei risultati.

## Caso di accettazione principale: immagini con domande

Implementa e prova end-to-end questo scenario, senza inserire il numero 98 nella
logica generale.

### Input

Una directory contiene 98 immagini. Ogni immagine può contenere zero, una o più
domande; alcune possono essere ruotate, poco leggibili o duplicate. L'utente
chiede di risolvere tutte le domande e generare:

1. `notes.md`: spiegazioni ordinate degli argomenti emersi;
2. `solutions.md`: ogni domanda, risposta, procedimento e riferimento alla
   sorgente;
3. `cheat-sheet.md`: formule, regole e richiami sintetici deduplicati.

### Pipeline attesa

```text
freeze inventory and hashes
  -> OCR/VLM extraction per image
  -> split and normalize questions
  -> stable question IDs and deduplication
  -> solve each unique question with the configured model tier
  -> validate each answer and retain source provenance
  -> hierarchical reductions for notes and cheat sheet
  -> assemble solutions in canonical source/question order
  -> validate coverage and artifact structure
  -> atomically publish the three Markdown files
```

Usa gli executor esistenti, come ricerca file, `read_files_ocr`, trasformazioni
di entries, consultazione dei tier e `write_files`, quando i loro contratti sono
adatti. Introduci nuove primitive soltanto per la semantica durevole mancante,
non per duplicare funzioni già presenti.

Un question ID deve essere stabile e includere almeno l'identità della sorgente,
la posizione locale e un hash del testo normalizzato. Le immagini duplicate non
devono generare soluzioni duplicate, ma la provenienza deve conservare tutti i
path che contenevano la domanda.

Un'immagine illeggibile non deve sparire: deve risultare fallita o richiedere
attenzione. `solutions.md` deve rendere visibile ogni eventuale elemento non
risolto. Il job può essere `completed_with_errors` soltanto secondo una policy
esplicita; non può essere presentato come successo completo.

Ripeti il test anche con un corpus sintetico almeno dieci volte più grande, in
modo che siano obbligatori batching, checkpoint e più livelli di riduzione.

## Prove obbligatorie

Aggiungi test unitari, di integrazione e failure-injection che dimostrino almeno:

1. creazione e lettura owner-scoped di un job;
2. chiavi stabili e deduplicazione degli input;
3. due worker in race sulla stessa unità: un solo commit valido;
4. crash dopo l'esecuzione ma prima del commit;
5. crash dopo il commit ma prima dell'ack;
6. `SIGKILL`/restart a circa 30%, 60% e durante la pubblicazione finale;
7. recupero delle lease scadute con fencing del worker precedente;
8. retry selettivo degli errori transitori;
9. nessun retry automatico di un effetto esterno ambiguo;
10. pausa, ripresa e cancellazione cooperativa;
11. invalidazione minima dopo modifica di un solo input;
12. stabilità dell'ordine con completamento parallelo fuori ordine;
13. map/reduce che supera la capacità di un singolo prompt;
14. rispetto dei budget dello scheduler e assenza di pool paralleli privati;
15. propagazione di cap e truncation fino allo stato finale;
16. isolamento completo tra utenti;
17. download degli artefatti dalla UI dopo logout/login e da un altro PC;
18. stato e notifiche Telegram senza flooding;
19. corpus da 98 immagini con conteggio completo;
20. corpus almeno 10× più grande con uno o più restart e nessuna duplicazione.

I test di restart devono avviare un processo reale o un worker isolato, terminarlo
nel punto controllato e verificare lo stato persistito dopo la nuova partenza.
Non sostituirli con test che chiamano semplicemente due volte la stessa funzione.

## Criteri di completamento

Considera questa richiesta completata soltanto quando:

- il motore è integrato nel runtime Metnos e non vive come script esterno;
- gli executor/API di controllo sono tipizzati, firmati, ammessi e sottoposti a
  policy e autorità;
- i worker usano lo scheduler centrale e rispettano i budget;
- un job sopravvive realmente al riavvio senza duplicare risultati o effetti;
- il caso delle immagini produce e rende scaricabili tutti e tre gli artefatti;
- UI e Telegram permettono almeno avvio, stato e recupero dei risultati;
- cap, errori e risultati parziali sono comunicati onestamente;
- i test elencati sono eseguiti e ne viene riportato l'esito;
- la documentazione spiega architettura, modello di consistenza, schema dati,
  recovery, configurazione, limiti e uso da linguaggio naturale;
- non rimangono TODO indispensabili per la sicurezza o per la ripresa.

## Modalità di lavoro e consegna

Procedi autonomamente entro questo mandato. Prima produci una breve analisi delle
strutture esistenti che intendi riusare e delle lacune accertate; poi implementa
per incrementi verificabili. Non fermarti a un documento di design o a uno
scaffold.

Preserva le modifiche estranee già presenti nel worktree. Evita migrazioni
distruttive e mantieni compatibilità con i turni interattivi correnti. Se una
scelta richiede un ampliamento materiale di autorità o un effetto esterno non
autorizzato, sospendi solo quel punto e descrivi precisamente la decisione
necessaria, continuando il lavoro indipendente possibile.

Alla consegna fornisci:

- sintesi dell'architettura implementata;
- elenco dei file modificati e delle migrazioni;
- contratti pubblici di executor e API;
- istruzioni UI, Telegram e richiesta naturale di esempio;
- risultati dei test e delle prove di restart;
- limiti residui reali, senza dichiarare implementate funzioni soltanto previste.
