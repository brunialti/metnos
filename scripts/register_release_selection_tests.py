#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register release selection, command and observation regressions (S-021).

Registration does not run tests by default. The cluster accepts only its two
native probes skipped without an explicitly delegated unit; raw logs stay local.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.release_selection_s021"
GROUPS = {
    "module": (
        "tests/internal/test_release_test_selection.py",
        "tests/internal/test_release_service_times.py",
        "tests/internal/test_release_command.py",
        "tests/internal/test_release_revision.py",
        "tests/internal/test_release_portable_collection.py",
        "tests/internal/test_release_step_failure.py",
        "tests/internal/test_s020_release_timings.py",
    ),
    "cluster": (
        "tests/internal/test_s020_prepare_boundaries.py",
        "tests/runtime/infra/test_rm0008_release_cycle.py",
        "tests/portable/test_rm0008_workflow_preparation.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "internal/tools/rm0008_release_cycle.py",
                        "Select tests from changed sources and consumers, stop the release "
                        "command at its first failure, and keep service/timing observation "
                        "failures from interrupting the essential child process.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"release_selection_s021_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import json, os, subprocess, sys, tempfile
            from pathlib import Path
            import xml.etree.ElementTree as ET
            root = Path(_RT).parent
            directory = Path(tempfile.mkdtemp(prefix='release-selection-s021-{level}-'))
            log, report = directory / 'pytest.log', directory / 'pytest.xml'
            env = {{**os.environ, 'PYTHONPATH': 'runtime:.'}}
            env.pop('PYTEST_ADDOPTS', None)
            env.pop('METNOS_REQUIRE_REAL_BIRTH_LINUX', None)
            print(json.dumps({{'log': str(log), 'report': str(report)}}), flush=True)
            with log.open('w') as output:
                result = subprocess.run([
                    os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                    '-m', 'pytest', '-q', '-r', 's', '--tb=short', '-p', 'no:cacheprovider',
                    '-o', 'xfail_strict=true', f'--junitxml={{report}}',
                    *{selectors!r}], cwd=root, env=env,
                    stdout=output, stderr=subprocess.STDOUT, text=True, timeout=900)
            assert result.returncode == 0, f'pytest rc={{result.returncode}}; see {{log}}'
            cases = list(ET.parse(report).getroot().iter('testcase'))
            assert cases and not any(case.find('failure') is not None or
                case.find('error') is not None for case in cases), str(report)
            skipped = [(case, case.find('skipped')) for case in cases
                       if case.find('skipped') is not None]
            expected = {{('tests.runtime.infra.test_rm0008_release_cycle',
                         f'test_native_cutover_stdio_probe[{{value}}]')
                        for value in ('False', 'True')}} if {level!r} == 'cluster' else set()
            assert len(skipped) == len(expected), str(report)
            assert {{(case.get('classname'), case.get('name')) for case, _ in skipped}} == expected, str(report)
            assert all(item.get('type') == 'pytest.skip' and item.get('message') ==
                'requires an explicitly delegated native test unit' for _, item in skipped), str(report)
            print(json.dumps({{'passed': len(cases) - len(skipped), 'skipped': len(skipped),
                'log': str(log), 'report': str(report)}}))
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
            results, counts = run_cases(cases, registry, "release_selection_s021", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
