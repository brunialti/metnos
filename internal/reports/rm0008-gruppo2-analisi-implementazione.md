# RM-0008 — Analisi di implementazione del gruppo 2

## 1. Scopo e stato della decisione

Questo documento definisce il solo gruppo 2 della sequenza non permutabile del
§23.6 di RM-0008: radice autore e predisposizione delle autorità. È un ingresso
eseguibile per agenti di codifica e non autorizza ancora la migrazione dei
chiamanti, il collegamento della guardia chiusa, il passaggio F4 o la chiusura
della roadmap.

La base di sviluppo è la registrazione Git `f1699122`. Le modifiche locali
successive sono conservate esclusivamente come materiale forense. Non devono
essere copiate in blocco, perché mescolano responsabilità dei gruppi 2, 3 e 4 e
contengono una realizzazione Windows che non chiude le gare sui percorsi.

Il gruppo 2 non è completato dalla sola generazione di coppie di chiavi. Il suo
criterio di uscita richiede contemporaneamente:

1. l'autenticazione della chiave autore predefinita e la sua consegna privata al
   solo pubblicatore Birth;
2. la predisposizione completa e separata delle autorità Admission e Producer;
3. l'installazione dei registri pubblici di approvazione e revisione semantica;
4. la predisposizione canonica e rileggibile del materiale del contesto di
   ammissione, senza dichiararlo ancora applicato dal runtime;
5. una transazione recuperabile e sicura su Linux e Windows;
6. prove di modulo, di processo e di arresto che attraversino i caricatori
   produttivi, più una prova installata limitata al predispositore. La prova
   installata dell'intero bootstrap, dell'avvio e della pubblicazione appartiene
   al gruppo 3 e ai gruppi successivi.

Anche dopo il soddisfacimento di queste condizioni, il risultato del gruppo 2
rimane `prepared_not_active` e non crea un `bootstrap.json` produttivo.

## 2. Diagnosi verificata

### 2.1 Cause radice

| Causa | Evidenza nel codice corrente | Conseguenza |
|---|---|---|
| Il pubblicatore non riceve la chiave autore. | `executor_birth_bootstrap._build()` passa a `_assemble_birth_core()` soltanto chiavi fidate pubbliche, mentre `contract_store.commit_birth_snapshot()` richiede `private_key`. | Una richiesta ammessa non può completare la pubblicazione produttiva. |
| La provenienza e la paternità sono configurabili. | Ogni voce `producers` di `bootstrap.json` contiene `origin` e `author`. | Il file di configurazione può scegliere fatti autorevoli che il §23.8 assegna a una tabella chiusa del gruppo 3. |
| Il contesto è dichiarativo, non effettivo. | `_context_builder()` accetta undici oggetti con percorsi e configurazioni scelti dal file di avvio. Il test integrato usa file vuoti e una mappa sintetica `{"name": componente}`. | Un `admission_context_id` può attestare valori che non rappresentano il codice, le politiche o i registri realmente usati. |
| Tre componenti del contesto non hanno ancora un consumatore produttivo. | `template_allowlist`, `primitive_allowlist` e `dependency_allowlist` sono campi dell'identità, ma non governano i controlli di `executor_birth_shadow.py`. | Modificarne il digest non modifica il comportamento; la loro presenza attuale non costituisce una barriera. |
| Il catalogo produttivo dei controlli è incompleto rispetto al §7. | `_CHECK_CATALOG_V1` esegue soltanto Standard, proprietà, revisione semantica e approvazione. | Linter, vocabolario, chiusura delle dipendenze e altri controlli elencati dalla roadmap non sono dimostrati dal percorso produttivo. |
| Approvazione e revisione semantica caricano soltanto materiale pubblico. | I due moduli di autorità dichiarano esplicitamente che la firma avviene fuori banda. | Un test che genera una chiave privata e poi la scarta dimostra soltanto il caricamento, non una predisposizione produttiva utilizzabile. |
| La sicurezza Windows è duplicata e basata in parte sui nomi di percorso. | Caricatore semantico, archivio chiavi e tentativo di predisposizione implementano verifiche differenti. | Restano finestre tra controllo del nome e uso dell'oggetto, gestione incompleta di junction, proprietario e liste di controllo degli accessi non uniformi. |
| Il file di blocco vuoto è trattato come corruzione. | Il caricatore Windows usa un blocco a intervallo che richiede almeno un byte. | Un arresto dopo la creazione e prima della scrittura lascia un impedimento permanente invece di uno stato recuperabile. |
| Le prove di modulo sono state interpretate come avanzamento del prodotto. | Le prove costruiscono archivi e configurazioni temporanei senza attraversare l'installatore e l'avvio installato. | Un esito verde non dimostra predisposizione, riavvio, identità del servizio o uso del percorso produttivo. |

### 2.2 Effetti osservati che non sono cause

Gli errori Windows della matrice pubblica sono effetti utili per localizzare i
difetti, ma non sono la causa architetturale principale. Correggere in sequenza
la dimensione di un file, un controllo ACL o una singola eccezione senza una
transazione e un modello di oggetti comune produce varianti dello stesso errore.
Per questo il codice multipiattaforma deve essere preceduto dalla primitiva di
accesso sicuro definita nel §7.

### 2.3 Confine di minaccia e garanzie per gruppo

Il confine finale di RM-0008, definito nei §§13 e 18 della roadmap, protegge da
servizio, chiamanti, installatore non autorizzato e arresti. Il solo gruppo 2
non può ancora dimostrare l'intero confine: l'installazione corrente esegue
`metnos-http` come servizio utente, permette di scegliere le radici mediante
`METNOS_USER_CONFIG` e `METNOS_INSTALL_ROOT` e conserva le chiavi sotto una
directory posseduta dallo stesso utente. Un processo con lo stesso UID Linux o
lo stesso SID Windows può quindi leggere o modificare file `0600`/`0700` o ACL
equivalenti. Nessuna closure Python corregge questa proprietà del sistema
operativo.

Le garanzie esatte del gruppo 2 sono perciò:

1. nessuna API Birth permette al chiamante di scegliere chiave, anello di
   fiducia, emittente Admission, verificatore, percorso o primitiva di commit;
2. il predispositore rifiuta link, junction, hard link, sostituzioni di oggetti,
   inventari inattesi, concorrenza ambigua e arresti non riconciliabili;
3. i file privati sono confidenziali rispetto a identità del sistema operativo
   diverse dall'identità autorizzata dal predispositore;
4. la vista pubblica non rende raggiungibili private o capacità di firma;
5. il risultato rimane inerte: non abilita il bootstrap, non autorizza una
   pubblicazione e non costituisce ancora la protezione finale dal servizio.

La protezione finale da processi dello stesso utente e dall'installatore non
autorizzato nasce soltanto quando i gruppi 4-6 combinano build chiusa,
distribuzione firmata, controllo preliminare e descrittore amministrativo. Se
la distribuzione finale mantiene il servizio sotto lo stesso utente, il suo
modello deve dichiarare esplicitamente fuori confine gli altri processi con lo
stesso UID/SID; se invece li include, il gruppo 6 deve introdurre una distinta
identità di servizio e una radice amministrativa non scrivibile da tale
identità. Questa decisione non viene anticipata dal gruppo 2 e deve essere
chiusa prima del passaggio reale F4.

Codice arbitrario già in esecuzione nello stesso interprete della chiave privata
può usarla o estrarla. In V1 questa superficie è chiusa staticamente dalla
guardia e dalla distribuzione firmata; una resistenza anche a codice ostile già
nel processo richiederebbe un firmatario separato o hardware crittografico.
`root`, `SYSTEM` e un amministratore realmente elevato restano fuori dal confine
di riservatezza V1.

## 3. Perimetro del gruppo 2

### 3.1 Lavoro incluso

Il gruppo 2 comprende:

- una sola astrazione multipiattaforma per aprire, creare, leggere, scrivere,
  bloccare e validare gli oggetti della predisposizione;
- la migrazione recuperabile della sola chiave privata `author` e dell'intero
  anello pubblico fidato verso un archivio Birth a percorso fisso;
- un archivio Admission distinto;
- un archivio Producer distinto per ogni capacità presente nel catalogo chiuso
  `_producer_capabilities_for_bootstrap()`;
- l'importazione, da una collocazione operatore fissa, del registro pubblico di
  approvazione e dell'autorità pubblica di revisione semantica;
- la creazione della directory vuota delle evidenze semantiche, senza fabbricare
  evidenze;
- un catalogo canonico del materiale del contesto, costruito dal codice e dai
  registri predisposti ma ancora inerte;
- un collegamento privato autore-Admission-primitiva di commit provato in
  integrazione su una radice isolata, senza installarlo nel bootstrap globale;
- una vista pubblica di sola verifica, priva di chiavi private, nucleo, fabbriche
  o funzioni di pubblicazione;
- un descrittore finale `prepared-v1.json`, scritto per ultimo e riletto prima
  del successo;
- prove Linux e Windows che includono processi concorrenti e arresti reali del
  processo in punti durevoli.

### 3.2 Lavoro espressamente rinviato

Il gruppo 2 non deve:

- inserire `origin` o `author` in file modificabili dall'operatore;
- costruire la tabella chiusa `ContractId -> (origine, autore)`, che appartiene
  al gruppo 3;
- attivare le fabbriche Producer produttive senza tale tabella;
- emettere il `bootstrap.json` finale o installare il nucleo globale Birth;
- migrare i 21 executor incorporati;
- sostituire la firma diretta di Phase 3 o del generatore incorporato;
- implementare il bootstrap iniziale, la riattestazione o la macchina completa
  di convergenza dell'installatore;
- interpretare o modificare gli stati `legacy`, `recovery_required`,
  `store_only` e `active` di Phase 3;
- applicare il catalogo del contesto ai controlli, ai 21 involucri o al runtime;
- rimuovere il decodificatore produttivo corrente prima che il gruppo 3 possa
  sostituirlo atomicamente con il percorso sigillato;
- rendere vincolante `--birth-closed`, modificare l'inventario finale della
  guardia o impostare a vero il diniego compilato;
- creare le chiavi distinte di distribuzione, passaggio e testa richieste dal
  gruppo 5;
- autenticare la distribuzione o proteggere dal medesimo UID/SID: queste prove
  richiedono i gruppi 4-6;
- dichiarare F4, F5, F6 o RM-0008 completate.

## 4. Disposizione dei dati

### 4.1 Percorsi fissi

Il predispositore riceve un solo `ProvisioningLayoutV1` costruito
dall'installatore, non dal runtime Birth e non dal chiamante del commit. Nel
gruppo 2 la disposizione transitoria predefinita rimane sotto
`PATH_USER_CONFIG/birth`, perché cambiare identità del servizio e radice
amministrativa appartiene alla distribuzione dei gruppi 5-6. Questa radice
transitoria non deve essere descritta come protetta dal medesimo UID/SID.

`ProvisioningLayoutV1` contiene soltanto descrittori già aperti della radice e
della collocazione di ingresso operatore, più l'identità di servizio attesa. Non
contiene percorsi forniti da JSON, ambiente letto dal runtime o dal candidato.
L'adattatore installatore può risolvere `PATH_USER_CONFIG` una volta prima di
entrare nel predispositore; il codice crittografico non lo rilegge.

La disposizione è:

```text
PATH_USER_CONFIG/birth/
├── provisioning-v1.lock
├── .birth-provisioning-v1.txn.<nonce>/
│   ├── transaction-v1.json
│   ├── checkpoints-v1/
│   │   └── <sequenza a 20 cifre>.json
│   ├── author-root-v1/
│   ├── authority-set/
│   └── prepared-v1.json
├── author-root-v1/
│   └── <archivio chiavi autore V1>
├── operator-input-v1/
│   ├── approval-authority.json
│   ├── semantic-authority.json
│   └── semantic-public/
│       └── <identificativo-chiave>.pub
├── authority-sets/
│   └── <set_id senza prefisso>/
│       ├── set.json
│       ├── admission/
│       │   └── <archivio chiavi V1>
│       ├── producers/
│       │   └── p-<digest-capacità>/
│       │       └── <archivio chiavi V1>
│       ├── approval/
│       │   └── authority.json
│       ├── semantic/
│       │   ├── authority.json
│       │   ├── public/
│       │   │   └── <identificativo-chiave>.pub
│       │   └── evidence/
│       └── context/
│           └── material-v1.json
└── prepared-v1.json
```

`operator-input-v1` è una collocazione di importazione a nome fisso. Il runtime
non la consulta. I file privati con cui l'operatore firma approvazioni o evidenze
semantiche non devono trovarsi in questa directory, nell'insieme di autorità o
nel processo Birth. Le prove isolate possono creare tali chiavi, ma devono
marcarle esplicitamente come sole fixture.

Prima dell'esecuzione di Phase 3 l'amministratore deve installare in questa
collocazione i due registri pubblici e le pubbliche semantiche mediante la
procedura operativa documentata dal gruppo 2. Il predispositore non genera né
importa le corrispondenti private e non accetta un percorso alternativo da riga
di comando, ambiente o JSON. L'installatore esegue un preflight di sola lettura:
assenza o invalidità produce gli errori distinti del §11 prima di creare la
transazione. Le fixture CI sono generate in una radice isolata e non vengono
copiate in un'installazione reale.

Il gruppo 2 installa soltanto il primo insieme. Non crea un puntatore `current`,
non seleziona epoche e non ruota chiavi. L'insieme è immutabile e il nome finale
deriva da `set_id`; un nome già esistente è accettato soltanto se ogni byte e
metadato riletti coincidono, altrimenti è conflitto. Se gruppi successivi
richiederanno rotazione, aggiungeranno un nuovo insieme e un selettore
autenticato senza modificare il primo.

`p-<digest-capacità>` usa il digest SHA-256 con dominio
`metnos.executor-birth.producer-capability-path/v1\0` del framing canonico di
`producer_id` e `operation`. Nessun nome di directory deriva mediante semplice
sostituzione di caratteri e nessun chiamante può aggiungere capacità.

`PATH_USER_STATE/birth` non viene attivato in questo gruppo. Le basi dati delle
ricevute Producer e delle approvazioni saranno create dal gruppo 3, quando
esisteranno le fabbriche chiuse e la macchina di convergenza. In questo modo un
indicatore di predisposizione non può essere confuso con un runtime attivo.

### 4.2 Sorgenti autore precedenti

`sign.DEFAULT_AUTHOR_KEY` vale il nome `"author"`, non un percorso. Le sole
sorgenti precedenti ammesse sono i nomi fissi
`PATH_USER_CONFIG/keys/author_priv.bin`,
`PATH_USER_CONFIG/keys/author_pub.bin` e tutti e soli i file regolari
`*_pub.bin` della medesima directory. Il predispositore li enumera e apre dalla
sessione sicura, impone inventario, dimensione e identificativo, e fallisce su
qualsiasi pubblica malformata. Non richiama `sign.list_trusted_publics()`, che
oggi ignora silenziosamente alcuni file invalidi, e non legge né copia altre
private presenti nel vecchio registro.

La copia temporanea della chiave privata autore è necessaria finché il gruppo 3
non sostituisce nello stesso passaggio tutte le firme dirette. Il gruppo 4 dovrà
ritirare la vecchia capacità. Questa duplicazione transitoria deve essere
registrata come debito aperto e non può sopravvivere alla chiusura statica F4.

### 4.3 Schemi canonici

`set.json` deve contenere esattamente:

- `schema_version=1`;
- `state="complete"`;
- `provisioning_transaction_id` e `provisioner_build_id`;
- `author_active_key_id`;
- `author_verifier_key_ids`, ordinati;
- `admission_active_key_id`;
- `admission_verifier_key_ids`, ordinati;
- `producer_keys`, mappa ordinata dall'identificativo canonico della capacità a
  `store_name`, `active_key_id` e `verifier_key_ids`;
- `approval_authority_sha256`;
- `semantic_authority_sha256`;
- `semantic_public_key_ids`, ordinati;
- `approval_input_sha256`, `semantic_input_sha256`,
  `producer_catalog_sha256` e `context_source_inventory_sha256`;
- `prepared_admission_context_id`;
- `prepared_context_epoch`;
- `context_material_sha256`;
- `set_id`.

Non deve contenere percorsi assoluti, byte privati, `origin`, `author`, nomi di
workload scelti dall'operatore o valori dichiarati dal candidato. `set_id` è il
digest con dominio `metnos.executor-birth.authority-set/v1\0` del documento
senza il campo `set_id`.

`prepared-v1.json` deve contenere esattamente `schema_version=1`,
`state="prepared_not_active"`, `set_id`, il nome relativo immutabile
`authority-sets/<set_id senza prefisso>`, `author_store="author-root-v1"`,
`author_store_public_inventory_sha256`, `set_json_sha256`,
`context_material_sha256`, `provisioner_build_id` e `transaction_id`. Deve
essere creato soltanto dopo la rilettura produttiva di tutti i componenti. Non
abilita Birth, non attesta gli stati Phase 3, non autentica la distribuzione e
non sostituisce il futuro certificato F4.

`material-v1.json` contiene esattamente `schema_version=1`,
`state="prepared_not_active"`, una mappa `components` con gli undici nomi
chiusi, `prepared_admission_context_id` e `prepared_context_epoch`. Ogni
componente contiene `version`, `files`, `configuration` e `component_digest`.
`files` è una lista ordinata di record con soli `label`, `size` e `sha256`; non
contiene percorsi assoluti o byte del codice. `configuration` è il valore JSON
canonico già risolto, oppure `null`. Il digest del componente e i due
identificativi riusano senza varianti i domini, il framing e l'ordine definiti
da `executor_birth_context._component_digest()`,
`executor_birth_identity.admission_context_id()` e
`executor_birth_context._context_epoch()`. Il gruppo 2 deve aggiungere vettori
golden byte per byte per JSON, digest dei componenti, identificativo ed epoca.

Il documento immutabile `transaction-v1.json` contiene esattamente
`schema_version=1`, `transaction_id`,
`protocol="birth-authority-provisioning-v1"` e `provisioner_build_id`.
Ogni file immutabile di `checkpoints-v1` contiene esattamente:

- `schema_version=1`, `transaction_id`, `checkpoint_sequence` e
  `previous_checkpoint_sha256` (`null` soltanto per la sequenza zero);
- `state`, appartenente all'enum chiuso `created`, `author_staged`,
  `inputs_staged`, `authorities_staged`, `context_staged`, `verified`,
  `author_installed`, `set_installed`, `marker_installed`;
- `author_source_public_inventory_sha256`, `approval_input_sha256`,
  `semantic_input_sha256`, `producer_catalog_sha256` e
  `context_source_inventory_sha256`, valorizzati quando acquisiti;
- `author_store_public_inventory_sha256`, `set_id`, `set_json_sha256` e
  `context_material_sha256`, valorizzati quando prodotti;
- `payload_inventory`, lista ordinata di record `relative_path`, `object_type`,
  `confidentiality`, `size`, `sha256`, `platform_identity`;
- `checkpoint_sha256`, digest con dominio
  `metnos.executor-birth.provisioning-checkpoint/v1\0` del documento senza
  questo campo.

`transaction_id` è un nonce casuale di 128 bit codificato in esadecimale
minuscolo. `platform_identity` è un oggetto tipizzato: su POSIX contiene
`device` e `inode`; su Windows contiene seriale del volume e identificativo
file completo a 128 bit. Il journal non contiene byte privati, ACL completi o
percorsi personali.

`payload_inventory` descrive soltanto i payload destinati ai tre finali
(`author-root-v1`, insieme e marcatore). Esclude directory di transazione,
`transaction-v1.json` e tutti i checkpoint: includere il checkpoint corrente
nel proprio inventario renderebbe il digest autoreferenziale. Per un file,
`object_type="file"`, `size` è l'intero non negativo e `sha256` è il digest dei
byte; per una directory, `object_type="directory"`, `size=null` e `sha256=null`.
`confidentiality` appartiene a `confidential|integrity_only`. Su POSIX
`platform_identity` contiene esattamente `platform="posix"`, `device` e
`inode`, interi non negativi. Su Windows contiene esattamente
`platform="windows"`, `volume_serial` come 16 cifre esadecimali minuscole e
`file_id` come 32 cifre esadecimali minuscole. I percorsi sono relativi POSIX canonici, ordinati
per byte UTF-8, senza punto, genitore, backslash o componenti vuoti.

`checkpoint_sequence` è un intero JSON compreso fra 0 e 8191. Soltanto il nome
autorevole usa `f"{checkpoint_sequence:020d}.json"`; il checkpoint non nasce
direttamente con tale nome. Il predispositore crea in modo esclusivo un solo
`.checkpoint-pending-<sequenza a 20 cifre>-<transaction_id>`, scrive gestendo le scritture
parziali, sincronizza, rilegge dallo stesso handle e lo rinomina per handle e
senza sostituzione in `<sequenza>.json`. I checkpoint precedenti non vengono
riscritti o rimossi. Il recupero accetta soltanto una catena contigua da zero,
con digest predecessore esatto, stato monotono e un solo ultimo elemento. La
directory `checkpoints-v1` contiene tutti e soli i nomi della sequenza contigua;
la radice di transazione contiene soltanto header, checkpoint e payload ammessi
dal più recente `payload_inventory`. Un oggetto payload completo scritto e
sincronizzato immediatamente prima dell'arresto ma non ancora inventariato può
essere adottato soltanto se è l'unico oggetto successivo previsto, ha nome e
metadati esatti e supera la verifica crittografica o byte-per-byte prevista per
quel passo; altrimenti il recupero è ambiguo. Il massimo è 8.192 checkpoint per
transazione e lo stato non può retrocedere.

È ammesso al massimo un pending per la sequenza immediatamente successiva. Se è
completo, canonico e legato al digest precedente, il recupero lo promuove; se è
vuoto o parziale, lo apre e rimuove tramite la catena di handle soltanto dopo
avere verificato nome, proprietario/DACL, tipo, link count e identità stabile,
poi ricostruisce il checkpoint dallo stato osservato. Un pending con altra
sequenza, più pending o un file non riconosciuto rendono il recupero ambiguo.
Le prove interrompono il processo dopo creazione, scrittura parziale,
sincronizzazione, rilettura e rinomina del pending.

La stessa regola pending→rilettura→rinomina no-replace si applica all'header e a
ogni file payload della transazione: nessun file autorevole nasce direttamente
col proprio nome finale. L'header usa
`.transaction-v1.pending.<transaction_id>`; ogni payload usa nel proprio padre
`.payload-pending-<object_sequence a 20 cifre>-<transaction_id>`. Sotto il
blocco esclusivo esiste al massimo un pending di file nell'intera transazione.
Il passo successivo è determinato univocamente dal checkpoint precedente e dal
catalogo ordinato, quindi il recupero conosce nome finale, tipo, profilo e limite
senza leggere istruzioni dal pending.

