# Metnos installer

A guided, **idempotent, six-phase** installer for a self-hosted Metnos
instance. Safe to interrupt and re-run at any point: every phase checks the
system before it acts and records what it did. English-only (i18n is not applied
to the installer).

> **Honest expectations.** Planning quality and latency depend on the models
> assigned to the Metnos tiers. A local accelerator is useful but not mandatory:
> the tiers may point to a compatible endpoint on another machine. The installer never pretends a
> missing prerequisite is fine: it tells you what will stay **dormant** and why.

## Quick start

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
bash install/bootstrap.sh          # interactive
bash install/bootstrap.sh --help   # see all options
```

`bootstrap.sh` finds a Python ≥ 3.12, creates the virtualenv, installs
dependencies, and hands off to the orchestrator (`python -m install`). From a
clone whose venv is already populated you can call the orchestrator directly:

```bash
python -m install              # resume (skip completed phases)
python -m install --check      # pre-flight only, writes nothing
python -m install --yes        # non-interactive (CI / re-provision)
```

## What it does: the six phases

| Phase | Name | Touches | Reversible |
|------:|------|---------|:----------:|
| 1 | **Bootstrap** | venv + Python deps + runtime data/state/config dirs | yes |
| 2 | **Infrastructure** | BGE-M3 embedder (mandatory); LLM tiers (local llama.cpp and/or frontier keys); optional VLM, Photon geocoder, SearXNG | yes |
| 3 | **Metnos code** | source skeleton + `i18n.sqlite` import | yes |
| 4 | **Sensitive data** | admin key (auto, 0600) + interactive credentials: Telegram / IMAP / Anthropic / OpenAI / Google Workspace / GitHub, stored **encrypted** with Fernet and HKDF | secrets encrypted |
| 5 | **Systemd** | user units + optional system units (the only `sudo` step) + reachability probes | yes |
| 6 | **First boot** | admin onboarding link + **skill selection** + a written `install_summary.md` | yes |

Each phase writes a sentinel JSON under `~/.local/state/metnos/install/`.
Re-running skips phases whose sentinel exists. To redo one:
`python -m install --force-phase 2`.

## The AI backend (bring your own)

Metnos never talks to a concrete model directly. It sees logical **tiers**
(`fast` / `middle` / `wise` / `frontier`) and a text **embedder**. Phase 2 wires
them up; you choose how they are served:

```
  metnos  ──▶  AI backend shim  ──▶  your engines
                (tiers + embeddings)   • compatible llama-server endpoint
                                       • local ONNX embeddings (BGE-M3, in-process)
                                       • frontier APIs (opt-in fallback)
