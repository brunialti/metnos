# V26.4.1 author static review — runner-only infrastructure delta

Verdetto autore: **PASS OFFLINE, LIVE BLOCCATO IN ATTESA DI REVIEW INDIPENDENTE**.

Ambito della review: runner, freeze, mutazioni, contamination audit e catena dei
gate. Nessun output live è stato letto e nessuna richiesta server/modello è
stata eseguita.

## Audit causale del K1 precedente

La diagnosi durevole V26.4 è legata dal freeze V26.4.1. Le osservazioni decisive
sono `URLError` con causa `PermissionError`, `errno=1`, nessuna richiesta nei log
server, zero richieste modello accettate, primo tentativo 9.780733 ms e tentativi
successivi 0.034–0.045 ms. Il pattern è coerente con un blocco sandbox locale e
incompatibile con una risposta lenta o errata del modello. L'accuracy rimane
non misurata.

## Delta consentito

Il delta è limitato a trasporto, diagnostica, contatori, orchestrazione batch,
output atomico e gate. Registry, schema, prompt, fixture e i bundle sorgente di
segmentatore/schema/validator/classifier/evaluator/request body sono identici a
V26.4. Nessun retry o secondo tentativo semantico è stato introdotto.

La tassonomia osservabile è:

| Fase | Conteggio | Trattamento batch |
|---|---:|---|
| apertura socket | `socket_attempts` | fail-fast se fallisce |
| status HTTP ricevuto | `http_responses` | fail-fast se non 2xx |
| richiesta accettata 2xx | `server_accepted_requests` | conservato anche se body read fallisce |
| response JSON decodificato | `response_json_documents_decoded` | fail-fast se non decodificabile |
| chat envelope valido | `decoded_chat_responses` | fail-fast se forma protocollo invalida |
| content/frame JSON | `decoded_frames` | output modello invalido se non decodificabile |
| valutazione modello | `evaluated_cases` | valido o invalido; entrambi continuano il batch |

Un errore di trasporto/protocollo produce `NOT_EVALUATED`, output diagnostico
monouso ed exit non-zero. Un content JSON o frame invalido produce
`evaluated_invalid`, contribuisce all'accuracy come fallimento modello e non
interrompe gli altri casi. Non vengono emessi record fittizi né `model_calls`.

## Privacy e diagnostica

I body sono letti con limiti espliciti. La diagnostica conserva byte/hash e
completezza; gli estratti sono corti e redatti. La query esatta e la sua forma
JSON-escaped sono sostituite da fingerprint. I probe coprono token, password,
Bearer, email, echo della query, body sovradimensionato e read timeout. Un frame
validator-invalid viene persistito soltanto come hash più error codes.

## Preflight e gate

Il preflight standalone può compiere esclusivamente un GET `/v1/models`, senza
body né inferenza, una volta e senza retry, dopo un gate esterno dedicato. Il
successivo live lock deve dichiarare esattamente una chiamata trasporto e zero
inferenze prima del lock, legare l'hash del risultato PASS e verificarne una
recenza massima di 15 minuti. Il runner impone inoltre un GET inline nello stesso
processo e contesto, massimo 5 secondi prima del primo POST. Un inline failure
blocca tutti i POST.

## Evidenza offline

- core 69/69;
- graph probe ereditato 125/125;
- infra mutation probe 72/72;
- oracle 34/34;
- contamination audit zero su 213 record complessivi;
- `verify_freeze`: PASS;
- rete/modello: 0.

Freeze revisionato:
`6f98647d6a9ea3b46ec521dca50e9f5bb00352d9b9360047219bd81976cd6eff`.

Questo documento non è indipendente e non soddisfa alcun gate di rete. La
review indipendente deve legare i byte archiviati, verificare il freeze e
produrre un verdetto separato prima di qualsiasi preflight.
