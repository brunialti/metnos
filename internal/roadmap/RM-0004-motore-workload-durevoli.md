# RM-0004 — Motore generico per lavori lunghi, persistenti e paralleli

| Campo | Valore |
|---|---|
| Stato | `active`; F0-F2 completate il 2026-08-20, F3 non iniziata |
| Creazione | 2026-08-17; mandato ricevuto in data anteriore, non tracciata |
| Ultima revisione | 2026-08-20 |
| Implementazione reale | Nucleo interno e inattivo disponibile in `runtime/durable_workloads/`: modelli chiusi, schema SQLite v1, migrazione atomica, repository owner-scoped, CAS, idempotenza, eventi, outbox e valutazione della completezza. Non esistono ancora claim, lease operative, fencing del commit, compilatore, worker, route, UI o notifiche attive |
| Progettazione | Gate V1-V5 ratificati da ADR 0213. Il nome pubblico è rinviato per decisione; la topologia futura è congelata ma non installata |
| Conservazione | Roadmap persistente fino a implementazione dimostrata o cancellazione esplicita di Roberto |
| Decisione di prodotto acquisita | Un carico lungo interrotto deve poter riprendere senza perdere il lavoro svolto e senza ripetere un effetto già prodotto; l'utente formula il risultato voluto, non il flusso |
| Autorizzazione F0-F2 | Acquisita da Roberto il 2026-08-20; attuazione limitata al nucleo interno inattivo |
| Origini | Mandato integrale in calce; `internal/design/TODO.md::JOB-001` |
| Decisioni applicabili | ADR 0183, 0186, 0190, 0193, 0196, 0201, 0204, 0205, 0207 e 0213 |
| Prossimo gate | F3 deve provare claim, lease, heartbeat, fencing e ripresa con processi concorrenti prima che una unità possa essere eseguita |
| Riservatezza | Documento interno. Non va copiato in `docs/`, incluso nel catalogo Tutor o pubblicato sul sito finché il comportamento non è implementato e verificato |

## 0. Esito della verifica

RM-0004 non duplicava una funzione già presente: chiedeva una semantica che il
codice non possiede ancora. Questa revisione trasforma il mandato in una
roadmap attuabile, senza dichiarare costruito alcun componente futuro.

La conclusione architetturale è netta:

1. Metnos dispone già dei confini giusti per **autorità, firma, collocazione,
   sandbox e concorrenza di una singola invocazione**;
2. dispone di primitive parziali per pianificazioni temporali, consegna remota,
   undo, eventi SSE e attività asincrone specialistiche;
3. non dispone di un'autorità persistente che rappresenti un corpus, le sue
   unità, i tentativi, i checkpoint, le dipendenze e il commit finale;
4. nessuna composizione delle primitive esistenti, senza un nuovo store e un
   coordinatore durevole, soddisfa ripresa dopo arresto, fencing e completezza;
5. il percorso corretto è un **organo interno di Metnos**, non un secondo
   agente: compila un piano tipizzato, reclama un'unità alla volta e la
   esegue sempre attraverso gli executor ammessi e lo scheduler centrale.

La direzione resta `active`, ma il suo fondamento non è più soltanto proposto.
ADR 0213 ha chiuso F0; F1 e F2 hanno introdotto un archivio inattivo e
verificabile senza collegarlo al runtime corrente. I pacchetti F3-F13 restano
circostanziati per agenti esecutivi, secondo §16-17, e non possono anticipare
i rispettivi gate.

### 0.1 Lessico di verifica

Nel testo analitico sono usate quattro etichette:

- **[VERIFICATO]**: comportamento osservato nel codice o nei test al
  20 agosto 2026;
- **[MANCANTE]**: semantica cercata nel codice e non trovata;
- **[PROPOSTA]**: disegno futuro, privo di autorità finché non è approvato;
- **[GATE]**: decisione che blocca il pacchetto dipendente.

Il termine interno provvisorio è *lavoro durevole*. I nomi di pacchetti e tabelle
riportati più avanti servono a rendere concrete le istruzioni; non introducono
un nuovo oggetto nel vocabolario pubblico.

### 0.2 Provenienza e integrità del mandato

Il mandato in calce è riportato integralmente e non viene corretto, neppure
dove usa una terminologia oggi diversa. Era rimasto in `runtime/static/`, una
cartella esposta dal server: il 17 agosto 2026 è stato trasferito nella roadmap
interna e la pubblicazione di file non destinati al web è stata chiusa alla
causa.

Il mandato usa anche `precise`. Questo nome conserva un significato storico o
descrittivo in alcuni documenti, ma non è una chiave oggi accettata da
`runtime/llm_router.py`. Il tier operativo per generazione divergente ed
editoriale è `creative`; non esiste però un alias universale
`precise -> creative`: per esempio `tutor.compose` è oggi registrato su `wise`.
L'implementazione dovrà quindi scegliere sempre un **workload** presente in
`runtime/llm_workloads.py`, lasciando al registro e al router la risoluzione del
tier. Il testo originale resta intatto come fonte storica.

## 1. Obiettivo e valore per l'utente

L'utente descrive un esito ampio — per esempio elaborare tutte le immagini di
una cartella e produrre tre documenti — e riceve subito:

- l'identificativo del lavoro e un piano comprensibile;
- inventario, denominatore reale e budget applicati;
- avanzamento che sopravvive a refresh, logout e riavvio;
- errori circoscritti alla singola sorgente, senza perdita silenziosa;
- artefatti verificati, con provenienza e possibilità di scaricamento;
- comandi naturali per stato, pausa, ripresa, annullamento e ritentativo.

Il valore non è «fare più passi in un turno». È poter affidare a Metnos un
risultato la cui cardinalità non è nota in anticipo, chiudere la chat e sapere
che ogni elemento sarà contabilizzato una volta che il sistema dichiara la
fine.

### 1.1 Promessa verificabile

La promessa di prodotto va espressa in termini tecnicamente sostenibili:

- esecuzione **almeno una volta** per i tentativi recuperabili;
- un solo risultato committato per chiave di unità e revisione;
- effetti osservabili *effectively once* soltanto quando l'effetto è
  idempotente o riconciliabile;
- nessun retry automatico di un effetto esterno ambiguo;
- nessun `completed` senza inventario sigillato, unità contabilizzate e
  artefatti richiesti convalidati;
- nessuna pretesa generica di *exactly once* tra SQLite, filesystem, provider e
  dispositivi remoti.

## 2. Stato del codice verificato

### 2.1 Quadro sintetico

| Area | Stato corrente verificato | Riuso ammesso | Semantica ancora mancante |
|---|---|---|---|
| Piano interattivo | `engine.types.Framework` contiene una sequenza tipizzata di `StepSpec`; l'esecutore ha cap ordinario 12 e hard ceiling runtime | candidato iniziale da compilare e validare | DAG persistente, inventario, revisioni, condizioni terminali e unità cardinali |
| Invocazione | `agent_runtime.invoke_executor` è il choke point comune per locale e remoto | obbligatorio per ogni unità | contesto durevole di lavoro/fase/unità/tentativo e commit con fencing |
| Concorrenza | `executor_scheduler` applica backpressure, semafori e metriche; default seriale | autorità finale dei budget di esecuzione | equità fra proprietari/lavori, priorità, quote persistenti e richieste multi-risorsa |
| Parallelismo interno | `parallel_walk` e `parallel_map_ordered` mantengono ordine stabile | dentro un executor ammesso | coordinamento fra processi e riavvii |
| Pianificazione temporale | scheduler v2 persiste callback e storico delle esecuzioni | sveglia, riconciliazione e manutenzione | stato per unità e ripresa del lavoro |
| Remoto | `invocations.py` firma, accoda, riconsegna e deduplica una `invocation_id` | trasporto di un singolo tentativo | legame atomico al tentativo, fencing del commit ed effetti incerti |
| Undo | journal e compensazioni passano dal choke point, inclusi device | rimedio e audit | prova di idempotenza o di commit una-volta |
| Eventi turno | SSE con `Last-Event-ID` finché il registro è in RAM; fallback al risultato finale | forma del protocollo SSE | stream persistente degli eventi intermedi di un lavoro |
| Attività immagini | runner systemd e checkpoint specialistici esistono | lezioni operative e futura migrazione | modello generico, unità transazionali e provenienza uniforme |
| LLM | workload chiusi, tier centrali e telemetria provider | selezione e binding | snapshot durevole, contesto per tentativo, costo per unità e digest del prompt effettivo |
| Proprietario | `owner_user_id`, lock condiviso/esclusivo e purge coordinato | confine obbligatorio | purge del nuovo store e degli artefatti; query sempre owner-scoped |
| Notifiche | `user_notices` e push Telegram best-effort | ripiego «prossima visita» | outbox persistente con ack e retry |
| Artefatti | download foto legato al turno e scritture file ordinarie | nessun riuso diretto come autorità | registro owner-scoped, digest, retention e pubblicazione riconciliabile |

### 2.2 Piano ed esecuzione del turno

**[VERIFICATO]** `runtime/engine/types.py` definisce `Framework`, `StepSpec` e
`RunResult`. Il piano è una sequenza adatta a un turno, non una descrizione
persistente di mappe, riduzioni o dipendenze cardinali. Il limite ordinario è
12 passi; solo pipeline canoniche costruite dal runtime possono alzarlo, entro
un tetto operativo. Il limite protegge il turno e non va rimosso.

**[VERIFICATO]** le onde parallele di `runtime/engine/parallel_steps.py` sono
volutamente conservative: soltanto letture radice contigue, statiche, conformi
allo standard e già ammesse possono sovrapporsi. Non costruiscono un DAG
speculativo e non persistono il loro stato.

**[PROPOSTA]** il compilatore durevole può ricevere dal motore un candidato, ma
deve convertirlo in un contratto differente e immutabile. Non deve serializzare
un `Framework` e chiamarlo «lavoro durevole».

### 2.3 Choke point e politica centrale di esecuzione

**[VERIFICATO]** `agent_runtime.invoke_executor` e il gemello asincrono
`submit_executor` attraversano `executor_scheduler` prima di entrare nella
stessa implementazione. In quel punto avvengono normalizzazione degli argomenti,
collocazione, scope, sandbox, undo e propagazione dell'identità del proprietario.
È il solo percorso ammesso per eseguire un'unità.

**[VERIFICATO]** la politica firmata `[execution]` espone effetto, classe di
parallelismo, classe di risorsa, chiave di concorrenza e gate di equivalenza.
Metadati mancanti o incompleti degradano a classe 0. Il pool parallelo è
disabilitato per impostazione predefinita.

**[MANCANTE]** lo scheduler conserva code e metriche soltanto in memoria, usa
semafori e non riceve oggi `owner_user_id`, identificativo del lavoro, priorità
o deadline come contesto di ammissione. Una sola `resource_class` non esprime
una fase che consuma contemporaneamente CPU, I/O e VLM. Non è quindi corretto
attribuirgli equità fra lavori o un budget multi-risorsa.

### 2.4 Scheduler v2

**[VERIFICATO]** `runtime/scheduler_v2` persiste `ScheduleEntry` e `Run` in
SQLite. Al riavvio marca `crashed` le esecuzioni rimaste `running`; per una
scadenza persa decide se eseguire o saltare secondo la finestra di tolleranza.
Il daemon vive nel processo HTTP e possiede un `ThreadPoolExecutor` dedicato
alle callback.

**[MANCANTE]** non esistono unità di corpus, risultati committati, lease,
fencing o ripresa dal checkpoint. Marcare una callback `crashed` non riprende
il lavoro che essa stava facendo. Il suo pool privato non deve diventare il
pool del nuovo motore.

**[PROPOSTA]** scheduler v2 può soltanto chiamare una manutenzione interna
idempotente — per esempio sweep delle lease o retention — oppure svegliare il
servizio durevole. Non deve essere lo store autorevole del lavoro.

### 2.5 Trasporto remoto e undo

**[VERIFICATO]** `runtime/invocations.py` conserva un'invocazione firmata con
stati `queued -> delivered -> done|failed|expired`, riconsegna dopo la deadline
e accetta una sola conclusione per `invocation_id`. `remote_exec.invoke_remote`
fa apparire il risultato remoto con la stessa forma di quello locale.

**[MANCANTE]** la coda non modella lavoro, fase, unità o fence. Se un worker
creasse una nuova `invocation_id` dopo un arresto, un effetto non idempotente
potrebbe essere eseguito di nuovo. Il primo risultato della coda non equivale
al diritto di committare il risultato dell'unità.

**[VERIFICATO]** ADR 0183 e il journal di undo consentono una compensazione e
mantengono il device d'origine. Le scritture di audit best-effort e una
operazione inversa non dimostrano però che l'effetto iniziale sia avvenuto una
sola volta. `revertible` non deve essere usato come sinonimo di `idempotent`.

### 2.6 Parallelismo deterministico e attività immagini esistenti

**[VERIFICATO]** ADR 0204-0205, `parallel_walk` e l'indicizzazione incrementale
forniscono visita completa della sorgente, ordinamento stabile e riuso locale
entro un'invocazione. Sono primitive utili per l'inventario o per il lavoro
interno di un executor; il loro stato non sopravvive come contratto di lavoro.

**[VERIFICATO]** `build_orchestrator.py`, `build_runner.py`,
`build_runner_unified.py` e `jobs/index_image_embed_backfill.py` mostrano tre
tecniche valide ma specialistiche: servizio transiente systemd, heartbeat su
file e checkpoint di dominio. Non condividono un modello transazionale per
unità e non devono essere promossi a nucleo per semplice rinomina.

**[MANCANTE]** il percorso asincrono della build immagini può archiviare il
marker dopo il tentativo di notifica; non offre la consegna durevole richiesta
qui. Sarà un candidato alla migrazione solo dopo la prova del motore generico.

### 2.7 Executor del caso immagini

**[VERIFICATO]** `read_files_ocr` accetta fino a 100 percorsi per invocazione,
esegue Tesseract in sequenza e usa un VLM entro limiti espliciti quando il testo
è insufficiente. È dichiarato `agentic`, ma non possiede una sezione
`[execution]`: lo scheduler lo tratta correttamente come seriale.

