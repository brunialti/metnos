# Arresto dell'analisi foto LRE — 16 settembre 2026

Stato: correzioni e nuova console pubblicate nella release 58; vecchio lavoro
cancellato con copia recuperabile su richiesta di Roberto. **Stabilità non
certificata:** la nuova indicizzazione è nuovamente `needs_attention`.
La protezione del servizio ha retto la contesa senza riavvio, ma rimangono
errori del catalogo e dell'analisi. Nessuna ripetizione durante la diagnosi.
Release osservata inizialmente: 57.

## Esito alle 12:22 (Europe/Rome)

Il servizio è pronto ma il lavoro `wrk_b7a73e10713b4c15a720d264cf8fd98a`
è `needs_attention`, revisione `rev_d9c8f1bcfbea475da2680a9d00fe9226`.
La scansione ha censito 30.942 sorgenti in 967 gruppi; il blocco `discover`
e tutti i 967 blocchi `folders` sono confermati. `analyze` ha 14 blocchi
da verificare, 953 pendenti e nessuno in esecuzione. Non si è perso tutto
il lavoro, ma non è provata la recuperabilità automatica dei blocchi interrotti.

## Cronologia dimostrata

- 12:04:42: servizio configurato con 31 corsie di orchestrazione automatiche.
  Non significa 31 analisi foto o 31 processi contemporaneamente.
- 12:15:11: scansione completata e confermata.
- 12:17:42: preparazione dei 967 gruppi completata.
- 12:17:47: quattro errori `sqlite3.OperationalError: database is locked`
  nel ciclo parallelo, entrando in transazioni per `reconcile_expired` e
  `adopt_reusable_results`; il servizio passa a `degraded`.
- 12:18:18: arresto delle corsie non concluso entro il limite, 14 ancora attive.
- 12:18:29–31: sei tentativi falliscono con
  `execution.executor_unavailable` / `capability_unavailable`, ripetizione
  manuale. Il codice colloca questa categoria nella cattura di un'eccezione
  del caricamento dell'esecutore verificato. Non dimostra che l'esecutore
  manchi dall'installazione: `discover` aveva usato lo stesso nome.
- 12:19:55: ancora otto blocchi segnati in esecuzione, uno assegnato e sei
  da verificare; file intermedi scritti alle 12:19:50. Quindi in quel momento
  non era un arresto completo, pur essendo già degradato.
- 12:20:21: ultimo file intermedio osservato; dieci parti `analysis`, non
  dieci gruppi LRE confermati.
- 12:20:49: processo del servizio terminato con codice 1; picco memoria
  contabilizzato da systemd 44,3 GB. Non è una misura della memoria delle
  sole foto né prova di esaurimento memoria.
- 12:20:52: servizio ripartito, PID 531750. Roberto ha confermato di averlo
  riavviato manualmente. Nessun riavvio è stato richiesto da questa diagnosi;
  questo episodio non è una prova di riavvio automatico del servizio.
- 12:21:19: recupero di nove assegnazioni scadute. Quella mai eseguita torna
  pendente; otto iniziate passano a verifica manuale per consumo incerto.
  I sei precedenti errori rimangono: totale 14. Servizio di nuovo `ready`.
- 12:22:03: API ancora `needs_attention`, nessun blocco in esecuzione;
  avanzamento delle unità note 50%, previsione finale assente. Il 50% non
  misura metà delle foto analizzate né metà del tempo necessario.

## Cause provate e limiti della diagnosi

`service._run_parallel_cycle` somma tutti i fallimenti delle corsie raccolti
nello stesso passaggio. Almeno tre fanno fallire il ciclo del servizio:
quattro contese SQLite contemporanee sono quindi sufficienti senza tre
distinti giri falliti. Ogni corsia esegue anche manutenzione e materializzazione
in `ExecutionBridge.run_once`, oltre al proprio lavoro. Questa è una causa
verificata di fragilità sotto contesa; il detentore originale del blocco di
scrittura non è stato identificato.

La causa interna esatta delle sei eccezioni del caricatore non è conservata
nel DTO: `ExecutionBridge._prepare_executor` le traduce tutte nella stessa
categoria. Non è dimostrato che abbiano la stessa causa della contesa SQLite.
Non disabilitare firme, attestazioni o controlli di generazione per aggirarle.

