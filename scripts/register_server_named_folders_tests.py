#!/usr/bin/env python3
"""Register and run the server named-folder checks (live turn 9bfdee52, 9/11/2026)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.remote.server_named_folders"
GROUPS = {
    "module": ("tests/runtime/remote/test_server_named_folders.py",
               "tests/runtime/remote/test_device_paths.py"),
    "cluster": (
        "tests/runtime/remote/test_server_named_folders.py",
        "tests/runtime/remote/test_device_paths.py",
        "tests/runtime/remote/test_server_filesystem_host.py",
        "tests/runtime/remote/test_filesystem_permissions.py",
        "tests/runtime/remote/test_device_filesystem.py",
        "tests/runtime/remote/test_device_filesystem_host.py",
        "tests/runtime/remote/test_transfer_deadline.py",
        "tests/runtime/infra/test_installer_managed_filesystem_host.py",
        "tests/runtime/safety/test_sandbox_skill_backed.py",
        "tests/runtime/safety/test_sandbox_local_models.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "remote", "runtime/server_filesystem_host.py",
                        "Named user folders on the server resolve to the installation owner's folders.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"server_named_folders_20261109_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='server-named-folders-{level}-', suffix='.log')
            os.close(fd)
            log = Path(name)
            with log.open('w') as output:
                result = subprocess.run([
                    os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                    '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
                    *{selectors!r}], cwd=root,
                    env={{**os.environ, 'PYTHONPATH': 'runtime:.'}},
                    stdout=output, stderr=subprocess.STDOUT, text=True, timeout=180)
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
            results, counts = run_cases(cases, registry, "server_named_folders_20261109", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
