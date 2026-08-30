# RM-0008 gruppo 6 — distribuzione e avvio F4

## 1. Stato di ingresso e risultato dell'analisi

Il gruppo 5 e' chiuso. Il commit pubblico `a5bd396` e il ciclo GitHub Actions
`33183713818` hanno concluso verdi tutti i nove lavori Linux e Windows. Nel
repository sorgente il punto di ingresso del gruppo 6 e' `225f9437`. La
politica compilata `closed_build_enforcement()` resta `False` e deve rimanere
falsa per tutto il gruppo 6.

Il gruppo 6 corrisponde al §23.6.6 della roadmap. Deve consegnare il percorso
produttivo che assembla e firma una distribuzione, la installa senza stati
parziali autorizzabili, costruisce e rilegge la catena richiesta, installa un
controllo preliminare dominante e porta il coordinatore fino a
`PREFLIGHT_VERIFIED` in un ambiente isolato.

Tre revisioni indipendenti del codice hanno raggiunto la stessa conclusione:
le primitive crittografiche e a sola aggiunta sono utilizzabili, ma oggi non
sono collegate in un percorso produttivo completo. La codifica non deve
iniziare tentando di riempire gli ultimi tre stati del journal: prima occorre
correggere quattro lacune strutturali.

1. Il verificatore della distribuzione non possiede un assemblatore o un
   installatore produttivo. Inoltre ammette esattamente un solo file con ruolo
   `service_unit`, mentre il prodotto installa piu' unita'.
2. La lettura a freddo della catena richiede oggetti `VerifiedDistribution`
   costruiti in memoria per tutte le versioni. Dopo un aggiornamento non e'
   possibile ricostruire l'oggetto della versione precedente confrontandolo
   con i file della versione corrente.
3. Le unita' correnti appartengono al gestore `systemd` dell'utente e sono
   modificabili dalla stessa identita' che esegue Metnos. Un
   `ExecStartPre` aggiunto a quelle unita' sarebbe aggirabile.
4. Il percorso produttivo del coordinatore termina a `RECEIPTS_COMPLETE`; la
   pubblicazione del certificato e' ancora una prova isolata e gli stati
   `BUILD_VERIFIED`, `HEAD_REQUIRED` e `PREFLIGHT_VERIFIED` non possiedono i
   campi e le riletture che dovrebbero attestare.

Queste lacune sono cause, non carenze da compensare con piu' test. Il gruppo e'
quindi diviso in quattro incrementi verticali. Ogni incremento risolve un
rischio distinto e possiede una sola famiglia di prove discriminanti.

## 2. Confine con il gruppo 7

Il gruppo 6 consegna codice produttivo e prove isolate fino a
`PREFLIGHT_VERIFIED`, ma non esegue il passaggio reale dell'installazione
operativa.

Restano esclusivamente nel gruppo 7:

- l'attivazione sul sistema reale della distribuzione separata con
  `closed_build_enforcement()` compilato a `True`;
- la disattivazione definitiva dei vecchi ingressi e l'avvio dello stack dalla
  sola distribuzione chiusa;
- il riavvio reale, il caricamento operativo a freddo, la ripetizione
  equivalente e i due cicli consecutivi di instradamento;
- la dichiarazione di completamento di F4.

Il gruppo 6 implementa l'installazione del piano amministrativo stabile, ma la
esegue soltanto nella VM usa-e-getta. Non modifica il server reale. Le unita'
candidate restano artefatti
firmati e vengono installate soltanto nella cella isolata con nomi gia'
non collidenti prima della firma. Installazione dei nomi reali e commutazione
dei servizi correnti appartengono al gruppo 7.

Finche' `closed_build_enforcement()` vale `False`, l'entrata produttiva di
preparazione nega prima di allocare la sequenza, pubblicare un claim o creare un
journal. Il gruppo 6 non puo' quindi prenotare sulle radici reali un candidato
che il successivo artefatto G7, con byte differenti, non potrebbe sostituire.
Sul server non e' ammessa alcuna mutazione G6. Piano amministrativo, claim,
`PREPARED` e stati successivi reali iniziano soltanto dal binario G7 col diniego
compilato vero; G6 prova
l'intera composizione esclusivamente nella cella isolata mediante una
capability privata che non entra nel grafo produttivo.

Il percorso produttivo sulle radici fisse puo' avanzare al massimo fino a
`RECEIPTS_COMPLETE`. Il gruppo 6 non espone una funzione pubblica che accetti
una attestazione di attivazione costruita in precedenza. Il gruppo 7 dovra'
aggiungere un unico involucro di completamento che, nella stessa chiamata e
senza rilasciare i blocchi di deployment, startup esclusivo e manutenzione:

1. installa e rilegge la topologia dominante;
2. neutralizza ogni `legacy_binding` in modo che l'identita' di servizio non
   possa ricrearlo e prova che non esistano ingressi legacy gia' in volo;
3. ricalcola dai byte correnti catalogo, topologia effettiva ed evidenza
   separata con `closed_build_enforcement()=True`;
4. costruisce la capacita' privata `_DominantStartupInstalledV1` legata a
   `request_id`, testa precedente, catalogo, topologia, evidenza di enforcement
   e alle tre sessioni di blocco vive;
5. consuma una sola volta la capacita' nello stesso stack, ripete subito le
   verifiche e soltanto allora oltrepassa `CERTIFICATE_READY`.

La capacita' non e' serializzabile e una ripresa ripete l'intero protocollo
sotto nuovi blocchi; non esiste un intervallo fra attestazione e consumo. Il
gruppo 6 usa soltanto la funzione privata
`_complete_ownership_cutover_isolated_v1()` con una
`_IsolatedActivationForTestV1`. La cella la ricostruisce, anche dopo un arresto,
da un descrittore di cella root-owned legato al medesimo `boot_id`, al
descrittore firmato, al prefisso isolato e alle sessioni di blocco vive. Questa
capacita' non e' accettata dalle radici produttive. Nessun booleano, protocollo
strutturale o oggetto fornito dal chiamante puo' sostituire le due capacita'.

## 3. Decisioni architetturali vincolanti

### 3.1 Piano amministrativo stabile

Il controllo preliminare non puo' essere importato dalla distribuzione che
deve ancora autenticare. Il gruppo introduce quindi un piano amministrativo
stabile, posseduto da `root`, esterno alle radici delle distribuzioni.

I percorsi produttivi sono fissi e non possono provenire da variabili
d'ambiente, argomenti, descrittori forniti dal chiamante o ricerca nel `PATH`:

- `/var/lib/metnos/executor-birth` per autorita', catena, journal e
  descrittori;
- `/var/lib/metnos/executor-birth/chain-v1` per `builds-v1`, `cutovers-v1`,
  `heads-v1` e `required-head-v1.bin`;
- `/var/lib/metnos/executor-birth/incoming-v1/sources-v1/<source-id>` per ogni
  sorgente candidata immutabile acquisita dall'installatore amministrativo;
- `/var/lib/metnos/executor-birth/releases-v1/<sequenza>` per gli alberi di
  distribuzione immutabili;
- `/usr/libexec/metnos/executor-birth-v1` per il lanciatore e il verificatore
  amministrativi;
- `/etc/systemd/system` per le unita' candidate del gestore di sistema.

Le directory create sotto la radice ownership e la radice delle distribuzioni
sono `root:root 0755` e ogni antenato viene verificato. `incoming-v1` viene
popolata soltanto dal comando amministrativo di ricezione, che copia una
sorgente scelta dall'amministratore in una directory temporanea, rifiuta link e
nomi non canonici, calcola `source_id` sui byte canonici e sull'identita' di
servizio, sincronizza e pubblica senza sovrascrittura la directory nominata da
quel digest. Una rilettura dello stesso identificativo deve essere
byte-identica. Sorgenti differenti convivono in directory differenti; la loro
raccolta non appartiene al gruppo 6. Il successivo assemblatore riceve soltanto
`source_id`, mai un percorso, e deriva la collocazione sotto la radice fissa.

Il piano amministrativo V1 e' un insieme chiuso di programmi Python che usano
soltanto la libreria standard. Viene eseguito esclusivamente come
`<python-canonico> -I -S /usr/libexec/metnos/executor-birth-v1/preflight.py`.
La verifica Ed25519 puo' invocare soltanto `<openssl-canonico>`, con argomenti
costruiti dal programma e senza usare shell o `PATH`. Interprete, OpenSSL e
programmi amministrativi devono essere file regolari posseduti da `root`, non
scrivibili dal gruppo, e i loro digest entrano nel prerequisito di avvio. Se
uno di questi requisiti manca, l'installazione si arresta; non esiste ripiego
sull'ambiente Python della distribuzione.

Il descrittore sostituisce i due nomi simbolici col percorso canonico riletto.
OpenSSL riceve soltanto una chiave pubblica Ed25519 in formato PEM
SubjectPublicKeyInfo ASCII, massimo 4 KiB, e viene eseguito come
`<openssl-canonico> pkeyutl -config /dev/null -provider default -propquery
provider=default -verify -pubin -inkey <chiave> -rawin -in <payload> -sigfile
<firma>`. I tre file temporanei sono creati con
`O_CREAT|O_EXCL|O_NOFOLLOW`, modo `0600`, in una directory root-owned `0700`
sotto `/run/metnos-executor-birth-v1`; dimensioni e identita' vengono
ricontrollate. Il processo usa stdin nullo, shell assente, timeout di cinque
secondi e ambiente esattamente `LC_ALL=C`, senza `PATH`, `HOME`, `OPENSSL_*`,
`LD_*`, `PYTHON*` o configurazione/provider forniti dall'esterno. Uscita,
timeout o segnale diversi dal successo chiuso sono un diniego; i temporanei
sono rimossi in `finally`.

Il profilo `ed25519-pkeyutl-v1` definisce il successo chiuso byte per byte:
codice di uscita `0`, `stdout` esattamente
`Signature Verified Successfully\n` e `stderr` esattamente
`Using configuration from /dev/null\n`. Quest'ultima riga e' l'uscita
informativa prodotta da OpenSSL 3.0.13 quando riceve l'opzione normativa
`-config /dev/null`; non viene scartata ne' interpretata liberamente. Ogni
byte mancante o aggiuntivo, ogni altra riga e ogni differenza nel codice di
uscita negano la verifica. Una futura versione di OpenSSL con un protocollo
di uscita differente richiede un nuovo profilo esplicito e certificato.

`stdout` e `stderr` sono drenati contemporaneamente da pipe non bloccanti e
limitati separatamente a 4 KiB; il superamento del limite equivale a un
diniego, seguito da terminazione e attesa obbligatoria del processo. La stessa
sequenza vale al timeout. Il cleanup tenta sempre, uno per uno, tutti e tre i
file conosciuti e la directory anche se una rimozione precedente fallisce; un
residuo impedisce il successo e viene trattato come recupero necessario al
successivo avvio. Un errore di cleanup non trasforma mai in successo l'errore
originario e non interrompe i tentativi sulle risorse successive.

`<payload>` non e' il JSON nudo: per il manifesto e' esattamente
`metnos.executor-birth.closed-build/v1\0 || manifest_bytes`, per il certificato
e' `metnos.executor-birth.ownership-cutover/v1\0 || certificate_bytes` e per la
testa e' `metnos.executor-birth.ownership-head/v1\0 || head_bytes`. La firma e'
sempre il file binario Ed25519 di 64 byte corrispondente. Ogni altro dominio,
framing o formato di chiave viene negato.

Le applicazioni Python non usano un ambiente virtuale mutabile: il catalogo
lega `python_executable`, le opzioni `-I -S -m`, il nome del modulo e gli
argomenti. Il
lanciatore amministrativo firmato contiene un ramo bootstrap interno: rilegge
testa e catalogo, verifica che la radice coincida esattamente con la sequenza
autenticata, applica gruppi/GID/UID, chiude esplicitamente il blocco startup,
conserva soltanto le directory della libreria standard root-owned gia' presenti
sotto `-I -S`, antepone quell'unica radice di distribuzione a `sys.path` e
imposta `sys.argv=[python_module, *target_args]`, quindi carica il modulo con
`runpy.run_module(python_module, run_name="__main__", alter_sys=False)` nello
stesso processo. `python_module` e' un campo firmato, rispetta la
grammatica chiusa definita nel §3.5.1 e viene risolto soltanto nella radice
autenticata; non esiste una trasformazione da percorso a modulo. Il lanciatore
non accetta modulo, percorso o radice dalla riga di comando. Moduli e dipendenze non appartenenti alla
libreria standard devono essere file dichiarati nella stessa distribuzione;
nessun `site-packages`, `PYTHONPATH` o percorso utente entra nel processo. Un
eseguibile non Python deve essere dichiarato nel manifesto e puo' caricare
soltanto dipendenze incluse nella distribuzione o librerie di sistema
root-owned non scrivibili dall'identita' di servizio.

La policy source-review ammette `runpy.run_module` soltanto per la singola
chiamata autenticata nello scope
`runtime/executor_birth_admin_preflight.py:_launch_python_target_v1`. La forma
AST deve essere esattamente
`runpy.run_module(plan.python_module, run_name="__main__", alter_sys=False)`,
con import letterale `runpy`, nessun alias, nessun argomento espanso, nessuna
keyword aggiuntiva e nessun rebinding di `runpy` o `plan`. L'eccezione riguarda
la chiamata, non l'intero scope: `run_path`, alias, reflection, una seconda
porta dinamica, un altro file o uno scope annidato restano negati. Guardia
canonica, clone autonomo e le due chiusure degli import applicano la medesima
regola e vengono provati in parita' con mutanti.

La riga di comando chiusa contiene soltanto:

- `check --entry-id <identificativo>` per il controllo preliminare del singolo
  servizio o ingresso amministrativo;
- `launch --entry-id <identificativo>` per una seconda verifica immediata e
  l'esecuzione del comando autenticato;
- `check-all` per la rilettura definitiva del coordinatore.

L'identificativo dell'ingresso e' scritto nell'unita' o nell'adattatore
posseduto da `root` e deve appartenere al catalogo firmato. Nessun comando
accetta percorsi, registri, chiavi, digest, radici o prove dal chiamante.
Tutti i comandi richiedono EUID zero. Le unita' antepongono `!` al comando
amministrativo: systemd non applica `User`, `Group` e
`SupplementaryGroups` al lanciatore, che parte come root e applica poi le
credenziali firmate, ma mantiene namespace, `NoNewPrivileges` e ogni altra
restrizione dell'unita'. `CapabilityBoundingSet` del lanciatore contiene
soltanto `CAP_SETUID`, `CAP_SETGID` e `CAP_SETPCAP`. Dopo
`setgroups`/`setgid`, ma prima del `setuid`, il lanciatore rimuove anche queste
tre capability dal bounding set; dopo il `setuid` azzera effective, permitted,
inheritable e ambient set, imposta `PR_SET_NO_NEW_PRIVS=1` e verifica in
`/proc/self/status` UID, GID, gruppi, `NoNewPrivs=1` e tutti i campi `Cap*` a
zero. Chiude ogni descrittore maggiore di 2, incluso il gate, prima di `execve`
o `runpy`; V1 non ammette socket activation o descrittori ereditati. Per unita'
`Type=notify` preserva soltanto le variabili dinamiche `NOTIFY_SOCKET`,
`WATCHDOG_USEC` e `WATCHDOG_PID` ricevute dal gestore, dopo grammatica chiusa e
solo se previste dalle direttive firmate; ogni altra variabile non firmata
viene eliminata. Il prefisso `+` e' vietato. Un'invocazione diretta
dall'identita' applicativa viene negata.

### 3.2 Distribuzioni immutabili e selezione atomica

Una distribuzione viene costruita in una directory temporanea sullo stesso
filesystem della destinazione. Il percorso finale usa la sequenza a venti
cifre, gia' allocata sotto il blocco di deployment. La pubblicazione della
directory finale e' senza sovrascrittura, dopo sincronizzazione dal basso verso
l'alto. Una directory finale gia' presente e byte-identica rende il tentativo
idempotente; ogni differenza e' un conflitto.

Le tre transazioni di directory usano lo stesso protocollo chiuso.

- Il ricevitore crea `incoming-v1/.receive-<nonce-128-bit>.tmp` con creazione
  esclusiva e modo `0700`; dopo il calcolo dell'identita' la rinomina in
  `incoming-v1/sources-v1/.<source-id>.tmp` senza sostituire nulla e infine nel
  nome finale `<source-id>`.
- L'assemblatore usa
  `releases-v1/.staged-<release-sequence-020d>-<closed-build-hex>.tmp`, dove
  l'identificativo e' privo del prefisso `sha256:`. G6-B3 crea o adotta e
  rilegge soltanto questo staging; il solo nucleo G6-B4, invocabile in futuro
  con l'autorizzazione distinta composta da G6-D, pubblica nel nome finale
  della sequenza con rename senza sovrascrittura.
- L'installatore amministrativo usa il fratello
  `/usr/libexec/metnos/.executor-birth-v1-<administrative-bundle-hash>.tmp` e
  pubblica l'intera directory nel nome fisso `executor-birth-v1`; non installa
  file singoli visibili prima del rename.

In ogni caso scrive con handle esclusivi senza link, sincronizza ciascun file
dal basso, rilegge identita' e byte, sincronizza ogni sottodirectory, rinomina
senza sovrascrittura e sincronizza la directory padre. Dopo un arresto, un
temporaneo con nome strutturato viene adottato soltanto se proprietario, modo,
schema, inventario e identita' content-addressed coincidono integralmente; se il
finale esiste deve essere byte-identico. Zero byte, scrittura intermedia,
identita' discordante, due temporanei validi diversi o un nome non strutturato
producono `birth_ownership_recovery_required`; non si sceglie per data. I
`.receive-*` senza identita' non vengono adottati: in una radice altrimenti
valida sono spostati dall'amministratore in quarantena prima di un nuovo
tentativo.

Non si scambia in posizione l'intera `/opt/metnos`. Le unita' stabili invocano
il lanciatore amministrativo, che risolve il comando esatto dalla testa
richiesta autenticata. `required-head-v1.bin` e' quindi l'unico selettore
atomico della distribuzione. Prima della sua sostituzione nessun percorso
considera richiesta la nuova distribuzione; dopo la sostituzione nessun
percorso puo' scegliere la precedente.

La distribuzione pubblicata e' posseduta da `root`, non e' scrivibile dal gruppo o
dall'identita' di servizio e non contiene link, reparse point, hard link
inaspettati, bytecode o file non dichiarati.

La verifica dell'albero usa un trie costruito esclusivamente dai percorsi del
manifesto autenticato. Le foglie sono tutti e soli i file dichiarati; le sole
directory ammesse sono i loro prefissi propri. Un file che e' anche prefisso
di un altro percorso, un componente `__pycache__` o un file con suffisso
`.pyc` o `.pyo` rendono invalido il manifesto, anche se firmato. Durante
l'enumerazione ogni nome deve essere un figlio atteso del nodo corrente: un
nome extra viene negato nel genitore senza aprirlo o attraversarlo.

Il verificatore mantiene aperto l'handle della radice e produce una fotografia
A completa di nomi, tipi, identita' e metadati prima di leggere i byte. Ogni
figlio viene aperto relativamente al parent con no-follow e tipizzato
dall'handle; i file richiedono un solo link. La lettura e l'hash usano la
stessa catena di handle e confrontano l'identita' prima e dopo. Dopo le
verifiche semantiche viene prodotta, dallo stesso handle di radice, una
fotografia B: A e B devono coincidere integralmente e il nome produttivo della
radice deve ancora identificare l'handle mantenuto aperto. Su POSIX si usano
descriptor relativi e `O_NOFOLLOW`; su Windows il loader usa l'oracolo nativo
handle-bound gia' certificato, con enumerazione per file ID e apertura relativa
che nega reparse point e oggetti delete-pending. Il programma amministrativo
produttivo resta Linux-only; su Windows ne vengono provati soltanto codec e
diniego prima di I/O.

Il limite esplicito del modello e' un attore `root` che muti e ripristini
l'intero albero fra tutte le osservazioni; viene escluso dal blocco di
deployment e dalla catena di proprieta'. Un successo garantisce invece che la
fotografia A, i byte letti e la fotografia B descrivano la stessa release e
che nessuna mutazione persistente o osservata durante la lettura sia ammessa.

### 3.3 Descrittore del predecessore transitorio

Prima di installare il controllo preliminare viene creato una sola volta un
descrittore canonico `predecessor-v1`. Il descrittore e' posseduto da `root`,
pubblicato senza sovrascrittura e contiene:

- identificativo e percorso assoluto dell'unica distribuzione precedente ammessa;
- inventario completo dei file realmente caricabili da ogni ingresso
  protetto, con dimensione e digest;
- catalogo dei comandi effettivi dei servizi e dell'installatore;
- digest del piano amministrativo e delle unita' transitorie;
- identificativo della transazione che ha acquisito la fotografia sotto
  manutenzione.

La radice precedente viene verificata contro l'inventario ad ogni avvio
transitorio. Il possesso `root` del solo descrittore non rende affidabili file
mutabili: i file vivi devono coincidere con la fotografia. Il descrittore non
e' un secondo puntatore e non viene aggiornato.

