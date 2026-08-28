# RM-0008 — passaggio di consegne del gruppo 4

## Stato corrente — 28/8/2026

Worktree: `/tmp/metnos-rm0008-a-only`, ramo unico `main`. Non toccare
`/opt/metnos` e non creare rami.

I gruppi 2 e 3 sono chiusi. Il loro ultimo ciclo GitHub completamente verde e'
`33154494801`. RM-0008 resta `active`.

Il gruppo 4 e' soltanto la chiusura statica F4 del §23.6.4. Il piano completo
e' `internal/reports/rm0008-gruppo4-piano-ottimizzato.md`.

Stato piu' recente: G4-B+C e' completo localmente fino al commit `44affb51`.
Guardie e inventario hanno zero rilievi, le prove mirate sono 150 verdi e la
suite portatile completa conta 326 prove verdi, 23 non applicabili e zero
errori. Manca la sola pubblicazione e la conferma GitHub Linux/Windows; il
gruppo 4 non e'
ancora chiuso.

G4-A e' implementato nel commit sorgente `3eeb4b1b` e documentato in
`e45a192e`. Il primo commit pubblico e' `74c6997`, ciclo GitHub
`33158138028`, **rosso**.
Le due revisioni reali sono state
pubblicate tramite Birth, non tramite firma diretta:

- `undo_last_turn`: generazione
  `sha256:81aa2cb088306d57c90a2af136e6d95d3564058374811e2a9793ab58a5a0ed6a`;
- `find_persons_indices`: generazione
  `sha256:c3432e96b7ff44dbb88d332cf03f7b7717129cd04e75fd3c0015ff3db6b93600`.

Il caricamento diretto da percorso e l'inserimento del fratello in `sys.path`
sono stati rimossi. Il padre legge le dipendenze dal manifest firmato, proietta
soltanto record gia' verificati e monta le sole radici necessarie in sola
lettura. Il figlio confronta i byte con il digest prima di eseguirli.

Prove locali eseguite sul commit:

- 100 prove funzionali e di sicurezza verdi; la sola prova rossa nello stesso
  lotto e' il congelamento dell'inventario, rinviato espressamente a G4-B+C;
- tre celle R1 del grafo produttivo verdi;
- gruppo 3: 228 verdi e una non applicabile;
- intera suite portatile in radici di stato isolate: 325 verdi e 22 non
  applicabili, zero errori;
- 1 prova della guardia normale rossa per cinque classificazioni rinviate al
  congelamento G4-B+C, come previsto dal piano;
- cella portatile con intenzione Birth reale verde;
- attraversamento padre-processo-dipendenza verde senza sandbox; la stessa
  prova con Bubblewrap non e' eseguibile su questo host per diniego del kernel,
  quindi la matrice Ubuntu resta la prova autorevole;
- il manifesto finale 2A richiede la cronologia pubblica e non e' eseguibile
  nel repository sorgente; verra' eseguito dalla matrice pubblica.

Una prima esecuzione non isolata della suite portatile ha prodotto 13 errori
con la stessa causa: leggeva i binding dell'installazione reale sotto
`~/.local/state/metnos`. Dodici errori appartenevano a prove storiche e uno alla
nuova cella. La singola misura discriminante con radici temporanee ha portato
lo stesso insieme a zero errori; non e' stata applicata alcuna correzione al
prodotto per questo fatto ambientale.

La misura corrente della guardia `--birth-closed` e' **28 rilievi**:

- 5 vecchie autorita' di firma;
- 3 ambiti non classificati;
- 16 eccezioni compilate non riportate nell'inventario;
- 1 politica chiusa mancante;
- 1 proprietario Birth mancante;
- 2 voci stale.

L'aumento da 26 a 28 non introduce autorita' di firma: registra il nuovo
lettore autenticato, il nuovo preparatore puro del digest e lo spostamento del
vecchio simbolo. Queste voci saranno classificate una sola volta in G4-B+C.

Il riesame di velocizzazione ha unito G4-B e G4-C in un solo incremento
pubblico. G4-A resta separato per ottenere prima la prova Linux e Windows della
nuova porta autenticata.

## Arresto pubblico G4-A

Nel ciclo `33158138028` sei lavori posseduti sono verdi, comprese le tre prove
Windows 2A. I due lavori della suite portatile generale sono rossi sullo stesso
test nuovo; il riepilogo e' rosso di conseguenza.

La causa e' delimitata: il test importava `agent_runtime`, che al caricamento
importa il sistema dei prompt e quindi `minijinja`. La suite portatile installa
intenzionalmente soltanto `cryptography`, `pytest` e `tomlkit`; non deve
trascinare l'intero prodotto. Linux e Windows falliscono entrambi con
`ModuleNotFoundError: minijinja` prima di eseguire il corpo della prova.

