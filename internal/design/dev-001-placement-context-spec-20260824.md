# DEV-001 — identita' e durata della destinazione di esecuzione

Stato: analisi di dettaglio completata il 24 agosto 2026; implementazione
autorizzata dalla richiesta di chiudere tutti i TODO.

## Evidenza e attualita'

Il difetto e' ancora attuale. I turni `1a07c3b89956446a` e
`bb35aae21d224835` hanno eseguito la stessa lettura della salute su due macchine
diverse: `temperatura cpu metnos` e' stata inviata a `PC-ROBERTO`, mentre
`temperatura cpu server metnos` e' rimasta sul server. Il primo risultato porta
la ricevuta remota e l'hostname Windows; il secondo porta l'hostname Linux del
server.

La causa non e' il planner: entrambi i turni hanno scelto correttamente
`get_processes(include_health=true)`. La deviazione avviene prima del planner in
`runtime/target_device.py`.

## Causa

La destinazione detta "ultima destinazione" non descrive oggi l'ultima
esecuzione reale. `chat_target_store` viene aggiornato soltanto quando la query
contiene un riferimento esplicito. Una scelta remota puo' quindi sopravvivere
per giorni e attraversare richieste locali eseguite nel frattempo. Inoltre la
chiave e' soltanto `canale:attore`: non distingue conversazioni diverse dello
stesso utente.

Il resolver non possiede infine un'identita' dati-driven del server. Riconosce
alcune locuzioni linguistiche inline, ma il nome dell'istanza non e' trattato
come identita' del server. Aggiungere una condizione per la frase osservata
sarebbe una correzione ad hoc e non risolverebbe altri nomi o installazioni.

## Contratto scelto

1. La memoria della destinazione e' contesto conversazionale, non una
   preferenza permanente. La chiave include proprietario autenticato, canale,
   attore e conversazione. Quando il canale non dispone di una conversazione,
   resta circoscritta alla sua identita' di chat.
2. Il contesto scade dopo un intervallo configurabile e limitato. Il valore
   predefinito coincide con la finestra breve gia' usata da Metnos per i
   riferimenti conversazionali; un record scaduto equivale a nessun record.
3. Dopo un turno con almeno un executor eseguito, la memoria rappresenta la
   collocazione osservata: un'esecuzione remota conserva il dispositivo; un
   turno interamente locale riporta il contesto al server. Risposte dirette e
   fallimenti senza esecuzione non cambiano la destinazione.
4. Il server espone un insieme di alias di istanza configurabili. L'hostname
   reale e il nome del prodotto sono valori iniziali, non rami nel resolver.
   Un alias nudo prevale sulla memoria soltanto quando la richiesta riguarda
   salute o caratteristiche della macchina. Un dispositivo nominato con una
   forma esplicita prevale sull'alias debole: `installa Metnos sul PC-X` resta
   destinato a `PC-X`.
5. La decisione continua a essere deterministica, indipendente dai nomi degli
   executor e dalle applicazioni. Lessico traducibile e alias di identita'
   restano dati separati dall'algoritmo.

## Compatibilita' e migrazione

Le vecchie righe `canale:attore` non vengono riutilizzate dalle nuove chiavi e
quindi decadono senza una migrazione rischiosa. Lo schema SQLite resta
compatibile. I dialoghi sospesi conservano gia' la destinazione esplicita nella
richiesta originale e non dipendono dalla memoria per autorizzare il resume.

## Verifiche obbligatorie

- nessun record, record scaduto e record fresco;
- isolamento per proprietario, canale e conversazione;
- aggiornamento dopo esecuzione locale e remota realmente osservata;
- alias server su domanda hardware, alias come normale nome di dominio e
  alias insieme a un dispositivo esplicito;
- dispositivo offline, nomi duplicati, path POSIX/Windows e resume dei gate;
- riproduzione dei due turni segnalati senza cambiare la query.

## Ripristino

La modifica e' separabile: il nuovo ambito delle chiavi, la scadenza e il
riconoscimento degli alias possono essere disattivati individualmente
ripristinando il contratto precedente. Nessuna riga utente viene cancellata.
