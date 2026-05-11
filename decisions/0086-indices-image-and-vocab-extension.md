---
id: 0086
title: Indici di dominio `*_indices_image` (scene + persons + gps) e ratifica vocab `indices` + qualifier dominio target
date: 2026-05-04
status: superseded-by-0117
superseded_by: 0117  # 2026-05-09 — un solo asse unified (identita' + VLM + EXIF), drop scene/persons/gps separati
area: vocab, executors, image-pipeline
related:
  - 0045  # naming convention compositiva
  - 0072  # adaptive re-rank intra-turno
  - 0075  # prefilter primary tools per object
  - 0085  # docs alignment 4may
  - 0117  # successore (unified image enrichment index)
---

> **SUPERSEDED-BY 0117 (2026-05-09)**: l'architettura a 3 idx disgiunti (scene/persons/gps) e' stata sostituita da UN solo indice unified per corpus (`unified/entries.jsonl` schema v4). VLM (Qwen2-VL-7B su :8081) sostituisce SigLIP per ricerca semantica del contenuto, ArcFace persiste per identita' e EXIF/GPS sono campi nativi nell'entry. `find_images_indices` non accetta piu' `idx=`. Vedi ADR 0117.

## Context

Roberto vuole tre capacita' nel dominio foto, finora non esposte come
executor a Metnos:

1. **Ricerca semantica per scene/concetti** ("trova foto di mare al
   tramonto"), via embedding cross-modale testo-immagine.
2. **Ricerca per identita'** ("trova foto con dentro X"), via face
   detection + face embedding.
3. **Ricerca per prossimita' geografica** ("foto vicine a Roma"), via
   parsing EXIF GPS.

I tre flussi richiedono uno **store persistente** di derivati pre-calcolati
(vettori CLIP, vettori ArcFace, coordinate GPS) per query veloci. Il
filesystem walk + encoding al volo non scala.

Vincolo (CLAUDE.md §7.9): **codice deterministico > LLM**. Tutta la
pipeline (encoding, similarity, ranking) deve essere deterministica;
nessun LLM nel critical path.

Vincolo (CLAUDE.md §10.3): **OSS self-hosted come default**. SigLIP
(open-weights) per scene, InsightFace `buffalo_l` (RetinaFace+ArcFace)
per volti, parser EXIF stdlib per GPS. Tutto in-process, nessun
microservizio HTTP esterno: Metnos puo' replicarsi su nodi multipli senza
dipendenze.

Vincolo (CLAUDE.md §2.2): **vocabolario chiuso e naming compositivo**.
Servono:

- (a) un nuovo OBJECT che rappresenti il "mezzo astratto persistente"
  delle ricerche (l'indice in se');
- (b) un meccanismo per esprimere il "dominio target" su cui l'indice
  agisce (image, message, text, ...).

## Decision

### 1. Vocabolario

**Nuovo OBJECT (15° canonico): `indices`**. Plurale invariante. Oggetto
*strumento* (mezzo astratto), non oggetto-dato. Documentato in
`runtime/vocab.py::OBJECTS` e in `CLAUDE.md §2.2` (contatore aggiornato
da 13 a 15: includendo `proposals` aggiunto il 3/5/2026 e `indices`
aggiunto oggi).

**Nuova famiglia di QUALIFIERS (6° famiglia): "dominio target di
operazione su mezzo astratto"**. Singolare aggettivale degli OBJECTS
canonici: `_image, _message, _text, _file, _event, _package, _signature`.
Si applica QUANDO l'oggetto principale del nome e' un mezzo astratto
come `indices`. Lista chiusa, allineata agli OBJECTS canonici.

**Pattern compositivo accettato**:

    verbo_indices_<dom>

dove `<dom>` e' un qualifier-dominio. Oggi materializzato per `<dom>=image`;
estendibile a `_message` (ricerca semantica mail), `_text` (semantic
search documenti), ecc.

**Eccezione semantica controllata**: `find_indices_<dom>` ritorna entries
del DOMINIO target (foto/messaggi/...), NON `indices`. Pattern accettato
per il fatto che `indices` e' oggetto-strumento (mezzo di ricerca) e il
dominio target e' esplicitato dal qualifier. Documentato in vocab.py
nel boundary di `find` e nel commento di `OBJECTS`.

`create_indices_<dom>`, `delete_indices_<dom>`, `get_indices_<dom>`
ritornano invece status sull'indice stesso (rispettivamente la
costruzione, la cancellazione, lo stato), coerenti con la regola
generale.

Per disambiguare con l'altro pattern equivalente `order_<obj>_<idx>`
(materializza ordinamento persistente del corpus, ADR informale del
3/5/2026): quando l'oggetto principale e' un MEZZO ASTRATTO (`indices`),
si preferisce `create_indices_<dom>`; quando l'oggetto principale e' il
DATO (es. `images`, `messages`) e si vuole materializzare un ordinamento
o derivato durevole su quel dato, si usa `order_<obj>_<qualifier>`. La
distinzione operativa: `create_indices_image` enfatizza il mezzo (l'indice
come oggetto manipolabile), `order_images_similar` enfatizzerebbe l'azione
sul corpus stesso. Il vocabolario sostiene entrambi.

