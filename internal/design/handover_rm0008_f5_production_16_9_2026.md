# RM-0008 F5 — implementation and production handover

Snapshot: 16 September 2026. Prepared at Roberto's request for an external agent.

**F5 is not closed or activated in production. This is not a deploy-only task.**
The latest quarantine implementation is committed and locally verified, but
productive integration, migration, evidence-derived certification and final
release verification remain. F6 has not started; RM-0009 is out of scope.

This document supersedes the operational status and next-step instructions in
the 15 September handover. It does not replace project invariants or subsequent
approved decisions. No external agent, watchdog or background deployment has
been started by this handoff.

## 1. Start here

- Worktree: `/opt/metnos/.claude/worktrees/rm0009-development`.
- Branch: `codex/rm0009-development` (the RM-0009 name is historical).
- Latest product change: `84414376`, exact-execution quarantine and recovery.
- Latest pre-handover checkpoint: `161b5d77`, verification and docs deployment.
- Previous implementation: `27d31561`, activation reader and queued-attempt
  guard; `20ea9b00`, isolated HTTP driver repair.
- The worktree was clean at `161b5d77`. This handover is committed afterwards;
  obtain its commit from Git history, not from a guessed hash.
- Interpreter for local checks: `/opt/metnos/.venv/bin/python`.

Use this worktree with exclusive ownership, or transfer its complete committed
branch. **Public GitHub alone does not contain the latest F5 increment.** Do not
copy production keys, service databases or unrelated local files into a transfer.
Recheck HEAD and uncommitted changes before taking ownership; preserve others'
work. Do not replace the live installation with this branch wholesale.

Read `CLAUDE.md` and `CLAUDE.mutabile.md` completely, then
`/opt/metnos/internal/AGENTS.md` and `internal/roadmap/README.md`. Relevant
references within the worktree:

1. [Approved decisions](decision_rm0008_g8_scope_15_9_2026.md), especially the
   16 September amendments on focused tests, ordering and quarantine authority.
2. [Work plan](rm0008_g8_work_plan_15_9_2026.md), current header and §§6.8–6.11;
   consult earlier evidence only when the remaining task needs it.
3. `internal/roadmap/RM-0008-porta-unica-nascita-executor.md`, particularly the
   F5 requirements and authority rules; subsequent approved amendments apply.
4. `internal/reports/rm0008-regole-di-lavoro-fra-gruppi.md` and
   `decisions/0224-single-deterministic-executor-birth-gate.md`.
5. Before installer work, read `install/INSTALL_NOTES.md` completely.

The [older handover](handover_rm0008_f5_f6_15_9_2026.md) remains useful for the
original inventory, not current completion status. Its caller-selected
activation reader, unresolved dedicated-key decision and strict
certification-before-isolated-integration order are superseded. Do not redo them.

## 2. Decisions already approved — do not reopen them unnecessarily

- KISS, modular and general-purpose. English code/comments; all user-visible
  text through i18n. No additional service or permission prompt just for F5.
- Order: **isolated integration → focused verification → certification →
  production**. Complete F5 first; F6 follows separately.
- Two complete consecutive focused cycles covering Birth, exact generations,
  caches, feedback and recovery replace the old 48-case mail/calendar profile.
  Freeze the focused profile before running it; retain failures/interruption.
- Still required: at least five genuine eligible technical admissions, at least
  two authenticated producers, independently evidenced closure of relevant
  defects, and the required real Linux/Windows checks. Fixture admissions and
  quarantine-only revisions do not satisfy the admission threshold.
- The existing administrative installation owner owns the evidence and defect
  ledger. A dedicated administrative key signs certification only: it neither
  publishes executors nor authorizes their actions.
- Historical signed receipts plus exact persistent rereads are acceptable
  where original temporary journals are unavailable; declare those gaps.
  This is not permission to infer missing associations or invent evidence.
