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
`<METNOS_INSTALL_ROOT>/.venv`, installs the exact hashed release dependencies and
then runs the six-phase Python orchestrator from that same checkout. Direct
orchestrator commands must use the installation environment:

```bash
./.venv/bin/python -m install
```

The supported closed distribution is Linux x86_64 with CPython 3.12. Bootstrap
and phase 1 both use `requirements-linux-x86_64.lock` with mandatory hashes,
binary wheels and no dependency resolution outside the lock. An incompatible
interpreter, missing lock, failed dependency installation or failed core import
stops installation; `--force` cannot bypass the supported platform. Bootstrap
does not upgrade pip or install an independent list of unpinned packages.

Source archives retain the same two public boundary-policy inputs as the Git
clone: `internal/reports/rm0007-m4-boundary-inventory.json` and
`internal/tools/render_contract_boundary_policy.py`. `.gitattributes` must
exclude their private neighbours without excluding those installation inputs.
`tests/portable/test_distribution_archive.py` checks the actual Git archive.

Downloaded models live in `$METNOS_USER_DATA/models`, independently of the
sealed source release. `METNOS_MODELS_DIR` explicitly selects an existing
model directory. Installer, runtime, Tutor fingerprints and sandbox projections
resolve the same location. Moving a deployment that used `<install_root>/models`
requires selecting that existing location or moving the verified model files
before activation; changing code alone does not relocate models.

The shell handoff prepends both the selected repository root and its `runtime/`
directory to `PYTHONPATH`. This is required by the runtime's reviewed flat peer
imports; an inherited path can never select modules from another checkout.

The filesystem permissions UI preserves existing personal/workspace access.
Additional server grants are account-scoped and independent of device grants;
they are stored with audit events in the existing devices database. They do not
alter OS permissions. Its IT/EN catalogue is materialized into the installation
seed by `scripts/sync_filesystem_permissions_i18n.py`; this seed must ship with
the matching runtime and templates. Native device filesystem operations require
client 0.2.64 advertising `filesystem_broker_v1` and signed matching contracts.
Older clients display an update notice rather than receiving expanded access.
See ADR 0237 for transfer limits and the five-second device authority lifetime.

Server grants outside the service workspace use one independent filesystem
host under the installer's ordinary OS identity. The root-owned binding in
`/etc/metnos/filesystem-host.json` retains that UID/GID across upgrades; an
application administrator never selects or acquires an OS identity. After the
authenticated release cutover, the installer refreshes the host unit to use
that release's immutable runtime and Python. Its private state and bootstrap
workspace live under `/var/lib/metnos-filesystem-host`; after configuration
initialization it selects the service's canonical workspace for authorization.
The host accepts only the service UID in its own user namespace, and the
service keeps connected descriptors out of executors. The existing account
grants remain authoritative for every operation and commit, including live
revocation and expiry. A grant is reported active only after a nondestructive
access probe succeeds under the identity that will perform its operations.

The host unit permits only `AF_UNIX` and `AF_NETLINK` socket families.
Bubblewrap needs `NETLINK_ROUTE` to configure loopback inside the document
worker's separate network namespace; omitting it prevents document parsing
before the worker starts. `AF_INET` and `AF_INET6` remain denied, with
`NoNewPrivileges=yes` and an empty capability bounding set. Refresh accepts
the exact earlier `AF_UNIX`-only managed recipe for this upgrade; any other
unit modification or override still blocks replacement.

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
The corpus includes authenticated published revisions only. An exact empty
reservation left by a rejected first admission contributes no content. A missing
current pointer with history, malformed pointer or link still stops verification.

## Birth authority inputs (RM-0008 group 2)

The public six-phase coordinator `install/managed_install.py` requests
administrative privileges after real user consent. It prepares the dedicated
service account and calls the existing closed installation transition.
`_prepare_install_source_v1` creates fresh installation author material,
root-only operator/reviewer keys and public registries using the existing
provisioners. Users do not run a separate authority command.

Private operator/reviewer keys live under
`/var/lib/metnos-operator-authority/<uid>/`; only the public authority registries
are copied to the service account. Existing exact authorities are verified on
re-entry. Links, changed bytes, ownership or permissions fail closed. A phase
marker or a service-account write cannot authorize activation.

The operator registry grants the named Birth scopes (`active`, `authority`,
`reactivation`), never a generic `birth` scope. Existing installations retain
their exact registry by default, including a list written by an earlier
installer. An explicit
`python -m install.operator_authority --user <service-account> --upgrade-scopes`
replaces only such an earlier scope list in the provisioning input, preserving
both private keys. It takes effect only in the next authenticated release
selection.

