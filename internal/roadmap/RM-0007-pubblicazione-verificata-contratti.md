# RM-0007 — Pubblicazione verificata delle varianti linguistiche dei contratti

> `RM-0007` · stato `active` · definita `2026-08-24` · revisione KISS
> `2026-08-25` · **controrevisione esterna eseguita il 2026-08-25: approvabile
> con quattro rilievi bloccanti di specifica (§17)** · implementazione vietata
> fino alla risoluzione di B1-B3 e alla decisione di Roberto su B4 · proposta
> ADR 0223 · documento interno

## 1. Esigenza

RM-0007 impedisce che una traduzione di un contratto executor diventi, di
fatto, un aggiornamento tecnico non autorizzato o lasci sul disco una
combinazione incoerente di manifest, firma e stato linguistico.

Il percorso corrente presenta difetti concreti:

1. `_promote_contracts()` modifica il manifest vivo prima che l'attivazione
   abbia concluso tutti i controlli;
2. `sign_executor()` ricalcola il digest dal codice presente in quel momento e
   può quindi adottare una modifica tecnica estranea alla traduzione;
3. manifest, firma e stato linguistico vengono scritti separatamente;
4. `verify_executor()` verifica una lettura del manifest e poi lo riapre per
   parsificarlo;
5. il loader effettua altre letture indipendenti;
6. il candidato registra la base da cui deriva, ma il publisher non la
   confronta prima della scrittura;
7. due promotori possono sovrascriversi;
8. un percorso del registro i18n viene usato direttamente come destinazione;
9. l'allineatore legacy resta un secondo scrittore;
10. le prove di attivazione sostituiscono il validatore reale con un doppio che
    accetta sempre.

Il rapporto storico
`internal/reports/rm0002-linter-manifest-multilingue-audit-20260824.md`
conserva prove, fotografie e revisioni avversariali.

### Garanzia richiesta

> Una variante linguistica parte da un manifest già verificato, può modificare
> soltanto i testi autorizzati della lingua bersaglio, conserva il digest del
> codice e diventa visibile insieme alla propria firma e al proprio stato. Un
> lettore usa esattamente i byte del manifest che ha verificato.

Questa è la garanzia minima che serve per rendere bloccanti i controlli di
RM-0002 senza trasformare RM-0007 in un nuovo sistema di packaging.

## 2. Decisione KISS candidata

La prima stesura proponeva un deposito generale di contratti e codice,
envelope e ricevute firmati, binding della release, un nuovo runner, recupero
automatico e un inventario universale. Era una soluzione coerente, ma
rispondeva a tre problemi distinti:

- pubblicazione linguistica sicura;
- deploy tecnico atomico di manifest e codice;
- identità dei byte eseguiti fino al momento dell'invocazione.

RM-0007 conserva soltanto il primo. La versione candidata usa:

1. una fotografia immutabile dei byte verificati;
2. firma e verifica come operazioni pure in memoria;
3. una generazione immutabile contenente tre file;
4. un solo puntatore atomico alla generazione corrente;
5. un blocco per contratto e il confronto della generazione attesa;
6. una sola API per la pubblicazione linguistica;
7. un importatore semplice di sorgenti tecniche già firmate e ammesse.

Non aggiunge un database autorevole: il problema e il formato pubblico sono
file-oriented e un secondo registro transazionale introdurrebbe un'autorità
nuova. Non aggiunge una seconda firma: la firma del manifest autentica il
contratto e il suo digest; l'hash della generazione impedisce di mescolare per
errore i tre file.

## 3. Confine e proprietà

### 3.1 Compreso

- manifest, firma e `manifest.lang_state.json`;
- varianti linguistiche prodotte dalla pipeline RM-0005;
- sorgenti tecniche già firmate prodotte da installer, generatori o
  importatori esistenti;
- fotografia verificata consumata dal loader;
- concorrenza fra scrittori Metnos cooperanti;
- arresto del processo durante la pubblicazione;
- Linux su filesystem locale e Windows su NTFS locale;
- migrazione senza doppia scrittura del contratto vivo.

### 3.2 Escluso

- copia del codice dentro la generazione;
- garanzia che i byte eseguiti molto tempo dopo il caricamento siano ancora
  quelli verificati;
- nuovo runner, package manager o formato di release;
- pubblicazione atomica del codice tecnico;
- transazione di una lingua intera o dell'intero catalogo;
- protezione da un processo arbitrario eseguito come lo stesso utente Metnos;
- filesystem di rete, FAT e supporti non certificati;
- raccolta automatica delle generazioni non referenziate;
- regole linguistiche, qualità semantica e localizzazione di `affinity`.

### 3.3 Rischio residuo dichiarato

Prima di pubblicare una traduzione, RM-0007 verifica che il codice presente
coincida con il digest già firmato e conserva quel digest senza ricalcolarlo nel
manifest. Se il codice cambia in seguito, il caricamento fallisce per digest
non valido: la traduzione non lo autorizza.

Il runner corrente può però riaprire quel percorso dopo la verifica. Legare
fino all'invocazione i byte di processi, builtin e bundle remoti richiede
censimento delle dipendenze, codice content-addressed o binding della release e
modifica dei runner. Questo lavoro è separato in `EXEC-BIND-001`; attribuirlo a
RM-0007 renderebbe falsa la qualificazione KISS.

### 3.4 Invarianti non negoziabili

