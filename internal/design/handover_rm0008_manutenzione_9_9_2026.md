# RM-0008: ripresa della manutenzione, 9 settembre 2026

## Prima di agire

**STOP richiesto dall'utente il 9 settembre alle 15:26 CEST.**
Il nuovo handover autorevole è
`/opt/metnos/internal/design/handover_rm0008_stop_9_9_2026_1526.md`.
Release2 build608c3a selezionata, G6 effettuato, fase5→6 rifiutata per
`administrative TCB signed binding`: Python gestito nel descriptor contro
Python OS fisso nel verificatore. Avvio locale HTTP verificato; quattro
servizi riavviati normalmente e operativi alle 15:26. Cookie nella nuova
release, nuova prova chat/Telepass ancora non eseguita. Reboot non effettuato.
Nessuna ulteriore attività in corso: non usare i vecchi comandi di cutover
qui sotto. Stato, hash e prossimo lavoro sono nel nuovo handover.

**Checkpoint corrente 9 settembre 15:15 CEST: sonda nativa continuità 122/122
riuscita; vecchia N2 PREPARED conservata fuori dai namespace attivi.**
Quattro oggetti esatti spostati senza sovrascrittura in
`/var/lib/metnos-admin/rm0008-withdrawn-n2-20260909`, sotto lock ordinati e
verifica della N1 autenticata prima/dopo ogni mossa. Nessun servizio fermato,
nessuna ricevuta cancellata, nessun rejected riscritto, vecchio set conservato.
Export corretto `/tmp/metnos-continuity-export.5MXSbT` congelato; suite private
2400/47 e pubbliche 2404/47, Chromium 38, GII zero. Build-only nuovo in corso
tramite `/tmp/metnos-rm0008-build-continuity-successor-20260909.py`.
Non usare il vecchio complete-successor: appartiene alla N2 archiviata.
Cookie non ancora dichiarati attivi; reboot reale non effettuato.
Il resoconto canonico sotto riportato prevale sui checkpoint storici.

**Checkpoint corrente, 9 settembre 14:43 CEST: N1 di nuovo disponibile;
cookie NON distribuiti; NON rilanciare il completamento N2 invariato.**
Il journal completato N1 è stato archiviato alle 14:20 con una sola rinomina
conservativa verificata. Il successivo completamento N2 ha invece fermato i
servizi durante la manutenzione e si è arrestato sulla riattestazione di
`core:change_files_format/manifest.toml`. Ora N2 ha un record PREPARED, il set
di autorità e ricevute di contesto parziali; la richiesta produttore
`sha256:d56bb4d40d2b795d754f89bc7d2629f2b8bac4292cdcda1b17a315a7534c9d59`
è terminalmente rejected (`property_runner_unavailable`). Non cancellarla,
riscriverla, né interpretare quell'errore generico come prova sufficiente per
un retry: occorre continuità autenticata per i current invariati e recupero
esplicito dello stato N2 incompleto, ancora da progettare e verificare.

Il riavvio N1 era bloccato dal confronto globale dei bundle di tutte le
transazioni, compresa N2 pendente. La rimozione di sole sei righe, già presente
nel prodotto N2, è stata verificata con 13 test e una sonda nativa sulla N1
autenticata prima di installarla nell'helper amministrativo live. SHA attuale
`cb7f1b8ace83036928d49746dcfa822088fb42e193fdf8de076861d3c7c9d1ee`;
backup root 0400 `/var/lib/metnos-admin/rm0008-preflight-cbe320-before-availability.py`.
Nessuna release firmata, head, unit o ricevuta storica modificata. Alle 14:43:
HTTP/browser 200, worker ready, Telegram attivo, stessi PID del ripristino.
Il vecchio raccordo helper pinna ancora cbe320: va aggiornato e ritestato prima
di un futuro uso, non invocato ora. Correzione lifecycle privata verificata
2368/47; continuità current in implementazione separata, non ancora congelata.
Il reboot reale resta da eseguire. Turno `901ab8ad`: timeout login, nessuno step
action, schermata esistente in esame; non attribuire ancora la causa ai cookie.

Il resoconto canonico aggiornato è
`/opt/metnos/internal/reports/rm0008-maintenance-analysis-20260909.md`.
**I checkpoint seguenti sono storici, non istruzioni per ritentare.**