Prima della pubblicazione del certificato, il controllo preliminare ammette
soltanto quel predecessore esatto. Dopo la pubblicazione del certificato il
ramo transitorio e' irraggiungibile per sempre. Certificato cancellato,
incompleto o corrotto non riabilita il predecessore.

### 3.4 Un solo catalogo dei servizi e degli ingressi

Un modulo dichiarativo chiuso diventa l'unica fonte per:

- rendering e installazione delle unita';
- prova canonica di manutenzione;
- inventario firmato della distribuzione;
- verifica della copertura del controllo preliminare;
- test di assenza di ingressi non classificati.

Il catalogo distingue sei classi.

1. `gated_service`: ogni servizio `metnos-*` avviabile direttamente, compresi
   HTTP, worker durevole, traduttore, prontezza, watchdog, Telegram,
   Playwright, display, LLM, SearXNG e Photon. Ognuno attraversa il medesimo
   controllo prima di eseguire il proprio comando.
2. `gated_timer`: i timer del traduttore e del watchdog. Il timer puo' restare
   privo di comando, ma deve attivare esclusivamente un servizio gia'
   controllato.
3. `stop_only`: `metnos-stack-quarantine.service`. Deve restare avviabile
   quando il controllo nega lo stack, ma il suo corpo e l'elenco delle unita'
   arrestate sono posseduti da `root` e inclusi nella firma.
4. `target`: il solo raggruppamento `metnos.target`; non esegue un comando e
   non sostituisce il controllo dei servizi attivabili direttamente.
5. `external_dependency`: servizi di terze parti che non importano e non
   eseguono la distribuzione Metnos. La classificazione e' esplicita; una
   dipendenza esterna non puo' rendere positiva la prontezza se il proprietario
   F4 e' negato.
6. `gated_entrypoint`: ogni ingresso amministrativo Metnos che puo' modificare
   repository, ambiente Python, dipendenze, unita' o servizi. Nel gruppo 6 il
   catalogo lega il percorso storico censito a un adattatore candidato
   inattivo; l'adattatore attraversa lo stesso controllo prima di eseguire il
   comando autenticato. La sostituzione dell'ingresso storico resta al gruppo
   7.

`metnos.target` non sostituisce il controllo per singolo servizio, perche' ogni
servizio puo' essere avviato direttamente. Le vecchie unita' in
`~/.config/systemd/user` non sono autorita'. Le unita' candidate sono unita' di
sistema possedute da `root` e mantengono `User=<identita-servizio>` per il
processo applicativo.

Gli ingressi storici in `install/`, `deploy/`, `runtime/playwright_sidecar` e
negli script di migrazione vengono censiti nello stesso catalogo. Il gruppo 6
puo' costruire adattatori candidati inattivi e provare il loro diniego prima di
ogni mutazione. Il ritiro, la sostituzione o la disabilitazione degli ingressi
reali appartengono al gruppo 7. Dopo quel passaggio nessun ingresso potra'
modificare repository, ambiente Python, dipendenze, unita' o servizi prima
dell'autenticazione.

### 3.5 Formati normativi nuovi

Tutti i documenti seguenti sono JSON ASCII canonici: chiavi ordinate,
separatori senza spazi, nessun duplicato, nessun numero in virgola mobile,
nessun valore non finito e nessun campo aggiuntivo. Gli interi non ammettono
booleani. Ogni digest usa la forma `sha256:<64 cifre esadecimali minuscole>`.
Percorsi relativi e assoluti usano le stesse regole chiuse gia' applicate dal
manifesto della distribuzione. Ogni percorso relativo contiene al massimo 32
componenti. Questo limite vale per ogni campo relativo dei formati nuovi:
file, `boundary_inventory_path`, `preflight_entrypoint`, locator relativi del
catalogo e percorsi relativi dei descrittori definiti nei §§3.5.2-3.5.4. Il
manifesto applica la stessa soglia; 32 componenti sono ammessi e 33 sono
rifiutati. Ogni caricatore limita la dimensione prima di decodificare.

#### 3.5.0 Descrittore della sorgente ricevuta

Ogni directory content-addressed contiene `received-source-v1.json`, limite
16 MiB, con esattamente:

```text
schema_version, source_id, service_user, files
```

`schema_version` vale l'intero `1`; `service_user` rispetta i vincoli del
descrittore di installazione. `files` contiene da uno a 20.000 elementi,
ordinati per i byte UTF-8 del percorso, ciascuno con esattamente `path`, `size`,
`content_hash`, `mode`. Il totale non supera 2 GiB. I percorsi sono relativi
canonici; dimensione, digest e modo (`420` o `493`) coincidono con file regolari
copiati senza seguire link. I percorsi dei file inducono al massimo 20.000
distinti percorsi-antenato propri: la radice content-addressed e
`received-source-v1.json` non sono contati. Ogni directory deve essere un
antenato di almeno un file; le directory vuote sono rifiutate. `content_hash` usa il dominio
`metnos.executor-birth.received-source-file/v1\0` con percorso e dimensione
incorniciati prima dei byte. `source_id` usa il dominio
`metnos.executor-birth.received-source/v1\0` sul documento senza quel campo.
Il descrittore non elenca se stesso. Directory e file sono `root:root`, non
scrivibili da gruppo o altri, e vengono riletti integralmente a ogni uso.

#### 3.5.1 Catalogo dei servizi

Il file firmato e'
`deployment/executor-birth-service-catalog-v1.json`, con limite di 256 KiB.
Contiene esattamente:

```text
schema_version, catalog_id, entries, legacy_bindings
```

`schema_version` vale l'intero `1`. `entries` e' una lista ordinata per i byte
UTF-8 di `entry_id`; gli identificativi sono univoci e rispettano
`[a-z0-9][a-z0-9-]{0,63}`. Ogni elemento contiene esattamente:

```text
entry_id, unit_name, external_unit_name, adapter_path, class, scope,
execution_kind, target_executable, target_executable_hash, python_module,
target_args, target_working_directory, target_environment, timer_target, unit_spec,
requires_preflight, readiness_owner
```

`legacy_bindings` e' una lista ordinata per `legacy_id`, senza duplicati. Ogni
elemento contiene esattamente:

```text
legacy_id, entry_id, kind, scope, locator, disposition
```

`legacy_id` rispetta la stessa grammatica di `entry_id`; `entry_id` deve
esistere in `entries`. `kind` appartiene a `user_unit`, `system_unit`, `script`,
`python_module`, `powershell`; `scope` appartiene a `user`, `system`,
`repository`, `installed`. Per le unita' `locator` e' un nome valido di unita';
negli altri casi e' un percorso canonico relativo alla sorgente ricevuta o un
percorso installato assoluto. `disposition` vale sempre
`retire_in_group7`. Il gruppo 6 censisce e prova l'adattatore, ma non modifica
il binding reale.

`class` appartiene all'insieme chiuso `gated_service`, `gated_timer`,
`stop_only`, `target`, `external_dependency` e `gated_entrypoint`; `scope`
appartiene a `system`, `external` e `administrative`. `execution_kind`
appartiene a `none|python_module|native_executable|systemctl_stop`.
`target_args` e' sempre una lista di massimo 28 stringhe, ciascuna senza NUL e
di massimo 4.096 byte UTF-8.
`target_executable` e `target_executable_hash` sono nulli se e solo se
`execution_kind=none`; altrimenti il primo e' il percorso canonico assoluto e il
secondo e' il digest con dominio
`metnos.executor-birth.target-executable/v1\0`, percorso canonico incorniciato,
dimensione e byte dell'eseguibile. Il
Se l'eseguibile appartiene alla distribuzione, il caricatore ricalcola il
digest target sul percorso assoluto. Deriva poi il percorso relativo con
`target_executable.relative_to(installation_root)` dopo la verifica canonica e,
separatamente su quel percorso relativo, dimensione e stessi byte,
pretende il `content_hash` col dominio
`metnos.executor-birth.closed-build-file/v1\0` della voce del
manifesto; i digest con domini diversi non vengono confrontati. Per Python o
`systemctl` il caricatore ricalcola il digest target e, separatamente sugli stessi percorso e byte,
pretende l'hash col dominio amministrativo presente nel prerequisito; i due
digest non vengono mai confrontati fra loro. Un altro eseguibile di sistema
deve essere root-owned, regolare, senza
catena risolvibile in una directory scrivibile dall'identita' di servizio.
Eseguibili sotto home, dati utente o ambienti virtuali esterni sono vietati.
`target_environment` e' una lista ordinata di oggetti con le sole chiavi `name`
e `value`; non esiste un campo per file di ambiente esterni.
`timer_target` e' nullo salvo per `gated_timer`. `adapter_path` e' nullo salvo
per `gated_entrypoint`; in quella classe coincide esattamente con
`/usr/libexec/metnos/executor-birth-v1/preflight.py`, invocato con `launch
--entry-id`; non esiste un secondo adattatore generato.

Un `gated_service` ha `requires_preflight=true`, esecuzione non `none` e unita'
nel gestore di sistema. Un `gated_entrypoint` ha `requires_preflight=true`,
esecuzione non `none`, `unit_name=null`, `scope=administrative` e non puo' essere
`readiness_owner`. Un `gated_timer`, `stop_only`, `target` o
`external_dependency` ha `requires_preflight=false`. Un temporizzatore puo'
indicare soltanto l'`entry_id` di un `gated_service`; `stop_only` puo' eseguire
soltanto il comando chiuso di arresto; una dipendenza esterna non puo' essere
`readiness_owner`. Per tutte le classi diverse da `gated_entrypoint`,
`adapter_path` e' nullo.

Il lanciatore richiede sempre EUID zero. Per un `gated_service`, dopo l'ultima
verifica applica nell'ordine gruppi supplementari, GID e UID numerici firmati,
directory, ambiente minimo e `umask`, verifica che l'identita' effettiva sia
quella attesa e soltanto allora esegue il target. `gated_entrypoint` e
`stop_only` restano root. Nessun ingresso e' quindi eseguibile dall'identita'
applicativa anche conoscendone `entry_id`.

`catalog_id` e' il digest del documento senza `catalog_id`, preceduto dal
dominio `metnos.executor-birth.service-catalog/v1\0`. Il digest di copertura e'
calcolato sui byte completi con il dominio
`metnos.executor-birth.service-coverage/v1\0`.

La matrice dei campi e' chiusa. `unit_name` e' una stringa non vuota, senza
slash o NUL, massimo 192 byte UTF-8 e conforme a
`[A-Za-z0-9][A-Za-z0-9_.@-]*\.(service|timer|target)`, soltanto per
`gated_service`, `gated_timer`, `stop_only` e `target`; il suffisso e'
rispettivamente `service`, `timer`, `service` e `target`, ed e' nullo nelle
altre classi.
`external_unit_name` e' un nome di unita' non nullo soltanto per
`external_dependency` ed e' nullo nelle altre classi; usa la stessa grammatica
di `unit_name`.

`unit_spec` e' nullo per `gated_entrypoint` ed `external_dependency`. Per ogni
altra classe contiene esattamente `fragment_hash` e `directives`.
`fragment_hash` usa il dominio
`metnos.executor-birth.systemd-fragment/v1\0` seguito da
`u64be(len(unit_name_utf8)) || unit_name_utf8 || u64be(len(fragment_bytes)) ||
fragment_bytes` ed e' anche legato alla corrispondente voce `service_unit` del
manifesto. `directives` e' una lista nell'ordine canonico sezione/nome; ogni
elemento contiene esattamente `section`, `name`, `value_type`, `values`.

`section` appartiene a `Unit|Service|Timer|Install`. `name` appartiene alla
allowlist chiusa `Description|Documentation|DefaultDependencies|Requires|Wants|
BindsTo|After|Before|PartOf|OnFailure|StartLimitIntervalSec|StartLimitBurst|
Type|User|Group|SupplementaryGroups|ExecStartPre|ExecStart|ExecStop|Restart|
RestartSec|TimeoutStartSec|TimeoutStopSec|WorkingDirectory|Environment|
NoNewPrivileges|PrivateTmp|ProtectSystem|ProtectHome|ReadWritePaths|
CapabilityBoundingSet|AmbientCapabilities|KillMode|KillSignal|SuccessExitStatus|
RemainAfterExit|UMask|NotifyAccess|WatchdogSec|Delegate|DelegateSubgroup|
ProtectKernelTunables|ProtectKernelModules|ProtectControlGroups|
RestrictNamespaces|RestrictRealtime|RestrictAddressFamilies|LockPersonality|
MemoryDenyWriteExecute|SystemCallArchitectures|MemoryAccounting|MemoryHigh|
MemoryMax|TasksAccounting|TasksMax|Nice|
LimitNOFILE|StandardOutput|StandardError|SyslogIdentifier|OnBootSec|
OnActiveSec|OnUnitActiveSec|OnCalendar|RandomizedDelaySec|Persistent|
AccuracySec|Unit|WantedBy|RequiredBy`. `value_type` appartiene a
`scalar|boolean|duration|integer|argv|environment|unit_list|path_list` ed e'
fissato per ciascun nome dal codec, non dal documento. `values` e' una lista
non vuota di stringhe canoniche del tipo; liste e relazioni sono ordinate e
senza duplicati. Sezione errata, direttiva sconosciuta, ripetizione non ammessa
o valore non canonico invalidano il catalogo.

La compilazione esegue inoltre un inventario meccanico di tutte le direttive
nei template e nelle unita' Metnos correnti. Ogni nome deve comparire nella
allowlist con sezione, tipo e decisione normativa espliciti; una direttiva
rimossa dal nuovo modello richiede una regola di migrazione e una prova
funzionale, non puo' scomparire come effetto implicito del renderer.

Il renderer usa soltanto `unit_spec` e non una seconda tabella. Dopo il
rendering un parser indipendente rilegge l'intero frammento, rifiuta righe,
sezioni, reset o specificatori non rappresentati e ricostruisce lo stesso
`unit_spec`. Comandi, utente, ambiente, timer e relazioni duplicati nei campi
semantici dell'entry seguono una mappa chiusa, non un'uguaglianza testuale.
Per un `gated_service`, `target_executable`, `python_module`, `target_args`,
`target_working_directory` e `target_environment` descrivono il target
applicativo eseguito internamente dal lanciatore; non vengono copiati
in `ExecStart`, `WorkingDirectory` o `Environment` dell'unita'. L'unita' usa
invece `WorkingDirectory=/`, ambiente vuoto e i soli `ExecStartPre`/`ExecStart`
amministrativi derivati da `entry_id`. `User`, `Group` e
`SupplementaryGroups` coincidono col descrittore firmato e vengono applicati al
target dal lanciatore a causa del prefisso `!`. Questa separazione rende i byte
delle unita' indipendenti dai contenuti della distribuzione e quindi identici
fra release V1.
Ogni `gated_service` usa inoltre `KillMode=control-group`; una configurazione
che possa lasciare discendenti fuori dallo stop e dal censimento e' vietata.
Le prime quattro classi hanno `scope=system`, `external_dependency` ha
`scope=external` e `gated_entrypoint` ha `scope=administrative`; nessun'altra
combinazione e' valida.
`target_working_directory` e' un percorso assoluto non nullo soltanto nelle
classi con esecuzione (`gated_service`, `gated_entrypoint`, `stop_only`) ed e'
nullo altrove. Per servizi e ingressi deve appartenere alla distribuzione
richiesta; per `stop_only` vale esattamente `/`. `target_environment` e' sempre
una lista, ordinata per `name`, senza
duplicati; nome e valore sono stringhe, il nome rispetta
`[A-Z_][A-Z0-9_]{0,127}` e il valore non contiene NUL e non supera 16 KiB.
Sono vietati `PATH`, `HOME`, `SHELL`, `VIRTUAL_ENV`, ogni nome con prefisso
`PYTHON`, `LD_`, `DYLD_`, `OPENSSL_` e ogni variabile capace di scegliere
radice, interprete o configurazione del controllo. Nessun file esterno puo'
cambiare il comando autenticato. `readiness_owner` e' un booleano; esattamente un
`gated_service` lo ha vero e ogni altra voce lo ha falso.

Un `gated_timer` ha obbligatoriamente `timer_target` non nullo ed
`execution_kind=none`;
ogni altra classe ha `timer_target=null`. `target` ed
`external_dependency` hanno `execution_kind=none`. Con
`execution_kind=python_module`, `target_executable` e' esattamente il Python
firmato, `python_module` e' non nullo e `target_args` contiene i soli argomenti
del modulo. Con `native_executable`, `python_module` e' nullo e l'eseguibile e'
dichiarato nella distribuzione oppure e' un eseguibile di sistema coperto da
`target_executable_hash`. Il nome modulo rispetta
`[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,31}`, ha massimo 255 byte,
non e' un namespace package e la sua `ModuleSpec` e l'intera chiusura di import
si risolvono sotto l'unica radice della distribuzione. `python_module` e' nullo
per gli altri tre tipi; con `none`, anche `target_args` e
`target_environment` sono vuoti e `target_working_directory` e' nullo. Per il
target nativo `execve` riceve esattamente
`[target_executable, *target_args]`; per Python il contratto di `sys.argv` e'
quello del §3.1. La sola
eccezione e' `stop_only`, che ha `execution_kind=systemctl_stop`, eseguibile
uguale al `systemctl` canonico e `target_args=["stop", ...unita-ordinate...]`.
Servizi
controllati e adattatori non eseguono direttamente questi target: invocano il
programma amministrativo con `launch --entry-id`, che rilegge la testa e poi
esegue il target autenticato. `stop_only` esegue invece il solo comando chiuso
appena definito. Una violazione di tipo, nullabilita', cardinalita' o relazione
fra campi rende invalido l'intero catalogo.

#### 3.5.2 Descrittore di installazione

Il file firmato e'
`deployment/executor-birth-deployment-v1.json`, con limite di 1 MiB. Contiene
esattamente:

```text
schema_version, descriptor_id, release_sequence, installation_root,
service_user, service_uid, service_gid, service_supplementary_gids,
service_home, service_shell,
administrative_root, system_unit_root, artifacts,
service_catalog_id, service_coverage_hash, python_executable,
openssl_executable, systemctl_executable, systemd_analyze_executable
```

Le tre radici devono coincidere rispettivamente con il percorso fisso della
sequenza sotto `releases-v1`, `/usr/libexec/metnos/executor-birth-v1` e
`/etc/systemd/system`. `service_user` e' un nome POSIX semplice, acquisito
dall'installatore amministrativo e firmato; non proviene da una richiesta di
avvio. Deve risolversi tramite il registro locale a un utente esistente con UID
e GID primario diversi da zero. Nome, UID e GID vengono riletti immediatamente
prima dell'installazione; `root`, un account assente o una risoluzione diversa
sono rifiutati. Sulla destinazione produttiva deve essere un account dedicato
senza login, senza lingering o user manager attivo e senza scrittura in alcuna
radice di ricerca delle unita' utente; dati e cache scrivibili vivono soltanto
nelle directory esplicitamente assegnate dal descrittore. La migrazione
dall'eventuale account umano corrente e il ritiro delle sue unita' appartengono
al gruppo 7. `service_home` e `service_shell` sono i percorsi assoluti riletti
dal registro; la shell appartiene alla lista chiusa `nologin|false` risolta ai
percorsi root-owned della piattaforma. `service_uid` e `service_gid` sono quegli interi positivi;
`service_supplementary_gids` e' una lista ordinata, senza duplicati, di interi
positivi risolta nello stesso momento. Il lanciatore rifiuta qualsiasi
divergenza successiva.
`service_home` non puo' coincidere con `installation_root` ne' esserne un
discendente: l'albero della release e' immutabile e posseduto da `root`, mentre
home, dati e cache assegnati al servizio devono restare esterni a quell'albero.

I quattro percorsi di eseguibile sono assoluti. L'installatore parte dai nomi
fissi `/usr/bin/python3`, `/usr/bin/openssl`, `/usr/bin/systemctl` e
`/usr/bin/systemd-analyze`, segue al massimo otto collegamenti simbolici
assoluti o relativi senza uscire da directory root-owned non scrivibili e
registra i quattro file regolari canonici
finali. Le
unita' usano `python_executable`, non il nome simbolico. Catena, file finale e
dipendenze caricate appartengono a `root` e non sono scrivibili da gruppo o
altri; percorsi e digest vengono riletti nel prerequisito.

`artifacts` e' ordinato per il percorso di destinazione e ogni elemento
contiene esattamente:

```text
source_path, destination_path, kind, install_phase, size, content_hash,
mode, uid, gid
```

`source_path` e' relativo alla distribuzione. `destination_path` e' assoluto e
appartiene a una delle due radici amministrative fisse. `kind` appartiene a
`administrative_program`, `service_unit`, `timer_unit`,
`target_unit`, `stop_only_unit`. `install_phase` appartiene a `group6_admin` e
`group7_cutover`: soltanto i programmi amministrativi usano la prima; tutte le
unita' usano la seconda. `uid` e `gid` valgono zero; `mode` vale
decimalmente `493` per i programmi e `420` per documenti e unita'. Dimensione
e digest devono coincidere con la copia firmata nella distribuzione.