Un pending completo viene riletto e promosso per handle. Unico pending vuoto o
parziale esattamente corrispondente al passo successivo può essere rimosso per
handle dopo i controlli del §7.6 e riscritto: una chiave casuale non ancora
promossa né inventariata può essere rigenerata, mentre una chiave già promossa o
inventariata non lo è mai. Un file col nome autorevole è quindi sempre completo;
se la rinomina è avvenuta prima del checkpoint, viene adottato con la regola del
payload successivo. Più pending, un pending inatteso o un file autorevole
parziale producono recupero ambiguo.
Il journal fornisce coerenza e recupero, non autenticazione:
prima dei gruppi 5-6 un processo che controlla la radice transitoria può
fabbricare journal, insieme e marcatore coerenti. Nessuno di questi artefatti è
quindi una decisione di autorizzazione o una prova di distribuzione.

I documenti JSON sono UTF-8 canonici, senza chiavi duplicate, con chiavi
ordinate, separatori `,` e `:`, senza ritorno a capo finale e con
`allow_nan=false`.

### 4.4 Framing, identificativi e limiti

Ogni identificativo della tabella usa testo ASCII minuscolo
`"sha256:" + sha256(payload).hexdigest()`. `CJ(x)` indica il JSON canonico del
§4.3; `EF(x)` indica `executor_birth_identity.encode_framed_v1(x)`. Nessun
agente può sostituire `CJ` con `EF` o concatenare liste senza framing.

| Campo | Payload SHA-256 esatto | Tipo/ordine | Limite prima dell'hash |
|---|---|---|---|
| `approval_input_sha256`, `approval_authority_sha256` | byte canonici esatti di `approval-authority.json` | file singolo | 64 KiB |
| `semantic_authority_sha256` | byte canonici esatti di `semantic-authority.json` | file singolo | 64 KiB |
| `semantic_input_sha256` | `b"metnos.executor-birth.semantic-input/v1\0" + EF({"schema_version":1,"authority_sha256":...,"public_keys":[...]})` | chiavi ordinate per `key_id`; record esatto `key_id,sha256` | 256 chiavi da 32 byte; documento 64 KiB |
| `producer_catalog_sha256` | `b"metnos.executor-birth.producer-catalog/v1\0" + EF({"schema_version":1,"capabilities":[...]})` | record esatto `producer_id,operation`, ordinati per UTF-8 della coppia | 1.024 capacità |
| `context_source_inventory_sha256` | `b"metnos.executor-birth.context-source-inventory/v1\0" + EF({"schema_version":1,"files":[...]})` | record esatto `component,label,size,sha256`, ordinati per componente ed etichetta UTF-8 | 4.096 file, 64 MiB totali |
| `author_store_public_inventory_sha256` | `b"metnos.executor-birth.author-public-inventory/v1\0" + EF({"schema_version":1,"active_key_id":...,"keys":[...]})` | record esatto `key_id,status,public_sha256`, ordinati per `key_id` | 256 chiavi |
| `context_material_sha256` | byte canonici esatti di `material-v1.json` | file singolo | 1 MiB |
| `set_json_sha256` | byte canonici esatti di `set.json` | file singolo | 1 MiB |
| `set_id` | `b"metnos.executor-birth.authority-set/v1\0" + EF(payload di set.json senza set_id)` | mappa tipizzata | 1 MiB codificato |
| `checkpoint_sha256` | `b"metnos.executor-birth.provisioning-checkpoint/v1\0" + CJ(checkpoint senza checkpoint_sha256)` | file singolo | 1 MiB |
| nome `p-<digest>` | `b"metnos.executor-birth.producer-capability-path/v1\0" + EF({"producer_id":...,"operation":...})` | mappa tipizzata | 256 byte per campo |
| `provisioner_build_id` | `b"metnos.executor-birth.provisioner-build/v1\0" + EF({"schema_version":1,"files":[...]})` | record esatto `label,sha256`, ordinati per etichetta UTF-8 | sette file, 16 MiB ciascuno |

Nei record della tabella, ogni `sha256` interno è a sua volta il testo ASCII
minuscolo del digest dei byte grezzi del file. I payload rifiutano campi
aggiuntivi, duplicati, valori Unicode non normalizzati in NFC nei campi testuali
e ordinamenti diversi da quello dichiarato.

La lista di `provisioner_build_id` contiene i seguenti file esatti:
`runtime/executor_birth_secure_fs.py`,
`install/birth_authority_provisioning.py`,
`runtime/executor_birth_keystore.py`,
`runtime/executor_birth_approval_authority.py`,
`runtime/executor_birth_semantic_authority.py`,
`runtime/executor_birth_context.py` e
`runtime/executor_birth_identity.py`. La lista è una costante chiusa; un agente
che rinomina un file deve aggiornare insieme specifica, costante e vettore.
Questo identificativo impedisce di riprendere per errore una transazione con un
protocollo diverso, ma non autentica la build.

I limiti V1, verificati prima dell'allocazione, sono:

- 64 KiB per configurazione dell'archivio chiavi, registro di approvazione e
  autorità semantica; chiavi Ed25519 pubbliche e private esattamente 32 byte;
- 1 MiB per un checkpoint, `set.json`, `prepared-v1.json` o una configurazione
  canonica di componente;
- 16 MiB per un singolo file di materiale del contesto e 64 MiB complessivi per
  tutti i byte letti dal catalogo;
- 4.096 voci nell'inventario chiuso, 1.024 capacità Producer, 4.096 file del
  contesto e 256 chiavi pubbliche per ciascun anello;
- 1.024 byte UTF-8 per un percorso relativo, 256 per etichetta, identificativo,
  versione, attore, ambito, produttore od operazione;
- interi JSON non negativi e non superiori a `2**63-1`; profondità JSON massima
  32 e massimo 65.536 elementi complessivi per documento.

Superare un limite produce un errore stabile prima di leggere o allocare il
payload eccedente. Il codice non usa la lunghezza dichiarata dal journal per
dimensionare senza prima confrontarla col limite.

## 5. Radice autore e pubblicatore privato

### 5.1 Ispezione prima della migrazione

Ogni esecuzione deve seguire questo ordine:

1. aprire e bloccare `provisioning-v1.lock`;
2. censire prima di tutto eventuale transazione, `author-root-v1`, insieme
   finale e marcatore, senza consultare sorgenti precedenti o ingressi operatore;
3. se esiste una sola transazione riconoscibile, convalidare journal e
   inventario e riprenderla secondo il checkpoint; una transazione già
   `verified` deve poter completare anche se gli ingressi sono scomparsi;
4. se `author-root-v1` esiste, convalidarlo senza importare `sign.py` e senza
   richiedere la presenza delle chiavi precedenti;
5. se l'archivio esiste ed è valido, usare esclusivamente tale archivio;
6. se l'archivio è assente e nessuna transazione contiene già la copia valida,
   permettere l'importazione soltanto dall'entrata interna di prima
   predisposizione; il gruppo 2 non interpreta gli stati Phase 3;
7. leggere in modo sicuro la privata `author` e l'anello pubblico precedente;
8. dimostrare che la pubblica derivata dalla privata coincide con la voce
   pubblica predefinita e che ogni identificativo pubblico è univoco;
9. predisporre la copia nella transazione, rileggerla con il caricatore
   produttivo e installarla con rinomina atomica senza sostituzione;
10. dopo l'installazione, riaprire l'archivio dal nome finale e confrontare
    identificativi e byte pubblici con transazione, `set.json` e marcatore.

Un archivio finale esistente ma non valido non deve essere riparato, sovrascritto
o ricreato. Deve produrre `birth_author_keystore_existing_invalid`.

### 5.2 Contenuto dell'archivio autore

L'archivio contiene una sola chiave privata attiva: quella predefinita
`author`. Contiene tutte le chiavi pubbliche fidate necessarie a rileggere la
storia, classificate come attiva o verificatore. Non importa altre private e non
genera una nuova identità autore.

Admission, ogni Producer, approvazione e revisione semantica devono avere chiavi
pubbliche diverse da tutte le chiavi autore e tra loro. Il controllo confronta i
32 byte grezzi, non soltanto nomi o identificativi.

### 5.3 Collegamento privato al commit

Nella nuova interfaccia Birth, `private_key` e `BirthCommitAuthorization` non
devono essere argomenti forniti dal nucleo o da un chiamante. Spostare la
privata ma lasciare selezionabili
`issuer`, `verifier`, `context_epoch_resolver` o la funzione di archivio
permetterebbe lo stesso scambio di autorità con un nome diverso.

La funzione oggi esportata da `contract_store` resta temporaneamente
raggiungibile dai chiamanti precedenti, perché il gruppo 2 non li migra. Il
nuovo pubblicatore può invocarla internamente con argomenti sigillati; nessun
nuovo chiamante può riceverla. Il gruppo 3 migra il bootstrap e il gruppo 4
rimuove o nega staticamente la vecchia autorità. Di conseguenza il gruppo 2 non
può dichiarare chiusa la raggiungibilità globale della primitiva.

La modifica deve introdurre il seguente contratto:

1. `BirthCommitFactsV1` è un valore immutabile e privo di callback. Contiene
   soltanto riferimento del manifest, predecessore autenticato, copia privata
   dello snapshot, `birth_request_id`, fatti dell'ammissione già prodotti dal
   nucleo, epoca osservata e identificatori necessari al commit;
2. `_BirthCommitPublisher` viene costruito dal modulo privato con chiave autore
   autenticata, anello pubblico autore, chiave Admission, emittente e
   verificatore Admission esatti, risolutore interno dell'epoca e riferimento
   alla sola primitiva `contract_store` prevista;
3. il pubblicatore costruisce internamente `BirthCommitAuthorization` con
   closure sigillate di emissione, verifica e risoluzione dell'epoca, quindi
   invoca la sola primitiva posseduta. La primitiva determina generazione e
   journal di authoring sotto i propri blocchi e richiama l'emittente sigillato
   con quei fatti; il verificatore dello stesso pubblicatore rilegge la ricevuta
   prima del journal `prepared`, della sostituzione dell'albero, della
   generazione RM-0007 e del puntatore autorevole. La copia privata di staging e
   la ricevuta durevole possono precedere tali mutazioni, come previsto dal
   protocollo corrente;
4. l'interfaccia destinata al nucleo può passare soltanto
   `BirthCommitFactsV1`; non può passare
   funzioni, classi, moduli, percorsi, chiavi o un oggetto
   `BirthCommitAuthorization` già costruito;
5. il gruppo 2 prova questo collegamento contro la vera primitiva di archivio
   su una radice isolata, ma non lo installa ancora nel bundle globale. Il
   gruppo 3 lo collegherà al bootstrap e alla macchina di convergenza.

La chiave autore e la chiave Admission non devono apparire in
`publisher_options`, nel risultato del bootstrap, nella vista pubblica, nelle
fabbriche Producer, nei log o nelle eccezioni. Il chiamante non può scegliere
percorso dell'archivio, anello fidato, nome della chiave, emittente,
verificatore, risolutore di epoca o funzione di pubblicazione alternativa.

### 5.4 Stato privato e vista di verifica

L'oggetto costruito per la prova di integrazione deve essere separato in:

- uno stato privato di modulo contenente nucleo, chiavi Admission e Producer,
  pubblicatore e capacità inattive;
- una vista immutabile di verifica contenente soltanto versione, identificativi
  delle chiavi, chiavi pubbliche, `set_id`,
  `prepared_admission_context_id`, `prepared_context_epoch` e stato
  `prepared_not_active`.

Nel gruppo 2 `_runtime_bundle_snapshot()` non viene ancora sostituito né
installato globalmente. La nuova vista pubblica deve comunque essere provata con
un'ispezione transitiva degli oggetti raggiungibili: non deve condurre a
`_BirthCore`, moduli con funzioni di commit, closure di firma, chiavi private,
emittenti, verificatori privati/callback di commit o fabbriche. Sono ammesse le
chiavi pubbliche Ed25519 e una facciata pura di verifica che non raggiunga stato
privato. Il gruppo 3 userà questa vista quando installerà il
bundle. Le cuciture che ricevono un nucleo sintetico devono essere nominate come
prove, non importate dal prodotto e censibili dal gruppo 4.

## 6. Predisposizione delle altre autorità

### 6.1 Admission

Se l'archivio Admission finale è assente, il predispositore genera una nuova
coppia Ed25519 dentro la directory di transazione. La privata non deve mai
esistere in un percorso temporaneo generico. Se l'arresto lascia una transazione
valida, la ripresa riusa la stessa coppia e non ne genera un'altra.

L'archivio usa lo schema già validato da `load_birth_keystore()`. Prima della
pubblicazione finale il caricatore produttivo deve dimostrare coppia coerente,
inventario chiuso, una sola attiva, anello ordinato e separazione da tutte le
identità note.

### 6.2 Producer

Il catalogo delle capacità viene acquisito una sola volta dal simbolo sigillato
`_producer_capabilities_for_bootstrap()`. Il predispositore crea esattamente un
archivio per ogni coppia `(producer_id, operation)` e nessun archivio aggiuntivo.

Il gruppo 2 registra soltanto il legame capacità-archivio. Non registra origine
o paternità. Le fabbriche restano inattive fino a quando il gruppo 3 non fornirà
la tabella chiusa per `ContractId` e non potrà quindi costruire una
`ProducerReceipt` con fatti scelti dal file di configurazione.

Ogni archivio deve avere una chiave distinta da autore, Admission, altri
Producer, approvazione e revisione semantica. Un riuso, anche fra due operazioni
dello stesso produttore, produce `birth_authority_key_reused`.

### 6.3 Approvazione

Il prodotto importa soltanto `approval-authority.json`, che contiene chiavi
pubbliche, attori e ambiti. Il predispositore lo decodifica con il caricatore
produttivo, ne verifica la codifica canonica, copia i byte nella transazione e lo
rilegge dalla collocazione finale.

La chiave privata dell'approvatore rimane fuori dal runtime Birth. In assenza
del registro pubblico operatore, la predisposizione resta incompleta con
`birth_approval_authority_input_missing`; non è ammesso creare una chiave e
scartarla per far passare l'avvio.

L'integrazione di `approval_registry.py` e `channels/approval.py` con la firma
esatta della decisione resta un requisito successivo prima dell'uso produttivo
delle approvazioni. Il gruppo 2 prova soltanto che l'autorità pubblica installata
è completa e utilizzabile da `verify_decision()`.

### 6.4 Revisione semantica

`semantic-authority.json` descrive esclusivamente:

- la directory relativa fissa `evidence`;
- le chiavi pubbliche con identificativo e stato;
- le versioni ammesse per ogni tipo di evidenza;
- i proprietari ammessi per ogni tipo di evidenza.

Ogni file pubblico è di 32 byte ed è importato da `semantic-public`. Il
predispositore rifiuta chiavi sconosciute, duplicate, riutilizzate da un altro
ruolo o non referenziate. Crea la directory finale `evidence` vuota con permessi
restrittivi; non crea record di evidenza e non conserva la privata del revisore.

L'assenza dell'ingresso operatore produce
`birth_semantic_authority_input_missing`. Un'autorità senza chiavi, versioni o
proprietari per tutti i tipi previsti non può essere marcata completa.

## 7. Primitiva filesystem multipiattaforma

### 7.1 Regola generale

Tutti i moduli di predisposizione devono usare una sola primitiva a handle. È
vietato aggiungere un altro insieme di controlli basato su `Path.exists()`,
`Path.resolve()`, `Path.read_bytes()` o controllo seguito da riapertura.

La politica di alto livello rimane in `install/birth_authority_provisioning.py`.
Le operazioni non autorevoli di basso livello possono vivere in un modulo
dedicato e condiviso, ma le funzioni di scrittura non devono essere esportate
dal runtime produttivo come capacità di predisposizione.

Il modulo comune introduce una `SecureRootSession` privata. La sessione riceve
un handle di radice già aperto e autenticato dal descrittore installatore,
mantiene quell'handle, tutti gli handle degli antenati e il blocco globale fino
alla fine, e accetta soltanto tuple di componenti relativi già convalidati. Le
operazioni minime sono `open_directory`, `read_file(maximum)`,
`create_file_exclusive(profile)`, `create_directory_exclusive(profile)`,
`inventory`, `rename_no_replace` e `dispose_transaction_object`; nessuna
restituisce un percorso da riaprire.

I nuovi caricatori interni ricevono `SecureDirectoryHandle` o
`SecureRootSession + nome relativo chiuso`, non `Path`. Gli adattatori pubblici
esistenti che ricevono `Path` restano temporaneamente per compatibilità dei
chiamanti non migrati: aprono una sessione una sola volta e delegano senza
riaperture per nome. Devono essere censiti come superficie precedente e rimossi
o negati dal gruppo 4. Il percorso installato del gruppo 2 usa esclusivamente
le entrate a sessione.

### 7.2 POSIX

La realizzazione POSIX deve:

- aprire la radice fissa e attraversare i discendenti mediante descrittori di
  directory e `openat`, con `O_NOFOLLOW`, `O_CLOEXEC` e `O_DIRECTORY` quando
  appropriato;
- usare `O_CREAT|O_EXCL` per ogni nuovo file;
- rifiutare oggetti non regolari, collegamenti con `st_nlink != 1`, proprietario
  diverso dall'identità del servizio e modalità diverse da `0700` per directory
  o `0600` per file riservati;
- scrivere tutti i byte gestendo scritture parziali, rileggere dallo stesso
  descrittore, sincronizzare file e directory e confrontare identità e
  dimensione prima e dopo;
- usare lo stesso `flock` sul byte/file di predisposizione: `LOCK_EX` con
  scadenza per il predispositore e `LOCK_SH` con scadenza per ogni caricatore;
- non seguire link durante inventario, recupero o pulizia.

La pubblicazione POSIX usa `renameat2()` con `RENAME_NOREPLACE`, nomi relativi e
i descrittori già aperti della directory di transazione e della radice finale.
Prima verifica stesso `st_dev`; `EEXIST` è conflitto, mentre `EXDEV`, `ENOSYS` o
un filesystem che rifiuta `RENAME_NOREPLACE` producono
`birth_provisioning_atomic_install_unsupported`. Non è ammesso ripiegare su
`os.rename()`, perché può sostituire una destinazione, né su una sequenza
controllo-rename. Dopo la rinomina sincronizza la directory radice, riapre il
nome finale con `openat` e confronta identità e inventario.

### 7.3 Windows

La realizzazione Windows usa `CreateFileW` soltanto per aprire il volume, la
radice di condivisione UNC o la radice di unità già risolta dall'installatore.
Questa sola apertura assoluta usa `FILE_FLAG_OPEN_REPARSE_POINT` e, per una
directory, `FILE_FLAG_BACKUP_SEMANTICS`; non usa `FILE_FLAG_WRITE_THROUGH` per
simulare una proprietà della radice.
Ogni componente discendente viene aperto o creato dalla funzione privata
`_win_open_relative_v1`, che chiama `NtCreateFile` da `ntdll` con
`OBJECT_ATTRIBUTES.RootDirectory` uguale all'handle padre e `ObjectName`
uguale a un solo componente UTF-16 già validato. Il componente non contiene
separatori, punto o genitore. `OBJECT_ATTRIBUTES.Attributes` contiene
`OBJ_CASE_INSENSITIVE`; `UNICODE_STRING.Length` e `MaximumLength` sono lunghezze
in byte e non includono un terminatore.

L'apertura usa `FILE_OPEN`; la creazione esclusiva usa `FILE_CREATE`. Le opzioni
contengono `FILE_OPEN_REPARSE_POINT|FILE_SYNCHRONOUS_IO_NONALERT` e, in modo
mutuamente esclusivo, `FILE_DIRECTORY_FILE` oppure
`FILE_NON_DIRECTORY_FILE`. Un risultato è successo soltanto se
`NT_SUCCESS(status)`; gli altri `NTSTATUS` sono convertiti una sola volta con
`RtlNtStatusToDosError` e poi nella tassonomia Birth. Non è ammessa una
riapertura assoluta di un discendente come ripiego.

Le costanti appartengono al dominio NT e hanno valori chiusi:
`FILE_OPEN=0x00000001`, `FILE_CREATE=0x00000002`,
`FILE_DIRECTORY_FILE=0x00000001`, `FILE_WRITE_THROUGH=0x00000002`,
`FILE_SYNCHRONOUS_IO_NONALERT=0x00000020`,
`FILE_NON_DIRECTORY_FILE=0x00000040`,
`FILE_OPEN_REPARSE_POINT=0x00200000` e
`OBJ_CASE_INSENSITIVE=0x00000040`. La prova G10 rifiuta alias con le costanti
Win32 `OPEN_EXISTING`, `CREATE_NEW` o `FILE_FLAG_*`.

La conversione usa l'enumerazione privata
`_NtOpenPurposeV1=read_required|lock_reader|create_exclusive|mutating_open|disposition`
e la tabella chiusa seguente. `ERROR_FILE_EXISTS` e `ERROR_ALREADY_EXISTS`
valgono `birth_provisioning_transaction_conflict` soltanto per
`create_exclusive`. `ERROR_FILE_NOT_FOUND` e `ERROR_PATH_NOT_FOUND` valgono
`birth_provisioning_lock_unavailable` per `lock_reader`,
`birth_provisioning_recovery_ambiguous` per `disposition` e
`birth_provisioning_io_unavailable` negli altri casi. `ERROR_ACCESS_DENIED` e
`ERROR_PRIVILEGE_NOT_HELD` valgono `birth_provisioning_elevation_required` per
`create_exclusive`, `mutating_open` e `disposition`, e
`birth_provisioning_acl_unsafe` per `read_required` e `lock_reader`.
`ERROR_SHARING_VIOLATION` vale `birth_provisioning_lock_unavailable` per
`lock_reader` e `birth_provisioning_io_unavailable` negli altri casi.
`ERROR_INVALID_PARAMETER`, `ERROR_NOT_SUPPORTED` ed
`ERROR_CALL_NOT_IMPLEMENTED` valgono
`birth_provisioning_atomic_install_unsupported`. Ogni altra risposta vale
`birth_provisioning_io_unavailable`. La causa di sistema resta concatenata
soltanto internamente; il messaggio pubblico contiene il solo codice Birth.

