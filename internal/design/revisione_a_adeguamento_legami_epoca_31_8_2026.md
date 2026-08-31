# F4-EPOCA-01 — revisione A del censimento dei legami

Data: 31 agosto 2026  
Commit esaminato: `937ba594ec0ac02e0a379a7879466e38930383e0`  
Verdetto: `MODIFICHE_RICHIESTE`

## Risultato confermato

Le otto prove mirate sono verdi e la misura reale e' stata riprodotta. Il fatto
centrale e' accettato: i 12 legami di O15 sono sei atti conclusi sotto due
rappresentazioni, tutte e sei le generazioni sono superate e nessuno dei 12
oggetti deve essere riscritto o ripuntato.

E' confermato anche il fatto trasversale che corregge il perimetro A: le
ricevute esistenti non coprono alcuna generazione corrente. La transizione deve
quindi censire le generazioni correnti indipendentemente dai 12 oggetti storici;
non puo' trattare quei 12 come l'elenco del lavoro da riattestare.

## Rilievo 1 — identita' del gemello incompleta

`per_generazione` indicizza le ricevute con la sola generazione, mentre il
contratto F4 usa gia' l'identita' composta `(contract_id, generation_id)`. Una
ricevuta Producer viene poi accoppiata tramite il solo digest. Il classificatore
deve usare la coppia completa e deve rifiutare ogni divergenza fra contratto e
generazione presenti nel percorso, nel documento e nella busta.

Prova minima richiesta: due contratti con la stessa generazione di prova non
devono poter condividere il gemello; contratto o generazione discordanti devono
produrre `non_classificato`.

## Rilievo 2 — autorita' scelta dal chiamante

`--ritirati` permette al chiamante di far classificare una generazione corrente
come `cessa_di_essere_corrente`. Questa e' una decisione autorevole, non un dato
diagnostico. Deve provenire dallo stato autenticato del contratto oppure la
classe va tolta dallo strumento finche' quella fonte non esiste. Un argomento
libero non puo' ridurre il lavoro necessario per F4.

## Rilievo 3 — i fatti decisivi non sono autenticati

Il classificatore legge direttamente `binding.json`, `current` e i JSON delle
ricevute. Non autentica la generazione corrente con le primitive del negozio e
non verifica firma e legami della ricevuta di ammissione. Cosi' la conclusione
e' riproducibile sui byte osservati, ma non ancora probante contro una
discordanza fra locatori e fatti firmati.

La correzione deve riusare le primitive produttive di inventario, lettura della
generazione corrente e verifica delle ricevute, senza crearne una seconda
implementazione. Le prove devono includere puntatore, contratto, generazione o
firma discordanti e mostrare un rifiuto.

## Rilievo 4 — due buste rifiutate sono chiamate illeggibili; un caso invalido
puo' passare

Le due buste reali senza `admission_receipt` appartengono a righe `rejected` e
sono JSON validi: non sono dipendenze illeggibili. Vanno escluse esplicitamente
per stato e forma terminale verificata. Al contrario, una busta `committed`
invalida che non ripeta il contesto in chiaro oggi finisce nell'elenco
`non_interpretabili`, ma non genera un legame e non cambia l'uscita verde.

La regola deve essere chiusa:

- rifiuto terminale valido senza ricevuta di ammissione: non e' un legame;
- conclusione valida con ricevuta: viene accoppiata e classificata;
- stato, autenticazione o busta incoerenti: `non_classificato`, quindi blocco.

Servono due prove distinte per il rifiuto valido e per la conclusione invalida
senza testo del contesto.

## Condizione del giro successivo

Il disegno dei tre futuri e la conclusione sui 12 oggetti non devono cambiare.
Il giro successivo deve rendere lo strumento coerente con quelle affermazioni:
identita' composta, nessuna autorita' dal chiamante, fatti autenticati e nessun
caso terminale ignorato. Restano prove mirate; la suite completa non va
eseguita in questa barriera.