Non si aggiunge `minijinja` alla suite, perche' il problema non e' una
dipendenza produttiva mancante. La correzione prevista e' estrarre la piccola
proiezione dei record autenticati in un modulo leggero gia' consumato da
`agent_runtime`. La prova portatile chiamera' quel simbolo produttivo e poi
attraversera' il vero processo figlio e la sandbox, senza importare il motore
completo. Risultato atteso: il test raggiunge il figlio su entrambi i sistemi;
su Linux il risultato deve inoltre attestare Bubblewrap.

La correzione e' stata salvata nel commit sorgente `19119f5f` e pubblicata nel
commit `df4070f`. Il simbolo leggero e'
`admitted_code_dependency_projection_v1()`; `agent_runtime` lo consuma senza
duplicare la selezione. Evidenza locale successiva:

- nuova cella con importazione di `minijinja` vietata: 2 verdi;
- porta, proiezione e rifiuti avversariali: 20 verdi;
- intera suite portatile isolata: 325 verdi, 22 non applicabili, zero errori.

I 22 casi non applicabili sono esatti: 19 appartengono a Windows, due al
delegatore Linux cgroup non disponibile nella sessione ordinaria e uno a
Bubblewrap negato dal kernel locale. GitHub possiede le esecuzioni reali per
questi confini. `PC-ROBERTO` e' online, ma il protocollo remoto accetta soltanto
executor firmati e non consente l'esecuzione arbitraria di `pytest`; non e'
stato aperto un bypass.

Il secondo ciclo pubblico `33158999751` ha confermato la correzione originaria:
tutti i lavori Windows, tutti i lavori posseduti e la suite portatile Windows
sono verdi. La sola suite Linux generale ha un errore nella nuova prova; il
riepilogo e' rosso soltanto come conseguenza.

Il log delimita una seconda causa ambientale indipendente. La prova sintetica
usava un executor senza autorita' di rete, quindi `sandbox.wrap_command()`
chiedeva anche una nuova rete isolata. Bubblewrap sul runner non delegato di
GitHub falliva nel configurarne il loopback con
`Failed RTM_NEWADDR: Operation not permitted`; 325 altre prove erano verdi.
Questa cella certifica invece la proiezione autenticata e il montaggio in sola
lettura. La separata prova Birth Linux certifica gia' l'isolamento di rete nel
servizio delegato ed era verde nell'ultimo ciclo completo.

La prima ipotesi correttiva ha isolato il confine di rete dichiarando
`network:http` nel solo executor sintetico. Era verde localmente con 326 prove
verdi, 21 non applicabili e zero errori. E' stata salvata nel commit sorgente
`a081a48b`, pubblicata come `820dcb4` e verificata nel ciclo `33159721631`.
Tutti i lavori tranne Linux generale sono verdi, ma il nuovo log mostra ancora
un errore: `bwrap: setting up uid map: Permission denied`, con 325 altre prove
verdi. La prima limitazione di rete mascherava quindi una limitazione piu'
bassa: il runner ordinario GitHub non possiede la delega necessaria per creare
la sandbox Bubblewrap completa. L'ipotesi `network:http` e' superata e viene
rimossa.

La correzione seguente e' stata salvata nel commit sorgente `ff218021`,
pubblicata come `9d5c711` e verificata nel ciclo `33160084022`. Il ciclo ha
fornito due fatti distinti. La suite Linux generale e' verde con 325 prove
verdi e 22 non applicabili. Nel servizio delegato le due prove Birth sono
verdi, mentre la prova G4-A raggiunge Bubblewrap ma non puo' montare
`/home/runner/work/metnos/metnos/runtime`: il servizio root non puo' attraversare
quel percorso protetto dopo l'apertura della mappa utente.

Lo stesso ciclo ha inoltre confermato che il flusso GitHub e' parte della base
2A congelata. I sei lavori posseduti, e di conseguenza anche il lavoro Windows
generale, rifiutano qualsiasi variazione con
`frozen acceptance baseline differs from the pre-fix commit`. Il flusso non
deve essere modificato. Il suo contenuto originale e il digest congelato
`3e953be12480be9a4e6dfa19812a053492b5e26e155c9ecb7b749c29bde135e9`
sono stati ripristinati.

La correzione finale riusa senza modificarlo il passo delegato esistente, che
esegue gia' tutto `test_executor_birth_runner_linux_real.py`. La prova G4-A e'
stata aggiunta a quel file e chiama lo stesso helper usato dalla cella
portatile. Quando il passo esistente imposta
`METNOS_REQUIRE_REAL_BIRTH_LINUX=1`, la nuova prova rende obbligatoria anche la
sandbox executor e trasforma ogni indisponibilita' in errore.

