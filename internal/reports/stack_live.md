# Metnos live stack gate

- Profile: `live-legacy-host-with-integrated-health`
- Result: `green`
- Generated: `2026-07-18T21:19:57Z`
- Live cutover performed: `false`
- Pilot contract: `20b4f14bfee1516ae98feee3cad57e4157c8203490162e7521e17910f5117ac2`
- Host fingerprint: `33631b19daeb02e1c6cd0a78b4002c703f3aa221b622c43aae7539541ede3b23`
- Playwright fingerprint: `metnos.playwright/1:33be7f1a2bc9fa063e6531aeee396bf6959f53b69f6d7851812b89b52c8ff99c`

| Cycle | Readiness | Natural turn | Successful steps | Rollback | Result |
|---:|:---:|---|---:|:---:|:---:|
| 1 | yes | `c2801cabbd0041e4` | 1 | yes | green |
| 2 | yes | `1f50d0d6e4d148e3` | 1 | yes | green |

The pilot completed in 36531 ms and restored the system HTTP baseline after
both cycles. Post-pilot composite health is ready and quiescent: system HTTP is
active; user HTTP and `metnos.target` are inactive; catalog count is 112; active
turns and all broker queues are zero; HTTP and Playwright share the fingerprint
above.

The isolated gate was green twice at 112/112, and the host-integrated gate was
green twice at 132/132. Fresh companion checks are Sites with real Chromium
5/5 and index/recovery 139/139. Global executor signatures are 82/83; the only
invalid signature is the pre-existing external `find_places` worktree change,
which was not modified or signed.

This report proves the composite health contract against the current mixed
legacy host and the two-cycle migration pilot with rollback. It deliberately
does not claim that the system-level HTTP service has been migrated to the user
target: no live cutover was performed.
