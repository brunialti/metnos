# RM-0008 — passaggio di consegne del gruppo 6

## Stato corrente — 29/8/2026

Worktree: `/tmp/metnos-rm0008-a-only`, ramo unico `main`. Non toccare
`/opt/metnos` e non creare rami.

Il gruppo 5 e' chiuso. Il commit pubblico e' `a5bd396`; il ciclo GitHub Actions
`33183713818` ha concluso verdi nove lavori su nove, con errore zero su Linux e
Windows. Il commit sorgente di ingresso del gruppo 6 e' `225f9437`.
RM-0008 resta `active`; `closed_build_enforcement()` resta `False`.

### Incremento G6-A completato e certificato pubblicamente

Sono completati tutti i sottoincrementi di G6-A: record storico autenticato,
lettura produttiva a freddo della catena, sessione sigillata del blocco di
deployment, codec di claim/disposizione/record V2, ispezione chiusa `INITIAL`
e resolver non mutante del grafo durevole. Due revisioni indipendenti finali
hanno approvato il diff con `P0=0`, `P1=0`, `P2=0`. Il commit pubblico finale
e' `3a50b71`; il ciclo GitHub Actions `33206146506` ha concluso verdi nove
lavori su nove, compreso il riepilogo bloccante, con errore zero su Linux e
Windows. G6-A e' quindi chiuso; RM-0008 resta aperto per G6-B, G6-C e G6-D.

Il codice ora:

- separa nominalmente il record storico autenticato dalla distribuzione viva;
- carica una sola fotografia sigillata delle tre autorita' fisse e non accetta
  root o registri dal chiamante produttivo;
- autentica tutti i manifesti storici, verifica file e guardie soltanto per la
  release richiesta e ricostruisce due o piu' versioni in un processo nuovo;
- deriva le radici produttive fisse e respinge link, proprietari, modi e
  hardlink non ammessi su antenati, directory e oggetti;
- mantiene il verificatore G5 dell'installazione corrente separato dal nuovo
  percorso `releases-v1`, con autorita' fissa e rilettura completa;
- crea e riprende le directory con modo esatto `0755`, sincronizzando sempre
  prima la directory e poi il parent; gli errori restano chiusi e stabili;
- lascia le seam con root e autorita' iniettabili in tipi privati di prova che
  non producono capacita' accettate dal prodotto.

Le prove mirate finali hanno dato `131 passed, 1 skipped`; la guardia
dell'inventario ha dato `1 passed`; la suite portabile completa ha dato
`410 passed, 24 skipped`, con errore zero. Tre revisioni indipendenti finali
hanno dato `P1=0`, `P2=0`. Le prove discriminano firma storica, predecessore,
sequenza, buco, duplicato, fork, testa richiesta, fotografia unica delle
autorita', tipo produttivo, metadati, umask e ripresa dopo errore di `fsync`.

La prova diretta su `192.168.1.137` non e' stata eseguita: la connessione SSH
alla porta 22 e' scaduta. Questo non lascia un vuoto di certificazione: il
percorso Windows ha superato la review dedicata e i lavori Windows della
matrice pubblica finale di G6-A.

### Sottoincremento lock di G6-A completato localmente

Il blocco di deployment restituisce ora una capability opaca e non
trasferibile. La sessione contiene soltanto un token e un sigillo: lease e
descrittore restano in registri privati, censiti dalla guardia come autorita' di
scrittura. Il consumo richiede l'identita' esatta di sessione, token, lease,
processo, radice, inode e file nominale; copia, serializzazione, clone,
sessione scaduta o costruita a mano sono respinti.

Il lock produttivo usa soltanto la radice fissa preesistente. Verifica
proprietario, modo, link, marker e antenati, ripara esclusivamente il residuo
vuoto e owner-only lasciato da una morte fra creazione e `fchmod`, e ripete
sempre la sincronizzazione del file e della directory. Dopo `fork` il figlio
invalida le lease ereditate e chiude i descrittori, percio' non puo' trattenere
il lock dopo la morte del titolare. La seam di prova resta nominalmente
separata e non produce una capability accettata dal prodotto.

Le prove definitive hanno dato `139 passed, 1 skipped` sulla regressione
mirata e `63 passed` sull'intera guardia dei confini. Due revisioni
indipendenti hanno concluso `P1=0`, `P2=0`, dopo prove causali su `fork`,
sostituzione del pathname, lease non registrata, clone popolato, sblocco
diretto, token ostile, metadati e ripresa degli `fsync`. Il diff e' pulito.

La prova SSH Windows e' stata ritentata usando direttamente l'IP
`192.168.1.137` e l'utente configurato `rober`, senza risoluzione del nome. La
porta 22 e' scaduta nuovamente: il PC puo' essere online per il trasporto
Metnos/Codex senza esporre SSH. Questo non modifica la decisione di usare la
sola matrice pubblica Windows alla chiusura del gruppo 6.

### Sottoincremento codec di G6-A completato localmente

Sono implementati tre codec separati e privi di scritture produttive:

- la prenotazione del successore con sette chiavi esatte, nome derivato dal
  predecessore, interi di tipo esatto e `claim_id` ricalcolato;
- la disposizione del journal V1 con sette chiavi esatte e hash calcolato sui
  byte originali incorniciati, senza ricodificare i record storici;
- il record V2 con le trentasette chiavi normative, dominio distinto dal V1,
  sette stati, soglie chiuse e `install_transaction_id` ricalcolato sul
  documento esatto a dieci campi.

I tipi non sono stati aggiunti a `__all__`; non esistono ancora API che
pubblicano claim, disposizione o `PREPARED`. Le prove definitive di codec,
lock e guardia hanno dato `119 passed`, con diff pulito. Due revisioni
indipendenti hanno concluso `P1=0`, `P2=0`. Il prossimo incremento deve
aggiungere soltanto letture non mutanti, inventario completo e resolver; tale
incremento e' descritto e concluso nella sezione seguente.

### Sottoincremento ispezione `INITIAL` di G6-A completato e pubblicato

La nuova ispezione non crea directory, file o riparazioni. Restituisce una
capacita' produttiva `INITIAL` soltanto sulla radice fissa, dopo due fotografie
identiche di directory vuote e assenza completa di ancora, puntatore, lock,
oggetti e temporanei correlati. Qualunque prefisso parziale o ambiguo usa
`birth_ownership_recovery_required`; una catena presente viene sempre delegata
alla lettura fredda e un suo errore non ripiega mai su `INITIAL`.

Il tipo produttivo usa un sigillo chiuso in una closure. Tipo, minter e nucleo
di ispezione sono classificati dalla guardia come autorita' privilegiate. Il
nucleo verifica direttamente tipo nominale, radice fissa e identita' della
fotografia delle autorita' e dei tre registri, per impedire che una chiamata
privata salti il costruttore produttivo. La seam portabile resta di tipo
distinto. Il lock persistente viene accettato nel solo percorso di catena
completa dopo verifica di tipo, link, dimensione, marker e, su POSIX, modo e
proprietario.

Le prove definitive danno `122 passed` su catena e guardia, mentre la guardia
reale `--birth-closed` e `git diff --check` sono verdi. I mutanti coprono
temporanei dell'ancora validi e malformati, variazione fra le due fotografie,
modo, proprietario, hardlink, marker e dimensione del lock, fabbricazione della
capacita', chiamata diretta al nucleo, accessi ostili, assenza di ripiego e
diniego Windows prima di I/O. I due riesami finali hanno concluso `P1=0` e
`P2=0`.

Il commit pubblico e' `3e6d05c`; il ciclo GitHub Actions `33203156363` ha
concluso con successo. Questo ciclo comprende anche i sottoincrementi lock e
codec precedenti, quindi il vecchio errore Windows del commit `e9fb5d6` non e'
piu' un errore aperto.

### Sottoincremento resolver non mutante di G6-A completato e pubblicato

Il resolver legge soltanto `coordinator-v1` sotto la sessione esatta del
blocco di deployment. Non crea directory, non pubblica file, non ripara e non
adotta prefissi. Verifica due inventari identici, metadati, nomi, cardinalita',
byte V1 originali, catene hash V1/V2, carry immutabili, claim, disposizione,
transazioni e predecessori. Un nuovo successore e' ammesso soltanto dopo
`PREFLIGHT_VERIFIED`; anche un claim pendente deve avere il `request_id`
ricalcolabile dai predecessori.

Il risultato G6-A e' deliberatamente una fotografia del grafo durevole, non
una certificazione operativa dello stato vivo. Il prodotto restituisce un tipo
opaco registrato per identita' e legato alla stessa sessione ancora viva; non
espone osservazione o sigillo. Costruzione diretta, token copiato, sessione
diversa, copia, serializzazione e `dataclasses.replace` non producono
autorita'. La seam di prova restituisce un tipo nominalmente distinto. La
riattestazione di catena, installazione e controllo vivo resta nel G6-D.

Le prove finali danno `54 passed` sul V2, `78 passed` sulla regressione del
coordinatore e `494 passed, 24 skipped` sulla suite locale equivalente al
workflow pubblico. La guardia reale `--birth-closed` e `git diff --check` sono
verdi. La suite estesa ha inoltre mostrato cinque sole prove UID non eseguibili
nel sandbox locale perche' `sudo` e' vietato; non sono regressioni del diff e
restano coperte dal runner Ubuntu pubblico. Due revisioni indipendenti hanno
concluso `P0=0`, `P1=0`, `P2=0` dopo mutanti su request pendenti, predecessore
non concluso, migrazione legacy A→B, ottavo record, oggetti hash ostili,
fabbricazione nominale e alias della guardia.

Il commit pubblico e' `3a50b71`; il ciclo GitHub Actions `33206146506` ha
concluso con successo tutti i nove lavori, inclusi portabilita', concorrenza,
identita' ACL reali e suite completa su Windows, le corrispondenti prove Linux
e il riepilogo finale. Non rimangono errori pubblici aperti per G6-A.

### Riesame e ottimizzazione di G6-B completati prima del codice

Due revisioni indipendenti hanno confrontato il piano G6-B col codice. Non
esiste ancora implementazione produttiva di ricevitore, catalogo unico,
programma amministrativo, assemblatore o installatore. Sono stati chiusi nel
disegno tre rischi P1 che avrebbero prodotto una falsa convergenza:

- il manifesto corrente ammette un solo `service_unit` e non conosce i ruoli
  `service_catalog` e `deployment_descriptor`;
- una capacita' preparata da sola non puo' autorizzare la pubblicazione finale,
  perche' salterebbe l'ordine claim, `PREPARED`, prerequisito e certificato;
- l'elenco autonomo in `executor_birth_maintenance_units.py` e' incompleto e
  deve essere una proiezione della sola tabella dichiarativa del catalogo.

Il piano normativo ora divide G6-B in quattro sottoincrementi: B1 corregge il
manifesto e introduce fonte unica, codec e renderer; B2 implementa la
ricezione content-addressed root-only; B3 prepara e firma la distribuzione
soltanto sotto la sessione viva e la fotografia G6-A; B4 prova il solo nucleo
filesystem di pubblicazione, senza esporlo nel grafo produttivo. Tipo, minter,
validatore e involucro produttivo dell'autorizzazione appartengono a G6-D.

Le nove aree di rischio sono conservate ma aggregate in quattro famiglie di
prove. Si riusa un solo harness di arresto per ricezione, staging, directory
amministrativa e pubblicazione; una prova causale separata pretende invece
zero I/O e fotografia invariata quando manca l'autorizzazione G6-D. Non si
ripetono firma, epoca/scopo delle chiavi, chiusura degli import, path Windows,
lettura handle-bound, catena fredda, claim, journal V2 o deployment lock gia'
certificati. `systemd` reale appartiene a G6-C; claim, `PREPARED` e recupero del
coordinatore appartengono a G6-D. Su Windows B2-B4 negano prima di sessione,
autorita' o filesystem; su Linux un unico runner root prova i killpoint reali.
Il piano corretto e' stato riletto da entrambi i revisori con esito finale
`P0=0`, `P1=0`, `P2=0`. Il primo passo di codice autorizzato e' soltanto
G6-B1.

### Sottoincremento G6-B1a del manifesto completato e certificato

