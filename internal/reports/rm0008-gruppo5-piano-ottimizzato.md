# RM-0008 gruppo 5 — autorita' e coordinatore F4

## 1. Stato di ingresso e perimetro

Il gruppo 4 e' chiuso. Il commit pubblico `a4d2dba` e il ciclo GitHub Actions
`33165938001` hanno concluso verdi tutti i nove lavori Linux e Windows. Nel
repository sorgente il punto di partenza e' `9fd7edf4`. La guardia normale, la
guardia `--birth-closed` e il rendering dell'inventario sono a zero; la suite
portatile conta 326 prove verdi, 23 non applicabili e zero errori.

Il gruppo 5 corrisponde al §23.6.5 della roadmap. Completa le autorita' di
deployment e la macchina recuperabile del coordinatore F4. Non assembla la
distribuzione, non installa la catena completa, non modifica le unita' di avvio
e non attiva ancora la politica chiusa. Queste operazioni appartengono ai
gruppi 6 e 7.

La rivendicazione del gruppo e' la seguente:

> Le tre autorita' di distribuzione, cutover e testa sono separate, persistite
> e rilette da registri posseduti da `root`; il coordinatore usa soltanto
> correnti e ricevute autenticate, produce la prova canonica completa di
> manutenzione e recupera in avanti quando il certificato ha superato il punto
> di non ritorno.

Il gruppo non dichiarera' un cutover installato. Il percorso produttivo dovra'
rifiutare il punto di non ritorno finche' il gruppo 6 non gli consegnera' una
attestazione sigillata del controllo preliminare transitorio e del predecessore
autenticato.

## 2. Difetti osservati prima della codifica

Il riesame del codice e una revisione avversariale indipendente hanno trovato
cinque lacune concrete. Il piano le tratta come prerequisiti, non come errori da
nascondere nei test.

| Lacuna | Evidenza nel codice | Conseguenza |
|---|---|---|
| fabbrica di riattestazione non produttiva | `_assemble_reattestation_core()` legge `birth.publisher_options`, che `_BirthCore` non possiede | il coordinatore non puo' riusare oggi il runtime sigillato |
| richiesta di riattestazione selezionabile | `ReattestationRequest` riceve dal chiamante `producer_receipt`, attore e motivo | il chiamante puo' scegliere dati autorevoli che devono appartenere a Birth |
| registri soltanto in memoria | non esistono codec e loader installati per le tre autorita' | una prova con dizionari costruiti dal test non certifica l'installazione |
| registro multipurpose della catena | `OwnershipChainStore` usa un solo `OwnershipCutoverRegistry` per cutover e testa | non e' dimostrata la separazione `ownership_cutover_v1` / `ownership_head_v1` |
| prova di manutenzione incompleta | `prove_stack_stopped()` perde `load_state` e `main_pid` e restituisce una mappa ridotta | non puo' alimentare `canonical_maintenance_proof()` senza inventare dati |

Esiste inoltre un limite installato gia' delimitato: le unita' correnti non
possiedono ancora un `ExecStartPre` ownership dominante. Il punto di non ritorno
del certificato non puo' quindi essere attraversato sull'installazione reale nel
gruppo 5. La sua installazione e la prova di copertura di tutti gli ingressi sono
criteri del gruppo 6.

## 3. Semplificazione scelta

Il gruppo resta diviso in due soli incrementi sorgente e usa una sola matrice
pubblica finale.

1. **G5-A — autorita' e registri separati.** Introduce i formati installati,
   la predisposizione recuperabile e i loader a freddo delle tre autorita'.
   Corregge nello stesso incremento `OwnershipChainStore`, affinche' cutover e
   testa non possano condividere il registro.
2. **G5-B — fabbrica sigillata e coordinatore.** Ripara la riattestazione
   produttiva, rende completa la prova di manutenzione e aggiunge la macchina
   durevole fino a `CERTIFICATE_PUBLISHED`, compreso il recupero dopo il punto
   di non ritorno.

Non vengono riprovati i codec crittografici gia' certificati. Le nuove prove
osservano soltanto separazione delle autorita', composizione produttiva,
transizioni durevoli e recupero. La suite portatile completa viene eseguita una
sola volta alla fine del gruppo.

## 4. G5-A — autorita' e registri separati

### 4.1 Layout e formati

La radice amministrativa V1 resta
`/var/lib/metnos/executor-birth`, `root:root 0755`. Sotto di essa il gruppo
predispone un contenitore di autorita' con nomi fissi. Il resolver produttivo
non accetta percorso da ambiente, richiesta o opzione della riga di comando.
Il nucleo della predisposizione riceve una capacita' di directory gia'
autenticata; il resolver installato che apre la radice fissa verra' collegato
dal gruppo 6.