La scansione iniziale scrive parti immutabili ma non conserva un cursore di
ripresa: una scansione interrotta ricomincia. Ora quella fase è confermata;
non deve essere cancellata o rifatta per recuperare la fase di analisi.
La previsione corrente esclude esplicitamente i lavori con più fasi, quindi
per questo piano resta assente anche quando il lavoro procede regolarmente.

## Intervento da progettare e verificare prima della ripresa

1. Riprodurre la contesa con un archivio isolato e più corsie, senza foto reali;
   misurare la durata delle transazioni e separare manutenzione/esecuzione.
2. Distinguere contesa temporanea da guasto del servizio, con attesa limitata
   e senza invalidare tentativi ancora vivi; mantenere fencing e limiti.
3. Contare i fallimenti del ciclo, non scambiare fallimenti simultanei di
   corsie per distinti cicli consecutivi. Provare arresto e recupero sotto carico.
4. Conservare un codice diagnostico chiuso della causa del caricatore,
   senza esporre eccezioni arbitrarie, percorsi o credenziali.
5. Verificare risorse, assegnazioni e consumi incerti prima di autorizzare
   la ripetizione dei 14 blocchi. Non cambiare direttamente lo stato nel DB.
6. Mostrare distintamente servizio pronto, lavoro da verificare, fase,
   unità confermate, parallelismo effettivo e motivo della stima assente.

## Evidenze in sola lettura

Evidenze private create dal lettore amministrativo sotto
`/var/lib/metnos-admin/agent-runs/`:

- `run-4zbpp0vp`: avanzamento alle 10:19:55 UTC.
- `run-nia1ni9l`: stato recuperato alle 10:21:36 UTC.
- `run-mkmg8_4a`: salute, release attestata, servizi e API alle 10:22 UTC.

Registri del servizio `metnos-durable-worker.service`, intervallo
10:04–10:22 UTC; lettura locale del codice corrispondente alla release 57.
La diagnosi non ha scritto al database di esercizio, cancellato indici,
premuto «Riprova» o modificato il numero di corsie.

## Correzioni candidate e prove indipendenti

- **Disponibilità del catalogo:** la data del database di statistiche entrava
  nella firma della cache. Una chiamata ordinaria da HTTP, Telegram o LRE
  poteva invalidarla e, se concorrente a entrambe le autenticazioni, produrre
  `store_snapshot_unstable`. Riproduzione con contratto firmato e quattro
  lettori concorrenti. Ora viene firmato e applicato lo stesso snapshot
  immutabile degli override di ciclo di vita. Conteggi d'uso ignorati,
  archiviazione/deprecazione e rimozione degli override ancora invalidanti,
  anche con WAL; stato illeggibile rifiutato. Una seconda review ha scoperto
  e fatto correggere anche un'applicazione finale scollegata dalla firma.
- **Scritture inutili:** controlli preliminari conservativi in sola lettura
  evitano le transazioni di recupero/riuso quando non c'è lavoro. Tutte le
  verifiche originali restano nella transazione autorevole; nessuna nuova
  autorità derivata dal controllo preliminare. Test su writer esterno,
  rinnovo concorrente della concessione, pausa, orologio e revisioni precedenti.
- **Supervisione:** contese native SQLite distinguibili da guasti, attesa
  crescente e limitata senza interrompere subito le corsie vive; un gruppo
  di eccezioni simultanee conta come un solo ciclo fallito. Firme, fencing,
  contabilizzazione e massimi di tentativi restano invariati.
- **Diagnostica:** gli errori del caricatore conservano una causa enumerata,
  senza testo arbitrario dell'eccezione; nessuna nuova ripetizione automatica.
- **Interfaccia:** descrizione/cartella/fase del lavoro, lettura chiara dei
  tempi e dei motivi di n.a., parallelismo non confuso con thread/processi,
  avvisi distinti dalla salute del servizio. Prove Chromium IT/EN isolate.

Stress riproducibile opzionale `test_contention_scale.py`: 31 corsie,
966 unità, circa 4 MB di risultati sintetici, nessun modello né file reale.
Prima: 2 SQLITE_BUSY, 5,60 s, manutenzione ripetuta circa 990 volte per tipo.
Dopo: 0 SQLITE_BUSY, 3,92 s, 966 unità confermate e nessuna scrittura di
manutenzione inutile. Non è una garanzia per qualunque scala o carico.

## Blocco residuo del lavoro già interrotto

