# Referto del punto 1 — censimento per il nuovo prototipo universale d'intento

Data: **12 agosto 2026**.

Stato: **proposta a Roberto, non accordo fra due agenti**. Questo referto
esegue soltanto il punto 1 dell'«Ordine di lavoro autorizzato» in
`checkpoint_qualita_intento_12_8_2026.md`. Non definisce ancora il contratto
ombra del punto 2.

## Esito

Il censimento è completato. La proposta della radice discriminata regge come
direzione, ma **non può essere innestata correttamente dentro V23lite** e non
può essere proiettata senza perdita nell'`Intent` corrente di produzione.

Il prototipo richiesto da Roberto deve quindi essere un **oggetto autonomo di
laboratorio**, con:

- un proprio tipo d'uscita discriminato;
- validazione e proiezione proprie;
- registri tecnici iniettati e congelabili;
- un adattatore separato usato soltanto per confronti e oracolo;
- nessun collegamento al runtime di produzione in questa fase.

Questa conclusione segue anche dall'indicazione successiva di Roberto: non
costruire una toppa, ma un prototipo nuovo, universale, molto curato e capace
di migliorare anche le prestazioni senza sacrificare la correttezza.

Sono emerse però quattro scelte di progetto non coperte dalle decisioni già
date. Sono elencate nel §9; per regola di Roberto non vengono decise qui.

Nessuna misura GPU è stata eseguita. Produzione e banco congelato sono rimasti
in sola lettura.

La verifica finale salvata si chiude con codice 0: 120 richieste, 10
sentinelle, 30 coppie indice/braccio, 6 impronte sigillate verificate e nessuna
divergenza nei controlli freschi. Un controllo SHA-256 indipendente conferma
inoltre inalterati il file del banco, i due output c10/c11, i due output con
riparo e la mappatura sigillata.

## 1. Metodo e perimetro

Sono stati letti e incrociati:

- il banco `unified_query_bench_v23_checkpoint.py`;
- le toppe e i lettori in `misure_11_8/`;
- i proiettori congelati V24/V24.1;
- il tipo `runtime.engine.types.Intent` e i suoi consumatori diretti;
- `vocab.SYSTEM_VERBS`, `SYSTEM_EXECUTOR_NAMES`, il registro dei builtin a
  verbo unico, gli helper universali e il catalogo firmato;
- manifest, implementazione, pausa e ripresa di `get_approval`;
- i quattro casi sentinella 25, 59, 66 e 77 già raccolti, senza nuova
  inferenza.

Il censimento dei consumatori è statico. Copre import, chiamate e accessi ai
campi trovabili nel repository; non rivendica di essere una traccia dinamica
completa di ogni caricamento riflessivo.

All'inizio del lavoro l'impronta osservata del solo file del banco era:

```text
b39f19ce3418ff0e6f2908462ddd5a47c5e1bd410211ce152d6ce2619b3c82cb
```

È un controllo di non modifica di questa sessione, non una nuova impronta
autorevole del banco complessivo.

## 2. Censimento di V23lite

### 2.1 Il banco non è un artefatto autosufficiente

Il file è congelato, ma all'importazione legge fonti vive:

- `runtime/vocab.py::{ACTIONS,OBJECTS,QUALIFIERS}`;
- `tests/benchmarks/catalog_snapshot.json`;
- `tool_grammar._UNIVERSAL_HELPERS`;
- router e workload correnti per la chiamata al modello.

Di conseguenza, l'immutabilità del solo `.py` non congela schema, registri o
comportamento. Il passo 3 dell'ordine autorizzato dovrà fissare anche queste
dipendenze e le loro impronte.

### 2.2 Schema model-facing

`schema("v23lite")` accetta una sola radice:

```text
{semantic_heads: [...], predicates: [...]}
```

Nel checkout osservato lo schema JSON canonico compatto misura **5.804 byte**.
Per ogni clausola richiede:

- 13 campi in `semantic_heads`;
- 8 campi in `predicates`;
- 21 campi complessivi, prima di contare i sotto-oggetti tagged.

Quattro valori sono specchi fra le due colonne:

```text
predicate_id
predicate_anchor_token_id
role
source_predicate_id <-> input_from_predicate_id
```

La variante c11 li rimuove dalla seconda colonna e li ricostruisce prima della
validazione. È una prova utile sul costo dell'uscita, non un nuovo contratto.

Lo schema non può esprimere:

- un controllo di sistema fuori da `ACTIONS`;
- un esito riconosciuto ma non rappresentabile;
- una dipendenza di controllo;
- il dominio protetto da una condizione;
- un'alternativa o un'astensione distinta dall'invalidità tecnica.

### 2.3 Validatore e normalizzazioni

`validate_frame_v23(..., prompt_variant="v23lite")` impone:

- forma esatta e pari cardinalità delle due colonne;
- identificatori uguali alla posizione e ancore crescenti;
- allineamento del ruolo e dei quattro specchi;
- enum, span e tagged role coerenti;
- arco dati soltanto all'indietro;
- `request` sempre eseguibile;
- ogni altro ruolo sempre non eseguibile;
- assenza di una persistenza duplicata.

Non esiste un arco di controllo. Un record `condition` può essere validissimo,
ma non governa alcun bersaglio.

Le misure del 12 agosto non validano il frame grezzo senza mediazione:

- `riparo_ruolo.py` muta in place `verb` e `object` dei record non-request;
- `uscita_specchi.py` reinserisce i campi derivati nel frame prima di chiamare
  il validatore originale.

Il frame archiviato è quindi la forma normalizzata, non sempre il documento
esattamente emesso dal modello. Il nuovo prototipo dovrà conservare distinti
almeno documento emesso, documento normalizzato ed esito del validatore.

### 2.4 Esistono tre proiezioni diverse chiamate di fatto “rotta”

1. Il `main()` del banco usa `folded_signature()` e quindi
   `folded_signature_v22()`. Questo proiettore può cambiare la coppia emessa in
   base a ruoli tipizzati, catalogo, scope e confronto lessicale fra gloss
   inglese e nomi tecnici.
2. `prova_specchi*.py` e `confronto_intento.py` leggono direttamente
   `predicates[].verb/object`, senza passare da `folded_signature_v22()`.
3. V24/V24.1 migrano il frame in un secondo grafo, lo validano e applicano un
   proiettore catalog-driven distinto.

I tre percorsi non hanno lo stesso contratto. In particolare, il primo usa
anche `_lexically_related()` su etichette inglesi: non va ereditato dal nuovo
prototipo, perché il vincolo corrente vieta instradamento da lessico.

### 2.5 Consumatori

Consumatori attivi nel laboratorio corrente:

- `unified_query_bench_v23_checkpoint.py::main`;
- `request_analysis_adversarial_v1.py`;
- `misure_11_8/confronto_intento.py`;
- `prova_cieca.py`, `prova_specchi.py` e `prova_specchi_riparo.py`;
- le toppe `riparo_ruolo.py`, `riparo_ancore.py` e `uscita_specchi.py`;
- gli altri lanciatori del ciclo 11 agosto, per dipendenza indiretta da
  `prova_cieca.arm()` o dal modulo del banco;
- il verificatore della colonna semantica, attraverso gli output congelati.

Consumatori congelati e non runtime:

- `candidates/v24_v241/metnos_v24_offline.py::migrate_v23/project_v24`;
- `candidates/v24_v241/metnos_v241_offline.py::migrate_v23/project_v241`;
- i runner storici V25/V25.1 che importano quei proiettori.

Nessun file V23lite è collegato alla produzione. Il runtime corrente riceve
invece un `Intent` piatto:

```text
verb, object, keywords, confidence, lang, actions[{verb, object}]
```

Una ricerca statica trova **17 file runtime** che leggono direttamente almeno
uno di `intent.verb`, `intent.object`, `intent.confidence` o `intent.actions`.
Il tipo non dispone di esito discriminato, ruoli, archi dati, archi di
controllo o astensione. Non è quindi una destinazione senza perdita per il
nuovo oggetto.

## 3. Reperti sulle sentinelle già misurate

Senza nuova chiamata al modello:

- indice 25, «undo ultima azione»: V23lite produce `change/files`;
- indice 66, «Annulla l ultima operazione.»: produce `delete/entries`;
- indice 59, richiesta esplicita di `get_approval`: la condizione
  `on_approve` resta un nodo non eseguibile ma senza bersaglio, mentre le rotte
  richieste diventano `get/entries` e `set/entries`;
- indice 77, ricerca + approvazione + pubblicazione + aggiornamento: il frame
  contiene quattro richieste indipendenti e nessuna relazione che protegga le
  ultime due.

I primi due casi provano la necessità di `system_control`; gli ultimi due
provano che il solo ruolo grammaticale non rappresenta il controllo.

## 4. Censimento dei controlli runtime

Non esiste oggi una fonte unica pronta a generare `system_control`.