Il descrittore non elenca se stesso. Il manifesto ne firma i byte. Nel gruppo
6 l'installatore copia e rilegge soltanto gli artefatti `group6_admin`; le
unita' `group7_cutover` restano byte firmati nella distribuzione e non vengono
scritte nei nomi reali. Il gruppo 7 usera' lo stesso descrittore per copiarle
prima del passaggio reale. Il caricatore confronta direttamente l'originale
firmato con ogni destinazione gia' installata.
`descriptor_id` usa il dominio
`metnos.executor-birth.deployment-descriptor/v1\0` sul documento senza quel
campo.

Questa e' la strategia per le radici multiple: il manifesto continua ad avere
una sola `installation_root`; programmi, catalogo e unita' compaiono come copie
canoniche sotto `deployment/` nella distribuzione. Il descrittore firmato lega
ogni copia alla destinazione posseduta da `root`, che viene verificata byte per
byte dopo l'installazione. Il catalogo dei servizi resta nella distribuzione
richiesta e viene caricato da li'; non esiste una copia corrente separata.

Il piano amministrativo e le unita' del protocollo V1 sono immutabili fra
distribuzioni: una distribuzione successiva deve contenere gli stessi byte e lo
stesso `administrative_bundle_hash`. Cambiarli richiede un protocollo V2
esplicito e non viene trattato come un normale aggiornamento G6.

#### 3.5.3 Descrittore del predecessore

Il file amministrativo e' `predecessor-v1.json`, con limite di 16 MiB, modo
`0644`, proprietario e gruppo `root`, pubblicato senza sovrascrittura nella
radice ownership. Contiene esattamente:

```text
schema_version, predecessor_id, transaction_id, installation_root,
files, service_commands, administrative_bundle_hash,
service_catalog_id, service_coverage_hash
```

`files` e' una lista ordinata di oggetti con esattamente `path`, `size` e
`content_hash`; copre l'intera chiusura di importazione e tutti i file
eseguibili dagli ingressi censiti. `service_commands` e' una lista ordinata di
oggetti con esattamente `entry_id`, `execution_kind`, `target_executable`,
`target_executable_hash`, `python_module`, `target_args`,
`target_working_directory` e `target_environment`, con gli stessi limiti del
catalogo.
`transaction_id` e' un digest del journal che ha acquisito la fotografia sotto
manutenzione: coincide esattamente con `install_transaction_id` del record V2
`PREPARED` riletto dalla directory della stessa richiesta. Non e' il digest di
un record scelto dal chiamante. Non esistono data, ora o campi liberi.

`predecessor_id` usa il dominio
`metnos.executor-birth.predecessor-descriptor/v1\0` sul documento senza quel
campo. Il documento non viene firmato con la chiave di distribuzione, per non
aggiungerle un secondo scopo: la sua autorita' deriva dalla catena di directory
possedute da `root` e dalla verifica integrale dei file vivi.

#### 3.5.4 Prerequisito di avvio

Il file amministrativo e'
`startup-prerequisites-v1/<request-id>.json`, con limite di 256 KiB, modo
`0644` e pubblicazione senza sovrascrittura. Contiene esattamente:

```text
schema_version, prerequisite_id, request_id, closed_build_id,
release_sequence, deployment_descriptor_id, predecessor_id,
administrative_bundle_hash, python_binary_hash, openssl_binary_hash,
openssl_tcb_hash, systemctl_binary_hash, systemd_analyze_binary_hash, service_catalog_id,
service_coverage_hash, systemd_manager_version, candidate_units_hash,
effective_units_hash
```

`predecessor_id` e' obbligatorio nel primo passaggio e negli aggiornamenti lega
sempre lo stesso descrittore iniziale; non autorizza il ritorno a quel
predecessore. `request_id`, `closed_build_id` e `release_sequence` devono
coincidere con la transazione. `candidate_units_hash` usa il dominio
`metnos.executor-birth.candidate-units/v1\0` sul documento canonico con
esattamente `schema_version=1` ed `entries`. Ogni elemento, ordinato per
`entry_id`, copre esattamente le voci con `unit_spec` non nullo e contiene
esattamente `entry_id`, `unit_name`, `fragment_hash`, `directives` ed
`enablement_links`. `directives` e' la proiezione canonica completa di
`unit_spec`; `enablement_links` e' la lista ordinata dei link assoluti che il
descrittore G7 dovra' installare, ciascuno con il solo `path` e il bersaglio
relativo esatto. Valori, frammento e link candidati derivano dal catalogo e dai
byte firmati, non dal testo libero di `systemctl`.
Per ogni `gated_service`, `ExecStartPre` e' esattamente
`!<python-canonico> -I -S <preflight.py> check --entry-id <entry-id>` e
`ExecStart` e' esattamente lo stesso prefisso privilegiato con `launch`; per
tutte le altre classi `ExecStartPre` e' assente. `ExecCondition` e il prefisso
`+` sono vietati, perche' il primo non ha la semantica di errore richiesta e il
secondo disattiverebbe le restrizioni dell'unita'.
Per `stop_only`, `exec_start` e' direttamente il comando `systemctl stop`
chiuso del catalogo: non invoca `check` o `launch`, non legge la catena e resta
quindi disponibile anche durante un diniego totale. Il frammento e la lista
sono comunque byte firmati e root-owned.
Prima della prenotazione l'installatore copia i frammenti in una radice di
validazione privata derivata dalla capacita' preparata ed esegue soltanto
`<systemd-analyze-canonico> --root=<radice-validazione> verify <unita-ordinate>`.
La lista delle unita' deriva dal descrittore, non dal chiamante. Il processo usa
shell assente, stdin nullo, `LC_ALL=C`, nessun `PATH` e timeout di trenta
secondi; soltanto uscita zero e assenza di riferimenti Metnos non catalogati
sono accettate. La radice viene rimossa in `finally`.

I quattro hash degli eseguibili usano il dominio
`metnos.executor-birth.administrative-executable/v1\0` seguito da
`u64be(len(path_utf8)) || path_utf8 || u64be(size) || file_bytes`. Prima e dopo
la lettura vengono riverificati file canonico, catena simbolica, proprietario e
modo. Le librerie dinamiche del sistema sono parte della base fidata del
sistema operativo: devono essere root-owned e non scrivibili dal servizio e
non possono essere deviate tramite ambiente; non sono dichiarate come file
della distribuzione.

`openssl_tcb_hash` usa il dominio
`metnos.executor-birth.openssl-tcb/v1\0` sul documento canonico con esattamente
`schema_version`, `command_profile`, `config_path`, `provider`, `elf_loader`,
`module_directory`, `files`. I primi quattro valori fissi sono rispettivamente
`1`, `ed25519-pkeyutl-v1`, `/dev/null` e `default`. Il raccoglitore stdlib legge
direttamente l'intestazione ELF di OpenSSL con `struct`, accetta soltanto ELF64
dell'architettura firmata ed estrae l'unico `PT_INTERP` assoluto. Esegue poi
esattamente quell'interprete root-owned con `--list <openssl-canonico>`, ambiente
`LC_ALL=C`, shell assente e timeout, e accetta soltanto righe nella grammatica
chiusa `nome => percorso (indirizzo)` oppure `percorso (indirizzo)`. L'unica
eccezione senza file e' il nome vDSO previsto dalla tabella chiusa
dell'architettura (`linux-vdso.so.1` sulle piattaforme G6), che viene
riconosciuto ma non inserito in `files`. Ogni altro percorso viene risolto con
la stessa catena autenticata di massimo otto link usata dal descrittore; viene
registrato il file finale canonico e la deduplicazione avviene dopo questa
risoluzione, cosi' `/lib` e `/usr/lib` usr-merged non divergono. Percorsi
mancanti o non assoluti negano; alias dello stesso file canonico vengono
registrati una volta, mentre lo stesso nome di libreria risolto a file canonici
diversi nega. `elf_loader` registra sempre il percorso finale canonico del
`PT_INTERP`, non il nome letterale incontrato nell'ELF.

Il comando chiuso `<openssl-canonico> version -m` deve produrre una sola riga
`MODULESDIR: "<percorso-assoluto>"` e fornisce l'unica directory dei moduli. La
directory viene risolta con la stessa catena limitata e
`module_directory` registra il percorso finale canonico. Il
raccoglitore include in `files` OpenSSL, interprete ELF,
intera chiusura restituita da `--list` e tutti i file regolari presenti nella
directory moduli, ordinati per percorso; rifiuta link, sottodirectory, file
speciali, piu' di 256 elementi o piu' di 256 MiB totali. Questa
sovra-approssimazione deterministica evita di dover osservare a tempo il
processo o indovinare quale provider sia stato caricato. `files` e' ordinata
per i byte UTF-8 del percorso finale, senza duplicati; ogni elemento contiene
esattamente `path`, `size`, `content_hash`. Ogni file e antenato
e' root-owned e non scrivibile dal servizio. Ogni `content_hash` usa
`metnos.executor-birth.openssl-tcb-file/v1\0` seguito da percorso, dimensione e
byte incorniciati come gli eseguibili amministrativi. Comandi, grammatica, limiti e hash
vengono ricostruiti a ogni prerequisito e `check-all`; una divergenza nega.

`effective_units_hash` usa il dominio
`metnos.executor-birth.effective-units/v1\0` sul documento canonico con
esattamente `schema_version=1` ed `entries`. Ogni elemento, ordinato per
`entry_id`, contiene esattamente `entry_id`, `unit_name`, `fragment_path`,
`fragment_hash`, `fragment_uid`, `fragment_gid`, `fragment_mode`, `dropins`,
`enablement_links`, `load_state`, `unit_file_state`, `need_daemon_reload`,
`configured_directives_hash`, `manager_projection` e `manager_added_edges`.
`dropins` e' una lista ordinata di oggetti con esattamente `path`,
`content_hash`, `uid`, `gid`, `mode` e deve essere vuota in V1.
`manager_projection` contiene esattamente `schema_version=1` e `properties`;
ogni proprieta', ordinata per nome, contiene esattamente `name`, `value_type` e
`values` e usa gli stessi tipi canonici di `unit_spec`. `name` e' sempre il
nome canonico della direttiva firmata, non il nome interno della proprieta' del
gestore. Le coppie `Exec*`/`Exec*Ex` producono una sola direttiva dopo il
confronto; `LimitNOFILE`/`LimitNOFILESoft` producono un solo
`LimitNOFILE`; `RestartUSec` proietta `RestartSec`; le basi di
`TimersMonotonic` proiettano le rispettive direttive `*Sec`. I nomi interni del
gestore restano nel piano di interrogazione e nei controlli di cardinalita',
ma non aggiungono proprieta' allo schema firmato.

Il programma invoca il solo `systemctl` canonico, con ambiente minimo e
timeout. Chiede `FragmentPath`, `DropInPaths`, `LoadState`, `UnitFileState` e
`NeedDaemonReload`, piu' la proiezione completa delle direttive ammesse che
hanno effetto nel gestore: tipo e notifica, credenziali, comandi, directory,
ambiente, riavvio e timeout, sandbox e capability, delega e cgroup, limiti di
risorse, logging, timer e relazioni. La tabella nome-direttiva/nome-proprieta' e'
unica, chiusa e versionata nel codec; una direttiva senza proprieta' osservabile
rende il catalogo V1 non rappresentabile. `Install` viene provato separatamente
dai link di abilitazione root-owned e byte-esatti.

Frammento e link vengono aperti direttamente senza seguire link. Il frammento
deve essere regolare `root:root`, non scrivibile da gruppo o altri e coincidere
coi byte firmati. Il link deve essere un link simbolico `root:root` col target
relativo esatto; il suo modo non costituisce una prova perche' Linux lo espone
come `0777`. Tutti gli antenati di entrambi devono essere `root` e non
scrivibili da gruppo o altri. Non sono ammessi drop-in estranei.
`configured_directives_hash` e' ricalcolato col parser indipendente sui byte del
frammento. Il suo valore usa il dominio
`metnos.executor-birth.systemd-configured-directives/v1\0` sul documento JSON
canonico con esattamente `schema_version=1` e `directives`. `directives` e' la
lista completa, gia' nell'ordine canonico del parser, di oggetti con
esattamente `section`, `name`, `value_type` e `values`; non coincide con il
`fragment_hash` e non include metadati o byte non interpretati. Una modifica
di ordine, tipo o valore cambia quindi entrambi i controlli indipendenti del
frammento. `need_daemon_reload` deve essere `no`: insieme al `daemon-reload`
causale della fabbrica prova che il gestore non usa una copia precedente.

Le proprieta' a corrispondenza univoca devono coincidere esattamente con la
proiezione firmata. Per `Requires`, `Wants`, `BindsTo`, `After`, `Before`,
`PartOf` e `OnFailure`, gli archi espliciti firmati devono essere presenti. Gli
archi aggiunti dal gestore dipendono anche da mount, generatori e sandbox
dell'host e non vengono predetti da una allowlist.

`manager_added_edges` e' invece la fotografia autenticata della differenza
effettiva, ordinata per relazione e nome unita'. Ogni elemento contiene
esattamente `relation`, `unit_name`, `origin_kind`, `fragment_path`,
`source_path`, `source_size`, `source_content_hash`, `source_uid`,
`source_gid`, `source_mode`, `size`, `content_hash`, `uid`, `gid`, `mode`,
`load_state` e `unit_file_state`. `relation`
appartiene all'insieme chiuso, versionato per il manager supportato,
`Requires|Requisite|Wants|BindsTo|PartOf|Upholds|RequiredBy|RequisiteOf|
WantedBy|BoundBy|ConsistsOf|UpheldBy|Conflicts|ConflictedBy|Before|After|
OnFailure|OnSuccess|Triggers|TriggeredBy|PropagatesReloadTo|
ReloadPropagatedFrom|PropagatesStopTo|StopPropagatedFrom|JoinsNamespaceOf`.
Il verificatore richiede tutte queste proprieta' con
`systemctl show`, anche quando vuote; non esiste una lista aperta o scelta dal
catalogo. `origin_kind` appartiene a
`root_fragment|root_generator|manager_virtual`. Per i primi due, percorso,
dimensione, digest e metadati sono obbligatori e la sorgente viene aperta senza link sotto
una radice root-owned non scrivibile; per `manager_virtual` i sei campi
di file (`fragment_path`, `size`, `content_hash`, `uid`, `gid`, `mode`), i sei
campi di sorgente (`source_path`, `source_size`, `source_content_hash`,
`source_uid`, `source_gid`, `source_mode`) e `unit_file_state` sono nulli e il
nome appartiene alla lista chiusa delle unita' intrinseche del
gestore supportato. Unita' transient o controllate dall'identita' di servizio
sono vietate. `origin_kind` classifica l'origine del file dell'unita' bersaglio,
non pretende di descrivere la provenienza dell'arco, che systemd non espone.
Il profilo 255 non include `References` o `ReferencedBy`: non sono proprieta'
esposte dall'interfaccia `org.freedesktop.systemd1.Unit` supportata. La diversa
proprieta' `Refs` descrive riferimenti del gestore non configurabili e non e'
un arco stabile del grafo firmato.
`content_hash` usa il dominio
`metnos.executor-birth.systemd-origin-file/v1\0` seguito da
`u64be(len(path_utf8)) || path_utf8 || u64be(size) || file_bytes`, sempre sul
percorso finale canonico.
`source_content_hash` usa lo stesso framing col dominio distinto
`metnos.executor-birth.systemd-origin-source/v1\0`.

`systemd_manager_version` proviene dalla proprieta' manager `Version`, con
grammatica e lista supportata chiuse. La fabbrica G7 o della cella costruisce la
fotografia soltanto dopo byte, drop-in, link e `daemon-reload` verificati, sotto
startup gate e manutenzione; il suo hash entra nella capability e nel
prerequisito. `check`, `launch` e `check-all` pretendono poi l'identita' esatta
della fotografia, inclusi archi e origini. Qualunque proprieta' omessa, valore
aggiunto o deriva nega. Cosi' la baseline reale dell'host evita falsi rossi
senza poter nascondere una nuova dipendenza dopo l'attestazione.

Il verificatore definitivo non considera sufficiente una singola fotografia.
Acquisisce dalla collocazione fissa il prerequisito handle-bound `P0`, costruisce
la fotografia completa `S0`, rilegge il prerequisito come `P1`, ricostruisce da
zero la fotografia completa `S1` e rilegge infine `P2`. Il solo harness di
prova puo' inserire un killpoint fra `S0` e `P1`. Identita', metadati e byte di
`P0`, `P1` e `P2` coincidono; documento e catture di `S0` e `S1` coincidono.
Dopo `P2` vengono rivalidati TCB, frammenti, link, origini e la sola epoca
ownership selezionata. La comparsa di un claim successore non ancora scelto non
equivale a una modifica della testa richiesta; una modifica di testa, frame,
build, richiesta, ultimo record o prerequisito invece nega.

Questa osservazione resta non autorizzante finche' il protocollo interprocesso
di `check-all` non prova il gate startup esclusivo e la manutenzione gia'
detenuti dal coordinatore. Non sono equivalenti un booleano, un path o un FD
ricevuto dalla CLI. Prima che tale protocollo sia definito e provato, nessun
wrapper della fotografia puo' essere consumato dal dispatch o pubblicare
l'attestazione definitiva.

##### 3.5.4.1 Link di abilitazione

Ogni elemento di `enablement_links` contiene esattamente `path` e `target`.
Per una direttiva firmata `[Install] WantedBy=T`, i due valori sono
`/etc/systemd/system/T.wants/<unit-name>` e `../<unit-name>`; per
`RequiredBy=T` sono `/etc/systemd/system/T.requires/<unit-name>` e
`../<unit-name>`. `T` e' il `unit_name` o `external_unit_name` ottenuto dalla
risoluzione dell'entry citata nel medesimo catalogo. In particolare,
`external-default` diventa `default.target`, senza produrre un frammento per
quell'unita' esterna.

`path` e' assoluto, canonico e figlio stretto di `/etc/systemd/system`;
`target` e' relativo e coincide byte per byte con `../<unit-name>`. La sua
risoluzione lessicale dalla directory del link deve produrre esattamente
`/etc/systemd/system/<unit-name>`. Componenti aggiuntivi `.` o `..`, target
assoluti, collisioni di percorso, duplicati e target differenti negano. La
lista e' ordinata per i byte UTF-8 di `path`. B3 compila e firma i link; G6-C
li installa nella cella e G7 sul sistema reale, sempre rileggendo tipo,
proprietario, modo e contenuto del link.

La sorgente V1 corrente produce esattamente questi undici link:

```text
/etc/systemd/system/default.target.wants/metnos.target -> ../metnos.target
/etc/systemd/system/metnos.target.requires/metnos-http.service -> ../metnos-http.service
/etc/systemd/system/metnos.target.wants/metnos-durable-worker.service -> ../metnos-durable-worker.service
/etc/systemd/system/metnos.target.wants/metnos-i18n-translator.timer -> ../metnos-i18n-translator.timer
/etc/systemd/system/metnos.target.wants/metnos-llm.service -> ../metnos-llm.service
/etc/systemd/system/metnos.target.wants/metnos-photon.service -> ../metnos-photon.service
/etc/systemd/system/metnos.target.wants/metnos-playwright.service -> ../metnos-playwright.service
/etc/systemd/system/metnos.target.wants/metnos-searxng.service -> ../metnos-searxng.service
/etc/systemd/system/metnos.target.wants/metnos-side-display.service -> ../metnos-side-display.service
/etc/systemd/system/metnos.target.wants/metnos-stack-watchdog.timer -> ../metnos-stack-watchdog.timer
/etc/systemd/system/metnos.target.wants/metnos-telegram-daemon.service -> ../metnos-telegram-daemon.service
```

Qualunque variazione di numero, percorso o target richiede una modifica
esplicita della sorgente V1, del vettore atteso e delle prove; non puo' essere
appresa dall'host.

##### 3.5.4.2 Protocollo chiuso `systemctl show`

Il programma usa soltanto il `systemctl_executable` autenticato e l'argv
esatto `--no-pager --plain --all show --property=<lista-ASCII-ordinata> --
<unit>`,
senza shell, con stdin nullo, ambiente contenente il solo `LC_ALL=C` e timeout
di dieci secondi. Richiede uscita zero, standard error vuoto, standard output
al massimo 4 MiB e standard error al massimo 4 KiB. L'output e' UTF-8, termina
con un solo LF, non contiene CR o NUL e ha al massimo 4096 righe da 64 KiB.
Ogni riga ha la forma `Nome=valore`; il nome appartiene alla lista richiesta.
Duplicati sono vietati, salvo le proprieta' ripetibili
`ExecStartPre`, `ExecStartPreEx`, `ExecStart`, `ExecStartEx`, `ExecStop`,
`ExecStopEx`, `TimersMonotonic` e `TimersCalendar`, per le quali la
cardinalita' e' comunque derivata dal catalogo.

La versione manager viene letta con una chiamata separata, senza nome unita',
e argv esatto `--no-pager --plain --all show --property=Version`; deve produrre
la sola riga `Version=<valore>`. Systemd 255 omette una proprieta' sconosciuta
pur restituendo uscita zero.
Il verificatore non usa quindi la riuscita del processo come prova di
supporto: pretende l'insieme e la cardinalita' esatti delle proprieta'
obbligatorie per la classe. Nel build supportato, anche con `--all`, un array
`Exec*` vuoto omette entrambe le proprieta' associate: questa e' la sola
assenza ammessa per una proprieta' nota. Se il comando e' presente entrambe
devono avere lo stesso numero di elementi. Proprieta', righe
o cardinalita' ulteriori negano. La sola versione supportata da questo profilo
e' `255.4-1ubuntu8.17`, letta dalla proprieta' manager `Version`; aggiungere
una versione richiede le prove empiriche del §6.2.