Lettura `run-igmgra5p` alle 10:39:56 UTC: `usage_unknown=1`,
`usage_complete=0`, consumo noto 116.489 token in ingresso e 3.637 in uscita,
costo registrato zero. I sei errori del caricatore hanno uso verificato;
gli otto tentativi interrotti hanno uso incerto. Non inferire che l'uso
ignoto sia zero perché i modelli sono locali.

Non esiste oggi un percorso canonico per riconciliare questo flag storico:
la ripetizione manuale è bloccata dal controllo contabile, la registrazione
dei consumi richiede una concessione viva, e l'ammissione di revisione è
ammessa soltanto su un lavoro iniziale. Nessuna scrittura SQL amministrativa
è autorizzata come sostituto. Un nuovo lavoro non riusa automaticamente la
scansione idempotente (il riuso canonico è limitato a unità pure).

Chiesta a Roberto una scelta: nuova procedura esplicita di riconciliazione
prudenziale conservando i risultati, oppure nuovo lavoro con vecchio storico
intatto e preparazione ripetuta. Nel frattempo si possono pubblicare le
correzioni preventive senza sbloccare arbitrariamente il lavoro esistente.

## Decisione e pubblicazione — 16 settembre, 12:54 Europe/Rome

Roberto ha scelto la soluzione semplice: nuovo lavoro, **cancellazione del
vecchio e pulizia dello storico**, senza nuova procedura contabile. Ha chiesto
di attendere il ridisegno della UI: era già incluso nella candidata; la
pulizia è avvenuta soltanto dopo il riscontro della nuova console viva.

- Codice e prove: `61ea468c`; impronte riviste: `910c6761`.
- Release 58: `sha256:4e6ed3ff9093e443aef9b8306e4fb3248d656b259c8d4e7561b7287d2e08b0af`.
  Procedura canonica conclusa con `CUTOVER_OK`, `RELEASE_EDITS_ADMITTED`,
  uscita 0. Nessuna modifica diretta alla release installata.
- Suite principale: 723 superati, 6 saltati; le quattro prove di carico
  opzionali e le prove Chromium sono state eseguite separatamente.
  Browser IT/EN e desktop/mobile: 4 superati. Altri 33 controlli su guide,
  riferimenti, integrazione immagini e caricatore superati.
- Riscontro `run-3hdikc1d` alle 12:53:08: HTTP, Telegram, browser e LRE
  attivi, motore pronto, nuova descrizione/cartella/parallelismo nell'API,
  nessuna chiave i18n irrisolta nella pagina. Il vecchio job restava fermo,
  con zero unità in esecuzione: disponibilità del servizio non scambiata
  per avanzamento del lavoro.
- Guide pubbliche IT/EN pubblicate via `deploy.sh --static-only`, controllo
  di 99 pagine indicizzabili; distribuzione `55f459ce.mykleos.pages.dev`.
  Nessuna pubblicazione di questo rapporto, foto o dati interni; nessun push
  GitHub. Cloudflare resta solo distribuzione della documentazione.

### Cancellazione autorizzata e recuperabilità

Riscontro amministrativo `run-vvz4rgfo`: copia SQLite online verificata,
annullamento tramite API ordinaria, attesa dello stato terminale, nuova
verifica dell'esatto job/revisione e assenza di unità vive sotto transazione.
Rimosso soltanto `wrk_b7a73e10713b4c15a720d264cf8fd98a` con cancellazione
in cascata dello storico correlato; controllati tutti i residui e le chiavi
esterne. Nessun flag contabile azzerato per riabilitare tentativi scaduti.

Copia privata recuperabile:
`/var/lib/metnos-admin/agent-runs/run-vvz4rgfo/lre-before-reset.sqlite3`.
I file intermedi del vecchio lavoro restano conservati su disco, separati
per identificativo; non sono un indice completo pubblicato e non diventano
risultati del nuovo job. Foto originali, conversazioni, configurazione e
altri dati non sono stati cancellati. Un eventuale ripristino deve essere
selettivo: non sovrascrivere il DB vivo dopo l'avvio di nuovi lavori.

### Nuovo avvio reale e verifica in corso

