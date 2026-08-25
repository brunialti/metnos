# RM-0007 — Pubblicazione verificata delle varianti linguistiche dei contratti

> `RM-0007` · stato `in_progress` · definita `2026-08-24` · specifica KISS
> consolidata `2026-08-25` · controrevisioni conservate nel rapporto collegato
> e risolte dalla matrice §17 · ADR 0223 ancora `proposed` fino a M4 · documento
> interno

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

## 2. Decisione KISS

La prima stesura proponeva un deposito generale di contratti e codice,
envelope e ricevute firmati, binding della release, un nuovo runner, recupero
automatico e un inventario universale. Era una soluzione coerente, ma
rispondeva a tre problemi distinti:

- pubblicazione linguistica sicura;
- deploy tecnico atomico di manifest e codice;
- identità dei byte eseguiti fino al momento dell'invocazione.

RM-0007 conserva soltanto il primo. La versione normativa usa:

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
- transazione ordinaria di pubblicazione per una lingua intera o per più
  contratti; il solo marker globale di migrazione di §4.3 resta compreso;
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

Su Windows/NTFS la sostituzione atomica di un file chiuso protegge dal crash
del processo, ma la libreria standard non offre una barriera di durabilità per
la directory equivalente a quella disponibile sui filesystem Linux ammessi.
La v1 certifica quindi atomicità e recupero dopo arresto del processo su NTFS;
non promette che l'ultimo puntatore sopravviva a una perdita improvvisa di
alimentazione. Il lettore apre, legge e chiude subito `current`; il publisher
riprova per un tempo finito le sole violazioni di condivisione transitorie.

Il confine una-tantum di cutover è più restrittivo. Su Linux M4 scrive il
marker temporaneo, esegue `fsync` sul file, lo rinomina e sincronizza la
directory padre; dopo lo swap sincronizza di nuovo la directory padre. Su
Windows apre il marker definitivo con `CreateFileW(FILE_FLAG_WRITE_THROUGH)`,
scrive `v1\n`, chiama `FlushFileBuffers()` sullo stesso handle e lo chiude;
sposta poi la directory shadow, già completa e sullo stesso volume, verso il
nome produttivo assente con `MoveFileExW(MOVEFILE_WRITE_THROUGH)`. Ogni errore
delle primitive native interrompe il cutover senza dichiararlo concluso.
Questo requisito non trasforma ogni publish ordinario in una transazione
contro perdita di alimentazione.

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
12. Il digest del codice si risolve sempre dalla directory del manifest
    sorgente censita dall'inventario, mai dalla directory della generazione;
    ogni percorso risolto deve inoltre ricadere in una delle radici di codice
    ammesse per quella sorgente.
13. Prima di dichiarare un retry già eseguito si rilegge e si verifica
    integralmente `current`; l'idempotenza richiede l'intera postcondizione
    desiderata, identificata dalla stessa generazione, non la sola presenza
    delle patch.
14. Nel percorso vivo attivato da M4, una pubblicazione linguistica aggiorna
    anche i tre file di authoring in modo idempotente. Una successiva
    pubblicazione tecnica non può eliminare o regredire testi e provenienza
    linguistica già correnti.
15. Ogni lettore vivo e ogni scrittore cooperante del contratto passa dal
    confine censito in §8; nessun percorso alternativo può diventare vivo per
    semplice scrittura o firma.
16. Dopo che esiste il marker o la radice produttiva, nessuna loro assenza o
    corruzione può riattivare automaticamente il layout legacy.
17. Dopo il cutover l'inventario fornisce soltanto binding strutturali; non
    legge manifest di authoring per decidere contenuto, nome o lifecycle vivi.
18. Ogni pubblicazione tecnica acquisisce il writer lock una sola volta nel
    publisher; firma offline, lock annidati e sequenze sign-then-publish non
    costituiscono il flusso operativo.

## 4. Modello persistente minimo

### 4.1 Layout

```text
PATH_USER_STATE/
  contract-publications.ACTIVE
  contract-publications-shadow/<nonce>/v1/
    <contract-key>/...
  contract-publications/v1/
    <contract-key>/
      binding.json
      writer.lock
      current
      generations/
        <generation-id>/
          manifest.toml
          manifest.toml.sig
          manifest.lang_state.json
```

La radice shadow è distinta dalla radice produttiva e non viene mai consultata
dal loader. Dentro una singola pubblicazione si usa inoltre una directory
temporanea non autorevole nella stessa directory `generations/`. Non serve un
journal persistente né una procedura automatica di raccolta.

`contract-key` deriva dall'identità canonica restituita dall'inventario comune
di RM-0002. Il registro i18n fornisce un'identità di risorsa, mai un path di
destinazione. Il deposito rifiuta componenti `..`, symlink o reparse point del
deposito, file non regolari e percorsi risolti fuori dalla radice configurata.

`binding.json` è immutabile e contiene soltanto, in JSON canonico,
`{"contract_id":"<origin>:<relative_manifest>","schema_version":1}` più una
newline finale. Non contiene nomi, lifecycle, hash del manifest, path assoluti
o radici. Il nome `<contract-key>` deve essere il SHA-256 esadecimale del
`ContractId` canonico contenuto nel binding. Un binding assente, modificato,
duplicato o non corrispondente alla directory è `binding_invalid`.
Alla prima pubblicazione viene scritto con temporaneo sibling, flush, chiusura
e rename senza sovrascrittura. Un retry può riusare soltanto un binding
byte-identico; il binding non entra nell'hash della generazione e non viene mai
aggiornato.

Dopo il cutover il loader enumera soltanto le directory contratto e i loro
binding. Dalla mappa generale e versionata degli origin ricostruisce in modo
deterministico `source_root`, `manifest_path` e `allowed_code_roots`, senza
aprire il manifest di authoring; nome, lifecycle, schema e ogni altro contenuto
vivo provengono esclusivamente dalla generazione verificata. L'eventuale stato
abilitato/disabilitato continua a provenire dal registro operativo generale
delle skill, usando l'identità strutturale, non dal manifest sorgente. Non
esiste un indice globale dei binding.

Nel disegno precedente `<generation-id>` indicava anche la forma logica. Sul
filesystem il nome della directory è invece **soltanto il digest esadecimale
minuscolo di 64 caratteri**, senza `sha256:`: i due punti non sono portabili su
NTFS. `current` contiene l'identificatore logico completo su una sola riga:

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
stesso identificatore ma byte diversi è corruzione e blocca il contratto. La
conversione fra identificatore logico `sha256:<hex>` e nome fisico `<hex>` è
totale e unica; qualunque altra forma viene rifiutata.

