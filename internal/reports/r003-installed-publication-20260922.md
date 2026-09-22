# R-003 installed publication bridge — 22 September 2026

## Outcome

The approved bridge and completed-retirement correction are implemented.
The corrected release **73 is selected and operational**, with HTTP, Telegram
and LRE healthy. The controller preserved the superseded inactive candidate
through its supported withdrawal path; no checkout ownership workaround was used.
**R-003 remains open.** Roberto selected `organize_files` for the real publication
test, then explicitly requested a pause while it is in development. No new
executor generation was published, and no F5 certification is claimed.
The earlier refusal and repairs below remain historical evidence, not current
production status.

## Implementation and evidence

- Product branch: `codex/r003-installed-publication`, worktree
  `/opt/metnos/.claude/worktrees/f5-final-integration-20260922`.
- `727cdfcd`: named authoring input through the installed reconciler; source
  data cannot change runtime imports, configuration or authority. Topology is
  checked before admission. The administrative wrapper refuses linked
  worktrees and dirty primary checkouts, and plans before admitting.
- `114c4bbc`: derived source bindings and inventory for the reviewed candidate.
- `8ebac9c9`: administrative Git reads cannot refresh or take ownership of
  the developer's index.
- `17ca94dc`: the public build uses its required creation permissions and
  restores the caller's private umask, including on failure.
- Principal-checkout controller integration: `c901ff8a`, `e0f163ac`,
  `673f23ef`. No retired legacy entrypoint was restored.

Focused checks: **391 passed** for the initial bridge, snapshot boundary and
intent adapters; the subsequently extended controller suite has **228 passed**.
These counts overlap and must not be added. The exported public projection
passed **7/7** manifest checks after its normal Git-index inventory generation.
Private source review and the closed Birth boundary guard also passed.
JUnit receipts are under `/tmp/metnos-r003-20260922.5xj9vo/`.

Two diagnostic runs were not green: the public certification check from the
private tree rejected its different runtime test support, and an unindexed
raw export lacked the required public Git inventory. Neither check was
disabled or its frozen expectations relaxed. The final public test used the
publisher's ordinary inventory generator in an isolated local Git copy; this
is not a new GitHub CI receipt.

Private reviewed sources: 790,
`sha256:07cef6b0341d2d51ff11c1e5e7c984ff9afddadaf01ddae1cda2d2fcfd4e2d0c`.
Public projected sources: 778,
`sha256:52d98c8c7201c7f07efd34400426ea0d2a4618124900498afaa13d4a197cf34f`.

## Operational observations and repairs

1. `run-v7csfqjc`: the Python environment inventory refused pre-existing
   compiled caches, before a release build or service stop. `run-tqeb2nm_`
   proved all 31 cache files equal to compilation of their original sources.
   `run-lw4aqc32` moved their 198,712 bytes to the recoverable archive
   `/var/lib/metnos-admin/agent-runs/run-lw4aqc32/derived-bytecode` and removed
   only nine empty cache directories. No dependency source or user data was
   removed. Integrity rules were unchanged.
2. The root diagnostic's Git status refreshed `.git/index` as root with mode
   0600. `run-xo0e4wdm` restored only its owner/mode to `roberto:roberto`,
   0664, verifying identical content. Both the diagnostic and permanent
   controller now use `--no-optional-locks`.
3. `run-2h06y45e`: the administrative launcher's 0077 umask made the newly
   created, empty release staging directory 0700 instead of required 0755.
   The controller correction is tested; `run-7il3rbml` restored the intended
   mode only on that exact empty directory. No existing release was edited.
4. `run-258xe2cw`: release 73 was successfully built with closed build ID
   `sha256:f14abb3f83aee6df2e4c76b9613e9ecd82a76747774c737370c2faf1e2ff91f6`.
   Its audit stopped before activation in
   `birth_authority_provisioner._transition_roots_v2`: the signed predecessor
   names `/opt/metnos`, whose required owner is root:root; its observed owner
   is roberto:roberto, mode 0755. The system and legacy user unit roots meet
   their respective owner requirements. No ownership workaround was applied.

Final authenticated observation `run-_4ohnyok`: release 72, head
`sha256:d3a1f856bb495fe4469f199916f3500361e50db1c22566a9ed9a4c059611741f`,
HTTP/Telegram/LRE active with unchanged PIDs, health and quiescence true,
zero active HTTP turns and zero active LRE attempts.

## Resume and actual closure

The proposed ownership change to `/opt/metnos` is **withdrawn** after reviewing
its consequences for the shared development checkout. No ownership change was
performed. Roberto requested a durable solution instead.

Read-only observation `run-ty_2t_gz` confirms that the authenticated selected
release already binds an exactly reproducible retirement receipt. The proposed
fix separates this historical proof from current service and authority checks;
it requires no new key or duplicate evidence archive. Architecture, evidence
and acceptance criteria are in
`internal/design/rm0008_retirement_checkpoint_20260922.md`.
Roberto approved the retirement-contract change and its implementation.
The superseded release 73 must be preserved through the controller's supported
withdrawal procedure, never patched or activated through a permission workaround.

## Approved retirement implementation

Product commits `3ae08aca` and `581fd197` implement and document the general
policy. One verifier authenticates the selected completed predecessor and
recomputes its existing dominant-startup receipt. Unchanged historical repository
steps no longer require the old checkout to remain present or root-owned.
Current service restrictions and conflicting processes are still observed;
new repository obligations retain exact evidence and protected-root checks.
Initial retirement and ordinary executor Birth requirements are unchanged.
No new key, service, marker, journal, authority bypass or permission change
was introduced. The selected head is reread to reject concurrent changes.

