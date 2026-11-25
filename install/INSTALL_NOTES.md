# Metnos installer — maintainer notes

The working reference of the installer. Read it before changing anything under `install/`, and
update it in the same change when a contract changes. It says what is true now, not how we got
here.

- To install: [INSTALL.md](INSTALL.md).
- To reuse services you already run: [SERVICES.md](SERVICES.md).
- Packages, models, units and files: [`manifest.toml`](manifest.toml).

| Word | Meaning |
|---|---|
| Service account | The OS user `metnos`. Every Metnos service runs as this user. |
| Signed release | The verified, read-only copy of source and Python that the services run. The code calls it the closed distribution. |
| Birth | The admission gate (Executor Birth). No executor becomes active without passing it. |
| Transition | The one administrative operation that selects a signed release and activates its units. |
| Companion | A helper service: text model, vision model, SearXNG, Photon, browser. |
| LRE | Long jobs, run by the supervised durable worker. |
| Tutor | The built-in help. It answers from the documentation. |
| Fail closed | When something is unexpected, stop. Never continue on a guess. |

## 1. What the installer does and how to run it

It builds a complete working instance: service account, signed release, fresh signing
authorities, models, companions, system services and a first-access link.

```bash
bash install/bootstrap.sh --check   # prerequisites only
bash install/bootstrap.sh           # install, or resume an interrupted install
```

Supported platform: Linux x86_64 with systemd and CPython 3.12. `--force` does not widen it.

What happens, in order:

1. `bootstrap.sh` finds the source tree, creates or reuses `.venv` in it and installs the
   dependencies of `requirements-linux-x86_64.lock`.
2. `python -m install` reads the optional service profile, asks the language, shows the safety
   notice and asks for confirmation.
3. `managed_install.py` asks for administrator access. As root it copies the source to a
   temporary read-only place and builds the managed Python.
4. Phases 1 and 2 run as the service account.
5. Root prepares the local companion units (inactive), creates the authorities and installs
   the signed release **without activating it**.
6. Phases 3 and 4 run as the service account, from the installed release.
7. The LRE configuration is written (off by default). Root activates the signed release and
   refreshes the filesystem host.
8. Phases 5 and 6 run as the service account.

| Option | Effect |
|---|---|
| `--check` | Pre-flight only. Through `bootstrap.sh` it may still create or update `.venv`. The read-only form is `./.venv/bin/python -m install --check`: it creates no data, configuration, credentials, state or markers. |
| `--yes` | Accepts later non-sensitive defaults. It never accepts the safety notice, never invents or copies credentials, never opts into a paid model. |
| `--force` | Continues past non-fatal warnings. It cannot bypass the platform, the lock or a failed import. |
| `--force-phase N` | Repeats phase N (0 shows the safety notice again). |
| `--only-phase N` | Runs phase N alone. Earlier phases must be complete. |
| `--skip` | Refused. Declare an existing service in `services.toml` instead. |

Run the orchestrator directly only with the installation environment:
`./.venv/bin/python -m install`.

## 2. Prerequisites

- Internet access, Git, at least 8 GB free besides the chosen models. A GPU is optional.
- The system packages of `manifest.toml`, table `[system_packages]`: `debian` always,
  `ubuntu_24_04_all_local` when every companion is local. Ubuntu 24.04 is the reference;
  INSTALL.md has the install command.
- `polkitd`, even when every companion is remote.
- systemd in one of the exact versions of `SUPPORTED_SYSTEMD_VERSIONS`
  (`runtime/executor_birth_admin_preflight.py`): today `255.4-1ubuntu8.17` and `255.4-1ubuntu8.18`.
- Browser binaries and native libraries: separate prerequisites, not brought by the Python lock.

The administrative step stops if one of these programs is missing:

| Program | Needed |
|---|---|
| `/usr/bin/python3.12`, `/usr/bin/openssl`, `/usr/bin/systemctl`, `/usr/bin/systemd-analyze`, `/usr/lib/polkit-1/polkitd` | always |
| `/usr/bin/Xvfb` | local browser |
| `/usr/bin/git` | local SearXNG |
| `/usr/bin/java`, `/usr/bin/unzstd` | local Photon |

## 3. The six phases

| # | Phase | What it does |
|---:|---|---|
| 1 | Bootstrap | Verifies the installed dependency lock, the required imports and the service-account directories. |
| 2 | Infrastructure | Downloads and verifies BGE-M3. Prepares every local companion that `services.toml` does not replace. Starts no service. |
| 3 | Metnos source | Verifies the installed initial catalog through the ordinary loader. Seeds stores and translations, signs the language request, compiles and verifies Tutor. |
| 4 | Sensitive data | Creates the administrator key. Collects optional credentials into the encrypted store. |
| 5 | System services | Root has run the transition; the phase checks HTTP and the selected services. |
| 6 | First boot | Selects capabilities, prints a one-use first-access link (15 minutes) and the detected addresses, saves the summary. |