Se la directory finale esiste già, il publisher non la sostituisce e non la
cancella. Verifica che contenga esattamente i tre file regolari previsti, senza
file aggiuntivi, link simbolici o reparse point. Se nomi e byte coincidono,
riusa la generazione e ripete comunque la barriera di durabilità della
directory prima di procedere al puntatore; se differiscono o la struttura non
è valida, restituisce `generation_corrupt`. Non viene mai eseguito
`os.replace()` fra directory di generazione.

### 4.3 Confine globale di cutover

`contract-publications.ACTIVE` contiene esattamente `v1\n`. È un confine di
migrazione irreversibile per il bootstrap, non seleziona una generazione e non
autorizza contenuti. Il loader applica queste regole in ordine:

1. radice produttiva e marker entrambi assenti: layout legacy;
2. radice produttiva presente: layout solo-deposito, anche se il marker manca;
3. marker presente e radice produttiva assente o incompleta: fail-closed,
   cutover da riprendere;
4. in nessun altro caso è ammesso il fallback legacy.

La perdita o l'assenza del solo marker non può quindi riattivare il layout
legacy dopo lo swap. La radice shadow, collocata fuori dalla radice produttiva,
non influisce sul bootstrap. Il marker viene reso durevole **prima** dello
swap con le primitive precise di §3.3 e resta poi permanente. La directory
shadow completa viene rinominata globalmente in `contract-publications` sullo
stesso volume: Linux usa `rename()` seguito da `fsync` della directory padre;
Windows richiede destinazione assente e usa
`MoveFileExW(MOVEFILE_WRITE_THROUGH)`. Un arresto prima del marker lascia il
legacy; dopo il marker lascia il sistema fail-closed o store-only, mai di
nuovo legacy. La v1 non introduce una modalità ibrida per contratto né una
seconda autorità sul contenuto.

Il cutover è una breve operazione di manutenzione quiescente. Il gate blocca
nuovi turni, scheduler e publisher, attende la fine dei turni già ammessi e
disabilita reload e watcher del catalogo. Il processo di migrazione diventa
l'unico writer, rigenera l'inventario, confronta l'insieme esatto dei
`ContractId` e riverifica binding, firme e digest sul codice corrente. Dopo il
marker e lo swap esegue, con gli ingressi ancora chiusi, un caricamento
store-only completo e un restart/swap globale controllato. Solo se entrambi
sono verdi riabilita reload, scheduler, publisher e nuovi turni. Non si
introduce un lock globale nel percorso ordinario.

### 4.4 Autorità dello stato linguistico

`manifest.lang_state.json` conserva provenienza e hash utili al workflow
RM-0005. Non decide quale executor venga caricato e non richiede una seconda
firma. È incluso nella generazione per evitare che il workflow osservi uno
stato appartenente a un manifesto diverso.

Il formato canonico v1 è un oggetto JSON con le sole chiavi di primo livello
`schema_version` (intero `1`) e `selectors` (oggetto). Le chiavi di
`selectors` sono:

- `description` per la descrizione del contratto;
- il percorso strutturale completo di ogni descrizione nello schema `args`,
  per esempio `args.properties.<name>.description` oppure
  `args.properties.messages.items.properties.body.description`.

L'enumeratore percorre ricorsivamente i costrutti standard JSON Schema che
contengono uno schema singolo, una lista di schemi o una mappa di schemi; non
contiene elenchi di executor o profondità speciali. La posizione strutturale
distingue il keyword `description` da una proprietà che si chiama a sua volta
`description`. I punti separano i segmenti nella v1; un nome di segmento che
contiene un punto viene rifiutato come ambiguo.

La forma legacy `args.<name>.description` non è un alias: viene rifiutata dal
deposito. M4 migra una sola volta i companion esistenti e corregge tutti i
produttori, compresi `runtime/sign.py`, `runtime/synth_request.py` e
`runtime/migrate_manifest_descriptions.py`. La migrazione parsifica il
manifest e ricostruisce l'insieme autorevole delle risorse localizzate: per
ciascuna risorsa emette il selettore canonico, aggiunge le voci mancanti e
scarta con evidenza di audit le voci che non corrispondono più a una risorsa.
Il risultato della migrazione contiene sia i byte canonici sia liste ordinate
di voci aggiunte, scartate, normalizzate e private di provenienza: il chiamante
M4 registra queste liste prima del cutover e non può ignorare scarti silenziosi.
Se il vecchio `version_hash` coincide con il testo corrente, conserva
`source_lang` e `source_hash` soltanto se il tag sorgente è canonico, quella
lingua esiste nella stessa risorsa e l'hash coincide con il suo testo corrente;
in caso contrario azzera la provenienza. Se `version_hash` non coincide,
calcola quello corrente e azzera entrambi i campi di provenienza, perché non è
più dimostrabile a quale testo appartenessero. Collisioni fra due voci legacy
che pretendono la stessa risorsa interrompono la migrazione. Il risultato
viene sottoposto alla validazione stretta v1 prima di essere scritto: non si
rinominano chiavi alla cieca e non si conserva provenienza non verificabile.

La fotografia iniziale del 2026-08-25 ha misurato 107 companion, 630 selettori
corti, nessun selettore canonico, 27 `version_hash` non corrispondenti al testo
corrente e 12 selettori orfani. Sono dati di caratterizzazione per le prove M0,
non conteggi ammessi nel codice o criteri di successo della migrazione.

Per ogni selettore e lingua lo stato conserva:

- `version_hash` del testo pubblicato;
- `source_lang`;
- `source_hash`.

Queste sono le sole tre chiavi ammesse. `version_hash` è sempre
`sha256:<64hex>` minuscolo; `source_lang` è un tag normalizzato oppure `null` e
`source_hash` è `sha256:<64hex>` oppure `null`, coerentemente con l'assenza di
una lingua sorgente. Campi sconosciuti e combinazioni parziali sono rifiutati.

I tag di lingua sono normalizzati con `i18n_registry.normalize_language()`.
La serializzazione canonica usa UTF-8, `sort_keys=True`,
`ensure_ascii=False`, separatori JSON compatti `(",", ":")` e una sola
newline finale. Chiavi, tag o hash non canonici sono rifiutati prima del
calcolo della generazione. Tutti i produttori usano la stessa funzione di
codifica posseduta da `runtime/i18n_materializer.py`: l'identità non dipende
dall'ordine di inserimento. Non si duplicano encoder in `sign.py`, nel
publisher o negli script di migrazione.

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
    source_manifest_dir: Path
    allowed_code_roots: tuple[Path, ...]
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
6. risolve ogni voce di `code.files` rispetto a
   `ManifestRef.manifest_dir`, anche quando contiene `../`, e dopo la
   risoluzione richiede che il file ricada in una delle `allowed_code_roots`
   associate alla sorgente; non usa mai la directory della generazione come
   base;
7. verifica lo stato e, per una generazione, il suo identificatore;
8. restituisce il valore immutabile.

Il loader usa `VerifiedManifest.parsed` e non riapre `manifest.toml`.
Il nome del campo resta **`parsed`** in loader, cache, prove e documentazione;
non introdurre alias come `manifest`, `data` o `payload`.

