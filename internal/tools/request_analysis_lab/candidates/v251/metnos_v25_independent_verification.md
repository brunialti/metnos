# Independent V24.1 / V25 verification

Offline only; no model/server call and no raw query/model output persisted.

| Suite | Baseline | V24.1 | Valid | Improvements | Regressions | Recomputed=saved |
|---|---:|---:|---:|---:|---:|---|
| v23lite | 105/109 | 107/109 | 109/109 | 2 | 0 | yes |
| v23_full | 100/109 | 102/109 | 107/109 | 2 | 0 | yes |
| v21_adversarial | 44/50 | 44/50 | 45/50 | 0 | 0 | yes |

V25 targeted independently recomputes route 16/16, schema 16/16, strict 7/16, active question claims 0, active argument claims 0, retries 0.

V25.1 A/B freeze integrity and 6/6 self-tests pass, but mutation audit proves two escape paths: explicit places can avoid `question_slot`, and held files can avoid `support_argument`. It is diagnostic, not a by-construction acceptance candidate.

The spreadsheet overlay is shadow-evaluation only; source/query/catalog hashes match and original gold remains unchanged. It is not human-approved production authority.
