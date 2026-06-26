# Metnos public install — overview

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

`bootstrap.sh` finds a Python ≥ 3.12, creates the virtualenv, installs the
dependencies, then hands off to the orchestrator (`python -m install`). It
provisions everything a working instance needs — embedder, LLM serving, support
services, signed executors — and finishes by exercising a **real turn** against
the fresh instance.

## Architecture — Metnos is self-contained

```
┌──────────────────────────────────────────┐
│  metnos (this repo)                      │  ← personal AI assistant
│   - runtime, executors, chat HTTP        │
│   - in-process embedder (BGE-M3 ONNX)    │  ← autonomous, no external hub
│   - virt/ model facade (config-driven)   │
└────────────────┬─────────────────────────┘
                 │ tiers point at
                 ▼
┌──────────────────────────────────────────┐
│  LLM backend (your choice)               │  ← inference engine
│   - llama-server (any OpenAI-compat GGUF)│
│   - or a remote endpoint, or frontier API│
└──────────────────────────────────────────┘
```

Metnos never names a concrete model: it sees logical **tiers**
(`fast` / `middle` / `wise` / `frontier`). The three local tiers point at one
`llama-server` (a ~35B MoE GGUF by default); `frontier` is an opt-in cloud
fallback. The tier→model binding lives in `~/.config/metnos/llm_tiers.toml` —
changing a model is editing the TOML, not the code. **Embeddings run in-process**
(BGE-M3 ONNX); there is no `suprastructure` dependency.

## Changing the LLM after install

Edit `~/.config/metnos/llm_tiers.toml` to point a tier at a different
`llama-server` endpoint or model, then restart the serving unit. No re-install,
no code change — the binding is config, the planner never sees it.

See [`../README.md`](../README.md) for the full project overview and
[`../INSTALL_NOTES.md`](../INSTALL_NOTES.md) for the install contract.
