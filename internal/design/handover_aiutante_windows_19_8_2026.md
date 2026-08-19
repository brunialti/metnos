# Consegna — aiutante elevato Windows (ADR 0210 D), 19/8/2026 sera

> Per chi riprende. Leggi questo PRIMA di toccare qualsiasi cosa: contiene lo
> stato reale, il difetto aperto, e cinque trappole che costano ore se le
> reincontri da zero.

## Stato in una riga

**CHIUSO E PROVATO DAL VIVO.** ADR 0210 parte D funziona per intero.

Prova finale, 19/8/2026 ore 22:51 su PC-ROBERTO — `LibreHardwareMonitor`,
programma NON gia' presente:

- scelta `machine` (non `machine_setup`): l'aiutante c'era ed e' stato
  **riconosciuto**, nessuna installazione di componenti di mezzo;
- portata **macchina**, cioe' per tutti gli utenti;
- invocazione `inv-18cd5016bdf6348e2655a17b` → **done** in 27 secondi:
  installazione vera, eseguita dall'aiutante come sistema;
- superati i 25 secondi e' comparso il messaggio d'attesa, e l'esito e'
  arrivato **da solo**: «esito tardivo consegnato a 1 collegamenti».

Cioe': tutte e cinque le parti costruite in questi due giorni hanno funzionato
insieme, sulla macchina vera — installazione dell'aiutante, avvio come
servizio, riconoscimento, esecuzione privilegiata, consegna asincrona
dell'esito.

## L'ultimo difetto — winget non esiste, per il sistema

Trovato da Roberto scegliendo un programma NON gia' installato
(`LibreHardwareMonitor`): tutti i tentativi precedenti finivano su 7zip, che
c'era gia', e non arrivavano mai a lanciare davvero il gestore.

    package_operation_failed · spawn_failed: program not found

`winget` non e' un programma nel percorso di ricerca: e' un **alias
d'esecuzione installato PER UTENTE** sotto `WindowsApps`. L'aiutante gira come
sistema, e per il sistema quell'alias non esiste. Sembra un guasto della
macchina; e' un guasto di prospettiva.

Corretto risolvendo il percorso vero nella cartella del pacchetto
`Microsoft.DesktopAppInstaller_*__8wekyb3d8bbwe`, leggibile da chiunque; fra
piu' versioni si prende l'ultima. Se non lo si trova si prova comunque per
nome, cosi' dove l'alias c'e' il comportamento non cambia.

**Da riprovare**: installare un programma non ancora presente, «per tutti gli
utenti». E' l'unica cosa che manca per dire chiuso.

## L'ultimo difetto, e perche' era invisibile

Il client verificava chi c'era dall'altro capo del canale **aprendo il
processo del servizio e leggendone il token**. Non poteva riuscire: il client
gira senza privilegi, il servizio gira come sistema, e Windows non lascia a un
processo utente aprire il token di un processo di SYSTEM. Falliva su qualunque
macchina, sempre — e falliva PRIMA di scrivere una parola, quindi la
connessione si chiudeva a vuoto e dall'altra parte restava solo «messaggio
senza delimitatore: l'altro capo ha chiuso subito». Quella riga, ripetuta
tutta la sera, non diceva la causa.

Corretto in due passi, ed e' stato il secondo a chiudere:

1. **Chiedere di chi e' l'OGGETTO, non chi lo serve.**
   `GetSecurityInfo(OWNER_SECURITY_INFORMATION)` sulla pipe: un client che
   l'ha appena aperta ha i diritti per leggerlo. Garanzia equivalente — un
   oggetto di proprieta' privilegiata lo puo' creare solo chi ha i privilegi.
2. **Accettare anche il gruppo amministratori** (`S-1-5-32-544`). Windows non
   assegna sempre l'oggetto all'account che lo crea: con l'impostazione
   predefinita del token, un processo elevato produce oggetti di proprieta'
   del GRUPPO. Pretendere esattamente `S-1-5-18` rifiutava l'aiutante vero su
   una macchina normale.

