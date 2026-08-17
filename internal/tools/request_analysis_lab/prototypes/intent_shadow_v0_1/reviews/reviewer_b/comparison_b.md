# Confronto meccanico post-cieco A/B

Confronto eseguito senza modificare né riadjudicare i due oracle. Tutti i 120 casi sono abbinati per indice; testo e SHA-256 coincidono in 120/120, senza duplicati o casi mancanti.

## Risultato

- `expected` identico: **102/120**; divergente: **18/120**.
- Radice identica: 107; divergente: 13.
- Reason identico: 106; divergente: 14. Nello scope dei 37 casi non rappresentabili in almeno un oracle: 23 accordi e 14 disaccordi.
- Sequenza di rotte operazione identica: 103; divergente: 17. Nello scope dei 94 grafi presenti in almeno un oracle: 77 accordi e 17 disaccordi.
- Controlli di sistema: 2 casi nello scope, entrambi identici.
- Struttura delle barriere: 3 accordi e 1 disaccordo nei 4 casi interessati; diverge il caso 77.
- Dipendenze `data_from`: 8 accordi e 11 disaccordi nei 19 casi interessati.
- Radici A: 94 grafi, 2 controlli, 24 non rappresentabili. Radici B: 81 grafi, 2 controlli, 37 non rappresentabili.

## Divergenze

La colonna “segnale” è solo triage: `i` indica una probabile diversa applicazione di regole o contratti già esistenti; `ii` indica una possibile scelta semantica nuova. Non seleziona un oracle vincente.

| Indice | Categoria | Expected A, in breve | Expected B, in breve | Segnale |
|---:|---|---|---|:---:|
| 9 | identificazione persona | `find/persons` | `get/persons` | i |
| 11 | identificazione persona | `find/persons` | `get/persons` | i |
| 22 | necessità del conteggio | `read/messages → compute/entries` | `read/messages` | i |
| 24 | store e composto chiuso | grafo issue/store/conteggio | `outside_registry` | i |
| 38 | precedenza del reason | `ambiguous_intent` | `outside_registry` | ii |
| 39 | rotte file qualificate GitHub | grafo README + ricerca web | `outside_registry` | i |
| 40 | store e composto chiuso | grafo store/commento/update | `outside_registry` | i |
| 44 | store e composto chiuso | `find/issues → write/files` | `outside_registry` | i |
| 76 | store e composto chiuso | `find/issues → write/files` | `outside_registry` | i |
| 77 | store, barriera e composto | grafo con barriera `approved` | `outside_registry` | i |
| 84 | decomposizione inventario corpus | `find/dirs → get/images` | `get/images` | i |
| 85 | rotta file qualificata GitHub | `read/files` | `outside_registry` | i |
| 97 | dominio noto come URL | `read/urls` | `outside_registry` | i |
| 100 | rotte file GitHub nel composto | grafo README + ricerca web | `outside_registry` | i |
| 106 | store e composto chiuso | grafo issue/store | `outside_registry` | i |
| 109 | albero repository GitHub | `list/dirs` | `outside_registry` | i |
| 112 | dominio noto come URL | `read/urls` | `outside_registry` | i |
| 113 | proiezione campi o operazione | `read/events → create/files` | `outside_registry` | ii |

Le due possibili scelte nuove sono: priorità fra `ambiguous_intent` e `outside_registry` quando entrambe le cause coesistono (38); confine fra proiezione di campi già strutturati e operazione `extract` indispensabile fuori registro (113). Le altre 16 divergenze sembrano risolvibili applicando fonti e regole già approvate, senza estendere il contratto.

## Quattro controlli per revisore

Non ci sono query o hash identici fra le due proposte.

- Duplicati semantici forti: A35 ↔ B36 (`mixed_root_kinds`); A38 ↔ B37 (clausola indispensabile fuori registro e divieto del sottografo parziale).
- Sovrapposizioni non duplicate: A36 ↔ B35 condividono la proprietà di ownership della barriera, ma stressano rispettivamente esiti fratelli e dipendenza dominante; A38 ↔ B38 condividono safe stop e `outside_registry`, ma usano meccanismi diversi.
- Target distinti: A37 isola negazione e assenza di mutazioni; A36 aggiunge un ramo `rejected` non vuoto; B35 aggiunge una dipendenza pre-barriera; B38 isola la posizione storica rispetto a quella corrente.
- Tre coppie hanno lo stesso JSON `expected`; soltanto le prime due sono classificate come duplicati semantici forti.

Il dettaglio completo contiene query, hash, entrambi gli `expected`, dimensioni divergenti e triage non vincolante in `comparison_b.json`.
