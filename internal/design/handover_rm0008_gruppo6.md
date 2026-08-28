# RM-0008 — passaggio di consegne del gruppo 6

## Stato corrente — 28/8/2026

Worktree: `/tmp/metnos-rm0008-a-only`, ramo unico `main`. Non toccare
`/opt/metnos` e non creare rami.

Il gruppo 5 e' chiuso. Il commit pubblico e' `a5bd396`; il ciclo GitHub Actions
`33183713818` ha concluso verdi nove lavori su nove, con errore zero su Linux e
Windows. Il commit sorgente di ingresso del gruppo 6 e' `225f9437`.
RM-0008 resta `active`; `closed_build_enforcement()` resta `False`.

### Incremento G6-A1 completato localmente

E' completato il primo sottoincremento di G6-A: record storico autenticato e
lettura produttiva a freddo della catena. Non sono ancora implementati il codec
V2, `successor-claims-v1`, la disposizione del journal V1 e la sessione
sigillata del blocco di deployment; pertanto G6-A e RM-0008 restano aperti.

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
alla porta 22 e' scaduta. Il percorso Windows ha comunque superato la review
dedicata; la matrice pubblica Linux/Windows resta unica e viene eseguita alla
chiusura del gruppo 6, come stabilito dal piano.

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

Completare soltanto G6-A con codec/store di `successor-claims-v1`, codec V2,
resolver del journal per transazione, disposizione esplicita del journal V1 e
sessione sigillata restituita da `_deployment_lock_v1()`. Non esporre ancora il
percorso che pubblica claim o `PREPARED` e non aprire in parallelo G6-B, G6-C o
G6-D.

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
