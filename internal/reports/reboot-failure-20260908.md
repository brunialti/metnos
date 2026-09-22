# Post-reboot failure analysis — 2026-09-08

Scope: read-only diagnosis of production Metnos, its local model dependency,
and the corresponding public installation paths. No production files, units,
trust records, credentials or services were changed by this investigation.
Times below are Europe/Rome. Host boot: 18:49:36.

## Confirmed findings

### 1. RM-0008 lacks boot-time initialization of its volatile startup lock

The installed release is
`/var/lib/metnos/executor-birth/releases-v1/00000000000000000001`,
from frozen source `b715a765a0f664d1c2c660e5d48e5f6abada0169`.

- `install/executor_birth_startup_gate.py:115` creates the private runtime
  directory, and lines 127–141 create the empty root-owned lock with mode 0600.
- The productive call is in `install/birth_authority_provisioner.py:5226`,
  during the transition, not ordinary machine startup. The completed-transition
  branch at lines 5211–5218 returns before this initialization.
- Installed `/usr/libexec/metnos/executor-birth-v1/preflight.py:45–52` selects
  `/run/metnos-executor-birth-v1/startup-v1.lock`.
- Operational check/launch acquires that lock before attesting the release
  (line 11833). Its reader deliberately never creates it (line 14469).
- `/run` is tmpfs. The complete runtime directory is absent after this reboot.
  Missing directory metadata maps to the public error
  `birth_ownership_preflight_missing` (lines 5751–5763).
- Effective tmpfiles configuration contains no Metnos startup-lock rule;
  installed system units contain no boot-time initialization dependency for it.

At 18:50:02 HTTP, durable-worker and side-display failed in ExecStartPre with
exit 20. Their application processes never started. This failure is independent
of model availability: recreating only the model service cannot unblock it.

This is a lifecycle defect, not proof that the durable authority chain was lost.
The release, chain, coordinator and attestation directories remain present.
Their presence is not a substitute for a fresh successful full preflight.

### 2. The installed llama.cpp binary retains a temporary build RUNPATH

The configured stable executable resolves to
`/home/roberto/llama.cpp-0827-v030/build/bin/llama-server`.
`libllama-server-impl.so` exists alongside it. However, readelf shows RUNPATH
`/tmp/supra-llama-v030.N9nDBU/build-vulkan/bin:` in both the executable and
private shared libraries. That temporary directory is now absent.

Read-only reproduction with the ELF loader:

1. Normal dependency listing fails to find `libllama-server-impl.so`.
2. Listing with an explicit library path to the real installed binary directory
   resolves the private libraries, including the Vulkan dependency, successfully.

Neither probe starts the model or runs inference. The defect is a relocated
build still depending on its staging directory; it is not a missing GGUF,
Telegram error or RM-0008 authority error. The precise deletion time of the
temporary build directory was not established.

During the investigation, an independent edit appeared at 19:52:06 in
`/opt/suprastructure/scripts/supra-model` and
`src/suprastructure/models/__init__.py`. It resolves the binary symlink and
prepends its actual directory to LD_LIBRARY_PATH. The existing service retry
started PID 83339 at 19:52:09; the model health endpoint subsequently returned
`{"status":"ok"}`. This investigation did not make that edit or restart it.
This recovery does not certify a future reboot or the public installer.

### 3. Whole-stack shutdown explains why Restart=on-failure did not recover Metnos

`metnos-stack-ready.service` requires HTTP and the model, and declares
`OnFailure=metnos-stack-quarantine.service`. The readiness dependency failure
invoked the quarantine unit, which explicitly stopped the Metnos services and
both timers, including the watchdog. Telegram was terminated during preflight.
`metnos.target` never reached active state. No retry jobs remained for Metnos.

The coordinated stop is deliberate fail-closed behavior, not the originating
defect. Per-service restart settings cannot be treated as a complete recovery
mechanism after an explicit coordinated stop. Removing the safety checks or
adding blind retry loops would not repair missing prerequisites.

The older production user units under Roberto remain persistently masked.
Additional failing user-unit logs belong to the separate `metnos-e2e` account,
not a demonstrated reactivation of the retired production owner.

