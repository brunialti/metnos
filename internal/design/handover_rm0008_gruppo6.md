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

G6-A e G6-B1 sono chiusi con errore zero. Il prossimo incremento e'
esclusivamente G6-B2: ricevitore root-only e transazione content-addressed da
sorgente a `source_id`. Prima del codice ridurre la famiglia di prove B2 ai
soli rischi non gia' coperti da manifesto e catalogo; poi usare un solo albero
non banale e un solo harness di ripresa per account, link, hardlink,
sostituzione, idempotenza e assenza di mutazioni sulle altre radici. Non
anticipare preparatore B3, pubblicazione B4, G6-C o G6-D e non esporre claim,
disposizione o `PREPARED`.

## Regole operative

- commit piccoli soltanto su `main`, con footer
  `RM-0008-Status: candidate-not-certified`;
- aggiornare questo file dopo ogni evidenza, correzione, revisione e risultato
  pubblico;
- una sola famiglia di prove per rischio distinto;
- nessuna matrice completa prima della chiusura del gruppo;
- nessuna correzione per tentativi: prima causa e misura discriminante;
- nessuna attivazione reale della build chiusa, nessun avvio reale dello stack
  candidato e nessuna dichiarazione F4 prima del gruppo 7.