Il verificatore del manifesto ammette ora i ruoli `service_catalog` e
`deployment_descriptor`, li lega ai due percorsi fissi sotto `deployment/` e
ne richiede esattamente una occorrenza. `service_unit` richiede invece una o
piu' occorrenze. Anche `schema_version` richiede ora il tipo intero esatto e
non accetta il booleano `true`.

La regressione combinata di manifesto, catena e coordinatore ha dato
`174 passed, 1 skipped`; la guardia reale `--birth-closed` e
`git diff --check` sono verdi. I mutanti coprono assenza e duplicazione dei
nuovi materiali, pluralita' delle unita', spostamento del catalogo o del
descrittore mantenendo ruolo e cardinalita', e `schema_version=true`. Due
revisioni indipendenti finali hanno concluso `P0=0`, `P1=0`, `P2=0`.

Il commit pubblico e' `5cc2b08`; il ciclo GitHub Actions `33207820031` ha
concluso con successo tutti i nove lavori su Linux e Windows, compreso il
riepilogo finale. G6-B1a ha quindi errore pubblico zero.

Questo sottoincremento non implementa ancora catalogo, renderer o proiezione
di manutenzione e non effettua scritture produttive. Il passo successivo resta
G6-B1b, fonte dichiarativa unica e codec del catalogo.

### Sottoincremento G6-B1b completato e certificato pubblicamente

La fonte dichiarativa unica descrive ora tutte le sei classi previste: servizi,
timer, arresto di quarantena, target, dipendenze esterne e ingressi
amministrativi. Da questa sola fonte vengono compilati il catalogo firmabile,
le unita' candidate e le proiezioni usate da manutenzione e cutover. Il
censimento meccanico comprende quindici unita' candidate, le quattro unita'
storiche di backup e traduzione prompt da ritirare nel gruppo 7 e tutti gli
ingressi amministrativi presenti in `install/`, `deploy/`,
`runtime/playwright_sidecar` e `scripts/`.

Il codec del catalogo applica schema e relazioni chiusi, JSON canonico, limiti,
ordinamenti, digest del catalogo e della copertura, binding degli eseguibili e
hash dei frammenti. Il renderer produce i frammenti esclusivamente dal
catalogo; un parser indipendente li rilegge e deve ricostruire la stessa
specifica. La sorgente statica contiene anche le ricette di target e direttive:
la rilettura produttiva ricompila la sorgente con radice, interprete, identita',
gruppi, home e hash autenticati e pretende uguaglianza esatta. Il caricatore
pubblico accetta soltanto il record produttivo autenticato, lo riattesta e
rilegge catalogo e unita' dai percorsi fissi gia' legati al manifesto.

L'analisi avversariale ha mostrato che gli installer storici controllano
servizi `systemd` utente e non possono diventare target amministrativi root
semplicemente impostando `HOME`. G6-B1b non anticipa quindi il cutover del
gruppo 7: gli storici restano esclusivamente `legacy_bindings` con
`retire_in_group7`. I sedici ingressi candidati puntano tutti a un solo modulo
stdlib, `runtime.executor_birth_admin_operations`, con una sola operazione
firmata, directory ribasata sulla release e ambiente vuoto. Il modulo firma il
vocabolario finale ma, fino al gruppo 7, nega prima di ogni I/O operativo con
`birth_ownership_closed_enforcement_required`; fuori Linux nega prima di I/O
con `birth_ownership_platform_unsupported`.

La prova causale verifica la mappa esatta operazione-binding, il caricamento
reale con `python -I -S` da una release ribasata, directory corrente esterna e
ambiente vuoto. Una guardia AST chiude tutto il codice eseguito durante
l'import; un solo processo auditato prova tutte le sedici operazioni e i due
argomenti invalidi rendendo fatale qualunque accesso a file, processi o rete.
La regressione Linux combinata ha dato `266 passed, 1 skipped`; guardia dei
confini, compilazione e controllo del diff sono verdi. La prova diretta su
Windows 11, Python 3.14, tramite `192.168.1.137` ha dato
`31 passed, 1 skipped`.

Due revisioni indipendenti sullo snapshot esatto hanno concluso
`P0=0`, `P1=0`, `P2=0` prima della pubblicazione.

Il primo commit pubblico di B1b e' `7fae026`; il ciclo GitHub Actions
`33215057932` ha però rilevato lo stesso errore di prova su Linux e Windows.
Il catalogo completo conserva correttamente quattro ingressi e quattro unita'
storiche da ritirare, mentre l'export pubblico esclude intenzionalmente
`deploy/`, lo script di migrazione del percorso Python e lo script storico di
rinomina. L'oracolo pretendeva erroneamente la presenza fisica anche nel
sottoinsieme pubblico.

La correzione lascia invariato il catalogo. Nell'albero completo il censimento
continua a pretendere uguaglianza totale; nell'export ammette esclusivamente
gli otto elementi nominati dalla politica pubblica e rifiuta ogni omissione o
presenza ulteriore. Il vero export generato localmente ha dato `32 passed` su
Linux e `31 passed, 1 skipped` su Windows; la regressione completa resta
`266 passed, 1 skipped`. Due revisioni indipendenti della sola correzione hanno
nuovamente concluso `P0=0`, `P1=0`, `P2=0`.

La correzione pubblica e' il commit `2ce4b42`; il ciclo GitHub Actions
`33215680165` ha concluso verdi tutti i nove lavori, compresi suite completa
Ubuntu, suite completa Windows e riepilogo bloccante. G6-B1b e' quindi
certificato con errore zero. G6-B nel suo complesso resta aperto per B2, B3 e
B4.

## Analisi del gruppo 6

Prima dell'avvio del codice, tre revisioni indipendenti hanno
esaminato distribuzione, catena, coordinatore, servizi e installatore. Hanno
concordato quattro lacune bloccanti:

1. manca l'assemblatore e l'installatore della distribuzione firmata;
2. la catena non puo' essere riletta a freddo dopo due versioni senza oggetti
   sigillati costruiti in memoria;
3. le unita' utente correnti non possono contenere un controllo preliminare
   dominante, perche' sono modificabili dall'identita' di servizio;
4. il coordinatore produttivo termina a `RECEIPTS_COMPLETE` e gli ultimi stati
   non attestano ancora release, testa richiesta e controllo definitivo.

Il piano ottimizzato e' in
`internal/reports/rm0008-gruppo6-piano-ottimizzato.md`. Divide il gruppo in
quattro incrementi verticali e usa una sola matrice pubblica finale:

- G6-A: record storico, catena fredda e journal per transazione;
- G6-B: assemblaggio firmato e installazione della release;
- G6-C: controllo dominante e installatore unico;
- G6-D: composizione completa del coordinatore e recupero.

Il primo giro avversariale del piano ha dato `NON APPROVATO` prima di qualunque
modifica al codice. Ha rilevato cinque P1: schemi non abbastanza chiusi,
confusione fra ancora iniziale e aggiornamenti, ordine errato fra fotografia e
manutenzione, assenza di una prenotazione durevole del successore e possibile
ritiro anticipato degli ingressi del gruppo 7. Il piano e' stato corretto
aggiungendo formati, domini, limiti, produttori, caricatori, prenotazione
no-replace, protocolli distinti per primo passaggio e aggiornamenti e contratti
per file e simbolo. E' stato inoltre allineato nella roadmap l'ordine dei
blocchi gia' approvato nel gruppo 5.

Il secondo giro mirato ha ancora dato `NON APPROVATO` e ha impedito l'avvio del
codice. Ha individuato claim pubblicata prima della build preparata, sorgente
singola non aggiornabile, incompatibilita' fra journal V1 e nuovi campi,
catalogo incapace di descrivere gli ingressi amministrativi, schemi non
completamente chiusi, isolamento `systemd` insufficiente, assenza di un blocco
contro nuovi avvii e collisione potenziale delle unita' reali. Il piano e'
stato nuovamente corretto: sorgenti content-addressed, claim dopo tutte le
verifiche reversibili, journal V2 con disposizione esplicita del V1, classe
`gated_entrypoint`, matrici e framing esatti, blocco condiviso/esclusivo degli
avvii e cella `systemd` firmata con tutti i riferimenti ribasati prima della
firma. Il gruppo 6 non installa piu' le unita' nei nomi reali; questo resta al
gruppo 7.

Il terzo controllo indipendente ha ancora bloccato il codice. I rilievi
principali erano: uso errato di `ExecCondition`, topologia effettiva non legata
a `PREFLIGHT_VERIFIED`, launcher invocabile dall'utente applicativo, lock degli
installer troppo corto, import Python non eseguibili sotto `-I -S`, sorgente
non selezionata dall'entrata del coordinatore e possibilita' di attraversare il
punto di non ritorno prima del gruppo 7. Il piano corrente li corregge con
`ExecStartPre` firmato, launcher root-only che poi riduce UID/GID, supervisione
completa degli installer, bootstrap `sys.path` firmato, `source_id` esplicito,
verifica della configurazione `systemd` effettiva e capacita' sigillata
producibile soltanto dal gruppo 7. La prova root e' ammessa soltanto in una VM
GitHub usa-e-getta. Anche framing, `unit_spec`, transazioni di directory e
matrice differenziale del verificatore autonomo sono ora espliciti.

Il quarto controllo ha dato ancora `NON APPROVATO`, quindi il codice resta
intenzionalmente fermo. I rilievi nuovi erano concreti: prefisso systemd `+`
che annullava la sandbox, confusione fra target applicativo e launcher stabile,
contratto Python incompleto, confronto falso-rosso delle dipendenze implicite,
TCB OpenSSL non raccoglibile in modo deterministico, recovery della cella non
ricostruibile, copertura incompleta delle direttive correnti e rischio di
prenotare sulle radici reali una build G6 col diniego ancora falso. Il piano e'
stato corretto con prefisso `!` e drop completo dei privilegi, unione chiusa
dei target, proiezione systemd esplicita/implicita, raccolta ELF e directory
provider deterministica, descrittore durevole della cella, inventario
meccanico delle direttive e soprattutto un diniego prima di claim/`PREPARED`
finche' `closed_build_enforcement()` non vale `True`. Le prove delle directory
ora coprono anche zero byte, scrittura parziale e albero parziale con un solo
harness parametrico.

Il quinto controllo ha trovato quattro residui implementativi prima del freeze:
crash del supervisore con discendenti ancora vivi, baseline delle dipendenze
systemd dipendente dall'host, descrittore della cella non ancora normativo e
mancanza della prova esplicita di zero mutazioni con enforcement falso. Il
piano ora usa un registro cgroup durevole in `/run` riconciliato sotto gate
esclusivo, fotografa e lega le dipendenze aggiunte dal gestore con le rispettive
origini root-owned, definisce percorso/schema/framing del descrittore isolato e
pretende un confronto completo prima/dopo dell'entrata pubblica G6. Sono stati
anche chiusi la risoluzione usr-merged della TCB OpenSSL, l'inventario `Nice` e
i domini distinti degli hash del target, del manifesto e degli eseguibili
amministrativi. Il nuovo snapshot attende ancora il verdetto finale; nessun
codice produttivo e' stato modificato.

Il controllo di univocita' successivo ha richiesto un ultimo affinamento P2:
ordine crash-safe unico record→cgroup→fork→barriera, schema chiuso della TCB,
dominio degli hash delle origini systemd e insieme completo delle relazioni
effettive. Il piano ora lega anche mount e parent cgroup v2, enumera le radici e
il predicato nominale della cella, deriva correttamente il percorso manifesto
relativo dal target assoluto e include nel medesimo harness i killpoint fra
claim, disposizione e `PREPARED`. Queste modifiche attendono il nuovo verdetto
congelato; il codice resta fermo.

Il nuovo snapshot e' stato approvato da tutti e tre i revisori indipendenti.
Hash approvati: piano
`2a2a92110a33261863f04ad760e43836542681f129aa389c53ab64b8078768c6` e
handover precedente
`3c6a794f3d5e4ce4e8c41a3a374404d7997f066c56b2046a4a16b24cde45cb70`.
Verdetto consolidato: `P1=0`, `P2=0`. L'aggiunta di questo paragrafo registra
soltanto il verdetto e non modifica il piano approvato. G6-A puo' iniziare; gli
incrementi successivi restano chiusi fino al criterio di uscita di G6-A.