Su x64 `UNICODE_STRING` ha dimensione 16 e offset `Length=0`,
`MaximumLength=2`, `Buffer=8`; `OBJECT_ATTRIBUTES` ha dimensione 48 e offset
`Length=0`, `RootDirectory=8`, `ObjectName=16`, `Attributes=24`,
`SecurityDescriptor=32`, `SecurityQualityOfService=40`; `IO_STATUS_BLOCK` ha
dimensione 16, unione `Status|Pointer` all'offset zero e `Information`
all'offset 8. `NtCreateFile` restituisce `NTSTATUS` a 32 bit con segno e riceve,
nell'ordine, puntatore a handle, `ACCESS_MASK`, puntatore a
`OBJECT_ATTRIBUTES`, puntatore a `IO_STATUS_BLOCK`, puntatore opzionale a
`LARGE_INTEGER`, attributi file, condivisione, disposizione di creazione,
opzioni di creazione, buffer EA opzionale e lunghezza EA. G10 confronta queste
definizioni ctypes con una sonda ABI indipendente compilata sul runner, non con
le costanti del prodotto.

I caricatori finali non concedono `FILE_SHARE_DELETE`. La pubblicazione è
diversa: la directory di transazione deve essere aperta con
`DELETE|SYNCHRONIZE|READ_CONTROL|WRITE_DAC|WRITE_OWNER|FILE_READ_ATTRIBUTES`,
con `FILE_SHARE_READ|FILE_SHARE_WRITE` e senza `FILE_SHARE_DELETE`, perché il
medesimo handle sarà rinominato mediante la propria autorizzazione `DELETE`.
L'inventario di directory richiede inoltre
`FILE_LIST_DIRECTORY|FILE_TRAVERSE`; un caricatore di file usa soltanto
`FILE_READ_DATA|FILE_READ_ATTRIBUTES|READ_CONTROL|SYNCHRONIZE`. Non è ammesso
controllare con un handle e rinominare per nome con `MoveFileExW`.

Per ogni componente della catena il codice mantiene aperto l'handle fino al
completamento dell'operazione e verifica:

- percorso finale ottenuto da `GetFinalPathNameByHandleW`;
- attributi e reparse tag tramite `FileAttributeTagInfo`;
- tipo, dimensione, numero di link e cancellazione pendente tramite
  `FileStandardInfo`;
- identità stabile tramite seriale del volume e `FileIdInfo` completo a 128
  bit; non tronca l'identificativo alle varianti storiche a 64 bit;
- assenza di sostituzione prima e dopo lettura o scrittura.