La normalizzazione non usa `float`. I booleani sono soltanto `yes|no`; gli
interi sono decimali canonici, con segno ammesso soltanto per `Nice`; le liste
vengono decodificate con il tokenizer C-quoted chiuso di systemd, rifiutano
escape sconosciuti, duplicati ed elementi vuoti e sono ordinate per byte
UTF-8. Le durate del gestore ammettono soltanto componenti
`<intero>[.<da-uno-a-sei-decimali>](us|ms|s|min|h|d|w)`, separati da un solo
spazio, e vengono convertite in microsecondi interi. `MemoryHigh` e
`MemoryMax` normalizzano i suffissi firmati `K|M|G|T` in base 1024 e il valore
del gestore in byte. `infinity` e' ammesso soltanto per `MemoryHigh`,
`MemoryMax` e `TasksMax`; ogni altro campo deve avere un valore finito. Per
`WatchdogUSec`, l'assenza firmata corrisponde al valore finito zero.
I segnali numerici diventano il nome `SIG*` della tabella chiusa Linux; le
capability diventano nomi `CAP_*` maiuscoli, unici e ordinati; `UMask` e'
esattamente di quattro cifre ottali. La proiezione registra i valori gia'
normalizzati, cosi' forme testuali equivalenti non cambiano l'hash.

##### 3.5.4.3 Mappa direttiva-proprieta'

La mappa V1 e' la seguente. Nella sezione `Unit`, `Description`,
`Documentation`, `DefaultDependencies`, `Requires`, `Wants`, `BindsTo`,
`After`, `Before`, `PartOf`, `OnFailure` e `StartLimitBurst` usano la
proprieta' omonima. `StartLimitIntervalSec` usa
`StartLimitIntervalUSec`. Le relazioni sono insiemi di nomi unita';
`Documentation` conserva il tipo `scalar` del codec firmato. Poiche' non e'
emessa dalla sorgente V1, nella proiezione corrente deve avere `values=[]`;
un valore manager non vuoto nega. Descrizione, booleano e intero sono scalari.

Nella sezione `Service`, usano la proprieta' omonima:

```text
Type, User, Group, SupplementaryGroups, Restart, WorkingDirectory,
Environment, NoNewPrivileges, PrivateTmp, ProtectSystem, ProtectHome,
ReadWritePaths, CapabilityBoundingSet, AmbientCapabilities, KillMode,
KillSignal, SuccessExitStatus, RemainAfterExit, UMask, NotifyAccess,
Delegate, DelegateSubgroup, ProtectKernelTunables, ProtectKernelModules,
ProtectControlGroups, RestrictNamespaces, RestrictRealtime,
RestrictAddressFamilies, LockPersonality, MemoryDenyWriteExecute,
SystemCallArchitectures, MemoryAccounting, MemoryHigh, MemoryMax,
TasksAccounting, TasksMax, Nice, StandardOutput, StandardError,
SyslogIdentifier
```

Le conversioni residue sono:

```text
RestartSec -> RestartUSec
TimeoutStartSec -> TimeoutStartUSec
TimeoutStopSec -> TimeoutStopUSec
WatchdogSec -> WatchdogUSec
LimitNOFILE -> LimitNOFILE e LimitNOFILESoft
```

Per `LimitNOFILE` entrambi i valori del gestore devono coincidere con l'unico
valore firmato. `SupplementaryGroups`, capability, architetture e famiglie di
indirizzi vengono confrontati come insiemi chiusi dopo la normalizzazione;
`Environment` come mappa ordinata nome-valore senza nomi duplicati;
`SuccessExitStatus` come due insiemi separati di codici e segnali. La forma
normalizzata viene poi resa nel campo `values` del tipo firmato, usando un
solo spazio fra elementi ordinati quando quel tipo e' `scalar`.

`ExecStartPre`, `ExecStart` ed `ExecStop` usano ciascuno sia la proprieta'
storica omonima sia `ExecStartPreEx`, `ExecStartEx` o `ExecStopEx`. Il parser
accetta soltanto la struttura chiusa che espone `path`, `argv[]`, flag statici,
`start_time`, `stop_time`, `pid`, `code` e `status`. Verifica strettamente la
grammatica ma esclude dalla fotografia soltanto gli ultimi cinque valori
dinamici; numero dei comandi, percorso e argv devono coincidere nelle due
proprieta'. Per ogni servizio `gated_service`, il singolo prefisso `!` deve
apparire in `*Ex` come il solo flag `no-setuid`. `privileged`, `ambient`,
`ignore-failure`, `no-env-expand` o qualunque altro flag negano. Il comando
`stop_only`, che non ha prefisso, richiede flag vuoti. Gli argv V1 non
contengono whitespace nei singoli argomenti; una futura estensione richiede un
codec esplicito e nuovi vettori.

Nella sezione `Timer`, `Persistent` e `Unit` usano la proprieta' omonima;
`RandomizedDelaySec` e `AccuracySec` usano `RandomizedDelayUSec` e
`AccuracyUSec`. `OnBootSec`, `OnActiveSec` e `OnUnitActiveSec` sono gli
elementi `OnBootUSec`, `OnActiveUSec` e `OnUnitActiveUSec` delle righe
ripetibili `TimersMonotonic`. `OnCalendar` e' l'elemento `OnCalendar` di
`TimersCalendar`. Il parser ammette la struttura chiusa
`{ base=valore ; next_elapse=valore-dinamico }`, con ordine e punteggiatura
esatti; verifica ma scarta soltanto `next_elapse`. Base duplicata, inattesa o
mancante nega. V1 consente una sola espressione calendario gia' nella forma
canonica emessa da systemd 255.

`WantedBy` e `RequiredBy` non hanno una proiezione manager sostitutiva: sono
provati esclusivamente dai link del §3.5.4.1. Le undici direttive ammesse dal
codec ma non emesse dalla sorgente corrente sono `Documentation`,
`DefaultDependencies`, `ExecStop`, `Environment`, `ReadWritePaths`,
`AmbientCapabilities`, `SuccessExitStatus`, `UMask`, `OnBootSec`,
`OnCalendar` e `RandomizedDelaySec`. Restano non emettibili nel catalogo
produttivo finche' ciascuna non possiede almeno un vettore positivo e un
mutante negativo specifico. In particolare non si puo' introdurre una nuova
espressione `OnCalendar` o forma `SuccessExitStatus` senza estendere prima il
codec chiuso.

Per ciascuna classe il verificatore interroga tutte le proprieta' applicabili
della tabella, anche quando la direttiva non e' configurata; i valori
predefiniti del gestore entrano cosi' in `manager_projection`. Una direttiva
firmata deve coincidere con la propria forma normalizzata. Se una direttiva
non ha una proprieta' osservabile, il catalogo V1 non e' rappresentabile e la
compilazione nega; le sole eccezioni sono i due link `Install`. I campi
`values` vuoti rappresentano una proprieta' ripetibile non configurata oppure
la sola eccezione scalar V1 `Documentation`, non una proprieta' obbligatoria
mancante.

##### 3.5.4.4 Archi aggiunti e origine

Il programma richiede sempre tutte le proprieta' di relazione gia' enumerate
in questo paragrafo, anche vuote. Sottrae dagli archi osservati soltanto gli
archi diretti presenti nelle direttive firmate della medesima unita'. Non
predice ne' sottrae inversi: inversi automatici e archi prodotti dai link
firmati restano intenzionalmente nella fotografia effettiva. Ogni altro arco
diventa un elemento di `manager_added_edges`, inclusi dipendenze predefinite,
relazioni automatiche dei timer, mount e generatori. L'ordine e'
`(relation, unit_name)` per byte UTF-8; coppie duplicate negano. Il limite e'
4096 residui per unita' candidata e 65536 nell'intera fotografia.

Per ogni unita' bersaglio residua viene eseguita una seconda osservazione con
l'insieme esatto `Id`, `LoadState`, `FragmentPath`, `SourcePath`, `Transient`
e `UnitFileState`. `Id` coincide col nome richiesto, `LoadState` e' `loaded` e
`Transient` e' `no`. `root_fragment` richiede un frammento regolare
`root:root`, con un solo hard link, non scrivibile da gruppo o altri, al
massimo 1 MiB e sotto una sola radice canonica tra
`/etc/systemd/system`, `/run/systemd/system`,
`/usr/local/lib/systemd/system` e `/usr/lib/systemd/system`, escluse le
directory dei generatori. `SourcePath` deve essere vuoto e viene registrato
come nullo; anche `source_size`, `source_content_hash`, `source_uid`,
`source_gid` e `source_mode` sono nulli. `UnitFileState` viene registrato e appartiene a
`enabled|enabled-runtime|linked|linked-runtime|alias|static|disabled|indirect`.
`root_generator` applica gli stessi controlli sotto
`/run/systemd/generator`, `/run/systemd/generator.early` o
`/run/systemd/generator.late`, richiede `UnitFileState=generated` e registra
quel valore. `SourcePath` e' obbligatorio, assoluto e canonico; deve essere un
file regolare `root:root`, non scrivibile da gruppo o altri, sotto `/etc` o
`/usr`, con tutti gli antenati ugualmente sicuri, un solo hard link e limite
di 1 MiB. Percorso, dimensione, contenuto, uid, gid e modo della sorgente sono
registrati nei sei campi `source_*`; i byte alimentano il dominio dedicato
definito sopra. Percorso, byte e metadati del frammento vengono riletti senza
seguire link e alimentano l'altro hash gia' definito.

Per il solo manager esatto supportato, la lista chiusa `manager_virtual` e'
`-.slice|system.slice`: `FragmentPath`, `SourcePath` e `UnitFileState` sono
vuoti e diventano nulli nel record insieme ai metadati di file e sorgente.
`-.mount` non e' virtuale,
ma `root_generator`; `init.scope` e' vietata perche' transient. Qualunque
device, scope, unita' transient o unita' senza frammento fuori dalla lista
chiusa nega. La lista non viene estesa osservando l'host.

La compilazione fallisce se la versione supportata non espone una proprieta'
richiesta o se un valore non appartiene alla grammatica chiusa.

La fabbrica della capacita' G7 e la cella eseguono prima il solo
`<systemctl-canonico> daemon-reload`, ne richiedono uscita zero e poi costruiscono
la fotografia effettiva. `check-all` non effettua il reload e non modifica il
gestore: rilegge una nuova fotografia e pretende che resti identica a quella
del prerequisito.

Se anche una sola unita' non e' installata e caricata in questo modo, il
prerequisito e `check-all` negano. Il gruppo 6 soddisfa questa condizione
soltanto nella cella usa-e-getta; il sistema reale potra' soddisfarla solo dopo
l'installazione del gruppo 7.

`prerequisite_id` usa il dominio
`metnos.executor-birth.startup-prerequisite/v1\0` sul documento senza quel
campo.

Il caricatore verifica nuovamente tutti i legami e soltanto allora costruisce
la classe sigillata `_StartupPrerequisiteV1`. Il costruttore di prova non e'
raggiungibile dal percorso produttivo.

#### 3.5.5 Prenotazione durevole del successore

Soltanto dopo che l'assemblatore ha prodotto e riletto una
`_VerifiedStagedDistributionV1`, ma prima di creare il journal V2 della
transazione, il coordinatore pubblica senza sovrascrittura
`successor-claims-v1/<identificativo-predecessore>.json`, modo `0644`, limite
16 KiB. Per la prima sequenza il nome usa `initial`; per le successive usa
l'identificativo della testa precedente senza il prefisso `sha256:`. Il
documento contiene esattamente:

```text
schema_version, claim_id, previous_head_id, release_sequence,
request_id, source_id, closed_build_id
```

`previous_head_id` e' nullo soltanto per la sequenza uno. `claim_id` usa il
dominio `metnos.executor-birth.successor-claim/v1\0` sul documento senza quel
campo. `schema_version` vale l'intero `1`, `release_sequence` e' un intero
positivo e tutti gli identificativi non nulli sono digest. `request_id` viene
derivato col dominio V1 gia' esistente dai valori riletti di build chiusa,
build precedente e certificato precedente. Una rilettura identica e'
idempotente; qualunque altro candidato per la
stessa testa precedente e' un conflitto durevole, anche dopo la morte del
processo. `source_id` deve coincidere col descrittore immutabile della sorgente
e `closed_build_id` con la capacita' preparata riletta. Se il processo muore
dopo la prenotazione ma prima di `PREPARED`, la ripresa ricostruisce la stessa
capacita' esclusivamente da `source_id`, ne verifica nuovamente
`closed_build_id` e completa `PREPARED`. Una prenotazione senza sorgente o
distribuzione preparabile identica richiede recupero amministrativo; non viene
scavalcata. Un'area preparata priva di prenotazione non e' autorevole e puo'
essere riusata soltanto se la successiva prenotazione lega esattamente le sue
identita'.

#### 3.5.6 Disposizione del journal precedente

Il record del gruppo 5 resta per sempre un documento V1 e non viene ricodificato.
L'unico ponte ammesso e' il documento root-owned
`coordinator-v1/legacy-disposition-v2.json`, modo `0644`, limite 16 KiB,
pubblicato senza sovrascrittura e contenente esattamente:

```text
schema_version, disposition_id, legacy_journal_hash, legacy_request_id,
legacy_state, successor_request_id, reason
```

`schema_version` vale l'intero `2`. Gli identificativi sono digest.
`legacy_journal_hash` usa il dominio
`metnos.executor-birth.legacy-journal/v2\0`, seguito da `u64be` del numero di
record e, in ordine di sequenza, da `u64be(len(record_bytes)) || record_bytes`
per ogni record V1 originale. `legacy_state`
appartiene soltanto a `PREPARED|RECEIPTS_COMPLETE`; `reason` vale la stringa
fissa `superseded_before_certificate`. `disposition_id` usa il dominio
`metnos.executor-birth.legacy-disposition/v2\0` sul documento senza quel campo.

La disposizione puo' essere emessa soltanto quando il journal V1 e' valido e
termina in uno dei due stati ammessi, ancora fissa e puntatore richiesto sono
assenti e la catena e' nello stato `INITIAL`. Essa lega il primo `request_id`
V2, che deve ripartire da una sorgente preparata e da una prenotazione valide.
Sotto lo stesso blocco di deployment l'ordine e' claim, disposizione, record
V2 `PREPARED`; ogni prefisso di questa sequenza e' ripreso soltanto con le
stesse identita'.
Se il V1 e' almeno `CERTIFICATE_READY`, se esiste qualunque artefatto di
certificato o catena, oppure se una disposizione presente non coincide, il
risultato e' `birth_ownership_recovery_required`. Non esiste una transizione
di record V1 in V2 e nessun hash V1 viene nascosto.

#### 3.5.7 Attestazione definitiva del controllo preliminare

`check-all` pubblica senza sovrascrittura
`preflight-attestations-v1/<request-id>.json`, modo `0644`, limite 256 KiB. Il
documento contiene esattamente:

```text
schema_version, attestation_id, request_id, closed_build_id,
release_sequence, head_id, required_head_frame_hash,
deployment_descriptor_id, service_catalog_id, service_coverage_hash,
candidate_units_hash, administrative_bundle_hash, python_binary_hash,
openssl_binary_hash, openssl_tcb_hash, systemctl_binary_hash, systemd_analyze_binary_hash,
effective_units_hash, checked_entry_ids
```

`schema_version` vale l'intero `1`; sequenza e identificativi coincidono con
la testa richiesta, il prerequisito e il descrittore riletti.
`release_sequence` e' un intero positivo; tutti i campi con suffisso `_id` o
`_hash` sono digest non nulli. La lista degli
ingressi e' ordinata per byte, senza duplicati, e coincide esattamente con
tutti gli elementi del catalogo che richiedono controllo piu' tutte le unita'
candidate non esterne. `attestation_id` usa il dominio
`metnos.executor-birth.preflight-attestation/v1\0` sul documento senza quel
campo. Una rilettura diversa per lo stesso `request_id` e' un conflitto.

#### 3.5.8 Descrittore durevole della cella isolata

La seam di prova usa esclusivamente il file fisso
`/var/lib/metnos/executor-birth/test-only-v1/isolated-cell-v1.json`, limite 256
KiB, directory `root:root 0700` e file `root:root 0600`. Il documento contiene
esattamente:

```text
schema_version, cell_id, boot_id, runner_marker_hash, prefix,
ownership_root, administrative_root, system_unit_root, request_id, source_id,
closed_build_id, service_catalog_id, deployment_descriptor_id,
candidate_units_hash, initial_empty_proof_hash, unit_names
```

`schema_version` vale `1`; i sei identificativi e i tre hash sono digest.
`boot_id` e' la stringa UUID minuscola riletta da
`/proc/sys/kernel/random/boot_id`. Il marcatore e' il file fisso
`/run/metnos-rm0008-isolated-runner-v1`, `root:root 0400`, regolare senza link,
massimo 4 KiB, creato con `O_EXCL` dal workflow GitHub-hosted prima di avviare
il codice; contiene una sola riga
`github-hosted:<run-id-decimale>:<attempt-decimale>`. `runner_marker_hash` copre
con dominio `metnos.executor-birth.isolated-runner/v1\0` percorso e byte
incorniciati di quel file. Il marcatore assente o non conforme rende la seam
inaccessibile, quindi server e PC personali non sono eleggibili. `prefix` rispetta
`rm0008-[a-z0-9]{16}-`. Le tre radici valgono esattamente
`/var/lib/metnos/executor-birth`,
`/usr/libexec/metnos/executor-birth-v1` e `/etc/systemd/system`: l'ultima deve
essere il vero percorso del gestore della VM, non una simulazione.
`initial_empty_proof_hash` usa il dominio
`metnos.executor-birth.isolated-empty/v1\0` sulla fotografia canonica che prova
assenti le prime due radici e assenti nella terza tutti i nomi Metnos e tutti i
nomi col nuovo prefisso prima della creazione. La lista
`unit_names` e' la lista ordinata, senza duplicati, di tutte e sole le unita'
firmate, ciascuna iniziata dal prefisso.

`cell_id` usa il dominio `metnos.executor-birth.isolated-cell/v1\0` sul
documento senza quel campo. Il file viene scritto una sola volta con temporaneo
esclusivo nella stessa directory, file-fsync, rename senza sovrascrittura e
parent-fsync. Un esistente e' accettato soltanto se byte-identico. Prima della
prima pubblicazione la factory richiede radici e nomi assenti; in recovery
richiede invece stesso boot e marker, ricalcola sorgente, build, catalogo,
descrittore, grafo e inventario delle unita' dai byte vivi e non accetta dati
dal chiamante. File mancante dopo una mutazione, file parziale o qualunque
divergenza produce `birth_ownership_recovery_required`.

Il predicato privato `_is_isolated_cell_v1()` richiede congiuntamente marker,
boot, `cell_id`, prova iniziale, tre radici esatte e prefisso su ogni
`entry_id`, unita', relazione, timer e comando di stop del catalogo firmato;
ogni porta deve appartenere all'allocazione isolata firmata e risultare libera
prima della pubblicazione. La capability include il tipo nominale della cella e non e' accettata
dall'involucro produttivo, che richiede `_DominantStartupInstalledV1`; la seam
isolata non e' importata dal grafo produttivo. Percorsi uguali non rendono
quindi intercambiabili le due autorita'.

### 3.6 Manifesto firmato e record storico

Il formato della distribuzione viene separato in due risultati autenticati.

- `AuthenticatedDistributionRecordV1` attesta schema canonico, firma,
  identificativo della build, sequenza, predecessore e inventario. Non afferma
  che quei file siano la distribuzione viva corrente.
- `VerifiedDistribution` aggiunge la verifica completa dei file della distribuzione
  installata e resta l'unica fonte di `ClosedBuildIdentity`.

Il verificatore completo chiama prima il verificatore del record e poi verifica
la distribuzione viva. La catena a freddo usa i record storici per tutte le teste e
richiede un solo `VerifiedDistribution` per la testa attualmente richiesta.
In questo modo una seconda distribuzione non dipende dalla presenza dell'intero
albero della prima.

L'assemblatore produttivo deriva l'inventario da regole compilate, non da una
lista del chiamante. Firma con la chiave `closed_distribution_v1` caricata a
freddo dalla radice del gruppo 5. L'insieme chiuso dei ruoli aggiunge
`service_catalog` e `deployment_descriptor`; `service_unit` ammette una o piu'
occorrenze. `boundary_inventory`, `dependency_lock`, `service_catalog` e
`deployment_descriptor` hanno esattamente una occorrenza. Ogni programma
amministrativo usa il ruolo gia' esistente `preflight`. Tutte le unita', i
programmi e i documenti installabili sono copie firmate sotto la sola radice
della distribuzione; ciascun artefatto viene confrontato byte per byte con la
destinazione esterna soltanto nella fase autorizzata dal descrittore.