Le tre identita' sono:

| Registro | Scopo esclusivo | Chiave privata | Registro pubblico |
|---|---|---|---|
| distribuzione | `closed_distribution_v1` | `0600`, solo `root` | `0644`, `root:root` |
| certificato | `ownership_cutover_v1` | `0600`, solo `root` | `0644`, `root:root` |
| testa | `ownership_head_v1` | `0600`, solo `root` | `0644`, `root:root` |

Ogni registro e' un JSON ASCII canonico V1 con schema chiuso, chiave pubblica
Ed25519, identificativo derivato dai byte pubblici e un solo scopo. I registri
del certificato e della testa possono continuare a produrre oggetti
`OwnershipCutoverRegistry`, ma devono provenire da file distinti e il loader
deve rifiutare scopi multipli. Il registro di distribuzione produce soltanto
`DistributionRegistry`.

Il loader restituisce un bundle sigillato con tre membri nominalmente distinti.
La costruzione fallisce se due chiavi pubbliche coincidono oppure se una di esse
coincide con una chiave autore, Admission o Producer riletta dal set Birth
autenticato. La separazione confronta i 32 byte pubblici, non soltanto nomi,
prefissi o scopi.

### 4.2 Predisposizione e recupero

La predisposizione genera le tre chiavi una sola volta in una directory di
transazione sullo stesso filesystem. Scrive file esclusivi, sincronizza ogni
file e directory, rilegge con i loader produttivi, registra checkpoint canonici
monotoni e pubblica il contenitore finale con rename senza sovrascrittura.

Un nuovo processo tratta cosi' gli stati osservabili:

- contenitore finale completo: rilegge tutto e restituisce gli stessi byte;
- transazione riconosciuta e coerente: completa soltanto i passi mancanti;
- file finale parziale, doppia transazione o byte non concordanti: errore
  `birth_ownership_authority_recovery_required`;
- destinazione gia' presente ma diversa: conflitto immutabile;
- permessi, proprietario, link, reparse point o numero di link errati: rifiuto
  prima di caricare una chiave.

Il risultato restituito dal provisioner non costituisce prova. Il criterio di
successo e' una nuova apertura a freddo del contenitore finale con i loader
produttivi.

### 4.3 Correzione della catena

`OwnershipChainStore` deve ricevere due registri separati e nominati:
`cutover_registry` per `append_cutover()` e `head_registry` per
`append_head()`, `read_required_head()` e le verifiche della catena. Il vecchio
argomento generico `registry` viene rimosso; non resta un adattatore che accetti
un registro multipurpose.

Le prove esistenti della catena vengono riscritte con due coppie Ed25519
diverse. Un mutante che usa la chiave di cutover per una testa, o viceversa,
deve fallire con `birth_ownership_key_unauthorized`.

### 4.4 Prove minime G5-A

- codec canonico e rifiuto di campi extra, duplicati, scopo multiplo e
  identificativo non derivato;
- tre chiavi pubbliche diverse tra loro e da autore, Admission e tutti i
  Producer;
- installazione, nuova apertura a freddo e retry byte per byte identico;
- arresto dopo ciascun file e ciascun checkpoint, seguito da ripresa in un
  processo nuovo;
- su Linux delegato eseguito come `root`: proprieta' e modalita' esatte e
  tentativo di scrittura del registro da parte dell'identita' di servizio
  rifiutato;
- catena con registri separati e due mutanti di scambio rifiutati;
- nessuna prova basata soltanto su `chmod` in un processo non privilegiato.

### 4.5 Criterio di uscita G5-A

- il cold loader ricostruisce tre registri distinti senza valori ricevuti dal
  provisioner;
- nessuna chiave di deployment coincide con una chiave Birth;
- `OwnershipChainStore` non puo' essere costruito con un solo registro;
- prove mirate e cella Linux `root` verdi.

## 5. G5-B — fabbrica sigillata e coordinatore

### 5.1 Riparazione della riattestazione produttiva

`_BirthCommitPublisher`, che gia' possiede anello autore, verificatori
Admission e radice dello store, espone al solo nucleo sigillato tre operazioni
ristrette: acquisizione della corrente esatta, persistenza della ricevuta di
riattestazione e rilettura della ricevuta. Non espone chiavi, anelli, percorsi o
opzioni. `_assemble_reattestation_core()` usa queste operazioni e smette di
leggere il campo inesistente `publisher_options`.

Il bundle Birth contiene inoltre una fabbrica sigillata dedicata al cutover.
Il suo ingresso e' soltanto `CurrentGeneration`. La fabbrica:

1. acquisisce la generazione dalla porta autenticata del publisher;
2. deriva origine dal `ManifestRef` e paternita' dalla tabella chiusa;
3. usa l'autorita' Producer amministrativa gia' predisposta per
   `installer_phase3`, senza consegnarne la chiave;
4. usa attore, operazione, motivo, obiettivo, nonce e `request_id` fissi e
   deterministici;
5. registra o rilegge la `ProducerReceipt` dal registro durevole;
6. restituisce internamente una `ReattestationRequest` sigillata.

L'API produttiva `reattest_current_generation()` riceve quindi soltanto la
corrente. Il costruttore dati e la funzione che accettano una richiesta restano
esclusivamente come giuntura privata delle prove. Nessun chiamante produttivo
puo' fornire `producer_receipt`, chiave, origine, paternita', attore o motivo.

### 5.2 Prova canonica completa di manutenzione

`prove_stack_stopped()` conserva l'ordine chiuso delle unita' e restituisce per
ciascuna esattamente `scope`, `unit`, `load_state`, `active_state` e
`main_pid`. L'elenco e' ordinato per `(scope, unit)`, senza duplicati, e include
tutte le unita' di `CONTRACT_CUTOVER_UNITS` piu'
`system:metnos-http.service`.

Il modulo della guardia passa questi stessi valori a
`canonical_maintenance_proof()`. Non esiste un secondo elenco nel
coordinatore. Il codec viene rafforzato con un controllo dell'insieme completo
quando la fonte e' produttiva; un sottoinsieme ben formato deve essere
rifiutato.

Sotto lo stesso blocco il coordinatore acquisisce una prova prima del
censimento e una subito prima della firma. Entrambe devono essere canoniche e
descrivere l'insieme completo; la seconda, non una fotografia storica scelta
dal chiamante, viene legata al certificato.

### 5.3 Registro durevole del coordinatore

Il registro amministrativo e' posseduto da `root`, ha schema chiuso e contiene
almeno gli stati monotoni:

```text
PREPARED
RECEIPTS_COMPLETE
CERTIFICATE_PUBLISHED
BUILD_VERIFIED
HEAD_REQUIRED
PREFLIGHT_VERIFIED
```

Il gruppo 5 attraversa produttivamente soltanto i primi due stati. La
pubblicazione del certificato e' abilitata soltanto da un'attestazione sigillata
dei prerequisiti di avvio, che verra' prodotta dal gruppo 6. Il gruppo 5 prova
in ambiente isolato la transizione e il recupero di
`CERTIFICATE_PUBLISHED`, ma non la presenta come passaggio installato.
`BUILD_VERIFIED`, `HEAD_REQUIRED` e `PREFLIGHT_VERIFIED` sono riconosciuti dal
codec e dalla monotonia, ma restano non attraversati fino al gruppo 6.

Ogni record lega almeno `request_id`, build chiusa verificata, predecessore,
hash dell'inventario, versione della guardia, prova completa delle correnti,
hash della manutenzione, identificativo del certificato e digest dei byte
payload/firma quando disponibili. Ogni avanzamento viene scritto, sincronizzato
e riletto prima del passo successivo.

Il registro e gli artefatti non sono due verita' alternative. All'apertura il
coordinatore confronta sempre lo stato dichiarato con i file autenticati:

- prima del payload del certificato, una firma orfana concordante puo' essere
  completata soltanto dalla stessa richiesta;
- il rename del payload `ownership-cutover-v1.json` e' il punto di non ritorno;
- se il payload autenticato esiste ma il journal e' ancora
  `RECEIPTS_COMPLETE`, il recupero riconosce il punto di non ritorno dai byte,
  verifica tutti i legami e registra `CERTIFICATE_PUBLISHED`;
- se il journal dichiara `CERTIFICATE_PUBLISHED` ma la coppia autenticata non
  esiste, il sistema resta fermo con
  `birth_ownership_recovery_required`;
- dopo il punto di non ritorno nessun percorso cancella il certificato,
  ripristina i proprietari precedenti o autorizza la build predecessore.

La sostituzione di `required-head-v1.bin` e' un secondo confine di upgrade del
gruppo 6; non viene chiamata punto di non ritorno del certificato e non viene
confusa con esso.

### 5.4 Ordine del coordinatore

L'ordine non permutabile e' il seguente:

1. caricare a freddo le tre autorita' e verificare la loro disgiunzione;
2. verificare una `VerifiedDistribution` reale prodotta dal verificatore del
   manifest, mai da un costruttore di prova;
3. acquisire `catalog_admission_lock`, blocco di riconciliazione e manutenzione,
   writer lock ordinati e blocco di deployment;