**[VERIFICATO]** il fallback VLM passa da `vlm_client`, configurato nel dominio
VLM separato. Oggi non attraversa `llm_workloads`, non registra nel sink LLM
provider/modello/token per unità e usa parametri di generazione propri. Prima
del caso di accettazione serve quindi un adattamento di provenienza; non basta
annotare a posteriori il nome del modello.

**[VERIFICATO]** `write_files` offre `skip_if_exists`, undo e output
vettoriale, ma `runtime/backends/files/local.py::_write_one` apre direttamente
il path finale. `skip_if_exists` non confronta il digest del contenuto. Esso
non soddisfa ancora il protocollo di pubblicazione atomica e riconciliabile
richiesto dal mandato.

**[MANCANTE]** l'output degli executor è descritto da `schema_inline`, una
notazione utile a standard e planner ma non convalidata come JSON Schema sul
risultato di ogni invocazione. Un piano durevole deve ammettere soltanto fasi
per le quali esiste un validatore meccanico dell'output effettivo.

### 2.8 LLM, prompt e riproducibilità

**[VERIFICATO]** `runtime/llm_workloads.py` è il registro chiuso
workload-verso-tier; `llm_router.resolved_tier_spec` completa il binding con
provider, modello, endpoint e policy. I consumer non devono fissare questi
valori.

**[VERIFICATO]** `llm_telemetry` conosce tier, provider, modello, token e
latenza, ma il contesto è soltanto il tier. Non conosce lavoro, revisione,
fase, unità, tentativo o fence. I sink sono best-effort e non costituiscono un
ledger di costo.

**[VERIFICATO]** `prompt_loader` calcola e conserva hash per l'allineamento
linguistico, ma l'API di rendering restituisce testo. Non consegna al chiamante
un'identità completa del template effettivo, del fallback linguistico e delle
variabili semanticamente rilevanti.

**[PROPOSTA]** ogni fase LLM congela il nome logico del workload, lo snapshot
redatto del binding risolto, il digest del prompt effettivo e la versione dello
schema. Non si conservano credenziali né si duplica il prompt in chiaro. Se il
binding non è più disponibile, si crea una revisione esplicita: nessun cambio
silenzioso di modello in una revisione già iniziata.

### 2.9 Identità, cancellazione e canali

**[VERIFICATO]** `user_lifecycle.owner_session` acquisisce un lock condiviso e
rifiuta proprietari con cancellazione iniziata; `owner_deletion` acquisisce il
lock esclusivo. `users.py` revoca le superfici di accesso e chiama i purge dei
registri owner-scoped prima di rimuovere l'utente.

**[PROPOSTA]** un worker mantiene il lock condiviso soltanto durante
reclamo, esecuzione e commit di un'unità con durata massima, non per tutta la durata del
lavoro. Il nuovo store e la radice degli artefatti devono entrare nella
sequenza di purge; una cancellazione avviata impedisce nuovi reclami.

**[VERIFICATO]** `TurnEventLog` supporta `Last-Event-ID`, ma conserva gli eventi
intermedi in memoria per cinque minuti dopo la chiusura. Il fallback su JSONL
recupera il risultato finale del turno, non l'intero stream.

**[VERIFICATO]** `user_notices` è best-effort e fail-open. Il daemon Telegram
svuota la coda prima dell'invio: un errore di rete successivo può perdere
l'avviso. Serve una outbox con reclamo, ack e retry per gli eventi importanti
del lavoro; `user_notices` resta soltanto un ripiego.

**[VERIFICATO]** le URL delle foto sono firmate e legate a un turno recente.
Non rappresentano un artefatto durevole owner-scoped con retention e revoca.

### 2.10 Evidenza eseguibile della ricognizione

Il 20 agosto 2026 sono stati eseguiti 111 test mirati, tutti verdi, sui moduli
che potrebbero essere riusati:

```text
tests/runtime/executors/test_executor_scheduler.py
tests/runtime/infra/test_parallel_walk.py
tests/runtime/remote/test_invocations.py
tests/runtime/remote/test_late_result_a0.py
tests/runtime/infra/test_undo_chokepoint.py
tests/runtime/infra/test_build_runner.py
tests/runtime/infra/test_build_orchestrator.py
tests/runtime/executors/test_read_files_ocr_agentic.py
tests/runtime/http/test_users.py

111 passed in 3.75s
```

Questa batteria dimostra che le basi correnti non sono rotte. Non è una prova
del motore futuro: non esiste ancora alcun test per lease di unità, fencing,
ripresa di un corpus, pubblicazione riconciliabile o riduzione gerarchica.

## 3. Lacune da colmare

Il minimo nucleo nuovo deve fornire, senza delegarlo a prosa LLM:

1. piano immutabile e versionato, distinto dal piano di turno;
2. inventario sigillato con identità di sorgente e regole per input instabili;
3. store transazionale di lavori, fasi, unità, tentativi, risultati ed eventi;
4. reclamo con lease, heartbeat e fencing token monotono;
5. commit idempotente con vincoli unici e compare-and-set;
6. registro degli artefatti e protocollo filesystem/SQLite riconciliabile;
7. invalidazione per dipendenze e revisioni esplicite;
8. riduzioni gerarchiche checkpointate e ordine canonico;
9. equità fra proprietari e lavori, quote e budget multi-risorsa;
10. adattatore locale/remoto che non aggiri `invoke_executor`;
11. snapshot di executor, prompt e binding LLM/VLM;
12. API, eventi persistenti, UI e outbox Telegram owner-scoped;
13. ripresa all'avvio, conservazione, raccolta dei dati non più referenziati e
    cancellazione completa dei dati utente;
14. prove con processi reali terminati nei punti di errore richiesti.

## 4. Decisioni obbligatorie prima dell'implementazione

### 4.1 Nome e vocabolario pubblico

**[GATE V1]** `tasks` indica già attività ricorrenti e promemoria dello
scheduler v2. Riutilizzarlo per un corpus durevole produrrebbe due semantiche
incompatibili. `jobs`, `workloads` e `artifacts` non sono oggetti pubblici
approvati in `vocab.py`.

Raccomandazione: mantenere un nome interno provvisorio
`durable_workloads` e approvare separatamente l'oggetto pubblico, con i verbi
canonici già disponibili quando adeguati. Nessun agente deve aggiungere token,
affinity, route o executor pubblici prima della decisione di Roberto e del
relativo aggiornamento normativo.

### 4.2 Topologia del worker

**[GATE V2]** raccomandazione: un servizio systemd supervisionato, parte del
ciclo di vita di `metnos.target`, separato dal processo HTTP. È un coordinatore
deterministico dello stesso runtime, non un secondo agente. La separazione
mantiene la chat responsiva e consente il riavvio indipendente del worker.

L'ADR deve decidere:

- nome e dipendenze dell'unità;
- se il worker sia richiesto alla readiness quando la funzione è attiva;
- politica `Restart`, shutdown e `KillMode` del gruppo di processi;
- numero di processi iniziale, che in v1 dovrebbe essere uno con più tentativi
  ammessi dallo scheduler centrale;
- modalità degradata dell'HTTP quando il worker non è disponibile.

Co-ospitare il coordinatore nell'HTTP è possibile, ma non raccomandato: un
riavvio del server interromperebbe anche heartbeat e tentativi e aumenterebbe
la contesa sul loop. Un agente esecutivo non può cambiare questa scelta.

### 4.3 Modello di consistenza e ammissibilità degli effetti

**[GATE V3]** va approvata un'estensione dello standard executor che dichiari
la semantica durevole, senza inferirla dal nome o da `revertible`. Profilo
proposto:

- `pure`: nessun effetto esterno; retry automatico ammesso;
- `idempotent`: chiave nativa e risultato riconoscibile; retry ammesso;
- `reconcilable`: esiste una lettura autorevole che distingue «eseguito» da
  «non eseguito»; retry solo dopo riconciliazione;
- `manual_only`: effetto ambiguo; nessun retry automatico.

La v1 deve accettare automaticamente soltanto `pure`, letture e pubblicazioni
interne nel deposito degli artefatti. Qualunque ampliamento a effetti remoti o
mutanti richiede contratto firmato, test di collisione e strategia di
riconciliazione. Undo resta una compensazione, non un profilo di consistenza.

### 4.4 Estensione della politica centrale

**[GATE V4]** l'ADR deve definire un contesto di ammissione opzionale, senza
cambiare il comportamento dei chiamanti esistenti:

```text
ExecutionContext(
  owner_user_id,
  workload_id,
  revision_id,
  stage_id,
  unit_key,
  attempt_id,
  priority,
  resource_claims,
  deadline_at
)
```

Il contesto non concede autorità. Serve a equità, telemetria e budget. Le
risorse multiple devono essere acquisite in ordine canonico per evitare
deadlock; provider e tier derivano dal binding congelato, non da stringhe
proposte dall'LLM.

### 4.5 Contratto del piano e pubblicazione

**[GATE V5]** vanno ratificati:

- schema `metnos.durable-plan/1` e regole di compatibilità;
- insieme chiuso dei tipi di fase iniziali;
- sintassi dei riferimenti fra fasi, senza `eval` o template arbitrari;
- collocazione dei blob e durata di retention;
- differenza tra artefatto scaricabile nel deposito Metnos e pubblicazione in
  un path/provider richiesto dall'utente;
- estensione di `write_files` o nuovo contratto firmato per una pubblicazione
  atomica con digest, se si consente il secondo caso.

F0 termina soltanto con ADR accettata, decisione di Roberto sui nomi pubblici e
fixture canoniche congelate. F1-F13 non possono trasformare una raccomandazione
di questa sezione in decisione implicita.

## 5. Architettura proposta

### 5.1 Flusso e confini

```text
richiesta autenticata
        |
        v
motore Metnos -> compilatore durevole -> validatore deterministico
                                           |
                                           v
                              piano e inventario immutabili
                                           |
                                           v
                                 store transazionale
                                           |
                    +----------------------+----------------------+
                    |                                             |
                    v                                             v
        coordinatore / coda equa                        API / eventi / UI
                    |
                    v
        claim + lease + fencing
                    |
                    v
      agent_runtime.invoke_executor
          |                 |
          v                 v
       server          coda remota firmata
          |                 |
          +--------+--------+
                   v
       risultato staged e convalidato
                   |
                   v
          commit compare-and-set
                   |
             mappe / riduzioni
                   |
                   v
       artefatti verificati + outbox
```

Sono autorità distinte:

- il **compilatore** decide soltanto la forma candidata del piano;
- il **validatore** ammette nomi, schema, autorità, budget e dipendenze;
- lo **store** decide lo stato persistente;
- il **coordinatore** decide quale unità tentare, non che cosa le sia
  consentito fare;
- `invoke_executor` decide collocazione, sandbox e capacità effettive;
- il **committer** decide se il tentativo possiede ancora il fence valido;
- il **validatore di copertura** è l'unico che può rendere il lavoro
  completabile.

Nessun risultato testuale LLM può cambiare direttamente una di queste
decisioni.

### 5.2 Moduli interni proposti

Il nome del pacchetto Python resta provvisorio fino a V1. Se approvato, la separazione
raccomandata è:

| Modulo | Responsabilità unica | Non deve fare |
|---|---|---|
| `models.py` | enum chiusi, dataclass immutabili, schema del piano | I/O o migrazioni |
| `migrations.py` | versioni SQLite additive e controlli di compatibilità | logica di scheduling |
| `storage.py` | transazioni, CAS, claim, heartbeat, commit e query owner-scoped | invocare executor |
| `admission.py` | convalida di piano, catalogo, contratti, autorità e budget | correggere il piano con un LLM |
| `compiler.py` | candidato da richiesta naturale e inventario dichiarato | ampliare catalogo o autorità |
| `inventory.py` | scansione con limiti espliciti, hash, identità e sigillo | elaborare contenuti |
| `coordinator.py` | equità, priorità, unità pronte e backoff | creare pool privati |
| `worker.py` | ciclo claim-esegui-stage-commit con shutdown cooperativo | scrivere SQL direttamente |
| `execution.py` | ponte verso `invoke_executor`, remoto e contesto LLM | bypassare scheduler o sandbox |
| `artifacts.py` | blob, staging, digest, pubblicazione e riconciliazione | autorizzare path arbitrari |
| `reduction.py` | albero stabile di fan-in e invalidazione dei nodi | caricare il corpus intero |
| `events.py` | eventi monotoni persistenti e outbox | comporre messaggi di dominio |
| `service.py` | inizializzazione, recupero, stato di salute e ciclo di vita | contenere logica di fase |

`runtime/jobs/` contiene oggi callback di manutenzione. Non va riutilizzato per
il pacchetto principale: la sovrapposizione renderebbe ambiguo il termine
*lavoro* anche nel codice.

### 5.3 Regola di dipendenza

Le dipendenze devono puntare verso il centro:

```text
HTTP / Telegram / executor di controllo
                  -> service API
                  -> storage + admission
worker -> coordinator -> storage
worker -> execution -> agent_runtime.invoke_executor
worker -> artifacts/reduction -> storage
```

Route, template e canali non importano SQLite. Gli executor non importano lo
store. Il worker non importa moduli HTTP. `storage.py` non importa loader,
router LLM o canali: riceve record già convalidati.

## 6. Modello persistente

### 6.1 Collocazione e proprietà

**[PROPOSTA]** metadati in un database SQLite dedicato sotto
`config.PATH_USER_STATE`; blob e artefatti sotto `config.PATH_USER_DATA`, in
una radice per proprietario. I nomi concreti si fissano in V1.

Requisiti dello store:

- WAL, `foreign_keys=ON` e `busy_timeout` con un limite esplicito;
- migrazioni numerate e transazioni esplicite;
- timestamp UTC e contatori monotoni, senza usare l'orologio murale per il
  fencing;
- permessi 0600 per database/file e 0700 per directory;
- nessun JSON mutabile come unica fonte di uno stato critico;
- nessuna deduplicazione fisica tra proprietari nella prima versione;
- blob temporanei creati nello stesso filesystem della destinazione atomica;
- backup prima di una migrazione non additiva e rifiuto del downgrade
  incompatibile.