### Sottoincremento G6-B2 completato e certificato pubblicamente

Il codec portabile `received-source-v1` e il ricevitore Linux root-only sono
implementati, ma G6-B2 non e' ancora dichiarato chiuso. Il codec applica JSON
canonico, schema chiuso, modi ammessi e calcolo content-addressed in streaming.
I limiti normativi sono ora condivisi dal codec e dal ricevitore: descrittore
da 16 MiB, 20.000 file, 20.000 directory, profondita' massima di 32 componenti
e 2 GiB complessivi. Costruzione e decodifica provano sia il massimo ammesso
sia il primo valore rifiutato. Il ricevitore usa la radice produttiva unica
definita dalle autorita' G6-A, acquisisce il blocco di deployment esistente e
non accetta una radice o un blocco scelti dal chiamante produttivo.

La transazione verifica due fotografie della sorgente, rifiuta link, hardlink,
oggetti speciali, sostituzioni e sovrapposizioni con la radice amministrativa,
quindi pubblica con due rename senza sovrascrittura e rilettura completa. I
controlli dell'account comprendono linger, gestore utente, tutti i percorsi
home e runtime caricabili da systemd e otto radici globali. Ogni directory o
unita' gia' esistente e scrivibile dall'account viene rifiutata; ACL non
valutabili in modo autonomo sono rifiutate. Un prefisso vuoto, owner-only e
`0700`, lasciato da un arresto fra `mkdir` e `fchmod`, viene completato in modo
handle-bound; stati diversi restano chiusi.

Scansione, apertura dei file, verifica e pulizia sono iterative e chiudono i
descrittori anche sui rami TOCTOU di errore. L'uso dei descrittori dipende dalla
profondita' limitata e non dal numero di directory sorelle; una prova con
ottanta directory sotto `RLIMIT_NOFILE=64` e la pulizia alla profondita'
massima sono verdi. Una pulizia interrotta dopo il primo file puo' essere
ripetuta in sicurezza. Tutti i diciannove scope che esercitano o propagano
scritture sono classificati `store_write` dalla guardia e dall'inventario.

Un solo harness multiprocesso attraversa l'entrata produttiva root e usa lo
stesso albero non banale con file da 2 MiB, fratelli e sottodirectory. Il figlio
viene realmente terminato con `SIGKILL` in otto punti: prima della prima
scrittura, a meta' file, dopo un file, dopo il `fsync` della sottodirectory,
prima del rename, subito dopo il rename, prima e dopo il `fsync` del parent. I
cinque residui ancora privati danno soltanto
`birth_ownership_recovery_required`; i tre stati strutturati convergono al
retry e vengono riletti integralmente. La cella root isolata e' verde anche
nel percorso normale e non modifica il server reale.

La regressione mirata corrente ha dato `211 passed, 5 skipped`; la suite
portabile completa ha dato `588 passed, 25 skipped`. Le guardie normale e
`--birth-closed`, la cella Linux root isolata e `git diff --check` sono verdi.
I due riesami avversariali indipendenti finali hanno approvato lo snapshot con
`P0=0`, `P1=0`, `P2=0`; il riesame esterno ha ripetuto `130 passed, 2 skipped`
e ha verificato in modo causale il punto di arresto dopo il vero `fsync` della
sottodirectory.

L'esportazione pubblica finale contiene 1.581 file e ha superato il controllo
anti-PII e anti-segreti con zero rilievi. Sullo stesso export la selezione
portabile B2 ha dato `130 passed, 2 skipped` su Linux. La prova diretta sul PC
Windows raggiunto tramite `192.168.1.137`, con Python 3.14, ha dato
`103 passed, 29 skipped`: gli skip sono esclusivamente prove che richiedono
Linux o privilegi root.

Il primo commit pubblico B2 e' `77f2665`. Il ciclo GitHub
`33222690771` non certifica B2: la suite portabile completa e' verde sia su
Windows sia su Linux (`588 passed, 25 skipped` su Linux), ma la modifica del
workflow storico ha causato due fallimenti collegati. Le sei celle 2A hanno
rifiutato correttamente la modifica della loro base congelata; la nuova cella
root B2 e' stata inoltre avviata sul filesystem reale del runner invece che
nella radice usa-e-getta richiesta dal suo contratto. Non e' stata modificata
l'implementazione per inseguire questi risultati. La correzione unica
ripristina il workflow congelato al digest normativo
`3e953be12480be9a4e6dfa19812a053492b5e26e155c9ecb7b749c29bde135e9`.
Il test root B2 resta nella suite e verra' collegato al runner Linux isolato
comune a B2-B4 previsto dal piano, senza duplicare la matrice.

La correzione privata e' `23a400ee` e il commit pubblico e' `19c6e63`. Il ciclo
GitHub `33223120603` ha concluso con successo tutti i nove lavori: suite
complete Linux e Windows, sei celle 2A e riepilogo finale bloccante. Gli avvisi
sulla futura dismissione di Node.js 20 nelle azioni di upload e download non
sono errori e non cambiano il risultato. G6-B2 e' quindi chiuso con errore
pubblico zero; RM-0008 resta `candidate-not-certified` per B3, B4, G6-C e G6-D.

### Sottoincremento G6-B3 in corso

Il perimetro B3 e' stato nuovamente delimitato prima del codice. B3 puo'
preparare una distribuzione e restituire una capacita' opaca, ma non puo'
pubblicare la release finale, installare unita', creare claim o produrre il
record `PREPARED`. La nuova autorita' privata materializza soltanto la chiave
`distribution` dalla radice fissa; non carica e non espone le chiavi `cutover`
o `head`. E' nominale, non copiabile e non serializzabile. Le sue 50 prove
iniziali, la compilazione e la guardia chiusa sono verdi. Il riesame separato
ha chiesto di normalizzare anche una capability emessa ma alterata e di
congelare i limiti del payload di firma. Le due correzioni sono provate con
token sostituito o cancellato e payload vuoto, di tipo errato, al massimo
esatto e al massimo piu' uno. Il verdetto finale dell'autorita' e'
`APPROVATO`, con `P0=0`, `P1=0`, `P2=0`.

I codec portabili dei descrittori di installazione, predecessore e prerequisito
di avvio sono implementati con schema chiuso, JSON canonico, domini separati e
controlli dei legami fra campi. Il primo riesame ha trovato e fatto correggere
tre aperture: programma amministrativo arbitrario, nome di unita' fuori
grammatica e una falsa dichiarazione di copertura nel nome di una prova. Il
codec ora ammette l'unico `deployment/admin/preflight.py`, richiede almeno
un'unita' valida e prova JSON non canonico e chiavi duplicate su tutti e tre i
formati. Il riesame finale e' `APPROVATO`, con `P0=0`, `P1=0`, `P2=0`, e ha
rieseguito `142 passed`.

La regressione Windows allargata a codec, sorgente ricevuta e autorita' ha
inizialmente individuato due prove che tentavano di sostituire `os.geteuid`
senza predisporre l'attributo assente su Windows. La sola seam di prova e' stata
corretta; la ripetizione finale, inclusi i riproduttori dei due riesami, ha dato
`76 passed, 34 skipped`. La regressione Linux combinata di manifesto,
catalogo, tre codec, sorgente ricevuta e autorita' ha dato
`184 passed, 1 skipped`; guardia chiusa e `git diff --check` sono verdi. Questo
autorizza il commit privato incrementale della base B3, non la chiusura di B3
ne' un commit pubblico: il nucleo preparatore e la capacita' preparata mancano
ancora.

Dopo il commit privato `0e68ede5` sono state svolte due analisi read-only
indipendenti sul residuo B3. Entrambe confermano l'ordine obbligato: prima
completare i byte definitivi del programma amministrativo autonomo
`runtime/executor_birth_admin_preflight.py`, poi costruire staging, manifesto e
capability. Il programma deve essere gia' completo in B3, perche' cambiarlo in
G6-C cambierebbe firma e `administrative_bundle_hash`; G6-C installera' e
provera' operativamente gli stessi byte. In B3, con prove G6-C assenti, i tre
comandi chiusi devono negare come prova mancante e non mutare nulla.

Soltanto dopo quel file il nucleo preparatore potra' validare la sessione e la
fotografia opaca della stessa sessione, incrociare grafo e catena fredda,
derivare sequenza e predecessori, rileggere la sorgente fissa, compilare
catalogo, unita', descrittore, inventario e manifesto, firmare una sola volta e
restituire una capability opaca legata allo staging. B3 non produce ancora
descrittore del predecessore o prerequisito di avvio e non pubblica release,
claim, disposizione o `PREPARED`. La pubblicazione resta B4; installazione e
verifica systemd restano G6-C; composizione e journal restano G6-D.

Il primo tentativo di iniziare il programma amministrativo si e' fermato prima
di scrivere codice: il piano non fissava ancora target esatti dei link,
proiezione completa direttiva-proprieta', normalizzazione dell'output manager e
classificazione chiusa delle origini aggiunte da systemd. Due analisi
indipendenti e osservazioni read-only sul manager Ubuntu 24.04 locale hanno ora
prodotto il delta normativo §§3.5.4.1-3.5.4.4. Sono fissati gli undici link,
la mappa completa, il controllo dei flag `Exec*Ex`, le grammatiche di durata e
timer, i limiti degli archi e le sole unita' virtuali `-.slice|system.slice`;
`-.mount` e' generata e `init.scope` e' transient.

Le due revisioni avversariali del delta sono concluse. Hanno fatto correggere
il comando esatto e la cardinalita' di `systemctl show`, il tipo V1 di
`Documentation`, l'insieme chiuso dei valori `infinity`, la semantica di
`SourcePath` e `UnitFileState` e il legame crittografico dei byte sorgente dei
generatori. Un rilievo sulla ripetizione di `TimersMonotonic` e' stato ritirato
dopo l'evidenza reale di due righe omonime su systemd 255.4. I verdetti finali
sono entrambi `APPROVATO`, con `P0=0`, `P1=0`, `P2=0`. Il blocco normativo e'
quindi rimosso e l'implementazione B3 puo' iniziare dai byte definitivi del
programma amministrativo.

Il primo incremento del programma amministrativo e' ora presente ma non
ancora committato: contiene soltanto i contratti puri per CLI, JSON canonico,
identificativi e percorsi, argv/output `systemctl`, versione manager, parole e
durate systemd, strutture `Exec*` e timer. Non collega ancora `main`, non legge
le radici reali e non avvia processi. Prima delle prove sono stati corretti
sette errori causali della bozza, fra cui flag vuoti `Exec*Ex`, forma reale di
`TimersMonotonic`, valori `Install` gia' risolti, limiti UTF-8 e uso improprio
del filesystem in un parser puro. Le due revisioni avversariali hanno poi
fatto correggere processi `Exec` gia' terminati, campi dinamici iniettati,
interi e profondita' JSON ostili, il massimo positivo di 20.000 file, percorsi
non canonici, unita' residue `.socket`/`.mount` e apertura della lista di
proprieta' ripetibili. La famiglia finale ha dato `23 passed` sia su Linux sia
sul PC Windows 192.168.1.137 con Python 3.14. Entrambi i verdetti finali sono
`APPROVATO`, con `P0=0`, `P1=0`, `P2=0`; questo primo incremento puro e'
quindi committabile, ma non rende ancora eseguibili i tre comandi. Il commit
privato e' `a2458f7c`; l'export incrementale sul solo `main` pubblico e'
`2fad2c5`. Il ciclo GitHub `33227587849` e' concluso con successo: tutti i
nove lavori, incluse le suite complete Ubuntu e Windows e il riepilogo
bloccante, sono verdi.

Il PC Windows riavviato e' nuovamente raggiungibile in SSH diretto a
`192.168.1.137`: risponde come `ROBERTO_PC_HP` con Python 3.14.0. Non e' stato
ancora usato per nuovo codice B3, perche' il programma amministrativo resta
da implementare e poi sottoporre alle sue prove portabili e Windows.

### G6-B3, secondo incremento sospeso per analisi causale

Il secondo incremento candidato del programma amministrativo ha aggiunto le
radici fisse, il lettore bounded/no-follow, il registro pubblico di
distribuzione, il codec del manifesto, la verifica Ed25519 tramite OpenSSL e
la rilettura dei file, dell'inventario boundary e della chiusura locale degli
import. Prima della revisione ha dato `27 passed` su Linux e compilazione
`python -I -S` verde. Questo risultato non certifica l'incremento.

