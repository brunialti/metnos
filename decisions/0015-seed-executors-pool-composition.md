---
id: 0015
title: Seed executor pool — 22 (then 27) executors with OSS self-hosted backends
date: 2026-04-26
status: accepted
area: executor
related:
  - 0014
  - 0016
---

## Context

Once the granularity heuristics were settled (ADR 0014), the next
question was concrete: which executors does Metnos ship with at first
boot? The bootstrap needs *enough* primitive grammar that any
plausible early request can compose a chain (or, when not, generate
an executor that fits cleanly in the existing taxonomy). Too few
seeds and every new request triggers expensive synthesis; too many
seeds and the catalog is bloated before any real use.

The seed pool also doubles as the first contract test for the runtime:
the manifest schema, the sandbox profile, the signature, the audit
trail must all work end to end on a spread of capabilities (FS,
shell, network, LLM, mail, channel, parse, time). The decision was
taken on 26 April 2026 with 22 candidates, and revised on 27 April
to 27 after the live reality check exposed three concrete gaps
(`pkg_*` family, `geo_poi_search`, `find_file`).

## Decision

The seed pool ships 27 executors organized in eight families:

- **FS (5)**: `fs_read`, `fs_write`, `fs_list`, `fs_move`, `fs_stat`.
- **Shell (1)**: `shell_exec` (allowlist + sandbox).
- **Net (5)**: `web_fetch`, `http_post`, `web_search`, `geo_search`,
  `geo_poi_search`.
- **LLM (3)**: `llm_chat`, `llm_classify`, `llm_extract`.
- **Mail (4)**: `imap_status`, `imap_fetch`, `imap_move`, `smtp_send`.
- **Channel (2)**: `telegram_send`, `voice_say`.
- **Parse (2)**: `pdf_extract_text`, `image_ocr`.
- **Time (1)**: `parse_date_nl`.
- **Packages, cross-OS (4)**: `pkg_install`, `pkg_uninstall`,
  `pkg_search`, `pkg_list_installed`.

Each follows the granularity heuristics of ADR 0014: ~80 lines in
`main.py`, ~30 in `tests/birth.py`, ~5 birth tests (positive,
negative, edge, structured error, timeout), 1–3 declared capabilities,
30 s median timeout, 3–5 structured error classes, one external
dimension (fs / network / parser / LLM). Median realization estimate:
one day per executor, with extremes at half a day (`fs_stat`) and
two days (`llm_extract`, `smtp_send` with idempotency tracking).

Backend choices follow the OSS-self-hosted-first principle (ADR
0016):

- **`web_search`**: SearXNG self-hosted on `metnos-server:8888`,
  inherited from giorgio2 (smoke-tested 26/4). Auto-enrichment
  (top-N URL fetch + content extraction) baked into the executor
  rather than split into `web_fetch` chains, with a ~10s timeout per
  fetch and an 8000-char cap per result.
- **`geo_search`**: Nominatim (OSM) self-hostable, public fallback
  with polite rate limit. Strict geocoding/reverse-geocoding only.
- **`geo_poi_search`** (added 27/4 after the live reality check):
  Overpass API (OSM), category-based POI search around a center,
  with `open_at` filter.
- **`pkg_*`** (added 27/4): cross-OS dispatch, Linux backends in
  priority order `apt → dnf → pacman → snap → flatpak`, Windows
  backends `winget → choco`. Privilege handling delegated to the
  client's OS (NOPASSWD-restricted sudoers on Linux, explicit UAC
  elevation on Windows). `pkg_install` and `pkg_uninstall` are
  critical with `approval=always`; `pkg_search` and
  `pkg_list_installed` are read-only.
- **`voice_say`**: TTS via Piper local; routing to Echo speakers when
  detected, or to giorgio2's `myoming2` Linux satellites; arbitration
  reused from `core/satellite_arbiter.py`.
- **`image_ocr`**: Tesseract local.
- **`pdf_extract_text`**: pdfplumber/pdfminer local.
- **`parse_date_nl`**: deferred to implementation; likely
  dateparser or duckling.

## Alternatives considered

**Smaller seed (10–12 executors).** Pro: faster bootstrap, cheaper
to validate. Con: too many early requests fall through to synthesis
before the synt has any reusable patterns to compose against; the
mnestoma starts hollow and stays so. Rejected.

**Larger seed (40+ executors).** Pro: covers more first-boot
scenarios. Con: bloats the catalog before usage data has identified
what is actually needed; each seed is a maintenance burden;
violates parsimony. The introvertive cascade (ADR 0010) cannot
prune what was never proven necessary. Rejected.

**SaaS-first backends.** Pro: zero ops. Con: violates ADR 0016
(OSS self-hosted as default), introduces recurring cost and external
dependency, exposes data to third parties, defeats the
"lives-in-your-house" framing. Rejected.

**One executor per file format.** Pro: each loads only the relevant
backend. Con: the planner must discriminate by extension (something
the LLM does poorly); for mixed lists the dispatch must move to the
pipeline level. Discussed and partially adopted in the metadata
executor (ADR 0005, `get_files_metadata` with internal per-format
dispatcher) — but not in the seeds, which keep one executor per
canonical operation. Rejected for general use.

## Consequences

The catalog at first boot is 27 executors with median complexity
~80 lines. They exercise the full runtime contract (manifest, signing,
sandbox, audit, vaglio decision, channel reply) on every dimension.
giorgio2 is reused as a pattern source (search, voice, ocr) rather
than reinvented (`feedback_open_source_first`).

Open at the time of writing the seed:

- the exact NOPASSWD sudoers whitelist for Linux package managers
  (proposal: allow `apt`/`dnf`/`pacman` without password, the
  *semantic* authorization comes from `approval=always` upstream);
- Windows UAC elevation when the client is headless or locked
  (queue of pending installs?);
- the OSM-category → NL-vocabulary mapping for `geo_poi_search`
  (how much in seed, how much left to synt);
- `web_search` enrichment coupling: keep auto-enrichment in
  `web_search` (current decision) or separate it into a
  `fetch_after_search` synthesized step (cleaner granularity but
  fragmented);
- `voice_say` integration with the existing home-agent (which one,
  exactly).

Some of those open points have already been answered in the executor
diary (ADR 0030); others wait for first contact with usage.