### 2. I quattro executor

| Executor | Verbo | Output | Riassunto |
|---|---|---|---|
| `create_indices_image` | create | status build | Costruisce/refresha l'indice |
| `find_indices_image` | find | entries (foto) | Interroga l'indice (eccezione semantica) |
| `delete_indices_image` | delete | status delete | Cancella l'indice |
| `get_indices_image` | get | entries (status indici) | Introspection |

**Argomento discriminator**: `idx = "scene" | "persons" | "gps"`. Tre
sotto-tipi di indice nello stesso namespace di executor, perche' il flusso
operativo e l'API utente coincidono (path/foto in input, foto in output).
La differenziazione interna (modello, dim del vettore, schema entry) vive
dentro l'executor.

### 3. Modelli e backend

- **scene**: SigLIP-base-patch16-224 (Xenova ONNX quantizzati int8) —
  `text_model_quantized.onnx` + `vision_model_quantized.onnx` + tokenizer
  HuggingFace. Embedding 768 dim, L2-normalized, cosine = dot product.
- **persons**: InsightFace `buffalo_l` — RetinaFace `det_10g.onnx`
  (detection + 5 landmarks) + ArcFace `w600k_r50.onnx` (embedding 512
  dim). Pipeline: detect → align (Umeyama similarity transform a
  template 112x112) → embed → L2-normalize.
- **gps**: parser EXIF stdlib (Pillow + `PIL.ExifTags.GPSTAGS`). Niente
  modello ML.

Tutti i modelli sono **in-process** (no HTTP server esterno). Pacchetti
ONNX caricati lazy via singleton thread-safe (`get_clip_engine()`,
`get_face_engine()`). Backend isolati in `runtime/clip_embedding.py`
(~295 LOC) e `runtime/face_embedding.py` (~480 LOC), entrambi modellati
sul pattern `suprastructure.embedding.onnx_embedding.EmbeddingService`.

### 4. Storage

Indice persistente sotto:

    ~/.local/share/metnos/index/image/<sha8(base_path)>/<idx>/

Ogni `(base_path, idx)` ha la sua dir, identificata dai primi 16 hex
del SHA-256 di `base_path.resolve()`. Tre file:

- `entries.jsonl` — una riga per entry. Per `scene` e `gps`: una entry per
  foto. Per `persons`: una entry per FACCIA rilevata (multi-faccia per
  foto = N entries con stesso source path).
- `vectors.npy` — float32 array shape `(N, dim)` allineato con `entries.jsonl`.
  Assente per `gps` (le coordinate sono direttamente nelle entries).
- `meta.json` — `{version, idx, model, dim, n_entries, base_path,
  last_refresh_at}`.

**Refresh incrementale di default**: confronto `(mtime, size)` contro
filesystem; encode solo le foto nuove o modificate. `force=true`
ricostruisce da zero.

**Niente paletti di "solo locale"**: l'indice puo' essere sincronizzato
fra nodi propri di Roberto (server in piu' location) tramite il
filesystem o un layer di sync futuro. Oggi nessuna restrizione.

### 5. Lazy build

`find_indices_image` costruisce l'indice automaticamente al primo uso se
manca. Output ha `lazy_built: true` + `notice: "indice ... non esisteva,
costruito automaticamente"`. Il runtime prepende il notice all'utente.

### 6. Truncation visibility

`create_indices_image` espone `cap_field="max_files"` quando il walk
filesystem raggiunge il cap (default 50000). `find_indices_image` espone
`cap_field="top_k"` quando l'output e' troncato dal limite top_k.
Coerente con CLAUDE.md §2.7+§2.11.

### 7. Capability

Due nuove capability dichiarate:

- `index.write` — scrittura sotto `~/.local/share/metnos/index/image/**`
  (richiesta da `create_*` e `delete_*`).
- `index.read` — lettura sotto la stessa root (richiesta da `find_*` e
  `get_*`).

## Consequences

- **Pipeline immagini live nel runtime**. Roberto puo' usare query come
  «cerca foto di mare» o «cerca foto con Lucia» o «foto entro 5 km
  dall'ufficio» con risposta in pochi secondi su corpus medi (1000 foto
  ~ 1 minuto la prima volta, secondi i refresh).
- **Pattern estendibile**. Lo schema `*_indices_<dom>` e' apertura del
  vocabolario verso "mezzi astratti di ricerca" senza esplosione del
  numero di executor: ogni nuovo dominio richiede 4 executor (create,
  find, delete, get) ma riusa il pattern di storage e il qualifier-
  dominio. Espansioni naturali: `indices_message` (semantic mail
  search), `indices_text` (RAG su docs).