Il confronto sull'eseguibile atteso resta un rafforzativo quando riesce:
ottenerlo richiede proprio la cosa che da li' non si puo' fare.

## Che cosa funziona, PROVATO DAL VIVO (non in prova)

1. **Autoaggiornamento**: il PC e' passato da solo 0.2.25 -> 0.2.31, piu' volte,
   senza che nessuno toccasse niente.
2. **Ripristino automatico**: quando una versione non si e' confermata, il
   client e' tornato indietro da se' ed e' tornato online.
3. **Attesa con tetto**: `21:22:07 ramo ancora in corso dopo 25s`. L'utente ha
   ricevuto il messaggio onesto invece della pagina d'errore del proxy.
4. **Consegna asincrona dell'esito**: `21:22:37 esito tardivo affidato al
   ciclo`. Arriva in chat da solo.

## LA CAUSA RADICE — trovata il 19/8 sera

**L'aiutante non parla il protocollo dei servizi di Windows.** Zero
occorrenze di `StartServiceCtrlDispatcher` / `RegisterServiceCtrlHandler` /
`SetServiceStatus` in tutto `helper-rs`.

`win_serve::run()` e' un ciclo normale: crea la pipe e aspetta. Ma un
programma registrato come servizio DEVE presentarsi al gestore dei servizi
entro 30 secondi e dichiararsi in esecuzione. Non facendolo, Windows si
arrende con **1053** («il servizio non ha risposto alla richiesta di avvio nel
tempo previsto»), e il servizio non parte MAI.

Conseguenza: l'aiutante non puo' rispondere sul canale, quindi la funzione non
poteva riuscire — nemmeno con tutto il resto perfetto. Tutti i difetti
corretti oggi erano reali, ma stavano davanti a questo.

Testo arrivato a Roberto in chat: `avvio del servizio fallito (rc=1053):
[SC] StartService OPERAZIONI NON RIUSCITE 1053`.

### Analisi — che cosa vuole esattamente Windows

Un programma registrato come servizio non e' un programma normale: il gestore
dei servizi (SCM) lo lancia e poi **aspetta che sia lui a farsi vivo**. La
sequenza obbligata:

1. `StartServiceCtrlDispatcherW` con una tabella che associa il nome del
   servizio a una `ServiceMain`. Questa chiamata NON torna finche' il servizio
   non finisce: e' lei che tiene il filo principale.
2. Dentro `ServiceMain`: `RegisterServiceCtrlHandlerW` per ottenere il
   riferimento con cui riferire lo stato, e per ricevere i comandi (arresto,
   spegnimento).
3. `SetServiceStatus(SERVICE_RUNNING)` — **e' questo il «eccomi»**. Senza,
   dopo 30 secondi Windows dichiara 1053.
4. Il ciclo di lavoro.
5. All'arresto: `SetServiceStatus(SERVICE_STOPPED)` prima di uscire, o il
   gestore lo considera caduto.

Cosa c'e' oggi: il punto 4 e basta. Gli altri quattro mancano tutti.

**Un dettaglio che conta**: se il programma NON e' stato lanciato dal gestore
(qualcuno lo esegue a mano per capirci qualcosa), il punto 1 fallisce con
1063. Trattarlo come un errore renderebbe impossibile provarlo a mano; e'
invece il segnale «non sei sotto il gestore», e la cosa giusta e' girare il
ciclo direttamente.

**Come si ferma un ciclo che aspetta**: il ciclo sta fermo su una pipe, in
attesa di un client, e potrebbe restarci giorni. Interromperlo dall'esterno
in modo pulito richiederebbe I/O asincrono; per un componente che non tiene
stato in memoria — il registro delle chiavi consumate si scrive subito — la
via semplice e corretta e' dichiarare l'arresto al gestore e terminare.

### Scelta di Roberto: strada (a) — FATTA il 19/8 sera

**(a) Parlare il protocollo.** `StartServiceCtrlDispatcherW` + una
`ServiceMain` che registra il gestore dei controlli, dichiara
`SERVICE_RUNNING`, gira il ciclo e risponde a `SERVICE_CONTROL_STOP`. Circa
ottanta righe, forma corretta per un componente di sistema, tiene la politica
di riavvio gia' impostata. Va scritta bene: si prova solo su Windows.

**(b) Non essere un servizio.** Un'attivita' pianificata che gira come SYSTEM
all'avvio, come fa gia' il client (`MetnosClient`). Nessun protocollo da
implementare, pattern gia' nel repo, parte subito. Si perde la politica di
riavvio del gestore dei servizi e la voce in `services.msc`.

Fatta la **(a)**: `helper-rs/src/win_service.rs`. La sequenza per intero,
piu' due scelte che vale la pena conoscere.

**Se non ci lancia il gestore non e' un errore.** `StartServiceCtrlDispatcherW`
fallisce con 1063 quando qualcuno esegue il programma a mano per capirci
qualcosa: si gira il ciclo direttamente. Trattarlo come guasto renderebbe
impossibile provarlo fuori dal gestore, che e' proprio quando serve.

**Fermarsi termina il processo.** Il ciclo sta su una pipe ad aspettare un
client e puo' restarci giorni; interromperlo dall'esterno vorrebbe dire I/O
asincrono su tutto il canale. Il componente non tiene stato in memoria — il
registro delle chiavi consumate si scrive subito — quindi all'arresto si
dichiara `SERVICE_STOPPED` e si esce. Dichiararlo PRIMA di uscire non e' un
dettaglio: uscire in silenzio farebbe considerare il servizio caduto, e la
politica di riavvio lo rimetterebbe in piedi subito dopo averlo fermato
apposta.

Guardia: `test_l_aiutante_parla_il_protocollo_dei_servizi` in
`tests/runtime/remote/test_helper_wire_contract.py`.

**MAI PROVATO SU WINDOWS.** Compila per il bersaglio, 115 prove verdi, ma la
sequenza col gestore si verifica solo su una macchina vera: il prossimo
tentativo di installazione e' anche la sua prima prova. Se fallisce ancora, il
motivo arriva in chat da solo — quella catena funziona.

## Il difetto che ha permesso di trovarla

Ultimo tentativo: **55 secondi, nessuna finestra di conferma, fallito**.

Ipotesi forte, dai tempi: l'elevazione passa senza chiedere (Roberto e'
amministratore), l'aiutante parte, e si ferma nell'attesa che il **servizio
si avvii** — `avvia_servizio()` in `helper-rs/src/win_setup.rs` aspetta 10 s e
poi esce con **codice 4**.

**PRIMA COSA DA FARE: chiedere a Roberto il testo del messaggio arrivato in
chat.** Contiene il motivo esatto scritto dall'aiutante, e **vive solo li'** —
non passa dal registro del server. Senza quel testo si tira a indovinare.

Se dice «registrato ma non e' partito entro dieci secondi»: il servizio non
parte. Perche' un servizio Windows non parte, in ordine di probabilita':
manca il consenso (no: viene scritto prima), il binario non e' dove il
servizio crede, oppure il servizio cade all'avvio. `win_serve::run` si rifiuta
di partire senza appaiamento — verificare che `%ProgramData%\Metnos\helper\
pairing.json` esista dopo il tentativo.

### Stato noto della macchina (da errori precedenti)

- Il servizio **`MetnosHelper` ESISTE** gia' (errore 1073 il 19/8). Ora
  l'installazione lo corregge invece di fermarsi.
- `metnos-helper.exe` e' in `C:\Program Files\Metnos\`.
- `%ProgramData%\Metnos\` era **vuoto**: nessun appaiamento, nessun registro.
- L'aiutante **non risponde** sul canale (la scheda mostra `machine_setup`,
  che compare solo quando l'aiutante manca).

## Un difetto scoperto mentre scrivevo questa consegna

**L'esito consegnato puo' sparire in silenzio.** Il 19/8 il registro diceva
`esito tardivo affidato al ciclo`, e in chat non e' comparso niente: il
browser aveva la pagina CARICATA PRIMA che l'ascoltatore di `operation_done`
esistesse, e uno `EventSource` scarta senza dire niente gli eventi per cui non
ha un ascoltatore.

Nell'immediato basta ricaricare la pagina. Ma la consegna e' «spara e
dimentica»: se in quel momento nessuno ascolta — pagina chiusa, riconnessione
in corso, versione vecchia — **l'esito e' perso per sempre**, e chi aveva
ricevuto «ti dico com'e' andata» non lo sapra' mai. E' esattamente il difetto
che quella funzione doveva chiudere, spostato di un metro.

Forma giusta: l'esito va CONSERVATO (un turno, o una coda per proprietario) e
consegnato alla prima occasione utile, non buttato nell'etere sperando che
qualcuno sia in ascolto. `publish_to_user` ritorna gia' quante code ha
raggiunto: **zero e' il segnale che oggi viene ignorato.**

## 19/8 SERA — risolto, col registro dell'aiutante in mano

Dopo la correzione del protocollo dei servizi l'installazione «per tutti gli
utenti» e' arrivata **fino a winget**, che ha risposto «7zip c'e' gia' ed e'
aggiornato». Il servizio era partito davvero.

Restava che subito dopo l'aiutante non rispondesse. Il suo registro
(`%ProgramData%\Metnos\helper\audit.log`, copiato a mano da Roberto perche'
ssh e' chiuso e la sandbox non lo legge) ha chiuso la questione in due righe:

    paired   S-1-5-21-...-1001
    refused  connection_error: message without terminator: the other end closed early
    refused  connection_error: message without terminator: the other end closed early

Il servizio era **vivo e in ascolto**: accettava connessioni. Qualcuno si
collegava e chiudeva senza mandare niente.

**Causa**: il comando `check` del client apriva DUE connessioni — una sonda
per guardare chi c'era dall'altro capo, chiusa subito, e poi quella per la
richiesta vera. Ma il canale serve **un client alla volta**: la sonda si
bruciava l'istanza della pipe, e la richiesta vera arrivava nel buco fra una
istanza e la successiva. Il client riferiva «l'aiutante non risponde» mentre
l'aiutante era li'.

**Correzione**: sonda rimossa. Il controllo su CHI c'e' non si perde — `chiedi`
giudica l'altro capo prima di scrivere una sola parola, ed e' sempre stato il
suo mestiere. Era la sonda a essere di troppo. Guardia:
`test_una_richiesta_apre_una_sola_connessione`.

**Da qui**: pubblicare, far prendere la versione al PC, e riprovare
l'installazione «per tutti gli utenti». Se il ragionamento regge, e' l'ultimo
anello: servizio che parte (fatto), aiutante che risponde (questo), richiesta
che passa.

Lo stato sulla macchina, dal file: appaiamento presente e corretto — SID del
proprietario, chiave del client, chiave del server, indirizzo
`http://192.168.1.33:8765`. Quattro `paired` = quattro installazioni riuscite.

