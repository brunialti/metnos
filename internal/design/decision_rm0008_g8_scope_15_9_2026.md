# RM-0008 — approved G8 principles and F5/F6 assignment

Date: 15 September 2026.
Status: **principles approved; technical contract preparation assigned**.
Owner: Codex, including F6 after completion and verification of F5.
Inspected source checkpoint: `06a9472e` on `codex/rm0009-development`.
Current work and evidence: [G8 work plan](rm0008_g8_work_plan_15_9_2026.md).

## 1. Decision and its exact scope

Roberto answered **APPROVO** to the proposal to define the group 8 technical
contract using these principles:

1. Separate, installation-bound certification derived from real evidence:
   at least five successful technical admissions, at least two producers,
   two complete consecutive cycles and no open defects in the relevant scope.
2. Lossless migration, without assigning historical data to new generations
   on the basis of an executor name.
3. Retirement of legacy mechanisms only after effective restrictions and
   pending promotions have received a safe disposition.

Roberto subsequently instructed **F6 LO FARAI TU DOPO**. Codex therefore owns
the subsequent F6 work as well; it is not a separate, unassigned handover.
The subsequent **ok. procedi** starts work under this assignment. The work
plan distinguishes the existing-boundary prerequisite repair from new G8
authority/schema contracts that still need to be completed and reviewed.

This records approval of the stated principles and the assignment, not
approval of every technical detail in the previous draft or handover. It
does not assert that an authority has been installed, a migration has run,
or F5/F6 have passed their exit criteria. New authority scopes or normative
exceptions remain subject to RM-0008 section 17; they must not be inferred
from the assignment of F6.

### Historical evidence clarification — approved on 15 September 2026

After an explanation of both alternatives, Roberto explicitly answered
**PRIMA OPZIONE**: certify historical acts using authenticated receipts,
exact bindings and independently reread persistent state. The temporary
authoring journals and source snapshots are not generally retained after
publication; their absence must be stated, not hidden or reconstructed as
if they had been preserved.

This approval resolves the evidence-sufficiency question raised by the
independent internal review. It does not waive signatures, exact identity,
complete inventory, deduplication, terminal success, historical authority,
cycles or defect closure. It does not assert that the installed candidates
already meet the threshold. An absent temporary artifact is distinct from a
missing or inconsistent durable receipt: the latter cannot qualify.

The certifier must distinguish a verified signed journal/source binding from
original journal/source bytes actually reread. Available originals, if used,
must match their authenticated binding. No new admissions, signing keys or
artificial edits may be manufactured to populate the historical threshold.

### Incremental certification and bounded retention — instructed on 16 September 2026

Roberto explicitly rejected recurring examination of the entire history and
indefinite retention of all release trees. Security certification remains
required for Birth; its ordinary cost must follow the new act and affected
dependencies, not the lifetime number of releases or admissions.

- Reuse an authenticated certified baseline. Validate a new Birth against
  the current policy, authorities and relevant predecessor; do not replay
  every earlier Birth or recursively reopen earlier certification dossiers.
- Historical reconciliation for the initial F5 entry/migration is a separate,
  one-time workflow. Reuse unchanged evidence while preparing it. Once its
  result is certified, ordinary activation and releases verify that result
  and applicable changes, not the original full inventory again.
- Keep current integrity, identity, revocation and compatibility checks.
  Reopen the affected evidence when those checks invalidate its reuse; a
  full historical audit is an explicit investigation/recovery operation,
  not an automatic prerequisite for every request, restart or publication.
- Separate compact proof records from executable release trees. Retain the
  active tree, a small explicit recovery set and any tree still used by an
  in-flight operation. Older trees must be removable after their necessary
  evidence is preserved and live references are resolved. Do not mistake
  removing old code for permission to discard unresolved migration data.
- Reuse existing owners and certification mechanisms. This requirement does
  not call for another service, signing authority or general audit framework.

This clarification itself changed the acceptance criteria, not installed
behavior. The subsequent instruction **applica** authorizes implementing
them. Work-plan section 5.16 records the bounded runtime reader and release
code retention implementation, its tests and the actual cleanup. The
standalone administrative verifier is now also bounded and passed a read-only
installed proof (§5.17). The release coordinator's own journal inventory is
still a separate follow-up. The runtime candidate is not deployed and F5
certification remains open.
Do not confuse its signed required-state baseline with an unverified cache
or a completed F5 entry certificate.

### Proportionate checks — clarified on 16 September 2026

