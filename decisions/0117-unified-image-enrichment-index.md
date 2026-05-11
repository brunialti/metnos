# ADR 0117 — Unified Image Enrichment Index v4

Status: proposed
Date: 2026-05-09
Supersedes: ADR 0086 (image domain indices, 3 separate IDX_TYPES)
Related: ADR 0093 (async indexing build), ADR 0098 (web crawl strategy — pattern riusato),
ADR 0112 (scheduler v2 asyncio), ADR 0113 (named persons registry + composition filters),
ADR 0114 (synth admission policy)

## Context

L'architettura indici immagine introdotta da ADR 0086 ha tre indici di dominio
disgiunti, persistiti in tre cartelle separate sotto
`~/.local/share/metnos/index/image/<sha8>/{scene,persons,gps}/`:

- `scene` = embedding SigLIP-base 768-dim (uno per foto), per match testo→immagine.
- `persons` = embedding ArcFace 512-dim (uno per faccia rilevata), per match identita'.
- `gps` = (lat, lon, alt) da EXIF, niente modello ML.

PR4 (5/5/2026) ha aggiunto 9 enrichments cheap/medium per-entry calcolati al
build time (`runtime/index_schema.py::ENRICHMENTS`), ognuno marcato con un
campo `domain` che decide a quale dei 3 indici si applica
(`fields_for_domain(idx)`). Il risultato e' un'architettura sparsa:

- 3 cartelle storage indipendenti per la stessa foto.
- 3 entries.jsonl che possono divergere (entry rinviata a un build, persa
  in un altro).
- 3 schema_version separati. La migration v1→v2 (PR4) ha dovuto iterare
  ognuno dei 3 in cicli distinti.
- Compositive pipeline (es. «Silvia al mare») richiede `paths_filter` come
  ponte fra due esecuzioni distinte di `find_images_indices`, una per
  `idx=persons` e una per `idx=scene`. Costo: due passate, due cap top_k.

Il bug live «foto di Silvia al mare» (5/5/2026 sera) ha esposto il limite:
quando `find_persons_indices(name="silvia")` ritorna una manciata di paths
e poi il secondo step interroga scene su quel sottoinsieme, l'indice scene
e' stato costruito con una soglia/parametri diversi e la foto «giusta»
puo' essere mancante in uno dei due. La query NL chiede UNA cosa («foto di
silvia al mare»); l'architettura impone DUE indici a essere coerenti.

Inoltre, il segnale `description / keywords / location_hint / activity_hint`
che un VLM moderno (Qwen2-VL-7B) puo' produrre rende lo scene SigLIP
ridondante: una description testuale ben formata, embeddata via MiniLM/BGE,
copre il caso text→image in modo piu' preciso e in piu' aggiunge keyword
tag-like utili per filtri composti («keywords contains 'mare'»).

Architectural debt:
- Ogni cambio schema = 3 migration paths.
- Ogni nuovo enrichment = decidere `domain` (artificiale: alcuni servono
  a tutti).
- Pipeline compositive = ginnastica di `paths_filter`/from_step.
- 3× lookup I/O in find time per query semplice.

## Decision

Unificare i 3 indici in UN indice composito per corpus. Schema v4 con due
layer ortogonali per-foto:

1. **Identity layer (ArcFace)** — `faces[]` annidato nell'entry foto, ogni
   faccia con `bbox`, `embedding_face` (512 base64), `detect_score`,
   `landmarks?`. Ereditato dal vecchio indice persons, ma denormalizzato
   per-foto invece che per-faccia (1 entry foto contiene N volti).
2. **Visual / semantic layer (VLM)** — `description`, `keywords`,
   `location_hint`, `activity_hint` generati da Qwen2-VL-7B-Instruct
   (single call/foto), piu' `embedding_text` (MiniLM/BGE su description,
   384/1024 dim) per ricerca semantica. SOSTITUISCE l'embedding SigLIP
   (rimosso dal nuovo schema; `clip_embedding.py` non viene cancellato per
   ora ma non e' piu' richiamato dal builder unified).

EXIF resta on-demand: `exif_gps` (`{lat, lon}` o null), `taken_at_iso`,
`image_w/h`, `mtime`, `size`, `sha256`.

Storage:
```
~/.local/share/metnos/index/image/<sha8>/unified/
    entries.jsonl          # una riga per foto (NON per faccia)
    embeddings_text.npy    # shape (N, 384 o 1024) float32
    embeddings_face.npy    # shape (M, 512) float32, M = sum(faces)
    meta.json              # schema_version=4, model_text, model_vlm, ...
```

Schema entry (v4):
```json
{
  "path": "/.../IMG_001.jpg",
  "sha256": "...",
  "name": "IMG_001.jpg",
  "mtime": 1715000000.0,
  "size": 4_500_000,
  "image_w": 4032,
  "image_h": 3024,
  "taken_at_iso": "2024-08-12T15:30:00",
  "exif_gps": {"lat": 44.4, "lon": 8.9},

  "description": "Una bambina sorridente in costume da bagno corre sulla spiaggia, mare blu sullo sfondo, sole pomeridiano.",
  "keywords": ["bambina", "spiaggia", "mare", "costume da bagno", "estate"],
  "location_hint": "spiaggia",
  "activity_hint": "correre",
  "embedding_text_idx": 0,

  "faces": [
    {"bbox": [120, 80, 340, 510], "detect_score": 0.997, "embedding_face_idx": 0,
     "landmarks": [[210, 195], [275, 195], [243, 240], [220, 295], [265, 295]]}
  ]
}
```

