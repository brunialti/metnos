# V26.5.6.5 — successore clause-owned, checkpoint OFFLINE

Status: **STATIC BLOCK** indipendente (9/8/2026), su un author checkpoint
`inference=false` con self-test **42/42 PASS**. Zero rete, zero chiamate
modello, zero trasporto, zero gate consumato, nessun live. V26.5.6.4 e il gold
non sono stati toccati.

La review riproduce 42/42, il risultato d'autore byte-identico, 9/9 artifact,
5/5 dipendenze e 10/10 righe di hash, e **conferma la correzione S1**: su 4.000
frame schema-validi casuali i quattro codici che uccisero il live V26.5.6.4 non
si accendono mai. Blocca invece su quattro difetti offline: l'espansore **non è
totale** (cinque input scalari lo fanno sollevare, e lo stadio 2 della condotta
non è protetto, quindi il caso si perde su tutti e tre gli stadi); l'impronta S4
è query-free **solo** sui frame schema-validi; `typed_ambiguity` con clausole
fuori registro riapre `clause_ids`, il codice che uccise V26.4.1;
`max_atoms_per_analysis` non è imposto dallo schema di risposta e lo stadio 3
censura sé stesso quando il proprio schema interno fallisce. Dettaglio e
riproduzione: `metnos_v26565_independent_static_review.{md,json}`, sonda
`metnos_v26565_independent_static_review_probe.py` (seme fisso, deterministica;
non fa parte del freeze).

Questo candidato applica S1-S4 del post mortem
`../v26564/metnos_v26564_live_postmortem.md`, che spiegava lo 0/34 del live
K1/34.

## La causa e la correzione

V26.5 aveva tolto `clause_id` e promosso lo **span sorgente a chiave primaria
dell'identità di clausola**. Ne seguivano due invarianti globali — span
iniettivo sulle ancore, e uguaglianza *esatta* di span fra dipendenza e
proiezione — non esprimibili nello schema JSON, quindi non imponibili alla
decodifica vincolata, vivi solo nella prosa del prompt e **fatali prima di
ogni validazione semantica**.

V26.5.6.5 sposta l'identità di clausola **nella struttura del documento**:

```
{"status":"supported",
 "clauses":[ {"clause_start_segment_id":…, "clause_end_segment_id":…,
              "clause_role":…, "clause_role_proof":…,
              "projection":{…},            <- oggetto singolo, non lista
              "dependencies":[ … ]} ]}     <- annidate nella loro clausola
```

- **identità = posizione** nell'array delle clausole; nessun `clause_id`,
  `atom_id`, `alternative_id` o `output_index` viene emesso;
- **una sola proiezione per clausola**: una seconda è *irrappresentabile*, non
  vietata a parole;
- **dipendenze annidate**: appartengono alla clausola per contenimento, quindi
  nessuna uguaglianza di span serve più ad attaccarle;
- **span solo come prova**: due clausole possono condividere lo stesso span, e
  una dipendenza può avere lo span che la sua evidenza sostiene;
