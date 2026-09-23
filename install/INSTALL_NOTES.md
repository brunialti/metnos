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

The shell handoff prepends both the selected repository root and its `runtime/`
directory to `PYTHONPATH`. This is required by the runtime's reviewed flat peer
imports; an inherited path can never select modules from another checkout.

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

Before Phase 3 the administrator uses the public, idempotent procedure:

```bash
sudo "$PWD/.venv/bin/python" -m install.operator_authority --user "$USER"
```

The procedure creates two fresh private keys in the root-only fixed location
`/var/lib/metnos-operator-authority/<uid>/` and installs two public registries
in `$METNOS_USER_CONFIG/birth/operator-input-v1/`:

- `approval-authority.json` — public approver keys, actors and scopes;
- `semantic-authority.json` plus `semantic-public/<name>.pub` — the public
  semantic reviewer keys the document references.

The corresponding private keys must never be placed there, in the authority set
or anywhere the Birth process can read. The procedure refuses a root target,
symlinks, unexpected objects, changed bytes, owners or modes; re-entry verifies
an exact completed result. Phase 3 performs a read-only preflight
of these two documents before it publishes the executor contracts: a missing or
malformed registry stops the phase with a distinct error rather than being
completed by a generated key.

On a fresh installation the author key does not exist yet, so the first call
defers without creating any object; the provisioner runs to completion right
after the contracts are installed, and is idempotent. It creates
`author-root-v1`, one immutable `authority-sets/<set_id>` and the marker
`prepared-v1.json`, whose state is `prepared_not_active`.

## Closed-build administrative installation (RM-0008 group 6)

The root-only G6 installer consumes an authenticated closed distribution while
the fixed deployment lock is held. It verifies the full release twice around
one stable capture of the signed deployment descriptor and administrative
preflight, then cross-checks their hashes, paths, phases and service-account
identity before changing the administrative namespace.

The transition passes an `AuthenticatedDistributionRecordV1` to G6, obtained
by authenticating the exact payload and signature of its verified installed
distribution. A `VerifiedDistribution` is not interchangeable with this input;
neither nominal validation nor signature verification may be bypassed.

G6 installs only the artifact marked `install_phase=group6_admin`, as an exact
root-owned executable at
`/usr/libexec/metnos/executor-birth-v1/preflight.py`. Artifacts marked
`group7_cutover`, including every signed systemd unit, are verified but must
not be copied by this step. Publication uses a descriptor-bound staging tree,
no-replace rename and directory synchronization. An exact final tree is
idempotent, an exact completed staging tree is resumed, and every partial,
extra or metadata-inconsistent tree requires explicit recovery.

## Closed-build transition (RM-0008 group 7)

The one-shot administrative transition is the only operation that selects a
new required closed-build head. It receives one exact reviewed source tree into
root-owned content-addressed storage, builds and signs the closed distribution,
verifies it again from the installed copy, completes the durable ownership
coordinator, and activates only the target and readiness units named by the
signed service catalog.

At `RECEIPTS_COMPLETE`, candidate preparation authenticates the distribution
and binds its exact bytes, hashes and release identity to the durable record.
It does not require a build archive that is published only after the cutover
certificate; an existing conflicting archive is still rejected. Dominant
startup binds the current contract-receipt catalog identity, distinct from the
signed service catalog identity. Both observations retain their own checks:
the receipt proof is reread under the transition locks and the service catalog
is recaptured before certification. These checks do not advance publication.

The effective systemd snapshot is signed before timer activation. The inverse
`TriggeredBy` and implicit ordering `After` links of an exact catalog timer are
already bound by its signed `Timer.Unit`. They are normalized consistently in
both the property projection and the dependency inventory as the timer is
loaded or activated; an explicitly declared service `After` is still required.
Undeclared triggers, other dependency edges, and changes to the timer's
configured target remain subject to strict checks. An unconfigured watchdog's
equivalent disabled values (`0` and `infinity`) have one canonical identity;
explicitly configured watchdog values are still checked against the signature.