- Phase markers under `$METNOS_USER_STATE/install/` allow a resume. They are **not** security
  evidence: every continuation authenticates source, release and transition again. A failed
  phase writes no marker.
- The service profile is recorded in phases 2 and 5. Changing or removing it after phase 2
  stops the resume.
- Phase 3 does not run `sign-all`, restore retired contracts or use a publisher bypass.
- The Tutor compiler gets the CPU budget before it imports numerical libraries; it respects
  affinity, container quotas and tighter caps.
- Phase 6 reads the capability switches from `runtime/skills_catalog.py`. Documentation must
  not keep a second list. Google Workspace connects later through its own OAuth flow.

## 4. Where things live

`<home>` is `/var/lib/metnos-service`, the home of the service account.

| What | Where |
|---|---|
| Configuration, credentials (`METNOS_USER_CONFIG`) | `<home>/.config/metnos` |
| Data, summary (`METNOS_USER_DATA`) | `<home>/.local/share/metnos` |
| State, phase markers (`METNOS_USER_STATE`) | `<home>/.local/state/metnos` |
| Models | `$METNOS_USER_DATA/models`, or the existing directory in `METNOS_MODELS_DIR` |
| Signed source and Python (`METNOS_INSTALL_ROOT`, `METNOS_VENV`) | root-owned release stores |
| Operator and reviewer private keys | `/var/lib/metnos-operator-authority/<uid>/`, root only |
| Administrative preflight | `/usr/libexec/metnos/executor-birth-v1/preflight.py` |
| Startup lock rule | `/etc/tmpfiles.d/metnos-executor-birth-v1.conf` |
| Filesystem host | `/etc/metnos/filesystem-host.json`, `/var/lib/metnos-filesystem-host` |
| Vision startup profile | `/etc/metnos/vlm-startup.toml` |
| Private settings | `$METNOS_USER_CONFIG/runtime.toml`, `lre.env`, `services.toml` |
| Browser choices | `$METNOS_USER_DATA/browser-engine.env` |
| Signed language request | `$METNOS_USER_STATE/i18n/localization_request.json` |

- The bootstrap `.venv` only launches the installer. Services never run from it.
- The caller's service profile and real consent are copied into the new installation. Never
  borrow authorities, credentials, history, sessions or databases from another instance.
- Root reading the runtime configuration must create nothing in the service account's tree.
  The service creates its own private directories.

## 5. Contracts that must not break

### 5.1 Dependencies and source

- One lock, `requirements-linux-x86_64.lock`, for bootstrap, phase 1 and the release builder:
  mandatory hashes, binary wheels only, nothing resolved outside it. Bootstrap does not upgrade
  pip and installs nothing unpinned.
- A wrong interpreter, a missing lock, a failed dependency install or a failed core import
  stops the installation.
- The handoff puts the repository root **and** its `runtime/` directory first in `PYTHONPATH`.
  The runtime needs both, and an inherited path must never select another checkout. Phase 5's
  import check uses the same two roots and the same environment as the units.
- A source archive installs like a Git clone. It keeps the two boundary-policy inputs
  `internal/reports/rm0007-m4-boundary-inventory.json` and
  `internal/tools/render_contract_boundary_policy.py`; `.gitattributes` excludes their private
  neighbours, not them (`tests/portable/test_distribution_archive.py`).
- The release builder takes every wheel from the offline wheelhouse and checks its hash. It
  publishes a new content-addressed Python environment and never patches an existing one. It
  removes bytecode that has a source file; bytecode without source, links and other forbidden
  files fail validation. Permissions become 0755/0644 with no special bits.
- The lock includes Playwright and `pillow-heif`: HEIC photos are decoded in process, and no
  source photo is converted or overwritten.

### 5.2 Consent and language

- The safety notice always needs interactive acceptance.
- The accepted choice records the instance language, an optional requested language and the
  localization state. Phase 3 signs them with the installation author key and writes the file
  atomically. The same choice and corpus version leave it byte for byte unchanged.
- The corpus holds authenticated published revisions only. An empty reservation left by a
  rejected first admission adds nothing. A missing current pointer with history, a malformed
  pointer or a link stops verification.

### 5.3 Authorities and keys

- Every installation gets fresh authorities (`_prepare_install_source_v1`): author material,
  root-only operator and reviewer keys, public registries. The user runs no separate command.
