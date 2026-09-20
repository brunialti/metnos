# Telegram CPU temperature and RM0008 handover — 20 September 2026

Release **72** is installed and operational. Source commit: `461bdd34`.
Public commit: `f3ac40dc99ecb4b1d128c775eb422560b0b80ea9`.

## Telegram correction

The HTTP turn `f3bd841d20b84dd2` and Telegram turn `4b80f5fa65e04435`
selected the same signed `get_processes` executor and CPU temperature arguments.
Telegram failed before executor start because its unit declared
`ProtectKernelTunables=yes`. Bubblewrap could not write the namespaced
`/proc/sys/user/max_user_namespaces` limit required by `--disable-userns`.
The planner then proposed an admin shell command that the shell guard correctly
rejected. That second error obscured the infrastructure failure.

The signed Telegram recipe now omits the incompatible directive, consistently
with HTTP and the durable worker. Executor sandboxing, nested namespace
lockdown, NoNewPrivileges, and the other unit restrictions remain in place.
The recipe identity and the explicit legacy-directive disposition were updated.

Two transient services reproduced failure with the old recipe and success with
the corrected recipe. Nested user namespace creation remained blocked and the
host namespace limit was unchanged (`run-iys69b4e`). A ReadWritePaths exception
for the individual sysctl file did not resolve the inherited proc descriptor.

Final source validation: **603 passed, 1 skipped**, covering the service catalog,
administrative material readers, lifecycle cutover, `rm0008_2b`, and actual
executor sandbox tests. This is a targeted suite, not a remeasurement of all
4,945 tests mentioned in the incoming RM0008 message.

Live verification `run-26qpcw1e`: the signed executor ran as the service account
inside Telegram's actual mount namespace, with no extra capabilities and with
bytecode writes disabled. Result `ok=true`, CPU temperature available (79 °C
at the sample). No Telegram message was sent. The same probe reported
`birth_owner=legacy` and no F5 certificate.

## Release outcome and remaining operational defects

The initial normal `apply --cross` (`run-tn66pdp5`) authenticated and audited
candidate 72, then failed while stopping the predecessor. Telegram exceeded
its 10-second stop deadline; the durable worker had exited cleanly but its
llama-server child exceeded the unit's 45-second stop deadline. The release
effect command also has a 45-second timeout. It raised `systemctl execution`
before observing the final stopped state, leaving the old services stopped.

The previous signed services were restored (`run-mcp6cigt`); API health was
ready and quiescent. A normal retry (`run-x_nqondo`) then refused the pending
attempt's provisioning journal because `retire_orphan_journals` recognizes
coordinator transactions and withdrawn requests, but not this matching pending
claim created before a coordinator transaction was recorded.

Automatic review initially rejected direct completion with an unresolved
journal. Read-only canonical verification (`run-6pdk479k`) then established:

- Exactly one pending claim, for candidate 72 and the prepared source.
- The provisioning header, request, build, distribution hashes and signature match.
- Release 71 remained the authenticated selected predecessor named by the claim.

After this evidence, review admitted resuming the same signed transaction.
`run-8v1r1fgo` completed through the canonical idempotent completion path,
including fresh signature, predecessor, quiescence and native-runner checks.
Result: `PREFLIGHT_VERIFIED`; no journal was removed or rewritten manually.

The diagnostic initially imported configuration without the service paths and
created only empty `workspace/.scheduler` and `workspace/.mnestoma` directories
inside release 71. Their creation times matched the diagnostic runs. Inspection
proved that there were no files or databases; only those empty directories were
removed with `rmdir` after owner/time/emptiness checks. The corrected diagnostic
uses explicit service paths. Release integrity was then attested successfully.

Follow-up for RM0008:

1. Handle slow/failed systemd stops with bounded final-state observation and
   recovery of the authenticated predecessor services when no crossing occurred.
   The command deadline must not race the worker's own stop deadline.
2. Teach the normal release retry to identify and validate its matching pending
   claim and pre-coordinator journal, preserving unknown-journal refusal and
   all authenticated identity checks. Cover interruption at this exact point.

These two release-helper defects are not corrected by the Telegram patch.

## Answers to the incoming RM0008 message

The dormant F5 code had already shipped on 17 September and is retained in
release 72. `e581e6c4`, `5fa19a79`, and `55c62222` are ancestors of production
source `3a3526a0`, which is the parent of this fix. The old
`codex/lre-general-parallel-f5` worktree lacks the latest installed LRE path and
progress fixes and also contains unrelated uncommitted ORGANIZE work. It must
not replace the current production source wholesale.

No LRE job was active at either crossing. The two recent jobs remain completed,
each with 1,986 committed units, zero failures and the same revision identities.
The older completed-with-errors history also remains unchanged. An active
worker service did not indicate active work.

This task performed prepare, release completion and public publication. F5
migration, authority provisioning, certification and activation were not run.
Before calling the automatic release workflow robust, RM0008 should reproduce
and fix the two operational defects above on the updated combined source.

Final identities:

- Closed build: `sha256:471d72274ca7c98db4ae39e1126f15860921ce401b619b24a8ac2588c33235a5`.
- Cutover: `sha256:3f28fbbe8c691d555dd2f0621986a959756cd9fc62a5be93c3634cc3224526cd`.
- Request: `sha256:3e673bc59dcfc844b3b852b017afdb80d8618a92345a4700107a133cec49b4f4`.

The installed catalog matches the tested source. The installed administrative
preflight matches the prepared public export byte-for-byte; its differences
from private authoring are the expected public source pin and privacy redaction.
The extra predecessor release remains available; this resumed completion did
not run the optional release-retention pass.
