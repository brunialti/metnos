#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register and run the tests of the undo of a whole request: one «annulla» reverses the request and every turn that continues it (the rest of a long job before and after a consent, a turn resumed after a yes in the chat), most recent first, through the closed file operations and the tools' own reverses alike, and the answer says what came back and what stayed, with why (17/11/2026)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.undo_request_20261117"
GROUPS = {
    "module": (
        "tests/runtime/infra/test_undo_request_unit.py",
    ),
    "cluster": (
        "tests/runtime/infra/test_undo_actor_isolation.py",
        "tests/runtime/infra/test_undo_execution_outcome.py",
        "tests/runtime/infra/test_undo_chokepoint.py",
        "tests/runtime/infra/test_reverse_patterns_authenticated_dispatch.py",
        "tests/runtime/infra/test_reverse_patterns_declared.py",
        "tests/runtime/infra/test_lre_answers.py",
        "tests/runtime/infra/test_lre_answers_partitions.py",
        "tests/runtime/executors/test_move_files_undo.py",
        "tests/runtime/engine/test_fast_path.py",
        "tests/runtime/engine/test_undo_single_run.py",
        "tests/runtime/engine/test_orchestration.py",
        "tests/runtime/remote/test_server_folders.py",
        "tests/runtime/safety/test_mass_mutation_gate.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/undo.py",
                        "The unit of an undo is the request: the turn where the person asked and "
                        "every turn that continues it; one «annulla» reverses all of it and says "
                        "what came back and what stayed.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"undo_request_20261117_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='undo-request-{level}-', suffix='.log')
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
            results, counts = run_cases(cases, registry, "undo_request_20261117", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