---

## Secondo giro

Data: 31 agosto 2026
Commit esaminato: `dfb551a74773081b09f734e62dc8cc35b99a10a0`
Verdetto: `MODIFICHE_RICHIESTE`

Le correzioni su identita' composta, ritiro autenticato e casi terminali sono
accolte. Restano tre punti dello stesso confine di autenticazione.

### Rilievo 5 — ricevuta e contesto restano dati non autenticati

`contesto_corrente` legge direttamente `prepared-v1.json` e
`material-v1.json`; `legami_del_negozio` decodifica i JSON delle ricevute ma
non ne verifica la firma. Il limite e' dichiarato dal rapporto, ma dichiararlo
non rende probante il verdetto. Le chiavi pubbliche necessarie sono gia'
nell'insieme preparato e non richiedono di attivare il nucleo ne' di caricare
chiavi private.

La correzione minima e' una sola acquisizione in sola lettura mediante
`open_prepared_root_session_v1`, `load_prepared_set_v1` e il registro pubblico
dell'insieme, sotto `global_lock(exclusive=False, create=False)`. Questa forma
e' stata provata sul dato reale e restituisce un insieme, un verificatore
Admission e undici registri Producer senza ricostruire da `PATH_RUNTIME`. Da
quella acquisizione devono derivare il contesto e i
verificatori; ogni AdmissionReceipt deve passare
`verify_admission_receipt`, compresi identita', contesto e ciclo approvato.
Nessun lettore JSON parallelo deve decidere l'autorita'.

### Rilievo 6 — la busta Producer ignora entrambe le prove disponibili

Il classificatore legge soltanto `state` e `terminal_envelope`. La riga reale
contiene anche la ricevuta Producer firmata in `encoded` e la firma della busta
in `terminal_auth`; entrambe sono ignorate. Percio' una riga modificata nel DB
puo' ancora essere chiamata conclusione o rifiuto valido.

Prima di classificare una riga occorre:

1. verificare la ricevuta Producer e la sua corrispondenza con le colonne
   durevoli, usando il registro pubblico Producer dell'insieme;
2. verificare `terminal_auth` col `signing_key_id` interno alla busta e con il
   dominio produttivo;
3. pretendere che `request_id`, stato, risultato, contratto, generazione e byte
   della AdmissionReceipt concordino fra riga, busta e gemello verificato.

Un rifiuto terminale privo di una di queste prove non viene escluso: blocca.
Non serve un secondo formato diagnostico; vanno riusati codec, domini e
registri produttivi.

### Rilievo 7 — l'inventario autenticato non possiede ancora i percorsi letti

`stato_autenticato_dei_contratti` non rifiuta `inventario.problems`; inoltre
la scansione successiva torna a derivare il contratto dal `binding.json`
grezzo di ogni cartella. Lo stato corrente puo' quindi essere autenticato
mentre il percorso dal quale viene presa la ricevuta non lo e'.

La scansione deve partire dai riferimenti accettati dall'inventario produttivo,
pretendere zero problemi e raggiungere per ciascun contratto la directory del
negozio mediante la primitiva posseduta dal negozio. Directory inattese,
binding discordante o contratto non inventariato bloccano l'intero censimento,
anche se non contengono una ricevuta del contesto cercato.

### Prove minime del terzo giro

- firma AdmissionReceipt errata, contesto alterato e marker/set incoerenti;
- firma Producer errata, colonna diversa dal contenuto firmato e richiesta
  diversa;
- `terminal_auth` errata e busta firmata con identita' diversa dal gemello;
- problema inventariale o directory inattesa senza ricevute del contesto;
- dato reale: 12 storici, due rifiuti terminali autenticati, zero ignoti.

La riga d'uso iniziale va inoltre allineata: cita ancora `--ritirati`, gia'
rimosso. E' una correzione documentale, non un rilievo autonomo.

