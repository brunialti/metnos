# Metnos stack lifecycle gate

- Profile: `isolated-systemd-contract`
- Result: `green`
- Generated: `2026-08-24T11:39:42.730360Z`
- Live ownership observed by this isolated gate: `false`

| Cycle | Result | Passed | Duration ms |
|---:|---|---:|---:|
| 1 | green | 148 | 1315 |
| 2 | green | 148 | 1265 |

The isolated profile validates unit semantics, composite readiness,
fresh/upgrade behavior, two-cycle pilot evidence, rollback and guarded
cutover failure recovery. It neither inspects nor changes live service
ownership; that evidence belongs to `stack_live.*`.