Checkpoint precedente: Release 2 firmata ma **non selezionata**. Il primo
completamento si è fermato prima della manutenzione con
`birth_provisioning_transaction_conflict`: il journal V2 di N1, già completato
con head/fase 6, non era stato archiviato dal prodotto. N2 ha soltanto il claim
riservato, nessun checkpoint. Nessun servizio fermato. Il recupero conservativo
è una sola rinomina nello stesso Birth root, da
`.birth-provisioning-v2.txn.7231f5ab98d54ea94ccbbcfcea3478a1` a
`.birth-provisioning-v2.completed.7231f5ab98d54ea94ccbbcfcea3478a1`, preservando
i sei oggetti e il piano riservato 0600. Modulo `/tmp/metnos-rm0008-archive-completed-journal-20260909.py`,
SHA `be1f44463782279bbd1bfccfea8e9b1413e50041c1b697387f039ec03c8e474c`,
31 test passati. Wrapper `/tmp/metnos-rm0008-preserve-completed-n1-20260909.py`:
audit nativo riuscito sotto lock deployment e Birth esclusivo; archiviazione
non ancora eseguita. Non cancellare né modificare la release firmata. Riprendere
la stessa N2 tramite `/tmp/metnos-rm0008-complete-successor-20260909.py complete`
solo dopo il recupero. La correzione lifecycle del prodotto è separata, in corso
nel worktree privato, e richiederà un successivo incremento coerente.

Pubblico `4e3dea4`: **tutti i nove job CI 34346999399 riusciti**. Cookie ancora
non in esercizio; nessun avviso chat-ready. Dopo attivazione analizzare il turno
`901ab8ad` come richiesto. Per fatti successivi fa fede il resoconto canonico
`/opt/metnos/internal/reports/rm0008-maintenance-analysis-20260909.md`.

Checkpoint 13:43 CEST: integrazione successore completata e congelata; suite
privata **2340 passate/47 esclusioni**, suite pubblica **2344/47**, selezione
Chromium reale/cookie/manutenzione **325/1**, GII zero. Il verificatore candidato
ha autenticato in sola lettura i quattro servizi correnti sulla macchina reale,
senza avviarli o arrestarli. Questa non è ancora una prova completa di cutover
o reboot. Cookie **non distribuiti**, turno chat non ancora dichiarato pronto.

Pin privato `sha256:85f0724bd79798057a191f78e4ec22ff32dbea40d47f40dea3cdbf8289df6c88`;
pin pubblico/export `sha256:6c345ff71a66fa5f81b4d94618a35d177cc53fda9ab7681430eb51c5e9dfa2d6`.
Export `/tmp/metnos-successor-export.Y2Ttpl`: solo metadati normalizzati 644/755,
vuote directory workspace diagnostiche rimosse. Scanner produttivo locale passato
con 1745 file/201 directory. Primo build-only rifiutato prima della ricezione
per permessi export, non durante un cutover. Il retry build-only è in corso:
non dichiarare Release 2 costruita finché non si legge l'esito. Nessun servizio
fermato; non ripetere `/tmp/m8`. Pubblicazione Git incrementale gestita dall'agente
administrative_replacement, ancora da confermare con commit/esito CI effettivi.
Esito successivo letto: build-only riuscito, Release 2 firmata
`sha256:3878c006f041536370eae081bb9b51b4153713a0af506d08dbd16e622a31a514`,
source `sha256:72109fe3c63166767c2e8f0c22fc0b16fe1e9ae5064dd078e4a82408962c6c4c`.
Contesto precedente verificato; nessun cambio head o arresto servizi.
Pubblico `4e3dea411a28ff3bc2b10d5cc9f5cadf4f565a55` pubblicato normalmente,
manifest 7/7 e GII zero, note inglesi; CI `34346999399` ancora seguita.
Restano audit pre-cutover e raccordo conservativo dell'helper d'emergenza.
I checkpoint seguenti sono storici; prevale il resoconto canonico aggiornato.

Ultimo aggiornamento: pubblico `e3a8e96f9342cd5d81e1077f035c85a48c6f1da5`,
nota inglese `Refresh model settings by content on every platform`.
CI `34342409019`: tutti i nove controlli riusciti, inclusi Windows e Ubuntu.
Corregge il vero errore Windows del precedente `9703989`: cache ora sui byte
esatti, non sui metadati; 2138 test pubblici riusciti/43 esclusioni, manifesto
7/7 e GII zero. Guide IT/EN già distribuite con Pages `67524226`.
Dettagli aggiornati nel resoconto canonico; i checkpoint sottostanti sono storia.

