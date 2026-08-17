# Checkpoint compatto — qualità del prototipo di estrazione dell'intento

Data: **12 agosto 2026**.

Questo documento conserva le decisioni esplicite date da Roberto dopo il
riesame della colonna semantica. Non modifica né riapre la `SINTESI OPERATIVA`
firmata: registra nuove indicazioni di Roberto per il lavoro successivo.

## Obiettivo immediato

Migliorare la **correttezza della rotta** prodotta dal prototipo. Velocità,
numero di token e riduzione dei campi sono secondari. La produzione resta in
sola lettura e il prototipo continua a vivere nel laboratorio in ombra.

Il confronto diretto già eseguito riguarda l'estrattore attuale contro
`V23lite + riparo_ruolo` sulle stesse 120 richieste:

- 65 rotte uguali, non ancora adjudicate;
- fra le 55 diverse: 9 vittorie del prototipo, 28 dell'attuale, 18 fallimenti
  comuni;
- quindi **non esiste ancora** un totale corretto di successi su 120;
- nessuna voce lessicale è oggi cancellabile;
- mediana circa 310 ms per l'attuale e 3.961 ms per il prototipo.

## Decisioni esplicite di Roberto

1. **Esito “non rappresentabile”: approvato come requisito.** Deve impedire che
   una richiesta fuori dalla rappresentazione diventi un'azione falsa. Roberto
   osserva correttamente che migliora soprattutto sicurezza e interazione: non
   trasforma da solo una risposta sconosciuta in una rotta corretta.
2. **I controlli di sistema non entrano nelle azioni canoniche.** In particolare
   `undo` non va aggiunto ad `ACTIONS`. Codex deve analizzare il problema e
   proporre una rappresentazione separata.
3. **La dipendenza di controllo va rappresentata.** Una frase come «se approvo,
   pubblica» deve conservare il legame tra condizione e azione. Codex deve
   proporre il meccanismo concreto, implementarlo nel laboratorio e provarlo.
4. **Niente nuovo hardcoding.** Sono ammesse soltanto guardie generali, guidate
   da contratti e registri autorevoli. Vietati dizionari di parole, controlli
   sulla query grezza, eccezioni per una rotta e riscritture del tipo
   «se trovi X, cambia in Y».
5. **Costruire e usare la verifica completa.** Vanno giudicati anche i 65
   accordi, così da ottenere finalmente i successi reali su 120 per entrambi i
   percorsi.
6. **Procedere una famiglia alla volta.** Dopo il nucleo di rappresentazione e
   verifica, provare la fetta del ciclo di vita degli artefatti già concordata;
   `sites` resta la seconda candidata.
7. **Standard dei prompt: obiettivo confermato.** Non si accorcia un prompt per
   il gusto di accorciarlo. Si vuole uno standard unico per creare e verificare
   i prompt. Ogni prompt riscritto secondo lo standard deve essere misurato
   contro il proprio controllo; non si adotta per eleganza o sola conformità.

Queste sono decisioni di Roberto, non un accordo ricostruito fra agenti.

## Proposta tecnica iniziale di Codex da analizzare

Questa è la proposta di partenza richiesta da Roberto, **non ancora una modifica
di produzione**. Nomi e forma definitiva vanno confermati dopo il censimento dei
consumatori.

### 1. Radice discriminata, fuori da `ACTIONS`

L'uscita dell'analizzatore diventa una scelta esclusiva fra tre esiti:

```text
operation_graph   normale grafo di operazioni
system_control    comando interno di Metnos, per esempio undo_last_turn
unrepresentable   significato riconosciuto ma non esprimibile dal contratto
```

- `system_control` usa identificatori ricavati da un registro autorevole dei
  controlli runtime, separato da `ACTIONS`; non contiene sinonimi o parole della
  query.
- La prima fetta è `undo_last_turn`, già esistente come controllo runtime.
- `unrepresentable` non può contenere predicati eseguibili né una rotta.
- In una prima versione prudente, una richiesta che mescola controllo di
  sistema e normali operazioni può risultare `unrepresentable`; non si inventa
  una composizione non ancora supportata.

Questa forma evita sia `undo` fra le azioni canoniche sia l'abuso del campo
`role`, che oggi descrive la funzione grammaticale del predicato.

### 2. Archi di controllo distinti dagli archi dati

Il grafo operativo conserva `input_from_predicate_id` soltanto per il flusso dei
dati e aggiunge una collezione separata concettualmente equivalente a:

```text
control_edges = [
  {controller_id, target_id, outcome: true|false}
]
```

- `controller_id` identifica una condizione oppure un'operazione dichiarata
  come barriera dal registro tecnico, per esempio `get/approval`.
- `target_id` identifica l'operazione subordinata.
- Più archi possono partire dalla stessa barriera: «approva, poi pubblica e
  aggiorna» conserva entrambe le azioni sotto la stessa decisione.
- Il proiettore raggruppa i bersagli in una continuazione protetta; non li
  presenta come richieste libere.

Il contratto attuale di `get_approval` accetta un solo executor per ramo. La
proiezione di più bersagli richiede quindi di studiare una continuazione
tipizzata del piano; non va simulata concatenando stringhe o creando un
executor fittizio. Questo punto è parte dell'analisi iniziale e non è ancora una
decisione di produzione.

### 3. Guardie consentite

Le guardie possono soltanto verificare proprietà generali:

- l'esito è esattamente uno fra grafo, controllo di sistema e non
  rappresentabile;
- un controllo di sistema esiste nel registro autorevole e non porta azioni
  canoniche;
- un esito non rappresentabile non porta operazioni eseguibili;
- ogni rotta esiste nel registro generato dai manifest revisionati;
- gli identificatori degli archi esistono, i ruoli sono compatibili e il grafo
  non contiene cicli;
- ogni barriera dichiarata governa almeno un bersaglio;
- un bersaglio controllato non compare nella proiezione incondizionata.

Una guardia **rifiuta** una struttura incoerente; non indovina la semantica e
non corregge una rotta in base alle parole dell'utente.

## Ordine di lavoro autorizzato

1. Censire schema, validatore, proiettore e consumatori di V23lite, dei controlli
   runtime e di `get_approval`; verificare la proposta sopra contro il codice.
2. Scrivere il contratto ombra preciso e sottoporlo a revisione avversariale.
   Se emerge una nuova scelta di progetto non coperta dalle decisioni sopra,
   fermarsi e presentarla a Roberto.
3. Congelare e riconciliare inventario, vocabolario, catalogo, contratti e
   impronte; generare il registro tecnico dai manifest revisionati, come già
   previsto dal passo 2 della sintesi.
4. Costruire l'oracolo di rotta per **tutte** le 120 richieste, inclusi i 65
   accordi, prima di osservare il nuovo candidato.
5. Implementare nel solo laboratorio la fetta minima:
   `unrepresentable`, `system_control=undo_last_turn` e archi di controllo per
   approvazione. Prima prove deterministiche, poi una sola misura GPU con
   controllo fresco affiancato.
6. Misurare separatamente: successi completi di rotta, astensioni, errori,
   mutazioni non richieste, `undo`, consenso, negazione e rami condizionali.
   Le colonne di sicurezza non si compensano.
7. Se la fetta supera il controllo, passare ai bersagli 49, 54, 67 e 73 con il
   38 di controllo; poi, in un ciclo distinto, a `sites`.
