# RM-0008 transition closure handover — 2 September 2026

## Purpose

Complete RM-0008 with one production transition, verify the previously failed
turn `81f1ce66878646a4` (`avvia dropbox`), update public documentation and Tutor,
publish only to the public `main` branch, and remove every other public branch.

Do not announce that the turn can be retried until production activation and
the exact live turn both succeed. The required announcement is exactly:

`ORA PUOI RITESTARE IL TURNO`

## Exact workspace state

- Worktree: `/tmp/metnos-rm0008-f4-transizione`
- Local branch: `codex/rm0008-f4-transizione` (local staging only; never publish it)
- Base commit: `9a6587f096b3bb42dcbb83da50f2f55caa33c63b`
- Modified implementation/evidence files before this handover: 19
- Binary diff SHA-256 immediately before adding this handover:
  `422cef309104b625edef0e392062434a65f9a2306733d0c1f97a9ada88c3d730`
- Production `/opt/metnos` was still at `14ea7179` at the last observation.
- The previous activation helper `/tmp/metnos-rm0008-activate.sh` is obsolete
  and must not be reused. It failed before changing production with
  `birth_provisioning_acl_unsafe`.

The working tree is intentionally uncommitted at this checkpoint. Preserve the
19 changes and this handover; do not reset or reconstruct them from memory.

## Verified result at handover

The focused transition and static-boundary batch is green:

```text
112 passed, 1 skipped in 24.01s
```

The release's full static-boundary assembly test is independently green:

```text
1 passed in 23.02s
```

`git diff --check` is green. Earlier in this phase the deterministic broad
suite completed with `1818 passed, 32 skipped, 8 subtests passed`; do not rerun
the complete suite merely to repeat that evidence. Run only checks affected by
subsequent edits, then one final closure gate if the roadmap explicitly
requires it.

## Architecture fixed by this candidate

The production topology has three existing infrastructure services:

- `llama-server.service`
- `searxng.service`
- `photon.service`

They are system services with assets outside the Metnos release. The prior
candidate incorrectly attempted to replace them with `metnos-llm.service`,
`metnos-searxng.service`, and `metnos-photon.service`, and required a nonexistent
tracked `runtime/bin/llama-server`. The current candidate represents the three
real services as external dependencies while retaining the three old user-unit
names solely in the retirement inventory. This avoids duplicated ownership and
keeps `metnos.target` dependent on the real services.

The deterministic topology census is now:

```text
dominant unit names:       12
legacy bindings:           39
cross-scope homonyms:      13
same-destination overlaps: 1
RESULT: exact overlap confirmed
```

Other completed corrections:

- Birth authorities are prepared as the dedicated target account, not as root.
- The target account and the legacy account are explicit, distinct inputs.
- Initial predecessor evidence is published under the deployment lock before
  candidate preparation.
- Legacy units that are genuinely absent are accepted as `not-found` and are
  still sealed by the retirement step.
- Process observation is restricted to relevant entry points instead of
  rejecting unrelated processes that merely use the repository tree.
- The release descriptor records the resolved Python executable rather than a
  virtual-environment symlink.
- The isolated administrative launch admits only validated root-owned system
  package paths.
- The boundary inventory explicitly classifies the new predecessor publisher
  as a coordinator store owner.

## Files changed

Productive implementation:

- `install/birth_authority_provisioner.py`
- `install/executor_birth_distribution_release.py`
- `install/executor_birth_transition.py`
- `runtime/contract_boundary_guard.py`
- `runtime/contract_cutover_guard.py`
- `runtime/executor_birth_admin_preflight.py`
- `runtime/executor_birth_service_catalog.py`
- `runtime/stack_reconcile.py`

Evidence, probes, pins, and tests:

- `internal/reports/rm0007-m4-boundary-inventory.json`
- `internal/tools/prova_b3_server_post_transizione.py`
- `internal/tools/sonda_sovrapposizione_unita_rm0008.py`
- `scripts/publish-public.sh`
- the seven modified `tests/portable/test_executor_birth_*.py` and
  `tests/portable/test_contract_cutover_guard_session.py`

The reviewed-source pins were converged with
`internal/tools/rm0008_repin_source_roots.py`:

```text
private 702 sha256:94c72a7d56f9988ab198d772b30a45219befe54412ca9fc95594dcbf08a68005
public  690 sha256:1bb1c6d87105c5d464f879467ef6ac10f6c940501054a67a14d1acb9dae0df24
```

## Remaining closure sequence

1. Review the current diff for internal consistency. Do not redesign the
   topology unless a failing check proves a concrete defect.
2. Run the directly affected focused tests and the overlap probe. They must
   remain green with zero failures.
3. Commit the candidate locally. Record the exact commit and require a clean
   tree before activation.
4. Build one new root activation helper. It must:
   - require the exact clean candidate commit;
   - verify the three external services are loaded and active;
   - create a dedicated no-login `metnos` system account with home
     `/var/lib/metnos-service` if absent;
   - copy the four legacy XDG trees from `/home/roberto` to the corresponding
     target-account locations, once before stopping the old stack and once
     after it is stopped;
   - create `/var/lib/metnos/executor-birth` as root-owned mode `0755`;
   - stop only the exact legacy user and system units;
   - make the fixed predecessor source roots root-owned and not writable by
     group or others, without changing `.git`, virtual environments, models,
     or unrelated paths;
   - run `install/executor_birth_transition.py deploy` with
     `--service-user metnos`, `--legacy-service-user roberto`, and
     `--legacy-installation-root /opt/metnos`;
   - restore the old exact units only if failure occurs before the productive
     transition starts. Once the transition journal exists, resume that same
     transaction instead of attempting to return to the legacy path.
5. Have the operator execute that helper once with `sudo`; monitor its
   transient system unit until completion.
6. Verify production health, the ownership chain, `metnos.target`, public HTTP,
   Tutor, and the exact live query `avvia dropbox`.
7. Update the RM-0008 roadmap with final evidence and keep roadmap entries in
   reverse date order. Update all affected public documentation and regenerate
   Tutor.
8. Run the strong GII publication gate, including personal-data and secret
   checks. Public code comments and documentation must be English.
9. Publish the tested result directly to public `main`. Do not push a feature
   branch. After `main` is verified green, delete every other remote branch.

## Non-negotiable stop conditions

Stop without changing production if any of these is not proven:

- candidate commit is exact and the tree is clean;
- the three external infrastructure services are active;
- there are no active Metnos turns or sessions at cutover;
- source and target accounts are distinct;
- predecessor roots pass ownership and mode checks;
- the strong public-data gate passes.

Do not weaken a guard, remove a registry entry, or change an expected digest
solely to make a test green. Every updated expectation in this candidate is
bound to the production topology described above.
