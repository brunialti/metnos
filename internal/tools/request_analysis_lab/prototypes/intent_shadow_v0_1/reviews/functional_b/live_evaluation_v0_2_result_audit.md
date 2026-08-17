# Audit indipendente dei risultati live v0.2

Data: 2026-08-13  
Ambito: sola validità dei risultati già misurati; nessuna nuova inferenza,
nessuna GPU, rete o modifica a produzione, banco, batch, sigilli e oracolo.

## Esito

**FAIL per la validità numerica del campo `semantic_exact`.**

L'integrità della misura è **PASS** e il verdetto finale resta
**`candidate_fail`**, ma il report pubblicato non può essere considerato
numericamente corretto: contiene 62 falsi negativi di esattezza semantica.

## Integrità e identità ricontrollate

- mapping confermato: `A` è il controllo Metnos corrente con adapter congelato;
  `B` è il candidato intent-shadow 0.1;
- 158 coppie complete, ciascuna con un solo `A` e un solo `B`;
- 316 record, ordinali esatti `1..316`, alternanza AB/BA corretta;
- pannelli esatti e non compensati: 120 canonici, 4 controlli tipati, 34
  Phase-1;
- nessun duplicato fra identità, hash richiesta, hash query, testo query o
  `opaque_case_id`; nessun off-by-one negli ordinali dei tre pannelli;
- batch, journal, marker, seal, freeze replay, oracle, freeze oracle e sorgenti
  congelate hanno impronte coerenti;
- 316/316 risposte HTTP grezze legate ai rispettivi byte e contenuti modello;
- replay offline: 316/316 estrazioni riprodotte, una sola differenza diagnostica
  già autorizzata sul record 80, zero differenze inattese;
- zero errori di trasporto;
- 239 proiezioni valide sono state anche ricostruite dal documento decodificato
  con un normalizzatore indipendente; nessuna differenza.

## Difetto trovato

L'oracolo contiene documenti nel formato ammesso al modello, dove
`data_from` vuoto viene omesso. Il risultato misurato è invece una proiezione
semantica normalizzata, dove `data_from: []` viene materializzato.

Il valutatore live costruisce la mappa gold senza normalizzarla e poi esegue
direttamente `actual == expected`:

- `live_evaluator.py:216-221`: gold grezzo;
- `live_evaluator.py:234-238`: confronto diretto;
- `contratto_ombra_prototipo_intento_12_8_2026.md:149`: omissione legittima
  di `data_from` vuoto;
- `intent_shadow_normalize.py:145-170`: proiezione che materializza
  `data_from`;
- `intent_shadow_evaluate.py:631-633`: il valutatore offline corretto già
  normalizza il gold prima del confronto.

Conseguenza: 62 righe semanticamente esatte sono marcate false:

- `canonical_120.A`: 46;
- `canonical_120.B`: 16;
- controlli tipati: 0;
- falsi positivi `semantic_exact`: 0.

Il self-check riproduce i conteggi del valutatore live, quindi conferma il
calcolo implementato ma non rileva questo errore di rappresentazione.

## Conteggi ricalcolati — pannello canonico 120

Il suffisso “pubblicato” indica il calcolo letterale del valutatore v0.2;
“corretto” confronta entrambe le parti nella stessa proiezione semantica.

| Colonna | A pubblicato | A corretto | B pubblicato | B corretto |
|---|---:|---:|---:|---:|
| `semantic_exact` | 29 | **75** | 9 | **25** |
| `root_exact` | 91 | 91 | 37 | 37 |
| `correct_abstention` | 27 | 27 | 7 | 7 |
| `technical_valid` | 120 | 120 | 117 | 117 |
| `false_action_avoided` | 107 | 107 | 113 | 113 |
| `undo_exact` | 120 | 120 | 120 | 120 |
| `consent_exact` | 117 | 117 | 117 | 117 |
| `negation_exact` | 107 | 107 | 113 | 113 |
| `branch_ownership_exact` | 117 | 117 | 117 | 117 |
| `incorrect_abstention` (non gate) | 19 | 19 | 26 | 26 |

## Conteggi ricalcolati — controlli tipati 4

