#!/usr/bin/env python3
"""Register and run LEXICON-THREADS-001: one detection-lexicon connection per thread (10/11/2026)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.i18n.detection_lexicon_threads"
GROUPS = {
    "module": ("tests/runtime/i18n/test_detection_lexicon_threads.py",),
    "cluster": (
        "tests/runtime/i18n/test_detection_lexicon_threads.py",
        "tests/runtime/i18n/test_detection_lexicon.py",
        "tests/runtime/i18n/test_detection_lexicon_readiness.py",
        "tests/runtime/i18n/test_detection_lexicon_residual_nz.py",
        "tests/runtime/i18n/test_detection_translate_daemon.py",
        "tests/runtime/i18n/test_reconciliation_lexicon.py",
        "tests/runtime/i18n/test_executor_deep_crawl_lexicon.py",
        "tests/runtime/infra/test_loader_lifecycle_signature.py",
        "tests/runtime/sites/test_cookie_rejection_guard.py",
        "tests/runtime/learning/test_proposal_evaluator.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "i18n", "runtime/detection_lexicon.py",
                        "Each thread reads and writes the detection lexicon through its own SQLite connection.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"detection_lexicon_threads_20261110_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='detection-lexicon-threads-{level}-', suffix='.log')
            os.close(fd)
            log = Path(name)
            with log.open('w') as output:
                result = subprocess.run([
                    os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                    '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
                    *{selectors!r}], cwd=root,
                    env={{**os.environ, 'PYTHONPATH': 'runtime:.'}},
                    stdout=output, stderr=subprocess.STDOUT, text=True, timeout=600)
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
            results, counts = run_cases(cases, registry, "detection_lexicon_threads_20261110", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