Una prova reale ha mostrato che OpenSSL 3.0.13, con l'argv normativo e
`-config /dev/null`, restituisce `0`, stdout
`Signature Verified Successfully\n` e stderr
`Using configuration from /dev/null\n`. Il piano e' stato aggiornato per
accettare esclusivamente questi byte nel profilo `ed25519-pkeyutl-v1`; quattro
mutanti di uscita o codice sono negati. Questo delta resta da includere nella
nuova review complessiva.

Le due revisioni avversariali hanno bloccato l'incremento. Il rilievo comune e'
che il verificatore censiva soltanto alcuni nomi `executor_birth*.py`: un file
non dichiarato come `runtime/hidden.py` restava accettato e una forma alias di
import dinamico poteva caricarlo. Lo stesso difetto e' presente nel loader
esistente, quindi la correzione deve essere unica e coerente in entrambi i
verificatori. Sono inoltre aperti: policy boundary amministrativa piu' debole
di quella compilata, record autenticato fabbricabile o mutabile in profondita',
percorso fisso `deployment/admin/preflight.py` non ancora vincolato, limite
20.000 non applicato dal codec del manifesto, prove POSIX non separate su
Windows e cleanup/output OpenSSL da rendere completamente limitati. I verdetti
correnti sono rispettivamente `P0=1, P1=2, P2=2` e
`P0=0, P1=5, P2=1`: non esiste approvazione.

Su richiesta dell'utente lo sviluppo e' stato fermato prima di altre
correzioni. Le tre analisi read-only sono ora concluse. Hanno concordato:
trie chiuso dei percorsi firmati; fotografie handle-bound A e B con byte letti
fra le due; diniego immediato degli extra senza attraversarli; adapter POSIX
nel programma autonomo e riuso dell'oracolo nativo certificato nel loader
Windows; record composto soltanto da scalari e tuple con riverifica corrente
della firma; output OpenSSL limitato e cleanup che tenta tutte le risorse;
test POSIX separati dai codec e dal diniego Windows.

Il piano chiarisce inoltre che 20.000 file e 2 GiB sono limiti della
distribuzione finale: una sorgente valida al proprio massimo puo' essere
negata dal preparatore se gli artefatti generati fanno superare il limite.
`deployment/admin/preflight.py` e' ora il solo entrypoint firmato. La parita'
boundary viene divisa in un snapshot successivo ma resta obbligatoria prima
della chiusura B3: il primo snapshot non viene collegato a `main` e non produce
autorita'.

La bozza pathname-based introdotta dal root nel loader e' stata rimossa
integralmente dopo che l'analisi ha mostrato 23 regressioni e TOCTOU residuo;
`runtime/executor_birth_distribution_manifest.py` e' tornato byte-identico
all'ultimo commit. Il worktree conserva soltanto il candidato iniziale e
l'inizio interrotto delle costanti boundary in
`runtime/executor_birth_admin_preflight.py`, i suoi test e i due documenti.
Questi byte non sono approvati e non devono essere committati prima del nuovo
incremento isolato. Nessun commit privato o pubblico contiene il secondo
incremento.

### Aggiornamento operativo prioritario — 29/8/2026

Questa sezione prevale sulla descrizione storica del secondo incremento
sospeso. Serve come passaggio di consegne immediatamente eseguibile se la
sessione corrente termina. Il repository di lavoro resta
`/tmp/metnos-rm0008-a-only`, sul solo ramo `main`. Non usare `/opt/metnos` per
modificare RM-0008 e non creare rami. L'ultimo commit di codice e' `a87f6515`,
il secondo incremento meccanico B3, committato col footer
`RM-0008-Status: candidate-not-certified`. Il commit precedente `8f25f402`
corregge il binding del daemon Telegram al runtime Metnos gestito; la stessa
correzione e' gia' sul `main` pubblico come `16f21a9`, con suite portabile
pubblica verde (`645 passed, 25 skipped`). Il nuovo incremento B3 e' pubblicato
sul solo `main` come `e55252b`. Il ciclo GitHub `33244190518` ha concluso verdi
tutti i nove lavori, compreso il riepilogo bloccante.

Il commit `a87f6515` contiene otto file:

- `internal/design/handover_rm0008_gruppo6.md`;
- `internal/reports/rm0008-gruppo6-piano-ottimizzato.md`;
- `runtime/executor_birth_admin_preflight.py`;
- `runtime/executor_birth_distribution_manifest.py`;
- `tests/portable/test_executor_birth_admin_preflight.py`;
- `tests/portable/test_executor_birth_distribution_manifest.py`;
- `tests/portable/test_executor_birth_ownership_chain.py`;
- `tests/portable/test_executor_birth_ownership_coordinator.py`.

Dopo lo stop richiesto dall'utente sono state svolte tre analisi indipendenti
prima di riprendere il codice: albero esatto handle-bound su Linux e Windows;
autorita' del record, OpenSSL e separazione delle prove Windows; confini,
percorsi e limiti normativi. La correzione pathname-based che aveva prodotto
23 regressioni e lasciato TOCTOU e' stata eliminata completamente. Le due
implementazioni correnti derivano invece dal disegno consolidato.

Nel loader esistente `runtime/executor_birth_distribution_manifest.py` sono
ora presenti trie esatto del manifesto, fotografia handle-bound A, rilettura
dei byte e dei significati, fotografia B e costruzione di
`VerifiedDistribution` soltanto dopo l'uguaglianza A/B. Gli extra vengono
negati al parent senza attraversarli; sono negati directory vuote, link,
hardlink, oggetti speciali e bytecode anche se dichiarato. L'adapter POSIX usa
descrittori relativi no-follow; quello Windows riusa le primitive native gia'
certificate di `executor_birth_secure_fs`. Il manifesto applica inoltre i
limiti finali di 20.000 file e 2 GiB e richiede l'unico entrypoint
`deployment/admin/preflight.py`. Ogni oggetto e' ora vincolato anche allo
stesso device POSIX o volume Windows della radice. Le aperture POSIX dei file
attesi includono `O_NONBLOCK`, cosi' la sostituzione concorrente con una FIFO
non puo' bloccare il verificatore.

Nel programma autonomo `runtime/executor_birth_admin_preflight.py` sono ora
presenti record composti soltanto da scalari e tuple, binding dell'artefatto e
riverifica della firma con la trust root produttiva prima di leggere l'albero.
La verifica POSIX usa trie esatto e sequenza fotografia A, byte e semantica,
fotografia B. OpenSSL usa processo senza shell, ambiente chiuso, output e
errore limitati separatamente a 4 KiB, timeout, kill e wait; il solo profilo
ammesso e' quello esatto di OpenSSL 3.0.13 documentato nel piano. La pulizia
tenta chiave, payload, firma e directory anche dopo un errore. Un residuo
`.verify-*` viene classificato come stato di recupero prima di un nuovo
tentativo. Kill, attesa e chiusura delle pipe hanno limiti temporali propri;
un errore di teardown o pulizia non maschera l'errore causale attivo, ma resta
fatale se e' l'unico errore. Le prove POSIX sono marcate Linux-only; Windows
conserva codec portabili e diniego prima di I/O.

La prova del programma amministrativo non dipende piu' dal file privato
`internal/reports/rm0007-m4-boundary-inventory.json`, escluso dall'export
pubblico. La fixture costruisce un inventario indipendente usando le costanti
pubbliche compilate di `contract_boundary_guard`; questo evita un fallimento
certo della matrice GitHub sull'export.

Le correzioni note sono ora finalizzate localmente. Le prove discriminanti
coprono device/volume diversi dalla radice, sostituzione FIFO e presenza di
`O_NONBLOCK`, limite OpenSSL esatto di 4096 byte, superamento separato su
stdout e stderr, timeout con processo reap, teardown limitato e pulizia
fallita con conservazione della causa e successivo diniego del residuo. Le
evidenze aggiornate sono:

- loader: `51 passed, 1 skipped`;
- programma amministrativo: `40 passed`;
- regressione combinata unica di programma amministrativo, manifesto,
  catalogo, catena e coordinatore: `204 passed, 1 skipped` in 7,29 secondi;
- compilazione dei moduli e `git diff --check`: verdi;
- export pubblico: 1.585 file, cancello duro con zero PII, zero segreti e zero
  file sensibili; la stessa regressione nell'export ha dato
  `204 passed, 1 skipped`.

La prova diretta dell'export su `rober@192.168.1.137`, host
`ROBERTO_PC_HP`, Python 3.14.0, ha inizialmente scoperto un solo difetto nella
prova Windows: il wrapper di `_win_open_relative_v1` chiamava `directory` sia
il primo parametro sia il parametro keyword nativo. Il conflitto fermava il
test prima dell'asserzione e non coinvolgeva il codice prodotto. Il parametro
del wrapper e' stato rinominato `parent_handle`; la prova discriminante ha
dato `1 passed` e la regressione Windows completa dell'export ha dato
`155 passed, 48 skipped` in 12,82 secondi. Gli skip sono le prove esplicitamente
Linux-only.

Le due revisioni read-only finali sono concluse. Il riesame del loader ha
confermato chiusi i rilievi same-device/volume e `O_NONBLOCK`. Un rilievo
iniziale sulla DACL Windows e' stato ritirato dopo l'analisi di raggiungibilita':
`verify_installed_distribution_record_v1` nega subito non-Linux, mentre
`verify_current_installation_distribution_v1` viene fermato dal cold loader
Linux-only delle autorita' prima dell'I/O della distribuzione; il percorso
Windows osservato resta soltanto una seam di prova nominalmente distinta. Il
riesame OpenSSL ha richiesto due prove mancanti, ora aggiunte: cleanup fallito
come unico errore e timeout di teardown con chiusura di entrambe le pipe. Il
verdetto finale di entrambi i domini e' `P0=0`, `P1=0`, `P2=0`.

Non rimane un difetto noto in questo snapshot. Le condizioni locali, il commit
privato `a87f6515`, il commit pubblico `e55252b` e il ciclo GitHub
`33244190518` sono conclusi con errore zero. Il precedente rosso del commit
Telegram `16f21a9` e' superato: nel nuovo export l'inventario pubblico e' stato
rigenerato e tutti i lavori owned 2A, Linux e Windows sono verdi.

Anche se questo snapshot raggiunge errore zero, G6-B3 non e' chiuso. E' il
primo snapshot meccanico del programma autonomo: non e' collegato al `main()`
del programma e non produce capability. Prima della chiusura B3 restano il
clone statico stdlib indipendente di `discover()` e `birth_closed_findings()`,
poi il nucleo
preparatore e la capability opaca. Questi passi vanno aggregati nel minimo
numero di famiglie discriminanti, senza ripetere le prove gia' certificate.
B4, G6-C e G6-D restano fuori perimetro.

### Snapshot B3 boundary autonomo convergente, riesame finale pendente

Il programma amministrativo contiene ora un clone autonomo standard-library
della scoperta statica e di `birth_closed_findings()`. Il clone analizza
soltanto i byte Python gia' autenticati e raccolti fra le fotografie A e B;
non importa, non esegue e non legge tramite pathname
`contract_boundary_guard.py` dalla distribuzione. La verifica semantica valida
prima lo schema e la policy dell'inventario, poi pretende zero finding dalla
scansione indipendente prima della fotografia B.

Il confronto read-only sull'intero albero corrente analizza 671 sorgenti e
produce 338 facts sia con l'oracolo certificato sia col clone, con uguaglianza
completa di percorso, scope, linea, capability, chiamate e indicatori
dinamici. Sul vero inventario entrambi producono zero finding. La prova
portabile differenziale usa un albero di release compatto e confronta le tuple
canoniche complete per alias/import dinamico, nuovo scope con capability e
alterazione di uno scope di eccezione. Una prova separata verifica il wiring
nel percorso semantico senza indebolire il codice produttivo.