An installation whose private operator/reviewer keys no longer exist (one
provisioned before the keys were kept on the host) can sign nothing, so no
generated executor can become active. The ordinary procedure refuses to
repair it. `python -m install.operator_authority --user <service-account>
--replace-lost-keys` is the explicit replacement: it moves the installed
public registry, whole, to `/var/lib/metnos-operator-authority/retired/` and
creates a fresh authority; it is refused while either private key exists.
The administrator runs it through
`sudo internal/tools/install_operator_authority.sh [--replace-lost-keys]`,
which executes the selected signed release, never a worktree; without the
option it only shows the state. The new registry is adopted by the next
release. The private keys must be backed up by the administrator.

The root-owned `metnos-synth-review` launcher, installed once with
`sudo internal/tools/install_synth_review.sh <account>` after a release, accepts
exactly `review`, `approve` and `activate` and runs `install.synth_review` from
the selected release. The same script removes the retired `metnos-f5-authority`
launcher and its sudoers rule.
`activate` is the signer behind the «Activate» button of the administration
page (ADR 0240): `review` followed by `approve` for a consent the service
reports. It is not granted to the developer account; the script installs
`metnos-synth-decision.socket` (`/run/metnos-synth-decision/socket`, root and
the service group, one connection at a time) and the per-connection
`metnos-synth-decision@.service`, which runs the launcher with the document on
standard input. The launcher accepts a socket request only from the
administration service (account, initial user namespace, its control group),
and executors never see the socket. Without the socket the page says that the
signer is missing and activates nothing. A script that predates `activate`
must be run again once.
The first consumes candidate bytes and three to six operator-confirmed cases;
the second consumes only the exact review identity, subject hash and decision.
Both run the authenticated installed implementation in a bounded transient
systemd unit. The existing Birth runner receives that unit's cgroup delegation
under the service identity; code never runs with the authority's root identity.
Interruption stops the entire unit. Review records and keys remain root-only;
only a verified signed proof reaches the service's semantic evidence store.
The existing Birth provisioner owns that write: it authenticates the proof
against the selected authority and uses its exclusive installation session.
The review caller cannot select a storage path or mutate the reader session.
Before review and approval, retained authoring bytes pass through the existing
closed-candidate preparation. It computes the code digest from captured source
bytes before binding tests or consent; source and language state are preserved.
Review grants no admission: a separate expiring consent resumes the original
Synth producer through all Birth checks; other producers are refused. An
admitted candidate is active. A changed candidate still fails the ordinary
Birth checks.
This administrative integration currently
requires the supported Linux distribution; portable runner tests do not prove
equivalent administrative custody on Windows.

The complete authenticated transition prepares the distribution and initial
catalog while services remain inactive. Activation happens only after the
application preparation phases, through the same administrative transition.

## Closed-build administrative installation (RM-0008 group 6)

Reading runtime configuration from the administrative process must not create
directories in the service account's data tree. The unprivileged runtime
creates its own private subdirectories; host provisioning owns the canonical
parent layout. Importing configuration as root has no initialization effects.

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

The manager interface is qualified for exactly `255.4-1ubuntu8.17` and
`255.4-1ubuntu8.18`. After authenticating the deployment descriptor, a bounded
read through its fixed product systemctl verifies this support before source
loading, edge reservation or service quiescence. This early check grants no
attestation: the later native double observation and exact signed manager
version/topology binding remain mandatory. An existing prerequisite for another
manager version is not reinterpreted or rewritten.

Before installing the startup gate or entering service quiescence, a successor
reads the entire unit set against its authenticated candidate and predecessor
catalogs. The writing core repeats the same validation under all three locks.
A rollback that left both the exact predecessor fragment and its exact
transition-specific historical backup, with no staged candidate, is resumable:
only the restored current copy is removed, then the normal pending replacement
is completed. The backup inode and bytes remain unchanged. Foreign fragments,
unsafe metadata and conflicting staging still fail before any unit is changed;
interruption around removal or publication resumes from the same native states.

The effective systemd snapshot is signed before timer activation. The inverse
`TriggeredBy` and implicit ordering `After` links of an exact catalog timer are
already bound by its signed `Timer.Unit`. They are normalized consistently in
both the property projection and the dependency inventory as the timer is
loaded or activated; an explicitly declared service `After` is still required.
Undeclared triggers, other dependency edges, and changes to the timer's
configured target remain subject to strict checks. An unconfigured watchdog's
equivalent disabled values (`0` and `infinity`) have one canonical identity;
explicitly configured watchdog values are still checked against the signature.