8. Trattare lo standard dei prompt come esperimento separato dalla modifica di
   schema: stesso contenuto, una sola trasformazione per volta, revisione
   avversariale prima della misura e confronto affiancato sulle 120.

## Stato dell'ordine dopo il censimento del 12 agosto 2026

Il **punto 1 è completato**. Referto:
`internal/design/referto_censimento_prototipo_intento_12_8_2026.md`.

Il censimento ha verificato nel codice che:

- V23lite non ha una facciata autonoma: schema, validatore, normalizzazioni e
  più proiettori condividono direttamente la vecchia radice;
- il runtime di produzione consuma un `Intent` piatto in almeno 17 file e non
  può conservare irrappresentabilità o dipendenze di controllo;
- nessuno degli insiemi correnti (`SYSTEM_VERBS`, nomi speciali, builtin a
  verbo unico, helper e capability) è il registro autorevole dei controlli;
- il manifest firmato di `undo_last_turn` è la fonte concreta della prima
  fetta, ma manca il metadata generale che lo classifichi come controllo di
  radice;
- `get_approval` dichiara un solo executor per ramo; la ripresa multi-passo
  esistente o riesegue la query oppure conserva una coda interna non firmata,
  quindi non è ancora la continuazione tipizzata richiesta;
- il nuovo lavoro deve essere un prototipo autonomo di laboratorio, non una
  toppa al banco e non un innesto prematuro nel runtime.

Il punto 2 **non è iniziato**. Prima del contratto preciso Roberto deve
scegliere:

1. archi di controllo piatti oppure regioni/continuazioni possedute dalla
   barriera;
2. esiti booleani oppure identificatori chiusi dichiarati dal contratto della
   barriera;
3. registro ombra revisionato e pin-nato oppure modifica immediata dei metadata
   firmati di produzione;
4. continuazione tipizzata immutabile oppure ripresa corrente con nuova analisi
   della query.

Codex raccomanda rispettivamente: regioni possedute, esiti tipizzati, registro
ombra e continuazione immutabile. Sono raccomandazioni, non decisioni assunte.

La revisione avversariale del referto contesta completezza del censimento
statico, rischio di duplicazione, falsa sicurezza dell'astensione, difficoltà
del dataflow attraverso regioni e trasferibilità dei guadagni di latenza. Le
risposte mantengono separati validità e accuratezza, richiedono registri
generati, oracolo completo e prova affiancata. Nessun rilievo autorizza il punto
2 senza le scelte sopra.

Produzione è rimasta in sola lettura; nessun servizio riavviato, nessuna misura
GPU, nessuna modifica al banco congelato e nessun commit. Il verificatore
salvato si chiude con codice 0 su 120 richieste, 10 sentinelle, 30 coppie e 6/6
impronte; il controllo SHA-256 indipendente conferma inalterati banco, output
c10/c11, output con riparo e mappatura sigillata.

## Stato dell'ordine dopo il contratto ombra del 12 agosto 2026

Roberto ha approvato le quattro raccomandazioni del censimento:

1. regioni possedute dalla barriera;
2. esiti tecnici chiusi;
3. registro ombra revisionato e congelato;
4. continuazione tipizzata e immutabile.

La quarta decisione vale per il prototipo corrente. Roberto ha chiesto di
lasciare aperta la possibilità di provare in futuro un comportamento diverso;
un'eventuale alternativa dovrà avere una nuova versione e non cambierà in
silenzio questo contratto.

Il **punto 2 è completato**. Contratto preciso e revisione avversariale:
`internal/design/contratto_ombra_prototipo_intento_12_8_2026.md`.

Il contratto stabilisce tre radici esclusive (`operation_graph`,
`system_control`, `unrepresentable`), operazioni ordinarie referenziate da
registro, barriere con continuazioni annidate per esito, archi dati soggetti a
dominanza, normalizzazione non mutante, validazione fail-closed e una
continuazione legata a impronta. Il modello non emette identificatori propri,
campi specchio, input runtime o testo libero per inventare rotte ed esiti.

La revisione avversariale ha attaccato copertura reale, possesso semanticamente
sbagliato, casi omessi, fragilità degli ordinali, obsolescenza della
continuazione, deriva del registro, abuso dell'astensione, merge condizionali,
contaminazione e costo dei casi complessi. Le difese rendono obbligatori
inventario con impronte, mutation test, oracolo per colonne e misura
affiancata; la versione 0.1 fallisce chiusa sui merge condizionali.

Non è emersa una nuova scelta necessaria per il passo seguente. Il primo punto
non completato è ora il **punto 3: riconciliare e congelare l'inventario**.

Produzione e banco sono rimasti in sola lettura; servizi non riavviati, GPU 0,
commit 0.

## Stato dell'ordine dopo l'inventario congelato del 12 agosto 2026

Il **punto 3 è completato**. Referto e artefatti:

- `internal/design/referto_inventario_prototipo_intento_12_8_2026.md`;
- `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_registry_v0_1.json`;
- `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_registry_v0_1.freeze.json`;
- `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/verify_registry.py`.

Il registro ombra contiene 80 rotte ordinarie, 1 controllo di
sistema, 1 barriera, 2 esiti chiusi e
6 ragioni di non rappresentabilità. 12 fonti sono legate
da impronta. `undo_last_turn` è il solo controllo promosso e
`get/approval` la sola barriera; `admin` resta un verbo di sistema
osservato ma non promosso, perché Roberto non ne ha autorizzato la
classificazione.

Il primo risultato a zero è stato respinto dalla revisione avversariale:
il verificatore non faceva fallire una voce del catalogo non classificata.
È stato aggiunto l'invariante, separate le voci speciali e aggiunta una
mutazione dedicata. Il ciclo finale termina con codice 0 ed **error_count
0**; tutte le 7 mutazioni vengono catturate.

Restano 3 limiti dichiarati: porte dati astratte, file di firma pin-nati
ma non riverificati crittograficamente dal laboratorio, copertura limitata al
catalogo congelato. Non sono errori nascosti e vietano qualunque proiezione in
produzione.

La revisione avversariale conclude che errore zero prova coerenza,
completezza rispetto alle fonti e congelamento, non accuratezza semantica. Il
primo punto incompleto è ora il **punto 4: costruire l'oracolo completo**.

Produzione e banco sono rimasti in sola lettura; servizi non riavviati, GPU 0,
commit 0.

## Punto 4 avviato — fermo sulle tre decisioni di adjudicazione

> **Stato storico, superato dalla sezione terminale «Punto 4 completato».**
> Questo paragrafo conserva il motivo dell'arresto iniziale e non descrive più
> il punto attivo dell'ordine autorizzato.

Il punto 4 non può essere completato correttamente finché Roberto non chiude le
tre questioni già lasciate aperte nel §16 della consegna. Sono state tradotte
in linguaggio semplice in:

`internal/design/scelte_oracolo_pendenti_12_8_2026.md`.

Le decisioni sono:

1. se una risposta giusta soltanto come categoria debba contare come errore;
2. se un arresto prudente sia una chiusura sicura anche quando l'accuratezza è
   sbagliata;
3. se “mutazione non richiesta” significhi soltanto un vero tentativo di
   cambiare stato oppure qualunque interpretazione errata.