- **Vocabolario cresce di 1 OBJECT (`indices`) e 5 nuovi QUALIFIERS**
  (`message, file, event, package, signature` — `image` e `text` erano
  gia' presenti nella famiglia "formato file"; la stessa parola serve
  due famiglie semantiche senza ambiguita' al sito di uso).
- **CLAUDE.md §2.2 documenta** la 6° famiglia di qualifier e l'eccezione
  sintattica `find_indices_X → X`.
- **Niente backward-compat**: nessun executor `index_image` o
  `find_photos_semantic` prefigurato; il naming finale e' `*_indices_image`
  da subito.
- **Open carry-over**: (i) test `scene` skippano in env senza
  `tokenizers` python package — Roberto ha `.venv-image-poc` con tutti
  i deps; sara' inglobato nel daemon Metnos al prossimo restart; (ii)
  sync indice fra nodi `.33` e altri server: rinviato a quando emerge
  il bisogno reale; (iii) reverse-geocode `gps` (estrarre nome luogo da
  lat/lon) non implementato in v0.1.0 — riusabile via `find_places`
  esistente come post-processing di `find_indices_image(idx="gps")`.

## References

- `/opt/myclaw/runtime/vocab.py` (OBJECTS bump, QUALIFIERS bump,
  ACTION_MAPPING per `find` + `create`).
- `/opt/myclaw/runtime/clip_embedding.py` (SigLIP backend).
- `/opt/myclaw/runtime/face_embedding.py` (RetinaFace+ArcFace backend).
- `/opt/myclaw/executors/create_images_indices/` (manifest + code; rinominato 5/5/2026).
- `/opt/myclaw/executors/find_images_indices/` (manifest + code; rinominato 5/5/2026).
- `/opt/myclaw/executors/delete_images_indices/` (manifest + code; rinominato 5/5/2026).
- `/opt/myclaw/executors/get_images_indices/` (manifest + code; rinominato 5/5/2026).
- `/opt/myclaw/runtime/tests/test_{create,find,delete,get}_images_indices.py`
  (25 test, 21 always-on + 4 SigLIP-gated).
- `/opt/myclaw/CLAUDE.md` (§1, §2.2, §10.6.13, ADR registry bump 85→86).

## Update 2026-05-05

**Rename `*_indices_<dom>` → `*_<dom>_indices`** (4 executor): `indices`
cessa di essere il 16° OBJECT canonico e diventa qualifier nella nuova
famiglia "modalità". Schema di lettura `azione_oggetto[_modalità]` —
più trasparente per LLM medium che riconoscono il dominio principale
(`images`) come oggetto e il mezzo di ricerca (`indices`) come modalità.

Razionale operativo:

- **Lettura più diretta**: `find_images_indices` = "trova immagini via
  indici", l'oggetto target è subito chiaro. La forma precedente
  `find_indices_image` invertiva oggetto/modalità e richiedeva la
  postilla "eccezione semantica" per spiegare che l'output era foto.
  La forma nuova rende l'eccezione meno costosa (resta, ma il pattern
  è leggibile da subito).
- **Tassonomia qualifier semplificata**: famiglie collassate da 5 a 3
  (formato / modalità / safety). Modalità assorbe le ex famiglie
  "operazione" (`_size, _format, _similar, _loc`), "granularità di
  dominio" (`_lines, _paragraphs, _sentences, _pages, _segments`) e
  "dominio target di mezzo astratto" (ora ridondante perché il dominio
  è l'OBJECT principale, non un qualifier). `indices` entra come
  nuovo qualifier di modalità.
- **OBJECTS 16 → 15**: rimosso `indices`. La lista chiusa torna ai 15
  domini di prima classe + `signatures` + `inputs`.
- **Conservazione**: `signatures` resta OBJECT di prima classe (entità
  persistente classificata, ADR 0071). Solo `indices` viene declassato
  perché era veramente un mezzo astratto, mai un'entità autonoma.

Tabella aggiornata degli executor (rispetto alla §3 sopra):

| Nome canonico (5/5/2026) | Verbo | Output       | Note |
|--------------------------|-------|--------------|------|
| `create_images_indices`  | create | status build  | Costruisce/refresha l'indice |
| `find_images_indices`    | find   | entries (foto)| Interroga l'indice (eccezione semantica) |
| `delete_images_indices`  | delete | status delete | Cancella l'indice |
| `get_images_indices`     | get    | entries (status indici) | Introspection |

Manifest re-firmati Ed25519 (digest aggiornato), filesystem rinominato,
Python module renamed, test moduli rinominati, riferimenti runtime
aggiornati (`prefilter._OBJECT_PRIMARY_TOOLS["images"]`, commenti
agent_runtime / photo_endpoint / http_routes_agent / channels/daemon),
vocab.py aggiornato (15 OBJECTS, qualifier `indices` aggiunto a famiglia
"modalità"), CLAUDE.md §2.2 + §10.6.13 aggiornati.

**Niente backward-compat** (CLAUDE.md §7.1): nessun alias `find_indices_image`
in catalog. La rotta è il nome canonico nuovo.
