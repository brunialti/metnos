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
The accepted language selection records the operational instance language, an
optional requested language and the localization state. Phase 3 signs these
facts with the installation author key and atomically writes
`$METNOS_USER_STATE/i18n/localization_request.json`. Re-running the installer
with the same selection and corpus version leaves the signed document byte for
byte unchanged.

## Birth authority inputs (RM-0008 group 2)

Phase 3 prepares an **inactive** Birth authority set. It does not activate the
Birth runtime and it migrates no caller.

Before Phase 3 the administrator installs two public registries in the fixed
location `$METNOS_USER_CONFIG/birth/operator-input-v1/`:

- `approval-authority.json` — public approver keys, actors and scopes;
- `semantic-authority.json` plus `semantic-public/<name>.pub` — the public
  semantic reviewer keys the document references.

The corresponding private keys must never be placed there, in the authority set
or anywhere the Birth process can read. Phase 3 performs a read-only preflight
of these two documents before it publishes the executor contracts: a missing or
malformed registry stops the phase with a distinct error rather than being
completed by a generated key.

On a fresh installation the author key does not exist yet, so the first call
defers without creating any object; the provisioner runs to completion right
after the contracts are installed, and is idempotent. It creates
`author-root-v1`, one immutable `authority-sets/<set_id>` and the marker
`prepared-v1.json`, whose state is `prepared_not_active`.

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
   initial stores, copies the complete i18n seed, publishes executor contracts
   with a key trusted by this installation and uses the same key to sign the
   accepted instance-localization request. On a fresh installation this phase
   also performs the one-way switch to the immutable contract store before the
   runtime services that admit work, read contracts or publish contract
   translations are installed and started. Infrastructure prepared by phase 2,
   such as a local language-model service, may already be running.
4. **Sensitive data** creates the administrator key and can collect Telegram,
   IMAP/SMTP, Anthropic, OpenAI and GitHub credentials through the encrypted
   runtime store. It also records whether the Web UI listens on every private-
   LAN IPv4 interface (the guided and `--yes` default) or on loopback only.
   Google Workspace is connected later through OAuth.
5. **Systemd services** renders user units, establishes `metnos.target` as the
   integrated owner where safe, and records bounded health results.
6. **First boot** selects catalogued capabilities, emits a consumable
   administrator link only when the HTTP service is available, prints exact
   detected local/LAN URLs without placeholders, and writes those URLs to the
   installation summary.

Each completed phase writes a per-user sentinel below
`$METNOS_USER_STATE/install/`. Re-running the installer skips completed phases;
`--force-phase N` removes and rebuilds only the selected phase's result. A
mandatory failure must stop the run and must not commit that phase's sentinel.

## Model and asset contract

`fast` (levels `micro`, `procedural`, `fidelity`), `middle`, `wise`,
`creative` and `frontier` are logical roles. The planner must not depend on concrete model names. Text-tier bindings live in
`~/.config/metnos/llm_tiers.toml`; embedding and vision-language bindings live
in `embedding_tiers.toml` and `vlm_tiers.toml`. The web chat exposes their
effective values under **Settings → System → Models**.

Service units must not export temperature, thinking, or reasoning-budget
knobs. Those values belong exclusively to the selected tier configuration;
operations may still set output ceilings, deadlines, grammars, and tool schemas.

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
host. Phase 3 generates or reuses that installation's trusted signing material.
It writes each key component atomically and the private component first. If a
fresh key creation stops between the two writes, the next fresh-install retry
derives the missing public component from the valid private key; a public-only
or malformed remainder stays fail-closed. Phase 3 then normalizes the
language-state companions, signs and verifies every installed, non-retired
contract, prepares an isolated immutable generation catalog and activates it.
This census also includes contracts belonging to disabled
skills: disabling a skill controls visibility, but does not remove its installed
contract or authorize a later fallback to mutable source files. The preparation
report is written durably before activation, so a crash between the marker and
the store move can be resumed without
guessing. The activation is allowed only while the central lifecycle lock is
held, HTTP and the contract readers, schedulers, publishers and restart
controllers are proven inactive, and the browser broker reports no work in
progress. Phase-2 utilities that do not consume contracts, such as an LLM or a
search service, need not be stopped. An older live installation must therefore
be stopped explicitly; a momentarily idle HTTP endpoint is not sufficient
evidence of quiescence. Phase 3 does not stop or later restart a live stack on
the operator's behalf: it fails with a stable diagnostic and leaves its phase
sentinel uncommitted, so the phase can be resumed after an explicit maintenance
stop.

