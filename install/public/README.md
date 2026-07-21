# Metnos public install overview

Public install path for a Metnos instance cloned from GitHub.

> **There is one supported installer: `install/bootstrap.sh`.** It is the
> friendly, idempotent, six-phase flow documented in
> [`../README.md`](../README.md). This directory only holds a few low-level
> helper scripts it can call; you normally never run them by hand.

## Quick start

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
bash install/bootstrap.sh --check   # pre-flight only, writes nothing
bash install/bootstrap.sh           # interactive, six-phase setup
```

`bootstrap.sh` finds Python 3.12 or newer, creates the virtual environment,
installs the dependencies, then hands off to the orchestrator
(`python -m install`). It provisions the selected model bindings, runtime data,
signed executors, and optional support services, then verifies the fresh
instance with a real turn.

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

Metnos sees logical **tiers** (`fast` / `middle` / `wise` / `frontier`) rather
than model names. Local tiers may share one compatible endpoint or use separate
ones; `frontier` is opt-in. The tier-to-model binding lives in
`~/.config/metnos/llm_tiers.toml`, so changing a model changes configuration,
not planner code. Embeddings run in-process; there is no dependency on another
project's Python environment.

## Changing the LLM after install

Edit `~/.config/metnos/llm_tiers.toml` to point a tier at a different
`llama-server` endpoint or model, then restart the serving unit. No re-install
or code change is required: the binding is configuration, and the planner never
sees it.

See [`../README.md`](../README.md) for the full project overview and
[`../INSTALL_NOTES.md`](../INSTALL_NOTES.md) for the install contract.