| Fonte | Insieme osservato | Che cosa governa | Perché non è il registro richiesto |
|---|---|---|---|
| `vocab.SYSTEM_VERBS` | `admin`, `undo`, `synthesize`, `consult` | nomi riservati | include concetti senza executor e non prova esposizione o contratto |
| `vocab.SYSTEM_EXECUTOR_NAMES` | `undo_last_turn`, `consult_frontier` | eccezioni alla grammatica dei nomi | `consult_frontier` è un'operazione agente; non descrive la semantica di controllo |
| `loader.VERB_UNIQUE_REGISTRY` | `admin`, `sudoer` al boot | invocazione privilegiata e chiamanti ammessi | `sudoer` è nascosto; `undo_last_turn` non vi appartiene |
| `tool_grammar._UNIVERSAL_HELPERS` | sei trasformatori più `undo_last_turn` | disponibilità nel pool | mescola controllo e normali operazioni dati |
| catalogo firmato | executor realmente ammessi | identità, schema, autorità | non ha un campo che classifichi la radice d'intento |
| lessico di rilevazione | bypass corrente dell'undo | routing dalla query | è proprio il meccanismo lessicale che il prototipo non deve ereditare |

Il manifest firmato di `undo_last_turn` è la fonte concreta più forte per la
prima fetta: nome esatto, args runtime-owned, output, collocazione e capability
`system:undo`. Tuttavia il prefisso di capability non basta come classificatore
universale: esistono anche capability `system:read` su normali executor e
`system:admin` sul builtin amministrativo.

Conclusione: l'identità `undo_last_turn` è coperta dalla decisione di Roberto;
la regola generale con cui un contratto diventa un controllo d'intento non è
ancora presente nel codice.

## 5. Contratto e ciclo di vita di `get_approval`

### 5.1 Contratto pubblico

`get_approval` è un executor canonico `get/approval`, non un controllo di
sistema. Il manifest firmato dichiara:

- `prompt` obbligatorio;
- `on_approve` obbligatorio, forma `{tool, args}`;
- `on_reject` opzionale con la stessa forma;
- capability `dialog.user_input`;
- collocazione server-only.

Ogni ramo porta **un solo executor**. Inoltre il JSON Schema annidato elenca
`tool` e `args` ma non marca `tool` come required; l'implementazione lo impone
a runtime e tollera anche l'alias `executor`, non dichiarato dal manifest.
Questa deriva non va copiata nel nuovo contratto.

`dialog.user_input` non identifica una barriera: è condivisa da `get_inputs` e
da executor sites che aprono dialoghi durante il proprio ciclo interno.

### 5.2 Pausa e ripresa effettive

Il runtime riconosce in modo generale una pausa da:

```text
decision == input_required AND dialog_id presente
```

Il seguito ha però due forme diverse:

1. per un gate normale nel framework, il callback diventa
   `resume_engine_gate`: dopo approvazione riesegue la query originale con
   `_gate_approved`, rimuove `get/approval` dall'intento e ricostruisce il
   piano;
2. per un gate emesso dentro un executor auto-riprendibile, il runtime può
   conservare una lista interna `tail_steps` e riprenderla dal risultato
   approvato.

La seconda forma dimostra che una continuazione a più passi è tecnicamente
possibile. Non è però un contratto pubblico di `get_approval`, non nasce dagli
archi dell'analizzatore e non è una forma firmata disponibile al proiettore.
La prima forma non è una continuazione tipizzata: rianalizza e ripianifica la
query, quindi può cambiare rotta o osservazione fra consenso ed esecuzione.

## 6. Verifica della proposta iniziale

| Elemento proposto | Verdetto contro il codice |
|---|---|
| radice esclusiva `operation_graph | system_control | unrepresentable` | **necessaria e compatibile come nuovo oggetto**, incompatibile con i consumatori V23lite senza adattatore |
| controlli fuori da `ACTIONS` | **confermato**; `undo` non è in `ACTIONS` e i due falsi instradamenti lo provano |
| `unrepresentable` senza rotta | **necessario**; oggi invalidità tecnica, assenza d'intento e significato non rappresentabile sono confusi |
| archi dati separati dagli archi di controllo | **necessario**; `source_predicate_id` descrive soltanto dati e non protegge bersagli |
| guardie generali | **attuabili**; devono validare anche il grafo unito dati+controllo e non riparare semantica |
| barriera ricavata da registro tecnico | **lacuna**; il registro semantico della barriera non esiste |
| più bersagli sotto un'approvazione | **rappresentabile nell'analisi**, non proiettabile onestamente nel contratto pubblico attuale di `get_approval` |
| proiezione nel runtime corrente | **non autorizzata e non lossless**; `Intent` e cache non conoscono radice, astensione o controllo |

