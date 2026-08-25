# RM-0008 — Porta unica di nascita e ciclo controllato degli executor

> RM-0008 · stato `ready` · revisionata il 25 agosto 2026 · conservazione
> persistente · 33 correzioni adversarial approvate da Roberto · rilievi sul
> dossier risolti nel §21 · prove in
> `internal/reports/rm0008-adversarial-evidence-20260825.md` · implementazione
> autorizzata e iniziata: F0 completata, F1 successiva

## 1. Obiettivo

Ogni executor nuovo o modificato deve attraversare una sola porta runtime prima
di diventare operativo. La porta espone in `runtime/executor_birth.py` una sola
API pubblica:

```python
birth_executor(request: BirthRequest) -> BirthResult
```

Il modello può proporre e correggere un candidato, ma non può attestarne
l'origine, ampliare l'autorità, approvarlo, firmarlo o pubblicarlo. La Birth
Gate applica i controlli deterministici comuni; sottopone le revisioni non
fidate a revisione semantica e prove isolate; consegna a RM-0007 soltanto byte
ammessi; riserva preesercizio, feedback e riparazione automatica agli executor
sintetizzati idonei.

## 2. Stato verificato e fonti

L'implementazione deve riusare l'inventario RM-0002, l'Executor Standard, il
linter, i registri di capacità, collocazione e undo, il publisher atomico
RM-0007, la guardia del confine, il ciclo di vita Synt, il router dei modelli,
le cache, i lavori durevoli e l'audit.

Il codice presenta più chiamanti diretti del publisher. Evidenze, feedback,
invecchiamento e rollback non sono sempre associati alla generazione esatta.
Alcuni controlli Synt consentono erroneamente di proseguire quando falliscono o
sono disattivabili tramite ambiente. Il runner non isola completamente l'host e
non prova la terminazione di figli e nipoti. Alcuni percorsi pubblicano prima
delle prove. Alcune cache restano obsolete dopo modifiche al solo manifest.
Macchine a stati sovrapposte, riattivazione fuori porta e conservazione non
coordinata completano il quadro.

Il rapporto `internal/reports/rm0008-adversarial-evidence-20260825.md`, con un
riferimento al codice per ogni fatto, è parte normativa dell'input di sviluppo.
Il codice installato resta l'autorità sul comportamento corrente finché questa
roadmap non è implementata, certificata e distribuita.

## 3. Architettura e radice di fiducia

```text
produttore autorizzato
  -> ricevuta del produttore e candidato non operativo
  -> copia privata immutabile
  -> candidate_id, semantic_core_id, admission_context_id
  -> controlli obbligatori che proibiscono in caso di errore
  -> revisione, prove e approvazione applicabili
  -> pre-commit RM-0007 con generation_id esatto
  -> ricevuta di ammissione durevole
  -> generazione e puntatore RM-0007
  -> rilettura autenticata di contratto e codice
  -> preexercise, active oppure quarantined
```

Il loader continua a fidarsi del manifest firmato e della generazione
autenticata da RM-0007, come stabilisce ADR 0224. `AdmissionReceipt` non è una
seconda autorità crittografica. La sua assenza non rende invisibile una
generazione corrente già autenticata, ma blocca promozione, rollback,
riattivazione, quarantena, riparazione e ripetizione equivalente con
`generation_not_admitted`.

Una ricevuta persa si recupera soltanto tramite `reattestation`: un operatore
autorizzato emette una nuova `ProducerReceipt`; Birth acquisisce i byte correnti
e riesegue tutti i controlli applicabili. La procedura non sposta il puntatore e
non modifica la generazione. Un controllo non ripetibile, fallito o non
disponibile nega la riattestazione. Non è consentito derivare la ricevuta dalla
sola presenza del manifest.

## 4. Applicabilità e classificazione

`executor_origin` descrive l'origine stabile. `revision_authorship` descrive
l'autore della revisione. `revision_class` è calcolata dai byte e non è una
dichiarazione autorevole del chiamante.

| Caso | Birth | Revisione semantica | Preesercizio/frontier/riparazione |
|---|---:|---:|---:|
| core o builtin, revisione umana fidata | sì | no | no |
| qualunque origine, modifica prodotta da modello | sì | sì | solo se origine sintetizzata |
| origine importata o non fidata | sì | sì | no |
| sintetizzato Metnos | sì | sì | sì, se idoneo |
| sola localizzazione provata RM-0002/RM-0007 | sì | trasporto evidenza | no |

La precedenza è conservativa. `first_birth`, `reactivation_revision` e
`promotion_revision` derivano prima dallo stato. Altrimenti cambia il codice,
la lista dei file o una dipendenza: `code_revision`; cambia capacità, ambito,
effetto, undo, collocazione, piattaforma o sandbox: `authority_revision`; cambia
un altro campo tecnico: `contract_revision`. `localization_revision` è valida
soltanto con prova RM-0002/RM-0007 e nucleo semantico invariato.
`equivalent_republish` richiede candidato, nucleo, contesto, predecessore e
ricevuta identici. Quando cambiano più dimensioni, i controlli applicabili sono
l'unione, mai quelli della sola etichetta meno restrittiva.

L'approvazione umana non cambia origine o autore e non converte `uncertain` in
`aligned`.

## 5. Contratti chiusi

```text
RevisionClass = first_birth | code_revision | authority_revision |
  contract_revision | localization_revision | equivalent_republish |
  promotion_revision | reactivation_revision | reattestation
RevisionAuthor = model | human | importer | maintenance
CheckStatus = passed | failed | unavailable | not_applicable
SemanticVerdict = aligned | misaligned | uncertain
FailureReviewVerdict = false_feedback | repairable | misaligned | uncertain
BirthOutcome = admitted | preexercise | quarantined | rejected | needs_human
```