Prima del cutover l'inventario completo può parsificare l'authoring per audit e
migrazione. Dopo il cutover usa una modalità strutturale distinta: enumera
`binding.json`, valida `storage_key`, ricostruisce il `ManifestRef` dalla mappa
degli origin e non legge i byte del manifest sorgente. Il `ManifestRef` passato
al deposito concede soltanto identità, directory sorgente e radici ammesse;
`VerifiedManifest.parsed` concede il contenuto vivo.
Nel tipo condiviso `manifest_hash`, `name`, `lifecycle` e `skill_name` sono
osservazioni nullable dell'inventario authoring: `verify_manifest_source()` le
pretende, mentre `current_manifest()` accetta il riferimento strutturale e
ricava nome e lifecycle esclusivamente dai byte pubblicati e verificati.

### 5.2 Firma pura

In `runtime/sign.py`:

```python
def sign_manifest_bytes(manifest_bytes: bytes, *, private_key: Ed25519PrivateKey) -> bytes: ...

def verify_manifest_bytes(
    manifest_bytes: bytes,
    signature_bytes: bytes,
    *,
    trusted_publics: Iterable[TrustedPublic],
) -> SignerIdentity: ...
```

Entrambe sono pure rispetto al filesystem. `sign_executor()` resta un
involucro per le sorgenti di authoring: carica la chiave e passa l'oggetto alla
primitiva pura. Anche `trusted_publics` contiene chiavi pubbliche e identità già
risolte in memoria; nessuna delle due primitive riceve nomi di chiave, path o
configurazione e nessuna accede al filesystem. La pubblicazione linguistica
non chiama `sign_executor()`.

### 5.3 API pubblica

```python
def current_manifest(
    ref: ManifestRef,
    *,
    trusted_publics: Iterable[TrustedPublic],
) -> VerifiedManifest: ...

def publish_localization(
    ref: ManifestRef,
    *,
    expected_generation_id: str,
    source_language: str,
    target_language: str,
    patches: tuple[LocalizationPatch, ...],
    private_key: Ed25519PrivateKey,
    trusted_publics: Iterable[TrustedPublic],
) -> PublicationResult: ...

def publish_technical_update(
    ref: ManifestRef,
    *,
    expected_generation_id: str | None,
    draft: TechnicalDraft,
    private_key: Ed25519PrivateKey,
    trusted_publics: Iterable[TrustedPublic],
    removal: SurfaceRemoval | None = None,
) -> PublicationResult: ...

def publish_signed_source(
    ref: ManifestRef,
    *,
    expected_generation_id: str | None,
    trusted_publics: Iterable[TrustedPublic],
    removal: SurfaceRemoval | None = None,
) -> PublicationResult: ...

def activate_store(
    expected_catalog: Mapping[ContractId, str],
    *,
    shadow_root: Path,
    trusted_publics: Iterable[TrustedPublic],
) -> None: ...

def rollback(
    ref: ManifestRef,
    *,
    expected_generation_id: str,
    target_generation_id: str,
    actor: str,
    reason: str,
    trusted_publics: Iterable[TrustedPublic],
) -> PublicationResult: ...
```

`TechnicalDraft` contiene i byte proposti di manifest e stato e gli hash della
fotografia di authoring da cui derivano; non contiene chiavi, path concessi o
codice copiato. `SurfaceRemoval` contiene soltanto l'insieme canonico e non
vuoto dei selettori rimossi, `actor` e `reason` non vuoti. Non è una scorciatoia
per cambiare testi: autorizza soltanto rimozioni che corrispondono esattamente
alla differenza strutturale verificata dello schema.

`activate_store()` confronta l'insieme esatto dei contratti ammessi
dall'inventario con `expected_catalog`, riverifica binding, `current` e
generazioni nella radice shadow e applica il protocollo quiescente di §4.3:
marker durevole prima, swap globale della radice poi. Non accetta sottoinsiemi
e non modifica manifest, firma o stato. È idempotente soltanto per stati
completi verificati: marker + radice valida restituiscono `repeated`; marker
senza radice riprende dallo shadow verificato; radice valida senza marker resta
store-only e consente di ripristinare il marker in manutenzione. Non cancella
mai marker o radice e rifiuta una radice preesistente incoerente.

`publish_signed_source()` non genera codice, non lo copia e non ricalcola il
digest nel manifest. Importa nel deposito una sorgente già firmata, conforme e
con digest verificato. Consente a installer, generatori e importatori di
continuare a produrre file di authoring committabili su Git senza farli leggere
come contratto vivo dopo il cutover.

Il flusso operativo canonico dopo il cutover è un solo comando fail-loud:

```text
python3 runtime/sign.py publish executors/<nome>
```

Il sottocomando `publish` carica chiavi e configurazione, fotografa la modifica
proposta in un `TechnicalDraft` immutabile e chiama una sola volta
`publish_technical_update()`. È quest'ultima l'unica proprietaria di mutex e
`writer.lock`: sotto quel lock riverifica base, draft e codice, aggiorna il
digest in memoria, esegue i controlli, firma, pubblica e riconcilia authoring.
Gli helper interni che ricevono il suffisso `_locked` non acquisiscono mai il
lock. Il comando non chiama prima `sign_executor()`, non concatena firma e
publish e non annida `publish_signed_source()`.

Il vecchio sottocomando `sign` resta disponibile per preparazione offline, ma
stampa esplicitamente che la sorgente firmata **non è viva**. M4 censisce e
migra tutti i callsite operativi; non sono ammessi script che simulino il nuovo
flusso concatenando due comandi indipendenti.

`PublicationResult` contiene soltanto `contract_id`, generazione precedente,
generazione corrente, operazione e `repeated`. Non è firmato e non diventa una
seconda autorità.

## 6. Algoritmo di pubblicazione linguistica

Traduzione e revisione semantica avvengono prima. Il publisher non invoca LLM.

1. normalizzare le lingue con `i18n_registry.normalize_language()`;
2. risolvere `ContractId`, `ManifestRef.manifest_dir` e radici ammesse tramite
   l'inventario comune;
3. acquisire, nello stesso ordine in ogni API, il mutex di processo del
   contratto e poi `writer.lock`, entrambi con timeout finito;
4. leggere `current` una volta e caricare e verificare integralmente la
   generazione indicata **prima** di valutare conflitto o idempotenza;
5. caricare e verificare la generazione immutabile
   `expected_generation_id`, confrontare gli hash di sorgente, bersaglio
   precedente e candidato, quindi applicare le patch soltanto in memoria;
6. confrontare strutturalmente base e candidato dopo avere rimosso unicamente
   i valori bersaglio dei selettori autorizzati;