Kernel-discovered mount dependencies are recorded as `kernel_mount`, without
inventing a root-owned fragment. They require a non-transient loaded `.mount`,
the exact `/proc/self/mountinfo` origin, no fragment, unit-file state or drop-ins,
and a digest of the manager's mount point, source, filesystem type and options.
The perpetual root mount synthesized by systemd (`-.mount`, `Where=/`) may
have an empty `SourcePath`; this absence is bound in the digest, with the same
parameter checks. Other mounts cannot use an empty source.
Origin identity is rechecked with those parameters; the existing double
observation detects changes. Other unclassified origins remain denied.

The complete Linux x86_64 CPython 3.12 release uses
`requirements-linux-x86_64.lock`, including Playwright and its pinned runtime
dependencies because the signed catalog installs the browser sidecar. The
image-indexing decoder also includes pinned `pillow-heif`: HEIC sources are
decoded in process, including digest-named private snapshots. The corresponding
hash-verified wheel must be present in the offline wheelhouse before building.
No source photo is converted or overwritten on disk. The
offline builder verifies wheel hashes and publishes a new content-addressed
Python environment; it never patches an existing environment. Before sealing,
source-backed bytecode shipped inside wheels is removed: `--no-compile`
only prevents new caches. Source-less bytecode, links and other forbidden files
still fail validation; original wheel hashes and source files remain unchanged.
Sealing keeps
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

Initial contract convergence runs as the signed service account in one bounded
transient system service. Like the runtime Birth host, it uses `Delegate=yes`
and `DelegateSubgroup=metnos-birth-host`: a plain uid-switched subprocess cannot
provide the runner's required isolation. Its environment is cleared and rebuilt
from the authenticated descriptor; interruption stops the entire transient unit,
with a separate manager timeout if the controller is killed. This starts no
catalog consumer and creates no persistent service.

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
An additional repository entry present in the authenticated initial census
needs its exact preserved size/hash. An entry absent within that census's
complete source-root coverage never belonged to the previous installation:
the signed history proves that no retirement was needed. Later authoring files
at that name do not acquire historical authority and need not be deleted or
root-owned. Outside the census coverage, absence cannot be inferred; bytecode
and cache paths are excluded. Known entries still need their preserved file.
No file is fabricated, deleted or renamed by a successor.
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
preserved; property execution, semantic review and renewed operator approval
are explicitly recorded as not applicable, bound to the prior signed receipt,
rather than reported as newly passed. No new or extended consent is issued.
Historical receipts and producer records remain untouched. New or
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

After initial catalog activation, the candidate's serialization lock remains
outside the moved container. Legacy-state verification accepts this exact
digest-named lock with empty or NUL content and unchanged private service
ownership, permissions and link count. The lock is not deleted or treated as
an incomplete catalog; other unexpected shadow entries remain invalid.

Initial transition maintenance binds its lifecycle lock to the authenticated
deployment account and observes the current signed service catalog. Units may
start absent and become installed, but must remain inactive with PID zero.
This works while the first ownership chain is incomplete, including retry;
ordinary readers continue to reject partial chains. Unknown system-manager
state is refused, and initial services are never stopped implicitly.

The administrative development wrapper is
`internal/tools/rm0008_release_cycle.py publish --executor <name> [--plan]`.
It authenticates the selected release, requires a clean primary checkout,
and runs the plan before admission. The historical `--sign` flag submits
the candidate to Executor Birth; it cannot select direct signing.