The complete Linux x86_64 CPython 3.12 release uses
`requirements-linux-x86_64.lock`, including Playwright and its pinned runtime
dependencies because the signed catalog installs the browser sidecar. The
image-indexing decoder also includes pinned `pillow-heif`: HEIC sources are
decoded in process, including digest-named private snapshots. The corresponding
hash-verified wheel must be present in the offline wheelhouse before building.
No source photo is converted or overwritten on disk. The
offline builder verifies wheel hashes and publishes a new content-addressed
Python environment; it never patches an existing environment. Sealing keeps
packaged executable files executable, normalizes permissions to 0755/0644,
and removes special permission bits. Browser binaries and native libraries
remain separate installation prerequisites. This complete release profile
does not change the legacy six-phase installer's optional-sidecar choices.

Before the first transition prepares its new context, unchanged current
contracts are authenticated with the verified historical set's public keys.
This read-only verification does not construct a historical Birth runtime.
Any contract requiring publication still needs the strict runtime context
check; the transition never executes old authority under changed source.

The startup lock is volatile, not an authority record. The administrative
installer also publishes an exact root-owned tmpfiles rule in
`/etc/tmpfiles.d/metnos-executor-birth-v1.conf`. At boot, systemd prepares the
private directory and empty lock before `sysinit.target` and therefore before
the gated services. The boot-only, non-truncating rule never unlinks an existing
lock or grants permission to bypass preflight. An unexpected existing rule is
rejected, not overwritten. Acceptance must include startup after volatile
runtime state is absent, preservation of held lock identities, and an actual
reboot; a successful transition alone is not reboot certification.

Legacy retirement bindings identify required files of the previous installation,
not every entry point of the candidate. The new contract-convergence module is
covered by the candidate's signed runtime inventory and preflight, but is not
required to exist in the old tree. Missing required legacy files remain an error.
On replay, the immutable predecessor census is securely reread and all its
transition bindings are checked against the current authenticated inputs.
It is not rebuilt from paths that retirement may already have renamed, and it
does not replace current quiescence or topology checks.

A successor authenticates the selected completed predecessor and recomputes its
existing signed dominant-startup receipt. Identical historical repository
retirements reuse that proof; the old checkout is not reopened or required to
remain root-owned. A pending, abandoned or incomplete release is not a completed
checkpoint. The selected build and receipt must match, and selection is reread
after the live checks. This changes no first-transition requirement and grants
no execution or publication authority to development sources.

A successor may add a repository retirement binding without rewriting the
initial census. Every previous step must remain identical; removals, changed
identities, duplicate destinations and additional unit retirements are refused.
Only an additional repository entry needs new filesystem evidence: it must
either have the exact preserved size/hash
from the authenticated initial census, or be absent from that census within its
complete source-root coverage and also absent on disk. The latter observation
uses unchanged owned directory handles without following links and refuses
stray retirement/preservation artifacts. Entries known to the census still need
their preserved file. No file is fabricated, deleted or renamed by a successor.
Live service masks, unit replacements and conflicting legacy processes are
still checked; historical process names need no surviving checkout directory.
The release tool and the locked transition share this checkpoint/delta verifier.
It refuses unsupported deltas before stopping services and repeats observations
under the transition locks. No flag disables the checks. The first transition
still requires all declared legacy repository files.

The transition is resumable and exact repetition is idempotent. The live
user-level HTTP unit is stopped inside the coordinated switch and the signed
system unit takes ownership; the same-name system unit is preserved as the
signed destination rather than masked as a retired alias. Once the new head is
required, an older build cannot be selected again. Recovery resumes the exact
recorded transaction or requires a later release with a higher sequence; it
does not restore the former publication path.

Before preparing a successor, the authenticated predecessor must be at
`PREFLIGHT_VERIFIED` (coordinator sequence 6). Under the deployment and Birth
provisioning locks, its exact completed V2 journal is verified against the
historical published set and moved without replacement to
`.birth-provisioning-v2.completed.<transaction-id>` in the same Birth root.
The atomic, durable rename preserves all bytes and permissions, including the
confidential material plan. Incomplete, conflicting or ambiguous journals are
never archived. Completed journals are inert evidence: they are not runtime
authority or selectable recovery inputs. Repetition after the rename does not
read or reactivate the archive and may prepare the next transaction normally.

