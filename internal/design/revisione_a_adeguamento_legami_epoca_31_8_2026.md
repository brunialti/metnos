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