- Only the public registries reach the service account. On re-entry the existing authorities
  are verified exactly: a link, changed bytes, ownership or permissions stop the installation.
- A phase marker or a write by the service account never authorizes activation.
- The operator registry grants the named scopes `active`, `authority` and `reactivation`, never
  a generic `birth` scope. An existing installation keeps its registry (changes: section 6).
- The administrator must back up the private keys.

### 5.4 Signed release and transition

The transition is the only operation that selects a new signed release. It stores one exact
reviewed source tree as root, builds and signs the release, verifies the installed copy and
activates only the units named by the signed service catalog. Order: release and initial
catalog are prepared with services inactive; then phases 3 and 4; activation comes last.

- **Resumable, not reversible.** A repetition changes nothing; an interruption resumes the
  recorded transaction. An older release can never be selected again: recovery uses the same
  transaction or a later release with a higher sequence.
- **Two steps, two sets of files.** The administrative step installs only the artifact marked
  `install_phase=group6_admin`, the preflight executable. Artifacts marked `group7_cutover`,
  every signed systemd unit included, are verified there and copied only by the cutover.
- The administrative step needs an `AuthenticatedDistributionRecordV1`, never a
  `VerifiedDistribution`. It verifies the whole release twice around one stable capture and
  cross-checks hashes, paths, phases and service account before changing anything.
- Files are published by staging, no-replace rename and directory sync. An exact final tree is
  accepted, an exact completed staging is resumed, anything else needs explicit recovery.
- The systemd version is checked before sources load or services stop. That early check
  attests nothing: the later double observation and signed version binding stay mandatory.
- The whole unit set is validated against the candidate and predecessor catalogs before the
  startup gate is installed, and again under the three transition locks. A foreign unit file,
  unsafe metadata or conflicting staging stops everything before any unit changes.
- The systemd snapshot is signed before timers start. Links that systemd derives from a
  catalog timer (`TriggeredBy`, implicit `After`) are covered by its signed `Timer.Unit`;
  other undeclared edges are refused. An unconfigured watchdog has one identity (`0` or
  `infinity`).
- A mount found by the kernel is recorded as `kernel_mount`: loaded, non-transient, exact
  `/proc/self/mountinfo` origin, no unit file or drop-in, digest of mount point, source, type
  and options. Only the root mount `-.mount` may have an empty source.
- The startup lock is volatile, not an authority record. The tmpfiles rule creates it at boot,
  before the gated services; the rule is boot-only and never truncates. An unexpected existing
  rule is refused, not overwritten.
- Contract convergence runs as the service account in one bounded transient system service
  with `Delegate=yes` and `DelegateSubgroup=metnos-birth-host`: a plain uid-switched
  subprocess cannot isolate the runner. Its environment is rebuilt from the authenticated
  descriptor. Interruption stops the whole unit; nothing persistent is left.
- In the first transition units may go from absent to installed but stay inactive (PID zero).
  An unknown manager state is refused. Services are never stopped implicitly.
- The contract-receipt catalog and the signed service catalog are two identities. Each has its
  own check and is read again under the locks.

A later release (a successor):

- Starts only from a completed predecessor at `PREFLIGHT_VERIFIED`. A pending, abandoned or
  incomplete release is not a starting point.
- The predecessor's completed provisioning journal is renamed, bytes and permissions intact,
  to `.birth-provisioning-v2.completed.<transaction-id>`: inert evidence, not authority and
  not a recovery input. An incomplete or ambiguous journal is never archived.
- An unchanged executor keeps its admission once its exact generation and source match the
  predecessor's signed receipt. Property run, semantic review and operator approval are
  recorded as "not applicable", not as passed again. No consent is issued or extended. New or
  changed executors get the full checks.
- Unchanged contracts are authenticated with the historical public keys, read only. Old
  authority is never executed under changed source.
- The history of repository retirements may grow but not change: removals, changed identities,
  duplicate destinations and extra unit retirements are refused. No file is fabricated,
  deleted or renamed. Unsupported changes are refused before services stop. No flag disables
  these checks.

First transition over an older, unsigned installation:

- Every declared legacy repository file must exist. The bindings name files of the old
  installation, not every file of the new release.
- The running user-level HTTP unit is stopped inside the coordinated switch. The signed system
  unit of the same name takes over and is not masked.
- The initial catalog's serialization lock stays outside the moved container. Verification
  accepts that exact lock and no other unexpected entry.

### 5.5 Review and activation of generated executors

The root-owned launcher `metnos-synth-review` signs generated executors. It runs
`install.synth_review` from the selected release and accepts exactly three verbs.

- `review` takes the candidate bytes and three to six cases confirmed by the operator.
  `approve` takes only the exact review identity, subject hash and decision. `activate` is
  both, for a consent that the service reports: it is the signer behind the «Activate» button
  (ADR 0240).
