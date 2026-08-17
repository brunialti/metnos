# V26.3 author freeze — NON AUTORIZZATO

Stato: **freeze autore pre-run; nessun run nativo eseguito; esecuzione non
autorizzata**.

Questo snapshot conserva byte per byte il candidato V26.3 preparato offline.
Il candidato mantiene i campi semantici minimi V26.2, ripristina il registro
tecnico completo e simmetrico, usa segmentazione UAX #29, una call e zero
retry. Non contiene liste, sinonimi o esempi della lingua sorgente.

Prima di qualsiasi chiamata modello mancano obbligatoriamente:

1. audit indipendente dell'oracolo e dei criteri di misura;
2. review indipendente di prompt, schema, validator e policy fail-closed;
3. un gate lock separato che leghi con SHA-256 review, freeze e audit;
4. verifica della catena completa delle dipendenze;
5. autorizzazione esplicita del coordinatore al singolo run congelato.

`metnos_v263_phase1_author_pre_gate.json` è stato rinominato apposta. Il suo
campo storico `inference_allowed=true` significa soltanto che i controlli
statici dell'autore erano verdi. **Non è un'autorizzazione operativa** e non
sostituisce review indipendente o gate lock. Non va passato al runner per
eseguire inferenza.

Non esiste in questa directory alcun risultato live V26.3. Un file risultato
apparso prima della review e del gate lock sarebbe fuori protocollo e non
riceverebbe credito.

## Esito offline

- mutation: 37/37;
- righe prompt surface/mixed: 0;
- query intere nel prompt: 0 su 109 + 34 + 70;
- overlap contiguo di almeno tre token: 0 su 109 + 34 + 70;
- chiamate modello/server: 0;
- V25.3 primo tentativo: binding binario 34/34, ma tupla completa a sette
  campi 24/34;
- V26.2: relazione 17/34 e `interpretation` 19/34 contro 32/34 e 33/34 nel
  primo tentativo V25.3.

Il confronto sostiene la diagnosi “contratto tecnico rimosso”, ma non la
dimostra causalmente: V25.3 esponeva anche campi di scaffolding ridondanti.
Soltanto un nuovo run nativo del freeze, dopo review, può testare l'ipotesi.

## File byte-identici

| File | SHA-256 |
|---|---|
| `metnos_v263_phase1_runner.py` | `57580bfc8ee7576b1486bb6f9990d90926d0bc82aa8c4006c7058d8f6d8ea7ac` |
| `metnos_v263_phase1.prompt.txt` | `7ae33768167dd4803903008e1e1a690c99d6da7a4e718404be6d194526e74146` |
| `metnos_v263_phase1.schema.json` | `163aeea62eba817c3390d8b2ff7610bf82bb1b31ffd5f914274754ca4034d360` |
| `metnos_v263_phase1_controls_frozen.json` | `9196cd736eee25aeabb5b1c1b4803c8b63e71f8c32f63034bf3bfe336f4c32e8` |
| `metnos_v263_phase1_mutations_frozen.json` | `6c5608092b417d5d52a2643e93ffb8f2c34b448ae40ae2c0c5400a00cff2babc` |
| `metnos_v263_phase1.freeze.json` | `80c1b4e70f1ef5cb75d1ddea1458f3a4934598729a03c3c552c562fe8eeaeb0c` |
| `metnos_v263_phase1_contamination_audit.json` | `4a3de10ed31eaa8e10049bc255632bd320df6bae366c1533e6395ac515743769` |
| `metnos_v263_phase1_author_pre_gate.json` | `2f7ddffd6cb37f0e7b380adcb9935dba6a3cddc4cfa990b0dec38701aa8ddde8` |
| `metnos_v263_offline_review.md` | `9921d17f4bb7d44f159c75459776dd861fae9946a3d4bf4ff19d9972fe4f7b62` |
| `metnos_v263_offline_delta.json` | `0d3393aafae4c14f62bcc8644f3b7cbf9fde8f433e7923d19c3d87b215cb1054` |
| `metnos_v263_offline_analysis.py` | `e4dcb41d8e49d7587a5cc4a27de67efa88f12e487ec3e14453c0cd28e9125bf7` |

Gli hash sopra sono quelli degli originali `/tmp`. Il README è nuovo e non fa
parte del freeze.

## Dipendenze da preservare

Il runner congelato usa path assoluti di laboratorio e importa la catena
V26.2 → V25.3. Prima di un replay, materializzare le copie esatte in `/tmp` e
verificarne gli hash senza modificare il runner:

| Dipendenza | SHA-256 |
|---|---|
| `/tmp/metnos_v262_minimal_phase1_runner.py` | `0b6ab465fc9cfd19ddd2a59c49d2ce0d5ff4e31867ebc8d0f1c2aa8e558b5cb8` |
| `/tmp/metnos_v262_minimal_phase1.freeze.json` | `757f461e26cb39083c039d4a4f3871e56e94a3bc30e5c838ff8a1401d5e8ff9d` |
| `/tmp/metnos_v253_phase1_relation_runner.py` | `c417278c40aca616b07b95834f75043920e3dc2427a729ab245a7c3f9bf74615` |
| `/tmp/metnos_v253_phase1_relation.freeze.json` | `758ffae7a6487cdd103712f2ecb532455f4662ee8e60abf9bbd9a218bc95a4bb` |
| `/tmp/metnos_prompt_contamination_audit.py` | `1cedfa7115744da79764c6b2fd31c2be67eada3fcc18d23bb46a5108eb190169` |

V26.2 è conservato nella directory sorella `../v262/`; V25.3 è ora conservato
in `../v253/`; il bench V22 e l'auditor esatti sono in `../replay_deps/`.
La mappa completa basename→`/tmp`, inclusa l'eccezione di sicurezza del gate,
è in `../REPLAY_CHAIN.md`.

## Ordine delle alternative

Il solo candidato da revisionare ora è A: atomo relazionale minimo con
contratto tecnico completo. Le alternative restano in riserva e non vanno
lanciate in parallelo sullo stesso design set:

1. due sezioni sintassi/semantica nella stessa risposta se A fallisce sulla
   struttura delle clausole;
2. algebra di candidati relazionali se A forza una relazione o abusa di
   `ambiguous`;
3. fact table più unificatore deterministico di catalogo se la scelta di
   relazione resta instabile fra domini.