## Public installation implications

The local model service uses the private Suprastructure launcher. The public
managed path in the installed release is different:

- `install/llm_manager.py:476–528` extracts a prebuilt archive into persistent
  storage, rather than performing this host's temporary source-build relocation.
  Therefore the exact temporary RUNPATH is proven for this host, not every
  upstream prebuilt archive.
- Existing binaries are reused based on presence (lines 482–486), without a
  dependency-resolution/relocatability check.
- The generated public user unit executes the binary directly (lines 557–574).
  It does not use the newly corrected private Suprastructure launcher.
- `install_user_unit` returns early when the endpoint already responds
  (lines 613–617), before enabling or validating the managed unit's boot path.
  An isolated execution of that exact function, with filesystem and subprocess
  calls mocked, returned installed/healthy true and enabled/started false,
  with zero systemctl calls. This is not a production startup test.
- `provision` computes `ok` from binary/model presence (line 794), separately
  from service health. Phase 2 preserves an unhealthy flag and prints a warning;
  it nevertheless records the local path as provisioned (phase2_infra.py:228–241).
- An explicitly reused external endpoint is a legitimate separate mode, but its
  reachability today must not imply installer-owned restart guarantees.

The current tests for the RM-0008 startup gate create the gate explicitly before
using it. The install test validates create/reopen/lock and rejects bad metadata;
it does not demonstrate ordinary startup after volatile state disappears.
No reboot/cold-boot/tmpfiles coverage was found in the searched portable Python
tests. The roadmap itself requires a reboot in its operational evidence
(`internal/roadmap/RM-0008*.md`, section 23.2). The earlier successful transition
and functional turns did not demonstrate that requirement.

## Bounded correction and acceptance criteria — not implemented here

1. Give the fixed root-owned startup lock an explicit boot lifecycle, completed
   before every gated entrypoint. Preserve path, ownership, permissions and
   fail-closed validation. Never replace/unlink an existing lock while holders
   may exist; do not give each service ownership of the same shared directory.
   Represent the mechanism in the installer and the authenticated distribution,
   not only as an undocumented host repair or a replay of the old cutover.
2. Install a relocatable llama.cpp bundle or provide one controlled common
   dependency-resolution policy for managed launches. Cover both server and
   companion binaries. Validate from the final installation directory with
   the original staging directory unavailable and no ambient shell shortcuts.
3. Keep managed installation distinct from external endpoint reuse. Verify the
   managed startup registration, actual endpoint health and whole-stack readiness;
   an existing unrelated listener must not stand in for these checks.
4. Add automated tests for missing runtime state, hostile metadata, shared-lock
   lifetime, relocated shared libraries and the existing-endpoint branch.
5. Certify a real clean boot without an interactive login or surviving staging
   files: model readiness, full preflight, HTTP, Telegram, browser, LRE, i18n
   timer and watchdog; then harmless functional requests on the relevant channels.
   Follow with a second boot to demonstrate repeatability.

No trust reset, authority bypass, broad architecture rewrite or duplicate
supervisor is justified by these findings. Further preflight failures cannot
be ruled out while the first missing prerequisite prevents the full check.

## Follow-up implementation prepared, not yet applied

Persistent isolated worktree: `/opt/metnos/.claude/worktrees/rm0008-reboot`,
branch `codex/rm0008-reboot`, based on reviewed private commit `8519edb1`.
The installer now prepares an exact root-owned, boot-only, non-truncating
tmpfiles rule. Targeted tests cover two simulated cold boots using the real
systemd-tmpfiles command, an existing held lock, malformed rule refusal and
failure before activation. Twenty-one targeted tests passed.

The one-shot `internal/tools/repair_rm0008_boot.py` in that worktree is pinned
to the installed preflight and deployment descriptor. It refuses active work,
holds the deployment lock while preparing the boot rule and runtime, performs
the full existing preflight and checks model readiness before starting the
exact Metnos target. It never changes signed releases, unit definitions or
authorities; it never reboots the host. Results use journal tag
`metnos-boot-repair`. Run only with root and zero arguments.