## 19/8 NOTTE — il blocco del computer, e una lezione ripetuta tre volte

Dopo la correzione della sonda, l'installazione ha smesso di arrivare
all'aiutante: il client si rifiutava di eseguire QUALUNQUE cosa su quel
computer. Non c'entrava l'aiutante.

**Catena, dall'inizio.** Per leggere il registro dell'aiutante avevo chiesto a
Metnos di leggere `C:\ProgramData\Metnos\helper\audit.log`. La lettura e'
stata negata — quella cartella e' del SISTEMA — ma il client aveva gia'
ANNOTATO di aver concesso alla sandbox l'accesso a quel percorso. Da li' il
vicolo cieco: per togliere quel permesso servono gli stessi diritti che
servivano per darlo; non avendoli, la revoca falliva; e il fail-closed fermava
ogni esecuzione **per una concessione mai avvenuta**.

**Correzioni, in ordine, e ognuna ha scoperto la successiva:**

1. Il messaggio diceva «1 ACL non revocabile» — un numero, senza il percorso.
   Ora lo nomina. Senza questo non si sarebbe trovato niente.
2. «Accesso negato» in revoca ora SCARTA la voce: se non abbiamo diritti per
   togliere, non li avevamo per dare, quindi non c'e' niente di vecchio da
   temere. Ogni altro fallimento continua a bloccare, che e' giusto.
