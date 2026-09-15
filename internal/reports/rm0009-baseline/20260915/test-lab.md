# RM-0009 test laboratory

## Scope

`internal.tools.rm0009_test_lab` is a stdlib-only fixture helper for isolated
RM-0009 tests. It has no runtime imports and creates no data from production.
Each `temporary_lab()` call creates one private `0700` temporary root with
synthetic `config`, `data`, `state`, `cache`, `workspace`, and two fictional
user namespaces. The returned environment contains only the explicit
`METNOS_USER_*`, `METNOS_WORKSPACE`, and relevant XDG path overrides; it does
not change the process environment or supply `HOME` or `USERPROFILE`.

The closed `LabStore` enumeration provides separate synthetic `GOVERNANCE` and
`TARGET` SQLite archives. Connections enable WAL and foreign keys, and always
roll back uncommitted work before closing. Product schemas remain the test's
responsibility.

## Evidence — initial version

The requested isolated command was run with empty source configuration and
data paths:

```text
METNOS_USER_DATA=/tmp/metnos-rm0009-checks-qJn9f3/data \
METNOS_USER_CONFIG=/tmp/metnos-rm0009-checks-qJn9f3/config \
/opt/metnos/.venv/bin/python -m pytest -q tests/internal/test_rm0009_test_lab.py
```

Initial red result: collection failed as expected because
`internal.tools.rm0009_test_lab` did not yet exist (1 collection error).

Initial implementation result: 6 collected, 6 passed, 0 skipped, 0 failed.

The tests cover two separate fictional users; a closed, secret-free override
environment; separate databases; visibility only after explicit commit;
rollback at close; WAL; foreign-key enforcement; unknown-store rejection; and
cleanup of only the private temporary root while a real external fixture file
remains unchanged.

## Coordinator follow-up

The laboratory now has **14 tests**. The additional cases cover simultaneous
laboratories, rollback of an explicit transaction on exception, connection
closure, DDL outside versus inside BEGIN, cleanup on exception, stale use,
and accidental database symlinks/hardlinks or replacement of the data path.
The five new path/lifetime guard cases were first observed failing, then
passed after the guards were added. The combined preparatory suite has 62
passing tests; see `preparation-review-followup.md` for independent checks and
source digests, and `verification.json` for the coordinator's final run.

Connections deliberately do not introduce an implicit BEGIN. Migration tests
must begin the transaction they intend to roll back; DDL outside a transaction
can persist without an explicit commit. The static path checks only catch
fixture mistakes: they do not prevent races or hostile code in the same
process. Create laboratories through `temporary_lab()` and keep all
connections inside its lifetime. Do not use this module for candidate code.

## Boundary

This isolates **test archives only**. It is **not** a sandbox for candidate
code and is **not** a certification of the runtime, FS-A, Birth, network
isolation, or any RM-0009 release prerequisite. No runtime path, service,
signature, real archive, key, or process environment was modified.