Ogni `CheckSpec` versionata contiene un predicato di applicabilità e il flag
`mandatory`, entrambi fissati dalla politica e non dal chiamante. Un controllo
obbligatorio e applicabile prosegue soltanto con `passed`. `failed` e
`unavailable` producono `rejected`; `not_applicable` richiede la prova che il
predicato è falso. Il publisher non viene chiamato.

`ProducerReceipt` contiene `schema_version`, `receipt_id`, `issuer_id`,
`executor_origin`, `revision_authorship`, `objective_hash`,
`candidate_source_id`, `issued_at`, `expires_at`, `nonce` e `authentication`.
È autenticata, monouso e non derivabile dal manifest.

`candidate_source_id` autentica l'insieme chiuso presente in una radice di
staging dedicata. La radice locale non entra nella ricevuta e non conferisce
autorità: serve soltanto a localizzare i byte attestati.

`BirthRequest` contiene `request_id`, `manifest_ref`, `expected_revision_id`,
`producer_receipt`, `actor`, `reason`, `approval_refs` e `operation_hint`.
Contiene inoltre `candidate_source_root`, percorso locale della staging root
dedicata. Il percorso non entra in alcuna identità ed è valido soltanto se la
copia chiusa produce il `candidate_source_id` della ricevuta. `operation_hint`
è informativo.

`CandidateSnapshot` contiene `private_root`, `ContractId`, `manifest_bytes`,
`language_state_bytes`, la mappa completa `code_files[path, bytes]`,
`predecessor_id`, i tre identificatori, origine, autore e `objective_hash`. Dopo
la copia ogni controllo, revisione o prova usa esclusivamente questa mappa; è
vietato ricostruire `<name>/<name>.py`, omettere helper o riaprire percorsi vivi.

`CheckResult` contiene `check_id`, `rule_version`, `status`, `error_code`,
`evidence_hash` e `redacted_detail`.

`SemanticReview` contiene `verdict`, `observed_effects`,
`undeclared_effects`, `reason`, `tests` e `confidence`. Passa soltanto un oggetto
completo con `verdict=aligned` e almeno un segnale indipendente applicabile:
oracolo deterministico, caso umano versionato o relazione metamorfica. Separare
ruolo e contesto del modello è obbligatorio, ma non crea indipendenza. Senza
segnale indipendente l'esito operativo è `uncertain` anche se il modello scrive
`aligned`.

`FailureReview` è legata all'esecuzione fallita esatta. `false_feedback` può
soltanto proporre un ripristino umano tramite confronto-e-scambio; `repairable`
produce una nuova `BirthRequest`; `misaligned` conserva la quarantena;
`uncertain` richiede un umano. Oggetto malformato o servizio indisponibile
conservano la quarantena.

`AdmissionReceipt`, indicizzata da `(ContractId, generation_id)`, contiene
versioni, `receipt_id`, i tre identificatori, predecessore, hash della ricevuta
del produttore, classe, mappa ordinata `check_id -> (rule_version, status,
evidence_hash)`, hash di revisione e approvazione, ciclo di vita approvato,
generazione, data, `kind=admission|reattestation` e autenticazione.

`BirthReport` è distinto dalla ricevuta di autorità. È append-only e gestito
dalla conservazione; registra provenienza, contratto, nome, classe, generazioni,
ogni controllo, riferimenti di produttore e revisore, prove, approvazione, firma,
pubblicazione, rilettura ed esito di ciclo di vita. Viene emesso anche per un
rifiuto con zero chiamate al publisher.

`ExecutionReceipt` in `StepLog` registra al dispatch: richiesta e turno,
riferimento autenticato alla query ridotta, hash e payload conservabile degli
argomenti, hash e payload ridotto dell'output, `ContractId`, nome,
`generation_id` esatto e timestamp. Il feedback deve riferire questa ricevuta;
un record incompleto produce `feedback_binding_invalid` senza quarantena.

## 6. Identità

Le identità usano SHA-256, campi delimitati dalla lunghezza e domini versionati.
I domini byte sono rispettivamente
`metnos.executor-birth.candidate/v1\0`,
`metnos.executor-birth.semantic-core/v1\0` e
`metnos.executor-birth.admission-context/v1\0`.

Il codec V1 rappresenta ogni valore come tag di tipo di un byte, lunghezza del
payload unsigned a 64 bit big-endian e payload. Mappe e array antepongono anche
la cardinalità unsigned a 64 bit. I tag di null, stringa UTF-8, intero con
segno, booleano, array e mappa sono distinti; il booleano non è un intero. Le
chiavi di mappa sono stringhe e si ordinano per byte UTF-8. I vettori golden del
codec e delle tre identità sono normativi e un loro cambiamento richiede una
nuova versione.

`candidate_id` comprende `ContractId.value`, la proiezione TOML tipizzata del
manifest priva del solo blocco runtime-owned `birth`, stato linguistico, tutti i
file di codice ordinati per percorso UTF-8, origine, autore e `objective_hash`.
Commenti, spazi e ordine TOML non cambiano l'identità; un cambiamento dei byte
linguistici sì quando cambia il valore analizzato. Firma, predecessore,
timestamp, percorsi assoluti, modello e audit sono esclusi. Il manifest sorgente
non può dichiarare il blocco `birth`: soltanto Birth può produrlo durante il
commit. La rimozione nella proiezione serve alla rilettura del risultato finale,
non permette al produttore di fornirlo.

