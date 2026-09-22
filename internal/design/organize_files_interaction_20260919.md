# organize_files — contesto e interazione

Data: 2026-09-19; aggiornato 2026-09-23. Stato: candidato di sviluppo
completo, firmato soltanto come sorgente di authoring e non pubblicato. La
certificazione e l'ammissione in esercizio appartengono a RM-0008. Questo
documento distingue i requisiti richiesti da Roberto dalle funzioni presenti
nel candidato.

## Requisiti espressi

Il nome pubblico ratificato è `organize_files`. L'organizzazione usa criteri
componibili su file regolari, un'anteprima congelata, conferma esplicita ed
effetti reversibili. I chiarimenti, la scelta dei file, la revisione dei casi
incerti e la conferma devono avvenire mediante form sia nella chat HTTP sia
su Telegram. Il testo del comando resta l'ingresso naturale al flusso.

La presenza di `confirmation_token` in una risposta non dimostra consenso.
L'applicazione richiede il completamento autenticato del form associato al
piano esatto. Il pianificatore non può approvare il proprio piano copiando un
valore dagli output. Annullamento, scadenza o invio ripetuto non eseguono il
ramo applicativo.

## Stato del contesto verificato nel codice

Dentro una richiesta composta, i consumatori possono usare gli output degli
step precedenti tramite i riferimenti tipizzati della pipeline
(`runtime/engine/executor.py`, `_resolve_from_step`). Una ripresa dopo un form
può reinserire gli step già eseguiti attraverso `resume_with_scratchpad` e
`seed_state` (`runtime/agent_runtime.py`, `run_turn` e `_run_engine`;
`runtime/orchestration.py`, `_process_resume_planner_with_dialog_values`).
Questo è il proseguimento esplicito di un lavoro sospeso.

Non è presente un recupero generale dei risultati di un comando concluso per
risolvere automaticamente «questi file» in un nuovo comando. La chat invia
richiesta e identificativi di conversazione/sessione, mentre la lettura dei
turni recenti alimenta la cronologia visibile
(`runtime/templates/chat.html`, invio a `/agent/turn/submit`;
`runtime/http_routes_agent.py`, `turns_recent`). L'identificativo della
conversazione non inserisce da solo gli output passati nel pianificatore.

Tutor conserva un solo scambio informativo recente dello stesso utente e
conversazione per 15 minuti, in memoria del processo
(`runtime/tutor/conversation.py`). Tale contesto non è una lista di file
utilizzabile dagli executor. Anche il ricordo della destinazione dispositivo
e il registro undo hanno finalità circoscritte.

Perciò «questi file» è un esempio valido quando l'insieme appartiene alla
pipeline corrente o a una continuazione esplicita. Tra comandi indipendenti
richiederebbe un meccanismo generale aggiuntivo: non va dichiarato già
implementato né introdotto come memoria privata di `organize_files`.
In assenza di un insieme identificabile, il form raccoglie sorgente e
selezione prima di costruire il piano.

Una fotografia abbreviata dello scratchpad non è sufficiente per una
mutazione completa: `_snapshot_scratchpad` può limitare le liste a 50 record.
La continuazione di ORGANIZE deve conservare l'intero insieme verificato nel
piano persistito e usarne il riferimento esatto, senza rieseguire la ricerca
né interpretare la parte mostrata come insieme completo.

## Flusso implementato nel candidato

La richiesta completa passa direttamente alla costruzione dell'anteprima.
Se mancano informazioni, un form mostra i soli campi necessari: sorgenti,
criteri, eventuale archivio di confronto e destinazione. I valori già espliciti
restano visibili; non occorre farli digitare di nuovo. Un criterio semantico
come il progetto richiede prima dati strutturati associati ai file. Il form
espone i casi incerti; l'organizzatore non inventa etichette mancanti.

Il form dell'anteprima mostra conteggi reali e rende consultabile l'elenco
completo delle azioni: sorgente, destinazione, copia conservata per ogni
duplicato, conflitti e file lasciati invariati. Un limite di visualizzazione
è dichiarato e non riduce il piano. Il controllo di applicazione è disponibile
soltanto per un piano completo e coerente con le scelte già espresse.

Il solo controllo che può applicare il piano è il form autenticato creato dal
runtime. La callback è monouso e riprende direttamente executor e argomenti
congelati, senza rieseguire query o planner. Il grant non è un argomento
inventabile dal pianificatore: il runtime lo emette durante la callback e lo
lega a digest del payload finale, generazione firmata dell'executor,
proprietario, attore, canale e turno. Modifica, annullamento, scadenza, replay,
canale o turno diverso non aprono il ramo applicativo.

La ricevuta conclusiva riporta gli effetti reali e l'accesso al ripristino.
Prima di ogni effetto l'executor scrive e sincronizza un journal write-ahead;
dopo ogni effetto ne registra l'identità osservata. La ripresa con lo stesso
token riconcilia il journal invece di ricostruire il piano. Anche il reverse è
write-ahead, conserva il digest byte-exact della ricevuta di commit e rende lo
stato `undone` terminale: un secondo apply è rifiutato e un secondo undo non
produce effetti.

Le operazioni locali sono ancorate a directory già aperte senza seguire
symlink. Gli spostamenti usano rename atomico no-replace e verificano device e
inode della sorgente e della destinazione. L'executor non crea gerarchie di
destinazione: una cartella mancante, un passaggio tra filesystem o una
cancellazione con hardlink falliscono chiusi in preview o prima dell'effetto.
Prima dell'applicazione prova nello stesso parent il round-trip di proprietario,
mode, timestamp e tutti gli xattr, inclusi gli ACL rappresentati come xattr;
se la piattaforma non può conservarli non applica il piano.

## Canali e consegna dell'esito

HTTP e Telegram usano lo stesso schema `form_only` e lo stesso stato pendente.
HTTP presenta il form nella chat. Telegram consegna esclusivamente un pulsante
con URL HTTPS dello stesso origin configurato; l'URL non può contenere
userinfo, path, query o fragment. Un invio testuale non vale come conferma.
Il form applica CSP `frame-ancestors 'self'` e usa `postMessage` solo verso lo
stesso origin.

Dopo che la callback ha persistito la ricevuta, il runtime consegna l'esito al
canale originario. La consegna Telegram usa un outbox durevole: un fallimento
esplicito torna ritentabile, mentre una morte di processo durante una send
diventa `ambiguous` terminale, perché Telegram non offre una chiave idempotente
che consenta di escludere il doppio invio.

Le prove di sviluppo coprono apertura form, binding e replay, callback
monouso, URL Telegram, consegna, applicazione, ricevuta e undo. RM-0008 deve
ripetere questi casi sul ramo integrato e svolgere l'accettazione isolata
HTTP/Telegram; questo candidato non è stato pubblicato, distribuito o avviato
nei servizi.

## Confini intenzionali

`organize` valuta e materializza una politica; `move` esegue destinazioni già
definite. `sort`, `group`, `classify` e `order` non sono sinonimi né fratelli
ammessi per richiamo implicito. Il runtime applica il contratto generale degli
effetti congelati e non contiene rami sul nome `organize_files`.

Il riferimento non ambiguo agli output completi di una query conclusa resta il
TODO di progetto `QUERY-OUTPUT-001`; non è implementato come memoria privata
dell'executor. L'anteprima corrente persiste sempre l'elenco completo, mentre
il limite `max_preview` riguarda solo la presentazione.