1. La pubblicazione linguistica non calcola un nuovo digest del codice.
2. La base viene verificata prima di applicare la traduzione.
3. Soltanto selettori censiti della lingua bersaglio possono cambiare.
4. Al commit, codice e digest devono ancora coincidere con la base firmata.
5. Manifesto parsificato e manifesto firmato provengono dagli stessi byte.
6. Una generazione pubblicata non viene modificata.
7. Il puntatore cambia soltanto dopo che la generazione è completa e verificata.
8. Il candidato viene rifiutato se la generazione corrente non è più quella
   attesa.
9. Il registro i18n non concede percorsi né autorità di firma.
10. Un errore lascia corrente l'ultima generazione valida.
11. Nessuna regola dipende dal nome dell'executor o da una lingua specifica.

## 4. Modello persistente minimo

### 4.1 Layout

```text
PATH_USER_STATE/contract-publications/v1/
  <contract-key>/
    writer.lock
    current
    generations/
      <generation-id>/
        manifest.toml
        manifest.toml.sig
        manifest.lang_state.json
```

Durante la preparazione si usa una directory temporanea senza nome autorevole
nella stessa directory di `generations/`. Non serve uno staging persistente né
una procedura automatica di raccolta.

`contract-key` deriva dall'identità canonica restituita dall'inventario comune
di RM-0002. Il registro i18n fornisce un'identità di risorsa, mai un path di
destinazione. Il deposito rifiuta componenti `..`, symlink o reparse point del
deposito, file non regolari e percorsi risolti fuori dalla radice configurata.

`current` contiene una sola riga:

```text
sha256:<64 cifre esadecimali>
```

Non contiene JSON, path, lingua, nome executor o duplicati dello stesso dato.

### 4.2 Identificatore della generazione

L'identificatore è il digest dei tre file in ordine fisso. Per evitare
ambiguità di concatenazione:

```python
def generation_id(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in (
        "manifest.toml",
        "manifest.toml.sig",
        "manifest.lang_state.json",
    ):
        payload = files[name]
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return "sha256:" + digest.hexdigest()
```

Il lettore ricalcola sempre il digest. Una directory già esistente con lo
stesso identificatore ma byte diversi è corruzione e blocca il contratto.

### 4.3 Autorità dello stato linguistico

`manifest.lang_state.json` conserva provenienza e hash utili al workflow
RM-0005. Non decide quale executor venga caricato e non richiede una seconda
firma. È incluso nella generazione per evitare che il workflow osservi uno
stato appartenente a un manifesto diverso.

Lo stato deve almeno conservare, per selettore canonico e lingua:

- `version_hash` del testo pubblicato;
- `source_lang`;
- `source_hash`.

Non contiene `generation_id`. Se il processo si arresta dopo il cambio del
puntatore ma prima dell'aggiornamento del registro, il retry confronta questi
hash con il candidato e completa la riconciliazione in modo idempotente.

## 5. Contratti Python minimi

Tutto il deposito appartiene a un solo nuovo modulo,
`runtime/contract_store.py`. Non creare moduli distinti per snapshot, lock,
recovery, envelope o receipt.

### 5.1 Fotografia verificata

```python
@dataclass(frozen=True, slots=True)
class VerifiedManifest:
    contract_id: ContractId
    generation_id: str | None
    source_root: Path
    manifest_bytes: bytes
    manifest_hash: str
    parsed: Mapping[str, object]
    signature_bytes: bytes
    signature_hash: str
    language_state_bytes: bytes
    language_state: Mapping[str, object]
    signed_by: str
    declared_code_digest: str
    verified_code_digest: str
```

La funzione di verifica:

1. legge una volta i byte del manifest;
2. parsifica quegli stessi byte;
3. verifica la firma su quegli stessi byte;
4. ricava `code.files` dal mapping già parsificato;
5. richiede che identità del manifest, `ContractId` e riferimento
   dell'inventario coincidano;
6. calcola il digest attraverso le radici ammesse associate a `ContractId`;
7. verifica lo stato e, per una generazione, il suo identificatore;
8. restituisce il valore immutabile.

Il loader usa `VerifiedManifest.parsed` e non riapre `manifest.toml`.

### 5.2 Firma pura

In `runtime/sign.py`:

```python
def sign_manifest_bytes(manifest_bytes: bytes, *, key_name: str) -> bytes: ...

def verify_manifest_bytes(
    manifest_bytes: bytes,
    signature_bytes: bytes,
    *,
    trusted_publics: Iterable[TrustedPublic],
) -> SignerIdentity: ...
```

Entrambe sono pure rispetto al filesystem. `sign_executor()` resta un
involucro per le sorgenti di authoring e usa le primitive pure; la pubblicazione
linguistica non lo chiama.

### 5.3 API pubblica

```python
def current_manifest(contract_id: ContractId) -> VerifiedManifest: ...

def publish_localization(
    contract_id: ContractId,
    *,
    expected_generation_id: str,
    source_language: str,
    target_language: str,
    patches: tuple[LocalizationPatch, ...],
) -> PublicationResult: ...

def publish_signed_source(
    ref: ManifestRef,
    *,
    expected_generation_id: str | None,
) -> PublicationResult: ...

def rollback(
    contract_id: ContractId,
    *,
    expected_generation_id: str,
    target_generation_id: str,
    reason: str,
) -> PublicationResult: ...
```

`publish_signed_source()` non genera codice, non lo copia e non ricalcola il
digest nel manifest. Importa nel deposito una sorgente già firmata, conforme e
con digest verificato. Consente a installer, generatori e importatori di
continuare a produrre file di authoring committabili su Git senza farli leggere
come contratto vivo dopo il cutover.