La lista finale del manifesto contiene da uno a 20.000 file e la somma delle
dimensioni dichiarate non supera 2 GiB. Questi sono limiti della distribuzione
assemblata, non una promessa che ogni sorgente ricevuta al proprio massimo sia
assemblabile: il preparatore nega prima della firma se sorgente e artefatti
generati superano uno dei due limiti. Il percorso
`deployment/admin/preflight.py` e' obbligatorio col ruolo `preflight` e
`preflight_entrypoint` coincide esattamente con quel percorso.

Il record amministrativo autenticato contiene soltanto scalari immutabili,
tuple di file frozen, manifesto canonico e firma. Un seal o il tipo Python non
sono mai autorita' sufficiente. Ogni consumo produttivo riparsa il manifesto,
confronta tutti i campi materializzati, ricarica il registro dalla radice fissa
e riverifica la firma prima di derivare il percorso della release e leggere
l'albero. La seam di prova produce un tipo distinto che il percorso produttivo
nega prima di consultare radici o registri di prova.

### 3.7 Catena a freddo e transazioni multiple

`OwnershipChainStore` produttivo apre soltanto la radice fissa, verifica la
catena di proprieta' di ogni antenato e carica i tre registri distinti. Una
nuova entrata `read_required_chain_cold_v1()` legge da disco manifesti, firme,
certificati, teste e puntatore richiesto; non riceve una mappa di oggetti
sigillati dal chiamante.

Prima della prima testa, `inspect_ownership_chain_state_v1()` puo' restituire
lo stato sigillato `INITIAL` soltanto se ancora fissa e puntatore richiesto sono
assenti e le directory `builds-v1`, `cutovers-v1` e `heads-v1` sono vuote. Se
uno solo di questi elementi e' presente, oppure esiste un temporaneo non
riconciliabile, restituisce `birth_ownership_recovery_required`. Dopo la prima
testa l'unico stato valido proviene da `read_required_chain_cold_v1()`; non
esiste ripiego su `INITIAL` quando una lettura fallisce.

Ogni nuova transazione usa soltanto il journal V2:

`coordinator-v1/transactions-v2/<request-id>/record-<sequenza>-v2.json`.

Sotto il blocco di deployment il coordinatore rilegge la testa richiesta,
deriva sequenza e predecessori e calcola `request_id`. Lo stesso tentativo e'
idempotente. Prima di creare la directory verifica la distribuzione preparata
e pubblica la prenotazione durevole del §3.5.5; due candidati diversi per lo
stesso successore sono quindi in conflitto anche se hanno `request_id`
differenti.

L'eventuale journal singolo V1 gia' presente direttamente sotto
`coordinator-v1` viene decodificato dal codec V1 invariato e i suoi byte non
vengono mai riscritti. Non puo' essere continuato con campi V2: viene trattato
soltanto secondo la disposizione chiusa del §3.5.6. Record V1, disposizione e
directory V2 discordanti producono `birth_ownership_recovery_required`; non
esiste una scelta per data o nome. Il primo certificato continua a essere
l'ancora immutabile. Gli aggiornamenti successivi aggiungono certificati nella
catena senza riscrivere l'ancora.

L'ordine dei blocchi ereditato e gia' provato dal gruppo 5 e': blocco di
deployment esterno, blocco esclusivo degli avvii, blocco di
cutover/manutenzione, infine eventuali blocchi interni dell'archivio. Il gruppo
6 corregge nello stesso commit la frase storica discordante del §7 della
roadmap. Il nuovo orchestratore acquisisce il blocco di deployment una sola
volta e chiama un nucleo privato che riceve una sessione sigillata «blocco gia'
detenuto»; non richiama `prepare_ownership_cutover_v1()` dall'interno dello
stesso blocco.

Il blocco degli avvii e' il file fisso root-owned
`/run/metnos-executor-birth-v1/startup-v1.lock`, regolare, senza link, modo
`0600`. Sta DENTRO la radice di esecuzione del prodotto e non sotto
`/run/lock`: quella e' `1777` per la FHS, e la stessa regola di catena che il
modulo applica a ogni percorso rifiuta un antenato scrivibile da gruppo o
altri, quindi il file sarebbe stato inapribile per costruzione, root incluso. Soltanto il programma root apre il file e acquisisce un blocco
condiviso; `launch` ripete tutta la verifica. Per un `gated_service` mantiene
il blocco fino al passaggio finale: sugli eseguibili imposta `FD_CLOEXEC` prima
dell'`execve`, sul ramo Python lo chiude esplicitamente dopo la riduzione dei
privilegi e immediatamente prima di `runpy`. Il test dimostra in entrambi i casi
che il descrittore non resta aperto, perche' il successivo stop censisce e termina il
servizio. Per un `gated_entrypoint` resta invece un processo supervisore
root-owned che mantiene il blocco per tutta la vita del figlio, inoltra i
segnali e usa un cgroup amministrativo root-owned senza `Delegate`. Non
rilascia il blocco finche' il cgroup dedicato ai figli non e' vuoto; il
supervisore resta nel cgroup amministrativo padre. L'ordine unico e': record
attivo sincronizzato, creazione del cgroup, fork, auto-registrazione del figlio
nel cgroup e conferma del padre, infine apertura della barriera che consente il
target. Subito dopo il fork il figlio imposta `PR_SET_PDEATHSIG=SIGKILL`,
ricontrolla il PPID e termina se il supervisore e' gia' morto; prima della
barriera non esegue alcun byte del target. Un
figlio che fa doppio fork, cambia
sessione o lascia uscire il parent resta nello stesso cgroup. V1 richiede
cgroup v2 e `cgroup.kill`; l'assenza della primitive e' piattaforma non
supportata. Discendenti residui vengono terminati con `cgroup.kill` e rendono
fallito l'ingresso. Attende la
terminazione e ne propaga lo stato: nessun installer gia'
avviato puo' sopravvivere all'acquisizione esclusiva. Il coordinatore acquisisce il blocco
esclusivo prima di arresto e censimento e lo mantiene fino a
`PREFLIGHT_VERIFIED`. In questo modo un avvio gia' entrato completa prima
dell'esclusiva e viene poi incluso nello stop, mentre nessun nuovo processo
puo' nascere fra censimento e sostituzione della testa. Timeout o file non
sicuro arrestano il passaggio.

Il registro root-owned
`/run/metnos-executor-birth-v1/entrypoint-cgroups-v1` e' `0700`; ogni avvio
usa il mount cgroup v2 unico con root `/` e mount point fisso
`/sys/fs/cgroup`. La factory G7 o della cella verifica la relativa riga di
`/proc/self/mountinfo` e crea una sola volta il parent `root:root 0700`
`/sys/fs/cgroup/metnos-executor-birth-v1/entrypoints-v1`; non abilita controller
ne' `Delegate` e rimuove soltanto i figli nominati dal registro. Ogni avvio
pubblica prima del cgroup figlio e prima di sbloccare il target un file
`<launch-id>.active.json` `0600`, massimo 16 KiB, con esattamente
`schema_version`, `launch_id`, `launch_nonce`, `boot_id`, `entry_id`,
`cgroup_mount_hash`, `cgroup_parent_path`, `cgroup_path` e `supervisor_pid`.
`launch_nonce` codifica in esadecimale 32 byte
CSPRNG e `launch_id` e' il digest con dominio
`metnos.executor-birth.entrypoint-launch/v1\0` sui 32 byte;
`cgroup_parent_path` vale il percorso fisso appena definito e `cgroup_path` e'
esattamente `<cgroup_parent_path>/<launch-id>`. `cgroup_mount_hash` usa il
dominio `metnos.executor-birth.cgroup2-mount/v1\0` sui byte della sola riga
mountinfo, dopo averne validato campi, escape, filesystem `cgroup2`, root,
mountpoint e opzione `rw`; viene ricalcolato a ogni uso.
La pubblicazione usa temporaneo esclusivo, file-fsync, rename no-replace e
parent-fsync. Dopo lo svuotamento normale il supervisore pubblica allo stesso
modo `<launch-id>.done`, con esattamente `schema_version=1`, `launch_id` e
`active_record_hash`, prima di liberare il gate. `active_record_hash` usa il
dominio `metnos.executor-birth.entrypoint-active/v1\0` sui byte canonici del
record attivo; un `done` diverso e' un conflitto.

Dopo ogni acquisizione esclusiva e in ogni recovery, il coordinatore riconcilia
il registro prima di stop o censimento: verifica stesso `boot_id`, schema e
bijezione fra record e directory del subtree; per ogni record non concluso usa
`cgroup.kill`, attende `cgroup.events populated 0`, pubblica `done` e soltanto
poi rimuove cgroup e record sincronizzando le directory. Un record senza cgroup
viene chiuso nello stesso modo; cgroup senza record, file parziale o record
discordante produce `birth_ownership_recovery_required` e non consente il
cutover.

La prova causale copre due casi nello stesso harness: il parent applicativo
esce lasciando un nipote mutante e il supervisore viene ucciso con `SIGKILL`
mentre il nipote e' vivo. In entrambi i casi l'acquisizione esclusiva deve
riconciliare l'intero subtree, impedire la mutazione e avanzare soltanto dopo
`populated 0` e record durevole concluso. Lo stesso harness inserisce killpoint
dopo record-fsync, creazione cgroup, fork, auto-registrazione, apertura della
barriera e pubblicazione `done`; ogni ripresa converge o nega senza lasciare un
cgroup popolato non registrato.

### 3.8 Significato probante degli stati

Il gruppo 6 introduce `OwnershipCoordinatorRecordV2`, con
`schema_version=2` e dominio
`metnos.executor-birth.ownership-coordinator-record/v2\0`. Il codec V1 resta
separato e di sola lettura. Il record V2 contiene esattamente le chiavi
seguenti; le prime ventidue sono riportate per esteso e conservano tipi,
nullabilita' e soglie del V1:

```text
schema_version, sequence, state, previous_record_sha256,
request_id, previous_closed_build_id, previous_cutover_id,
closed_build_id, distribution_payload_hash,
distribution_signature_hash, boundary_inventory_hash,
boundary_guard_version, current_receipts, maintenance_before_hash,
maintenance_after_hash, maintenance_proof_b64, startup_prerequisite_id,
startup_prerequisite_digest, cutover_id, catalog_id,
certificate_payload_hash, certificate_signature_hash,
source_id, successor_claim_id, deployment_descriptor_id,
install_transaction_id, installed_tree_hash, release_sequence,
previous_head_id, head_id, head_payload_hash, head_signature_hash,
required_head_frame_hash, verified_chain_head_id,
preflight_attestation_hash, service_coverage_hash,
administrative_bundle_hash
```

`state` appartiene all'ordine chiuso `PREPARED`, `RECEIPTS_COMPLETE`,
`CERTIFICATE_READY`, `CERTIFICATE_PUBLISHED`, `BUILD_VERIFIED`,
`HEAD_REQUIRED`, `PREFLIGHT_VERIFIED`; `sequence` e' l'indice intero da zero a
sei e deve coincidere con lo stato. Ogni directory comincia da zero e non
ammette buchi o duplicati.

`source_id`, `successor_claim_id`, `deployment_descriptor_id`,
`install_transaction_id`, `service_coverage_hash` e
`administrative_bundle_hash` sono digest. `release_sequence` e' un intero
positivo; `previous_head_id` e' nullo se e solo se la sequenza vale uno. Questi
campi sono obbligatori da `PREPARED`. Tutti gli altri campi nuovi sono digest
nullabili secondo la tabella seguente.

`install_transaction_id` usa il dominio
`metnos.executor-birth.install-transaction/v1\0` sul documento canonico con
esattamente `schema_version=1`, `request_id`, `source_id`, `closed_build_id`,
`release_sequence`, `previous_head_id`, `successor_claim_id`,
`deployment_descriptor_id`, `service_coverage_hash` e
`administrative_bundle_hash`. Ogni valore viene prima riletto dal proprio
artefatto; il chiamante non puo' fornirne uno gia' calcolato.

| Stato | Campi nuovi che diventano obbligatori |
|---|---|
| `PREPARED` | `source_id`, `successor_claim_id`, `deployment_descriptor_id`, `install_transaction_id`, `release_sequence`, `previous_head_id` secondo la regola iniziale, `service_coverage_hash`, `administrative_bundle_hash` |
| `RECEIPTS_COMPLETE` | nessun nuovo campo; restano obbligatorie prova corrente e manutenzione del gruppo 5 |
| `CERTIFICATE_READY` | campi del certificato e del prerequisito gia' definiti dal gruppo 5 |
| `CERTIFICATE_PUBLISHED` | nessun nuovo campo; la coppia del certificato deve essere riletta dalla collocazione corretta |
| `BUILD_VERIFIED` | `installed_tree_hash` |
| `HEAD_REQUIRED` | `head_id`, `head_payload_hash`, `head_signature_hash`, `required_head_frame_hash`, `verified_chain_head_id` |
| `PREFLIGHT_VERIFIED` | `preflight_attestation_hash` |

Un campo anticipato rispetto alla propria soglia o nullo dopo la soglia rende
il journal invalido. `previous_record_sha256` del V2 e' il digest col dominio
V2 dei byte canonici del record V2 precedente nella stessa directory; non
punta mai a un record V1. Il legame col V1 esiste soltanto nella disposizione.
`installed_tree_hash` usa il dominio
`metnos.executor-birth.installed-tree/v1\0`, seguito da `u64be` del numero di
file e, in ordine di percorso, da `u64be(len(path_utf8)) || path_utf8 ||
u64be(size) || content_hash_raw_32` per ogni file riletto.

`head_id` resta esattamente
`sha256("metnos.executor-birth.ownership-head-id/v1\0" ||
canonical_head_without_head_id)`. `head_payload_hash` usa
`sha256("metnos.executor-birth.head-payload-hash/v2\0" ||
u64be(len(head_bytes)) || head_bytes)`; `head_signature_hash` usa lo stesso
framing col dominio `metnos.executor-birth.head-signature-hash/v2\0` e i 64
byte della firma. `required_head_frame_hash` usa
`sha256("metnos.executor-birth.required-head-frame-hash/v2\0" ||
u64be(len(frame_bytes)) || frame_bytes)`, dove `frame_bytes` e' esattamente
`REQUIRED_HEAD_MAGIC || u32be(len(head_bytes)) || head_bytes || signature`.
Nessuno di questi campi usa JSON ricostruito o digest fornito dal chiamante.
`preflight_attestation_hash` usa il
dominio `metnos.executor-birth.preflight-attestation-record/v1\0` sui byte
canonici dell'attestazione definitiva del §3.5.7.

I campi attestano:

- `install_transaction_id` e `installed_tree_hash` della distribuzione
  installata;
- `release_sequence` e `previous_head_id`;
- `head_id`, digest del payload della testa e digest della firma;
- digest del frame `required-head-v1.bin`;
- `verified_chain_head_id`;
- digest dell'attestazione del controllo preliminare;
- digest della copertura dei servizi e dell'installatore.

Le soglie sono chiuse.

- `CERTIFICATE_READY` e `CERTIFICATE_PUBLISHED` richiedono i campi del
  certificato e del prerequisito transitorio.
- `BUILD_VERIFIED` richiede la distribuzione finale, il manifesto e la firma riletti
  dal percorso installato.
- `HEAD_REQUIRED` richiede build, certificato e testa aggiunti, puntatore
  sostituito e catena fredda riletta fino alla stessa testa.
- `PREFLIGHT_VERIFIED` richiede una nuova esecuzione del controllo definitivo,
  la stessa testa verificata e la copertura completa degli ingressi.

Una riapertura non restituisce uno stato soltanto perche' il record esiste.
Ricostruisce dal disco l'evidenza propria dello stato e rifiuta ogni deriva.

### 3.9 Primo passaggio e aggiornamenti successivi

Il primo passaggio e gli aggiornamenti condividono manifesto, distribuzione,
catena e controllo preliminare, ma non pubblicano il certificato nello stesso
modo.

Nel primo passaggio:

1. `ownership-cutover-v1.json` e `.sig` vengono pubblicati nei nomi fissi;
2. quella coppia diventa l'ancora immutabile e viene copiata byte per byte anche
   in `cutovers-v1/<cutover-id>`;
3. la pubblicazione del payload nel nome fisso e' il punto di non ritorno dal
   regime precedente: da quel momento il controllo transitorio non ammette piu'
   il predecessore e lo stack resta fermo finche' distribuzione e testa non
   sono complete.

In ogni aggiornamento successivo:

1. i nomi fissi dell'ancora non vengono mai riscritti;
2. il nuovo certificato viene aggiunto soltanto a `cutovers-v1`;
3. `CERTIFICATE_PUBLISHED` significa che l'oggetto futuro e' disponibile, non
   che e' gia' richiesto;
4. fino al confronto-e-scambio di `required-head-v1.bin` resta autorevole la
   precedente distribuzione chiusa; quel confronto-e-scambio e' il punto di non
   ritorno dell'aggiornamento.

Il controllo preliminare decide sempre dal regime osservato su disco. Se
l'ancora fissa non esiste, vale soltanto il descrittore transitorio. Se l'ancora
esiste ma manca una testa richiesta completa, nega tutto. Se la testa richiesta
esiste, ammette soltanto la sua distribuzione, indipendentemente da oggetti
successori gia' aggiunti ma non ancora richiesti.

## 4. Sequenza produttiva non permutabile

Questa sequenza e' raggiungibile sulle radici reali soltanto quando
`closed_build_enforcement()` vale `True`. Nell'artefatto G6 l'entrata pubblica
si arresta prima del passo 1; la cella usa l'involucro privato isolato.

1. Il comando amministrativo root-only verifica per primo il diniego compilato,
   riceve l'unico `source_id` da completare, ne valida soltanto la grammatica di
   digest e acquisisce una sola volta il blocco di deployment; ogni autorita'
   successiva viene ricalcolata dai byte sotto la radice fissa.
2. Caricare a freddo le tre autorita' e la catena richiesta corrente.
3. Allocare la sequenza e derivare predecessori e `request_id`; assemblare e
   rileggere la distribuzione preparata content-addressed.
4. Completare tutte le verifiche reversibili: account non privilegiato,
   manifesto, firma, file, catalogo, descrittore, eseguibili canonici, piano
   amministrativo e grafo delle unita' tramite `systemd-analyze`. Installare e
   rileggere soltanto i programmi `group6_admin`; non scrivere unita' reali.
5. Pubblicare la prenotazione che lega sorgente e distribuzione gia' verificate
   e scrivere immediatamente il record V2 `PREPARED`. Una ripresa fra i due
   passi ricostruisce la stessa capacita' dalla prenotazione.
6. Acquisire il blocco esclusivo degli avvii, poi, sotto i blocchi gia'
   detenuti, il blocco di cutover/manutenzione; mantenerli entrambi fino a
   `PREFLIGHT_VERIFIED`.
7. Nel primo passaggio soltanto, fotografare sotto manutenzione il predecessore,
   pubblicare `predecessor-v1.json` e provare direttamente il controllo
   transitorio sul diniego e sull'ammissione del solo predecessore. Negli
   aggiornamenti verificare invece che il controllo definitivo ammetta la testa
   corrente.
8. Convergere a `RECEIPTS_COMPLETE` riusando il nucleo del gruppo 5 senza
   riacquisire il blocco di deployment.
9. L'entrata preparatoria rilascia manutenzione e startup gate e restituisce il
   risultato a `RECEIPTS_COMPLETE`, senza pubblicare certificato o testa. La
   futura entrata G7 riacquisisce i blocchi nell'ordine, ricostruisce lo stato e
   non accetta alcuna capacita' dal chiamante.
10. Sotto gli stessi blocchi vivi, G7 installa/rilegge la topologia, ritira gli
    ingressi legacy, verifica l'enforcement, costruisce e consuma immediatamente
    la capacita' sigillata del §2. La cella esegue lo stesso nucleo con la sola
    capability isolata ricostruita dal proprio descrittore. Poi vengono
    verificati programmi amministrativi, catalogo, grafo candidato e
    configurazione effettiva e viene prodotto e riletto il prerequisito.
11. Rileggere il prerequisito e pubblicare il certificato nella collocazione
    definita dal §3.9. Solo nel primo passaggio questo e' il punto di non ritorno
    dal predecessore.
12. Pubblicare senza sovrascrittura la directory finale della distribuzione,
    rileggere manifesto e file e registrare `BUILD_VERIFIED`.
13. Aggiungere la build, assicurare la presenza del certificato nella catena e
    aggiungere la testa, rileggendo ogni coppia autenticata.
14. Sostituire per ultimo `required-head-v1.bin` con confronto-e-scambio,
    rileggere l'intera catena a freddo e registrare `HEAD_REQUIRED`. Negli
    aggiornamenti questo e' il punto di non ritorno.
15. Avviare un nuovo processo del piano amministrativo, eseguire `check-all`
    anche sulla topologia effettiva e registrare `PREFLIGHT_VERIFIED`.
16. Rilasciare manutenzione e blocco degli avvii. Le unita' candidate restano
    soltanto artefatti firmati; installazione nei nomi reali e avvio
    appartengono al gruppo 7.

## 5. Incrementi verticali

### 5.1 G6-A — record storico, catena fredda e codec durevoli

Modifiche produttive:

- separazione fra record di distribuzione autenticato e distribuzione viva
  verificata;
