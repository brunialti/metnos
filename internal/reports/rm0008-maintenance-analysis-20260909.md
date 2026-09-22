# RM-0008: availability and maintenance analysis

9 September 2026 — **Release 2 selected; application services operational;
final administrative attestation incomplete; work stopped at user request**.

## Stop checkpoint, 15:26 CEST — authoritative handover

Read `/opt/metnos/internal/design/handover_rm0008_stop_9_9_2026_1526.md` first.
The user explicitly requested an immediate detailed handover and a safe stop.
No further deployments, restarts or analysis are running in this task.

New build `sha256:608c3a4590de2d56e9a702bde7f21943a7d103cd7f7ba80f5591e08f3c141059`
passed native audit and genuine receipt preparation/G6/head publication, but
failed the final phase5→6 crossing with `preflight publication`. Native cause:
`administrative TCB signed binding`: the signed descriptor selects managed
Python while the verifier's fixed OS Python path differs. No blind retry was
performed. Correct helper35b3 service-local HTTP attestation passed; normal
starts recovered HTTP644146, browser644215, worker644147 and Telegram644218.
At 15:26 all are active, HTTP is operational (not maintenance-only), worker
ready, browser connected/contracts aligned and no pending browser sessions.
The real new login turn and final reboot remain untested. No new cookie-ready
Telegram was sent. Do not treat this as completed RM-0008 certification.

Public main7132b4d was pushed after GII0, full2404/47 and manifest7/7, English
notes. New CI34356573475 was still in progress when checked. Public guide
deployment for the latest changes remains outstanding. Exact artifacts,
archived failed attempt, scripts, hashes and recovery caveats are in the handover.

## Earlier checkpoint: native continuity proved; unselected attempt preserved

9 September, 15:15 CEST. The read-only native probe of the reviewed continuity
path passed for **all 122 actual current components**, missing 0, refused 0.
It authenticated prior signed receipts and current bytes without issuing
receipts, writing producer state, or contacting external websites. Probe
`/tmp/metnos-rm0008-probe-current-continuity-20260909.py`, SHA256
`1f5429e97b0813f027b677542c5965e2ff0cd3338f752e8508db8522c915c572`.

The exact old, unselected PREPARED N2 was then preserved under deployment,
startup and Birth locks. Four fixed objects were moved with no-replace renames
to `/var/lib/metnos-admin/rm0008-withdrawn-n2-20260909`: the PREPARED coordinator
directory, unselected Birth journal, unselected Release 2 and successor claim
(last). Post-move byte/inode/metadata checks passed; authenticated N1 and its
chain remained unchanged. No service was stopped, receipt erased, rejection
rewritten or authority set removed. The source, published old set, partial
receipts and producer history remain in place. Recovery script SHA256
`9571aa3c604f3daadc8c5061e98535a0746130cf0d80f8d73d6ca07c395bfdc7`;
40 local tests and native audit passed before the operation.

New frozen export `/tmp/metnos-continuity-export.5MXSbT` passed private
2400/47, public 2404/47, native Chromium 38/0, GII zero, and the actual receiver
census. Source ID `sha256:a9327fa4e620ee1b3d26fce365ad1223a9a9a8a7677499c25bf3bf048b71d769`.
New build-only is running; cookie activation is **not yet claimed**. The new
helper reconciler SHA256 `3eaae876594e6841a078339597bf9644a26551213a59ce48865689ed6c4073ef`
binds live cb7f1b8 bytes to signed successor 35b3dc1 and passed 53 tests.
Only the new continuity build/completion path may be used; old N2 completion
instructions below are historical. No real reboot has been performed.

## Earlier checkpoint: availability restored; N2 receipt preparation failed

At 14:43 CEST all four recovered services retained their PIDs, HTTP/browser
health were 200, and the worker was ready. Read-only analysis of turn
`901ab8ad5fb44a7a` found open at 09:01:50 UTC, discovering at 09:01:51,
and login timeout/close at 09:03:56, with no intervening action or recorded
credential submission. Its saved screenshot was copied to a private temporary
directory and inspected: the Telepass homepage remained obscured by an open
cookie banner with “Solo necessari” visible. No login form or CAPTCHA was
visible. This proves unresolved cookies at timeout, not iframe structure or
the absence of any partial typing. No contact with the website was made.
Redacted trace: `/tmp/metnos-sites-901ab8ad-redacted.json`;
screenshot: `/tmp/metnos-turn-901ab8ad.JG3Tm5/login-timeout.png`.

The user explicitly requested activation. The private candidate now contains
authenticated unchanged-current continuity (+196/-7 product lines in four
modules; 290 focused tests passed, 15 skipped). The previous signed receipt,
current bytes/generation and predecessor context must match; dynamic checks
are explicitly N.A. with prior evidence, never falsely PASSED. Native read-only
proof across the actual inventory and a new coherent source freeze remain in
progress. The existing signed N2 is not changed. A separate exact conservative
withdrawal is being tested for its four unselected control objects, preserving
the old set, partial receipts and terminal rejection. No withdrawal or new
activation has yet occurred at this checkpoint.

9 September, 14:32 CEST. N1's exact completed journal was preserved successfully.
The resumed N2 completion then entered maintenance and stopped services, but
failed during reattestation of `core:change_files_format/manifest.toml`, before
G6/helper/unit replacement or head publication. N2 now has a PREPARED record
and staged/published authority set, not merely a pending claim. Several context
receipts were committed; one producer request is terminally rejected:
`sha256:d56bb4d40d2b795d754f89bc7d2629f2b8bac4292cdcda1b17a315a7534c9d59`,
with persisted `property_runner_unavailable` at 12:21:56 UTC. Do not blindly
rerun completion or delete/rewrite that receipt.