A successor preserves admission for an unchanged current executor only after
verifying its exact generation and source against the signed receipt and
authenticated context of the immediate predecessor. The prior lifecycle is
preserved; property execution and semantic review are explicitly recorded as
not applicable, with the prior receipt hash, rather than reported as newly
passed. Historical receipts and producer records remain untouched. New or
changed executors still require the full checks; missing or invalid historical
evidence never becomes implicit initial adoption.

This entry is for a release transition, not routine executor maintenance.
After cutover, an ordinary executor edit uses the reconciler from the
authenticated installed release, running as the service account. Its command
is `stack_reconcile deploy --executor <name> --changed-only --source-root
<clean-primary-checkout> --sign`. The explicit source root supplies candidate
files only; it never selects runtime code, configuration or authority. First
replace `--sign` with `--plan` for the read-only comparison. A named deployment
leaves unrelated contracts untouched, and unchanged input needs no new
admission. A successful admission is followed by a quiescence-controlled
activation and a verified catalog reread. Do not run a development checkout's
runtime against the live store, or publish from a linked worktree.

The administrative development wrapper is
`internal/tools/rm0008_release_cycle.py publish --executor <name> [--plan]`.
It authenticates the selected release, requires a clean primary checkout,
and runs the plan before admission. The historical `--sign` flag submits
the candidate to Executor Birth; it cannot select direct signing.

### Optional F5 certification custody (development)

The dedicated certification key is not part of F4's mandatory three-key
inventory. `install.birth_certification_authority_provisioner` owns its
optional fixed-root Linux preparation under the existing administrative lock.
It returns public verification material only and installs neither a
certificate nor an activation. It is not called by the six-phase installer,
ordinary Birth or service startup.

The separate `certification-authority-v1` directory below the administrative
Birth root contains root-owned `private.bin` (0600) and `registry.json` (0644).
Exact retry reuses the key; interrupted preparation resumes the same complete
staged key. Public-without-private or a mismatched pair requires explicit
recovery, not automatic regeneration. A revoked registry stays revoked on
retry. The public reader initializes no user directories and never reads the
private file. Native Windows custody and the evidence-derived certificate
remain unfinished; this procedure must not be presented as F5 activation.

The optional `install.birth_certification_evidence.administrative_evidence_v1`
context records the administrative evidence in a separate fixed-root directory.
Its private, append-only SQLite store preserves the initial defect census,
review proofs, frozen routing profiles and every cycle outcome. A process
interruption remains an interrupted cycle on recovery; it cannot be skipped
when looking for consecutive successes. The same completed turn cannot count
twice. Exact retry/review and artifact hashes never confer publication power.
Only the administrator can open this context; ordinary Birth and startup do
not read or write it. This persistence component does not yet run the HTTP
harness, issue an F5 certificate, migrate state or activate lifecycle changes.

New prepared sets also contain the maintenance capability
`promoter:quarantine`, with its own producer key through the existing catalog.
An older set without that optional F5 capability can still bootstrap F4.
Quarantine itself requires the fixed F5 activation and an already migrated
`birth/executor_epochs.sqlite` in the selected instance state. Its review
outbox is `birth/failure_reviews.sqlite` in the same private directory.
These files are neither populated by ordinary F4 startup nor synthesized as
replacement migration evidence. Live feedback wiring and final qualification
remain development work; provisioning the optional capability does not enable it.

### Optional one-time lifecycle cutover (development)

`install.birth_lifecycle_migration` moves an installation from its name-based
executor lifecycle state to the epoch store. It is not part of the six-phase
installer, ordinary Birth or service startup, and it issues no certificate,
publishes no executor and retires no file.

It is two commands, because its halves need opposite conditions. `plan` runs
while the services run: only a live process can say which stores this
installation selected. A child reads that process's environment — only `HOME`,
`METNOS_USER_DATA`, `METNOS_USER_STATE`, `METNOS_EXECUTOR_STATS_DB` and
`METNOS_PROMOTER_DB`, with the main PID rechecked — drops permanently to the
service account, censuses the stores and decides. The reviewed decision is
recorded root-owned at `certification-v1/migration-plan.json` (0644). Planning
changes nothing else. The selected sources are `executor_stats` in the state
root and `proposal_promote` in the data root.