3. La (2) non funzionava, pur essendo corretta: `check_win32` scriveva il
   codice di Windows **dentro la stringa** del messaggio, e la condizione
   cercava un codice che nell'errore non c'era. Ora l'errore lo porta davvero.

### La lezione, incontrata TRE volte in un giorno

«codice 3» senza il passo. «1 ACL non revocabile» senza il percorso. Un numero
di Windows leggibile solo da un umano. Sempre la stessa forma:

> **Un dato diagnostico che vive solo nella prosa e' un dato che nessun
> programma puo' usare — e spesso nemmeno una persona.**

Chi riprende: quando scrivi un errore, chiediti se chi lo riceve puo' AGIRE.
Se la risposta e' «puo' solo mostrarlo», il dato e' nel posto sbagliato.

### Rischio residuo, da tenere d'occhio

Un executor che chiede un percorso su cui non ha diritti fa comunque
ANNOTARE una concessione che non avviene. Oggi non blocca piu' la macchina,
ma il registro dei permessi si sporca. La forma giusta sarebbe non annotare
una concessione che non e' riuscita — cioe' verificare l'esito PRIMA di
scrivere la voce. Non fatto.

## 19/8 NOTTE TARDI — l'installazione RIESCE, ma il client non vede l'aiutante

**L'installazione e' completata**: «✓ Operazione completata» su
`install_packages`, PC-ROBERTO. Prima falliva con «il file e' utilizzato da un
altro processo» (errore 32) — e quell'errore era esso stesso la prova che
l'aiutante era VIVO: teneva aperto il proprio eseguibile. Corretto fermando il
servizio prima di sostituirlo.

