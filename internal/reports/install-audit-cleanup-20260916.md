# Public installation audit: interruption and cleanup

Date: 16 September 2026. Status: **cleanup verified; installation acceptance incomplete**.

The one-shot public GitHub installation test started at 01:00 Europe/Rome.
It ran in the disposable Ubuntu machine `metnos-audit-20260916-LoHdw4SC`,
with its own `metnos-audit` account and Python environment. Public clone
`45f1065f65a300c9626e0bf552daf1ed9fe0e386` was the last observed clean checkout
(`run-dsnaihqu`). Operator-authority setup passed in `run-ctew40ih`; actual
Italian consent/bootstrap phase 1 passed in `run-5cy0pg9q`.

At 02:22:49 the controlling Codex process exited with status 1 after
`Selected model is at capacity`. This is not a completed installation,
component acceptance, or successful end-to-end chat test. The earlier summary
and report under the original audit directory stopped at the initial
checkpoint and are superseded by this interruption/cleanup record.

## Authorized cleanup and evidence

Roberto explicitly requested final removal of the test environment and
continuation of RM-0008 F5/F6. The production stack was already restored.
The pre-removal inspection (`run-37vwlaqc`) checked the root-owned inventory,
exact directory and guest hostname, stopped container, absent host test user,
absence of nested mounts, and all originally active production units.
The test directory occupied **2,602,156,032 bytes**.

Removal (`run-lzezmrmq`) affected only:

- `/var/lib/machines/metnos-audit-20260916-LoHdw4SC`, including the guest
  account, checkout, venv, native packages, caches and test-only authorities;
- the two user scheduling links for `metnos-install-audit-20260916`;
- the five exact system container/recovery/watchdog/deadline unit files and
  their timer enablement links;
- the two matching host recovery/watchdog helpers and the audit control-state
  directory, moved into the private cleanup evidence directory.

The disposable guest was permanently deleted to recover space; reproducing
it requires a fresh installation. Retired control files remain recoverable in
`/var/lib/metnos-admin/agent-runs/run-lzezmrmq`. Package inventory, selected
guest installation/apt logs, phase evidence and evidence hashes were saved
there before removal. Existing reviewed-run evidence and the original
28,837,586-byte agent log remain preserved. No production data, photographs,
indices, shared models, releases or unrelated worktree changes were removed.

The first final check compared the entire health response and stopped on a
changing response field, after removal had completed. No deletion was retried.
A separate read-only verification initially lacked `rg` in the restricted
administrative PATH; the next run used the installed system text tool.

Final verification **`run-4qz5_sa4` passed**, exit 0:

- exact test root and active control-state path absent;
- all five system units and both user units `not-found`;
- host recovery helpers absent, no test mounts or host test account;
- preserved log/archive hashes reread successfully;
- all recorded production units active and HTTP health `ok=true`;
- HTTP, durable-worker and Telegram process IDs and activation timestamps
  identical to their pre-cleanup values.

Filesystem free space increased from 1,091,997,290,496 to
1,094,597,169,152 bytes: approximately **2.60 GB recovered**. This observed
filesystem delta may include concurrent ordinary activity. The final host
unit-file/mount census found no remaining audit entries. The active production
worker was not mistaken for an orphan test process.

Reproducible one-off cleanup source:
`internal/tools/install_audit_cleanup_20260916.sh`, final SHA-256
`8354b7b8598bcb6a19694994f0ca5d1cc4e5880688e9e40016f30b6c51d1a106`.
Original audit documents and log:
`/opt/metnos/.claude/worktrees/rm0008-reboot/internal/coordination/install-audit-20260916`.

No installation retry is scheduled. Full public-install acceptance remains
open and is separate from F5/F6. This cleanup did not deploy or certify F5/F6.