`apply` holds the existing maintenance barrier for the whole migration and then
proves separately that the services which write those stores are stopped. The
barrier's own target list is the legacy bindings the F4 transition retired;
those units are masked, so asking whether they are stopped always answers yes,
and after that transition the services that really run carry the same names in
system scope. The cutover therefore asks the installed catalog which units this
product runs, and refuses with `cutover_topology_unknown` when it cannot read
them or with `cutover_writer_running` when one is alive. That is a precondition, not an
optimisation: one executor call during the copy would write a row nobody
preserves. It refuses a decision other than the reviewed one, whether the stores
or the catalog selection moved. After the copy each source is made unwritable
(0400) and the observed mode is reported; that stops the next ordinary writer
and cannot close a handle a running process already holds, which is again why
the barrier comes first.

Inside the privileged child the epoch store must already exist at
`birth/executor_epochs.sqlite`; it is not created here. Every selectable
generation is admitted first, so no window exists in which a restricted
executor becomes visible again. The stores are then read read-only and
query-only, preserved byte-identically, decided, and finally restricted, with
the exact source object rechecked before and after. Counters and instants do
not cross: **the inactivity clock restarts at cutover**, so nothing can be
archived for `METNOS_EXECUTOR_DEPRECATED_DAYS` afterwards.

The root parent writes `certification-v1/migration.json` (0644, root-owned)
last, inside the barrier, and only when every decision is settled. An open promotion or a
restriction with no selectable generation blocks the marker: those cases need a
disposition, and losing them silently is what retirement must not do. A
different marker already present is a recovery operation with its own evidence,
never a retry. Until that marker exists the installation keeps using its
name-based state and the command can simply be run again.

The marker selects which store owns lifecycle state. It does not activate F5:
the operations that need a derived qualification still require the separate
certificate, and without it they refuse individually rather than reselecting the
retired state.

### Optional evidence-derived F5 certificate (development)

`install.birth_certification_issuer` signs the F5 activation document. It is
not part of the six-phase installer, ordinary Birth or service startup, and it
publishes no executor and grants no capability.

`derive` reports exactly what `issue` would sign. Both refuse before the
migration: a certificate authorising a lifecycle the installation has not moved
to is the one thing this order exists to prevent. The migration marker is read
first, the migration it names must verify against the epoch store, and only
then is the qualification derived.

The issuer composes the historical reconciliation itself, through the owner
readers, and rereads both raw inventories afterwards. Its only input is the
bounded public archive candidates, which remain untrusted bytes: the
declaration owner accepts them where path, role, size and hash match the
historical signed distribution. No count, receipt list or cycle outcome is
accepted from a caller, and the signed payload carries none.

The threshold is the approved one — at least five genuine technical
admissions, at least two authenticated producers, two complete consecutive
cycles and no open defect in the declared scope. A quarantine is not an
admission. The declared evidence scope is recomputed from the history observed
now and must equal the one the census recorded, so a gap that appeared since
refuses the certificate instead of signing a review of something else. Every
refusal names itself: `census_absent`, `open_defect`, `cycle_interrupted`,
`profile_absent`, `consecutive_cycles_insufficient`,
`technical_admissions_insufficient`, `authenticated_producers_insufficient`,
`duplicate_admission`.

The dedicated key never leaves the signing function, a revoked authority never
signs, and the published document is read back through the runtime's own
`load_f5_activation` before the issuer reports success.

### Private HTTP runtime settings

The signed HTTP recipe selects `METNOS_ENGINE=v3`; its launcher does not inherit
arbitrary environment additions, and the signed-unit preflight rejects extra
service drop-ins. Instance settings reuse `$METNOS_USER_CONFIG/runtime.toml`,
without embedding personal account names in the public catalog:

```toml
[mail]
default_account = "metnos_system"

[telos]
nightly_enabled = false
```