**MA il difetto vero e' ancora li'**, e adesso e' isolato: dopo
l'installazione riuscita, la scheda continua a offrire `machine_setup` — il
bottone che compare SOLO quando `_helper_present()` dice di no. Quindi:

- il servizio si installa (provato),
- il servizio parte e gira (provato: tiene aperto il file),
- **il client non riesce a parlarci** (aperto).

Non e' piu' un problema di installazione. E' la conversazione fra i due.

### RISOLTO IL PERCHE' — e il controllo, com'e' scritto, non puo' funzionare

Fatto arrivare il motivo fino alla scheda (era gia' noto al client e veniva
buttato via — quarta volta nella stessa giornata). Dice:

    peer_not_local_system · Dall'altro capo del canale non c'e' il servizio
    di sistema ma «(non ispezionabile)»

Il client non riesce a LEGGERE chi c'e' dall'altro capo. Non e' un caso
particolare: `helper_win::chiedi` prende il PID del server della pipe, apre il
processo e ne legge il token per ricavarne il SID. Ma il client gira **senza
privilegi** e il servizio gira **come sistema**: Windows non lascia a un
processo utente aprire il token di un processo di SYSTEM. **Il controllo, come
e' scritto, non puo' riuscire mai** — su nessuna macchina.

Ecco perche' l'aiutante non e' mai stato riconosciuto, nemmeno quando era
installato, avviato e in ascolto: il giudizio falliva prima di scrivere una
sola parola, `chiedi` usciva chiudendo la connessione, e nel registro
dell'aiutante restava «messaggio senza delimitatore: l'altro capo ha chiuso
subito» — la riga che si e' vista ripetersi tutta la sera.

