# RM-0008 — passaggio di consegne del gruppo 4

## Stato corrente — 28/8/2026

Worktree: `/tmp/metnos-rm0008-a-only`, ramo unico `main`. Non toccare
`/opt/metnos` e non creare rami.

I gruppi 2 e 3 sono chiusi. Ultimo commit sorgente: `baf9557e`; ultimo commit
pubblico: `57ec5a9`; ciclo GitHub Actions `33154494801`: otto lavori su otto
verdi. RM-0008 resta `active`.

Il gruppo 4 e' soltanto la chiusura statica F4 del §23.6.4. Il piano completo
e' `internal/reports/rm0008-gruppo4-piano-ottimizzato.md`.

Misura iniziale della guardia `--birth-closed`: **26 rilievi**:

- 5 vecchie autorita' di firma;
- 2 ambiti non classificati;
- 16 eccezioni compilate non riportate nell'inventario;
- 1 politica chiusa mancante;
- 1 proprietario Birth mancante;
- 1 voce stale.

## Prossimo passo unico

Eseguire G4-A: portare `undo_last_turn` e `find_persons_indices` sulla porta
autenticata `admitted_module_v1`, includendo preparazione del digest senza
firma diretta, pubblicazione tramite una vera intenzione Birth, prova sandbox e
prova di alterazione.

Non rigenerare ancora l'inventario. Non toccare
`closed_build_enforcement()`: deve restare `False` per tutto il gruppo 4.

## Regole operative

- commit piccoli solo su `main`, con footer
  `RM-0008-Status: candidate-not-certified`;
- dopo ogni pubblicazione attendere matrice pubblica tutta verde;
- nessuna seconda correzione se la prima fallisce: prima nuova diagnosi;
- aggiornare questo file dopo nuova evidenza, correzione e risultato pubblico;
- non iniziare F5 o F6.