`embedding_text_idx` e `embedding_face_idx` puntano a indici nei file
`.npy` per evitare base64 inflation in JSONL.

### Migration v3→v4

`runtime/index_schema_upgrade_v4.py`:

Per ogni `<sha8>/` con scene/persons/gps esistenti:
1. Legge entries da tutti e 3 (riusa codice di
   `index_schema_upgrade.py`).
2. Per ogni path distinto, costruisce una unified entry:
   - Riusa face embeddings dal vecchio persons (denormalizzato:
     persons aveva 1 riga per faccia → unified ha 1 riga per foto con
     `faces[]`).
   - Riusa GPS da gps/entries.jsonl se presente.
   - **Chiama VLM** (HTTP a `localhost:8081`) per generare description /
     keywords / location_hint / activity_hint.
   - **Chiama MiniLM/BGE** su description per embedding_text.
3. Atomic write `unified/entries.jsonl` + `embeddings_*.npy`.
4. Bump `meta.json::schema_version=4`.
5. NON cancella scene/persons/gps subdirs in questa fase. Cleanup
   separato (futuro phase 11) dopo verifica unified completo.

Boot hook idempotente in `metnos_http_server.make_app`: se trova
`schema_version<4` su un indice, spawna migration via systemd-run --user
(reuso pattern ADR 0093). NON blocca il boot.

### Find unified

`executors/find_images/find_images.py` (rinominato da find_images_indices):
- Drop `idx` (un solo asse).
- Args: `query_text`, `name`, `reference_images`, `min_face_pixels`,
  `min_face_count`, `max_face_count`, `paths_filter`, `top_k`,
  `base_path`, `time_window`, `near_lat/lon/radius_km`.
- Internal:
  - Carica `unified/entries.jsonl` UNA volta (one-shot lookup).
  - Identity filter (`name`/`reference_images`): ArcFace cosine vs
    `faces[].embedding_face`.
  - Composition filter (`min_face_pixels`/`min_face_count`/`max_face_count`):
    su `faces[].bbox` + `len(faces)`.
  - Content filter (`query_text`): cosine su `embedding_text` PIU'
    BM25 su `description+keywords` (compositive a livello di score).
  - Time/GPS filters on-demand.
- Manifest: `idx=` arg ignored con log warning (backward compat path
  ammesso solo se schema v4 disponibile; schema v3 → error_class
  `schema_too_old`).

### find_persons_indices come thin alias

3 righe: `name=` arg → invoca `find_images(name=...)`. Ritorna stesso
shape entries. Mantiene retrocompatibilita' a livello di routing PLANNER
ma il vero engine e' uno solo.

## Trade-offs

### Pro

- Single source of truth per foto. Niente divergenza inter-indici.
- Compositive query «X+Y» risolte in una passata, top_k corretto.
- VLM description e' searchable in lingua naturale: «bambina che corre»
  matcha senza embedding magic. Embedding_text serve per query a-la
  «emozione vacanza» dove il keyword match fallisce.
- Rimuove SigLIP scene: -1 modello da gestire (~440MB onnx). Qwen2-VL e'
  l'unico nuovo asset (~7GB Q4_K_M, ma sostituisce SigLIP + offre keyword
  generation che SigLIP non ha).
- Aderisce §7.1 (no shim): drop scene/gps come storage indipendenti
  invece di mantenere un wrapper.

### Contro

- Build cost ~30h per 30k foto stimato (Qwen2-VL ~3s/foto su Strix Halo
  GPU; ArcFace gia' contato; serial bottleneck VLM single-stream).
- VLM allucinazioni: description sbagliata → embedding_text e keywords
  sbagliati. Mitigazione: prompt prescrittivo IT che chiede SOLO cose
  visibili, schema JSON strict, retry su parse fail.
- Migration v3→v4 NON e' idempotent come PR4 (richiede VLM che potrebbe
  essere down): unified/ vuoto fino a quando il batch finisce. Mitigazione:
  scene/persons/gps NON cancellati finche' unified verificato; find
  fallback su v3 se schema_version<4 e flag esplicito.
- Rebuild end-to-end vs upgrade incrementale: la prima migrazione costa
  30h, ma sblocca capacita' che un upgrade sparso non offre.

### Numeri target

- LOC totali (codice produzione + test, post-fase 8): target ~2000.
  Distribuzione approssimativa: schema 100 + create 400 + migration 300
  + find 600 + manifest+planner 200 + test 600.

## Consequences

- ADR 0086 superseded per la parte "3 IDX_TYPES separate". La parte
  "indici di dominio in-process" resta valida.
- `IDX_TYPES = ["scene", "persons", "gps"]` deprecato (rimosso da
  `index_schema.py` post-migration).
- `ENRICHMENTS` PR4 sopravvive come `LEGACY_ENRICHMENTS_V3`, riferito
  solo dalla migration. Future enrichments si aggiungono come campi
  diretti in `UNIFIED_FIELDS`.
- Manifest `find_images_indices` rinominato `find_images` con bump major.
- `find_persons_indices` mantiene il nome (thin alias a find_images con
  arg `name`).
- Test suite: 5 nuovi file, ~50 test totali (vedi phase 8).

## References

- ADR 0086 — image domain indices (superseded scope: 3 separate IDX_TYPES).
- ADR 0093 — async indexing build (riusato per migration spawn).
- ADR 0098 — web crawl strategy (pattern di `error_class` + soft-fail).
- ADR 0112 — scheduler v2 asyncio (boot hook spawn).
- ADR 0113 — named persons registry (capabilities di composizione).
- ADR 0114 — synth admission policy (politiche di admission).