The ordinary restart of N1 exposed a second real startup case missing from the
earlier acceptance: the old repaired helper compared **every** transaction's
administrative bundle with N1, including pending N2. It refused with
`administrative bundle changed` before selecting the valid old epoch. The
per-release correction already present in signed N2 was applied to the live
helper by deleting only this six-line global comparison. Thirteen focused tests
reproduced the original failure and retained negative signature/head/binding
checks. A native read-only candidate probe selected and authenticated exact N1
before installation. Installer:
`/tmp/metnos-rm0008-preflight-availability.An1eTJ/install.py`;
live helper SHA256 `cb7f1b8ace83036928d49746dcfa822088fb42e193fdf8de076861d3c7c9d1ee`.
Old cbe320 bytes retained root-owned 0400 at
`/var/lib/metnos-admin/rm0008-preflight-cbe320-before-availability.py`.
No signed release, unit, head, certificate or historical receipt was changed.

Normal service starts recovered HTTP, browser, worker and Telegram. At 14:31
both HTTP health and browser health returned 200; HTTP reported worker ready.
The cookie fix remains **not deployed**. The fixed helper reconciliation runner
still pins the previous cbe320 bytes and **must be coherently repinned/retested
before any future N2 completion**. Current code must also address the failed
runtime-property path and its terminal-receipt recovery; services must not be
stopped again merely to discover the next error. Failed turn `901ab8ad` is being
inspected read-only in parallel. A real reboot remains unperformed.

## Earlier checkpoint: completed N1 journal prevents Release 2 preparation

9 September, 14:18 CEST. The first Release 2 completion invocation refused at
`_resume_published_authority_set_v2` with `birth_provisioning_transaction_conflict`,
**before entering maintenance or stopping services**. It reserved the exact
Release 2 claim but wrote no Release 2 provisioning checkpoint. The immutable
Release 2 and selected Release 1 were not modified.

A locked read-only census established the missing lifecycle step: Release 1 is
already at coordinator phase 6, `PREFLIGHT_VERIFIED`, but its completed journal
`.birth-provisioning-v2.txn.7231f5ab98d54ea94ccbbcfcea3478a1` still occupies the
active-journal namespace. Preparing N2 compares that N1 header with N2 and
refuses. This is a product defect, not an unfinished user transaction.

The conservative repair preserves all six journal objects, including the
confidential material plan with mode 0600, by one same-parent no-replace rename
to `.birth-provisioning-v2.completed.7231f5ab98d54ea94ccbbcfcea3478a1`.
No deletion, new authority or selector rewrite is intended. Exact filesystem
module: `/tmp/metnos-rm0008-archive-completed-journal-20260909.py`, SHA256
`be1f44463782279bbd1bfccfea8e9b1413e50041c1b697387f039ec03c8e474c`.
Its independent 31-test run passed, including rename interruptions and replay.
The root wrapper `/tmp/metnos-rm0008-preserve-completed-n1-20260909.py` binds the
operation to the authenticated N1 head, set and checkpoints under deployment
and exclusive Birth locks; its native audit is in progress. **The rename has
not yet been executed at that checkpoint.** At 14:20 CEST the native operation
completed with `COMPLETED_N1_JOURNAL_PRESERVED archived`; all bytes, inodes and
permissions were reread intact after the single no-replace rename. The same
signed N2 completion runner is now being resumed. No release is patched.
A minimal product lifecycle fix and real consecutive
release tests are being implemented separately for the next coherent increment;
the already signed N2 is not patched.

Public commit `4e3dea411a28ff3bc2b10d5cc9f5cadf4f565a55` is now confirmed on
`main`, and all nine jobs of run `34346999399` passed. Cookie login remains
**not deployed**. No chat-retry-ready notification or real reboot has occurred.
After activation, investigate the user's failed turn `901ab8ad`.

## Earlier checkpoint: signed Release 2 built; pre-cutover checks

9 September, 13:43 CEST. Historical predecessor verification, previous-context
selection, stop-only successor quiescence, exact helper/unit replacement,
read-only legacy-retirement observation and journal 5→6 continuation are now
integrated. Full private portable suite: **2340 passed, 47 skipped**. Native
Chromium/cookie/security and maintenance selection: **325 passed, 1 skipped**.
The corresponding public candidate passed **2344 tests, 47 skipped**, with GII
zero in both filesystem and staged index. The four additional public tests are
existing documentation tests; no executor payload or prior public guide was
replaced. Public publication is being handled separately and is not yet claimed.

A root-authorised, read-only native probe of the candidate verifier authenticated
all four currently selected services: HTTP, Telegram, browser and durable worker.
The probe reread unchanged live and immutable helper hashes afterward; it did
not start or stop services. This proves old-selected-service compatibility, not
the complete successor cutover or reboot.

Frozen private source-review pin:
`sha256:85f0724bd79798057a191f78e4ec22ff32dbea40d47f40dea3cdbf8289df6c88`.
Public/export pin:
`sha256:6c345ff71a66fa5f81b4d94618a35d177cc53fda9ab7681430eb51c5e9dfa2d6`.
Export: `/tmp/metnos-successor-export.Y2Ttpl`. The archive-like export lacks Git
metadata, so its two Git-evidence tests failed; both pass in the public clone.
They were not removed or weakened.

The first build-only invocation refused **before source reception/copying**:
the export preserved worktree file modes 0664/0775, while the receiver requires
0644/0755. Only the temporary export's metadata was normalized, preserving the
executable distinction. Two empty runtime workspace directories and their empty
parent, created by a diagnostic import, were removed from that export; no data
was removed. Local diagnostic imports now use a separate temporary workspace.
The productive source scanner then passed: **1745 files, 201 directories,
31992422 bytes**, with the same reviewed content pin. The build-only retry
completed successfully, without head changes or service stops:

- source: `sha256:72109fe3c63166767c2e8f0c22fc0b16fe1e9ae5064dd078e4a82408962c6c4c`;
- signed Release 2: `sha256:3878c006f041536370eae081bb9b51b4153713a0af506d08dbd16e622a31a514`;
- predecessor context verified: `sha256:469df47af79517e225d380a7a318f9cc17fb8747a0193e8d583d908bde7ed8b5`;
- Release 2 descriptor SHA256: `4058258cd9ebba824437974659beab8653f346336dd8df5e08a7f81ba6f9c34c`;
- signed Release 2 helper SHA256: `d1f803155c9ef2eb5a82d4a32b9616658e3a86687b7a62c71e66efc835f2984b`.

Public increment `4e3dea411a28ff3bc2b10d5cc9f5cadf4f565a55` was pushed normally
to `main` after seven manifest tests and GII zero, with English notes. Remote
CI `34346999399` is still being monitored; no final result claimed yet.
Native health before cutover: ready/quiescent true, active turns zero; HTTP,
Telegram, worker and browser retain their previous PIDs and zero restarts.

Cookie login remains **not deployed**, and no chat-retry-ready notification has
been sent. No services have been stopped in this continuation; no real reboot
has been performed. The root-repaired live helper still requires the explicit
conservative reconciliation described below before a normal successor cutover.

## Earlier checkpoint: configuration CI green; chat cookie rollout pending

The Windows job of run `34340747729` exposed a real rapid-repair cache bug:
file metadata could remain indistinguishable and the repaired endpoint stayed
stale. The subsequent correction caches parsing by exact file content, not
timestamps or inode identity. The failing Windows assertion was retained and
a deterministic frozen-metadata regression added. Local public suite:
**2138 passed, 43 skipped**; private configuration/maintenance selection:
**170 passed, 1 skipped**; focused public binding/configuration selection:
**230 passed** with `tests/portable` on PYTHONPATH (needed by existing bare
support imports). Manifest activity **7 passed** and index GII **0 findings**.

Public commit **e3a8e96f9342cd5d81e1077f035c85a48c6f1da5**, English message
`Refresh model settings by content on every platform`, is on public `main`.
Run https://github.com/brunialti/metnos/actions/runs/34342409019 completed with
**all nine jobs successful**, including Windows and Ubuntu full suites.
The already deployed Pages `67524226` documentation remains accurate; no new
HTML changes were needed. Public source-review pin:
`sha256:35d3bc72194b4c8602b5a207651abdebef6b88b1bfec8fcf4a8ec5cc8090ef8d`.

Roberto prioritised retrying his login turn in chat. Cookie code remains tested
but **not installed**, and no retry-ready notification has been sent. Live
services have not been stopped by this continuation. The candidate updater now
has independently tested cores for exact helper/unit replacement, stop-only
successor quiescence, read-only legacy-retirement verification, per-release
bundle binding and the journal 5→6 resume. Composition selection: **202 passed,
15 skipped**; guard with continuously checked additional units: **33 passed**.
New selected-service binding accepts its signed recipe even while the fixed
helper contains the next recipe; current admission still requires today's
recipe. Material/launch selection: **319 passed, 2 skipped, 1 expected pending
source-review-pin failure** while parallel code edits remain in progress.

Outstanding before any live attempt: integrate the historical predecessor
reader (current compiled review must not be imposed on N), previous-context
selection including interrupted head publication, all installer callers,
policy/source-pin synchronization and signed N→N+1 end-to-end crash tests.
The live root-authorised repaired helper differs from the signed old helper;
the product deliberately has no arbitrary-byte exception for it. Resolve this
explicitly, preserving the repair and immutable release. No real reboot yet.

## Configuration verification, 12:28 CEST

The live Models page returned HTTP200 with all three configuration families
configured. HTTP PID309618, Telegram PID310385 and durable worker PID310386
remain active, with no restarts. HTTP has writable service-owned configuration
and state roots; no user settings were edited during verification.

Two reproducible model-configuration defects were found: invalid TOML silently
became factory defaults in the cached resolver, and atomic replacement with a
preserved mtime retained the previous endpoint. The focused tests failed before
the change. The resolver now rejects invalid/unreadable input and keys its cache
by file identity, size and nanosecond timestamps. Valid missing-file defaults
remain; repair is observed on the next resolution without recertification.
The production change is confined to one existing resolver function.

Added real isolated HTTP route tests for both Birth and prompt maintenance:
authenticated LLM/VLM reset of damaged input, preserved backup, save, invalid URL,
stale revision, rejected unauthenticated mutation and execution still unavailable.
Combined configuration/maintenance/startup selection: **168 passed, 1 skipped in
4.94s**. Live configuration was not changed; the fixes are not yet installed.
Public full suite: **2136 passed, 43 skipped in 110.89s**. Manifest activity:
**7 passed in 8.66s**. Full filesystem/index GII zero, no new exemptions.
Published commit `9703989b1549997413344d832e063a919f6a0762`, English message
`Reject invalid model settings and refresh replaced configuration`.
The canonical public inventory was regenerated to 884 paths for the new test
before push. The earlier uncommitted/stale-inventory activity refusals were
pre-publication evidence checks, not failures in production. The published
revision subsequently failed Windows CI; the latest checkpoint above records
the actual failure and its verified follow-up rather than treating this earlier
in-progress result as success.

Public IT/EN guides were deployed with Cloudflare Pages (`67524226`), static
only, and reread byte-identical to the reviewed files:
IT `ad4a9e0d0be7e0e16302cfc6404e83453fb340cb5f1c38320b97b00cfb1b552a`,
EN `876a3e7189df0238ff36e095ce513b32d12e805375777c384891ef61e4761b44`.
Empty WAL and its SHM were preserved in `/tmp/metnos-config-doc-residue.9S1drM`.
No local Tutor update or service restart occurred. Final main-agent topology
and configuration rerun: 72 passed in 0.56s.