F6 preparation uses a separate receipt-only authority and a durable maintenance
barrier. Startup and administrative writers refuse an unfinished session;
the owner holds the existing deployment/startup/authority/Birth locks and
checks that installation-owned writer units and their control groups are
stopped. External shared model services are not stopped. SQLite owners use
native schemas and atomic object bundles, and the private recovery journal
persists and verifies each receipt before deletion. These components have no
installed collection command yet: their presence does not activate F6 or
authorize cleanup. The native LRE blob owner preserves artifacts referenced
by jobs or result rows and refuses incomplete or unreadable stores. Referenced
blobs remain retained while the metadata bundle is collected; only a subsequent
inventory can authorize their deletion. Recovery durably confirms absence,
including interruption between unlink and directory synchronization. Pending
intents always resume the owner's idempotent deletion, even when the payload
is already absent: container cleanup and durability must finish before the
outcome is acknowledged. Native
publication copies and artifact staging have separate owners that reuse the
same file validation. Publication references retain their copies; terminal-job
scratch can close independently of pending delivery records. Collection holds
and retires the native workspace fence, preserving its inode against stale
writers. Package-owned scratch outside ArtifactStore is not covered by this
adapter. Empty directories and fence files are retained. The LRE inventory
joins blob/publication/staging edges only when all native job versions, states
and windows agree. Missing jobs, duplicate files and unresolved references
block the join. A two-pass native test collects closed metadata first, then
unreferenced files, preserving another active user and an administrative hold.
This bounded LRE inventory is not a complete installation census.
The native undo owner compacts whole operations without changing surviving
records or their versions. Pending work, reversible effects, partial undo,
ambiguous receipts and the latest completed boundary per actor stay retained.
Keeping that boundary prevents undo from unexpectedly reaching an older turn.
Recovery resumes a partial copy or durably observes the atomic replacement
using the original signed intent. This component does not resolve external
contract, turn or blob references and has no standalone cleanup entrypoint.
The protected-undo owner joins actual journal handles to authenticated native
envelopes. Referenced secrets remain retained until their operation is gone;
an incomplete or ambiguous operation also retains its actor's unreferenced
files. Missing native receipt data blocks inventory unless a complete undo
could already have discarded it. Private-file checks are shared with the LRE
owners; native decryption is reused with the custody-checked installation key.
Collection rechecks the exact file/key version, references and both retention
windows, then durably confirms absence using the original signed receipt.
No plaintext enters the maintenance journal. Legacy discard/expiry paths and
other history blobs still need integration with the full maintenance scope.
Turn journals now share the undo owner's recoverable JSONL compaction, with
separate native identity and closure rules. Open input, incomplete execution
evidence and contradictory records stay retained. Exact execution receipts
are parsed by the native verifier and matched to the turn and executor;
surviving bytes, versions and feedback readability are preserved. Daily backups
and native gzip archives have distinct physical identities. The archive owner
reuses the turn parser and closure rules, retaining a whole compressed segment
if any turn is open, lacks valid execution evidence or is still within its
retention window. Compressed and expanded input is bounded; corruption, unsafe
custody and unknown entries block inventory. Removal and interrupted recovery
durably confirm absence using the original receipt. External references and
legacy archive pruning still require the complete inventory and maintenance
integration; these components provide no independent cleanup command.
Feedback journals use the same compactor, retaining the native 200-line
operational window, canonical errors still used by unbounded learning and
uncertain effects. Native counters, rejection decisions,
change-intent discovery and observation remain unchanged after collection.
The journal inventory joins feedback and undo to physical turn copies and
their parents. Bidirectional copy references preserve daily
logs, backups and whole gzip segments together when any copy is retained;
closed connected groups remain collectible. Protected-undo projections
must agree before merging their blob references. Missing references, duplicate
objects or changed rereads block inventory. Contract references and the full
installation census still require integration.
Native `_history` backup files now share descriptor custody with private LRE
files, while accepting safe source permissions preserved by native `copy2`.
ArtifactStore's default remains 0600. Backup expiration uses the later of
mtime and ctime, so copying an old source cannot create an already-expired
backup. Actual undo paths and legacy digest fallback retain physical copies;
unfinished operations retain their turn's files and unscoped backups. The
journal join also keeps references from retained turns, archives and feedback,
merging them with protected-undo references only when projections agree.
Unreferenced expired backups are durably removed through signed maintenance
intents, including recovery after unlink. Empty native namespaces remain.
Frozen plans, transaction receipts and unknown history entries currently stop
this inventory: their native closure owners still need integration, as does
the autonomous history reaper. This is not a complete history inventory or an
activated cleanup entrypoint.
Telos proposal journals use the same recoverable compactor. Physical copies
are linked, including timestamp collisions in the native microsecond decision
key. Only expired rejected proposals without a previous acceptance are closed;
acceptance history still requires operative-marker closure. Native decisions
remain durable: physical last-write order and rejection suppression must survive
proposal collection. Pending proposals retain original bytes and file priority.
Acceptance-marker inventory preserves all pending categories and processed
results. Only the native pre-dispatch invalid-input branch closes an expired
marker/result pair; other results still require Birth/job closure. Copies
sharing a signature and matching physical proposals are linked. Custody and
interrupted unlink recovery reuse signed maintenance. This partial owner does
not complete Telos operational closure or activate cleanup.
Signed-store inventory authenticates every physical generation, retirement and
V1/V2 admission receipt with native historical verifiers. It preserves exact
predecessor links and current-pointer roots without loading authoring sources or
applying today's policy to old admissions. All signed history remains retained:
there is no native age-based closure or rollback revocation. Unknown staging or
recovery namespaces stop this partial inventory. This owner grants neither
publication nor collection authority; cross-store references remain to be joined.
The periodic state reaper defers physical undo, protected-undo, backup-history,
turn-log and periodic-audit collection to exclusive retention maintenance.
Native approval expiration still records an expired decision without deleting
the row; independent functional maintenance continues. Other cleanup entrypoints
still need reconciliation. This candidate change does not activate F6.
Native proposal expiration preserves the row and an explicit closure timestamp;
operational readers treat it as absent. A fresh observation reopens it and
invalidates its F6 version. Human decisions remain retained. The native writer
migrates the schema; retention inventory never migrates a live store. Physical
collection requires closure plus 90 days and the existing signed maintenance.
Administrative holds use an explicit private register. Initialization and
version-checked replacement reuse the native durable writer under administrative
exclusion; an interrupted change blocks collection until recovery. Holds cannot
change during an unfinished collection. The installed command still needs wiring.
The complete physical inventory, cross-store references,
existing cleanup entrypoints and installed recovery still require integration.

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

