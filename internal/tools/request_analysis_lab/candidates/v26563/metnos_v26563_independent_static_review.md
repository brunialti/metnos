# Review infrastrutturale indipendente V26.5.6.3

Data: 9 agosto 2026.

Verdetto: **STATIC BLOCK**. Nessun gate, preflight, trasporto, modello o live è
autorizzato.

## Evidenza offline

| Controllo | Esito |
|---|---:|
| self-test autore con `/usr/bin/python3 -I -B` | 66/66 PASS |
| `--verify-freeze` | PASS |
| pin freeze | 20/20 |
| tree jsonschema/regex | 38/38 + 11/11 |
| inventory non-stdlib | 51/51 |
| byte semantici parent | 8/8 identici |
| request POST ricostruite | 34/34 identiche |
| `__pycache__`/`.pyc` nel candidato | 0 |
| rete/server/modello reali | 0/0/0 |

I fix principali sono reali. Le otto mutazioni V26.5.6.2 vengono respinte
prima del gold anche dopo serializzazione e nuovo parse JSON. Lo stesso vale
per request-body bytes/SHA alterati, endpoint con userinfo/path/query/fragment,
transport o facade latency oltre il totale, summary latency alterata e campi
`response_body` aggiunti alla diagnostica record o preflight. Il batch genuino
non contiene alcun campo response-body. Le request POST ricostruite
dall'evaluator coincidono byte per byte con il runner per tutti i 34 controlli.

IPv4 e IPv6 loopback canonici sulle porte 1 e 65535 sono accettati; forme non
canoniche e porte oltre 65535 sono respinte. Entrambi i parser accettano però
la porta 0, e un batch interamente fake su `127.0.0.1:0` o `[::1]:0` arriva a
`PHASE1_EVALUATED`. È fail-closed per un trasporto reale, perché non identifica
un listener utilizzabile, ma non è un confine endpoint operativo completo:
il successore deve imporre `1 <= port <= 65535` o documentare esplicitamente
che il contratto è soltanto sintattico.

Gate split, interpreter 3.12.3 `-I -B`, Unicode 15 e verifica completa dei tree
prima di import/compile restano invariati e verdi.

## Blocker: tipi annidati non esatti

La closure usa ancora uguaglianza Python su alcune mappe annidate. Di
conseguenza `1 == True` e `0 == False` aggirano il requisito di tipo JSON
esatto. Quattro fixture sintetiche vengono accettate, aprono come primo gold
`source_controls34` e terminano `PHASE1_EVALUATED`:

- `inline_transport_preflight.interpreter_context.isolated = 1`;
- `inline_transport_preflight.interpreter_context.dont_write_bytecode = 1`;
- `result.validation.attempted = 1` per un record valido;
- `result.validation.attempted = 0` per `model_content_json_invalid`.

Anche i quattro timestamp preflight possono essere traslati insieme in valori
negativi, mantenendo le durate, e vengono accettati. Questo non cambia le
metriche, ma mostra che il dominio temporale non è chiuso.

Il fix minimo è validare envelope, chiavi e tipo esatto di ogni valore
annidato prima del confronto (`is True`/`is False` per i booleani), includendo
`interpreter_context`, tutte le varianti `validation` e i futuri gate; i
timestamp `time_ns` devono essere interi non negativi.

## Osservazioni non autorevoli

`raw_frame_sha256` e `diagnostic.model_content {bytes,sha256}` non sono
ricostruibili dal batch persistito: sostituirli con valori arbitrari ben
formati apre gold. L'evaluator Phase-1 non li legge per metriche o gate, quindi
sono oggi semplici osservazioni e non un bypass decisionale. Tuttavia il
validator li descrive come “proof” e il batch non li etichetta come non
autorevoli. Per coerenza con la rimozione del response-body, il successore deve
rimuoverli oppure spostarli in un envelope chiuso `observation_only` con
`authoritative=false`, escluso esplicitamente da gate, metriche e verdetti.

Il registro resta limitato a 10 relazioni. Serve un successore byte-distinct e
una nuova review offline; non creare gate V26.5.6.3. La versione leggibile a
macchina è `metnos_v26563_independent_static_review.json`.