Private source-review pin after configuration and the agent's isolated unit
replacement core: `sha256:8b8216b7369d39824892c223cf0200a99988cc5cde118720facb4a52eec8c44e`
(755 sources); boundary guard passed. Public configuration-only pin:
`sha256:a5aae6b9b259b60f8bb3d57bfc39c6e9aadc52ee561419e48a65b269271468a0`
(742 reviewed sources). The unit replacement core is tested, not integrated or
published; preserve that distinction.

Successor analysis is now recorded in the private worktree's
`internal/design/rm0008_successor_update_analysis_9_9_2026.md`.
The initial three installer gaps are not the complete blocker: administrative
bundle identity includes service units and is incorrectly frozen across the
whole release history. The per-release binding, successor quiescence, helper/unit
replacement, legacy preservation and post-head recovery must be tested together
before any production attempt. Cookie chat retry is still not ready.

## Earlier parallel checkpoint, 12:05 CEST

The maintenance increment is now public as
`0281db22897b367b585b9fc12416c728f91e3269`, English message
`Keep authenticated maintenance available when readiness fails`.
Thirteen scoped files, 176 insertions and 9 deletions; no executor payload or
production service was modified by publication. Public portable suite:
**2127 passed, 47 platform/opt-in skips in 111.43s**. Manifest activity:
**7 passed in 9.01s**. Final filesystem/index GII: zero findings. Remote run
https://github.com/brunialti/metnos/actions/runs/34337722455 completed successfully
around 12:06 CEST: all nine jobs passed, including Linux service proof, Windows
and the certification summary.

The first local full run exposed 23 recipe-binding failures after removing
the readiness OnFailure relation. The autonomous verifier's exact recipe pin
was updated to the reviewed new topology; a new rehashed-mutant test rejects
reintroducing the stack-wide failure stop. No old or arbitrary recipe was
admitted. Current public source-review pin:
`sha256:daaaaa4a4bb346d63ab66d7193f4d60c9c3c2cabdcfe9ecffafc7512d1968473`.

Pages deployment `26b96372` completed through the audited public clone,
`deploy.sh --static-only`. IT/EN architecture pages are HTTP200 and byte-identical
to reviewed files (IT SHA256 `107e471c6957e40983543ac0a844aa38bf4cc4be4a6022dc6471b81c1cab51bb`,
EN `dc913ea5695f21e28eca8bc603bc9171c72b2ab168dfc72b9e1d11ad5a1cd81a`).
The local Tutor catalog was not changed. Empty SQLite WAL and its SHM, produced
by static validation, were preserved outside the public tree in
`/tmp/metnos-public-doc-residue.Zsniqq`.

Semantic cookie implementation is complete **in the private candidate only**:
202-line helper, shared bounded precondition before login discovery and credential
forms, Unicode/local model, no language keyword lists. Cache stores only hashes
and typed decisions; four local decisions and two clicks per flow. Credentials
stay in the existing injection component; its redactor is temporarily referenced
and always removed after login. No model call occurs without a plausible panel.
Unknown/unsafe/stale/unresolved panels stop credential filling. Main-DOM only;
iframe/shadow roots and preference navigation remain unsupported, no CAPTCHA work.
Full sites cluster: 408 passed/37 explicit skips. Real local-model synthetic
probes: 6/6 in IT/EN/Chinese/Arabic/Hindi plus an unsafe/injected panel,
0.624–1.263 seconds. Not a real-account proof.

Final combined native maintenance/certification/real-Chromium cookie selection:
**339 passed, 1 skipped in 54.81s**. The restricted run could not bind fixture
sockets; the first native opt-in run lacked the explicit installed browser cache.
The successful command used `METNOS_SITES_SIM=1`,
`PLAYWRIGHT_BROWSERS_PATH=/home/roberto/.cache/ms-playwright`,
`PYTHONDONTWRITEBYTECODE=1`, the development venv, and `-p no:cacheprovider`.
Tests used intercepted synthetic pages and credentials, not live accounts.
Private closed boundary validation passed with 755 sources and pin
`sha256:26c2e8f820e443a5e233871ad9594fed23473afcef1f75037c9876836e8c4c5e`.

Native read-only health still found HTTP PID309618, Telegram PID310385 and
worker PID310386 active with zero restarts; local model status ok. No machine
reboot, cookie deployment or signed release update has occurred. The user asks
to be notified **when the corrected chat login can actually be retried**: do not
send that readiness notification on source tests or public publication alone.
The next bounded task is an authorized successor update, not another initial
cutover, reset or overwrite of Release1. A follow-up analysis is checking which
existing transition primitives can be reused without new machinery.

## Earlier parallel checkpoint, 11:48 CEST

The user explicitly requested two parallel tasks: semantic cookie handling
inside login, and completion of RM-0008 maintenance/certification fixes.
Agent `semantic_login_cookies` owns browser helpers/prompts/tests in the private
worktree; the main agent owns maintenance, certification and publication.
Do not declare either unpublished source or isolated tests deployed.

- The new signed service source no longer connects aggregate readiness failure
  to the stack-wide quarantine stop. All per-service signed startup checks stay.
- The watchdog preserves authenticated HTTP maintenance and refuses automatic
  restart through an unverified service catalog. It records not-ready, not success.
- Combined isolated startup/configuration/maintenance/catalog/security/repair
  selection: **258 passed, 1 skipped in 3.68s**. Two old private tests incorrectly
  equated historical one-shot repair hashes with the evolving development source
  or current host. They now test refusal of a changed predecessor before effects;
  historical repair pins and scripts were not relaxed or re-enabled.
- A new public POSIX maintenance test uses the real reconciliation implementation
  with synthetic external observations, never production configuration/services.
  Public catalog and maintenance selection: **66 passed in 0.48s**.
- Last native read-only check: stack ready/quiescent, active turns 0, browser
  active/pending/approval/factor counts 0, model health ok. No service restart.
