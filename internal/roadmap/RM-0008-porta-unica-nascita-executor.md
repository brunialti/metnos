# RM-0008 — Porta unica di nascita e ciclo controllato degli executor

> RM-0008 · stato `active` · avanzamento verificato il 30 agosto 2026 ·
> conservazione persistente · gruppi 1-5 della ripresa (§23.6), G6-A, G6-B1,
> G6-B2 e G6-B3 completati; G6-C1 e G6-C2 sono certificati. La cella C2
> installa le sole unità private già ribassate e firmate ed esegue
> `daemon-reload` nella VM root usa-e-getta; il commit pubblico `bdd58a5` è
> verde su tutti i nove job nel run GitHub Actions `33318421582`. La fotografia
> systemd viva di G6-B3 resta
> non autorizzante ed è pubblicata nel commit `5b2b3e6`, certificato con tutti
> gli otto job Linux/Windows verdi nel run GitHub Actions `33314651224`.
> RM-0005 è chiuso; `closed_build_enforcement()` resta `False`. Il prossimo
> incremento minimo è G6-C3, diniego e ammissione reali nella stessa cella.
> G6-B4, G6-C3/C4, G6-D e F4-F6 non sono ancora completati.

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

Il payload wire V1 della ricevuta del produttore ha esattamente tali dieci
campi esclusa `authentication`, più `authentication` come undicesimo campo.
`schema_version` è l'intero `1`; hash e identificatori di sorgente sono digest
SHA-256 canonici. `nonce` è composto da 32 cifre esadecimali minuscole. I tempi
sono UTC nella sola forma `YYYY-MM-DDTHH:MM:SSZ`, senza frazioni: scadenza
strettamente successiva all'emissione, emissione non oltre 30 secondi nel futuro
e verifica anteriore alla scadenza. `receipt_id` è SHA-256 del payload privo di
`receipt_id` e autenticazione, con dominio
`metnos.executor-birth.producer-receipt-id/v1\0`. L'autenticazione firma il
payload completo privo della sola autenticazione con dominio
`metnos.executor-birth.producer-receipt/v1\0` ed ha esattamente
`algorithm="ed25519"`, `key_id` e `signature` Base64 canonico.

Il registro emittenti V1 associa ogni `issuer_id` a una o più chiavi nominate
per rotazione. Ogni voce contiene `key_id`, chiave pubblica Ed25519, insieme non
vuoto delle origini consentite e insieme non vuoto degli autori consentiti;
`issuer_id` e `key_id` identificano univocamente la voce. La verifica richiede
che origine e autore ricadano entrambi nella stessa voce. Il registro è un
componente dell'`admission_context_id`; nessuna chiave globale o dichiarazione
del manifest può sostituirlo.

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

Il payload wire V1 di `SemanticReview` è JSON UTF-8 canonico senza chiavi
duplicate, spazi esterni, preamboli o suffissi e contiene esattamente i sei
campi precedenti. `verdict` è `aligned|misaligned|uncertain`; le due liste di
effetti contengono al massimo 32 stringhe non vuote, senza NUL e di massimo 256
byte UTF-8 ciascuna; `reason` è una stringa non vuota, senza NUL e di massimo
2.000 byte UTF-8; `confidence` è un intero, distinto da booleano, tra 0 e 100.
`tests` contiene al massimo 16 oggetti con esattamente `test_id`,
`kind=example|metamorphic` e `description`; identificativo e descrizione sono
stringhe non vuote senza NUL, rispettivamente di massimo 128 e 1.000 byte
UTF-8, e `test_id` non può duplicarsi. Sono proposte non autorevoli e non
contengono shell, fixture o autorità. `aligned` richiede almeno un effetto
osservato, nessun effetto non dichiarato e una ragione non vuota.

Il revisore riceve `candidate_id`, `admission_context_id`, manifest, stato
linguistico e l'intera mappa ordinata dei file. Un solo secondo tentativo è
ammesso esclusivamente dopo un payload malformato e usa lo stesso workload e
lo stesso livello. Errori di trasporto, scadenza o indisponibilità non vengono
ripetuti e producono `semantic_review_unavailable`.

`IndependentEvidence` V1 contiene esattamente `evidence_id`, `evidence_version`,
`kind=deterministic_oracle|human_case|metamorphic_relation`, `owner_id`,
`candidate_id`, `admission_context_id`, `status` ed `evidence_hash`. Identità e
hash sono digest SHA-256 canonici; versione e proprietario sono stringhe non
vuote senza NUL. Lo stato è `passed|failed|unavailable|not_applicable`. Soltanto
un'evidenza `passed`, legata allo stesso candidato e contesto, con versione
presente nella politica di revisione e proprietario registrato non coincidente
con il modello generatore soddisfa l'indipendenza. Assenza o non applicabilità
rendono operativo il verdetto `uncertain`; un binding diverso produce
`evidence_obsolete`. In F2 l'hash della revisione e dell'evidenza è obbligatorio
in memoria; la persistenza diventa obbligatoria in F3/F4.

`FailureReview` è legata all'esecuzione fallita esatta. `false_feedback` può
soltanto proporre un ripristino umano tramite confronto-e-scambio; `repairable`
produce una nuova `BirthRequest`; `misaligned` conserva la quarantena;
`uncertain` richiede un umano. Oggetto malformato o servizio indisponibile
conservano la quarantena.

Il payload wire V1 di `FailureReview` è JSON UTF-8 canonico, senza chiavi
duplicate, spazi esterni, preamboli o suffissi. Contiene esattamente
`verdict`, `execution_receipt_id`, `execution_receipt_hash`, `candidate_id`,
`generation_id`, `failure_evidence_hash`, `reason`, `repair_objective` e
`confidence`. I tre hash e `execution_receipt_id` sono digest SHA-256
canonici; `generation_id` è una stringa non vuota senza NUL di massimo 128 byte
UTF-8. `reason` è non vuota, senza NUL e di massimo 2.000 byte UTF-8;
`confidence` è un intero, distinto da booleano, tra 0 e 100.
`repair_objective` è `null` salvo per `repairable`, per cui è una stringa non
vuota senza NUL di massimo 1.000 byte UTF-8. È soltanto l'obiettivo ridotto di
una nuova richiesta: non può contenere codice, shell, percorsi, credenziali,
fixture, patch, identificativi di workload o autorità di pubblicazione.

La richiesta al revisore contiene gli stessi identificativi e hash, il codice
di errore tipizzato, l'output e gli argomenti già ridotti secondo la politica di
`ExecutionReceipt`, e nessun byte vivo riaperto dall'host. Il workload è fisso;
non sono ammessi tentativi successivi, estrazione tollerante o ripiego di
livello. Prima dell'invocazione Birth verifica un consenso d'istanza valido e
legato a `executor.birth.failure_review`; in sua assenza restituisce
`failure_review_consent_required`, conserva la quarantena e notifica
l'amministratore. La risposta passa soltanto se tutti i binding coincidono
esattamente con la richiesta. Nessun verdetto muta direttamente la generazione:
ogni eventuale azione successiva usa il proprio protocollo CAS e la propria
autorità.

`AdmissionReceipt`, indicizzata da `(ContractId, generation_id)`, contiene
versioni, `receipt_id`, i tre identificatori, predecessore, hash della ricevuta
del produttore, classe, mappa ordinata `check_id -> (rule_version, status,
evidence_hash)`, hash di revisione e approvazione, ciclo di vita approvato,
generazione, data, `kind=admission|reattestation` e autenticazione.

Il payload wire V1 di `AdmissionReceipt` ha esattamente: `schema_version=1`,
`policy_version`, `identity_version=1`, `receipt_id`, `contract_id` nella forma
canonica `ContractId.value`, `generation_id`, `candidate_id`,
`semantic_core_id`, `admission_context_id`, `predecessor_id` nullable,
`producer_receipt_hash`, `revision_class`, `check_results`,
`semantic_review_hash` nullable, `approval_hash` nullable,
`birth_request_id`, `authoring_journal_hash`, `approved_lifecycle`, `kind`,
`issued_at` e `authentication`. `birth_request_id` e
`authoring_journal_hash` sono digest SHA-256 canonici e obbligatori per
`kind=admission|reattestation`; permettono di ricostruire e provare la stessa
transazione dopo la rimozione del journal operativo. Il codec F1 storico senza
questi campi è sostituito in F4 e non è accettato dal commit operativo.
`approved_lifecycle` è `active|preexercise|quarantined`. Un valore non
applicabile per revisione o approvazione è `null`, distinto da un digest.
`check_results` è una mappa senza duplicati indicizzata da `check_id`; ogni
valore ha esattamente `rule_version`, `status` ed `evidence_hash`. Una ricevuta
di ammissione può contenere soltanto `passed|not_applicable`: `failed` o
`unavailable` non sono rappresentabili come ammissione.

`receipt_id` usa il dominio
`metnos.executor-birth.admission-receipt-id/v1\0`; firma e autenticazione usano
`metnos.executor-birth.admission-receipt/v1\0` e lo stesso envelope Ed25519
chiuso della ricevuta del produttore. `issued_at` usa la medesima forma UTC. In
F1 emissione e verifica sono codec puri con chiavi di prova: nessuna ricevuta è
persistita, consumata o consultata dal loader. L'emittente Birth operativo e lo
store univoco vengono introdotti soltanto nelle fasi di commit.

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
segno in complemento a due minimo, booleano, array e mappa sono rispettivamente
`n`, `s`, `i`, `b`, `a` e `m`; il booleano non è un intero. Le
chiavi di mappa sono stringhe e si ordinano per byte UTF-8. I vettori golden del
codec e delle tre identità in
`tests/runtime/executors/test_executor_birth_identity.py` sono normativi e un
loro cambiamento richiede una nuova versione.

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

Il contratto F4 è chiuso come segue. `commit_birth_snapshot()` riceve la
`CandidateSnapshot` privata e un `request_id` digest SHA-256 canonico; non usa un
`TechnicalDraft` che riapre l'authoring vivo. Il nuovo albero contiene
esattamente `manifest.toml`, `manifest.toml.sig`,
`manifest.lang_state.json` e tutti e soli i `code.files`. `authoring_tree_id` è
il digest con dominio `metnos.executor-birth.authoring-tree/v1\0` della lista
ordinata per byte UTF-8 degli oggetti `{relative_path,size,sha256}`.