Turno `c0f620dae1f5402c`, stessa richiesta letterale
`cerca localmente foto con il mare`, inviato una sola volta dopo aver
verificato il deposito vuoto. Nuovo lavoro
`wrk_515d36161628473e8b5bd54218023af6`, revisione
`rev_0d75db80950c4426adee56b91e2ba939`, avviato alle 12:54:26.
Alle 12:57:27 la scansione ha scritto 437 parti, 13.984 sorgenti:
avanzamento effettivo osservato, non soltanto heartbeat. Motore pronto,
nessun riavvio dei quattro servizi, analisi non ancora cominciata.

Roberto ha approvato l'aspetto della nuova interfaccia. Test e seed IT/EN
verificati; percorsi e identificativi non si traducono. Review finale del
design: 4 prove browser nuovamente superate. Rilievo minore residuo,
**non corretto in questa release**: il rinnovo dell'elenco ricrea i pulsanti
e può perdere il fuoco della tastiera sull'attività selezionata. Non influisce
sul lavoro o sulla persistenza; da preservare in un prossimo intervento UI.

Roberto ha chiesto di non aggiungere conteggi tecnici di processi/thread:
resta soltanto il conteggio semplice dei blocchi simultanei già disponibile.
Non viene introdotta una percentuale sintetica di parallelismo, né vengono
confusi thread esistenti, unità avviate e uso effettivo di CPU/GPU.

### Regressione reale osservata — certificazione respinta

- 13:02:10: scansione confermata, 30.942 sorgenti in 967 gruppi.
- 13:04:37: preparazione dei 967 gruppi conclusa.
- 13:04:43–44: due cicli di contesa SQLite (1 e 5 corsie). Il nuovo
  supervisore mantiene i lavori vivi; nessun riavvio né consumo reso ignoto
  mediante arresto forzato. Motore torna `ready` alle 13:05:50.
- 13:05:49–50: quattro caricamenti falliscono con causa conservata
  `store_inventory_invalid`. **Non** è `store_snapshot_unstable`, e non è
  una prova che la correzione della firma delle statistiche sia inefficace.
  Il sottocodice dell'inventario è ancora perso: lacuna diagnostica da chiudere.
- 13:21:12 (`run-mp8j6m5m`): 2 gruppi di analisi confermati, 6 falliti
  permanentemente, 4 da verificare, 7 ancora in esecuzione e 948 pendenti.
  Scritte 127 parti immagine e 2 aggregazioni da 32. Il lavoro è parziale e
  non può essere presentato come sano o completato.

Le letture amministrative non hanno riavviato servizi né ripetuto unità.
Quattro inventari concorrenti a coppie come utente `metnos` sono puliti;
quattro audit completi senza effetti autenticano 123 contratti ciascuno
(`run-howu0a9u`). Il fallimento storico non è perciò un contratto
persistentemente assente; la sua causa transitoria precisa resta indimostrata.

Limite file aperti del worker: soft 1024, hard 524288; campione attuale
134 descrittori, di cui 126 SQLite. Prova isolata con composizione reale
di 31 corsie per tre cicli, garbage collector disabilitato: picco 231,
76 dopo ogni chiusura, nessuna crescita tra cicli. **Non** attribuire
il guasto a esaurimento descrittori né aumentare limiti come presunto fix.

`run-h1d5tcp7`: le 32 foto del primo gruppo fallito sono JPEG leggibili
da Pillow, con dimensione e mtime uguali all'inventario. Nessuna analisi
modello né scrittura eseguita dalla sonda. Il motivo applicativo esatto
non è persistito: il collegamento conserva solo `execution_failed`.

La previsione di fine dell'intero piano multifase rimane **non implementata**;
`n.a.` non è un aggiornamento mancato. Non confondere questo limite con
l'arresto del lavoro e non dichiarare conclusa la richiesta originale di ETA.

## Ripresa delle correzioni — 16 settembre, 15:08 Europe/Rome

`run-ls88n451`: servizio pronto, nessun blocco vivo; analisi con 4 gruppi
confermati, 11 falliti, 4 da verificare, 948 pendenti. Ultimo progresso alle
13:32. Conservate 216 parti immagine e quattro aggregazioni. Nessun riavvio.
La decodifica completa delle 32 foto del primo gruppo fallito è riuscita
(`run-c6_aulkv`), non soltanto la verifica delle intestazioni JPEG.

`run-ywk6_jc7`: tutti gli undici fallimenti applicativi terminano la loro ultima
chiamata VLM con 512 token in uscita; i quattro gruppi riusciti hanno 32 chiamate
ciascuno e ultima risposta inferiore al limite. Uso noto in tutti i tentativi.