- New public increment is being certified from `rm0008-ci-public`; source-review
  pin `sha256:c04d7a7404326821a2dcf41abc3351fc41c5bbf3635e98fa264b2fc3a5b3680e`.
  Do not confuse that with the old live helper or the separately changing private
  source pin. GitHub publication and Pages deployment still need confirmation.

Browser incident before this checkpoint: Chromium crashed with
`chrome_crashpad_handler: --database is required`. Created only the service-owned
0700 leaf `/var/lib/metnos-service/.config/google-chrome-for-testing`, leaving its
protected parent unchanged. Both headed honest/stealth synthetic browsing probes
then passed and their exact sessions were closed. Product `server.py` now derives
private browser XDG leaves for child launches; six environment tests and the
53-test browser selection passed, but this product change is not deployed.

User turn `901ab8ad5fb44a7a` opened the site and timed out in login discovery.
Audit showed no overlay dismiss, credential use or login attempt. No CAPTCHA
was observed. The lexical cookie draft passed six real-browser synthetic cases
but is superseded by the user's semantic requirement: `action_resolver.normalize`
reduces Chinese `拒绝全部` to an empty string. Do not add language lists or claim
that draft works in Chinese. Semantic implementation is in progress separately.

General core update remains the next integration requirement: G6 administrative
publication and dominant topology installation accept only absent or identical
files, not a changed authorized successor; the transition's retirement path also
needs predecessor-aware verification. Do not run another first-cutover/reset or
overwrite the frozen release. A real machine reboot is still unperformed.

## Recovery checkpoint, 09:48 CEST

The service-local preflight is installed in the root-owned administrative helper.
Its previous exact bytes are retained in
`/var/lib/metnos-admin/rm0008-service-startup-20260909/preflight.before.py`.
Installed SHA256: `cbe320dcc21d066ca565a6972fd1af611320a913798d6ce8cd7bce1cf432f099`.
No frozen release file, certificate, ownership journal or historic receipt was
changed. Eight service-local checks passed against production before replacement.
The managed service Python is now actually executed, after the same privilege drop.

HTTP, Telegram (real daemon, not dry-run), durable worker, browser and display are
active. HTTP health reports the durable worker ready and available. The public
chat origin returns the expected login redirect instead of 502. Authenticated
HTTP turn `7baa7483d2024798` executed `get_now` successfully and returned
`Sono le 09:46.` (`final_kind=answer`, executor success, runtime duration 169 ms).
The requested recovery Telegram notification was sent successfully, once.
Subsequent live Tutor turn `c743d44178574b55` answered
`Come funziona LRE in Metnos?` in 13.61 seconds (`final_kind=answer`, no executor
effects). This additionally exercises the local model and retrieval after recovery;
the earlier clock request alone was not evidence of model inference.

Validation: 361 preflight/material/TCB/systemd/launch tests passed, 3 skipped;
8 bounded repair tests passed; the closed boundary guard passed with the updated
reviewed source pin, without new roles or exceptions. The first repair invocation
stopped before writes because its dynamic module registration was missing; that
was corrected and covered with a real verifier-load regression test. The first
notification invocation stopped before sending because the ad-hoc process lacked
the configured workspace environment; the successful invocation used the service's
explicit data/config/state/workspace paths.

At 09:51 CEST the complete read-only readiness probe passed: catalog parity,
browser/display, model/search, LRE and quiescence. Starting the normal enabled
`metnos.target` then completed successfully, including stack-ready and watchdog.
HTTP retained PID 309618 through the subsequent 10:04 health check, Telegram
PID 310385 and durable worker PID 310386. No forced process restart was needed.

At 10:38 CEST HTTP `/agent/health` on port 8770 returned 200, uptime 3197.9
seconds and LRE `ready` with a fresh heartbeat. Browser health on port 8771
returned 200, contract aligned, Chromium connected; local model health on port
8080 returned 200. The public chat origin still returned the login redirect.
The actual Telegram unit is `metnos-telegram-daemon.service`, not
`metnos-telegram.service`. HTTP, Telegram and worker retained their original PIDs
with zero automatic restarts. `/health` is not the Metnos HTTP health route;
probing another port or unit name is not evidence of a production outage.

This is not full RM-0008 closure. Actual machine-reboot verification,
maintenance-only HTTP fallback, registry control convergence and the general
authorized update path remain separate work.
Do not rerun the conservative reset or the exact-predecessor repair on the now
running system. Do not report the worktree's remaining runtime changes as deployed.

## Public publication checkpoint, 10:15 CEST

Public `brunialti/metnos` main includes incremental English commits
`0c8238d20e9a5fb98f2717715cb7c5de90436bdc` (service startup/maintenance) and
`fcb9a7a8ccb8ef6a9bbe2630044ddfc4b0696cd6` (generated test inventory).
Final filesystem/index GII passed without new exemptions. The second commit
adds only the two new test paths to the canonical inventory; the first CI
attempt correctly refused the omitted entries before running six acceptance
activities. The local full manifest activity then passed 7/7 on the corrected
clean commit. No acceptance criterion or production classification was weakened.
That run passed all six acceptance activities and the general Windows suite.
The delegated Linux proof exposed two stale runpy-era expectations: a fresh
Python process sets `LC_CTYPE=C.UTF-8` (PEP 538) and `-m` sets argv[0] to the
module filename. Reproduced with an isolated actual process, not assumed from
the failure. Public commit `2e893bfa46d073ad15ed9362aa1d3a532ef0acaa` corrects
only those test expectations and adds actual-process regression coverage; it
also strengthens the real systemd proof by matching `/proc/PID/exe` to the
managed interpreter's inode. Exact environment equality, UID/GID/groups,
capabilities, open descriptors and certificate checks remain. Product bytes
and both reviewed source pins are unchanged by this test-only follow-up.
That follow-up run failed on a new test assertion: the activation fixture's
`environment` is a distribution verification context, not the managed Python
environment. Accessing its nonexistent `python_executable` attribute was a test
bug. Commit `d95a145fc81d9b1f2afd64708afbfb065df61272` reads the executable from
the selected signed catalog entry instead, with portable coverage explicitly
distinguishing administrative and service Python. The inode assertion is retained.
21 focused launch/fixture tests and the complete 7-cell manifest activity pass;
final-index GII remains zero. No production change accompanied either follow-up.
Final remote run: https://github.com/brunialti/metnos/actions/runs/34329981699
completed successfully at 10:42 CEST on the exact public commit `d95a145`.
All nine jobs passed, including the certification summary. General Linux:
2125 passed, 45 skipped; the separate real delegated Linux proof: 7 passed
in 244.25 seconds, including actual systemd denial/admission and relational
drift rejection. General Windows: 1439 passed, 689 platform skips; the separate
real-identity oracle: 11 passed. All six specific acceptance activities passed.
Skipped platform cells are not represented as executed live proofs. The unrelated
Node 20 deprecation annotations did not fail any job and were not changed.

