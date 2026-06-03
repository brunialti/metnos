# Matrice deterministica `intent × data_kind → presentazione`

> Proposta sistemica (31/5/2026) — da rivedere PRIMA dell'implementazione.
> Obiettivo: la modalità di output NON è scelta dall'LLM-proposer (oggi sceglie
> `describe_entries`=testo anche dove serve una gallery), ma è una **funzione
> deterministica** di due input già disponibili a runtime:
>   - `intent.verb` (da intent_extractor)
>   - `data_kind` del PRODUCER terminale (lookup deterministico executor→output)
> Il runtime sceglie il terminale di presentazione e **sovrascrive** la scelta
> del proposer. §7.3 generale, §7.9 deterministico, ADR 0095.

## 1. Modi di presentazione (canonici, ancorati a output_format.py)

| sigla | modo | builtin esistente | descrizione |
|---|---|---|---|
| **S** | SCALAR | `format_kv` | un numero/metrica + unità ("31445 foto", "77 GB", "GPU 61°C") |
| **G** | GALLERY | attachments (img) | 20 preview inline + 100 gallery; header 1 riga; NIENTE descrizioni verbose |
| **T** | TEXT_SUMMARY | `describe_entries`/`format_section` | sintesi in prosa (mail, articoli web, profilo persona) |
| **L** | LIST | `format_list`/`format_table` | elenco compatto di identificatori/righe (path, nomi, eventi) |
| **W** | WEB_RESULTS | `format_search_results` | link titolati con snippet (ricerca web di soli link) |
| **M** | GEO | `format_kv` + link mappa | coordinate/indirizzo (+ eventuale link mappa) |
| **R** | ACTION_RECEIPT | `format_kv`/`format_list` | esito strutturato: "N creati/spostati/inviati/eliminati" |
| **F** | FILE_DELIVERY | attachment file | file prodotto (doc/xlsx/zip) come allegato + 1 riga nota |
| **D** | DIALOG | get_inputs/request_* | richiesta input/consenso (non è presentazione di dati) |

## 2. Classi di intent (raggruppamento dei 23 verbi per OUTPUT)

- **COUNT/METRIC**: `compute` (+ marker "quanti/quanto/quante/numero di/quanti GB").
- **VISUALIZE**: `render` (+ marker "mostra/fammi vedere/visualizza/guarda").
- **READ**: `read`, `describe` (contenuto da sintetizzare).
- **ENUMERATE**: `find`, `list`, `get` (produttori; presentazione dipende da data_kind).
- **TRANSFORM**: `filter`, `sort`, `group`, `classify`, `compare` (in-memory; se terminali → modo di default del data_kind).
- **MUTATE**: `move`, `delete`, `send`, `write`, `create`, `set`, `share`, `change`, `order`.
- **PACKAGE/PRODUCE-FILE**: `compress`, `extract`, `create_files_doc/spreadsheet`, `change_*_format`.

## 3. MATRICE — data_kind (riga) × classe intent (colonna) → modo

| data_kind ↓ \ intent → | COUNT | VISUALIZE | READ | ENUMERATE (find/list/get) | MUTATE |
|---|---|---|---|---|---|
| **images** | S | **G** | **G** (+caption) | **G** | R |
| **urls / web** | S | W | **T** (sintesi + fonti) | **W** (link titolati) | R |
| **messages/mail** | S | — | **T** (riassunto) | **L** (mittente/oggetto/data) | R (send) |
| **files** | S ("…e sottocartelle") | G se immagini | **T** (contenuto file) | **L** (path/nome) | R |
| **dirs** | S | — | — | **L** | R |
| **events** | S | — | **L** (ora+titolo) | **L** | R (create/delete) |
| **contacts/persons** | S | G se foto | **T** (profilo/scheda) | **L** | R |
| **places** | S | — | **M** | **M** + **L** | — |
| **processes** | S | — | **T**/tabella stato | **L**/`format_table` | R (kill via admin) |
| **numbers** | **S** | — | S | S | — |
| **texts** | S (righe) | — | **T** | **L** (righe/match) | R |
| **signatures** | S | — | T | **L** | R |
| **packages** | S | — | T | **L** | R |
| **proposals/tasks** | S | — | T | **L** | R |
| **credentials** | S | — | **L** (metadata-only) | **L** | R |