Raccomandazione di Codex: significato esatto obbligatorio; arresto prudente
positivo soltanto nella colonna di sicurezza; mutazione limitata alle azioni
che cambiano davvero stato.

La revisione avversariale ha verificato che scegliere implicitamente una delle
tre definizioni permetterebbe di ottenere uno zero artificiale. Per questo il
ciclo è fermo prima del congelamento dell'oracolo. Produzione e banco sono
intatti; servizi non riavviati, GPU 0, commit 0.

## Punto 4 ripreso — regole chiuse, fonte dell'oracolo non coperta

> **Stato storico, superato dalla sezione terminale «Punto 4 completato».**
> L'audit resta valido come prova della lacuna delle fonti preesistenti; la
> scelta di autorità è stata poi chiusa da Roberto con la doppia revisione AI.

Roberto ha chiuso le tre definizioni:

1. significato inesatto uguale errore, anche con categoria generale corretta;
2. arresto prudente sicuro ma non accurato;
3. soltanto un vero cambiamento o effetto esterno è una modifica non
   richiesta; una lettura o ricerca sbagliata è errore, non modifica.

L'audit riproducibile delle fonti è in:

- `internal/design/referto_blocco_oracolo_intento_12_8_2026.md`;
- `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_oracle_source_audit_v0_1.json`;
- `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/audit_oracle_sources.py`.

L'audit termina con errori 0 ma prova un blocco di copertura: delle 120
richieste congelate, soltanto 3 hanno oggi una risposta d'oro indipendente
riutilizzabile; ne mancano 117. Il gold vivo dei benchmark appartiene
a un campione diverso. I controlli presenti sono 34 sui 38 richiesti; ne
mancano 4. Le fonti esistenti non contengono già le tre nuove radici
tipizzate.

Promuovere l'accordo dei bracci c10/c11/controllo a gold sarebbe circolare.
La nuova scelta è quindi fra adjudicazione indipendente rigorosa delle
richieste scoperte, raccomandata da Codex, oppure un'etichetta provvisoria per
consenso che non può sostenere un verdetto finale.

Il punto 4 resta incompleto. Per la regola di Roberto, Codex si ferma su questa
scelta senza assumerla. Produzione e banco sono intatti; servizi non
riavviati, GPU 0, commit 0.

## Standard dei prompt: interpretazione vincolante

`internal/design/manifesto_scrittura_prompt.md` resta una raccolta di prove, non
lo standard definitivo: il suo §1 è falsificato e i punti non misurati non si
promuovono per analogia.

Il futuro standard deve almeno:

- conservare un inventario esplicito dei fatti prima e dopo la riscrittura;
- essere coerente campo per campo con schema e validatore;
- distinguere regole generali, definizioni e esempi senza imporre una forma che
  cambi il contenuto;
- vietare sinonimi e instradamento lessicale nel prompt;
- avere controllo e candidato eseguiti di fianco sullo stesso campione;
- restare separato dalle modifiche di schema, così il risultato è attribuibile.

La precedente formula «niente nuove modifiche di prosa al prompt» è superata
**solo in questo perimetro deciso da Roberto**: costruzione e prova di uno
standard unificato. Non autorizza ritocchi liberi per inseguire il punteggio.

## Questioni ancora aperte

Le tre questioni del referto semantico non sono state decise in questo scambio e
non vanno risolte unilateralmente:

1. al caso 18, verificare tutti i divieti operativi o soltanto i sei enumerati;
2. al caso 69, classificare `send/messages` come mutazione non richiesta oppure
   soltanto come rotta semanticamente errata;
3. usare `fail_closed` anche per una negazione correttamente conservata oppure
   dichiararlo non applicabile quando non c'è irrappresentabilità.

Non bloccano il censimento e il progetto del nuovo contratto; devono però essere
chiuse prima di pubblicare i totali definitivi della colonna semantica e
dell'oracolo.

## Vincoli operativi invariati

- sola lettura sulla produzione; nessun riavvio e nessuna modifica al runtime;
- nessun commit;
- non modificare mai `unified_query_bench_v23_checkpoint.py`;
- una sola misura GPU per volta, controllo fresco affiancato, stesse 120;
- banda di rumore ±1; sotto tre casi nessun verdetto;
- regressioni su `undo`, consenso, negazione e rami condizionali in colonne
  separate e mai compensate;
- dopo ogni lavoro, rieseguire i conteggi e aggiungere attacco avversariale e
  risposte al referto;
- ogni referto prodotto da un solo agente dichiara che vale come proposta a
  Roberto, non come accordo fra due agenti.

## Revisione avversariale del consolidamento

### Attacco

1. La radice discriminata è soltanto una buona ipotesi: non è stato ancora
   dimostrato che tutti i consumatori possano gestirla né che sia la forma più
   piccola compatibile con richieste composte.
2. Il «registro autorevole dei controlli di sistema» non è ancora una fonte
   unica pronta all'uso: `SYSTEM_VERBS`, `SYSTEM_EXECUTOR_NAMES`, helper
   universali e contratti builtin coprono insiemi diversi. Scegliere una fonte
   senza censimento ricreerebbe incoerenza.
3. `get_approval` oggi accetta un solo executor per ramo. Dire che il proiettore
   raggruppa più bersagli non basta: serve un contratto di continuazione
   tipizzato, altrimenti il caso 77 resta inesprimibile.
4. Roberto ha autorizzato guardie generali, non riparazioni deterministiche
   della semantica. Un proiettore che cambiasse `login/urls` in `login/sites`
   sarebbe hardcoding mascherato.
5. L'oracolo completo non può essere chiuso onestamente finché i tre criteri
   ancora aperti non sono definiti. Partire comunque produrrebbe un numero con
   regole cambiate durante la lettura.
6. Uno standard dei prompt può alterare il contenuto anche conservando tutte le
   frasi, per esempio cambiando ambito e precedenza. La sola lista dei fatti non
   prova equivalenza.

### Risposte

1. La prossima attività è censimento e proposta, non implementazione immediata;
   la radice discriminata è marcata come provvisoria e i casi misti inizialmente
   si chiudono prudentemente.
2. Il registro viene compilato soltanto dopo aver riconciliato tutte le fonti;
   ogni conflitto viene registrato e rimesso a Roberto.
3. La continuazione a più azioni è dichiarata come lacuna. Non si concatena, non
   si annida artificiosamente `get_approval` e non si modifica la produzione.
4. Le guardie rifiutano strutture incoerenti e il proiettore conserva soltanto
   struttura già dichiarata; nessuno dei due corregge il significato o legge la
   query grezza.
5. Si può preparare meccanicamente l'oracolo, ma l'adjudicazione definitiva
   attende i tre criteri. Il candidato nuovo resta non osservato fino al
   congelamento.
6. Ogni riscrittura riceve anche un controllo di ambito, precedenza, esempi e
   istruzioni implicite, seguito dalla misura affiancata. Se cambia contenuto,
   non è una prova della sola forma.

## Frase di riattivazione