Local public portable suite: 2117 passed, 47 skipped, with the declared real
dependencies from the existing development venv; four static deployment tests
also passed. A final native private maintenance cluster passed 180 tests, 1 skip.
Earlier local failures under system Python (missing real tomlkit) or the restricted
sandbox (socket/sudo unavailable) were environment failures, not fixed by weakening
tests. The Windows and delegated Linux proofs remain remote CI's responsibility.

Cloudflare Pages deployment `da1c5b60` succeeded through the audited public
checkout's `./deploy.sh --static-only`. Both live IT/EN executor architecture
pages were HTTP 200 and byte-identical to the approved files. This mode validates
all three generated references and the 99-page public inventory, without creating
Tutor signing authority or changing the live Tutor database. Default deployment
still builds Tutor. The script now derives its own checkout root. The raw authoring
docs were not uploaded: GII detected five private-reference findings there.

Public source-review pin: `sha256:898acf9322dd586972e8a434fba346ba042b51b66cbd943491e80d6b7be85038`.
Private source-review pin: `sha256:67d0965362a64e5c3e51e2d81f1afde616cc2e089e742235366a0704adef8aa9`.
These differ deliberately because of the reviewed public projection. Never install
public helper bytes against the private source pin or amend old release receipts.

Local test/deployment residues were moved, not deleted, to
`/tmp/metnos-public-test-residue.X7AqG1` and
`/tmp/metnos-public-doc-residue.zxRoBU`; they must not enter Git/public deployment.
For the next public change: stage only scoped files, run the existing
`generate_production_inventory_v1.py --write`, stage that generated JSON, verify
GII against the final index, commit, and run the manifest activity before push.
Do not use the old authoring-root publisher with force-push defaults.

## Approved direction and scope

Roberto explicitly approved separating certification from ordinary maintenance,
configuration and updates, then requested thorough analysis before code changes.
He reaffirmed the need for proportionate controls, especially at system startup.
This supersedes the approval-pending statements in the earlier incident report.
The task's recorded `blocked` goal status is stale, not a missing authorization.
It must not be converted to `complete` while the incident remains unresolved.

No product code, installed service, certificate or ownership record was changed
during this analysis. The source reviewed is the private worktree
`/opt/metnos/.claude/worktrees/rm0008-reboot`, including its previously pending
boot/CI work. Code paths below are relative to that worktree, not the older live
authoring checkout. The review follows critical startup, control, execution and
maintenance paths; it does not claim to audit every repository module.

## Proportionate controls: the intended balance

| Boundary | Necessary control | What must not happen |
|---|---|---|
| Starting an installed system service | Protected entrypoint, correct account/permissions, essential configuration, supported runtime and required confinement | Re-certifying every executor and the entire host dependency graph before opening administration |
| Invoking an existing admitted executor | Current code/signature/admission binding, revocation, arguments, capabilities and sandbox | Requiring the complete component-producing subsystem merely to read verification material |
| Admitting a new or changed executor | Complete admission and controlled publication before use | Treating an existing name or an administrator session as permission to execute unverified new code |
| Authorized maintenance/update | Authenticated operator, bounded operation, explicit target and recoverable changes | Requiring the old installation certificate to still match the very artifact being legitimately updated |
| Ordinary configuration | Validate the proposed values and affected dependency before applying them; preserve the previous configuration on rejection | Recertifying the whole installation or losing administration because an unrelated preference is invalid |

Routine configuration and compatible OS updates must not require recertifying
all unrelated executors. A fault should disable only operations whose safety
depends on it. This does not mean continuing with a compromised core, bypassing
authentication or ignoring a genuine mandatory dependency. If the web runtime
or identity store itself is unusable, independent authenticated local repair
must remain possible; a web UI cannot survive arbitrary substrate failures.

Configuration is explicitly in scope, not an exception to the maintenance
requirement. Model selection, endpoints and ordinary preferences need ordinary
validation and, where necessary, a controlled restart of the affected service.
Settings that change identity, privileges or admitted capabilities retain their
specific authorization checks. Do not silently replace an invalid explicit
mail account, enablement policy or credential selection with another value.

## Live checkpoint

At **08:25:58 CEST**, 9 September:

- HTTP: `metnos-http.service` inactive/dead; port 8770 does not answer.
- Telegram: `metnos-telegram-daemon.service` failed; now a **system** unit.
- `metnos-stack-ready.service` and `metnos.target`: inactive/dead.
- Local model: port 8080 health returns HTTP 200.
- No orphaned high-CPU process from this analysis was observed.

Public CI success at `41c67748ebd93900541b8e015c1885920f21c94a` is separate
from production recovery. Previous host attempts and detailed evidence remain
in `internal/reports/reboot-failure-20260908.md`.

## Confirmed findings

### 1. Two independent refusals, not one missing lock

