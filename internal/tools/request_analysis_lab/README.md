# Request Analysis Lab

> **CHECKPOINT ATTIVO 9/8/2026**: V26.5.1–V26.5.4 sono STATIC BLOCK;
> V26.5.5 ha review STATIC PASS offline; V26.5.6, V26.5.6.1 e V26.5.6.2 sono
> **STATIC BLOCK**. Anche V26.5.6.3 è **STATIC BLOCK** indipendente nonostante
> 50 test ereditati + 16 nuovi = 66 PASS: chiude gli otto bypass V26.5.6.2,
> ricostruisce la request-body ed elimina il response-body, ma interi 0/1 sono
> ancora accettati in campi booleani annidati prima del gold. Porta 0 e
> osservazioni hash non autorevoli richiedono inoltre un contratto esplicito.
> V26.5.6.4 ha review indipendente **STATIC PASS**: stessi byte semantici, 66
> test ereditati + 19 nuovi = 85 PASS; census exact-type su 148 path,
> timestamp nonnegativi, porte `1..65535` e osservazioni non ricostruibili
> rimosse. Probe separato: 594 sostituzioni scalari e 45 mutazioni complete
> respinte prima del GOLD; request proof 34/34.
> Rete/modello reali zero fino al live. Il gate preflight e l'external gate
> K1/34 (27/27 offline) sono stati **consumati**: il live K1/34 è stato eseguito
> ed è **fallito semanticamente, 0/34 frame validi**, con trasporto perfetto
> (34/34 HTTP 200, retry 0, p50 6,109 s, p95 9,447 s). 34/34 hanno superato lo
> schema JSON; 33 sono morti nell'adapter (32 span collisi + 1 dipendenza) e uno
> solo ha raggiunto il validator, quindi la qualità semantica di 33 casi resta
> **non misurata**. Causa: la forma compatta ha tolto `clause_id` e promosso lo
> **span sorgente a chiave dell'identità di clausola**; l'invariante non è
> esprimibile nello schema, vive solo nella prosa del prompt e aborta prima
> della validazione. Il gold è rappresentabile (max 1 proiezione per grafo): non
> è un limite dell'encoding sul gold, è il modello che emette ancore in eccesso.
> Post mortem, metriche, successore minimo e sonda offline in
> `candidates/v26564/metnos_v26564_live_postmortem.{md,json}` e
> `metnos_v26564_postmortem_encoding_probe.py`. Nessun rerun: il gate esigeva
> `output_must_be_absent` e l'output ora esiste.
> **V26.5.6.5 è il successore clause-owned ed è ora STATIC BLOCK indipendente**
> (author checkpoint OFFLINE `inference=false`, self-test 42/42 riprodotto,
> risultato d'autore byte-identico, 9/9 artifact e 5/5 dipendenze, zero `.pyc`).
> L'identità di clausola sta nella struttura (una sola `projection` per
> clausola, `dependencies` annidate, span solo come prova, `source_ordinal`
> sull'appiattimento derivato) e la correzione **regge**: su 4.000 frame
> schema-validi casuali i quattro codici che uccisero il live V26.5.6.4 non si
> accendono mai. Confermati anche archi fra clausole, Unicode su 9 scritture ×
> NFC/NFD/NFKC/NFKD, contaminazione 0 su prompt **e** schema, zero letterali di
> superficie, `DERIVED_REFERENCE_BRIDGE` confinato al self-test. Bloccano invece
> quattro difetti offline: l'espansore **non è totale** (cinque input scalari lo
> fanno sollevare e lo stadio 2 della condotta non è protetto: il caso si perde
> su tutti e tre gli stadi, cioè il guasto V26.5.6.4); l'impronta S4 è
> query-free **solo** sui frame schema-validi; `typed_ambiguity` con clausole
> fuori registro riapre `clause_ids`, il codice che uccise V26.4.1;
> `max_atoms_per_analysis` non è imposto dallo schema di risposta e il validator
> ritorna subito dopo il proprio schema interno, azzerando la misura semantica.
> Report e sonda deterministica in `candidates/v26565/`. Registro tipizzato e
> validator congelati **riusati invariati**. Nessun live, nessun gate: prossimo
> passo = successore che chiude i quattro blocker offline. Il registro ha 10
> relazioni;
> snapshot 96 route e checkout 103 manifest hanno overlap 81, con
> `intent_contract` 0/103. Il disegno a tre layer è congelato in
> `catalog_registry_v1/README.md`: non certifica i 109 e non autorizza cutover.
> Leggere anche l'handover, le review in `v26562/` e `v26563/`, e il checkpoint
> review MD/JSON e gate/verifier preflight in `candidates/v26564/`.
> V26.2–V26.4.1 e i relativi gate non vanno rilanciati.
>
> **ORACOLO PHASE 1**: leggere `oracles/phase1_v1/README.md`. Le metriche sono
> separate per binding, dependency, coverage, safety ed evidence; il vecchio
> exact a sette campi non è un gold oggettivo.

Questa directory contiene i checkpoint durevoli dell'analisi iniziata il
7–8 agosto 2026. Un agente nuovo, incluso Claude, deve partire da:

1. `../../design/handover_request_analysis_8_8_2026.md`;
2. `../../design/analysis_request_analysis_strutturata_8_8.md`;
3. `../../design/review_request_analysis_residui_8_8_2026.md`.

Non partire dal solo benchmark: i documenti spiegano denominatori, freeze,
contraddizioni del gold, limiti dei manifest e cosa non deve essere portato in
produzione.

## File versionati

- `unified_query_bench_v23_checkpoint.py`: checkpoint del runner V23 usato per
  costruire V23lite/V24; è un laboratorio, non codice runtime.
