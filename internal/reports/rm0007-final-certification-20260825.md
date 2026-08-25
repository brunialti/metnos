# RM-0007 — Certificazione finale M0-M4

> Data `2026-08-25` · revisione candidata
> `8746c4f3c5f1b285c1f9d5c1df99b1e57e7ce665` · esito `green`

## Esito

Il deposito a generazioni è il confine produttivo unico dei contratti. Il
loader legge snapshot autenticati; firma, stato linguistico e manifest
diventano visibili insieme. La procedura operativa usa `sign.py publish` e il
percorso legacy non torna vivo dopo il marker.

## Cutover e ripetibilità

Il primo ciclo ha migrato l'installazione di riferimento da `legacy` ad
`active`: 122 contratti firmati e verificati, 122 binding autenticati, 122
executor caricati e nessun ritiro inatteso. Il rapporto durevole del cutover è
`contract_store_cutover.v1.json`; il censimento rigenerabile dei confini è
`internal/reports/rm0007-m4-boundary-inventory.json`.

Il secondo ciclo ha ripetuto la pubblicazione sul deposito già attivo, seguito
da riavvio controllato. La verifica centralizzata ha restituito `ok=true` e
`ready=true`, con parità catalogo `122/122`, nessun elemento mancante o
inatteso e tutti i componenti richiesti in stato valido.

## Certificazione

| Gate | Esito |
|---|---|
| suite runtime completa: 7.260 test e 1.166 subtest | green |
| prove portabili del deposito: 12 test | green |
| controlli documentali interni: 28 test | green |
| client Windows/Rust: 93 test | green |
| inventario manifest: 123 contratti, 0 problemi, 0 errori | green |
| due cicli del corpus di routing | green |
| due cicli produttivi e readiness finale | green |

Tutti i gate M0-M4 sono soddisfatti. ADR 0223 è accettata e non restano
attività RM-0007.