La suite completa resta esclusa. Il terzo giro puo' fermarsi alle prove mirate
e a una nuova esecuzione in sola lettura sul dato reale.

---

## Terzo giro

Data: 31 agosto 2026
Commit esaminato: `df450ad77d0a913130ffb9cc77dfc776486998ac`
Verdetto: `MODIFICHE_RICHIESTE`

La traduzione in inglese dei commenti nelle prove e' corretta e soddisfa la
regola di lingua appena fissata. L'uscita diagnostica in italiano non e'
documentazione incorporata nel codice e non costituisce un rilievo.

Il delta tecnico rispetto a `dfb551a74773081b09f734e62dc8cc35b99a10a0`
modifica soltanto commenti e formattazione nel file di prova. Non cambia il
classificatore, non aggiunge prove di autenticazione e non chiude i rilievi 5,
6 e 7. Restano quindi necessarie, senza ulteriori ampliamenti, le correzioni e
le prove minime gia' elencate nel secondo giro. La suite completa resta
esclusa da questa barriera.

---

## Quarto giro

Data: 31 agosto 2026
Commit esaminato: `dd86dc05993a05f6698b6f073b994950e6fb5438`
Verdetto: `MODIFICHE_RICHIESTE`

Sono accolti il caricamento unico dell'autorita' preparata, la verifica delle
firme Admission e Producer, la verifica di `terminal_auth` e l'inventario
produttivo con zero problemi. Le dieci prove mirate sono verdi. La nuova
esecuzione reale in sola lettura conferma 12 oggetti storici, zero relazioni
non classificate e il blocco dovuto alla pubblicazione incompleta gia'
segnalata. Restano tre correzioni concentrate sullo stesso confine probatorio.

### Rilievo 8 — i tre atti autenticati non sono ancora legati fra loro

Il classificatore autentica separatamente la ricevuta Admission, la ricevuta
Producer e la busta terminale, ma poi le accoppia quasi soltanto mediante
`(contract_id, generation_id)`. Non verifica i legami che rendono quei tre atti
un'unica conclusione:

- `AdmissionReceipt.producer_receipt_hash` deve essere l'hash esatto dei byte
  Producer presenti in `encoded`;
- `AdmissionReceipt.birth_request_id`, `terminal_envelope.request_id` e la
  colonna durevole `request_id` devono coincidere;
- i byte Admission nella busta devono essere identici al gemello autenticato
  letto dal negozio, non soltanto avere la stessa identita';
- `receipt_hash`, `issuer_id`, `objective_hash`, `candidate_source_id`,
  `executor_origin`, `revision_authorship` ed `expires_at` della riga devono
  coincidere con la ricevuta Producer autenticata;
- `result_binding` deve coincidere con il legame canonico della busta terminale;
- per un rifiuto, `rejection_code` della riga ed `error_code` della busta devono
  coincidere esattamente: l'alternativa fra i due non e' probante.

Una prova avversariale indipendente ha costruito firme Admission e Producer
valide con lo stesso contratto e la stessa generazione, ma con hash Producer e
richieste discordanti. Il classificatore ha comunque restituito
`{'epoca_storica': 2}`. E' un falso verde riproducibile, non un requisito
teorico.

La correzione deve riusare i codec e i calcoli canonici produttivi. Servono una
prova positiva con una coppia realmente coerente e prove negative singole per
hash Producer, richiesta, byte Admission, colonne durevoli, `result_binding` e
codice di rifiuto discordanti.

### Rilievo 9 — provenienza, tempo e percorsi devono restare fail-closed

Il contenuto JSON non autenticato di un oggetto precedente non puo' decidere da
solo che l'oggetto e' fuori perimetro. Un oggetto Admission non verificabile
puo' essere escluso dal lavoro corrente soltanto quando lo stato corrente
autenticato dimostra che la generazione del suo percorso non e' corrente;
altrimenti blocca. Le righe Producer non verificate restano oggetti precedenti
non verificati e non possono autorizzare, ridurre o soddisfare alcun lavoro F4.
In ogni caso F4 riattesta tutte le generazioni correnti autenticate, anche se
non esiste una vecchia ricevuta.