The bootstrap `.venv` only launches the administrative installer. The installed
source and Python environment live in the existing root-owned release stores.
`METNOS_INSTALL_ROOT` and `METNOS_VENV` identify those verified objects.

The fixed service account uses `/var/lib/metnos-service` as its home. Its
`METNOS_USER_CONFIG`, `METNOS_USER_DATA` and `METNOS_USER_STATE` remain distinct
XDG directories, respectively `.config/metnos`, `.local/share/metnos` and
`.local/state/metnos`. The caller's optional services profile and real consent
are copied into this installation; private authorities from other instances,
credentials, history, sessions and mutable databases must never be borrowed.

Clean-install tests use a separate systemd guest with its own service account,
stores and units. A container on the same host sees its own localhost; explicit
reachable endpoints are required to reuse host services.

## Six-phase responsibilities

1. **Bootstrap:** verify the exact installed dependency lock, prerequisite
   imports and service-account directories.
2. **Infrastructure:** download verified BGE-M3 assets and prepare every local
   companion unless explicitly replaced in `services.toml`. No new service is
   activated here; `--skip` is not accepted by the managed flow.
3. **Metnos source:** verify the installed authenticated initial catalog, seed
   stores and localization, then compile and verify Tutor before startup.
   Project the existing native CPU budget into the compiler before importing
   numerical libraries; respect affinity, container quotas and tighter caps.
4. **Sensitive data:** create the administrator key and collect optional
   credentials through the encrypted store. The managed listener uses
   `0.0.0.0:8770`; Google Workspace connects later through OAuth.
5. **System services:** the administrative parent activates the authenticated
   transition; the unprivileged phase checks HTTP and selected service health.
6. **First boot:** select catalogued capabilities, print a consumable onboarding
   link and exact detected URLs, and save the installation summary.

The parent runs application phases as the fixed service account, using the
installed Python environment. It writes LRE's default-off configuration before
activation. Local dependency units are prepared inactive under fixed system
names; existing incompatible units are refused.

Sentinels under `$METNOS_USER_STATE/install/` support resumption. They are not
security evidence: every continuation authenticates the received source,
distribution and durable transition. `--force-phase N` repeats a phase;
`--only-phase N` requires earlier phases. A failed phase never commits its
sentinel. Changing the profile after phase 2 stops resumption.

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
The optional reCAPTCHA tile classifier (`captcha_grid_classifier`,
recaptcha-classification-57k by DannyLuna, MIT) follows this rule. Phase 2 asks
with a default of no and fetches it from Hugging Face at the pinned revision
only on acceptance; `--yes` leaves it out and the distribution never ships it.
Its absence, a failed download or a different digest disables only the automatic
answer to 3x3 image grids, which then go to the person; installation continues.

A compatible text endpoint may be local or remote. Reuse requires an explicit
entry in `services.toml`; a responding undeclared local endpoint is a conflict,
not permission to adopt it. Managed local provisioning must report artifact installation,
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

Distributed executor signatures alone do not establish local trust. The
administrative initial-adoption path below binds the reviewed received source,
catalog and local receipts. Phase 3 verifies the resulting immutable catalog
through the ordinary authenticated loader. It does not run `sign-all`, restore
retired contracts or use an old technical-publisher bypass. Disabled skills
still retain their installed contracts; visibility is a separate concern.

Phase 6 reads the first-party capability switches from
`runtime/skills_catalog.py`; documentation must not maintain a competing list.
The `google-workspace` bundle is first-party but is connected through its own
OAuth-backed provider flow, not through a distinct phase-6 switch.

Documentation changes affect Tutor's knowledge base. Any change to public
documentation, UI navigation, manifests, executor descriptions or installation
guides requires a Tutor rebuild and a query-level verification before release.

## Initial distributed catalog adoption

A proven new instance may adopt the exact catalog of a reviewed distribution.
The root provisioner validates the accepted source census and authenticated
initial journal, then passes an immutable anonymous root-owned descriptor to
the delegated initial producer. Each submission must match its bound code,
manifest, language state, origin and admission context. Resume accepts only
the same catalog. An empty store or a newly generated signature is insufficient.
Standard, lint, closure and authorization checks remain mandatory; signed local
receipts explicitly mark adoption and dynamic checks not applicable. Ordinary new, modified or
imported components still require full Birth. Catalog activation remains atomic.
This candidate requires complete isolated installation acceptance before release.