`AuthoringInstallJournalV1` è JSON UTF-8 canonico e contiene esattamente
`schema_version=1`, `request_id`, `contract_id`, `source_origin`,
`canonical_tree_id`, `old_tree_id`, `new_tree_id`, `candidate_id`,
`semantic_core_id`, `admission_context_id`, `predecessor_generation_id`,
`new_generation_id`, `staging_basename`, `backup_basename`,
`recovery_action="restore_old_until_new_pointer"` e `state="prepared"`.
Gli identificatori sono digest canonici; `old_tree_id` e predecessore sono
nullable soltanto alla prima nascita. I basename sono core-generated come
`.birth-stage-<64-hex-request-id>` e `.birth-backup-<64-hex-request-id>` e non
contengono separatori. Nessun percorso libero entra nel journal.

Non nasce una seconda chiave. La stessa `AdmissionReceipt` autentica il journal
includendo il controllo obbligatorio `authoring_install_journal_v1` con
`rule_version="1"`, `status="passed"` ed `evidence_hash` uguale al digest con
dominio `metnos.executor-birth.authoring-journal/v1\0` del journal canonico. Il
callback di emissione riceve anche `request_id` e `journal_hash`; la rilettura
verifica firma, binding di ammissione e questo controllo. Un issuer che non
accetta o non restituisce tali legami fallisce chiuso.

Canonico, staging e backup sono directory sorelle sullo stesso filesystem. Una
directory di controllo derivata dal `ContractId`, esterna all'albero sostituito,
contiene soltanto `authoring.lock`, `version.json` e `journal.json`.
`version.json` è canonico e contiene esattamente `schema_version=1`,
`contract_id`, `version` intero non negativo distinto da booleano e `tree_id`.
Un lettore acquisisce il token condiviso, legge versione e tree ID, acquisisce
l'intero insieme chiuso, rilegge versione e accetta soltanto se coincidono; in
caso contrario scarta e ripete entro la scadenza. Il writer acquisisce il token
esclusivo e incrementa la versione soltanto dopo la postcondizione completa.
POSIX usa `flock(LOCK_SH|LOCK_EX)`; Windows usa blocchi byte-range
condivisi/esclusivi; entrambi hanno coordinamento RW intra-processo e timeout
finito. Primitive assente o filesystem incompatibile produce
`authoring_atomic_install_unsupported`.

La sequenza vincolante è: blocco catalogo; token esclusivo; writer lock; verifica
copia e calcolo payload firmati/generazione; emissione, persistenza e rilettura
della ricevuta legata al journal; persistenza e `fsync` del journal `prepared`;
rename canonico a backup; rename staging a canonico; rilettura tree ID;
installazione generazione RM-0007; sostituzione del puntatore `current`;
rilettura di generazione, ricevuta e authoring; incremento e sincronizzazione
della versione; rimozione journal; rimozione backup.

Il riconciliatore usa gli stessi tre blocchi e una matrice chiusa: puntatore
vecchio e canonico vecchio elimina staging e journal; puntatore vecchio con
canonico assente ripristina il backup vecchio; puntatore vecchio con canonico
nuovo elimina il nuovo e ripristina il vecchio; puntatore nuovo e canonico nuovo
completa rilettura/versione e pulisce. Puntatore nuovo con canonico vecchio o
qualunque identità estranea produce `authoring_recovery_ambiguous`. Alla prima
nascita, finché il puntatore è assente, il nuovo albero viene rimosso e torna
l'assenza attestata. Journal, ricevuta, staging, backup o versione alterati o non
reciprocamente legati bloccano il recupero.

Prima dell'attivazione la guardia censisce tutti i lettori diretti di
`manifest_dir` e li migra all'API con token. Le prove iniettano un crash dopo
ogni frontiera fra ricevuta, journal, due rename, generazione, puntatore,
rilettura, versione e pulizia; coprono prima nascita, aggiornamento, replay,
conflitto, alterazione, lettori concorrenti Linux/Windows e timeout.

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

### 7.3 Chiusura atomica dei proprietari precedenti

La proprietà esclusiva non dipende da un flag nello stato scrivibile dal
servizio. È la congiunzione di una build F4 chiusa, che nega
incondizionatamente le vecchie API sulla radice produttiva, e di un certificato
firmato che autorizza l'avvio di quella build dopo il censimento. Assenza,
cancellazione, alterazione o mancata corrispondenza del certificato bloccano
l'avvio della build chiusa e non riaprono il percorso precedente. Dopo il punto
di non ritorno è vietato avviare una build anteriore a F4. Il marker
`contract-publications.ACTIVE` di RM-0007 resta indipendente e non viene
reinterpretato.

Il certificato usa la chiave Birth di ammissione già attiva, con autorizzazione
separata `ownership_cutover_v1` nel registro storico core-owned. La firma
Ed25519 usa il dominio
`metnos.executor-birth.ownership-cutover/v1\0`; una chiave ammessa a firmare
ricevute ma priva di tale scopo non può firmare il cutover. Nessuna chiave
pubblica o selezione di autorità proviene dal certificato o dal chiamante.

Il payload V1 è JSON ASCII canonico, senza campi extra o duplicati, e contiene
esattamente:

```text
schema_version=1
cutover_id
previous_cutover_id
request_id
signing_key_id
catalog_id
current_count
current_receipts
maintenance_evidence_hash
boundary_inventory_hash
boundary_guard_version
closed_build_id
```

`previous_cutover_id` è nullo soltanto per il primo cutover e lega gli upgrade
successivi in una catena append-only. `current_receipts` è la lista ordinata per
byte UTF-8 di oggetti contenenti esattamente `contract_id`, `generation_id` e
`receipt_hash`; non ammette duplicati. `current_count` coincide con la lunghezza
della lista, compreso zero. Tutti gli identificativi e gli hash sono SHA-256
canonici. `catalog_id` usa il dominio
`metnos.executor-birth.current-catalog/v1\0` e il framing length-delimited della
lista completa. `cutover_id` usa il dominio
`metnos.executor-birth.ownership-cutover-id/v1\0` e il payload canonico senza
il solo `cutover_id`. La firma è un file separato di esattamente 64 byte.

`boundary_inventory_hash` autentica i byte canonici dell'inventario della
guardia; `boundary_guard_version` è la versione chiusa della sua politica.
`closed_build_id` autentica il manifest di distribuzione firmato, che include
almeno gli hash di `contract_store.py`, `sign.py`,
`contract_boundary_guard.py`, tutti i moduli `executor_birth*`, il preflight del
servizio e la versione del pacchetto. Il certificato non contiene tempi,
percorsi personali o dati liberi dell'operatore.

#### Manifest di distribuzione della build chiusa

Una build F4 non costruisce mai un `ClosedBuildIdentity` da parametri del
chiamante. L'unico produttore è il verificatore root-owned del manifest di
distribuzione V1. Questo artefatto è distinto dai manifest degli executor, dal
manifest dell'installer e dal certificato di cutover; non contiene chiavi,
percorsi del trust store o opzioni di verifica selezionabili tramite ambiente,
CLI, configurazione utente o richiesta.

Il payload è JSON ASCII canonico, senza chiavi duplicate, campi extra, spazi o
newline finale, con chiavi ordinate, separatori `,` e `:`, escaping ASCII e
`allow_nan=false`. Il limite è 16 MiB; la firma Ed25519 separata è di 64 byte.
Contiene esattamente:

```text
schema_version=1
closed_build_id
previous_closed_build_id
release_sequence
product_version
platform
architecture
signing_key_id
installation_root
certificate_directory
boundary_inventory_path
boundary_inventory_hash
boundary_guard_version
preflight_entrypoint
files
```

`previous_closed_build_id` è nullo soltanto per la prima build chiusa e negli
upgrade coincide con la build dell'ultima testa accettata. `release_sequence` è
un intero positivo, mai booleano, e cresce esattamente di uno. `product_version`
è la SemVer della sorgente unica di versione. `platform` appartiene a
`linux|windows`, `architecture` a `x86_64|aarch64`, e la coppia coincide con il
processo verificatore. Linux V1 usa come `installation_root` esattamente
`/var/lib/metnos/executor-birth/releases-v1/{release_sequence:020d}` e come
radice di autorita' `/var/lib/metnos/executor-birth`; `/opt/metnos` non e' una
radice di distribuzione V1. Le copie amministrative esterne sono legate dal
descrittore firmato alle sole radici `/usr/libexec/metnos/executor-birth-v1` e
`/etc/systemd/system`, mai da ambiente o richiesta. Un diverso insieme di
radici richiede un protocollo versionato nuovo.
Windows accetta soltanto percorsi drive-absolute normalizzati e rifiuta UNC,
device namespace, ADS e reparse point; certifica parser ed enforcement, non il
cutover amministrato.

`files` è non vuota, ordinata per byte UTF-8 di `path` e priva di duplicati.
Ogni elemento contiene esattamente `path`, `size`, `content_hash` e `role`.
`path` è relativo canonico NFC con `/`, senza segmenti vuoti, `.`, `..`,
backslash o NUL. `size` è un intero non negativo, non booleano, e coincide con
la lettura bounded dall'handle. `role` appartiene a
`runtime_code|preflight|boundary_guard|boundary_inventory|service_unit|service_catalog|deployment_descriptor|product_version|dependency_lock`.
Il digest di ogni file è:

```text
sha256("metnos.executor-birth.closed-build-file/v1\0" ||
       u64be(len(path_utf8)) || path_utf8 || u64be(size) || file_bytes)
```

La lettura non segue link o reparse point, richiede file regolare con un solo
hard link e proprietà/modalità amministrative, controlla identità e metadati
prima e dopo e rifiuta file mancanti o mutati. Nel sottoinsieme sigillato sono
vietati file extra caricabili, `.pyc`, `__pycache__`, namespace sovrapposti o un
diverso `sys.path`.