### 6.2 Entità minime

| Entità | Campi normativi principali | Vincoli essenziali |
|---|---|---|
| `workloads` | id, owner, richiesta originale redatta, stato, priorità, budget, revisione attiva, date, motivo terminale | owner non vuoto; stato chiuso; indice `(owner, state)` |
| `revisions` | id, workload, numero, piano canonico, hash piano, snapshot catalogo/policy, supersedes, date | `UNIQUE(workload_id, number)` e piano immutabile dopo ammissione |
| `stages` | revisione, id stabile, ordine, tipo, executor/workload logico, schema I/O, retry, invalidazione, dipendenze | `UNIQUE(revision_id, stage_key)`; grafo aciclico validato |
| `sources` | revisione, source id, device, locator redatto, ordinal, tipo, size, mtime, hash, stato | `UNIQUE(revision_id, source_id)` e ordinal stabile |
| `units` | revisione, fase, unit key, source/shard, stato, attempt count, next attempt, lease owner, fence, expiry, result id | `UNIQUE(revision_id, stage_id, unit_key)`; fence intero crescente |
| `attempts` | id, unità, numero, fence, worker/device/invocation, start/end, esito, errore, snapshot executor/LLM, metriche | append-only; `UNIQUE(unit_id, number)` |
| `results` | id, unità, attempt, digest, schema, blob/ref, provenienza, committed at | un solo risultato attivo per unità; attempt e fence coerenti |
| `dependencies` | risultato/nodo figlio, risultato sorgente, ruolo, ordinal | unicità della relazione e ordine stabile |
| `artifacts` | id, workload/revisione, nome logico, digest, mime, size, stato, blob, retention, published target | nome logico unico nella revisione; digest verificato |
| `publications` | artifact, target, stato prepare/published, expected digest, osservazione, attempt | nessun `published` senza verifica leggibile |
| `events` | workload, event id, tipo, payload redatto, data | `UNIQUE(workload_id, event_id)` monotono |
| `outbox` | event, canale, destinatario owner-scoped, stato, tentativi, next attempt, ack | dedup per evento/canale; niente delete prima dell'ack |
| `scheduler_credits` | owner/lavoro, deficit, ultima scelta, quota | aggiornamento nella stessa transazione del claim |

Le colonne usate per selezione, isolamento, unicità o transizione devono essere
relazionali. JSON è ammesso per il piano immutabile, snapshot, diagnostica ed
errori strutturati, sempre con `schema_version` e limite di dimensione.

### 6.3 Chiavi e digest

Tutti i digest usano una serializzazione canonica, prefisso di dominio e
versione. Non si concatena JSON non ordinato.

```text
source_id = H(device_id, identità filesystem/provider, content_hash)

unit_key = H(
  stage_contract_digest,
  ordered_dependency_digests,
  semantic_args_digest,
  shard_coordinates
)

result_digest = H(output_schema_version, canonical_result_or_blob)
```

`revision_id` non entra nel digest semantico del risultato: in questo modo una
nuova revisione può riusare un nodo invariato. L'associazione alla revisione
resta nel vincolo della riga e nel grafo delle dipendenze.

Sono esclusi dal digest semantico solo campi esplicitamente dichiarati
irrilevanti, come timestamp di telemetria. Un agente non può compilare una
lista ad hoc di esclusioni per far passare un test.

### 6.4 Snapshot di esecuzione

Ogni tentativo registra almeno:

- nome, versione, digest del codice e hash del contratto firmato dell'executor;
- `membership`, `source`, `transport`, `intelligence` e policy di esecuzione;
- argomenti semanticamente rilevanti in forma redatta e relativo digest;
- collocazione reale, device e `invocation_id` quando remoto;
- workload LLM logico, tier/livello richiesto e binding risolto;
- provider, modello, policy di generazione e digest della configurazione,
  senza endpoint segreti o credenziali;
- ruolo, lingua, sorgente effettiva e digest del prompt; digest delle variabili
  semanticamente rilevanti, non copia indiscriminata del prompt;
- token, latenza, costo quando disponibile e motivo di assenza altrimenti.

Il binding è congelato per revisione o fase secondo V5. Se non può essere
ricostruito, l'unità diventa `needs_attention`; non viene eseguita con il
default corrente.

## 7. Macchine a stati e invarianti

### 7.1 Stato del lavoro

```text
draft -> admitted -> queued -> running
                       |         |
                       |         +-> pause_requested -> paused -> queued
                       |         +-> cancel_requested -> cancelled
                       |         +-> needs_attention
                       |         +-> failed
                       |         +-> completed_with_errors
                       |         +-> completed
                       +------------> cancelled
```

Regole:

- `draft` può contenere un errore di compilazione, ma nessuna unità eseguibile;
- dopo `admitted`, il piano della revisione è immutabile;
- `pause_requested` impedisce nuovi claim; non invalida un commit già in
  corso;
- `cancel_requested` è cooperativo e impedisce nuovi claim. Un tentativo
  mutante non viene ucciso nel mezzo se ciò renderebbe l'effetto più ambiguo;
- `needs_attention` è quiescente e non equivale a successo. La ripresa richiede
  una decisione registrata o una nuova revisione;
- `completed_with_errors` è ammesso soltanto se la policy della revisione
  enumera le classi tollerate e ogni fallimento compare negli artefatti;
- lo stato terminale deriva dalle unità e dagli artefatti nella stessa
  transazione, non da un contatore aggiornato best-effort.

### 7.2 Stato dell'unità

```text
pending -> leased -> running -> committed
   |          |          |
   |          |          +-> retry_wait -> pending
   |          |          +-> failed_permanent
   |          |          +-> needs_attention
   |          +------------> pending       (lease scaduta, fence nuovo)
   +-----------------------> cancelled | skipped
```

Solo `committed` alimenta una fase a valle. `running` con un file già presente
non è completata finché il digest non è riconciliato e il CAS non è riuscito.
Una unità col fence precedente può terminare fisicamente, ma non può più
committare.

### 7.3 Condizione di completezza

L'unica funzione che può proporre `completed` verifica in una transazione:

1. inventario presente e sigillato;
2. nessuna sorgente instabile o non contabilizzata;
3. tutte le unità richieste in stato terminale ammesso;
4. nessun cap, truncation o output parziale non accettato;
5. ogni dipendenza richiesta risolta nella stessa revisione o riusata con
   digest compatibile;
6. ogni artefatto obbligatorio in stato `committed` o `published`, secondo il
   piano;
7. digest, schema e postcondizioni degli artefatti validi;
8. budget e costo finali materializzati;
9. evento terminale e record outbox inseriti nella stessa transazione.

La percentuale è una proiezione: il dato autorevole resta il sestetto
`discovered / committed / failed / skipped / attention / pending`.

## 8. Protocolli transazionali

### 8.1 Ammissione e sigillo dell'inventario

1. Autenticare il proprietario e acquisire `owner_session`.
2. Compilare un candidato senza eseguirlo.
3. Risolvere executor e workload esclusivamente dal catalogo caricato e
   verificato.
4. Convalidare schema, autorità, effetti, budget, riferimenti e aciclicità.
5. Scansionare l'input entro limiti di numero, byte e profondità.
6. Per ogni file: risolvere host/device, applicare la policy symlink, leggere
   `lstat`, calcolare hash da contenuto e rileggere metadata; se cambia durante
   l'hash, marcarlo instabile e ritentare entro un limite prestabilito.
7. Ordinare per identità canonica, assegnare ordinali e sigillare il manifest.
8. Inserire revisione, fonti, fasi, unità iniziali ed evento `admitted` in una
   sola transazione.

Un input aggiunto dopo il sigillo non entra silenziosamente. Richiede una nuova
revisione o una policy esplicita di inventario dinamico, fuori dalla v1.

### 8.2 Claim, lease e fencing

Il claim deve essere una singola operazione di storage:

1. `BEGIN IMMEDIATE`;
2. scegliere un'unità pronta secondo equità, dipendenze, `next_attempt_at` e
   stato del lavoro;
3. incrementare il fence monotono dell'unità;
4. creare l'attempt append-only con quel fence;
5. scrivere worker, scadenza lease e stato `leased` con CAS;
6. aggiornare il credito di scheduling;
7. commit;
8. eseguire fuori dalla transazione.

L'heartbeat aggiorna la scadenza soltanto con
`WHERE unit_id=? AND attempt_id=? AND fence=? AND state IN (...)`. Zero righe
aggiornate significa perdita della lease: il worker interrompe il lavoro
cooperativo e non prova a committare.

La durata della lease supera il massimo intervallo heartbeat, non il timeout
totale dell'executor. Operazioni molto lunghe rinnovano; operazioni bloccate
scadono. Il jitter del retry è deterministico, derivato da chiave unità e
numero del tentativo, così test e restart non cambiano il calendario.

### 8.3 Commit del risultato

1. Convalidare la forma dell'output contro lo schema meccanico della fase.
2. Scrivere eventuali blob nel deposito content-addressed e verificarne il
   digest.
3. Aprire `BEGIN IMMEDIATE`.
4. Rileggere unità, attempt e fence.
5. Se il fence è scaduto, registrare il tentativo tardivo e rifiutare il
   commit senza sovrascrivere il vincitore.
6. Inserire il risultato con vincolo unico; se esiste, accettarlo soltanto se
   il digest coincide.
7. Collegare provenienza e dipendenze.
8. Portare l'unità a `committed`, liberare lease e inserire l'evento.
9. Creare le unità dipendenti ora sbloccate in ordine deterministico.
10. Valutare la completezza della fase e del lavoro.
11. Commit.

Un digest diverso per la stessa chiave non viene risolto con «ultimo vince»:
è `contract_violation` o `needs_attention` e conserva entrambi i tentativi per
l'audit.

### 8.4 Crash nei punti critici

| Punto di arresto | Stato osservabile al riavvio | Azione corretta |
|---|---|---|
| prima dell'invocazione | attempt con lease, nessun effetto noto | attendere scadenza e ritentare |
| durante una lettura/purezza | lease scade | ritentare con fence nuovo |
| dopo risultato, prima del blob | nessun risultato committato | ritentare |
| dopo blob, prima del DB | blob orfano possibile | ritentare; GC lo rimuove solo dopo grace period |
| dopo insert risultato, prima dell'ack worker | unità già `committed` | nuovo worker non la reclama; ack duplicato è no-op |
| dopo effetto esterno ambiguo | esito ignoto | riconciliare o `needs_attention`, mai retry cieco |
| durante pubblicazione | record `prepared` e temp/finale possibili | eseguire riconciliazione per digest |

### 8.5 Blob e pubblicazione filesystem

SQLite e filesystem non condividono una transazione. Il protocollo corretto è
prepare-publish-reconcile:

1. scrivere un temporaneo nella directory del deposito;
2. `fsync` del file, hash e verifica size;
3. rinominare verso il nome content-addressed; `fsync` della directory;
4. inserire o riusare la riga del blob; un blob orfano è innocuo e raccoglibile;
5. per un target finale, creare `publication=prepared` con digest atteso;
6. scrivere il temporaneo **nella directory del target**, poi `fsync`;
7. applicare la policy di collisione e rinominare atomicamente;
8. rileggere il file finale e confrontarne il digest;
9. marcare `published` con CAS;
10. al boot, riconciliare ogni `prepared`: digest uguale significa commit
    recuperabile; digest diverso significa `needs_attention`.

La pubblicazione interna al deposito Metnos è la baseline v1. Per path esterni,
share o provider si usa un executor con profilo durevole firmato. Il runtime
non usa scritture privilegiate per aggirare `write_files`.

### 8.6 Invocazione remota

Il ponte remoto richiede un'estensione compatibile della coda:

- il worker genera prima un `attempt_id` stabile;
- l'enqueue accetta una `invocation_id` fornita o una `dispatch_key` unica e
  rifiuta lo stesso id con payload diverso;
- il payload firmato include lavoro, revisione, fase, unit key, attempt e
  fence, oltre al contesto già previsto;
- la relazione viene registrata prima di attendere il device;
- un result remoto viene conservato come risultato del tentativo, ma solo lo
  store durevole decide il commit dopo aver verificato il fence;
- una risposta tardiva del device non può riaprire un'unità già vinta;
- per effetti non puri vale la classificazione V3, indipendentemente dal fatto
  che il trasporto abbia deduplicato la `invocation_id`.

### 8.7 Effetti esterni e riconciliazione

Una unità mutante può essere ritentata automaticamente soltanto se il contratto
indica:

- chiave di idempotenza inviata al provider, oppure
- identità dell'oggetto risultante e lettura autorevole di riconciliazione;
- esiti `not_applied`, `applied_same`, `applied_conflict`, `unknown`;
- postcondizione osservabile;
- comportamento su timeout prima e dopo l'invio.

`unknown` e `applied_conflict` portano a `needs_attention`. Una compensazione
undo può essere proposta dopo, ma non trasforma retroattivamente un effetto in
idempotente.

## 9. Piano, revisioni e invalidazione

### 9.1 Contratto del piano durevole

Il piano v1 contiene almeno:

- versione dello schema e hash canonico;
- obiettivo originale, criteri terminali e policy di errore;
- inventario o regola esatta che lo produce;
- fasi con tipo chiuso `inventory`, `map`, `reduce`, `validate`, `publish`;
- dipendenze, schema di input/output e cardinalità attesa;
- executor o workload logico; mai codice, shell o nome inventato;
- proiezioni tipizzate fra output e input, senza espressioni valutabili;
- policy di retry, timeout, invalidazione e batching;
- limiti per unità, concorrenza, token, costo, tempo e dimensione;
- artefatti obbligatori, schema, ordine e postcondizioni.

Le espressioni tra fasi sono riferimenti strutturati, per esempio oggetti
`{"ref": "source.path"}` o `{"ref": "dependency.entries", "field": "..."}`
validati contro uno schema chiuso. Non si persistono Jinja, Python, SQL o
template shell provenienti dal modello.

### 9.2 Compilazione da linguaggio naturale