The functional runner receives its explicit support inventory from the signed
distribution, including Monitor and `install/data/i18n_seed.sqlite`; it never
falls back to the host runtime or a live translation database. These bytes are
bound to the property context, so a changed dependency invalidates reuse of the
corresponding proof. The release controller exercises the same isolated stdio
runner before stopping services. This operational check does not replace Birth
admission or waive isolation, undo, authorization or fresh quiescence checks.

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

The vault is encrypted with its own key, `~/.config/metnos/vault.key`, created
by `runtime/credentials.py` at first use and never by the installer; the HTTP
admin key (`admin.key`) does not open it (ADR 0082). A backup or a migration of
an account must carry `vault.key` together with `credentials/`. No executor
sandbox ever receives `admin.key`, and the configuration root is not exposed by
a broad file grant (`runtime/sandbox.py`, `_mask_private_configuration`).

A laboratory installation that still holds Birth receipts written with the
retired F5 states (`preexercise`, `quarantined`, `promotion_revision`) cannot be
read by the current code: rebuild it, do not migrate it.

## Optional sidecars

`install/sidecar.py::SIDECARS` is the executable registry and therefore the
source from which lists and tests should be derived:

| Name | Purpose | Lifecycle |
|---|---|---|
| `searxng` | self-hosted web search | system service with health check |
| `photon` | offline geocoding | system service with health check |
| `vlm` | visual-language enrichment | lazy process; no persistent unit |
| `playwright` | JavaScript rendering and graphical site sessions | user service; Side also requires Xvfb |

Sidecars are optional capabilities but are installed locally by default.
The optional user `services.toml` selects existing endpoints; every omitted
component stays local. Explicit `--skip` excludes a companion. The profile
is validated and probed before phase-2 downloads and before phase-5 startup;
invalid or unreachable selections stop rather than provisioning replacements.
No cloud fallback is selected after a local failure. Generated private
application bindings agree with the profile included in the signed system
service catalog, which excludes externally owned services. Profile identity is recorded in phases 2 and 5;
resume detects changes, including removal. Existing local companion ownership
must be migrated explicitly before switching. See `install/SERVICES.md`.
An absent sidecar leaves only its
dependent capability dormant or explicitly degraded. A sidecar installer must
distinguish downloaded, installed, started, healthy and failed states; it must
not turn a partial result into success.

The browser engine defaults to Chromium. `METNOS_SITES_BROWSER_ENGINE=camoufox`
selects the pinned Linux x86_64 Camoufox installation, including its integrated
fingerprint masking; `METNOS_SITES_STEALTH_ALLOWED=false` forbids that selection.
Camoufox also requires explicit `METNOS_SITES_WEBSOCKETS_ALLOWED=1`. This setting
allows unrestricted WebSocket destinations for either engine; its public default
is false, and Camoufox fails closed because its isolated world cannot enforce
Playwright's WebSocket interception. HTTP host and credential controls remain.
There is no engine fallback or additional browser extension.

`python -m install.playwright_sidecar --prepare` and the delegating shell script
prepare without activating services. The installer verifies the exact archive
length and SHA-256 before extraction, then records the pinned receipt. Startup
never downloads a browser. The engine, socket choice, stealth ceiling and browser directory are
saved atomically with mode 0600 in `$METNOS_USER_DATA/browser-engine.env`, loaded
by the server before engine selection (also under the signed minimal environment)
and preserved on later installation runs; explicit process environment values
override saved choices, including an administrative false ceiling. The complete release lock includes
Camoufox 0.5.6, Playwright 1.61.0 and playwright-captcha 0.1.5; the CAPTCHA
library's API-client dependencies do not authorize or activate an external solver.

Persistent units are system units running as the dedicated service account.
The administrative coordinator prepares them; no user linger is required. The
VLM remains lazy. For locally installed vision the coordinator writes the
following restricted startup profile from the verified asset paths.

The browser unit has a dedicated `TemporaryFileSystem` at
`/run/metnos-browser-downloads` with an aggregate physical quota of 128 MiB
(`size=134217728,mode=1777,nodev,nosuid,noexec`). Both the closed-build service
catalog recipe and the ordinary unit template declare the same mount; native
manager preflight compares its exact configuration. All browser engines and
sessions share the quota. Startup verifies the actual tmpfs mount and quota,
then creates a private service-owned directory; an absent or oversized mount
fails closed. The separate admission limit is 32 MiB per completed file;
Chromium reception can fill the shared quota before size admission rejects it.
This change does not extend the supported systemd version set.