La release include almeno `contract_store.py`, `sign.py`, la guardia, tutti i
moduli `executor_birth*.py`, la sorgente unica di versione, verificatore e
preflight, inventario chiuso installabile, lock delle dipendenze e unità/drop-in
effettivamente avviata. La lista nominale non basta: il compilatore include la
chiusura transitiva dei moduli importabili dal proprietario Birth e dalle
eccezioni chiuse, più ogni configurazione amministrativa che può cambiare
interprete, root o avvio. Un import produttivo non risolto in un file firmato,
nella libreria standard o nel lock delle dipendenze blocca la build.

Il manifesto contiene una o più occorrenze `service_unit` ed esattamente una
occorrenza per ciascuno dei ruoli `boundary_inventory`, `dependency_lock`,
`service_catalog` e `deployment_descriptor`. Mancanza, duplicazione o ruolo
sconosciuto rendono la distribuzione non valida.

L'inventario chiuso è materializzato fuori da `internal/`, in un percorso
installato firmato. Il suo hash è
`sha256("metnos.executor-birth.boundary-inventory/v1\0" || inventory_bytes)`;
deve superare `--birth-closed` e coincidere con schema, versione guardia, unico
owner ed eccezioni compilate. Manifest, certificato e inventario non possono
ridefinire la politica.

`closed_build_id` è il digest del JSON canonico senza il solo campo omonimo,
preceduto dal dominio `metnos.executor-birth.closed-build-id/v1\0`. La firma
copre il payload completo con dominio
`metnos.executor-birth.closed-build/v1\0`. Il registro storico core-owned è
separato dal manifest; l'identificativo della chiave è
`distribution-ed25519-v1-sha256-<sha256(raw_public_key)>` e lo scopo esclusivo è
`closed_distribution_v1`. Scopi di cutover, ammissione o firma executor non lo
implicano. Il verificatore controlla identificativo, scopo, revoca ed epoca e,
soltanto dopo firma e file, crea in memoria il `ClosedBuildIdentity` sigillato.

#### Catena append-only, anti-downgrade e recupero

I nomi fissi `ownership-cutover-v1.json` e `.sig` restano l'ancora immutabile
del primo cutover: non sono lo store degli upgrade. Gli aggiornamenti usano:

```text
builds-v1/<closed_build_id>.json|.sig
cutovers-v1/<cutover_id>.json|.sig
heads-v1/{release_sequence:020d}-{cutover_id}.json|.sig
required-head-v1.bin
```

Build, cutover e head sono append-only e pubblicati con temporanei esclusivi,
rename no-replace e fsync. Il record head contiene esattamente
`schema_version`, `release_sequence`, `cutover_id`, `closed_build_id`,
`previous_head_id`, `head_id` e `signing_key_id`. `head_id` usa il dominio
`metnos.executor-birth.ownership-head-id/v1\0` sul payload senza `head_id`; la
firma usa `metnos.executor-birth.ownership-head/v1\0` e una chiave con scopo
separato `ownership_head_v1`. Il primo head lega l'ancora; ogni successivo lega
esattamente predecessore, build e cutover e incrementa la sequenza di uno.

Il preflight non sceglie il file col numero maggiore e non ripiega. Parte
dall'ancora, verifica una catena contigua e unica e richiede che l'ultima testa
coincida con `required-head-v1.bin`, pubblicato per ultimo dal coordinatore
root-owned e coperto dal descriptor di deployment. Questo selettore è un solo
file con framing esatto
`metnos-ownership-required-head-v1\0 || u32be(payload_length) || payload_json || signature_64`:
`payload_json` è il JSON canonico completo del record head, la firma è la stessa
firma `ownership-head/v1`, non sono ammessi byte finali e la dimensione è
limitata prima dell'allocazione. La sostituzione atomica del singolo file,
seguita da fsync della directory, lascia quindi osservabile sempre o la testa
vecchia completa o quella nuova completa; non esiste una coppia
payload/firma che possa lacerarsi durante un upgrade. Assenza, buco, fork,
predecessore errato, oggetti mancanti o una testa precedente producono
`birth_ownership_downgrade` o `birth_ownership_recovery_required`. Prima della
sostituzione atomica di `required-head-v1` resta avviabile soltanto la vecchia
build; dopo, soltanto la nuova. Un crash successivo lascia lo stack fermo e il
recovery completa byte per byte quella testa, senza fallback.

Il journal amministrativo distingue temporanei, coppie orfane e punto di non
ritorno. Gli orfani non referenziati anteriori possono essere conservati o
rimossi soltanto con journal concordante e non sono selezionabili. Dopo il
punto di non ritorno non si cancella alcuna autorità e un ripristino richiede
una nuova build e un nuovo head con sequenza superiore. Un retry accetta solo
byte, firme e identità identici.

Il protocollo protegge da servizio, chiamanti, installer non autorizzato, crash
e cancellazioni parziali. Un amministratore root ostile capace di ripristinare
insieme filesystem, unità e trust anchor richiede secure boot e contatore
monotono TPM; tale modello è fuori da V1 e non viene implicitamente dichiarato.

Gli errori aggiuntivi sono `birth_ownership_distribution_missing`,
`birth_ownership_distribution_invalid`,
`birth_ownership_distribution_key_unauthorized`,
`birth_ownership_distribution_file_mismatch`,
`birth_ownership_distribution_extra_file`,
`birth_ownership_distribution_platform_mismatch`,
`birth_ownership_distribution_chain_invalid`, `birth_ownership_downgrade` e
`birth_ownership_distribution_recovery_required`. I dettagli esterni non
espongono percorsi arbitrari, byte, chiavi o digest osservati.

`maintenance_evidence_hash` usa il dominio
`metnos.executor-birth.maintenance-proof/v1\0` sul documento canonico
`{schema_version:1,source,units}`. `source` appartiene all'enum chiuso del
coordinatore amministrativo; `units` è ordinato e ogni elemento contiene
esattamente `scope`, `unit`, `load_state`, `active_state` e `main_pid`. Tutte le
unità interessate devono essere inattive o fallite e avere PID zero. Il
coordinatore opera dentro `contract_cutover_guard`, prova la quiescenza prima e
dopo la firma, rilegge e autentica ogni ricevuta dalla memoria durevole,
ricalcola il censimento subito prima della firma e richiede zero
`birth_migration_findings`, zero scope non classificati o obsoleti e lo stesso
hash di inventario incorporato nella build chiusa.

Sul sistema Linux amministrato i file sono
`/var/lib/metnos/executor-birth/ownership-cutover-v1.json` e `.sig`. La directory
è creata dall'installer come `root:root 0755`; i file sono `root:root 0644` e il
servizio non può crearli, rimuoverli o sostituirli. Un eventuale percorso diverso
proviene soltanto dal manifest d'installazione root-owned e il suo hash entra nel
`closed_build_id`, mai da ambiente o richiesta. La lettura è limitata, tramite
handle, e rifiuta link, reparse point, hard link, cambi d'identità o metadati e
riletture diverse. Windows certifica parser ed enforcement, mentre il cutover
amministrato resta Linux/systemd.

L'ordine dei blocchi e' il blocco di deployment posseduto da `root`, quindi il
blocco esclusivo degli avvii, `catalog_admission_lock`, blocco di
riconciliazione e manutenzione e infine gli eventuali writer lock dei contratti
ordinati per `ContractId`. `check` e `launch` prendono il blocco degli avvii in
modo condiviso; il coordinatore lo prende in modo esclusivo prima di stop e
censimento e lo conserva fino al controllo preliminare finale. Il blocco di
deployment viene acquisito una sola volta dall'orchestratore esterno; un nucleo
privato sigillato riceve la sessione gia' detenuta e non tenta di riacquisirla.
La sequenza e':

1. installare un `ExecStartPre` root-owned che consente una build precedente
   soltanto finché il certificato non esiste e, quando esiste, consente
   esclusivamente il `closed_build_id` autenticato;
2. fermare lo stack, censire e riattestare tutte e sole le correnti;
3. scrivere e sincronizzare payload e firma temporanei, rileggerli per handle,
   rinominare prima la firma e poi il payload senza sovrascrittura e
   sincronizzare la directory; il rename del payload è il punto di non ritorno;
4. installare la build chiusa corrispondente e verificarla integralmente;
5. all'avvio, prima dell'ingresso, ricensire catalogo e ricevute e confrontare
   build, inventario, guardia e certificato; solo allora esporre readiness.

Un retry accetta file esistenti soltanto se sono byte per byte identici e la
firma è valida. Un crash prima del payload non chiude la proprietà e lascia lo
stack fermo. Una firma orfana può essere rimossa soltanto dal recovery root se
payload è assente e firma e journal coincidono. Payload senza firma valida,
build precedente dopo payload, build chiusa non pronta o qualunque divergenza
restano fail-closed e riprendono dal journal root-owned; non esiste una
procedura che cancelli il certificato o riabiliti l'autorità precedente.

Nella build chiusa, sulla radice produttiva, sono negate prima di leggere input,
chiavi o callback: `publish_technical_update`, `reactivate_technical_update`,
`rollback`, `publish_signed_source` dopo l'attivazione RM-0007 e gli equivalenti
`sign_executor`, `publish_executor`, `publish_authoring_update`,
`reactivate_executor_contract` e `rollback_executor_contract`, inclusi alias,
import dinamici e subprocess. L'unica pubblicazione tecnica usa
`commit_birth_snapshot` con `BirthCommitAuthorization` sigillata. Restano
eccezioni chiuse: sola localizzazione RM-0007, ritiro riduttivo, bootstrap
one-shot precedente al certificato e strumenti offline diretti a una radice
non produttiva che non può essere caricata. Riavvio, installer e rollback
applicativo producono intent Birth.