- `activate` arrives on `/run/metnos-synth-decision/socket` (root and the service group, one
  connection at a time). The launcher accepts it only from the administration service.
  Executors never see the socket, and the developer account does not get `activate`.
- The work runs in a bounded transient systemd unit under the service identity. Code never
  runs as root. Interruption stops the whole unit.
- The code digest is computed from the captured source bytes before tests or consent are bound.
- Review records and keys stay root-only. Only a verified signed proof reaches the service's
  evidence store, written by the Birth provisioner. The caller cannot choose a storage path.
- Review grants no admission. A separate expiring consent resumes the original Synth producer
  through every Birth check; other producers are refused. An admitted candidate is active; a
  changed candidate fails the ordinary checks.
- Linux only: the portable tests do not prove the same custody on Windows.

### 5.6 Services and units

- Every persistent unit is a system unit that runs as the service account. No user linger.
- Never edit a signed unit and never add a drop-in: preflight rejects them. A change of units,
  environment or service topology needs a new signed release.
- `install/units/*.tmpl` and the signed service catalog must declare the same things:
  scheduler opt-in, browser download mount, watchdog clock.
- Before activation the transition installs a minimal polkit rule for the service account and
  the exact catalog units: start, stop, restart, and restart of the Metnos target. No
  wildcards, no unit-file changes. An identical root-owned rule is reused. Missing polkit, a
  link or a conflicting rule stops activation; an administrator's policy is never replaced.
- The i18n translator timer is mandatory: the target requires it and readiness fails when it
  is not active. Its worker may be idle between runs.
- Readiness checks the server and the catalog contracts, not an open port.
- Coordinated start, stop and restart go through `runtime/stack_reconcile.py`, after checking
  that no turn and no browser session would be interrupted.
- Watchdog: first check three minutes after its timer starts, then every 30 minutes. Services
  lets an administrator set 5–1440 minutes with no restart and no release. Only the interval
  can change; the command stays signed.
- A failed readiness must not call the stack-wide stop unit. The watchdog leaves a
  maintenance-only HTTP process running and never restarts through an unverified catalog.
- **Maintenance-only mode.** If Birth or prompt bootstrap fails inside HTTP, the server keeps
  only its authenticated maintenance routes (model configuration, bounded service control): no
  scheduler, no background producer, no execution, no publication. Health says
  `operational=false` and `maintenance_only=true`. A controlled restart after the repair
  tries again.
- A fresh install never installs competing user units, disables an unrelated listener or
  replaces an incompatible local companion. Existing installations change only through the
  administrative release path, in a maintenance window.
- Development-only units are not in the public service catalog.

### 5.7 Service profile and companions

`install/sidecar.py::SIDECARS` is the registry; derive lists and tests from it.

| Companion | Purpose | How it runs |
|---|---|---|
| `searxng` | web search | system unit with health check |
| `photon` | offline geocoding | system unit with health check |
| `playwright` | JavaScript pages and graphical site sessions (needs Xvfb) | system unit |
| `vlm` | image understanding | started on demand, no persistent unit |

- Everything is prepared locally unless `services.toml` names an existing endpoint. A local
  endpoint that answers but is not declared is a conflict, not permission to adopt it.
- The profile is validated and probed before the phase-2 downloads and before the phase-5
  checks. An invalid or unreachable entry stops the installation; nothing replaces it.
- No cloud fallback after a local failure (ADR 0228). The frontier role needs explicit consent
  and credentials.
- The profile is part of the signed release. The signed service catalog leaves out services
  that someone else owns.
- Local dependency units are prepared inactive under fixed system names (`llama-server.service`,
  `searxng.service`, `photon.service`). A different existing unit is refused, never replaced.
  Moving a local companion to another owner is an explicit migration.
- Report five states apart: downloaded, installed, started, healthy, failed. Downloaded files
  are not a healthy service and a partial result is never success. A missing companion leaves
  only its own capability dormant.

### 5.8 Models and assets

- `fast` (levels `micro`, `procedural`, `fidelity`), `middle`, `wise`, `creative` and
  `frontier` are logical roles. The planner must not depend on a concrete model name.
- Bindings live in `llm_tiers.toml`, `embedding_tiers.toml` and `vlm_tiers.toml` in the
  configuration directory. The chat shows them in **Settings → System → Models**.
- Units must not export temperature, thinking or reasoning-budget settings: those belong to
  the tier configuration. Operations may still set output ceilings, deadlines, grammars and
  tool schemas.
- BGE-M3 (ONNX model and tokenizer) is mandatory. Phase 2 puts it where the in-process
  embedder reads it and checks the pinned SHA-256 values; a missing or corrupt file aborts.