`PublicationResult` contiene soltanto `contract_id`, generazione precedente,
generazione corrente, operazione e `repeated`. Non è firmato e non diventa una
seconda autorità.

## 6. Algoritmo di pubblicazione linguistica

Traduzione e revisione semantica avvengono prima. Il publisher non invoca LLM.

1. normalizzare le lingue con `i18n_registry.normalize_language()`;
2. risolvere `ContractId` e radici ammesse tramite l'inventario comune;
3. acquisire `writer.lock` con timeout finito;
4. leggere `current` una sola volta;
5. se non coincide con `expected_generation_id`, verificare se la generazione
   corrente contiene già esattamente le patch richieste: in tal caso restituire
   `repeated=True`; altrimenti `commit_conflict`;
6. caricare e verificare integralmente la generazione corrente;
7. confrontare hash della sorgente, del precedente bersaglio e del candidato;
8. applicare le patch soltanto in memoria;
9. confrontare strutturalmente base e candidato dopo avere rimosso unicamente i
   valori bersaglio dei selettori autorizzati;
10. richiedere che `[code].files` e `[code].digest` siano identici;
11. ricalcolare il digest dal codice corrente e richiedere che coincida ancora
    con quello firmato;
12. eseguire standard executor, linter della lingua bersaglio e controlli del
    candidato sui byte preparati;
13. firmare i nuovi byte in memoria e riverificare la firma;
14. aggiornare in memoria il solo stato linguistico pertinente;
15. scrivere i tre file in una directory temporanea sullo stesso volume;
16. ricaricare e verificare la directory temporanea;
17. sincronizzare i file e chiudere tutti gli handle;
18. rinominare la directory in `generations/<generation-id>`;
19. rendere persistente la directory `generations` dove la piattaforma lo
    consente;
20. scrivere e sincronizzare un puntatore temporaneo;
21. sostituire `current` con `os.replace()`;
22. rendere persistente la directory del contratto dove la piattaforma lo
    consente;
23. rilasciare il lock;
24. riconciliare il registro RM-0005 dagli hash dello stato corrente.

Ogni errore prima del punto 21 lascia invariato `current`. Un errore dopo il
punto 21 non annulla una pubblicazione già visibile: il retry la riconosce e
completa la riconciliazione.

### 6.1 Blocco portabile

Il lock è implementato nello stesso `contract_store.py`:

- file permanente, mai cancellato come protocollo di rilascio;
- Linux: `fcntl.flock`;
- Windows: `msvcrt.locking` sul primo byte di un file regolare;
- tentativi non bloccanti fino a una scadenza monotona;
- errore stabile `lock_timeout`;
- nessun lock basato sulla sola esistenza del file.

Tutti gli scrittori Metnos che possono cambiare la sorgente tecnica dello stesso
contratto acquisiscono il medesimo lock. Il lock serializza gli scrittori
cooperanti; `expected_generation_id` impedisce di usare un candidato preparato
prima dell'acquisizione e ormai obsoleto.

### 6.2 Importazione di una sorgente firmata

`publish_signed_source()` esegue sotto lo stesso lock:

1. confronta la generazione corrente con quella attesa;
2. legge una sola fotografia di manifest, firma e stato dalla sorgente;
3. verifica identità, firma, digest, standard executor e ammissione tecnica;
4. non modifica né rifirma i byte ricevuti;
5. crea e verifica la generazione con l'algoritmo di §4.2;
6. pubblica il puntatore con i punti 15-22 di §6.

Una sorgente modificata durante la lettura non viene ammessa: firma, digest o
seconda verifica della directory preparata falliscono. Gli strumenti che
modificano in posto una sorgente già ammessa devono acquisire `writer.lock`;
gli strumenti che producono una directory nuova possono lavorare fuori dal
lock e acquisirlo soltanto attraverso `publish_signed_source()`.

## 7. Lettura, sorgenti e ripristino

### 7.1 Loader

Per un contratto migrato il loader:

1. legge `current` una sola volta;
2. valida la forma dell'identificatore;
3. carica la directory immutabile indicata;
4. ricalcola l'identificatore e costruisce `VerifiedManifest`;
5. costruisce l'executor da `VerifiedManifest.parsed`.

Un avvio a freddo con puntatore malformato, generazione assente o firma non
valida rifiuta quel contratto. Non sceglie “la directory più recente” e non
ripiega silenziosamente sulla sorgente di authoring.

### 7.2 Sorgenti tecniche

Manifest e codice nel repository, negli import o nelle directory di generazione
restano sorgenti di authoring. Possono essere creati, provati e firmati con gli
strumenti esistenti. Diventano vivi soltanto attraverso
`publish_signed_source()`.

Questo evita un divieto indiscriminato di `write_text()`: la guardia statica
deve vietare scritture dentro il deposito e letture vive dalle sorgenti per i
contratti migrati, non impedire a generatori e installer di preparare artefatti.

### 7.3 Ripristino

`rollback()` acquisisce il lock, verifica che `current` coincida con la
generazione attesa, verifica integralmente la generazione scelta e sostituisce
atomicamente il puntatore. Registra attore e motivo nel normale audit operativo.
Non copia file e non modifica la generazione precedente.

Staging incompleti e generazioni complete non referenziate vengono segnalati da
una diagnostica in sola lettura. La prima versione non li elimina.

## 8. Modifiche file per file

