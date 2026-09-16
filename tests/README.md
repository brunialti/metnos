# Metnos verification workspace

All development-time verification assets live under this directory. Product
runtime code must not create new test, benchmark or simulator trees elsewhere.

| Directory | Purpose | Default command |
|---|---|---|
| `runtime/` | deterministic unit, component and contract tests, grouped by domain | `./.venv/bin/python -m pytest -q tests/runtime` |
| `runtime/scheduler_v2/` | scheduler component tests | `./.venv/bin/python -m pytest -q tests/runtime/scheduler_v2` |
| `portable/` | public cross-platform certification; focused development and complete release checks | see [portable verification](portable/README.md) |
| `e2e/` | isolated HTTP/CLI end-to-end scenarios and live opt-in probes | `tests/e2e/run.sh` |
| `simulator/` | planner research and opt-in domain simulators, including real-browser sites and web-UI lifecycle probes | `METNOS_SITES_SIM=1 ./.venv/bin/python -m pytest -q tests/simulator/sites` |
| `benchmarks/` | reproducible performance and routing benches, frozen corpora and benchmark tools | see `benchmarks/README.md` |
| `stress/` | frozen stress corpora and historical evidence | run through the owning harness |
| `internal/` | release/conformance gate tests | `./.venv/bin/python -m pytest -q tests/internal` |
| `tools/` | gate and manifest-suite entry points | `./.venv/bin/python tests/tools/run_executor_manifests.py` |

`tests/runtime/conftest.py` redirects mutable HOME/XDG/Metnos roots before
collection and seeds only non-secret test material. A full suite must never
read or write the live user state.

Two runtime paths intentionally remain outside this tree:

- `runtime/test_runner.py` is part of synthesized-executor admission and is
  used by production code, despite its historical name.
- `runtime/testing/` stores product observability/conformance state consumed by
  the admin runtime; it is not a pytest suite.

Generated caches and run output (`.pytest_cache`, `__pycache__`, E2E `tmp/`,
simulator caches) are disposable and must stay ignored.

Browser simulators are optional by design: `simulator/sites/` needs
`METNOS_SITES_SIM=1` plus the installed Chromium distribution;
`simulator/web/` is collected only when the Playwright Python package is
available. Their absence skips the probe rather than weakening runtime unit
coverage.
