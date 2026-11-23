#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register grouped systemd observations (S-026 Tempo 2); never run tests here."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.grouped_systemd_20261122"
GROUPS = {
    "module": (("tests/portable/test_s026_grouped_systemd.py",), ()),
    "private": (("tests/runtime/test_s026_grouped_systemd_captures.py",), ()),
    "portable": (("tests/portable/test_executor_birth_systemd_quiescence.py",
                  "tests/portable/test_contract_cutover_guard_session.py"), ()),
    "runtime": (("tests/runtime/infra/test_stack_reconcile.py",),
                ("-k", "systemctl_show or user_unit_absence or root_user_manager or missing_explicit_service_user")),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/stack_reconcile.py",
                        "Fresh grouped observations retain exact unit identity, "
                        "closed fields, manager absence and bounded transports.")
    for name, (selectors, options) in GROUPS.items():
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='grouped-systemd-{name}-', suffix='.log')
            os.close(fd)
            log = Path(name)
            with log.open('w') as output:
                result = subprocess.run([
                    os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                    '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
                    *{selectors!r}, *{options!r}], cwd=root,
                    env={{**os.environ, 'PYTHONPATH': 'runtime:.',
                         'PYTHONDONTWRITEBYTECODE': '1',
                         'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}},
                    stdout=output, stderr=subprocess.STDOUT, text=True, timeout=180)
            output = log.read_text()
            print('Detailed test log:', log)
            print(output[-2400:].strip())
            assert result.returncode == 0, output[-8000:]
            assert re.search(r'\\b[1-9][0-9]* passed\\b', output), output
            assert not re.search(r'\\b[1-9][0-9]* (?:skipped|xfailed|xpassed)\\b', output), output
            if {name!r} != 'runtime':
                assert not re.search(r'\\b[1-9][0-9]* deselected\\b', output), output
        ''')
        registry.add_case(MODULE, f"grouped_systemd_20261122_{name}",
                          "module" if name in ("module", "private") else "cluster", "integration", "python", code)
    return registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    registry = register(args.db)
    try:
        print({case.name: case.id for case in registry.cases_for_module(MODULE)}, flush=True)
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