| File | Modifica obbligatoria | Vietato |
|---|---|---|
| `runtime/contract_store.py` | unico nuovo modulo: snapshot, lock, generazioni e pubblicazione | framework di plugin, DB o seconda firma |
| `runtime/sign.py` | primitive pure; adattatore di authoring | firma linguistica che ricalcola il digest |
| `runtime/loader.py` | mapping dello snapshot per i contratti migrati | riaprire il manifest o ripiegare in silenzio |
| `runtime/i18n_pipeline.py` | costruire patch e chiamare `publish_localization()` | scrivere manifest o firma vivi |
| `runtime/i18n_activation.py` | validatore reale sulla lingua e sullo snapshot | validatore sempre positivo nel test completo |
| `runtime/i18n_translator.py` | allineatore ritirato o delegato | secondo publisher linguistico |
| installer, generatori e importatori | `publish_signed_source()` dopo firma e ammissione | rendere viva una sorgente per semplice scrittura |
| `runtime/manifest_inventory.py` | dipendenza comune posseduta da RM-0002 | seconda scansione dentro RM-0007 |

## 9. Ordine di implementazione

Ogni fase corrisponde a un commit autonomo. Nessuna implementazione deve
iniziare prima della controrevisione esterna e della promozione di RM-0007 a
`ready`.

### M0 — Caratterizzazione e inventario

- aggiungere prove dei difetti elencati in §1;
- censire dinamicamente lettori e scrittori vivi;
- completare l'inventario condiviso previsto da RM-0002 L2;
- creare fixture Linux/Windows per generazione, conflitto e arresto.

**Gate:** ogni difetto ha una prova causale; nessun conteggio del catalogo è
cablato.

### M1 — Fotografia e firma pura

- estrarre le primitive pure in `sign.py`;
- implementare `VerifiedManifest` sul layout legacy;
- fare usare al loader gli stessi byte verificati dietro flag spento;
- aggiungere una prova di attivazione col validatore reale.

**Gate:** sostituire A con B fra le letture non produce combinazioni; catalogo
legacy invariato; primitive pure senza accessi al filesystem.

### M2 — Deposito minimo

- implementare generazione, puntatore, lock e diagnostica in
  `contract_store.py`;
- implementare `publish_signed_source()` e generazione iniziale;
- provare conflitto, idempotenza e i confini di arresto;
- non modificare ancora il loader di produzione.

**Gate:** da una stessa base committa un solo writer; il puntatore indica
sempre una generazione completa; prova reale Linux e Windows.

### M3 — Pubblicazione linguistica

- implementare patch, confronto strutturale e digest preservato;
- collegare la pipeline RM-0005;
- delegare o ritirare l'allineatore;
- distinguere traduzione pubblicata da lingua attivata.

**Gate:** nessuna differenza tecnica viene firmata; candidato obsoleto e
validatore fallito lasciano invariato il puntatore; il retry post-commit è
idempotente.

### M4 — Migrazione e cutover

- creare generazioni iniziali soltanto da sorgenti valide;
- confrontare catalogo legacy e versionato in modalità ombra;
- migrare i produttori a `publish_signed_source()`;
- attivare il loader a generazioni;
- aggiungere la guardia statica descritta in §7.2;
- provare ripristino, aggiornamento e riavvio.

**Gate:** nessun contratto mescola layout; nessun secondo publisher linguistico;
nessun executor scompare; due cicli completi verdi.

## 10. Prove obbligatorie

### 10.1 Autorità e differenza

- firma o digest della base errati;
- modifica di schema, capability, placement, policy, undo o codice;
- modifica di una lingua diversa;
- selettore non registrato o appartenente a un altro contratto;
- sorgente o bersaglio cambiati dopo la candidatura;
- path del registro fuori dall'inventario;
- lingua BCP-47 non canonica o collidente.

### 10.2 Coerenza e concorrenza

- manifest parsificato dagli stessi byte firmati;
- file mancante, aggiunto o modificato nella generazione;
- due promotori dalla stessa generazione: uno solo riesce;
- stesso candidato ripetuto dopo il commit: successo idempotente;
- candidato diverso con base vecchia: conflitto;
- loader continuo: soltanto generazione vecchia o nuova;
- scrittore tecnico cooperante contro publisher linguistico.

### 10.3 Arresti e piattaforme

Terminare un processo reale:

1. durante la scrittura della directory temporanea;
2. dopo il rename della generazione;
3. dopo la scrittura del puntatore temporaneo;
4. dopo `os.replace()` e prima della riconciliazione.

Dopo il riavvio, `current` indica la vecchia o la nuova generazione completa.
Le prove vengono eseguite su Linux locale e Windows NTFS reale, non soltanto
con mock. Il rapporto distingue crash del processo da garanzia contro perdita
di alimentazione.

### 10.4 Integrazione

- validatore reale nella lingua bersaglio;
- fallimento pre-commit senza modifica visibile;
- pubblicato ma non attivato dichiarato come dormiente;
- sorgente tecnica modificata non diventa viva senza pubblicazione;
- generazione iniziale rifiutata se la sorgente non è valida;
- rollback solo verso generazione verificata;
- registro riconciliato dopo arresto post-commit;
- cache invalidata da `generation_id`, non dalla combinazione di `mtime`.

## 11. Rischi