La proposta è quindi promossa da “buona ipotesi” a **base coerente per un
prototipo autonomo**, non a contratto preciso né a modifica di produzione.

## 7. Letture da punti di vista diversi

### 7.1 Teoria dei tipi

La radice discriminata è un tipo somma: rende impossibile confondere undo,
grafo e astensione. Il controllo richiede inoltre un tipo diverso dal flusso
dati. Questa separazione elimina classi di stati invalidi invece di insegnare
al modello a evitarli in prosa.

### 7.2 Sicurezza

Un elenco piatto di azioni più un gate non basta. La proprietà importante è la
**dominanza**: ogni bersaglio protetto deve appartenere soltanto alla
continuazione governata e non deve essere raggiungibile dal ramo
incondizionato. Il validatore deve controllare la proprietà sul documento, non
inferirla dalla posizione testuale.

### 7.3 Interazione

`unrepresentable` migliora soprattutto il comportamento: consente domanda di
chiarimento o arresto prudente. Per non diventare una scorciatoia del modello,
ogni astensione va giudicata dall'oracolo come corretta o indebita e mantenuta
separata dagli errori tecnici.

### 7.4 Compilatori

Il prototipo va trattato come un front-end:

```text
documento del modello
  -> validazione strutturale
  -> forma normale immutabile
  -> validazione semantica contro registri
  -> proiezione tipizzata
  -> adattatore di sola valutazione
```

Non deve esistere un metodo che contemporaneamente muti, validi e proietti il
medesimo dizionario, come accade oggi nelle toppe del banco.

### 7.5 Prospettiva non ortodossa: regioni controllate

Invece di una lista globale di archi, una barriera potrebbe possedere
strutturalmente le sue continuazioni. Gli identificatori diventerebbero
percorsi derivati e un bersaglio protetto non potrebbe apparire per errore
nella radice incondizionata.

Questa forma può essere più corta e più sicura, ma complica gli archi dati che
attraversano il confine della regione. È una scelta nuova, non una conclusione
di questo referto.

### 7.6 Evoluzione e universalità

Un contratto universale non deve enumerare nel codice `get_approval` o
`undo_last_turn` come casi speciali. Deve ricevere registri tipizzati e
congelati. La prima fetta può contenere un solo elemento, purché il meccanismo
di compilazione non dipenda dal suo nome.

## 8. Prestazioni: evidenza e leve lecite

Nel confronto fresco affiancato già archiviato per c11:

| | controllo A + riparo ruolo | c11 |
|---|---:|---:|
| campi richiesti per clausola | 21 | 17 |
| token di completamento totali | 56.207 | 47.790 |
| mediana token | 284 | 246 |
| latenza mediana | 4.033 ms | 3.587 ms |

Il taglio vale circa **−15,0% token di uscita** e **−11,1% di mediana** in
quella coppia. Non prova la qualità semantica di c11, che resta indecisa, ma
prova che derivare informazione ridondante è una leva reale.

Per il nuovo prototipo le leve promettenti, ancora da misurare, sono:

- un discriminante emesso per primo e branch corti per undo/astensione;
- identità derivate dalla posizione, senza specchi;
- struttura clause-owned, senza usare gli span come identità;
- registri compilati una volta e iniettati, senza duplicarli nell'uscita;
- nessuna espansione dello schema in un ramo per ogni rotta: la linea V26 ha
  già mostrato che uno schema molto ramificato può comprare precisione al
  prezzo di una latenza proibitiva;
- persistenza separata di grezzo, forma normale ed esito, senza seconda
  chiamata LLM.

Sono ipotesi di progetto. La misura GPU resta quella prevista al punto 5, con
controllo fresco affiancato; non viene anticipata per scegliere la forma.

## 9. Nuove scelte da sottoporre a Roberto

### Scelta 1 — forma del controllo

(a) **Lista piatta di archi**, vicina alla proposta iniziale:
`{controller_id,target_id,outcome}`.

(b) **Regioni/continuazioni possedute dalla barriera**, con bersagli annidati e
identità derivate. È la raccomandazione di Codex per sicurezza by-construction
e compattezza, ma va provata sugli archi dati trasversali.

### Scelta 2 — dominio degli esiti

(a) Booleano `true|false`, minimo e sufficiente alla prima approvazione.