La validazione storica della firma Producer non deve usare un
`registered_at` modificabile nel database ne' ripiegare silenziosamente
sull'ora corrente quando il timestamp e' invalido. Deve derivare l'istante dal
campo firmato della ricevuta, oppure riusare il verificatore storico
produttivo; un timestamp durevole invalido deve bloccare.

Infine l'inventario dei percorsi deve usare `lstat`, rifiutare collegamenti e
oggetti non regolari e confrontare i percorsi posseduti esatti. `is_dir()` e
`resolve()` non devono permettere a un alias di una directory inventariata di
evitare il blocco. La stessa regola vale per i file ricevuta.

### Rilievo 10 — le prove devono esercitare il formato produttivo corrente

La fixture terminale usa ancora una busta ridotta `schema_version: 1`, mentre
il prodotto emette la busta canonica V2. La prova positiva deve costruire il
formato produttivo corrente e tutti i legami del rilievo 8; altrimenti il verde
non dimostra il contratto dichiarato. Vanno inoltre tradotti in inglese i
commenti rimasti nel nuovo file di prova, allineata la sezione `Usage` agli
argomenti effettivi e rimosso l'import duplicato.

La suite completa resta esclusa. Il prossimo giro richiede soltanto queste
prove mirate e la ripetizione in sola lettura sul dato reale.

---

## Quinto giro

Data: 31 agosto 2026
Commit esaminato: `41845f1fc392ef01386f13a29bb69c762f0ff46d`
Verdetto: `MODIFICHE_RICHIESTE`

E' accolto il rilievo B2: il censimento deve conoscere anche il percorso V2.
La specifica A `9290d86ffae73212a773f806650822d6c6b2e5f4` ne congela ora forma,
proprietario e semantica. La coesistenza sulla stessa generazione e' ammessa:
V1 storica e V2 corrente sono atti distinti identificati dalla tripla
`(contract_id, generation_id, admission_context_id)`.

L'implementazione di `41845f1f` usa invece ancora la coppia
`(contract_id, generation_id)` e pretende che V1 e V2 concordino sullo stato
della generazione. La prova nominata «V1 storica e V2 corrente» costruisce in
realta' due ricevute dello stesso contesto e rende la generazione non corrente
per entrambe: non esercita il caso dichiarato. Va corretta usando due contesti
distinti e senza fondere le due triple. Una copia ripetuta della stessa tripla
deve concordare byte per byte.

Il censimento B1 puo' misurare lo stato V1 realmente presente. Dopo la prima
transizione, pero', l'autorita' V2 corrente deriva dalla catena richiesta e
l'autorita' storica dal suo insieme immutabile: il solo
`load_prepared_set_v1` non puo' diventare un secondo selettore di autorita'. Il
supporto produttivo V2 di B2 deve consumare la selezione autenticata fornita
dal caricatore A; fino ad allora un V2 incontrato senza autorita' autenticata
blocca, non viene classificato dal contenuto.

Il nuovo delta non modifica la catena fra Admission, Producer, busta e riga
durevole: il rilievo 8 e la sua prova di falso verde restano aperti. Restano
aperti anche il rilievo 9 su contenuto non verificato, istante firmato e
`lstat`, e il rilievo 10 sulla busta produttiva V2. I nuovi commenti italiani
nel file di prova vanno tradotti; `_ESADECIMALE_64` deve essere applicato ai
due componenti V2 oppure rimosso, non lasciato come controllo apparente.

Non servono nuove famiglie di prove oltre a quelle gia' richieste: basta
correggere la fixture V1/V2, chiudere i legami esatti del rilievo 8 e applicare
le condizioni fail-closed del rilievo 9. Nessuna suite completa in questa
barriera.

---

## Sesto giro

