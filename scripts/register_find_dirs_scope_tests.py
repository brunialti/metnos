#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register the S-010 directory scope and truthful continuation tests."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.find_dirs_scope_20261117"
GROUPS = {
    "module": (
        "tests/runtime/infra/test_find_dirs_argument_scope.py",
        "tests/runtime/remote/test_relative_tool_scope.py",
        "tests/runtime/test_s010_long_answer_counts.py",
        "tests/runtime/infra/test_lre_find_dirs_archive.py",
    ),
    "cluster": (
        "tests/runtime/remote/test_filesystem_search.py",
        "tests/runtime/infra/test_sandbox_relative_paths.py",
        "tests/runtime/infra/test_lre_answers.py",
        "tests/runtime/infra/test_lre_answers_partitions.py",
        "tests/runtime/infra/test_long_answers.py",
        "tests/runtime/engine/test_table_reading_order.py",
        "tests/runtime/engine/test_partition_plans.py",
        "tests/runtime/safety/test_sandbox_skill_backed.py",
        "tests/runtime/safety/test_sandbox_local_models.py",
        "tests/runtime/remote/test_server_named_folders.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/server_filesystem.py",
                        "S-010 C1-C5/C7: named-folder visibility, preserved device scopes, "
                        "truthful relative-path errors and complete long-job answers.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"find_dirs_scope_20261117_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='find-dirs-scope-{level}-', suffix='.log')
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
            results, counts = run_cases(cases, registry, "find_dirs_scope_20261117", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
