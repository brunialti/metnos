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

## IL DIFETTO APERTO

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