**MUTATE → R uniforme** per tutti i data_kind, ECCEZIONI:
- `create_files_doc/spreadsheet`, `compress_*`, `change_*_format`, `extract_files_zip` → **F** (file delivery).
- `send_messages` → **R** ("inviato a X via Y").

**TRANSFORM (filter/sort/group/classify/compare) terminali** → modo = default ENUMERATE del data_kind sottostante (es. `filter_entries` su mail terminale → L; su immagini → G).

## 4. Input deterministici

- **intent.verb**: da `intent_extractor` (già nel turno). Marker COUNT/VISUALIZE disambiguano find/get/render (deterministico su keyword, no LLM aggiuntivo).
- **data_kind del producer terminale**: lookup deterministico `executor → output_data_kind` (es. `find_images_indices→images`, `find_urls→web`, `read_messages→mail`, `find_files→files`, `get_processes→processes`, `get_location/find_places→places`). Tabella in `vocab`/manifest (`output_kind`).

→ `presentation_mode = PRESENT[(intent_class, data_kind)]` — pura, testabile, deterministica.

## 5. Regole trasversali (risolvono i bug osservati 31/5)

1. **VISUALIZE+images → G, MAI describe-testo**: "fammi vedere foto di X" → gallery delle top-K rilevanti; niente "non ho trovato" testuale, niente descrizioni verbose. (bug run-2)
2. **COUNT → solo S**: "quante foto di X" → solo il numero, niente gallery né descrizioni. (bug run-1: dava numero + descrizioni miste)
3. **No "allargo?" su ricerca semantica ranked** (G/W con scoring): il top-K *è* la risposta; il totale si espone come info ("mostro le 100 più pertinenti di N"), NON come notify-then-ask di troncamento. `truncated_intentional` resta solo per cap user-richiesto (es. find_files top=K esplicito). (bug "Allargo a 31655?")
4. **READ web → T deve avere il contenuto**: se il modo è T ma il producer ha solo metadata (find_urls snippet), il runtime inserisce deterministicamente `read_urls_html` prima della sintesi (no describe su soli snippet → no "Pipeline malformata"). (bug web task #4)
5. Il **proposer non sceglie il terminale di presentazione**: lo decide il runtime. Il proposer resta responsabile solo dei PRODUCER. `describe_entries`/`render` diventano dettagli implementativi del modo T/G scelto deterministicamente.

## 6. Casi limite — DECISI da Roberto (31/5/2026)

1. **"foto di X" senza verbo** → default **G** (gallery). ✅
2. **persons "dimmi tutto su X"** → **T+G** (profilo + foto enrollate se presenti). ✅
3. **"mostrami le mail"** → **T** (sintesi testo, non lista). ✅
4. **processes stato** → **tabella** (`format_table`), non prosa. ✅
5. **"lista/elenco foto"** → **G** (gallery, sempre per images). ✅
6. **Marker VISUALIZE/COUNT** deterministici → confermati. ✅

Marker (keyword, deterministici, IT+EN):
- COUNT: `quanti|quante|quanto|numero di|conta|count|how many|how much`.
- VISUALIZE: `mostra|mostrami|fammi vedere|vedi|visualizza|guarda|show|show me|display|view`.
- (assenza di marker su data_kind=images con verbo find/get → default G).

- **multi-step con cambio data_kind**: presentazione = data_kind dell'ULTIMO producer.
- **Dove vive il resolver**: `runtime/output_policy.py` (NUOVO, puro) — `data_kind_of` + `intent_class` + `presentation_mode`. Override applicato in engine `dispatch`/`executor` dopo i producer. `output_format.py` per il rendering.

## 7. Locus implementazione (a valle dell'approvazione)

1. `vocab.py`: tabella `OUTPUT_KIND[executor]` o `output_kind` nei manifest (data_kind del producer).
2. `pipeline_shape.py`: `presentation_mode(intent_class, data_kind)` pura.
3. `agent_runtime`/`engine/executor`: dopo i producer, selezione terminale deterministica → S/G/T/L/W/M/R/F via `output_format`.
4. Soppressione "allargo?" per modi ranked (G/W).
5. Proposer prompt: rimuovere la scelta del terminale (semplificazione, non più responsabile di describe vs gallery).