- `request_analysis_adversarial_v1.py`: fixture adversarial multilingue
  congelata. Il digest logico della suite è
  `620803a44fc63e88d4b613963ca55e7f9a82a4c87f2f6c912b1e65904ea472f8`.
- `question_focus_controls_v1.json`: 34 controlli per il residuo position
  focus, 9 positivi e 25 negativi in 8 lingue; SHA-256
  `22dac65689f720e07022ac204ba0fd25feebdf5e8db98a9d89f5bf00c120dcbf`.
- `intent_gold_adjudication_overlay_v1.proposed.json`: unica correzione
  proposta del gold spreadsheet; SHA-256
  `8392aabcfaa2c40b14eb69371a0f6e0a0efc9e4faf1268e3308f79319f1f9723`.
  È `proposed_pending_human_review`, evaluation-only e vietata a runtime,
  prompt e projector.
- `intent_gold_adjudication_overlay_v1.1.shadow.json`: stessa adjudication con
  provenance più completa, congelata per le sole misure shadow; SHA-256
  `892358b893d941a885d17184a21c756b14df5c2082e0c30b37e058b379317577`.
  Non equivale ad approvazione dei metadata di produzione.
- `candidates/`: archive immutabile dei freeze V24/V24.1 e V25, con codice,
  schema, prompt, lock, mutation e risultati; leggere il README interno prima
  di ricostruire gli esperimenti. `candidates/v26564/` contiene anche i due
  output del live K1/34 archiviati byte-identici (`9c15cc29…fc04` e
  `c2602051…4522`), il post mortem causale e la sonda offline che riproduce la
  causa senza rete né modello. `candidates/v26565/` è il successore
  clause-owned: schema e prompt generati dal registro congelato, condotta
  best-effort a tre stadi, self-test e mutation suite 42/42, freeze
  `inference=false`, nessun live; con la review indipendente **STATIC BLOCK**,
  la sua sonda deterministica e il risultato della sonda.
- `oracles/phase1_v1/`: audit e oracolo tipizzato indipendenti per i 34
  controlli. Contiene report, schema, overlay e hash-chain; le copie sono
  byte-identiche agli artifact congelati. Non modifica né sostituisce la
  fixture originale.
- `prompt_contamination_audit.json`: audit redatto contro 109+34+70. Non
  contiene query o n-gram in chiaro. I prompt V25–V25.2 hanno zero query intere
  e zero overlap di almeno quattro token, ma conservano otto righe con esempi o
  mapping surface IT/EN; perciò non ricevono credito per l'obiettivo massimo.

## Stato numerico al checkpoint

- baseline legacy: 96/109;
- V23lite raw: 98/109, 109/109 validi;
- V24.1 su V23lite: 107/109 strict legacy, 108/109 adjudicato, zero
  regressioni;
- V25 targeted: route 16/16 e current-location 7/7 in sei lingue, ma evidence
  strict soltanto 7/16 perché i claim opzionali non vengono emessi;
- V25.3, primo output: 34/34 sul solo riconoscimento binario, ma 24/34 sulla
  tupla semantica completa; è una diagnosi post-hoc, non un risultato di
  accettazione;
- V26.2: evaluator storico 26/34 valutabili; con `unsupported` correttamente
  fail-closed la copertura è 19/34 e il binding è 16/19. Solo 5 tuple complete;
  prova negativa congelata, non rilanciare;
- V26.4: K1 non valutabile per `PermissionError(errno=1)`, zero richieste
  accettate dal server; il gate è consumato e l'accuracy resta non misurata;
- V26.4.1: freeze runner-only, semantica V26.4 invariata, 69/69 core,
  125/125 graph e 72/72 infra; freeze
  `6f98647d6a9ea3b46ec521dca50e9f5bb00352d9b9360047219bd81976cd6eff`,
  review runner-only PASS, gate preflight/live pendenti;
- soglia di sostituzione: 108/109 adjudicato, già raggiunta da V24.1, purché
  non emergano regressioni strutturali, safety leakage o instabilità; 109/109
  resta un obiettivo di rifinitura. Pubblicare separatamente strict legacy e
  mantenere 9/9 positivi, 0/25 leakage e K>=5.

Gli artifact intermedi V24.1/V25 sono elencati con hash nel handover e, al
checkpoint, risiedono in `/tmp`. Prima di usarli verificare gli hash e copiare
nel repository soltanto il candidato finale congelato.

## Regole di ripresa

- Preservare il worktree sporco elencato nel handover; non ripristinare file
  dell'utente.
- Non modificare il gold originale in place.
- Non aggiungere stopword, suffissi, sinonimi o frasi trigger della lingua
  sorgente né nel codice né nel projector.
- Il catalogo tecnico può e deve avere ID/enum/contratti finiti: questo non è
  hardcoding linguistico.
- Congelare schema, prompt, validator, projector, catalog e fixture prima di
  ogni run di accettazione.
- Pubblicare invalidità, retry, grounding, latenza e regressioni, non soltanto
  exact match.
- Dopo il 100%, costruire il holdout storico con redazione, deduplica e oracle
  indipendente come descritto nel handover; non usare il vecchio piano come
  gold automatico.
- Il holdout deve includere esplicitamente richieste multi-azione,
  multi-dominio, con dipendenze, anafore, condizioni, negazioni e quote. La
  suite principale contiene già 24 richieste composte, ma non sostituisce
  questo blocco storico congelato.

## Confine di produzione

Nessun file in questa directory è collegato al runtime. Il catalog audit è
ancora fail-closed: 0/96 contratti sono production-reviewed. Un risultato di
benchmark non autorizza la rimozione delle liste correnti finché la facade
shadow, i consumer, i manifest, i test di outage e il rollback non sono pronti.
