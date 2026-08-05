# Metnos stack lifecycle gate

- Profile: `isolated-systemd-contract`
- Result: `green`
- Generated: `2026-07-18T21:19:51.522288Z`
- Live cutover performed: `false`

| Cycle | Result | Passed | Duration ms |
|---:|---|---:|---:|
| 1 | green | 112 | 915 |
| 2 | green | 112 | 864 |

The identical host-integrated companion gate was also green twice at 132/132.
Each host cycle emitted only the 180 previously known `aiohttp.NotAppKey`
warnings.

The isolated profile validates unit semantics, composite readiness,
fresh/upgrade behavior, two-cycle pilot evidence, rollback and guarded
cutover failure recovery. It does not claim that the live legacy HTTP
service has already been migrated.
