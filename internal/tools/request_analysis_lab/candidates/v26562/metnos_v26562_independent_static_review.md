# Review infrastrutturale indipendente V26.5.6.2

Data: 9 agosto 2026.

Verdetto: **STATIC BLOCK**. Nessun preflight, gate, trasporto, modello o live è
autorizzato.

## Evidenza fresca offline

| Controllo | Esito |
|---|---:|
| self-test autore con `/usr/bin/python3 -I -B` | 50/50 PASS |
| `--verify-freeze` | PASS |
| pin freeze | 20/20 |
| tree jsonschema/regex | 38/38 + 11/11 |
| inventory non-stdlib | 51/51 |
| byte semantici parent | 8/8 identici |
| differenziali contatore JSON/UTF-8 | 1.000/1.000 |
| `__pycache__`/`.pyc` nel candidato | 0 |
| rete/server/modello reali | 0/0/0 |

Due dei tre fix sono causali. Il call graph frozen separa
`_verify_author_bytes` da `_verify_author_gates_absent`: i due verifier
operativi chiamano soltanto il primo e poi aprono il gate atteso, mentre solo
`verify_freeze` applica la policy author-time di assenza. La raggiungibilità è
stata verificata sul sorgente/AST senza sostituire funzioni e senza creare un
gate. Inoltre l'evaluator rifiuta separatamente l'assenza di `-I` o `-B` con
exit 2 e diagnostica sanitizzata prima di leggere il batch. Un manifest
dependency internamente ricalcolato, con stesso `file_count` ma una membership
regex sostituita, fallisce prima di `compile`; executable 3.12.3, Unicode 15,
membership e hash dei due tree reali coincidono con i pin.

Restano verdi anche opener senza proxy, redirect deny, loopback IP letterale,
conteggio degli open, output anchor e cap 7.609.728 byte prima del dump. Il
self-test fresco usa solo 1 GET + 34 POST finti; gli otto byte semantici parent
sono invariati.

## Blocker riproducibile

La closure pre-gold non è ancora esatta. Valori JSON booleani vengono
confrontati con interi tramite `==` (`True == 1`, `False == 0`), le copie dei
counter nel summary non vengono tipizzate autonomamente e l'endpoint inline è
riconosciuto con `startswith` invece che con il parser loopback del runner.
Alcune prove diagnostiche restano inoltre soltanto ben formate, non derivate
dalla query pin-nata.

Su un batch completo genuino, dopo serializzazione e nuovo parse JSON, tutte
le seguenti mutazioni hanno aperto come primo gold `source_controls34` e hanno
prodotto `PHASE1_EVALUATED`:

- `records[0].ordinal = true`;
- `result.model_request_attempts = true`;
- `summary.inference_counters.invalid_cases = false`;
- `summary.inline_preflight_counters.socket_attempts = true`;
- `request_body_bytes=false`, `transport_preflight_calls=true` e
  `inference_calls=false` nel preflight;
- `request_body_bytes=999` con un SHA-256 arbitrario nella diagnostica;
- latenze transport/facade non coerenti con la latenza totale;
- endpoint `http://127.0.0.1:8080@192.0.2.1`, che il parser URL interpreta con
  destinazione non-loopback.

Questa è una violazione fail-open della regola “batch integralmente chiuso
prima di ogni `GOLD_IDENTITIES`”. Il fix minimo è imporre il tipo JSON esatto
prima di ogni confronto, validare separatamente entrambe le copie dei counter,
riusare il parser endpoint IP-letterale, e ricalcolare o eliminare ogni prova
derivabile (request body e relazioni tra latenze) prima del primo gold read.

Il registro resta limitato a 10 relazioni: anche dopo il fix serviranno un
successore byte-distinct, nuova review e gate separati prima di qualunque K1 o
live. La versione machine-readable è
`metnos_v26562_independent_static_review.json`.
