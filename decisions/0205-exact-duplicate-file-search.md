---
id: 0205
title: Ricerca esatta e completa dei file duplicati
date: 2026-07-30
status: accepted
area: executor | filesystem | cache | routing
related: [0182, 0193, 0196, 0204]
---

# 0205 - Ricerca esatta dei file duplicati

## Problema osservato

Nel turno `914e50fc`, la richiesta «Trova i file di immagini duplicati nella
cartella Immagini del server» produsse il piano
`find_files -> get_files -> compute_entries(count)`. La sorgente conteneva
31.669 file secondo il vecchio piano, ma `find_files` ne mostrò 1000 e
`get_files` ne elaborò 200. Il risultato finale fu `0`: i limiti erano troppo
stretti e, soprattutto, la pipeline non confrontava il contenuto.

## Decisione

`find_files_hash` è l'operazione atomica per i duplicati esatti. È generica per
qualunque tipo di file e non cancella né modifica i risultati. Il routing è
descritto nel manifest bilingue e nel catalogo semantico; non dipende dalla
frase del turno.

La sorgente è completa per default:

- `max_files=0` e `max_depth=0` significano nessun limite;
- `max_results` limita soltanto le righe presentate dopo il calcolo;
- una scansione parziale esiste soltanto se il chiamante imposta
  esplicitamente un `max_files` positivo;
- `source_complete`, `scan_truncated` e `results_truncated` distinguono le due
  forme di limite.

## Algoritmo esatto

1. Il visitor parallelo raccoglie firma `stat` e percorso dei file ammessi.
2. Soltanto le classi con uguale dimensione sopravvivono.
3. Si legge una piccola impronta BLAKE2b di inizio, centro e fine. Essa è solo
   un filtro negativo: una collisione non può dichiarare un duplicato.
4. Si calcola SHA-256 completo su ogni candidato rimasto.
5. Solo dimensione e SHA-256 completo uguali formano un gruppo.

Il risultato indica gruppi, file nei gruppi, copie ridondanti e byte
ridondanti; ogni riga collega una copia a un originale deterministico. Errori o
file cambiati durante la lettura sono espliciti e impediscono di dichiarare
completa la sorgente.

## Routing e piani già memorizzati

Il collaudo successivo al primo deploy (`ff3f8054be214fac`) ha mostrato una
seconda causa indipendente: L0 poteva riusare il vecchio piano perché l'intento
era classificato come `images`, mentre il nuovo executor appartiene alla
famiglia canonica `files`. La firma della famiglia vedeva soltanto fratelli con
lo stesso nome-oggetto e non riconosceva il rapporto già dichiarato dal
vocabolario tra contenuti specializzati e carrier filesystem.

La correzione non riconosce la frase «immagini duplicate». `pool_sig` espande
gli oggetti presenti in `FILE_CARRIER_OBJECTS` verso i carrier canonici
`files` e `dirs`; una nuova capability filesystem può quindi invalidare una
decisione precedente per `images` o `texts`. La firma resta indipendente dalla
query, dall'utente, dalle affinity e dalla lingua, perciò non trasforma L1 in
una cache personale. Il contestuale incremento di `ROUTING_EPOCH` rende
inutilizzabili anche le decisioni registrate prima di questa relazione.

## Budget dinamico e cache

Le impronte brevi usano fino al budget centrale assegnato. Lo streaming
completo si adatta alla dimensione media: al massimo 8 stream da 1 MiB in su,
16 da 256 KiB in su e il budget assegnato per file piccoli. Un override locale
può soltanto ridurre il valore. Questo evita che più thread peggiorino un NAS
già saturo.

La cache SQLite è best-effort e non partecipa alla correttezza. Una voce è
riusata soltanto se coincidono dimensione, `mtime_ns`, `ctime_ns`, device e
inode. Le chiavi dei percorsi sono SHA-256 e nessun percorso in chiaro viene
persistito. Ogni utente ha un file di cache separato, individuato da un hash
opaco di `_actor_email` o `_actor`; una cache calda di un utente non diventa
evidenza o stato osservabile di un altro.

La cache tecnica non equivale a una modifica dei file dell'utente. Il manifest
dichiara `metnos:cache` e il sandbox monta in scrittura soltanto
`PATH_USER_CACHE/file_hashes`; la capability è ammessa anche in modalità
ReadOnly e non concede accesso al resto della cache dell'account di servizio.

## Alias di percorso e sandbox

Il turno live `9e950e9305a04ebb` scelse correttamente `find_files_hash`, ma
fallì su `/server/Immagini`: l'executor conosceva gli alias bilingui, mentre il
sandbox veniva costruito prima e non vedeva la directory NAS a cui l'alias
avrebbe condotto.

La correzione è generale e a privilegio minimo. Un manifest può indicare un
argomento tipizzato con `fs:read hint=["arg:<nome>"]`. Prima di avviare
Bubblewrap, il runtime prova il percorso letterale e, se manca, applica il
resolver centrale degli alias utente. Soltanto il percorso esistente scelto
viene montato in sola lettura; altri argomenti, alias non registrati e percorsi
inesistenti non concedono alcun bind. Il manifest di `find_files_hash` usa
`arg:base_path`, non una radice filesystem globale.

## Misura sul corpus reale

La scansione completa di `/server/Immagini` ha osservato 31.908 immagini:
8.424 candidate per dimensione, 5.667 candidate a SHA-256 completo, 2.694
gruppi duplicati, 5.663 file nei gruppi, 2.969 copie ridondanti e
6.355.948.038 byte ridondanti. Sul NAS la prima lettura è limitata dalla banda
e ha richiesto circa 103-116 secondi nelle varianti misurate; con cache valida
la stessa verifica completa ha richiesto circa 5,03 secondi. Per questo il
budget è adattivo e la cache è persistente, ma la prima esecuzione non viene
falsamente abbreviata.

## Conseguenze

La richiesta originale richiede un solo executor e produce un conteggio
esatto dell'intero corpus anche se la chat mostra solo i primi risultati. Un
futuro consenso per costi eccezionali può governare scansioni molto più grandi,
ma non deve reintrodurre cap silenziosi o trasformare un output parziale in una
risposta completa.
