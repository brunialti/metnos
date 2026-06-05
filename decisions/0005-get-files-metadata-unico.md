---
id: 0005
title: A single "get_files_metadata" executor instead of N specialized ones
date: 2026-04-28
status: accepted
area: executor
related:
  - 0002
  - 0003
---

## Context

On April 28, 2026, after the first live "sort photos by year" run had
succeeded thanks to `get_file_dates`, Roberto proposed extending the
capability to include the **place of capture** (reverse-geocoding from
EXIF GPS coordinates). The target use case was: "sort all image files
in /home/user/images, prepending date and place to the name; 'unknown'
if missing".

This raised an architectural choice: do we add a second specialized
executor (`get_files_gps`, `search_places`, `get_files_device`,
`get_files_dimensions`...) or one that covers all file metadata?

Behind this choice was a structural tension already open in the project
(memory `metnos_executor_granularity`): too-fine granularity produces
executor explosion and confuses the composer; too-coarse granularity
produces monolithic executors, bloated descriptions, slow backend loading
even when not needed.

## Decision

A single executor `get_files_metadata`, vectorial, with argument
`fields: list[str]` that declaratively selects what to extract. Default is
`["dates.semantic"]`. The string `"all"` enables every field.

Values in `fields` are semantic metadata names, not tool names:

- `dates.semantic` (date_epoch + date_source: 'exif' | 'mtime')
- `dates.created` (only EXIF DateTimeOriginal)
- `dates.modified` (mtime)
- `gps` (raw {lat, lon} coordinates)
- `place` (place slug via reverse-geocode with local cache)
- `device` (camera make + model)
- `image_dimensions` (width + height)

Internal backend is a **per-format dispatcher** (PIL for images, and
in the future mutagen for audio, pypdf for PDF, etc.). The dispatcher is
pluggable: adding a new format requires a new internal extraction
function, not a new executor.

Reverse-geocode backend: public Nominatim with throttle ≥1.1s, SQLite
cache in `~/.local/share/metnos/geo_cache.sqlite` keyed on (lat round
to 5, lon round to 5) → place slug. Uniform errors via the message
repository (ADR 0004): rate-limit hitting 5 consecutive 429s aborts
the op with `ERR_EXT_SVC_LIMIT`. The choice of public Nominatim (vs
self-hosted) is intentionally provisional: 1000 req/day are enough
today; when higher volumes are needed we will switch to self-host
(Roberto already aligned the memory `feedback_open_source_first`).

`get_file_dates` is deprecated the same day (see ADR 0003), replaced by
`get_files_metadata(fields=["dates.semantic"])`. The output signature is
backward-compatible (`date_epoch`, `date_source` fields remain), so the
`move_files` manifest refactor doesn't require changes there.

`move_files` now accepts the `{place}` placeholder in `dst_template`,
falling back to "unknown" if the entry has no `place` field.

## Alternatives considered

**N specialized executors per metadata type** (`get_files_gps`,
`get_files_device`, `get_files_dimensions`, `search_places`).
Pro: each executor is small with a focused description. Con: the composer
must know the right combination, and getting "date + place" requires a
3-4 step pipeline. Specialized executor explosion confuses the composer
(big lookup table) and multiplies the writing/signing work. Rejected.

**One executor per file format** (`get_image_metadata`,
`get_audio_metadata`, `get_pdf_metadata`). Pro: each loads only the
relevant backend. Con: the composer must discriminate by extension
(something the LLM does poorly); for a mixed list (files of different
types) dispatch must happen at pipeline level, not internal. Rejected.

**Reverse-geocode in a separate executor** (`get_places(coords)`).
Pro: separation of concerns (metadata = local; place = network).
Con: the pipeline becomes longer (3 intermediate steps just to enrich
entries with place); the LLM struggles to merge a list of entries with
a list of places. Kept as a **cost-on-network optimization**: the
geocode backend is internal to `get_files_metadata` but called ONLY
when `place` is in `fields`. No overhead for callers who don't ask for
it.

## Consequences

The pool gains 1 general executor instead of 3-5 specialized ones. The
manifest description grows (must document `fields`), but the
LLM-readable manifest pattern (memory `feedback_manifest_llm_readability`)
handles it with concrete examples.

Birth tests: 6 initially; the manifest covers default fields, empty list,
invalid fields, missing path, `"all"` keyword. Live Nominatim test
verified outside the birth tests (direct call to `_reverse_geocode` with
known coordinates of Rome and Boulder, clean slugs, cache hit on second
call).

Opens a direction: future executors can follow the same pattern "one
executor + fields[]" for other semantic metadata families (e.g.,
`get_messages_metadata` with fields `[from, subject, has_attachment,
language]`; `get_events_metadata` with `[start, end, attendees,
location]`). The "fields select" pattern becomes a Metnos idiom.

The drift between hand-written and synt-generated executors (see ADR
0001) extends: "fields select" is also a capability the synt cannot yet
produce. Not blocking.
