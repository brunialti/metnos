# R-006 — acceptance anchor regression attribution

Date: 22 September 2026. Author: Codex, original RM-0008/F5 handover task
(`e8c34028`). Scope: diagnosis and coordination only; no product changes,
reference updates, publication, authority installation or service operations.

## Outcome

The reported `current acceptance anchor differs` refusal is caused by a
merge regression, not by the private-launcher test relocation in `3cddd734`
or its public export `92eab91ec16112f818d4d246b61acbf0f7678632`.

Merge `0f922c5c` retained the updated acceptance test but restored the old
`RM0008_ACCEPTANCE_EVOLUTION_SHA256` from its LRE parent. The first parent
already contained the matching reference. A second Codex session took R-006
concurrently; these findings were passed to that session in the shared
`/opt/metnos/internal/coordination/richieste-aperte.md` instead of starting
a competing correction.

## Exact evidence

File: `tests/portable/test_rm0008_acceptance_evolution.py`.
Reference: `runtime/contract_boundary_guard.py::RM0008_ACCEPTANCE_EVOLUTION_SHA256`.
Consumer: `tests/portable/rm0008_2a_acceptance/certification_v1.py`
`::_validate_current_exact_acceptance_blobs`.

| Revision | Test SHA-256 | Reference |
|---|---|---|
| `bfb5b5af` (2 September) | `1babce04a78b8345cbacb9bf5677bebade3958e655f0dc45884ad70636322167` | Matches |
| `830e36ca` (16 September) | `67eb6e545ec299e11431a544732edfdb3522304c064ea078b0cd14ddeeaef5c4` | Matches |
| `5421af8c`, first parent of `0f922c5c` | `67eb6e545ec299e11431a544732edfdb3522304c064ea078b0cd14ddeeaef5c4` | Matches |
| `0f922c5c` | `67eb6e545ec299e11431a544732edfdb3522304c064ea078b0cd14ddeeaef5c4` | Old `1babce04…` restored from second parent `00b86d95` |
| `1942462b`, current F5 checkpoint | `67eb6e545ec299e11431a544732edfdb3522304c064ea078b0cd14ddeeaef5c4` | Still old `1babce04…` |
| Public `92eab91ec16112f818d4d246b61acbf0f7678632` | `67eb6e545ec299e11431a544732edfdb3522304c064ea078b0cd14ddeeaef5c4` | Still old `1babce04…` |

The net test change since `bfb5b5af` is the addition of the four variants of
`test_portable_import_prerequisite_requires_the_exact_reviewed_edge` in
`830e36ca`; the existing negative cases remain. An intermediate diagnostic
test added by `e489605a` was removed by `2aad5bf0`, which restored the test
and reference to the matching `67eb6e54…` pair. The later merge lost that
reference again without reverting the test.

Ordinary path history hid the merge-side change. The discriminating checks
were:

```sh
git log -m --full-history -G RM0008_ACCEPTANCE_EVOLUTION_SHA256 -- runtime/contract_boundary_guard.py
git diff 0f922c5c^1 0f922c5c -- runtime/contract_boundary_guard.py
git show 0f922c5c^1:tests/portable/test_rm0008_acceptance_evolution.py | sha256sum
git show 0f922c5c:tests/portable/test_rm0008_acceptance_evolution.py | sha256sum
```

`internal/tools/rm0008_repin_source_roots.py` handles the public/private
whole-source roots, not this separate acceptance reference. Running it alone
does not repair the mismatch; do not broaden automatic reference updates to
hide changes to the acceptance guard.

## Verification performed

On the clean F5 worktree at `1942462b`:

```sh
timeout 45 /opt/metnos/.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/portable/test_rm0008_acceptance_evolution.py
```

Result: **13 passed, 1 failed in 0.11 s**. The failure is
`test_current_acceptance_anchor_content_is_independently_bound`, raising the
same exact message as the historical public manifest jobs. No test or product
source was modified for this reproduction.

Existing public receipts, independently reread:

- Run `35505233907`, predecessor `f3ac40dc…`: refusal at
  `2026-09-20T10:30:10.6306577Z` in `ci-baseline-manifest.log`.
- Run `35508891679`, `92eab91e…`: refusal at
  `2026-09-20T11:49:42.3652898Z` in `ci-manifest.log`.
- Both logs remain under `/tmp/metnos-f5-public-20260920.Sc47tL/`.

A fresh GitHub query on 22 September found a later completed failed run,
`35521704038`, source `aaddc3911808fb1187812ffe42e75c675a4634c0`; its manifest
job `106106793243` is failed. The CLI returned no log text for that job, so
this report **does not attribute its failure message** or other jobs to the
same cause. The older receipts and local reproduction establish the diagnosed
regression without treating the newer status as a detailed failure receipt.

## Handoff and closure boundary

The receiving R-006 owner can review restoration of the exact first-parent
reference, retain all negative assertions, verify the affected family, then
perform the source-review/export steps and obtain a new public manifest result.
This report does not apply that restoration or claim a green certification.
R-006 stays open until its recorded closure criteria are met.

R-003 was not executed: the principal checkout is still dirty and no release
window was established in this task. R-005 remains a source handoff: fix
`3cddd734` is not an ancestor of principal checkout `52a9c7ae`, and that
principal commit does not track `internal/tools/install_f5_authority.sh`.
This is not evidence about any separately installed launcher; none was
inspected or changed here. R-001 changes were neither adopted nor cleaned.
R-002's overlapping loader/synth changes were read; no merge was attempted.
