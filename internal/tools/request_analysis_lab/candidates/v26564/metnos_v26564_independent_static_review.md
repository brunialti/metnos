# Review indipendente offline V26.5.6.4

Data: 9 agosto 2026.

Verdetto: **STATIC PASS**. Il verdetto riguarda esclusivamente i byte frozen e
la correttezza offline del contratto K1/34. Non autorizza gate, preflight,
trasporto, modello o live.

## Evidenza riprodotta

| Controllo | Esito |
|---|---:|
| self-test con `/usr/bin/python3 -I -B` | 85/85 PASS |
| `--verify-freeze` | PASS |
| artifact frozen | 6/6 hash identici |
| byte semantici parent | 8/8 identici |
| path scalari normalizzati | 148 |
| coppie path/tipo osservate | 150 |
| sostituzioni scalari exact-type indipendenti | 594/594 respinte |
| closure scalare→container/container exhaustive | 435/435 respinte |
| mutazioni complete prima di GOLD | 45/45 respinte |
| request POST bytes/SHA ricostruite | 34/34 |
| `__pycache__`/`.pyc`, gate e output candidati | 0 |
| rete/server/modello reali | 0/0/0 |

Il replay autore conferma inoltre 2.028 foglie scalari nel batch valido, 1.994
nel batch misto invalido, mutation148, un GET fake più 34 POST fake e cap batch
7.609.728 byte prima della codifica.

## Probe indipendenti

Sono stati costruiti soltanto batch sintetici in memoria. Le forme genuine
`evaluated_valid`, model-JSON-invalid, schema-invalid, adapter-invalid,
validator-invalid e facade-input-invalid sono state tutte accettate
correttamente dall'evaluator offline. Le 148 path normalizzate diventano 150
coppie path/tipo considerando anche i tipi alternativi dei branch; per ciascuna
sono stati provati tutti i tipi JSON scalari errati tra boolean, integer, float,
string e null. Tutte le 594 sostituzioni sono state fermate dal census.

Il vocabolario derivato indipendentemente dallo schema full contiene esattamente
le stesse 9 chiavi integer e 8 chiavi string del census. Dict e list non sono
foglie scalari, ma restano chiusi prima del GOLD da envelope esatti, shape di
branch, jsonschema e validator. Sostituzioni scalare→dict/list, container vuoti,
container di tipo errato, chiavi extra vuote e chiavi note in posizioni errate
sono comprese nelle 45 mutazioni end-to-end respinte con zero aperture GOLD.
In aggiunta, l'intera pipeline di checkpoint e validazione pre-GOLD ha respinto
300/300 sostituzioni delle 150 coppie path/tipo con dict/list vuoti, 71/71
container osservati col tipo opposto e 64/64 container non vuoti svuotati.

Runner ed evaluator accettano soltanto loopback canonico IPv4/IPv6 con porta
`1..65535`: 1 e 65535 sono accettate; 0, 65536, porta assente e `localhost`
sono respinti. Valori negativi nei quattro timestamp, ordine invertito e durata
wall/monotonic incoerente sono respinti prima del GOLD. La comparazione prevista
per i gate distingue ricorsivamente bool/int, int/float e null/bool, e chiude
chiavi e lunghezze di dict/list.

I batch persistiti validi e invalidi non contengono `raw_frame_sha256`,
`model_content` o `response_body`; ogni reiniezione provata viene respinta prima
del GOLD. La sola prova request-body rimasta è derivata da query, segmenti,
prompt e schema pin-nati: bytes e SHA coincidono per tutti i 34 controlli
(102/102 anche ripetendo il confronto su tre branch di batch).

Non è emerso alcun difetto causale nel perimetro richiesto. Il registro resta
limitato a 10 relazioni e questo PASS non certifica la suite generale 109 né un
cutover runtime. Preflight gate ed external gate restano assenti.

## Identità frozen

| Artifact | SHA-256 |
|---|---|
| runner | `6fb5785d32b73f9d7dad4e917b211fdcdc12d9ca05088cc6d8ed9c4b167b146a` |
| evaluator | `f2c4a900f3b1cc6d9341785f99f7386891dc490812e9330cb256401a0cb967a3` |
| self-test | `ad9e4c0d401f2bdf6ff1bafad0df2d619e5d8aed42483fdeeecd0600602ca03e` |
| author freeze | `d41a440551af5bfc430000e12bcdda2cbc89ca8c601b137d74b6e57f47f6cd56` |
| author pre-gate | `365bc9637fb3c1d1d09812968b5ad85de0875b0b09fc3f21834c89fbca178236` |
| author self-test result | `dbccbbd61bff02501ed163337fe911e2524d5cb082afdea44762ba632a8a30c7` |
