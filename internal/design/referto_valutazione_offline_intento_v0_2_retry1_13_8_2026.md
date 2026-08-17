# Referto della valutazione offline intento v0.2 — retry 1

Data: 13 agosto 2026

## Esito semplice

La valutazione è riuscita tecnicamente, ma il candidato **B non supera il confronto**. Il verdetto gerarchico è `candidate_fail`.

Sul pannello principale di 120 casi, B ottiene 9 corrispondenze semantiche esatte contro 29 di A: differenza B−A = −20. Sui 4 controlli speciali, B ottiene 0/4; quindi non soddisfa il requisito 4/4. Il risultato legacy, favorevole a B per 29/34 contro 25/34, resta separato e non può compensare questi fallimenti.

## Bracci confrontati

- **A**: estrattore di intento Metnos corrente, eseguito ex novo, più adapter congelato del laboratorio.
- **B**: candidato intent-shadow 0.1 con estrazione diretta nello schema.

La corrispondenza A/B è stata verificata contro il protocollo. Tutte le 158 coppie contengono entrambi i bracci e tutte le 316 righe corrispondono alle identità del manifest.

## Prerequisiti verificati prima di aprire il gold

- Gate replay v0.2: **PASS**, 316/316 record e 158/158 coppie.
- Differenze ammesse saved-vs-replay: 1, esattamente quella prevista.
- Differenze inattese: 0.
- Verifica canonica dell'oracolo: **PASS**, 23/23 fonti, `error_count=0`.
- SHA-256 freeze oracolo risigillato: `eb9d051af8cee705536348ac602f7888595e5046937ec2ef09a632fddba63a03`.
- SHA-256 payload oracolo: `2cdec2776fa48594db8729ec1aa17a635956a61f64b2ce7ad00aa32f8cc0427f`.

## Pannello canonico — 120 casi per braccio

| Colonna critica | A | B |
|---|---:|---:|
| `semantic_exact` | 29 | 9 |
| `root_exact` | 91 | 37 |
| `correct_abstention` | 27 | 7 |
| `technical_valid` | 120 | 117 |
| `false_action_avoided` | 107 | 113 |
| `undo_exact` | 120 | 120 |
| `consent_exact` | 117 | 117 |
| `negation_exact` | 107 | 113 |
| `branch_ownership_exact` | 117 | 117 |

Dato diagnostico aggiuntivo: `incorrect_abstention` è 19 per A e 26 per B.

Conteggi di stato:

| Braccio | Totale | Valid | Invalid | Technical | Transport |
|---|---:|---:|---:|---:|---:|
| A | 120 | 116 (70 rappresentabili + 46 non rappresentabili) | 4 | 0 | 0 |
| B | 120 | 60 (27 rappresentabili + 33 non rappresentabili) | 57 | 3 | 0 |

## Controlli speciali tipizzati — 4 casi per braccio

| Colonna critica | A | B |
|---|---:|---:|
| `semantic_exact` | 1 | 0 |
| `root_exact` | 3 | 0 |
| `correct_abstention` | 1 | 0 |
| `technical_valid` | 4 | 4 |
| `false_action_avoided` | 3 | 4 |
| `undo_exact` | 4 | 4 |
| `consent_exact` | 4 | 4 |
| `negation_exact` | 3 | 4 |
| `branch_ownership_exact` | 4 | 4 |

Dato diagnostico aggiuntivo: `incorrect_abstention` è 1 per A e 1 per B.

Conteggi di stato:

| Braccio | Totale | Valid | Invalid | Technical | Transport |
|---|---:|---:|---:|---:|---:|
| A | 4 | 4 (2 rappresentabili + 2 non rappresentabili) | 0 | 0 | 0 |
| B | 4 | 1 (0 rappresentabili + 1 non rappresentabile) | 3 | 0 | 0 |

## Legacy Phase-1 — 34 casi per braccio

| Braccio | `direct_binding_exact` | Totale |
|---|---:|---:|
| A | 25 | 34 |
| B | 29 | 34 |

Conteggi di stato:

| Braccio | Totale | Valid | Invalid | Technical | Transport |
|---|---:|---:|---:|---:|---:|
| A | 34 | 34 (30 rappresentabili + 4 non rappresentabili) | 0 | 0 | 0 |
| B | 34 | 24 (13 rappresentabili + 11 non rappresentabili) | 6 | 4 | 0 |

Il pannello legacy è stato mantenuto separato, senza compensazione con i pannelli canonico e speciale.

## Verdetto gerarchico

- Differenza canonica `semantic_exact` B−A: **−20**.
- Requisito speciale B = 4/4: **non soddisfatto**; B è 0/4.
- Regressioni critiche di B: `semantic_exact`, `root_exact`, `correct_abstention`, `technical_valid`.
- Verdetto finale: **`candidate_fail`**.

B migliora `false_action_avoided` e `negation_exact` di 6 casi nel pannello canonico e pareggia A su `undo_exact`, `consent_exact` e `branch_ownership_exact`. Questi miglioramenti non superano il gate gerarchico approvato.

## Controllo deterministico

Il self-check ha ricalcolato dalle singole righe i denominatori, tutti gli aggregati tipizzati, le nove colonne critiche e il verdetto. Ha inoltre verificato le identità, la presenza delle 158 coppie A/B, l'assenza di compensazione tra pannelli e zero errori di trasporto. Esito del self-check: **PASS**.

Per questi conteggi: `valid` = `valid_representable` + `valid_unrepresentable`; `invalid` = `document_invalid` + estrazione mancante; `technical` = `technical_invalid`; `transport` = risposta HTTP non accettata o `transport_error`. Nel batch le categorie sono mutuamente esclusive.

## Artefatti e impronte

- Output macchina riuscito: `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_evaluation_v0_2_retry1.json`
  - SHA-256: `6eee1f559b20f3145780ae9dca52eaf101df82f442214cdfec0751f50845d25d`
- Self-check macchina: `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_evaluation_v0_2_retry1_selfcheck.json`
  - SHA-256: `de84fc703fe379537a8363908b61661318280b692e4af19bbbf2139ac247f2b3`
- Primo output fallito, conservato senza sovrascrittura: `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_evaluation_v0_2.json`
  - SHA-256: `96fc99ddd2d8518737da0bb51da9e88ce09adea5917397d7d4a1d4432cc83fbc`

Impronte degli ingressi rimasti immutati:

- Batch: `eac40d320a8e2d98b0a11ab2ed652cf9ecc64b4893fb2e658a9bbde50143ca0b`.
- Journal: `403135c52aff5852e782f6549bc55f0f08d64b6a92e7a8fb4b9c6374c7515c4e`.
- Freeze del batch sigillato: `8f58208fd6082b6fafda953882356c3f4d0084cf183aefeeaafb8b08847cde4c`.
- Oracle: `3e0d1155fad7b2920b3b04bb6e617747849c8fe4a1abfea5920f1b51dba6a123`.

## Vincoli rispettati

È stata eseguita una sola nuova valutazione offline dopo il risigillo. Non è stato eseguito un terzo tentativo. Non sono stati usati GPU, rete, endpoint o servizi. Raw, batch, journal, seal, oracle ed expected non sono stati modificati. Checkpoint e handover restano volutamente invariati in attesa della revisione indipendente dei risultati.