Priorità esplicita: rendere riprovabile il turno login in chat. Cookie ancora
solo nel candidato, **non installati**; nessun messaggio di prova pronta.
Il successore ha ora nuclei testati per sostituzione conservativa, quiescenza
senza disabilitazione, osservazione legacy senza modifiche, bundle per release,
ripresa 5→6 e avvio dei servizi selezionati con helper di versione successiva.
Mancano integrazione lettura del contesto precedente, chiamanti e prova firmata
N→N+1 con interruzioni. Le impronte private sono intenzionalmente da aggiornare
dopo le modifiche parallele; non lanciare il deployment con quelle correnti.
Il vecchio helper riparato root-owned differisce dall'artifact firmato N:
non aggirare questo problema ammettendo byte arbitrari nel prodotto.
Nessun nuovo arresto live, nessun reboot reale in questa continuazione.

Aggiornamento 12:05 CEST: incremento manutenzione `0281db2` pubblicato con note
inglesi, GII zero, 2127 test portabili e 7 prove manifest riusciti. CI remoto
`34337722455` completata: tutti i nove controlli riusciti. Guide IT/EN
distribuite (`26b96372`), HTTP200 e byte-identiche. Cookie semantici completati
nel candidato privato, non installati; selezione combinata con Chromium reale
339 passate/1 esclusione. Dettagli e nuove impronte nel resoconto canonico.
Roberto vuole essere avvertito quando può riprovare il turno in chat: avvertire
solo dopo distribuzione e verifica del percorso reale, non ora.

Aggiornamento 12:28 CEST: verificate anche le configurazioni come richiesto.
Pagina Modelli in esercizio HTTP200, tre famiglie configurate, servizi stabili.
Due difetti riprodotti e corretti solo nel candidato: TOML invalido mascherato da
default e cache che ignorava sostituzioni con data conservata. Prove complete
configurazione/manutenzione/avvio: 168 passate, una esclusione; comprende vere
richieste HTTP isolate di salvataggio/ripristino, autenticazione e conflitti.
Nessuna impostazione reale modificata. Incremento pubblico `9703989` con note
inglesi: 2136 prove portabili e 7 prove manifest riuscite, GII zero; inventario
canonico aggiornato a 884 file Python. CI `34340747729` in corso. Guide IT/EN
distribuite (`67524226`), byte-identiche alle pagine revisionate. Correzioni
configurazioni non ancora installate nella release in esercizio.

L'analisi del successore è in `rm0008_successor_update_analysis_9_9_2026.md`:
il bundle, incluse le unità, è ancora congelato erroneamente su tutta la storia.
Non basta sostituire i file. Core sostituzione unità implementato e testato
dall'agente (122 passate/15 esclusioni), ma non collegato al percorso produttivo.
Nessun nuovo tentativo in esercizio. I cookie non sono ancora distribuiti.

Metnos è nuovamente utilizzabile. Non ripetere transizione, reset, `/tmp/m8`
o la riparazione a predecessore esatto. Non copiare il checkout di sviluppo
sopra la release immutabile. Roberto ha già autorizzato il recupero conservativo
e la manutenzione; non confondere lo stato storico `blocked` dell'obiettivo
con una mancanza di consenso. Non salvare credenziali in questo documento.

Il resoconto operativo aggiornato è
`/opt/metnos/internal/reports/rm0008-maintenance-analysis-20260909.md`.
Contiene diagnosi, confini approvati, errori delle prove, verifiche e link CI.

## Cosa è realmente installato

- Verificatore amministrativo `/usr/libexec/metnos/executor-birth-v1/preflight.py`:
  SHA256 `cbe320dcc21d066ca565a6972fd1af611320a913798d6ce8cd7bce1cf432f099`.
- Copia precedente recuperabile:
  `/var/lib/metnos-admin/rm0008-service-startup-20260909/preflight.before.py`.
- Il verificatore autentica la storia firmata e i file immutabili, ma controlla
  il singolo servizio senza congelare l'impronta storica degli strumenti OS.
  L'interprete gestito viene eseguito davvero dopo la rinuncia ai privilegi.
- Inizializzazione volatile al boot già riparata tramite tmpfiles.
- La release `00000000000000000001`, le firme, la catena e i journal non sono
  stati riscritti. La riparazione esatta è privata e non un aggiornamento generico.

## Prove in esercizio

- HTTP PID309618, Telegram PID310385, worker LRE PID310386: attivi, zero
  riavvii automatici al controllo successivo alla pubblicazione.
- Browser, display, modello, ricerca e controllo completo pronti;
  `metnos.target` attivo e abilitato, watchdog operativo.
- Turno `7baa7483d2024798`: `get_now` realmente eseguito, risposta riuscita.
- Turno Tutor `c743d44178574b55`: risposta su LRE in 13,61 secondi,
  nessun executor eseguito. Questa seconda prova esercita anche il modello.