The principal controller uses the same verifier (`4454cf4a`, integrated through
`6dbf4937`). The root `AGENTS.md` notice and R-003 coordination are tracked in
`6dbf4937`; agents must not publish concurrently or change checkout ownership.

Final disjoint focused suites: **1,235 passed**, two environment skips:

- Retirement, initial/successor transition, dominant startup and preflight
  materials: 310 passed, with ownership cases in an isolated root namespace.
- Release controller and stack reconciler: 366 passed as the ordinary user.
- Authority coordinator and administrative preflight: 429 passed; two existing
  POSIX ACL xattr cases skipped because the test filesystem returns EINVAL.
- Context selection, bootstrap, snapshot and runtime context: 130 passed.

The new checkpoint tests cover forged or changed bindings, incomplete and
abandoned predecessors, missing old checkouts, concurrent selection changes,
and conflicting processes. Historical filesystem steps are not reread; current
unit obligations are. New-delta failures remain covered by transition tests.
JUnit receipts: `/tmp/metnos-retirement-tests-20260922.T94ren/`.
An earlier diagnostic mixed root-only filesystem fixtures with a controller
fixture requiring a non-root service GID; the final suites use their intended
identities. No validation or test expectation was weakened for that mismatch.

The new public projection passed **7/7** canonical manifest cells after the
standard Git-index inventory generation (938 Python paths). Isolated projection:
`/tmp/metnos-retirement-public-20260922.Kj54fS`. This is a local proof, not a
GitHub CI receipt. Closed Birth boundary and private source review also passed.

Reviewed private source root (790):
`sha256:cbc21912648c2cb42dba1530410cfa424c2faf3b2a5cfb84e1d249d11eacd244`.
Reviewed public source root (778):
`sha256:d05f13f09e5934349160c8e911c11b6e5965b22fe2828c529afa9c30ac9a5744`.
Prepared source: 1,810 files, census
`e882cb7fe86bda6b603492006be347ff8d32ef94b211f5f8363ca9813ec8fd50`.

The bilingual public architecture documentation was deployed and read back
from metnos.com (deployment `dda464e2.mykleos.pages.dev`). It explicitly labels
the correction as not yet deployed. Static publication did not rebuild or
change the live signed Tutor catalog.

Immediately before the authorized application, read-only receipt
`run-boztm3me` confirmed release 72, unchanged service PIDs, healthy operational
HTTP, quiescence and zero active HTTP turns or LRE attempts. Production
activation and the real named-executor publication remain separate acceptance
steps; the local tests above alone do not close R-003 or F5.

## Successful activation and pending executor proof

Activation receipt `run-86r39239`: return code 0, empty stderr, signed release
73 build `sha256:05ae607b6a57e942dc0ff19fa1d8e35cb70949f642cd7a88b5232fb5f07f0389`,
transaction state `PREFLIGHT_VERIFIED`. Both fresh audits passed before crossing.
The subsequent plan and reconciliation found 107 unchanged contracts and
admitted no changed executor. Read-only receipt `run-oahfpfxd` confirmed selected
head `sha256:0f4cf24c853b75b1d5e1b6583a852fb1dafe110393b45895bcce897e832895d4`,
healthy operational services, quiescence and zero active HTTP/LRE work.
The final public documentation deployment is `9e4d2263.mykleos.pages.dev`.

Preliminary probe `run-zxcsmpeo` refused before publication because the general
loader opens a catalog lock for writing, incompatible with this deliberately
read-only probe. Probe `run-iu6ohej3` instead used the existing authenticated
read-only inventory/current-contract APIs and reread every revision and the
selected head. It observed 123 contracts before and after, all unchanged.
The named publication plan refused `read_files` with `candidate_file_extra`;
the development candidate contains an old `__pycache__` directory. Nothing was
admitted or restarted. No product protection was relaxed. This is not a
successful publication receipt. Candidate hygiene must be checked before
the later `organize_files` attempt, without changing its developer's files.

Roberto's replacement candidate and pause supersede the provisional proof
below. Main commit `0e5d866b` integrated that temporary candidate; `323ec299`
reverted only those unshipped edits, preserving the original commit and branch.
No `organize_files` code was edited or tested. Resume only when Roberto marks
it ready and committed, from a clean primary checkout, with fresh quiescence,
before/after generations, exact receipt, unrelated-contract preservation,
and the agreed harmless real execution. Do not repeat unchanged local suites.
Tutor catalog rebuilding, the final real-turn proof and public Git publication
are not claimed by this report. The prepared release-turn diagnostic was not
run after the user's pause.

### Superseded provisional candidate (preserved for traceability)

The single-executor proof candidate is `55708ae1` on
`codex/r001-rm0008-cleanup-20260922`: it corrects inaccurate scalar-only
documentation in `read_files`, translates comments to English, and updates
the derived code digest. Executable AST is identical to the selected source.
Its temporary principal-checkout integration was reverted as recorded above;
it was never published.
Do not describe this documentation change as a new functional F5 admission.

Do not resume this provisional candidate. Use `organize_files` only after the
developer's readiness handoff. A repeated no-op plan alone does not satisfy
R-003. Public push and F5/F6 closure remain separate.