1. Il turno riconosce una richiesta pesante senza eseguirla come normale
   sequenza lunga.
2. Il motore seleziona un preset ammesso o propone fasi dal catalogo corrente.
3. `compiler.py` produce soltanto il candidato `durable-plan/1`.
4. `admission.py` ricalcola ogni fatto: esistenza executor, firma, schema,
   effetto, collocazione, dipendenze, budget e autorità.
5. Se manca una capacità, il lavoro resta in bozza e segue Synt/proposta,
   test, firma e ammissione. Non genera codice durante l'esecuzione.
6. Se sono richiesti costo, credenziali o mutazioni non già autorizzate, il
   normale gate Metnos raccoglie il consenso prima di `admitted`.
7. Il turno restituisce identificativo, sintesi del piano e limiti; il worker
   prosegue fuori dal turno.

Il rilevamento di una richiesta «pesante» deve fallire in modo conservativo:
un falso negativo resta un turno ordinario con i suoi cap; un falso positivo
mostra una bozza e non produce effetti.

### 9.3 Revisioni e richiesta duplicata

- Un retry HTTP usa una idempotency key del submit e riottiene lo stesso
  lavoro.
- La chat registra una chiave di presentazione legata a proprietario,
  conversazione e richiesta accettata, così la riconsegna dello stesso evento
  non crea un duplicato.
- La somiglianza semantica non unisce automaticamente due richieste: può
  proporre di collegarsi a un lavoro attivo, ma l'utente può chiedere una nuova
  esecuzione.
- Qualunque modifica a piano, binding incompatibile, inventario o criteri
  terminali crea una revisione numerata con `supersedes`.
- Una revisione non mescola risultati incompatibili. Può riusare soltanto
  nodi il cui digest di dipendenza coincide.

### 9.4 Invalidazione minima

Il digest di un nodo include:

- digest delle sorgenti o dei risultati dipendenti;
- versione e schema della fase;
- executor e hash del contratto/codice;
- argomenti semantici;
- workload, binding e prompt per fasi generative;
- ordine/shard e policy di riduzione.

Quando cambia una sorgente, si invalidano la sua unità e i discendenti del DAG.
I fratelli indipendenti restano committati. Quando cambia soltanto un artefatto
di presentazione, non si ricalcola l'OCR se il suo digest non dipende dalla
presentazione.

### 9.5 Riduzione gerarchica

I nodi di riduzione sono costruiti con ampiezza massima configurata, ordine degli input
stabile e chiave derivata dai digest figli. Il livello successivo nasce solo
quando tutti i figli previsti sono terminali secondo policy. Nessun prompt
riceve l'intero corpus se supera il budget; si riducono record strutturati o
testi di dimensione limitata con provenienza.

Una variazione del fan-in è semanticamente rilevante se il riduttore non è
provato associativo. In assenza di tale prova entra nel digest della fase.

## 10. Scheduling, budget e priorità

### 10.1 Due livelli, una sola autorità di esecuzione

Il coordinatore sceglie **quale** unità pronta proporre; lo scheduler centrale
decide **se e quando** l'invocazione può consumare risorse. Il coordinatore non
crea un `ThreadPoolExecutor`, non chiama subprocess e non assegna budget
interni agli executor.

Il worker può avere più reclami in corso solo tramite l'API centrale ammessa. Il
fan-out interno di un executor continua a usare `assigned_workers()`.

### 10.2 Equità proposta

La selezione persistente usa deficit round-robin su due livelli:

1. proprietario, con quota e limite alle unità in corso;
2. lavoro del proprietario, con peso limitato e incremento progressivo della
   priorità in base all'attesa.

Il credito viene aggiornato nella stessa transazione del claim. Un riavvio non
azzera la fairness né fa saltare un lavoro in testa. Le priorità sono poche,
chiuse e limitate dalla policy; non sono un numero arbitrario scelto dal
planner.

Gli slot interattivi della chat mantengono una riserva per la responsività. Il
lavoro in background riceve comunque avanzamento minimo: l'age boost impedisce
starvation. Quote e valori iniziali devono essere misurati in F0, non copiati
dal numero di CPU del computer di sviluppo.

### 10.3 Risorse multiple

Il contesto di ammissione può reclamare più dimensioni, per esempio:

```text
global=1, owner=1, workload=1, cpu=1, local_io=1,
llm:<binding>=1, provider:<account-or-endpoint>=1, device:<id>=1
```

Le chiavi dinamiche derivano da identità già convalidate e vengono redatte
nella telemetria. Tutti i semafori sono acquisiti in ordine lessicografico
canonico e rilasciati in ordine inverso. Un timeout di ammissione riporta
l'unità in `retry_wait` senza contarlo come fallimento dell'executor.

### 10.4 Budget

I budget minimi sono: unità, tentativi per unità, errori tollerati, wall clock,
tempo CPU quando misurabile, token, costo, byte letti/scritti, artefatti e
concorrenza. Sono congelati nella revisione e possono essere solo ristretti
dalla policy operativa. Un aumento richiesto crea una decisione registrata o
una nuova revisione.

Budget esaurito è uno stato strutturato, non un errore transitorio e non una
truncation nascosta. Il lavoro non può diventare `completed` finché l'utente
non accetta esplicitamente una copertura ridotta prevista dalla policy.

## 11. Identità, sicurezza e ciclo di vita dei dati

### 11.1 Isolamento del proprietario

Ogni tabella e ogni API ricevono l'identificativo immutabile autenticato del
proprietario. Il nome visualizzato, `actor`, canale o device non sono chiavi di
autorizzazione. Le query di dettaglio hanno sempre forma equivalente a
`WHERE owner_user_id=? AND id=?`; cercare prima per `id` e controllare dopo è
vietato perché può produrre canali laterali e messaggi differenti.

Il percorso degli artefatti deriva da un digest del proprietario e da ID
convalidati, mai dal nome fornito dall'utente. Il digest del contenuto non
concede accesso. In v1 non si condividono blob fisici fra proprietari.

### 11.2 Autorità e lavoro non presidiato

- Il piano conserva la minima autorità necessaria, non una copia delle
  credenziali.
- Ogni invocazione ricalcola le capability effettive dal contratto firmato.
- Un'attività che continua senza il turno deve usare i mandati persistenti
  previsti da ADR 0190 e verificarne scopo, proprietario, scadenza e revoca.
- Un consenso interattivo valido per un singolo turno non diventa un mandato
  permanente.
- Credenziale mancante o revocata porta a `needs_attention`, non a un backend
  alternativo non approvato.
- Un executor remoto riceve soltanto l'ambiente già ammesso e firmato; il
  worker non aggiunge path, token o capability.

### 11.3 Cancellazione utente

La sequenza proposta è:

1. `owner_deletion` acquisisce il lock esclusivo esistente;
2. il servizio marca i lavori non terminali `cancel_requested` e impedisce
   claim nuovi;
3. attende, entro un tempo massimo, la fine dei commit già protetti dal lock
   condiviso;
4. revoca download, outbox e riferimenti remoti;
5. elimina righe del proprietario in ordine referenziale;
6. elimina la radice privata degli artefatti con elenco esplicito e controlli
   anti-symlink;
7. verifica che non restino lease, pubblicazioni o notifiche;
8. prosegue la cancellazione utente esistente.

La cancellazione deve essere idempotente: un'interruzione riparte senza
ricreare dati.
Non si mantiene un worker sotto `owner_session` per ore, perché impedirebbe la
cancellazione.

### 11.4 Input e path

- La sorgente registra device e identità filesystem/provider; un path Windows
  non viene interpretato sul server.
- Symlink, hard link, mount e file speciali seguono una policy esplicita.
- Il congelamento usa hash e metadata riletti per rilevare modifiche durante
  la scansione.
- File rimossi dopo il sigillo restano nell'inventario come `missing`, non
  scompaiono dal denominatore.
- File modificati diventano `source_changed`; la policy decide nuova revisione
  o attenzione, mai sostituzione silenziosa.
- Directory che cresce oltre cap/byte/time produce un errore di inventario
  visibile prima dell'ammissione.

## 12. API, UI, eventi e canali

### 12.1 Contratto interno prima del nome pubblico

Fino alla decisione V1, service e test usano un'API Python interna con DTO
owner-scoped. Le route HTTP e gli executor di controllo vengono aggiunti solo
dopo l'approvazione del vocabolario. Le operazioni minime restano:

- submit/attach;
- elenco e dettaglio;
- piano, revisione, sorgenti, fasi ed errori;
- pausa, ripresa e annullamento;
- ritentativo selettivo e risoluzione di `needs_attention`;
- creazione esplicita di una revisione;
- elenco e scaricamento degli artefatti.

Ogni mutazione usa idempotency key e precondizione di versione. Un comando
stale riceve conflitto, non sovrascrive una decisione più recente.

### 12.2 Eventi persistenti

Gli eventi hanno un ID monotono per lavoro assegnato nella transazione che
produce il cambiamento. Il payload ha dimensione limitata ed è localizzato in
fase di lettura quando
possibile e non contiene prompt, credenziali o contenuti completi.

La route SSE applica:

- autenticazione e confronto owner prima di aprire lo stream;
- `Last-Event-ID` per replay dal database;
- heartbeat senza creare righe persistenti;
- backpressure e chiusura pulita;
- retention che conserva almeno gli eventi necessari a ricostruire lo stato
  mostrato;
- fallback JSON sullo stesso read model, non scansione dei turni recenti.

`TurnEventLog` è un precedente di protocollo, non lo store da estendere.

### 12.3 UI web

La UI iniziale deve mostrare, senza caricare migliaia di unità:

- elenco paginato dei lavori del proprietario;
- titolo, stato, date, priorità e contatori reali;
- dettaglio del piano e della revisione attiva;
- progresso per fase e categorie di errore;
- tier/workload e budget effettivi, non nomi inventati dal piano;
- azioni abilitate secondo stato e versione;
- elenco paginato delle unità fallite o in attenzione;
- artefatti con nome, size, digest breve, retention e download;
- avvisi chiari per copertura parziale, costo, credenziali e policy.

La pagina usa il CSS e i componenti comuni esistenti; non introduce stili
inline o una seconda navigazione. Il thread HTTP non calcola hash, riduzioni o
anteprime pesanti. Nessuna schermata presenta il lavoro come completo perché
lo stream si è chiuso.

### 12.4 Download degli artefatti

La route risolve `(owner, workload, artifact_id)` nel registro, verifica stato,
retention e revoca, poi apre il blob con difese anti-symlink. Il nome del
download proviene dal record convalidato e viene sanificato nelle intestazioni.

Un URL firmato opzionale contiene identificativo dell'artefatto, legame con il
proprietario, scadenza e nonce/revoca; non deriva dalla scansione di `TurnLog` e non usa l'indice
posizionale delle foto. Logout/login non invalida l'artefatto, mentre la
cancellazione utente o la revoca sì.

### 12.5 Telegram e outbox

Gli eventi notificabili iniziali sono: accettazione, richiesta di intervento,
fallimento terminale, completamento e disponibilità artefatti. Aggiornamenti di
progresso sono opt-in e coalescenti; mai uno per unità.

Protocollo outbox:

1. la transazione di stato inserisce una riga deduplicata;
2. il daemon reclama la consegna con lease breve;
3. invia al destinatario ricavato dal proprietario corrente;
4. salva message ID/ack e stato `sent`;
5. su errore applica backoff; non elimina la riga;
6. pairing revocato o utente cancellato porta a esito terminale redatto.

Il link Telegram deve aprire una superficie autenticata o usare una firma
scoped e breve; non rende pubblico il blob.

## 13. Gestione operativa

### 13.1 Avvio e ripresa

All'avvio il servizio, prima di reclamare nuove unità:

1. convalida versione schema e permessi;
2. acquisisce un lock di bootstrap, senza essere singleton globale per tutta
   la vita;
3. riconcilia lease scadute e incrementa i fence solo al nuovo claim;
4. riconcilia blob temporanei e pubblicazioni `prepared`;
5. chiude attempt impossibili lasciati senza unità valida;
6. ricostruisce contatori materializzati e segnala discrepanze;
7. riprende outbox e lavori non terminali;
8. dichiara il servizio pronto soltanto dopo queste operazioni, eseguite entro
   limiti espliciti.

Una riconciliazione troppo grande prosegue in batch e rende il servizio
`degraded`, non blocca indefinitamente systemd. Le operazioni sono idempotenti
e testate con due avvii consecutivi.

### 13.2 Health e metriche

Health espone conteggi, non dati del contenuto:

- lavori per stato e proprietari attivi aggregati;
- unità pronte, in volo, in retry e più vecchia attesa;
- lease scadute e commit stale rifiutati;
- throughput e latenza per classe di risorsa;
- retry per classe di errore;
- outbox pendente e più vecchia notifica;
- byte blob, orfani eleggibili e retention arretrata;
- token/costo aggregati e budget esauriti;
- versione dello schema, identificativo del worker e ultima ripresa riuscita.

La cardinalità di metriche e log è limitata: le etichette esportate non
contengono identificativi di unità, percorsi, prompt o proprietari. Il dettaglio
sensibile resta nell'audit circoscritto al proprietario.

### 13.3 Retention e garbage collection

- Nessun blob referenziato da una revisione non terminale è eliminabile.
- I blob senza riferimento hanno un grace period maggiore della massima
  durata di prepare/commit.
- Artefatti finali e risultati intermedi hanno retention distinta.
- Un artefatto scaduto resta registrato come tale; la UI non inventa un link.
- La GC reclama batch piccoli con lock e ricontrolla il riferimento nella
  transazione immediatamente precedente all'unlink.
- Una cancellazione manuale inattesa produce `artifact_missing` e
  `needs_attention`, non una riga di successo stantia.
- La manutenzione può essere svegliata da scheduler v2, ma la sua logica vive
  nel servizio/store durevole.

### 13.4 Distribuzione e rollback

La funzione parte dietro un interruttore spento. Ordine raccomandato:

