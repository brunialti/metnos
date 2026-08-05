# Politica centrale di esecuzione degli executor

Stato: implementata; canary trasversale read-only attivo in produzione.
Decisione: ADR 0196. Contratto: `EXECUTOR_STANDARD.md` sezione 6.1.

## Obiettivo

Fare in modo che limiti, retropressione, metriche e assegnazione dei worker si
modifichino in un punto solo, senza cambiare la semantica degli executor
esistenti. Il motore deve servire executor locali, remoti e futuri executor
mutanti senza confondere concorrenza e autorita'.

## Flusso

```text
manifest --loader--> ExecutionPolicy normalizzata
                         |
invoke_executor ----------+--> ExecutorScheduler.invoke
                                | metriche + retropressione
                                | classe 0: thread chiamante
                                ` classe 1-3: solo se ammessa e abilitata
                                      |
                                      ` pool unico, cap hardware/risorsa/chiave
```

Il wrapper sincrono preserva ordine, oggetti argomento, risultato ed eccezioni.
`submit_scheduled` e' il solo ingresso per futura concorrenza tra invocazioni;
non va disseminato nei singoli executor.

Per un executor con `[execution]`, `assigned_worker_environment()` produce lo
stesso ambiente sia per il sottoprocesso locale sia per il job remoto. Nel
secondo caso il valore viaggia nel payload firmato `env_injections` e il client
lo applica dopo `env_clear()`. `assigned_workers()` applica infine il minimo fra
budget firmato, limite dell'implementazione e CPU visibili nella sandbox del
device: un Synt remoto non calcola mai autonomamente un budget piu' ampio.

## Contratto per generatori e modelli locali

La generazione ha due zone:

1. **nucleo vincolante**, prodotto dal runtime: identificatore standard,
   versione, ciclo di vita ammesso, contratto I/O e politica seriale;
2. **implementazione elastica**, prodotta dal modello: algoritmo, helper locali,
   gestione del dominio e, se davvero indipendente, un ciclo concorrente
   ordinato che usa `assigned_workers()`.

`generated_executor_contract.py` e' la sola sorgente del nucleo. Skill codegen,
Synth request e proposta Synt consumano lo stesso contesto e convalidano il
manifest risultante prima di scriverlo. Il modello non riceve un campo libero
per `parallelism_class`: la promozione avviene in una revisione separata, dopo
test e benchmark.

## Ammissione di una classe positiva

- output strutturalmente equivalente alla baseline seriale in 2-8 run;
- ordine di ingresso preservato quando e' osservabile;
- nessuna dipendenza fra elementi, oppure dipendenza isolata da chiave;
- per effetti non read-only: identita' runtime presente, prova di collisione e
  idempotenza, postcondizione verificabile;
- cap di risorsa e hardware applicati dal motore;
- rollback alla classe 0 sempre possibile senza cambiare il codice pubblico.

## Migrazione dei pool interni esistenti

I pool osservati in `find_urls`, `read_urls_html`, `read_urls_pdf`,
`compute_files_loc` e `create_images_indices` sono gia' limitati e in genere
ricompongono l'ordine. Non vanno sostituiti in blocco. Per ciascuno:

1. acquisire latenza p50/p95, memoria, numero reale di worker e tasso errori;
2. sostituire il calcolo locale dei worker con il budget assegnato;
3. aggiungere equivalenza su errori parziali, timeout e ordinamento;
4. confrontare carico singolo e simultaneo con altri executor;
5. promuovere la classe solo se qualita' e robustezza non peggiorano.

Questa sequenza evita pool annidati e sovraccarico, ma conserva le ottimizzazioni
locali che hanno semantica specifica del dominio.

## Allineamento remoto just-in-time

Il trasporto remoto applica gia' l'allineamento immediatamente prima
dell'esecuzione, non come sincronizzazione preventiva del device:

1. l'invocazione firmata identifica l'executor con `manifest_sha256` e
   `code_sha256`;
2. `client-rs::executors::ensure_executor()` usa una cache immutabile per hash,
   scarica il bundle soltanto quando manca e ne verifica firma, manifest e
   digest prima di restituire un percorso eseguibile;
3. ogni risposta di polling annuncia `shim_sha256`; un valore diverso dallo
   shim caricato invalida lo stato in-processo;
4. `Runner::execute()` risolve executor, shim e interprete dopo la scelta del
   device e subito prima di entrare nel sandbox;
5. lo shim firmato comprende gli helper runtime e l'albero backend necessario
   agli executor remoti, incluso `backends/files/local.py`;
6. un import mancante appartenente allo shim consente un solo refetch e retry,
   prima che l'executor abbia potuto produrre effetti.