Per `semantic_core_id`, Birth analizza TOML con il parser autorevole e costruisce
una proiezione tipizzata. `MANIFEST_FIELD_GRAMMAR_V1` è una grammatica ricorsiva
chiusa: enumera chiavi tecniche per ogni tabella, distingue mappe i cui nomi sono
dati (`properties`, fixture e risultati attesi) e non accetta estensioni
implicite. `LINGUISTIC_SURFACE_PATHS_V1` è il predicato versionato che seleziona
la `description` radice e ogni `description` localizzata nei nodi JSON Schema
ammessi sotto `args`; non è una lista derivata dal catalogo installato. La
proiezione rimuove soltanto tali superfici e il blocco runtime-owned Birth. Nome, ciclo di
vita, codice, schemi, capacità, ambito, effetti, undo, collocazione, piattaforma,
sandbox, affinità, prove e campi tecnici restano inclusi. Un campo sconosciuto
produce `semantic_core_unknown_field` e impedisce il trasporto dell'evidenza.

Le mappe ordinano le chiavi per byte UTF-8; gli array conservano ordine e
cardinalità. Stringhe, interi, booleani, array e mappe hanno tag distinti e
framing lunghezza-payload. Campo assente e campo vuoto differiscono. Le stringhe
non subiscono trim o normalizzazione Unicode implicita. Float, date e orari sono
rifiutati con `semantic_core_type_unsupported`. Commenti, spazi e ordine TOML
non cambiano il digest. Alias e default non vengono materializzati.

I percorsi di `code.files` sono relativi, POSIX, canonici e privi di `..`, link,
duplicati o collisioni di maiuscole. Tutti i byte entrano nel digest. La versione
della proiezione e della lista linguistica entra nel framing.

`admission_context_id` usa una struttura V1 chiusa con undici componenti
obbligatori, ciascuno formato da versione non vuota e digest SHA-256 canonico:
Standard, linter, vocabolario, registro di autorità, registro sandbox, catalogo
proprietà, runner, politica di revisione e liste ammesse di template, primitive
e dipendenze. La versione della struttura entra nel framing. Campi mancanti o
aggiuntivi sono rifiutati.
Qualunque modifica del contesto invalida revisione e approvazione: la prima
versione non tenta di classificare cambiamenti come permissivi.

## 7. Protocollo atomico

Sotto `catalog_admission_lock`, Birth autentica e consuma la ricevuta del
produttore, verifica predecessore e unicità, copia l'insieme chiuso in una
directory privata senza link e calcola le identità. Poi libera il blocco. Ogni
controllo legge soltanto la copia.

Ogni nuovo produttore consegna una staging root dedicata contenente esattamente
`manifest.toml`, `manifest.lang_state.json`, i file dichiarati da `code.files` e
le sole directory parent necessarie. Firma, file aggiuntivi, link simbolici,
reparse point, hard link, device, FIFO e socket sono vietati. I riferimenti a
codice condiviso esterno non sono ammessi nella staging: il produttore copia
tutti i file dichiarati. Durante la migrazione, una sorgente legacy che non può
provare questo envelope produce `candidate_envelope_unattested` e non ottiene un
falso esito positivo.

I controlli fuori blocco seguono questo ordine: identità e ciclo di vita; TOML,
Standard e schemi; vocabolario; chiusura file, contenimento, AST, import ed entry
point; capacità, ambito, effetti, undo, collocazione, piattaforma e sandbox;
RM-0002 e stato linguistico; prove e proprietà; non interferenza del routing;
revisione semantica; approvazione esatta. Il primo fallimento obbligatorio
interrompe il percorso.

### 7.1 Installazione recuperabile dell'albero di authoring

F4 introduce nel confine RM-0007 `commit_birth_snapshot()`. La primitiva riceve
il riferimento del manifest, la copia privata, il predecessore e l'autorizzazione
Birth. Sotto il blocco crea una directory sorella sullo stesso filesystem con
manifest, stato linguistico e tutti i `code.files`; rifiuta file extra, link,
reparse point e hard link non attestabili; scrive con permessi restrittivi,
rilegge i digest e sincronizza file e directory.

Prima della sostituzione scrive e sincronizza un journal autenticato `prepared`
con identità vecchia, nuova e azione di recupero. F4 adotta un solo meccanismo
portabile: ogni lettore dell'albero acquisisce un token di versione condiviso;
`commit_birth_snapshot()` acquisisce il token esclusivo per sostituzione,
rilettura e consegna a RM-0007. L'ordine è sempre `catalog_admission_lock`, token
di authoring, writer lock del contratto; nessun percorso può invertirlo. Il
lettore registra la versione prima e dopo la lettura e scarta il risultato se
cambia. La guardia censisce anche i lettori. Se un lettore non può usare il
token o la piattaforma non può sostituire la directory sorella, la primitiva
restituisce `authoring_atomic_install_unsupported` e non pubblica. Non è lecito
descrivere come atomica la sostituzione indipendente di più file.

Dopo la sostituzione rilegge dal percorso canonico e ricalcola candidato e
nucleo. Conserva il backup fino alla postcondizione pubblicata. Al crash, il
riconciliatore legge il journal: `prepared` senza puntatore ripristina il vecchio
albero; un puntatore alla nuova generazione completa rilettura ed epoca;
un'identità diversa da entrambe produce `authoring_recovery_ambiguous` e blocca.
Su Windows vale il limite RM-0007: atomicità rispetto al crash del processo su
NTFS, non durata dell'ultima voce in caso di perdita di alimentazione.

### 7.2 Ricevuta prima del puntatore

Non si prevede `generation_id` e non si firma fuori dal blocco. RM-0007 prepara
e firma i tre payload sotto i propri blocchi. `_commit_payloads_locked` conosce
l'identificativo esatto e possiede già il punto `precommit` prima di installare
la generazione e scrivere il puntatore.

