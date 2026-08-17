# V26.4.1 — transport smoke e runner fail-fast

Questo è un design offline. Non modifica il runner/freeze V26.4 e non
autorizza chiamate modello.

## Obiettivo

Separare tre risultati che V26.4 confonde:

1. il processo non può raggiungere localhost;
2. il server risponde con errore HTTP;
3. il server risponde 2xx ma body/JSON/frame non sono validi.

Solo il terzo gruppo può contenere una vera osservazione del modello. Un errore
di trasporto non deve produrre 34 falsi record di accuracy.

## Due autorizzazioni distinte

### A. Transport smoke, zero inferenza

Un lock dedicato autorizza soltanto:

- metodo `GET`;
- URL esatto `http://127.0.0.1:8080/v1/models`;
- zero body e zero endpoint di completion;
- un tentativo, zero retry;
- un output diagnostico esatto e separato.

Lo smoke deve girare con lo stesso eseguibile Python, utente, namespace,
sandbox e privilegi previsti per il run. Valida status 200 e forma minima
della risposta, non timestamp o byte instabili. Il suo output viene congelato,
revisionato e legato dal successivo model gate.

Un `curl` eseguito fuori sandbox non sostituisce questo smoke: dimostra la
salute del server, non la raggiungibilità dal runner.

### B. Model run

Un nuovo lock monouso lega freeze, review, smoke PASS e contesto di esecuzione.
Autorizza il POST K1/34 solo in contesto esplicitamente unsandboxed/escalated.
Nessun retry automatico. Il gate V26.4 già consumato non è riutilizzabile.

## Classificazione precisa degli errori

`HTTPError` va catturato prima di `URLError`, perché ne è una sottoclasse.
L'outcome di trasporto deve avere almeno:

```json
{
  "phase": "connect|http|response_json|response_shape|frame_json",
  "error_type": "URLError",
  "cause_type": "PermissionError",
  "errno": 1,
  "http_status": null,
  "response_body_bytes": 0,
  "response_body_sha256": null,
  "response_body_excerpt_redacted": null
}
```

Per `HTTPError` leggere il body con limite esplicito, registrare lunghezza,
SHA-256, content type e un estratto corto redatto. L'estratto non deve
contenere query, prompt, bearer token, cookie o header; se la redazione non è
dimostrabile, conservare solo hash e lunghezza. Per una risposta 2xx non JSON,
fare lo stesso e classificare `response_json`. Errori di forma OpenAI e JSON
nel campo `message.content` restano distinti.

## Fail-fast e semantica dei contatori

Al primo errore prima di una risposta 2xx decodificata:

- interrompere il batch;
- scrivere un artefatto `NOT_EVALUATED` atomico nel path autorizzato;
- non creare 33 record sintetici;
- uscire non-zero;
- non trasformare il fallimento in accuracy zero.

Contatori separati:

- `socket_attempts`: apertura tentata;
- `http_responses`: ricevuto uno status HTTP;
- `server_accepted_requests`: risposta 2xx;
- `decoded_chat_responses`: envelope OpenAI decodificato;
- `decoded_frames`: content JSON decodificato;
- `evaluated_cases`: validator eseguito su un frame.

`model_calls` non va usato perché confonde questi livelli. Un EPERM locale
produce `socket_attempts=1` e tutti gli altri contatori a zero.

Gli errori semantici del frame non sono trasporto: sono risultati modello
valutabili e possono continuare attraverso i 34 casi secondo il protocollo
congelato.

## Pseudocodice minimo

```python
try:
    response = urlopen(request, timeout=120)
except HTTPError as exc:
    return fail(http_diagnostic(exc))
except URLError as exc:
    return fail(url_diagnostic(exc, exc.reason))
except (TimeoutError, OSError) as exc:
    return fail(socket_diagnostic(exc))

http_responses += 1
server_accepted_requests += 1
body = bounded_read_and_hash(response)
try:
    envelope = json.loads(body)
except JSONDecodeError as exc:
    return fail(response_json_diagnostic(exc, body))

decoded_chat_responses += 1
try:
    raw_frame = envelope["choices"][0]["message"]["content"]
except (KeyError, IndexError, TypeError) as exc:
    return fail(response_shape_diagnostic(exc, envelope))

try:
    frame = json.loads(raw_frame)
except JSONDecodeError as exc:
    return fail(frame_json_diagnostic(exc, raw_frame))
```

Il chiamante deve distinguere `TransportFailure` da `FrameValidationFailure`.
Solo il primo interrompe immediatamente il batch.

## Test offline obbligatori prima di un nuovo gate

- `PermissionError -> URLError`: un solo tentativo, exit non-zero, nessun caso
  valutato;
- connect refused, DNS/route, timeout e TLS error classificati separatamente;
- HTTP 400/404/413/422/500: status e body hash presenti, un solo tentativo;
- body 2xx non JSON, envelope senza choices, content non JSON;
- body enorme: limite rispettato, hash/troncamento espliciti;
- redazione di token/query/header verificata con canary;
- errore semantico frame: resta valutabile e non attiva fail-fast trasporto;
- contatori invarianti per ogni fase;
- mutazione di smoke lock/output/freeze/review: rifiuto;
- esistenza output monouso: rifiuto.

## Criterio per tornare al live

Nessuna chiamata K1 finché il transport smoke, eseguito nel medesimo contesto
unsandboxed del runner, non è PASS e legato in un nuovo freeze/review/gate.
L'eventuale nuovo run resta una nuova autorizzazione indipendente, non un retry
del gate V26.4.
