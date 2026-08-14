# Metnos public install overview

Public install path for a Metnos instance cloned from GitHub. There is one
supported installer: `install/bootstrap.sh`. It is the guided, idempotent,
six-phase flow documented in [`../README.md`](../README.md). This directory
contains low-level helpers used by that flow; they are not alternative
installers.

## Quick start

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
bash install/bootstrap.sh --check   # check requirements; may initialise .venv
bash install/bootstrap.sh           # interactive, six-phase setup
```

`bootstrap.sh` finds Python 3.12 or newer, creates the virtual environment,
installs the dependencies, then hands off to the orchestrator
(`python -m install`). It provisions the selected model bindings, runtime data,
signed executors, and optional support services, then verifies service startup
and the HTTP health endpoint. A complete installation check also requires one
harmless request through the chat after onboarding.

The final phase prints the exact local Web UI URL and, when private-LAN access
is enabled, the exact URL for every detected private IPv4 address. Use the
local URL on the server or a printed LAN URL from another device on the same
trusted network. The UI is plain HTTP by default: never port-forward that port
or expose it directly to the Internet. The same connection details are saved
in `~/.local/share/metnos/install_summary.md`.

## Architecture: Metnos is self-contained

```
┌──────────────────────────────────────────┐
│  metnos (this repo)                      │  ← executor-defined agent architecture
│   - runtime, executors, chat HTTP        │
│   - in-process embedder                  │  ← local by default
│   - virt/ model facade (config-driven)   │
└────────────────┬─────────────────────────┘
                 │ tiers point at
                 ▼
┌──────────────────────────────────────────┐
│  LLM backend (your choice)               │  ← inference engine
│   - compatible llama-server endpoint    │
│   - or a remote endpoint, or frontier API│
└──────────────────────────────────────────┘
```

The admitted executor set determines the concrete operating domain. A personal
or household assistant is one possible installation profile, not Metnos's
architectural definition.

Metnos sees logical **tiers** (`fast` with `micro` / `procedural` /
`fidelity`, `middle`, `wise`, `creative`, and `frontier`) rather than model names. Local tiers may share one
compatible endpoint or use separate ones; `frontier` is opt-in. The tier-to-model binding lives in
`~/.config/metnos/llm_tiers.toml`, so changing a model changes configuration,
not planner code. Embeddings run in-process; there is no dependency on another
project's Python environment.

## Changing the LLM after install

In the web chat, open **Settings → System → Models**. The page shows the
effective tier bindings, their provenance and generation parameters. Edit the
configuration there and save it, or use **Restore** to recreate the defaults
shipped by the installed version. The underlying file is
`~/.config/metnos/llm_tiers.toml`; no reinstall or planner-code change is
required.

See [`../README.md`](../README.md) for the full project overview and
[`../INSTALL_NOTES.md`](../INSTALL_NOTES.md) for the install contract.