`publish_technical_update` riceve per le pubblicazioni operative un callback
Birth sigillato. Il callback riceve contratto, generazione esatta e digest dei
payload, confronta le identità, scrive e sincronizza `AdmissionReceipt`. Un
fallimento lascia il puntatore invariato. Al ritorno RM-0007 riautentica i
payload, come già fa, poi installa e seleziona la generazione. Una ricevuta senza
puntatore è un residuo innocuo e riconciliabile. La guardia impedisce a chiamanti
operativi diversi da Birth di evitare o fornire il callback. La localizzazione
RM-0007 conserva la sola eccezione autorizzata.

Dopo il puntatore Birth rilegge generazione e codice, apre l'epoca e restituisce
successo. La ripetizione idempotente richiede lo stesso `request_id`, ricevuta
già consumata, candidato, nucleo, contesto, predecessore e operazione; restituisce
il precedente risultato soltanto con ricevuta valida e generazione coerente.
Ogni differenza produce `producer_receipt_replayed` o `commit_conflict`.

## 8. Revisione, proprietà e approvazione

`executor.birth.semantic_review` usa il router centrale, almeno il livello
`wise`, scadenza reale e contesto separato. Una politica versionata impone
`frontier` quando rischio, complessità o incertezza superano le soglie; se il
livello richiesto manca, restituisce `semantic_review_unavailable`, senza
ripiego silenzioso. Revisione e prove ricevono l'intera mappa chiusa dei file.

`executor.birth.failure_review` usa frontier soltanto con consenso dell'istanza
e contenuto ridotto. Se manca, mantiene la quarantena e notifica
l'amministratore. Una revisione locale può chiedere un umano, non riparare.

Il catalogo `runtime/executor_birth_properties.py` definisce per ogni proprietà
identificativo, versione, precondizione deterministica, proprietario, generatore,
fixture, oracolo, isolamento e obbligatorietà. Copre almeno output dichiarato
contro reale; cardinalità zero, uno e molti; limite zero e inferiore al totale;
troncamento; undo andata-ritorno; copia prima della cancellazione; coerenza tra
`entries` e `results`. Le proprietà non sostituiscono il segnale indipendente.

L'approvazione umana è obbligatoria per sintetizzati con nuovo codice,
comportamento o autorità e per ogni `uncertain`. È legata a candidato, nucleo,
contesto, ciclo di vita mostrato e scadenza. Registra un override separato e non
riscrive il verdetto. Riusa `approval_registry.py` e `channels/approval.py`.

## 9. Runner isolato

`runtime/test_runner.py` non esegue shell fornite dal modello. Costruisce
l'ambiente da una lista chiusa senza copiare quello host; usa directory privata;
nega rete, credenziali e percorsi personali; applica scadenze per fase e totale,
limiti di memoria e output, gruppo di processi privato, terminazione e drenaggio
limitati anche in `finally`.

Il risultato attesta PID/PGID o l'equivalente Windows e la terminazione di figli
e nipoti. Se non può provarla restituisce `process_termination_unattested`.
`bounded_subprocess.py` va esteso e non va presunto sufficiente per Windows,
directory, shell o smontaggio. La disciplina copre candidato, pytest di
riferimento ed equivalenza.

## 10. Ciclo di vita e preesercizio

```text
proposed -> synthesized -> preexercise -> active
                         -> quarantined
active -> quarantined | deprecated -> archived
```

Ogni transizione tra `preexercise`, `active` e `quarantined` è una nuova
revisione firmata RM-0007 con ricevuta e predecessore; non esistono flag laterali
per nome. `preexercise` sostituisce `promoted_grace`. Invecchiamento e ritiro
possono soltanto ridurre visibilità o autorità.

Sono sempre esclusi dal preesercizio: rete reale; credenziali, segreti e
metadata; `code:exec` e comandi generici; autorità di dialogo o input utente;
percorsi reali personali o sensibili; ambito illimitato; capacità o output che
possono riversare segreti in log, cache o canali. Nuove capacità partono non
idonee. `ReadOnly` è necessario ma non sufficiente.

Ogni cache del preesercizio è vietata salvo legame esplicito a
`(ContractId,generation_id,lifecycle)` e invalidazione su promozione, quarantena
o ritiro. Nessun piano o memo durevole ne riusa il risultato; record legacy o
privo del ciclo di vita equivale a mancato riscontro. Il compilatore durevole
accetta soltanto `active`.

F5 può iniziare dopo almeno cinque ammissioni tecniche reali, non simulate, da
almeno due produttori, tutte con ricevuta e rilettura coerente, zero difetti di
integrità o bypass aperti e due cicli consecutivi di routing senza divergenze
inspiegate. Le ripetizioni idempotenti non aumentano il conteggio. Ridurre la
soglia richiede una nuova decisione di Roberto.

Il feedback usa l'`ExecutionReceipt` esatta e un confronto-e-scambio sulla
generazione invocata. Prima pubblica la revisione `quarantined`, poi la rende non
selezionabile e accoda `failure_review` con chiave idempotente della ricevuta.
Se la generazione non coincide restituisce `stale_feedback` senza mutazioni. Se
l'accodamento fallisce, la quarantena resta e il lavoro è ripetibile. Una
riparazione è un nuovo candidato.

Rollback richiede destinazione ammessa e non in quarantena. Riattivazione passa
da Birth. Storia senza ricevuta non è rollbackabile.

## 11. Cache, epoche e durevoli

Una funzione comune produce l'identità di catalogo: `generation_id` RM-0007 o
fallback con dominio distinto per virtuali e legacy. La usano `tools_sig`,
`pool_sig` sull'intera famiglia, `catalog_epoch` e indici token, Bloom, trie,
trie_v2, FTS5 e semantici. L0, L1 e alternative persistono e confrontano
l'identità. Una firma legacy causa ricostruzione.