> Riprendi il lavoro Metnos sulla qualità dell'estrazione di intento. Leggi in
> quest'ordine la `SINTESI OPERATIVA` di
> `internal/design/sintesi_soluzione_intento.md` senza riaprirla, poi
> `internal/design/checkpoint_qualita_intento_12_8_2026.md`, quindi i §16-17 di
> `internal/design/handover_prompt_ontologia_11_8_2026.md` e il «Prompt di
> avanzamento» di `internal/design/prompt_avanzamento_codex.md`. Le decisioni
> nuove di Roberto sono nel checkpoint: esito non rappresentabile; controlli di
> sistema fuori da `ACTIONS`; archi di controllo; sole guardie generali guidate
> dai registri, nessun hardcoding; oracolo completo sulle 120; una famiglia alla
> volta; standard unificato dei prompt sempre misurato contro il controllo.
> Esegui il primo punto non completato dell'«Ordine di lavoro autorizzato» del
> checkpoint. Produzione in sola lettura, nessun commit, banco congelato
> intatto, una sola misura GPU con controllo affiancato. Dopo ogni lavoro fai la
> revisione avversariale e aggiorna checkpoint e consegna. Su una nuova scelta
> non coperta dalle decisioni di Roberto fermati e presentala, non deciderla da
> solo.

## Ripresa del punto 4 — doppia revisione AI cieca (12/8/2026)

> **Stato storico, superato dalla sezione terminale «Punto 4 completato».**
> Il testo seguente fotografa il confronto prima dell'adjudicazione e del
> congelamento canonico.

Roberto ha autorizzato due revisori AI separati, dichiarando di non avere un
revisore umano indipendente disponibile. I due revisori hanno lavorato in modo
cieco sugli stessi 120 casi e hanno proposto 4 controlli ciascuno. Questa
procedura è dichiarata come doppia revisione AI e non viene presentata come
revisione umana indipendente.

Entrambi i lavori sono completi e superano i controlli di forma con errore 0.
Il confronto successivo abbina 120/120 casi: 102 `expected` coincidono e 18
divergono, 13 dei quali sulla radice principale. Sedici divergenze sembrano
applicazioni differenti di regole già approvate. Le due serie di controlli
contengono duplicazioni semantiche e non possono essere unite automaticamente.

Durante la revisione Roberto ha approvato questa decisione: se una richiesta
composta contiene una clausola indispensabile fuori registro, l'intera
richiesta è `unrepresentable/outside_registry`; un sottografo parziale non è
una verità eseguibile.

Restano tre possibili scelte nuove, sui casi 38, 84 e 113: precedenza tra
ambiguità e `outside_registry`; scope plurale/enumerazione di `get/images`;
confine tra semplice proiezione di campi già strutturati e nuova operazione.
Codex si ferma senza deciderle.

Il punto 4 resta incompleto: nessun oracolo finale, freeze o promotore è stato
creato. Banco, campione, registro e impronte salvate risultano integri; nessuna
misura GPU, nessun riavvio, nessun commit e nessuna modifica di produzione
attribuibile a questo lavoro. Il worktree di produzione contiene modifiche
precedenti e non è globalmente pulito.

Referto dettagliato:
`internal/design/referto_confronto_doppio_revisore_oracolo_intento_12_8_2026.md`.

Dopo le decisioni di Roberto: adjudicare i 18 disaccordi, scegliere 4 controlli
non duplicati, costruire e congelare l'oracolo, aggiungere verificatore e test
di mutazione, quindi ciclare fino a errore 0.

### Decisione parziale successiva di Roberto

Per il caso 38, nella versione attuale, prevale temporaneamente
`unrepresentable/outside_registry`. Roberto precisa che la capacità reale potrà
essere introdotta in futuro tramite una pipeline di estrazione testuale simile
a quella di `/opt/giorgio2` e, potenzialmente, template per lettori PDF. Questa
è una direzione futura, non un'autorizzazione a modificare ora il contratto o il
registro. L'oracolo v0.1 resterà storico: un supporto futuro richiederà una nuova
versione esplicita.

Successivamente Roberto approva il criterio per il caso 113: scegliere o
rinominare campi già strutturati è una proiezione dati, non una nuova
operazione. `read/events` seguito da `create/files` è quindi rappresentabile.
Interpretazione di testo libero, calcoli e derivazione di nuovi valori restano
fuori da questa decisione e richiedono copertura esplicita.

Roberto chiarisce poi che Metnos trova le immagini ricorsivamente. La verifica
affiancata conferma che `get_images_indices`, senza `base_path`, enumera tutti
gli indici `unified` materializzati e restituisce `base_path` e `n_entries` per
ciascuno. Il caso 84 è quindi rappresentabile con il solo `get/images`;
`find/dirs` è superfluo. La precedente proposta di non rappresentabilità era un
errore di analisi e non una scelta da demandare a Roberto.

Il `catalog_snapshot` congelato risulta arretrato rispetto al manifest corrente
su questa semantica. Non viene modificato; lo scarto va dichiarato nella
provenienza dell'oracolo.

Roberto chiarisce inoltre il requisito futuro del registro dei corpus: deve
guidare il task notturno di reindicizzazione, ma il task deve anche riconciliare
il registro dopo cancellazioni effettuate fuori da Metnos. Un indice mancante
con directory presente va ricostruito/riallineato; una directory realmente
cancellata richiede di sistemare anche la voce di registro. Roberto precisa che
«sistemare» significa marcare la voce come `indisponibile`, non cancellarla.
Questa regola copre anche un disco o mount temporaneamente non raggiungibile e
vieta la rimozione automatica della voce.

Non restano scelte semantiche aperte note per l'oracolo. Il punto 4 rimane incompleto finché
i 18 disaccordi non sono adjudicati e l'oracolo non è costruito e verificato.

## Punto 4 completato — oracolo canonico e verifica avversariale (12/8/2026)

Il punto 4 dell'ordine autorizzato è **completato**. L'oracolo canonico è:

`internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_oracle_v0_1.json`.

Contiene esattamente 120 casi legati per indice, testo e SHA-256 al campione
congelato: 102 `expected` coincidono nelle due revisioni indipendenti e 18 sono
stati adjudicati applicando le decisioni di Roberto. Le radici risultano 84
`operation_graph`, 2 `system_control` e 34 `unrepresentable`. Ai 34 controlli
esistenti sono stati aggiunti esattamente 4 controlli distinti — composto
fail-closed, vista multi-corpus `get/images`, proiezione di campi strutturati e
similarità da foto con `find/persons` — per il totale autorizzato di 38.

La provenienza è una doppia revisione AI indipendente, rimasta cieca fino alle
due consegne, e **non** una revisione umana: `human_review=false`. L'accordo dei
vecchi bracci c10, c11 e controllo non è stato promosso a verità. I 102 accordi
non dimostrano accuratezza universale; i 18 casi divergenti usano
l'adjudicazione autorizzata del revisore A. L'attestazione vale soltanto per i
120 casi, i 38 controlli e il registro ombra 0.1.

Il verificatore termina con `error_count=0` e il freeze contiene 23 fonti,
tutte presenti e coerenti. La suite ufficiale respinge 107/107 mutazioni
negative e accetta 6/6 controlli positivi che riordinano soltanto liste prive
di ordine semantico. Il replay indipendente mirato respinge 30/30 mutazioni e
accetta 6/6 riordini; il piano avversariale termina con 32/32 controlli
superati. Le quattro falle di robustezza emerse sono chiuse:

- D-01: ogni JSON caricato rifiuta chiavi duplicate;
- D-02: ogni JSON rifiuta `NaN`, infinito e numeri che diventano non finiti;
- D-03: tipi JSON esatti, metadati e freeze chiusi ricorsivamente; 47/47
  confusioni di tipo e 5/5 mutazioni dello schema del freeze vengono respinte;
