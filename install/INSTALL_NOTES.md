# Metnos installer contract

This document defines the invariants that installation code, service templates,
the machine-readable manifest and user documentation must satisfy together. It
is a maintenance reference, not a development log.

## Supported entry point

The supported installation starts with:

```bash
bash install/bootstrap.sh
```

The bootstrap script resolves the source tree, creates or reuses
`<METNOS_INSTALL_ROOT>/.venv`, installs the declared Python dependencies and
then runs the six-phase Python orchestrator from that same checkout. Direct
orchestrator commands must use the installation environment:

```bash
./.venv/bin/python -m install
```

`bash install/bootstrap.sh --check` may create or update `.venv` before it
reaches the Python pre-flight. When that environment already exists,
`./.venv/bin/python -m install --check` is the read-only pre-flight: it must not
create Metnos data, configuration, credentials, state or sentinels.

The initial safety notice always requires interactive acceptance. `--yes`
automates later optional choices only after that acceptance has been recorded.

## Canonical paths and user isolation

The installer and every generated unit use the same environment contract as
`runtime/config.py`:

| Purpose | Variable | Default |
|---|---|---|
| source tree | `METNOS_INSTALL_ROOT` | resolved checkout |
| Python environment | `METNOS_VENV` | `<METNOS_INSTALL_ROOT>/.venv` |
| user data | `METNOS_USER_DATA` | `~/.local/share/metnos` |
| user state | `METNOS_USER_STATE` | `~/.local/state/metnos` |
| user configuration | `METNOS_USER_CONFIG` | `~/.config/metnos` |

The source tree and its virtual environment belong to the installation. Data,
credentials, sessions, capability choices and operational state belong to the
account running Metnos. Code must not derive the virtual environment from an
application user's name or data directory, and must not collapse the three XDG
roots into one path.

An installation test must override all three user roots and the workspace before
importing runtime modules. Tests must never borrow the live user's credentials,
turn history, signing keys, session registry or mutable databases.

## Six-phase responsibilities

1. **Bootstrap** runs pre-flight checks, creates the per-user directory layout,
   installs the full Python dependency set and verifies core imports.
2. **Infrastructure** installs the mandatory BGE-M3 text embedder, binds the
   logical LLM tiers and installs only the sidecars explicitly selected.
3. **Metnos source** verifies `install/`, `runtime/` and `executors/`, creates
   initial stores, copies the complete i18n seed and signs executors with a key
   trusted by this installation.
4. **Sensitive data** creates the administrator key and can collect Telegram,
   IMAP/SMTP, Anthropic, OpenAI and GitHub credentials through the encrypted
   runtime store. Google Workspace is connected later through OAuth.
5. **Systemd services** renders user units, establishes `metnos.target` as the
   integrated owner where safe, and records bounded health results.
6. **First boot** selects catalogued capabilities, emits an administrator link
   only when the HTTP service is available, and writes the installation summary.

Each completed phase writes a per-user sentinel below
`$METNOS_USER_STATE/install/`. Re-running the installer skips completed phases;
`--force-phase N` removes and rebuilds only the selected phase's result. A
mandatory failure must stop the run and must not commit that phase's sentinel.

## Model and asset contract

`fast`, `middle`, `wise` and `frontier` are logical roles. The planner must not
depend on concrete model names. Text-tier bindings live in
`~/.config/metnos/llm_tiers.toml`; embedding and vision-language bindings live
in `embedding_tiers.toml` and `vlm_tiers.toml`. The web chat exposes their
effective values under **Settings → System → Models**.

The BGE-M3 ONNX model and tokenizer are mandatory. Phase 2 places them at the
paths used by the in-process embedder and verifies their pinned SHA-256 values.
An absent or corrupt mandatory asset aborts the phase. Optional assets must
likewise use a pinned revision or digest whenever the upstream distribution
provides a stable artifact.

A compatible text endpoint may be local or remote. If one already answers at
the configured address, phase 2 binds the local tiers to it without downloading
another engine. Managed local provisioning must report artifact installation,
service start and endpoint health separately; downloaded files alone are not a
healthy model service.

## Catalogs and signed capabilities

A fresh installation needs the complete `install/data/i18n_seed.sqlite`. It must
use the runtime `i18n` schema, pass SQLite integrity checking and contain every
user-facing key required at first boot in both supported languages. A small
fallback table is not an acceptable release artifact.

For an existing account, the runtime merges only missing `(key, language)` rows
from that bundled baseline when it opens the per-user catalog. It never
overwrites an existing translation. Consequently a release can add a string or
a language without discarding that user's reviewed wording.