- Pin every downloaded model in the release pipeline: an immutable source revision and a
  SHA-256, whenever upstream offers a stable artifact. Code and `manifest.toml` carry the
  same hashes.
- Models are data, not signed source. Installer, runtime, Tutor fingerprints and sandbox read
  the same location. Changing code does not move models.
- The reCAPTCHA tile classifier (`captcha_grid_classifier`) is optional. Phase 2 asks, default
  no; `--yes` leaves it out; the release never ships it. Without it only the automatic answer
  to 3x3 image grids is off: they go to the person. Source and license are in INSTALL.md.

Local vision on a signed installation reads its paths from `/etc/metnos/vlm-startup.toml`. The
installer writes it from the verified asset paths; an administrator may also place it:

```toml
[default]
model = "/srv/metnos-models/vision.gguf"
mmproj = "/srv/metnos-models/vision-projection.gguf"
llama_bin = "/srv/metnos-engines/llama/bin/llama-server"
```

- Role names match `vlm_tiers.toml`. The file and its parent directories are root-owned, not
  writable by group or others, and not links. Paths must be readable by the service account.
- The profile downloads nothing. No profile: default startup. Invalid profile: fail closed.
- `METNOS_VLM_MODEL`, `METNOS_VLM_MMPROJ` and `METNOS_VLM_LLAMA_BIN` win where a trusted
  launcher already sets them. Ordinary model configuration cannot add host executables, shell
  commands or environment variables.
- HTTP and LRE share one readiness function and one startup lock. LRE starts vision only after
  admission, resource acquisition and runner verification. Executors never start host
  processes from their sandbox.

### 5.9 Browser

- Default engine: Chromium. `METNOS_SITES_BROWSER_ENGINE=camoufox` selects the pinned Camoufox
  build with its fingerprint masking; `METNOS_SITES_STEALTH_ALLOWED=false` forbids it.
- Camoufox also needs `METNOS_SITES_WEBSOCKETS_ALLOWED=1`. That setting opens WebSocket
  destinations for either engine and is off by default; without it Camoufox refuses to start.
  HTTP host and credential controls stay on.
- No engine fallback, no extra browser extension. The CAPTCHA library's client dependencies do
  not authorize an external solver.
- `python -m install.playwright_sidecar --prepare` prepares without activating. Archive length
  and SHA-256 are checked before extraction. Startup never downloads a browser. Versions are
  pinned in the lock.
- Engine, sockets, stealth ceiling and browser directory are saved atomically (mode 0600) in
  `browser-engine.env`, read before the engine is selected and kept by later runs. Explicit
  environment values win, including an administrative "false" ceiling.
- Downloads use a tmpfs at `/run/metnos-browser-downloads`, 128 MiB in total for all engines
  and sessions (`size=134217728,mode=1777,nodev,nosuid,noexec`). Unit template and signed
  catalog declare the same mount. At startup the service verifies the real mount and quota; a
  missing or larger mount stops it. One completed file may be 32 MiB at most.
- A site download is captured before the approved click, transferred with its hash and
  published atomically by the executor in its sandbox. The site's file name never chooses the
  output path. A timeout or an uncertain effect never authorizes another click (ADR 0191).

### 5.10 Files on the server

- The permissions page keeps existing personal and workspace access. Extra server grants are
  per account, independent of device grants, stored with audit events in the devices
  database. They do not change OS permissions.
- Its texts reach the installation seed through `scripts/sync_filesystem_permissions_i18n.py`
  and `scripts/sync_server_folders_i18n.py`. The seed ships with the matching runtime and
  templates.
- Grants outside the service workspace are served by one separate filesystem host, under the
  ordinary OS identity of whoever installed. The root-owned binding keeps that UID/GID across
  upgrades. An application administrator never chooses or acquires an OS identity.
- After the cutover the installer points the host unit at the new release. It replaces the
  unit only when the installed one is exactly a known managed recipe; any other edit or
  override blocks the refresh.
- Until that refresh succeeds the host runs the previous code. The service asks the host which
  closed operations it knows before sending one that older hosts lack (folder creation, move); an
  older host is refused before any effect (`ERR_FS_HOST_OUTDATED`), never treated as success.
- The host accepts only the service UID in its own user namespace. The service keeps connected
  descriptors away from executors. Account grants decide every operation, including live
  revocation and expiry. A grant shows as active only after a harmless access probe succeeds
  under the identity that will do the work.
- The host unit allows only the `AF_UNIX` and `AF_NETLINK` socket families. Bubblewrap needs
  `NETLINK_ROUTE` to set up loopback for the document worker. `AF_INET` and `AF_INET6` stay
  denied, with `NoNewPrivileges=yes` and no capabilities.
