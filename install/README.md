# Metnos installer

Idempotent six-phase installer. Designed to be safe to interrupt and
re-run at any point.

## Quick start

```bash
git clone https://github.com/brunialti/metnos.git
cd metnos
bash install/bootstrap.sh
```

Or, when `metnos.com` is live:

```bash
curl -fsSL https://metnos.com/install.sh | sh
```

## What it does

| Phase | Name | Touches | Reversible |
|------:|------|---------|:----------:|
| 1 | Bootstrap | venv + Python deps + runtime dirs | yes |
| 2 | Infrastructure | downloads BGE-M3 ONNX; optional llama.cpp, VLM, photon, SearXNG | yes |
| 3 | Metnos code | source skeleton + i18n.sqlite | yes |
| 4 | Sensitive data | interactive prompts for admin / Telegram / IMAP / API keys | secrets stored encrypted |
| 5 | Systemd | writes user units + optional system units (sudo required) | yes |
| 6 | First boot | admin onboarding link, optional sample index | yes |

Each phase writes a sentinel JSON in `~/.local/state/metnos/install/`.
Re-running the installer skips phases whose sentinel exists. To redo a
phase: `python -m install --force-phase 2`.

## Layout

```
install/
├── bootstrap.sh        # shell entry: find python, create venv, hand off
├── manifest.toml       # declarative single source of truth
├── __main__.py         # python -m install orchestrator
├── state.py            # sentinel management
├── ui.py               # rich-based terminal UI + progress
├── preflight.py        # disk / python / network / libstdc++ checks
├── phases/
│   ├── phase1_bootstrap.py   # done
│   ├── phase2_infra.py       # TODO
│   ├── phase3_code.py        # TODO
│   ├── phase4_secrets.py     # TODO
│   ├── phase5_systemd.py     # TODO
│   └── phase6_firstboot.py   # TODO
└── units/              # systemd unit templates
```

## Options

```
python -m install --help

  --resume              Skip done phases (default).
  --force               Continue past non-fatal pre-flight warnings.
  --force-phase N       Re-run phase N (clears its sentinel first).
  --only-phase N        Run only phase N.
  --yes, -y             Auto-confirm prompts (non-interactive).
  --enable COMPONENT    Force optional component on   (e.g. --enable vlm).
  --skip COMPONENT      Force optional component off  (e.g. --skip photon).
```

## Safety

- **Idempotent.** Every step checks the world before acting.
- **Reversible.** Phase 5 systemd writes are the only sudo operations,
  and they happen only after explicit consent with a summary of what
  will be created.
- **Sandboxed secrets.** Phase 4 stores credentials via Fernet+HKDF
  (`runtime/credentials.py`, ADR 0131). Nothing plaintext on disk.
- **No silent failures.** Every download verifies a sha256; every
  systemd unit is health-probed after start.
- **Auditable.** Each phase's sentinel JSON records what it did, when,
  and which optional components were chosen.