7. richiedere che `[code].files` e `[code].digest` siano identici e ricalcolare
   il digest dal codice corrente usando la directory sorgente e il
   contenimento di §3.4.12;
8. eseguire standard executor, linter della lingua bersaglio e controlli del
   candidato sui byte preparati;
9. aggiornare in memoria il solo stato linguistico pertinente, serializzarlo
   nel formato canonico di §4.4, firmare deterministicamente i nuovi byte del
   manifest e riverificare la firma;
10. calcolare l'identificatore della **postcondizione completa**, cioè dei tre
    byte payload desiderati;
11. se la generazione corrente non coincide con quella attesa, impostare
    `repeated=True` e passare ai punti 19-21 soltanto quando la corrente
    verificata ha esattamente l'identificatore e i tre payload desiderati; in
    ogni altro caso restituire `commit_conflict`;
12. se la corrente coincide con quella attesa e anche con la postcondizione
    completa, impostare `repeated=True` e passare ai punti 19-21 senza
    riscrivere il puntatore;
13. scrivere i tre file in una directory temporanea non autorevole dentro
    `generations/`, quindi sullo stesso volume della destinazione;
14. sincronizzare e chiudere ogni file, ricaricare la directory temporanea e
    verificarla integralmente;
15. se `generations/<64hex>` non esiste, rinominare la temporanea con un rename
    che non sovrascriva; se esiste, applicare il controllo e il riuso esatto di
    §4.2, altrimenti `generation_corrupt`;
16. ripetere la barriera di durabilità di `generations/` anche quando la
    directory è stata riusata; su Windows registrare il limite dichiarato in
    §3.3;
17. creare nella directory del contratto un puntatore temporaneo, scriverlo,
    sincronizzarlo e chiuderlo, quindi sostituire `current` con `os.replace()`;
    su Windows riprovare per una durata finita le violazioni di condivisione e
    non cancellare mai prima il vecchio puntatore;
18. sincronizzare la directory del contratto dove la piattaforma lo consente;
19. soltanto dopo l'abilitazione M4, con il lock ancora acquisito,
    riconciliare idempotentemente i tre file di authoring con i tre payload
    della generazione corrente, usando file temporanei e sostituzioni atomiche
    per ciascun file, e riverificarli; M3 usa solo fixture isolate e non tocca
    authoring produttivo;
20. rilasciare prima `writer.lock` e poi il mutex di processo;
21. anche nel ramo `repeated=True`, rileggere e riverificare `current` dopo il
    rilascio e, soltanto quando M4 ha abilitato il percorso vivo, riconciliare
    il registro RM-0005 dalla fotografia appena letta, mai dal candidato
    conservato in memoria.

Ogni errore prima del punto 17 lascia invariato `current`. Un errore dopo il
punto 17 non annulla una pubblicazione già visibile: il retry ricostruisce la
stessa postcondizione, verifica la generazione eventualmente già presente,
ripara l'authoring sotto lock e riconcilia il registro da una nuova lettura
quando il percorso M4 è vivo.
Se un altro writer pubblica fra il rilascio e il punto 21, la rilettura evita
che una fotografia obsoleta ripristini nel registro lo stato precedente.

### 6.1 Blocco portabile

Il lock è implementato nello stesso `contract_store.py`:

- file permanente, mai cancellato come protocollo di rilascio;
- Linux: `fcntl.flock`;
- Windows: file regolare permanente lungo almeno un byte; apertura una sola
  volta, `seek(0)` e `msvcrt.locking()` sul primo byte; acquisizione e rilascio
  usano lo stesso handle, che resta aperto per tutta la sezione critica;
- un mutex in-process per `ContractId` precede sempre il lock del sistema
  operativo, perché i lock a intervallo non serializzano in modo uniforme due
  thread dello stesso processo su tutte le piattaforme;
- tentativi non bloccanti con attesa limitata fino a una scadenza monotona;
- errore stabile `lock_timeout`;
- nessun lock basato sulla sola esistenza del file.

Tutti gli scrittori Metnos che possono cambiare la sorgente tecnica dello stesso
contratto passano da un'API di pubblicazione che acquisisce il medesimo lock.
Un chiamante non acquisisce il lock prima di invocarla. Il lock serializza gli
scrittori cooperanti; `expected_generation_id` impedisce di usare un candidato
preparato prima dell'acquisizione e ormai obsoleto.

### 6.2 Pubblicazione tecnica

`publish_technical_update()` è il percorso del comando `sign.py publish` e
possiede l'intera transazione:

1. acquisisce una sola volta mutex e `writer.lock`; nessun chiamante e nessun
   helper interno li acquisisce di nuovo;
2. rilegge e autentica integralmente i byte persistiti di `current` prima di
   confronto o idempotenza: struttura, firma, stato e generation digest devono
   essere validi. Non confronta però il vecchio digest dichiarato col codice
   che l'aggiornamento sta intenzionalmente sostituendo; il candidato viene
   invece verificato contro il codice corrente. Per un contratto nuovo ammette
   `expected_generation_id=None`
   soltanto se `current` e cronologia non esistono; crea quindi il binding
   immutabile prima della prima generazione o riusa un binding byte-identico
   lasciato da un tentativo interrotto. Una cronologia senza
   `current` è corruzione, non inizializzazione;
3. richiede che gli hash di authoring contenuti nel draft coincidano ancora
   con i file sorgente, così il mirror non sovrascrive un edit concorrente;
4. confronta draft e generazione corrente, applica la politica linguistica di
   §6.3 e verifica la differenza tecnica completa;
5. calcola il digest dal codice corrente usando base sorgente e containment,
   aggiorna soltanto `[code].digest` nei byte in memoria e riverifica standard,
   ammissione e controlli RM-0002 richiesti;
6. firma i byte in memoria con l'oggetto chiave ricevuto, riverifica subito la
   firma e costruisce lo stato canonico;
7. applica postcondizione, idempotenza, generazione e puntatore con lo stesso
   algoritmo di §6;
8. soltanto nel percorso produttivo abilitato da M4, riconcilia authoring sotto
   lo stesso lock; dopo il rilascio rilegge `current` e riconcilia il registro.

`publish_signed_source()` resta il confine per una sorgente importata già
firmata. Possiede a sua volta un solo lock, non chiama
`publish_technical_update()` e non rifirma: verifica gli stessi vincoli di
base, codice, lingua, rimozione e postcondizione prima di delegare agli helper
`_locked`. Installer e generatori locali usano invece
`publish_technical_update()`.

### 6.3 Politica generale degli aggiornamenti tecnici

Un publish tecnico può cambiare codice, schema e superfici senza eccezioni per
executor, ma non può usare la firma dell'autore per aggirare il controllo
linguistico:

- ogni coppia selettore/lingua già presente conserva **identici** testo e stato;
  la modifica di un testo esistente passa da `publish_localization()`;
- ogni lingua già corrente resta rappresentata in tutte le superfici ancora
  applicabili;