I percorsi restituiti da Windows sono normalizzati soltanto per il confronto,
con trattamento esplicito di prefissi `\\?\`, UNC, maiuscole/minuscole e nomi
lunghi. La normalizzazione non viene usata per riaprire l'oggetto.

La pubblicazione di `author-root-v1`, dell'insieme immutabile e del marcatore
usa un handle sorgente distinto e già convalidato per ciascuno dei tre oggetti.
Le due directory vengono aperte con `FILE_DIRECTORY_FILE`; il file marcatore
con `FILE_NON_DIRECTORY_FILE`. Tutti e tre usano le opzioni native
`FILE_WRITE_THROUGH|FILE_OPEN_REPARSE_POINT`, accesso `DELETE` e nessuna
condivisione di cancellazione:

1. tutti i file discendenti sono chiusi dopo sincronizzazione e rilettura;
2. restano aperti l'handle sorgente dell'oggetto da pubblicare e l'handle della
   radice finale;
3. `SetFileInformationByHandle(FileRenameInfo)` riceve una
   `FILE_RENAME_INFO` con `RootDirectory` uguale all'handle della radice,
   `ReplaceIfExists=FALSE` e il solo nome relativo finale UTF-16;
4. radice e destinazione devono appartenere allo stesso volume;
5. destinazione esistente, comparsa concorrente o errore di rinomina lasciano
   invariati sia l'oggetto già finale sia la transazione; dopo il fallimento il
   codice rilegge entrambi per handle e classifica il conflitto.

Per `SetFileInformationByHandle(FileRenameInfo)`, `ERROR_FILE_EXISTS` o
`ERROR_ALREADY_EXISTS` producono `birth_provisioning_transaction_conflict`.
`ERROR_ACCESS_DENIED` o `ERROR_SHARING_VIOLATION` producono lo stesso conflitto
soltanto se la riconciliazione indipendente trova la destinazione; se questa è
assente, `ERROR_ACCESS_DENIED` produce `birth_provisioning_elevation_required`
e `ERROR_SHARING_VIOLATION` produce `birth_provisioning_io_unavailable`.
`ERROR_NOT_SUPPORTED`, `ERROR_NOT_SAME_DEVICE` e `ERROR_INVALID_PARAMETER`
producono `birth_provisioning_atomic_install_unsupported`.
`ERROR_FILE_NOT_FOUND` o `ERROR_PATH_NOT_FOUND` producono
`birth_provisioning_recovery_ambiguous`; ogni altro errore produce
`birth_provisioning_io_unavailable`. Nessun ramo decide il codice prima di
riconciliare sorgente e destinazione attraverso gli handle parent.

Non si sostituisce mai una directory esistente e non si usa
`MOVEFILE_REPLACE_EXISTING`. Dopo la rinomina il caricatore riapre dal nome
finale e riconfronta identità, inventario e byte con il journal.

Prima di creare qualunque oggetto il predispositore verifica tramite le informazioni
del volume che il filesystem sia NTFS e dichiari `FILE_PERSISTENT_ACLS`. Su un
altro filesystem o su una condivisione UNC che non dimostri entrambe le
proprietà restituisce `birth_provisioning_atomic_install_unsupported`. La
matrice prova NTFS; la prova UNC è positiva soltanto quando il server espone le
stesse garanzie, altrimenti verifica il rifiuto chiuso.

Il proprietario e la lista di controllo degli accessi devono essere applicati
con `SetSecurityInfo` sullo stesso handle, non mediante un secondo accesso per
nome. File e directory discendenti nascono mediante `_win_open_relative_v1`
con il descrittore restrittivo passato in
`OBJECT_ATTRIBUTES.SecurityDescriptor`; l'handle restituito resta quello usato
per applicazione, verifica, scrittura e rilettura. Non è ammesso usare
`CreateDirectoryW`, creare prima un oggetto ereditato, correggerlo dopo o
riaprirlo per percorso.

Prima della prima scrittura il codice applica proprietario e DACL con
`SetSecurityInfo`, tipo `SE_FILE_OBJECT` e mask composta da
`OWNER_SECURITY_INFORMATION`, `DACL_SECURITY_INFORMATION` e
`PROTECTED_DACL_SECURITY_INFORMATION`, controllando il valore `DWORD` restituito
dalla funzione, non `GetLastError`. Rilegge poi con
`GetSecurityInfo` sullo stesso handle e confronta la forma canonica seguente.

Il proprietario esatto di ogni oggetto è `SYSTEM`. La DACL è non nulla,
protetta e contiene soltanto ACE `ACCESS_ALLOWED` esplicite, con `AceFlags=0`,
in quest'ordine: `SYSTEM`, `Builtin Administrators`, SID esatto del servizio e,
solo per `integrity_only`, `Authenticated Users`. Sono rifiutate ACE negate,
ereditate, object-specific, callback, condizionali, SID estranei, duplicati e
mask più ampie o più strette. Le due classi chiuse sono:

- `confidential`: `SYSTEM` e `Builtin Administrators` hanno
  `FILE_ALL_ACCESS`. Sul file il servizio ha esattamente
  `FILE_READ_DATA|FILE_READ_EA|FILE_READ_ATTRIBUTES|READ_CONTROL|SYNCHRONIZE`;
  sulla directory ha esattamente `FILE_LIST_DIRECTORY`, `FILE_TRAVERSE`,
  `FILE_READ_EA`, `FILE_READ_ATTRIBUTES`, `READ_CONTROL` e `SYNCHRONIZE`. Non
  esiste ACE `Authenticated Users`. Questa classe
  copre directory contenenti private, private autore, Admission e Producer e
  file di configurazione che le rendono usabili;
- `integrity_only`: registri pubblici, `set.json`, materiale del contesto,
  journal senza segreti, blocco e marcatore. `SYSTEM` e
  `Builtin Administrators` hanno `FILE_ALL_ACCESS`; servizio e
  `Authenticated Users` hanno le stesse mask di sola lettura appena definite
  rispettivamente per file o directory. Nessun altro SID ha una ACE.

Proprietario e DACL sono verificati su ogni directory e file, inclusi blocco,
journal, JSON pubblici e private; non basta verificare la radice.

Prima di creare qualunque file riservato, il predispositore apre il token con
`OpenProcessToken`, legge gruppi e privilegi con `GetTokenInformation`, risolve
SID e LUID con `LookupAccountNameW`/`LookupPrivilegeValueW`, oppure usa
`ConvertStringSidToSidW` se il descrittore contiene già un SID. Azzera
`LastError` prima di `AdjustTokenPrivileges`, legge immediatamente il risultato
e `ERROR_NOT_ALL_ASSIGNED`, conserva `PreviousState` e lo ripristina in
`finally`. Abilita `SeRestorePrivilege` soltanto per assegnare `SYSTEM` come
proprietario. Il fallimento produce `birth_provisioning_elevation_required`
senza lasciare materiale segreto. L'identità del servizio è derivata dal
descrittore di installazione, non da un argomento libero.

I file sono aperti con l'opzione nativa `FILE_WRITE_THROUGH`, ricevono
`FlushFileBuffers()` dopo la scrittura e vengono riletti dallo stesso handle.
Windows non offre un equivalente generale di `fsync` della directory: il
documento non deve rivendicarlo. La garanzia V1 usa file completi sincronizzati,
checkpoint a sola aggiunta, rinomina per handle senza sostituzione e rilettura
finale; eventuali garanzie ulteriori dipendenti dal filesystem devono essere
misurate nella prova installata, non presunte.

### 7.4 File di blocco e arresto durante la creazione

Predispositore e caricatori contendono sullo stesso intervallo ma non hanno gli
stessi diritti. Soltanto il predispositore può creare con `FILE_CREATE` e
inizializzare il file sotto blocco esclusivo; se il nome esiste usa
`FILE_OPEN`. Un caricatore di sola lettura usa soltanto `FILE_OPEN`, non
crea, non scrive e fallisce con codice stabile se il file manca o è vuoto.

Su Windows il predispositore apre con `GENERIC_READ|GENERIC_WRITE`, il
caricatore con `GENERIC_READ`; entrambi concedono soltanto
`FILE_SHARE_READ|FILE_SHARE_WRITE`, senza `FILE_SHARE_DELETE`, e usano
`FILE_OPEN_REPARSE_POINT`. `LockFileEx` opera sull'intervallo di un byte
all'offset zero, descritto da `OVERLAPPED` azzerata: ogni tentativo usa
`LOCKFILE_FAIL_IMMEDIATELY`, il predispositore aggiunge
`LOCKFILE_EXCLUSIVE_LOCK`, il caricatore no. Su POSIX ogni tentativo usa
`flock(LOCK_EX|LOCK_NB)` o `flock(LOCK_SH|LOCK_NB)`.

Il ritentativo usa orologio monotono, attese deterministiche di 5, 10, 20, 40,
80 e poi 100 millisecondi fino alla scadenza; nessuna chiamata di lock può
bloccare oltre la scadenza. `UnlockFileEx` o `LOCK_UN` usa lo stesso
handle/intervallo in `finally`. Il blocco Windows oltre la fine del file è
valido; `msvcrt.locking()` non è il protocollo RM. Si ritenta soltanto
`ERROR_LOCK_VIOLATION` su Windows o `EACCES|EAGAIN` su POSIX; ogni altro errore
viene normalizzato e restituito immediatamente.

Dopo avere acquisito il blocco esclusivo, il predispositore rilegge identità e
dimensione. Se il file è vuoto e tutti gli altri controlli sono validi, scrive
il byte canonico `0`, sincronizza e prosegue. In questo modo un arresto tra
creazione e inizializzazione è recuperabile. Un file con contenuto diverso,
più link, reparse point o identità mutata è rifiutato.

### 7.5 Gerarchia dei blocchi

L'ordine globale, mai invertibile, è:

1. `provisioning-v1.lock`, condiviso per caricatori ed esclusivo per il
   predispositore;
2. i `birth-keystore.lock` dei singoli archivi, in ordine del percorso relativo
   canonico;
3. dopo avere rilasciato tutti i blocchi di predisposizione, i blocchi runtime
   `catalog_admission_lock`, authoring e writer nell'ordine già normativo.

Il predispositore può quindi mantenere il blocco globale esclusivo mentre
rilegge gli archivi con i loro blocchi condivisi. Il bootstrap del gruppo 3
manterrà il blocco globale condiviso mentre carica e congela tutte le autorità,
poi lo rilascerà prima di acquisire la barriera e i blocchi dei contratti.
Nessun percorso può acquisire `provisioning-v1.lock` o un blocco di archivio
mentre possiede già un blocco runtime. Timeout e annullamento rilasciano in
ordine inverso.

Per non rompere i chiamanti precedenti, `load_birth_keystore(root)` resta
autonomo e mantiene il solo `birth-keystore.lock` locale, implementato con la
nuova primitiva sicura. I percorsi storici provengono da `bootstrap.json` e non
hanno necessariamente una radice Birth o un blocco globale. Questa entrata è
compatibilità censita, non il caricatore del nuovo insieme.

Il modulo espone soltanto internamente
`_load_birth_keystore_in_session(directory, session)`, usato dal predispositore
già titolare del blocco globale; `session` è una capacità privata non costruibile
da percorsi o booleani e lega identità della radice, modalità del blocco e
handle effettivo. La variante acquisisce ancora il blocco locale. Approvazione,
autorità semantica e materiale del contesto seguono la stessa distinzione fra
entrata storica e entrata interna in sessione. È vietato rilasciare e
riacquisire il blocco globale fra controllo e apertura locale.

### 7.6 Pulizia sicura

La pulizia può rimuovere soltanto oggetti di transazione creati dall'esecuzione
corrente o una sola transazione precedente riconoscibile e coerente col journal.
Il journal non è descritto come autenticato. Ogni oggetto deve coincidere
per identità, tipo e inventario con la registrazione osservata. Non si usano
glob generici e non si rimuove mai una radice finale esistente.

Su Windows la rimozione procede dal basso verso l'alto con handle aperti con
le maschere esatte definite nel §16.13.2, sempre comprensive di `DELETE`, e
senza condivisione di cancellazione. Dopo una nuova verifica di
identità, tipo, link e proprietario/DACL, l'oggetto deve appartenere a uno di
due insiemi chiusi: `payload_inventory`, oppure metadati del protocollo
(`transaction-v1.json`, catena contigua dei checkpoint, unico pending ammesso e
directory contenitrici). La validazione è distinta per tipo:

- l'header richiede nome esatto e i soli quattro campi chiusi
  `schema_version`, `transaction_id`, `protocol`, `provisioner_build_id`, con
  nonce uguale al nome della transazione;
- ogni checkpoint richiede schema completo del §4.3, sequenza/nome, stesso
  `transaction_id`, digest proprio e predecessore esatto nella catena contigua;
- una directory richiede nome e ruolo attesi dal protocollo, handle parent
  concordante, identità, proprietario/DACL e tipo; prima della rimozione deve
  avere l'inventario di figli esatto e infine vuoto;
- un pending completo richiede lo schema del file finale atteso e viene promosso
  o eliminato soltanto secondo lo stato osservato.

L'unica eccezione è il solo pending vuoto o parziale del passo immediatamente
successivo: non se ne interpreta il contenuto e non gli si richiedono schema o
digest. Il nome esatto e il limite derivano dal checkpoint completo precedente
e dal catalogo chiuso; devono inoltre coincidere parent aperto della
transazione, proprietario/DACL, tipo regolare, link count uno, dimensione entro
il limite e `FileId`/identità stabile prima e dopo. Soltanto allora viene
cancellato sul medesimo handle. Qualunque secondo pending o divergenza è
ambigua. La rimozione usa esclusivamente
`SetFileInformationByHandle(FileDispositionInfoEx)` con i soli flag
`FILE_DISPOSITION_FLAG_DELETE|FILE_DISPOSITION_FLAG_POSIX_SEMANTICS` e nessuna
riapertura per nome. Un oggetto con `FILE_ATTRIBUTE_READONLY` viene rifiutato
prima della disposizione; la primitiva non modifica attributi per renderlo
cancellabile. Dopo la chiamata richiede
`FileStandardInfo.DeletePending=true` sul medesimo handle; dopo la chiusura
richiede l'assenza dall'inventario del padre. `ERROR_INVALID_PARAMETER` e
`ERROR_NOT_SUPPORTED` producono
`birth_provisioning_atomic_install_unsupported`, senza ripiego su
`FileDispositionInfo`. `ERROR_ACCESS_DENIED` e `ERROR_PRIVILEGE_NOT_HELD`
producono `birth_provisioning_elevation_required`; `ERROR_SHARING_VIOLATION` e
ogni altro errore producono `birth_provisioning_io_unavailable`. Una chiamata
riuscita che non porta `DeletePending` a vero produce anch'essa
`birth_provisioning_io_unavailable` e non viene riportata come rimozione. Su
POSIX usa `unlinkat` relativo al descrittore padre,
con `AT_REMOVEDIR` per directory vuote. `os.walk`, `shutil.rmtree` e glob sono
vietati nel percorso autorevole.

Un oggetto estraneo, una seconda transazione o un inventario ambiguo producono
`birth_provisioning_recovery_ambiguous` e richiedono intervento. La sicurezza ha
precedenza sulla pulizia automatica.

## 8. Transazione e recupero

### 8.1 Ordine vincolante

La predisposizione segue esattamente questo ordine:

1. controllo preliminare dei privilegi Windows, senza scritture;
2. apertura della radice fissa e acquisizione del blocco esclusivo;
3. censimento per handle, nell'ordine, di transazioni, marcatore,
   `author-root-v1` e insiemi finali; nessun ingresso esterno viene ancora
   aperto;
4. classificazione mediante la matrice del §8.2. Se esiste una transazione,
   verifica di protocollo, `provisioner_build_id`, checkpoint, inventario e
   identità di piattaforma prima di qualsiasi altra azione;
5. se la transazione è `verified` o successiva, completamento esclusivamente dai
   suoi byte, anche quando sorgente autore e ingressi operatore non esistono più;
6. soltanto per una prima transazione non completa, acquisizione sicura della
   sorgente autore precedente, degli ingressi pubblici operatore, del catalogo
   Producer e delle sorgenti del contesto; ogni digest viene reso durevole nel
   journal prima di avanzare;
7. creazione esclusiva della transazione, oppure ripresa dell'unica transazione
   riconosciuta; nessun segreto viene scritto prima di proprietario e ACL;
8. importazione dell'autore e generazione una sola volta delle chiavi Admission
   e Producer dentro la transazione;
9. copia canonica degli ingressi pubblici e costruzione del materiale del
   contesto inerte;
10. rilettura di ogni archivio con il caricatore produttivo e verifica globale
    di coppie, inventari, unicità e separazione delle chiavi;
11. scrittura e rilettura di `set.json`, del journal `verified` e del marcatore
    di transazione; chiusura dell'inventario del payload, dopo la quale nessun
    byte destinato ai tre finali può essere aggiunto o rigenerato. Possono
    essere aggiunti soltanto i checkpoint append-only successivi;
12. sincronizzazione dei file; su POSIX anche delle directory, su Windows
    applicazione della strategia durevole del §7.3;
13. rinomina senza sostituzione di `author-root-v1`; riapertura finale e
    confronto di chiavi pubbliche, inventario e digest col journal e con
    `set.json`;
14. rinomina senza sostituzione dell'insieme nel nome immutabile derivato da
    `set_id`; riapertura finale e confronto completo;
15. scrittura esclusiva, sincronizzazione, rinomina senza sostituzione e
    rilettura di `prepared-v1.json`;
16. avanzamento durevole del journal a `marker_installed`, rilettura con i
    caricatori produttivi di autore, insieme e marcatore, quindi rimozione della
    sola transazione concordante;
17. rilascio del blocco.

Non esiste sostituzione di una destinazione finale. La radice autore può essere
pubblicata prima dell'insieme perché conserva l'identità precedente e rimane
inerte; il journal deve sopravvivere fino al marcatore, così un arresto tra le
due rinomine è sempre classificabile.

### 8.2 Matrice di recupero

| Marcatore | Autore finale | Insieme finale | Transazione | Azione |
|---|---|---|---|---|
| assente | assente | assente | assente | Prima predisposizione: consultare gli ingressi e creare una transazione. |
| assente | assente | assente | directory vuota sicura col nonce valido, senza header | Creare `transaction-v1.json` e checkpoint zero; qualunque figlio presente rende il recupero ambiguo. |
| assente | assente | assente | unico header pending completo e coerente col nonce | Promuovere l'header per handle, quindi creare il checkpoint zero. |
| assente | assente | assente | unico header pending vuoto o parziale, sicuro e coerente col nonce | Rimuovere il pending per handle, riscriverlo e promuoverlo; qualunque altro figlio rende il recupero ambiguo. |
| assente | assente | assente | header valido senza checkpoint | Creare esclusivamente il checkpoint zero; header o file aggiuntivi rendono il recupero ambiguo. |
| assente | assente | assente | incompleta, riconoscibile e coerente | Riprendere i byte registrati. Consultare soltanto gli ingressi non ancora acquisiti; non rigenerare segreti già inventariati. |
| assente | assente | assente | `verified`; stage autore, insieme e marcatore tutti presenti | Installare dai soli byte della transazione; gli ingressi possono essere scomparsi. |
| assente | valido e concordante | assente | ultimo checkpoint `verified`; stage autore assente, stage insieme e marcatore presenti | Riaprire l'autore finale, confrontarne identità e byte con `payload_inventory`, aggiungere `author_installed` e continuare. |
| assente | valido e concordante | assente | ultimo checkpoint `author_installed`; stage autore assente, stage insieme e marcatore presenti | Riaprire l'autore, rinominare l'insieme e continuare. |
| assente | valido e concordante | valido e concordante | ultimo checkpoint `author_installed`; stage autore e insieme assenti, stage marcatore presente | Riaprire l'insieme finale, confrontarlo con `set_id` e inventario, aggiungere `set_installed` e continuare. |
| assente | valido e concordante | valido e concordante | ultimo checkpoint `set_installed`; soli stage autore e insieme assenti, stage marcatore presente | Riaprire entrambi, rinominare il marcatore e continuare. |
| assente | valido | assente | assente | Stato impossibile per arresto conforme: `birth_provisioning_recovery_ambiguous`, senza ricostruzione. |
| assente | assente | valido | assente | Stato impossibile per arresto conforme: `birth_provisioning_recovery_ambiguous`, senza ricostruzione. |
| assente | valido | valido | assente | Perdita o manomissione del journal: `birth_provisioning_recovery_ambiguous`; il marcatore non viene ricostruito. |
| valido e coerente | valido e concordante | valido e concordante | assente | Successo di sola ispezione; nessuna sorgente precedente o ingresso operatore viene aperto. |
| valido e coerente | valido e concordante | valido e concordante | ultimo checkpoint `set_installed`; tutti e tre gli stage assenti | Riaprire il marcatore finale e confrontarne identità e byte col journal; aggiungere `marker_installed`, quindi completare. |
| valido e coerente | valido e concordante | valido e concordante | checkpoint `marker_installed`; tutti e tre gli stage assenti | Verificare di nuovo i tre finali, quindi rimuovere soltanto quella transazione. |
| valido | assente, diverso o non valido | qualunque | qualunque | Conflitto irreparabile automatico; non cancellare il marcatore. |
| qualunque | non valido | qualunque | qualunque | `birth_author_keystore_existing_invalid`; non riparare o sovrascrivere. |
| qualunque | qualunque | non valido o non concordante | qualunque | `birth_authority_set_conflict`; non riparare o sovrascrivere. |
| qualunque | qualunque | qualunque | build, protocollo, digest d'ingresso o inventario non concordante | `birth_provisioning_transaction_conflict`; conservare gli oggetti per diagnosi. |
| qualunque | qualunque | qualunque | più transazioni o oggetti estranei | `birth_provisioning_recovery_ambiguous`; nessuna pulizia automatica. |

Il protocollo non deve mai rigenerare una chiave perché una rilettura è fallita.
Una differenza dopo l'installazione è un errore, non un invito a riprovare con
altri byte.

Ogni riga di successo riapre `author-root-v1` e confronta identificativo attivo,
insieme delle pubbliche e byte pubblici con `author_active_key_id`,
`author_verifier_key_ids` e `author_store_public_inventory_sha256` di
`set.json`. Riapre inoltre ingressi copiati, registri e materiale del contesto e
confronta i rispettivi digest. Un marcatore coerente non rende autorevole un
insieme incoerente.

`prepared-v1.json`, `transaction-v1.json` e la loro coerenza non corrispondono
agli stati `legacy`, `recovery_required`, `store_only` o `active`. Il gruppo 3
deve riaprire e riconvalidare autore, insieme, registri e materiale del contesto
sotto la propria barriera prima di qualunque attivazione; non può tradurre
`prepared_not_active` direttamente in `active`.

## 9. Materiale del contesto predisposto, non ancora applicato

### 9.1 Separazione fra predisposizione e attivazione

Il gruppo 2 aggiunge una fabbrica interna priva di parametri liberi, per esempio
`prepare_installed_admission_context(layout, authority_set)`. La fabbrica
risolve i file da una costante posseduta dal codice, apre ogni oggetto con la
primitiva sicura, serializza configurazioni già risolte e produce
`material-v1.json` secondo il §4.3. Non accetta digest, percorsi, versioni o
liste dal candidato, dal Producer o da `bootstrap.json`.

Il decodificatore corrente degli undici componenti in
`executor_birth_bootstrap._context_builder()` resta temporaneamente presente ma
non viene usato dalla prova del predispositore. Rimuoverlo nel gruppo 2
spezzerebbe il bootstrap precedente prima che esista quello sostitutivo. Il
gruppo 3 deve, nello stesso incremento atomico che installa il bootstrap
sigillato, usare la fabbrica interna, eliminare la selezione libera e
ricostruire il contesto sotto la barriera di convergenza.

I campi `prepared_admission_context_id` e `prepared_context_epoch` attestano i
byte predisposti; non attestano che i controlli li consumino. Non possono essere
inseriti in una `AdmissionReceipt` produttiva finché il gruppo 3 non ha
dimostrato l'applicazione del catalogo.

### 9.2 Catalogo V1 e stato di applicazione

Il catalogo V1 deve associare almeno il materiale seguente. Uno stesso file può
appartenere a più componenti quando modifica più comportamenti. Per ogni
componente la configurazione canonica include
`enforcement_state="productive"|"prepared_only"`; il gruppo 2 deve usare
`prepared_only` quando il codice corrente non applica realmente la politica.

| Componente | Materiale da predisporre | Stato verificato alla base `f1699122` |
|---|---|---|
| `standard` | `executor_standard.py`, `presentation_contract.py`, `code_file_paths.py`, `naming_grammar.py` e costanti risolte dello Standard. | `productive` per i controlli oggi invocati. |
| `linter` | `manifest_lint.py`, `manifest_rules.py` e l'identità del futuro adattatore Birth. | `prepared_only`: `_CHECK_CATALOG_V1` non invoca ancora il linter completo. |
| `vocabulary` | `policy.py`, `capabilities.py`, `vocab.py`, `CAPABILITY_REGISTRY`, `PROVIDER_SKILLS` e vocabolario consumato. | Misto; registrare `prepared_only` finché il gruppo 3 non prova il consumo completo. |
| `authority_registry` | Identificativi e byte pubblici di autore, Admission, Producer, approvazione e revisione semantica; ambiti e stati. Mai private. | `prepared_only`: l'insieme non è ancora installato nel bundle. |
| `sandbox_registry` | Politica risolta, interprete, `bwrap` o helper Windows, limiti e funzionalità. | `prepared_only`: Linux usa ancora risoluzioni ambientali non autenticate. |
| `property_catalog` | `executor_birth_properties.py`, `executor_birth_property_runner.py` e serializzazione completa e ordinata delle sette specifiche. | `productive` soltanto per le proprietà attraversate dal catalogo corrente. |
| `runner` | `executor_birth_runner.py`, `executor_birth_runner_windows_v1.py`, `bounded_subprocess.py`, helper e costanti di scadenza, memoria, processi e output. | `productive` per il runner corrente, con completezza da riesaminare nel gruppo 3. |
| `review_policy` | `executor_birth_semantic_review.py`, `executor_birth_semantic_authority.py`, `llm_workloads.py`, soglie, workload, versioni e proprietari. | `productive` per il controllo semantico corrente; il nuovo registro è ancora `prepared_only`. |
| `template_allowlist` | Identificativi e digest dei soli modelli interni del runner e della revisione semantica. | `prepared_only`: oggi l'identità non governa una risoluzione chiusa. |
| `primitive_allowlist` | Fixture, generatori, oracoli e operazioni interne richiamabili dalle proprietà. | `prepared_only`: oggi l'identità non governa la raggiungibilità. |
| `dependency_allowlist` | Regole degli import locali, libreria standard ammessa e dipendenze autenticate. | `prepared_only`: oggi non esiste enforcement completo. |

L'elenco preciso di file, simboli e configurazioni serializzati è una costante
chiusa e revisionata. Il gruppo 2 prova che un'aggiunta o una rimozione nella
costante cambia i digest attesi e che non è possibile sostituirla con
`configuration={"name": ...}`. Non pretende ancora una prova comportamentale
dei componenti `prepared_only`.

### 9.3 Obblighi del gruppo 3 conservati

Prima di usare un `admission_context_id` produttivo il gruppo 3 deve:

- invocare realmente il linter sul manifest congelato;
- applicare un controllo AST e di risoluzione degli import alla mappa chiusa dei
  file e agli involucri dei 21 executor;
- risolvere in modo chiuso modelli e primitive delle proprietà;
- sostituire su Linux `shutil.which("bwrap")` e `sys.executable` non autenticati
  con il registro predisposto e completare il legame Windows;
- installare i registri di autorità nel bundle privato e dimostrarne il consumo;
- cambiare ogni `enforcement_state` interessato a `productive`, ricostruire
  identificativo ed epoca e provare che una modifica di ciascuna politica cambia
  sia comportamento sia identità;
- analizzare import statici e caricamenti dinamici noti e fallire se un nuovo
  file locale eseguito non appartiene allo snapshot o a una dipendenza chiusa.

Questi punti non sono eliminati: sono assegnati al gruppo 3 perché richiedono
gli involucri, il bootstrap e la tabella chiusa che il §23.6 vieta di anticipare.

### 9.4 Stabilità e rilettura

Il predispositore congela i byte una volta, li descrive in `material-v1.json` e
li rilegge prima del marcatore. Il gruppo 3 non si fida del marcatore: riapre le
sorgenti installate, ricostruisce il materiale e confronta ogni digest sotto la
barriera. Un aggiornamento della distribuzione o di un registro produce un
nuovo materiale e una nuova epoca; non modifica in posto l'insieme immutabile.

### 9.5 Vettore golden minimo del contesto

Il vettore seguente è normativo e già riproducibile alla base `f1699122`. Per
tutti gli undici componenti usa `version="1"`, nessun file e la configurazione
canonica `{"enforcement_state":"prepared_only","fixture":"v1"}`. I digest
attesi sono:

| Componente | Digest |
|---|---|
| `standard` | `sha256:3848d69a2e2d89bc24d1e87304c16ad27fb158862fd7b79565dc849d6f36a952` |
| `linter` | `sha256:f58f0037431e9965eaf697617687ca66443fd59829575b22cd821575d47151e8` |
| `vocabulary` | `sha256:6aa47c6c49534271cb419cda28cb0ec06eb9aee2b8a99afbfdaba1fe35a1b980` |
| `authority_registry` | `sha256:87886c06ed696b9d52f4c327b9bf06177df1050a08e2c95ff50c5dd0ee2e0e3e` |
| `sandbox_registry` | `sha256:55ed0376bddf12d793c9262c902d8cf324fbcc978de62b1d692ea25e53b40f7c` |
| `property_catalog` | `sha256:417a5714e9c61749baf3e7527b3fe26404b7ae57ca8d57ea26165018738f06b3` |
| `runner` | `sha256:d68adf14a533e57809f0759effb7f9ac4f23747b80e070c48afb91d9a3ea48ad` |
| `review_policy` | `sha256:eee40cfa270d09e3ff0eee90ddd02783a6972a7cb820af90d5f322fd9abe5ca2` |
| `template_allowlist` | `sha256:7fccbc7ae834cdb499b2457314bc96ded3e21535fedbd07273fe1d2de4a57e87` |
| `primitive_allowlist` | `sha256:b2db20dfbdf40669c399f093974f6ad729bc2070683134eb8de187e73e57b324` |
| `dependency_allowlist` | `sha256:0cf3749d74b739baa81bac198b9917cea915e4ad47e6c016df64a9527df7444a` |

L'identificativo atteso è
`sha256:4bf49733b5fe2295b90df04cc906bc7ffece72a79cd956f5a3dd3aa0f5c04710`;
l'epoca attesa è
`sha256:9eac9b907a5d5a1f799c6eb09751eadcd5bc6a5de0959013001d56f4dc29d86c`.
Il vettore prova il framing; non trasforma la fixture in materiale produttivo.
Vettori ulteriori devono fissare `set_id`, checkpoint e marcatore quando i loro
codec vengono introdotti nell'incremento corrispondente.

## 10. Modifiche richieste per file

La codifica deve essere suddivisa in registrazioni Git autonome. I nomi sono
indicativi; se un agente usa nomi diversi deve mantenere le stesse proprietà.

Gli incrementi 2B-2E esercitano transazioni soltanto su radici isolate di prova
e le rimuovono al termine. Non lasciano una transazione reale da riprendere con
il commit successivo, perché `provisioner_build_id` cambia insieme ai moduli.
Il primo stato persistente di un'installazione reale viene creato soltanto dalla
build completa dell'incremento 2F. Non si introduce una compatibilità implicita
fra protocolli di commit diversi.

### 10.1 Incremento 2A — accesso sicuro

- Introdurre il modulo comune di accesso a handle.
- Autenticare la radice già aperta e adottare un descrittore consumabile una
  volta, mantenendo identità e profilo vincolati alla sessione.
- Validare volume e funzionalità del filesystem prima di ogni creazione.
- Su Windows, costruire, applicare e verificare il descrittore di sicurezza
  esatto e abilitare e ripristinare il privilegio minimo necessario.
- Produrre un inventario relativo a handle con identità, tipo e metadati di
  collegamento sufficienti a riconoscere oggetti estranei.
- Sostituire nel caricatore dell'archivio chiavi le aperture vulnerabili con la
  nuova primitiva.
- Riutilizzare la stessa lettura nel caricatore semantico e nel caricatore del
  registro di approvazione.
- Introdurre il blocco condiviso/esclusivo comune e la rinomina Windows per
  handle, senza ancora predisporre dati.
- Introdurre la disposizione sicura e riconciliata di un oggetto di transazione
  isolato, senza journal o checkpoint.
- Normalizzare ogni errore di sistema in codici Birth stabili, senza percorsi o
  dettagli ACL nel messaggio pubblico.
- Non aggiungere ancora generazione di chiavi.

Queste capacità restano operazioni di basso livello. Non scelgono radici,
identità, profili, dati, politica transazionale o stato del predispositore.

### 10.2 Incremento 2B — archivio autore

- Aggiungere il predispositore con ispezione prima della migrazione.
- Aggiungere `transaction-v1.json`, checkpoint durevoli e inventario chiuso.
- Copiare soltanto la privata predefinita e l'anello pubblico.
- Implementare sincronizzazione e matrice di recupero della radice autore.
- Provare il riavvio senza sorgente precedente.
- Non collegare ancora Phase 3 al percorso Birth.

### 10.3 Incremento 2C — insieme delle autorità

- Generare Admission e tutti gli archivi Producer nella transazione.
- Importare e convalidare approvazione e revisione semantica pubbliche.
- Applicare il controllo globale di separazione delle chiavi.
- Rendere durevoli i checkpoint fino a `authorities_staged`; non scrivere ancora
  `set.json`, perché identità e digest dipendono dal materiale del contesto.

### 10.4 Incremento 2D — materiale del contesto inerte

- Introdurre il catalogo V1 posseduto dal codice.
- Registrare esplicitamente `productive` o `prepared_only` per ogni componente.
- Costruire e rileggere `material-v1.json`, identificativo predisposto ed epoca
  senza collegarli a una ricevuta produttiva.
- Scrivere e rileggere `set.json`, calcolare `set_id` e chiudere l'inventario
  soltanto dopo che `material-v1.json` è durevole.
- Conservare il decodificatore precedente fino alla sostituzione atomica del
  gruppo 3; aggiungere prove che il nuovo predispositore non lo usa.
- Produrre vettori golden completi e il rapporto delle politiche non ancora
  applicate.

### 10.5 Incremento 2E — collegamento privato di integrazione

- Legare private autore e Admission, anelli, emittente, verificatore, epoca e
  primitiva esatta al pubblicatore interno.
- Far accettare al nucleo soltanto `BirthCommitFactsV1`, senza callback.
- Separare stato privato e vista pubblica di verifica.
- Attraversare la vera primitiva di archivio su radice isolata e autenticare il
  risultato con i caricatori reali.
- Dimostrare che le facciate pubbliche non restituiscono chiavi, nucleo,
  fabbriche o funzioni di firma.
- Non installare il pubblicatore nel bundle globale e non attivare Producer.

### 10.6 Incremento 2F — marcatore e prova installata del predispositore

- Scrivere `prepared-v1.json` soltanto dopo la rilettura completa.
- Esporre una sola entrata interna di prima predisposizione e una entrata di
  ispezione, entrambe a disposizione fissa e senza parametri crittografici.
- Aggiungere in `install/phases/phase3_code.py::run()` un preflight produttivo
  di sola lettura degli ingressi pubblici prima di `_install_executor_contracts()`.
  Dopo il preflight e ancora prima della macchina, invocare
  `prepare_or_defer_until_legacy_author_exists()`: l'entrata ispeziona e riprende
  sempre transazione o finali esistenti; se la sorgente autore precedente
  esiste, completa la predisposizione; soltanto nello stato completamente vuoto
  e senza autore restituisce `author_not_yet_created` senza creare alcun
  oggetto. Non riceve né interpreta la modalità Phase 3.
- Subito dopo `_install_executor_contracts()`, che su una nuova installazione
  crea la chiave autore precedente, invocare obbligatoriamente
  `ensure_executor_birth_authorities_prepared()`. L'adattatore non riceve
  percorsi, chiavi o modalità, non entra nelle quattro branche e non ne
  interpreta gli stati; risolve disposizione e ingressi fissi
  dell'installatore e chiama il predispositore idempotente.
- Questa doppia chiamata è necessaria per il recupero: se fresh install attiva
  lo store e cade prima dell'`ensure`, al riavvio l'entrata inspect-first trova
  la chiave o la transazione e converge prima che il ramo `active` corrente
  tenti il bootstrap ancora incompleto. Il gruppo 2 prova la convergenza della
  predisposizione ma non dichiara riuscito l'intero ramo `active`, che resta
  consegna del gruppo 3.
- Invocare dalla copia installata lo stesso adattatore reale nella prova
  isolata. Non è ammesso un modulo richiamato soltanto dai test.
- Non attivare il runtime Birth e non migrare chiamanti.
- Eseguire la prova installata limitata al predispositore e la matrice pubblica
  Linux/Windows. Il rapporto deve chiamarla
  `installed_provisioner_proof_v1`, non certificazione del bootstrap.

Ogni incremento parte soltanto dopo il verde dell'incremento precedente. Un
fallimento deve riportare alla diagnosi del contratto interessato; non autorizza
modifiche opportunistiche a più livelli.

## 11. Codici di errore stabili

Almeno i seguenti codici devono essere distinti:

| Codice | Significato |
|---|---|
| `birth_provisioning_elevation_required` | Il token Windows non può applicare proprietario e ACL richiesti. |
| `birth_provisioning_lock_unsafe` | Il file di blocco non è un oggetto regolare, univoco e stabile. |
| `birth_provisioning_lock_unavailable` | Il blocco condiviso o esclusivo non è stato acquisito entro la scadenza. |
| `birth_provisioning_acl_unsafe` | Proprietario, DACL, ereditarietà o diritti effettivi non corrispondono alla classe dell'oggetto. |
| `birth_provisioning_io_unavailable` | Apertura, scrittura completa, sincronizzazione, rinomina per handle o rilettura non possono garantire il protocollo. |
| `birth_provisioning_atomic_install_unsupported` | Filesystem, volume o API non offrono rinomina no-replace, ACL persistenti e durabilità richieste. |
| `birth_provisioning_recovery_ambiguous` | Esistono più transazioni, oggetti estranei o identità non riconciliabili. |
| `birth_provisioning_transaction_conflict` | Protocollo, build, checkpoint, digest d'ingresso o inventario della transazione non concordano. |
| `birth_author_keystore_unavailable` | La radice autore finale è assente durante una sola ispezione. |
| `birth_author_keystore_existing_invalid` | La radice autore finale esiste ma non supera il caricatore produttivo. |
| `birth_author_identity_incomplete` | Manca privata o pubblica predefinita durante la prima migrazione. |
| `birth_author_identity_mismatch` | Privata e pubblica predefinite non formano una coppia. |
| `birth_authority_key_reused` | Due ruoli crittografici condividono gli stessi byte pubblici. |
| `birth_approval_authority_input_missing` | Manca il registro pubblico operatore. |
| `birth_approval_authority_invalid` | Il registro pubblico operatore non è canonico o completo. |
| `birth_semantic_authority_input_missing` | Manca l'autorità pubblica semantica. |
| `birth_semantic_authority_invalid` | Chiavi, versioni, proprietari o percorsi semantici non sono validi. |
| `birth_context_catalog_incomplete` | Un componente effettivo o una dipendenza locale non è assegnato. |
| `birth_context_material_changed` | Un file o una configurazione è mutato durante l'acquisizione. |
| `birth_authority_set_conflict` | La destinazione o il marcatore non corrisponde alla transazione. |

Le eccezioni pubbliche non devono includere byte di chiavi, descrittori di
sicurezza, SID non necessari, percorsi personali o contenuto dei registri.

## 12. Piano di prova obbligatorio

### 12.1 Prove comuni Linux e Windows

Ogni piattaforma deve provare:

1. installazione da stato assente con tutte le identità distinte;
2. seconda esecuzione di sola ispezione con byte e `set_id` invariati;
3. seconda esecuzione senza la sorgente autore precedente;
4. completamento da transazione `verified` dopo avere rimosso tutti gli ingressi
   esterni;
5. archivio autore esistente ma corrotto, senza tentativo di riparazione;
6. autore valido ma non concordante con `set.json` o marcatore;
7. chiave riutilizzata fra ogni coppia di ruoli;
8. registro approvazione mancante, non canonico, duplicato o con ambito vuoto;
9. autorità semantica mancante, con chiave non referenziata, proprietario vuoto
   o directory di evidenze collegata;
10. file, directory, blocco, chiave pubblica e privata sostituiti con link;
11. hard link verso un file vittima, dimostrando che i byte vittima restano
   invariati;
12. due processi concorrenti, con una sola transazione e un solo insieme finale;
13. arresto reale del processo dopo creazione del pending, scrittura parziale,
    scrittura completa, sincronizzazione, rilettura, rinomina e checkpoint di
    header, ogni classe di payload e journal del §8.1, con successiva convergenza
    allo stesso `set_id` e agli stessi byte pubblici;
14. arresto dopo la creazione vuota del blocco e recupero senza intervento;
15. comparsa concorrente della destinazione finale o del marcatore subito prima
    della rinomina: `ReplaceIfExists=FALSE`/no-replace deve lasciare entrambi gli
    oggetti invariati e produrre conflitto;
16. transazione con `provisioner_build_id`, protocollo, digest d'ingresso,
    inventario o `set_id` differente, sempre rifiutata;
17. insieme finale valido con transazione residua concordante, ripulita; stessa
    situazione con transazione non concordante, conservata e rifiutata;
18. modifica di ciascun file e configurazione del catalogo e variazione dei soli
    componenti attesi, senza rivendicare enforcement comportamentale;
19. rifiuto di materiale dimostrativo `configuration={"name": ...}` e vettori
    golden identici sulle due piattaforme;
20. assenza transitiva di private, moduli di commit, emittenti, verificatori
    privati/callback e funzioni autorevoli nella vista pubblica; chiavi pubbliche
    e facciate pure di verifica restano ammesse;
21. pubblicazione attraverso il collegamento privato e la vera primitiva su una
    radice isolata, con esito verificato dal caricatore;
22. tentativi di sostituire, uno alla volta, radice di archivio, anello autore,
    chiave autore, chiave Admission, emittente, verificatore, risolutore di
    epoca, primitiva di commit o `BirthCommitAuthorization`, tutti impossibili
    dall'API del nucleo o rifiutati prima della mutazione;
23. in un nuovo processo privo di ancora esterna, sostituzione coerente
    dell'intero insieme e dei documenti digest-only da parte del proprietario
    della radice: la prova deve dimostrare e registrare che il gruppo 2 non può
    rilevarla. La prova positiva con l'ancora reale appartiene ai gruppi 5-6 e
    non può essere simulata nel verde del gruppo 2.
24. compatibilità di `load_birth_keystore(Path)` con un archivio precedente
    esterno a qualunque radice Birth e privo di `provisioning-v1.lock`, usando
    soltanto il blocco locale e le nuove aperture sicure.

### 12.2 Prove Windows specifiche

Windows deve inoltre provare:

- junction su ogni antenato, inclusa una sostituzione sincronizzata fra apertura
  e uso;
- reparse point finale e intermedio;
- sostituzione del nome mentre l'handle è aperto, che deve fallire o restare
  legata allo stesso oggetto;
- numero di link maggiore di uno letto con `FileStandardInfo`;
- proprietario e ACL di ogni directory e di ogni file, non soltanto delle
  directory principali, con distinzione `confidential`/`integrity_only`;
- esecuzione non elevata con errore stabile e nessun file segreto creato;
- errore di `SetSecurityInfo` normalizzato e nessuna destinazione marcata
  completa;
- file di blocco vuoto conteso da due processi con `LockFileEx`;
- byte da `0x00` a `0xff` scritti e riletti senza trasformazione di testo;
- arresti mediante `TerminateProcess`, non eccezioni lanciate nello stesso
  interprete;
- caricamento reale con token dell'identità di servizio temporanea; tale token
  deve poter leggere ma non modificare, cancellare o cambiare DACL in entrambe
  le classi;
- con un secondo utente ordinario reale, lettura negata sugli oggetti
  `confidential`, lettura riuscita sugli `integrity_only` e modifica,
  cancellazione o `WRITE_DAC` negate su entrambe. Una simulazione con
  `AccessCheck` non sostituisce queste prove;
- rinomina di directory tramite `SetFileInformationByHandle` con handle radice,
  destinazione assente e stesso volume, più rifiuto della destinazione esistente;
- nomi lunghi, prefisso `\\?\`, condivisione UNC loopback obbligatoria e
  differenze di maiuscole/minuscole senza riapertura per nome normalizzato;
- creazione di file e directory con DACL già restrittiva, seguita dal confronto
  canonico di owner, ordine ACE, mask e flag sul medesimo handle;
- rifiuto di filesystem non NTFS o privo di `FILE_PERSISTENT_ACLS` prima della
  creazione di qualunque oggetto;
- dimensione e offset ABI di `FILE_RENAME_INFO`, `FILE_ID_INFO`,
  `FILE_DISPOSITION_INFO_EX`, `OVERLAPPED`, `UNICODE_STRING`,
  `OBJECT_ATTRIBUTES` e `IO_STATUS_BLOCK`, più le firme ctypes di
  `NtCreateFile` e `RtlNtStatusToDosError`, su Windows Server 2022 x64;
- seriale volume `8000000000000001` conservato come 16 cifre esadecimali senza
  segno o rifiuto spurio.

Le prove ACL che richiedono elevazione devono essere eseguite in un'attività
dedicata della matrice pubblica. Se GitHub non espone un token adatto, il gruppo
2 resta non certificato su quel requisito: non è ammesso sostituirlo con una
simulazione. La prova del servizio Windows definitivo e della distribuzione
firmata resta comunque al gruppo 6.

### 12.3 Prove POSIX specifiche

POSIX deve inoltre provare:

- sostituzione di un componente fra due `openat`;
- proprietario errato e modalità `0755`, `0644` o più permissive sui segreti;
- directory su filesystem diverso per la transazione finale;
- scrittura parziale e errore di sincronizzazione;
- concorrenza fra blocco condiviso del caricatore ed esclusivo del
  predispositore;
- inventario modificato mentre il descrittore della directory è aperto.

Gli arresti POSIX devono usare un processo figlio terminato con `SIGKILL` dopo
un segnale di checkpoint durevole; una eccezione controllata nello stesso
processo prova soltanto il ramo di errore, non la recuperabilità da arresto.

### 12.4 Prova installata limitata al predispositore

`installed_provisioner_proof_v1` deve partire da una copia installata e da una
radice vuota creata dall'entrata interna del predispositore, non da un helper che
scrive direttamente gli archivi. Deve:

1. predisporre ingressi pubblici operatore distinti;
2. invocare `prepare_or_defer_until_legacy_author_exists()` senza sorgente
   autore e verificare `author_not_yet_created` con radice invariata;
3. creare la sorgente autore mediante la stessa funzione legacy usata
   dall'installatore isolato, quindi invocare
   `ensure_executor_birth_authorities_prepared()` caricato dalla copia
   installata;
4. arrestare il processo;
5. eliminare o rendere indisponibile la sorgente autore precedente in una copia
   isolata dell'installazione;
6. rieseguire lo stesso adattatore dalla copia installata, senza avviare la
   macchina Phase 3;
7. caricare ogni archivio mediante i caricatori produttivi;
8. ricostruire il materiale del contesto dal catalogo installato;
9. verificare `set_id`, identificativo predisposto, epoca, stato
   `prepared_not_active` e inventario autore;
10. provocare l'arresto reale del processo dopo il ritorno di
    `_install_executor_contracts()` e prima dell'`ensure`, quindi dimostrare che
    al riavvio l'entrata inspect-first converge prima di richiamare la macchina;
11. dimostrare che runtime, Phase 3 e fabbriche Producer non sono attivi, che
    nessun chiamante è stato migrato e che il decodificatore precedente non è
    stato rimosso.

La prova deve produrre un rapporto canonico con versione della piattaforma,
identificativo della registrazione Git, passaggi attraversati e ciò che non è
ancora provato. Non deve essere chiamata prova installata di Phase 3, avvio
Birth, riattestazione, pubblicazione produttiva, archivio a freddo o
certificazione F4: tali prove appartengono ai gruppi successivi.

La prova separata pubblicatore → vera primitiva su archivio isolato è una prova
di integrazione interna del gruppo 2 e non va inclusa né rinominata come prova
installata.

## 13. Criterio di uscita

Il gruppo 2 può essere dichiarato completato soltanto quando la seguente tabella
è allegata alla registrazione Git conclusiva e ogni cella richiesta per il
gruppo 2 è verde. Una cella rinviata deve contenere `N/A`, il gruppo esatto che
la possiede e il motivo normativo; non può essere lasciata vuota o colorata
verde.

| Requisito | Simbolo produttivo | Prova di modulo | Prova di integrazione | Prova installata | Esito | Git |
|---|---|---|---|---|---|---|
| Radice autore autenticata | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |
| Pubblicatore con consegna privata | da compilare | da compilare | da compilare | N/A — attivazione gruppo 3 | da compilare | da compilare |
| Admission distinta | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |
| Tutti i Producer distinti | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |
| Approvazione pubblica installata | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |
| Autorità semantica installata | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |
| Materiale del contesto canonico e inerte | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |
| Recupero da ogni arresto | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |
| Sicurezza Linux | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |
| Sicurezza Windows reale nel confine del predispositore | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |
| Vista pubblica priva di autorità | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |
| Runtime ancora non attivo | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |
| Nessuna interpretazione o modifica delle quattro branche Phase 3 | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |
| Limiti dello stesso UID/SID e dell'autenticazione dichiarati | da compilare | da compilare | da compilare | da compilare | da compilare | da compilare |

L'esito deve elencare separatamente i requisiti non provati. Il verde di test che
non attraversano il simbolo produttivo non può riempire la colonna della prova
installata.

Il criterio di uscita del gruppo 2 non include: applicazione comportamentale del
contesto, bootstrap iniziale, quattro stati Phase 3, avvio Birth,
riattestazione, pubblicazione produttiva, caricamento a freddo del solo archivio,
protezione dal medesimo UID/SID, distribuzione autenticata o servizio Windows
definitivo. Omettere questi limiti dal rapporto è un fallimento del gruppo, non
una semplificazione editoriale.

## 14. Decisione sul codice locale precedente

| Gruppo di modifiche locale | Decisione | Motivazione |
|---|---|---|
| Caricamento della chiave autore fissa e confronto con la pubblica | Conservare il concetto e riscrivere sulla nuova primitiva. | Il requisito è corretto, ma l'ordine deve essere ispezione prima della sorgente precedente. |
| Closure del pubblicatore e vista di sola verifica | Conservare il concetto e riesaminare ogni oggetto raggiungibile. | Soddisfa la consegna privata, purché il nucleo e le altre private non tornino nella vista. |
| Recupero dell'authoring nel bootstrap | Rinviare al gruppo 3. | Appartiene al bootstrap iniziale e alla macchina di convergenza. |
| Riattestazione produttiva | Rinviare ai gruppi 3 e 5. | Richiede tabella chiusa e coordinatore. |
| Modifiche all'inventario della guardia | Rinviare al gruppo 4. | Il gruppo 2 non rende ancora vincolante la guardia. |
| Modifiche estese all'archivio chiavi Windows | Riscrivere. | La realizzazione corrente duplica controlli e non mantiene una catena di handle. |
| Predispositore della sola radice autore | Usare come inventario di casi, non come sorgente da copiare. | Contiene idee valide, ma non realizza l'insieme completo né chiude proprietario, blocco e junction. |
| Modifiche di Phase 3 | Non copiare. | Mescolano predisposizione, migrazione, recupero e pubblicazione prima che il gruppo 2 sia completo. |

## 15. Regola per la ripresa della codifica

La prima modifica di prodotto è autorizzata soltanto dopo una revisione di questo
documento contro il codice e RM-0008. Durante la codifica, ogni nuovo errore deve
essere classificato rispetto a un'invariante di questa specifica. Al secondo
fallimento consecutivo della stessa classe, l'implementazione si arresta e torna
alla diagnosi; non si aggiunge una terza variante finché una prova deterministica
non ha identificato la causa e delimitato la correzione.

Il documento non chiude RM-0008. Definisce il prossimo incremento controllabile
e impedisce che un altro esito verde parziale venga scambiato per completamento
del prodotto.

## 16. Arresto diagnostico dell'incremento 2A del 25 agosto 2026

### 16.1 Stato verificato e limite dell'evidenza

Il codice esplorativo dell'incremento 2A resta locale e privo di registrazione
Git. È basato sulla registrazione `83345c18`, che contiene soltanto questa
specifica. La raccolta mirata riproducibile del 25 agosto 2026 contiene 75
`node-id`: su Linux supera 63 prove e ne salta dodici perché specifiche per
Windows. L'elenco nell'ordine di raccolta pytest ha impronta SHA-256
`73c821eda82ee6908a3b20c8c06076bf2edc0d96d0a548d9bd4cfe83b204826c`; i
quattro moduli e i sei file di prova hanno impronta aggregata
`8069ebdd5f6bb6c7b110277c00a6a302d4d241c6d7a605dc50c8f8083c8c113b` con
Python 3.12.3. Il comando completo è registrato nel pacchetto di revisione
`rm0008-incremento-2a-pacchetto-review-adversarial.md`. Il dato resta una
fotografia del prototipo locale, non un criterio futuro basato sul conteggio e
non una certificazione dell'incremento.

La matrice pubblica esistente esegue `tests/portable`, ma non raccoglie le prove
causali collocate in `tests/runtime/contracts`. Non contiene neppure l'attività
Windows dedicata alle ACL reali prescritta dal §12.2. L'esecuzione pubblica
`32887571156`, verde sulla sola registrazione documentale `83345c18`, non prova
il codice locale. Nessun esito verde corrente può quindi riempire le celle di
sicurezza, concorrenza o compatibilità del §13.

Il presente §16 è un piano diagnostico. Non autorizza da solo modifiche a test,
matrice o prodotto. La ripresa richiede l'approvazione esplicita di questa
sezione da parte dei tre revisori diagnostici; soltanto dopo tale approvazione
si applica l'ordine del §16.8.

### 16.2 Separazione obbligatoria fra diagnosi e accettazione

Per ogni criterio devono esistere due artefatti distinti e nominati con lo stesso
identificativo:

- **D — riproduttore diagnostico.** Registra in modo deterministico la causa nel
  prototipo. Può essere un inventario statico, una sonda dell'ordine delle
  chiamate, una iniezione di errore o una prova dinamica. Un D può terminare con
  successo proprio perché ha osservato il difetto; non entra mai fra i controlli
  obbligatori di certificazione e non può produrre un verde di conformità.
- **A — prova di accettazione.** Afferma l'invariante desiderata attraverso il
  simbolo produttivo. Per una causa di prodotto già dimostrata deve essere
  rossa sul prototipo vulnerabile e verde dopo la correzione. Per un requisito
  di copertura non ancora provato può risultare verde alla prima esecuzione: in
  quel caso chiude la lacuna probatoria senza autorizzare una modifica inutile
  al prodotto. Soltanto gli A entrano come controlli obbligatori nella matrice.

Un arresto reale mediante `SIGKILL` o `TerminateProcess` è una prova di
integrazione e recuperabilità. Non sostituisce il D deterministico: un singolo
arresto può non manifestare una finestra di durabilità pur se il ramo difettoso
è presente. Analogamente, l'assenza di un simbolo è dimostrata staticamente e
non richiede un arresto artificiale.

Prima della modifica della matrice deve esistere un manifesto canonico con, per
ogni A, identificativo del criterio, `node-id` di pytest, piattaforma proprietaria,
simbolo produttivo attraversato e divieto di salto o successo inatteso. La
raccolta viene certificata sugli identificativi, non sul numero totale dei test.
Il nome di ogni prova, inclusa una prova preesistente, deve descrivere soltanto
la garanzia realmente esercitata; una prova nello stesso processo non può essere
nominata come certificazione multiprocesso.

### 16.3 Criteri rossi: causa già individuata

#### R1 — Proprietà strutturale della capacità di scrittura

La causa è l'uso di un token Python raggiungibile come se fosse un sigillo. Il
costruttore del descrittore, le funzioni di apertura della radice e la funzione
di adozione sono importabili; il descrittore può inoltre essere modificato prima
dell'adozione.

- **D-R1:** inventario statico di ogni costruzione, adozione e operazione
  mutante; costruzione diretta del token e sostituzione di handle, radice o
  identità prima dell'adozione.
- **A-R1:** la sola entrata supportata appartiene al modulo installatore;
  radice e UID o SID sono risolti internamente; il descrittore è consumabile una
  volta e i suoi valori non sono modificabili dopo la costruzione; nessuna
  fabbrica o capacità mutante è transitivamente raggiungibile dalle facciate
  pubbliche o dal runtime produttivo. La guardia controlla staticamente il grafo
  di importazione e chiamata della distribuzione, non lo stack dinamico Python.

Questa chiusura non promette inforgiabilità contro codice arbitrario eseguito
nello stesso interprete e con lo stesso UID o SID. La sicurezza ulteriore deriva
dai diritti del sistema operativo, dall'inventario chiuso della distribuzione e
dalla guardia statica. Il limite deve restare dichiarato nel rapporto finale.

#### R2 — Blocco globale esclusivo prima di ogni mutazione

La causa è che lo stato autorevole abilita creazione e rinomina senza conservare
la modalità condivisa o esclusiva del blocco globale.

- **D-R2:** per ogni operazione mutante attualmente presente, invocazione senza
  blocco e sotto blocco globale condiviso, con inventario prima e dopo per
  mostrare che l'I/O viene raggiunto.
- **A-R2:** nessuna mutazione è tentata senza blocco globale esclusivo; il
  rifiuto precede apertura, creazione, rinomina o disposizione. Il blocco
  condiviso resta sufficiente per i caricatori interni di sola lettura. Quando
  viene introdotta, anche la disposizione appartiene a questa prova.

#### R3 — Disposizione autenticata degli oggetti di transazione

La causa è l'assenza di `dispose_transaction_object`. Il solo helper Windows di
ripristino non è una capacità legata alla sessione e sopprime alcuni errori.

- **D-R3:** verifica statica dell'assenza dell'operazione e inventario dei rami
  di ripristino che chiamano direttamente helper di piattaforma.
- **A-R3:** l'operazione è relativa a handle, richiede blocco globale esclusivo
  e identità attesa, non segue link e rifiuta hard link, oggetti estranei e
  directory non vuote. Su POSIX sincronizza il descrittore padre dopo
  `unlinkat`; su Windows usa la disposizione sul medesimo handle e riconcilia
  mediante rilettura. Restituisce un esito chiuso e l'identità osservata, ma non
  conosce journal o checkpoint: la loro registrazione appartiene al chiamante
  del 2B. Non rivendica un `fsync` generale delle directory Windows.

#### R4 — Recupero durevole del file di blocco POSIX vuoto

La causa è che la directory viene sincronizzata solo quando il processo
corrente ha creato il file. Il recupero di un file vuoto lasciato da un arresto
sincronizza il file, ma non necessariamente la sua voce di directory.

- **D-R4:** sonda deterministica dell'ordine scrittura completa → `fsync` del
  file → `fsync` della directory sul ramo di recupero di un file vuoto già
  esistente.
- **A-R4:** ogni transizione da file vuoto a byte canonico sincronizza file e
  directory nello stesso ordine. Una prova separata termina il primo processo
  dopo la creazione vuota e dimostra la convergenza del secondo.

#### R5 — Profili Windows autenticati e verificati sul medesimo handle

La radice viene già controllata per tipo, reparse point e percorso finale, ma
non rispetto al proprietario e alla DACL richiesti. I profili degli oggetti
creati non sono registrati in un catalogo autenticato e vengono poi inferiti
dagli argomenti di lettura. Il percorso storico non può essere esentato mediante
un generico stato `authoritative=False`: deve avere un profilo storico chiuso.

- **D-R5:** su un processo amministrativo, aprire con successo una radice NTFS
  e alterare separatamente proprietario, protezione della DACL, ordine e tipo
  delle ACE, SID o maschera. Registrare che il prototipo raggiunge l'uso senza
  il confronto richiesto oppure applica un profilo diverso da quello di
  creazione.
- **A-R5:** un oracolo di test indipendente usa `GetSecurityInfo`, `GetAce` ed
  `EqualSid`, senza chiamare il generatore SDDL o il verificatore produttivo.
  L'oracolo conferma anzitutto la singola alterazione; quindi l'entrata
  produttiva, eseguita con diritti amministrativi sufficienti ad aprire
  l'handle, deve rifiutarla nel confronto proprietario/DACL. Il catalogo dei profili è
  chiuso e posseduto dal codice o dal proprietario installatore, non è un valore libero
  reso soltanto immutabile.
- **A-R5-accesso:** con un token reale dell'identità di servizio e con un secondo
  utente ordinario, verificare lettura e dinieghi effettivi distinti per
  `confidential` e `integrity_only`. Un processo non elevato deve restituire il
  codice stabile e non lasciare segreti. L'indisponibilità dei token non diventa
  salto o verde: l'attività obbligatoria resta non certificata.

#### R6 — Rinomina Windows dopo ispezione e riconciliazione

Esistono due cause distinte. Una directory già ispezionata è conservata con un
handle privo del diritto di eliminazione e la rinomina riusa quell'handle. Una
sorgente non presente nella cache può invece essere rinominata senza verifica
completa del profilo.

- **D-R6-memorizzata:** una seconda sessione apre e inventaria una transazione
  esistente, poi tenta la rinomina; il prototipo deve esporre il fallimento di
  accesso causato dall'handle memorizzato.
- **D-R6-nuova:** una nuova sessione incontra una sorgente non memorizzata con
  DACL alterata e registra che il ramo di rinomina raggiunge l'I/O senza il
  confronto del profilo.
- **A-R6:** la rinomina usa un handle sorgente distinto con i diritti minimi
  necessari, valida volume, FileID a 128 bit, tipo e profilo, conserva identità
  e inventario della directory finale e riconcilia sorgente e destinazione dopo
  ogni errore. Destinazione già presente o creata alla barriera immediatamente
  prima della chiamata produce conflitto, lasciando invariate entrambe le parti.

#### R7 — Inventario comune completo e stabile

Il record comune non distingue un collegamento simbolico da un file regolare e
non contiene il tag di reparse. Su POSIX un collegamento simbolico entra con
`directory=False`; su Windows la doppia enumerazione conserva nome, FileID e
tipo di directory, ma forza il numero di link a uno e perde il reparse tag.

- **D-R7-POSIX:** inventariare la directory padre che contiene un collegamento
  simbolico e mostrare che il record prodotto non lo distingue da un file
  regolare.
- **D-R7-Windows:** inventariare la directory padre che contiene un hard link o
  una junction e mutare un ingresso alla barriera fra le due enumerazioni.
- **A-R7:** il record condiviso distingue almeno file regolare, directory,
  collegamento simbolico e reparse point e conserva gli attributi specifici di
  piattaforma necessari. Ogni ingresso è riaperto dal descrittore padre e porta
  identità completa, tipo, tag reparse quando applicabile e numero reale di
  link. Link non ammessi, identità discordante o mutazione fra enumerazioni
  falliscono chiusi su entrambe le piattaforme.

#### R8 — Tre caricatori interni vincolati alla stessa radice

Soltanto l'archivio chiavi possiede un'entrata interna parziale. Approvazioni e
autorità semantica usano ancora la facciata storica basata su percorso. Manca la
contesa del blocco globale per tutti e tre e quella del blocco locale per il solo
archivio chiavi.

- **D-R8:** inventario statico delle entrate interne mancanti e prova che il
  test corrente dell'archivio chiavi controlla la presenza logica del globale,
  ma non una contesa locale fra processi.
- **A-R8-globale:** per archivio chiavi, approvazioni e autorità semantica, il
  processo A conserva il globale esclusivo. Il processo B adotta una sessione
  distinta sulla stessa radice autenticata e sullo stesso identificativo di
  oggetto, entra mediante l'API interna e non completa l'acquisizione globale
  condivisa prima del rilascio di A.
- **A-R8-locale:** soltanto per l'archivio chiavi, A e B adottano sessioni
  distinte sulla stessa radice e conservano ciascuno il proprio globale
  condiviso. A detiene il blocco locale esclusivo e B, mediante il caricatore
  interno, non completa il blocco locale condiviso prima del rilascio. Per
  approvazioni e autorità semantica non viene inventato un blocco locale non
  previsto dalla specifica. L'ordine resta globale → locali ordinati.

### 16.4 Criteri di copertura bloccanti non ancora provati

Questi criteri non corrispondono ancora a una regressione del prodotto. Le loro
prove A possono quindi risultare verdi alla prima esecuzione; in tal caso si
registra l'evidenza e non si modifica il prodotto.

#### C1 — Blocco Windows realmente multiprocesso

Il test corrente crea e legge il blocco in sequenza; passerebbe anche senza una
contesa reale.

- **D-C1:** registrare che la prova corrente non contiene sovrapposizione né una
  barriera fra processi.
- **A-C1-contesa:** processi avviati con modalità `spawn` provano
  condiviso/condiviso, condiviso/esclusivo ed esclusivo/condiviso, con barriera
  e scadenza misurata.
- **A-C1-recupero:** una barriera strumentata nel simbolo produttivo segnala il
  punto successivo a `FILE_CREATE`, applicazione ACL e `LockFileEx`, ma precedente
  alla prima `WriteFile`. Soltanto a quel punto il processo viene terminato con
  `TerminateProcess`; un nuovo scrittore recupera il file vuoto e produce
  esattamente il byte canonico. Un lettore non crea mai il file.

#### C2 — Sostituzione Windows dopo il vincolo dell'handle

Gli handle dei discendenti sono ora conservati fino alla chiusura della
sessione, ma manca una prova Windows della proprietà.

- **D-C2:** registrare il solo percorso nominale corrente e l'assenza di un
  avversario sincronizzato; non trattarlo come prova di sicurezza.
- **A-C2:** il processo A vincola radice, directory intermedia e file finale. Il
  processo B tenta rinomina o sostituzione dopo ciascun segnale, usa una
  junction soltanto per radice e intermedi di tipo directory e usa un reparse
  point o collegamento simbolico per il file finale. L'operazione di B è negata
  oppure A resta sullo stesso volume e FileID o fallisce chiuso; A non legge mai
  i byte sostitutivi.

#### C3 — Sostituzione POSIX fra aperture relative

La prova corrente sostituisce oggetti nello stesso processo dopo che sono stati
memorizzati, ma non sincronizza un avversario fra due `openat` consecutivi.

- **D-C3:** inventariare le prove correnti e registrare l'assenza della barriera
  fra apertura della radice, apertura di un intermedio e uso del componente
  successivo.
- **A-C3:** il processo A apre la radice o il primo intermedio e segnala una
  barriera; il processo B sostituisce il componente successivo. A deve restare
  sul medesimo `st_dev`/`st_ino` o fallire chiuso, senza leggere, inventariare o
  modificare l'oggetto sostitutivo. La prova si ripete per radice, ogni
  intermedio e oggetto finale.

#### C4 — Blocco POSIX realmente multiprocesso

La prova corrente usa più descrittori nello stesso processo e non dimostra la
contesa richiesta fra caricatore e predispositore.

- **D-C4:** registrare che la prova corrente non attraversa processi distinti e
  non misura una scadenza monotona.
- **A-C4:** processi distinti provano condiviso/condiviso,
  condiviso/esclusivo ed esclusivo/condiviso con barriera, scadenza misurata e
  rilascio dopo la terminazione del detentore. Il caricatore conserva il blocco
  condiviso mentre il predispositore attende quello esclusivo; nessuna
  mutazione avviene prima dell'acquisizione esclusiva.

Le barriere C1-C4 appartengono al banco di prova: sono realizzate mediante
orchestrazione dei processi o intercettazione confinata delle chiamate di
sistema. Nessun callback, percorso, variabile d'ambiente, punto di arresto o
politica selezionabile dal chiamante entra nella capacità distribuita.

### 16.5 Criteri gialli ancora obbligatori

I criteri seguenti non hanno tutti una regressione dimostrata, ma restano requisiti
normativi dell'incremento 2A e devono diventare verdi prima della registrazione
del codice.

| ID | Ambito | Evidenza ancora necessaria |
|---|---|---|
| G1 | Inventario e snapshot comuni | Aggiunta, rimozione, rinomina e sostituzione dello stesso nome, incluso un file non JSON, mentre la directory è aperta; limite massimo dell'inventario; confronto completo prima e dopo tutte le letture. |
| G2 | Compatibilità storica | `load_birth_keystore(Path)` su archivio esterno alla radice Birth usa soltanto il blocco locale e non crea né cerca `provisioning-v1.lock`; approvazioni e semantica pubbliche POSIX accettano materiale sicuro posseduto da UID diverso; gli entrypoint reali precedenti passano su Linux e Windows senza mutazioni. |
| G3 | Durabilità POSIX di creazione e rinomina | Scrittura corta ed `EINTR`; stato dopo errore di sincronizzazione; rinomina fra parent distinti con sincronizzazione di entrambi; `EXDEV`, `ENOSYS` o assenza di `renameat2` producono `atomic_install_unsupported` senza ripiego. Arresti reali restano prove di integrazione separate. |
| G4 | Ciclo di vita ed errori stabili | Errori di chiusura, sblocco e adozione non espongono `OSError`, non mascherano l'errore primario e chiudono ogni handle una sola volta. I messaggi pubblici non contengono percorso, SID, DACL o diagnostica di sistema. |
| G5 | Ripristino del privilegio Windows | Iniezione deterministica del fallimento del secondo `AdjustTokenPrivileges`, incluse risposta falsa e `ERROR_NOT_ALL_ASSIGNED`, con un solo `CloseHandle`; confronto reale di `TokenPrivileges` prima e dopo un corpo che solleva. |
| G6 | Raccolta pubblica tracciabile | Manifesto dei `node-id`, attività separate per portabilità, concorrenza e ACL, zero salti o successi inattesi nelle celle possedute dalla piattaforma, risultato associato alla registrazione Git esatta. |
| G7 | Volume Windows | Rifiuto di volume non NTFS o privo di `FILE_PERSISTENT_ACLS` prima di ogni creazione, con inventario invariato e oracolo del volume indipendente. |
| G8 | Proprietario POSIX autorevole | Radice, directory e file autorevoli con UID diverso da quello autenticato sono rifiutati prima dell'uso; la prova resta distinta da G2, dove il profilo pubblico storico può ammettere un proprietario differente purché non modificabile dal servizio. |
| G9 | Percorsi Windows | Percorsi lunghi, prefisso `\\?\`, UNC e differenze di maiuscole/minuscole seguono la matrice positiva o il rifiuto chiuso del §12.2; l'assenza di un server UNC certificabile non diventa uno skip verde. |
| G10 | ABI e identità volume Windows | Dimensioni e offset di `FILE_RENAME_INFO`, `FILE_ID_INFO`, `FILE_DISPOSITION_INFO_EX`, `OVERLAPPED`, `UNICODE_STRING`, `OBJECT_ATTRIBUTES` e `IO_STATUS_BLOCK`, nonché la firma ctypes di `NtCreateFile`, `RtlNtStatusToDosError`, apertura realmente relativa e tabella degli stati, sono verificati su Windows Server 2022 x64; il seriale `8000000000000001` resta unsigned e viene reso con sedici cifre esadecimali. |
| G11 | Byte binari Windows | Ogni byte da `0x00` a `0xff` viene scritto e riletto senza trasformazione di testo attraverso la primitiva produttiva. |
| G12 | Fallimento sicurezza Windows | Un errore reale o iniettato di `SetSecurityInfo` produce il codice Birth stabile e nessuna destinazione marcata completa; inventario e residui vengono riconciliati. |

### 16.6 Matrice minima di tracciabilità delle prove 2A

La tabella seguente non sostituisce i dettagli dei §§12.1-12.3. Impedisce che un
requisito essenziale venga perso durante la codifica.

| Criterio | Piattaforma o attività | Simbolo produttivo | Barriera o errore causale | Oracolo distinto | Prima della correzione | Dopo la correzione |
|---|---|---|---|---|---|---|
| R1 | guardia statica | entrata installatore e capacità di scrittura | inventario transitivo | analizzatore AST | A rosso | A verde |
| R2 | Linux e Windows | tutte le mutazioni | nessun globale / globale condiviso | inventario prima/dopo | A rosso | A verde |
| R3 | Linux e Windows | `dispose_transaction_object` | identità, hard link, oggetto estraneo | inventario da handle | simbolo assente | A verde |
| R4 | Linux | lock comune | file vuoto già esistente | sonda ordine `fsync` | A rosso | A verde |
| R5 | Windows ACL | apertura e verifica profilo | una proprietà ACL alterata | API ACL indipendenti e token reali | A rosso | A verde |
| R6 | Windows portabile | rinomina senza sostituzione | memorizzata, non memorizzata, destinazione concorrente | volume + FileID128 + inventario | A rosso | A verde |
| R7 | Linux e Windows portabile | inventario comune | symlink POSIX; hard link, junction e mutazione Windows | handle dei figli + tipo indipendente | A rosso | A verde |
| R8 | Linux e Windows concorrenza | tre caricatori interni | globale esclusivo per tutti; locale esclusivo per il solo keystore | sessioni distinte sulla stessa radice + barriera | API/prova assenti | A verde |
| C1 | Windows concorrenza | blocco globale e locale | processi `spawn`, scadenza, barriera prima di `WriteFile`, terminazione | tempo monotono + byte canonico | non provato | A verde o difetto localizzato |
| C2 | Windows concorrenza | capacità di directory e lettura | scambio dopo apertura | FileID128 e byte letti | non provato | A verde o difetto localizzato |
| C3 | Linux concorrenza | catena `openat` e lettura | scambio fra due aperture relative | `st_dev` + `st_ino` + byte letti | non provato | A verde o difetto localizzato |
| C4 | Linux concorrenza | blocco globale | processi distinti, scadenza e terminazione | tempo monotono + inventario | non provato | A verde o difetto localizzato |
| G1 | Linux e Windows portabile | inventario e lettura | mutazione fra scansioni | identità completa | parziale | A verde |
| G2 | Linux e Windows compatibilità | tre facciate `Path` | archivio esterno e UID diverso | assenza del globale + risultato reale | parziale | A verde |
| G3 | Linux | creazione e rinomina | `EINTR`, `fsync`, parent distinti, `EXDEV` | ordine syscall + stato finale | parziale | A verde |
| G4 | Linux e Windows, iniezione di errori | chiusura, sblocco, adozione | errore di pulizia | contatore handle + codice Birth | parziale | A verde |
| G5 | Windows ACL | `_win_restore_privilege` | secondo `AdjustTokenPrivileges` | stato token indipendente | non provato | A verde |
| G6 | matrice pubblica | raccolta pytest | manifesto `node-id` | confronto manifest/raccolta | assente | A verde |
| G7 | Windows volume | apertura radice | volume non NTFS o senza ACL persistenti | API volume indipendente + inventario | parziale | A verde |
| G8 | Linux | radice e oggetti autorevoli | UID diverso da quello autenticato | `fstat` indipendente | parziale | A verde |
| G9 | Windows percorsi | apertura relativa | percorso lungo, `\\?\`, UNC, maiuscole/minuscole | percorso finale e identità | parziale | A verde |
| G10 | Windows ABI | strutture Win32 e identità | layout x64 e seriale high-bit | dimensioni/offset e valore unsigned | parziale | A verde |
| G11 | Windows binario | creazione, scrittura e lettura | byte `0x00`-`0xff` | confronto byte-per-byte | parziale | A verde |
| G12 | Windows sicurezza | applicazione ACL | errore `SetSecurityInfo` | codice stabile + inventario residui | non provato | A verde |

Ogni riga posseduta da 2A deve essere un controllo obbligatorio sulla
piattaforma indicata. Una riga non eseguibile deve restare rossa; può diventare
`N/A` soltanto se il testo normativo la assegna esplicitamente a un gruppo
successivo e ne indica il proprietario.

### 16.7 Prove insufficienti

Non chiudono alcun criterio:

- una contesa eseguita in sequenza o fra due handle dello stesso processo;
- una ACL costruita e verificata dalla stessa funzione produttiva;
- una junction o un link già presente senza sostituzione sincronizzata dopo
  l'apertura;
- un percorso nominale chiamato «handle-bound» senza processo avversario;
- un token privato costruito direttamente dal test al posto dell'entrata
  installatore;
- una eccezione controllata al posto di un arresto reale quando si dichiara la
  recuperabilità;
- un verde pubblico che non raccoglie il simbolo produttivo e il relativo A;
- un salto dovuto all'indisponibilità di privilegi, account o token richiesti.

### 16.8 Ordine vincolante della ripresa

Dopo l'approvazione esplicita prevista dal §16.1, la ripresa segue questo ordine:

1. produrre e riesaminare tutti i D senza modificare il comportamento del
   prodotto;
2. scrivere gli A, il manifesto canonico e la raccolta pubblica; soltanto gli A
   diventano controlli obbligatori, mentre i D restano allegati diagnostici;
3. chiudere R1 e R2, che delimitano proprietario e mutazioni;
4. provare C1, C2, C3 e C4, perché blocco multiprocesso e vincolo dell'handle su
   entrambe le piattaforme sono prerequisiti del recupero;
5. chiudere su POSIX R4, G3, G8, R3 e G4, in quest'ordine;
6. predisporre nella matrice pubblica l'attività Windows bloccante con seconda
   identità locale, processo non elevato e verifica indipendente dei diritti
   effettivi. Nessuna correzione di prodotto R5-R7 è ammessa prima che questa
   attività esista e dimostri di poter fallire sui controlli negativi;
7. chiudere su Windows G7, G10, R5, G5, G12, R7, R6, R3, G9 e G11, in
   quest'ordine: supporto del volume e ABI, profili ACL, ripristino del
   privilegio e fallimenti di sicurezza, inventario e handle, rinomina e
   riconciliazione, disposizione, percorsi e byte binari;
8. chiudere R8, G1 e G2 sui tre caricatori reali;
9. eseguire le prove di arresto reale, la sostituzione sincronizzata e l'intera
   matrice sulle piattaforme proprietarie;
10. richiedere una nuova revisione indipendente del codice e delle evidenze.

Se manca un'evidenza causale deterministica, statica o dinamica, il criterio torna
alla diagnosi e la correzione non parte. Se una prova di arresto non manifesta
una finestra già dimostrata dal D, non annulla la causa: segnala soltanto che la
prova di integrazione deve essere resa più controllabile. Al secondo fallimento
consecutivo della stessa classe dopo la correzione si applica nuovamente
l'arresto diagnostico del §15.

### 16.9 Registrazioni candidate e criterio conclusivo del codice 2A

Per eseguire GitHub Actions sullo stesso SHA del prototipo sono ammesse
registrazioni candidate incrementali esclusivamente su `main`, unico ramo
pubblico autorizzato. Ogni candidata:

- è approvata in sola lettura prima della pubblicazione;
- supera tutte le prove locali applicabili e non regredisce controlli già
  certificati;
- contiene nel messaggio il marcatore `RM-0008-Status: candidate-not-certified`;
- non viene etichettata, rilasciata o descritta come 2A completata;
- associa il risultato della matrice pubblica al proprio SHA esatto;
- al secondo fallimento della stessa classe riattiva l'arresto diagnostico.

La registrazione conclusiva e qualunque dichiarazione di certificazione 2A sono
consentite soltanto quando:

1. tutte le righe R1-R8, C1-C4 e G1-G12 sono verdi sulla piattaforma proprietaria;
2. il manifesto dimostra la raccolta pubblica esatta, senza salti o successi
   inattesi nelle celle interessate;
3. non restano rilievi P0 o P1, né P2 che violino un requisito normativo;
4. ogni P3 ha una disposizione esplicita: corretto, rinviato con proprietario o
   accettato con motivazione;
5. limiti e requisiti non provati sono dichiarati, senza trasformarli in verde;
6. la revisione indipendente ha approvato sia il codice sia l'evidenza.

Queste condizioni si aggiungono al §13 e non autorizzano la chiusura di RM-0008
o l'avvio dei gruppi successivi.

### 16.10 Barriera Windows predisposta, non ancora certificata

La prima attività ammessa dal passo 6 è stata predisposta senza modificare il
prodotto. La calibrazione è uno step obbligatorio del controllo storico
`Python 3.12 / windows-2022`; non è un'attività sorella che possa essere esclusa
da una regola di protezione già esistente. Essa usa soltanto codice sotto
`tests/windows_identity` e non importa moduli del runtime Metnos.

Il controllore amministrativo crea tramite `NetUserAdd` due account locali
temporanei e distinti, li associa al gruppo `Builtin Users` risolto mediante
SID e non mediante nome localizzato, e conserva le password soltanto in buffer
mutabili mai trasferiti in argomenti, ambiente, file, registri o artefatti. Ogni
sonda nasce sospesa tramite `CreateProcessWithLogonW`: prima della ripresa il
controllore verifica SID del token, assenza di elevazione e di appartenenza a
`Builtin Administrators`, livello d'integrità inferiore ad alto e assenza di un
token collegato inatteso.

L'oracolo strutturale non usa il generatore SDDL né il verificatore produttivo.
Legge proprietario, protezione della DACL, ACE, SID, ordine, flag e maschere con
`GetSecurityInfo`, `GetSecurityDescriptorControl`,
`GetSecurityDescriptorDacl`, `GetAclInformation`, `GetAce`, `IsValidSid` ed
`EqualSid`. Le sonde eseguono accessi reali con `CreateFileW`, `ReadFile` ed
enumerazione di directory. Tre controlli negativi noti dimostrano il rifiuto di
un proprietario diverso da `SYSTEM`, di un lettore estraneo aggiunto a un
oggetto `confidential` e di una maschera scrivibile assegnata al servizio.

La raccolta contiene undici casi senza `skip` o `xfail` e verifica entrambi i
profili su file e directory, le due identità reali e i dinieghi di scrittura,
append, creazione di figli, eliminazione e modifica DACL. Timeout, API non
disponibile, errore diverso da `ERROR_ACCESS_DENIED`, mancato ripristino del
privilegio o pulizia incompleta rendono rosso il controllo. Una revisione
statica indipendente non ha rilevato P0-P3 residui.

Questa sezione registra soltanto la predisposizione. La barriera diventa
evidenza utilizzabile per il passo 7 esclusivamente dopo un esito verde del
runner pubblico `windows-2022` associato allo SHA esatto della candidata. Fino
ad allora R5-R7 restano congelati.

### 16.11 Prima esecuzione pubblica della barriera

La candidata `d677347c50a78c1d5c3f9a75df865407ab0aa460` ha avviato
l'esecuzione pubblica `32902651884`. Linux ha completato con successo suite
portabile e prova delegata. Windows Server 2022, immagine
`20260818.277.1`, ha confermato NTFS ma la calibrazione si è arrestata prima di
creare account o oggetti ACL.

Il log localizza il solo errore in `_token_information`: il wrapper applicava a
ogni classe la richiesta preliminare con buffer nullo e lunghezza zero. Tale
schema è necessario per `TokenUser` e `TokenIntegrityLevel`, che contengono SID
a lunghezza variabile; sulla classe fissa `TokenElevation`, invece, il runner ha
restituito immediatamente `ERROR_BAD_LENGTH` (`WinError 24`). Nessun ramo di
account, privilegio, descrittore, accesso effettivo o pulizia era ancora stato
raggiunto.

La correzione candidata è quindi limitata al contratto ABI osservato: per
`TokenElevation` e `TokenElevationType` passa direttamente un buffer tipizzato
di quattro byte e richiede che `ReturnLength` coincida; conserva il doppio
passaggio per le due informazioni variabili. R5-R7 restano invariati e
congelati. Questo è il primo fallimento della classe e la correzione non è
evidenza finché una nuova esecuzione pubblica sul relativo SHA non è verde.

### 16.12 Certificazione pubblica della barriera Windows

La candidata `096284975579cb7fc21e0acb3b7f4e40eb605dd7` ha avviato
l'esecuzione pubblica `32903083843`. Entrambe le attività obbligatorie sono
terminate con successo sul medesimo SHA: `Python 3.12 / ubuntu-24.04` e
`Python 3.12 / windows-2022`.

Sul runner Windows, la calibrazione con identità reali e oracolo ACL
indipendente ha superato tutti gli undici casi in 5,21 secondi, senza salti o
esiti attesi invertiti. La successiva suite portabile ha superato 111 prove e
ne ha saltate sei, tutte estranee alla cella Windows posseduta dalla barriera,
in 131,15 secondi. Il controllo Linux ha superato a sua volta sia la suite
portabile sia la prova delegata prevista dal workflow.

Questa evidenza chiude la precondizione P1-a e autorizza il passo 7 del §16.8.
Non certifica da sola R5-R7, non rende verde alcuna prova di prodotto ancora da
scrivere e non dichiara completato l'incremento 2A o RM-0008.

### 16.13 Decisioni chiuse necessarie al manifesto di accettazione 2A

La rilettura successiva alla chiusura dei riproduttori D ha mostrato che otto
dettagli non erano abbastanza determinati per affidare la codifica a un agente
senza autorità normativa. Le decisioni seguenti appartengono al solo incremento
2A, non introducono una seconda politica di predisposizione e sono vincolanti
per i test A, per il manifesto e per l'implementazione.

#### 16.13.1 Unica entrata installatore e descrittore consumabile

L'unica entrata supportata che può produrre la capacità mutante 2A è
`install/birth_authority_provisioning.py::open_birth_provisioning_layout_v1()`.
Non accetta percorsi, UID, SID, handle, profili, descrittori o funzioni di
risoluzione. Risolve una sola volta la radice fissa `PATH_USER_CONFIG/birth`, la
collocazione fissa `operator-input-v1` e l'identità di servizio attraverso la
configurazione dell'installatore. Restituisce un `ProvisioningLayoutV1`
immutabile; la disposizione mantiene privati i descrittori già aperti e offre
una sola adozione della sessione Birth. La collocazione di ingresso operatore è
esposta alla sessione soltanto come capacità di lettura. Il descrittore della
radice Birth porta il ruolo chiuso `birth_integrity_only`; i sottoalberi
confidenziali vengono chiusi dal catalogo del §16.13.4.

Le prove isolate possono sostituire le funzioni private di risoluzione del
modulo installatore prima di invocare l'entrata senza argomenti. Questa
sostituzione è un'iniezione del banco di prova, non una variante dell'API
distribuita e non compare nel grafo del prodotto.

`_AuthenticatedRootDescriptor` contiene handle come tupla, percorso di solo
confronto e `_PlatformIdentity` immutabili. Nessun campo rappresenta lo stato di
adozione. Il consumo singolo è conservato esternamente dal modulo di accesso,
per identità dell'istanza, e una seconda adozione fallisce prima di trasferire o
chiudere un handle. Il token globale `_DESCRIPTOR_TOKEN` viene eliminato: non è
un sigillo.

La guardia R1 non usa una lista parziale di directory. Il file versionato
`tests/portable/rm0008_2a_acceptance/production-python-inventory-v1.json`
classifica ogni file Python tracciato ed esportato nella distribuzione pubblica
come `productive`, `test` oppure `documentation`. La classificazione non è una
dichiarazione libera: `conftest.py` e ogni file sotto `tests/` sono `test`, ogni
file sotto `docs/` è `documentation` e ogni altro file Python pubblico è
`productive`. Non esiste una categoria generica `tooling` che possa esentare
uno script distribuito. Ne segue che moduli alla radice,
`runtime/**/*.py`, `install/**/*.py`, `executors/**/*.py`, `tutor/**/*.py` e
tutti gli script Python distribuiti sono produttivi. La guardia confronta
l'insieme completo dei file tracciati con l'inventario e ricalcola la classe da
queste tre sole regole; fallisce se un file è assente, duplicato, non più
presente o classificato diversamente. Un aggiornamento della distribuzione deve
aggiornare nello stesso commit anche questo inventario.

L'analisi AST e del grafo delle chiamate attraversa tutti i file classificati
`productive`, compresi gli import e gli alias risolti fra moduli. Ammette la
costruzione e l'adozione del descrittore e ogni chiamata mutante soltanto in
`install/birth_authority_provisioning.py`. Nel modulo filesystem sono ammesse le
sole definizioni delle primitive e i richiami privati indispensabili alla loro
attuazione, non una seconda fabbrica o una seconda entrata mutante. Test,
diagnostica, documentazione e strumenti non diventano per questo superficie
produttiva; la loro classificazione è comunque esplicita e verificata.

Questa proprietà resta strutturale. Non protegge contro codice arbitrario già
eseguito con lo stesso UID o SID e nello stesso interprete; tale limite non può
essere rimosso dal rapporto conclusivo.

#### 16.13.2 Firma chiusa della disposizione 2A

La sessione mutante espone esattamente:

```python
dispose_transaction_object(
    expectation: _DisposalExpectation,
) -> _DispositionResult
```

`_ObjectKind` è l'enumerazione chiusa `regular_file|directory` e
`_DisposalClass` è l'enumerazione chiusa
`complete_file|partial_pending_file|empty_directory`. Il record immutabile
`_DisposalExpectation` contiene esattamente:

```python
components: tuple[str, ...]
identity: _ObjectIdentity
kind: _ObjectKind
role: _BirthObjectRole
disposal_class: _DisposalClass
links: int
expected_size: int | None
maximum_partial_size: int | None
content_sha256: str | None
inventory: tuple[_InventoryEntry, ...] | None
```

Le combinazioni ammesse sono chiuse. `complete_file` richiede
`kind=regular_file`, `links=1`, `expected_size` esatto, digest SHA-256 canonico,
`maximum_partial_size=None` e `inventory=None`. `partial_pending_file` richiede
`kind=regular_file`, `links=1`, `expected_size=None`, limite non negativo in
`maximum_partial_size`, `content_sha256=None` e `inventory=None`; è ammesso
soltanto per l'unico nome pending successivo autorizzato dal catalogo e dal
journal come descritto nel §7.6. `empty_directory` richiede `kind=directory`,
`links=2` su POSIX e `links=1` su Windows,
`expected_size=None`, `maximum_partial_size=None`, `content_sha256=None` e
`inventory=()`. Qualunque altra combinazione viene rifiutata prima di aprire il
nome. `components` deve essere una tupla non vuota di componenti relativi
canonici; `components=()` identifica la radice e viene sempre rifiutato prima
di qualunque apertura o disposizione.

`_InventoryEntry` è un record immutabile con i campi esatti `name`, `identity`,
`kind`, `role`, `links` e `size`; `name` è un singolo componente canonico e
`size` è un intero non negativo per il file oppure `None` per la directory. Un
file regolare è autorizzabile soltanto con `links=1`. Una directory vuota è
autorizzabile soltanto con `links=2` su POSIX e `links=1` su Windows; un valore
diverso produce `birth_provisioning_recovery_ambiguous`. Su una piattaforma il
cui filesystem non espone tale semantica l'operazione è non supportata, non
allarga il predicato. I reparse point e i collegamenti simbolici hanno un tipo
distinto e non sono mai autorizzabili dalla disposizione 2A. `expected_size` e
`maximum_partial_size` devono inoltre rispettare il limite del ruolo e
dell'oggetto nel catalogo prima di leggere i byte.

`_DispositionResult` è immutabile e contiene esattamente `identity`, `kind` e
`removed=True`. L'assenza iniziale dell'oggetto non è un successo idempotente:
produce `birth_provisioning_recovery_ambiguous`, perché 2A non possiede ancora
un journal che possa dimostrare una disposizione precedente. L'insieme dei
nomi autorizzati non entra nella primitiva: dal 2B il solo predispositore,
proprietario del catalogo e del journal, costruisce l'aspettativa per un nome
già riconosciuto. R1 vieta la costruzione produttiva del record altrove.

L'operazione richiede il blocco globale esclusivo prima di qualunque apertura
del nome. Apre l'oggetto relativamente all'handle della directory padre, senza
seguire collegamenti, e confronta sul medesimo handle identità completa, tipo,
ruolo di sicurezza, numero di collegamenti, dimensione e inventario. Per un
file completo legge e calcola il digest sullo stesso handle. Per il solo pending
parziale verifica che la dimensione osservata non superi il limite. Una
directory deve essere vuota e non può essere un reparse point. Ogni discordanza
fallisce chiusa prima della disposizione.

Su POSIX il codice esegue `fstat`, lettura e digest sul medesimo descrittore,
poi usa `unlinkat` relativo al descrittore padre, con `AT_REMOVEDIR` soltanto per
la directory vuota, sincronizza la directory padre e verifica l'assenza con un
nuovo accesso relativo. Su Windows la sessione rimuove dalle proprie mappe e
dalla lista di chiusura, una sola volta, ogni handle memorizzato del bersaglio e
dei suoi discendenti e lo chiude prima dell'apertura di disposizione. Apre poi
un nuovo handle relativo al padre. Per un file richiede esattamente
`DELETE|SYNCHRONIZE|READ_CONTROL|FILE_READ_ATTRIBUTES|FILE_READ_DATA`; per una
directory richiede esattamente
`DELETE|SYNCHRONIZE|READ_CONTROL|FILE_READ_ATTRIBUTES|FILE_LIST_DIRECTORY|FILE_TRAVERSE`.
In entrambi i casi concede `FILE_SHARE_READ|FILE_SHARE_WRITE` e non
`FILE_SHARE_DELETE`. Verifica metadati, byte e digest su quell'handle, applica
esclusivamente `SetFileInformationByHandle(FileDispositionInfoEx)` con i flag
`DELETE|POSIX_SEMANTICS` fissati dal §7.6 e richiede
che `FileStandardInfo.DeletePending` diventi vero sul medesimo handle. Chiude
quindi l'handle di disposizione esattamente una volta. Soltanto dopo la chiusura
riconcilia l'inventario attraverso l'handle del padre. Non riapre un
percorso assoluto e non tratta un oggetto in cancellazione differita come
assenza già riuscita. Ogni fallimento di pulizia conserva l'errore primario e
viene normalizzato secondo G4.

#### 16.13.3 Nomi e firme dei tre caricatori interni

Le tre entrate interne in sessione sono fissate come segue:

```python
_load_birth_keystore_in_session(
    directory: tuple[str, ...],
    session: _SecureRootSession,
    *,
    forbidden_public_keys: Iterable[bytes | Ed25519PublicKey] = (),
) -> LoadedBirthKeyStore