| Rischio | Gravità | Contromisura |
|---|---:|---|
| traduzione adotta modifica tecnica | bloccante | base e digest verificati, firma pura |
| verificato A, caricato B | bloccante | fotografia unica |
| manifest, firma e stato incoerenti | bloccante | generazione immutabile e puntatore unico |
| aggiornamento perso | alta | lock e generazione attesa |
| path del registro diventa autorità | alta | `ContractId` dall'inventario |
| writer legacy resta vivo | alta | cutover e guardia statica mirata |
| puntatore corrotto al cold boot | alta | rifiuto del contratto, nessuna scelta euristica |
| codice cambia dopo la verifica | alta, residua | digest rifiutato al load; EXEC-BIND-001 per garanzia forte |
| semantica Windows diversa | alta | prova NTFS reale |
| crescita dello spazio | media | rapporto; nessuna GC prematura |

## 12. Criteri di completamento

RM-0007 passa a `implemented` soltanto quando:

- la localizzazione non ricalcola il digest;
- la base viene verificata prima della modifica e nuovamente sotto lock;
- il loader interpreta gli stessi byte verificati;
- manifest, firma e stato diventano visibili come una generazione;
- un candidato obsoleto non cambia il puntatore;
- due scrittori non perdono aggiornamenti;
- il registro i18n non concede path;
- il validatore reale è coperto dall'attivazione;
- l'allineatore non è un secondo publisher;
- le sorgenti non diventano vive senza `publish_signed_source()`;
- crash e concorrenza sono provati su Linux e Windows;
- una pubblicazione fallita non rende indisponibile la generazione precedente;
- non esistono eccezioni per executor o lingua.

Passa a `closed` dopo cutover sull'installazione di riferimento,
documentazione operativa e rapporto finale. Fino alla controrevisione esterna
resta `active`.

## 13. Rinviato esplicitamente

Non appartengono a RM-0007:

- codice copiato o content-addressed nelle generazioni;
- nuovo runner locale o protocollo dei bundle remoti;
- binding dell'intera release dei builtin;
- pubblicazione tecnica atomica di manifest e codice;
- envelope e seconda firma;
- ricevute crittografiche;
- database come puntatore autorevole;
- rollback mediante duplicazione della generazione;
- transazioni multi-contratto;
- raccolta automatica e deduplicazione;
- supporto di filesystem di rete;
- isolamento della chiave in un servizio separato.

Un elemento rinviato rientra soltanto se il revisore dimostra che, senza di
esso, una delle invarianti di §3.4 non può essere soddisfatta.

## 14. Istruzioni per l'implementatore

1. Leggere questo documento, ADR 0223, ADR 0220 e il rapporto di audit.
2. Implementare una sola fase M0-M4 per commit.
3. Scrivere prima la prova che riproduce il difetto della fase.
4. Non aggiungere componenti elencati in §13.
5. Non usare path provenienti dal registro o dal candidato.
6. Non introdurre nomi di executor, lingue o conteggi nel codice.
7. Non vietare genericamente le scritture nelle sorgenti di authoring.
8. Non mantenere due publisher linguistici.
9. Non invocare LLM mentre il lock è acquisito.
10. Non riaprire il manifest dopo avere costruito `VerifiedManifest`.
11. Non usare il ripiego legacy per un contratto già migrato.
12. Fermarsi se una fase richiede di allentare firma, digest, contenimento o
    confronto della generazione attesa.
13. Registrare per ogni fase commit, prove e risultato.

## 15. Mandato per la controrevisione esterna

Il revisore deve tentare di confutare la proposta e classificare ogni rilievo
come bloccante, rischio accettabile, hardening futuro o preferenza stilistica.

1. La garanzia ristretta chiude davvero i difetti che bloccano RM-0002 L5?
2. Quale difetto richiede necessariamente di copiare il codice nella
   generazione?
3. `manifest.lang_state.json` deve essere nella generazione oppure può essere
   ricostruito senza perdere provenienza e idempotenza?
4. Una seconda firma proteggerebbe da un guasto non già coperto dalla firma del
   manifest e dall'hash della generazione?
5. Lock e `expected_generation_id` rimuovono due fallimenti distinti? Sono
   entrambi necessari?
6. Il blocco Windows basato su `msvcrt.locking` soddisfa la concorrenza reale su
   NTFS?
7. `os.replace()` e i flush descritti distinguono correttamente atomicità,
   crash del processo e perdita di alimentazione?
8. Il retry dopo commit ma prima della riconciliazione è realmente idempotente
   senza una ricevuta persistente?
9. Il cutover impedisce a uno stesso contratto di essere letto o scritto nei
   due layout?
10. `publish_signed_source()` è sufficiente per installer, Synt, importatori e
    promoter senza diventare un publisher tecnico incompleto?
11. Il caricamento dei builtin con file `../../...` può usare radici ammesse
    generali senza eccezioni nominali?
12. Quale componente rimasto può essere eliminato senza perdere una invariante?
13. Quale componente rinviato è invece indispensabile alla v1?
14. Esiste una soluzione più semplice che superi tutte le prove M0?

Il revisore deve inoltre indicare una stima separata per nucleo, migrazione e
certificazione Windows. La stima precedente di 7–12 giorni è ritirata perché
riferita a un perimetro diverso.

## 16. Registro

| Data | Stato | Evento |
|---|---|---|
| 2026-08-24 | `ready` | prima specifica a generazioni, giudicata troppo ampia |
| 2026-08-25 | `active` | perimetro ridotto alla pubblicazione linguistica; specifica KISS candidata alla controrevisione esterna |
| 2026-08-25 | `active` | controrevisione esterna eseguita (§17): approvabile con 4 rilievi bloccanti di specifica, 3 rischi quantificati, 3 irrobustimenti; stima 12-19 giorni in tre blocchi |

