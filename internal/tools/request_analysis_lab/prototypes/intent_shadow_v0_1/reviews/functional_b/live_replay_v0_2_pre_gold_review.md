# Revisione funzionale B — replay/evaluator 0.2 pre-gold

Data: 13 agosto 2026  
Esito: **PASS 18/18 — ready_to_evaluate**

Confine rispettato: nessun oracle/gold aperto e nessuna valutazione eseguita. Sono stati rieseguiti soltanto il test finito e il gate pre-gold 0.2 già esistenti. Nessun uso di rete, GPU o servizi.

| N. | Controllo congelato | Esito | Evidenza |
|---:|---|:---:|---|
| 1 | Oracle chiuso prima del gate | PASS | `validate_pre_gold()` precede ogni accesso atteso; la prova di fallimento anticipato non raggiunge i due loader gold. Test e replay riportano `oracle_opened=false` ed `evaluation_executed=false`; l'output di valutazione 0.2 non esiste. |
| 2 | Freeze, versione e provenienza | PASS | Versioni gate/evaluator 0.2 corrette; lock del freeze valido; 41/41 fonti e 3/3 file propri coincidono con le impronte congelate. L'insieme chiuso non contiene fonti oracle/gold. Freeze SHA-256 `2e4608c8924790ebd1c4faafaa99fbeb905b52dc82ff276d3cc4ac5a398fceef`. |
| 3 | Integrità batch e sigillo | PASS | Batch `complete`, `gold_opened=false`, 316 record e 316 POST accettati. Il sigillo lega batch, journal, marker e autorizzazione; batch SHA-256 `eac40d320a8e2d98b0a11ab2ed652cf9ecc64b4893fb2e658a9bbde50143ca0b`, sigillo SHA-256 `8f58208fd6082b6fafda953882356c3f4d0084cf183aefeeaafb8b08847cde4c`. |
| 4 | Integrità delle risposte raw | PASS | Per 316/316 record il gate verifica Base64, SHA-256 del body, wrapper HTTP, contenuto modello salvato e relativa impronta prima del replay. |
| 5 | Integrità journal, checkpoint e marker | PASS | 316 righe journal, ciascuna identica in JSON canonico al record batch corrispondente; checkpoint a record/POST 316 e marker dal primo POST coerenti con il sigillo. Journal SHA-256 `403135c52aff5852e782f6549bc55f0f08d64b6a92e7a8fb4b9c6374c7515c4e`. |
| 6 | Completezza e composizione dei 316 record | PASS | 240 record canonici, 8 controlli tipizzati, 68 legacy; 158 record per braccio. Entrambi i replay verificano, ricostruiscono e ricalcolano 316/316 record. |
| 7 | Puntatore e record autorizzati esatti | PASS | Unica eccezione: `adapter_metadata.implicit_actions_ignored`, indice zero-based 80. Sono vincolati anche request 81, sample 40, pannello/posizione, caso, braccio A e hash query/richiesta; tutto il resto resta esatto per tipo e valore. |
| 8 | Campo presente e booleano esatto | PASS | Nel record sigillato autorizzato il campo è presente ed è `false`; nel replay seed 0 è presente ed è `true`. Il confronto richiede `type(value) is bool` da entrambi i lati prima di applicare l'eccezione. |
| 9 | Entrambe le direzioni ammesse | PASS | Le prove finite accettano uguaglianza totale, `false -> true` e `true -> false` soltanto sul puntatore e record autorizzati. |
| 10 | Campo mancante rifiutato | PASS | La prova finita `missing_field_is_rejected` passa. |
| 11 | Tipo diverso da booleano rifiutato | PASS | La prova finita `non_boolean_is_rejected`, inclusa la distinzione JSON tra `true` e `1`, passa. |
| 12 | Altro metadata rifiutato | PASS | La prova finita modifica `adapter_metadata.primary_response_consumed` oltre al booleano autorizzato ed è respinta. |
| 13 | Differenza semantica rifiutata | PASS | La prova finita modifica il documento semantico ed è respinta. |
| 14 | Due differenze rifiutate | PASS | La prova finita aggiunge una seconda differenza fuori puntatore ed è respinta; il gate limita inoltre a una sola differenza autorizzata complessiva. |
| 15 | Altro record/indice rifiutato | PASS | La prova finita sull'indice 81 è respinta; il codice richiede anche uguaglianza esatta dell'intera identità congelata, quindi un altro record allo stesso indice non è autorizzato. |
| 16 | Replay completo con seed 0 e 2 | PASS | Seed 0: 316/316, una sola differenza autorizzata (`saved=false`, `replay=true`) all'indice 80, zero inattese. Seed 2: 316/316, zero differenze autorizzate e zero inattese. Test finito complessivo: 12/12 PASS. |
| 17 | Evaluator post-sigillo; pannelli e verdetto invariati | PASS | L'evaluator 0.2 aggiunge il gate completo prima del gold e poi riusa aggregazioni e `typed_verdict` 0.1. Restano separati 120 canonici, 4 controlli tipizzati e 34 legacy; restano le nove colonne senza regressione, 4/4 e miglioramento minimo 3/120, senza compensazione fra pannelli. |
| 18 | Nessuna modifica a runtime o dati | PASS | Dopo test e replay tutte le impronte congelate restano identiche; nessun file runtime, batch, raw, journal, sigillo, freeze o report pre-gold è stato scritto. Nessuna rete, GPU, servizio o commit; unica nuova scrittura: questo referto. |

Il report pre-gold persistente resta byte-identico, SHA-256 `03edefc1986e21dd764a0ce993551d33749b9b4f4cc4f15a77612116fb33f576`.