Gli errori V1 sono `birth_ownership_proof_missing`,
`birth_ownership_proof_invalid`, `birth_ownership_binding_invalid`,
`birth_ownership_key_unauthorized`, `birth_ownership_build_mismatch`,
`birth_ownership_inventory_mismatch`, `birth_ownership_catalog_changed`,
`birth_ownership_cutover_conflict`, `birth_ownership_legacy_api_closed` e
`birth_ownership_recovery_required`. Soltanto il recovery amministrativo può
ritentare gli ultimi stati. La guardia possiede una modalità `--birth-closed`
che richiede esattamente un proprietario Birth, nessuna capacità
`publish_technical|reactivate|rollback|sign` fuori dai moduli sigillati, le sole
eccezioni elencate e zero confini dinamici. La CI prova zero, una e molte
correnti, ricevuta già presente, canonicalizzazione, chiave e scopo, mismatch di
catalogo/inventario/build, cancellazione che non riapre, accessi diretti e
riflessivi, crash a ogni frontiera, orfani, replay, conflitto, downgrade,
parser/enforcement Windows e preflight Linux reale.

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

Su Windows Birth usa un helper Rust dedicato e non il modulo privato del client
remoto. Il profilo AppContainer è `Metnos.ExecutorBirth.V1`, distinto da quello
del client; non riceve `internetClient` né altre capability di rete. Gli unici
ACE temporanei riguardano binario runtime, copia privata del candidato e work
root privata. Sono registrati per richiesta e revocati da una guardia RAII anche
su errore. Nessun percorso personale, grant del manifest o registro ACL del
client è condiviso.

La richiesta wire V1 dell'helper è JSON UTF-8 canonico con esattamente
`schema_version=1`, `request_id`, `candidate_id`, `phase`, `private_root`,
`entrypoint` e `arguments`. `request_id` e `candidate_id` sono digest SHA-256;
`phase` è `candidate|reference|equivalence`; `private_root` è un percorso
assoluto già acquisito da Birth e non entra nelle identità; `entrypoint` è un
percorso relativo POSIX, senza link, `..` o collisioni di maiuscole, contenuto
nella copia privata. `arguments` contiene al massimo 32 stringhe senza NUL, di
massimo 4.096 byte UTF-8 ciascuna. Ambiente, interprete, limiti, rete e grant non
sono campi della richiesta: appartengono alla politica V1 dell'helper.

La work root V1 contiene esattamente `candidate/` e `work/`: `candidate/` è la
copia chiusa in sola lettura con manifest, stato linguistico e `code.files`;
`work/` è la sola directory scrivibile e contiene le fixture core-owned. V1
esegue esclusivamente entrypoint Python `.py`. L'interprete è il runtime Python
bundled dell'installazione, risolto da una configurazione core-owned esterna alla
richiesta; percorso assoluto e digest SHA-256 sono registrati nel registro
sandbox incluso nell'`admission_context_id`. L'helper rifiuta interprete
mancante, mutato, non regolare o fuori dalla radice runtime installata. Non usa
`sys.executable`, `PATH`, associazioni file o un interprete indicato dal
candidato. L'attestazione aggiunge `runtime_binary_hash`, che deve coincidere
con il registro.

Il registro sandbox V1 contiene inoltre percorso assoluto e hash SHA-256 del
file di configurazione dell'helper. Birth avvia l'eseguibile registrato soltanto
come `metnos-birth-sandbox --config <percorso> --config-hash <digest>` e invia la
richiesta wire su stdin; nessun altro argomento è ammesso. Il file è regolare,
senza reparse point, sotto la radice di installazione amministrata e con ACL di
scrittura limitata ad amministratori e SYSTEM. È JSON UTF-8 canonico con
esattamente `schema_version=1`, `runtime_root`, `runtime_binary` e
`runtime_binary_hash`; i percorsi sono assoluti, il binario è contenuto nella
radice e il digest è canonico. L'helper verifica prima l'hash del file ricevuto,
poi schema, contenimento e hash del runtime. Percorso e digest attesi provengono
dal registro sandbox dell'`admission_context_id`, non dal candidato, da variabili
d'ambiente, dal registro Windows o da un path predefinito implicito.

La risposta wire V1 è JSON UTF-8 canonico con esattamente `schema_version=1`,
`request_id`, `candidate_id`, `status`, `error_code`, `exit_code`,
`stdout_base64`, `stderr_base64`, `stdout_bytes`, `stderr_bytes`,
`stdout_truncated`, `stderr_truncated`, `elapsed_ms` e `attestation`.
`status` è `passed|failed|test_environment_unavailable`; `error_code` ed
`exit_code` sono nullable soltanto dove coerente. I conteggi e il tempo sono
interi non negativi, distinti da booleani. L'oggetto `attestation` contiene
esattamente `backend=windows-appcontainer-job-v1`, `helper_binary_hash`,
`runtime_binary_hash`, `profile_name`, `appcontainer_sid`, `network_capability=false`,
`assigned_before_resume`, `active_processes`, `tree_empty`,
`termination_attested`, `memory_limit_bytes`, `process_limit`,
`stdout_limit_bytes` e `stderr_limit_bytes`. Birth accetta un'esecuzione soltanto
se i binding coincidono, l'hash dell'helper è nel registro sandbox del contesto,
assegnazione prima del risveglio è vera, processi attivi sono zero, albero vuoto
e terminazione attestata sono veri, e tutti i limiti coincidono con V1.

L'helper legge e drena le pipe con limiti prima di attendere i thread, termina
l'intero Job Object su timeout o overflow e non degrada mai a solo Job Object.
Profilo, ACL, runtime, AppContainer o attestazione indisponibili producono
`test_environment_unavailable`. La certificazione Windows compila l'helper e
prova file host, credenziali e rete non accessibili, nipote terminato, overflow,
timeout e indisponibilità; un test che verifica soltanto il Job Object non
soddisfa questo requisito.

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

L'inventario iniziale congela 24 **scope statici di propagazione**, non 24
produttori indipendenti: funzioni, wrapper, `main` e scope di modulo dello stesso
flusso possono comparire separatamente. Il cutover richiede sia l'azzeramento di
questi scope sia la verifica dei flussi foglia elencati sopra. La guardia tratta
come debito tecnico Birth `publish_technical`, riattivazione, firma e rollback;
non assimila a un bypass il ritiro riduttivo, la sola localizzazione o il confine
di migrazione una tantum.

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
- **F1, tipi e identità in osservazione — completata il 25 agosto 2026:** implementare contratti, copia, identità
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
| 2026-08-25 | `active` | F1 completata: staging chiusa e snapshot privato anti-link/anti-race; codec tipizzato e vettori golden per le tre identità; contesto di ammissione chiuso; ricevute Producer e Admission autenticate come codec puri. Le prove strutturali e dinamiche confermano zero chiamate al publisher, nessun consumo e nessuna influenza sul loader. F2 è la fase successiva. |
| 2026-08-25 | `active` | Interruzione prudenziale e verifica completa del residuo: F4-F6 dispongono di primitive significative, ma non sono integrate né certificabili in produzione. Il §23 rende esplicite le lacune e l'ordine non permutabile della ripresa. |
| 2026-08-25 | `active` | Ripristinata la matrice pubblica: la scrittura binaria conserva firme Ed25519 di 64 byte anche su Windows; l'esecuzione GitHub 32868770779 è verde su Windows 2022 e Ubuntu 24.04. |
| 2026-08-25 | `active` | La verifica precedente alla chiusura statica ha trovato tre prerequisiti non aggirabili: chiave autore assente dal pubblicatore Birth produttivo, 21 executor incorporati con involucro incompatibile e bootstrap iniziale dell'installatore ancora affidato a firma precedente. I §§23.6 e 23.8 correggono l'ordine di sviluppo senza ridurre i criteri di F4-F6. |
| 2026-08-28 | `active` | Gruppi 2 e 3 della ripresa completati: autorità predisposte, bootstrap sigillato attivo, registri consumati, controlli chiusi e politiche produttive. Il criterio di uscita dettagliato è in `internal/reports/rm0008-gruppo3-piano-ottimizzato.md` §11. |
| 2026-08-28 | `active` | Chiuso il blocco Windows del predispositore: preparazione e pubblicazione sono separate dal checkpoint durevole `verified`; ciclo pubblico `33153843377`, commit `a04267b`, otto lavori su otto verdi. Il gruppo 4, chiusura statica F4, è il successivo. |
| 2026-08-28 | `active` | Riesame dopo il gruppo 3 completato: la guardia chiusa misura 26 rilievi iniziali e il piano ottimizzato del gruppo 4 li divide in tre incrementi causali. Primo incremento: autenticare i due caricamenti fra executor; il bit F4 resta falso. |
| 2026-08-28 | `active` | G4-A implementato e in certificazione locale: due dipendenze fra executor passano dalla porta autenticata e le revisioni sono state pubblicate da intenzioni Birth reali. Il riesame di velocizzazione conserva G4-A separato e unisce rimozione delle firme e congelamento dell'inventario in G4-B+C, risparmiando una matrice pubblica senza ridurre le prove. |

## 23. Verifica dello stato e piano esecutivo prima della ripresa

Questa sezione registra la verifica svolta dopo l'interruzione prudenziale dello
sviluppo del 25 agosto 2026. Lo scopo non è ridurre i requisiti precedenti, ma
distinguere le primitive già costruite dalla loro effettiva integrazione nel
prodotto. Un test unitario prova il contratto della primitiva; non prova che il
percorso produttivo la invochi, che l'installatore la configuri o che un
riavvio la applichi.

### 23.1 Verdetto verificato

F2 e le fondamenta di F3-F6 contengono una quantità significativa di codice e
prove. F4, F5 e F6 non sono tuttavia completate in senso produttivo.

- F4 dispone di riattestazione, censimento stabile, certificato firmato,
  manifest di distribuzione, catena a sola aggiunta, selettore atomico, guardia
  statica e controllo preliminare di avvio. Manca il coordinatore posseduto da
  `root` che li componga; mancano inoltre la predisposizione delle autorità e il
  collegamento all'installatore e ai servizi. La guardia chiusa non è verde e
  la politica compilata che nega i percorsi precedenti resta `False`.
- F5 dispone di politica di preesercizio, archivio delle epoche, indice univoco
  dell'epoca corrente, coordinatore del ciclo, identità delle memorie
  temporanee e primitive di riscontro. Questi moduli si dichiarano inattivi e
  non sono collegati a caricatore, instradatore, memorie temporanee,
  pubblicazione RM-0007 o tentativi durevoli. Il vecchio stato per nome e
  `promoted_grace` restano attivi.