| Colonna | A pubblicato | A corretto | B pubblicato | B corretto |
|---|---:|---:|---:|---:|
| `semantic_exact` | 1 | 1 | 0 | 0 |
| `root_exact` | 3 | 3 | 0 | 0 |
| `correct_abstention` | 1 | 1 | 0 | 0 |
| `technical_valid` | 4 | 4 | 4 | 4 |
| `false_action_avoided` | 3 | 3 | 4 | 4 |
| `undo_exact` | 4 | 4 | 4 | 4 |
| `consent_exact` | 4 | 4 | 4 | 4 |
| `negation_exact` | 3 | 3 | 4 | 4 |
| `branch_ownership_exact` | 4 | 4 | 4 | 4 |
| `incorrect_abstention` (non gate) | 1 | 1 | 1 | 1 |

## Stati ricalcolati

| Pannello/braccio | Rappresentabile valido | Astensione valida | Documento invalido | Tecnico | Trasporto | Totale |
|---|---:|---:|---:|---:|---:|---:|
| canonico A | 70 | 46 | 4 | 0 | 0 | 120 |
| canonico B | 27 | 33 | 57 | 3 | 0 | 120 |
| tipati A | 2 | 2 | 0 | 0 | 0 | 4 |
| tipati B | 0 | 1 | 3 | 0 | 0 | 4 |
| Phase-1 A | 30 | 4 | 0 | 0 | 0 | 34 |
| Phase-1 B | 13 | 11 | 6 | 4 | 0 | 34 |

Le categorie sommano esattamente ai denominatori e non si sovrappongono in
questo batch.

## Phase-1 separato

- `A`: 25/34 `direct_binding_exact`;
- `B`: 29/34 `direct_binding_exact`;
- nessuna conversione automatica e nessuna compensazione con gli altri
  pannelli.

## Gate e verdetto

Calcolo pubblicato:

- delta canonico `B-A`: `9-29 = -20`;
- controllo speciale B: `0/4`;
- regressioni: `semantic_exact`, `root_exact`, `correct_abstention`,
  `technical_valid`;
- verdetto: `candidate_fail`.

Calcolo semanticamente corretto:

- delta canonico `B-A`: `25-75 = -50`;
- controllo speciale B: `0/4`;
- stesse quattro regressioni;
- verdetto: **`candidate_fail`**.

Quindi il difetto rende errati i punteggi exact e il delta, ma non inverte il
verdetto.

## Patch minima proposta, non applicata al banco congelato

Il gold deve essere normalizzato una sola volta nella stessa proiezione usata
dal risultato, prima di chiamare `_row`:

```diff
--- a/live_evaluator.py
+++ b/live_evaluator.py
@@
+from intent_shadow_normalize import normalize_document, semantic_document_json
+from intent_shadow_registry import load_frozen_registry
 from live_protocol import (
     CRITICAL_NO_REGRESSION_COLUMNS,
     HERE,
+    REGISTRY_PATH,
     REQUEST_MANIFEST_PATH,
@@
 def _gold_map(oracle):
     result = {("canonical_120", case["case_id"]): case["expected"] for case in oracle["cases"]}
     result.update({("typed_controls_4", case["control_id"]): case["expected"] for case in oracle["new_controls"]})
     if len(result) != 124:
         raise RuntimeError("gold map count mismatch")
-    return result
+    registry, _identity = load_frozen_registry(REGISTRY_PATH)
+    return {
+        key: semantic_document_json(normalize_document(value, registry).document)
+        for key, value in result.items()
+    }
```

La regressione minima da fissare è: gold con operazione senza `data_from` e
proiezione equivalente con `data_from: []` devono produrre
`semantic_exact=true`.

La patch va introdotta come nuova versione del valutatore e deve produrre un
nuovo artefatto di valutazione; non va sovrascritto il report v0.2 né alterato
il freeze della misura già consumata.

## Limiti di lettura dei numeri

- `technical_valid` significa soltanto “non `technical_invalid`”: include i
  `document_invalid`;
- `false_action_avoided` e `negation_exact` sono la stessa formula in questo
  valutatore, quindi non sono due prove indipendenti;
- `undo_exact`, `consent_exact` e `branch_ownership_exact` valgono true anche
  nei casi non applicabili; il denominatore pubblicato resta 120 o 4;
- `evaluation_executed_once` e `third_attempt_executed=false` sono dichiarati
  dal self-check ma non sono dimostrabili autonomamente dai soli artefatti
  immutabili disponibili.

Questi limiti non cambiano il ricalcolo sopra; impediscono però di leggere ogni
colonna come una percentuale autonoma di accuratezza generale.