Riproduzione locale limitata alla sesta foto del primo gruppo fallito, senza
scrivere all'indice e senza rendere pubblici percorso o risposta:

- `run-k3rxvy4r`: modello fermato per inattività, nessuna risposta; nessun
  avvio implicito durante l'invocazione diagnostica.
- `run-znv97_3p`: normale avvio gestito del modello, stessa politica installata;
  `finish_reason=length`, 512 token, 1694 caratteri, JSON non valido,
  `no_json_found`. Guasto riprodotto.
- `run-5kw780ns`: stessa foto/prompt/modello/limite, risposta vincolata a schema;
  `finish_reason=stop`, 186 token, JSON valido, descrizione presente.

Correzione candidata: schema di descrizione nel dominio foto, parametro
generico nel client VLM, validazione della risposta e rifiuto esplicito di
`length` dopo la contabilizzazione. Nessun aumento automatico dei limiti,
nessuna ripetizione nascosta o ripiego libero. Lo schema entra nell'identità
dell'analisi; il preesistente riuso legacy senza identità resta distinto.
Un formato vincolato non garantisce che qualsiasi risposta/lingua stia in 512
token; l'eccesso rimane un errore onesto. Prova reale del backend locale,
non certificazione di ogni provider configurabile.

Diagnostica candidata: sottocodici inventario ed errno enumerati; codici errore
executor conservati solo dall'enum dello schema approvato. Gli schemi immagini
discovery/part/published passano a /2: il vecchio lavoro non è migrabile in modo
implicito. Quattro errori del catalogo restano di causa non ancora dimostrata.

Roberto ha approvato la stima della **fase corrente**, separata dalla fine
dell'intero piano. Implementazione e prove IT/EN disponibili; almeno tre
completamenti della medesima fase, inventario chiuso, nessuna incertezza,
ancora temporale persistita e freschezza limitata. Stato di attenzione prioritario.

Roberto ha inoltre autorizzato: pubblicazione, prova limitata, quindi annullamento
del lavoro attuale e nuova indicizzazione completa **conservando storico e
risultati intermedi**. Nessuna nuova cancellazione di foto, indice o storico.
Attivazione ancora da eseguire al presente checkpoint.

Verifiche candidate: 661 test superati, 4 prove di carico opzionali saltate;
la replica senza HTTP ne supera 649. Ulteriori due prove aggiunte su rifiuto
HTTP dello schema senza ripetizione e identità dell'analisi: gruppo mirato
47/47. Console: prove Node/catalogo e browser reale IT/EN desktop/mobile
superate dall'agente UI (63 test complessivi con API e aggregazioni).
Una prima suite nel sandbox è stata interrotta dopo 547 successi perché
bloccata dal server locale; replica autorizzata conclusa positivamente.
Guide Tutor: corretta segmentazione che separava condizioni e promessa
di ripresa; 20/20 controlli documentali riusciti. Review VLM indipendente:
nessun difetto bloccante, limiti del formato e del riuso legacy esplicitati.

## Pubblicazione e prova limitata — 16 settembre, 15:37 Europe/Rome

Release 59 pubblicata tramite autorità canonica: build
`sha256:23e1dc2ae7ab669c245b35a717eaeeee51b210597acef2fb2f775b70a9814609`,
`CUTOVER_OK`, `RELEASE_EDITS_ADMITTED`, uscita 0. Guide statiche IT/EN
pubblicate su Pages (`1e9ac020`); verifica HTTP dal client di controllo
rifiutata con 403, quindi non attestata la lettura dal dominio pubblico.

`run-lw518ckd`: prova autorizzata su sei copie private, inclusa la foto del
guasto riprodotto; turno `1e66f8c5402a4ea4`, lavoro
`wrk_495453140014412a9c70792b4a23c21f`. `run-bhfdribe` e `run-0_env7fh`:
completato, 5/5 unità confermate, zero errori/attenzioni. Il vecchio lavoro
resta fermo e conservato; l'archivio completo **non è ancora ripartito**.
La prova terminata non equivale all'indicizzazione delle 30.942 foto.