- D-04: le sei liste di autorità e la lista base dei controlli sono insiemi
  canonici esatti, unici e indipendenti dall'ordine; duplicati, aggiunte,
  omissioni e sostituzioni vengono respinti.

L'oracolo è rimasto identico byte per byte durante D-01–D-04. Il freeze è un
sigillo deterministico, non una firma esterna né una prova matematica contro
chi abbia autorità di modificare e risigillare insieme tutte le fonti.

Restano vincolanti le decisioni di Roberto:

1. una categoria quasi giusta con significato errato è un errore;
2. uno stop prudente può essere sicuro ma resta inaccurato;
3. soltanto un vero cambiamento di stato o effetto esterno è una mutazione non
   richiesta; una lettura o ricerca sbagliata è errore di accuratezza;
4. una clausola indispensabile fuori registro rende l'intero composto
   `unrepresentable/outside_registry`, senza sottografo parziale eseguibile;
5. il caso 38 è temporaneamente `unrepresentable/outside_registry`; una futura
   pipeline per fatture con estrazione testuale e possibili template PDF
   richiederà una nuova versione e non modifica retroattivamente la v0.1;
6. il caso 84 è il solo `get/images`, cioè la vista corrente degli indici
   `unified` materializzati, non il futuro registro persistente dei corpus;
7. il caso 113 è `read/events -> create/files`: selezione e rinomina di campi
   già strutturati sono una proiezione, non una nuova operazione.

Il futuro registro dei corpus sarà persistente e indipendente dagli indici e
guiderà la reindicizzazione notturna. Una voce il cui corpus non è raggiungibile
verrà marcata `indisponibile`, non cancellata automaticamente. Registro futuro
e indisponibilità sono esplicitamente fuori dagli `expected` v0.1. Il
`catalog_snapshot` resta l'autorità congelata con 96 executor ed è dichiarato
arretrato rispetto agli 83 manifest correnti; questi ultimi sono osservati ma
non sostituiti silenziosamente.

Banco, campione, registro, oracolo e produzione sono rimasti integri. Non sono
state eseguite misure GPU, riavvii di servizi o commit; il worktree conteneva
già modifiche precedenti e non viene dichiarato globalmente pulito.

I punti 1, 2, 3 e 4 dell'ordine autorizzato sono completati. Il primo punto
incompleto è ora il **punto 5**, con questo mandato esatto:

> Implementare nel solo laboratorio la fetta minima:
> `unrepresentable`, `system_control=undo_last_turn` e archi di controllo per
> approvazione. Prima prove deterministiche, poi una sola misura GPU con
> controllo fresco affiancato.

Il punto 5 non è iniziato in questa chiusura.

## Punto 5 — analisi pre-implementazione e scelta pendente (12/8/2026)

È stata completata soltanto l'analisi preparatoria, in sola lettura. Questa
sezione non avvia il punto 5: non sono stati creati candidati, non è stato
modificato il laboratorio e non è stata eseguita inferenza.

Il nucleo deterministico può essere costruito senza nuove decisioni
semantiche, esclusivamente sotto
`internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/`. Deve
restare separato in quattro responsabilità:

1. estrazione dei byte e del JSON model-facing, con schema e prompt derivati
   dal registro congelato e senza query codificate nel programma;
2. normalizzazione immutabile, limitata a identificatori posizionali, porte
   univoche, materializzazione dei casi vuoti omessi e impronte;
3. validazione strutturale e semantica fail-closed di radici, rotte, controllo
   `undo_last_turn`, regioni `get/approval`, esiti, porte, dominanza e
   continuazioni;
4. valutazione separata, unica componente autorizzata a leggere l'oracolo dopo
   che l'output del modello è stato salvato e legato alle impronte.

Il core deve conservare tipi distinti per `OperationGraph`, `SystemControl`,
`Unrepresentable`, `BarrierRegion`, `OutcomeCase`, `DataEdge` e
`Continuation`. Un errore di trasporto, parsing, schema o limite di risorsa è
un'**invalidità tecnica**: non può essere convertito in
`unrepresentable`. Il laboratorio costruisce e verifica le continuazioni, ma
non le esegue e non rianalizza la frase dopo l'approvazione.

Prima di chiudere valutatore e protocollo di misura serve una scelta di
Roberto sui 34 controlli legacy. La loro fixture congela una tupla di
focus/binding e `expect_binding`; non contiene un `expected` della radice
intent-shadow. La sola rotta non conserva tutte le distinzioni del pannello,
quindi sono vietate conversioni automatiche di `expect_binding=false` in una
rotta diversa o in `unrepresentable`.

Le alternative da decidere sono:

- **A — consigliata:** mantenere i 34 controlli come pannello legacy
  congelato e separato, con il proprio oracolo e le proprie metriche. I 4 nuovi
  controlli restano tipizzati secondo il contratto 0.1. I due pannelli non si
  convertono e non si compensano in una media unica;
- **B:** prima di dichiarare un unico totale tipizzato di 38, autorizzare una
  nuova adjudicazione umana o AI che assegni ai 34 controlli `expected`
  compatibili con una nuova versione esplicita del contratto/oracolo.

La scelta non impedisce di implementare e provare offline extractor,
normalizzatore e validatore; impedisce invece di congelare il valutatore
finale e di attribuire un denominatore unico ai 38 controlli.

Restano rinviate, e vanno presentate a Roberto prima dell'unica misura GPU, le
scelte di protocollo: identità e impronta del controllo fresco, ordine dei due
bracci, seed, modello, budget, timeout, denominatori pubblicati e limiti
tecnici esatti di byte, profondità e nodi. I limiti tecnici possono essere
parametrici durante le prove, ma il loro superamento resta sempre invalidità
tecnica e mai esito semantico `unrepresentable`.

Stato formale: i punti 1–4 restano completati; il punto 5 **non è iniziato** ed
è fermo alla scelta di Roberto fra A e B. Nessuna misura GPU è autorizzata da
questa analisi.

## Punto 5 — decisione sui controlli legacy (12/8/2026)

> La scelta pendente descritta nella sezione precedente è chiusa da Roberto.

Roberto approva l'alternativa **A**. I 34 controlli legacy restano un pannello
congelato separato, valutato esclusivamente con il proprio oracle Phase-1. Non
vengono convertiti automaticamente in `expected` intent-shadow e non vengono
compensati, sommati o mediati con i 120 casi dell'oracolo canonico né con i 4
nuovi controlli tipizzati 0.1.

I risultati devono quindi pubblicare denominatori distinti: 120 casi canonici,
4 controlli tipizzati intent-shadow e 34 controlli legacy Phase-1. La dicitura
storica «34 + 4 = 38» continua a descrivere l'inventario autorizzato dei
controlli, non un unico denominatore semantico tipizzato.

Questa decisione sblocca l'implementazione deterministica offline del punto 5.
Restano non autorizzati la modalità GPU e qualsiasi scelta implicita su
controllo fresco, ordine dei bracci, seed, modello, budget, timeout,
denominatori di verdetto e limiti tecnici finali: tali parametri dovranno
essere presentati a Roberto prima dell'unica misura.