```

- **Embeddings** run **in-process**: standalone ONNX BGE-M3, no external hub required. The model/endpoint is config-driven (`embedding_tiers.toml`); Metnos is autonomous for embedding out of the box.
- **Chat tiers** point at a compatible `llama-server` endpoint, local or remote. Canonical defaults live in `runtime/llm_router.py`; `frontier` remains an opt-in binding configured with credentials from phase 4.

Without a local `middle`/`wise` tier the planner falls back to frontier for every
turn (higher latency and cost). The installer warns you about this rather than
hiding it.

## Skills: modular capabilities

Phase 6 lets you choose which **first-party skills** start enabled:
`system` · `photos` · `mail` · `web` · `geo` · `calendar` · `github` · `google-workspace` · `sqldatabase` · `frontier`. The
**core** (local files, processes, time, scheduler, in-memory helpers) is always
on and needs nothing external.

All skills default to **on**, but a skill you enable without configuring its
prerequisite (an IMAP account, a SearXNG instance, a GitHub token, …) stays
**dormant**, visible but inert, until that prerequisite exists. Nothing breaks.

You can change skills any time *after* install, from the CLI or right in chat:

```bash
cd /opt/metnos   # run from your Metnos install directory
python3 runtime/cli/skills_cli.py list           # status + prerequisites
python3 runtime/cli/skills_cli.py disable github
```
> *"which skills do I have?"* · *"enable photos"* · *"disable the web"*

## Optional sidecars

A few capabilities lean on **self-hosted companion services** too heavy to force
on every install. They are off by default; phase 2 offers them, and you can add
one any time afterwards. Each is a **user-level systemd unit** (no `sudo`) that
survives logout once `loginctl enable-linger` is set.

```bash
python -m install.sidecar --list       # what's available
python -m install.sidecar searxng      # add self-hosted web search (real install)
```

| Sidecar | Backs | Cost | Status |
|---------|-------|------|--------|
| **SearXNG** | web search (`find_urls`) | ~200 MB | available |
| **VLM** | image captions (`find_images_indices`) | ~1.9 GB | available |
| **Photon** | offline geocoding (`get_location`, places) | ~3 GB index | available |
| **Playwright** | JS rendering and graphical website sessions | ~700 MB | available |

`searxng` clones SearXNG into `~/.local/share/metnos/sidecars/searxng`, builds a
dedicated venv, writes a single-user (redis-less) `settings.yml` under
`~/.config/metnos/searxng/`, and starts `metnos-searxng.service` on `:8888`, the
runtime's default `METNOS_SEARXNG_URL`, so it works with zero further config.

`vlm` fetches the configured visual model and projector into
`<install>/models/vlm`. It has **no persistent service**: image indexing is intermittent, so the
VLM is lazy-launched on `:8081` on first use and auto-stops after 10 min idle.

If a sidecar is absent, the dependent feature stays unavailable or reports its
degraded path explicitly; unrelated capabilities continue to work.

The Playwright sidecar also installs `metnos-side-display.service`, a
persistent Xvfb display on `:99` used by the graphical Side browser. The base
system package list includes `xvfb`; if it is missing, installation reports
the condition explicitly and does not silently switch browser surfaces.

## Integrated service lifecycle

Phase 5 renders `metnos.target`, the readiness/quarantine services and a
bounded watchdog. On a fresh installation the target is the single owner of
the HTTP service and every installed companion unit; readiness requires HTTP,
catalog and sidecar contract checks rather than only an open port.

The service panel reports integrated component state but does not expose
component-level start, stop or restart. Coordinated operations use
`runtime/stack_reconcile.py`, which first proves turn and browser quiescence.

An upgrade that still has an active system-level `metnos-http.service` is not
cut over automatically. Phase 5 installs the user units, records that migration
is required and keeps the working system service as the rollback baseline. Use
the guarded pilot documented in [`../systemd/README.md`](../systemd/README.md);
do not start a second listener or disable the legacy unit manually.

## Options

```
python -m install [options]

  --resume              Skip completed phases (default).
  --check               Pre-flight checks only; write nothing.
  --force               Continue past non-fatal pre-flight warnings.
  --force-phase N       Re-run phase N (clears its sentinel first).
  --only-phase N        Run only phase N.
  --yes, -y             Auto-confirm prompts (non-interactive).
  --enable COMPONENT    Force an optional component on   (e.g. --enable vlm).
  --skip COMPONENT      Force an optional component off  (e.g. --skip photon).
```

## Layout

```
install/
├── bootstrap.sh        # shell entry: find python, create venv, hand off
├── manifest.toml       # declarative single source of truth
├── __main__.py         # `python -m install` orchestrator
├── sidecar.py          # optional self-hosted sidecars (searxng/photon/vlm)
├── playwright_sidecar.py  # lazy JS-render sidecar (first web-search use)
├── preflight.py        # disk / python / network / libstdc++ checks
├── state.py            # sentinel management (idempotency)
├── ui.py               # terminal UI + progress (rich)
├── disclaimer.py       # one-time consent + expectations
├── phases/
│   ├── phase1_bootstrap.py
│   ├── phase2_infra.py
│   ├── phase3_code.py
│   ├── phase4_secrets.py
│   ├── phase5_systemd.py
│   └── phase6_firstboot.py
├── units/              # systemd unit templates
└── public/             # public-distribution variant + notes
```

## Safety

- **Idempotent.** Every step checks the world before acting; re-running is safe.
- **Reversible.** Phase 5 (systemd) is the only `sudo` operation, and only after
  explicit consent with a summary of what will be created.
- **Sandboxed secrets.** Phase 4 stores credentials via Fernet and HKDF
  (`runtime/credentials.py`). Nothing plaintext lands on disk.
- **No silent failure.** Every download verifies a sha256; every systemd unit is
  health-probed after start; skills that can't work yet are reported as dormant,
  not pretended-working.
- **Auditable.** Each phase's sentinel JSON records what it did, when, and which
  optional components and skills were chosen.

## After install

```bash
# fresh user-target install:
systemctl --user status metnos.target
python3 runtime/stack_reconcile.py check

# legacy upgrade: keep using the system scope until the migration gate passes
systemctl status metnos-http.service
# or run directly:
python3 runtime/metnos_http_server.py --host 0.0.0.0 --port 8770
curl http://127.0.0.1:8770/agent/health
```

The first-boot phase prints a one-shot admin onboarding URL and writes
`~/.local/share/metnos/install_summary.md` recording every choice you made.

See [`../README.md`](../README.md) for the project overview and security model.