La migrazione crea `executor_epochs` con chiave primaria
`(contract_id,generation_id)` e campi: nome, fonte, stato, prima e ultima
osservazione, chiamate totali/riuscite/fallite, feedback positivi/negativi,
ultimo esito, inattività, override e motivo, `state_version`, creazione e
aggiornamento. Vincoli impediscono contatori negativi e booleani diversi da
0/1. Indici coprono `(name,state)`, `(state,last_used_at)` e `(source,state)`.

Lo schema iniziale è vincolante:

```sql
CREATE TABLE executor_epochs (
  contract_id TEXT NOT NULL, generation_id TEXT NOT NULL,
  name TEXT NOT NULL, source TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('current','deprecated','archived')),
  first_seen_at TEXT NOT NULL, last_used_at TEXT,
  total_calls INTEGER NOT NULL DEFAULT 0 CHECK(total_calls>=0),
  successful_calls INTEGER NOT NULL DEFAULT 0 CHECK(successful_calls>=0),
  failed_calls INTEGER NOT NULL DEFAULT 0 CHECK(failed_calls>=0),
  positive_feedback INTEGER NOT NULL DEFAULT 0 CHECK(positive_feedback>=0),
  negative_feedback INTEGER NOT NULL DEFAULT 0 CHECK(negative_feedback>=0),
  last_call_ok INTEGER CHECK(last_call_ok IN (0,1) OR last_call_ok IS NULL),
  inactivity_since TEXT, lifecycle_override TEXT, override_reason TEXT,
  historic_epoch_ref TEXT,
  state_version INTEGER NOT NULL DEFAULT 1 CHECK(state_version>=1),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(contract_id,generation_id), UNIQUE(name,generation_id)
);
CREATE INDEX idx_epochs_name_state ON executor_epochs(name,state);
CREATE INDEX idx_epochs_state_used ON executor_epochs(state,last_used_at);
CREATE INDEX idx_epochs_source_state ON executor_epochs(source,state);
CREATE UNIQUE INDEX idx_epochs_single_current
  ON executor_epochs(contract_id) WHERE state='current';
CREATE TABLE executor_epoch_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract_id TEXT NOT NULL, generation_id TEXT NOT NULL,
  event_seq INTEGER NOT NULL, ts TEXT NOT NULL, event_kind TEXT NOT NULL,
  source TEXT, prior_state_version INTEGER, new_state_version INTEGER NOT NULL,
  detail_json TEXT,
  UNIQUE(contract_id,generation_id,event_seq),
  FOREIGN KEY(contract_id,generation_id)
    REFERENCES executor_epochs(contract_id,generation_id) ON DELETE RESTRICT
);
CREATE TABLE executor_legacy_state (
  legacy_id INTEGER PRIMARY KEY AUTOINCREMENT,
  legacy_name TEXT NOT NULL, legacy_table TEXT NOT NULL,
  legacy_row_json TEXT NOT NULL,
  resolution TEXT NOT NULL DEFAULT 'unresolved'
    CHECK(resolution IN ('unresolved','attested','discarded')),
  migrated_at TEXT NOT NULL
);
```

`executor_epoch_history` contiene identificatori esatti, sequenza per epoca,
data, evento, versione precedente e nuova e dettaglio JSON redatto. Ogni mutazione
usa `BEGIN IMMEDIATE`, confronto su `state_version`, incremento e storia nella
stessa transazione; zero righe produce `epoch_conflict`.

La migrazione transazionale conserva le tabelle legacy e copia le righe name-only
in `executor_legacy_state` con `resolution=unresolved`, senza inventare contratto
o generazione. Verifica conteggi e digest e registra `PRAGMA user_version`.
Il loader non applica stato legacy a generazioni RM-0007.

Una nuova generazione apre sempre un'epoca con contatori zero. Un rollback apre
una nuova epoca corrente collegata all'epoca storica in sola lettura; non eredita
contatori o override. Il riferimento storico resta nel rapporto di nascita.
Nessun feedback o invecchiamento per nome colpisce un successore.

Il ponte durevole conserva la propria verifica per tentativo. Restituisce
`execution.runner_absent`, `execution.dormant`, `execution.retired` o
`execution.quarantined` senza invocare l'executor.

## 12. Conservazione per raggiungibilità

Il collector usa un grafo persistente versionato. I nodi comprendono generazioni,
ritiri, ricevute, copie candidate, proposte, blob, prove, approvazioni, feedback,
revisioni, epoche, segmenti audit e ricevute minime. Archi tipizzati collegano
selezione, predecessore, ammissione, provenienza, prove, approvazione, esecuzione,
revisione, riparazione, destinazione rollback e riferimenti audit.

Le chiavi persistenti minime sono:

```sql
retention_nodes(node_type, node_id, object_version, state,
                created_at, closed_at, eligible_after,
                PRIMARY KEY(node_type,node_id))
retention_edges(source_type, source_id, edge_type, target_type, target_id,
                state, edge_version, created_at, closed_at,
                PRIMARY KEY(source_type,source_id,edge_type,target_type,target_id))
retention_runs(run_id PRIMARY KEY, graph_version, root_version,
               started_at, state)
retention_candidates(run_id, node_type, node_id, observed_version,
                     eligible_after, status,
                     PRIMARY KEY(run_id,node_type,node_id))
```

Il modulo del collector definisce enum chiusi per tipi e stati. L'inserimento di
un arco verifica nella stessa transazione l'esistenza dei due nodi; riferimenti
pendenti silenziosi non sono ammessi.