- lettura fredda della catena dalla radice fissa, senza mappe autorevoli del
  chiamante;
- supporto di due o piu' distribuzioni consecutive;
- codec V2 separato, lettura V1 invariata, disposizione legacy e archivio delle
  prenotazioni;
- nessuna pubblicazione produttiva di prenotazione o `PREPARED`, perche'
  l'identita' candidata nasce soltanto in G6-B.

Prova discriminante unica: due distribuzioni consecutive vengono riaperte in
un nuovo processo usando soltanto archivio e seconda distribuzione viva. Una
tabella parametrica modifica soltanto firma storica, predecessore, sequenza,
buco, duplicato, fork e testa richiesta; ogni mutante deve fallire chiuso. Le
alterazioni dei file vivi appartengono esclusivamente a G6-B.

Non si ripetono le prove di codec, scrittura binaria o separazione delle chiavi
gia' certificate nei gruppi 1 e 5.

### 5.2 G6-B — materiali firmabili e installazione della distribuzione

Modifiche produttive:

- schema e contenuto completo del catalogo unico dei servizi;
- rendering deterministico di tutti i programmi amministrativi, descrittori e
  file delle unita' che verranno installati da G6-C;
- catalogo compilato dei ruoli e dei file;
- assemblatore e firma tramite la sola autorita' di distribuzione caricata a
  freddo;
- area di preparazione sul filesystem di destinazione e sincronizzazione dal
  basso; la primitiva di pubblicazione finale senza sovrascrittura resta
  privata e non e' autorizzabile dalla sola capacita' preparata;
- verifica dell'area di preparazione tramite capacita' sigillata, senza esporre una
  `Path` produttiva libera;
- restituzione della sola capacita' preparata; claim e journal appartengono
  alla composizione G6-D;
- rilettura completa della distribuzione finale nella seam nominale di prova;
  l'autorizzazione produttiva alla pubblicazione nasce soltanto in G6-D.

Prova discriminante unica: una matrice parametrica copre determinismo,
mancanza, extra, alterazione, pluralita' delle unita', link e mutazione durante
la lettura. Un solo harness multiprocesso usa un albero non banale, con file
grande, fratelli e sottodirectory, e termina realmente il figlio: prima della
prima scrittura, a meta' file, dopo un file ma prima dei fratelli, dopo il
`fsync` di una sottodirectory ma non del resto dell'albero, prima e dopo il
rename e prima e dopo il `fsync` del parent. Lo stesso harness e' parametrizzato
per sorgente, distribuzione preparata e directory amministrativa. Ogni ripresa
deve produrre byte finali identici oppure `recovery_required`; non puo' adottare
zero byte, file parziali o alberi incompleti. G6-C consuma questi stessi byte e
non rigenera un secondo catalogo o un secondo inventario.
Un caso costruisce due release con modulo, argomenti, directory e ambiente del
target differenti e prova che cataloghi e manifesti cambiano, mentre tutti i
frammenti di unita' e `administrative_bundle_hash` restano byte-identici.

Il riesame adversarial precedente al codice rende vincolanti quattro
sottoincrementi, eseguiti e revisionati in ordine.

1. **G6-B1, compatibilita' e fonte unica.** Correggere ruoli e cardinalita'
   del manifesto; introdurre codec, tabella dichiarativa unica, renderer,
   parser indipendente e proiezione di manutenzione. Il caricatore produttivo
   accetta soltanto un `AuthenticatedDistributionRecordV1` produttivo, lo
   riattesta dalla radice fissa con `verify_installed_distribution_record_v1()`
   e rilegge il file `service_catalog` gia' legato al manifesto; non accetta
   `VerifiedDistribution` fornita dal chiamante, byte, mapping o percorsi. La
   sola famiglia di prove combina una tabella canonica comune a tutti i nuovi
   codec, compresi `received-source`, catalogo e descrittore di deployment,
   inventario meccanico degli ingressi, copertura delle sei classi, pluralita'
   delle unita' e round-trip parser/renderer.
2. **G6-B2, ingresso reale.** Implementare il ricevitore root-only e la
   transazione content-addressed da sorgente a `source_id`. Una famiglia Linux
   usa un albero non banale e combina account, link, hardlink, sostituzione
   durante la lettura, idempotenza e fotografia di non mutazione delle altre
   radici. La tabella portabile prova soltanto codec, limiti e rifiuto prima di
   I/O fuori da Linux.
3. **G6-B3, nucleo preparatore bloccato.** La preparazione produttiva e' un
   nucleo privato che riceve la sessione esatta e viva di deployment, la
   fotografia opaca G6-A e il solo `source_id`; sequenza e predecessori sono
   ricalcolati sotto quel blocco. Compila preflight, catalogo, unita',
   descrittore e inventario, carica a freddo una sola volta l'autorita' privata
   di distribuzione, firma, rilegge integralmente lo staging e restituisce una
   capacita' nominale non copiabile e priva di `Path`. Una famiglia integrata
   copre autorita', determinismo fra due release e i mutanti mancante, extra,
   alterato, link e sostituzione durante la lettura. La stessa famiglia esegue
   il vero `admin_preflight.py` con `-I -S`, prova che importa soltanto libreria
   standard e rifiuta ogni placeholder o dipendenza G6-C non ancora definita.
   L'implementazione interna procede in due snapshot revisionabili: prima
   autenticazione, record immutabile e albero esatto; poi porting autonomo
   standard-library della scoperta e della guardia boundary. Il primo snapshot
   non viene collegato a `main`, non produce una capacita' autorizzante e non
   puo' essere dichiarato verifica completa della distribuzione. Prima di
   chiudere B3, la policy deve coincidere esattamente con owner, 62 scope di
   scrittura coordinata, 16 eccezioni e moduli sealed compilati, e la scansione
   indipendente dei sorgenti deve dare lo stesso esito di
   `discover()+birth_closed_findings()` sul loader certificato. Importare o
   eseguire la guardia contenuta nella distribuzione non soddisfa questa prova.
   Il porting deve applicare una grammatica chiusa ai sorgenti prima della
   cattura: radice esatta in `runtime`, `install`, `scripts` o `executors`,
   suffisso finale esattamente `.py`, nessun componente intermedio con
   suffisso `.py` anche con casing alternativo e sola eccezione nominale
   `deployment/admin/preflight.py`, che non appartiene al censimento ma e'
   compreso nei budget Python e nel controllo AST. I limiti compilati sono
   2.048 sorgenti, 1 MiB per sorgente, 32 MiB totali, 100.000 nodi AST per
   file, quattro milioni totali, profondita' 64, 512 scope e 8.192 chiamate
   per file. Byte, numero e totale sono negati dal manifesto prima della
   cattura; nodi, profondita', scope e chiamate sono contati iterativamente
   prima di qualunque visitor ricorsivo. Anche `MemoryError` deve diventare un
   diniego stabile.

   Ogni chiamata a `__import__` o `importlib.import_module` e' chiusa e deve
   produrre diniego, anche quando il target e' una stringa letterale sicura.
   Sono chiusi anche moduli relativi o discendenti di un boundary, alias di
   prima classe e accessi riflessivi a `importlib`, `builtins` o `sys.modules`
   che possono raggiungere il confine. Il prodotto deve quindi contenere zero
   chiamate di importazione dinamica; registri finiti e hook di test usano
   import letterali. La sola porta che esegue codice resta
   `runtime/admitted_module_v1.py`, gia' autenticata e certificata nel gruppo
   3: non viene sostituita o degradata da questo controllo.
   Canonico e clone sono provati separatamente contro aspettative indipendenti
   e poi confrontati campo per campo; non basta usare uno come oracolo
   dell'altro. Le prove di grammatica, AST, import dinamici e isolamento
   `-I -S` possono correre in parallelo. Restano seriali soltanto servizi reali,
   PC Windows e test con LLM reale; B3 non richiede alcun LLM reale.
4. **G6-B4, transazione di pubblicazione non autorizzante.** Implementare il
   solo nucleo filesystem di rename no-replace, sincronizzazione e rilettura,
   senza una funzione produttiva che lo renda raggiungibile o che accetti una
   autorizzazione ancora inesistente. Una seam di tipo distinto esercita
   pubblicazione, rilettura tramite
   `verify_installed_distribution_record_v1()` e concorrenza. G6-D definira'
   tipo, proprietario, minter e validatore dell'autorizzazione nominale e,
   soltanto dopo averli verificati insieme alla stessa sessione viva, invochera'
   questo nucleo. L'unico harness di arresto viene riusato per ricevitore,
   staging, profilo privato `administrative_directory` e pubblicazione; G6-C
   riusa quel profilo senza una nuova matrice. Non si moltiplicano matrici per
   helper o killpoint. Una prova causale separata passa una capacita' preparata
   al solo grafo produttivo disponibile in B e pretende diniego prima di ogni
   I/O: autorizzazione assente, fabbricata o di test non puo' rendere
   raggiungibile il nucleo. La fotografia prima/dopo di claim, journal,
   `PREPARED`, certificato, head, piano amministrativo e systemd deve essere
   identica.

Questa divisione conserva nove rischi distinti — schema, ricezione e TOCTOU,
copertura del catalogo, autorita', binding degli artefatti, stabilita' fra
release, arresto, gara no-replace e confine di gruppo — ma li esercita in sole
quattro famiglie. Non ripete firma alterata, scopo/epoca delle chiavi, chiusura
degli import, path Windows, lettura handle-bound, catena fredda, claim, journal
V2 o deployment lock gia' certificati. B3 produce i byte definitivi e completi
dei tre comandi del controllo operativo. A G6-C appartengono soltanto
installazione byte-identica e prova reale di quei byte con `systemd`, startup
gate e cgroup; G6-C non implementa, rigenera o modifica il programma firmato.
Claim, `PREPARED` e recupero del coordinatore appartengono a G6-D.

I criteri di piattaforma sono comuni ai quattro sottoincrementi. Su Windows si
eseguono codec, parser, renderer, determinismo e inventario; ogni ingresso o
nucleo amministrativo importabile di B2, B3 e B4 deve restituire
`birth_ownership_platform_unsupported` prima di consultare sessione,
fotografia, autorita' o filesystem. Non si simula `systemd`. Su Linux un unico
runner root usa processi realmente terminati con `SIGKILL` e prova fsync,
rename no-replace, metadati e ripresa per i quattro profili del medesimo
harness.

### 5.3 G6-C — controllo dominante e installatore unico

Modifiche produttive:

- primitive di installazione e rilettura del piano amministrativo e produzione
  del descrittore transitorio sotto sessioni sigillate, senza eseguirne ancora
  la composizione;
- consumo del catalogo unico di G6-B, senza ridefinirlo;
- unita' candidate del gestore di sistema, possedute da `root`, con processo
  applicativo eseguito come identita' di servizio;
- controllo preliminare transitorio e definitivo senza registri, percorsi o
  prove scelti dal chiamante;
- censimento degli ingressi storici e adattatori candidati inattivi; nessun
  ingresso operativo viene ritirato o disabilitato nel gruppo 6.

Prova discriminante unica: una cella Linux eseguita come `root` gira soltanto
in una VM GitHub ospitata usa-e-getta con `systemd` reale e filesystem proprio;
non e' eseguibile sul server Metnos o su una macchina personale. Prima di
iniziare richiede assenti le radici fisse, i nomi di unita' e il piano
amministrativo, altrimenti nega senza pulire nulla. Costruisce poi una
distribuzione di prova separata e la firma soltanto dopo aver assegnato un
prefisso casuale non collidente a ogni `entry_id`, nome di unita', riferimento
`Requires/Wants/After/Before/PartOf`, bersaglio `Unit=`, comando di quarantena e
porta. Il renderer e' lo stesso di produzione, ma la capacita' privata di prova
fornisce l'intero catalogo ribasato prima del rendering; nessun byte viene
riscritto dopo la firma. Un'asserzione chiusa rifiuta qualunque riferimento
Metnos non prefissato nei byte isolati.

Il descrittore di prova firmato destina quelle unita' non collidenti a
`/etc/systemd/system`; la cella vi installa byte-identici, ricarica il vero
gestore e usa `systemctl show` e `systemctl cat` soltanto come osservazione. I
comandi della cella scrivono un marcatore causale in una directory temporanea.
Un avvio diretto negato deve lasciare assente il marcatore; un temporizzatore
reale deve attivare un servizio ugualmente negato; una sola istanza HTTP
isolata attraversa il controllo ammesso fino alla salute. La cella prova anche
che l'UID applicativo non puo' invocare direttamente `check` o `launch`. Il
target osserva UID/GID/gruppi firmati, `NoNewPrivs=1`, tutti i set capability a
zero, nessun descrittore ereditato oltre 0/1/2 e lo stesso namespace mount e le
stesse restrizioni filesystem dichiarate dall'unita'; una variante con prefisso
`+` deve fallire la compilazione. La cella prova inoltre che il coordinatore
ottiene il blocco esclusivo subito dopo il passaggio al target, tramite
`FD_CLOEXEC` o chiusura esplicita nel ramo Python. La quarantena di
prova resta avviabile e puo' arrestare soltanto unita' col prefisso. La cella
rimuove in `finally` i soli file elencati nel proprio descrittore e ricarica il
gestore; la distruzione della VM elimina anche radici, autorita', journal e
piano amministrativo di prova. I byte produttivi ricevono separatamente verifica del descrittore,
grafo canonico e `systemd-analyze`, ma non vengono installati dal gruppo 6.
Mock, sola analisi di stringhe o rinomina del solo file non costituiscono la
prova.

Una positiva prova che `Triggers/TriggeredBy` del timer isolato entrino nella
fotografia. Un solo mutante aggiunge dopo l'attestazione un'unita' ausiliaria
root-owned con `Conflicts=` verso il candidato e ricarica il gestore:
`ConflictedBy` e la nuova origine devono cambiare la fotografia e il successivo
`check-all` deve negare. Non si aggiunge una matrice per ogni relazione.

La stessa famiglia G6-C contiene una matrice differenziale compatta per il
nuovo confine autonomo: un caso valido e un mutante singolo per schema,
canonicalita', firma, scopo della chiave, registro, manifesto, certificato,
testa, puntatore richiesto e predecessore vengono forniti sia ai loader
canonici G6-A sia al processo amministrativo esterno. Entrambi devono produrre
la stessa decisione ammetti/nega e lo stesso codice pubblico di classe. Non si
ripetono combinazioni fra mutanti: questa prova esiste per impedire che la
reimplementazione standard-library/OpenSSL sia piu' permissiva del loader
canonico.
La stessa matrice include una positiva con import transitivo fra due moduli
firmati e due negative: modulo esterno alla radice e modulo presente ma non
dichiarato nel manifesto.

La transazione della directory amministrativa riusa lo stesso harness di
arresto di G6-B; non introduce una seconda matrice o nuove combinazioni.

La prova Linux installata richiede privilegi reali. Su Windows questa
topologia e' esplicitamente non applicabile: resta una sola prova di rifiuto
`birth_ownership_platform_unsupported`, non una simulazione di `systemd`.

### 5.4 G6-D — composizione del coordinatore e recupero

Modifiche produttive:

- installazione/rilettura dei programmi amministrativi, verifiche reversibili,
  claim e genesi `PREPARED` nell'ordine del §4;
- acquisizione startup/manutenzione e produzione del descrittore del
  predecessore legato a quel `PREPARED`;
- produttore reale del prerequisito sigillato;
- collegamento del percorso del gruppo 5 alla pubblicazione del certificato;
- transizioni e riletture di `BUILD_VERIFIED`, `HEAD_REQUIRED` e
  `PREFLIGHT_VERIFIED`;
- recupero in avanti da ogni frontiera nuova;
- verifica definitiva in un processo nuovo.

Prova discriminante unica: una matrice multiprocesso interrompe una sola volta
ciascuna nuova frontiera semantica: claim pubblicato, disposizione V1
pubblicata quando applicabile, `PREPARED` pubblicato e riletto, certificato
pubblicato, distribuzione finale pubblicata, testa aggiunta, puntatore richiesto
sostituito e record finale scritto. Lo stesso harness esegue sia il primo V2
senza disposizione sia il ponte V1→V2; un claim orfano deve ricostruire soltanto
lo stesso `request_id`, mentre un candidato diverso resta in conflitto. Il
recupero usa soltanto disco e caricatori produttivi. Non si ripetono i
punti interni di `_append_pair()` o del codec gia' certificati.

Una prova di concorrenza usa due coordinatori: uno solo puo' allocare il
successore; lo stesso `request_id` riprende, un candidato diverso e' in
conflitto. Una prova finale attraversa l'intera sequenza isolata ma non avvia lo
stack reale.

Prima di queste prove, un caso separato chiama la sola entrata pubblica G6 con
una sorgente valida e la policy compilata realmente falsa, senza monkeypatch.
Confronta prima e dopo l'inventario completo di radice ownership, release,
piano amministrativo, unita' e directory runtime: nomi, tipi, uid/gid, modi,
dimensioni e digest devono essere identici. Pretende
`birth_ownership_closed_enforcement_required` e assenza di nuovi lock,
temporanei, sequenze, release, claim o journal. Questo e' il test discriminante
del diniego prima di qualunque mutazione.

## 6. Contratti di implementazione per gli agenti di codifica

### 6.1 G6-A

`runtime/executor_birth_distribution_manifest.py` deve aggiungere la classe
sigillata `AuthenticatedDistributionRecordV1` e le funzioni produttive
`authenticate_distribution_record_v1(encoded, signature)` e
`verify_installed_distribution_record_v1(record)`. La prima verifica soltanto
schema, identita', firma ed epoca caricando l'autorita' esclusivamente dal
trust store fisso. La seam privata `_authenticate_distribution_record_for_test`
puo' ricevere un registro soltanto nelle prove e non produce un tipo accettato
dal percorso produttivo. La seconda deriva il percorso esclusivamente dal
record firmato e richiede l'uguaglianza esatta con
`/var/lib/metnos/executor-birth/releases-v1/{release_sequence:020d}`; un mero
prefisso non e' sufficiente. Verifica poi piattaforma, architettura, file,
importazioni, inventario e guardia. Solo la seconda crea
`VerifiedDistribution` e `ClosedBuildIdentity`.

`runtime/executor_birth_ownership_chain.py` deve conservare
`read_required_chain()` come primitiva di prova e aggiungere il percorso
produttivo `read_required_chain_cold_v1()`. Quest'ultimo non riceve mappe o
registri: carica le tre autorita' dalla radice fissa, autentica ogni record
storico, verifica la distribuzione viva della testa richiesta e restituisce
`VerifiedOwnershipChain` con la tupla ordinata dei record autenticati. La
costruzione produttiva di `OwnershipChainStore` non accetta una radice; una
classe privata resta disponibile per le prove portabili.

`runtime/executor_birth_ownership_coordinator.py` deve aggiungere
il codec/store di `successor-claims-v1`, il codec V2, il resolver del journal
per transazione e la disposizione esplicita del journal V1 precedente.
`_deployment_lock_v1()` restituisce una sessione sigillata privata. G6-A non
espone ancora il nucleo che pubblica claim o crea `PREPARED`; nessun booleano o
parametro pubblico puo' simulare il blocco detenuto.

### 6.2 G6-B

Il nuovo `install/executor_birth_source_receiver.py` espone il solo comando
amministrativo `receive --source <directory-assoluta> --service-user <nome>`.
Richiede EUID zero. Il percorso sorgente e il nome dell'account sono scelte
esplicite dell'amministratore, non autorita' di avvio: il ricevitore apre i file
senza seguire link, copia i byte nella radice fissa `incoming-v1`, registra
l'account nel descrittore firmato e dimentica il percorso originario. Non
esegue codice dalla sorgente e non modifica distribuzioni, catena, certificato
o servizi. Produce il descrittore del §3.5.0, pubblica
`incoming-v1/sources-v1/<source-id>` e stampa soltanto `source_id`. La stessa
sorgente e lo stesso account sono idempotenti; byte o account diversi producono
un altro identificativo e non sovrascrivono nulla. L'account deve esistere ed
essere non privilegiato secondo il §3.5.2.

Il nuovo `runtime/executor_birth_service_catalog.py` possiede enum, codec,
domini e catalogo compilato del §3.5.1. Espone soltanto
`load_service_catalog_v1(record: AuthenticatedDistributionRecordV1)` sul
percorso produttivo: il tipo deve essere l'artefatto nominale emesso
dall'autenticazione produttiva; il caricatore lo riattesta con
`verify_installed_distribution_record_v1()` e deriva il file dal ruolo e dalla
radice firmati. Non accetta una `VerifiedDistribution` fornita dal chiamante,
byte, mapping, registri o percorsi liberi. L'emittente resta privato
all'assemblatore. L'elenco non viene duplicato in
`executor_birth_maintenance_units.py`: quel modulo deriva le proprie tuple dal
catalogo e rifiuta una classe sconosciuta.

G6-B introduce anche il sorgente completo
`runtime/executor_birth_admin_preflight.py`. E' il programma autonomo
standard-library del §3.1 e implementa codec, verifica e rendering dei tre
comandi chiusi, ma in questo incremento non viene ancora installato o eseguito
come controllo di sistema. In questo modo l'assemblatore G6-B firma byte reali
e completi; non esistono placeholder dipendenti da G6-C.