1. schema e read-only inspection;
2. worker con executor fittizio e corpus temporanei;
3. preset immagini su fixture sintetiche;
4. singolo proprietario pilota;
5. UI e Telegram;
6. abilitazione predefinita dopo prove restart e 10x.

Il rollback spegne nuovi submit e lascia leggibili lavori/artefatti. Non
downgrada il database distruttivamente. Prima di rimuovere codice deve esistere
un esportatore o un periodo di compatibilità per i lavori non terminali.

## 14. Invarianti e non-obiettivi

### 14.1 Invarianti

1. Ogni unità attraversa `agent_runtime.invoke_executor` o il suo gemello
   ammesso; nessuna chiamata diretta a codice executor.
2. Nessun pool privato nel motore, nei preset o nelle route.
3. Ogni record operativo è owner-scoped con identità autenticata immutabile.
4. Piano e inventario ammessi sono immutabili; ogni cambiamento è una revisione.
5. Un fence scaduto non può committare, anche se il risultato è valido.
6. Un effetto ambiguo non viene ritentato automaticamente.
7. Ordine finale derivato da ordinali/identità, mai dall'ordine di completamento.
8. Cap, truncation, parzialità e item mancanti propagano fino allo stato finale.
9. Workload LLM e binding sono centrali; nessun nome modello nei consumer.
10. Nessun nuovo verbo, oggetto, capability, risorsa o stato pubblico senza
    processo di approvazione applicabile.
11. Tutti i messaggi utente passano da i18n; stati e codici interni restano
    indipendenti dalla lingua.
12. Documentazione pubblica descrive solo il comportamento distribuito e
    provato; questa RM resta interna.

### 14.2 Non-obiettivi della prima versione

- orchestratore generale per codice o DAG forniti dall'utente;
- shell generale, plugin dinamici o download automatico di skill;
- promessa di *exactly once* universale;
- transazioni distribuite fra server, device e provider;
- modifica in corsa di una revisione;
- inventario che cresce continuamente;
- deduplicazione di blob fra utenti;
- più coordinatori geografici active-active;
- migrazione immediata di ogni attività asincrona esistente;
- uso di un LLM per decidere retry, fencing, completezza o autorizzazione;
- sostituzione del limite di passi dei turni interattivi.

## 15. Rischi e misure anti-regressione

| Rischio | Segnale precoce | Difesa obbligatoria |
|---|---|---|
| doppio commit dopo lease scaduta | due attempt con output per la stessa unità | fence nel CAS, vincolo unico e test con due processi |
| doppio effetto esterno | timeout dopo invio, nessun risultato | profilo V3, idempotency key/reconcile, altrimenti attenzione |
| falso completamento | contatore 100% con source mancanti | denominatore sigillato e completion validator unico |
| perdita fra file e DB | pubblicazione `prepared` dopo un arresto anomalo | digest, `fsync`, rinomina e riconciliatore di ripresa |
| attesa indefinita della chat | aumenta il tempo di attesa interattivo durante un lotto | riserva centrale, priorità crescente con l'attesa e test sotto carico |
| attesa indefinita di un proprietario | lavoro sempre pronto ma mai scelto | credito persistente e test deterministico con più proprietari |
| esplosione della coda | tentativi simultanei o fornitore indisponibile | attesa esponenziale con variazione deterministica, interruttore automatico e quote |
| modello cambiato a metà | risultati con provider diversi senza revisione | binding congelato e fail-closed |
| prompt cambiato senza invalidazione | digest uguale dopo edit del template | identità prompt effettiva e test di mutazione |
| perdita notifica | riga rimossa prima di invio | outbox con conferma successiva all'invio e iniezione dei guasti |
| fuga tra utenti | ricerca per ID senza proprietario nella query SQL | API del repository che richiede prima il proprietario e test IDOR su ogni route |
| cancellazione bloccata | il worker mantiene il lock per ore | lock limitato al tentativo e blocco dei nuovi reclami |
| raccolta distruttiva | blob attivo non trovato | ricontrollo transazionale, periodo di grazia e dati di prova attivi |
| piano inventa capacità | nome non nel catalogo | admission deterministica e fixture avversariali |
| output executor malformato | `ok=true` ma schema incompatibile | validazione meccanica prima del commit |
| regressione dei turni correnti | cambia la firma o il valore predefinito di invocazione | parametri opzionali, interruttore di funzionalità disattivato e suite completa |
| riuso improprio scheduler v2 | callback lunga nel pool del daemon | test statico: worker non importato dal daemon callback |
| riuso improprio della build immagini | il nucleo dipende da `create_images_indices` | pacchetto generico senza importazioni di domini o configurazioni |
| log sensibili | percorsi o prompt nelle metriche | redazione e test del contratto di osservabilità |

## 16. Piano di attuazione per pacchetti verificabili

### 16.1 Regole di assegnazione

Ogni pacchetto qui sotto è un'unità di consegna, non un'indicazione generica.
Va assegnato a un solo agente esecutivo per volta e prodotto in un commit
inglese separato. L'agente riceve:

- questa sezione e i gate già ratificati;
- il percorso dei soli file di competenza;
- fixture e interfacce congelate dal pacchetto precedente;
- comando dei test obbligatori;
- formato del rapporto di consegna di §17.5.

Un agente di livello inferiore **esegue**, non ridisegna. Se trova una
contraddizione fra specifica, ADR e codice, si ferma sul solo punto e restituisce
una segnalazione di blocco con riferimenti; non sceglie il significato più
comodo.

Ordine delle dipendenze:

```text
F0 -> F1 -> F2
              +-> F3 --+
              +-> F4 --+
              +-> F5 --+-> I1 / F7
              +-> F6 --+       |
                                +-> F8 ----------------+
                                +-> F9 -> F10 UI/SSE --+-> I2
                                +-> F10 outbox --------+
                                +-> F11 senza UI ------+
                                                        |
                                                        v
                                                       F12 -> F13
```

F3-F6 possono procedere in parallelo soltanto dopo che F2 ha congelato le
interfacce. L'integrazione comune avviene in F7, non mediante unioni spontanee
fra gli agenti. Dopo F7, servizio, API, outbox e configurazione senza
interfaccia possono avanzare in parallelo; UI e SSE consumano il contratto F9.
Il gate I2 ricompone F8-F11 prima della certificazione distruttiva F12.

### 16.2 Sviluppo parallelo e integrazione finale

Lo sviluppo parallelo è possibile e consigliato in **due onde**, non come
modifica simultanea indiscriminata dello stesso albero.

#### Onda A — nucleo dopo F2

| Filone | Pacchetto | Proprietà esclusiva principale | Non modifica |
|---|---|---|---|
| A | F3 lease e ripresa | reclamo, tentativo, fence, worker fittizio | artefatti, pianificatore, scheduler centrale |
| B | F4 artefatti | deposito dei blob, pubblicazione, raccolta | reclamo, worker, route |
| C | F5 piano | schema logico, ammissione, inventario, riduzione | SQL condiviso, scheduler degli executor |
| D | F6 pianificazione | `executor_scheduler`, contesto opzionale, adattatore | archivio e piano durevole |

F1 deve avere già creato tutte le tabelle previste; F2 congela `models`, DTO e
porte del repository. Se un filone scopre che manca un'operazione condivisa,
non la aggiunge di nascosto al file posseduto da un altro: propone un'estensione
di porta con test contrattuale, che l'integratore approva e applica una volta.

**Gate I1 / F7:** un unico integratore unisce i commit nell'ordine
F3 -> F4 -> F5 -> F6, esegue i test dopo ogni integrazione e costruisce soltanto
alla fine il ponte reale. Se due filoni hanno toccato lo stesso simbolo
condiviso, il conflitto non si risolve scegliendo una versione: si riconciliano
le invarianti e si aggiunge un test che dimostra la composizione.

#### Onda B — prodotto dopo F7

| Filone | Pacchetto | Può avanzare con | Punto di ricongiungimento |
|---|---|---|---|
| E | F8 servizio | facciata e percorso verticale F7 | ciclo di vita e stato di salute I2 |
| F | F9 API | DTO e facciata congelati in F7 | route circoscritte al proprietario I2 |
| G | F10 outbox | eventi e facciata F7 | adattatore Telegram I2 |
| H | F11 configurazione senza interfaccia | piano, esecuzione e artefatti F7 | UI, download e test E2E I2 |
| I | F10 UI/SSE | specifica OpenAPI e fixture F9 congelate | I2 |

La configurazione di dominio non aspetta il CSS per provare OCR,
deduplicazione e riduzioni; la UI non aspetta il corpus reale per costruire
paginazione e stati, perché usa fixture contrattuali. Nessuno dei due filoni
inventa campi mancanti nell'altro.

**Gate I2:** l'integratore unisce servizio, API, outbox, configurazione e UI,
poi esegue un solo test E2E con un worker reale. F12 parte soltanto dopo I2
verde; iniettare guasti su rami separati produrrebbe prove non rappresentative
del sistema integrato.

#### Assetto raccomandato del gruppo

- un responsabile architetturale conserva le decisioni F0-F2, la matrice delle
  invarianti e il ramo d'integrazione; non accetta cambiamenti di contratto
  impliciti;
- fino a quattro agenti esecutivi possono coprire i filoni A-D se
  l'integratore è una quinta figura distinta;
- con quattro partecipanti complessivi, la configurazione più prudente è un
  integratore e tre agenti esecutivi: il quarto filone entra nel primo spazio
  liberato, senza trasformare l'integratore in autore simultaneo di più rami;
- nell'onda B, F9 deve congelare le fixture OpenAPI prima che il filone UI
  avanzi oltre le simulazioni; questo vincolo rende il parallelismo parziale,
  non totale;
- F12 appartiene a un agente di prova che non abbia scritto il codice
  transazionale principale. L'integratore conserva la decisione finale di
  accettazione o rifiuto.

L'integrazione avviene su un ramo pulito creato dalla base congelata. Ogni
pacchetto arriva come commit autonomo, accompagnato dal rapporto di §17.5;
l'integratore applica un commit alla volta, esegue le prove previste e conserva
la sequenza dei commit. Un pacchetto che richiede di riscrivere retroattivamente
un contratto già congelato torna al filone di origine oppure riapre
esplicitamente il gate architetturale: non viene adattato silenziosamente in
fase di integrazione.

#### Disciplina dei rami

- base comune marcata dopo F2 e dopo F7;
- rami brevi, un pacchetto e proprietà dei file non sovrapposta;
- interfacce condivise cambiano soltanto con commit di contratto dedicato;
- fixture guidate dai consumatori permettono ad API, UI e configurazione di
  avanzare senza simulazioni arbitrarie;
- nessuna rifirma, migrazione o modifica al vocabolario viene risolta durante
  un'integrazione;
- l'integratore non riscrive la logica dei filoni: rifiuta il pacchetto se
  il rapporto di consegna o i test sono insufficienti;
- dopo ogni gate di integrazione: suite dei pacchetti, suite dei moduli centrali,
  `git diff --check`, audit della circoscrizione al proprietario e test di
  riavvio del percorso verticale.

Questa struttura può ridurre il calendario, non il lavoro totale. Il guadagno
realistico è sulle parti indipendenti; F0-F2, F7, I2 e F12 restano sequenziali
perché fissano o verificano invarianti comuni.

### F0 — Decisioni, misure e contratti congelati

- **Assegnazione:** responsabile architetturale; non adatta a un agente
  puramente esecutivo.
- **Dipendenze:** nessuna.
- **Scopo:** chiudere V1-V5 e produrre le fixture normative per tutti i
  pacchetti.
- **Stato:** completata il 2026-08-20. Decisione in ADR 0213; misure, censimento,
  quattro JSON Schema e coppie di esempi validi/invalidi in
  `tests/fixtures/durable_workloads/`.

**Istruzioni:**

1. Misurare tempi di attesa e concorrenza su carico interattivo, OCR, VLM e I/O;
   non derivare i default dalla sola CPU nominale.
2. Censire gli executor necessari al preset immagini e classificare, con
   evidenza, schema d'output, effetto, collocazione, intelligence, trasporto,
   risorse e idoneità al retry.
3. Definire il nome pubblico o decidere esplicitamente di rinviare la superficie
   naturale, mantenendo l'API interna.
4. Ratificare topologia del worker, modello di consistenza, piano v1,
   `ExecutionContext`, profilo durevole degli executor e pubblicazione.
5. Decidere il rapporto tra configurazione VLM e workload LLM senza codificare
   un alias fittizio `precise`.
6. Produrre JSON Schema canonici per piano, errori, eventi e output del preset;
   ogni schema ha esempi validi e invalidi.
7. Produrre un diagramma delle transazioni e una matrice
   effetto-verso-retry-verso-reconcile.
8. Registrare ADR, decisione di Roberto e data in testa a questa RM.

- **File ammessi:** `decisions/`, questa RM, eventuali fixture sotto
  `tests/fixtures/durable_workloads/`; nessun runtime produttivo.
- **Vietato:** creare route, token di vocabolario, tabelle o unità systemd prima
  dell'accettazione dell'ADR.
- **Prove:** validazione degli schemi; controllo dei riferimenti ADR; revisione
  manuale della matrice da parte del responsabile.
- **Gate di uscita:** V1-V5 decisi, fixture versionate e nessun campo «da
  definire» indispensabile a F1-F7.

### F1 — Modelli, schema e migrazioni

- **Assegnazione:** agente esecutivo per il database.
- **Dipendenze:** F0.
- **Scopo:** creare il modello persistente senza worker, route o LLM.
- **Stato:** completata il 2026-08-20 in `runtime/durable_workloads/`.
  Schema v1, apertura esplicita, migrazione additiva, dump stabile, limiti JSON,
  permessi e rifiuto degli schemi futuri sono coperti dalla suite dedicata.

- **File ammessi:** nuovo pacchetto Python approvato (`models.py`,
  `migrations.py`, `schema.py` se previsto), `runtime/config.py` per i percorsi
  approvati, `tests/runtime/durable_workloads/test_schema.py` e fixture F0.
- **Interfacce da consegnare:** `open_db(path=None)`, `migrate(conn)`, versione
  dello schema leggibile, enumerazioni chiuse e DTO immutabili.

