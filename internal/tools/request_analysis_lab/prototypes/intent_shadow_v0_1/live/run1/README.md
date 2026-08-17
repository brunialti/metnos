# Intent IR v0.2 — protocollo live RUN 1

Stato: **spento e non armato**. Questo namespace prepara il primo dei tre run
massimi autorizzati; non contiene un file di autorizzazione e l'import/default
non effettua rete, GPU, endpoint o scritture di produzione.

- A: controllo corrente, snapshot fresco e adapter read-only.
- B: IR v0.2, `response_format=json_schema` strict e compilatore deterministico.
- Critic B: OFF; una risposta primaria, raw conservato byte per byte, zero repair.
- Carico: 158 query × 2 bracci = 316 richieste seriali, ordine AB/BA alternato.
- Gold: inaccessibile al runner; l'evaluator lo apre solo dopo batch completo e sigillo validi.
- Esecuzione: single-use, zero retry, consumo al primo POST accettato.
- Fallimenti transport, envelope HTTP o adapter fermano e sigillano partial;
  soltanto JSON/IR model-facing invalido viene contato e la corsa continua.
- Lingua B: tag BCP47 normalizzato ed esplicito, unico prompt neutro, nessuna
  allowlist/fallback o branch binario; tag malformato rifiutato pre-send.

`build_artifacts.py` materializza e verifica snapshot, pannello query-only,
protocollo, manifest e freeze. `verify.py` controlla gli stati `disarmed` e
`armed`; l'autorizzazione deve essere l'ultimo artefatto e non è qui presente.

I dizionari in questo namespace sono soltanto documenti JSON del laboratorio.
Un porting in Metnos dovrà usare oggetti tipizzati e immutabili, senza copiare
questo modello dati in produzione. Numeri e pannelli 120/4/34 appartengono solo
al protocollo della misura, mai alla soluzione candidato.