- **`source_ordinal` deterministico**: indicizza l'appiattimento derivato
  (per clausola, in ordine: dipendenze, poi la proiezione; clausole in ordine
  d'array), quindi gli archi fra clausole restano esprimibili senza etichette.

## S2: l'insieme fatale è esattamente lo schema

L'espansore è **totale**: non solleva mai, su nessun input. Tutto ciò che non
sa derivare diventa un codice raccolto e non fatale. Le uniche invalidità
fatali sono quelle che lo schema esprime, quindi la decodifica vincolata le
impedisce per costruzione. Il validator congelato V26.5.3 e il registro
tipizzato V26.4.1 sono **riusati invariati**: l'espansore produce esattamente
la forma normale che quel validator già accetta.

Conseguenza verificata: `primary_cardinality`, `orphan_dependency`,
`clause_span_consistency` e `projection_missing` — quattro codici del validator
congelato — non possono più scattare su un frame schema-valido.

## S3/S4: diagnosi non censurante e impronta strutturale

La condotta **non si ferma al primo stadio**: schema, espansione e validazione
girano sempre, ognuno riporta i propri codici. Nel live V26.5.6.4 l'adapter
aveva abortito 33 casi su 34 prima di qualunque controllo semantico, lasciando
il validator non misurato; qui `stages_measured` è 3 anche quando lo schema
fallisce.

Ogni record porta un'**impronta strutturale query-free**, anche sui
fallimenti: stato, conteggi di clausole/atomi/dipendenze/archi, multiinsiemi di
relazioni, ruoli, atti linguistici, tipi di binding e famiglie di prova, e il
solo bit che contava nel guasto precedente (`distinct_clause_spans`). Non
contiene richiesta, testo dei segmenti né alcuno span. Il test di riservatezza
verifica che **ogni stringa dell'impronta appartenga al vocabolario tecnico
chiuso**.

## Self-test 42/42

```bash
/usr/bin/python3 -I -B \
  internal/tools/request_analysis_lab/candidates/v26565/metnos_v26565_offline_selftest.py
```

| Gruppo | Esito |
|---|---|
| materialisation | 4/4 — schema e prompt uguali al generatore, registro pinnato |
| representability | 2/2 — **34/34** casi gold rappresentabili e validi, **34/34** con esattamente la semantica gold |
| regression_of_the_v26564_failures | 4/4 — span collisi accettati, dipendenza con span stretto accettata |
| no_fatal_invariant_outside_the_schema | 5/5 — doppia proiezione, dipendenza orfana e clausola senza proiezione sono schema-invalide; espansore totale su ~60 frame di sonda |
| best_effort_diagnosis | 3/3 — tutti e tre gli stadi misurati anche con schema fallito |
| coverage_shapes | 5/5 — multi-clausola, multi-dominio, misto rappresentabile/fuori registro, typed ambiguity |
| no_emitted_identity | 5/5 — nessuna etichetta numerica richiesta dallo schema |
| unicode_metamorphic | 3/3 — 9 scritture diverse, **una sola impronta** |
| contamination | 2/2 — zero query verbatim, zero 4-gram condivisi |
| fingerprint_privacy | 3/3 — nessun testo, nessuno span, solo denotazioni chiuse |
| fail_closed | 3/3 — registro mutato, registro non-bytes, pin validator alterato |
| mutation_suite | 3/3 — **12/12** mutazioni semantiche respinte, tutte misurate su 3 stadi |

Il gold non è stato modificato: la fixture, l'overlay e i controlli sono letti
in sola lettura. Il ponte `DERIVED_REFERENCE_BRIDGE` del self-test collega due
**registri tecnici congelati** (la notazione dell'oracolo e i riferimenti del
registro tipizzato) per la sola costruzione della fixture: una voce, nessun
token di lingua sorgente, mai usato a runtime.

## Limiti dichiarati

- Il registro resta **Phase-1 con 10 relazioni**: questo candidato **non
  certifica i 109** della suite generale, e nessun risultato qui vale come
  accuratezza.
- Non è stato eseguito **alcun live**: `inference=false`, zero chiamate
  modello, zero rete, nessun gate creato o consumato.
- 42/42 offline non è una misura di qualità semantica del modello: dimostra
  soltanto che il contratto è rappresentabile, che l'insieme fatale coincide
  con lo schema e che la diagnosi non censura.
- Un eventuale live richiede **un gate nuovo**: quello di V26.5.6.4 esigeva
  `output_must_be_absent` ed è esaurito.

## Residuo `__pycache__`: risolto

Il README d'autore segnalava una `__pycache__/` da rimuovere a mano. Alla
review la directory **non è presente**: bundle privo di `.pyc`, verificato
insieme al freeze. Il self-test e la sonda girano con `/usr/bin/python3 -I -B`
e non producono bytecode.

## Prossimo passo

Successore che chiude B1-B4 **offline**, senza toccare prompt, schema, registro,
validator, fixture, overlay o gold, con i quattro test mancanti (contenitori
scalari, impronta su frame schema-invalido, `typed_ambiguity` con clausole fuori
registro, frame oltre `max_atoms_per_analysis`). Nessun trasporto è
autorizzabile e nessun gate va progettato prima di una review PASS.

## Hash

| Artifact | SHA-256 |
|---|---|
| `metnos_v26565_registry_projection.py` | `acdfe3e347f314037a733b784fa3978d4c96ced7f794b0d2868a17f4360eb939` |
| `metnos_v26565_expander.py` | `693803b3863358aa652eb2e8dd7a849981842ffd6bc8b707d3195a3f74a21577` |
| `metnos_v26565_offline_pipeline.py` | `2acd745373e8f518391d1b47099e73dd48f01ad6a4678a8c021e80e8bb5e4cd1` |
| `metnos_v26565_offline_selftest.py` | `8a68394942e1e24eac3f98397330b209ef3cdd31abbbfc631dd7c57f2eb51f35` |
| `metnos_v26565_clause_owned.schema.json` | `3cd2655474d30868090010feb6a21dc08ecfe03f059b99cf3bd85fde85f32b49` |
| `metnos_v26565_clause_owned.prompt.txt` | `0c798be9821a484e718b4c6a5ed8ebc623dee16c08fe8713d1723d22bada5cba` |
| `metnos_v26565_contamination_audit.json` | `b9ee5a25435fe9859ba7bdd333ab57b09f109ef6dea500b7b7922b7b42f5bea6` |
| `metnos_v26565_author_selftest_result.json` | `51b53bc338daea8187f281a61c0ee83046b70f9e6175f013a992c40fb24bcb12` |
| `metnos_v26565_author_pre_gate.json` | `d9bc4f13915e9e83e9f2b11267adc02f880ab255fc0d2983802fe1ac7dbe3add` |
| `metnos_v26565_author.freeze.json` | `b7b02367bba88c244dd772b0722f4925292e4e318eca80b5df0b0556f3638f90` |

Dipendenze congelate riusate invariate: registro tipizzato V26.4.1, validator
iniettato V26.5.3, fixture Phase-1, controlli runtime 34, overlay dell'oracolo.
Il README non fa parte della tabella di freeze; nemmeno i quattro artifact di
review (`metnos_v26565_independent_static_review.{md,json}`, la sonda e il suo
risultato), aggiunti dopo il freeze e che non lo alterano.