**Istruzioni:**

1. Tradurre esattamente le entità di §6 nello schema ratificato.
2. Usare foreign key, check e unique constraint per ogni invariante che il DB
   può applicare; non lasciarlo a commenti Python.
3. Rendere additive le prime migrazioni; ogni migrazione aggiorna la versione
   solo dopo successo completo.
4. Configurare WAL, foreign keys e busy timeout su ogni connessione, inclusi i
   test.
5. Limitare dimensione e versione dei campi JSON al confine di serializzazione.
6. Applicare permessi 0600/0700 senza dipendere dall'umask.
7. Non aprire il DB all'import del modulo e non usare singleton di connessione.
8. Aggiungere un controllo che rifiuta schema più nuovo del codice.

- **Test obbligatori:** database vuoto, doppia migrazione idempotente, rollback
  su errore iniettato, vincoli di proprietario, stato e fence, chiavi esterne,
  schema futuro rifiutato, permessi, apertura concorrente e fixture di
  aggiornamento dalla versione precedente quando esisterà.
- **Vietato:** SQL nelle route; `ALTER` distruttivi; migrazione che cancella
  righe; stato critico conservato solo in JSON.
- **Gate di uscita:** test del pacchetto verdi, dump dello schema confrontabile
  e nessuna API di dominio oltre apertura e migrazione.

### F2 — Repository transazionale e macchine a stati

- **Assegnazione:** agente esecutivo per la logica di persistenza.
- **Dipendenze:** F1.
- **Scopo:** rendere esprimibili tutte le transizioni senza eseguire unità.
- **Stato:** completata il 2026-08-20. Il repository applica isolamento per
  proprietario, idempotenza, CAS, eventi atomici, nove controlli di completezza
  e cancellazione selettiva; nessuna API esegue o reclama unità.

- **File ammessi:** `storage.py`, eventuali query e DTO interni del pacchetto,
  `tests/runtime/durable_workloads/test_durable_workload_storage.py` e
  `test_state_machine.py`. Non modificare scheduler, runtime dell'agente o
  HTTP.

**API minima:**

```text
create_draft(owner, request_key, ...)
admit_revision(owner, workload_id, plan, inventory, ...)
get/list owner-scoped
request_pause/resume/cancel(owner, id, expected_version)
record_attention_resolution(...)
append_event_in_transaction(...)
evaluate_completion(...)
purge_owner(owner)
```

**Istruzioni:**

1. Ogni metodo pubblico richiede `owner_user_id`; nessun default `host`.
2. Implementare una tabella esplicita delle transizioni consentite e testare
   tutte le coppie stato-origine/stato-destinazione.
3. Applicare optimistic version/CAS alle mutazioni di controllo.
4. Inserire evento e cambiamento di stato nella stessa transazione.
5. Derivare contatori dalle unità; eventuali contatori materializzati hanno un
   ricostruttore e un test di discordanza.
6. Rendere idempotenti submit con request key e comandi con idempotency key;
   la stessa chiave con un payload diverso produce un conflitto.
7. `evaluate_completion` applica tutti i nove controlli di §7.3 e non accetta
   un booleano «success» dal chiamante.
8. `purge_owner` elimina soltanto il proprietario richiesto ed è ri-eseguibile.

- **Test obbligatori:** due proprietari con gli stessi ID esterni; IDOR; comando
  obsoleto; doppio invio; payload discordante; tutte le transizioni illegali;
  completamento con sorgente mancante, limite raggiunto, artefatto mancante e
  risultato parziale; rollback fra stato ed evento; cancellazione selettiva.
- **Vietato:** catturare `sqlite3.IntegrityError` e trasformarlo sempre in
  successo; query per solo ID; richiamo LLM; correzioni automatiche dei piani.
- **Gate di uscita:** la macchina a stati è completamente esercitabile con dati
  fittizi e non esiste un percorso che produca `completed` fuori
  dall'archivio.

### F3 — Reclamo, lease, fencing e ripresa del processo

- **Assegnazione:** agente esecutivo per concorrenza e persistenza, con fixture
  congelate.
- **Dipendenze:** F2.
- **Scopo:** dimostrare che più processi non possono registrare definitivamente
  due risultati per la stessa unità.

- **File ammessi:** estensione di `storage.py`, nuovi `coordinator.py` e
  `worker.py` con adattatore di esecuzione fittizio, infrastruttura di prova
  sotto `tests/runtime/durable_workloads/`. Nessun executor reale.

**API minima:**

```text
claim_next(worker_id, now, lease_duration, capabilities) -> Lease | None
mark_running(lease)
heartbeat(lease, new_expiry) -> bool
commit_result(lease, validated_result) -> CommitOutcome
fail_attempt(lease, structured_error, retry_decision)
reconcile_expired(now, batch_size)
```

**Istruzioni:**

1. Eseguire il reclamo con `BEGIN IMMEDIATE`, una selezione equa fittizia e
   deterministica e l'incremento del fence nella stessa transazione.
2. Il token `Lease` contiene attempt e fence; nessuna API accetta soltanto
   `unit_id` per heartbeat o commit.
3. Eseguire il callable fittizio fuori dal DB.
4. Rifiutare commit e heartbeat obsoleti con esito distinto, non con un'eccezione
   generica.
5. Rendere il primo digest vincente; digest identico è replay idempotente,
   digest diverso è conflitto conservato.
6. Gestire l'arresto: smettere di reclamare nuove unità, completare o
   abbandonare quelle in corso entro un tempo massimo e non estendere le lease
   dopo la richiesta di arresto.
7. Implementare un'attesa esponenziale con variazione deterministica e tetto.
8. Usare un processo reale nei test: barriera o descrittore di file per
   terminare in
   punti controllati, mai due chiamate sequenziali alla stessa funzione come
   simulazione di restart.

- **Test obbligatori:** due processi in competizione; arresto prima
  dell'esecuzione, dopo il risultato ma prima del commit e dopo il commit ma
  prima della conferma; lease scaduta; vecchio worker che termina tardi;
  heartbeat perso; due avvii consecutivi; pianificazione dei nuovi tentativi
  stabile dopo il riavvio.
- **Vietato:** lock solo in memoria; PID come fence; `sleep` lungo nei test;
  `SELECT` e successivo `UPDATE` fuori dalla stessa transazione.
- **Gate di uscita:** in almeno 100 competizioni ripetute esiste un solo commit
  e il processo con il fence precedente non modifica lo stato.

### F4 — Deposito degli artefatti e pubblicazione riconciliabile

- **Assegnazione:** agente esecutivo per il filesystem.
- **Dipendenze:** F2; usa lease fittizie finché F3 non è integrata.
- **Scopo:** implementare §8.5 nel solo deposito Metnos.

- **File ammessi:** `artifacts.py`, repository degli artefatti conforme alle
  porte congelate in F2 e `tests/runtime/durable_workloads/test_artifacts.py`.
  Nessuna route e nessuna
  modifica a `storage.py` o `write_files` in questo pacchetto.

**Istruzioni:**

1. Radice circoscritta al proprietario e derivata da un ID sottoposto a hash;
   directory 0700, file 0600.
2. L'API accetta byte o flussi già autorizzati e metadati di dimensione
   limitata; non apre percorsi sorgente arbitrari.
3. Scrivere un file temporaneo, eseguire `fsync`, calcolare il digest,
   rinominare verso una destinazione basata sul contenuto ed eseguire `fsync`
   sulla directory.
4. Inserire il riferimento nel DB soltanto tramite il repository; un blob
   esistente è riutilizzabile solo se dimensione e digest coincidono.
5. Implementare preparazione e riconciliazione per una destinazione temporanea di test sullo
   stesso filesystem.
6. Rifiutare symlink, directory e destinazioni fuori dalla radice nella
   baseline.
7. Raccolta in lotti con periodo di grazia, ricontrollo referenziale immediato
   e log di dimensione limitata.
8. Cancellare i dati del proprietario da un elenco esplicito, senza glob estesi,
   con difese contro percorsi sostituiti durante l'operazione.

- **Test obbligatori:** contenuto duplicato per lo stesso proprietario;
  proprietari diversi; arresto dopo `fsync` e prima della rinomina, dopo la
  rinomina e prima del DB, e durante la pubblicazione; digest finale
  discordante; collegamento simbolico; raccolta che non elimina dati attivi;
  orfano recente o vecchio; cancellazione selettiva; filesystem che solleva un
  errore durante `fsync`.
- **Vietato:** `/tmp` per il file da rinominare; hash usato come autorizzazione;
  `shutil.rmtree` su percorso non ricostruito e verificato; segnare
  `published` prima della rilettura.
- **Gate di uscita:** dopo ogni punto di arresto, un secondo processo riconcilia
  uno dei soli esiti `committed`, `retryable` o `needs_attention`, senza file
  finali parziali.

### F5 — Piano v1, ammissione e invalidazione

- **Assegnazione:** agente esecutivo per schema e compilatore; nessuna libertà
  di vocabolario.
- **Dipendenze:** F0 e F2.
- **Scopo:** trasformare fixture candidate in revisioni ammesse, senza
  esecuzione.

- **File ammessi:** `admission.py`, `compiler.py`, `inventory.py` e
  `reduction.py` per la sola costruzione del grafo, oltre a test e fixture.
  Eventuali modifiche al motore soltanto in un pacchetto successivo, dopo una
  prova isolata.

**Istruzioni:**

1. Parser schema-first di `durable-plan/1`; campi ignoti bloccanti dove
   potrebbero ampliare semantica.
2. Risolvere executor con il loader verificato e congelare digest/contratto.
3. Risolvere workload con `llm_workloads.tier_for` e binding col router; non
   accettare tier libero dal candidato.
4. Convalidare output con JSON Schema approvato; `schema_inline` da solo non è
   sufficiente.
5. Verificare tipi di fase, riferimenti, DAG aciclico, cardinalità, budget e
   profilo effetti.
6. Canonicalizzare e hashare senza dipendere da ordine dei dict o locale.
7. Costruire invalidazione per digest delle dipendenze e albero di riduzione
   stabile.
8. L'inventario applica cap, policy symlink, doppio stat e ordinamento; nessun
   input instabile viene sigillato come sano.
9. Il compilatore LLM, se aggiunto, produce soltanto candidato; tutti i test di
   ammissione funzionano senza modello.

- **Test obbligatori:** executor inesistente o non firmato; workload o tier
  ignoto; ciclo; riferimento a un campo inesistente; output senza schema;
  autorità o effetto non ammessi; hash stabile; modifica di un input che
  invalida solo i discendenti; modifica del prompt, dell'executor o del binding;
  input che cresce durante il calcolo dell'hash; collegamenti simbolici e
  percorsi di dispositivo; riduzione oltre l'ampiezza massima.
- **Vietato:** `eval`, Jinja o Python dal piano; ripiego su un executor
  «simile»; accettazione basata sulla prosa del manifest; chiamata LLM nel
  validatore.
- **Gate di uscita:** la stessa fixture produce byte identici di piano canonico
  e grafo su due processi; ogni mutazione avversariale attesa viene respinta.

### F6 — Equità e contesto nello scheduler centrale

- **Assegnazione:** agente esperto del runtime centrale; modifica piccola e
  retrocompatibile.
- **Dipendenze:** F0 e interfacce F2.
- **Scopo:** aggiungere `ExecutionContext` e risorse multiple senza cambiare i
  turni correnti.

- **File ammessi:** `runtime/executor_scheduler.py`, metadati e standard
  soltanto se l'ADR lo richiede, adattatore di
  `agent_runtime.invoke_executor` e relativi test. Nessuna importazione del
  pacchetto durevole nello scheduler.

**Istruzioni:**

1. Aggiungere parametro opzionale; `None` riproduce esattamente il percorso
   attuale.
2. Il contesto viene costruito dal worker e validato come dato interno; non è
   copiato dagli args executor.
3. Acquisire slot globali, risorse, executor e identità in ordine canonico;
   rilascio sempre completo su timeout/eccezione.
4. Integrare il selettore fair approvato senza affidarsi all'equità accidentale
   di `Semaphore`.
5. Riservare capacità interattiva e dimostrare che le attività in sottofondo
   non restano in attesa indefinita.
6. Estendere le metriche solo con etichette limitate e prive di dati sensibili.
7. Propagare al device soltanto budget assegnati, mai owner/path nel nome di
   variabile non previsto dal wire.
8. Mantenere seriali executor privi di policy o equivalence gate.

- **Test obbligatori:** suite dello scheduler esistente invariata; ordine di
  acquisizione; scadenza a ogni slot; nessuna perdita di slot; equità fra i
  proprietari A e B; due lavori dello stesso proprietario; priorità limitata;
  chat sotto saturazione; attività in sottofondo non lasciate in attesa
  indefinita; metadati incompleti trattati come seriali; budget ricevuto dal
  trasporto remoto.
- **Vietato:** secondo pool; casi speciali codificati per nome di executor o
  fornitore; priorità illimitata; log di proprietario o unità; modifica dei
  valori predefiniti di produzione nel medesimo commit.
- **Gate di uscita:** test preesistenti compatibili per byte e struttura, e
  misure F0 entro le soglie ratificate.

### F7 — Ponte di esecuzione locale, remoto e LLM

- **Assegnazione:** agente integratore dopo la revisione di F3, F5 e F6.
- **Dipendenze:** F3, F5, F6; F4 per i risultati conservati come blob.
- **Scopo:** eseguire un'unità reale senza ancora esporre API pubbliche.

- **File ammessi:** `execution.py`, `worker.py`, estensioni compatibili di
  `invocations.py`, `remote_exec.py`, `llm_telemetry.py`, `prompt_loader.py` e
  test. Le modifiche ai moduli centrali restano minime e, se possibile,
  separate per commit.

**Istruzioni:**

1. Caricare executor dal catalogo verificato e confrontare il digest congelato
   prima di ogni attempt.
2. Costruire `ExecutionContext` da righe store, non dagli argomenti del piano.
3. Invocare esclusivamente il choke point universale con owner, device,
   timeout e autonomia ammessi.
