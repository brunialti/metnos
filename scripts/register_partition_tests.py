#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register and run the tests of a step served partition by partition: the declaration in the manifest, the reduction to a literal list, the one result shape, the durable plan with one unit per partition, its admission and its answer, the row-driven move and folder creation, and the plan brought to the served form (16/11/2026)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.partitioned_runs_20261116"
GROUPS = {
    "module": (
        "tests/runtime/infra/test_partitioned_runs.py",
        "tests/runtime/durable_workloads/test_partitioned_invocation.py",
        "tests/runtime/test_lre_submission_partitions.py",
        "tests/runtime/infra/test_lre_answers_partitions.py",
        "tests/runtime/executors/test_files_inside_a_partition.py",
        "tests/runtime/engine/test_partition_plans.py",
        "tests/runtime/engine/test_question_before_a_removal.py",
        "tests/runtime/engine/test_request_grounding.py",
    ),
    "cluster": (
        "tests/runtime/test_lre_submission.py",
        "tests/runtime/infra/test_lre_answers.py",
        "tests/runtime/durable_workloads/test_direct_invocation.py",
        "tests/runtime/durable_workloads/test_direct_partial_result.py",
        "tests/runtime/durable_workloads/test_entry_identity_fanout.py",
        "tests/runtime/durable_workloads/test_internal_runners.py",
        "tests/runtime/durable_workloads/test_runtime_bindings.py",
        "tests/runtime/executors/test_executor_birth_identity.py",
        "tests/runtime/executors/test_executor_metadata.py",
        "tests/runtime/executors/test_move_files_undo.py",
        "tests/runtime/executors/test_executor_standard_move.py",
        "tests/runtime/executors/test_executor_standard_dirs_domain.py",
        "tests/runtime/safety/test_mass_mutation_gate.py",
        "tests/runtime/engine/test_final_message_invariant.py",
        "tests/runtime/engine/test_guard_pipeline_contract.py",
        "tests/runtime/skills/test_manifest_language_state_release_check.py",
        "tests/runtime/skills/test_manifest_routing_hygiene.py",
        "tests/runtime/infra/test_manifest_head_budget.py",
        "tests/runtime/i18n/test_seed_i18n_gate_keys.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/partitioned_runs.py",
                        "A tool declares what it can be divided on; the runtime calls it once per "
                        "partition, marks every row with its partition and joins the results; a long "
                        "tool gets one durable unit per partition.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"partitioned_runs_20261116_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='partitioned-runs-{level}-', suffix='.log')
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
            results, counts = run_cases(cases, registry, "partitioned_runs_20261116", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