4. scrivere e rileggere `PREPARED`;
5. acquisire la prima prova completa di manutenzione;
6. censire le correnti, riattestare le mancanti tramite la fabbrica sigillata,
   rileggere ogni ricevuta autenticata e ricensire;
7. richiedere zero rilievi di migrazione, zero ambiti non classificati o stale
   e lo stesso hash di inventario della build verificata;
8. acquisire la seconda prova completa di manutenzione, scrivere e rileggere
   `RECEIPTS_COMPLETE`;
9. verificare l'attestazione sigillata dei prerequisiti di avvio; in sua assenza
   fermarsi prima di firmare;
10. produrre e rileggere i byte esatti del certificato;
11. pubblicare firma e poi payload senza sovrascrittura; il payload e' il punto
    di non ritorno;
12. rileggere il certificato dal disco e soltanto allora registrare
    `CERTIFICATE_PUBLISHED`.

### 5.5 Prove minime G5-B

- assemblaggio produttivo della riattestazione con il vero publisher sigillato;
- rifiuto di ogni API produttiva che tenti di fornire una `ProducerReceipt`;
- una corrente con ricevuta gia' valida e una corrente realmente riattestata;
- doppio censimento, doppia prova di manutenzione e rifiuto di variazione in
  unita', catalogo, ricevuta, inventario o build;
- rifiuto di un sottoinsieme di unita' anche se canonicalmente valido;
- arresto di processo prima e dopo ogni confine durevole, in particolare tra
  pubblicazione del payload e checkpoint;
- recupero in un processo nuovo usando soltanto disco e loader produttivi;
- prima del punto di non ritorno: certificato assente e proprietari precedenti
  non dichiarati chiusi;
- dopo il punto di non ritorno: certificato byte-identico, nessuna riapertura e
  stato fermo finche' gruppo 6 non autentica build, testa e preflight;
- nessun oracolo basato su `Rig`, `_sealed_reattestation_core_for_test`,
  `_verified_distribution_for_test`, registri in memoria o semplice eccezione
  Python al posto della terminazione del processo.

### 5.6 Criterio di uscita G5-B

- il coordinatore raggiunge `RECEIPTS_COMPLETE` sul percorso produttivo con
  zero errori;
- il percorso produttivo non puo' attraversare il punto di non ritorno senza
  l'attestazione del gruppo 6;
- la macchina isolata attraversa e recupera `CERTIFICATE_PUBLISHED` senza
  cancellazione o fallback;
- fabbrica sigillata, prova completa, registro e certificato sono riletti dai
  rispettivi store;
- prove mirate, R1, guardie e suite portatile finale sono verdi;
- matrice pubblica Linux e Windows interamente verde.

## 6. Cose che non costituiscono una soluzione

- Un registro multipurpose o la stessa chiave con due scopi.
- Tre nomi diversi che contengono gli stessi byte pubblici.
- Un dizionario di chiavi costruito dal test al posto del cold loader.
- Una `ReattestationRequest` produttiva riempita dal coordinatore con dati
  selezionabili.
- Un sottoinsieme di unita' passato al codec canonico.
- Il solo stato del journal usato per decidere se il punto di non ritorno e'
  stato superato.
- Un cutover reale prima del controllo preliminare dominante.
- Dichiarare completati `BUILD_VERIFIED`, `HEAD_REQUIRED` o
  `PREFLIGHT_VERIFIED` nel gruppo 5.
- Cambiare `closed_build_enforcement()` da `False`.

## 7. Sequenza di verifica e pubblicazione

Durante lo sviluppo si eseguono soltanto le prove del file modificato, la cella
produttiva posseduta dal gruppo e R1. Alla fine si eseguono una volta guardia
normale, guardia `--birth-closed`, rendering byte-identico dell'inventario e
suite portatile completa in radici isolate.

I due incrementi ricevono commit sorgente separati su `main`, sempre con footer
`RM-0008-Status: candidate-not-certified`. Si esegue una sola pubblicazione
incrementale su `main` pubblico dopo il verde locale completo. Il gruppo 6 non
inizia finche' la matrice pubblica del gruppo 5 non e' interamente verde.

Se una prova pubblica fallisce, si registra prima lavoro, passo e causa
osservata. Non viene applicata una seconda correzione finche' una misura
discriminante non ha ristretto la causa.

## 8. Evidenze da consegnare

Il passaggio di consegne finale conterra' per ogni requisito: simbolo
produttivo, prova di modulo, prova di integrazione, prova Linux `root`, risultato
Windows, commit sorgente, commit pubblico e ciclo GitHub. Indichera'
esplicitamente come non applicabili al gruppo 5 il cutover installato, la catena
completa, il controllo preliminare dominante e l'attivazione della build chiusa,
assegnandoli al gruppo 6 o 7.