4. Convalidare output prima dello staging/commit; mappare errori con tassonomia
   chiusa. Unknown non è transitorio.
5. Estendere la coda remota con dispatch idempotente e contesto firmato; stesso
   ID con payload diverso fallisce.
6. Aggiungere contextvars LLM per lavoro/fase/unità/attempt e un sink durevole
   di dimensione limitata; la telemetria non deve poter far fallire la chiamata,
   ma il registro
   richiesto dal piano deve segnalare `usage_missing`.
7. Esporre dal prompt loader identità/digest effettivi senza cambiare il testo
   renderizzato; testare fallback lingua.
8. Per VLM registrare binding, prompt, policy e uso secondo la decisione F0;
   nessun nome modello letto direttamente dall'executor entra come verità.
9. Per effetti `manual_only`, un timeout produce attenzione e conserva ogni
   evidenza; non chiama di nuovo l'executor.

- **Test obbligatori:** executor fittizio, puro e locale; output malformato;
  digest dell'executor cambiato; binding mancante; prompt cambiato; uso LLM
  associato al tentativo; arresto e ripetizione dell'accodamento remoto;
  risultato tardivo con fence scaduto; payload discordante; effetto ambiguo non
  ritentato; proprietario e dispositivo propagati.
- **Vietato:** importare e chiamare `executor.code_path`; modificare gli
  argomenti per inserire metadati non dichiarati; ripiego su un altro modello;
  dedurre l'idempotenza da `ok`, `revertible` o dal nome.
- **Gate di uscita:** una pipeline interna fittizia di mappatura e riduzione
  sopravvive a un riavvio locale e remoto simulato, con un solo commit per unità
  e provenienza completa.

### F8 — Servizio supervisionato, ripresa e stato di salute

- **Assegnazione:** agente esecutivo per l'infrastruttura, dopo la ratifica di
  V2.
- **Dipendenze:** F7.
- **Scopo:** rendere il worker un componente gestito del ciclo di vita Metnos.

- **File ammessi:** punto d'ingresso `service.py`, modello systemd approvato,
  installazione e ciclo di vita dello stack, `services_registry.py`, stato di
  salute e test infrastrutturali. Nessuna route di controllo utente.

**Istruzioni:**

1. Integrare l'unità soltanto nell'inventario chiuso previsto da V2 e in
   `metnos.target`; nessuna individuazione di unità arbitrarie.
2. Eseguire migrazione e ripresa prima di dichiarare il servizio pronto,
   procedendo per lotti di dimensione limitata.
3. Configurare arresto cooperativo, scadenza e terminazione del gruppo in modo
   coerente con i test dei tentativi.
4. Evitare un secondo worker attivo per errore; il coordinamento reale resta
   nel DB/fencing, non soltanto in systemd.
5. Esporre uno stato di salute applicativo con schema chiuso e senza dati
   sensibili.
6. Se il worker non è disponibile, HTTP e chat restano attivi e dichiarano il
   degrado; non eseguono unità nel processo HTTP come ripiego.
7. L'interruttore di funzionalità disattivato impedisce nuovi invii, ma
   mantiene lettura e cancellazione.
8. Aggiungere log strutturati di avvio, ripresa e arresto, limitati per lotto.

- **Test obbligatori:** verifica dell'unità con `systemd-analyze`; appartenenza
  al target; doppio avvio; SIGTERM durante attesa, tentativo e commit; ciclo di
  riavvio limitato; schema incompatibile; DB bloccato; ripresa con migliaia di
  righe; HTTP disponibile con worker fermo.
- **Vietato:** `systemd-run` per ogni unità del corpus; worker avviato da una
  route; ripiego nel pool HTTP; unità non registrata nel catalogo dei servizi.
- **Gate di uscita:** arresto e riavvio reali riprendono il percorso verticale
  F7; lo stato di disponibilità distingue `ready`, `recovering` e `degraded`.

### F9 — Contratti di controllo e API circoscritte al proprietario

- **Assegnazione:** agente esecutivo per le API, dopo la decisione V1.
- **Dipendenze:** F7 e facciata/DTO congelati; integrazione con F8 al gate I2.
- **Scopo:** esporre lo stesso servizio a chat e UI senza duplicare la logica.

- **File ammessi:** DTO e facciata del servizio, nuovo modulo per le route,
  registrazione delle route, contratti degli executor approvati e test HTTP.
  Nessuna modifica a CSS o Telegram.

**Istruzioni:**

1. Congelare prima l'API Python interna; route ed executor sono adattatori
   sottili.
2. Ricavare il proprietario dal contesto autenticato e ignorare quello
   eventualmente presente nel corpo della richiesta.
3. Applicare la paginazione con un cursore opaco e limiti massimi a elenchi,
   eventi e unità.
4. Applicare alle mutazioni una chiave di idempotenza, `expected_version` e
   codici di conflitto
   strutturati.
5. Rendere non ambigui i comandi illegali: la pausa su uno stato terminale è
   un'operazione nulla dichiarata oppure un conflitto, secondo il contratto;
   non produce mai una transizione implicita.
6. Se esistono executor di controllo, generarli/ammetterli con standard,
   capacità e firma normali; nessuna scorciatoia incorporata ma priva di
   contratto.
7. Localizzare i messaggi utente; API conserva codici stabili indipendenti
   dalla lingua.
8. Registrare nell'audit ogni controllo senza memorizzare richiesta, percorso o
   contenuto in chiaro oltre quanto già autorizzato.

- **Test obbligatori:** autenticazione; IDOR su ogni route; proprietario nel
  corpo ignorato o rifiutato; cursore invalido; limite massimo; doppio comando;
  versione obsoleta; cancellazione utente concorrente; stati illegali; errori
  localizzati in italiano e inglese; executor e HTTP che producono lo stesso
  DTO.
- **Vietato:** SQL nel modulo delle route; route generica `action=<string>`;
  nomi fuori vocabolario; restituzione del piano grezzo con campi interni o
  istantanee sensibili.
- **Gate di uscita:** matrice operazione-per-stato completamente testata e
  nessuna query priva del vincolo sul proprietario, secondo il controllo
  statico dedicato.

### F10 — UI, SSE persistente e Telegram

- **Assegnazione:** due pacchetti separabili, UI e outbox/canali; integrazione
  finale unica.
- **Dipendenze:** F9 per UI/SSE; F7 per il filone outbox, poi gate I2.
- **Scopo:** rendere il lavoro osservabile e controllabile senza bloccare la
  chat.

- **File ammessi:** modelli, CSS e componenti comuni, route per SSE e
  scaricamento, `events.py` e outbox, adattatore Telegram e test. Non modificare
  la pianificazione.

**Istruzioni UI/SSE:**

1. Progettare elenco e dettaglio paginati secondo §12.3, riusando layout e
   variabili grafiche comuni.
2. Non caricare unità complete nella pagina iniziale; errori e sorgenti sono
   pannelli paginati.
3. Trasmettere gli eventi dal DB usando `Last-Event-ID`; non persistere i
   segnali periodici di attività.
4. Riconnettere dopo l'aggiornamento della pagina e rendere lo stato dal modello
   di lettura autorevole.
5. Mostrare pulsanti con la versione corrente, uno stato disabilitato coerente
   e una conferma per le azioni distruttive.
6. Consentire lo scaricamento tramite il registro degli artefatti, mai tramite
   un percorso ricevuto dal browser.

**Istruzioni Telegram/outbox:**

1. Inserire le notifiche nella transazione di stato.
2. Separare reclamo, lease e conferma; un errore di invio mantiene la riga.
3. Coalescere il progresso e imporre frequenza massima configurabile.
4. Risolvere l'associazione Telegram al momento dell'invio e verificare il
   proprietario.
5. Non produrre allegati o collegamenti pubblici; applicare il contratto di
   scaricamento.

- **Test obbligatori:** aggiornamento e riconnessione da un ID evento
  intermedio; evento precedente alla memoria del processo; uscita e nuovo
  accesso; altro proprietario; flusso lento; 10.000 eventi; scaricamento
  revocato o scaduto; errore Telegram prima e dopo l'invio; riavvio del daemon;
  deduplicazione; accorpamento; associazione revocata; nessuna raffica di
  notifiche su 1.000 unità.
- **Vietato:** interrogazione ogni secondo per riga; CSS in linea; percentuale
  senza denominatore; rimozione dalla coda prima dell'invio; URL basato su
  TurnLog o foto; un messaggio per unità.
- **Gate di uscita:** un lavoro continua con il browser chiuso, riappare dopo
  l'accesso da un altro PC e produce una sola notifica terminale, consegnabile
  anche dopo un riavvio.

### F11 — Preset immagini e adeguamento controllato degli executor

- **Assegnazione:** agente del dominio immagini affiancato da un revisore dei
  contratti degli executor.
- **Dipendenze:** F7 per la configurazione senza interfaccia; F8-F10 soltanto
  per la prova E2E e la consegna al gate I2.
- **Scopo:** implementare il caso delle 98 immagini sul motore, senza logica
  numerica speciale.

- **File ammessi:** configurazione e piano di prova del dominio, JSON Schema di
  estrazione e soluzione, test E2E; modifiche a `read_files_ocr`, VLM o
  `write_files` soltanto in commit distinti, dopo audit e nuova firma
  nominativa.

**Pipeline normativa:**

```text
inventory
 -> map OCR/VLM per sorgente
 -> map split/normalizzazione delle domande
 -> dedup delle occorrenze
 -> map soluzione per domanda canonica
 -> map validazione
 -> reduce gerarchico note
 -> reduce gerarchico formulario
 -> assemble soluzioni per ordine canonico
 -> validate copertura/struttura
 -> commit dei tre artefatti Markdown
```

**Identità:**

- `question_occurrence_id = H(source_id, coordinate_locale,
  normalized_text_hash)` soddisfa il legame alla sorgente;
- `canonical_question_key = H(normalized_text_hash, semantic_schema_version)`
  controlla il riuso della soluzione;
- una tabella many-to-one conserva tutte le occorrenze duplicate;
- dedup semantica LLM, se prevista, è una fase esplicita con confidenza e non
  fonde elementi sotto soglia; l'identità esatta resta deterministica.

**Istruzioni:**

1. Generare fixture sintetiche con 98 immagini: zero/una/più domande,
   rotazioni, bassa leggibilità, duplicati e un file corrotto.
2. Non inserire `98`, nomi degli artefatti o path nel nucleo generico; sono
   dati del preset/piano.
3. Chiamare OCR per unità o per lotti di dimensione misurata; nessun ciclo
   parallelo privato.
4. Prima di abilitare parallelismo di `read_files_ocr`, aggiungere policy
   firmata e prove di equivalenza e contesa delle risorse. In assenza resta
   seriale: il comportamento rimane corretto, soltanto più lento.
5. Portare il VLM nel registro approvato in F0/F7, con binding e digest del
   prompt.
6. Usare workload registrati per estrazione, soluzione e riduzione; non passare
   `precise` al router e non fissare `creative` se il registro assegna `wise`.
7. Rendere `solutions.md` capace di elencare occorrenze non risolte e motivi.
8. Validare Markdown, sezioni obbligatorie, ordine, link alle sorgenti e
   assenza di elementi non contabilizzati.
9. Baseline: artefatti nel deposito Metnos. Pubblicazione su path esterno solo
   dopo contratto atomico approvato; non usare `write_files overwrite` come se
   fosse già riconciliabile.

- **Test obbligatori:** 98 fonti conteggiate; duplicati con una soluzione e più
  provenienze; immagini senza domande; immagine illeggibile; domanda multipla;
  completamento fuori ordine; riavvio al 30% e al 60%; modifica di un'immagine
  che invalida solo i discendenti; tre artefatti con digest stabile a parità di
  binding; binding cambiato che richiede una revisione; nessun prompt oltre il
  budget.
- **Vietato:** flusso Python con casi codificati fuori dal piano; numero 98 nel
  runtime; risposta mancante eliminata dal denominatore; LLM che decide
  `completed`; nuova firma di massa.
- **Gate di uscita:** il corpus produce i tre artefatti, ogni identificativo di
  sorgente compare nel registro di copertura e una seconda esecuzione senza
  cambiamenti riusa i nodi ammessi senza generare nuove soluzioni.

### F12 — Iniezione dei guasti, corpus 10x e certificazione

- **Assegnazione:** agente di test avversariale distinto dagli implementatori.
- **Dipendenze:** gate I2 con F8-F11 integrati.
- **Scopo:** tentare di falsificare le promesse prima della distribuzione.

- **File ammessi:** infrastruttura e dati di prova sotto `tests/`, strumenti di
  test interni e rapporto riservato. Il runtime si modifica solo con correzioni
  separate e riesecuzione integrale.

**Istruzioni:**

1. Generare almeno 980 sorgenti senza duplicare in memoria l'intero corpus.
2. Inserire punti di guasto nominati prima e dopo ogni transazione e operazione
   filesystem di §8.
3. Terminare processi reali con SIGKILL nei punti controllati; riavviare un
   processo pulito.
4. Eseguire worker concorrenti, due proprietari, chat interattiva e dispositivo
   remoto fittizio sotto saturazione.
5. Simulare fornitore indisponibile, limite di richieste, binding rimosso,
   credenziale revocata, salto dell'orologio di sistema, disco pieno, DB
   occupato e file modificato.
6. Verificare database e filesystem con un controllo indipendente: vincoli,
   orfani, contatori, digest, fence e proprietari.
7. Ripetere i test non deterministici per un numero fissato di volte; conservare
   il seme e
   distribuzione delle latenze.
8. Eseguire la suite Metnos completa e almeno un turno reale HTTP e Telegram
   con l'interruttore di funzionalità attivo e disattivato.

- **Prove obbligatorie:** tutte le 20 prove del mandato, più IDOR, cancellazione
  durante un tentativo, errore dell'outbox, aggiornamento dello schema e
  ripresa, raccolta concorrente e assenza di attesa indefinita per le richieste
  interattive.
- **Vietato:** simulazione del processo nei test di riavvio; indebolire le
  asserzioni o ridurre il corpus per rispettare il tempo; dichiarare riuscito un
  test instabile soltanto perché ha superato un nuovo tentativo.