- F6 dispone del grafo di raggiungibilità, marcatura, cancellazione, ricevuta
  minima e coda d'uscita. Il registro produttivo degli adattatori è vuoto; non
  esistono raccoglitore, pianificazione o funzioni produttive di cancellazione.
  Non è quindi avvenuta alcuna conservazione reale governata dal grafo.
- La matrice pubblica dell'esecuzione GitHub `32868770779` è verde su Ubuntu
  24.04 e Windows 2022. Essa certifica la portabilità delle primitive attuali,
  compresa la scrittura binaria delle firme, ma non costituisce la
  certificazione finale: non attraversa ancora il coordinatore F4, il passaggio
  produttivo, F5 e F6.

### 23.2 Evidenze e lacune per F4

Le seguenti primitive costituiscono basi accettabili, ma non autorizzano la
chiusura della fase:

| Requisito | Evidenza presente | Lacuna da chiudere |
|---|---|---|
| registrazione Birth e ricevuta prima del puntatore | `executor_birth_operational.py`, `contract_store.py::commit_birth_snapshot` e prove di arresto/rilettura | dimostrazione sul percorso installato e su ogni produttore reale |
| riattestazione delle correnti | `executor_birth_reattestation.py` e `executor_birth_cutover.py` | fabbrica produttiva sigillata di richieste e ricevute Producer; nessun dato autorevole deve essere scelto dal chiamante |
| manutenzione e censimento | coordinatore puro con doppio censimento | guardia posseduta da `root` che mantenga il blocco, produca osservazioni canoniche complete e le ricontrolli prima della firma |
| certificato e catena | `executor_birth_ownership_cutover.py`, `executor_birth_distribution_manifest.py`, `executor_birth_ownership_chain.py` | registri di fiducia installati, emittente del manifest, allocazione della sequenza, installazione posseduta da `root` e collegamento completo al controllo preliminare |
| avvio senza ritorno a versioni precedenti | `executor_birth_ownership_preflight.py` | `ExecStartPre` dominante su tutti gli ingressi e verifica della catena richiesta, non soltanto dell'ancora iniziale |
| proprietario unico | guardia `--birth-closed` e punto di diniego | inventario chiuso ancora non valido, eccezioni mancanti, installatore e generatore incorporato con autorità di firma precedente, booleano compilato ancora falso |
| prova operativa | prove unitarie e portabili | prova generale Linux sotto `root`, passaggio controllato, caricamento a freddo dal solo archivio, riavvio e due cicli di instradamento |

Il coordinatore F4 deve possedere un registro durevole con almeno gli stati
`PREPARED`, `RECEIPTS_COMPLETE`, `CERTIFICATE_PUBLISHED`, `BUILD_VERIFIED`,
`HEAD_REQUIRED` e `PREFLIGHT_VERIFIED`. Nel primo passaggio,
`CERTIFICATE_PUBLISHED` pubblica l'ancora fissa ed e' il punto di non ritorno
dal regime precedente: dopo tale stato il recupero non puo' cancellare il
certificato, riaprire i proprietari precedenti o avviare il predecessore. Negli
aggiornamenti successivi il nuovo certificato viene aggiunto soltanto alla
catena; la distribuzione chiusa corrente resta autorevole fino al
confronto-e-scambio di `required-head-v1.bin`, che e' il punto di non ritorno
dell'aggiornamento. Ogni passaggio deve rileggere il proprio risultato
autenticato prima di avanzare.

Il controllo preliminare transitorio deve essere installato prima del passaggio
al nuovo regime. In assenza del certificato permette soltanto l'artefatto
predecessore autenticato indicato dal descrittore posseduto da `root`. In
presenza del certificato permette soltanto l'artefatto chiuso e la testa
richiesta corrispondenti. Assenza ambigua, firma errata, catena incompleta o
artefatto precedente arrestano tutti i servizi dominati dal controllo
preliminare.

### 23.3 Diagnosi Windows risolta e prova da conservare

Il registro pubblico ha mostrato firme `.sig` con dimensione di 65 o 66 byte,
mentre Ed25519 produce sempre 64 byte. Attributi, numero di link e tipo del file
erano corretti. La causa è `_write_temporary()` in
`executor_birth_ownership_cutover.py`: `os.open()` e `os.write()` operavano in
modalità testo su Windows, perciò un byte `0x0a` della firma veniva espanso in
CRLF.

La correzione è stata applicata aggiungendo `O_BINARY` all'apertura. La prova
deterministica scrive tutti i 256 valori di byte e include quindi certamente
`0x0a`; i byte riletti devono essere identici. La diagnostica temporanea dei
metadati rifiutati è stata rimossa, mentre l'errore pubblico è rimasto stabile.
La registrazione Git che usa `GetFileInformationByHandleEx` fornisce metadati
Win32 con struttura stabile e va mantenuta come primitiva condivisa, riducendo
eventuali duplicazioni. L'esecuzione pubblica `32868770779` ha superato l'intero
insieme portabile su Windows 2022 e Ubuntu 24.04. La prova Windows finale resta
comunque obbligatoria dopo l'integrazione dei percorsi produttivi F4-F6: il
verde attuale non può attestare codice che non è ancora collegato.

### 23.4 Evidenze e lacune per F5

Il certificato di attivazione F5 non deve fidarsi di conteggi dichiarati. Un
certificatore separato deve ricostruire la soglia del §10 da evidenze
in sola aggiunta e autenticate: almeno cinque ammissioni tecniche reali e non
semplici ritentativi idempotenti, almeno due produttori distinti autenticati,
ricevute rilette, due cicli di instradamento consecutivi e zero difetti o
aggiramenti irrisolti.
Gli identificativi fittizi firmati provano soltanto l'autenticità del
contenitore e non soddisfano la soglia.

L'integrazione deve seguire questo ordine:

1. migrare senza perdita lo stato precedente per nome e conservarlo come
   irrisolto finché non esiste una corrispondenza autenticata con
   `(ContractId, generation_id)`;
2. disabilitare le letture produttive di `promoted_grace` e delle sostituzioni di
   ciclo per nome soltanto dopo una riconciliazione completa e verificata;
3. collegare il coordinatore del ciclo al pubblicatore RM-0007 reale, con
   pubblicazione, rilettura e CAS dell'epoca esatta;
4. tenere il preesercizio fuori dalla selezione ordinaria e dai durevoli; la
   politica chiusa deve essere derivata dalle capacità firmate, non da fatti
   forniti dal candidato;
5. includere `(ContractId, generation_id, lifecycle)` in tutte le famiglie di
   memorie temporanee e provare l'invalidazione di L0, L1, alternative e
   prefiltri a ogni transizione;
6. applicare `DurableBirthAttemptGuard` a ogni tentativo durevole e rileggere
   generazione, ricevuta ed epoca immediatamente prima dell'invocazione;
7. collegare il riscontro reale allo `StepLog`: CAS esatto, pubblicazione e
   rilettura della quarantena, esclusione dalla selezione prima della coda
   d'uscita, ripresa idempotente dopo arresto e rifiuto del riscontro obsoleto.

Le prove devono attraversare il caricatore, l'instradatore e le memorie
temporanee reali. Funzioni di richiamo fittizie e basi di dati temporanee
restano prove di modulo e non dimostrano l'integrazione.

### 23.5 Evidenze e lacune per F6

Il grafo non può essere attivato finché manca anche un solo proprietario. Devono
esistere adattatori collocati con i rispettivi dati, o un protocollo
equivalente provato, per generazioni, ricevute Admission e Producer, evidenze,
`StepLog`, proposte, registri di verifica e ogni altro nodo dichiarato. Ciascun
adattatore deve censire riferimenti e radici, applicare
la cancellazione del proprio oggetto e riconciliare la coda d'uscita in modo
idempotente.

Prima della prima cancellazione produttiva sono obbligatori:

1. raccolta completa del grafo e confronto con i proprietari reali;
2. esecuzione della marcatura in osservazione e diagnostica senza cancellazione;
3. prova che `audit_jsonl` e `proposals_cleanup` non cancellino autonomamente
   oggetti governati dal grafo;
4. nuova marcatura sotto blocco, CAS delle versioni e ricevuta minima autenticata
   prima della cancellazione;
5. arresto e ripresa per ogni tipo di nodo, gara con un nuovo arco, doppia
   cancellazione e mantenimento delle generazioni storiche non richieste;
6. una cancellazione reale controllata e recuperabile su dati predisposti, mai
   sulla storia corrente.

### 23.6 Sequenza non permutabile per la ripresa

Il lavoro successivo deve essere suddiviso nei seguenti gruppi. Ogni gruppo
produce codice, prove e una registrazione Git autonoma; il gruppo seguente
parte soltanto dopo il criterio di uscita del precedente.

1. **Ripristino della matrice:** correzione binaria Windows, prova deterministica
   e reale, rimozione della sola diagnostica temporanea, insieme di prove
   portabili verdi su Linux e Windows.
2. **Radice autore e predisposizione delle autorità:** autenticazione della
   chiave autore predefinita contro il registro di fiducia e consegna privata al
   pubblicatore Birth; creazione e installazione separate delle autorità
   Admission e Producer, delle approvazioni e del contesto semantico. Questo
   gruppo non migra chiamanti e non rende ancora vincolante la guardia.
3. **Bootstrap iniziale e involucri incorporati:** bootstrap privato e sigillato
   posseduto da Birth per il solo stato precedente al certificato; fabbrica
   sigillata di riattestazione e tabella chiusa di provenienza e paternità;
   controllo preliminare transitorio minimo installato prima dell'attivazione;
   macchina di convergenza dell'installatore con ripresa; migrazione dei 21
   executor incorporati verso involucri che attestino tutti i byte realmente
   eseguiti. Generatore e installatore cessano di firmare direttamente soltanto
   nello stesso cambiamento che rende operativo il percorso sostitutivo.
4. **Chiusura statica F4:** eliminazione delle autorità precedenti residue,
   classificazione esatta dei chiamanti, inventario `birth_closed` e guardia
   `--birth-closed` verde sullo stesso albero destinato alla distribuzione. Non
   sono ammesse eccezioni che nascondano una capacità produttiva. La politica
   compilata che nega i percorsi precedenti resta falsa.