Sono radici: puntatore corrente; predecessore richiesto da un ritiro; generazioni
ammesse e rollbackabili; feedback, revisioni, approvazioni o audit aperti; lavori
in corso; epoca corrente; finestre non scadute e blocchi legali.

Una run registra `run_id`, versione del grafo e candidati con versione osservata.
La marcatura seleziona soltanto nodi irraggiungibili, chiusi e oltre TTL. Prima
della cancellazione verifica con confronto-e-scambio versione, nuovi archi e
puntatore. Scrive e sincronizza una ricevuta minima autenticata e non personale,
poi elimina dalle foglie. Crash e doppia esecuzione riprendono dallo stato del
singolo oggetto senza duplicare ricevute.

Una generazione RM-0007 si elimina soltanto se non corrente, non richiesta da un
ritiro, non rollbackabile e non referenziata. Audit e `proposals_cleanup` non
possono cancellare autonomamente oggetti del grafo. Gli errori sono
`retention_referenced`, `retention_window_open`, `retention_state_changed` e
`retention_partial`.

## 13. File e migrazione dei chiamanti

Nuovi: `runtime/executor_birth.py`, `runtime/executor_birth_receipts.py`,
`runtime/executor_birth_properties.py`.

Da modificare: confine RM-0007 (`runtime/contract_store.py`, `runtime/sign.py`,
`runtime/manifest_inventory.py`, `runtime/contract_boundary_guard.py`);
Synt/importazione (`runtime/synth_request.py`, `runtime/synt.py`,
`runtime/synt_multistage.py`, `runtime/jobs/promoter*.py`,
`runtime/skill_admission.py`, `runtime/cli/skills_cli.py`); revisione/runner
(`runtime/synt_stage6_verify.py`, registro workload, `runtime/test_runner.py`,
`runtime/bounded_subprocess.py`); produttori (`change_applier_extend.py`,
`change_rollback.py`, `stack_reconcile.py`, generatore builtin e installer);
ciclo/cache (`loader.py`, `generated_executor_contract.py`,
`engine/cache_validity.py` e strategie dipendenti dal catalogo); stato
(`agent_runtime.py`, `turn_feedback.py`, `executor_aging.py`); conservazione
(`proposals_cleanup.py`, `audit_jsonl.py`, archivi promoter).

Ritirare dal prodotto `METNOS_SYNT_STAGE6_DISABLED`,
`METNOS_STAGE6_VERIFY_IMPORTED`, `METNOS_SMOKE_AT_IMPORT`,
`METNOS_SYNT_LINT_DISABLED`, `METNOS_STAGE6_VERIFY_FAKE`, `LLM_VERIFY_MODELS`.
I test usano iniezione di dipendenze.

Migrare a Birth: cambiamenti e rollback, richieste/approvazioni Synt, promoter,
skill import/reinstall, restart `sign_first`, generatore builtin e installer.
Eccezioni: sola localizzazione RM-0007, ritiro riduttivo, cutover una tantum e
strumenti offline che non rendono operativo un executor.

## 14. Errori minimi

Input: `birth_request_invalid`, `producer_receipt_invalid|expired|replayed`,
`origin_authorship_mismatch`, `feedback_binding_invalid`.

Copia/identità: `candidate_path_invalid`, `candidate_file_missing|extra`,
`candidate_link_forbidden`, `candidate_changed`, `snapshot_unavailable`,
`candidate_envelope_unattested`, `semantic_core_unknown_field`,
`semantic_core_type_unsupported`.

Controlli: `revision_class_invalid`, `evidence_obsolete`,
`evidence_transport_forbidden`, `admission_context_changed`,
`approval_required|invalid|expired`, `contract_nonconformant`,
`lifecycle_missing`, `transition_invalid`, `capability_unknown`,
`scope_forbidden`, `code_digest_mismatch`,
`semantic_review_failed|unavailable|uncertain`,
`metamorphic_failed|unavailable`, `test_environment_unavailable`,
`process_termination_unattested`.

Commit: `commit_conflict`, `receipt_commit_failed`, `publish_failed`,
`authoring_atomic_install_unsupported`, `authoring_recovery_ambiguous`,
`reload_authentication_failed`, `reload_code_mismatch`, `epoch_conflict`,
`generation_not_admitted|quarantined`, `promotion_not_eligible`,
`stale_feedback`, `reactivation_base_invalid`.

Soltanto errori dichiarati ripetibili dal contratto possono impostare
`retryable=true`. Dettagli e audit devono essere redatti da segreti e dati
personali.

## 15. Fasi non permutabili

Ogni fase produce un commit Git autonomo e inizia dopo i criteri della precedente.

- **F0, caratterizzazione e riparazioni — completata il 25 agosto 2026:** congelare inventario e prove rosse dei
  bypass; registrare ricevute e rimuovere i 12 orfani; creare fixture storiche;
  rifiutare verdetti non tipizzati; ritirare le esclusioni d'ambiente convertendo
  i test all'iniezione. Completata quando inventario e ricevute sono completi,
  l'ambiente non cambia gli esiti e le prove interessate passano.
- **F1, tipi e identità in osservazione:** implementare contratti, copia, identità
  e ricevute senza pubblicare. Completata con prove unitarie verdi e zero chiamate
  al publisher.
- **F2, runner e revisione:** implementare isolamento, terminazione, revisore,
  proprietà e approvazione. Completata quando host invisibile, figli terminati su
  Linux/Windows e indisponibilità provocano rifiuto.
- **F3, Birth in osservazione:** eseguire classificazione e controlli senza
  pubblicare. Completata quando ogni divergenza è spiegata e il publisher resta
  a zero.
