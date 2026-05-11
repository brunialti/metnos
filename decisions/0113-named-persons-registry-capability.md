# 0113 — Named persons registry, composition filters, procedural index schema

Status: accepted, 2026-05-08

## Context

Photo search by name was infeasible without uploading reference photos every
query. The `find_images_indices(idx="persons", reference_images=[...])` path
required the user to attach photos of e.g. Matteo each time. No persistent
identity exists in the system.

Composition queries («primo piano di Matteo», «ritratto di Iacopo») were also
unsupported: SigLIP scene index is semantic, not structural — it has no notion
of "face occupies most of the frame". The persons index had bounding boxes per
detected face but no filter on bbox size.

Compositional queries («Matteo al mare», «Iacopo a scout») required scoring
SigLIP only on Matteo's photos, not the global corpus.

The corpus index had a frozen schema (path, name, mtime, size, face_idx, bbox,
score) — no way to add fields like image dimensions, EXIF taken_at, blur score,
brightness without editing the builder per-field. New dimensions (scene/persons/
gps and future `colors`/`blur`/`documents`) were not registry-driven either.

A diagnostic on a real query, «cerca volti in primo piano in Immagini»,
exposed three independent defects: SigLIP returned 100 noise-score entries
(no confidence floor), `describe_entries` ingested all 100 inflating the LLM
prompt to 60 KB, and `find_images_indices` had no cap visibility per §2.7.

## Decision

Add a persistent **named persons registry** orthogonal to the per-corpus
indices. Add **composition filters** on the persons index. Add a
**registry-driven enrichment schema** with bumpable version. Add
**implicit build-all-on-missing** so any query on an unindexed folder
spawns full indexing across all `IDX_TYPES`.

### Storage layer (PR1)

`runtime/persons_registry.py` + `~/.local/share/metnos/persons.sqlite`.
Two tables: `persons(slug PK, name, n_examples, ...)` and
`person_examples(id, person_slug FK CASCADE, image_path, face_box, embedding
BLOB, sha256, ...)`. Slug is case- and accent-insensitive (NFKD + lowercase
+ `-`/`/` → space → `_`); display name preserved on first enroll. Mode
`add` (default) accumulates examples; `replace` wipes. UNIQUE(slug, sha256,
face_box) makes re-enrollment idempotent. Top-k cosine matching at query
time (max over all examples), no centroid. WARN at ≥50 examples per
person. ~376 LOC + 36 tests.

### Executor layer (PR2)

Four new executors:
- `set_persons(name, paths, mode)` — enroll, with `get_inputs`-driven
  multi-face disambiguation (ADR 0090).
- `get_persons(name=None)` — registry lookup, with token-anywhere resolution
  for partial names.
- `delete_persons(name)` — remove + cascading examples; resolves ambiguous
  names via dialog.
- `find_persons_indices(name | reference_images, ...)` — corpus query, with
  `name` resolving via `PersonsRegistry.resolve_name()` (exact match first,
  then token-anywhere). Ambiguous → `decision="needs_inputs"` dialog.

Added `resume_executor_with_values` callback in orchestration for the
disambiguation flow. ~779 LOC + 55 tests.

### Quality and pipeline (mini-PR + PR3)

`find_images_indices`:
- SigLIP confidence floor `_MIN_SIGLIP_SCORE = 0.12` hardcoded; below →
  `entries=[]` + `error_class="low_confidence"`.
- `min_face_pixels: int | None` filter for `idx="persons"`: 10000 ≈ half-bust,
  40000 = primo piano (≈200×200 px), 80000 = stretto.
- Cap visibility per §2.7: `truncated`, `truncated_what`, `used`,
  `available_total`, `cap_field`, `cap_value`.
- `paths_filter: list[str] | None` for compositional pipelines: restricts
  scan to a subset of paths BEFORE scoring (different semantics from
  post-hoc `filter_entries`).