Data: 31 agosto 2026
Commit esaminato: `d7c7382f34a6df64fcf3f88cfef3dc5fc708c786`
Verdetto: `MODIFICHE_RICHIESTE`

Sono accolte l'identita' a tripla, la derivazione dell'istante dal campo
firmato `issued_at` e la verifica `lstat` degli oggetti al primo livello del
negozio. Le quindici prove dichiarate sono verdi. La ripetizione reale in sola
lettura conferma 12 legami storici e zero non classificati; l'uscita resta
correttamente bloccata dai due rilievi inventariali gia' noti.

Restano quattro falsi verdi, riprodotti sull'oggetto Git immutabile esaminato.

### Rilievo 11 — la ricevuta nella busta non e' ancora il gemello del negozio

`_catena_ammissione` riceve `byte_busta` ma non lo usa. Dopo la verifica firma,
la ricevuta nella busta viene accoppiata al negozio soltanto mediante la tripla.
Due AdmissionReceipt entrambe firmate, con la stessa tripla ma byte diversi,
vengono quindi classificate come due fatti storici coerenti. La prova
indipendente usa nel negozio una ricevuta con un `producer_receipt_hash` e nella
busta una ricevuta con un altro hash, entrambi validamente firmati: il risultato
e' ancora due `epoca_storica`, zero ignoti.

La correzione minima e' confrontare i byte Admission decodificati dalla busta
con `gemello.prove["byte"]` prima di ereditare corrente e ritirato. Nello stesso
punto, i tre identificativi di richiesta devono esistere e coincidere, non
soltanto non discordare quando presenti.

### Rilievo 12 — riga e busta accettano ancora assenze e un rifiuto discorde

I confronti di `receipt_hash`, campi Producer, `result_binding`, `receipt_id` e
`request_id` sono condizionali: un campo assente viene accettato. La fixture
conferma il difetto per costruzione, perche' usa una tabella ridotta che non
contiene la maggior parte delle colonne produttive e resta verde. Anche il
rifiuto usa `rejection_code OR error_code`: due codici diversi, uno nella riga
e uno nella busta firmata, vengono ancora registrati come un solo rifiuto
terminale valido. Il caso indipendente lo riproduce.

La correzione minima e' pretendere lo schema produttivo e uguaglianza esatta di
tutti i campi obbligatori. Per il rifiuto, `rejection_code` ed `error_code`
devono entrambi esistere e coincidere; per il commit, pubblicazione, legame e
identita' devono esistere e coincidere.

### Rilievo 13 — formato terminale e percorsi ricevuta non sono produttivi

La verifica terminale esegue `json.loads` e verifica la firma, ma non applica la
decodifica canonica V2 del prodotto. La fixture continua a emettere
`schema_version: 1`, senza rapporto ed errore canonici, e tutte le prove verdi
passano su quella forma. Deve riusare la decodifica produttiva V2 o una
primitiva pubblica estratta da essa; la prova positiva deve usare la tabella
vera del Producer store, non uno schema ridotto parallelo.

Inoltre `lstat` copre soltanto le directory al primo livello. La scansione delle
ricevute usa ancora `glob`, `is_dir` e `read_bytes`: una AdmissionReceipt
spostata fuori dal negozio e raggiunta mediante collegamento viene accettata
come storica. La prova indipendente lo riproduce. Ogni directory V2 e ogni file
ricevuta devono essere oggetti regolari posseduti, raggiunti senza seguire
collegamenti.

### Rilievo 14 — il contenuto non verificato decide ancora il fuori ambito

Quando una AdmissionReceipt non verifica, il suo JSON non autenticato decide
ancora se il contesto e' diverso. Una ricevuta non verificabile posta sulla
generazione corrente autenticata, ma con un altro contesto scritto nei byte,
viene mossa in `fuori_ambito` e non blocca. Il quarto caso indipendente lo
riproduce. Il contenuto non verificato non puo' decidere il perimetro: se la
generazione del percorso e' corrente, il dubbio blocca; soltanto lo stato
corrente autenticato puo' dimostrare che un oggetto precedente non riduce il
lavoro F4.