- Initial qualification is reusable and incremental. Ordinary Birth/startup
  must not recertify the whole history or retain all 53 old code distributions.
  Preserve required signed evidence and recovery references.
- Existing promoter maintenance capability `promoter:quarantine`, through the
  single Birth owner, may quarantine the exact `ExecutionReceipt` generation.
  Promotion/rollback authority is unchanged; quarantine grants no reactivation.
- Production release was authorized after successful completion. This is not
  permission to bypass the exit checks or overwrite concurrent production work.

## 3. Implemented and verified, but not productive F5

| Boundary | Existing implementation and limit |
|---|---|
| Certification custody/evidence | `install/birth_certification_authority_provisioner.py`, `install/birth_certification_evidence.py`, `runtime/executor_birth_certification_authority.py`: dedicated-key provisioning and administrative evidence persistence. Complete qualification issuance/composition remains missing. |
| Fixed activation reader | `runtime/executor_birth_lifecycle.py::load_f5_activation()` takes no caller-selected authority/path. Authenticates the fixed installation, current head/build and public certification authority; checks protected files and rereads the frontier. No certificate was installed by this work. |
| Queued attempts | `runtime/agent_runtime.py` and `runtime/durable_workloads/execution.py` recheck exact generation after readiness and immediately before transport, including scheduler wait. Productive `RuntimeFactory` composition remains missing. |
| Quarantine publication | `runtime/executor_birth_quarantine.py`, `executor_birth_operational.py`, `executor_birth_commit_publisher.py`: authenticated lifecycle-only successor, exact receipt binding, independent reread, predecessor CAS and recoverable replay. Ordinary Birth rejects the quarantine key. |
| Feedback composition | `runtime/executor_birth_lifecycle.py::apply_execution_failure`: publication/reread → epoch/cache replacement → receipt-keyed review outbox. Private real-store fixtures exercise it; live turn feedback does not call it yet. |
| Epoch retry | `runtime/executor_birth_epoch_store.py`: exact contract/generation lookup and idempotent successor reconciliation; retries preserve counters/history and do not affect other executors. This is not an installed migration. |
| HTTP test driver | `tests/e2e/driver/server.py`: isolated defaults, operational readiness and owned-process cleanup. Actual focused F5 cycles have not run. |

Quarantine authenticates the unchanged original admission instead of rerunning
suspect code or a model review. Code, language resources and other manifest
fields cannot change on this route. Its signed checks explicitly record reuse.
An existing F4 prepared set lacking only the optional quarantine key still boots.

Recovery tests include loss of temporary staging, interruption after publication,
two-hour replay, outbox failure, stale feedback and a newer signed successor.
Expired fresh claims remain denied; recovery authenticates the existing claim.

## 4. Remaining work — recommended completion sequence

### A. Freeze the remaining integration and migration boundary, then connect it

Do not redesign the already approved authority. Identify exact remaining owners
and runtime call sites before patching. Start from these observed gaps:

- `runtime/turn_feedback.py::apply_feedback` still uses legacy name-based state.
  Consume the runtime-retained `StepLog.execution_receipt`, including asynchronous
  outcomes, and connect `apply_execution_failure`; reject executor-supplied fake
  receipts. Current code has no live caller of this new entry.
- `runtime/executor_birth_failure_review.py` has review logic, but the existing
  outbox (`enqueue_failure_review_inactive`) lacks productive consumption and
  recovery wiring. Reuse an existing job owner, not a new daemon.
- Complete `LifecycleCoordinator` productive publication composition for the
  remaining transitions and derive preexercise facts from authenticated state.
  An injected callback in a test is not the productive publisher.
- Wire `runtime/executor_birth_durable_guard.py::DurableBirthAttemptGuard` into
  `runtime/durable_workloads/runtime_bindings.py::RuntimeFactory`.
