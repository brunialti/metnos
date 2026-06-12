# INSTALL_NOTES — rules & lessons for the Metnos installer

> **READ THIS BEFORE EDITING ANYTHING UNDER `install/`.** Update it whenever you
> change an install↔runtime contract. The installer's job is to **replicate a
> fully working production environment from a git checkout** — not merely to
> boot an HTTP server. Validate every change with the isolated test harness
> (see bottom) and a real chat turn, never just "it started".

## North star
A fresh `git clone` + `bash install/bootstrap.sh` must produce an instance that
behaves like the reference (production) instance: same engine, same routing
config, same data catalogs, a real chat turn returns `kind=answer`. If a turn
errors, the install is **not** done.

## Canonical env contract (MUST match `runtime/config.py`)
The installer and the systemd units MUST export exactly the names the runtime
reads. Mismatches here silently break the runtime (wrong paths, empty catalog).

| Meaning                | Canonical env (runtime)   | NOT (old installer names) |
|------------------------|---------------------------|---------------------------|
| source/code tree       | `METNOS_INSTALL_ROOT`     | ~~`METNOS_REPO_DIR`, `METNOS_HOME`~~ |
| data (models, dbs)     | `METNOS_USER_DATA`        | ~~`METNOS_HOME`~~         |
| state (sentinels)      | `METNOS_USER_STATE`       | ~~`METNOS_STATE`~~        |
| config (secrets/tiers) | `METNOS_USER_CONFIG`      | ~~`METNOS_CONFIG`~~       |
| venv                   | `METNOS_VENV`             | —                         |

- `runtime/config.py`: `PATH_ROOT = METNOS_INSTALL_ROOT (or legacy METNOS_HOME)`;
  `PATH_USER_DATA/STATE/CONFIG = METNOS_USER_*`. `PATH_WORKSPACE = PATH_ROOT/workspace`
  (so the DBs under `workspace/` live next to the **code**, not under user data).
