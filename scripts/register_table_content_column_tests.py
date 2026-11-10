#!/usr/bin/env python3
"""Register and run: a metadata table never shows what the content view shows (10/11/2026)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "engine.executor"
MODULE_ROW = ("runtime", "runtime/engine/executor.py",
              "Cached framework execution and query binding")
_MODULE_TESTS = (
    "tests/runtime/engine/test_table_content_column.py",
    "tests/runtime/engine/test_read_content_presentation.py",
    "tests/runtime/engine/test_table_date_column.py",
)
GROUPS = {
    "module": _MODULE_TESTS,
    "cluster": _MODULE_TESTS + (
        "tests/runtime/engine/test_output_policy_normalize.py",
        "tests/runtime/engine/test_finalizer_unico.py",
        "tests/runtime/engine/test_final_message_invariant.py",
        "tests/runtime/engine/test_table_pagination.py",
        "tests/runtime/engine/test_sites_extracted_period_binding.py",
        "tests/runtime/engine/test_ensure_extract_clause.py",
        "tests/runtime/safety/test_false_mutation.py",
        "tests/runtime/sites/test_site_consent_tail.py",
        "tests/runtime/sites/test_sites_security.py",
        "tests/runtime/backends/test_google_photos_backend.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    # The module already exists in the shared registry: keep its row as is.
    if registry.conn.execute("SELECT 1 FROM modules WHERE name = ?", (MODULE,)).fetchone() is None:
        registry.add_module(MODULE, *MODULE_ROW)
    names = set()
    for level, selectors in GROUPS.items():
        name = f"table_content_column_20261110_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='table-content-column-{level}-', suffix='.log')
            os.close(fd)
            log = Path(name)
            with log.open('w') as output:
                result = subprocess.run([
                    os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                    '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
                    *{selectors!r}], cwd=root,
                    env={{**os.environ, 'PYTHONPATH': 'runtime:.'}},
                    stdout=output, stderr=subprocess.STDOUT, text=True, timeout=300)
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
            results, counts = run_cases(cases, registry, "table_content_column_20261110", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