- Verify all actual L0/L1/autopath/preexercise selection and cache consumers use
  exact generation/lifecycle. Preserve existing correct identity code rather
  than replacing it. Connect counters, receipt deduplication, positive feedback
  and aging to the epoch owner; retire remaining name-based writers/readers.

Define the durable activation/migration mode before selecting legacy versus F5
paths. **After migration, a missing/revoked certificate must not silently select
legacy state again.** Before migration, an absent optional F5 certificate must
not prevent normal F4 startup. Refuse only the affected unsafe operation, not
unrelated executors or the whole service. The fixed reader alone does not solve
this state transition. Do not use the old flag-only `quarantine_for_feedback`
helper as a substitute for a signed revision.

### B. Finish and exercise the one-time lossless migration

Reuse the epoch-v3 owner and existing preservation/reconciliation primitives.
Complete the real writer census, all-writer barrier, exact historical bindings,
reconciliation/recovery record and durable retirement of old readers/writers.
Handle unresolved legacy restrictions explicitly; do not associate a generation
from its name or revive previously restricted capabilities accidentally.

Configured successor state is under `PATH_USER_STATE/birth/`:
`executor_epochs.sqlite` and `failure_reviews.sqlite`. The productive failure
entry refuses a missing migrated epoch DB; it does not initialize an empty one.

Previous service-state census, 15 September, UID 995/GID 985 (revalidate before
cutover, not on every Birth):

- `/var/lib/metnos-service/.local/state/metnos/executor_stats.db`: 198 stats,
  468 history rows; 58 deprecated and 44 archived, overlapping sets.
- `/var/lib/metnos-service/.local/share/metnos/promoter.sqlite`: 8 rows,
  6 archived, 2 review-needed, no open grace recorded in that snapshot.

Preserve complete rows, not just summary counts. Check direct SQL paths in
change-apply/rollback against environment-selected aging paths. Do not delete
other administrative-home stores merely because they appear old. No migration
or legacy deletion has been performed by the latest increment.

### C. Prove the integrated paths and derive the certification

Use the repaired isolated HTTP driver, prepared isolated authorities and the
existing independent postcondition oracle. Run the two complete focused cycles
on the same candidate, exercising real selection, feedback, cache replacement,
queued generation changes and interruption/recovery; keep unrelated executors
usable. Include required native Windows/Linux boundary coverage.

Complete the administrative issuer from persisted observations and the known-
defect ledger. Derive eligibility and associations from authenticated history;
do not sign caller-provided counts. Reuse `runtime/executor_birth_history.py`
and the historical readers. Previous observation `run-aiforucs` found 6,504 acts
(6,480 reattestations and 24 technical candidates), two issuer categories and
53 contexts: **these are observations, not a passed qualification**. Residual
unjoined/ambiguous evidence is recorded in the work plan and needs explicit
disposition. Do not rescan all history for each normal operation.

Bind the final qualification, completed migration, installation, current head
and closed build. Complete invalidation/revocation and defect-reopening behavior.
The existing activation reader expects
`/var/lib/metnos/executor-birth/certification-v1/active.json` (schema 1,
purpose `f5_activation_v1`, policy `rm0008-f5/1`). Do not create this file by
hand or use test signatures to bypass the unfinished issuer.

### D. Produce one coherent release, preserve current production, then activate

Current concurrent LRE worktree:
`/opt/metnos/.claude/worktrees/lre-backend-release`, observed HEAD `e69d47d8`.
The latest documentation baseline used deployed source
`318a5f1558bf2ffbac4a5df6715cb14be59a8da1`. These are coordination snapshots,
not an assertion that production will still have the same selected head.
Re-read the installed signed release and current concurrent work before merging.

The shared `/opt/metnos` checkout has unrelated work; `rm0008-reboot` is not this
task's write area. Integrate the necessary changes deliberately. Do not revert
LRE by releasing the entire older F5 tree over it.

