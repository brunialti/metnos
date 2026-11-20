#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register tests of the final folder summary through the written turn (S-019)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.final_partition_summary_20261120"
GROUPS = {
    "module": (
        "tests/runtime/infra/test_s019_final_partition_summary.py",
    ),
    "cluster": (
        "tests/runtime/infra/test_partitioned_runs.py",
        "tests/runtime/infra/test_lre_answers_partitions.py::test_the_folders_are_one_answer_in_the_conversation_that_asked",
        "tests/runtime/infra/test_lre_answers_partitions.py::test_a_folder_that_could_not_be_read_is_said_and_the_others_are_given",
        "tests/runtime/infra/test_lre_answers_partitions.py::test_the_answer_carries_one_line_for_each_folder",
        "tests/runtime/infra/test_lre_answers_partitions.py::test_a_folder_nobody_answered_for_is_named_not_left_out",
        "tests/runtime/infra/test_lre_answers.py::test_the_answer_comes_back_as_a_turn_linked_to_the_request",
        "tests/runtime/infra/test_lre_answers.py::test_what_was_lost_is_said_once_in_the_words_the_chat_already_uses",
        "tests/runtime/infra/test_lre_answers.py::test_the_planners_own_sentence_is_used_when_there_is_one",
        "tests/runtime/infra/test_lre_answers.py::test_what_the_job_found_is_not_lost_when_a_later_step_fails",
        "tests/runtime/infra/test_lre_answers.py::test_many_results_of_a_failed_rest_come_as_a_file",
        "tests/runtime/infra/test_s013_r14.py::test_final_answer_with_unasked_removal_keeps_real_counts",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/agent_runtime.py",
                        "Final answers combine existing result columns for the same folders; "
                        "different folder sets and single results retain their summaries, "
                        "and failed searches retain their reasons without invented change counts.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"final_partition_summary_20261120_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='final-partition-summary-{level}-', suffix='.log')
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
            results, counts = run_cases(cases, registry, "final_partition_summary_20261120", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