### Prova indipendente e chiusura richiesta

Sul clone isolato dell'esatto commit, il comando mirato di B riporta 15 verdi.
La prova A aggiunge quattro sole variazioni dei casi gia' richiesti e riproduce:

1. byte Admission firmati diversi accettati come gemello;
2. codici di rifiuto diversi accettati come un solo rifiuto;
3. file ricevuta raggiunto mediante collegamento accettato come posseduto;
4. ricevuta non verificabile sulla generazione corrente spostata fuori ambito.

La fixture terminale va portata a V2 e alla tabella produttiva; i commenti
italiani reintrodotti nel file di prova vanno tradotti in inglese. Non serve
allargare il censimento, cambiare il disegno dei tre futuri o eseguire la suite
completa. Il prossimo giro deve rendere rossi questi quattro casi prima della
correzione e verdi dopo.

---

## Settimo giro

Data: 31 agosto 2026
Commit esaminato: `fb73a7b35d27b87d381db32dd689abfb4d52e2d4`
Verdetto: `MODIFICHE_RICHIESTE`

Sono accolti il confronto esatto dei byte Admission, l'esistenza dei tre
identificativi di richiesta, il confronto dei codici di rifiuto e la regola che
una ricevuta non verificabile sulla generazione corrente blocca. La misura
reale resta 12 storici, zero ignoti, con i due rilievi inventariali noti.

Restano due difetti riprodotti e un punto gia' dichiarato aperto dallo stesso
checkpoint B.

### Rilievo 15 — un oggetto non regolare viene ignorato invece di bloccare

`_ricevute_del_contratto` usa ora `lstat`, ma `_regolare` e `_cartella`
restituiscono falso e il chiamante salta l'oggetto. La forma e' quindi cambiata
da «seguire il collegamento» a «non censirlo», non a «bloccare». La prova
indipendente sposta una ricevuta fuori dal negozio e lascia un collegamento al
suo nome: il risultato contiene zero legami, zero ignoti e zero bloccanti.

La correzione minima e' far restituire alla scansione anche i problemi di
percorso e aggiungerli a `bloccanti`. Lo stesso vale per directory V1/V2,
componenti non esadecimali, file inattesi e qualunque errore `lstat`: nulla
all'interno delle radici possedute puo' sparire dal censimento.

### Rilievo 16 — `result_binding` deve dipendere dallo stato terminale

`_catena_riga_busta` pretende sempre che `result_binding` sia il digest della
busta. Il Producer store produttivo impone invece:

- `committed`: `result_binding` presente e `rejection_code` assente;
- `rejected`: `result_binding` assente e `rejection_code` presente.

Una riga `rejected` costruita mediante `register_producer_receipt`,
`claim_producer_receipt` e `finalize_producer_receipt`, con busta e codice
coerenti, viene classificata ignota e non come rifiuto terminale autenticato.
Il caso e' riprodotto sull'oggetto esaminato.

La correzione minima e' rendere il confronto dipendente dallo stato e usare
nelle fixture le API del Producer store. L'attuale `CREATE TABLE` non e' la
tabella produttiva: omette colonne, `NOT NULL`, vincoli di stato e migrazione.
Copiarne alcuni nomi non esercita il contratto reale.

### Rilievo 17 — prova durevole e busta V2

Il percorso `RM0008-Prova` continua a eseguire 15 casi: le quattro variazioni
del sesto giro non sono presenti nel file versionato. Devono entrare nella
prova, insieme ai due casi sopra, cosi' il prossimo checkpoint sia
riproducibile dal solo commit.

