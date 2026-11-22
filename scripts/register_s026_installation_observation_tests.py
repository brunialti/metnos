#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register optional installation observations and preview regressions (S-026).

The cluster retains two native probes requiring an explicitly delegated unit;
only those two skips are accepted. Registration does not run tests by default.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.installation_observation_20261122"
GROUPS = {
    "module": ("tests/internal/test_s026_installation_observation.py",),
    "cluster": (
        "tests/internal/test_s020_release_timings.py",
        "tests/runtime/infra/test_rm0008_release_cycle.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "internal/tools/rm0008_release_cycle.py",
                        "Opt-in process launch observations cannot stop a crossing; "
                        "preview is optional while authenticated audit remains binding.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"installation_observation_20261122_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='installation-observation-{level}-', suffix='.log')
            os.close(fd)
            log = Path(name)
            with log.open('w') as output:
                result = subprocess.run([
                    os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                    '-m', 'pytest', '-q', '-r', 's', '--tb=short', '-p', 'no:cacheprovider',
                    *{selectors!r}], cwd=root,
                    env={{**os.environ, 'PYTHONPATH': 'runtime:.', 'PYTHONDONTWRITEBYTECODE': '1'}},
                    stdout=output, stderr=subprocess.STDOUT, text=True, timeout=900)
            output = log.read_text()
            print('Detailed test log:', log)
            print(output[-2400:].strip())
            assert result.returncode == 0, output[-8000:]
            assert re.search(r'\\b[1-9][0-9]* passed\\b', output), output
            assert not re.search(r'\\b[1-9][0-9]* (?:xfailed|xpassed|deselected)\\b', output), output
            skips = re.findall(r'\\b([1-9][0-9]*) skipped\\b', output)
            reports = re.findall(r'^SKIPPED \\[([0-9]+)\\] (.+)$', output, re.M)
            if {level!r} == 'cluster':
                assert skips == ['2'], output
                assert len(reports) == 1 and reports[0][0] == '2', output
                assert re.fullmatch(
                    r'tests/runtime/infra/test_rm0008_release_cycle\\.py:[0-9]+: '
                    r'requires an explicitly delegated native test unit', reports[0][1]), output
            else:
                assert not skips and not reports, output
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
            results, counts = run_cases(cases, registry, "installation_observation_20261122", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
