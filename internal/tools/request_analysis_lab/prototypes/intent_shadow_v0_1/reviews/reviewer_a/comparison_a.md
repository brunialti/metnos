# Confronto meccanico Reviewer A–Reviewer B

Data: 2026-08-12  
Modalità: confronto post-fase cieca, senza nuovo giudizio e senza modifiche agli oracle.

## Abbinamento e risultato

- Casi abbinati per indice, hash e testo: **120/120**.
- Indici, hash o testi mancanti/divergenti: **0**.
- `expected` identici: **102**; `expected` divergenti: **18**.
- Root: **107** accordi, **13** disaccordi.
- Reason (inclusa l'assenza del campo): **106** accordi, **14** disaccordi.
- Sequenze `operation/route`: **103** accordi, **17** disaccordi.
- Archi dati: **109** accordi, **11** disaccordi.
- Controlli di sistema: **120** accordi, **0** disaccordi; i due casi effettivamente `system_control` coincidono.
- Barriere: **119** accordi, **1** disaccordo.

Distribuzione root: A propone 94 `operation_graph`, 24 `unrepresentable` e 2 `system_control`; B propone rispettivamente 81, 37 e 2.

I 18 indici divergenti sono: `9, 11, 22, 24, 38, 39, 40, 44, 76, 77, 84, 85, 97, 100, 106, 109, 112, 113`. Query, documenti `expected` completi di A e B, proiezioni normalizzate e categorie descrittive sono riportati in `comparison_a.json`.

## Segnali di triage, non adjudicazione

- **16** divergenze sembrano errori applicativi coperti dalle regole o dai confini di route già fissati: `9, 11, 22, 24, 39, 40, 44, 76, 77, 85, 97, 100, 106, 109, 112, 113`.
- **2** sembrano richiedere una scelta semantica non esplicitata: indice `38`, precedenza fra `ambiguous_intent` e `outside_registry` quando entrambi sono applicabili; indice `84`, necessità o meno di un nodo esplicito di enumerazione prima di `get/images` su più corpus impliciti.

Queste etichette servono soltanto a instradare la revisione: non selezionano A o B come verità.

## Quattro controlli per revisore

- Query o hash identici tra le due serie: **0**.
- Duplicati semantici: A35/B36 (`mixed_root_kinds`) e A38/B37 (clausola indispensabile fuori registro).
- Sovrapposizione tematica, non duplicato: A36/B35 (proprietà della mutazione sotto `get/approval`, con strutture di ramo/dipendenze diverse).
- Distinti e senza controparte: A37 (ambito della negazione) e B38 (posizione storica non supportata).

Un `expected` generico identico di tipo `unrepresentable/outside_registry` non è stato considerato da solo prova di duplicazione semantica.

## Verifica

Il JSON passa i controlli interni: 120 casi abbinati, 102 + 18 = 120, quattro controlli unici per ciascun revisore e nessun errore registrato. Nessun oracle è stato modificato.