B dichiara correttamente ancora aperto il decoder terminale. Per non introdurre
codice prodotto prima di B1, lo strumento interno puo' riusare direttamente
l'attuale `_decode_terminal_envelope` con una `BirthRequest` legata alla riga e
alla ricevuta verificata; la fixture usa `_terminal_envelope` e il vero Producer
store. L'estrazione di un codec pubblico resta nell'implementazione B2 gia'
assegnata a B. Non e' ammesso mantenere `json.loads` come secondo decoder ne'
accettare `schema_version: 1`.

I commenti italiani ancora presenti nel file di prova vanno tradotti in
inglese nello stesso incremento. Nessuna suite completa: bastano i casi mirati
versionati e la misura reale in sola lettura.

---

## Ottavo giro

Data: 31 agosto 2026
Commit esaminato: `f3f66c76a929eab7def869ea603848c32c84657f`
Verdetto: `MODIFICHE_RICHIESTE`

I rilievi 15 e 16 sono chiusi. Le 20 prove versionate sono verdi; la scansione
segnala ora gli oggetti non regolari come bloccanti. Una prova A indipendente
ha inoltre costruito un rifiuto coerente mediante le vere API
`register_producer_receipt`, `claim_producer_receipt` e
`finalize_producer_receipt`: il classificatore lo riconosce come unico rifiuto
terminale, senza legami o bloccanti. La ripetizione reale in sola lettura
conferma 12 legami storici, zero ignoti e il blocco sui due problemi
inventariali gia' noti.

E' chiusa anche la parte del rilievo 17 relativa alle variazioni versionate.
Restano soltanto i due punti strutturali dichiarati aperti nel checkpoint B:

1. il classificatore deve consumare la busta terminale canonica V2 tramite il
   decoder del prodotto, non mediante un secondo `json.loads` permissivo;
2. la fixture positiva deve creare la riga e la relativa emissione mediante le
   API reali del Producer store, non mediante il `CREATE TABLE` parallelo.

La soluzione minima per B1 e' riusare `_decode_terminal_envelope` con una
`BirthRequest` legata alla riga durevole e all'identita' di contratto
autenticata dalla catena di emissione; la prova deve emettere la busta con
`_terminal_envelope` e attraversare emissione, claim e finalize produttivi.
L'eventuale estrazione di un codec pubblico resta lavoro B2 e non deve
ritardare questa barriera diagnostica.

Prima del prossimo checkpoint vanno inoltre tradotti in inglese i nuovi
commenti italiani della prova (`due produttori`, `discorde`, `come impone il
Producer store`). Non servono altri casi, ne' modifiche di prodotto, ne' la
suite completa in questo giro. Con decoder V2, fixture produttiva, 20 prove
verdi e misura reale invariata, A puo' accettare B1.

---

## Nono giro

Data: 31 agosto 2026
Commit esaminato: `7f704e433185bc8056aa96e99a900b3fa17d208c`
Verdetto: `MODIFICHE_RICHIESTE`

La busta V2 prodotta da `_terminal_envelope` passa ora dal decoder canonico e
i 20 casi sono verdi. La misura reale resta 12 legami storici, zero ignoti e
il blocco sui due problemi inventariali noti. Una prova A costruita con il vero
Producer store conferma inoltre che un rifiuto V2 coerente viene riconosciuto.

Restano due falsi contratti nella prova e nella catena.

### Rilievo 18 — la fixture non usa ancora il Producer store

`riga()` crea ancora a mano `birth_producer_receipts` e
`birth_producer_issuance`, senza chiavi, vincoli, schema versionato o
migrazione. Inserisce persino `birth_producer_issuance.encoded = NULL`, forma
vietata dallo schema produttivo. Le 20 prove verdi non chiudono quindi il
requisito dichiarato: nominare le colonne non equivale ad attraversare le API.

La correzione minima e' usare almeno nei casi positivi
`get_or_issue_and_claim_producer_receipt` e `finalize_producer_receipt`, che
creano insieme ricevuta, emissione, claim e conclusione reali. I casi negativi
possono poi alterare una sola colonna del database cosi' prodotto. Non serve
riscrivere le venti prove ne' aggiungere un secondo costruttore.