La prova ha anche mostrato `uncertain_progress` durante una chiamata modello
regolare: `refresh_usage_complete` include tentativi ancora vivi, il cui uso
finale non è ancora registrabile. Correzione successiva del solo proiettore ETA:
consumi esplicitamente sconosciuti o tentativi terminali senza uso completo
invalidano ancora la stima; chiamate attive non la invalidano da sole.
Nessuna modifica alla contabilizzazione o ai gate di completamento. Nuova
pubblicazione necessaria prima della partenza lunga per evitare di interromperla.

Correzione del controllo ETA verificata: 28 prove progresso e 35 con controlli
owner/API di controllo; suite LRE/API finale 564 superate, 4 stress opzionali
saltate. Commit `426f67c1`. Accounting e immutabilità dei tentativi terminali
non modificati. Release 60 in attivazione al presente checkpoint.

### Verifica di copertura, non semplice stato «completato»

`run-165sad__` e `run-cpw45n9v`: prova con sei foto, sei percorsi inventariati,
sei voci uniche nell'indice pubblicato; zero mancanti, estranee o duplicate,
metadati e ricevuta finale concordi. Nessun file modificato/rimosso tra scoperta
e audit (dimensione/mtime). Il test **non** certifica l'archivio completo.

Sonda amministrativa read-only riutilizzabile:
`/tmp/metnos-lre-contention.O6Z8zp87/audit-photo-coverage.sh`, digest
`79d58d006295c5e1faa1a7a4589fe0cfffbdc77147b80e174fadb2901c051c61`.
Modalità `test` collaudata; `full` richiede un unico lavoro completato con
base `Immagini` e fallisce in caso di assenza/ambiguità. Confronta ricevute
discovery con hash verificato, insieme dei percorsi nell'indice, conteggi
metadata/ricevuta e metadati correnti dei file. Legge al massimo 100.000 voci
e 512 MiB; non modifica indice, storico o foto. Nessun contenuto/percorso
individuale compare nell'output.

Questo certifica la copertura dell'inventario sigillato. Per certificare anche
l'archivio corrente va esclusa l'aggiunta di nuove foto dopo quella scansione:
servono nuova ricognizione degli stessi percorsi/estensioni e confronto degli
insiemi. Dimensione/mtime non sono una nuova verifica completa del contenuto.
La pubblicazione del dominio passa già `source_count` della discovery come
`expected_count`; conteggio errato e percorsi duplicati bloccano l'attivazione.

## Ripartenza completa — 16 settembre, 15:52 Europe/Rome

Release **60** attiva: build
`sha256:ac6dc0ea7cd8c7ac473dd5f841ff82183763feee8a924ffcd837d89a60ff0321`,
`CUTOVER_OK`, `RELEASE_EDITS_ADMITTED`, uscita 0. Nessun executor cambiato
rispetto alla 59; differenza operativa limitata alla proiezione ETA. Ulteriori
30/30 test sulle fasi immagine, inclusi copertura incompleta e duplicati.

`run-jpqockg1`: vecchio lavoro annullato tramite API, senza cancellazioni,
972 unità confermate ancora presenti. Il conteggio API `failed=963` aggrega
anche le unità **annullate**, non indica 963 nuovi errori di analisi.
La prima ricerca dopo l'annullamento ha trovato l'indice della prova e non ha
ammesso il lavoro completo: rilevato prima di dichiarare la ripartenza.

`run-fuuccnz5`: unico puntatore attivo dell'indice di prova (base verificata,
generazione della prova, sei voci) rinominato reversibilmente da `meta.json`
a `meta.validation-20260916.archived.json`. Foto, generazione pubblicata,
ricevute e storico restano al loro posto; soltanto il corpus di collaudo
non compare più nella ricerca globale. Nessuna modifica all'indice reale.

Ripetuta la richiesta originale `cerca localmente foto con il mare`:
turno **`e764cd5a451845c8`**, nuovo lavoro
**`wrk_9ff050ecc9b048098167041d559d6c04`**, `running`, fase `discover`
alle **15:52:21 Europe/Rome**, zero errori/attenzioni. Non è ancora completato
e non è ancora attestata la copertura delle 30.942 foto; quel numero proviene
dall'inventario precedente. La stima reale della fase di analisi dovrà essere
osservata dopo almeno tre nuovi gruppi completati, non durante la scansione.
I quattro errori transitori del catalogo restano non riprodotti e non attribuiti
a una causa certa: la nuova diagnostica resta necessaria se si ripresentano.