- Device file operations need a client that advertises `filesystem_broker_v1` and signed
  matching contracts; older clients see an update notice. Limits and lifetimes: ADR 0237.

### 5.11 Catalogs, translations and Tutor

- A fresh install must seed the full catalog from `install/data/i18n_seed.sqlite`: runtime
  `i18n` schema, SQLite integrity check passed, every key needed at first boot, in both
  supported languages. A small fallback table is not a release artifact.
- For an existing account the runtime adds only missing `(key, language)` rows and never
  overwrites a translation.
- Signatures shipped with executors do not create local trust. **Initial adoption** does: a
  proven new instance may adopt the exact catalog of a reviewed release. Root validates the
  source census and the authenticated initial journal and hands an immutable root-owned
  descriptor to the initial producer. Each submission must match its code, manifest, language
  state, origin and admission context. A resume accepts only the same catalog. An empty store
  or a fresh signature is not enough.
- In adoption the standard, lint, closure and authorization checks stay mandatory; receipts
  mark adoption and dynamic checks as "not applicable". Anything new, changed or imported
  later needs full Birth. Catalog activation is atomic.
- The functional runner takes its support files from the signed release, Monitor and the i18n
  seed included, never from the host runtime or a live translation database. They are bound
  to the proof: a changed dependency invalidates it. The release controller runs the same
  isolated runner before stopping services; that does not replace Birth admission.
- Disabled skills keep their installed contracts; visibility is separate.
- Documentation feeds Tutor. A change to public documentation, UI navigation, manifests,
  executor descriptions or installation guides needs a Tutor rebuild and a check with a real
  question before release.

### 5.12 Credentials

- Phase 4 writes only canonical dictionary payloads through `runtime/credentials.py`: no
  second plaintext format. A single token uses a stable domain and a `value` field; a mail
  account uses its own domain and the fields its IMAP/SMTP backend needs.
- The installer never asks for a Google password. The OAuth result is stored in the user's
  credential scope. Extra mailboxes are separate credentials of the same Metnos user.
- The vault has its own key, `vault.key`, created by `runtime/credentials.py` at first use and
  never by the installer. The administrator key `admin.key` does not open it (ADR 0082). A
  backup or migration carries `vault.key` together with `credentials/`.
- No executor sandbox receives `admin.key`. A broad file grant never exposes the configuration
  root (`runtime/sandbox.py`, `_mask_private_configuration`).

### 5.13 Private runtime settings

The HTTP unit does not select a runtime engine; the runtime has one engine. The signed unit
inherits no extra environment. Instance choices go in `runtime.toml`, so that no personal
account name enters the public catalog.
Defaults when a key is absent:

```toml
[mail]
default_account = "metnos_system"

[telos]
nightly_enabled = false
```

- `METNOS_DEFAULT_MAIL_ACCOUNT` and `METNOS_TELOS_NIGHTLY` override the file. For the nightly
  job only the exact value `1` enables; any other value disables.
- The mail value is a non-empty string, the nightly value a boolean. A wrong type or an empty
  mail override fails; it never selects another account.
- A mail send without an explicit account resolves the default once and uses it for credential
  mounts and child environment. An explicit account wins. Children do not get `runtime.toml`.
- A bad optional mail setting blocks that send only, not HTTP startup or other channels.

### 5.14 LRE worker

- Before activation the installer creates `lre.env` (mode 0600) only if it does not exist.
  Fresh install: disabled. An update keeps the file byte for byte, even an invalid one. A
  missing, linked, oversized, ambiguous or malformed file fails closed.
- Worker and HTTP read it with the same strict runtime parser. The unit must not load it as a
  systemd `EnvironmentFile`: that is a second parser with different rules.
- Services writes only the canonical form and restarts the exact catalog unit. Disabling LRE
  never removes its store or artifacts.
- The worker uses the same bounded scheduler as HTTP. Without `METNOS_DURABLE_WORKERS` it
  derives its lanes from the free capacity, keeping one spare executor thread. A serial
  override and the central parallelism gate win. Capacity does not grow: one LLM slot and one
  VLM slot by default (ADR 0213).

### 5.15 Network

- The managed listener is `0.0.0.0:8770`: the server itself and the trusted private LAN. HTTP
  is unencrypted. Never describe it as safe for router port forwarding or Internet exposure.
- The public installer installs, owns and documents no remote-access service.

## 6. Administrative commands after installation

The scripts under `internal/tools/` are maintainer tools; a source archive does not contain them.