Executor signatures distributed by the project do not grant trust on a new
host. Phase 3 generates or reuses that installation's trusted signing material
and runs the canonical `sign-all` operation before the catalog is considered
available. Catalog verification must load the signed executors through the same
loader used by the server.

Phase 6 reads the first-party capability switches from
`runtime/skills_catalog.py`; documentation must not maintain a competing list.
The `google-workspace` bundle is first-party but is connected through its own
OAuth-backed provider flow, not through a distinct phase-6 switch.

Documentation changes affect Tutor's knowledge base. Any change to public
documentation, UI navigation, manifests, executor descriptions or installation
guides requires a Tutor rebuild and a query-level verification before release.

## Credentials

Phase 4 writes only canonical dictionary payloads through
`runtime/credentials.py`. It must not create a second plaintext format. Scalar
tokens use a stable domain and a `value` field. Mail accounts use isolated
account domains and carry the fields required by their IMAP/SMTP backend.

The installer never asks for a Google account password. Google Workspace uses
the browser-based OAuth flow and stores the resulting material in the user's
credential scope. Additional mailboxes are independent credentials belonging to
the same Metnos user unless a separate Metnos account is deliberately used.

`--yes` skips optional credential prompts. It must never invent credentials,
copy values from another account or weaken the initial consent gate.

## Optional sidecars

`install/sidecar.py::SIDECARS` is the executable registry and therefore the
source from which lists and tests should be derived:

| Name | Purpose | Lifecycle |
|---|---|---|
| `searxng` | self-hosted web search | user service with health check |
| `photon` | offline geocoding | user service with health check |
| `vlm` | visual-language enrichment | lazy process; no persistent unit |
| `playwright` | JavaScript rendering and graphical site sessions | user service; Side also requires Xvfb |

Sidecars are optional and off by default. An absent sidecar leaves only its
dependent capability dormant or explicitly degraded. A sidecar installer must
distinguish downloaded, installed, started, healthy and failed states; it must
not turn a partial result into success.

All persistent units installed by this flow are user units and require no
`sudo`. Keeping them alive without an interactive login may require the separate
host-administrator command `loginctl enable-linger`. The VLM remains lazy even
when its assets have been installed.

## Integrated service lifecycle

On a fresh host, `metnos.target` owns the HTTP server and installed companion
units. Composite readiness checks the server and catalog contracts rather than
only checking whether a port is open. Coordinated lifecycle operations use
`runtime/stack_reconcile.py` and must first establish that there is no active
turn or browser session that would be interrupted.

If a system-level `metnos-http.service` is already active, phase 5 installs the
user units but does not start a competing listener and does not disable the
working baseline. The guarded migration procedure in `systemd/README.md` must
prove the replacement and its rollback before ownership changes.

The HTTP health endpoint proves reachability, not planning quality or end-to-end
operation. A release installation is complete only after a harmless natural-
language request passes through the chat and returns a normal answer.

## Manifest boundaries

`install/manifest.toml` is the machine-readable inventory of the current
installable system. It describes requirements, models, units, directories,
configuration files and external services. It is not a changelog and does not
replace executable sources.

When an installation contract changes, update together:

- `requirements*.txt` for Python packages;
- `install/phases/` and `install/sidecar.py` for behavior;
- `install/units/*.tmpl` for generated services;
- `runtime/virt/` and `runtime/llm_router.py` for model bindings;
- `install/manifest.toml` for inventory;
- `install/INSTALL.md`, `install/README.md` and public documentation for users;
- Tutor's compiled catalog and its provenance report.

Do not place dates, obsolete module names, host-specific production paths or a
narrative of past fixes in the manifest or user-facing installation guides.

## Verification gates

Use the Metnos environment for all Python checks:

```bash
./.venv/bin/python -m pytest \
  tests/runtime/infra/test_installer_documentation_contract.py \
  tests/runtime/infra/test_installer_phase4_credentials.py \
  tests/runtime/engine/test_phase5_stack_target.py -q
./.venv/bin/python -m runtime.published_docs validate
```

Before a public release, also run the public-export gate and a clean install
under a dedicated account with isolated data, state, configuration, workspace,
ports and user services. That run must cover dependency installation, asset
integrity, executor signing, catalog loading, server readiness, one real chat
turn, the full isolated test suite, service shutdown and restoration of the
pre-existing instance. Preserve logs on failure; remove the isolated account's
artifacts only after the result has been recorded.