_load_approval_authority_in_session(
    authority_file: tuple[str, ...],
    session: _SecureRootSession,
) -> ApprovalAuthority

_load_semantic_authority_in_session(
    authority_file: tuple[str, ...],
    public_directory: tuple[str, ...],
    evidence_directory: tuple[str, ...],
    session: _SecureRootSession,
) -> PreprovisionedSemanticAuthority
```

Tutte richiedono che la sessione possieda il blocco globale condiviso o
esclusivo. Nessuna rilascia o riacquisisce il globale. Il caricatore
dell'archivio chiavi acquisisce inoltre il proprio blocco locale condiviso;
approvazioni e semantica non inventano un blocco locale. I tre argomenti
relativi della semantica devono coincidere con i nomi e con le relazioni
contenute nel catalogo chiuso; non trasformano percorsi dichiarati dal file in
autorità.

L'autorità semantica conserva una `_SecureDirectoryHandle` per le evidenze,
legata alla medesima sessione, invece di un `Path` da riaprire. L'autorità non
chiude e non trasferisce la sessione. Il bundle del gruppo 3 è l'unico
proprietario del ciclo di vita: mantiene la sessione aperta finché l'autorità è
utilizzabile, rilascia il blocco globale prima dei blocchi runtime come imposto
dal §7.5 e, dopo l'ultimo uso, invalida l'autorità e chiude la sessione
esattamente una volta. Ogni uso dell'autorità dopo tale chiusura fallisce con
`semantic_review_unavailable`, senza includere percorsi nel messaggio o nella
catena pubblica dell'errore. Nel 2A la prova resta entro la vita esplicita della
sessione e verifica anche questa invalidazione. Le tre facciate storiche basate
su `Path` restano separate e di sola lettura per G2.

#### 16.13.4 Ruoli e catalogo dei profili di sicurezza

Nessuna API interna accetta più il booleano `exact_private` o le stringhe libere
`confidential` e `integrity_only` come decisione autorevole. Usa
`_BirthObjectRole`, enumerazione chiusa con i ruoli
`birth_confidential`, `birth_integrity_only`, `historical_private` e
`historical_public`. Il tipo file o directory rimane separato nel record
tipizzato. La conversione ruolo e tipo → profilo di piattaforma è una tabella
costante del modulo di accesso:

- `birth_confidential`: proprietario POSIX uguale all'identità autenticata e
  modi esatti `0700/0600`; su Windows profilo `confidential`;
- `birth_integrity_only`: proprietario POSIX uguale all'identità autenticata e
  modi esatti `0755/0644`, che consentono lettura senza concedere modifica a
  gruppo o altri; su Windows profilo `integrity_only`;
- `historical_private`: proprietario POSIX uguale all'utente efficace e modi
  esatti `0700/0600`; su Windows proprietario uguale al SID utente del token
  corrente e DACL storica privata descritta sotto;
- `historical_public`: su POSIX il proprietario non è autorevole, ma gruppo e
  altri non possono scrivere; su Windows proprietario uguale al SID utente del
  token corrente e DACL storica pubblica descritta sotto.

Il catalogo del modulo installatore assegna ruoli ai nomi fissi e ai soli
schemi dinamici definiti dai §§4.1-4.3. Sono `birth_confidential` le directory
che contengono private o configurazioni necessarie a usarle, i file privati e
le configurazioni degli archivi. Sono `birth_integrity_only` il blocco globale,
registri pubblici, chiavi pubbliche, journal e checkpoint privi di segreti,
`set.json`, materiale del contesto e marcatori. La radice Birth e i contenitori
misti `authority-sets`, `authority-sets/<set_id>` e `producers` sono
`birth_integrity_only`, così i discendenti pubblici restano attraversabili.
L'archivio `author-root-v1`, l'archivio Admission e ogni singolo archivio
Producer sono `birth_confidential`.

La directory di transazione non è interamente confidenziale. La sua radice è
un contenitore misto `birth_integrity_only`; `transaction-v1.json`, la
directory `checkpoints-v1`, i checkpoint, `prepared-v1.json`, il contenitore
`authority-set`, `set.json`, `approval`, `semantic`, `context` e i relativi
oggetti pubblici sono `birth_integrity_only`. Soltanto `author-root-v1`,
`authority-set/admission` e ogni
`authority-set/producers/p-<digest-capacità>` sono `birth_confidential`; il
contenitore `authority-set/producers` resta `birth_integrity_only`. In questo
modo i checkpoint sono realmente raggiungibili dal profilo pubblico previsto.
La visibilità dei nomi nei contenitori misti è una proprietà accettata, non un
segreto. Nessuna regola generica «un discendente privato rende privato ogni
antenato» può sostituire la tabella chiusa.

I caricatori storici possiedono cataloghi distinti e costanti: archivio chiavi
e configurazione sono `historical_private`; registri di approvazione,
configurazione semantica, pubbliche ed evidenze sono `historical_public`. Su
Windows questi ruoli non riusano i profili Birth, che richiedono `SYSTEM` come
proprietario e il SID del servizio. La facciata storica ricava internamente il
SID utente da `TokenUser`; non accetta SID dal chiamante e non modifica ACL.
La DACL deve essere protetta, priva di ereditarietà e contenere, in questo
ordine, sole ACE `ACCESS_ALLOWED`: `SYSTEM` con controllo completo,
`Administrators` con controllo completo e utente corrente con controllo
completo. Per `historical_public` segue una quarta ACE
`Authenticated Users`, con la maschera esatta di lettura e attraversamento
`0x001200a9` per directory o di lettura `0x00120089` per file. Il profilo
privato non contiene tale ACE. Owner, flag di controllo, numero, ordine, tipo,
flag, SID e maschera di ogni ACE devono coincidere; materiale ereditato o
altrimenti sicuro ma non canonico fallisce chiuso. G2 usa fixture reali con
questi profili e dimostra che le facciate precedenti leggono senza mutarli.

Ogni creazione registra nella sessione il ruolo contro `_ObjectIdentity`, non
soltanto contro il nome. Ogni riapertura richiede che catalogo, identità e ruolo
concordino. Un oggetto già aperto non acquisisce un nuovo ruolo da un argomento
di lettura. La radice viene verificata all'adozione con il ruolo contenuto nel
descrittore installatore. Il confronto Windows analizza owner, controllo DACL,
numero, ordine, tipo e flag delle ACE, SID e maschere mediante le API native;
non confronta stringhe SDDL.

#### 16.13.5 Limite comune dell'inventario

Il limite G1 mantiene il valore V1 già fissato dal §4.4 e ha due ambiti
simultanei: al massimo 4.096 voci in una singola directory e al massimo 4.096
voci distinte nell'intera operazione logica di inventario chiuso. La 4.097ª voce
locale o aggregata produce `birth_provisioning_recovery_ambiguous` durante
l'enumerazione incrementale, prima di costruire il record aggiuntivo.

La sessione possiede il tipo privato `_InventoryBudgetV1`, inizializzato con il
limite immutabile 4.096. Un caricamento o una fotografia completa crea un solo
budget e lo passa a tutte le scansioni dell'albero. La stessa voce logica,
identificata da percorso relativo canonico e identità di piattaforma, conta una
sola volta anche se viene osservata nella scansione precedente e successiva o
riaperta per il controllo tipizzato; un nome con identità diversa non viene
deduplicato e produce prima l'errore di sostituzione previsto. Una chiamata
autonoma `inventory()` su una sola directory crea invece un proprio budget.
POSIX non può usare una funzione che materializzi prima tutti i nomi; Windows
controlla entrambi i contatori mentre decodifica i record di enumerazione.

#### 16.13.6 Matrice chiusa dei percorsi Windows

La matrice G9 è la seguente e non ammette salti:

| Caso | Esito richiesto |
|---|---|
| Percorso locale NTFS canonico | Accettato se percorso finale, volume e FileID coincidono. |
| Percorso locale più lungo di 260 caratteri | Accettato mediante forma verbatim, senza troncare né riaprire la forma normalizzata. |
| Prefisso locale `\\?\` | Accettato se rappresenta lo stesso oggetto e la stessa identità del percorso canonico. |
| Variazione di maiuscole e minuscole | Accettata soltanto quando l'handle risolve allo stesso FileID; il testo normalizzato serve solo al confronto. |
| UNC o `\\?\UNC\` raggiungibile | Accettato soltanto se l'oracolo indipendente conferma NTFS, `FILE_PERSISTENT_ACLS`, stesso volume e identità stabile. |
| UNC non raggiungibile o privo delle garanzie | Rifiutato con `birth_provisioning_atomic_install_unsupported`, inventario invariato e nessun oggetto creato. |
| Prefisso malformato, percorso relativo o attraversamento del genitore | Rifiutato prima dell'uso con il codice Birth stabile. |

G9 possiede tre celle UNC distinte e obbligatorie. La cella positiva crea con un
controllore amministrativo una condivisione SMB loopback dal nome casuale ma
registrato, la cui directory sorgente si trova sullo stesso volume NTFS del
workspace. L'oracolo indipendente verifica prima dell'uso che la forma
`\\127.0.0.1\<share>` sia raggiungibile, che il volume dichiari NTFS e
`FILE_PERSISTENT_ACLS` e che volume e FileID restino stabili. Attraverso il
simbolo produttivo esegue poi, sulla condivisione, creazione relativa con
descrittore di sicurezza, scrittura e rilettura, rinomina no-replace e
disposizione `FileDispositionInfoEx`; verifica identità, destinazione,
`DeletePending`, chiusura e inventario finale. Un errore di creazione, accesso
o verifica della condivisione o di una di queste operazioni rende rossa la cella
positiva; non viene trasformato in un rifiuto atteso. Il controllore rimuove la
condivisione e la directory soltanto dopo aver verificato che nessun handle sia
rimasto aperto.

La cella negativa usa separatamente un nome UNC sicuramente non pubblicato dal
controllore e verifica il rifiuto chiuso
`birth_provisioning_atomic_install_unsupported`, inventario invariato e nessun
oggetto creato. Una terza cella apre la condivisione loopback reale ma sostituisce
nel solo processo di prova il risultato di `GetVolumeInformationByHandleW` con
una risposta strutturalmente valida priva di `FILE_PERSISTENT_ACLS`; verifica
che il simbolo produttivo rifiuti prima della creazione. L'oracolo del
controllore conferma separatamente che la directory reale e il suo inventario
non sono cambiati. L'intercettazione resta nel banco di prova e non introduce
un callback o un'opzione nel prodotto. Nessuna cella usa `skip`, `xfail` o
selezioni basate sulla capacità osservata.

#### 16.13.7 Profondità sentinella per C2 e C3

Le prove di sostituzione usano la catena fissa
`first/middle/last/payload.bin`. Le barriere sono cinque e producono cinque
`node-id` distinti: radice, primo intermedio, intermedio centrale, ultimo
intermedio e oggetto finale. Una guardia AST separata rifiuta rami produttivi
che trattino una posizione dell'intermedio in modo diverso dal ciclo comune.
Questa profondità non riduce il contratto: il prodotto deve applicare la stessa
regola a ogni componente fino al limite di 1.024 byte; la sentinella copre
inizio, centro e fine del ciclo con un oracolo indipendente per ciascuna
barriera.

#### 16.13.8 Arresti posseduti da 2A e arresti rinviati

L'incremento 2A possiede gli arresti reali delle proprie primitive, su radici
isolate e senza inventare journal:

- creazione vuota del lock, acquisizione e punto precedente alla prima
  scrittura, con recupero del byte canonico;
- file creato con sicurezza già restrittiva, scrittura parziale, scrittura
  completa, sincronizzazione del file e sincronizzazione della directory padre
  POSIX;
- applicazione ACL Windows, scrittura parziale, scrittura completa e
  `FlushFileBuffers`;
- prima e dopo la chiamata nativa di rinomina e disposizione, richiedendo che lo
  stato osservato sia uno dei soli stati atomici ammessi. Il 2A riconosce lo
  stato, ma non adotta un successo incerto in assenza del journal 2B.

La matrice di esito è chiusa:

| Operazione e punto di arresto | Stato indipendente richiesto | Esito di un tentativo diretto 2A successivo |
|---|---|---|
| Rinomina, prima della chiamata nativa | Sorgente presente e destinazione assente. | Può ripetere la rinomina e riuscire. |
| Rinomina, dopo il successo nativo ma prima del ritorno | Sorgente assente e destinazione presente con la stessa identità. | `birth_provisioning_recovery_ambiguous`; soltanto il journal 2B può adottare il risultato. |
| Rinomina, errore nativo con destinazione inizialmente assente | Sorgente presente e invariata; destinazione assente. | Errore chiuso appropriato; nessuna adozione. |
| Rinomina, destinazione già esistente o comparsa alla barriera | Sorgente e destinazione sono entrambe presenti e conservano le rispettive identità, byte e inventari. | `birth_provisioning_transaction_conflict`; nessuna adozione. |
| Disposizione, prima della chiamata nativa | Oggetto presente con tutti i campi dell'aspettativa invariati. | Può ripetere la disposizione e riuscire. |
| Disposizione, dopo il successo nativo ma prima del ritorno | Oggetto assente; nessun altro nome o oggetto è cambiato. | `birth_provisioning_recovery_ambiguous`; soltanto il journal 2B può adottare il risultato. |
| Disposizione, errore nativo | Oggetto presente con identità, byte, metadati e inventario invariati. | Errore chiuso appropriato; nessuna assenza come successo. |

Un ritorno normale da entrambe le primitive è ammesso soltanto dopo la
post-validazione relativa al padre: la rinomina dimostra sorgente assente,
destinazione presente e identità invariata; la disposizione dimostra bersaglio
assente e inventario del padre altrimenti invariato. Le prove osservano lo stato
con un processo indipendente che non importa la primitiva di prodotto.

Le barriere sono intercettazioni confinate del banco di prova nel processo
figlio. Non aggiungono callback, variabili d'ambiente o punti di arresto al
prodotto. POSIX usa `SIGKILL` e il controllore richiede con `waitpid` che il
figlio sia terminato dal segnale `SIGKILL` prima dell'oracolo filesystem.
Windows usa `TerminateProcess` con il codice sentinella `0xEE`, richiede che la
chiamata restituisca successo, attende al massimo trenta secondi con
`WaitForSingleObject`, accetta soltanto `WAIT_OBJECT_0`, verifica con
`GetExitCodeProcess` il codice `0xEE` e chiude gli handle di processo e thread.
Soltanto dopo tali verifiche interroga filesystem, `DeletePending` e inventario.

Dal 2B al 2F restano proprietari degli arresti legati a
`transaction-v1.json`, ciascun payload reale, pending, checkpoint, journal,
pubblicazione dei tre finali, `set_id`, marcatore e convergenza del
predispositore. Nessuna prova generica 2A può colorare di verde quelle celle.

### 16.14 Collocazione e schema del manifesto A

Il manifesto pubblico canonico è
`tests/portable/rm0008-2a-acceptance-manifest-v1.json`. Le prove A comuni e
POSIX risiedono esclusivamente in
`tests/portable/rm0008_2a_acceptance/`; le prove A Windows, comprese quelle con
identità reali, risiedono esclusivamente in
`tests/windows_identity/rm0008_2a_acceptance/`. Le prove storiche che hanno
salti legittimi rimangono fuori da questi due alberi. Non si crea un albero
escluso dall'esportazione pubblica.

#### 16.14.1 Schema canonico

L'oggetto superiore contiene esattamente:

```json
{"cells":[],"schema_version":1,"suite_id":"rm-0008-increment-2a"}
```

`cells` viene poi popolato con record che contengono esattamente, senza campi
aggiuntivi, `criterion`, `node_id`, `activity`, `platform`,
`production_symbols`, `certification_symbols`, `oracle`,
`normative_subcase` e `pre_fix_disposition`. Tutte le stringhe presenti sono
non vuote, normalizzate NFC e prive di caratteri di controllo. Si applicano
inoltre i vincoli seguenti:

- `criterion` soddisfa
  `^(?:R[1-8]|C[1-4]|G(?:[1-9]|1[0-2]))$`;
- `activity` appartiene all'enumerazione chiusa `manifest`,
  `portable-ubuntu`, `portable-windows`, `concurrency-ubuntu`,
  `concurrency-windows`, `windows-acl`;
- `platform` vale `platform-independent` per `manifest`, `linux` per le due
  attività Ubuntu e `windows` per le tre attività Windows;
- `node_id` è l'identificativo pytest completo, relativo alla radice del
  deposito e contenuto in uno dei due alberi A. Ogni parametrizzazione usa un
  identificativo esplicito; spazi e caratteri di controllo sono vietati;
- `production_symbols` e `certification_symbols` sono liste ordinate e senza
  duplicati di stringhe ASCII che soddisfano
  `^[A-Za-z_][A-Za-z0-9_.]*::[A-Za-z_][A-Za-z0-9_.]*$`. Per G6 la prima lista
  è vuota e la seconda è non vuota, perché l'oggetto della prova è
  l'infrastruttura di certificazione. Per ogni altro criterio la prima è non
  vuota e la seconda è vuota. Nessun simbolo di test viene presentato come
  simbolo produttivo;
- `oracle` è una lista non vuota, ordinata e senza duplicati i cui valori
  appartengono all'enumerazione chiusa `ast-call-graph`, `byte-comparison`,
  `handle-counter`, `inventory-snapshot`, `monotonic-barrier`,
  `posix-disk-state`, `posix-fstat`, `posix-syscall-trace`,
  `process-exit-state`, `pytest-collection`, `win32-acl`,
  `win32-file-identity`, `win32-token-access`, `win32-volume`,
  `workflow-structure`;
- `normative_subcase` è una stringa ASCII che soddisfa
  `^[a-z0-9][a-z0-9-]*$`;
- `pre_fix_disposition` appartiene all'enumerazione chiusa
  `red|absent|may_green` ed è usato soltanto per la fotografia precedente alla
  correzione, mai per attenuare la certificazione finale.

Il file è JSON UTF-8 canonico: chiavi ordinate, separatori `,` e `:`, nessun
ritorno a capo finale e `allow_nan=false`. Le liste `production_symbols`,
`certification_symbols` e `oracle` sono ordinate per byte UTF-8. `cells` è
ordinata per la tupla
`(activity, criterion, node_id, normative_subcase)`, confrontata per byte
UTF-8. Sono uniche sia `(activity, node_id)` sia
`(criterion, activity, normative_subcase)`. Il medesimo `node_id` può comparire
in due record soltanto nella coppia `portable-ubuntu` e `portable-windows`; in
ogni altro caso la duplicazione è un errore.

#### 16.14.2 Inventario normativo chiuso delle celle

Il validatore contiene la costante letterale `REQUIRED_CELLS_V1`, indipendente
dal file JSON, e confronta esattamente l'insieme delle tuple
`(criterion, activity, platform, normative_subcase, pre_fix_disposition)`.
La costante contiene 248 record: 12 per `manifest`, 67 per `portable-ubuntu`,
60 per `portable-windows`, 17 per `concurrency-ubuntu`, 21 per
`concurrency-windows` e 71 per `windows-acl`. Un insieme vuoto, una cella
mancante, una cella aggiuntiva o una cardinalità diversa fallisce prima della
raccolta pytest.

Il valore precedente alla correzione appartiene alla tupla normativa e non è
scelto dal record JSON. Il valore predefinito è `may_green`. Sono `red`
esattamente le celle seguenti:

- R1 `descriptor-immutable-single-consumption` e
  `productive-graph-no-mutating-capability`;
- R2 gli otto slug che terminano in `-no-global` o `-shared-global`;
- R4 `empty-lock-fsync-order`;
- R5 `reject-owner`, `reject-unprotected-dacl`, `reject-ace-order`,
  `reject-ace-type-or-flags`, `reject-ace-sid`, `reject-ace-mask` e
  `catalog-role-identity-binding`;
- R6 `cached-source-renames` e `fresh-source-profile-rejected`;
- R7 su Ubuntu `symlink-record-rejected` e `hardlink-rejected`; su Windows
  `hardlink-rejected`, `junction-reparse-record-rejected` e
  `mutation-between-scans-rejected`.

Sono `absent` R1 `installer-only-entry`, tutte le celle R3 e, per entrambe le
attività di concorrenza, R8 `approval-global-exclusive`,
`semantic-global-exclusive` e `semantic-use-after-close`. Tutte le altre celle,
compresi i controlli positivi R2, R4, R5-R7, le due celle keystore R8, C1-C4 e
G1-G12, sono `may_green`. G6 appartiene a quest'ultima classe perché manifesto
e validatore vengono aggiunti insieme agli A, prima di modificare il prodotto.

Il `node_id` di ogni record deve terminare con `[<normative_subcase>]`, usando
esattamente lo slug seguente come identificativo pytest esplicito. Le celle
comuni R2, R3, G1 e G4 riusano il medesimo `node_id` nella sola coppia portabile
Ubuntu/Windows; tutte le altre hanno un `node_id` distinto.

L'inventario obbligatorio è il seguente. La cardinalità indicata è il numero di
slug della riga, non un conteggio aggregato di asserzioni interne.

| Criterio | Attività | Piattaforma | Slug normativi esatti | N |
|---|---|---|---|---:|
| R1 | `manifest` | `platform-independent` | `installer-only-entry`, `descriptor-immutable-single-consumption`, `productive-graph-no-mutating-capability` | 3 |
| R2 | `portable-ubuntu` e `portable-windows` | rispettivamente `linux` e `windows` | `create-file-no-global`, `create-file-shared-global`, `create-directory-no-global`, `create-directory-shared-global`, `rename-no-global`, `rename-shared-global`, `dispose-no-global`, `dispose-shared-global`, `exclusive-allows-mutations`, `shared-allows-readers` | 10 per attività |
| R3 | `portable-ubuntu` e `portable-windows` | rispettivamente `linux` e `windows` | `complete-file-success`, `empty-directory-success`, `reject-root-components`, `reject-absent`, `reject-identity`, `reject-kind`, `reject-role`, `reject-links`, `reject-size`, `reject-digest`, `reject-nonempty-directory`, `partial-pending-success`, `reject-partial-oversize`, `reject-foreign-pending` | 14 per attività |
| R3 | `portable-windows` | `windows` | `disposition-relative-open`, `disposition-file-access-mask`, `disposition-directory-access-mask`, `disposition-ex-invalid-parameter-no-fallback`, `disposition-ex-not-supported-no-fallback`, `disposition-deletepending-false`, `disposition-readonly-rejected`, `disposition-access-denied-mapping`, `disposition-residual-error-mapping` | 9 |
| R3 | `concurrency-ubuntu` e `concurrency-windows` | rispettivamente `linux` e `windows` | `dispose-crash-before-native`, `dispose-crash-after-native` | 2 per attività |
| R4 | `portable-ubuntu` | `linux` | `empty-lock-fsync-order`, `empty-lock-kill-and-recover` | 2 |
| R5 | `windows-acl` | `windows` | `reject-owner`, `reject-unprotected-dacl`, `reject-ace-order`, `reject-ace-type-or-flags`, `reject-ace-sid`, `reject-ace-mask`, `birth-confidential-file-access`, `birth-confidential-directory-access`, `birth-integrity-file-access`, `birth-integrity-directory-access`, `nonelevated-stable-error-no-secret`, `catalog-role-identity-binding` | 12 |
| R6 | `portable-windows` | `windows` | `cached-source-renames`, `fresh-source-profile-rejected`, `destination-existing-conflict`, `native-error-destination-absent`, `success-postvalidation`, `different-volume-rejected`, `source-fileid128-preserved` | 7 |
| R6 | `concurrency-windows` | `windows` | `destination-race-conflict`, `rename-crash-before-native`, `rename-crash-after-native` | 3 |
| R7 | `portable-ubuntu` | `linux` | `regular-record`, `directory-record`, `symlink-record-rejected`, `hardlink-rejected`, `mutation-between-scans-rejected` | 5 |
| R7 | `portable-windows` | `windows` | `regular-record`, `directory-record`, `hardlink-rejected`, `junction-reparse-record-rejected`, `mutation-between-scans-rejected` | 5 |
| R8 | `concurrency-ubuntu` e `concurrency-windows` | rispettivamente `linux` e `windows` | `keystore-global-exclusive`, `approval-global-exclusive`, `semantic-global-exclusive`, `keystore-local-exclusive`, `semantic-use-after-close` | 5 per attività |
| C1 | `concurrency-windows` | `windows` | `shared-shared`, `shared-exclusive`, `exclusive-shared`, `killed-holder-releases`, `empty-lock-crash-recovery`, `reader-never-creates` | 6 |
| C2 | `concurrency-windows` | `windows` | `swap-after-root`, `swap-after-first`, `swap-after-middle`, `swap-after-last`, `swap-final-object` | 5 |
| C3 | `concurrency-ubuntu` | `linux` | `swap-after-root`, `swap-after-first`, `swap-after-middle`, `swap-after-last`, `swap-final-object` | 5 |
| C4 | `concurrency-ubuntu` | `linux` | `shared-shared`, `shared-exclusive`, `exclusive-shared`, `killed-holder-releases`, `loader-blocks-provisioner-before-mutation` | 5 |
| G1 | `portable-ubuntu` e `portable-windows` | rispettivamente `linux` e `windows` | `add-between-scans`, `remove-between-scans`, `rename-between-scans`, `replace-same-name-between-scans`, `non-json-entry`, `local-4096`, `local-4097`, `aggregate-4096`, `aggregate-4097` | 9 per attività |
| G2 | `portable-ubuntu` | `linux` | `keystore-external-local-only`, `approval-public-other-uid`, `semantic-public-other-uid`, `keystore-legacy-no-mutation`, `approval-legacy-no-mutation`, `semantic-legacy-no-mutation` | 6 |
| G2 | `windows-acl` | `windows` | `keystore-historical-private`, `approval-historical-public`, `semantic-historical-public`, `keystore-no-global`, `historical-acl-no-mutation`, `historical-inherited-rejected` | 6 |
| G3 | `portable-ubuntu` | `linux` | `short-write`, `eintr-write`, `file-fsync-error-state`, `parent-fsync-error-state`, `rename-two-parents-fsync`, `rename-exdev`, `rename-enosys`, `renameat2-unavailable`, `crash-created`, `crash-partial`, `crash-complete`, `crash-file-fsync`, `crash-parent-fsync` | 13 |
| G4 | `portable-ubuntu` e `portable-windows` | rispettivamente `linux` e `windows` | `close-error-primary-preserved`, `unlock-error-primary-preserved`, `adoption-error-normalized`, `handle-close-exactly-once`, `public-error-redacted` | 5 per attività |
| G5 | `windows-acl` | `windows` | `restore-false`, `restore-not-all-assigned`, `body-error-restore`, `real-token-roundtrip`, `token-handle-close-once` | 5 |
| G6 | `manifest` | `platform-independent` | `schema-canonical`, `required-cell-inventory`, `production-inventory`, `collection-exact`, `no-skip-xfail`, `activity-selection`, `evidence-schema`, `pre-fix-snapshot`, `workflow-dependency` | 9 |
| G7 | `windows-acl` | `windows` | `reject-non-ntfs-file-create`, `reject-non-ntfs-directory-create`, `reject-no-persistent-acl-file-create`, `reject-no-persistent-acl-directory-create` | 4 |
| G8 | `portable-ubuntu` | `linux` | `reject-root-uid`, `reject-intermediate-uid`, `reject-file-uid` | 3 |
| G9 | `windows-acl` | `windows` | `local-canonical`, `local-long`, `local-verbatim`, `local-case-variant`, `unc-loopback-positive`, `unc-unreachable-rejected`, `unc-no-persistent-acls-rejected`, `malformed-prefix-rejected`, `relative-rejected`, `parent-traversal-rejected` | 10 |
| G10 | `windows-acl` | `windows` | `abi-file-rename-info`, `abi-file-id-info`, `abi-file-disposition-info-ex`, `abi-overlapped`, `abi-unicode-string`, `abi-object-attributes`, `abi-io-status-block`, `abi-ntdll-signatures`, `volume-serial-high-bit`, `ntcreate-relative-rootdirectory`, `ntcreate-no-createfilew-fallback`, `ntstatus-create-collision`, `ntstatus-lock-not-found`, `ntstatus-disposition-not-found`, `ntstatus-read-not-found`, `ntstatus-mutating-access-denied`, `ntstatus-read-access-denied`, `ntstatus-lock-sharing`, `ntstatus-other-sharing`, `ntstatus-unsupported`, `ntstatus-residual`, `rename-error-existing`, `rename-error-access-denied`, `rename-error-unsupported`, `rename-error-residual` | 25 |
| G11 | `portable-windows` | `windows` | `all-byte-values-roundtrip` | 1 |
| G12 | `windows-acl` | `windows` | `setsecurityinfo-access-denied-file`, `setsecurityinfo-access-denied-directory`, `setsecurityinfo-injected-dword-error`, `no-complete-destination`, `residues-reconciled`, `crash-after-acl-before-write`, `crash-partial-write`, `crash-complete-write`, `crash-flush` | 9 |

Ogni slug rappresenta un rapporto `call` distinto. Un test può condividere
fixture e funzione parametrizzata, ma non può fondere due slug in un rapporto o
usare un'unica asserzione aggregata per ridurre la cardinalità. Il validatore
ricostruisce la tabella dai record, verifica i subtotali per attività e il
totale 248, quindi verifica i `node_id` raccolti.

#### 16.14.3 Fotografia verificabile precedente alla correzione

Prima di modificare un file classificato `productive`, gli A e il manifesto
vengono pubblicati in un commit che modifica soltanto prove, inventari e
workflow. Le sei attività sono eseguite sul prototipo senza `xfail`, inversioni
di asserzione o trattamento speciale da parte di pytest. Un registratore
esterno alle prove raccoglie i rapporti reali. Ogni cella dichiarata `red` o
`absent` deve produrre esattamente un rapporto `call` con esito `failed`; un
esito `passed`, `skipped`, `error`, una raccolta fallita o un rapporto mancante
blocca la fotografia. Una cella `may_green` può produrre `passed` oppure
`failed`, ma non un salto, un errore di raccolta o un risultato atteso.
Una cella `absent` importa il modulo normalmente durante la raccolta e risolve
il simbolo mancante dentro la funzione di prova, così l'assenza produce il solo
rapporto `call=failed` previsto e non un errore di importazione o raccolta.

Ogni attività produce l'evidenza per-attività con lo stesso schema del
§16.14.5, salvo che `results` contiene record esatti
`node_id`, `declared_disposition` e `observed_outcome`;
`declared_disposition` ripete il valore del manifesto e
`observed_outcome` appartiene a `passed|failed`. Un job di fotografia, distinto
dal riepilogo di certificazione finale, verifica le regole precedenti e unisce
le sei evidenze nel file canonico
`tests/portable/rm0008-2a-pre-fix-evidence-v1.json`. L'oggetto contiene
esattamente `schema_version=1`, `suite_id="rm-0008-increment-2a"`,
`source_git_sha`, `manifest_sha256`, `production_inventory_sha256`,
`activities` e `results`. `source_git_sha` ha quaranta cifre esadecimali
minuscole; `manifest_sha256` e `production_inventory_sha256` usano il prefisso
e le sessantaquattro cifre del §16.14.5.
`activities` è la lista ordinata per byte del nome attività dei sei record
esatti `activity` e `runner_image`; `results` è la lista ordinata dei record
esatti `activity`,
`node_id`, `declared_disposition` e `observed_outcome`, nello stesso ordine
canonico delle celle.

Il file aggregato viene revisionato e aggiunto nel commit immediatamente
successivo, prima della prima modifica produttiva. Il suo `source_git_sha`
identifica il commit pubblico delle sole prove e il suo `manifest_sha256`
deve continuare a coincidere con il manifesto. Da quel momento schema e
validatore dell'inventario, `REQUIRED_CELLS_V1`, manifesto, prove A e fotografia
sono congelati. L'elenco meccanico `files` di
`production-python-inventory-v1.json` è la sola eccezione: viene rigenerato
deterministicamente nello stesso commit che aggiunge o rimuove un file Python,
senza cambiare schema o classificazione. Il digest precedente resta registrato
nella fotografia e quello corrente entra nelle evidenze finali.

Qualunque correzione agli artefatti congelati prima del verde finale richiede
una nuova fotografia su una base che conserva esattamente i byte produttivi del
prototipo; non è ammesso rigenerarla contro il prodotto già corretto. La
fotografia non è un'attività di certificazione, non entra nel riepilogo finale
e non trasforma un fallimento atteso in verde di prodotto.

#### 16.14.4 Inventario Python controllato da R1

Il file
`tests/portable/rm0008_2a_acceptance/production-python-inventory-v1.json`
contiene esattamente `schema_version=1`,
`inventory_id="rm-0008-production-python"` e `files`. Ogni elemento di `files`
contiene esattamente `path` e `classification`; `classification` appartiene a
`productive|test|documentation`. I percorsi sono relativi
POSIX canonici, NFC, senza punto, genitore, backslash, componenti vuoti o link
simbolici, terminano in `.py` e sono ordinati per byte UTF-8 senza duplicati.
Anche questo file usa il JSON canonico del paragrafo precedente.

Il validatore ottiene l'insieme autorevole con
`git ls-files -z -- '*.py'` sullo stesso SHA pubblico, rifiuta link simbolici e
lo confronta esattamente con `files`. In tal modo nessun nuovo file Python può
sfuggire alla classificazione. Ricalcola poi `test` per `conftest.py` e
`tests/**`, `documentation` per `docs/**` e `productive` per ogni altro
percorso; la classe registrata deve coincidere. R1 analizza tutti e soli i
record `productive` così determinati.

#### 16.14.5 Raccolta ed esito delle celle

Il validatore raccoglie integralmente i due alberi A e richiede che ogni
`node_id` raccolto appartenga ad almeno un'attività e che ogni `node_id` del
manifesto venga realmente raccolto. Confronta insieme e ordine esatti; non
ammette prove aggiuntive, mancanti o non manifestate. Per l'attività corrente
invoca gli identificativi elencati e confronta di nuovo insieme e ordine
esatti.

Nei due alberi A sono vietati `skip`, `skipif`, `xfail`, `xpass` e selezioni
condizionali basate sulle capacità osservate. Il controllo combina analisi AST,
marcatori raccolti ed esiti pytest, così un alias o un marcatore dinamico non
aggira la regola. Ogni cella deve produrre esattamente un rapporto della fase
`call` con esito `passed`; un salto, esito atteso, successo inatteso, rapporto
mancante o rapporto aggiuntivo rende rossa l'attività.

Ogni attività emette un documento di evidenza canonico con esattamente
`schema_version=1`, `suite_id="rm-0008-increment-2a"`, `git_sha`,
`manifest_sha256`, `production_inventory_sha256`, `runner_image`, `activity` e
`results`. `git_sha` è il SHA Git completo di quaranta cifre esadecimali
minuscole; entrambi i digest usano `sha256:` seguito da sessantaquattro cifre
esadecimali minuscole;
`runner_image` è la stringa non vuota pubblicata dal runner. `results` segue
l'ordine delle celle dell'attività e contiene record esatti
`{"node_id":...,"outcome":"passed"}`. Il documento non contiene orari,
percorsi temporanei o altri valori non deterministici.

#### 16.14.6 Attività pubbliche bloccanti

Le sei attività di prova sono: validazione del manifesto, portabilità Ubuntu,
portabilità Windows, concorrenza Ubuntu, concorrenza Windows e ACL con identità
reali Windows. Il riepilogo di certificazione è un settimo job bloccante, ma
non è un'attività del manifesto. Nel medesimo workflow dichiara in `needs` i
sei job A e il job matrice storico `portable-contract-store`; viene eseguito con
`if: always()` ma termina con successo soltanto se tutti e sette i risultati
sono `success`. Scarica poi i sei documenti di evidenza e richiede per tutti lo
stesso `git_sha`, lo stesso `manifest_sha256`, lo stesso
`production_inventory_sha256`, lo stesso `suite_id`, attività distinte e
insieme delle attività esattamente uguale all'enumerazione chiusa.
Richiede inoltre che ogni risultato sia `passed` e che l'unione dei risultati
corrisponda al manifesto secondo le regole di duplicazione precedenti. Poiché i
job appartengono alla stessa esecuzione GitHub, il SHA del job storico coincide
con quello delle evidenze; il riepilogo lo confronta comunque con
`GITHUB_SHA`.

Il job storico conserva la regressione precedente ma, su entrambe le gambe
della matrice, invoca la suite portabile con l'esclusione letterale
`--ignore=tests/portable/rm0008_2a_acceptance`. Non usa una selezione calcolata e
non raccoglie alcun A: in particolare la gamba Windows non incontra gli A
posseduti soltanto da Linux. I due alberi A sono raccolti e partizionati
esclusivamente dai sei job dedicati. Questa esclusione di albero nel job
storico non è uno `skip` di cella e il riepilogo continua a richiedere sia la
regressione storica sia tutte le 248 celle A.

La diagnostica D resta fuori dal manifesto A e da ogni hook pytest. In
particolare `tests/windows_identity/conftest.py` non invoca alcun riproduttore
D al termine di una sessione. Un'eventuale nuova diagnostica manuale è un passo
esplicito che chiama direttamente lo strumento D ed è abilitato soltanto da un
input `workflow_dispatch` dedicato; nessuna attività A imposta o eredita tale
input. Il workflow ordinario storico continua a fornire la regressione
generale, ma non sostituisce alcuna attività A. L'incremento 2A converge
soltanto quando il workflow ordinario e tutti i sette job A bloccanti sono verdi
sul medesimo commit pubblico: in tale stato il numero di errori, salti e
risultati attesi nelle celle A è zero.