## 17. Controrevisione esterna — verdetto e risposte al mandato

> Prodotta il 2026-08-25 in sola lettura, in risposta a §15, dallo stesso
> revisore che ha condotto il secondo giro avversariale su RM-0002. Consultati
> senza modificarli: ADR 0223, `internal/design/TODO.md`
> (`EXEC-BIND-001`, `AFF-I18N-001`, `PUB-001`) e il rapporto di audit
> `internal/reports/rm0002-linter-manifest-multilingue-audit-20260824.md`.
> Nessun file di prodotto, manifest, firma o stato è stato modificato.

### 17.1 Verdetto

**La riduzione di perimetro è giusta e la garanzia ristretta di §1 chiude i
difetti che bloccano RM-0002 L5.** Il confronto è verificabile: gli otto difetti
che l'audit del 24 agosto aveva confermato contro il codice — base non
verificata prima della rifirma, digest ricalcolato, tre file scritti
separatamente, byte diversi fra verifica e uso, candidato applicato a una base
cambiata, percorso del registro usato come destinazione, secondo scrittore
legacy, validatore finto nelle prove — hanno tutti un punto corrispondente in
§3.4, §5, §6 o §8. Nessuno resta scoperto.

Il verdetto è **approvabile con quattro rilievi bloccanti**, tutti di
specifica e tutti economici: nessuno richiede di cambiare il disegno, tre
richiedono di scrivere un'invariante che oggi è implicita e uno richiede una
decisione di Roberto su un file invariante.

Classificazione completa: **4 bloccanti, 3 rischi accettabili (quantificati),
3 elementi di irrobustimento futuro, 0 preferenze stilistiche**.

### 17.2 Rilievi bloccanti

#### B1 — Il digest del codice non può essere calcolato dalla directory della generazione

**Dato misurato:** **21 contratti builtin su 21** dichiarano `code.files` con
percorsi che escono dalla directory del manifest — `../../system/admin.py`,
`../../classify_entries.py`, `../../recurring_tasks.py` e così via. Non è
un'eccezione: è la forma normale di quella classe. E
`sign.compute_code_digest()` (`runtime/sign.py:96-104`) fa `manifest_dir / fname`
**senza normalizzare e senza controllo di contenimento**, il che oggi è
intenzionale e funziona.

Dopo il cutover `manifest_dir` non è più la directory sorgente: è la
generazione dentro `PATH_USER_STATE`. Risolvere lì `../../classify_entries.py`
esce dal deposito e cade in una directory arbitraria dello stato utente.
L'implementazione che «sposta il contratto e riusa la funzione di digest»
produce, a seconda di cosa trova, un errore di file assente oppure il digest di
un file sbagliato.

§5.1 punto 6 dice già la cosa giusta — «calcola il digest attraverso le radici
ammesse associate a `ContractId`» — ma è una riga sola dentro un elenco di
otto, e questo è precisamente il dettaglio che si perde in implementazione.

**Richiesta:** promuoverlo a invariante di §3.4 («il digest del codice si
calcola sempre dalla radice sorgente dichiarata del contratto, mai dalla
directory della generazione»), aggiungere la prova corrispondente in §10.1 e
citare esplicitamente il caso `../../` fra le fixture di M0.

#### B2 — Il selettore canonico non è definito, e finisce dentro l'hash della generazione

§4.3 richiede che lo stato conservi provenienza e hash «per selettore
canonico», ma non dice **quale** delle due forme oggi in uso sia quella
canonica:

- `args.<nome>.description` — la forma scritta nei companion su disco,
  verificata su `executors/find_files/manifest.lang_state.json`;
- `args.properties.<nome>.description` — la forma prodotta da
  `i18n_materializer.iter_localized_text_tables()` e usata da
  `i18n_pipeline._update_state()`.

È il rilievo ADV-009 dell'audit, confermato e oggi latente soltanto perché
nessuna terza lingua è mai stata promossa. RM-0007 non lo risolve: lo
**incapsula**. Dal momento in cui `manifest.lang_state.json` entra nella
generazione, il selettore fa parte dell'identità immutabile, e due forme dello
stesso testo producono due generazioni diverse per lo stesso contenuto.

**Richiesta:** scegliere la forma canonica in §4.3, dichiarare la migrazione
una-tantum dei companion esistenti come consegna di M4, e aggiungere a §10.1 la
prova che un selettore nella forma non canonica viene **rifiutato**, non
silenziosamente accettato.

#### B3 — Il retry dopo un arresto fra il passo 18 e il 21 non è idempotente su Windows

§6 dichiara che «un errore dopo il punto 21 non annulla una pubblicazione già
visibile: il retry la riconosce». La finestra scoperta è **prima** del 21.

Il passo 18 rinomina la directory temporanea in `generations/<generation-id>`.
Se il processo termina fra il 18 e il 21, `current` indica ancora la vecchia
generazione — corretto — ma la directory della nuova esiste già. Al retry, con
la stessa base e le stesse patch, il publisher ricostruisce **lo stesso**
identificatore e riprova il passo 18 su una destinazione che esiste. Su Linux
`os.replace` di una directory su una directory non vuota fallisce; su Windows
la sostituzione di una directory esistente non è ammessa affatto.

Il caso non è raro: è esattamente la finestra che §10.3 punto 2 chiede di
provare («dopo il rename della generazione»). La prova è prevista, il
comportamento no.