For ordinary `act_sites` downloads, an existing absolute `dst_dir` activates
native `fs:write` with the typed destination bind. The broker captures before
the approved click/submit and transfers authenticated bytes under `/session/`;
the executor publishes atomically in its filesystem sandbox after hash and
current path-policy checks. A site-provided filename never chooses the output
path. Vector `entries` carry paths and SHA-256 digests for a later `read_files`
invocation. A lost acknowledgement reuses the same verified local file;
timeouts and uncertain website effects do not authorize another click or POST.

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

The supported system-service installation requires `polkitd`, including when
all optional companions are remote. Before target activation, the authenticated
transition installs the existing minimal service-control rule for the selected
service account and exact catalog units. The rule permits start/stop/restart
only, plus restart of the Metnos target; no wildcards or unit-file changes.
An identical root-owned rule is reused. Missing policy support, links or a
conflicting existing rule stop activation rather than silently leaving Services
controls unusable or replacing an administrator's policy.

On a fresh host, the signed system-service catalog owns the HTTP server and
selected companion units. The i18n translator timer is a non-optional dependency: phase 5 installs
it before target activation, the target requires it, and composite readiness
fails when the timer is not active. Its oneshot worker may be inactive between
runs; the continuously active timer is the lifecycle and health object shown in
the Services page. Private development-only units are not part of the public
service catalog. Composite readiness checks the server and catalog contracts rather than
only checking whether a port is open. Coordinated lifecycle operations use
`runtime/stack_reconcile.py` and must first establish that there is no active
turn or browser session that would be interrupted.

The stack watchdog starts three minutes after timer activation. Its lightweight
clock runs the check every 30 minutes by default; administrators can save a
5–1440 minute interval on Services without a restart or another release.
Only the interval is mutable; the command remains in the signed catalog.
An in-progress check finishes before a preference change takes effect.
Installer templates and the signed service catalog must agree; adopting this
clock in an existing installation requires the first signed release.

Before activation, the coordinator configures the supervised LRE worker and creates
`~/.config/metnos/lre.env` with mode `0600` only when the file does not already
exist. A fresh installation is disabled. An update preserves the existing file
byte for byte, including an invalid file that requires operator attention;
missing, linked, oversized, ambiguous or malformed configuration fails closed.
The worker and the HTTP control plane read this file through the same strict
runtime parser. The unit must not load it as a systemd `EnvironmentFile`, which
would introduce a second parser with different acceptance rules. The Services
page writes only the canonical form and restarts the exact catalogued
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

Existing installations use the administrative migration/release path. The
fresh installer must not install competing user units, disable an unrelated
listener or replace an incompatible local companion. Ownership changes and
restarts require the guarded transition and a maintenance window.

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
in a separate systemd guest with isolated data, state, configuration, workspace,
ports and system services. That run must cover dependency installation, asset
integrity, authenticated initial adoption, catalog loading, server readiness, one real chat
turn, the full isolated test suite, service shutdown and restoration of the
pre-existing instance. Preserve logs on failure; remove the isolated account's
artifacts only after the result has been recorded.

Native F6 LLM metering uses the existing recoverable JSONL compactor for the
current log and both numbered and unique rotated segments. Completed native
measurements become eligible after 90 days; identical physical copies share
retention references. Unknown schemas/segments and inconsistent timestamps
block inventory. Empty compacted files and native rotation locks remain; this
component does not expose an installed maintenance command or activate F6.

The candidate Sites audit owner keeps native operational topology records
and session history without a valid closure. Closed history becomes eligible
90 days after the native close/reap event; closure witnesses remain until a
later inventory observes all corresponding history gone. Physical copies and
holds are joined across native rotated segments. Sites and metering share
bounded segment discovery and the existing recoverable JSONL compactor.
Unknown input or unresolved references block inventory. This is component
implementation and Linux validation, not the installed maintenance command,
complete cross-store inventory, Windows qualification or activation.

Synthesis proposal files now have a physical F6 owner for current JSON and
historical `_archived` copies. Only native failed-run states close the source;
synthesis/installation success does not close revision. Open
change intents retain their sources; closed intent witnesses remain until
all source copies are gone, preventing discovery from recreating a collected
row between bounded windows. The existing file custody, SQLite reader and
signed maintenance protocol are reused. Marker/Birth references and
the complete installed inventory still require integration; no activation.

Introvertiva snapshots use physical identities for current files and historical
`_archived/year/month` copies. The native lexical last-two window per operation
stays rooted, including copy references. Older snapshots expire after 90 days
from the latest filename timestamp, mtime or ctime. Collection rechecks the
reader window, file version and custody, and resumes parent-directory sync
after interrupted unlink through the existing signed maintenance protocol.
Unknown namespaces and incomplete JSON stop inventory. Snapshot records remain
available for the complete external-reference join; this component does not
activate collection or claim a complete installation inventory.