### Rilievo 19 — l'emissione lega soltanto il contratto, non la richiesta

`_decodifica_canonica` cerca l'emissione per `receipt_id` ma ne legge soltanto
`contract_id`. Non confronta `request_id`, `issuer_id`, `objective_hash`,
`candidate_source_id` ed `encoded` con riga e ricevuta Producer. Una prova A
parte da un rifiuto V2 creato e finalizzato con le API reali, poi modifica
soltanto `birth_producer_issuance.request_id`: il classificatore lo accetta
ancora come rifiuto autenticato. La catena durevole e' quindi presente ma non
interamente legata.

Va letta una sola riga di emissione e richiesta uguaglianza esatta di tutti i
campi condivisi prima di costruire `BirthRequest`. Il risultato del decoder
(`BirthResult`, byte Admission e identificativo della chiave) deve inoltre
alimentare i controlli successivi; chiamare il decoder come sola guardia e poi
rileggere la semantica con due `json.loads` mantiene due interpretazioni della
stessa busta.

Infine `git diff --check` fallisce per uno spazio finale in
`prova_classifica_legami_epoca.py:177`. Il prossimo checkpoint richiede solo:
fixture positiva tramite API, caso di richiesta emissione discorde, consumo
del risultato decodificato, `diff --check` e le prove mirate. Nessuna suite
completa.

---

## Decimo giro

Data: 31 agosto 2026
Commit esaminato: `9df14d63c50a2f0dcc19db03bf0528470b164223`
Verdetto: `MODIFICHE_RICHIESTE`

R18 e la correzione sostanziale di R19 sono accolti. Le 20 prove sono verdi,
`git diff --check` e' pulito e la misura reale e' invariata. La prova A
indipendente usa emissione, claim, finalize e busta V2 produttivi: il caso
coerente e' accettato e la modifica della sola richiesta nella catena di
emissione viene ora bloccata.

Resta soltanto da rendere quella proprieta' parte del commit, senza affidarla
alla prova temporanea A:

1. aggiungere ai casi versionati la modifica della sola
   `birth_producer_issuance.request_id` dopo la costruzione con le API, e
   pretendere un unico ignoto e zero rifiuti;
2. trattare `birth_producer_issuance.encoded` assente come discordanza: lo
   schema produttivo lo vieta e il censimento non deve trasformarne l'assenza
   in accordo;
3. non ignorare `sqlite3.Error` nelle mutazioni negative. Una mutazione che non
   avviene deve rendere rossa la prova, non lasciarla verde sul caso positivo.

Il decoder canonico precede ora ogni lettura semantica, quindi non e' stato
riprodotto un percorso permissivo residuo in questo commit; il consumo diretto
del valore decodificato resta il naturale consolidamento del codec pubblico in
B2. Per B1 bastano i tre adeguamenti sopra, 21 casi verdi, `diff --check` e la
misura reale. Nessuna suite completa.

---

## Undicesimo giro

Data: 1 settembre 2026
Commit esaminato: `df36c169333b5435c6ffe23c9d6a87024384622a`
Verdetto: `ACCETTATA`

I tre adeguamenti richiesti sono presenti e discriminanti. Le 21 prove
versionate sono verdi; il caso nuovo parte da una riga costruita con le API
produttive, altera soltanto la richiesta dell'emissione e ottiene un unico
ignoto e zero rifiuti. L'assenza dei byte dell'emissione blocca e le mutazioni
di prova pretendono di toccare esattamente una riga. `git diff --check` e'
pulito.

La ripetizione reale in sola lettura resta stabile: 12 legami tutti storici,
zero non classificati e due problemi inventariali noti che bloccano F4. Nessun
rilievo resta aperto sul perimetro B. A accetta l'esatto commit
`df36c169333b5435c6ffe23c9d6a87024384622a` per B1.
