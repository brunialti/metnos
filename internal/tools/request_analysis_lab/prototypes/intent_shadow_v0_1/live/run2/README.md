# Intent IR candidate v0.3 — protocollo live RUN 2

Stato: **spento e non armato**. Questo namespace prepara la seconda misura
adattiva; non contiene un file di autorizzazione e l'import/default
non effettua rete, GPU, endpoint o scritture di produzione.

- A: stesso identico snapshot e pannello query-only congelati nel RUN1; adapter read-only invariato.
- B: candidate v0.3, overlay solo prompt sull'IR v0.2; schema, validator e compilatore invariati.
- Critic B: OFF; una risposta primaria, raw conservato byte per byte, zero repair.
- Carico: 158 query × 2 bracci = 316 richieste seriali, ordine AB/BA alternato.
- Gold: inaccessibile al runner; l'evaluator lo apre solo dopo batch completo e sigillo validi.
- Esecuzione: single-use, zero retry, consumo al primo POST accettato.
- Fallimenti transport, envelope HTTP o adapter fermano e sigillano partial;
  soltanto JSON/IR model-facing invalido viene contato e la corsa continua.
- Lingua B: tag BCP47 normalizzato ed esplicito, unico prompt neutro, nessuna
  allowlist/fallback o branch binario; tag malformato rifiutato pre-send.

`build_artifacts.py` riusa e verifica snapshot/pannello RUN1 e materializza
protocollo, manifest e freeze RUN2. `verify.py` controlla gli stati `disarmed` e
`armed`; l'autorizzazione deve essere l'ultimo artefatto e non è qui presente.

I dizionari in questo namespace sono soltanto documenti JSON del laboratorio.
Un porting in Metnos dovrà usare oggetti tipizzati e immutabili, senza copiare
questo modello dati in produzione. Numeri e pannelli 120/4/34 appartengono solo
al protocollo della misura, mai alla soluzione candidato.

Unica modifica logica rispetto a RUN1: proiezione del prompt. Le tre radici sono
presentate insieme prima del catalogo; l'operazione iniziale deve omettere
`from`; ogni dipendenza deve puntare a un'operazione strettamente precedente e
visibile. Non esiste repair. Questa è una misura adattiva di sviluppo: non prova
universalità e si ferma su nuova policy semantica o ripetizione identica in stallo.
