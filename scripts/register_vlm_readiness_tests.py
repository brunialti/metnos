#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register isolated VLM lifecycle, readiness and existing automatic-retry regressions (18/11/2026)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.vlm_readiness_20261118"
GROUPS = {
    "module": (
        "tests/runtime/infra/test_vlm_lifecycle.py",
        "tests/runtime/infra/test_vlm_legacy_watchdog.py",
        "tests/runtime/infra/test_vlm_readiness.py",
        "tests/runtime/infra/test_vlm_deadline.py",
        "tests/runtime/infra/test_vlm_launcher_layout.py",
        "tests/runtime/durable_workloads/test_resource_readiness.py",
    ),
    "cluster": (
        "tests/runtime/infra/test_vlm_startup_profile.py",
        "tests/runtime/durable_workloads/test_lifecycle_convergence.py",
        "tests/runtime/durable_workloads/test_execution_bridge.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/virt/__init__.py",
                        "Local model readiness succeeds only with health, keeps one deadline, "
                        "renews idle use and owns lifecycle generations; temporary model "
                        "unavailability uses the workload engine's existing bounded retries.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"vlm_readiness_20261118_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='vlm-readiness-{level}-', suffix='.log')
            os.close(fd)
            log = Path(name)
            with log.open('w') as output:
                result = subprocess.run([
                    os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                    '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
                    *{selectors!r}], cwd=root,
                    env={{**os.environ, 'PYTHONPATH': 'runtime:.'}},
                    stdout=output, stderr=subprocess.STDOUT, text=True, timeout=900)
            output = log.read_text()
            print('Detailed test log:', log)
            print(output[-2400:].strip())
            assert result.returncode == 0, output[-8000:]
            assert re.search(r'\\b[1-9][0-9]* passed\\b', output), output
            assert not re.search(r'\\b[1-9][0-9]* (?:skipped|xfailed|xpassed|deselected)\\b', output), output
        ''')
        registry.add_case(MODULE, name, level, "integration", "python", code)
    return registry, names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry, names = register(args.db)
    try:
        cases = sorted((case for case in registry.cases_for_module(MODULE) if case.name in names),
                       key=lambda case: (case.level != "module", case.name))
        print({case.name: case.id for case in cases}, flush=True)
        if args.verify:
            from runner import run_cases

            os.environ.setdefault("METNOS_TEST_PYTHON", sys.executable)
            results, counts = run_cases(cases, registry, "vlm_readiness_20261118", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
