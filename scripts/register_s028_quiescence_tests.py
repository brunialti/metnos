#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Prepare S-028 quiescence regressions; registration never runs the cases."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.stack_quiescence_20261122"
GROUPS = {
    "module": ("tests/runtime/infra/test_s028_quiescence_wait.py",),
    "cluster": ("tests/runtime/infra/test_stack_reconcile.py",
                "tests/runtime/infra/test_rm0008_release_cycle.py"),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/stack_reconcile.py",
                        "Fresh quiescence after publication, bounded wait and preserved activation debt.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"stack_quiescence_20261122_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='s028-{level}-', suffix='.log')
            os.close(fd)
            log = Path(name)
            with tempfile.TemporaryDirectory(prefix='s028-home-') as home, log.open('w') as output:
                env = {{**os.environ, 'PYTHONPATH': 'runtime:.',
                       'PYTHONDONTWRITEBYTECODE': '1', 'HOME': home,
                       'METNOS_USER_DATA': home + '/data',
                       'METNOS_USER_CONFIG': home + '/config',
                       'METNOS_USER_STATE': home + '/state'}}
                result = subprocess.run([
                    os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                    '-m', 'pytest', '-q', '-r', 's', '--tb=short', '-p', 'no:cacheprovider',
                    *{selectors!r}], cwd=root, env=env,
                    stdout=output, stderr=subprocess.STDOUT, text=True, timeout=300)
            output = log.read_text()
            print('Detailed test log:', log)
            print(output[-2400:].strip())
            assert result.returncode == 0, output[-8000:]
            assert re.search(r'\\b[1-9][0-9]* passed\\b', output), output
            assert not re.search(r'\\b[1-9][0-9]* (?:xfailed|xpassed|deselected)\\b', output), output
            skips = re.findall(r'\\b([1-9][0-9]*) skipped\\b', output)
            reports = re.findall(r'^SKIPPED \\[([0-9]+)\\] (.+)$', output, re.M)
            if {level!r} == 'cluster':
                assert skips == ['2'] and len(reports) == 1 and reports[0][0] == '2', output
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
    args = parser.parse_args()
    registry, names = register(args.db)
    try:
        print({case.name: case.id for case in registry.cases_for_module(MODULE)
               if case.name in names})
    finally:
        registry.conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