Complete source review and refresh the source seal once at the finished
increment boundary. At `84414376`, R1's sole recorded finding is
`birth_closed_source_review_mismatch`: the source pin is deliberately stale,
not an error to suppress. The updated API projection digest
`sha256:d427f30537434a8d8bc49b689345ff0025fd636750ba6b3f45ab0639a26ba16c`
is **not** a whole-source approval. Run the exact-source public Linux/Windows
matrix after the coherent increment, not after every small edit.

Use the documented release/installer owner and the existing
`internal/tools/rm0008_release_cycle.py` prepare/apply workflow after reading
its current preconditions. Do not invoke a private publisher or manipulate
keys/activation as a shortcut. Review migration recovery and release withdrawal
before cutover; restoring an old name-based writer is not a safe F5 rollback.
Do not restart services while a user turn is active.

Finish Tutor rebuild/query verification, bilingual docs, production smoke
tests and observation. Record exact release/commit/certificate/migration and
test evidence; commit incrementally and publish only the approved public export.
F5 closes when the **installed real paths** meet the approved requirements,
not when a unit suite or documentation deployment is green. F6 follows after
that closure; this handover authorizes no retention deletion as a shortcut.

## 5. Verification already done — avoid unchanged repetitions

All counts below are overlapping families, not additive unique coverage.
Results were recorded on the latest product increment; no product tests were
rerun for this documentation-only handover.

| Family | Recorded result |
|---|---|
| Quarantine, lifecycle, epochs, operational core | 110 passed, 17.61 s |
| Producer table, authority provisioning, producer store, contract/history readers | 165 passed, 19.25 s; unchanged by later epoch edits |
| Quarantine and installer documentation | 37 passed, 11.44 s |
| Boundary policy and standalone projection | 104 passed, 3.34 s |
| Earlier queued-attempt bridge/scheduler/durable/guard family | 81 passed, 5.09 s at `27d31561` |
| Earlier activation/lifecycle family | 36 passed, 0.33 s; isolated signed release, not installed certification |

Core family files, under `tests/runtime/executors/`:
`test_executor_birth_quarantine.py`, `test_executor_birth_lifecycle.py`,
`test_executor_birth_epoch_store.py`, `test_executor_birth_operational.py`.
Run affected files with `/opt/metnos/.venv/bin/python -m pytest -q` from the
worktree; the structural check is
`/opt/metnos/.venv/bin/python runtime/contract_boundary_guard.py --birth-closed`.
Do not rerun them merely to reconstruct this handover; rerun when their boundary
changes, or when verifying the final integrated candidate.

The real-store quarantine fixtures use ephemeral authorities and an isolated
property oracle. They exercise actual signing/stores/SQLite/recovery but are
not live E2E or qualifying technical admissions. The last all-nine-jobs public
green was `9eac7814`, run `35102515345`, **before** the new reader/guard/quarantine
sources. It does not certify `84414376` or the forthcoming integrated candidate.

## 6. Documentation and cleanup status

The two architecture pages and ADR 0224 describe the new quarantine boundary
and explicitly leave F5 activation unfinished. Documentation preview:
https://9079e6c7.mykleos.pages.dev (also deployed on `metnos.com`). Only the two
F5 pages changed; 119 served assets were preserved from the concurrent LRE
deployment. Old/new preview interface pages were byte-identical; the custom
domain adds the Cloudflare analytics script and its inserted newline.

The 8.5 MB temporary documentation stage was removed. No test process owned by
this increment remained; the live HTTP and vision-model processes were left
running. Tutor, production runtime, keys and installed databases were not
changed by this increment. The handover itself changes documentation only.

## 7. First deliverable from the receiving agent

Confirm branch ownership and report a short remaining-path checklist against
sections 4A–4D. Select the first incomplete productive boundary, document its
cause/contract, then implement and verify it. Do not restart completed F4
adoption, request the approvals already recorded above, or rebuild the whole
historical audit merely to get oriented. If a genuinely new authority or
material scope change is needed, stop at that decision and ask Roberto.
