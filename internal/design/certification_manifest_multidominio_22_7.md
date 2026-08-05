# Certificazione dei fix — audit manifest multidominio 22/7/2026

## Esito

Il lotto di correzione derivato da
`internal/design/audit_manifest_multidominio_22_7.md` supera i gate di codice,
catalogo e avvio. I reperti P0/P1 sono chiusi nel codice e nei contratti
interessati; i residui del linter sono avvisi editoriali esplicitamente
classificati, non errori di caricamento o runtime.

Questa certificazione fotografa il worktree e il servizio locale del
22/7/2026. Le prove provider sono circoscritte ai rami OAuth Google descritti
qui sotto; non estendono la certificazione ad altri provider esterni.

## Chiusura dei temi M1-M8

- **M1 — undo:** fallback JIT da pattern sconosciuto a `module.reverse`,
  registro calendari, risultati undo veritieri, backup blob degli overwrite e
  reverse per provider/ramo sono ora coerenti. Le cancellazioni permanenti
  non promettono undo; i ripristini non sovrascrivono occupanti concorrenti.
- **M2 — OUT/schema:** descrizioni e schemi formali dei producer/trasformatori
  segnalati sono stati allineati alle forme realmente emesse, evitando pipe
  vuote presentate come successo.
- **M3 — argomenti:** argomenti effettivamente consumati sono stati dichiarati
  oppure resi esplicitamente runtime-owned; le chiavi private emesse dal
  modello non attraversano il confine di coercizione.
- **M4 — autorita' provider:** le capability dipendenti dal provider sono
  condizionate al ramo che le usa, senza montaggio generalizzato delle
  credenziali.
- **M5 — consenso:** i marker privati non possono essere auto-concessi dal
  planner. Il consenso per contesto locale destinato a un LLM esterno e'
  annotato nel manifest e inserito JIT, immediatamente prima della prima
  invocazione esterna che trasporta dati.
- **M6 — routing:** rimossi nomi propri, prodotti, verbi nudi e termini rubati;
  aggiunti confini reciproci tra executor fratelli e affinity mancanti per
  metadati file.
- **M7 — i18n:** la prosa user-facing di `get_proposals` usa il catalogo i18n;
  tier e descrizioni interessate sono neutrali/multilingua. Seed, database live
  e bundle device sono allineati.
- **M8 — residui puntuali:** OCR limitato e con autorita' dichiarata, fogli XLSX
  coerenti sugli indici, indice immagini unificato, scope path centralizzato,
  argomenti fantasma/riferimenti obsoleti rimossi e semantica `top=0`
  allineata.

## Budget model-facing

La correttezza del prompt non dipende piu' da un taglio fisso uguale per ogni
manifest. Il rendering applica una politica aggregata, deterministica e
bounded:

- teste manifest: budget totale massimo `N * 260 + 60` caratteri, hard cap 320
  per tool; le teste corte cedono il residuo a quelle lunghe;
- descrizioni argomento: slot di partenza 100 caratteri per required e 80 per
  optional, slack unico di pool 180, hard cap 180 per argomento;
- il taglio avviene a confine di parola;
- gli schemi sorgente non vengono mutati;
- nomi, descrizioni e schemi restano allineati anche se il catalogo e' fornito
  come iteratore/generatore.

I limiti fisici di authoring sono 240 caratteri per la testa, 320 per la
description completa e 180 per la description di un argomento. Nessuna testa
dei manifest correnti supera 240: la redistribuzione e' quindi una protezione
pragmatica per casi futuri, non una dipendenza necessaria del catalogo attuale.

## Gate eseguiti

- suite runtime completa: **4.644 passed**, **32 skipped dichiarati**,
  **366 subtest passed**, zero failure;
- birth suite: **351/351** su **82 executor**;
- firme manifest repository: **82/82 valide**;
- standard executor: **0 finding**;
- manifest linter: **0 errori**, **106 warning** su 82 manifest;
- test mirati del budget model-facing: **22/22 passed**;
- `git diff --check` e compilazione dei moduli toccati: verdi;
- servizio riavviato in quiete: `metnos-http.service` attivo, 2 task, health
  `ok=true`, log di startup senza errori.

I 106 warning residui sono: 95 avvisi di lunghezza, 7 euristiche
`output_shape` e 4 riferimenti `NON` morti. Restano debito editoriale da
ridurre con modifiche semantiche mirate; non sono stati risolti tramite tagli
di massa, perché ciò avrebbe potuto ridurre completezza e qualita' del routing.

## i18n e deduplicazione

Baseline certificata: **878 chiavi uniche**, **1.756 stringhe localizzate**
(878 IT + 878 EN), tutte valorizzate. La superficie user-facing comprende 655
chiavi e 1.310 stringhe nel bundle device.

Il controllo deduplicazione e' registrato come `I18N-DEDUP-001` in
`internal/design/TODO.md`: confronto deterministico di collisioni, duplicati
esatti/quasi-identici, placeholder e drift seed/live/device, inizialmente
read-only e senza merge automatici.

## Prove live aggiuntive del 22/7/2026

- Google Docs e Sheets: creazione di due fixture univoche, rilettura,
  acquisizione dell'undo JIT e annullamento 2/2; ricerca Drive successiva con
  zero fixture non cestinate.
- Google Drive: ramo `delete_files` in trash, undo
  `restore_trashed_files`, verifica della ricomparsa e cleanup finale tramite
  undo della creazione; 1/1 ripristino, zero skip.
- I writer di token, client secret e stato PKCE impongono modo `0600`; il token
  installato e' rimasto `0600` prima e dopo refresh/uso live.
- PC-ROBERTO: la query multidominio del TODO REM-001 ha completato 8/8 step,
  12/12 record, estrazione strutturata senza LLM, 5 report e XLSX da 13 righe
  nella stessa directory create-only; zero failure e nessun falso successo.

## Limiti della certificazione

- Il gate finale non ha inviato dati a LLM esterni e non ha eseguito
  cancellazioni permanenti su calendari, persone o provider cloud. Le sole
  mutazioni cloud erano fixture univoche, infine spostate nel cestino Drive.
- Il consenso outbound JIT e i reverse sono coperti da test di contratto,
  integrazione e regressione; i turni live futuri devono usare fixture o dati
  non distruttivi.
- Non sono stati provati live invii Gmail, ACL di condivisione o mutazioni
  Calendar: i relativi test restano strutturali/non distruttivi.

## Verdetto operativo

Il servizio locale e' pronto per un nuovo test live controllato. Per il primo
giro usare una query read-only o create-only con destinazioni nuove; verificare
poi journal, `effect_counts`, `false_success_detected`, artefatti prodotti e
quiete finale dello scheduler.