- **Gate di uscita:** nessuna duplicazione osservabile, nessuna sorgente non
  contabilizzata, nessun commit obsoleto accettato e rapporto con comandi,
  semi, tempi e limiti reali.

### F13 — Migrazione, documentazione, progetto pilota e distribuzione

- **Assegnazione:** integratore responsabile della consegna.
- **Dipendenze:** F12.
- **Scopo:** introdurre la funzione senza trasformare la roadmap in
  documentazione di un comportamento non ancora distribuito.

- **File ammessi:** interruttore di funzionalità e configurazione, migrazione
  selettiva dei percorsi specialistici approvati, documentazione pubblica,
  fonti Tutor, note di rilascio e rapporto del progetto pilota.

**Istruzioni:**

1. Nel primo rilascio, impedire per impostazione predefinita i nuovi invii;
   lettura e stato di salute devono funzionare.
2. Condurre il progetto pilota con un proprietario e un corpus non sensibile;
   osservare almeno un riavvio naturale o controllato.
3. Non migrare automaticamente build immagini esistenti finché il preset non
   dimostra parità funzionale e operativa. La migrazione è una decisione
   distinta con rollback.
4. Pubblicare soltanto architettura realmente attiva, uso, limiti, ripresa,
   conservazione e modello di consistenza. ADR, analisi e questa RM restano fuori
   dal sito e dal corpus Tutor.
5. Aggiornare Tutor soltanto dopo che route/UI pubblicate sono fonti vere e
   superano i gate di freschezza.
6. Aggiungere procedure operative per worker indisponibile, lavoro bloccato,
   lease scadute, disco pieno, binding indisponibile, ripristino e cancellazione.
7. Misurare uso reale, durata, costo, errori e interventi umani per il periodo
   deciso in F0.
8. Passare RM a `implemented` soltanto con link a prove, ADR, release e
   documentazione distribuita; non cancellarla.

- **Test obbligatori:** installazione nuova; aggiornamento; interruttore
  disattivato, attivato e nuovamente disattivato; rollback senza perdita della
  lettura; disponibilità del target; documentazione e collegamenti; filtro
  delle informazioni personali; Tutor che non anticipa capacità; prova E2E del
  progetto pilota.
- **Vietato:** pubblicazione della RM; assorbimento di script specialistici
  senza parità; cancellazione dell'archivio perché l'interruttore è disattivato;
  promessa di *exactly once*.
- **Gate di uscita:** tutti i criteri di §18 provati, progetto pilota accettato e
  nessun TODO indispensabile a sicurezza, consistenza, ripresa o cancellazione.

## 17. Protocollo operativo per agenti esecutivi

### 17.1 Preparazione obbligatoria

Prima di toccare file, l'agente:

1. legge integralmente `CLAUDE.md`, `CLAUDE.mutabile.md`, `AGENTS.md` applicabile,
   ADR del pacchetto e le sezioni RM citate;
2. esegue `git status --short` e conserva ogni modifica estranea;
3. legge implementazione e test dei simboli da modificare, non soltanto la RM;
4. scrive nel proprio piano il pacchetto, i file ammessi, i test e i divieti;
5. verifica che tutte le dipendenze abbiano un rapporto di consegna approvato;
6. se serve un nome, uno stato o un campo non congelato, segnala il blocco: non
   lo
   inventa.

### 17.2 Regole di implementazione

- Un pacchetto per volta, una responsabilità per modulo.
- Nessuna modifica a vocabolario, capacità, tier, modelli, autorità o
  topologia fuori da una decisione esplicita.
- Nessun accesso SQL fuori dal repository di persistenza.
- Nessun sottoprocesso, pool di thread o chiamata a un executor fuori dal ponte
  centrale.
- Nessun `except Exception: pass` su stato, commit, artefatto o outbox.
- Nessun nuovo tentativo deciso dal testo dell'errore; usare codici e tassonomia.
- Nessun valore predefinito permissivo per proprietario, effetto, schema, firma
  o binding.
- Nessuna firma di massa: si firma il solo contratto nominato dopo test e review.
- Nessun test rimosso, indebolito o marcato come instabile senza decisione del
  revisore.
- Nuove stringhe utente in i18n IT/EN secondo il processo corrente.
- Commenti spiegano l'invariante o il perché, non traducono riga per riga il
  codice.

### 17.3 Protocollo di segnalazione dei blocchi

L'agente si ferma soltanto sul sottopunto ambiguo e consegna:

```text
BLOCKER
Pacchetto:
Decisione mancante o contraddizione:
Evidenza nel codice/ADR/test:
Impatto se si sceglie A:
Impatto se si sceglie B:
Lavoro indipendente già completato:
File lasciati invariati:
```

Non usa la segnalazione di blocco per evitare test difficili. Se può proseguire su parti
indipendenti senza fissare la decisione, lo fa.

### 17.4 Verifica prima del commit

1. test unitari del pacchetto;
2. test di integrazione indicati;
3. test dei moduli centrali toccati;
4. controllo statico della circoscrizione al proprietario, delle importazioni
   proibite e dei pool privati;
5. `git diff --check`;
6. lettura completa delle differenze, incluse migrazioni, gestione degli errori
   e localizzazione;
7. prova con guasto almeno sul punto transazionale modificato;
8. nessun file generato, segreto, DB o artefatto di test nelle differenze;
9. commit in inglese, descrittivo e senza modifiche estranee.

La suite completa e i turni reali appartengono ai gate di integrazione; un
agente non li sostituisce con «i test locali passano».

### 17.5 Rapporto di consegna obbligatorio

```text
Package: F<n>
Objective completed:
Decisions/fixtures consumed:
Files changed:
Public/internal contracts changed:
Invariants enforced:
Migrations and rollback:
Tests run with exact results:
Failure injections run:
Security/owner-scope evidence:
Known limitations:
Blockers or follow-up (non-essential only):
Commit:
```

Un rapporto privo dei comandi e dei risultati dei test non apre il pacchetto
dipendente.

## 18. Criteri misurabili di completamento

RM-0004 passa a `implemented` soltanto se tutte le righe hanno evidenza
referenziata:

| Criterio | Evidenza minima |
|---|---|
| piano da richiesta naturale | turno reale, piano ammesso e nessun executor inventato |
| isolamento | test IDOR su API, archivio, artefatti e outbox, più cancellazione concorrente |
| ripresa | SIGKILL reale al 30%, al 60% e durante la pubblicazione, seguito da riavvio pulito |
| fencing | due worker, commit del solo fence vincente |
| effectively once | replay identico deduplicato e effetto ambiguo non ritentato |
| completezza | ogni sorgente nel denominatore e suite contro i falsi completamenti |
| invalidazione | una singola modifica ricalcola soltanto i discendenti |
| ordine | output identico con ordine di completamento permutato |
| riduzione | corpus oltre prompt, almeno due livelli checkpointati |
| scheduler | nessun pool privato, uso equo e chat reattiva sotto carico |
| modelli | workload/binding/prompt congelati, revisione su incompatibilità |
| artefatti | tre file Markdown validi, digest e scaricamento dopo un nuovo accesso o da un altro PC |
| Telegram | outbox resistente ai riavvii, accorpamento e nessuna raffica di notifiche |
| scala | almeno 980 sorgenti, riavvio e nessuna duplicazione |
| operazioni | stato di salute, conservazione, raccolta, disco pieno e ripresa dello schema |
| regressione | suite completa, comportamento invariato con funzione disattivata, turni HTTP e Telegram |
| documentazione | guide pubbliche solo dopo il rilascio, procedure operative e limiti dichiarati con precisione |

Inoltre:

- nessun TODO residuo può riguardare sicurezza, fencing, ripresa, cancellazione,
  completezza o pubblicazione;
- un limite prestazionale può restare soltanto se misurato, documentato e non
  viola i budget promessi;
- il caso immagini è un preset: il pacchetto centrale non importa moduli del
  dominio e non contiene `98`;
- l'implementazione distribuita conserva la normale architettura Metnos di
  piani, executor, policy, firma, sandbox e tier.

## 19. Valutazione di fattibilità, utilità, costo e tempi

Questa sezione è una stima tecnica, non un impegno di calendario. Va aggiornata
dopo le misure F0 e il primo percorso verticale completo.

### 19.1 Fattibilità

**Valutazione: alta, circa 8/10, con perimetro progressivo.** Le primitive più
difficili da integrare — identità circoscritta al proprietario, punto di
passaggio obbligato, scheduler centrale, firma, sandbox, trasporto remoto e
systemd — esistono già. SQLite, lease con fencing, blob indirizzati dal
contenuto e outbox sono tecniche note e coerenti con l'architettura.

La fattibilità scende nettamente se il requisito viene interpretato come
*exactly once con qualunque fornitore* o *DAG arbitrario generato dall'utente*:
quelle promesse non sono realizzabili in modo generale. Il disegno qui proposto
evita entrambe.

### 19.2 Utilità

**Valutazione: alta per la direzione di Metnos, condizionata all'uso reale.** Il
motore risolve un limite strutturale: oggi il turno e le attività specialistiche
non possono offrire continuità e completezza su corpus grandi. Il valore cresce
per OCR, PDF, classificazioni, trasformazioni e ricerche lunghe, soprattutto
quando audit e ripresa contano.

Sarebbe invece sovradimensionato per un unico lotto occasionale di immagini:
uno script specialistico costerebbe meno. L'investimento è giustificato se,
nel progetto pilota, emergono almeno due o tre famiglie ricorrenti di carichi
con durata superiore a un turno, cardinalità significativa o necessità di
tracciabilità. Il nucleo deve essere generico; la prima versione deve avere una
sola configurazione ben provata, non anticipare una piattaforma universale.

### 19.3 Costo di sviluppo

Stima in persone-giorno effettive, includendo revisione, iniezione dei guasti e
integrazione:

| Perimetro | Contenuto | Stima |
|---|---|---:|
| nucleo dimostrabile | F0-F4, executor fittizi, soli effetti puri, artefatti interni | 30-45 giorni |
| v1 utile | F0-F11, configurazione immagini, servizio, API/UI essenziale e outbox | 65-95 giorni |
| mandato completo certificato | F0-F13, remoto, corpus 10x, consolidamento, distribuzione e documentazione | 90-140 giorni |

Con una sola persona esperta il mandato completo richiede ragionevolmente da
quattro a sette mesi. Con due o tre flussi esecutivi e un'integrazione affidata
a una figura esperta, alcune fasi possono sovrapporsi, ma schema, scheduler,
ponte di esecuzione e certificazione restano colli di bottiglia: **circa 12-18
settimane di calendario** è una forchetta prudente. Agenti di livello inferiore
possono ridurre il tempo meccanico, non la revisione delle transazioni e delle
prove di arresto anomalo.

Il costo monetario si calcola moltiplicando i 90-140 giorni per il costo
giornaliero effettivo; una cifra assoluta senza quel dato sarebbe artificiale.

### 19.4 Costo operativo

- SQLite e i metadati hanno un costo modesto; blob intermedi e conservazione
  possono diventare la voce di archiviazione dominante.
- OCR locale costa soprattutto CPU; VLM e riduzioni generative consumano GPU,
  tempo e memoria.
- Fornitori esterni o tier `frontier` devono richiedere un consenso esplicito e
  restare entro un budget approvato in anticipo; il motore non rende il costo
  intrinsecamente prevedibile.
- Hash completi e validazioni duplicano una parte dell'I/O, scelta necessaria
  per la garanzia di ripresa.
- Stato di salute, migrazioni, raccolta e gestione degli incidenti introducono
  manutenzione permanente: non è una funzione «costruita una volta e
  dimenticata».

F0 deve misurare il costo per sorgente e F11 deve mostrarlo nella UI prima di
estendere il progetto pilota.

### 19.5 Pericoli principali

1. **Falsa sicurezza sull'esecuzione esattamente una volta.** È il rischio più
   grave: potrebbe duplicare invii, scritture o oggetti esterni.
2. **Falso completamento.** Un artefatto elegante ma incompleto distrugge la
   fiducia più di un errore esplicito.
3. **Espansione dell'autorità.** Un worker non presidiato amplifica qualunque
   elusione degli executor o dei vincoli sul proprietario e sulle capacità.
4. **Attesa indefinita e degrado della chat.** OCR e VLM possono occupare tutte
   le risorse se equità e riserve non sono gestite centralmente.
5. **Complessità operativa.** Lease, blob, DB, dispositivi, outbox e modelli
   creano più stati intermedi e più procedure di ripresa da mantenere.
6. **Riproducibilità apparente.** Congelare il nome del modello non basta se
   prompt, politica o servizio sottostante cambiano.
7. **Cancellazione e privacy.** Punti di ripresa e artefatti moltiplicano le
   copie di dati personali da eliminare.
8. **Deriva verso una piattaforma di flussi arbitrari.** Espressioni libere e
   casi speciali renderebbero il nucleo ingestibile.
9. **SQLite usato oltre il suo profilo.** È adeguato a un'istanza Metnos
   singola con concorrenza limitata; non va trasformato senza misure in un
   coordinatore distribuito ad alta scala.

### 19.6 Raccomandazione

Procedere, ma con tre punti di controllo dell'investimento:

1. **Dopo F0:** fermarsi se nomi, consistenza e autorità non sono risolti senza
   eccezioni ad hoc.
2. **Dopo F4:** valutare il nucleo su executor puri e arresti anomali reali. Se
   fencing e ripresa degli artefatti non sono solidi, non costruire UI o
   configurazioni di dominio.
3. **Dopo F11:** misurare il caso delle 98 immagini e almeno un secondo dominio.
   Solo uso, tempi e affidabilità reali giustificano F12-F13 e gli effetti
   remoti/mutanti.

La scelta con il miglior rapporto valore/rischio è una v1 con fonti in sola
lettura, trasformazioni pure, artefatti nel deposito Metnos e pubblicazione
esterna esplicita. Gli effetti esterni riconciliabili possono arrivare dopo;
non devono essere il requisito che ritarda o indebolisce il nucleo.

---

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
