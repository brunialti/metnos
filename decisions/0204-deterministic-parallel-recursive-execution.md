---
id: 0204
title: Visite ricorsive parallele, deterministiche e governate dal runtime
date: 2026-07-30
status: accepted
area: runtime | executor | filesystem | web
related: [0098, 0100, 0103, 0193, 0196]
---

# 0204 - Visite ricorsive parallele e deterministiche

## Problema

Gli executor ricorsivi avevano implementazioni separate: `os.walk`, `rglob`,
code locali e pool dimensionati direttamente dalla CPU. Un limite applicato
durante la visita poteva inoltre dipendere dall'ordine di completamento dei
thread e trasformare un risultato parziale in un conteggio apparentemente
completo. Aumentare soltanto un numero di worker avrebbe moltiplicato la
contesa e aggirato la politica centrale di ADR 0196.

## Decisione

`runtime/parallel_walk.py` è il visitor comune per gli alberi del filesystem.
Il chiamante passa funzioni senza stato condiviso:

- `accept(path, kind, depth)` seleziona un elemento;
- `transform(path, kind, depth, dir_entry)` costruisce il valore restituito;
- `descend(path, depth)` pota in modo esplicito un sottoalbero.

La modalità completa divide il dominio per directory. Le directory scoperte
entrano in una frontiera condivisa e i worker liberi prelevano il prossimo
task disponibile: è un bilanciamento dinamico adatto anche ad alberi molto
sbilanciati. Sono ammessi al massimo quattro task pendenti per worker. Il
completamento è non deterministico, ma la ricomposizione finale usa una chiave
di percorso stabile, quindi l'output non dipende dallo scheduling.

Quando `max_items` è positivo, la visita procede per livelli e ordina ogni
livello prima del taglio. Il limite è così riproducibile. `max_items=0` indica
una visita completa; `max_depth=None` indica profondità illimitata. I link
simbolici possono essere restituiti, ma non vengono mai seguiti. Errori di
lettura e callback sono dati espliciti e rendono `source_complete=false`.

Il runtime analizza una volta l'ambiente dell'istanza (CPU visibili e tetto
operativo `max_workers`) e ne deriva il budget totale. Il numero consegnato a
un executor proviene esclusivamente da `executor_workers.assigned_workers()` e
non può superare quel totale. Classe firmata, argomento o variabile specifica
del dominio possono soltanto ridurlo. L'effettivo fan-out è dinamico: non
supera il numero dei task disponibili e i thread sono creati pigramente. I manifest
ammessi dichiarano classe 3, effetto e classe di risorsa, con prova di
equivalenza seriale/parallela.

`parallel_map_ordered` riusa lo stesso nucleo di budget e ricompone i risultati
nell'ordine d'ingresso. Serve per il secondo stadio indipendente, per esempio
il conteggio LOC o gli aggregati di directory.

## Copertura dell'audit

Sono migrati al visitor comune tutti gli executor che esplorano ricorsivamente
il filesystem:

- `find_files` e `find_dirs`, attraverso `runtime/backends/files/local.py`;
- `list_dirs`;
- `compute_files_loc`;
- `create_images_indices`;
- `find_files_hash`.

`find_urls` è anch'esso ricorsivo, ma il suo albero è una frontiera di URL con
robots, rate limit e semafori per host. Conserva quindi la BFS specializzata a
livelli di ADR 0098; il fetch di ogni livello usa il medesimo budget centrale,
con override solo riduttivi e ricomposizione ordinata.

Le cancellazioni ricorsive non sono state parallelizzate: l'ordine bottom-up è
parte dell'effetto distruttivo e non è equivalente a una visita read-only. Il
`walk()` di un messaggio MIME percorre invece una struttura già materializzata
e piccola, non una ricerca ricorsiva su una sorgente esterna.

## Limiti e potatura

Non si applica un branch-and-bound generico: per una ricerca esatta non esiste
un bound che permetta di scartare una directory senza osservarla. La potatura è
ammessa soltanto tramite `descend` e conoscenza del dominio. Per i duplicati,
la riduzione corretta avviene dopo la visita con dimensione e impronta
campionata, senza perdere candidati esatti (ADR 0205).

## Evidenza

- test del core su ordine, profondità, symlink, potatura, limiti e budget;
- test di regressione su conteggi oltre il vecchio limite di 1000 e ordinamento
  dell'intero insieme prima del limite di visualizzazione;
- prova ermetica di equivalenza seriale/parallela della BFS web;
- prove di nascita concorrenti: 36/36 sui sette executor ammessi;
- chiusura dello shim device verificata con `executor_workers.py`,
  `parallel_walk.py` e backend filesystem.

## Conseguenze

L'algoritmo è comune, il dominio resta nei callback e nessun executor sceglie
autonomamente il proprio pool. Aumentare il budget centrale raggiunge tutti i
consumer ammessi; ridurlo conserva lo stesso risultato. Un limite di output
non può più essere confuso con la completezza della sorgente.

La ripartizione simultanea del totale tra più utenti non è introdotta da
questa decisione. Se il carico concorrente multiutente lo richiederà, sarà un
pool centralizzato dell'istanza, non una somma di pool privati agli executor.
