# RM-0008 — passaggio di consegne del gruppo 4

## Stato corrente — 28/8/2026

Worktree: `/tmp/metnos-rm0008-a-only`, ramo unico `main`. Non toccare
`/opt/metnos` e non creare rami.

I gruppi 2 e 3 sono chiusi. Ultimo commit sorgente: `d510ef50`; ultimo commit
pubblico: `57ec5a9`; ciclo GitHub Actions `33154494801`: otto lavori su otto
verdi. RM-0008 resta `active`.

Il gruppo 4 e' soltanto la chiusura statica F4 del §23.6.4. Il piano completo
e' `internal/reports/rm0008-gruppo4-piano-ottimizzato.md`.

G4-A e' implementato nel commit sorgente `3eeb4b1b`. Non e' ancora pubblicato.
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

## Prossimo passo unico

Registrare questo aggiornamento, pubblicare l'unico incremento su `main` e
attendere gli otto lavori GitHub verdi. Non iniziare G4-B+C prima di quel
risultato.

Non rigenerare ancora l'inventario. Non toccare
`closed_build_enforcement()`: deve restare `False` per tutto il gruppo 4.

## Regole operative

- commit piccoli solo su `main`, con footer
  `RM-0008-Status: candidate-not-certified`;
- dopo ogni pubblicazione attendere matrice pubblica tutta verde;
- nessuna seconda correzione se la prima fallisce: prima nuova diagnosi;
- aggiornare questo file dopo nuova evidenza, correzione e risultato pubblico;
- non iniziare F5 o F6.
