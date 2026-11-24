#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register module and cluster pytest cases in an explicit test database."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    for argument in ("module", "kind", "source", "description", "module-case", "cluster-case"):
        parser.add_argument(f"--{argument}", required=True)
    parser.add_argument("--module-tests", nargs="+", required=True)
    parser.add_argument("--cluster-tests", nargs="+", required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.module_case == args.cluster_case or args.timeout <= 0:
        parser.error("case names must differ and timeout must be positive")

    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(args.db)
    try:
        registry.add_module(args.module, args.kind, args.source, args.description)
        groups = (("module", args.module_case, tuple(args.module_tests)),
                  ("cluster", args.cluster_case, tuple(args.cluster_tests)))
        for level, name, selectors in groups:
            code = textwrap.dedent(f'''\
                import os, re, subprocess, sys, tempfile
                from pathlib import Path
                root = Path(_RT).parent
                fd, name = tempfile.mkstemp(prefix='registered-tests-{level}-', suffix='.log')
                os.close(fd)
                log = Path(name)
                with log.open('w') as output:
                    result = subprocess.run([
                        os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                        '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
                        *{selectors!r}], cwd=root,
                        env={{**os.environ, 'PYTHONPATH': 'runtime:.'}},
                        stdout=output, stderr=subprocess.STDOUT, text=True, timeout={args.timeout})
                output = log.read_text()
                print('Detailed test log:', log)
                print(output[-2400:].strip())
                assert result.returncode == 0, output[-8000:]
                assert re.search(r'\\b[1-9][0-9]* passed\\b', output), output
                assert not re.search(r'\\b[1-9][0-9]* (?:skipped|xfailed|xpassed|deselected)\\b', output), output
            ''')
            registry.add_case(args.module, name, level, "integration", "python", code)
        names = {args.module_case, args.cluster_case}
        cases = sorted((case for case in registry.cases_for_module(args.module) if case.name in names),
                       key=lambda case: (case.level != "module", case.name))
        print({case.name: case.id for case in cases}, flush=True)
        if not args.verify:
            return 0
        from runner import run_cases

        os.environ.setdefault("METNOS_TEST_PYTHON", sys.executable)
        results, counts = run_cases(cases, registry, args.module, verbose=True)
        for result in results:
            print(result.case.name, result.status, result.output, result.failure)
        return 0 if counts == {"pass": 2} else 1
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