5. **Autorità e coordinatore F4:** riuso della fabbrica sigillata di
   riattestazione nel coordinatore, prova canonica di manutenzione, registro
   posseduto da `root` e recupero oltre il punto di non ritorno. Le autorità e i
   registri posseduti da `root` per `closed_distribution_v1`,
   `ownership_cutover_v1` e `ownership_head_v1` devono essere distinti tra loro
   e non possono riusare chiavi autore, Admission o Producer.
6. **Distribuzione e avvio F4:** assemblaggio firmato, installazione atomica,
   catena completa, controllo preliminare transitorio e definitivo, copertura di
   tutti i servizi e dell'installatore. Poiche' la politica compilata resta
   falsa, questo gruppo non modifica il server gestito e l'entrata produttiva
   nega prima di claim e journal; installazione e composizione complete sono
   provate soltanto nella VM usa-e-getta.
7. **Passaggio e artefatto chiuso F4:** prova generale isolata, passaggio reale
   controllato, artefatto separato con diniego compilato vero, caricamento a
   freddo, riavvio, ripetizione equivalente e due cicli di instradamento. Solo
   questo gruppo può dichiarare F4.
8. **Certificatore e migrazione F5:** soglia derivata da evidenze, migrazione
   senza perdita e ritiro delle letture precedenti per nome.
9. **Integrazione F5:** ciclo, preesercizio, epoche, memorie temporanee,
   tentativi durevoli e riscontro attraversano i percorsi reali e superano prove
   di arresto e CAS su stato superato.
10. **Integrazione F6:** adattatori completi, raccolta in osservazione, coda
   d'uscita, cancellazione reale controllata e certificazione delle gare.
11. **Certificazione finale:** insieme completo di prove Linux e Windows, due
   cicli di instradamento reali consecutivi, prova non distruttiva, ADR 0224,
   documentazione italiana e inglese, note dell'installatore, procedura
   operativa di migrazione e recupero, pubblicazione e verifica della
   distribuzione installata.

### 23.7 Evidenza richiesta a ogni gruppo

Ogni agente di codifica deve consegnare una tabella con requisito, simbolo
produttivo, test di modulo, test di integrazione, prova installata, risultato e
registrazione Git. Deve inoltre indicare esplicitamente ciò che non è stato
provato. È vietato usare come prova produttiva un modulo importato soltanto dai
test, una funzione di richiamo fittizia, un certificato costruito da conteggi dichiarati,
una base di dati sintetica o una matrice che non esegua il percorso interessato.

La roadmap resta `active`. Non deve essere marcata `implemented` o `closed`
finché ogni riga dei §§15-17 e di questa sezione non dispone dell'evidenza
autorevole corrispondente.

### 23.8 Vincoli emersi prima della chiusura statica

La chiusura statica originariamente prevista subito dopo il ripristino della
matrice non è eseguibile senza rendere non funzionanti le nuove installazioni.
La verifica del codice ha prodotto le seguenti evidenze:

1. `executor_birth_bootstrap._build()` consegna al nucleo soltanto le chiavi
   pubbliche fidate. `commit_birth_snapshot()` richiede invece anche la chiave
   privata dell'autore. Il bootstrap Birth produttivo non può quindi completare
   una pubblicazione. La correzione deve caricare esclusivamente la chiave
   autore predefinita, verificarne la corrispondenza con la voce pubblica
   fidata, mantenerla distinta dalle chiavi Admission e Producer e consegnarla
   soltanto al collegamento privato del pubblicatore. Nessun chiamante può
   scegliere nome, percorso o chiave.
2. Tutti i 21 manifest incorporati osservati usano `code.files` con percorsi
   `../../...`. Lo snapshot chiuso rifiuta correttamente ogni attraversamento
   del genitore e il caricatore deve attestare il modulo realmente eseguito.
   Non è lecito allentare la normalizzazione, copiare un modulo diverso da
   quello caricato o firmare il solo manifest. Gli executor devono migrare verso
   involucri posseduti dal contratto. Ogni modulo non appartenente alla libreria
   standard e caricato transitivamente dall'entrata deve essere incluso nello
   snapshot mediante un percorso canonico posseduto dal contratto, oppure deve
   essere una dipendenza di runtime chiusa, autenticata sia da
   `admission_context_id` sia dalla distribuzione. Il caricatore deve provare che
   i byte eseguiti coincidano con quelli attestati. È vietato un involucro che si
   limiti a delegare verso codice esterno non legato. Fino al completamento di
   questa migrazione, la pubblicazione Birth del candidato deve fallire prima di
   qualsiasi mutazione con un errore stabile.
3. `install/phases/phase3_code.py` e
   `scripts/generate_builtin_executor_contracts.py` possiedono ancora capacità
   di firma diretta. Spostare la stessa chiamata in un modulo sigillato o
   aggiungere un'eccezione alla guardia non cambia l'autorità e non soddisfa il
   proprietario unico. Serve un bootstrap iniziale privato di Birth, ammesso
   soltanto sotto quiescenza, nello stato precedente al certificato e con
   inventario posseduto internamente. Non è un involucro di `sign_executor`, non
   accetta percorso, chiave o inventario dal chiamante, censisce internamente
   tutti e soli i contratti installati, prepara e firma soltanto come parte
   della pubblicazione atomica dell'albero ombra e non restituisce firme
   riutilizzabili. Phase 3 orchestra e conserva la prova di quiescenza, ma non
   possiede la firma. Il bootstrap diventa irraggiungibile dopo il certificato;
   da quel momento si usa soltanto il protocollo Birth ordinario.
4. L'installatore non predispone ancora in modo completo archivio Admission,
   archivi Producer, approvazioni, autorità semantica e contesto. La fabbrica
   dei Producer non può usare una sola coppia fissa di provenienza e paternità
   per un catalogo eterogeneo: deve derivare la coppia esatta da `ContractId`
   mediante una tabella chiusa posseduta dal sistema, mai da campi autorevoli
   forniti dal chiamante.
5. Il passaggio dell'installatore deve essere una macchina di convergenza
   recuperabile con una matrice esplicita. In `legacy` (precedente) crea
   l'albero ombra e persiste il rapporto prima dell'attivazione. In
   `recovery_required` (recupero obbligatorio) legge l'esatto rapporto di
   preparazione e lo valida contro l'albero ombra. In `store_only` (solo
   archivio) ignora ogni rapporto storico e ricostruisce lo stato dalla radice
   produttiva autenticata. In `active` (attivo) verifica il catalogo corrente.
   Il rapporto odierno non è firmato e non deve essere descritto o trattato come
   autenticato. Una volta raggiunto in sicurezza lo stato `active`, il percorso
   avvia Birth, riattesta le correnti mancanti mediante la fabbrica sigillata,
   converge l'installatore tramite Birth e verifica a freddo il solo archivio.
   Il controllo preliminare transitorio va installato prima di attivare
   l'archivio o dichiarare complete le ricevute.
6. La guardia chiusa rileva ancora sedici ambiti per i quali la politica
   compilata richiede una specifica `closed_exception`, assente o non
   corrispondente nell'inventario; rileva inoltre il proprietario Birth non
   ancora classificato come tale e le due autorità di firma precedenti indicate
   sopra. Il verde sarà significativo soltanto dopo aver rimosso le autorità
   reali e classificato gli ambiti esatti; non costituisce una soluzione
   trasformare il debito in eccezioni o cambiare soltanto il ruolo.

Questi vincoli non ampliano né riducono RM-0008. Rendono espliciti i prerequisiti
necessari affinché la rimozione dei vecchi firmatari non lasci un sistema senza
un percorso di installazione valido. Per questo i gruppi 2-4 del §23.6
sostituiscono l'ordine precedente e sono non permutabili.

### 23.9 Correzioni live incluse nel prossimo censimento

Durante l'implementazione B3 sono stati riprodotti due difetti live. Il
fallback `admin` per comandi nominati deriva ora dall'inventario della
grammatica safety anziche' da eccezioni per singolo comando. Il lessico shell
preesistente è stato rimosso dal consumer e registrato come risorsa
traducibile; polarità e invocazione privilegiate richiedono dati nativi pronti
e revisione manuale. Uno stato distinto segnala grammatica nativa non
disponibile e mantiene chiuso il percorso; il daemon non interroga il modello
se non riesce a caricare la politica di revisione umana. Le negazioni e le
revoche successive restano chiuse anche durante una materializzazione
linguistica parziale. Le destinazioni sono valutate in ordine: una correzione
esplicita successiva prevale e una revoca finale vieta il riuso della
destinazione ricordata o predefinita, anche senza device registrati. La
correzione ha riaperto
RM-0005, perché il suo rapporto storico conservava un'ondata di migrazione mai
conclusa nonostante il closeout nominale. La risoluzione WinGet deduplica
invece le identita' canoniche e continua a negare la
proiezione mutante quando ne resta piu' di una o una non e' valida. Entrambe le
correzioni sono generali e provate; non autorizzano una firma legacy.

Le prove correnti del percorso lessicale, amministrativo e di destinazione
terminano con `129 passed`; la regressione i18n e dei consumer collegati
termina con `592 passed, 1.144 subtests passed`. Due revisioni avversariali
indipendenti terminano entrambe con `P0=0`, `P1=0`, `P2=0`; anche `git diff
--check` è verde.

Il registro device non contiene ancora un indirizzo autenticato. Di
conseguenza un alias non viene trasformato in IP usando memoria conversazionale
o log storici. Questa lacuna resta separata dalla porta Birth e non ne modifica
la sequenza, ma ogni sua futura correzione dovra' a sua volta passare dalla
porta unica.

### 23.10 Verifica live del prerequisito Birth per il checkpoint RM-0005

Il tentativo autorizzato di firmare i 21 builtin del checkpoint laterale ha
dimostrato una differenza fra certificazione del gruppo 2 e installazione viva:
il codice e' in modalita' `STORE_ONLY`, ma la macchina non contiene ancora la
radice preparata `$METNOS_USER_CONFIG/birth`. Il nuovo varco termina prima di
pubblicare con `birth_provisioning_io_unavailable`; il firmatario precedente
nega correttamente l'operazione e non costituisce un percorso alternativo.

