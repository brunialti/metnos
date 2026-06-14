# Metnos installer

A friendly, **idempotent, six-phase** installer for a self-hosted Metnos
instance. Safe to interrupt and re-run at any point — every phase checks the
world before it acts and records what it did. English-only (i18n is not applied
to the installer).

> **Honest expectations.** The code is the easy part. Metnos wants real hardware
> — a machine that can run a capable LLM locally (the reference instance uses a
> 96 GB unified-memory box). If yours can't, you can point Metnos at a
> `llama-server` endpoint on another machine. The installer never pretends a
> missing prerequisite is fine: it tells you what will stay **dormant** and why.

## Quick start

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
bash install/bootstrap.sh          # interactive
bash install/bootstrap.sh --help   # see all options
```

`bootstrap.sh` finds a Python ≥ 3.11, creates the virtualenv, installs
dependencies, and hands off to the orchestrator (`python -m install`). From a
clone whose venv is already populated you can call the orchestrator directly:

```bash
python -m install              # resume (skip completed phases)
python -m install --check      # pre-flight only, writes nothing
python -m install --yes        # non-interactive (CI / re-provision)
```

## What it does — the six phases

| Phase | Name | Touches | Reversible |
|------:|------|---------|:----------:|
| 1 | **Bootstrap** | venv + Python deps + runtime data/state/config dirs | yes |
| 2 | **Infrastructure** | BGE-M3 embedder (mandatory); LLM tiers (local llama.cpp and/or frontier keys); optional VLM, Photon geocoder, SearXNG | yes |
| 3 | **Metnos code** | source skeleton + `i18n.sqlite` import | yes |
| 4 | **Sensitive data** | admin key (auto, 0600) + interactive credentials: Telegram / IMAP / Anthropic / OpenAI / Google Workspace / GitHub — stored **encrypted** (Fernet+HKDF, ADR 0131) | secrets encrypted |
| 5 | **Systemd** | user units + optional system units (the only `sudo` step) + reachability probes | yes |
| 6 | **First boot** | admin onboarding link + **skill selection** + a written `install_summary.md` | yes |

Each phase writes a sentinel JSON under `~/.local/state/metnos/install/`.
Re-running skips phases whose sentinel exists. To redo one:
`python -m install --force-phase 2`.

## The AI backend (bring your own)

Metnos never talks to a concrete model directly — it sees logical **tiers**
(`fast` / `middle` / `wise` / `frontier`) and a text **embedder**. Phase 2 wires
them up; you choose how they are served:

```
  metnos  ──▶  AI backend shim  ──▶  your engines
                (tiers + embeddings)   • llama-server (Gemma, Qwen, …) on :8080
                                       • local ONNX embeddings (BGE-M3)
                                       • frontier APIs (opt-in fallback)
```

- **Embeddings** are selected by `METNOS_AI_BACKEND` (`auto` | `local` | `suprastructure`). Public installs use `local` = standalone ONNX BGE-M3 — no external hub required.
- **Chat tiers** point at any OpenAI-compatible `llama-server` endpoint (local or remote). `middle`/`wise` default to a ~26B GGUF on `:8080`; `frontier` is opt-in (Anthropic/OpenAI keys from phase 4).

Without a local `middle`/`wise` tier the planner falls back to frontier for every
turn (higher latency and cost) — the installer warns you about this rather than
hiding it.

## Skills — modular capabilities

Phase 6 lets you choose which **first-party skills** start enabled:
`photos` · `mail` · `web` · `geo` · `calendar` · `github` · `frontier`. The
**core** (local files, processes, time, scheduler, in-memory helpers) is always
on and needs nothing external.

All skills default to **on**, but a skill you enable without configuring its
prerequisite (an IMAP account, a SearXNG instance, a GitHub token, …) stays
**dormant** — visible but inert — until that prerequisite exists. Nothing breaks.

You can change skills any time *after* install, from the CLI or right in chat:

```bash
python3 runtime/cli/skills_cli.py list           # status + prerequisites
python3 runtime/cli/skills_cli.py disable github
```
> *"which skills do I have?"* · *"enable photos"* · *"disable the web"*

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
- **Sandboxed secrets.** Phase 4 stores credentials via Fernet+HKDF
  (`runtime/credentials.py`, ADR 0131). Nothing plaintext lands on disk.
- **No silent failure.** Every download verifies a sha256; every systemd unit is
  health-probed after start; skills that can't work yet are reported as dormant,
  not pretended-working.
- **Auditable.** Each phase's sentinel JSON records what it did, when, and which
  optional components and skills were chosen.

## After install

```bash
# if you installed the systemd unit:
systemctl --user status metnos-http        # (or the system unit)
# or run directly:
python3 runtime/metnos_http_server.py --host 0.0.0.0 --port 8770
curl http://127.0.0.1:8770/agent/health
```

The first-boot phase prints a one-shot admin onboarding URL and writes
`~/.local/share/metnos/install_summary.md` recording every choice you made.

See [`../README.md`](../README.md) for the project overview, the security model,
and how Metnos differs from other self-hosted agents. Design rationale lives in
the ADRs under [`../decisions/`](../decisions/) (the installer is ADR 0145).

— Showcase project: feedback and a little patience are both welcome. 🙏