Il comportamento corretto e' quindi `resolve -> fetch-if-missing -> verify ->
activate -> execute` per il digest richiesto. Un bundle non disponibile o non
verificabile deve fallire chiuso; non e' ammesso usare una versione generica
"piu' recente" o eseguire prima dell'allineamento.

### Delta ancora aperto

Il pull JIT e il content addressing sono implementati, ma executor e shim
restano due radici separate e la composizione dello shim e' mantenuta da una
lista esplicita. Il passo successivo non e' introdurre un secondo updater, ma
rafforzare quello esistente:

- derivare e validare automaticamente la closure degli import ammessi;
- produrre un digest radice che vincoli manifest, codice executor, versione
  dell'API runtime e layer shim/backend richiesti;
- inserire quel digest nell'invocazione firmata;
- usare layer immutabili e deduplicati (`runtime-core`, pack di dominio,
  executor) sotto la stessa radice;
- serializzare con single-flight download concorrenti dello stesso layer;
- distinguere nelle metriche `provisioning_ms` da `queue_ms` e `run_ms`;
- rifiutare con un errore stabile `runtime_incompatible` un device che non puo'
  materializzare l'API richiesta.

Questo elimina la dipendenza dalla completezza manuale dello shim senza
anticipare download su device che potrebbero non essere scelti.

## Parallelismo fra passi del framework

La politica centrale governa la concorrenza di una singola invocazione e il
budget interno degli executor. Dal 20/7/2026 l'engine ammette inoltre una prima
forma fail-closed di concorrenza trasversale: una wave di almeno due step
contigui, statici, root e read-only. Il risultato viene sempre commesso nel
medesimo ordine del framework, indipendentemente dall'ordine di completamento.

Il canary non e' un DAG speculativo generale. Sono barriere seriali:

- classe 0, manifest legacy/non dichiarato o equivalenza non verificata;
- `from_step`, `from_steps`, `entries`, placeholder step/filler/runtime;
- condizioni sul passo precedente, seed/ripresa e handler in-process;
- form richiesto, guardia pre-invoke non superata o diniego dello scheduler;
- ogni effetto diverso da `read_only`.

Il solo ingresso asincrono e' `agent_runtime.submit_executor`, che inoltra la
stessa `_invoke_executor_impl` al pool unico di `ExecutorScheduler`. Preparazione
e preflight avvengono prima del submit; il commit, il journal, lo scope-state,
il Vaglio post-step e l'errore restano sul thread ordinatore. Se il primo peer
fallisce, i peer non ancora partiti vengono cancellati e ogni risultato non
commesso viene scartato. Il rollback operativo e'
`METNOS_ENGINE_PARALLEL_STEPS=0` + restart.

Al momento i soli manifest ammessi sono `read_messages` (classe 2) e
`read_events` (classe 1). File, contatti, LLM, output e mutazioni restano
seriali. La promozione di altri reader richiede la stessa prova di equivalenza;
non basta che appartengano a rami distinti.

L'estensione successiva, solo dopo il canary, e' un ready-set deterministico
costruito dal grafo delle dipendenze esplicite (`from_step`, `from_steps` e
placeholder `${stepN...}`):

- uno step entra nel ready-set solo quando tutti i producer referenziati sono
  terminali con un risultato utilizzabile;
- l'ordine del framework resta il tie-break stabile e l'ordine del journal;
- passi interattivi, dipendenze non risolte e classi non ammesse restano
  seriali;
- un passo di classe positiva usa esclusivamente lo scheduler centrale;
- effetti `create_only`, `reversible` o `mutating` richiedono la stessa
  identita' di concorrenza, equivalenza e postcondizione previste dallo
  standard; essere su rami distinti non amplia l'autorita';
- failure, partial result e cancellazione si propagano soltanto ai consumer
  dipendenti, non ai rami indipendenti gia' autorizzati.

Il turno reale `c4cd859f525c4646` mostra il margine. I rami file, posta,
calendario e contatti erano inizialmente indipendenti: il ramo file impiegava
circa 13 s (`find -> read -> extract`), mentre `read_messages` impiegava 15,2 s.
La sequenza li ha sommati; un ready-set li avrebbe sovrapposti. A valle,
scrittura del rapporto e creazione del foglio consumavano rispettivamente 1,5
e 1,7 s, ma possono sovrapporsi solo dopo ammissione esplicita come effetti
create-only isolati. La stima prudente e' 14-16 s risparmiati sui 36,7 s della
fase executor, senza parallelizzare due richieste LLM sull'attuale backend a
singolo slot.
