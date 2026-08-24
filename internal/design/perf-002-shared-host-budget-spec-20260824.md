# PERF-002 — budget di rete condiviso per host

## Verifica di attualita'

Il difetto locale del pool di `read_urls_html` non era piu' aperto: il numero
di worker e' gia' calcolato come minimo fra job, tetto globale e capacita'
aggregata degli host distinti. I test dimostrano che otto URL dello stesso host
creano quattro worker, non otto.

Restava attuale il difetto trasversale. Ogni invocazione costruiva un proprio
`HostThrottle`; due turni contemporanei potevano quindi usare ciascuno l'intero
limite verso lo stesso host. Il limite dichiarato descriveva il batch, non la
pressione complessiva esercitata dal runtime.

## Alternative

1. Serializzare intere invocazioni. Respinta: due batch con host disgiunti non
   condividono alcuna risorsa esterna e perderebbero parallelismo senza motivo.
2. Usare una chiave unica nello scheduler. Respinta: un batch puo' contenere
   piu' host; una chiave singola non rappresenta la contesa reale.
3. Coordinare gli slot sull'origine concreta. Scelta: ogni acquisizione entra
   in una coda FIFO per host e parte soltanto entro il limite piu' restrittivo
   fra il lavoro gia' attivo e la richiesta candidata.

## Contratto implementato

`runtime/host_throttle.py` mantiene un coordinatore condiviso dal processo
runtime. Tutte le istanze `HostThrottle`, comprese quelle create da executor o
turni diversi, usano lo stesso stato per host normalizzato. Host distinti hanno
budget indipendenti.

Limiti diversi possono convivere senza una tabella per executor. Un lavoro con
limite piu' prudente attende che il numero di acquisizioni attive sia compatibile
con il proprio contratto; quando entra, quel limite concorre al tetto effettivo.
La coda FIFO impedisce che richieste successive e piu' permissive lo affamino.
La soluzione dipende soltanto da host e limite richiesto, non da query, dominio,
lingua o nome dell'executor.

Il confine e' sufficiente per il deployment supportato: gli executor di rete in
questione hanno placement server e l'istanza HTTP e' un unico processo. Un
futuro deployment multi-processo dovra' promuovere lo stesso contratto in un
broker condiviso; non viene simulata oggi una garanzia fra processi inesistenti.

## Verifica

- due istanze indipendenti con limite 2 non superano due acquisizioni totali;
- due host distinti procedono contemporaneamente con limite 1;
- un waiter con limite piu' restrittivo precede il lavoro permissivo arrivato
  dopo;
- suite `read_urls_html`, ricorsione `find_urls` e coordinatore: 31 test verdi.
