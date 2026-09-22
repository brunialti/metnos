# R-003 installed publication bridge — 22 September 2026

## Outcome

The approved bridge is implemented and tested. Release **73** was built and
signed, but its pre-activation audit refused the current ownership of
`/opt/metnos`. **Release 72 remains selected and operational. R-003 is open.**
No executor was admitted, no service was restarted, and no F5 certification
or activation was claimed in this task.

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

Roberto was asked to approve changing **only `/opt/metnos` itself** to
root:root, preserving 0755 and leaving every descendant unchanged. This
would restrict creation/removal of direct root entries to administrators;
do not apply it without his answer, and do not weaken the retirement check.

After approval and fresh idle checks, resume the prepared release through
the normal controller from the clean principal checkout. Preserve the
signed candidate and evidence; do not rebuild unrelated history.

The single-executor proof candidate is `55708ae1` on
`codex/r001-rm0008-cleanup-20260922`: it corrects inaccurate scalar-only
documentation in `read_files`, translates comments to English, and updates
the derived code digest. Executable AST is identical to the selected source.
It is committed but not merged into the primary checkout or published.
Do not describe this documentation change as a new functional F5 admission.

Merge the candidate into the primary checkout while clean, then use
`internal/tools/rm0008_release_cycle.py publish --executor read_files`.
Closure requires the real receipt, before/after generation IDs, authenticated
catalog reread, unchanged unrelated contracts, service health, and execution
through the admitted catalog on disposable test files. A repeated no-op plan
alone does not satisfy R-003. Public push and F5/F6 closure remain separate.