La predisposizione richiede i registri pubblici indipendenti di approvazione e
revisione semantica definiti dal gruppo 2. Le chiavi private corrispondenti
devono essere scelte e custodite dall'operatore fuori dal processo Metnos; non
vengono generate implicitamente. Fino a questo passaggio RM-0005 resta
`reopened` e G6-B3 resta fermo al checkpoint gia' documentato. Dopo la
predisposizione si ripetono firma, catalogo, CI e smoke senza ridurre i gate.

### 23.11 Prerequisito laterale soddisfatto e ripresa di G6-B3

Le autorità operative indipendenti sono state create e custodite fuori dal
server; Birth ne consuma soltanto i registri pubblici. La radice preparata è
stata ricostruita dopo la correzione del verificatore delle proprietà, senza
riusare materiale non più coerente e conservando la radice precedente per
analisi. Il caricamento sigillato conferma l'insieme
`e79b9b5c0f1c0a44f072eaaf1905ee90040d18430184d76be6a151d42b4802c6`
e l'epoca
`sha256:d8845d364a8c346c450b7e0101cb0daba5c308299d7a1eee0937e61ab83e6571`.

Il collegamento produttivo possiede ora anche la riconciliazione del registro.
Il verificatore applica la proprietà di limite soltanto quando il contratto
dichiara sia l'ingresso di limite sia un'uscita di collezione leggibile dalla
macchina. Il generatore dei contratti incorporati può essere ripreso dopo
un'interruzione e non emette nuove ricevute per una generazione autenticata già
identica. L'esecuzione conclusiva ha pubblicato sei contratti e ne ha saltati
quindici già correnti; tutte le 21 firme sono valide.

Il gate laterale è verde: censimento `83 passed`, matrice firmata
`302 passed, 1.162 subtests passed` e perimetro Birth mirato
`51 passed, 1 skipped` per un caso non applicabile alla piattaforma. RM-0008
resta `active`, ma non è più fermo sul prerequisito RM-0005. Il commit pubblico
`97f38d9d46aa2bfaf6ab15a3a8ea1b93b9a44456` e il run GitHub Actions
`33309759454` sono verdi su Linux e Windows. Il percorso minimo riprende quindi
dalla fotografia systemd effettiva di G6-B3 descritta nell'handover.

Il gate della distribuzione riconosce 684 sorgenti privati con radice
`sha256:3089ab571fa2e8a2dbf09bd591492e628697c54d6dcfb507c674d52d17ded316`
e 672 sorgenti pubblici con radice
`sha256:057be58564833e198b491211bfbb222f8d0cde77bb3d4595e578b07719a1caf3`.
I tredici ingressi aggiunti sono esattamente le dodici partizioni del seme
lessicale e il censimento eseguibile; non sono state ammesse aggiunte implicite.

### 23.12 Fotografia systemd viva di G6-B3

Il terzo sottotaglio di G6-B3 è implementato localmente. Il preflight usa il
solo `systemctl` misurato, con argv, ambiente, timeout e limiti chiusi, e
costruisce l'intera fotografia effettiva da proprietà del manager, frammenti,
link di abilitazione e origini degli archi aggiunti. Tutti i file vengono
acquisiti senza seguire link e restano legati a percorso, identità, metadati e
byte. Drop-in, reload pendente, unità transient, origine non classificabile o
divergenza dalla proiezione firmata causano un diniego.

La lettura applica la sequenza `P0/S0/P1/S1/P2`, col solo killpoint di prova
fra `S0` e `P1`. Prerequisiti e fotografie devono essere identici; dopo il
confronto con gli hash firmati vengono rivalidati TCB, file, link e
prerequisito. Il wrapper prodotto rilegge inoltre la radice ownership e
confronta l'intera epoca selezionata, ignorando soltanto una claim successiva
ancora non autorevole. Il risultato è un tipo nominale non autorizzante e non
è consumato dal dispatch: il protocollo interprocesso del gate di avvio e
della manutenzione resta un confine successivo.

La matrice nuova termina con `8 passed`; insieme alla TCB amministrativa
termina con `52 passed`. Una lettura reale, non mutante, ha confermato la
versione supportata `255.4-1ubuntu8.17`. La prova con tutte le unità installate
resta assegnata alla VM usa-e-getta di G6-C e non deve essere anticipata sul
server gestito. Prima di consolidare il checkpoint vanno aggiornati il pin
source-review, le prove autonome e il profilo di esportazione, quindi creati e
pubblicati i commit incrementali su `main`.

Il consolidamento locale è concluso. Il preflight completo termina con
`249 passed`; guardia, manifesti e ownership terminano con
`206 passed, 1 skipped`; il catalogo builtin firmato termina con
`65 passed, 3 skipped`. L'omissione inventariale del helper di ripresa Birth è
stata classificata senza ampliare le sue capacità. I nuovi profili sono
`684 / sha256:2fe47e7d5b11358a7cc92877719a6f9da006718fe31ba0c00d90bafd7cae39da`
per il privato e
`672 / sha256:f662023198530f549d1977932a37cf4bd901b9fd524fd801ccb0a21ed1d57797`
per il pubblico. Il gate di esportazione è verde con zero dati personali,
segreti o file sensibili. Dopo commit e pubblicazione, il prossimo sottogruppo
è G6-C nella sola VM usa-e-getta; RM-0008 resta `active`.

La pubblicazione incrementale è conclusa nel commit privato `57b78c43` e nel
commit pubblico `5b2b3e658ed918695d14991fe80150a2cb875424`. Il run GitHub Actions
`33314651224` ha concluso verdi tutti gli otto job, compreso il riepilogo
bloccante. G6-B3 è quindi certificato; gli avvisi sulla versione Node usata
dalle azioni GitHub non hanno modificato l'esito. Il prossimo passo resta G6-C.

### 23.13 Primo incremento G6-C: programma amministrativo firmato

Il nuovo installatore G6-C consuma il record storico autenticato e il
descrittore di deployment già firmato sotto la sessione viva del deployment
lock. Esegue due verifiche complete della distribuzione attorno alla cattura
stabile dei byte, lega manifesto, descrittore, account e artefatti e pubblica
atomicamente soltanto `deployment/admin/preflight.py` nella radice
amministrativa fissa. Tutti gli artefatti `group7_cutover` sono verificati ma
restano nella distribuzione: nessuna unità è installata in questo incremento.

La destinazione esatta è idempotente e uno staging completo legato al
`descriptor_id` è promosso senza riscrittura. Stato parziale, file aggiunti,
modo errato o collisione terminano con recupero esplicito. Il percorso di prova
ha autorità e risultato nominalmente separati; Windows termina nel solo
`birth_ownership_platform_unsupported` prima di osservare il filesystem.

Le prove discriminanti terminano con `6 passed`; guardia e test connessi con
`77 passed`; la regressione mirata con `130 passed, 2 skipped`; preflight
autonomo e fotografia systemd precedente con `255 passed`. La politica
`birth-closed` riconosce esattamente cinque writer nuovi e nessun ampliamento
degli helper di lettura. G6-C resta aperto: il passo successivo è la cella
GitHub-hosted usa-e-getta con unità private pre-ribassate e firmate,
`daemon-reload`, diniego/ammissione reale e quarantena circoscritta. La suite a
copertura totale verrà eseguita soltanto prima della chiusura della fase.
I profili riesaminati sono 685 sorgenti private con radice
`sha256:8380d5b96ef25a8a8d41ad882935b89fb5a8ca5455bf59b5653049788c7c3136`
e 673 sorgenti pubbliche con radice
`sha256:173f218ca16987dbdc47598b5354fb5b1fbfd828b4a590eebe327adaf20c7c01`;
il gate sui 1.604 file esportati non rileva dati personali, segreti o file
sensibili.

La pubblicazione incrementale di G6-C1 è conclusa nel commit privato
`df5841c3` e nel commit pubblico
`5884f8ce0cad6890b551354c50a073bebdfb81d0`. Il run GitHub Actions
`33316187520` ha concluso verdi tutti i nove job e il riepilogo bloccante; i
soli avvisi riguardano la migrazione Node delle action. G6-C1 è certificato,
mentre G6-C resta `active` per la cella systemd reale e gli incrementi
successivi.

### 23.14 Secondo incremento G6-C: cella systemd firmata isolata

La capability privata G6-C2 nasce esclusivamente da record e ambiente di prova
autenticati, account firmato, sessione viva del deployment lock e namespace
casuale di 16 cifre esadecimali scelto prima della firma. Descrittore, catalogo,
copertura, manifesto e frammenti vengono verificati due volte; ogni entry, nome
e riferimento systemd deve appartenere allo stesso namespace. La copertura
firmata contiene almeno un servizio e un timer e coincide esattamente con gli
artefatti `group7_cutover`; non esiste rinomina post-firma.

L'installazione di prova rilegge integralmente la distribuzione prima di
osservare la radice unità, richiede una radice fisica ancorata `0755` con
proprietario esatto e un namespace completamente assente. Tutti i frammenti
sono scritti byte-identici in staging deterministici legati al descrittore,
sincronizzati e pubblicati con rename no-replace nei nomi già firmati.
Collisioni, sorgenti mutate e stati parziali richiedono recupero esplicito e
non attivano pulizia automatica. L'API produttiva continua a installare soltanto
il programma amministrativo C1 e non accetta la capability privata.

La prova reale riusa il certificatore Linux root già presente nel workflow
2A congelato: su una VM GitHub-hosted usa-e-getta installa il programma
amministrativo e le unità casuali in `/etc/systemd/system`, esegue
`systemctl daemon-reload`, verifica `FragmentPath` e byte, poi rimuove soltanto
gli oggetti elencati dal descrittore e ricarica il manager. Il server gestito
non è stato modificato. La matrice locale connessa termina con
`292 passed, 1 skipped`; il nucleo mirato finale con `82 passed, 1 skipped`.
Il profilo rimane di 685 sorgenti private con radice
`sha256:5f8f775d828a2a0030c7f438dc40c27a51c4dcdd67d8cab337e08a76b8c13971`
e 673 pubbliche con radice
`sha256:c0aa838eae3d375e8f3acf532b56c784c65f4793e83ec8c341288412d8e2648d`;
il gate sui 1.604 file esportati rileva zero PII, segreti o file sensibili.