`describe_entries`: `_DESCRIBE_CAP = 20`. Beyond → first 20 only in LLM
prompt + truncated visibility. Prevents 60 KB prompt bloat observed in
the «volti in primo piano» turn.

`find_persons_indices` parity: same `min_face_pixels` arg; same
build-all-on-missing logic.

### Routing (PR3.5)

`runtime/prompts/it/planner.j2` rule **(W) PIPELINE COMPOSITIVA SOGGETTO+
CONTESTO**: «Matteo al mare» → step1 `find_persons_indices(name)`, step2
`find_images_indices(idx="scene", paths_filter=from_step:1)`. Rule
**(W.bis) SOGGETTO + COMPOSIZIONE**: «primo piano di Matteo» → SINGOLO step
`find_persons_indices(name="matteo", min_face_pixels=40000)`. Manifest
affinity expanded with «primo piano», «ritratto», «ravvicinata», «close-up»,
«portrait», «mezzo busto», «half-bust».

### Dialog UX (PR5)

`get_inputs` `kind="choice_with_preview"`: each option has
`preview_image_path` optionally with `#bbox=x,y,w,h` for face crop. HTTP
form renders thumbnails; Telegram sends a media group + inline keyboard
(fallback to label-only beyond 10 options). Used by `delete_persons` (one
example per slug as preview), `set_persons` (multi-face crop selection),
`find_persons_indices` (ambiguous name resolution). New endpoint
`/agent/dialog/<id>/preview/<idx>` with anti-traversal check.

### Procedural index schema (PR4)

`runtime/index_schema.py`:
- `INDEX_SCHEMA_VERSION = 2`.
- `IDX_TYPES = ["scene", "persons", "gps"]` — extensible registry of
  index dimensions.
- `ENRICHMENTS: list[EnrichmentField]` — 9 fields: `image_w`, `image_h`,
  `taken_at_iso` (EXIF DateTimeOriginal), `bbox_area_fraction`,
  `face_count_in_photo`, `is_grayscale`, `brightness_mean`, `is_blurry`,
  `frontal_score`. Each has `(name, compute_fn, domain, cost_class,
  schema_min_version)`.

`create_images_indices`:
- Default `idx=None` → iterate `IDX_TYPES`, spawn one async build per
  dimension in parallel (back-compat: explicit `idx="scene"` builds only
  that one).
- Builder iterates `ENRICHMENTS` per photo, always producing the latest
  schema. Adding a 10th field = one row in the registry + bump version.

`runtime/index_schema_upgrade.py`: invoked at HTTP boot. For each existing
index dir, reads `meta.json`. If `schema_version < INDEX_SCHEMA_VERSION`,
spawns incremental upgrade that recomputes ONLY missing enrichment fields
(reuses `vectors.npy` and existing bbox), atomic tmp+rename, bumps
schema_version.

`find_images_indices` and `find_persons_indices` build-all-on-missing:
when invoked on a base_path with ANY missing IDX_TYPE, spawns async builds
for ALL missing types (not just the requested one). Returns
`decision="needs_build"` with `builds_pending`. Future queries on the
same folder are instant for any dimension. Rationale: minimize round-trips,
the user never has to ask twice.

10 new filter args on both find executors (parity), all using v2 schema:
`min_face_fraction`, `taken_after`, `taken_before`, `is_grayscale`,
`min_face_count`, `max_face_count`, `exclude_blurry`, `min_brightness`,
`max_brightness`, `min_frontal_score`. Schema v1 entries + filter request
→ `error_class="schema_too_old"`.

`find_images_indices` multi-dir `paths_filter` dispatch (PR4): builds a
per-sub-dir mapping of paths_filter, passes the restricted subset to each
sub-index, aggregates and applies the cap globally.

### find_urls topic = ranking, not filtering (POST-PR4)

