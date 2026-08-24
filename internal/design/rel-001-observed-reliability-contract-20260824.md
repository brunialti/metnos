# REL-001 — contratto di affidabilita' osservata

## Decisione

La fonte primaria e' il TurnLog canonico, non un secondo logger. Ogni nuovo
record dichiara versione prodotto e origine del routing; l'analizzatore legge
soltanto campi strutturali e non esporta richieste, risposte, argomenti, path,
utenti o identificativi di turno.

La tassonomia terminale e' chiusa: `completed`, `partial`, `failed`,
`awaiting_input`. Un falso successo intercettato dal runtime conta come
fallimento di affidabilita', anche se il messaggio mostrato all'utente e' stato
corretto. Un esito parziale richiede un marker `partial` oppure la compresenza
strutturale di effetti positivi e step falliti; un troncamento dichiarato non e'
automaticamente un fallimento.

Il dominio deriva dalla grammatica canonica del nome executor. Un turno che
tocca piu' oggetti e' `multi-domain`; una conversazione senza executor resta
`conversation`. Non esiste una mappa per nome di executor.

## Origine dei guasti

La prima versione distingue solo cio' che la fonte dimostra:

- `executor`: almeno uno step ha restituito `ok=false`;
- `planning`: il turno e' fallito prima di produrre step;
- `runtime`: il turno e' fallito dopo step che non dichiarano il fallimento;
- `none`: nessun fallimento terminale.

La causa esterna non viene dedotta dal testo o da elenchi di errori. Per
separare provider, rete e altri servizi esterni, gli executor dovranno emettere
in futuro un campo tipizzato di origine. UI client-side e sessioni vive orfane
richiedono fonti lifecycle separate e restano dichiarate come gap di copertura.

## SLO

Il sistema pubblica denominatori, completion rate, falsi successi, parziali,
timeout, recovery e p95 per dominio e versione. Le soglie non sono inventate su
un campione storico: verranno ratificate dopo un periodo di osservazione che
copra almeno un ciclo di release e un volume sufficiente per dominio. Il
vincolo gia' assoluto e' falsi successi non intercettati pari a zero; il campo
attuale misura quelli bloccati dalla guardia.

La baseline del 24 agosto contiene 3.343 record dal 1 luglio: 3.070 terminali
eleggibili, completion rate 0,70, 120 falsi successi intercettati e 10 timeout.
Tutti i record sono anteriori al nuovo campo versione e risultano quindi
`pre-telemetry`; non possono sostenere un confronto fra release.

## Verifica

Il report ha schema `metnos.reliability-snapshot/1`, input limitato per riga,
gestione esplicita dei record malformati e output atomico. I test provano
classificazione, dominio canonico, privacy, finestra temporale e record corrotti.
La raccolta plurimensile resta necessariamente in osservazione: non puo' essere
marcata completata nel giorno in cui la telemetria di versione viene introdotta.
