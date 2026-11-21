#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register A0 planning observation and its affected dispatch/HTTP cluster.

Registration is performed by the reviewer at integration; no tests run by default.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.planning_measure_20261120"
GROUPS = {
    "module": (
        "tests/runtime/test_planning_measure.py",
        "tests/runtime/test_planning_measure_ports.py",
        "tests/runtime/test_planning_measure_http.py",
        "tests/runtime/test_planning_measure_catalog.py",
        "tests/runtime/test_planning_measure_client.py",
    ),
    "cluster": (
        "tests/runtime/engine/test_guard_pipeline_contract.py",
        "tests/runtime/engine/test_guard_stats.py",
        "tests/runtime/engine/test_partition_plans.py",
        "tests/runtime/engine/test_engine_parallel_steps.py",
        "tests/runtime/engine/test_orchestration.py",
        "tests/runtime/http/test_http_turn_pool.py",
        "tests/runtime/infra/test_llm_virtualization_boundaries.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/planning_measure.py",
                        "Observe the ordinary L3 planner with memory disabled, mask values "
                        "and stop before durable admission or inline execution; an observer "
                        "failure remains inert outside measurement and stops an active measure.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"planning_measure_20261120_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='planning-measure-{level}-', suffix='.log')
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
            results, counts = run_cases(cases, registry, "planning_measure_20261120", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
