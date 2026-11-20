#!/usr/bin/env python3
"""Register S-013 independent rows and the retained whole-request undo proofs."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.s013_rows_20261119"
GROUPS = {
    "module": (
        "tests/runtime/infra/test_s002_undo_budget.py",
        "tests/runtime/infra/test_s013_rows.py",
        "tests/runtime/infra/test_s013_end_to_end.py",
        "tests/runtime/infra/test_s013_rimandi.py",
        "tests/runtime/infra/test_s013_live_rimandi.py",
        "tests/runtime/infra/test_s013_timeout_recovery.py",
        "tests/runtime/infra/test_s013_presentation.py",
        "tests/runtime/infra/test_s013_r13.py",
        "tests/portable/test_s013_retention_progress.py",
        "tests/runtime/executors/test_independent_rows_equivalence.py",
        "tests/runtime/infra/test_s002_list_outcomes.py",
    ),
    "cluster": (
        "tests/runtime/infra/test_undo_request_unit.py",
        "tests/runtime/infra/test_undo_execution_outcome.py",
        "tests/runtime/durable_workloads/test_long_tools_have_a_home.py",
        "tests/runtime/infra/test_lre_answers.py",
        "tests/runtime/infra/test_s011_request_flow.py",
        "tests/runtime/infra/test_s011_confirmation_delivery.py",
        "tests/runtime/remote/test_server_folders.py",
        "tests/runtime/remote/test_server_filesystem_host.py",
        "tests/runtime/remote/test_server_filesystem_protocol.py",
        "tests/runtime/infra/test_partitioned_runs.py",
        "tests/runtime/engine/test_generic_pipeline_invariants.py",
        "tests/runtime/http/test_session_event_attachments.py",
        "tests/runtime/http/test_dialog_async_completion.py",
        "tests/runtime/http/test_chat_late_outcome_files_browser.py",
        "tests/runtime/durable_workloads/test_direct_invocation.py",
        "tests/runtime/durable_workloads/test_partitioned_invocation.py",
        "tests/runtime/durable_workloads/test_schema.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/undo.py",
                        "Only the frozen rows approved after a long job may continue "
                        "durably; interrupted effects reconcile by identity, adopted "
                        "directories remain bounded, and request-wide undo reports "
                        "actual progress and retains its time-limited remainder.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"s013_rows_20261119_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='s002-undo-{level}-', suffix='.log')
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
    args = parser.parse_args()
    registry, names = register(args.db)
    try:
        print({case.name: case.id for case in registry.cases_for_module(MODULE)
               if case.name in names})
    finally:
        registry.conn.close()


if __name__ == "__main__":
    main()