| Need | Command |
|---|---|
| Show the operator authority | `sudo internal/tools/install_operator_authority.sh` |
| Replace lost operator keys | the same script with `--replace-lost-keys` |
| Replace an older scope list | `python -m install.operator_authority --user <service-account> --upgrade-scopes` |
| Install the signer of generated executors | `sudo internal/tools/install_synth_review.sh <account>` |
| Publish one changed executor | `stack_reconcile deploy --executor <name> --changed-only --source-root <clean-primary-checkout> --sign` |
| The same, from development | `internal/tools/rm0008_release_cycle.py publish --executor <name> [--plan]` |
| Add the grid classifier later | `sudo -H -u metnos bash -s captcha-grid < install/download_models.sh` |

- **Lost keys.** Without the private operator and reviewer keys nothing can be signed, so no
  generated executor can become active. The ordinary procedure refuses to repair this.
  `--replace-lost-keys` moves the installed public registry, whole, to
  `/var/lib/metnos-operator-authority/retired/` and creates a fresh authority. It is refused
  while either private key exists. The script runs the selected signed release, never a
  worktree. The next release adopts the new registry.
- **Scopes.** `--upgrade-scopes` replaces only an older scope list and keeps both private
  keys. It takes effect at the next release selection.
- **Publishing one executor.** Replace `--sign` with `--plan` first: it is the read-only
  comparison. Run it from the installed release, as the service account. The source root
  supplies candidate files only, never runtime code, configuration or authority. Other
  contracts are untouched and unchanged input needs no new admission. Admission is followed
  by a controlled activation and a verified reread of the catalog. `--sign` submits to Birth;
  it cannot select direct signing. The development wrapper needs a clean primary checkout.

## 7. Retention maintenance (in progress)

Retention maintenance, internally "F6", will delete old physical records safely. Today its
parts exist as libraries, `install/birth_retention_*.py`.

**Not there yet:** an installed command, the coordinator that selects the installed paths, the
complete inventory across stores, the reconciliation of the existing cleanup paths,
qualification under root custody and on Windows. **The presence of these modules activates
nothing and authorizes no cleanup.**

Rules that already hold:

- A session uses a separate receipt-only authority and a durable barrier. Startup and
  administrative writers refuse to run over an unfinished session.
- The owner holds the deployment, startup, authority and Birth locks and checks that the
  installation's writer units are stopped. Shared external model services are not stopped.
- Every deletion follows a signed intent. The private journal stores and verifies each receipt
  before deleting. Recovery confirms absence durably, even when interrupted between unlink and
  directory sync. No plaintext enters the maintenance journal.
- Unknown schemas, namespaces or segments, missing or duplicate references, and a difference
  between two reads stop the inventory.
- An inventory never migrates a live store, never creates a missing one and never guesses a
  path: the installed caller passes store paths explicitly.
- A name gives no right to delete. Empty directories, fence files and rotation locks stay.
- Administrative holds live in an explicit private register and cannot change during an
  unfinished collection.
- The periodic state reaper leaves undo, protected undo, backup history, turn logs and
  periodic audits to this maintenance.

| Store | Always kept | Collectable |
|---|---|---|
| LRE blobs, publication copies, staging | anything a job or result row references | metadata first; files only at a later inventory |
| Undo journal | pending work, reversible effects, partial undo, unclear receipts, the last completed boundary of each actor | whole closed operations |
| Protected undo | secrets until their operation is gone; an unclear operation also keeps the actor's other files | after both retention windows |
| Turn journals and archives | open input, incomplete evidence, contradictory records; a whole compressed segment if one of its turns is kept | closed groups, with all their copies |
| Feedback journals | the 200-line operating window, errors still used by learning, uncertain effects | the rest |
| `_history` backups | copies used by undo or by a kept turn | expired and unreferenced; age is the later of mtime and ctime |
| Nightly proposals and markers | decisions, pending proposals, acceptance history | expired rejected proposals never accepted before |
| Signed store | all signed history, staging, receipt temporaries | never by age |
| Change proposals and approvals | the row with its closure time; human decisions | closure plus 90 days |
| LLM metering | measurements not yet complete | completed ones after 90 days |
| Sites audit | topology records, history with no valid closure | 90 days after close |
| Synthesis proposal files | sources of open change intents | only after a failed run |
| Introspection snapshots | the last two of each operation | older ones after 90 days |
| Durable and image workspaces | unbound scratch, kept for audit | a clean report and absence in every admitted revision |
| Import and quality audits | the last status row of each skill | completed observations only |

The undo inventory recognizes native `undo_progress` receipts, including their boundary and
uncertain actor data. A final successful `undone` also completes a previous progress receipt.

Frozen plans, transaction receipts and unknown history entries still stop the history inventory.

## 8. Validation