(b) Identificatori chiusi dichiarati dal contratto della barriera, per esempio
esiti tecnici equivalenti ad approvato/rifiutato. È la raccomandazione di Codex
per universalità: non tutte le future barriere devono essere booleane.

### Scelta 3 — autorità del registro ombra

(a) Un **registro di laboratorio revisionato**, generato da manifest e
contratti con le loro impronte, che aggiunge esplicitamente il ruolo d'intento
senza modificare la produzione. È la raccomandazione per il prototipo.

(b) Estendere subito lo standard dei manifest firmati con metadata di
controllo/barriera. È più diretto per il futuro runtime, ma sarebbe già una
modifica di produzione e non è autorizzata in questa fase.

Non è sicuro inferire il ruolo da `SYSTEM_VERBS`, dal prefisso `system:*`, da
`dialog.user_input` o dal nome dell'executor.

### Scelta 4 — continuazione dopo il consenso

(a) Conservare una **continuazione tipizzata e immutabile** costruita dalla
proiezione, senza nuova analisi della query. È la raccomandazione di Codex per
correttezza e riproducibilità.

(b) Riutilizzare la ripresa corrente che riesegue la query con un bit di
pre-approvazione. Richiede meno nuova superficie, ma non garantisce che il
piano approvato sia quello eseguito.

Il punto 2 non inizia finché Roberto non chiude queste scelte o non restringe
esplicitamente il primo prototipo a una delle forme.

## 10. Revisione avversariale

### Attacco

1. Il censimento statico può non vedere un consumatore caricato per nome o da
   un file esterno; dichiarare completa la migrazione sarebbe prematuro.
2. Un nuovo oggetto autonomo rischia di duplicare vocabolario e catalogo e di
   creare un altro prototipo elegante ma scollegato dal sistema reale.
3. Le regioni annidate possono rendere difficili dataflow fra operazioni prima
   e dentro il gate, condizioni condivise e futuri esiti multipli.
4. Un ramo `unrepresentable` può alzare artificialmente la sicurezza
   astenendosi sui casi difficili e abbassare la copertura senza che un totale
   medio lo mostri.
5. Il guadagno c11 non è una previsione affidabile del nuovo prototipo: cambia
   soltanto quattro specchi, mentre una radice discriminata aggiunge struttura.
6. La capability firmata `system:undo` non dimostra da sola che l'executor sia
   un controllo di radice; capability `system:read` smentiscono la regola del
   prefisso.
7. `tail_steps` nel runtime può sembrare già la continuazione richiesta, ma è
   uno stato interno post-piano, non un contratto derivato dall'analisi.
8. Anche un grafo strutturalmente perfetto può proteggere il bersaglio sbagliato
   o produrre una rotta semanticamente falsa.

### Risposte

1. Il referto dichiara il censimento statico come limite. Prima di qualunque
   integrazione serviranno una traccia shadow e test di compatibilità dei
   consumatori; oggi non si integra nulla.
2. Schema e validatore del prototipo dovranno essere compilati da registri
   congelati, non contenere copie manuali di `ACTIONS`, oggetti o route.
3. Per questo la regione controllata resta un'alternativa sottoposta a Roberto,
   non una decisione. I casi con dataflow trasversale saranno prove
   deterministiche obbligatorie del contratto scelto.
4. L'oracolo deve contare separatamente astensione corretta, astensione indebita,
   errore e falsa azione; nessuna compensazione media.
5. I numeri c11 provano soltanto la leva “meno output derivabile”. La nuova
   forma avrà il proprio controllo affiancato e nessun verdetto sotto tre casi.
6. Il registro deve dichiarare esplicitamente il ruolo d'intento e pin-nare la
   fonte; non si userà il prefisso di capability come euristica.
7. Un'eventuale continuazione nuova deve avere schema, validatore, impronta e
   proprietà di non riesecuzione. Non si promuove per analogia il callback
   interno esistente.
8. L'oracolo completo sulle 120 e le colonne dedicate a undo, consenso,
   negazione e rami condizionali restano obbligatori. La validità strutturale
   non riceve credito di accuratezza.

## 11. Stato finale del lavoro

- punto 1 dell'ordine autorizzato: **completato**;
- punto 2: **non iniziato**, fermo sulle quattro scelte del §9;
- misura GPU: **0**;
- runtime/produzione: **nessuna modifica**;
- servizi: **nessun riavvio**;
- banco e output congelati: **nessuna modifica**;
- verificatore sigillato: **verde, 6/6 impronte**;
- commit: **nessuno**.