**Richiesta:** aggiungere a §6 un ramo esplicito fra il 17 e il 18 — se
`generations/<generation-id>` esiste già, verificarla integralmente; se
coincide, saltare al passo 20 e proseguire; se differisce, è la corruzione
descritta in §4.2 e il contratto si blocca. È l'unico punto in cui l'algoritmo,
così come è scritto, non fa quello che il documento promette.

#### B4 — Dopo il cutover, §7.10 di `CLAUDE.md` diventa falso

`CLAUDE.md` §7.10 è una regola **invariante** e dice: modifichi
`<executor>.py` o il solo `manifest.toml`, firmi con
`python3 runtime/sign.py sign executors/<name>`, riavvii, committi manifest e
firma insieme — «senza firma il loader scarta l'executor in silenzio».

Dopo il cutover quella sequenza non rende più viva la modifica: il loader legge
`current`, e la sorgente firmata resta una sorgente di authoring finché non
passa da `publish_signed_source()`. Il modo di sbagliare è **lo stesso** che
quella regola esiste per prevenire, con un nome nuovo: non più «senza firma
sparisce in silenzio», ma «senza pubblicazione la modifica non ha effetto in
silenzio». Chi sviluppa cambierà il codice, firmerà, riavvierà, e vedrà il
comportamento vecchio senza un solo messaggio d'errore.

§8 elenca otto file da modificare e non cita `CLAUDE.md`; M4 elenca sei
consegne e non cita il flusso di lavoro quotidiano.

**Richiesta:** aggiungere a M4 la consegna «aggiornare il flusso di authoring
documentato» e **portare a Roberto la modifica di §7.10**, che è invariante e
non si tocca di iniziativa. Fino ad allora il cutover non è completo, qualunque
prova verde dia il catalogo.

### 17.3 Rischi accettabili, con i numeri

Tre righe della tabella §11 si possono chiudere con una misura invece che con
un giudizio. Misurato sul catalogo reale di 122 contratti, il 25 agosto 2026:

| Voce | Misura | Conseguenza |
|---|---|---|
| peso di una generazione | **7,5 KB** in media (manifest + firma + stato) | tradurre l'intero catalogo in una lingua nuova costa **0,9 MB** di generazioni |
| costo dell'identificatore | **3,1 ms** per calcolare il digest delle generazioni di tutti e 122 i contratti | irrilevante all'avvio, dove la verifica delle firme e dei digest del codice già domina |
| finestra verifica→invocazione | invariata rispetto a oggi | RM-0007 non la peggiora e non la chiude; `EXEC-BIND-001` la possiede |

Conseguenza sulla tabella §11: la riga «crescita dello spazio: media» va
abbassata a **bassa**, e la scelta di non fare raccolta automatica in v1 è
corretta — a 7,5 KB per generazione, la raccolta costerebbe più rischio di
quanto risparmi spazio. La riga «codice cambia dopo la verifica: alta,
residua» resta com'è: è dichiarata onestamente in §3.3 ed è la sola cosa che
rende credibile la qualificazione KISS.

### 17.4 Irrobustimento futuro, non bloccante

**H1 — nominare i due modi di guasto Windows che «prova reale NTFS» non cattura
da sola.** §10.3 chiede la prova su NTFS reale, il che è giusto ma generico. I
due meccanismi che vale la pena provare per nome sono: (a) la sostituzione di
`current` con `os.replace()` fallisce con violazione di condivisione se un
lettore tiene quel file aperto senza condivisione in cancellazione — la
mitigazione è che il lettore apra, legga e chiuda subito, come §7.1 punto 1 già
prescrive, e che il publisher riprovi con attesa finita; (b) la sincronizzazione
di una directory non esiste su Windows, quindi la garanzia dei passi 19 e 22 è
strutturalmente più debole lì. §10.3 chiede già di distinguere crash del
processo da perdita di alimentazione: quella distinzione va scritta anche in
§3.3, dove il limite è dichiarato, non solo fra le prove.

**H2 — dare un tetto d'allarme alla diagnostica degli orfani.** §7.3 fa bene a
segnalare e non cancellare, ma un rapporto che nessuno legge non è un
controllo. Basta una soglia oltre la quale la diagnostica diventa un avviso
operativo.

**H3 — chiudere il ciclo con RM-0002 per iscritto.** Il TODO lo coordina già
(«L2 produce l'inventario neutro che RM-0007 consuma, L5 richiede il confine di
pubblicazione già in servizio») e §8 assegna correttamente la proprietà di
`manifest_inventory.py` a RM-0002. Manca la conseguenza operativa: **M0 di
RM-0007 non può iniziare prima che L2 di RM-0002 sia consegnata**, altrimenti
`ContractId` nasce dentro RM-0007 e la dipendenza si inverte. Una riga in §9.

### 17.5 Risposte alle quattordici domande di §15

1. **Sì**, risolti B1-B4. Gli otto difetti confermati dall'audit hanno tutti un
   punto corrispondente nella specifica.
2. **Nessuno**, per i difetti in perimetro. Copiare il codice servirebbe solo a
   chiudere la finestra verifica→invocazione, e i 21 builtin con `../../`
   dimostrano proprio che i file dichiarati **non sono** la chiusura delle
   dipendenze: copiarli darebbe una falsa garanzia. È `EXEC-BIND-001`.