At preparation time, `sudo -n true` requires a password. The prior RM-0008
passwordless grants have been revoked. Application and actual reboot testing
are therefore still pending a new administrative authentication. The public
llama.cpp installer improvements and release certification are also pending;
the startup-rule tests are not evidence that those separate tasks are complete.

## Operator execution and next denial — 20:59

The operator ran the prepared repair as root. At 20:59:20 it emitted
`BOOT_PREPARATION_INSTALLED`; at 20:59:38 the installed preflight returned
exit 21, `birth_ownership_preflight_invalid`. The persistent tmpfiles rule and
volatile startup directory now exist with the expected ownership and modes.
The repair stopped before target activation. This is not a successful recovery.

The installed CLI deliberately suppresses `PreflightError.detail` in `main`.
A new read-only helper, `internal/tools/diagnose_rm0008_preflight.py` in the
reboot worktree, calls the exact pinned installed check and preserves the denial
while reporting its internal detail and function/line stack (never locals).
It never starts services or changes authority, release or validation rules.
Its output is also available under journal tag `metnos-boot-diagnosis`.

Without root, all 12 signed unit property projections and all 25 discovered
dependency origins were checked against the installed parser successfully.
These partial observations do not authenticate the full ownership snapshot or
prove that the effective-systemd digest still matches its signed prerequisite.
The precise failing condition is still unknown. A noninteractive sudo attempt
for the read-only full diagnosis was denied for lack of authentication.

## Confirmed operating-system update incompatibility — 23:08

Using the installed read-only administrative TCB capture, without root or any
operational authorization, compared current measurements with the readable
signed startup prerequisite. `python_binary_hash` differs. The OpenSSL binary,
systemctl binary, systemd-analyze binary and OpenSSL TCB hashes all still match.
The mandatory comparison in installed preflight line 8659 rejects this state
as `administrative TCB signed binding` before any service launch is allowed.

The dpkg journal records an update of Python packages at 18:48:11–21, immediately
before the 18:49 reboot: `3.12.3-1ubuntu0.15` to `3.12.3-1ubuntu0.16`.
The current `/usr/bin/python3.12` has 8,025,024 bytes; the managed service Python
copy has 8,020,928 bytes. No package downgrade, binary substitution or signature
override was performed. The full root diagnosis has not yet been run, so other
independent failures have not been excluded.

This is a maintenance lifecycle gap, not just the missing volatile lock.
OS maintenance must have a controlled re-attestation/upgrade path; immutable
executor/release authority must not be silently bypassed or retrospectively
rewritten. A normal OS package update must not require resetting the executor
ownership history. The existing private TODO `PY-RUNTIME-IDENTITY-001` had
already identified the split between the administrative and managed Python.

At 23:07 HTTP is still inactive since 18:50:02, localhost:8770 refuses connections
and the model health on :8080 is OK. The reported browser 502 is consistent with
that origin-service outage, not evidence of a new browser or Cloudflare failure.

## Root diagnosis and unsuccessful conservative recovery — 23:20–23:32

Administrative authentication is available using the credential the operator
had already supplied. No credential was written to disk. Lack of root access
must no longer be reported as the blocker.

The exact installed full check, invoked by the read-only diagnosis helper,
confirmed `administrative TCB signed binding`. The official Ubuntu package
`python3.12-minimal=3.12.3-1ubuntu0.15` contains an interpreter whose SHA-256 is
`1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118`, identical
to the previously working managed interpreter. The installed Ubuntu changelog
identifies .16 as an SSL session memory-leak correction above the .15 security
release. An APT simulation selected exactly eight Python packages, no removals.

Those eight packages were conservatively restored to .15, with unrelated
service restarts suppressed. The full check then passed the administrative
TCB comparison but failed the independent effective-systemd binding:

- observed: `sha256:8b28ff5a0e448f8a22a99a4c663881a881e9a49873f8426bed7ea9d71053a2f3`;
- signed: `sha256:1b03d969fe2ffb723c1c8d52f6df1600fa7a7054097a3917b85fa12863ebc74b`.