I riesami read-only precedenti non hanno approvato gli snapshot intermedi.
Hanno trovato cause reali: import dinamici indiretti; alias e rebinding che
cancellavano autorita'; lambda e binding Python non visitati; reflection
tramite `builtins`, `globals()` e `sys.modules`; sorgenti Python fuori dalle
quattro radici o con casing/suffisso ambiguo; directory intermedia con nome
`*.py`; AST privo di budget e fixed point quadratico. La prova di wiring
verificava inoltre la sola presenza di un file e il processo `-I -S` caricava
il programma senza eseguire il censimento. Nessuno di questi esiti e' stato
committato.

La correzione locale applica ora la stessa semantica al guard canonico e al
clone. Ogni chiamata a `__import__` o `importlib.import_module` e' negata,
indipendentemente dal target. Sono negati anche alias di prima classe, moduli
boundary relativi o discendenti e accessi riflessivi a `importlib`, `builtins`
o `sys.modules` che possono raggiungere il confine. L'autorita' gia' osservata
non scompare dopo un rebinding e il censimento include lambda, import relativi
composti e binding puntati. Il fixed point e le prove fragili su record sono
stati rimossi.

Il prodotto distribuito contiene ora zero chiamate a `__import__` e
`importlib.import_module`. I registri finiti sono diventati import letterali
nei loader, nell'installer, nei resolver, nelle lenti, nei provider e negli
script; i due hook di test accettano soltanto `__fake_llm__` e
`skill_test_fakes`. Anche i tre accessi variabili a `sys.modules` sono stati
eliminati. `runtime/admitted_module_v1.py` resta l'unica porta autenticata gia'
approvata e provata nel gruppo 3: esegue i byte gia' riletti e autenticati e
non e' una scorciatoia di importazione dinamica.

Entrambi i parser del manifesto applicano prima della cattura una grammatica
chiusa: il file Python deve terminare esattamente `.py`, iniziare esattamente
con `runtime`, `install`, `scripts` o `executors`, salvo il solo
`deployment/admin/preflight.py`, e nessun componente intermedio puo' terminare
`.py` con qualunque casing. Anche l'entrata amministrativa e' inclusa nei
budget Python e nel controllo AST, pur non appartenendo alle quattro radici
del censimento boundary. I limiti compilati, calibrati sul massimo reale, sono
2.048 sorgenti, 1 MiB per sorgente e 32 MiB totali. Prima di ogni visitor
ricorsivo il parser AST conta iterativamente e nega oltre 100.000 nodi, 64
livelli, 512 scope o 8.192 chiamate per file; il totale e' limitato a quattro
milioni di nodi. `RecursionError`, `ValueError`, `OverflowError` e
`MemoryError` sono normalizzati nel diniego stabile.

Le prove deterministiche sono state separate e lanciate in parallelo. Le tre
famiglie centrali danno `67 passed`, `52 passed, 1 skipped` e `66 passed`.
Quattro gruppi di regressione sui moduli rifattorizzati danno rispettivamente
`60`, `51`, `31` e `71` prove passate. Le regressioni supplementari di URL,
tutor/notifiche, installer e contratti danno `17`, `150`, `13` e `29` prove
passate. Totale discriminante dello snapshot: `607 passed`, un salto previsto
e zero errori. Sono esclusi esplicitamente un caso che richiede una socket
locale vietata dalla sandbox e quattro casi che richiedono il modello BGE non
presente nel worktree; non sono contati come verdi. Nessun test usa un LLM
reale. Il confronto integrale produce 338 facts canonici e 338 autonomi
identici, con zero finding da entrambi. Compilazione e `git diff --check` sono
verdi. Il wiring pretende l'insieme esatto dei byte catturati e propaga lo
stesso diniego prima di versione e chiusura import; `-I -S` esegue davvero un
mutante boundary.

Lo snapshot resta senza commit e G6-B3 resta aperto fino al nuovo verdetto
read-only `P0=0`, `P1=0`, `P2=0`. Dopo quel verdetto si procede direttamente
al nucleo preparatore e alla capability opaca, senza una matrice completa
intermedia. Nessuna pubblicazione, claim, disposizione, `PREPARED`,
installazione o capacita' autorizzante e' stata aggiunta.

### Snapshot B3 corretto dopo il secondo riesame avversariale

Il secondo riesame non aveva approvato lo snapshot precedente: ha individuato
cinque famiglie P1 e due rilievi P2. Le cause erano la conservazione
dell'autorita' dopo un rebinding, l'acquisizione riflessiva o tramite
contenitori di namespace e callable sensibili, porte alternative di caricamento
Python, il mancato conteggio AST combinato della chiusura degli import e la
normalizzazione incompleta di `MemoryError`. Le correzioni sono state applicate
soltanto dopo aver riprodotto e classificato ogni causa.

Il guard canonico e il clone autonomo ora propagano gli alias di `getattr`,
`builtins`, `importlib` e `sys.modules`; negano moduli boundary usati come
valori di prima classe, lookup variabili e i loader di codice della libreria
standard. Il riconoscimento usa il binding importato reale: una variabile
locale chiamata `sign` e una funzione locale chiamata `run_module` non sono
piu' confuse con il modulo di firma o con `runpy.run_module`. Rimane ammesso il
solo `getattr` con nome letterale di una API boundary gia' classificata.

Il solo caricatore alternativo reale residuo era
`runtime/reverse_patterns_patch.py`. E' stato eliminato: al momento del
dispatch il runtime apre il catalogo vivo attraverso `load_catalog`, risolve
il record verificato per nome e passa quel record a
`load_admitted_module_v1`. Senza catalogo, record o ammissione il reverse
fallisce chiuso; non cerca piu' file negli alberi
`executors`, `skills` o `_imports` e non esegue byte scelti per pathname. Il
percorso precedentemente concordato di `undo_last_turn` attraverso la stessa
porta autenticata resta intatto e il file dell'executor e' rimasto byte-identico
al digest firmato. Il nuovo scope runtime e' censito esplicitamente come
`live_reader` nell'inventario M4.

Le prove adversariali aggiunte coprono alias di `getattr`,
`__getattribute__('__import__')`, alias di `sys.modules` e del suo `get`,
`spec_from_file_location`/`exec_module`, moduli boundary dentro tuple, liste,
espressioni condizionali e funzioni identita'. Due prove negative proteggono i
nomi locali `sign` e `run_module`; una prova separata conserva la chiamata
diretta alla API boundary nota. Sono inoltre provati il budget AST totale sia
nel censimento sia nella chiusura degli import e i `MemoryError` successivi al
parsing della versione e durante `ast.walk`.

Evidenza stabile corrente, ancora senza commit:

- programma amministrativo autonomo: `83 passed`;
- manifesto di distribuzione: `52 passed, 1 skipped`;
- guard canonico: `68 passed`;
- registry e contratti builtin: `62 passed`;
- routing, resolver, geo e workload durevoli: `63 passed`;
- undo autenticato, sandbox reale e URL con socket locale: `56 passed` fuori
  dalla sandbox ristretta;
- hook fake, Tutor, notifiche e installer: `199 passed`; quattro prove di
  embedding reale restano escluse per assenza del modello BGE, prerequisito
  ambientale gia' registrato e non attraversato dal codice modificato;
- albero completo: 671 sorgenti, 339 facts canonici e 339 autonomi, tuple
  integrali identiche, zero `dynamic_boundary_access`, zero finding di
  inventario e zero finding birth-closed in entrambe le implementazioni;
- compilazione Python e `git diff --check`: verdi.

Restano prima del commit soltanto due nuovi riesami indipendenti read-only.
G6-B3 resta aperto finche' entrambi i verdetti non sono
`P0=0`, `P1=0`, `P2=0`.

### Snapshot B3 dopo il terzo riesame avversariale

Il terzo riesame non ha approvato lo snapshot precedente. Ha dimostrato quattro
cause reali, poi corrette separatamente. La porta autenticata accettava un
record costruito dal chiamante con un percorso temporaneo e un digest calcolato
dallo stesso chiamante. Il guard e il clone non riconoscevano alcune
composizioni attraverso `sys.modules`, `types.FunctionType`, alias di prima
classe e il protocollo obsoleto `load_module`. Il percorso reale di
`undo_last_turn` apriva un secondo catalogo invece di conservare la fotografia
usata per scegliere il reverse. Infine il generatore Synt produceva ancora un
wrapper con `spec_from_file_location`, mentre due attraversamenti AST tardivi
del manifesto di distribuzione lasciavano uscire `MemoryError` grezzo.

La porta in `runtime/admitted_module_v1.py` ora distingue due sole provenienze.
Un record runtime ordinario viene confrontato con una rilettura fresca del
catalogo verificato e vengono usati i valori del catalogo, non quelli del
chiamante. Un record proiettato nel subprocess viene accettato soltanto se il
manifesto e la firma sono riletti senza seguire link, la firma Ed25519 e'
verificata con una chiave pubblica trusted dell'installazione e nome, digest,
file e punto di ingresso coincidono esattamente con il record. Il parent monta
nel sandbox soltanto i singoli file di chiave pubblica; non espone la directory
che contiene le chiavi private. Una prova in un nuovo processo modifica
deliberatamente `METNOS_ADMITTED_EXECUTORS_V1` dopo l'avvio e dimostra che il
codice temporaneo non viene eseguito. La prova Bubblewrap reale della dipendenza
firmata termina con `1 passed` senza skip.

`runtime/reverse_patterns_patch.py` non ricarica piu' il catalogo: senza la
fotografia ricevuta fallisce chiuso. L'executor firmato `undo_last_turn` passa
la stessa istanza di catalogo ad `apply_patterns`; il test discriminante
osserva l'identita' dell'oggetto e il dispatch attraversa la porta reale. Il
file e' stato rifirmato offline, senza pubblicarlo nello store vivo. Il digest
corrente e' `sha256:8f819a52ce9f242ace86de74822058baf609c863557f7cdc4482439983f4f895` e
`sign.py verify` lo riconosce come firmato da `author`. La suite undo completa,
eseguita fuori dalla restrizione che impedisce a Bubblewrap di aprire
`NETLINK_ROUTE`, termina con `23 passed`.

Il wrapper specializzato di `runtime/synt.py` dichiara ora il parent in
`[code].dependencies` e lo carica esclusivamente tramite
`runtime_admitted_executor_v1` e `load_admitted_module_v1`. Non contiene piu'
`spec_from_file_location`, `exec_module` o ricerca per pathname. Le semplici
validazioni sintattiche di `runtime/synth_request.py` e dello script di
migrazione usano `ast.parse`, senza produrre bytecode eseguibile.

Guard canonico e clone amministrativo riconoscono ora anche lookup
`__getitem__`, copie di `sys.modules`, `types.FunctionType`, `runpy`,
`importlib.util`, `load_module` e alias di prima classe dei loader. Il
riconoscimento richiede sempre un binding importato reale: `re.compile`, una
variabile locale `types`, una funzione locale `run_module` e il lookup
`sys.modules[__name__]` restano controlli negativi leciti. I due parser del
manifesto normalizzano inoltre i `MemoryError` prodotti dagli attraversamenti
AST tardivi della versione e della chiusura degli import.

Evidenza corrente dello snapshot, ancora senza commit:

- guard canonico: `68 passed`;
- clone amministrativo autonomo: `92 passed`;
- manifesto di distribuzione: `54 passed, 1 skipped` per la sola piattaforma;
- porta, record contraffatto, subprocess negativo, fallback Windows, lettori
  indice e Synt: `44 passed, 3 skipped` per prove Linux non pertinenti a quel
  gruppo;
- registri letterali e contratti: `53 passed`; hook fake: `18 passed`; Synt e
  adapter Birth: `10 passed`;
- dispatch autenticato e prove undo mirate: `7 passed`; suite undo reale:
  `23 passed`; dipendenza Bubblewrap reale: `1 passed`;
- albero completo: 671 sorgenti, 339 facts canonici e 339 autonomi identici,
  zero finding di inventario e zero finding birth-closed in entrambe le
  implementazioni;
- `compileall`, `git diff --check` e firma di `undo_last_turn`: verdi.

Questo paragrafo sostituisce soltanto lo stato operativo dei due snapshot
precedenti; non elimina le decisioni e i requisiti gia' concordati. Prima del
commit servono due nuovi riesami read-only sul contenuto corrente, entrambi con
`P0=0`, `P1=0`, `P2=0`. Fino a quel momento B3 resta aperto e non si avvia il
nucleo preparatore.