Removed the implicit drop of score=0 entries when `topic` was passed.
Replaced with explicit `min_score: float | None`. Aligns with the ADR 0098
text "Topic ranking BM25" (rank, not filter). Fixed a long-standing
test_pipeline_smoke failure where the test expected 3 BFS-visited pages
but only 1 was returned.

## Consequences

- ~3000 LOC prod added across 4 new executors + 1 storage helper + 2
  registry modules + manifest updates + planner prompt rule.
- ~3500 LOC tests added: 1093 PASS / 0 FAIL after the rollout.
- Build pattern: index missing → all dimensions in parallel. Minimizes
  round-trips for the user, increases storage by ~5-10% per folder
  (gps EXIF is cheap, persons ArcFace is the heaviest, scene is medium).
- Schema upgrade: existing indices migrate at first boot (minutes, not
  hours, since embeddings are reused). After upgrade, all queries assume
  v2 — no fallback paths kept (§7.1).
- Slug normalization is destructive on accents: «Maria» and «María» map
  to the same slug. Documented edge case; mitigation is to use
  disambiguating display names (`name="Maria_madre"` vs `name="Maria_zia"`).

### Post-rollout fix (8/5/2026 sera)

`discover_image_dirs` (recursive walk) is unsuitable for "auto-discover
corpora when base_path is None": it enumerated every year/event sub-directory
of `~/.local/share/metnos/Immagini/` as a distinct corpus (654 results),
each with its own sha8 → no existing index → 654 build-pending entries
in the `find_images_indices` payload (58 KB final_message). The
existing per-corpus index for the whole `Immagini/` was never recognized.

Fix: new `asset_discovery.discover_top_level_image_corpora(scope_root,
min_files=5)` that returns ONLY direct children of scope_root with
recursive image count ≥ min_files. One corpus = one top-level dir = one
sha8 = one index. `find_images_indices` switched to the new helper.
Re-signed. 6 deterministic tests added.

### Deferred

- **PR6** (corpus registry, `valutare`): `photo_corpora.sqlite` with
  `set/get/delete_corpora` executors and alias↔path mapping. Today
  multi-folder is via explicit `base_path=[a, b]` or symlink into
  `~/.local/share/metnos/`. Defer until a second corpus exists.
- **min_face_fraction implicit (no arg required)**: schema v2 has
  `image_w`/`image_h`, so a future PR could expose a verbal alias like
  `composition="close_up"|"portrait"|"wide"` mapping to fractions
  declaratively.
- **Telegram pagination >10 options**: today fallback to label-only.
  Could be paged in album+keyboard; rare case, deferred.
- **multi_choice_with_preview** for batch operations: deferred.

## Verified

- 1093 PASS / 0 FAIL on full runtime suite.
- 115 PASS / 1 skip (croniter optional) on scheduler v2 suite.
- Sign verify: 0 rejected on `set_persons`, `get_persons`,
  `delete_persons`, `find_persons_indices`, `find_images_indices`,
  `create_images_indices`, `describe_entries`, `find_urls`, `get_inputs`.
- Live test: `user_controllo_versione_amd_rocm` (the task that "never
  fired" under v1 scheduler) now firing through scheduler v2 (ADR 0112)
  unrelated to this ADR but verified in the same session.
- The query «volti in primo piano» now returns either real close-ups
  (with `idx="persons"`+`min_face_pixels=40000`) or a clean low-confidence
  error, no more 100-entry rumore.

## References

- ADR 0086 — image-domain indices.
- ADR 0090 — `get_inputs` declarative UI engine.
- ADR 0093 — async indexing build via systemd transient units.
- ADR 0098 — web crawl parallel + topic ranking strategy.
- ADR 0112 — scheduler v2 (companion rollout in same session).
- §2.5 — manifest authoring rules.
- §2.7 — truncation visibility cross-executor.
- §6 — prescrittivo prompt style for routing rules.
- §7.10 — re-sign rule after executor `.py` edit.