Il nuovo `runtime/executor_birth_distribution_assembler.py` possiede i codec
dei §§3.5.0-3.5.4. Non accetta una capability della sorgente o una sorgente
scelta dal chiamante: l'unico riferimento d'ingresso e' `source_id`. L'entrata
produttiva pubblica non viene ancora esposta. Il nucleo privato
`_prepare_closed_distribution_locked_v1(session, graph_snapshot, source_id)`
richiede la sessione esatta e viva di `_deployment_lock_v1()` e la fotografia
opaca emessa dal resolver G6-A per la medesima sessione. Non accetta percorsi,
liste di file, sequenze o predecessori: valida il solo digest, deriva e rilegge
la directory sotto la radice fissa `incoming-v1/sources-v1`, ricalcola
sequenza e predecessori dalla fotografia, compila i materiali in
`deployment/admin/` e `deployment/systemd/`, firma con
`distribution_private`, verifica l'area di preparazione e restituisce una
capacita' sigillata `_VerifiedStagedDistributionV1`. La seam portabile usa
sessione, fotografia e risultato di tipi nominalmente distinti che il percorso
produttivo rifiuta.

G6-B termina restituendo la capacita' preparata. Non installa il piano
amministrativo, non pubblica claim e non crea `PREPARED`; queste azioni
richiedono le primitive G6-C e la composizione G6-D. G6-A non anticipa la
pubblicazione.

Il nuovo `runtime/executor_birth_distribution_installer.py` non espone ancora
alcun installatore produttivo. Contiene il solo nucleo filesystem privato di
pubblicazione no-replace, sincronizzazione e rilettura e una seam nominalmente
distinta che lo prova con una capacita' di test. Il nucleo non decide se la
pubblicazione sia autorizzata e non e' raggiungibile dal grafo produttivo G6-B.
G6-D aggiungera' tipo, proprietario, minter e validatore dell'autorizzazione e
la funzione produttiva che, dopo aver verificato autorizzazione, sessione viva
e capacita' preparata legate per identita', invochera' il nucleo, rileggera' con
`verify_installed_distribution_record_v1()` e restituira' la
`VerifiedDistribution`. Ne' la seam ne' il futuro involucro accettano
destinazione, comportamento su conflitto o funzione di richiamo dal chiamante.

Le copie amministrative hanno nomi fissi:

- `deployment/admin/preflight.py`;
- `deployment/systemd/<nome-unita>` per ogni elemento non esterno del catalogo;
- `deployment/executor-birth-service-catalog-v1.json`;
- `deployment/executor-birth-deployment-v1.json`.

`administrative_bundle_hash` usa il dominio
`metnos.executor-birth.administrative-bundle/v1\0`, seguito da `u64be` del
numero di artefatti e, per ogni artefatto ordinato per `destination_path`, da:

```text
u64be(len(destination_path_utf8)) || destination_path_utf8 ||
u64be(len(kind_ascii)) || kind_ascii ||
u64be(len(install_phase_ascii)) || install_phase_ascii ||
u32be(mode) || u64be(size) || content_hash_raw_32
```

Comprende tutti i programmi e tutte le unita'; nessuna serializzazione o
campo implicito dipende dalla piattaforma.

### 6.3 G6-C

G6-C consuma senza rigenerarlo l'artefatto gia' firmato da G6-B come
`deployment/admin/preflight.py` e lo installa byte-identico nella radice
amministrativa fissa. Non ricopia ne' modifica in C il sorgente
`runtime/executor_birth_admin_preflight.py`: una modifica richiede di tornare
a B3 e rigenerare staging, descrittore, manifesto, identificativo e firma. Il
programma completo prodotto da B3 non importa moduli Metnos o pacchetti
esterni e implementa i tre comandi chiusi del §3.1 per `entry_id`; in esercizio
acquisisce il blocco condiviso degli avvii per `check` e `launch` e usa
percorsi costanti e restituisce codici di uscita stabili: zero per ammissione,
`20` per prova mancante, `21` per prova non valida, `22` per predecessore o
testa non corrispondente, `23` per piattaforma non supportata e `24` per
recupero obbligatorio. I dettagli sensibili restano sul giornale privato; lo
standard error esterno contiene soltanto il codice simbolico.
`check-all` rilegge distribuzione, testa, descrittore, catalogo, grafo candidato,
programmi amministrativi e configurazione `systemd` effettiva; senza
corrispondenza byte per byte non pubblica l'attestazione del §3.5.7.
Il ramo `launch` implementa nello stesso file il bootstrap Python chiuso del
§3.1; non esiste un secondo eseguibile direttamente invocabile dall'utente
applicativo.

Il nuovo `install/executor_birth_systemd.py` consuma esclusivamente il
descrittore firmato e installa copie byte-identiche con proprietario e modo
esatti. Nel gruppo 6 l'entrata produttiva installa soltanto `group6_admin` e
rifiuta `group7_cutover`. La funzione privata della cella accetta soltanto una
capacita' `_SignedIsolatedSystemdTestV1`: i suoi nomi, riferimenti, comandi e
destinazioni sono gia' ribasati nel catalogo e nel descrittore prima della
firma. Non contiene alcuna funzione di rinomina successiva.

`runtime/executor_birth_ownership_preflight.py` aggiunge il produttore reale
del prerequisito e la verifica transitoria/definitiva. Il ramo senza ancora
legge soltanto `predecessor-v1.json`; il ramo con ancora chiama soltanto
`read_required_chain_cold_v1()` e confronta la prova corrente col certificato
dell'ultima testa, non con l'ancora storica.

### 6.4 G6-D

`runtime/executor_birth_ownership_coordinator.py` aggiunge
`prepare_ownership_cutover_v1(source_id)`, che richiede EUID zero e si arresta
al massimo a `RECEIPTS_COMPLETE`. La funzione verifica
`closed_build_enforcement()` prima di acquisire il blocco e di ogni scrittura e
nel gruppo 6 produttivo nega; soltanto la seam isolata puo' entrare nel nucleo
con la propria capability. Non esiste ancora una
`complete_ownership_cutover_v1()` pubblica.
`source_id` e' soltanto la scelta amministrativa di un digest e non una
distribuzione o un percorso. La funzione rilegge il descrittore
content-addressed e usa la distribuzione preparata dal nucleo G6-B sotto la
stessa sessione di deployment. Dopo aver usato G6-C
per tutte le verifiche reversibili e l'installazione amministrativa, il
percorso preparatorio pubblica claim, eventuale disposizione V1 e
`PREPARED`; soltanto dopo acquisisce startup gate e manutenzione e fotografa il
predecessore col relativo `install_transaction_id`. La funzione privata del
gruppo 5 che converge le
ricevute viene richiamata con la sessione sigillata e con una manutenzione gia'
detenuta; i vecchi involucri pubblici restano compatibili ma non vengono
annidati.

Il nucleo privato delle transizioni finali e' unico. Il futuro involucro G7
costruira' e consumera' nello stesso stack soltanto
`_DominantStartupInstalledV1`; l'involucro della cella accetta soltanto
`_IsolatedActivationForTestV1`. Prima della prima mutazione di protocollo la
cella verifica l'assenza delle radici, inizializza la radice isolata e pubblica
senza sovrascrittura un
`isolated-cell-v1.json` root-owned, legato a `boot_id`, identita' del runner,
prefisso casuale, radici, `request_id`, catalogo e descrittore firmati. Dopo un
arresto un nuovo processo ricostruisce la capability soltanto da questo
documento e dalle evidenze vive, richiede lo stesso `boot_id` e tutte le unita'
prefissate; non ripete il requisito di radici assenti. I due tipi non hanno
ereditarieta' strutturale e nessun costruttore pubblico; la seam di prova non e'
esportata ne' importata dal grafo produttivo.

Prima di entrare in manutenzione il coordinatore acquisisce una sessione
sigillata `_ExclusiveStartupGateV1`; stop, censimento, certificato, build,
testa e controllo definitivo richiedono quella sessione e non accettano un
booleano sostitutivo. La sessione viene rilasciata soltanto dopo il record
`PREFLIGHT_VERIFIED` riletto; l'unica eccezione e' l'uscita preparatoria a
`RECEIPTS_COMPLETE`, che rilascia senza oltrepassare il certificato. Il futuro
involucro G7 deve riacquisirla e ricostruire tutte le evidenze prima di creare
la propria capability interna.

Il coordinatore separa `_publish_initial_anchor_v1()` da
`_append_successor_cutover_v1()`. La prima pubblica i nomi fissi e poi la copia
nella catena; la seconda scrive soltanto nella catena. Le transizioni
`BUILD_VERIFIED`, `HEAD_REQUIRED` e `PREFLIGHT_VERIFIED` sono funzioni nominali
distinte, ciascuna scrive, sincronizza, rilegge il proprio record e ricostruisce
dal disco l'evidenza prima di restituire.

### 6.5 Errori stabili aggiuntivi

I codec della distribuzione continuano a usare gli errori
`birth_ownership_distribution_*`. I nuovi confini aggiungono soltanto:

| Codice | Significato |
|---|---|
| `birth_ownership_service_catalog_invalid` | schema, classe, ordine o copertura del catalogo non validi |
| `birth_ownership_deployment_invalid` | descrittore o materiale amministrativo non valido |
| `birth_ownership_deployment_unsafe` | proprietario, modo, link, antenato o programma di sistema non sicuro |
| `birth_ownership_deployment_conflict` | destinazione o prenotazione gia' presente con identita' diversa |
| `birth_ownership_predecessor_invalid` | descrittore transitorio o fotografia viva non corrispondente |
| `birth_ownership_successor_conflict` | secondo candidato durevole per lo stesso predecessore |
| `birth_ownership_platform_unsupported` | percorso amministrativo richiesto fuori da Linux/systemd |
| `birth_ownership_closed_enforcement_required` | entrata produttiva negata prima di ogni mutazione perche' il diniego compilato non e' attivo |
| `birth_ownership_recovery_required` | stato parziale o ambiguo che non puo' essere adottato |

Un errore esterno non include percorsi personali, byte, chiavi o digest
osservati. La causa completa resta concatenata soltanto nel giornale
amministrativo protetto.

## 7. Criteri di uscita

Il gruppo 6 e' completo soltanto quando sono vere tutte le condizioni seguenti.

- Una catena di almeno due distribuzioni viene caricata a freddo senza conservare
  alberi storici e senza oggetti sigillati ereditati dal processo precedente.
- Assemblatore, firma, installazione e rilettura usano soltanto cataloghi e
  autorita' posseduti dal sistema.
- Il controllo transitorio e' installato e provato prima della pubblicazione
  del certificato; il controllo definitivo accetta soltanto la testa richiesta.
- Ogni servizio e ogni ingresso dell'installatore e' classificato. Nella
  topologia candidata non esiste un'unita' Metnos avviabile direttamente fuori
  dal catalogo; sul sistema reale l'inventario completo degli ingressi
  precedenti e' congelato e assegnato al passaggio del gruppo 7.
- Nella VM usa-e-getta il coordinatore raggiunge `PREFLIGHT_VERIFIED` e ogni
  ripresa ricostruisce l'evidenza dal disco. Sulle radici produttive il binario
  G6 nega prima di claim e `PREPARED`; durante il gruppo 6 non viene eseguita
  alcuna installazione sul server reale.
- `closed_build_enforcement()` resta `False` e nessun servizio reale viene
  commutato verso la distribuzione candidata.
- Prove mirate, prova Linux amministrativa, R1, guardie e suite portatile finale
  sono verdi.
- La matrice pubblica Linux e Windows termina con errore zero.

## 8. Budget delle prove e pubblicazione

Durante ogni incremento si eseguono soltanto:

1. le prove del simbolo modificato;
2. la singola prova produttiva del nuovo confine;
3. R1 soltanto quando cambia il grafo produttivo o il suo inventario;
4. regressioni precedenti soltanto se il nuovo codice attraversa quel confine.

Guardia normale, guardia chiusa, rendering dell'inventario e suite portatile
completa vengono eseguiti una volta alla fine. La matrice GitHub Linux/Windows
viene eseguita una volta alla chiusura del gruppo.

Ogni incremento riceve un commit sorgente separato su `main`, con footer
`RM-0008-Status: candidate-not-certified`. Gli incrementi gia' revisionati
possono essere pubblicati su `main` pubblico con commit incrementali marcati
per non avviare la matrice; l'ultimo commit avvia l'unica matrice completa.

Se una prova fallisce, prima di correggere si registrano punto esatto, fatto
osservato, ipotesi causale e risultato che distinguerebbe la correzione. Non si
accumulano modifiche successive sullo stesso errore senza una nuova misura.

## 9. Evidenze da conservare nel passaggio di consegne

Per ogni incremento il passaggio di consegne deve riportare:

- requisito e rischio distinto;
- simboli produttivi modificati;
- test mirati e prova del percorso reale;
- risultato della revisione avversariale;
- commit sorgente e pubblico;
- risultato GitHub, se applicabile;
- elementi non provati e gruppo che li possiede;
- prossimo passo unico.

Il documento va aggiornato dopo ogni nuova evidenza. Un altro agente deve poter
riprendere senza ricostruire la cronologia dai log o dalla conversazione.

## 10. Stato esecutivo B3 dopo il riesame avversariale

Il minimo percorso di chiusura B3 e' ora:

1. mantenere identici il guard canonico e il clone autonomo sui mutanti
   adversariali e sull'intero albero;
2. eseguire in parallelo le tre famiglie deterministiche centrali;
3. provare una sola volta i moduli prodotto toccati e il percorso undo
   autenticato;
4. congelare lo snapshot e far eseguire in parallelo due riesami read-only,
   uno di sicurezza e uno di regressione;
5. committare soltanto con zero finding P0, P1 e P2.

Lo stato misurato ai punti 1, 2 e 3 e' verde: 671 sorgenti, parita' completa
339/339, zero accessi dinamici e zero finding; `83`, `52` e `68` prove passate
con un solo skip di piattaforma previsto. Le regressioni prodotto indipendenti
hanno dato `62`, `63`, `56` e `199` prove passate; quattro prove di embedding
reale sono escluse per il modello BGE assente. Il caricamento per pathname di
`reverse_patterns_patch` e' stato sostituito dal catalogo vivo verificato e da
`load_admitted_module_v1`; `undo_last_turn` resta byte-identico al proprio
digest firmato. Non e' ancora autorizzato il commit: mancano i due verdetti del
punto 4.

## 11. Correzioni richieste dal terzo riesame B3

Il terzo riesame ha invalidato le ultime due frasi del paragrafo precedente:
il catalogo veniva ricaricato nel dispatch e `undo_last_turn` non conservava
una fotografia unica. La soluzione corrente applica invece queste condizioni
congiunte.

1. `undo_last_turn` apre una sola fotografia del catalogo e la passa fino al
   pattern reverse. Il dispatch non possiede alcun fallback che possa aprirne
   una seconda.
2. La porta rilegge il catalogo soltanto per confrontare un record runtime
   ordinario con l'autorita' corrente. Nel subprocess, dove il catalogo non e'
   montato integralmente, rilegge manifesto e firma della sola dipendenza e
   verifica la firma con le chiavi pubbliche trusted montate singolarmente.
3. Un record d'ambiente, un percorso e un digest scelti dal subprocess non
   costituiscono autorita'. Il test negativo in un processo nuovo altera la
   proiezione dopo l'avvio e non esegue i byte contraffatti.
4. I wrapper Synt dichiarano la dipendenza firmata e usano la stessa porta;
   tutte le ricerche dirette per pathname sono rimosse.
5. Guard e clone negano le forme equivalenti attraverso `sys.modules`,
   `FunctionType`, `runpy`, `importlib.util`, `load_module` e alias. I controlli
   negativi impediscono di trasformare omonimi locali e `re.compile` in falsi
   confini.
6. I due attraversamenti AST tardivi del parser produttivo convertono
   `MemoryError` negli errori stabili gia' previsti dal protocollo.

Le prove centrali sono state eseguite in tre processi paralleli indipendenti e
hanno prodotto `68`, `92` e `54 passed` con un solo skip di piattaforma. Le
prove reali non parallelizzabili hanno prodotto `23 passed` per l'undo e
`1 passed` per la dipendenza Bubblewrap. La parita' completa resta 339/339 su
671 sorgenti con zero finding. Il nuovo digest firmato di `undo_last_turn` e'
`sha256:8f819a52ce9f242ace86de74822058baf609c863557f7cdc4482439983f4f895`.

Il percorso minimo restante e' congelare questo diff e ottenere due revisioni
indipendenti read-only. Solo con due verdetti `P0=0`, `P1=0`, `P2=0` si esegue
il commit incrementale su `main` e si passa al nucleo preparatore B3. Nessun
aggiornamento dello store vivo e' autorizzato in questo incremento.

## 12. Correzioni richieste dal quarto riesame B3

Il quarto riesame ha dimostrato che il confronto con un catalogo ricaricato non
era sufficiente se le funzioni pubbliche di `loader` potevano essere sostituite
nello stesso interprete. Ha inoltre trovato un fallback relativo per la trust
root Windows, un mount che seguiva il link di una presunta chiave pubblica, due
lookup riflessi di `eval`/`exec` non censiti e un falso positivo delle chiusure
locali su un metodo omonimo a `runpy.run_module`.

Il contratto implementato e' ora il seguente.

1. Le funzioni del catalogo sono catturate all'import del runtime; una loro
   successiva sostituzione sul modulo `loader` non modifica la porta. Dopo il
   confronto col catalogo, la porta autentica direttamente manifesto, firma e
   binding prima di leggere il codice.
2. Il subprocess proiettato non richiede `loader`. Se il modulo non e' montato,
   il ramo ordinario e' indisponibile e soltanto il record proiettato, sigillato
   e firmato puo' proseguire.
3. `METNOS_USER_CONFIG`, home e fallback Windows devono produrre una radice
   assoluta. In caso contrario non esiste alcuna trust root; non si usa la
   directory corrente.
4. Il file `<signed_by>_pub.bin` deve essere un file regolare non-link,
   non-reparse e di 32 byte. La directory delle chiavi e le chiavi estranee non
   vengono montate.
5. Guard e clone negano anche `eval` e `exec` recuperati dal dizionario di
   `builtins`. Le chiusure locali usano il binding importato per distinguere
   `runpy.run_module` da un metodo locale omonimo.

Le prove discriminatorie sono verdi: porta `26`, guard `68`, clone `96`,
manifesto `56 passed, 1 skipped`, undo e dispatch `18`, Synt/indici/contratti
`60 passed, 3 skipped`, Bubblewrap reale `1 passed`. La parita' integrale resta
339/339 su 671 sorgenti, con zero finding. `compileall`, `git diff --check` e la
firma dell'executor undo sono verdi.

I riesami precedenti non autorizzano il commit perche' fotografavano un diff
diverso. Il prossimo e unico passo e' ottenere due verdetti indipendenti
read-only sul contenuto corrente, entrambi `P0=0`, `P1=0`, `P2=0`.

## 13. Candidato B3 congelato dopo il quinto riesame

Il quinto riesame ha separato tre rischi che non potevano essere chiusi
aggiungendo altri nomi a una denylist AST.

1. La riflessione Python non e' enumerabile in modo completo. La garanzia
   primaria diventa quindi il digest positivo dell'intero censimento Python,
   confrontato col valore atteso compilato nel verifier della release
   precedente gia' fidata. Il candidato non puo' auto-approvarsi modificando
   sorgente e copie del pin. La copia amministrativa deve essere byte-identica
   a quella runtime. Il guard AST resta un controllo indipendente di topologia.
2. Il record di una dipendenza e' trasportato nell'ambiente e puo' essere
   riscritto dal child. Firma e digest impediscono byte inventati, ma non il
   replay di un fratello B realmente firmato. Il mount generale `/opt` e'
   stato rimosso: il child vede soltanto A, dichiarata dal parent, insieme alla
   runtime, all'interprete e alla chiave pubblica esatta. La sandbox OS e'
   obbligatoria per ogni executor ordinario, anche builtin e handcrafted; il
   solo percorso naked richiede nome, metadati, file e digest firmato esatti
   del broker undo gia' revisionato.
3. Il broker undo e' intenzionalmente fuori Bubblewrap. Un nuovo contratto
   Birth con pattern ignoto poteva quindi far eseguire il proprio top-level nel
   broker prima della chiamata a `reverse()`. La porta autenticata rifiuta ora
   prima della compilazione ogni reverse non dichiarativo salvo nove
   accoppiamenti nome-digest revisionati. Il broker resta identico alla firma
   esistente; nessuna firma diretta o pubblicazione nello store vivo e' stata
   eseguita.

Sono stati chiusi anche il replay tramite hardlink della chiave pubblica e tre
difetti del wrapper Synt: parent non subprocess, arita' della funzione
`invoke` e valori JSON che in precedenza producevano letterali Python non
validi.

Il digest congelato dei 671 sorgenti e'
`sha256:87f7d309555793642066f778013be58024421b2026d00a2769e15c3324e1f4b5`.
Le due scoperte indipendenti producono 339 fatti identici e nessun finding. Le
suite centrali terminano con `69`, `111` e `57 passed`, piu' un solo skip di
piattaforma. Le regressioni mirate terminano con `55`, `84` e `45 passed`; i
tre skip Synt/prodotto sono ambientali gia' dichiarati. La prova Bubblewrap
reale A/B termina con `1 passed`. Il controllo `--birth-closed`, la firma di
`undo_last_turn` e `git diff --check` sono verdi.