- nuovi selettori e nuove lingue sono ammessi, ma devono avere copertura
  completa, stato canonico e superare linter locale e parità RM-0002 contro le
  altre lingue; il publisher non genera né completa traduzioni;
- il comando ordinario rifiuta qualunque rimozione di selettore;
- una rimozione di schema è ammessa soltanto con `SurfaceRemoval`: la lista
  deve coincidere esattamente con i selettori scomparsi, ciascuno deve non
  essere più applicabile al nuovo schema, e attore, motivo e diff vengono
  registrati nell'audit. La lista non autorizza modifiche o rimozioni diverse.

La prova di non regressione obbligatoria è: pubblicazione linguistica,
modifica soltanto tecnica del codice, `sign.py publish`, nuova generazione con
il nuovo digest e con testi e provenienza precedenti identici. Le prove
aggiungono inoltre un nuovo argomento multilingue valido, rifiutano una nuova
superficie incompleta e distinguono la rimozione ordinaria da quella esplicita
e auditata.

## 7. Lettura, sorgenti e ripristino

### 7.1 Loader

Il bootstrap applica la matrice di §4.3: usa il catalogo legacy soltanto quando
marker e radice produttiva sono entrambi assenti. In modalità deposito:

1. enumera le directory `<contract-key>` e legge soltanto `binding.json`;
2. verifica versione, `ContractId`, corrispondenza dello storage key e unicità;
3. ricostruisce un `ManifestRef` strutturale dalla mappa generale degli origin,
   senza aprire il manifest sorgente;
4. passa quel `ManifestRef` a `current_manifest(ref, ...)`;
5. legge `current` una sola volta, valida l'identificatore, ricalcola la
   generazione e costruisce `VerifiedManifest`;
6. costruisce l'executor esclusivamente da `VerifiedManifest.parsed`.

Un avvio a freddo con puntatore malformato, generazione assente o firma non
valida rifiuta quel contratto. Non sceglie “la directory più recente” e non
ripiega silenziosamente sulla sorgente di authoring. Marker presente con
radice mancante blocca l'intero bootstrap; radice presente con marker mancante
resta store-only e segnala il marker da ripristinare in manutenzione.

### 7.2 Sorgenti tecniche

Manifest e codice nel repository, negli import o nelle directory di generazione
restano sorgenti di authoring. Possono essere creati, provati e firmati con gli
strumenti esistenti. Diventano vivi soltanto attraverso
`publish_technical_update()` oppure, per import già firmati,
`publish_signed_source()`.

Il comando quotidiano è `python3 runtime/sign.py publish <directory>` (§5.3).
Il solo `sign` prepara una sorgente offline e non promette che sia viva.

Questo evita un divieto indiscriminato di `write_text()`: la guardia statica
deve vietare scritture dentro il deposito e letture vive dalle sorgenti per i
contratti dopo il cutover globale, non impedire a generatori e installer di
preparare artefatti.

### 7.3 Ripristino

`rollback()` acquisisce il lock, verifica che `current` coincida con la
generazione attesa, verifica integralmente la generazione scelta e sostituisce
atomicamente il puntatore. Registra attore e motivo nel normale audit operativo.
Non modifica né duplica alcuna generazione. Prima di rilasciare il lock
riconcilia i tre file di authoring con quella scelta soltanto nel percorso vivo
abilitato da M4; dopo il rilascio rilegge `current` e, nello stesso caso,
riconcilia il registro come in §6. Se un writer è intervenuto nel frattempo,
prevale sempre la fotografia appena riletta.

Staging incompleti e generazioni complete non referenziate vengono segnalati da
una diagnostica in sola lettura. La prima versione non li elimina.

## 8. Modifiche file per file

| File | Modifica obbligatoria | Vietato |
|---|---|---|
| `runtime/contract_store.py` | unico nuovo modulo: binding, snapshot, lock, generazioni, publisher linguistico/tecnico e cutover | framework di plugin, DB, seconda firma o lock annidati |
| `runtime/sign.py` | primitive pure; comando `publish` che prepara il draft e delega l'unico lock al deposito; avviso fail-loud per `sign` | sign-then-publish o lock proprio del comando |
| `runtime/loader.py` | matrice bootstrap marker/radice, binding strutturali e snapshot verificato | leggere authoring post-cutover, modalità ibrida o ripiego silenzioso |
| `runtime/i18n_pipeline.py` | M3 integrazione isolata/flag off; M4 chiamata viva a `publish_localization()` | scrivere manifest o firma vivi prima del cutover |
| `runtime/i18n_materializer.py` | unica enumerazione dei selettori e unico decoder/encoder canonico dello stato manifest | copie locali dello schema JSON |
| `runtime/i18n_activation.py` | validatore reale sulla lingua e sullo snapshot | validatore sempre positivo nel test completo |
| `runtime/i18n_translator.py` | allineatore ritirato o delegato | secondo publisher linguistico |
| `runtime/synth_request.py`, `runtime/migrate_manifest_descriptions.py`, `runtime/admin/i18n_migrate_manifests.py` | produrre soltanto selettori e byte-state canonici | forma legacy `args.<name>.description` |
| installer e generatori locali | `publish_technical_update()` dopo ammissione | firma offline seguita da secondo publish |
| importatori di artefatti già firmati | `publish_signed_source()` con gli stessi controlli tecnici e linguistici | bypass per provenienza esterna |
| `runtime/manifest_inventory.py` | prima del cutover inventario completo; dopo, `ManifestRef` strutturali da binding + mappa origin | parsificare authoring come contenuto vivo o indice globale duplicato |
| `CLAUDE.md` §7.10 | nello stesso commit di cutover, sostituire il flusso firma→riavvio con `sign.py publish` | aggiornamento anticipato o successivo al cutover |

### 8.1 Censimento obbligatorio dei confini

M0 produce un rapporto versionato, ricavato da ricerca statica e prove di
caratterizzazione, di ogni callsite in `runtime/`, `install/` e `scripts/` che
legge o scrive `manifest.toml`, `manifest.toml.sig`,
`manifest.lang_state.json`, chiama firma/verifica oppure costruisce un catalogo
executor. Per ciascuno registra: file e funzione, lettore o scrittore,
authoring o vivo, API di destinazione, fase di migrazione e prova.

Il rapporto non usa un elenco numerico cablato: una guardia rigenerabile
fallisce quando compare un callsite non classificato. M1 non inizia finché
ogni voce è classificata; M4 non crea `ACTIVE` finché ogni lettore vivo usa
`current_manifest(ref, ...)` e ogni scrittore vivo usa `publish_localization()`,
`publish_technical_update()`, `publish_signed_source()` o `rollback()`. Gli
strumenti che modificano in posto una sorgente ammessa passano dal publisher;
i generatori di directory nuove entrano nel lock soltanto al publish.