Once the immutable store is active, phase 3 never runs `sign-all` again. A
re-run sends each installed, non-retired authoring source through the
layout-aware technical publisher, which signs and publishes under one
per-contract lock. The publisher preserves authenticated retirement tombstones:
reinstalling does not
silently reactivate an executor whose authoring directory still exists. The
marker-only and root-only recovery states remain fail-closed for normal runtime
readers. Under the same stopped-stack guard, the installer completes a
marker-only recovery only from the exact saved preparation report. For a valid
root-only store it instead reconstructs the current catalog by authenticating
the bindings and revisions already present in that root; an old initial report
would be stale after later publications. Missing, stale or inconsistent
evidence blocks the phase with a diagnostic instead of inventing recovery
state. Catalog verification finally authenticates every binding and loads the
resulting contracts through the same loader used by the server.

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
units. The i18n translator timer is a non-optional dependency: phase 5 installs
it before target activation, the target requires it, and composite readiness
fails when the timer is not active. Its oneshot worker may be inactive between
runs; the continuously active timer is the lifecycle and health object shown in
the Services page. Private development-only units are not part of the public
service catalog. Composite readiness checks the server and catalog contracts rather than
only checking whether a port is open. Coordinated lifecycle operations use
`runtime/stack_reconcile.py` and must first establish that there is no active
turn or browser session that would be interrupted.

Phase 5 also installs the supervised LRE worker and creates
`~/.config/metnos/lre.env` with mode `0600` only when the file does not already
exist. A fresh installation is disabled. An update preserves the existing file
byte for byte, including an invalid file that requires operator attention;
missing, linked, oversized, ambiguous or malformed configuration fails closed.
The worker and the HTTP control plane read this file through the same strict
runtime parser. The unit must not load it as a systemd `EnvironmentFile`, which
would introduce a second parser with different acceptance rules. The Services
page writes only the canonical form and restarts the exact catalogued user
unit. Disabling LRE never removes its store or artifacts, and the idle worker
continues to publish health state.

The phase-5 import preflight must reproduce both supported Python package
roots: the installation root for `runtime.*` modules and its `runtime/`
directory for top-level runtime packages such as `durable_workloads`. It uses
the same installation virtual environment as the rendered units.

If a system-level `metnos-http.service` is already active, phase 5 installs the
user units but does not start a competing listener and does not disable the
working baseline. The guarded migration procedure in `systemd/README.md` must
prove the replacement and its rollback before ownership changes. Non-listening
companions that must survive a reboot—including the idle LRE worker, the i18n
timer and the watchdog—are attached directly to the user `default.target`
during this transition. They are the same units later owned by
`metnos.target`; the compatibility path does not create duplicate services.

The HTTP health endpoint proves reachability, not planning quality or end-to-end
operation. A release installation is complete only after a harmless natural-
language request passes through the chat and returns a normal answer.

The public installer does not install, own or document a maintainer-specific
remote-access service. Its supported browser path is direct access from the
server or the same trusted private LAN. The default HTTP listener must never be
described as safe for router port forwarding or direct Internet exposure.

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

The phase-4 `http_host` note is authoritative for phase 5. A new installation
records `0.0.0.0` for private-LAN access or `127.0.0.1` for loopback-only
access. Missing notes from an older installation fail closed to loopback.

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