The internal inventory composer requires an explicit exclusion guard and exact
owner scope. Shared owners must be the same instances, enumerate the same
physical identities, and agree on every field except references. References
are merged and must resolve. Both aggregate and per-component projections are
compared across two reads. This is not the installed census or an authorization
to collect: operational paths and remaining cross-store evidence are pending.

Historical context and producer declaration readers can now reuse the held
exclusive Birth session, preserving exact source authentication and frontier
checks. Signed-store scans expose immutable native receipt/evidence maps for
every physical copy. Review, approval, admission and independent-evidence
components join exact historical subjects without renewing consent. Consumed
approvals without admission and reviews persisted before evidence remain
open; native non-review evidence gains no invented review dependency. The
explicit context census still requires installed integration. These are
component tests, not root-custody qualification or collection activation.

Producer/admission joins now use native V1 publication and V2 reattestation
verification per physical copy. In-progress rows remain open; an unavailable
initial-predecessor policy fails closed. Native history documents protect
cross-turn backups even when introduced after a plan; aggregate document
bytes are bounded before parsing. The administrative exclusion yields a
readonly callable capability context whose lifetime matches the held locks.
It opens the existing prepared-root reader, never the provisioning layout,
and holds the existing Birth lock with creation disabled.
These changes do not supply the complete installed census or activate F6.

The internal historical context census covers the initial predecessor and all
chain targets, rejects any extra or missing physical authority set, and returns
native public bindings under the held exclusion. It does not infer an initial
producer policy. The administrative guard now also binds the data directory
to the resolved account. Affinity and efficacy audit owners classify only their
known completed native observations; unknown schemas fail closed. No installed
maintenance entrypoint or activation is implied by these component checks.

External durable workspaces now have granular native-fenced physical ownership.
Job closure requires a current native clean report and physical absence across
all admitted revisions. Installed composition must supply the total workspace
resolver and actual ArtifactStore workspace; runtime cleanup integration is
still pending.
Import audits preserve the last physical status row per skill; evaluator and
Stage6 owners recognize completed observations only. Component checks and
independent reviews do not constitute installed qualification or activation.

Signed-store inventory now validates native generation/binding/current staging
through read-only recovery planners and retains it OPEN. It never invokes
recovery deletion. Receipt temporaries are handled separately as described below.
Workspace resolution rejects unregistered historical runners before callbacks.
These component changes are tested and reviewed, not installed F6 activation.

Receipt staging from the native atomic writer is now retained as OPEN_AUDIT
for V1/V2, including empty or truncated writes. Its destination must reference
an authenticated, non-retired generation; custody and inventory budgets still
apply. Temporary bytes never confer admission authority or enter receipt maps.
The three operational SQLite owners now require explicit store paths from the
installed caller, with no ambient configuration fallback. Selecting and
authenticating those paths remains the installed coordinator's responsibility.
Absent native stores remain empty and are not created by inventory. These
component changes do not provide the installed coordinator or activate F6.

The internal installed-environment reader authenticates current native materials
and checks every gated service twice under a caller-held stability boundary.
It resolves the descriptor account and rejects conflicting signed XDG roots.
It returns immutable per-service inputs, without importing runtime config,
applying caller environment, opening Birth locks or renewing host certification.
Absent and differing overrides remain distinct for the future installed resolver;
this reader alone does not attest equal paths, bootstrap or complete F6 coverage.

Native image workspace discovery now supplements surviving revision bindings
from an explicit selected image root. It covers interruption before context
creation and retains unbound scratch as OPEN_AUDIT; names confer no deletion
authority. The existing owner checks fences and inventories contents once,
with bounded discovery and a second namespace observation. Unknown namespace,
writable directories or drift refuse the inventory. This component does not
select installed roots or replace runtime cleanup and its completion reports.

The internal installed-path observer now reuses the launcher's static signed
identity/environment construction and executes a fresh installed probe per
gated Python service. Native trusted Python paths, working directory and
interpreter are retained; candidate runtime modules are not injected. Package
origins precede imports; response keys and process output are bounded. Repeated
paths and authenticated materials must agree under the caller's live exclusion.
Approvals and scheduler defaults share their native writer authority. The probe
also obtains the artifact and source-authority choices from the installed LRE
production factory, verifies that worker and bridge are bound to the same exact
factory, and resolves those choices with the same pure normalizers used by the
two writer constructors. It does not construct a bridge or open either store.
Unsupported writers refuse; these selected paths are not a complete
writer/constructor census.
Root avoids config's known directory-initialization effects, but is not a readonly
sandbox. Component tests simulate host/UID boundaries; access as the service user,
service namespaces, full maintenance composition and installed qualification
remain pending. This change does not alter job archival or activate F6.
