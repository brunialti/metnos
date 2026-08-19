# Consegna — aiutante elevato Windows (ADR 0210 D), 19/8/2026 sera

> Per chi riprende. Leggi questo PRIMA di toccare qualsiasi cosa: contiene lo
> stato reale, il difetto aperto, e cinque trappole che costano ore se le
> reincontri da zero.

## Stato in una riga

Tutto committato e in esercizio; **l'installazione dell'aiutante non e' mai
riuscita fino in fondo**, ed e' l'unica cosa che manca.

- 20 commit su `session/detection-lexicon-i18n`, albero pulito.
- Suite Python **6385 verde**; aiutante **115** prove, client **73**; zero
  avvisi del compilatore sul bersaglio Windows.
- Pubblicata la **0.2.31** (client + aiutante insieme); il PC ce l'ha gia'.

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