## 9. Ordine di implementazione

Ogni fase corrisponde a un commit autonomo. Nessuna implementazione deve
saltare il gate della precedente. La controrevisione è risolta e RM-0007 è
`ready`; M0 parte soltanto dopo la consegna di RM-0002 L2, così `ContractId`
resta proprietà dell'inventario comune e la dipendenza non si inverte.

### M0 — Caratterizzazione e inventario

- aggiungere prove dei difetti elencati in §1;
- produrre il censimento completo e rigenerabile di §8.1;
- consumare senza duplicarlo l'inventario condiviso consegnato da RM-0002 L2;
- caratterizzare i percorsi relativi, compreso almeno un contratto reale con
  `../../`, provando base sorgente e contenimento nelle radici ammesse;
- creare harness multiprocesso per generazione, conflitto e arresto, eseguibile
  sia su Linux sia su Windows.

**Gate:** ogni difetto ha una prova causale; nessun conteggio del catalogo è
cablato.

### M1 — Fotografia e firma pura

- estrarre le primitive pure in `sign.py`;
- implementare `VerifiedManifest` sul layout legacy usando directory sorgente
  e radici ammesse esplicite;
- fare usare al loader gli stessi byte verificati dietro flag spento;
- aggiungere una prova di attivazione col validatore reale.

**Gate:** sostituire A con B fra le letture non produce combinazioni; catalogo
legacy invariato; primitive pure senza accessi al filesystem.

### M2 — Deposito minimo

- implementare binding immutabile, generazione, puntatore, lock e diagnostica in
  `contract_store.py`;
- implementare `publish_signed_source()` e generazione iniziale soltanto su
  radice shadow/fixture;
- rifiutare qualunque radice M2 che coincida, contenga o sia contenuta dalla
  radice o dal marker produttivi; il lock mutante resta helper interno e non
  offre un default che possa creare prematuramente la boundary produttiva;
- provare mutex + lock multiprocesso, conflitto, idempotenza per generazione
  completa, riuso dopo arresto fra rename e puntatore, corruzione della
  destinazione, retry finiti di `os.replace()` e ogni confine di arresto;
- non modificare ancora il loader di produzione.

**Gate:** da una stessa base committa un solo writer; il puntatore indica
sempre una generazione completa; prova reale Linux e Windows.

**Completata il 2026-08-25.** Il deposito minimo è nei commit `04da9cba` e
`4ad21acf`. La copia pubblica `babdb95f05846eae43617db65e62dd71c2220708`
ha superato il run GitHub Actions `32794475658` sia su Ubuntu sia su Windows
Server 2022 con NTFS reale.

### M3 — Pubblicazione linguistica

- implementare patch, confronto strutturale e digest preservato;
- implementare `TechnicalDraft`, `publish_technical_update()` e la politica di
  §6.3, con un solo owner del lock;
- provare il collegamento alla pipeline RM-0005 soltanto su deposito isolato o
  dietro flag spento per default;
- distinguere traduzione pubblicata da lingua attivata;
- applicare la stessa politica linguistica agli import già firmati.

M3 **non** modifica authoring produttivo, non cambia il registro produttivo e
non rende vivo il publisher: finché il legacy è autorevole, un mirror sarebbe
esso stesso una pubblicazione anticipata. Il gate è interno alla migrazione,
non una preferenza utente: le chiamate sullo store produttivo restituiscono
`publication_not_active`; soltanto fixture con radice iniettata possono
abilitarlo. M4 rimuove il blocco nello stesso cutover globale.

**Gate:** nessuna differenza tecnica viene firmata; candidato obsoleto e
validatore fallito lasciano invariato il puntatore; il retry post-commit è
idempotente nelle fixture; flag produttivo ancora spento e nessun file vivo
mutato.

**Completata il 2026-08-25 nel commit `d1abd0e2`.** La pipeline usa
`metnos.localization-candidate/2`, identità strutturale e `basis_id`; il
registro applica CAS esatto a lease, revisione e qualità. Il publisher resta
dormiente e accetta soltanto una radice isolata. Il gate combinato conta 454
test e 1.136 sottoprove verdi; tre prove specifiche di piattaforma sono state
saltate su Linux. È compresa la sequenza traduzione → modifica del solo codice
→ pubblicazione tecnica: la firma individua la generazione di authoring, il
diff tecnico viene ribasato sulla generazione viva e testo e provenienza
restano identici. Nessun authoring o registro produttivo è stato modificato.

### M4 — Migrazione e cutover

- migrare una tantum lo stato al formato canonico di §4.4, validando ogni
  selettore, e correggere tutti i produttori;
- creare generazioni iniziali soltanto da sorgenti canoniche e valide;
- confrontare catalogo legacy e versionato in modalità ombra;
- migrare i callsite censiti a `publish_technical_update()` oppure, solo per
  import già firmati, `publish_signed_source()`, e al comando canonico
  `sign.py publish`;
- collegare realmente la pipeline RM-0005, attivare la riconciliazione
  authoring sotto lock e quella del registro da una nuova lettura, quindi
  delegare o ritirare l'allineatore legacy;
- entrare in manutenzione quiescente, rigenerare l'inventario e riverificare
  l'intero deposito contro il codice corrente;
- creare e rendere durevole `ACTIVE`, eseguire lo swap globale shadow→radice
  produttiva e caricare il catalogo store-only mentre gli ingressi restano
  bloccati;
- aggiungere la guardia statica descritta in §7.2;
- nello stesso commit che crea il cutover, aggiornare `CLAUDE.md` §7.10 al
  flusso `python3 runtime/sign.py publish executors/<nome>`; l'autorizzazione è
  stata data da Roberto e la modifica non deve precedere né seguire il cutover;
- provare ripristino, modifica solo tecnica dopo localizzazione, aggiornamento
  e riavvio.

**Gate:** legacy soltanto con marker e radice entrambi assenti; la presenza di
uno dei due vieta ogni fallback; binding completi; nessun secondo publisher
linguistico; nessun executor scompare; `CLAUDE.md` descrive il comportamento
vivo; ingressi riaperti solo dopo load/restart globale verde; due cicli completi
verdi. ADR 0223 resta `proposed` fino al superamento di questo gate.

## 10. Prove obbligatorie

### 10.1 Autorità e differenza

- firma o digest della base errati;
- modifica di schema, capability, placement, policy, undo o codice;
- modifica di una lingua diversa;
- selettore non registrato o appartenente a un altro contratto;
- sorgente o bersaglio cambiati dopo la candidatura;
- path del registro fuori dall'inventario;
- lingua BCP-47 non canonica o collidente;
- `code.files` con `../../` risolto correttamente dalla directory sorgente e lo
  stesso percorso rifiutato quando, dopo `resolve()`, esce da tutte le radici
  ammesse;