The installed prerequisite binds exact OS executable bytes, including
`/usr/bin/python3.12`. The ordinary Ubuntu Python package update from revision
.15 to .16 changed those bytes. Startup rejects `administrative TCB signed
binding`. This is a demonstrated cause.

An independent full-systemd-observation mismatch remained when the previous
Python package was temporarily restored. The 12 signed unit fragments, their
explicit constraints and 25 origin checks pass, but the aggregate observation
differs. Only its old digest was saved, not the complete previous observation.
**The exact second difference is not established.** Do not blame an unrelated
host service or assert the difference is harmless without evidence.

Boot initialization of the missing volatile lock has already been installed.
It does not fix the other refusals. The unsuccessful Python rollback was fully
reversed; package holds or disabling security updates are not a final solution.

### 2. Global certification runs twice per service start

In `runtime/executor_birth_admin_preflight.py`, `_run_operational_command_v1`
(11815) invokes complete attestation for both `check` and `launch`.
`_attest_operational_preflight_v1` (14117) authenticates ownership/release,
binds host binaries, observes all cataloged systemd units and reloads materials.
`_observe_effective_systemd_core_v1` (13739) takes two full observations with
intervening/final rereads. `_bind_administrative_tcb_core_v1` (8629) binds five
exact host executable or library identities.

The installed HTTP and Telegram units call `check` in `ExecStartPre` and
`launch` in `ExecStart`: two full attestations and four systemd observations
per successful start. This is a call-graph count, **not a measured duration**.
Removing the duplicate alone would not repair the excessive failure scope.

`_make_launch_plan_v1` (14239) and `_launch_python_target_v1` (14431) also expose
the dual Python contract: launch uses `runpy.run_module` in the administrative
interpreter while adding managed-service environment paths. The mere presence
of a separate managed Python therefore does not isolate startup from OS Python
changes. Its stdlib/dependency and update policy must be coherent as well.

### 3. Readiness failure also stops the recovery services

The effective readiness unit has `OnFailure=metnos-stack-quarantine.service`.
Quarantine explicitly stops HTTP, Telegram, durable worker, browser, display,
readiness, translator and watchdog units. `Restart=on-failure` does not undo an
explicit service stop. Non-readiness and loss of authority to execute a given
operation need different consequences; a sidecar failure must not remove the
authenticated maintenance interface and its recovery mechanism.

### 4. Maintenance entrypoints are stubs; lifecycle control is partly unmigrated

`runtime/executor_birth_admin_operations.py` is a 49-line adapter whose 16 valid
operation names all return exit 78 before I/O. Its explanation says their
implementation was deferred to Group 7. The replacement routes include `backup`,
`install-metnos`, `install-llm` and `install-service-policy`. This proves those
routes do not implement their operation, not that every unrelated backup tool
is unavailable.

`runtime/services_registry.py::readiness_catalog` (198) projects the signed
post-transition system units. But `control` (831), snapshots, the system-unit
authorization list and LRE feature control use the historical `SERVICES`
registry. `runtime/stack_reconcile.py::restart` (857) still reads/restarts the
**user** target and rejects active **system** HTTP as conflicting legacy state.
Watchdog allowed-target validation also uses the old registry. Correct system
readiness observations did not imply correct system lifecycle operations.

One authoritative target selection must feed observation, control, restart and
the narrow authorization policy. Do not select whichever old/new service is
active or grant unrestricted systemctl access as a shortcut.

### 5. Removing the external check alone would not produce safe administration

`runtime/metnos_http_server.py::run_standalone` (358) initializes the full Birth
runtime before listening; activation failure is fatal once a set is prepared.
Bootstrap failure is sticky within that process. SMTP preferences, prompt
invariants, imports and application construction also precede listening.
`make_app` initializes automatic activities, not just administration routes.
Catching the Birth exception and starting everything would therefore be an
unsafe and incomplete change.

Retain the existing administrator authentication and host-identity boundary in
`http_auth.auth_middleware`. Bring up authenticated maintenance independently of
producer-dependent workers, without exposing secrets or arbitrary commands.
The catalog provider's cached empty result after a failed load also needs an
explicit bounded reload or normal restart following repair, not silent success
or an indefinite retry loop.

Concrete configuration example: `runtime_settings.mail_default_account` (220)
correctly rejects an invalid explicit value or unreadable/invalid TOML. The
problem is its call before the HTTP listener: an unrelated SMTP preference can
remove the very interface needed to repair it. Keep strict validation at the
mail/configuration boundary; do not solve this by choosing another mail account.
For a proposed edit, validate before commit and leave the prior valid settings
unchanged on rejection. For already-corrupt on-disk settings, show the error,
disable the affected operation and retain authenticated repair where safe.

### 6. Retain executor checks; decouple verification from production authority

Useful boundaries already exist: `loader._load_store_into_catalog` (2201),
`contract_store._load_generation` (1553), `_load_revision` (1666),
`current_contract` (1776), and `invocations.load_executor_artifact` (174).
They authenticate immutable contract/artifact material; invocation, capability
and sandbox controls must also remain. Invalid individual contracts must not
fall back to mutable authoring files.

However, `sign.list_trusted_publics` (183) can require
`executor_birth_prepared_root.load_required_context_runtime_v1` (564), loading
sealed author, admission and producer authorities together. Read-only consumers
must not require the entire capability-producing runtime.

The feedback receipt is **not** an existing pre-effect admission gate:
`agent_runtime._execution_receipt_for_dispatch` runs after invocation and its
verification helper currently needs the productive Birth bundle. Decoupling
must preserve admission before effects, report feedback failures honestly and
never rerun an already completed effect merely to obtain a receipt.

An existing executor may continue only if its code, signature, admission,
current authority and revocation state remain verifiable. Existing does not
mean modified, revoked or unverifiable code can run. Full admission remains
mandatory for new and changed candidates.