## Punto 5 avviato — fetta deterministica offline completata (12/8/2026)

La sott fase deterministica del punto 5 è implementata esclusivamente in:

`internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/`.

Il candidato non ha import di produzione, trasporto di rete o modalità GPU.
Contiene tipi immutabili, loader JSON fail-closed D-01–D-04, schema e prompt
derivati dal registro, validatore query-blind, normalizzatore non semantico,
continuazioni immutabili, extractor da byte JSON, runner dry-run/fake
query-only e valutatore offline separato. Il runner non apre oracle, overlay o
valutatore; il valutatore apre il gold soltanto dopo aver verificato il batch
completo e riproduce ogni estrazione dai byte raw salvati.

Le tre radici restano esclusive. `unrepresentable` è un esito semantico
tipizzato; `undo_last_turn` viene dal registro dei controlli; `get/approval`
possiede i corpi dei propri esiti e genera continuazioni legate a registro,
documento, percorso, esito e corpo. Un limite di byte, profondità, nodi o
stringa resta sempre invalidità tecnica. I valori oggi presenti nel codice
sono guardrail configurabili per le prove offline, non parametri GPU approvati.

I pannelli sono congelati e distinti secondo la decisione A:

- query-suite tipizzata: 120 casi canonici + 4 controlli 0.1;
- pannello legacy: 34 controlli query-only, legati all'oracolo Phase-1 e mai
  convertiti o compensati con i pannelli tipizzati.

Le prove deterministiche terminano con errore 0:

- 124/124 `expected` tipizzati completano decode, validazione,
  normalizzazione e proiezione;
- 7/7 gruppi di scenario superati, compresi i tre casi minimi, il registro
  sintetico rinominato anti-hardcoding, la contaminazione e il fake batch;
- 57/57 mutazioni negative respinte e 7/7 controlli positivi accettati;
- builder delle suite e verificatore del freeze candidato con
  `error_count=0`;
- dry-run query-only disponibile su 120 + 4 + 34, senza query negli output
  persistiti.

Il fake transport che riproduce il gold prova soltanto la condotta
deterministica: non è una misura di accuratezza del modello e non riceve
credito sperimentale. Produzione, banco e artefatti canonici di registro e
oracolo restano invariati, salvo il risigillo dell'handover già previsto.

Stato formale: il punto 5 è **avviato ma non completato**. Il prossimo passo è
presentare a Roberto, prima di qualunque inferenza, un protocollo chiuso per
controllo fresco, ordine dei bracci, seed, modello, budget, timeout,
denominatori di verdetto e limiti tecnici finali. Nessuna di queste scelte è
stata assunta e nessuna misura GPU è stata eseguita.

## Punto 5 — ciclo funzionale 2, tre blocchi chiusi (12/8/2026)

La prima revisione funzionale indipendente del candidato aveva concluso
45/48 controlli e individuato tre difetti bloccanti. Il ciclo 2 li chiude tutti
senza cambiare query, `expected`, registro o composizione dei pannelli:

1. il loader/extractor è totale sugli input JSON ostili ma entro i guardrail:
   outcome di tipo errato, interi oltre il limite, UTF-8 invalido, surrogate
   Unicode e ricorsione restituiscono invalidità deterministica, mai crash e
   mai `unrepresentable`;
2. l'evaluator valida prima del gold oggetti chiusi e tipi JSON esatti in ogni
   campo del batch; in particolare `true`/`false` non equivalgono a `1`/`0`, e
   il replay confronta byte JSON canonici;
3. prima del punteggio l'evaluator verifica il checkpoint candidato e usa il
   verificatore canonico pin-nato su oracle, freeze, lock e fonti. Oracle
   modificato, freeze assente/errato o risigillo incompatibile falliscono
   chiusi.

La matrice deterministica termina con 7/7 gruppi, 124/124 round-trip,
89/89 mutazioni negative respinte e 7/7 positive accettate; builder,
verificatore candidato e verificatore canonico hanno `error_count=0`. I 34
legacy restano separati secondo l'opzione A. Non è stata introdotta alcuna
nuova policy; GPU, rete, servizi, produzione e banco non sono stati usati.

Il punto 5 resta avviato ma incompleto. Resta invariato il prossimo passo:
presentare a Roberto il protocollo chiuso pre-GPU prima di qualunque inferenza.

## Punto 5 — sottfase deterministica chiusa dopo revisione indipendente (12/8/2026)

La revisione funzionale indipendente del ciclo 2 conclude **PASS 48/48** e
conferma chiusi B-01, B-02 e B-03. Nel perimetro deterministico/offline non
restano difetti bloccanti né non bloccanti: 0 difetti osservati.

La matrice finale verificata è: 89/89 mutazioni candidate negative respinte e
7/7 positive accettate; 7/7 scenari; 124/124 round-trip tipizzati; dry-run
158/158 sui pannelli 120 + 4 + 34; oracle 107/107 negative e 6/6 positive;
builder, verificatore oracle, verificatore candidato e lint tutti senza errori.
GPU e rete risultano assenti.

È quindi chiusa soltanto la **sottfase deterministica** del punto 5. Il punto 5
nel suo complesso resta aperto e non è autorizzata alcuna inferenza. Il
prossimo gate è l'approvazione esplicita di Roberto e il congelamento del
protocollo dell'unica misura GPU affiancata, inclusi controllo fresco, ordine,
seed, modello, budget, timeout, denominatori e limiti tecnici finali.

## Punto 5 — protocollo dell'unica misura preparato e congelato (12/8/2026)

Roberto ha approvato tutte le sette decisioni rinviate. Il laboratorio ha
preparato, senza eseguirlo, il protocollo affiancato in
`candidate_v0_1/live_measurement_protocol_v0_1.json`.

Il braccio A è il controllo fresco corrente: `runtime/intent_extractor.py`,
prompt `intent_extractor_v4` IT/EN, workload `intent.extract` fast/micro e
adapter 0.1, tutti improntati. Il braccio B è il candidato intent-shadow 0.1.
Entrambi sono legati allo stesso endpoint locale llama.cpp, build 1422
(`e3546c794`), modello fisico Qwen3.6-35B-A3B Q4_K_M e SHA-256 completo dei
pesi. Profilo comune: temperatura 0, seed 42, massimo 4000 token, timeout 120
secondi, thinking off, zero retry.

Il manifest query-only contiene 158 query e 316 richieste: AB sugli indici
pari e BA sui dispari; 120 canoniche + 4 tipizzate per entrambi, più 34 legacy
per entrambi nel pannello Phase-1 separato senza conversione o compensazione.
I limiti live sono 256 KiB, profondità 64, 10000 nodi, stringa 64 KiB e intero
64 cifre; ogni superamento è `technical_invalid`, mai `unrepresentable`.

Il runner live è distinto dal dry-run. Prima del primo socket scrive un marker
esclusivo e durevole; non offre retry/rerun; salva risposta raw, journal
append-only e checkpoint atomici. Un errore di trasporto/timeout arresta e
sigilla il parziale, mentre JSON/semantica invalidi contano errore e il batch
continua. Il valutatore apre gold soltanto dopo un batch completo di 316
record sigillato e riproduce ogni estrazione dai byte salvati.

