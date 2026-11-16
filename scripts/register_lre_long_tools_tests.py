#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register and run the tests of the declared place of a long tool: a shipped tool with a ceiling of ten minutes or more runs alone, has its own plan, or works with the person and stays in the turn; a refusal names the tool and the check (16/11/2026)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.lre_long_tools_20261116"
GROUPS = {
    "module": (
        "tests/runtime/durable_workloads/test_long_tools_have_a_home.py",
        "tests/runtime/durable_workloads/test_direct_invocation.py",
        "tests/runtime/test_lre_submission.py",
        "tests/runtime/executors/test_read_files_ocr_agentic.py",
        "tests/runtime/executors/test_executor_helpers.py",
        "tests/runtime/learning/test_scorecards.py",
        "tests/runtime/entries/test_get_inputs_subprocess.py",
    ),
    "cluster": (
        "tests/runtime/durable_workloads/test_image_preset_contracts.py",
        "tests/runtime/durable_workloads/test_image_indexing_plan.py",
        "tests/runtime/durable_workloads/test_direct_partial_result.py",
        "tests/runtime/durable_workloads/test_runtime_bindings.py",
        "tests/runtime/executors/test_executor_prerequisites.py",
        "tests/runtime/executors/test_executor_standard.py",
        "tests/runtime/executors/test_executor_catalog_metadata.py",
        "tests/runtime/infra/test_lre_answers.py",
        "tests/runtime/i18n/test_seed_i18n_gate_keys.py",
        "tests/runtime/remote/test_device_shim_i18n.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/durable_workloads/direct_invocation.py",
                        "A shipped tool with a long ceiling has a declared place to run: alone, with its own "
                        "plan, or in the turn with the person; a refusal names the tool and the check.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"lre_long_tools_20261116_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='lre-long-tools-{level}-', suffix='.log')
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
            results, counts = run_cases(cases, registry, "lre_long_tools_20261116", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
