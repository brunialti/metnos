# Pulizia controllata — passaggio del 24 agosto 2026

## Confine

L'inventario ha separato codice versionato, artefatti di build, worktree di
agenti, dati di test e collegamenti verso risorse locali. Build Rust, virtualenv,
`node_modules`, worktree concorrenti, dati E2E, modelli e stato utente non sono
stati rimossi: eta', nome o presenza sul disco non dimostrano che siano orfani.

## Rimozione verificata

La chiusura di EXE-001 ha reso irraggiungibile il vecchio generatore di manifest
nel promoter. Quel blocco costruiva un secondo formato legacy incompatibile con
lo standard e duplicava la fonte canonica ora condivisa con Synt. La ricerca dei
simboli ha confermato che `_build_manifest_toml` e i quattro serializzatori TOML
privati non avevano call-site. Sono state eliminate 109 righe, insieme alla
descrizione ormai falsa del flusso.

Il promoter conserva una sola sorgente: il candidate standard materializzato da
Synt. La promozione modifica esclusivamente il lifecycle tramite la transizione
canonica, firma, prova l'ammissione del loader e ripristina il candidate esatto
in caso di errore.

## Verifica

- riferimenti ai cinque helper rimossi: zero;
- suite promoter, review form e contratto generato: 31 test verdi;
- nessun file, dato o collegamento fuori da questa famiglia e' stato cancellato.

La pulizia generale resta un processo ripetibile, non un'autorizzazione a
cancellare automaticamente artefatti di build o stato operativo. Nuove
rimozioni richiedono la stessa prova di non raggiungibilita' e una suite mirata.
