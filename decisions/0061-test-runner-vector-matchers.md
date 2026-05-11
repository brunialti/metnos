---
id: 0061
title: Test runner matchers — field_eq, entries_field_eq, metadata fallback chain for vector schema
date: 2026-05-01
status: accepted
area: testing
related:
  - 0044  # vector executor schema (entries vs results)
complements:
  - 0044
---

## Context

The manifest test runner (`runtime/test_runner.py`) supports a fixed set of
expectation matchers (`ok`, `error_contains`, `content_contains`,
`metadata_field_eq`, etc.). On 2026-05-01, after a regression sweep
following the i18n migration (235 entries to/from sqlite DB), 12 of 147
manifest tests were red across 4 executors. Diagnosis showed the failures
were not caused by the i18n changes — they were structural mismatches
between test expectations and the post-vector-refactor (28/4) executor
output schema:

* **`compute_entries` (6 tests)** and **`sort_entries` (1 test)** used
  matchers `field_eq` and `entries_field_eq` that did not exist in the
  runner. Reported as `matcher sconosciuto`.
* **`write_files` (3 tests)** expected `metadata_field_eq:
  {bytes_written, mode, encoding}` but the post-2.6 vector schema places
  per-entry fields in `results[0].*` instead of a `metadata` object.

Adding the matchers is a runner-only change: it does not modify any test
case or executor output, so it complies with §8.2 ("test fail → fix code,
not the test").

## Decision

Three runner-level additions (`runtime/test_runner.py`):

1. **`field_eq: {key: value, ...}`** — top-level field equality on
   `actual.<key>`. Use case: scalar-output executors like
   `compute_entries` (`{ok, value, op, key, count_input}`) where the
   test wants to assert `value == 9`.

2. **`entries_field_eq: {"<idx>": {field: value, ...}, ...}`** — per-entry
   field check on `actual.entries[idx].<field>`. Index is a string
   (TOML lacks integer keys). Use case: `sort_entries` asserting
   `entries[0].name == "b"` after sort by size.

3. **`metadata_field_eq` fallback chain** — keep the existing matcher
   semantics, but on lookup miss in `actual.metadata`, fall back to:
   - `actual.results[0].<field>` when `len(results) == 1` (vector
     degenerate case, §2.1),
   - then `actual.<field>` (top-level).

   This bridges pre-vector tests written against the `metadata` schema
   with post-2.6 transformative executors that expose per-entry fields
   in `results[]`.

## Alternatives considered

* **Mass-rewrite the test cases** to remove `metadata_field_eq` and use
  ad-hoc matchers — violates §8.2 and creates churn proportional to the
  test corpus.
* **Restore a `metadata` field on every executor** — would force every
  vector executor to duplicate per-entry data into a top-level `metadata`
  block, violating §2.6 (entries vs results consistency).
* **Add a separate matcher `result_field_eq`** — explicit but doubles
  the matcher surface. Generalised fallback in `metadata_field_eq` is
  thinner and back-compatible with all existing pre-vector tests.

## Consequences

* All 12 red tests recover (147/147 green) without altering test cases or
  executor outputs.
* The runner becomes schema-aware: same matcher works whether the
  executor follows the scalar, metadata, or vector schema.
* Future vector executors gain test coverage for free using the existing
  matcher vocabulary.