### 7. Replaying first activation cannot replace a maintenance operation

`install/birth_authority_provisioner.py` (5208–5213) re-attests a completed
transition; it does not renew its obsolete host binding.
`executor_birth_dominant_topology._install_one_v1` (154) rejects differing
existing unit bytes. The administrative tree publisher in
`install/executor_birth_systemd.py` (525) also validates existing contents
against the expected tree, rather than replacing an approved predecessor.

The chain does support release sequences; the finding is not that higher
releases are impossible. Ordinary replacement of already-installed operational
artifacts is incomplete. Patching a frozen source alone invalidates its release
identity. Use an administrator-authorized exact-predecessor update, recoverable
staging and truthful postconditions. Keep old receipts and history; never edit
old hashes to make the host appear unchanged or replay first activation as an
ordinary configuration operation.

## Why the tests did not prove usability

Four existing isolated tests were executed unchanged:

1. `test_group7_administrative_operations_fail_closed_without_mutation`;
2. `test_restart_uses_only_integrated_target`;
3. `test_restart_never_starts_user_target_beside_legacy_http`;
4. `test_failed_bootstrap_is_sticky_and_fail_closed`.

**4 passed in 0.28 seconds.** They confirm blanket administrative refusal,
user-target restart, refusal beside system HTTP and sticky bootstrap failure.
They do not prove recovery. The restart test module normally substitutes the
legacy readiness catalog; separate system-readiness tests do not establish
working post-transition maintenance.

The approved requirement changes these integration expectations. Update them
together with the implementation while retaining negative security tests.
Do not disable tests or equate a listening port with complete recovery.

## Minimal coherent correction sequence

1. Unify lifecycle target resolution from approved deployment metadata and
   align the narrow control policy. Keep administration/recovery alive when
   unrelated readiness fails.
2. Separate installation/admission evidence from service-local startup checks.
   Keep fixed entrypoints, protected paths, service identities and confinement;
   resolve interpreter identity without an `accept-drift` escape switch.
3. Separate authenticated HTTP maintenance and read-only verification material
   from writer activation; expose unavailable capabilities explicitly and
   retain admission before effects.
4. Implement supported maintenance routes and approved product replacement
   using existing transaction primitives. Do not revive retired signing paths
   or introduce a second installer.
5. Prove isolated scenarios, then perform a controlled live update, actual user
   turn and reboot validation. Publish verified increments after GII with
   English Git notes and deployed IT/EN public documentation.

Reuse the existing registry, authentication, supervisor and contract store.
No new general certification framework, alternate dashboard, parallel service
manager or dynamic cleanup engine is needed. Measure startup/restart time and
subprocess counts. Neither source line count alone nor a passing denial test
is a measure of usability.

## Acceptance evidence required before closure

| Scenario | Observable requirement |
|---|---|
| Cold boot with empty volatile state | Lock initialization and configured services recover without manual preparation. |
| Compatible OS security update | No global dependency on the previous OS binary digest; runtime compatibility and confinement remain checked. |
| Unrelated host unit or optional dependency change | Affected readiness is reported without blanket shutdown. |
| Post-transition restart, service control and LRE toggle | One correct system target; no user/system duplication or broad privilege grant. |
| Producer activation fails | Authenticated maintenance works; new admission/publication is explicitly denied. |
| Existing admitted component is intact | Can operate independently of producer availability while its current verification context is valid. |
| Code tampering, bad signature, revocation or missing admission | Denied before effects; unrelated verified components remain independent. |
| Configuration, backup and authorized bug-fix/update | Supported operations succeed without placeholder refusal or immutable-history rewriting. |
| Invalid proposed preference or existing invalid SMTP setting | Reject the proposal without changing valid settings; isolate existing corruption to the affected operation and keep authenticated repair, without substituting another account. |
| Interrupted approved artifact replacement | Exact predecessor/successor remains identifiable; repeatable conservative recovery. |
| Verification repaired | Explicit bounded reload/restart, not a sticky empty catalog. |
| Fresh install and installed-instance upgrade | Both exercise a real successful user turn and service recovery. |

A real reboot check remains outstanding. Prior first-start certificates,
simulations and public CI do not substitute for deployed availability and
successful maintenance. RM-0008 must remain operationally unresolved until
these positive scenarios and the retained security checks pass.

## Implementation checkpoint and requested notification

Worktree: `/opt/metnos/.claude/worktrees/rm0008-reboot`. The first bounded
changes now unify service observation/control/restart, resolve the default SMTP
account at the mail invocation boundary, and keep authenticated HTTP maintenance
available after Birth or prompt bootstrap failure. Maintenance mode does not
start producer-dependent jobs and denies executor invocation and publication.
It reports non-operational health; a listening maintenance endpoint is not a
successful recovery. Current tests include HTTP requests over real temporary
loopback sockets with unchanged authentication and isolated application data.

At 09:04 CEST the broader selected regression completed: **183 passed, 1
skipped**. The earlier configuration-specific selection completed **190 passed,
1 skipped** before the HTTP maintenance slice. These are different selections,
not counts to add. Nothing from this implementation has been deployed. Native
service checks at 09:04 CEST still show HTTP inactive and Telegram failed.
The global administrative startup gate and independent read-only executor
verification remain required work; do not call this incident fixed.

Roberto explicitly requested an update here and **one Telegram message when
Metnos becomes usable again**. That notification is pending, not sent and not
scheduled through a Codex automation: no Telegram/automation tool is available
in this task. Use the installation's existing `channels.telegram.TelegramChannel`
with the configured host recipient and protected credential store after verified
recovery. Never expose bot tokens or change the Telegram polling offset for a
notification. Before sending, verify HTTP, the actual system Telegram daemon,
and one successful harmless real user request. Record the API acknowledgement;
do not claim delivery on an exception or announce recovery for maintenance-only
HTTP. Preserve this pending request across handovers.
