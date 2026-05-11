---
id: 0029
title: SQLite-backed test framework with module/cluster/system scope
date: 2026-04-26
status: accepted
area: testing
related:
  - 0023
---

## Context

By 26 April 2026 the POC of v1.1 had a small set of ad-hoc tests
distributed across executor folders (the birth tests — five per
executor, run by a per-executor `test_runner.py`) plus a handful of
manual end-to-end runs. Roberto's reaction was direct: *"there are
not enough tests; start to structure a robust test system based on a
DB"*. The ad-hoc state was about to grow with the seed pool (~22
executors) plus the 8 runtime modules; without structure it would
sprawl into something nobody wanted to maintain.

The framework had to support three levels of scope (single module,
module + neighbors, system end-to-end), have a single source of
truth for "which modules exist and which depend on which", make
cluster scope automatic from the dependency graph, and be runnable
in a few seconds for the inner-loop development of ADR 0023.

## Decision

A SQLite-backed framework lives under `runtime/testing/`.

**Schema.** Three core tables in `tests.db`:

- `modules`: kind ∈ {`executor`, `runtime`, `core`}; the 12 initial
  modules (8 runtime: `agent_runtime`, `loader`, `sign`, `prefilter`,
  `vaglio`, `cost_tracker`, `llm_provider`, `test_runner`; 4
  executors: `fs_read`, `fs_write`, `time_read`, `web_fetch`) are
  loaded by `seed_modules.py` (22 dependency edges).
- `module_dependencies`: directed edges (a → b means "a uses b").
- `test_cases`: keyed by `module + name`, with `level` ∈ `{module,
  cluster, system}`, `category` ∈ `{happy, edge, failure, security,
  integration}`, `test_kind` ∈ `{python, shell, birth, e2e}`,
  `test_code`, `setup_code`, `teardown_code`, `expected`.
- `test_runs`: every execution recorded with `status`, `duration_ms`,
  `output`, `failure_detail`, `triggered_by`.

**Cluster as derived data.** `cluster(X)` is computed as `X` plus all
modules that `X` uses or that use `X` (1 hop, both directions). The
graph is the source of truth; nobody hand-curates clusters.

**Four test kinds.**
- `python`: source executed in subprocess; auto-imports `runtime/`.
  Pass on exit 0.
- `shell`: shell command; pass on exit code matching `expected`
  (default 0).
- `birth`: re-runs an executor's `test_runner.py` on its manifest,
  optionally filtered to one test name. Reuses existing birth tests
  without duplication.
- `e2e`: launches `agent_runtime.py <query>` and verifies the
  `final_message` plus the executor invoked. System-level.

**Initial population.** 93 cases at first run, all green: 74 module
(17 birth + 57 python), 9 cluster integration, 10 system e2e. Five
security tests (manifest tampering, code tampering, sensitive value
in vaglio log, etc.) sit under the `security` category.

**Runner CLI.**
```
python3 seed_modules.py        # load modules + dependencies
python3 populate_cases.py      # load/refresh cases
python3 runner.py module <n>   # only the module's tests
python3 runner.py cluster <n>  # module + 1-hop neighbors
python3 runner.py level system # all e2e
python3 runner.py all          # all 93
python3 runner.py summary      # DB stats
```

The protocol of ADR 0023 (iterate-test-cluster) is the discipline
that gives the framework its operational role.

**Performance envelope** at 26 April 2026, on `qwen3:8b` with real
network for `web_fetch`:
- module python: 20–100 ms each
- birth fs/time: 80–100 ms each
- birth web_fetch: 1.4–1.7 s each (HTTP real)
- cluster integration with LLM: 200 ms – 5 s
- system e2e: 1.4–5.8 s each
- total all-93: 50–90 s
- total cluster fs_read (55 cases): ~30 s
- total module fs_read (5 birth): ~500 ms

## Alternatives considered

**Plain pytest, no DB.** Pro: ecosystem support, IDE integration.
Con: no native concept of "cluster from dependency graph"; would
require a custom collector that duplicates the DB structure; the DB
schema is the actual artifact we want, not a side product. Rejected
as the primary framework; pytest can wrap DB-driven cases later if
needed.

**Hand-curated test groups.** Pro: explicit; easy to read. Con:
groups drift from the actual code dependency graph; modifying a
module requires hand-editing the groups it belongs to; the cluster
property collapses. Rejected.

**No cluster scope, only module + system.** Pro: simpler. Con: the
regression class that motivated the framework — a change to
`prefilter` breaking `agent_runtime` — is exactly the cluster case.
Without cluster scope the framework fails to add value over plain
per-module testing. Rejected.

**Postgres or another full DB** instead of SQLite. Pro: scaling
later. Con: SQLite is sufficient for thousands of cases; runs in-
process; deploy is a single file. Switching later is a non-issue.
Rejected.

## Consequences

The framework is the operational base for ADR 0023 (development
protocol) and ADR 0024 (test failure means fix code). It is also the
authority that backs ADR 0022 (POC validates microdesign): a
microdesign is "approved" only if its module's cluster is green.

The framework grows. The 93 initial cases are a starting point; new
modules add seed entries, new behaviors add cases. Future work
queued:
- `runner.py changed` (auto-detect modified modules via mtime/git);
- `--junit-xml` for external CI integration;
- test parallelism for independent cases (currently sequential);
- a CLI helper `metnos-test add <module> <name> ...` to write cases
  without editing `populate_cases.py` by hand;
- gaps to fill: malformed manifest, multi-step retry chain,
  `cap_steps` reached, vaglio reject path.

A pattern visible already: the framework exposes manifest gaps as
well as code bugs. A modification to `fs_read` exposed that the
manifest's affinity didn't include "leggi" (Italian for "read") —
the LLM couldn't select it for a query in Italian, the cluster test
failed, the manifest was fixed alongside the code. Without the
framework, the gap would have surfaced months later in production.
