# RM-0008 — passaggio di consegne del gruppo 5

## Stato corrente — 28/8/2026

Worktree: `/tmp/metnos-rm0008-a-only`, ramo unico `main`. Non toccare
`/opt/metnos` e non creare rami.

Il gruppo 4 e' chiuso con commit pubblico `a4d2dba` e ciclo GitHub
`33165938001`, nove lavori su nove verdi. Il commit sorgente di partenza e'
`9fd7edf4`. RM-0008 resta `active`; `closed_build_enforcement()` resta `False`.

Il gruppo 5 e' in analisi esecutiva. Il piano completo e'
`internal/reports/rm0008-gruppo5-piano-ottimizzato.md`.

## Risultato dell'analisi e della revisione avversariale

La suddivisione in due incrementi e' confermata:

1. G5-A: tre autorita' e registri root-owned separati, cold load e correzione
   del registro multipurpose della catena;
2. G5-B: fabbrica sigillata di riattestazione, prova canonica completa e
   coordinatore recuperabile fino a `CERTIFICATE_PUBLISHED`.

Prima della codifica sono state delimitate cinque lacune reali:

- `_assemble_reattestation_core()` legge il campo inesistente
  `birth.publisher_options`;
- la richiesta produttiva permette al chiamante di scegliere la
  `ProducerReceipt`;
- le tre autorita' non hanno ancora formati o loader installati;
- `OwnershipChainStore` usa un solo registro per cutover e testa;
- `prove_stack_stopped()` perde due campi necessari alla prova canonica e non
  certifica da solo l'insieme completo.

Le unita' installate non hanno ancora il controllo preliminare ownership
dominante. Il coordinatore del gruppo 5 deve quindi rifiutare il punto di non
ritorno sul sistema produttivo finche' il gruppo 6 non consegna una attestazione
sigillata di quel prerequisito. Il gruppo 5 prova il recupero oltre il punto di
non ritorno soltanto in un ambiente isolato e non dichiara un cutover installato.

## Prossimo passo unico

G5-A e' implementato nel worktree ma non e' ancora committato. Sono presenti:

- codec e cold loader di tre registri a scopo singolo;
- tre chiavi private separate e controllo sui byte pubblici contro autore,
  Admission e tutti i Producer Birth;
- provisioner recuperabile con file esclusivi, checkpoint monotoni, fsync,
  pubblicazione finale e nuova apertura a freddo;
- due registri obbligatori e disgiunti in `OwnershipChainStore`;
- proprieta' `root:root`, modalita' e diniego dell'identita' di servizio nella
  cella Linux delegata gia' esistente;
- cinque nuovi store owner compilati nella guardia e nell'inventario.

Le prove locali correnti sono: 67 prove autorita'/catena verdi; 77 regressioni
dei soli confini attraversati verdi e 4 prove Linux reali non applicabili nella
sessione locale non delegata. Guardia normale e chiusa sono entrambe a zero e
il rendering e' byte-identico all'inventario. La matrice completa non viene
ripetuta ora: verra' eseguita una volta sola dopo G5-B, alla chiusura del gruppo.

La revisione avversariale del diff G5-A e' conclusa e non rileva blocchi
residui. Il prossimo passo unico e' registrare il commit sorgente G5-A su
`main`, quindi aprire G5-B senza ancora eseguire la matrice pubblica completa.

L'ultimo giro ha corretto due rilievi circoscritti: la fixture Windows non
invoca piu' il provisioner Linux e il codec rifiuta tipi numerici JSON o Base64
non canonici. I riproduttori del revisore sono ora tutti rifiutati; il verdetto
finale e' `APPROVATO`.

## Regole operative

- commit piccoli soltanto su `main`, con footer
  `RM-0008-Status: candidate-not-certified`;
- applicare a ogni incremento il budget minimo sufficiente definito in
  `internal/reports/rm0008-regole-di-lavoro-fra-gruppi.md` §8: una sola famiglia
  di prove per rischio distinto, nessuna ripetizione delle suite gia'
  certificate e matrice completa soltanto alla chiusura;
- una sola pubblicazione pubblica alla chiusura locale del gruppo;
- nessuna correzione successiva senza una nuova diagnosi se una prova fallisce;
- aggiornare questo file dopo ogni nuova evidenza, commit e risultato pubblico;
- non iniziare il gruppo 6, F5 o F6 prima della chiusura pubblica del gruppo 5.