Il protocollo è nello stato **preparato ma non autorizzato all'esecuzione**.
Il file di autorizzazione live è intenzionalmente assente e deve legare sia
una revisione indipendente sia l'autorizzazione finale root. Le prove correnti
sono solo preflight/fake locali; rete, GPU e inferenze restano 0. Il punto 5
resta aperto: il prossimo gate è l'audit indipendente del protocollo, poi una
nuova autorizzazione esplicita prima dell'unico POST iniziale.

Preflight locale finale: verificatore protocollo `error_count=0`; 7/7 prove
positive live; 41/41 mutazioni live negative respinte e 5/5 positive
accettate. Restano verdi anche 7/7 scenari e 124/124 round-trip candidato,
89/89 negative + 7/7 positive candidate, oracle 107/107 + 6/6, builder,
verificatori e compilazione. Il manifest resta 316/316 e il file oracle resta
byte-identico. Questi conteggi non consumano la misura.

## Punto 5 — correzione preflight del gate di astensione (13/8/2026)

La revisione funzionale indipendente del protocollo ha rilevato che il file
congelato elencava correttamente nove colonne senza regressione, mentre il
verdetto ne applicava otto e ometteva `correct_abstention`. Il laboratorio ha
centralizzato l'insieme canonico delle nove colonne e ora protocollo, loader,
evaluator, report e test usano e verificano lo stesso insieme.

Una regressione della sola `correct_abstention` impedisce esplicitamente
`candidate_pass`; il caso positivo senza regressioni resta ammesso quando
anche gli altri gate congelati sono soddisfatti. Le prove live sono ora 9/9;
la matrice mutazionale resta 41/41 negativa e 5/5 positiva. Il protocollo
resta preparato ma non autorizzato: nessun file di autorizzazione, marker,
POST, rete o inferenza è stato creato/eseguito. Serve ancora una nuova
revisione indipendente PASS prima dell'eventuale autorizzazione finale root.

## Punto 5 — armamento monouso, separazione auth/consumo (13/8/2026)

Il primo file di autorizzazione, SHA-256
`597037352ac6e10875499c42545184999afeb0ff3b97e0bf0352bb20e42e68bf`,
è **superseded**: ha rivelato un conflitto tecnico nel quale il verificatore
considerava l'autorizzazione stessa sia inattesa sia prova di run già iniziato.
Non ha prodotto POST, marker, journal o output.

Il fix distingue ora due stati espliciti. In stato `disarmed` l'autorizzazione
deve essere assente; in stato `armed` deve esistere esattamente il file indicato
e deve superare tutti i legami di hash. In entrambi gli stati, consumption,
journal, checkpoint, partial, batch, seal, output di valutazione e ogni altro
artefatto `live_run_*` restano bloccanti prima del primo POST. L'autorizzazione
non appartiene più a tale insieme.

`--execute-once` esegue il preflight armato prima del percorso di trasporto.
Una prova offline raggiunge un transport sentinel con percorsi temporanei e
conferma che auth mancante, extra o stale e ciascun vero artefatto live
falliscono chiusi. Restano invariati consumo al primo POST accettato, marker
prima del socket e zero retry. Nessun endpoint, rete o GPU è stato usato.

## Punto 5 — scelta A applicata, controllo pre-gold 0.2 PASS (13/8/2026)

Roberto ha approvato la scelta A per la sola spia diagnostica instabile. È
stato aggiunto nel laboratorio un replay/evaluator **0.2 affiancato**, senza
sovrascrivere la versione 0.1 e senza modificare batch live, raw, journal,
sigillo, marker, manifest, protocollo originario, expected o risultati.

Il confronto salvato-ricalcolato ammette un solo scarto possibile:
`adapter_metadata.implicit_actions_ignored`, presente da entrambe le parti e
booleano JSON esatto, soltanto nel record zero-based 80 legato alla sua
identità completa. Il report conserva `saved`, `replay` e il motivo. Tutti i
campi semantici e ogni altro metadata devono coincidere esattamente.

Il controllo pre-gold è **PASS**: 316/316 record, raw e righe journal
ricostruiti in ciascun processo; con `PYTHONHASHSEED=0` compare la sola
differenza autorizzata (`false`/`true`), con seed 2 le differenze sono zero;
quelle inattese sono sempre zero. Le prove finite sono 12/12, inclusi i due
versi booleani ammessi e tutti i rifiuti richiesti.

Batch, journal, checkpoint, marker, sigillo e freeze risultano integri. Il
freeze 0.2 sigilla un insieme chiuso di fonti e i tre file auto-improntati,
senza contenere gold e senza dipendere dal report o dai documenti. I pannelli
120/4/34 e i criteri approvati restano separati e invariati.

L'oracolo non è stato aperto e la valutazione non è stata eseguita. Il referto
è `internal/design/referto_pre_gold_evaluator_intento_v0_2_13_8_2026.md`;
l'evidenza macchina è
`candidate_v0_1/live_replay_pre_gold_report_v0_2.json`. Nessuna rete, nuova
GPU, endpoint, servizio, produzione o commit è stata usata. Il prossimo passo
è l'esecuzione offline esplicita dell'evaluator 0.2, che aprirà il gold soltanto
dopo aver ripetuto con successo lo stesso controllo pre-gold.

## Punto 5 — stato terminale del ciclo multidimensionale/offline (13/8/2026)

Gli sviluppi successivi alle sezioni storiche precedenti sono conclusi e
auditati. La sola misura live è stata consumata una volta e resta integra:
316/316 record. L'evaluator v0.3 corregge 62 falsi negativi di
rappresentazione senza falsi positivi e fissa il risultato canonico a A 75/120
e B 25/120, delta B−A −50, quindi `candidate_fail`. Il confronto raw storico
resta 29/120 e 9/120. I controlli tipizzati sono A 1/4 e B 0/4; barriere 0/3
per entrambi; controlli di sistema 2/2 per entrambi. Il pannello legacy resta
separato, A 25/34 e B 29/34.

La diagnosi multidimensionale individua due classi distinte: forma e semantica.
Nel braccio B ci sono 57 documenti invalidi e 3 errori tecnici; il vecchio
schema permetteva dettagli di archi e porte poi respinti dal validatore. Il
conteggio operativo iniziale era 139; il censimento aggregato completo trova
156 issue/archi problematici in 66 record B, 58 di dominanza e 98 di porta di
uscita. Separatamente restano confini di route e false astensioni: route esatta
A 51/84 e B 17/84; astensioni scorrette A 19 e B 26.

Il criterio di stallo è stato applicato: davanti a metriche centrali ferme o
regressive e a errori concentrati nella rappresentazione, si fermano i repair
locali e si riapre l'analisi su formato, semantica, controllo, sicurezza,
prestazioni e generalità. Non sono state introdotte eccezioni per query,
indici o hash del banco.

Il nuovo candidato `candidate_v0_2` implementa un'IR model-facing minima e un
compilatore deterministico per porte, percorsi, ordinali, esiti e
continuazioni. Esito offline: 30/30 prove ufficiali, 124/124 round-trip,
audit indipendente 36/36, circa 4,007 ms medi e zero difetti tecnici,
strutturali o di sicurezza nel perimetro finito. Il freeze auditato è
`7b68f9f62965ea24cf830ecc8c717fcdf6d6116ce3fa412082ed1f2287b4e992`.
Questa evidenza non misura l'accuratezza semantica live di v0.2.