- directory della generazione dimostrata estranea alla risoluzione del digest;
- `binding.json` canonico accettato; contract-key, versione o ContractId
  incoerenti, binding modificato, duplicato o con path assoluti rifiutati;
- selettore legacy `args.<name>.description`, selettore inesistente e stato
  non canonico rifiutati senza alias impliciti;
- migrazione data-led: risorsa mancante aggiunta, selettore orfano scartato e
  segnalato, provenienza conservata soltanto con `version_hash` coerente,
  provenienza azzerata quando il testo è cambiato e collisione rifiutata;
- serializzazioni ottenute da ordini di inserimento diversi producono gli
  stessi byte canonici e la stessa generazione.

### 10.2 Coerenza e concorrenza

- manifest parsificato dagli stessi byte firmati;
- arresto dopo il binding e prima della prima generazione: retry con binding
  identico completa; binding differente blocca;
- file mancante, aggiunto o modificato nella generazione;
- due promotori dalla stessa generazione: uno solo riesce;
- stesso candidato ripetuto dopo il commit: successo idempotente;
- arresto dopo il rename e prima del puntatore: riuso verificato della stessa
  directory, barriera ripetuta e commit riuscito;
- directory finale omonima con file extra, symlink/reparse point o byte diversi:
  `generation_corrupt`, mai sostituzione;
- candidato diverso con base vecchia: conflitto;
- loader continuo: soltanto generazione vecchia o nuova;
- scrittore tecnico cooperante contro publisher linguistico;
- due thread nello stesso processo e due processi distinti serializzati;
- `current` corrotto o non verificabile non può produrre `repeated=True`;
- writer A seguito da writer B: la riconciliazione fuori lock rilegge B e non
  ripristina nel registro lo stato di A;
- `sign.py publish` produce una sola acquisizione del writer lock; un helper che
  tenta di riacquisirlo fa fallire la prova, non viene tollerato dal timeout.

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

Su Windows le prove reali comprendono inoltre: lock e unlock del primo byte
sullo stesso handle; contesa fra processi con timeout monotono; riuso della
directory `<64hex>`; assenza di `:` nei nomi; lettore che chiude subito
`current`; violazione di condivisione transitoria durante `os.replace()` con
retry finito; nessuna cancellazione preventiva del puntatore. Un mock Windows
su Linux non soddisfa il gate M2/M4.

### 10.4 Integrazione

- validatore reale nella lingua bersaglio;
- fallimento pre-commit senza modifica visibile;
- pubblicato ma non attivato dichiarato come dormiente;
- sorgente tecnica modificata non diventa viva senza pubblicazione;
- generazione iniziale rifiutata se la sorgente non è valida;
- rollback solo verso generazione verificata;
- registro riconciliato dopo arresto post-commit;
- cache invalidata da `generation_id`, non dalla combinazione di `mtime`;
- marker assente con deposito shadow completo: loader solo legacy;
- marker e radice produttiva entrambi assenti: loader legacy; ciascuna loro
  presenza separata vieta il fallback;
- arresto dopo il marker durevole e prima dello swap: bootstrap fail-closed e
  ripresa del cutover; perdita del marker con radice presente: store-only;
- retry di cutover con marker + radice valida: `repeated`; radice incoerente:
  rifiuto senza cancellazione; radice valida senza marker: ripristino soltanto
  in manutenzione;
- creazione di `ACTIVE` impossibile se binding o generazione iniziale non sono
  validi;
- cutover rifiutato senza quiescenza dimostrata e inventario rigenerato subito
  prima del marker; nuovi turni, scheduler, publisher, reload e watcher restano
  bloccati fino a load store-only e restart/swap globale completi e verdi;
- marker presente con `current` mancante: rifiuto fail-closed e nessun fallback;
- nuova sorgente authoring dopo `ACTIVE`: resta invisibile finché
  `sign.py publish` crea binding e prima generazione; `current` mancante con
  cronologia preesistente resta corruzione e non viene reinizializzato;
- traduzione pubblicata → modifica solo tecnica → `sign.py publish`: traduzione
  e provenienza conservate; sorgente stale equivalente rifiutata;
- nuovo selettore e nuova lingua completi ammessi soltanto dopo linter locale e
  parità RM-0002; testo esistente modificato e nuova superficie incompleta
  rifiutati; rimozione ordinaria rifiutata, rimozione con lista esatta e motivo
  auditata;
- M3 con flag spento restituisce `publication_not_active` e non modifica
  manifest, firma, stato o registro produttivi;
- post-cutover, una sentinella che rende illeggibili i manifest di authoring non
  impedisce al loader di enumerare binding e caricare il catalogo;
- ramo idempotente: authoring riparato sotto lock e registro riconciliato da
  una nuova lettura;
- censimento rigenerato senza callsite lettori o scrittori non classificati;
- `sign` solo restituisce un messaggio inequivoco “firmato, non pubblicato” e
  `publish` fallisce se il puntatore finale non è quello atteso.

## 11. Rischi

| Rischio | Gravità | Contromisura |
|---|---:|---|
| traduzione adotta modifica tecnica | bloccante | base e digest verificati, firma pura |
| verificato A, caricato B | bloccante | fotografia unica |
| manifest, firma e stato incoerenti | bloccante | generazione immutabile e puntatore unico |
| aggiornamento perso | alta | lock e generazione attesa |
| path del registro diventa autorità | alta | `ContractId` dall'inventario |
| digest risolto dalla generazione o fuori radice | bloccante | base sorgente censita + containment dopo `resolve()` |
| retry confonde patch presenti con commit completo | alta | riverifica `current` + uguaglianza del `generation_id` desiderato |
| pubblicazione tecnica cancella o bypassa traduzioni | bloccante | politica §6.3 + controlli RM-0002 + rimozione esplicita auditata |
| writer legacy resta vivo | alta | cutover e guardia statica mirata |
| catalogo ibrido o ritorno implicito al legacy | bloccante | shadow separata; marker durevole prima dello swap; root o marker vietano fallback |
| authoring torna contenuto vivo post-cutover | bloccante | binding minimo + `current_manifest(ref)` + `VerifiedManifest.parsed` |
| puntatore corrotto al cold boot | alta | rifiuto del contratto, nessuna scelta euristica |
| codice cambia dopo la verifica | alta, residua | digest rifiutato al load; EXEC-BIND-001 per garanzia forte |
| semantica Windows diversa | alta | prova NTFS reale |
| crescita dello spazio | bassa | circa 7,5 KB per generazione misurati; rapporto, nessuna GC prematura |

## 12. Criteri di completamento

RM-0007 passa a `implemented` soltanto quando:

- la localizzazione non ricalcola il digest;
- la base viene verificata prima della modifica e nuovamente sotto lock;
- il loader interpreta gli stessi byte verificati;
- manifest, firma e stato diventano visibili come una generazione;
- un candidato obsoleto non cambia il puntatore;
- due scrittori non perdono aggiornamenti;
- il registro i18n non concede path;
- il digest usa la directory sorgente e non può uscire dalle radici ammesse;
- selettori e byte dello stato linguistico rispettano l'unico formato canonico;
- il validatore reale è coperto dall'attivazione;
- l'allineatore non è un secondo publisher;
- le sorgenti non diventano vive senza `publish_technical_update()` o
  `publish_signed_source()`;
- una pubblicazione tecnica conserva testi/stato esistenti, controlla ogni
  aggiunta e rende esplicita ogni rimozione;
- il retry riusa soltanto una generazione integralmente identica e verificata;
- authoring e registro vengono riconciliati anche nel ramo idempotente;
- crash e concorrenza sono provati su Linux e Windows;
- una pubblicazione fallita non rende indisponibile la generazione precedente;
- non esistono eccezioni per executor o lingua;
- marker/radice effettuano un unico cutover globale che non torna implicitamente
  al legacy;
- il loader post-cutover enumera binding e usa solo
  `VerifiedManifest.parsed`, senza parsificare authoring;
- M3 resta non produttivo e mirror/registro entrano soltanto con M4;
- `sign.py publish` ha un solo proprietario del lock e firma dentro la stessa
  transazione;
- il comando operativo e `CLAUDE.md` §7.10 descrivono lo stesso flusso vivo;
- il censimento non contiene callsite vivi non migrati.

Passa a `closed` dopo cutover sull'installazione di riferimento,
documentazione operativa e rapporto finale. La controrevisione è risolta e lo
sviluppo può iniziare nell'ordine M0-M4. ADR 0223 resta `proposed` e passa ad
`accepted` soltanto insieme al gate M4 verde.

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
11. Se esiste `ACTIVE` **oppure** la radice produttiva, non usare il ripiego
    legacy per alcun contratto.
12. Fermarsi se una fase richiede di allentare firma, digest, contenimento o
    confronto della generazione attesa.
13. Registrare per ogni fase commit, prove e risultato.
14. Risolvere `code.files` dalla directory sorgente dell'inventario e verificare
    sempre il contenimento dopo la normalizzazione; non passare mai la
    directory della generazione alle funzioni di digest.
15. Non dichiarare idempotenza prima di avere verificato `current` e confrontato
    l'identificatore dell'intera postcondizione.
16. Su Windows usare un file di lock permanente di almeno un byte, lo stesso
    handle e retry monotoni finiti; non cancellare mai `current` per facilitare
    `os.replace()`.
17. Il lock appartiene all'API di pubblicazione: nessun comando o helper lo
    acquisisce prima di chiamarla e nessun publisher pubblico ne chiama un
    altro.
18. Tenere lo shadow fuori dalla radice produttiva; dopo la verifica creare e
    rendere durevole `ACTIVE`, poi fare un solo swap globale. Non cancellare il
    marker e non interpretarne l'assenza come legacy quando la radice esiste.
19. Modificare `CLAUDE.md` §7.10 soltanto nel commit del cutover M4 e insieme al
    comando `sign.py publish` funzionante e provato.
20. Dopo una pubblicazione linguistica, provare sempre il successivo publish
    tecnico: coppie selettore/lingua esistenti devono restare identiche; nuove
    superfici passano RM-0002 e rimozioni richiedono lista esatta e motivo.
21. Dopo il cutover costruire `ManifestRef` dai binding e dalla mappa origin;
    non parsificare authoring per ricavare contenuto vivo.
22. In M3 mantenere spento il collegamento produttivo e non eseguire mirror;
    abilitarli soltanto dentro il cutover M4 quiescente.

## 15. Mandato per la controrevisione esterna

> Sezione storica, già eseguita. Conserva le domande originarie per interpretare
> il rapporto collegato in §17; non prevale sulla specifica aggiornata e non è
> un gate ancora aperto.

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
| 2026-08-25 | `ready` | B1-B4 e irrobustimenti integrati nei §§3-14; aggiunto cutover globale fail-closed; autorizzato l'aggiornamento coordinato di `CLAUDE.md` in M4 |
| 2026-08-25 | `ready` | seconda revisione avversariale integrata: boundary irreversibile, binding strutturali, M3 non produttiva, singolo lock tecnico, quiescenza completa e politica evolutiva; ADR resta `proposed` fino a M4 |
| 2026-08-25 | `in_progress` | M0 e M1 implementate e provate; M2 avviata sulla specifica consolidata |
| 2026-08-25 | `in_progress` | M2 certificata su Linux e Windows/NTFS; M3 implementata, controrevisionata e confinata a depositi isolati; M4 avviabile |

## 17. Controrevisioni — tracciabilità

Il testo integrale della controrevisione esterna del 2026-08-25 è conservato
nel [rapporto storico](../reports/rm0007-external-review-20260825.md). È
storico e non normativo; prevalgono sempre i §§1-14 di questa roadmap.

| Rilievo | Risoluzione normativa | Gate |
|---|---|---|
| B1 · base del digest | directory sorgente da `ManifestRef` e containment nelle radici ammesse (§§3.4, 5.1, 10.1) | M0-M2 |
| B2 · selettore/stato canonico | percorsi completi dello schema `args`, encoder unico e migrazione data-led con report (§4.4) | M2-M4 |
| B3 · retry dopo rename | verifica/riuso esatto della generazione e barriera ripetuta (§§4.2, 6) | M2 |
| B4 · workflow operativo | `sign.py publish` e `CLAUDE.md` §7.10 nello stesso cutover (§§5.3, 9) | M4 |
| C1 · ritorno implicito al legacy | shadow separata; marker durevole prima dello swap; marker **o** radice vietano fallback (§4.3) | M4 |
| C2 · authoring letto post-cutover | `binding.json` minimo, `ManifestRef` strutturale e contenuto solo da `VerifiedManifest.parsed` (§§4.1, 5.1, 7.1) | M2-M4 |
| C3 · mirror anticipato | M3 isolata/flag off; mirror e registro produttivi soltanto in M4 (§9) | M3-M4 |
| C4 · lock annidato nel publish tecnico | un solo owner del lock; draft, verifica, firma, publish e mirror nella stessa transazione (§§5.3, 6.2) | M3 |
| C5 · quiescenza incompleta | blocco di turni, scheduler, publisher, reload e watcher fino a load/restart store-only verde (§4.3) | M4 |
| C6 · evoluzione tecnica delle superfici | coppie esistenti immutate; aggiunte sotto RM-0002; rimozioni esplicite e auditate (§6.3) | M3-M4 |
| C7 · stato ADR prematuro | ADR 0223 resta `proposed` fino al gate M4 verde (§12) | M4 |

RM-0007 è `in_progress`: M0-M3 sono implementate e certificate. Restano M4,
il cutover produttivo, la ricertificazione completa e l'accettazione finale
dell'ADR 0223.