L'helper prepara sotto la propria radice temporanea una copia byte-per-byte dei
soli `admitted_module_v1.py` e `code_file_paths.py`, quindi usa la capacita'
relocabile gia' prevista da `sandbox.wrap_command()`. Bubblewrap non deve piu'
attraversare la home protetta del runner. Non viene concesso alcun percorso
host aggiuntivo: la prova richiede `--unshare-net`, il montaggio `--ro-bind`
della dipendenza, il montaggio `--ro-bind` della runtime temporanea e l'assenza
del percorso runtime sorgente dal comando. Windows resta eseguito dalla suite
generale sul runner Windows autorevole.

La verifica locale mirata e' verde con sandbox disabilitata; con la sandbox
ordinaria produce una prova verde e la sola non applicabilita' attesa sul
confine Linux non delegato. L'intera suite portatile isolata e' verde con 325
prove verdi, 23 non applicabili e zero errori.

## Chiusura pubblica G4-A

La correzione finale e' nel commit sorgente `51f55291` e nel commit pubblico
`9ff7040`. Il ciclo GitHub `33160774194` e' interamente verde: sei lavori
posseduti, suite generale Linux, suite generale Windows e riepilogo finale. La
prova Linux delegata ha eseguito nello stesso servizio sia Birth sia la nuova
porta autenticata. G4-A e' quindi chiuso con zero errori pubblici.

## Passo successivo dopo G4-A

Iniziare G4-B+C: rimuovere le cinque autorita' di firma dirette, classificare
una sola volta l'inventario risultante e portare la guardia chiusa a zero. Non
iniziare F5 o F6.

Non rigenerare ancora l'inventario. Non toccare
`closed_build_enforcement()`: deve restare `False` per tutto il gruppo 4.

## Arresto di analisi prima di G4-B+C

Il riesame precedente alla modifica ha trovato due lacune che renderebbero
insicura una sostituzione meccanica delle cinque firme dirette.

Il generatore incorporato dichiara oggi file come
`../../recurring_tasks.py`. Birth rifiuta correttamente questi percorsi perche'
un candidato deve essere un albero chiuso; il codice di rifiuto riprodotto e'
`candidate_path_invalid`. Il ramo `--sign` gia' presente per l'archivio attivo
e' quindi nominale, ma non puo' pubblicare i candidati correnti. G4-B deve
prima rendere portabile il contenuto attestato e verificare che il runtime usi
esattamente quei byte. Copiare un file nel candidato continuando a eseguire un
file esterno non autenticato sarebbe un falso risultato verde.

La guardia chiusa contiene inoltre una lacuna indipendente: `retire` e
`publish_localization` non appartengono a
`BIRTH_CLOSED_LEGACY_CAPABILITIES`. Un nuovo ambito non compilato che usa una
di queste capacita' puo' quindi passare senza rilievi. Prima del congelamento
occorre provare che le due capacita' esistano soltanto negli ambiti compilati e
che gli ambiti dedicati non possano assorbire una seconda mutazione.

Questi fatti sono cause delimitate, non errori di una modifica tentata.

## Stato G4-B+C durante lo sviluppo

Il prerequisito dei contratti incorporati e' completato nel commit sorgente
`dd2afda0`: ogni candidato contiene `implementation.py.src`, il manifesto
attesta quel file chiuso e il caricatore rifiuta il contratto se i byte non
coincidono con il modulo runtime eseguito. Il ramo di firma diretta del
generatore e' stato eliminato.

Il percorso iniziale dell'installatore e' ora implementato localmente, ma non
ancora certificato ne' pubblicato. La fase 3 prepara le autorita' e invia ogni
contratto iniziale a un bundle Birth privato, legato a una radice shadow
esplicita. Prima dell'attivazione vengono riletti generazione, ricevuta Birth,
identita' della richiesta e report durevole. Il bundle privato non viene
installato come runtime produttivo; il bundle produttivo viene avviato soltanto
dopo l'attivazione.

Una prova end-to-end temporanea ha individuato una causa reale precedente:
il clock del bundle conservava i microsecondi mentre le ricevute firmate hanno
precisione al secondo. Il clock viene ora normalizzato una sola volta alla
costruzione del bundle. Dopo la correzione sono verdi 39 prove mirate: 24 della
fase 3, 14 del bootstrap e una prova completa di pubblicazione, verifica del
report e attivazione iniziale. Restano da completare la guardia chiusa,
l'inventario e la verifica finale; nessuna modifica di questo blocco e' ancora
su GitHub.