**"It started" is not a result.** The health endpoint proves that the server is reachable. An
installation is complete only when a harmless natural-language request goes through the chat
and returns a normal answer.

Quick checks, always with the Metnos environment:

```bash
./.venv/bin/python -m pytest \
  tests/runtime/infra/test_installer_documentation_contract.py \
  tests/runtime/infra/test_installer_phase4_credentials.py \
  tests/runtime/engine/test_phase5_stack_target.py -q
./.venv/bin/python -m runtime.published_docs validate
```

Before a public release, also:

1. Run the public-export gate.
2. Install from scratch in a separate systemd guest with its own service account, data, state,
   configuration, workspace, ports and system services.
3. Cover dependency installation, asset integrity, authenticated initial adoption, catalog
   loading, server readiness, **one real chat turn**, the full isolated test suite, service
   shutdown and restoration of the instance that was there before.
4. Reboot once: startup with no volatile runtime state, held locks keep their identity. A
   successful transition is not a reboot test.
5. Ask Tutor a real question and try each chosen service.
6. Keep the logs on failure. Remove the guest's artifacts only after the result is recorded.

## 9. Known traps

| Trap | Rule |
|---|---|
| `bootstrap.sh --check` writes `.venv`. | For a read-only check use `./.venv/bin/python -m install --check`. |
| `--yes` on a first run. | The safety notice needs a person. Accept it once; then `--yes` works. |
| Editing `services.toml` after phase 2. | The resume stops. Restore the file; another profile needs a new signed release. |
| A local model server already answers. | Declare it in `services.toml`, or it is a conflict. |
| Installing inside a container. | `localhost` is the container. Give endpoints reachable from it. |
| A download has the right size and wrong bytes. | The downloader checks SHA-256, retries once fetching each chunk twice, then deletes the file. Never skip the hash. |
| Models "disappear" after moving the code. | Set `METNOS_MODELS_DIR`, or move the verified files before activation. |
| A drop-in on a signed unit, or `lre.env` as `EnvironmentFile`. | Both forbidden (5.6, 5.14). Use the private settings or a new release. |
| A development checkout run against the live store. | Never. Never publish from a linked worktree either. |
| Dates, old module names, production paths or stories of past fixes in the manifest or the guides. | Leave them out. |

## 10. Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| "Required system programs are missing: …" | A program of section 2 is absent. | Install the packages and run the bootstrap again. |
| "The service profile differs from the prepared installation." | `services.toml` changed after phase 2. | Restore it. |
| "Earlier installation phases are incomplete." | `--only-phase` was used too early. | Run again without it. |
| The installation was interrupted. | Completed phases are kept. | Run `bash install/bootstrap.sh` again. |
| Activation stops on the service-control rule. | `polkitd` is missing, or a different rule or a link is in its place. | Install `polkitd` or remove the conflict. |
| Health shows `maintenance_only=true`. | Birth or prompt bootstrap failed inside HTTP. | Repair, then do a controlled restart. |
| «Activate» says the signer is missing. | The decision socket is not installed. | Run `install_synth_review.sh` again. |
| No generated executor can be activated. | The private operator keys are gone. | See "Lost keys" in section 6. |
| Screen texts appear as `<missing:…>`. | The i18n seed lacks keys. | Rebuild the seed with every first-boot key. |
| Image grids always go to the person. | The optional classifier is absent or its digest differs. | Add it (section 6). |
| The browser unit does not start. | The download tmpfs is missing or larger than declared. | Restore the signed unit. |
| Camoufox does not start. | WebSockets are not allowed, or stealth is forbidden. | Set the variables of 5.9. |
| Documents cannot be read through server grants. | The filesystem host unit lacks `AF_NETLINK`. | Let the installer refresh the unit. |
| Local vision does not start. | `vlm-startup.toml` is invalid or not root-owned. | Fix the file (5.8). |
| The first-access link expired. | It lasts 15 minutes and works once. | Repeat phase 6, or open `/admin/login` with the administrator key. |
| Birth receipts name the states `preexercise`, `quarantined` or `promotion_revision`. | A retired lifecycle wrote them; the current code cannot read them. | Rebuild the installation. Do not migrate it. |

## 11. When a contract changes

Update together, in one change:

- `requirements*.txt` and the lock, for Python packages;
- `install/phases/` and `install/sidecar.py`, for behaviour;
- `install/units/*.tmpl` and the signed service catalog, for services;
- `runtime/virt/` and `runtime/llm_router.py`, for model bindings;
- `install/manifest.toml`, the inventory (it is not a changelog);
- this file, `INSTALL.md`, `README.md` and the public documentation;
- Tutor's compiled catalog and its provenance report.