3. **Deve stare nella generazione.** Ricostruirlo richiederebbe `source_lang` e
   `source_hash`, che non sono derivabili dal manifest pubblicato: si perderebbe
   la provenienza e la riconciliazione post-commit smetterebbe di essere
   idempotente, costringendo a introdurre la ricevuta persistente che §13
   rinvia.
4. **No, non in v1.** La firma del manifest autentica già ciò che decide il
   caricamento; l'hash della generazione impedisce di mescolare i tre file per
   errore. Una seconda firma proteggerebbe l'associazione fra i tre file da un
   avversario **attivo** che ha già i permessi dell'utente Metnos — cioè lo
   scenario che §3.2 esclude dichiaratamente. Aggiungerla senza cambiare quel
   confine sarebbe costo senza garanzia nuova.
5. **Sì, sono due guasti distinti e servono entrambi.** Il lock impedisce a due
   scritture di interlacciarsi. `expected_generation_id` impedisce di applicare
   un candidato preparato **prima** dell'acquisizione e ormai vecchio: il lock
   non può vederlo, perché quando lo si ottiene la base è già cambiata. Toglierne
   uno lascia scoperto l'altro caso.
6. **Sì per gli scrittori cooperanti**, che è il perimetro dichiarato, con due
   avvertenze: il blocco è per handle e per intervallo di byte, quindi va preso
   su `writer.lock` e mai sul puntatore; ed è mandatorio, quindi nessun lettore
   deve mai aprire il file di lock, neanche in diagnostica.
7. **Sì per l'arresto del processo, no per la perdita di alimentazione su
   Windows**, dove la sincronizzazione di directory non esiste. La distinzione
   è già richiesta in §10.3 e va dichiarata anche in §3.3 (vedi H1).
8. **Sì, ed è idempotente proprio grazie a §4.3.** Gli hash dello stato
   linguistico rendono la riconciliazione una funzione dello stato osservato,
   non della memoria di un processo morto. Senza quegli hash servirebbe la
   ricevuta persistente.
9. **Sì, a condizione che B4 diventi una consegna di M4** e che la guardia
   statica di §7.2 vieti la lettura viva dalla sorgente per i contratti
   migrati. Il divieto mirato è la scelta giusta: vietare genericamente le
   scritture bloccherebbe installer e generatori.
10. **Sì per installer, Synt e importatori**, che producono directory nuove e
    possono lavorare fuori dal lock. Il caso scoperto è **chi modifica in posto
    una sorgente già ammessa**: §6.2 impone a costoro di acquisire il lock, ma
    §14 — l'elenco che l'implementatore legge davvero — non lo dice. Va
    aggiunto lì.
11. **Sì, e i dati lo dimostrano.** 21 builtin su 21 usano `../../`: essendo la
    forma universale di quella classe, la regola generale «i file di codice
    risolvono dentro la radice sorgente dichiarata della classe del contratto»
    non ha bisogno di nominare nessun executor. È esattamente B1.
12. **Nulla.** Il perimetro è già al minimo: togliere il lock o la generazione
    attesa scopre un guasto distinto (domanda 5), togliere lo stato dalla
    generazione rompe l'idempotenza (domanda 3), togliere il puntatore riporta
    al problema dei tre file separati.
13. **Nessun elemento rinviato è indispensabile alla v1.** Il più vicino al
    confine è la raccolta delle generazioni, ed è fuori a ragione: 7,5 KB per
    generazione non giustificano il rischio di cancellare la cosa sbagliata.
14. **Non ne vedo una.** SQLite come puntatore sarebbe più rapido da scrivere,
    ma introduce un'autorità nuova su un formato pubblico orientato ai file e
    sposta la durabilità dentro un motore che va comunque sincronizzato. Un file
    di una riga sostituito con `os.replace()` è la primitiva più piccola che dia
    al lettore uno stato completo senza obbligarlo a prendere un lock.

### 17.6 Stima separata, come richiesto da §15

La stima ritirata di 7-12 giorni si riferiva a un perimetro diverso. Questa
vale per il perimetro KISS, a valle di L2 di RM-0002 e con B1-B4 risolti nella
specifica prima di iniziare.

| Blocco | Stima | Dove sta il rischio |
|---|---|---|
| nucleo: M1 + M2 + M3 | **6-9 giorni** | quasi tutto in M3: patch, confronto strutturale e digest conservato |
| migrazione e cutover: M4 | **4-6 giorni** | è il blocco più rischioso, perché tocca il loader **e** il modo di lavorare quotidiano (B4) |
| certificazione Windows: §10.3 su NTFS reale | **2-4 giorni** | dipende dall'accesso alla macchina: `ssh` e `ping` verso il PC sono chiusi e si diagnostica solo attraverso Metnos, il che va messo a preventivo e non scoperto in corsa |
| **totale** | **12-19 giorni** | M0 non è contato: appartiene a RM-0002 L2 |

La riga della certificazione Windows è quella che più facilmente viene
sottostimata: non è tempo di scrittura, è tempo di andata e ritorno su una
macchina che non si interroga direttamente.

### 17.7 Raccomandazione

Promuovere RM-0007 a `ready` **dopo** aver scritto B1, B2 e B3 nella specifica —
sono tre paragrafi, non tre progetti — e **dopo** che Roberto abbia deciso su
B4, che è l'unico punto che tocca un file invariante e il modo di lavorare di
tutti i giorni.

Non vedo motivi per riaprire il disegno. La versione KISS è più piccola della
prima, chiude gli stessi difetti, e la parte che ha rinviato l'ha rinviata
dicendo perché.