I commit privati sono `2b835d48` e `43961cf7`; la proiezione pubblica finale è
`bdd58a5765ae9965e8fa5d56ed79cec932ef7211`. Il primo tentativo pubblico
`88d2d05` ha dimostrato il diniego del workflow 2A congelato ed è stato
superato riusando, senza modificarla, la cella root già certificata. Il run
GitHub Actions finale `33318421582` ha concluso verdi tutti i nove job,
compreso il riepilogo bloccante; le sole annotazioni sono gli avvisi Node delle
action. G6-C resta `active`: il passo successivo è C3, con avvio reale,
diniego senza prerequisito e ammissione con il gate firmato.

### 23.15 Terzo incremento G6-C: attivazione systemd reale

La cella `tests/portable/test_executor_birth_systemd_activation.py` costruisce
una distribuzione firmata completa, installa il programma amministrativo e due
unità isolate, acquisisce la fotografia TCB/systemd e ricostruisce il grafo di
proprietà canonico. Prima del prerequisito nega sia l'avvio diretto sia il
timer reale; dopo il prerequisito prova diniego per identità applicativa,
ammissione causale attraverso il timer reale, credenziali e argv firmati,
soli descrittori 0/1/2, `NoNewPrivs=1`, capability azzerate, namespace mount
vivo, acquisizione del gate startup esclusivo e rimozione circoscritta.

Il primo run pubblico ha rilevato due difetti di fixture, entrambi corretti:
import POSIX a raccolta Windows e dipendenza da un inventario privato escluso
dall'export. Il secondo run ha mostrato che systemd 255 non espone le
proprietà nominali `References` e `ReferencedBy`; il profilo è stato allineato
all'interfaccia reale.

Il terzo run `33321931596` è rimasto rosso su
`PreflightError: systemd property set`. Causa accertata per misura diretta su
systemd 255.4, con l'argv esatto costruito dal modulo
(`--no-pager --plain --all show --property=...`): **una collezione di timer
priva di voci non viene resa come riga vuota, viene omessa del tutto**; solo
scalari e liste rendono il vuoto. Il piano dichiarava invece
`TimersCalendar` con cardinalità `max(1, calendar_count)`, quindi attendeva un
valore che nessun timer senza `OnCalendar` può esporre. La cardinalità è ora
il conteggio reale: una collezione a zero voci non entra nell'insieme atteso.

Nello stesso incremento i due dinieghi sulle proprietà systemd nominano la
differenza osservata — nomi mancanti, nomi inattesi, attesa e osservata per la
cardinalità. `detail` non raggiunge stderr; i nomi di proprietà non sono
payload, i valori restano fuori. Un rifiuto muto costava un giro completo di
CI per ogni ipotesi, ed è quanto è accaduto due volte su tre run.

Due celle portabili fissano la resa misurata e i dinieghi parlanti. La radice
sorgenti privata è stata ricalcolata dopo la modifica dei byte di runtime:
686 sorgenti, radice
`sha256:56f827a48be59b878af9b69f068bc49910ead6f60e7c2f83409fe735e8ea046c`,
allineata nei quattro punti che la vincolano (preflight, guard di confine,
inventario di confine, `publish-public.sh`).

Commit privato `eee22bf7`. Matrice mirata: 303 superati, 4 saltati. Matrice
portabile completa: 8 rossi, tutti già presenti sulla base `31954334` e
legati a `root` o all'ambiente CI; la base ne aveva 9, e il nono era proprio
l'attestazione di sorgente ora riallineata. G6-C resta `active` fino
all'esito verde del prossimo run pubblico; il passo successivo è C4.

Il quarto run `33323084847` ha superato il diniego sulle proprietà e si è
fermato più avanti, su `systemd origin identity`. Causa riprodotta per misura
locale su systemd 255.4: **una relazione può nominare un'unità che non
esiste**; systemd conserva l'arco e riporta il bersaglio come `not-found`
(osservato `After=network.target` sul manager utente). La cattura pretendeva
invece `LoadState=loaded` per ogni unità raggiunta, rendendo impossibile
costruire il grafo canonico su qualunque sistema reale. Il nodo assente è ora
un quarto tipo di origine, simmetrico a `manager_virtual`: nessun file,
nessun proprietario, nessuno stato, `load_state` vincolato a `not-found`.
L'evidenza non si perde, perché la comparsa successiva di quell'unità cambia
la fotografia effettiva invece di nascondersi dentro.

Nello stesso run il job Windows è diventato rosso con «rustup could not choose
a version of cargo to run»: la deviazione della home introdotta da `31954334`
per isolare i test portabili faceva perdere a rustup il proprio toolchain.
`tests/portable/conftest.py` fissa ora `RUSTUP_HOME` e `CARGO_HOME` alla loro
posizione reale prima di spostare la home; è ambiente rotto, non diniego sotto
prova.

I job 2A dello stesso run sono abortiti prima di eseguire una sola cella con
`frozen acceptance baseline differs from the pre-fix commit;
changed=['tests/portable/conftest.py']`. La base congelata 2A è quindi da
rifotografare: il file è stato modificato da `31954334` e nuovamente da questo
incremento. Finché la fotografia non viene rifatta, metà del workflow non
produce informazione.

Commit privati `90a3b01f` (unità assente, diagnostica parlante, rustup) oltre
a `eee22bf7`, `01602dfc`, `8d15ac43`, `d598d3be`. La pubblicazione resta
rinviata finché un secondo agente ha lavoro non committato nell'albero
condiviso: l'export legge il filesystem e prenderebbe il suo incremento a
metà. Il perno della revisione sorgenti va ricalcolato a ogni pubblicazione,
perché ogni commit che tocca una radice censita lo invalida per tutti.

Il quinto run `33323601852` ha superato anche il diniego sull'unità assente e
si è fermato su `duration component`. Terza causa misurata sulla stessa
interfaccia: **una durata pari a zero viene resa senza suffisso**
(`RandomizedDelayUSec=0`, `WatchdogUSec=0`) e **una illimitata come la parola
`infinity`** (`JobTimeoutUSec`), su proprietà che nessuna unità ha mai
impostato. Entrambe sono token canonici singoli che la grammatica a componenti
non può analizzare.

La distinzione introdotta è di sostanza, non di comodo: un direttivo firmato e
una proprietà viva non sono lo stesso ingresso. Ciò che un catalogo può
dichiarare resta stretto — `infinity` continua a essere rifiutato lì, perché un
timeout illimitato è una politica che un autore non deve poter firmare — mentre
ciò che systemd riporta non è una scelta, e i due siti di osservazione passano
ora `observed=True`.

Le tre cause di C3 hanno la stessa forma: un'assunzione non misurata su come
systemd rende la propria interfaccia, e un diniego muto che costava un giro di
CI per ipotesi. I dinieghi ora nominano la differenza osservata, ed è la
ragione per cui la terza causa è stata isolata in locale invece che in CI.

Commit privati `1fb9d8ce` (durata osservata) e `074efbde` (radice pubblica);
proiezione pubblica `b607fe0`. Radici: privata 687 sorgenti
`sha256:a12caa5261967255a041e8687eacacf479d2eb56ef970ac7e621634050846d59`,
pubblica 675 sorgenti
`sha256:f9a69dcee5c88c7ea5ddbb83e0cd7cac10e904b7ef5deab4fb560674789c96fe`.

Dal quinto incremento il lavoro G6 procede in un worktree separato
(`/tmp/metnos-rm0008-g6`, detached) e rientra in `main` per cherry-pick: due
agenti sullo stesso albero si invalidano a vicenda il perno della revisione
sorgenti, e l'export legge il filesystem, quindi chi pubblica mentre l'altro
edita si porta dietro lavoro a metà. È costato tre rinvii di pubblicazione.

### 23.16 Vincoli misurati prima di scrivere C4

Le quattro cause di C3 erano tutte assunzioni mai misurate su come systemd
rende la propria interfaccia, e ognuna è costata un giro di CI da otto minuti
perché la cella che le avrebbe scoperte richiede `root`. Le celle di
riproduzione esistono già e girano senza privilegi, ma le osservazioni che
riproducono sono **scritte a mano**: contengono le stesse assunzioni del
codice, quindi concordano sempre col difetto. Solo systemd vero dissente.

Prima di scrivere C4 le tre relazioni che la prova deve asserire sono state
misurate su systemd 255.4, la stessa versione maggiore del runner, con unità
d'utente usa-e-getta. Due risultati invalidano la procedura come descritta
nel passaggio di consegne:

1. **`TriggeredBy` non compare dopo `daemon-reload`.** Il servizio resta con
   `TriggeredBy=` vuoto finché il timer non viene **avviato**; solo allora
   l'arco inverso si materializza. La baseline positiva di C4 va quindi
   asserita dopo l'ammissione, non subito dopo l'installazione.
2. **`ConflictedBy` non compare né dopo `daemon-reload` né avviando
   un'ausiliaria `oneshot` ordinaria.** systemd carica le unità pigramente e
   scarica subito una `oneshot` inattiva senza riferimenti, e con essa
   spariscono i suoi archi. L'arco inverso appare **solo finché l'ausiliaria
   resta caricata**: con `RemainAfterExit=yes` il candidato mostra
   `ConflictedBy=<ausiliaria>`. Il caso differenziale di C4 deve quindi
   mantenere residente l'unità ausiliaria, e il `finally` deve fermarla prima
   di rimuoverla.
3. `Triggers` sul timer è invece presente già dopo `daemon-reload`.

Costo della misura: cinque minuti in locale. Costo che avrebbe avuto in CI:
due giri, entrambi sulla cella root-only.

**Ottimizzazione adottata, senza toccare il progetto**: le osservazioni di
riferimento vanno catturate da systemd vero e riprodotte, non inventate. Ciò
non allenta nessuna prova — la cella reale resta l'autorità — ma sposta la
scoperta della classe di difetto dominante dal canale da otto minuti a quello
da due.
