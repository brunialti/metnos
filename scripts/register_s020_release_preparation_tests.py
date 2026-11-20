#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register candidate preparation and release timing regressions (S-020).

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
MODULE = "runtime.release_preparation_20261120"
GROUPS = {
    "module": (
        "tests/internal/test_s020_repin.py",
        "tests/internal/test_s020_prepare_boundaries.py",
        "tests/internal/test_s020_release_timings.py",
        "tests/internal/test_s020_inventory_final.py",
        "tests/runtime/skills/test_s020_builtin_check.py",
    ),
    "cluster": (
        "tests/runtime/infra/test_rm0008_release_cycle.py",
        "tests/internal/test_rm0008_boundary_projection_gates.py",
        "tests/runtime/infra/test_export_public_payload_mirrors.py",
        "tests/internal/test_rm0008_repin_source_roots.py",
        "tests/runtime/skills/test_builtin_executor_contracts.py::test_all_planner_visible_builtins_have_valid_signed_contracts",
        "tests/runtime/skills/test_builtin_executor_contracts.py::test_admin_contract_describes_unprivileged_commands_without_granting_them",
        "tests/runtime/skills/test_builtin_executor_contracts.py::test_loader_admits_builtins_only_from_their_signed_contracts",
        "tests/runtime/skills/test_builtin_executor_contracts.py::test_private_contracts_keep_signature_and_code_checks",
        "tests/runtime/skills/test_builtin_executor_contracts.py::test_builtin_handler_source_registry_is_total_and_literal",
        "tests/runtime/skills/test_builtin_executor_contracts.py::test_builtin_contract_rejects_a_runtime_module_that_differs_from_admitted_bytes",
        "tests/runtime/skills/test_builtin_executor_contracts.py::test_builtin_generator_sign_mode_resumes_one_closed_birth_candidate",
        "tests/runtime/skills/test_builtin_executor_contracts.py::test_builtin_generator_preserves_source_version",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "internal/tools/rm0008_release_cycle.py",
                        "Generate before committing, verify the final candidate without "
                        "repairing it, compare shared public projection roots with the "
                        "final export, and collect developer-owned release timing events.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"release_preparation_20261120_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='release-preparation-{level}-', suffix='.log')
            os.close(fd)
            log = Path(name)
            with log.open('w') as output:
                result = subprocess.run([
                    os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                    '-m', 'pytest', '-q', '-r', 's', '--tb=short', '-p', 'no:cacheprovider',
                    *{selectors!r}], cwd=root,
                    env={{**os.environ, 'PYTHONPATH': 'runtime:.'}},
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
            results, counts = run_cases(cases, registry, "release_preparation_20261120", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