**La via corretta**: non ispezionare il processo, ma chiedere **di chi e' il
canale**. `GetSecurityInfo(OWNER_SECURITY_INFORMATION)` sull'handle della pipe
da' il SID del proprietario dell'oggetto, e un client senza privilegi puo'
leggerlo. Un oggetto di proprieta' del sistema lo puo' creare solo il sistema:
la garanzia e' equivalente, ed e' ottenibile.

Il controllo sull'eseguibile atteso, invece, resta impossibile dallo stesso
lato (richiede di aprire il processo): va tenuto come rafforzativo quando
riesce, non come condizione.

### Ipotesi superate (tenute per storia)

1. **Il giudizio su chi risponde rifiuta.** `judge_peer` confronta
   l'eseguibile all'altro capo con `C:\Program Files\Metnos\metnos-helper.exe`.
   Se il percorso reale differisce anche solo per la forma (maiuscole, nome
   corto 8.3, un collegamento), il client rifiuta un aiutante autentico.
2. **La versione in esecuzione non capisce la domanda.** Il `check` manda
   ormai una richiesta `Version`; un aiutante installato prima che quel verbo
   esistesse la rifiuterebbe come malformata.
3. **Il canale non si apre affatto** (nome della pipe, SID, tempi).

**Come si distingue, ed e' gia' funzionato una volta:** il registro
dell'aiutante, `%ProgramData%\Metnos\helper\audit.log`. Roberto puo'
copiarlo (ssh e' chiuso, la sandbox non lo legge — e chiederlo via executor
ha bloccato la macchina, vedi sopra: NON rifarlo).

- una riga `refused` con un motivo → siamo nella (1) o (2), e il motivo lo
  dice;
- nessuna riga nuova → il client non arriva nemmeno a connettersi, ipotesi (3).

### Piccolo debito trovato adesso

L'esito «era gia' come lo volevi» imposta un campo `note` che **nessuno
mostra**: l'utente legge il generico «Operazione completata». Il messaggio c'e'
e non arriva.

## PROSSIMO LAVORO — «installato» non basta: serve «attivo»

Deciso con Roberto il 19/8/2026 sera, dopo che l'installazione di
LibreHardwareMonitor e' riuscita e la temperatura ha continuato a non
leggersi: quel programma espone i sensori **solo mentre gira**. Chi ha chiesto
«installa X» non deve dover sapere questo genere di dettagli.

### La forma scelta: lo dichiara CHI NE HA BISOGNO

Non `install_packages`. La conoscenza «mi serve LibreHardwareMonitor attivo»
appartiene a chi legge le temperature: e' li' che e' vera e che restera' vera.
Se entrasse nell'installatore, fra sei mesi l'installatore conoscerebbe le
esigenze di dieci funzioni diverse e non si toccherebbe piu'.

Scartate:
- **elenco di «programmi da avviare»**: nomi cablati, vale finche' qualcuno lo
  aggiorna (§7.3);
- **chiederlo al modello**: risponderebbe plausibilmente e talvolta sbagliato,
  su un'azione che tocca la macchina (§7.9);
- **farlo in silenzio**: cio' che gira su un computer lo decide chi lo
  possiede. Una funzione che si auto-installa i prerequisiti fa scoprire DOPO
  cosa c'e' dentro casa.

### Come si comporta

La capacita' dichiara il bisogno; quando non e' soddisfatto **si offre**, dalla
stessa scheda di consenso dell'installazione. Due bottoni distinti, perche'
sono due impegni diversi:

- **«avvialo ora»** — parte adesso e RESTA ACCESO. Al prossimo riavvio del
  computer sparisce, e con lui la temperatura.
- **«avvialo sempre»** — parte anche a ogni accensione. Un programma in piu'
  sempre attivo.

**Verificato: «ora» non si richiude da solo.** L'alternativa «accendi, leggi,
spegni» e' stata scartata: quel programma impiega secondi a inizializzare i
sensori, quindi ogni controllo di stato diventerebbe lento e traballante, e un
programma acceso e spento di continuo e' peggio di uno che sta acceso.