### Snapshot B3 dopo il quarto riesame avversariale

Il quarto riesame ha respinto anche lo snapshot precedente e ha individuato
quattro difetti distinti. La porta usava funzioni pubbliche e mutabili del
modulo `loader`; un catalogo sostituito nello stesso interprete poteva quindi
presentare byte scelti dal chiamante. La selezione della radice delle chiavi
accettava un fallback relativo o la directory corrente quando il profilo
Windows non era disponibile. La proiezione montava un file `*_pub.bin` dopo il
solo controllo `is_file()`, che segue i link. Infine guard e clone non
riconoscevano `eval` e `exec` ottenuti dal dizionario di `builtins`, mentre le
due chiusure locali rifiutavano per il solo nome un innocuo metodo locale
`Runner.run_module`.

Le correzioni sono separate e falliscono chiuso. Il runtime cattura le due
funzioni del catalogo prima dell'esecuzione degli executor e, dopo il confronto
con il record corrente, rilegge e verifica comunque manifesto, firma Ed25519 e
binding completo. Nel subprocess minimo il modulo `loader` puo' essere assente:
quel percorso accetta esclusivamente il record proiettato sigillato e
ri-autenticato. La radice delle chiavi e' valida soltanto se assoluta; nessun
fallback usa piu' la directory corrente. Il singolo file pubblico montato deve
essere regolare, non link, non reparse point e lungo esattamente 32 byte.

Guard canonico e clone autonomo riconoscono ora anche
`sys.modules['builtins'].__dict__['eval'](...)` e la forma equivalente con
`__getitem__('exec')`. Le chiusure di import risolvono invece il callable dal
binding importato: `runpy.run_module` viene negato, un metodo locale omonimo
resta valido. Le prove discriminatorie nuove coprono il catalogo sostituito, la
firma mancante, il link da chiave pubblica a privata, home/config relativi, i
due lookup riflessi e il controllo negativo del metodo locale.

Una prima esecuzione della prova Bubblewrap reale ha inoltre mostrato un errore
di integrazione introdotto dalla cattura eager di `loader`: il child minimo non
monta quel modulo. La causa e' stata corretta senza ampliare i mount: l'import
iniziale del catalogo e' opzionale e il ramo runtime ordinario fallisce chiuso
se non e' disponibile. La ripetizione reale, fuori dal sandbox del runner, e'
verde con `1 passed`.

Evidenza corrente, ancora senza commit:

- porta autenticata: `26 passed`; porta piu' dispatch: `29 passed`;
- undo mirato con sandbox disabilitato: `15 passed`; insieme undo e dispatch:
  `18 passed`;
- guard canonico: `68 passed`; clone autonomo: `96 passed`; manifesto di
  distribuzione: `56 passed, 1 skipped` soltanto per piattaforma;
- Synt, indici e contratti builtin: `60 passed, 3 skipped` per dipendenze
  ambientali gia' note;
- dipendenza Bubblewrap reale: `1 passed`;
- 671 sorgenti, 339 facts canonici e 339 autonomi identici, zero finding di
  inventario e zero finding birth-closed;
- firma di `undo_last_turn`, `compileall` e `git diff --check`: verdi.

I verdetti ottenuti prima di queste modifiche sono obsoleti. Servono due nuovi
riesami read-only sul diff corrente con `P0=0`, `P1=0`, `P2=0`; fino ad allora
B3 resta aperto e non si committa.

### Snapshot B3 dopo il quinto riesame avversariale

Il quinto riesame ha trovato tre cause ulteriori e ha reso obsoleti anche i
verdetti precedenti. Una denylist AST non puo' dimostrare l'assenza di ogni
forma riflessiva di esecuzione Python. Un child con dipendenza dichiarata A
poteva inoltre sostituire il record d'ambiente con quello autentico di un
fratello B, perche' il mount generale di `/opt` rendeva entrambi visibili.
Infine un nuovo contratto Birth con pattern reverse non dichiarativo poteva
eseguire il proprio top-level dentro il broker `undo_last_turn`, che opera
deliberatamente senza Bubblewrap.

La radice di sicurezza e' ora il digest positivo di tutti i sorgenti Python
nelle quattro radici chiuse. Il valore atteso e' compilato nel verifier della
release precedente gia' fidata; non e' una dichiarazione del candidato. Il
manifesto di distribuzione ricalcola il digest usando il runtime fidato, il
preflight installato ripete il calcolo e richiede che la propria copia
amministrativa sia byte-identica a quella runtime. Una prova altera un sorgente
e riscrive entrambi i pin presenti nel candidato: il verifier produttivo gia'
caricato conserva il valore atteso precedente e rifiuta il candidato. Il guard
AST resta una seconda cintura indipendente per topologia e regressioni, non
pretende di essere una sandbox semantica di Python.

Il mount generale `/opt` e' stato rimosso. Restano visibili soltanto runtime,
interprete, directory dell'executor corrente, radici esatte delle dipendenze
dichiarate e singole chiavi pubbliche necessarie. La sandbox OS e' obbligatoria
per ogni executor ordinario, indipendentemente da origine e appartenenza; il
solo percorso naked richiede anche il digest firmato `8f819a...` e l'unico file
`undo_last_turn.py`, quindi una revisione Birth successiva fallisce chiusa fino
a una nuova revisione della distribuzione. La prova
Bubblewrap reale carica A, sostituisce il record con quello validamente firmato
di B e dimostra che B non e' montato e non viene eseguito.

La porta autenticata chiude inoltre il vocabolario dei reverse eseguibili:
qualunque pattern non appartenente al catalogo dichiarativo e' ammesso soltanto
per i nove accoppiamenti nome-digest gia' revisionati. Il controllo avviene
prima di compilare o eseguire il top-level. `undo_last_turn` non e' stato
rifirmato per questa correzione: e' tornato byte-identico al digest gia' firmato
`sha256:8f819a52ce9f242ace86de74822058baf609c863557f7cdc4482439983f4f895`.

Le altre correzioni dello snapshot comprendono il rifiuto delle chiavi
pubbliche con hardlink, il rifiuto Synt dei parent non `local-subprocess`, la
chiamata standard del parent con un solo argomento e la ricostruzione JSON
corretta di booleani, null e valori composti.

Evidenza del candidato congelato:

- radice sorgenti `sha256:87f7d309555793642066f778013be58024421b2026d00a2769e15c3324e1f4b5`;
- 671 sorgenti, 339 fatti canonici e 339 autonomi identici, zero finding di
  inventario e zero finding `birth-closed`;
- guard `69 passed`, preflight autonomo `111 passed`, distribuzione
  `57 passed, 1 skipped` per piattaforma;
- porta, undo, dispatch e sandbox mirati `55 passed`; regressioni Synt e
  prodotto `84 passed, 3 skipped`; sandbox skill `45 passed`;
- integrazione porta/Birth `39 passed, 1 skipped` nel contenitore e prova
  Bubblewrap discriminante A/B reale `1 passed` fuori dal contenitore;
- firma di `undo_last_turn`, controllo reale `--birth-closed` e
  `git diff --check` verdi.

Lo snapshot e' tecnicamente verde ma B3 resta aperto. Prima del commit servono
due nuove revisioni indipendenti e da zero sul contenuto congelato, entrambe
con `P0=0`, `P1=0`, `P2=0`. Dopo il commit pubblico e la matrice Linux/Windows
si prosegue col nucleo preparatore B3; non si dichiara chiuso RM-0008.

### Esito finale delle revisioni sul candidato B3

Il 29 agosto 2026 due revisioni indipendenti e read-only hanno riesaminato il
candidato finale con radice
`sha256:87f7d309555793642066f778013be58024421b2026d00a2769e15c3324e1f4b5`.
Entrambe hanno concluso `P0=0`, `P1=0`, `P2=0`. La revisione di regressione ha
ricontrollato pin, inventario, parita', sandbox, Bubblewrap reale e firma undo;
la revisione di qualita' ha ricontrollato anche la coerenza delle pagine
italiane e inglesi. Nessun revisore ha modificato file.

Il commit incrementale su `main` e' quindi autorizzato. Restano da registrare
hash del commit, pubblicazione e risultato della matrice GitHub Linux/Windows.
Questa autorizzazione chiude il candidato architetturale revisionato, non il
nucleo preparatore B3 e non RM-0008.

## Decisioni gia' fissate

- Il piano amministrativo vive fuori dalle release ed e' posseduto da `root`.
- Le release sono directory immutabili pubblicate senza sovrascrittura.
- `required-head-v1.bin` e' il solo selettore atomico della release.
- Prima del certificato e' ammesso soltanto il predecessore autenticato dal
  descrittore root-owned; dopo il certificato non esiste alcun ritorno.
- La catena usa record firmati storici e verifica i file vivi soltanto per la
  testa richiesta.
- Un solo catalogo chiuso governa servizi, timer, eccezioni, dipendenze,
  installazione, manutenzione, firma e test.
- Le unita' candidate restano artefatti firmati nel gruppo 6; soltanto la cella
  isolata installa un catalogo separato gia' non collidente. I nomi reali
  appartengono al gruppo 7.
- L'ordine dei blocchi e': deployment, blocco esclusivo degli avvii,
  cutover/manutenzione, poi store.
- La politica compilata chiusa non cambia nel gruppo 6.
- G6 non modifica il server reale: installatore, piano amministrativo e
  composizione vengono provati nella VM usa-e-getta. La preparazione produttiva
  nega prima di claim e journal; prima installazione e prima prenotazione reali
  appartengono al binario G7 col diniego compilato vero.
- Per l'eventuale prova Windows diretta usare l'IP `192.168.1.137`; il nome
  `PC-ROBERTO` resta soltanto l'identificativo del dispositivo.

## Prossimo passo unico

G6-A, G6-B1 e G6-B2 sono chiusi con errore zero. Il prossimo incremento e'
soltanto G6-B3: nucleo preparatore bloccato, autorita' privata letta a freddo,
artefatti riletti integralmente e capacita' nominale priva di percorsi. Prima
del codice rileggere il piano approvato e delimitare la singola famiglia di
prove B3, senza duplicare prove gia' certificate in B1 o B2. Non anticipare
pubblicazione B4, G6-C o G6-D e non esporre claim, disposizione o `PREPARED`.

## Regole operative

- commit piccoli soltanto su `main`, con footer
  `RM-0008-Status: candidate-not-certified`;
- eseguire prove e strumenti Python con `/opt/metnos/.venv/bin/python`; per
  l'export impostare `METNOS_VENV=/opt/metnos/.venv`. Il Python di sistema e
  `/opt/suprastructure/.venv` non contengono tutte le dipendenze Metnos (in
  particolare `tomlkit==0.15.0`) e non sono ambienti validi per RM-0008;
- aggiornare questo file dopo ogni evidenza, correzione, revisione e risultato
  pubblico;
- una sola famiglia di prove per rischio distinto;
- nessuna matrice completa prima della chiusura del gruppo;
- nessuna correzione per tentativi: prima causa e misura discriminante;
- nessuna attivazione reale della build chiusa, nessun avvio reale dello stack
  candidato e nessuna dichiarazione F4 prima del gruppo 7.

## Correzione CI Linux della base B3 — stato di handover

Il commit sorgente `0d57a887` e il commit pubblico `30b2fee4` hanno prodotto la
matrice GitHub `33258481590`. Sette job su otto sono verdi, compresi l'intero
Windows e la suite portabile Ubuntu (`749 passed, 25 skipped`). L'unico errore
e' la prova Birth delegata Linux: dopo la rimozione corretta del bind generale
`/opt`, Bubblewrap non trovava il Python non-venv installato da
`actions/setup-python` sotto `/opt/hostedtoolcache/.../x64`.

La causa e' stata corretta senza riaprire `/opt`. `runtime/sandbox.py` monta
read-only soltanto `sys.prefix`, `sys.exec_prefix`, `sys.base_prefix` e
`sys.base_exec_prefix`, deduplicati. Sorgente canonica e destinazione lessicale
restano distinte per i venv raggiunti tramite symlink. Prefissi generici,
prefissi che contengono runtime o executor, interprete e libreria standard non
coperti producono `SandboxUnavailableError`.

Evidenza locale del candidato corrente:

- review avversariale finale: `P0=0`, `P1=0`, `P2=0`;
- radice privata: `sha256:9097f35f635e88f52b360f9540fbfdb9b25b4567384a5d6264a74076d714b4c0`
  su 671 sorgenti;
- radice pubblica: `sha256:f66473c54d13f7dedb43b8f357f04b7da83f906d6e2e42c0296b44bd14e29a46`
  su 659 sorgenti;
- test sandbox `68 passed`; gate centrali mirati `199 passed`;
- Bubblewrap A/B reale privato e pubblico: `1 passed` ciascuno;
- suite pubblica completa: `749 passed, 25 skipped`;
- publisher `--check`: root privato/pubblico coerenti e zero PII/segreti.

La replica locale del wrapper root `systemd-run` non era eseguibile senza
password `sudo`; e' stata quindi lasciata, correttamente, alla matrice pubblica.
Il commit sorgente e' `afd53ebe`; l'export incrementale sul solo `main` pubblico
e' `86e69ec5b95b5c88af924db3bc027e774bd06ea7`. Il run GitHub
`33259624116` ha concluso con tutti i job a `success`, compresi Birth delegato
Ubuntu, suite completa Windows e riepilogo finale. L'errore della base
architetturale B3 e' quindi zero. Il prossimo passo unico e' il nucleo
preparatore B3; RM-0008 resta `active`.

## Riesame preliminare del nucleo B3 — blocco P0 aperto

Prima di iniziare il preparatore, tre analisi read-only hanno confrontato il
contratto B3 con il codice corrente. Hanno rilevato un errore riproducibile nel
programma che B3 dovrebbe copiare e firmare: `runtime/executor_birth_admin_preflight.py`
contiene parser e verificatori, ma non possiede `main()` ne' il dispatch
operativo di `check`, `launch` e `check-all`. L'esecuzione isolata reale

```text
python -I -S runtime/executor_birth_admin_preflight.py check-all
```

termina con codice `0`, stdout vuoto e stderr vuoto senza svolgere alcun
controllo. Firmare questi byte renderebbe apparentemente valido un preflight
non operativo.

Il rilievo e' classificato P0 e precede il nucleo preparatore. Non viene
risolto con un semplice dispatch che nega sempre e non viene spostato in G6-C:
la decisione piu' restrittiva gia' registrata in questo handover richiede byte
amministrativi definitivi in B3, perche' una modifica successiva cambierebbe
manifesto, firma e `administrative_bundle_hash`. Sono in corso la delimitazione
del percorso operativo mancante e la riconciliazione delle frasi meno recenti
del piano che attribuiscono ancora a G6-C l'implementazione dei tre comandi.

Restano inoltre da congelare prima del preparatore: consumo e rilettura fresca
della fotografia del coordinatore, riconciliazione con la catena richiesta,
separazione netta fra staging B3 e rename finale B4, consumo singolo della
firma e tipo nominale distinto per la distribuzione verificata nello staging.
Il worktree non contiene ancora codice del preparatore; l'ultimo punto
certificato pubblico resta il run `33259624116` con errore zero.

La mappa tecnica del P0 e' ora conclusa. Il file corrente, di 4.621 righe,
termina con parser puri di systemd. Il delta non e' un semplice `main()`:
mancano i codec autonomi e la riconciliazione della catena ownership, i decoder
e i legami di catalogo/descrittore/prerequisito, le prove degli eseguibili e
della TCB OpenSSL, la fotografia systemd effettiva, l'attestazione di
`check-all` e il bootstrap chiuso di `launch`. Poiche' il programma deve
funzionare con `python -I -S`, questi componenti non possono importare i codec
Metnos omologhi e richiedono cloni stdlib provati per parita'. La dimensione
stimata del delta e' 2.500-4.000 righe produttive; trattarlo come una piccola
correzione sarebbe una falsa convergenza.

Il completamento viene diviso nel minimo di quattro incrementi, senza
pubblicare o firmare byte intermedi:

1. punto d'ingresso, codici pubblici, blocco startup, stato ownership e codec;
2. catalogo, descrittore, prerequisito, TCB ed effettiva configurazione systemd;
3. `check`, `check-all` e attestazione no-replace;
4. `launch`, riduzione dei privilegi, bootstrap Python/native e supervisione
   dei discendenti.

Il secondo P0 di contratto sul bootstrap e' risolto nel piano prima del codice.
Il `runpy.run_module` normativo resta necessario sotto `-I -S`; la policy non
introduce una whitelist di scope, ma una sola eccezione positiva per la forma
AST esatta nello scope autenticato `_launch_python_target_v1`. Guardia,
verificatore del manifesto e rispettivi cloni autonomi devono applicare la
stessa regola; alias, reflection, keyword mutate, altro scope o una seconda
porta dinamica restano negati e vengono provati con mutanti.

Il primo incremento e' ora implementato localmente, ma non ancora committato.
Il programma possiede `main()`, accetta soltanto le tre forme gia' fissate,
nega piattaforme non Linux e identita' non root prima dell'I/O operativo,
normalizza ogni errore nei soli codici pubblici 20-24 e non espone dettagli o
traceback. Il nucleo operativo resta intenzionalmente un diniego `MISSING` e
verra' sostituito dagli incrementi B3 successivi prima di staging e firma. Le
12 prove mirate sono verdi. La suite completa del file mostra soltanto il
fallimento statico previsto della vecchia radice source-review: il pin non
viene aggiornato su un incremento intermedio e verra' ricalcolato dal
verificatore precedente fidato soltanto sul candidato B3 completo.

Il primo sottoincremento dei codec ownership autonomi e' anch'esso completato
localmente e attende il riesame avversariale. Il programma decodifica i tre
registri pubblici a scopo singolo, pretende chiavi distinte e ricostruisce
certificato cutover, testa e frame `required-head` canonici. I risultati si
chiamano esplicitamente `DecodedOwnership*`: la decodifica strutturale non
autentica la firma e non produce autorita'. La prova di parita' usa i codec
runtime omologhi, dimostra il legame `sequenza 1` se e soltanto se predecessore
nullo, nega chiavi condivise e byte finali nel frame e mostra che una firma
alterata resta soltanto un candidato decodificato mentre il verificatore
canonico la rifiuta. La suite del preflight, escluso unicamente il vecchio pin
source-review, termina con `121 passed, 1 deselected`; compilazione e
`git diff --check` sono verdi. Claim, journal V2, descrittore del predecessore
e osservazione handle-bound restano fuori da questo sottoincremento.

Il primo riesame indipendente ha approvato questo sottoincremento con
`P0=0`, `P1=0`, `P2=0`. Il secondo ha confermato `P0=0`, ma ha trovato due P1
prima che il codice diventasse operativo. `main()` catturava anche
`SystemExit`, quindi il futuro target Python avrebbe trasformato perfino
`SystemExit(0)` in recupero 24; ora il normalizzatore cattura soltanto
`Exception` e la prova propaga lo stato del target senza output del preflight.
Inoltre il parser candidato classificava correttamente come invalido un frame
`required-head` alterato, ma la futura lettura dalla radice durevole deve
classificare lo stesso danno come recupero: un wrapper distinto applica ora
questa semantica e il mutante con byte finali prova entrambi i risultati. I
tipi strutturali sono stati resi privati `_Decoded*`; mutanti compatti coprono
purpose e prima sequenza del registro e `catalog_id` del cutover. La suite
aggiornata, sempre senza il solo vecchio pin, da' `122 passed, 1 deselected`.
Serve un nuovo verdetto sul diff corretto prima del prossimo sottoincremento.

Il riesame finale del diff corretto e' concluso con `P0=0`, `P1=0`, `P2=0`.
Sono stati rimossi anche i simboli di firma non ancora usati e il limite del
frame deriva ora dall'unica costante della magic. Questo chiude localmente il
punto d'ingresso e i primi codec non autorizzanti; non chiude il preflight B3.
Il sottoincremento successivo e' limitato a claim e prefisso journal V2 fino a
`HEAD_REQUIRED`, senza I/O produttivo o collegamento al dispatch.

Claim e prefisso journal V2 sono ora implementati localmente come sola
decodifica non autorizzante. Il confronto con il coordinatore canonico copre
identita' del claim, vincolo sequenza/predecessore, stati `000`-`005`, hash dei
record, campi persistenti, soglie, prova di manutenzione e
`install_transaction_id`. La prima review ha individuato una falsa negazione
P1: il limite JSON generale di 120.000 nodi respingeva un record canonico da
30.000 ricevute e circa 6 MiB che il runtime accetta entro il limite di 8 MiB.
La causa e' stata rimossa derivando il tetto dei nodi dalla dimensione in byte
gia' limitata del documento; resta il limite di profondita'. Il vettore ad alta
cardinalita' prova ora l'accettazione sia col decoder canonico sia col clone.
Mutanti diretti coprono inoltre transazione, base64 e legame della manutenzione,
soglie del certificato e dell'albero, testa verificata e attestazione prematura.
Valori JSON non stringa nei campi enumerati della manutenzione vengono
normalizzati in `INVALID`, senza `TypeError` interno. Dopo le correzioni la
suite del preflight, escluso soltanto il vecchio pin source-review
intenzionalmente non aggiornato, termina con `124 passed, 1 deselected`;
compilazione e `git diff --check` sono verdi. I riesami finali, compresi i
delta sui tipi enumerati e sul legame della manutenzione, hanno concluso
`P0=0`, `P1=0`, `P2=0`. Il sottoincremento claim/journal e' quindi approvato
localmente. Nessun commit o pubblicazione e' stato eseguito; il prossimo
sottoincremento minimo e' il decoder autonomo di `predecessor-v1`.

Anche il decoder autonomo di `predecessor-v1` e' ora completato e approvato
localmente con `P0=0`, `P1=0`, `P2=0`. Il clone e' privato, non autorizzante,
stdlib-only e senza I/O. Replica il dominio dell'identificativo, i limiti e gli
ordinamenti di file, comandi e ambiente e i legami dei quattro tipi di comando.
Helper specifici evitano tre divergenze degli helper amministrativi generali:
il test positivo ammette correttamente percorso e radice oltre 4096 byte e
unita' oltre 191 caratteri; il basename `received-source-v1.json` resta invece
vietato. Dieci mutanti ricalcolano l'identificativo quando necessario e
isolano root, basename, ordini di file/comandi/ambiente, intero booleano,
denylist ambiente, modulo Python, nome unita' e self-ID. La suite aggiornata,
escluso il solo vecchio pin source-review, termina con
`125 passed, 1 deselected`; i sette test canonici predecessor sono verdi,
come compilazione e `git diff --check`. Questo e' un punto fermo senza commit o
pubblicazione; prima del sottoincremento successivo vengono riesaminati i turni
indicati dall'utente.

Il riesame del percorso dei turni conferma la decisione che evita una chiusura
apparente: B3 deve produrre i byte operativi completi prima di staging e firma;
G6-C li installa e li prova, ma non li implementa o modifica. Una frase piu'
vecchia del piano attribuiva ambiguamente a G6-C il "controllo operativo" ed e'
stata corretta per distinguere implementazione B3 da installazione e prova C.
Non emergono file fuori perimetro, rami aggiuntivi o commit intermedi: il solo
ramo e' `main` e il diff locale e' limitato a preflight, relativa suite e tre
documenti RM-0008. Il percorso minimo non cambia: il prossimo incremento e'
l'osservazione fixed-root e handle-bound delle prove ownership gia'
decodificate, con autenticazione delle firme e legami claim/journal; non si
avvia ancora il preparatore, non si aggiorna il pin e non si pubblica.

Il riesame indipendente dell'I/O ownership ha precisato il confine del prossimo
incremento. Letture successive con `_read_bounded_regular_v1` stabilizzano il
singolo file, ma non impediscono di comporre puntatore, archivi, claim, journal
e predecessore appartenenti a epoche diverse. Il core nuovo deve quindi aprire
una sola volta la radice fissa `/var/lib/metnos/executor-birth`, mantenere vivi
gli handle delle sottoradici e leggere ogni discendente con `dir_fd` e
`O_NOFOLLOW`. Inventario e identita' vengono fotografati prima e dopo la
decodifica e la radice per pathname viene confrontata nuovamente con l'handle
prima del ritorno. La mera presenza di `predecessor-v1.json` non seleziona lo
stato iniziale, perche' il file resta no-replace dopo il primo passaggio:
`INITIAL` richiede invece assenza simultanea di ancora e testa richiesta e tre
archivi vuoti; ogni stato parziale richiede recupero.