No Metnos service was started and no authority check was bypassed. Because the
rollback did not restore availability, the original .16 package set was
restored immediately. APT completed with exit 0 and all eight package versions
were independently reread as .16. No hold or automatic-update suppression was
installed. HTTP remains inactive/dead; no residual diagnosis process was found.

The additional helper `internal/tools/inspect_rm0008_systemd.py` captures the
authenticated current systemd observation read-only. All 12 signed fragments
and property constraints pass; their full observation hash still differs.
The signed prerequisite stores the aggregate digest, not the full prior
effective snapshot. The retained pre-transition unit manifest is not that
snapshot and must not be presented as it. Counterfactual removal of each single
additional relation, consistently from the edge list and property projection,
did not reproduce the signed digest. The exact second difference is therefore
not yet established. No external UPS/service ordering was changed.

### Maintenance design gap requiring an explicit decision

The running preflight freezes both OS executables and the entire observed
systemd dependency graph into the release's startup prerequisite. This couples
application availability to incidental host state. It has no ordinary supported
platform-maintenance reauthorization command. The G6 administrative publisher
and dominant-topology publisher also refuse an existing different artifact;
they are not general upgrade mechanisms. Note that the current installed and
source administrative scripts actually have identical bytes: G6 replacement
would become an issue for a changed preflight, not for this current check.

Proposed bounded correction, not yet authorized as a new design or implemented:
retain strict signed executor/release verification, but separate it from host
compatibility and from access to a read-only maintenance interface. Define an
explicit administrator-authorized upgrade/reattestation operation that preserves
history and validates the exact predecessor before replacing mutable deployment
artifacts. Cold boot, normal OS update, controlled deployment replacement and
failure recovery must be acceptance tests, not inferred from a successful first
cutover. Do not clear claims, rewrite immutable receipts, or suppress preflight
to manufacture a success.

## Public CI correction checkpoint

The test-only branch `codex/rm0008-ci-repair`, commit
`8f42b6382bed68ed78277f5c72a4e980d1128cad`, was published after GII reported
zero findings. Main was not changed. Six test files were corrected for platform
assumptions and runner virtualenv identity; local Linux: 2,094 passed, 47 skipped.
Run `34279354831` got past the old collection failures. Windows then exposed
68 further failures (1,408 passed, 630 skipped), predominantly POSIX-specific
tests invoked on Windows. The run failed and must not be called certified or
merged as a completed CI repair. The exact remaining failure list is in job
`102240098119`. CI work is secondary to the production availability incident.

### Follow-up CI repair — 23:46

The Linux portable step of run `34279354831` actually passed (2,096 passed,
45 skipped). Its separate delegated real-systemd step failed before exercising
the signed timer: the full offline Python wheelhouse had never been prepared.
The workflow now installs system Python venv support and downloads every wheel
from the unchanged production lock with mandatory hashes, then installs the
wheelhouse as root-owned read-only files inside the disposable runner VM.

For Windows, actual POSIX filesystem tests are individually marked Linux-only;
simulated authority, ordering, ACL and cleanup tests remain cross-platform.
Missing syscall doubles are supplied explicitly and the store platform test
uses the product entry instead of a POSIX-only fixture helper. No production
validator or signature was changed. The targeted suite passed 433 tests; the
full local Linux suite passed 2,094 with 47 host-dependent skips in 109.75s.

GII reported zero findings. Commit
`9adffc28b724fc845bfe25f4210d3602bc6fae5b` was pushed to the existing repair
branch with English notes; main remains unchanged. New run `34282444426` is
pending verification, not a certification success. The same patches are also
present in the private reboot worktree.

At 23:44:32, native host checks still show HTTP and Telegram inactive/dead,
HTTP health unavailable and model health returning 200. No service restart or
live trust mutation was attempted during this CI correction.

### Follow-up CI and host checkpoint — 9 September, 00:05