Le etichette devono dire la conseguenza, non il meccanismo: chi legge non
sceglie fra «temporaneo» e «permanente», ma fra «fino al riavvio» e «da qui in
poi». Stesso principio dei bottoni «solo per me» / «per tutti gli utenti», che
il 19/8 ha funzionato bene.

## TRAPPOLE — leggile, non riscoprirle

1. **ssh e ping verso il PC sono CHIUSI** (porta 22 e ICMP), pur essendo il
   computer acceso e Metnos perfettamente funzionante. E' cambiato qualcosa
   nel firewall, forse durante i tentativi di installazione elevata. **Non
   puoi diagnosticare via ssh**: usa Metnos (un turno, o gli executor) oppure
   chiedi a Roberto.
2. **La versione del client la sai dal server**: `devices.list_devices()[0]
   .profile_json` -> `client_version`. Non serve il PC.
3. **PowerShell via ssh**: usare `-EncodedCommand` (base64 UTF-16LE) scritto
   da uno script Python su file, MAI inline in bash — le virgolette si
   rimangiano tutto.
4. **`sc.exe` vuole chiave e valore come DUE argomenti** (`"start="`,
   `"auto"`). Uniti, Windows li richiude fra virgolette e `sc` risponde con la
   sua schermata d'aiuto. Misurato: due prove certificavano la forma sbagliata.
5. **`start= auto` non avvia niente adesso**: vuol dire «al prossimo riavvio».
   Il servizio va avviato esplicitamente, dopo aver scritto l'appaiamento.
6. **La pulizia ACL all'avvio del client** revocava permessi su cartelle
   intere (Documenti, Download): 15 minuti al 100% di un core, in silenzio, e
   impediva agli aggiornamenti di confermarsi. Corretta (parte in disparte),
   ma se il debito si riaccumula il primo avvio dopo torna a costare minuti.

## Lavoro chiesto e NON fatto

- **«Era gia' installato» non deve sembrare un fallimento** (chiesto da
  Roberto poco fa). Forma giusta: dopo un fallimento, chiedere al gestore se
  il pacchetto c'e'; se c'e', l'operazione e' riuscita. **Niente tabelle di
  codici d'uscita**: sarebbe indovinare, e sbagliare significherebbe
  dichiarare riuscita un'operazione fallita.
- **Consegna asincrona su Telegram**: fatta solo su HTTP. Regola di Roberto:
  prima la UI, poi Telegram.
- **Commenti in italiano in `helper-rs`/`client-rs`**: debito verso la regola
  «commenti del codice in inglese». Si converte cio' che si tocca.
- Una scheda pendente mia, `f2a7134c1abf410d` (sender `http:host`), lasciata
  aperta da una verifica: `dialog_pending.cancel_pending(sender_id, dialog_id,
  owner_user_id=...)`.

## Come si lavora con Roberto (imparato oggi, a caro prezzo)

- **Analisi PRIMA di codificare.** Me l'ha ripetuto due volte perche' due
  volte ho corretto meta' difetto: lo stesso blocco era duplicato in quattro
  punti e ne stavo toccando uno.
- **KISS, niente sovraccostruzione.** Ha bocciato una macchineria da 400 righe
  a favore di «ogni pezzo si aggiorna da se'», che era piu' semplice E piu'
  robusta.
- **Misurare, non ipotizzare.** Ogni volta che ho indovinato ho sbagliato;
  ogni volta che ho misurato sulla macchina vera ho trovato la causa.
- **Parole semplici**, niente log grezzi, esito prima di tutto.
- Commit locali in italiano, **senza trailer**; chiedere prima di committare.
- `/ultrareview <BASE>` — l'argomento e' la base del confronto, non il ramo.
  Con `main` sono 3269 file (oltre il tetto di 500): usare il commit d'inizio
  lavoro.

## Riferimenti

- ADR 0210 (parte D), `helper-rs/README.md`.
- Difetto ACL: memoria `project_acl_boot_blocca_autoaggiornamento.md`.
- Commit di oggi: `git log --oneline e0640cfa..HEAD` (20).