- **F4, commit Birth:** introdurre installazione con journal, callback pre-commit
  e rilettura. Prima di rendere vincolante la guardia, entrare in manutenzione,
  congelare l'inventario e riattestare tramite il protocollo del §3 tutte e sole
  le generazioni correnti autenticate. Verificare che ogni puntatore corrente
  abbia una ricevuta valida; un errore interrompe il cutover e conserva i vecchi
  proprietari. Soltanto dopo chiudere i bypass e migrare, per ultimi, rollback,
  riattivazione, riavvio e installer. Completata con proprietario unico, nessuna
  generazione corrente priva di ricevuta, prove di crash, rilettura esatta e
  ripetizione idempotente. F6 tratta la storia non corrente, che resta non
  rollbackabile fino a eventuale ammissione esplicita.
- **F5, preesercizio/cache/epoche:** inizia solo dopo la soglia del §10; implementa
  ciclo, identità cache, StepLog, feedback ed epoche. Completata quando A e B
  invalidano ogni cache, feedback obsoleto non muta B, riparazione nasce pulita e
  preesercizio non entra nei durevoli.
- **F6, conservazione e certificazione:** implementare il grafo e verificare che
  il cutover delle generazioni correnti sia già concluso in F4. Non riammettere
  nuovamente le correnti. Una generazione storica viene sottoposta a Birth
  soltanto in seguito a una richiesta esplicita di renderla rollbackabile;
  altrimenti resta priva di ricevuta e non selezionabile. Completata con suite
  Linux/Windows, due cicli di routing, prova reale controllata e
  ADR/norme/documentazione/deploy allineati.

## 16. Prove obbligatorie

Le prove devono coprire: dominio e canonicalizzazione di ogni identità; campi
assenti/vuoti e tipi TOML; tutti i file, link, extra, mutazioni e gare; replay,
tamper, scadenza, tripla non corrispondente, riattestazione e ricevuta orfana;
isolamento dell'host e terminazione Linux/Windows; crash in ogni punto tra copia,
journal, ricevuta, generazione, puntatore, rilettura ed epoca; tutte le famiglie
di cache e un fratello non scelto; feedback A dopo B; nuova epoca e rollback;
migrazione con conteggi/digest; riferimenti aperti, gare mark/sweep e doppio
sweep; alias, wrapper, import dinamici e subprocess del confine.

Le prove delle epoche devono tentare di inserire due righe `current` per lo
stesso `contract_id` e verificare che l'indice
`idx_epochs_single_current` rifiuti la seconda nella stessa transazione. Le
prove del cutover F4 devono includere zero, una e molte generazioni correnti,
una ricevuta già presente, un fallimento a metà censimento e una ripetizione
equivalente immediatamente successiva alla chiusura dei vecchi chiamanti.

Ogni gruppo di sviluppo deve aggiungere nel proprio commit fixture, azione,
risultato ed errore atteso nel file di test più vicino al modulo modificato. F6
esegue inoltre suite completa, due cicli di routing e una prova reale non
distruttiva.

## 17. Completamento e condizioni di arresto

`implemented` richiede: tutti i percorsi attraversano Birth; zero bypass; identità
esatte; errori chiusi; nessuna autorità al modello; revisione e segnale
indipendente corretti; runner e processi attestati; approvazione esatta;
preesercizio soltanto idoneo; feedback, stato e rollback per generazione; cache
invalidate; conservazione per raggiungibilità; rilettura e crash idempotenti;
cutover, piattaforme, routing, prova reale, ADR e documentazione allineati.
`closed` richiede anche installazione distribuita provata e zero residui.

Un agente di codifica deve fermarsi indicando file, simbolo, informazione
mancante e requisito bloccato se trova: schema o enum non definito; produttore
senza emittente; campo tecnico non classificabile; piattaforma senza prova di
terminazione o installazione; impossibilità di CAS o ricevuta prima del
puntatore; storia non attestata; nuova capacità, autorità, eccezione o chiamante;
necessità di cambiare non-obiettivi o EXEC-BIND. Non deve inventare fallback,
compatibilità per nome o default permissivi.

## 18. Non-obiettivi

Non sostituire o duplicare RM-0002, RM-0007 o EXEC-BIND-001; è ammessa solo
l'estensione pre-commit sigillata. Non creare package manager o secondo
catalogo. Non riesaminare semanticamente tutta la storia. Non introdurre regole
solo per nome, dominio o lingua. Non dare al modello chiavi, publisher o dati
reali. Non fondare la sicurezza su un solo parere. Non accumulare senza limite
revisioni rifiutate.

## 19. Tracciabilità delle 33 correzioni

| Correzioni | Requisito |
|---|---|
| 1-2,18 | origine, autore, classe e revisione import/model (§4-5) |
| 3-5,21,24 | ambiente, ciclo, restart e reactivation (§7, §10, §13, F0) |
| 6 | consenso e degradazione frontier (§8) |
| 7-8,17,19 | tre identità, promozione, copia e CAS (§6-7) |
| 9-10,27-28,32 | preesercizio, feedback, quarantena, epoche (§10-11) |
| 11,22-26,31,33 | revisione, segnali indipendenti, proprietà, runner (§5, §8-9) |
| 12 | framing e guardia (§6-7, §13) |
| 13,16 | barriera durevole e diagnostica (§11) |
| 14 | stato incoerente, orfani e soglia reale (§10, F0, F5) |
| 15,30 | identità comune cache/prefiltri (§10-11) |
| 20 | ricevute per retry, rollback, quarantena e riattestazione (§3, §5, §7, §10) |
| 29 | grafo di raggiungibilità (§12) |

## 20. Aggiornamenti normativi richiesti

