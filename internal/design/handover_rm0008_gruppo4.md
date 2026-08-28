# RM-0008 — passaggio di consegne del gruppo 4

## Stato corrente — 28/8/2026

Worktree: `/tmp/metnos-rm0008-a-only`, ramo unico `main`. Non toccare
`/opt/metnos` e non creare rami.

I gruppi 2 e 3 sono chiusi. Ultimo commit sorgente: `d510ef50`; ultimo commit
pubblico: `57ec5a9`; ciclo GitHub Actions `33154494801`: otto lavori su otto
verdi. RM-0008 resta `active`.

Il gruppo 4 e' soltanto la chiusura statica F4 del §23.6.4. Il piano completo
e' `internal/reports/rm0008-gruppo4-piano-ottimizzato.md`.

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

La correzione e' ora implementata e non ancora committata. Il simbolo leggero
e' `admitted_code_dependency_projection_v1()`; `agent_runtime` lo consuma senza
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

## Prossimo passo unico

Committare la sola estrazione descritta sopra, pubblicare un solo incremento
correttivo su `main` e attendere tutti i lavori GitHub verdi. Non iniziare
G4-B+C prima di quel risultato.

Non rigenerare ancora l'inventario. Non toccare
`closed_build_enforcement()`: deve restare `False` per tutto il gruppo 4.

## Regole operative

- commit piccoli solo su `main`, con footer
  `RM-0008-Status: candidate-not-certified`;
- dopo ogni pubblicazione attendere matrice pubblica tutta verde;
- nessuna seconda correzione se la prima fallisce: prima nuova diagnosi;
- aggiornare questo file dopo nuova evidenza, correzione e risultato pubblico;
- non iniziare F5 o F6.