Per mantenere il rischio verificabile senza duplicazioni, il lavoro e' diviso
in due soli incrementi verticali. Il primo realizza la fotografia fixed-root
handle-bound e restituisce un candidato privato esplicitamente non
autorizzante. Il secondo autentica registri, coppie archiviate, ancora e testa,
completa il supporto del journal fino a `PREFLIGHT_VERIFIED` e riconcilia tutti
i legami col coordinatore. La separazione evita che un tipo parziale sembri
autorita' e consente test brevi che mutano fra le fotografie A e B puntatore,
inventario o predecessore. Nessun commit, aggiornamento del pin o pubblicazione
precede il completamento del preflight operativo.

La fotografia fixed-root e' ora implementata localmente. Il risultato e' un
tipo privato denominato esplicitamente `Candidate`, mentre la seam portabile
restituisce un tipo nominalmente distinto. Il core non rilegge discendenti per
pathname: conserva gli handle di radice, autorita', catena, archivi,
coordinatore e transazioni; verifica inventari e identita' A/B e il rebound di
ogni directory e file dal medesimo genitore. Le chiavi private vengono soltanto
inventariate come file root-owned `0600`, hardlink singolo e 32 byte, senza
leggerne il contenuto. Sono gia' decodificati i formati autonomi disponibili;
record storico V1, disposizione legacy e record V2 numero 006 restano byte
catturati e saranno completati nell'incremento di autenticazione, senza falsa
negazione dello stato valido.

Le tre famiglie probatorie brevi sono verdi: stato iniziale e stato con testa
richiesta, sostituzione di puntatore/claim/predecessore fra A e B e mutanti di
hardlink, modo, coppia incompleta e rebound della radice. Il totale mirato,
escluso intenzionalmente il vecchio pin source-review, e' `134 passed,
1 deselected` in meno di un secondo; compilazione e `git diff --check` sono
verdi. Il diff resta non committato in attesa del riesame avversariale di
questo incremento.

Il primo riesame avversariale della fotografia ha riprodotto tre P1 interni al
confine filesystem. La catena degli antenati era validata soltanto in A; uno
dei genitori poteva quindi diventare scrivibile prima del ritorno. Inoltre un
solo `.required-head-v1.lock` veniva scambiato per stato iniziale e un nome
temporaneo con prefisso `ownership-cutover-v1.` restava fuori dal filtro della
radice. Sono stati corretti fotografando e riverificando identita' e policy di
tutti gli antenati, negando il lock nello stato completamente vuoto e chiudendo
l'inventario dei nomi simili all'ancora sia in A sia in B. E' stata chiusa
anche una perdita di descrittore quando la registrazione iniziale di una
directory fallisce, compreso il ramo `MemoryError`. I mutanti nuovi riproducono
esattamente i tre P1; un caso aggiuntivo conserva l'invariante che la presenza
del predecessore non seleziona il regime iniziale. La suite ora termina con
`138 passed, 1 deselected`; serve il verdetto finale sul delta.

Le osservazioni della review su firme, catena autenticata e legami del grafo
non sono difetti occultati del tipo corrente: il risultato resta denominato
`Candidate`, non ha consumer produttivi e il piano li assegna esplicitamente al
secondo e ultimo incremento di questa coppia. Quel lavoro resta obbligatorio
prima del dispatch, dello staging o di qualunque commit.

La riverifica finale del primo incremento filesystem e' conclusa con tre
verdetti indipendenti `P0=0`, `P1=0`, `P2=0`. La seam `MemoryError` non lascia
descrittori aperti; i tredici casi fixed-root e l'intera suite senza il pin
intermedio producono rispettivamente `13 passed` e `138 passed,
1 deselected`. Compilazione e `git diff --check` restano verdi. L'incremento e'
quindi approvato localmente, ma non viene committato da solo: il passo unico e'
ora autenticare la fotografia e riconciliare catena e coordinatore, compresi
record V2 `006` e disposizione legacy.

## Punto fermo del 29 agosto: turni `9119a307` e `052f0f91`

I due turni sono stati analizzati sui record reali prima di modificare codice.
Non costituiscono un nuovo requisito della porta Birth, ma ogni sorgente
modificata entra nel censimento e dovra' attraversare B3 prima della
pubblicazione finale.

Il turno `052f0f91` non e' fallito per un divieto di Linux. Un ping reale e
limitato dal server verso `192.168.1.137` ha ricevuto risposta. Il difetto e'
nel selettore: `admin` e' il fallback protetto per i comandi, ma veniva
iniettato mediante una lista manuale incompleta. La correzione locale non
aggiunge il caso `ping`: `safety.canonicalize.command_grammar_binaries()`
espone l'inventario unico delle famiglie comprese nella grammatica di
canonicalizzazione. Il prefilter aggiunge `admin` in coda soltanto quando il
nome e' accompagnato da un segnale strutturale generale: una forma di
invocazione immediatamente precedente oppure, se il comando apre la richiesta,
un primo argomento inequivocabilmente CLI. Forme e polarita' provengono dal
registro traducibile; invocazione, negazione, inibizione e contrasto richiedono
risorse native pronte e revisione manuale. Il vecchio elenco shell
italiano/inglese e' migrato nel concetto `admin.shell_intent` conservando
esattamente l'unione editoriale preesistente. Il ramo shell applica lo stesso
controllo di polarita' e non puo' piu' aggirare il fallback sicuro.

Il solo nome non basta, perche' la grammatica contiene anche parole comuni
come `date`, `file`, `last` e `who`. Una revoca successiva senza nuovo target
nega il comando; una negazione che nomina un binario diverso resta limitata a
quel binario. Un test con `futurectl` prova che un comando nuovo non richiede
modifiche al selettore. La forma live `fai ping a pc-roberto` e' positiva;
prosa, negazioni, inibizioni, revoche successive, punteggiatura e lingua
parzialmente materializzata sono negativi. Una terza lingua con forme Unicode
e multi-parola segue lo stesso percorso dati. La polarita' restituisce anche
lo stato distinto `unavailable`: una risorsa nativa o manuale mancante non
viene confusa con una frase positiva. Il daemon interrompe il ciclo prima di
elencare le traduzioni o chiamare il modello se non riesce a caricare la
politica di revisione umana.

La destinazione e' una sequenza ordinata di menzioni: una correzione esplicita
successiva prevale, mentre una revoca finale vieta il riuso della destinazione
ricordata o predefinita. Alias deboli non prevalgono su riferimenti espliciti e
il controllo resta attivo anche quando non esistono device registrati. Le prove
correnti terminano con `129 passed` sul perimetro mirato e `592 passed, 1.144
subtests passed` sulla regressione i18n e sui consumer collegati. Due revisioni
indipendenti finali concludono entrambe `P0=0`, `P1=0`, `P2=0`; compilazione e
`git diff --check` sono verdi.

L'analisi ha anche invalidato il closeout nominale di RM-0005: il rapporto
storico del lessico dichiarava ancora una terza ondata da svolgere. RM-0005 e'
ora `reopened`; il percorso shell/ping e' corretto, mentre il censimento
residuo deve essere concluso prima di una nuova chiusura.

La verifica live successiva ha chiuso anche il percorso fra selezione ed
esecuzione. Il secondo filtro del proposer rimuoveva `admin` perché il registro
dei nomi runtime non lo classificava come nome di sistema; dopo la selezione il
callback principale bypassava il dispatcher condiviso e tentava di avviare il
builtin a verbo unico come subprocess. Infine la guardia di onestà cercava il
verbo dell'intento nel nome dell'executor e non riconosceva la ricevuta del
varco amministrativo. Le correzioni sono tutte per contratto, non per comando:
`admin` viene conservato soltanto quando il prefilter lo ha già scelto; ogni
builtin esposto al planner usa `VERB_UNIQUE_REGISTRY` e il dispatcher
in-process; `approval_required` ferma la coda; `execute_silent` riuscito
soddisfa l'azione e il suo riepilogo deterministico prevale nei turni composti
soltanto da operazioni amministrative. Il bootstrap del registro precede ogni
lettura, così cache e fixture non possono produrre un fallback d'ordine verso
il subprocess.

La prova reale conclusiva è il turno `13f78d922e1c47b8`: lo step `admin` ha
eseguito `ping -c 4 192.168.1.137`, con quattro pacchetti trasmessi, quattro
ricevuti e zero per cento di perdita; la risposta finale contiene lo stdout
reale. `mount` e `umount` derivano dallo stesso inventario canonico e sono
ammessi; CIFS/SMB/NFS restano approval-controlled. Non è stato eseguito un
mount reale perché avrebbe introdotto un effetto non necessario. La suite
finale conta `187 passed, 4 subtests passed` sul perimetro mirato e `594 passed,
1.144 subtests passed` sulla regressione i18n e sui consumer collegati.

Consolidamento concluso su `main`: commit privati `25769cbc` (runtime, test e
documenti) e `bff1595e` (radici sorgente revisionate); commit pubblico
incrementale `62c61b5cfc0c3aec144aac81bff9c59279c0390e`. Il run GitHub Actions
`33271817356` è verde in tutti gli otto job bloccanti, compresi Ubuntu,
Windows, identità ACL reali, concorrenza e riepilogo di certificazione. Sul
repository pubblico esiste soltanto il ramo `main`. La documentazione è stata
rigenerata e distribuita; l'avviso GitHub sulla futura dismissione del runtime
Node 20 delle action artifact è non bloccante e non modifica il verdetto.

Resta distinta una lacuna di identita': il registro del device non conserva un
indirizzo IP autenticato. Per questo il nome `PC-ROBERTO` non puo' essere
trasformato fiduciariamente in `192.168.1.137`; il valore raccolto dal dialogo
non va dedotto da log o dal modello. Il fallback generale rende eseguibile la
ripresa che contiene l'IP, ma l'eliminazione del dialogo iniziale richiedera'
un campo address autenticato e aggiornato nel protocollo device, non una
regola dedicata al nome di questo PC.

Nel turno `9119a307`, `run_processes` ha negato correttamente un input privo di
`resolved_id`. Il produttore `find_packages` confondeva pero' due righe WinGet
con due identita': righe duplicate della stessa identita' erano marcate
ambigue. Il fix locale canonicalizza tutte le identita', emette
`resolved_id` soltanto se ogni riga e' valida e l'insieme case-insensitive ha
cardinalita' uno e pubblica candidati diagnostici non consumabili quando le
identita' sono realmente distinte. Nessun fallback verso il display name o
verso `package_id` e' ammesso. I candidati sono deduplicati e ordinati prima
del limite; conteggio e troncamento restano espliciti. Un test di proiezione
prova che un `resolved_id` annidato nella diagnostica non puo' autorizzare
`run_processes`. Duplicato equivalente, ordine provider invertito, due
identita', coppia valida/invalida e limite sono coperti. Il riesame finale e'
`P0=0`, `P1=0`, `P2=0`.

Il manifesto authoring di `find_packages` contiene versione, schema e digest
aggiornati, ma la firma precedente non viene riusata: la nuova firma e la
pubblicazione restano bloccate fino al completamento della porta Birth. Non e'
stato eseguito alcun commit, aggiornamento dello store vivo o push.

## Nota operativa sul riavvio del servizio — 29 agosto

Il riavvio live ha mostrato due unita' omonime ma appartenenti a manager
diversi: la vera istanza in esercizio e' l'unita' utente
`systemctl --user metnos-http.service`; la vecchia unita' di sistema tenta di
usare la stessa porta e lo stesso lock. Il lock ha impedito il duplicato, ma
l'unita' legacy e' entrata nel proprio ciclo di restart. E' stata fermata e la
sola unita' utente e' stata riavviata con successo; la porta 8770 risponde.
Un processo Unix nello stato zombie non puo' trattenere porta o lock: il
rafforzamento generale del contratto del servizio riguarda processi residui
vivi, `KillMode=control-group` e il diniego del restart loop sul codice stabile
di istanza gia' attiva, non l'uccisione indiscriminata per nome o porta.