I 120 casi sono da ora un campione aperto di regressione, ampliabile e
versionato, non una prova universale e non un elenco da ottimizzare caso per
caso. Il referto completo, inclusa la revisione avversariale, è
`internal/design/referto_analisi_multidimensionale_intento_13_8_2026.md`.

**Stato dell'ordine:** il primo punto incompleto resta il punto 5. La nuova
scelta di Roberto è pendente: autorizzare o non autorizzare una nuova misura
GPU affiancata per v0.2. Le opzioni saranno presentate da root. Questa sezione
non decide e non autorizza la misura; in questa chiusura non sono stati usati
GPU, rete, endpoint o servizi.

## Punto 5 — RUN1 candidate v0.2 terminale e RUN2 offline (13/8/2026)

La scelta pendente della sezione precedente è stata poi autorizzata da
Roberto. Il RUN1 candidate v0.2 è stato eseguito una sola volta e resta
integro: 316/316 record e POST accettati, zero retry, batch e seal completi,
replay pre-gold 316/316 con zero differenze inattese.

La metrica v0.3 dà, sul canonico, **A 79/120 e B 16/120**, delta −63 e
`candidate_fail`. I controlli tipizzati sono A 1/4 e B 0/4; il legacy resta
separato, A 25/34 e B 28/34. Nel totale B: 123 documenti invalidi, un errore
tecnico e 34 documenti validi.

Tutti i 123 invalidi hanno `FROM_SELF_OR_FORWARD`; il primo passo usa
`from:[0]` in ciascuno, mentre i 34 validi lo omettono. Le 157/158 radici
`operation_graph` e l'unico output troncato per enumerazione sono coerenti con
un prompt sbilanciato: un solo template operativo completo, esempio
consumatore isolato e radici controllo/rinuncia relegate ai cataloghi.

Il controllo mostra inoltre drift di esito: 79/120 contro 75/120 nella misura
precedente, pur con gate di ambiente integri. RUN2 riusa perciò esattamente
snapshot e pannello RUN1 e conserva A affiancato.

Unica modifica sperimentale autorizzata per RUN2: proiezione prompt bilanciata
e query-free. Schema, validator, compiler, adapter semantico, modello, limiti,
registry ed evaluator restano invariati. Il prerequisito BCP47 v0.2.1 è
separato e byte-invariante sull'intero workload. RUN2 è ancora disarmato:
nessuna nuova GPU, rete o inferenza è autorizzata da questa sezione.

Referto completo:
`internal/design/referto_run1_candidate_v0_2_intento_13_8_2026.md`.
Il punto 5 resta **incompleto** fino a misura RUN2, valutazione e revisione.

## Punto 5 — RUN2 candidate v0.3 terminale, RUN3 style-only (13/8/2026)

RUN2 è completo e integro: 316/316 record e POST accettati, zero retry, replay
pre-gold 316/316 e zero differenze inattese. La metrica v0.3 dà sul canonico
**A 79/120 e B 45/120**, delta −34 e `candidate_fail`; tipizzati A 1/4 e B
1/4; legacy separato A 25/34 e B 27/34.

Nel totale B ci sono 95 grafi validi, 33 astensioni valide, 30 documenti
invalidi e zero errori tecnici. Tutti gli invalidi hanno
`FROM_SELF_OR_FORWARD`; 29 colpiscono il primo passo. Rispetto a RUN1,
l'esattezza canonica sale 16→45 e gli invalidi totali scendono 123→30.

Restano 47 documenti canonici validi ma non esatti: 13 astensioni errate, 13
azioni errate su richieste fuori registro, 8 ragioni di astensione errate, 7
grafi con operazioni extra e 6 route errate. `false_action_avoided` scende
119→94. Non sono autorizzati repair né correzioni per singola query.

La proposta coverage-before-root è stata fermata prima del freeze ed è
archiviata come non eseguita. Il prossimo ciclo autorizzato è RUN3
**style-only**: S0 CURRENT v0.3 byte-identico, S1 breve prescrittivo ADR 0027,
S2 procedurale compatto. Le regole e le autorità sono identiche e mappate
riga per riga; cambiano soltanto forma e ordine di presentazione ammesso.

RUN3 userà 158 query × 3 bracci = 474 richieste seriali, con ordine latino
ABC/BCA/CAB. Schema, registry, validator, compiler, adapter, modello, limiti ed
evaluator restano invariati. S0 deve riprodurre esattamente raw, estrazione e
metriche aggregate B di RUN2 su tutti i 158 casi; anche un solo drift produce
`non_attributable_anchor_drift`. Il punto 5 resta incompleto; prima di GPU servono
fake/replay, preflight disarmato e due audit indipendenti.

Referto completo:
`internal/design/referto_run2_candidate_v0_3_intento_13_8_2026.md`.

## Punto 5 — RUN3_STYLE terminale, risultati solo descrittivi (13/8/2026)

Le sezioni precedenti sono storiche. Il disegno finale di RUN3_STYLE ha
aggiunto il controllo di sistema corrente ed e' quindi composto da quattro
bracci: A_SYSTEM_CURRENT, S0_CURRENT, S1_METNOS_SHORT e S2_PROCEDURAL. La
misura e' completa e integra: 158 casi per braccio, **632/632** POST accettati,
zero retry, batch e seal completi e replay pre-gold 632/632 senza differenze
inattese.

Le metriche descrittive canoniche sono, nello stesso ordine, **79/120,
44/120, 21/120 e 18/120**. I controlli tipizzati sono **1/4, 1/4, 0/4 e
1/4**; il legacy separato e' **25/34, 27/34, 26/34 e 27/34**.

La misura non e' attribuibile allo stile. A_SYSTEM_CURRENT riproduce RUN2-A su
149/158 raw ed estrazioni; S0_CURRENT riproduce RUN2-B su 120/158. Poiche' la
tolleranza era zero per entrambi, il verdetto obbligatorio e'
`style_measure_valid: false` / `non_attributable_anchor_drift`. Non esiste un
winner e nessuna pairwise puo' essere promossa a miglioramento causale.

La diagnosi esclude request builder, runner seriale, trasporto e adapter: le
richieste anchor sono byte-identiche 158/158 e modello, fingerprint, profilo e
token di prompt coincidono. Il drift nasce nel backend condiviso e stateful,
con due slot, continuous batching, prompt cache/reuse, speculative MTP, storia
cache diversa fra due e quattro bracci e altro traffico nella finestra. Il
protocollo non isolava questo stato pur richiedendo identita' byte-per-byte.

L'audit indipendente post-evaluation e' **20/20 PASS, 0 FAIL** sul calcolo e
sull'integrita', ma conferma il divieto di attribuzione. Referto:
`internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/reviews/functional_b/prompt_style_v0_1_run3_post_evaluation_audit.md`, SHA-256
`d48baa33e0787cf0d152e360fc9034123a328d83fe54b0f812b3bfd9790f3e40`.
Referto di chiusura:
`internal/design/referto_run3_prompt_style_intento_13_8_2026.md`.

Il primo punto incompleto resta il punto 5. E' aperta una nuova scelta
metodologica di Roberto: backend deterministico isolato con repliche esatte,
oppure confronto realistico con repliche bilanciate e criterio statistico
predefinito. Questa sezione non sceglie, non prepara RUN4 e non autorizza una
nuova GPU, rete o esecuzione.