- Telegram di ripristino già inviato con successo: non duplicarlo.
- Ultimo controllo alle 10:38 CEST: HTTP `http://127.0.0.1:8770/agent/health`
  200, LRE pronto; browser `http://127.0.0.1:8771/health` 200, contratto allineato;
  modello `http://127.0.0.1:8080/health` 200. Nome reale dell'unità Telegram:
  `metnos-telegram-daemon.service`. PID invariati, zero riavvii automatici.
- Nessun reboot dell'intera macchina eseguito; non confondere l'avvio riuscito
  o la prova isolata tmpfiles con una nuova prova completa di reboot.

## Codice e pubblicazione

Worktree privato: `/opt/metnos/.claude/worktrees/rm0008-reboot`.
Contiene anche modifiche CI della sessione precedente: non annullarle.
Checkout autore `/opt/metnos` molto modificato: non usarlo per esportare tutto.
Clone pubblico verificato: `/opt/metnos/.claude/worktrees/rm0008-ci-public`.
Su `brunialti/metnos`, main, sono pubblicati incrementi con note in inglese:
`0c8238d`, `fcb9a7a`, `2e893bf`, `d95a145`.
L'ultimo corregge un riferimento errato nel test di attivazione Linux;
21 test mirati e 7 prove manifest passano. Run CI `34329981699` completato
con successo alle 10:42 CEST: tutti i 9 job verdi, riepilogo incluso.
Linux: 2125 test passati, 45 saltati; ulteriore prova Linux reale 7/7.
Windows: 1439 passati, 689 esclusioni di piattaforma; oracolo identità reale 11/11.
Link: https://github.com/brunialti/metnos/actions/runs/34329981699.
GII filesystem + indice finale zero problemi, senza nuove esenzioni.
Le impronte di revisione private e pubbliche sono diverse per costruzione.

Sito IT/EN distribuito: rilascio Pages `da1c5b60`; pagine modificate HTTP200
e byte-identiche ai file revisionati. Pubblicato dal clone pubblico con
`./deploy.sh --static-only`; catalogo Tutor in esercizio non ricompilato.
La copia grezza delle pagine nel checkout autore contiene riferimenti privati:
non pubblicarla. Il vecchio publisher con force-push non va usato.

## Cosa resta da fare, senza dichiararlo già distribuito

Aggiornamento 11:48 CEST: Roberto ha chiesto esplicitamente lavoro parallelo su
cookie semantici nel login e certificazione/manutenzione. Il primo è affidato
all'agente `semantic_login_cookies`; non ci sono modifiche browser in esercizio
oltre alla creazione della sola cartella privata di Chrome per correggerne il
crash iniziale. La modifica XDG del prodotto è testata, non distribuita.

Il nuovo catalogo sorgente rimuove l'arresto generale causato dal solo errore di
prontezza. Il watchdog conserva HTTP di manutenzione e non riavvia attraverso
un catalogo servizi non verificato. Prove combinate: 258 passate, 1 esclusione;
ulteriore selezione pubblica catalogo/manutenzione: 66 passate. Le correzioni
restano da installare mediante aggiornamento coerente. Nessun reboot eseguito.
I due vecchi test privati dipendenti da impronte storiche della macchina sono
stati sostituiti da prove del rifiuto prima degli effetti, senza cambiare i pin
degli script monouso: questi ultimi non vanno rieseguiti.

Il resoconto canonico in testa contiene diagnosi del turno `901ab8ad5fb44a7a`,
difetto Unicode del vecchio filtro cookie, riparazione browser e punti mancanti
del percorso successore. La pubblicazione del nuovo incremento è in verifica;
non sostituire ancora l'ultimo commit remoto verificato indicato sopra.

La certificazione remota del commit `d95a145` è chiusa con successo. Per nuovi
test usare il generatore dell'inventario dopo lo staging e prima del commit;
non indebolire controlli o esclusioni per ottenere un esito verde.

1. Distribuire attraverso un aggiornamento coerente le correzioni già testate
   a HTTP di manutenzione, configurazione SMTP per invocazione e controllo
   servizi nel catalogo selezionato. Sono su GitHub, non nella release live.
2. Completare il percorso ordinario autorizzato di aggiornamento del nucleo;
   non fingere che i comandi amministrativi ancora incompleti siano operativi.
3. Separare il guasto di una dipendenza dall'arresto dell'interfaccia di
   manutenzione. La relazione globale di quarantena esiste ancora.
4. Prova di reboot nel perimetro autorizzato, poi criteri RM-0008 F5/F6:
   recupero del servizio non equivale a chiusura dell'intera roadmap.

Non interrompere turni attivi e non disabilitare LRE per rendere verdi i controlli.
In presenza di stato inatteso, identificare la divergenza prima di modificare
file o autorità. Nessun ampliamento automatico di privilegi o fiducia.
