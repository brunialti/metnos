#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register module and cluster checks for comparison scope and grouped actions."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.partition_scope_20261118"
GROUPS = {
    'module': (
        'tests/runtime/engine/test_stepref_array_schema.py',
        'tests/runtime/engine/test_partition_scope.py',
        'tests/runtime/engine/test_partition_plans.py',
        'tests/runtime/engine/test_partition_sheets.py',
        'tests/runtime/engine/test_partition_destinations.py',
        'tests/runtime/engine/test_partition_destination_diagnostics.py',
        'tests/runtime/engine/test_planning_repair.py',
        'tests/runtime/engine/test_local_partition_repair.py',
        'tests/runtime/engine/test_partition_repair_prompt.py',
        'tests/runtime/infra/test_partitioned_global_projection.py',
        'tests/runtime/infra/test_s011_request_flow.py',
        'tests/runtime/infra/test_s011_confirmation_delivery.py',
        'tests/runtime/entries/test_entry_field_roles.py',
        'tests/runtime/backends/test_tabular_projection.py',
        'tests/runtime/engine/test_planner_sees_output_fields.py',
        'tests/runtime/executors/test_executor_metadata.py',
        'tests/runtime/executors/test_executor_catalog_metadata.py',
    ),
    'cluster': (
        'tests/runtime/engine/test_engine_core.py',
        'tests/runtime/infra/test_partitioned_runs.py',
        'tests/runtime/infra/test_lre_answers_partitions.py',
        'tests/runtime/infra/test_undo_request_unit.py',
        'tests/runtime/durable_workloads/test_partitioned_invocation.py',
        'tests/runtime/test_lre_submission_partitions.py',
        'tests/runtime/executors/test_files_inside_a_partition.py',
        'tests/runtime/executors/test_find_files_hash.py',
        'tests/runtime/engine/test_request_grounding.py',
        'tests/runtime/engine/test_question_before_a_removal.py',
        'tests/runtime/i18n/test_parser_detection_lexicon.py',
        'tests/runtime/infra/test_tabular_proposer_prompt.py',
        'tests/runtime/skills/test_manifest_language_state_release_check.py',
        'tests/runtime/skills/test_manifest_lang_state.py',
        'tests/runtime/executors/test_move_files_undo.py',
        'tests/runtime/executors/test_executor_standard_dirs_domain.py',
        'tests/runtime/entries/test_filter_entries_overlap.py',
        'tests/runtime/entries/test_sort_entries_record_date.py',
        'tests/runtime/entries/test_sort_entries_typed.py',
        'tests/runtime/engine/test_piping_from_step.py',
        'tests/runtime/engine/test_engine_from_step_safety.py',
        'tests/runtime/engine/test_guard_pipeline_contract.py',
        'tests/runtime/engine/test_guard_idempotence.py',
        'tests/runtime/engine/test_guardia_conserva_gli_args.py',
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/partitioned_runs.py",
                        "Whole-scope comparisons grouped by folder, explicit independent scopes, "
                        "typed column roles across transformations, unchanged confirmation "
                        "and whole-request undo.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"partition_scope_20261118_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='partition-scope-{level}-', suffix='.log')
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
            results, counts = run_cases(cases, registry, "partition_scope_20261118", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