- `bootstrap.sh` MUST `cd "$REPO_DIR"` before `exec python -m install` (otherwise
  `-m install` loads the package from the caller's cwd).
- Unit templates set: `METNOS_INSTALL_ROOT=@REPO_DIR@`, `METNOS_USER_DATA=@DATA_DIR@`,
  `METNOS_USER_CONFIG=@CONFIG_DIR@`, `METNOS_USER_STATE=@STATE_DIR@`. Never `METNOS_HOME`.

## LLM tiers are PURE ABSTRACT (user directive)
- Tiers `fast/middle/wise/frontier` are **roles**, not models. No accelerator
  (GPU/NPU) and no specific model are required by default.
- The installer writes `~/.config/metnos/llm_tiers.toml` with `provider` +
  `endpoint` (model OPTIONAL — a llama-server serves whatever GGUF it loaded).
- `runtime/llm_router.py` accepts a tier as configured when it just has a
  `provider`; `endpoint`/`base_url` are aliases; the wise "quality floor" does
  NOT gate on model name (the old gemma/qwen whitelist was stale + coupled).
- **Endpoint SoT** (fix 12/6/2026): every `call_llm` consumer (HTTP provider,
  deterministic describe `/props` + `/apply-template`) resolves the llama-server
  endpoint via `runtime/llm_router.py::tier_endpoint(tier)` — which re-reads
  `llm_tiers.toml` (`METNOS_LLM_TIERS_CONFIG` honoured) at call time. `:8080`
  (`LOCAL_DEFAULT_ENDPOINT`) is only the last-resort default when nothing is
  configured. No other hardcoded llama-server URL is allowed in runtime code.
- Default LLM flow (no hardware assumed): if a local endpoint already answers
  (`_endpoint_alive`) → wire to it (no download); else interactive managed
  provisioning via `llm_manager` (CPU path is first-class, `ngl=0`); else
  fall back to the frontier API. Never fail for lack of a GPU.
- **Managed provisioning STARTS the server** (fix 12/6/2026): `provision()`
  no longer just writes `metnos-llm.service` — `llm_manager.install_user_unit`
  copies it to `~/.config/systemd/user/`, `daemon-reload`s, `enable --now`s it
  (USER unit, no sudo, same approach as phase5) and waits for `/health`
  (`METNOS_LLM_START_TIMEOUT_S`, default 180s — GGUF load can take minutes).
  Honest outcomes (§2.8) in `out["service"]` = `{installed, enabled, started,
  healthy, reason}`: endpoint already serving → unit installed but NOT started
  (no double bind); no systemd / enable failure / health timeout → reason says
  so, phase2 warns instead of claiming "verified". Only applies to managed
  provisioning, never to wire-to-existing-endpoint.
- Concrete model identities belong in ONE dated tier→model table in the docs,
  not scattered through code/prompts. Keep tier-level language everywhere.
- **ROCm asset guard** (fix 12/6/2026): `rocminfo` alone does NOT prove a
  usable ROCm runtime — without `librocblas.so` the HIP build silently falls
  back to CPU. `_pick_llama_asset` probes `_rocm_runtime_complete()` (ldconfig
  + `/opt/rocm*/lib` glob) and prefers the **Vulkan** asset when incomplete
  (production runs Vulkan on the same AMD GPUs). cpu/cuda/vulkan paths
  untouched.
- **llama-completion (deterministic describe)**: the managed provisioning
  extracts the WHOLE llama.cpp release archive, so `llama-completion` lands
  next to `llama-server` (same release = version-aligned by construction).
  `llm_manager.acquire_llama` chmods it; phase5 exposes its path to the unit
  as `METNOS_LLAMACPP_COMPLETION_BIN` (`@COMPLETION_ENV@` placeholder), which
  `runtime/llm_helpers.py::_completion_bin` reads first. If absent (wired to
  an existing endpoint — no provisioning — or an old release without the
  binary), the install does NOT fail: phase5 writes an honest comment and the
  runtime falls back to HTTP generation with `meta.deterministic=false` (§2.8).

## Engine / routing config (replicate production, ADR 0161/0164, §11)
A bare install defaults to `METNOS_ENGINE=simple`, which leaves many queries
uncovered (`Praxis non ha coperto … LEGACY=0`). The unit template MUST carry the
production-tested engine env:
`METNOS_ENGINE=metis`, `METNOS_PROPOSER_GRAMMAR=1`, `METNOS_PROPOSER_VERB_FILTER=1`,
`METNOS_PROPOSER_FAST_CONFIDENCE=0.70`, `METNOS_PREFILTER_RULES=1`,
`METNOS_INTENT_CLASSIFIER=1`, `METNOS_REASONING_BUDGET=4096`.
- NOTE: `METNOS_PRAXIS*`, `METNOS_USE_GRAMMAR`, `METNOS_VERB_FILTER` are **NOT
  read** by the code — they are vestigial in the prod unit. Do not cargo-cult them.

## Data catalogs the runtime needs (seed the FULL env)
- **Embedder (BGE-M3)**: runtime reads `<INSTALL_ROOT>/models/embedding-bge/onnx/
  sentence_transformers_int8.onnx` + `tokenizer.json`. The int8 ONNX emits token
  embeddings (3-D); the runtime mean-pools, so Xenova's quantized export works
  saved under that exact name. Runs on CPU. No degraded mode — abort if missing.
- **i18n catalog**: the runtime uses table **`i18n`** (cols: key, lang, text,
  needs_translation, source_lang, …) — NOT a `messages` table. `runtime/i18n.py`
  auto-creates the `i18n` table. A fresh install MUST seed the full catalog
  (prod ≈ 1000 rows) or user-facing strings render as `<missing:MSG_*>`. Ship a
  bundled seed; do not hand-write a 6-key stub with the wrong schema.
- **Executors**: `.sig` files shipped in the repo are signed with the upstream
  author key (not trusted on the user's machine). phase3 runs `sign.py sign-all`
  with a locally-generated key, else the loader rejects all handcrafted
  executors and the catalog is empty.

## What the base install MUST include vs what is lazy
- **Mandatory in the base install** (everything except the JS sidecar): the
  BGE-M3 embedder AND the local LLM (real model + llama.cpp via `llm_manager`,
  unless wired to an existing endpoint). These are downloaded for real — a base
  install is not "done" without a working embedder and a working LLM tier.
- **Lazy** — the **Playwright JS-render sidecar** only: install it on first use
  of the web-search capability, NOT during the base install. Do not wire
  `install/playwright_sidecar.py` into the 6 phases.
- **Optional** (user choice, off by default): VLM / Photon / SearXNG scaffolds.

## Hard rules
- i18n: the **installer UI is English-only** (decision, 9/6). The IT/EN choice
  during install selects **Metnos's runtime language**, NOT the installer's.
  - The language gate (`disclaimer.ask_language`) picks the Metnos locale
    (IT/EN tested; other ISO codes possible but untested → run in EN meanwhile).
  - phase4 records it (`notes["locale"]`); phase5 writes it as `METNOS_LANG`
    into the unit (`@LANG@` placeholder) so the runtime actually talks to the
    user in that language. Default `it`.
  - `install/i18n.py` exists (full IT/EN catalog) but `locale()` is pinned to
    `"en"` for now — scaffolding for a future fully-localized installer. Do NOT
    key the installer UI off the user's Metnos-language choice (mixed-language
    installer). Only `__main__`/phase1 currently call `i18n.t()` (render EN).
- Installer UI: linear but elegant (rich-based `ui.py`).
- Honest phase outcomes (§2.8): never print "running / onboarding URL" if the
  service did not actually start (phase6 reads `http_enabled`/`http_healthy`).
- No silent half-install: a mandatory step that fails ABORTS (BGE-M3).
- Idempotent re-runs: with no sha256 pinned, trust an existing non-empty file
  (don't re-download); pin sha256 in the release pipeline.
- Port: `METNOS_HTTP_PORT` overrides; validate range + in-use.

## Isolated test harness (protect production!)
- Dedicated user `mnostest` (linger on). INSTALL_ROOT = a clone; HTTP on an alt
  port (prod = 8770); LLM wired to the shared `:8080` or a tiny CPU model.
- `/tmp/mh.sh` (or re-create): `sync` (working-tree install/+runtime/ → clone),
  `nuke` (wipe state/config/units/dbs, keep venv+model), `disclaimer`, `install`,
  `status`, `journal`. Drive prompts via `printf 'PW\n<answers>' | sudo -S -u
  mnostest env -i … bash bootstrap.sh` (sudo -S eats the first line as the pwd).
- Validate: service active, `/agent/health` 200, chat UI 200, a real turn →
  `kind=answer`. Confirm prod (:8770) still healthy and no writes into `/opt/metnos`.
- CLEAN UP at the end: remove the user, home, linger, user units, clone, harness.
