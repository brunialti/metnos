# V26.4 K1/34 — NOT_EVALUATED per blocco localhost del sandbox

## Verdetto

L'unico tentativo autorizzato è stato consumato, ma **non ha valutato il
modello**. Tutti i 34 record sono `not_evaluated` con
`transport_or_json`. Non è consentito rilanciare con il gate originale.

Non leggere gli zeri del summary grezzo come accuracy: `positive_usable=0` e
gli altri gate a zero sono placeholder prodotti dopo errori di trasporto.
L'accuracy live V26.4 resta sconosciuta.

## Causa confermata

Il processo Python del runner era confinato dal sandbox. Una GET
inference-free a `/v1/models`, eseguita subito dopo con `urllib` nello stesso
contesto, fallisce con:

```text
urllib.error.URLError: <urlopen error [Errno 1] Operation not permitted>
cause: PermissionError(errno=1)
```

La stessa GET tramite il `curl` già autorizzato passa sia prima sia dopo il
run. Il server era vivo; nella finestra del run non compare alcun POST nel
log server. Di conseguenza il body con schema e prompt non ha raggiunto
l'endpoint: schema non supportato, modello e crash server non sono stati
esercitati e non possono essere la causa di questo risultato.

Il profilo temporale è coerente con il rifiuto locale: primo tentativo
9,780733 ms, altri 33 fra 0,034157 e 0,044768 ms, somma 10,968665 ms.

## Artefatto conservato

Il risultato grezzo `/tmp/metnos_v264_typed_phase1_controls_k1.json` è stato
archiviato byte-identico come
`metnos_v264_typed_phase1_controls_k1.json`, SHA-256
`7fb2ac3aa3315dcc56ef5ee11296d8f94d6a6d8bb9c4d06d689adbc5421b83b3`.
Gate e output `/tmp` non sono stati modificati.

## Difetto infrastrutturale emerso

Il runner congelato nasconde la diagnosi utile: unisce errori socket, HTTP e
JSON in `transport_or_json`, scarta tipo/eccezione/body, continua altri 33
casi, conta ogni socket negato come `model_calls=1` ed esce con codice zero.
Il freeze V26.4 resta immutabile; la correzione appartiene a V26.4.1.

V26.4.1 deve:

- avere uno smoke test di trasporto separatamente autorizzato e senza
  inferenza, eseguito nello stesso contesto del futuro run;
- fermarsi al primo errore di trasporto ed uscire non-zero;
- conservare tipo e causa dell'eccezione; per `HTTPError`, status, lunghezza e
  SHA-256 del body più un estratto corto e redatto;
- distinguere tentativi, richieste accettate dal server e risposte decodificate;
- autorizzare il run live soltanto in contesto unsandboxed/escalated dopo lo
  smoke PASS, con un nuovo gate indipendente. Nessun retry automatico.

I dettagli machine-readable sono in
`metnos_v264_k1_not_evaluated_diagnosis.json`.
