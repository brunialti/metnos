# RM-0008 · revisione A del recupero pubblicazione · quarto giro

Data: 1 settembre 2026  
Agente: A  
Ancora revisionata: `a0c0666f`  
Stato: **MODIFICHE_RICHIESTE**

## Esito

Le 27 prove di B sono verdi. Le quattro prove indipendenti del giro precedente
sono ora verdi. Resta un solo bordo di durabilità, riprodotto dalla stessa
sonda:

```text
PASS authorization is inventory-only
PASS partial cleanup is resumable
PASS receipt binds the exact container
PASS receipt distinguishes preparation from commit
FAIL idempotent retry makes the final removal durable idempotent retry returned before syncing the parent directory
```

## R15 — la ripresa dopo l'ultimo `rmdir` non sincronizza la radice

La sonda interrompe l'esecuzione dopo la rimozione del nome ritirato ma prima
del relativo `fsync(fd_radice)`. La ricevuta è già `committed`. Il tentativo
successivo osserva entrambi i nomi assenti e ritorna subito successo dal ramo
idempotente, senza rendere durevole la rimozione che il tentativo precedente
non aveva sincronizzato.

Correzione minima: nel ramo «entrambi assenti + ricevuta committed», eseguire
`fsync(fd_radice)` prima di restituire l'esito. Se il `fsync` fallisce, il giro
deve fallire e restare ripetibile.

## Criterio di accettazione

- Le 27 prove di B restano verdi.
- Le cinque prove indipendenti diventano verdi senza indebolirle.
- `git diff --check` resta pulito.
- Nessuna applicazione al negozio reale.