La guardia chiusa e l'inventario sono ora completati localmente. Le sedici
eccezioni compilate ammettono ciascuna il proprio insieme esatto di capacita';
`retire` e `publish_localization` sono rifiutate fuori dagli ambiti compilati e
un'eccezione non puo' assorbire una seconda autorita'. Il vecchio sottoprocesso
di firma di `manifest_refactor` e' stato sostituito dalla chiamata statica
offline. Sulla materializzazione esatta dell'indice Git, guardia normale e
guardia chiusa hanno zero rilievi e il rendering dell'inventario e' identico
byte per byte. Tutte le 56 prove della guardia sono verdi. Restano la verifica
mirata complessiva, una sola suite portatile completa e la prova pubblica. Le
righe seguenti registrano il loro esito.

La verifica locale finale di G4-B+C e' conclusa. I commit produttivi sono
`dd2afda0`, `5c57aeaa` e `8f501197`. La politica
`closed_build_enforcement()` resta `False`, come richiesto per tutto il gruppo
4. La guardia normale, la guardia `--birth-closed` e il confronto byte per byte
dell'inventario sono tutti a zero errori sul commit candidato.

Il primo insieme mirato ha trovato una sola configurazione di prova del
caricatore rimasta al vecchio percorso `../../recurring_tasks.py`. Il prodotto
rifiutava correttamente quel formato. La configurazione e' stata allineata
all'involucro `implementation.py.src` nel commit `61e0f4b3`; l'insieme mirato
finale conta 150 prove verdi.

La suite portatile completa ha poi trovato 13 errori con una sola causa: due
funzioni di costruzione chiedevano l'inventario vivo e, su una macchina gia'
attiva, leggevano correttamente il catalogo reale dell'utente. Ora chiedono
esplicitamente `inventory_authoring_manifests()` nel commit `44affb51`. Due
prove discriminanti e la successiva suite completa confermano la diagnosi. Il
risultato finale e' 326 prove verdi, 23 non applicabili e zero errori. Questa
correzione riguarda soltanto l'isolamento delle prove e non modifica il
prodotto.

Il candidato locale G4-B+C e' quindi completo. Manca soltanto la pubblicazione
su `main` e la matrice GitHub Linux/Windows interamente verde; prima di tale
risultato il gruppo 4 non e' dichiarato chiuso.

`PC-ROBERTO` e' visibile come host Codex locale, ma non espone un progetto
Metnos salvato. Il trasporto remoto del prodotto accetta soltanto executor
firmati e non permette di lanciare una suite arbitraria. Non verra' creato un
executor di test privilegiato. Finche' un clone Metnos non viene configurato
come progetto Codex sul PC, la matrice GitHub Windows resta la prova
autorevole.

## Ottimizzazione preliminare del gruppo 5

Il gruppo 5 del §23.6 non e' ancora F5. Completa il coordinatore F4: prepara le
tre autorita' distinte `closed_distribution_v1`, `ownership_cutover_v1` e
`ownership_head_v1`, installa i registri posseduti da `root`, riusa la fabbrica
sigillata di riattestazione, acquisisce la prova canonica di manutenzione e
gestisce la ripresa oltre il punto di non ritorno.

Il perimetro e' medio e viene ridotto a due incrementi sorgente:

1. predisposizione delle tre autorita' e verifica di separazione, proprieta' e
   registri;
2. composizione del coordinatore e una prova integrata di arresto e ripresa
   prima e dopo il punto di non ritorno.

Le primitive crittografiche e di archivio gia' provate non saranno ricoperte
con copie di test equivalenti. Restano obbligatorie una prova mirata per ogni
nuovo confine, un solo attraversamento produttivo del coordinatore e una sola
matrice pubblica finale. La stima prudente e' 3-5 ore, esclusa l'attesa GitHub.
Il gruppo 5 non inizia prima della chiusura pubblica verde del gruppo 4.

## Prossimo passo unico corrente

Pubblicare una sola volta il candidato G4-B+C su `main`, attendere tutti i
lavori GitHub e aggiornare questo file con commit pubblico e ciclo. Se un
lavoro e' rosso, fermarsi e diagnosticare il primo errore reale prima di ogni
nuova modifica.

## Regole operative

- commit piccoli solo su `main`, con footer
  `RM-0008-Status: candidate-not-certified`;
- dopo ogni pubblicazione attendere matrice pubblica tutta verde;
- nessuna seconda correzione se la prima fallisce: prima nuova diagnosi;
- aggiornare questo file dopo nuova evidenza, correzione e risultato pubblico;
- usare il minimo insieme probatorio: guardia specifica, prove mirate gia'
  esistenti e una sola suite completa prima della pubblicazione; non creare
  nuovi impianti di test se una prova esistente osserva gia' lo stesso rischio;
- non iniziare F5 o F6.