## Diagnosi successiva: tre cause distinte, 16 settembre pomeriggio

La precedente incertezza sul catalogo è stata risolta con una riproduzione:
un residuo di pubblicazione legittimo (cartella `generations` vuota e
`writer.lock` NUL di un byte, senza binding) veniva verificato prendendo un
blocco esclusivo. Lettori contemporanei si respingevano a vicenda. Il catalogo
conservava 123 contratti validi, ma segnalava `binding_invalid` sul solo residuo.
Prova iniziale: 42 errori su 62 letture con 31 lettori (`run-8f40rq1x`, riepilogo
`run-zs2tc_3w`). Funzione candidata con blocco condiviso: 310 letture su 310
riuscite (`run-3kpmeufa`), senza cambiare dati o codice del lavoratore vivo.
Lo scrittore resta escluso; proprietà, permessi e contenuti sono rivalidati.
233 test del catalogo/caricatore superati, inclusi lettori concorrenti e
scrittore attivo. Il nuovo testo amministrativo IT/EN non attribuisce più ogni
richiesta di verifica a contabilità incompleta.

Il nuovo lavoro `wrk_9ff050ecc9b048098167041d559d6c04` ha scoperto 30.942 foto,
967 gruppi. Cinque tentativi hanno incontrato il conflitto del catalogo, con
consumi noti pari a zero. Un sesto è fallito dopo 13 chiamate VLM: il file
successivo è HEIC. La prova su tutti i 32 file di quel gruppo ha trovato dieci
HEIC non decodificabili dal lettore precedente (`run-t6d79ofr`), non prove di
foto corrotte. Con il decoder HEIF candidato tutti i 32 file sono leggibili
(`run-av13qtqb`), senza chiamate a modelli o modifiche agli originali.

La domanda di corsie sommava CPU=2, disco=16 e VLM=1 per unità che richiedono
tutte queste risorse insieme: fino a 19 tentativi per una sola analisi visiva.
Al controllo `run-w33mu1vr` (14:34:56 UTC) restavano 7 gruppi analizzati,
1 errore permanente, 11 richieste di verifica (5 catalogo e 6 scadenze),
948 in attesa, nessun tentativo vivo. Le sei scadenze a 1800 secondi hanno
reso incerti i consumi: non si azzera o riscrive quella contabilità.
Il nuovo limite di domanda usa il collo di bottiglia per profilo e condiviso
tra lavori; il coordinatore conserva l'ammissione reale. Nessun limite o
tempo massimo viene aumentato. 58 test di supervisione/concorrenza superati.

Ulteriore protezione: ogni foto completata salva un riferimento privato
rivalidabile nella stessa generazione. Prova di interruzione a metà gruppo:
il nuovo tentativo richiama i modelli solo per le foto mancanti; nessun indice
parziale viene pubblicato. Testano anche sorgente/contesto/generazione diversi,
riferimenti alterati e file illeggibili. 68 test foto/LRE superati.
Il recupero non aggira la contabilità incerta e non importa automaticamente
vecchi frammenti senza riferimento: lo storico rimane conservato.

Dipendenza `pillow-heif==1.7.0`, wheel CPython 3.12 Linux hash
`7c4751fcffb55f555a7559cfa6721bdfb30f50f14b7f0cbd2b1cba3b5d5961b7`,
inserita nel deposito offline (`run-yyl6sqnn`). Nessun ambiente installato
modificato; la distribuzione canonica costruisce un nuovo ambiente immutabile.
La suite complessiva ha 645 test superati e 4 stress opzionali non eseguiti;
due prove browser sono state bloccate dal contenitore di sviluppo e vengono
rieseguite fuori da quel contenitore. Attivazione e prova reale ancora da
registrare: questi risultati non attestano l'indicizzazione completa del corpus.

Verifica conclusiva del candidato: **913 test superati, 4 stress opzionali
non eseguiti**; suite interfaccia con browser reale IT/EN **4/4 superate** fuori
dal contenitore limitato (nessun test disabilitato). Il controllo delle
risorse vive `run-bxde469z` trova 13 descrittori e un solo thread sul lavoratore
inattivo: nessuna evidenza di esaurimento dei descrittori. Nessun riavvio manuale
eseguito durante la diagnosi. Il difetto di capacità include una regressione
dedicata a profili indipendenti posti dopo gruppi già saturi.