Run `34282444426` failed. Windows was reduced to one failing simulated identity
test (1,423 passed, 682 skipped): it supplied POSIX user IDs but still exposed
`os.name == "nt"`. The replacement is a module-local OS double, not a change
to the product's platform refusal. Linux passed the portable suite and full
wheelhouse setup, then failed at the real catalog lock: the activation fixture
had neither bound service state nor created its service-owned catalog lock.
The fixture now prepares those real files in its temporary state and starts
the actual service-account user manager before capturing its signed baseline.
No product lock, signature or denial is mocked in the real activation proof.

The six historical 2A activities also refused the changed workflow before
executing tests. The verifier now admits only the exact old-to-new workflow
blob pair for the reviewed dependency preparation. The original historical
manifest/evidence remain unchanged; all six acceptance jobs and their summary
remain byte-identical. New negative tests reject an unknown blob, changed
mode, changed predecessor, or removal of mandatory wheel hashes. The staged
Python inventory was regenerated with its existing generator (880 paths).

Local checks: 2,101 portable tests passed, 47 skipped; real historical manifest
activity 7 passed; concurrency activity 17 passed. The local portable activity
had 62 passes and five privilege-environment failures at `sudo -n chown` in
the restricted sandbox; these are NOT reported as successful local proof and
the tests remain unchanged. The public clone was initially shallow; fetching
its public history allowed the original pre-fix ancestry check to run.

After GII reported zero findings, English commits `d647183`, `a3f4ae3` and
`41c6774` were pushed to `codex/rm0008-ci-repair`. Main remains unchanged.
Run `34284018003` tests head `41c67748ebd93900541b8e015c1885920f21c94a`.
Its final result is not yet established. Changes are mirrored in the private
reboot worktree; its public-projection inventory is regenerated only for a
future public export, not from the private index.

Native production check at 00:05:15: `metnos-http.service` loaded/inactive/dead,
`metnos-telegram-daemon.service` loaded/failed, `metnos.target` inactive;
HTTP 8770 unavailable, model 8080 healthy. Earlier shorthand references to
`metnos-telegram.service` do not identify the actual installed Telegram unit;
use `metnos-telegram-daemon.service`. No live trust mutation or restart was
made by this CI work. The maintenance-design decision above remains open.

At 00:08, the native Windows job `102255245664` completed successfully:
1,430 portable tests passed, 683 platform/opt-in cases skipped, one warning.
All six 2A activity jobs also completed successfully, including the real
Windows ACL identities and Ubuntu ownership tests that the local sandbox
could not run. Linux's final delegated-systemd job is still running; the full
workflow and main-merge decision remain pending.

### CI correction verified — 9 September, 00:14

Run `34284018003` completed with overall SUCCESS: both platform jobs, all six
2A activities and the blocking summary passed at exact commit
`41c67748ebd93900541b8e015c1885920f21c94a`. Ubuntu passed 2,103 portable tests
(45 skipped), then all seven delegated Linux tests in 404.51 seconds. This
includes the actual signed systemd denial/admission and relational-drift proof,
not only fixtures or syntax checks. Windows passed 1,430 tests (683 skipped).

After rerunning GII (zero findings), the same tested commit was fast-forwarded
to public `main`, without rewriting history. No runtime or installer product
code is changed by this CI branch. Its 22 changed files are workflow, tests
and their inventory. English commit notes are preserved.

This closes the reported CI failure for that commit, NOT the production
availability incident. The boot repair remains only in the private worktree
and the already installed tmpfiles rule. HTTP/Telegram have not been restarted
or certified recovered. The explicit maintenance-design decision is still
required; do not clear ownership history or bypass the installed check.

### Approval received and pre-change analysis — 9 September, 08:25

The approval-pending sentence above is now superseded: Roberto explicitly
approved separating certification from ordinary maintenance and requested
proportionate startup controls. He then requested thorough analysis before
product edits. No product change or service restart was made during this
analysis; HTTP remains inactive, Telegram failed, and the model endpoint healthy.

The completed source-path analysis, demonstrated lifecycle/stub defects and
positive/negative acceptance criteria are recorded in
`internal/reports/rm0008-maintenance-analysis-20260909.md`. Four unchanged tests
passed while confirming the current unusable maintenance expectations; this
is diagnostic evidence, not a recovery claim. There is no outstanding design
assent to request again. The recorded goal status is stale, not completion.