These are the defaults when the corresponding keys are absent. A present
`METNOS_DEFAULT_MAIL_ACCOUNT` or `METNOS_TELOS_NIGHTLY` overrides its file
setting; Telos preserves the exact environment opt-in `1`, with every other
environment value disabling it. The mail value must be a nonempty string and
the Telos file value a boolean; empty mail overrides and incorrectly typed file
settings fail instead of silently selecting another account or enablement state.
Each local mail-send invocation without an explicit account resolves the SMTP
default once and uses that same account for credential mounts and the child
environment. Explicit invocation accounts keep precedence. Children do not
receive `runtime.toml`, and the HTTP process environment is not mutated.
Invalid optional mail configuration blocks that invocation, not HTTP startup
or unrelated channels; it never silently selects another account.
Environment precedence is not permission to modify a signed unit or restore
its legacy drop-ins; preserve private choices in the existing configuration.

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

For a closed-build system service, optional native vision assets are separate
installation prerequisites, not changes to a signed unit. An administrator can
place the existing model, projection and native engine paths in
`/etc/metnos/vlm-startup.toml`:

```toml
[default]
model = "/srv/metnos-models/vision.gguf"
mmproj = "/srv/metnos-models/vision-projection.gguf"
llama_bin = "/srv/metnos-engines/llama/bin/llama-server"
```

The role names match `vlm_tiers.toml`. The profile and its parent directories
must be root-owned, not group/world writable and not symbolic links. Paths
must reference assets readable by the service account and a runnable native
engine; its sibling library directory is projected only into the launcher.
Provision and verify those assets separately: this profile performs no download
and does not make the closed sidecar-install adapter available. A missing
profile preserves legacy startup; an invalid present profile fails closed.
The three existing `METNOS_VLM_MODEL`, `METNOS_VLM_MMPROJ` and
`METNOS_VLM_LLAMA_BIN` startup variables retain precedence where a trusted
launcher already provides them. Ordinary user model configuration cannot add
host executables, shell commands or arbitrary environment variables.

HTTP and LRE use the same host readiness function. LRE starts local vision only
after admission, resource acquisition and runner verification, within the
attempt deadline. Concurrent callers share the startup lock, and a later job
can start the model again after idle shutdown. Executors never launch host
processes from inside their sandbox. PID and log files follow the configured
user state/data roots, including in isolated test installations.

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

The supervised worker opts into the same bounded executor scheduler as HTTP.
With no explicit `METNOS_DURABLE_WORKERS` override, it derives its controller
lanes from that scheduler's available capacity, preserving a spare executor
thread and the existing lane ceiling. An explicit serial override and the
central parallelism gate remain authoritative. Independent ready units may
overlap only within the frozen plan's `max_concurrency`, worker capabilities,
and central resource and executor limits. Idle workers back off; useful
progress refills free lanes without waiting for the next idle poll. The
legacy unit template and the closed-build signed target recipe must declare
the same scheduler opt-in; changing a live signed unit or adding a drop-in is
not a supported activation path.

Model units with a frozen, bounded zero-cost contract reserve their maximum
token use atomically when a lease is acquired. Exact persisted usage replaces
that reservation; expired leases require reconciliation, and unknown usage
blocks further admission. This does not increase model capacity: default host
limits remain one LLM and one VLM slot, the LLM class override remains binding,
and mutating calls without a verified independent path identity stay serial.

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

If Birth or prompt bootstrap fails inside HTTP, the application retains its
existing authenticated maintenance routes for model configuration and bounded
service control. It starts no scheduler or producer-dependent background jobs
and rejects execution/publication requests. Health reports `operational=false`
and `maintenance_only=true`; composite readiness remains false. A controlled
restart after repair reevaluates bootstrap. This application behavior does not
bypass an external systemd startup check or repair a broken Python installation.

In the closed service catalog, aggregate readiness failure must not invoke the
stack-wide stop unit. Each service retains its signed startup verification.
The watchdog leaves an authenticated maintenance-only HTTP process running and
does not restart through an unverified service catalog. Readiness remains false;
an explicit authorized restart retries initialization after repair. Deploying
this policy requires a coherent signed catalog update, not an extra drop-in.

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
