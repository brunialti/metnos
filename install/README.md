# Metnos installer

A guided, **idempotent, six-phase** installer for a self-hosted Metnos
instance. Safe to interrupt and re-run at any point: every phase checks the
system before it acts and records what it did. English-only (i18n is not applied
to the installer).

Planning quality and latency depend on the models assigned to the Metnos tiers.
A local accelerator is useful but not mandatory: the tiers may point to a
compatible endpoint on another machine. When a prerequisite is unavailable,
the installer reports which capability remains dormant and why.

## Quick start

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
bash install/bootstrap.sh          # interactive
bash install/bootstrap.sh --help   # see all options
```

`bootstrap.sh` finds a Python ≥ 3.12, creates the virtualenv at
`<METNOS_INSTALL_ROOT>/.venv` (separate from every user's data), installs
dependencies, and hands off to the orchestrator (`python -m install`). From a
clone whose venv is already populated you can call the orchestrator directly:

```bash
./.venv/bin/python -m install              # resume completed work
./.venv/bin/python -m install --check      # read-only pre-flight
./.venv/bin/python -m install --yes        # non-interactive after consent
```

On the first run, `--yes` does not bypass the language choice or the explicit
acceptance required by the safety notice.

## What it does: the six phases

| Phase | Name | Touches | Reversible |
|------:|------|---------|:----------:|
| 1 | **Bootstrap** | pre-flight, Python dependencies, per-user data/state/config directories | yes |
| 2 | **Infrastructure** | mandatory BGE-M3 embedder, LLM tier bindings, selected optional sidecars | yes |
| 3 | **Metnos source** | source verification, initial stores, full i18n seed, local executor signing and verified Tutor catalog | yes |
| 4 | **Sensitive data** | admin key and optional Telegram, mail, frontier-provider and GitHub credentials | secrets encrypted |
| 5 | **Systemd** | user units, integrated target and bounded health probes | yes |
| 6 | **First boot** | capability selection, temporary admin link and `install_summary.md` | yes |

Each phase writes a sentinel JSON under `~/.local/state/metnos/install/`.
Re-running skips phases whose sentinel exists. To redo one:
`./.venv/bin/python -m install --force-phase 2`.

The first Tutor build turns the public documentation and current manifests
into a signed semantic catalog. On a CPU-only host this can take several
minutes. Phase 3 completes that work before the service starts, so the bounded
readiness circuit cannot interrupt it. Later runs compare source content and
reuse unchanged vectors; a documentation change invalidates and refreshes the
catalog.

Google Workspace is connected after installation through its OAuth flow. Phase
4 does not request a Google password or store one.

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

Phase 6 lets you choose which catalogued **first-party capabilities** start
enabled: `github` · `photos` · `mail` · `web` · `geo` · `calendar` ·
`sqldatabase` · `frontier` · `system`. The **core** — including local files,
processes, time, the local scheduler and deterministic helpers — is always on
and needs no external backend.

The repository also includes the first-party `google-workspace` bundle. It
groups Gmail, Calendar, Drive, Contacts, Sheets and Docs behind the dedicated
OAuth connection; it is not a separate switch in the phase-6 capability list.

All skills default to **on**, but a skill you enable without configuring its
prerequisite (an IMAP account, a SearXNG instance, a GitHub token, …) stays
**dormant**, visible but inert, until that prerequisite exists. Nothing breaks.

You can change skills any time *after* install, from the CLI or right in chat:

```bash
cd /opt/metnos   # run from your Metnos install directory
./.venv/bin/python runtime/cli/skills_cli.py list
./.venv/bin/python runtime/cli/skills_cli.py disable github
```

You can also ask in chat: “Which capabilities do I have?”, “Enable photo
search”, or “Disable web access”.

## Optional sidecars

A few capabilities lean on **self-hosted companion services** too heavy to force
on every install. They are off by default; phase 2 offers them, and you can add
one any time afterwards. Each is a **user-level systemd unit** (no `sudo`) that
survives logout once linger is enabled, except the VLM, which starts only when
needed and stops after inactivity.

```bash
./.venv/bin/python -m install.sidecar --list
./.venv/bin/python -m install.sidecar searxng
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
`<install>/models/vlm`. It has **no persistent service**: image indexing is
intermittent, so the VLM is launched on `:8081` on first use and stops after ten
minutes of inactivity.

`photon` keeps the downloaded country archive while it builds the local index.
If download expansion or the long import is interrupted, the next run rejects
the unverified partial output and resumes from the last certified artifact. The
compressed archive is validated as a complete zstd frame; durable completion
receipts are written only after expansion and the Java importer finish
successfully. The mere presence of a JSONL file or `photon_data/` is never
treated as success.

If a sidecar is absent, the dependent feature stays unavailable or reports its
degraded path explicitly; unrelated capabilities continue to work.

The Playwright sidecar also installs `metnos-side-display.service`, a
persistent Xvfb display on `:99` used by the graphical Side browser. The base
system package list includes `xvfb`; if it is missing, installation reports
the condition explicitly and does not silently switch browser surfaces.

User services require no administrator privileges. To keep them running after
logout and across reboots, the host administrator may enable linger once:

```bash
sudo loginctl enable-linger "$USER"
```

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
./.venv/bin/python -m install [options]

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
├── manifest.toml       # machine-readable inventory
├── __main__.py         # `python -m install` orchestrator
├── sidecar.py          # optional sidecar registry and installers
├── playwright_sidecar.py  # browser-sidecar implementation
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
- **User-scoped services.** Phase 5 writes user units and does not invoke
  `sudo`; host packages, an optional command symlink and linger are separate
  administrator choices.
- **Sandboxed secrets.** Phase 4 stores credentials via Fernet and HKDF
  (`runtime/credentials.py`). Nothing plaintext lands on disk.
- **No silent failure.** Pinned assets are checked by SHA-256; services with a
  health endpoint are probed after start; unavailable capabilities are reported
  as dormant instead of being presented as working.
- **Auditable.** Each phase's sentinel JSON records what it did, when, and which
  optional components and skills were chosen.

## After install

```bash
# fresh user-target install:
systemctl --user status metnos.target
./.venv/bin/python runtime/stack_reconcile.py check
curl http://127.0.0.1:8770/agent/health

# legacy upgrade: keep using the system scope until the migration gate passes
systemctl status metnos-http.service
```

The health endpoint proves that the HTTP process is reachable; it does not
prove model quality or a complete application turn. After onboarding, send a
harmless request in chat, for example: “What time is it, and which time zone are
you using?” The installation is operational only when that request returns a
normal answer.

The first-boot phase prints a one-shot admin onboarding URL and writes
`~/.local/share/metnos/install_summary.md` recording every choice you made.

See [`../README.md`](../README.md) for the project overview and security model.
The normative installation procedure is [`INSTALL.md`](INSTALL.md); current
maintenance invariants are collected in [`INSTALL_NOTES.md`](INSTALL_NOTES.md).