F6 deve emendare ADR 0224 in tre punti: la ricevuta governa le transizioni ma
non è autorità del loader; importazioni non fidate e revisioni da modello
ricevono revisione semantica, mentre preesercizio/frontier/riparazione restano
solo Synt; `promoted_grace` è sostituito da `preexercise`.

## 21. Risoluzione della revisione del dossier

| Rilievo | Risoluzione |
|---|---|
| R1 | Radice di fiducia, perdita e riattestazione definite nel §3. |
| R2 | Eliminata la previsione della generazione; usato il pre-commit RM-0007 (§7). |
| R3 | Ripristinata soglia numerica di ammissioni reali (§10, F5). |
| R4 | Rapporto probatorio reso fonte normativa separata (§2). |
| R5 | Roberto ha approvato le 33 correzioni; sviluppo ancora non autorizzato. |
| R6 | Prosa riscritta in italiano; inglese solo per identificatori reali. |
| R7 | Definiti canonicalizzazione, installazione, epoche e grafo (§6-7, §11-12). |
| R8 | Verdetto rigoroso e ambiente in F0; runner multipiattaforma resta in F2. |

Il controllo di regressione ha inoltre ripristinato: segnale indipendente,
contratto del riesame dei fallimenti, feedback legato all'esecuzione, divieti del
preesercizio, mappa completa dei file, escalation frontier, `BirthReport`,
classificazione conservativa, idempotenza e transizioni firmate.

### 21.1 Verifica della risoluzione

Le otto risoluzioni sono state ricontrollate sul testo e sul codice. `R1`-`R8`
sono chiuse. Due conferme utili a chi implementa:

- il punto di aggancio del §7.2 **esiste già**: `_commit_payloads_locked`
  (`runtime/contract_store.py:3211-3265`) conosce l'identificativo esatto e
  invoca `precommit(desired)` subito prima del commit;
- la firma attuale passa un solo argomento, ma l'unico uso odierno
  (`runtime/contract_store.py:3996`) cattura il contesto per chiusura: Birth può
  fare lo stesso, quindi il callback sigillato non richiede di cambiare la firma
  pubblica di RM-0007.

Restano due punti puntuali, entrambi con correzione già scritta.

**P1 — finestra F4-F6 sulle generazioni prive di ricevuta. Alto, risolto.**
Il §3 stabilisce che l'assenza di ricevuta blocca anche la ripetizione
equivalente. Le 122 generazioni correnti dell'installazione non hanno ricevuta e
la ricevono soltanto in F6. Fra F4, che rende Birth unico proprietario, e F6
ogni ripubblicazione idempotente di un executor invariato — reinstallazione di
una skill, riesecuzione dell'installer, riavvio con firma, ramo di ritentativo
di `change_applier_extend` — non è classificabile: non è `equivalent_republish`
perché manca la ricevuta del predecessore, e nessun campo tecnico è cambiato.
Per il §17 l'agente di codifica deve fermarsi («storia non attestata»).
La riattestazione delle sole generazioni correnti è ora il primo passo del
cutover di proprietà in F4. Avviene prima che la guardia chiuda i vecchi
chiamanti; il cutover si interrompe se il censimento non è completo. Non esiste
un'eccezione temporanea che trasformi implicitamente una ripetizione in
`reattestation`.

**P2 — vincolo mancante nello schema dichiarato vincolante. Medio, risolto.**
`executor_epochs` ammette due righe `current` per lo stesso contratto: la chiave
primaria è `(contract_id,generation_id)` e nessun indice limita lo stato. Poiché
il §11 dichiara lo schema vincolante, verrebbe realizzato così com'è, e
invecchiamento, override e diagnostica non avrebbero un'epoca corrente unica cui
riferirsi. Lo schema del §11 contiene ora l'indice parziale univoco
`idx_epochs_single_current`; il §16 impone la prova transazionale che rifiuta
una seconda epoca corrente dello stesso contratto.

P1 e P2 sono chiusi. Il revisore del §21 concorda sull'intero documento e non
ha altri rilievi: l'analisi è terminata e resta soltanto l'autorizzazione allo
sviluppo.

## 22. Registro

| Data | Stato | Evento |
|---|---|---|
| 2026-08-25 | `active` | Definita Birth Gate e svolti cinque giri adversarial. |
| 2026-08-25 | `active` | Convergenza su 33 correzioni. |
| 2026-08-25 | `ready` | Roberto approva; dossier consolidato per review esterna. |
| 2026-08-25 | `active` | Review del dossier: due bloccanti e regressioni di consolidamento. |
| 2026-08-25 | `ready` | Risolti R1-R8 e ripristinati i requisiti persi; nuova review esterna richiesta. |
| 2026-08-25 | `ready` | Verifica della risoluzione (§21.1): R1-R8 chiuse; restano P1 (riattestazione delle generazioni correnti in F4) e P2 (vincolo di epoca corrente unica); con quelle due l'analisi è concordata. |
| 2026-08-25 | `ready` | P1 e P2 risolti: riattestazione corrente prima della chiusura dei chiamanti in F4 e indice univoco dell'epoca corrente; analisi adversarial concordata. |
| 2026-08-25 | `active` | Roberto autorizza lo sviluppo. F0 avviata: controlli Synt e importazione resi fail-closed, variabili d'ambiente ritirate e documentazione allineata; guardia Birth e quarantena firmata degli orfani ancora aperte. |
| 2026-08-25 | `active` | F0 completata: la guardia congela 24 chiamanti da migrare; L5/L6 non espongono bypass; 12 residui non ammessi sono stati censiti, attestati con ricevute Ed25519 e spostati senza cancellazione nella quarantena sullo stesso filesystem. La verifica successiva trova zero residui. F1 è la fase successiva. |