Non e' ancora autorizzato il commit. Servono due review da zero sul candidato
congelato e due esiti esatti `P0=0`, `P1=0`, `P2=0`. Solo dopo si committa su
`main`, si pubblica incrementalmente, si richiede errore zero su Linux e
Windows e si continua il nucleo preparatore B3.

## 14. Autorizzazione del candidato architetturale B3

Il 29 agosto 2026 le due review finali indipendenti hanno approvato il
candidato con `P0=0`, `P1=0`, `P2=0`. La radice riesaminata e'
`sha256:87f7d309555793642066f778013be58024421b2026d00a2769e15c3324e1f4b5`;
il censimento contiene 671 sorgenti e le due scoperte producono 339 fatti
identici senza finding. Le pagine italiane e inglesi descrivono ora il rifiuto
fail-closed, la rimozione del mount generale `/opt` e l'eccezione undo
vincolata a file e digest esatti.

Il commit su `main` e la pubblicazione incrementale sono autorizzati. La loro
evidenza e l'esito Linux/Windows vanno aggiunti dopo l'osservazione pubblica.
Il nucleo preparatore B3 resta il passo successivo e RM-0008 resta `active`.

## 15. Compatibilita' del sandbox col Python del runner pubblico

La matrice pubblica `33258481590` ha confermato sette job su otto. Suite
portabili, Windows, concorrenza, manifesto e ACL erano verdi. L'unico errore
era deterministico nel test Birth delegato Linux: il runner GitHub usa un
Python non-venv sotto `/opt/hostedtoolcache`, mentre il sandbox, dopo la
rimozione necessaria del bind generale `/opt`, montava un prefisso soltanto
nel caso venv. `bwrap` terminava quindi con `execvp .../bin/python: No such
file or directory`.

La correzione non ripristina `/opt`. Il sandbox deriva esclusivamente i quattro
prefissi dell'interprete attivo, ne verifica risoluzione, ampiezza e copertura
di eseguibile, `stdlib` e `platstdlib`, poi monta ciascuna radice esatta in sola
lettura. Per i venv raggiunti tramite symlink conserva la destinazione
lessicale ma usa come sorgente il target canonico verificato. Un prefisso
generico o che contiene codice del prodotto fallisce chiuso.

Il primo riesame ha trovato e bloccato la regressione dei venv tramite symlink;
dopo la correzione il secondo riesame ha concluso `P0=0`, `P1=0`, `P2=0`.
Le prove correnti sono: sandbox `68 passed`, gate mirati `199 passed`, prova
Bubblewrap A/B privata e pubblica `1 passed` ciascuna, suite pubblica completa
`749 passed, 25 skipped` e publisher `--check` verde. Le nuove radici sono
`sha256:9097f35f635e88f52b360f9540fbfdb9b25b4567384a5d6264a74076d714b4c0`
per 671 sorgenti private e
`sha256:f66473c54d13f7dedb43b8f357f04b7da83f906d6e2e42c0296b44bd14e29a46`
per 659 sorgenti pubbliche.

Il wrapper root `systemd-run`, non eseguibile localmente senza password `sudo`,
e' stato provato dalla matrice pubblica. Il commit sorgente `afd53ebe` e il
commit pubblico `86e69ec5b95b5c88af924db3bc027e774bd06ea7` hanno prodotto il
run `33259624116`: tutti i job sono `success`, compresi Birth delegato Ubuntu,
suite completa Windows e riepilogo di certificazione. La base architetturale
B3 converge quindi a errore zero. Il passo successivo e' il nucleo preparatore
B3; RM-0008 non e' chiuso.

## 16. Confine I/O del preflight operativo

La lettura operativa non concatena aperture assolute di singoli file. Un file
puo' essere stabile mentre il suo genitore o un altro oggetto del grafo viene
sostituito, producendo una fotografia composta da epoche diverse. Il preflight
apre percio' una sola volta la radice ownership fissa, apre i discendenti in
modo relativo con `dir_fd` e `O_NOFOLLOW`, mantiene vivi gli handle e confronta
inventario e identita' prima e dopo la decodifica. Prima del ritorno confronta
anche la radice nuovamente raggiunta per pathname con l'handle iniziale.

L'implementazione procede in due incrementi soltanto. Il primo cattura una
fotografia privata non autorizzante e prova sostituzioni e aggiunte fra le
fotografie A e B. Il secondo autentica chiavi e archivi, completa il journal
V2 fino a `PREFLIGHT_VERIFIED` e riconcilia claim, transazione, predecessore,
build, cutover, testa e puntatore richiesto. `INITIAL` e' ammesso soltanto se
ancora e puntatore sono assenti e i tre archivi della catena sono vuoti; la
presenza di `predecessor-v1.json` non sceglie il regime. Questa divisione e'
un confine di verifica, non un rinvio di funzionalita': staging e firma restano
bloccati finche' entrambi gli incrementi non sono conclusi.

Il primo incremento e' implementato: copre entrambi i regimi, rileva le
mutazioni fra A e B e nega symlink/hardlink, modi errati, coppie incomplete e
rebound della radice. Il primo riesame ha inoltre richiesto la riverifica degli
antenati in B, il diniego del lock isolato nello stato iniziale e la chiusura
dei temporanei dell'ancora: i tre rilievi sono corretti e coperti da mutanti.
La suite mirata conta `138 passed, 1 deselected`; il solo
deselezionato e' il pin source-review, che per contratto si aggiorna sul
candidato operativo completo. Tre riesami finali del delta corretto hanno
concluso `P0=0`, `P1=0`, `P2=0`. L'incremento e' approvato localmente; il passo
successivo autentica firme e catena e riconcilia il grafo prima di qualunque
commit o aggiornamento del pin.

## 17. Difetti live osservati durante B3

I turni `052f0f91` e `9119a307` sono difetti laterali, non criteri nuovi di
RM-0008. Vengono comunque corretti prima del pin finale, perche' le sorgenti
modificate devono essere incluse nella medesima distribuzione attestata.

1. Il fallback amministrativo deriva i binari dalla grammatica di
   canonicalizzazione. Invocazione e polarita' provengono dal registro
   traducibile; le risorse sintattiche di sicurezza sono native, pronte e a
   revisione manuale. Il vecchio elenco shell IT/EN e' migrato nel
   concetto `admin.shell_intent`, con equivalenza editoriale congelata. Il
   ramo shell applica lo stesso controllo di polarita'. Un nome futuro prova
   la proprieta' generale; prosa, negazioni, inibizioni, revoche, punteggiatura
   e traduzione parziale provano il diniego. Lo stato `unavailable` rende
   esplicita una grammatica nativa incompleta; il daemon non chiama il modello
   se il confine di revisione umana non e' caricabile. Il ping reale verso il
   PC risponde.
2. La risoluzione WinGet considera l'insieme delle identita' canoniche, non il
   numero di righe. Duplicati equivalenti convergono; identita' distinte o
   invalide restano fail-closed e producono soltanto diagnostica non
   consumabile, deduplicata e limitata con conteggio esplicito.
3. Le menzioni di destinazione sono risolte in ordine. Una correzione esplicita
   successiva prevale; una revoca finale impedisce il riuso di sticky e default,
   anche senza device registrati. Gli alias deboli non scavalcano riferimenti
   espliciti. Alias device e indirizzo IP non vengono inventati: oggi il registro non
   persiste un address autenticato. L'eventuale estensione del protocollo e'
   separata e deve essere generale per ogni device.

La verifica live ha aggiunto il tratto mancante fra selezione ed esecuzione:
il secondo filtro del proposer conserva il nome runtime `admin` soltanto quando
la grammatica lo ha già selezionato; tutti i builtin a verbo unico esposti al
planner attraversano il registro e il dispatcher in-process; un esito
`approval_required` arresta gli step successivi; una ricevuta
`execute_silent` riuscita soddisfa l'azione di sistema e diventa la risposta
autorevole nei turni composti soltanto da operazioni amministrative. Il turno
reale `13f78d922e1c47b8` ha eseguito `ping -c 4 192.168.1.137` con quattro
pacchetti trasmessi, quattro ricevuti e zero per cento di perdita. `mount` e
`umount` sono ammessi dal medesimo inventario; i 33 test CIFS/SMB/NFS ne provano
il percorso approval-controlled senza introdurre un mount reale.

Le prove locali correnti sono `187 passed, 4 subtests passed` sul perimetro
mirato; la regressione i18n con i consumer collegati e' `594 passed,
1.144 subtests passed`. Due revisioni indipendenti finali concludono entrambe
`P0=0`, `P1=0`, `P2=0`; compilazione e `git diff --check` sono verdi. RM-0005 e' stata riaperta
perche' il closeout storico non aveva
censito tutti i lessici del prefilter; la correzione shell/ping non autorizza
una nuova chiusura finche' il residuo documentato non e' concluso.
La review finale Dropbox conclude `P0=0`, `P1=0`, `P2=0`. La firma authoring modificata
non viene aggiornata attraverso il percorso legacy: sara' prodotta dalla porta
Birth completa insieme agli altri byte B3. Il lavoro principale riprende
dall'autenticazione della fotografia fixed-root e dalla riconciliazione del
grafo.

## 18. Incremento B3 di autenticazione della fotografia

Il nucleo locale autentica esclusivamente la fotografia fixed-root catturata
nel primo incremento e non riapre per pathname gli oggetti di autorita'. Sono
verificati registri, manifesti e firme, certificati, teste, ancora e puntatore;
la riconciliazione comprende catena, claim, transazioni V2 `000..006`, journal
V1, disposizione legacy e descrittore del predecessore. Il risultato non e'
ancora un'attestazione operativa: i riferimenti ad albero installato, systemd e
attestazione definitiva restano strutturali finche' i relativi controlli vivi
non saranno implementati.

La gestione dei crash segue il protocollo e non il massimo nome in archivio.
Un claim puo' essere pendente e i prefissi `PREPARED` o
`RECEIPTS_COMPLETE` possono precedere la build archiviata. Anche il record
`BUILD_VERIFIED` precede l'append dello step 13: la build puo' ancora mancare
all'esatto killpoint di sequenza 4 e diventa obbligatoria quando una testa la
seleziona o il journal raggiunge sequenza 5. Il certificato puo' essere pronto
a sequenza 2; al bootstrap la pubblicazione e' provata dall'ancora fissa,
mentre negli update e' provata dall'oggetto archiviato. Una testa
successiva gia' aggiunta durante la ripresa non diventa autorevole finche' il
puntatore richiesto e il record `HEAD_REQUIRED` non convergono. Il
`predecessor-v1` resta legato alla transazione iniziale, mentre tutte le
release V1 conservano lo stesso `administrative_bundle_hash`.

Il predecessore e' assente prima della transazione iniziale, puo' mancare al
solo `PREPARED` e deve esistere da `RECEIPTS_COMPLETE`. La finestra fra CAS di
`required-head` e record `HEAD_REQUIRED` e' riconosciuta solo col journal a
`BUILD_VERIFIED`. La riconciliazione e' bidirezionale: build, cutover e testa
archiviati richiedono claim e transazione nelle rispettive soglie minime 4, 2
e 4, quindi una coppia firmata orfana non diventa uno stato di ripresa.
Prima della prima testa richiesta, ancora e journal di bootstrap a sequenza 2
o 4 formano soltanto uno stato autenticato di ripresa, mai una catena
operativa.

La matrice probatoria minima contiene 31 casi discriminanti nuovi. I mutanti
di firma conservano copie fisse, frame e digest coerenti; mutanti separati
coprono i tre legami di predecessore e le associazioni con build, certificato
e testa. La suite
preflight completa, escluso il solo pin source-review da aggiornare a candidato
operativo congelato, produce `169 passed, 1 deselected`; WinGet/piping produce
`68 passed, 8 subtests passed`. Compilazione e controllo del diff sono verdi.
Le due review avversariali indipendenti finali concludono entrambe `P0=0`,
`P1=0`, `P2=0`. Il candidato resta non committato e non firmato: il passo
successivo collega catalogo, descrittore, prerequisito, TCB e configurazione
systemd senza anticipare l'autorita' operativa dei comandi.

## 19. Congelamento del digest e scomposizione del prossimo incremento B3

`startup_prerequisite_digest` e' congelato come SHA-256 grezzo dei byte
canonici completi del documento `startup-prerequisite-v1`. Il valore testuale
e' esattamente `sha256:` seguito da sessantaquattro cifre esadecimali
minuscole. Non viene anteposto alcun dominio e non si calcola il digest su un
oggetto JSON ricostruito, su un documento privo di `prerequisite_id` o su una
selezione di campi. In formula:

```text
startup_prerequisite_digest =
    "sha256:" || hex_lower(SHA256(startup_prerequisite_canonical_bytes))
```

La distinzione e' intenzionale. `prerequisite_id` continua a identificare il
contenuto logico col proprio dominio e con la regola del documento privo del
campo identificativo; `startup_prerequisite_digest` prova invece i byte
completi effettivamente pubblicati e riletti. Il journal conserva quindi sia
il legame semantico sia il legame byte-per-byte, senza introdurre un secondo
dominio implicito o una ricostruzione che potrebbe divergere fra produttore e
verificatore. Un file con identificativo valido ma byte, framing o
canonicalizzazione differenti non soddisfa il digest del journal.

L'analisi causale del confine successivo ha mostrato che catalogo e descrittore
possono essere autenticati integralmente dalla distribuzione gia' selezionata,
mentre TCB e configurazione systemd richiedono osservazioni vive, subprocess e
blocchi diversi. Riunire questi due confini nello stesso sottotaglio renderebbe
difficile attribuire un diniego a provenienza dei byte, deriva degli
eseguibili oppure mutazione del manager. Il lavoro viene pertanto separato
senza ridurre alcun controllo:

1. il prossimo sottotaglio e' puro e non autorizzante. Aggiunge al preflight
   autonomo, stdlib-only, i cloni chiusi dei codec di catalogo, descrittore di
   deployment e prerequisito, con parita' rispetto ai produttori canonici;
   deriva inoltre il grafo candidato soltanto dai byte firmati della release
   selezionata e ne verifica identificativi, copertura, artefatti, frammenti e
   radici fisse. Non esegue subprocess, non legge lo stato systemd effettivo,
   non pubblica o sostituisce prerequisiti e non rende operativi `check`,
   `check-all` o `launch`;
2. un sottotaglio successivo e separato misura gli eseguibili amministrativi e
   la TCB OpenSSL con percorsi fissi, ambiente chiuso e riverifica prima e dopo
   ogni subprocess;
3. un ulteriore sottotaglio separato costruisce e confronta la fotografia
   systemd effettiva sotto i blocchi prescritti, applica i killpoint del
   prerequisito e soltanto allora completa l'attestazione viva.

I tipi decodificati e il grafo candidato del primo punto restano osservazioni:
non sono capability, non possono essere forniti dal chiamante e non vengono
accettati dall'entrata operativa. Fino alla conclusione dei due sottotagli vivi
successivi, il dispatch pubblico continua a fallire chiuso con
`birth_ownership_missing`.

## 20. Esito del sottotaglio puro B3

Il primo punto della scomposizione e' implementato e approvato localmente. Il
preflight autonomo lega catalogo, descrittore e prerequisito ai byte firmati,
al manifesto, alla transazione e al predecessore. Copertura, ruolo, tipo,
dimensione e hash sono verificati su ogni artefatto; il programma
amministrativo non e' piu' una sola dichiarazione del descrittore, ma un file
catturato e confrontato byte per byte. I frammenti systemd vengono riparsati e
il grafo candidato produce gli undici link V1 esatti. Per i target interni alla
release coincidono byte catturati, dimensione del manifesto, hash del file e
hash path-aware del catalogo.

L'equivalente autonomo di `_source_identity` congela l'intera ricetta V1 con un
fingerprint strutturale. I soli valori di contesto gia' legati al descrittore
sono sostituiti da oggetti JSON tipati, impossibili da imitare nei campi stringa
del catalogo. Account, gruppi supplementari, home e Python amministrativo sono
riconciliati col descrittore. Home e dati del servizio non possono trovarsi
dentro la radice immutabile della release. Il predecessore coincide col
catalogo corrente soltanto nel bootstrap; nelle release successive resta
correttamente ancorato alla fotografia iniziale.

I mutanti probatori ricostruiscono identificativi e digest a valle e coprono:
preflight assente o alterato, source recipe modificata, marker preinserito,
account divergente, target interno con dimensione dichiarata errata,
predecessore divergente alla release uno e predecessore iniziale legittimo in
un aggiornamento. L'evidenza finale e' `27 passed` nel file nuovo, `86 passed`
nel perimetro materiali/codec/catalogo e `169 passed, 1 deselected` nella suite
autonoma; il solo caso escluso e' il pin source-review da aggiornare sul
candidato operativo completo. Import isolato `-I -S`, compilazione e controllo
del diff sono verdi. Due review indipendenti concludono `P0=0`, `P1=0`,
`P2=0`.

Il risultato resta osservazionale e non viene accettato dal dispatch. Pin,
firme, staging, store vivo, commit e pubblico restano invariati. Il passo
successivo misura gli eseguibili amministrativi e la TCB OpenSSL; la fotografia
systemd effettiva resta il sottotaglio seguente.

## 21. Esito del sottotaglio TCB B3

Il secondo sottotaglio e' implementato localmente. La misura dei quattro
eseguibili amministrativi precede l'autenticazione ownership; la verifica delle
firme usa lo stesso OpenSSL canonico misurato e la misura viene rivalidata
subito dopo. Percorsi e hash sono poi legati al descrittore e al prerequisito.
La stessa osservazione copre tutti i target del catalogo esterni alla release,
senza affidarsi a `PATH`, shell o ambiente ereditato.

La TCB OpenSSL deriva direttamente l'interprete dall'ELF64, acquisisce due
volte la chiusura prodotta da `loader --list`, legge la directory moduli con il
comando chiuso `openssl version -m` e include tutti i file regolari presenti.
Risoluzione limitata dei link, controlli su proprietario, modo e ACL, lettura
handle-bound e confronti ripetuti chiudono i cambi di eseguibile, loader,
libreria o modulo durante la misura. Il documento e ogni file usano i domini e
il framing definiti nel §3.5.4.

Una review avversariale ha rilevato che il primo binder produttivo riceveva il
core dei materiali separatamente dalla fotografia ownership. La correzione
richiede ora la capability produttiva autenticata e seleziona da essa testa
richiesta, build, ultimo record stabile e predecessore, usando esclusivamente
la TCB gia' contenuta nella medesima capability. Un secondo wrapper nominale
separa l'osservazione produttiva dall'osservazione della seam di prova.

Le prove finali del sottotaglio sono 44 e non duplicano i test gia' esistenti
su timeout, limiti del runner e verifica Ed25519. Il perimetro
TCB/materiali/codec/catalogo produce `130 passed`; l'intera suite preflight,
escluso il pin source-review rinviato, produce `240 passed, 1 deselected`.
Compilazione, esecuzione isolata `-I -S` e controllo del diff sono verdi. Il
risultato resta non operativo. Due review avversariali indipendenti sullo
snapshot finale concludono `P0=0`, `P1=0`, `P2=0`: nessun pin, firma, store,
commit o pubblicazione viene anticipato. Il prossimo sottotaglio e' la
fotografia systemd effettiva.

## 22. Gate laterale RM-0005 e percorso minimo di rientro

Prima di proseguire con la fotografia systemd, il census lessicale che aveva
riaperto RM-0005 viene chiuso come gate indipendente. Il percorso minimo non
duplica le prove B3: esegue una sola suite i18n completa, i soli consumer
modificati, il census runtime, il catalogo firmato e uno smoke produttivo.

La seconda suite i18n e' verde (`539 passed, 1.162 subtests passed`), ma la
review incrociata ha trovato tre cause ancora aperte prima della firma:
census non universale sui contenitori linguistici; confine amministrativo non
interamente i18n/ready-only; audit documentale non unico e non tipizzato su
tutti i percorsi. Il percorso minimo non cambia: si correggono soltanto questi
tre confini, si eseguono i mutanti discriminanti e una sola regressione ampia,
quindi si richiede review `P0=0, P1=0, P2=0`.

La rigenerazione dei contratti builtin resta rinviata fino a quel verdetto.
Nessun rilievo RM-0005 puo' essere differito a RM-0008. Dopo firma, catalogo
verde, pubblicazione incrementale su `main`, GitHub Actions e smoke live verdi,
il lavoro ritorna direttamente al sottotaglio systemd di G6-B3.

Il riesame successivo ha aggiunto un gate P0: firma, Law 1, carta, consenso e
fire amministrativi devono consumare la stessa closure argv, inclusi wrapper,
path normalizzati e device distruttivi; il consenso deve essere esatto e
one-shot. In parallelo, census e audit vengono chiusi sui dataflow e sulle
identita' reali, non su esempi lessicali. Nessuna attivita' G6-B3, firma o
pubblicazione puo' precedere la nuova prova a errore zero.