Roberto explicitly requested a leaner checking procedure. Apply the existing
group rules §8 directly: targeted tests while editing, one real traversal per
affected boundary, and one final Linux/Windows matrix for the completed
increment. Keep a compact record of source scope, command, outcome and reason
to rerun; reuse an outcome while its relevant inputs and environment are
unchanged. A documentation-only edit does not invalidate runtime tests.
Repair a failed check and rerun its affected family, not every historical
suite. Source seals, signatures, current-state integrity and release
postconditions remain mandatory; no new test scheduler or result cache is
needed. This is evidence reuse, not permission to waive a failed check.

## 2. Order and stopping condition

The sequence remains:

1. G8: evidence-based F5 entry certification and reconciled legacy migration.
2. G9: complete and verify the productive F5 lifecycle integrations.
3. G10: design, implement and verify F6 retention with its real data owners.
4. G11: complete the final closure checks and evidence dossier.

F4 is already verified and must not be repeated. G8 entry certification is
not completion of F5. The detailed F6 contract is developed at G10 entry,
using the verified F5 result, rather than frozen prematurely now.

After F5/F6 completion, stop for the requested external review. RM-0009
does not start automatically. The previously superseded G8 proposal remains
historical and unapproved as a whole.

## 3. G8 contract preparation

The following is the work list for closing the technical contract, not a
claim that the listed interfaces or installed prerequisites already exist.
It refines the approved principles without lowering the existing threshold.

| Handover decision | Contract detail to close before the corresponding code |
|---|---|
| N1 — certification authority | Exact signer scope, provisioning owner, public reader, protected custody and revocation/rotation path; no private Birth keys exposed to the certifier. |
| N2 — evidence dossier | Closed versioned fields, bounded canonical encoding, signing domains, complete stable frontier and installation/F4/source/build binding; historical proof separate from current-release activation. |
| N3 — technical admission | Executable predicate across admission kind, revision class, lifecycle, signed terminal outcome and publication operation; authenticated act time and exact receipt/request/generation deduplication. |
| N4 — consecutive cycles | A complete versioned routing profile fixed before execution, its evidence producer and ordered records including failures and interruptions. |
| N5 — no open defects | Initial census of known findings, authenticated scope/frontier, closure evidence and independent review, including subsequent reopenings. |
| N6 — migration | Installed source/owner/writer census, successor to epoch schema 2, exact historical associations, all-writer barrier and durable reconciliation/recovery record. |
| N7 — restrictions | Explicit treatment of effective legacy restrictions before retiring the reader; no silent increase in visibility and no invented historical identity. |
| N8 — pending promotions | Safe drain/disposition of open grace cases before retirement; no conversion of an already active generation to preexercise. |

For each item, reuse the owning component in handover section 6.2. Do not
introduce a generic framework, an additional publication authority, or
caller-selected production roots to avoid completing an owner interface.
Installed paths are resolved from authoritative configuration, not from
the paths of this development checkout.

Each implementation slice must identify the new risk, the affected owner,
one discriminating test family and the actual productive traversal. Reuse
earlier evidence where its boundary is unchanged; do not repeat the 244
baseline tests merely to increase the reported count. Linux/Windows and
installed evidence remain explicit exit gates, not assumed successes.

## 4. Source observations confirmed during preparation

These observations add concrete acceptance conditions; they are not evidence
that the installed threshold or migration has been satisfied.

- `runtime/executor_birth_operational.py::_terminal_envelope` signs the
  report, publication, request and embedded admission receipt. A signed
  intermediate hint can have no publication, so a signed envelope alone is
  insufficient evidence of a successful admission.
- The successful operational path binds `AdmissionReceipt.issued_at` to the
  core's act time and embeds the receipt in the terminal result. Historical
  verification must authenticate this complete binding; an unsigned DB
  finalization timestamp cannot supply the historical validity time.
- `runtime/executor_birth_producer_store.py::verify_terminal_registration_v2`
  currently requires a sealed request and uses the store-opening path.
  Certification needs a genuinely read-only historical owner interface, not
  a fabricated request or a verifier that migrates the DB while reading it.
- `runtime/executor_birth_epoch_store.py::_verify_legacy_migration` currently
  requires every copied row to remain `unresolved`. Retrying after a valid
  later association therefore needs an explicit schema/reconciliation
  contract that separates preservation of the original row from its
  resolution history. Do not relax source-byte verification to make retry pass.

This decision record itself changed no product code, installed data,
authorities or running services. Subsequent source changes, tests and
read-only installed observations are recorded in the work plan; none are
retroactively claimed as evidence for the original approval.

## References

- [Detailed handover](handover_rm0008_f5_f6_15_9_2026.md), sections 4–6.
- [Group working rules](../reports/rm0008-regole-di-lavoro-fra-gruppi.md).
- [RM-0008 normative roadmap](../roadmap/RM-0008-porta-unica-nascita-executor.md).
