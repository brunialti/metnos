#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register and run the tests of a request that asks for more than its long step: the long step is handed to LRE, the rest of the plan runs when the job ends, and nothing is removed without asking (15/11/2026)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.lre_rest_of_request_20261115"
GROUPS = {
    "module": (
        "tests/runtime/test_lre_submission.py",
        "tests/runtime/infra/test_lre_answers.py",
        "tests/runtime/safety/test_mass_mutation_gate.py",
        "tests/runtime/http/test_session_event_attachments.py",
    ),
    "cluster": (
        "tests/runtime/durable_workloads/test_direct_invocation.py",
        "tests/runtime/durable_workloads/test_direct_partial_result.py",
        "tests/runtime/durable_workloads/test_image_indexing_plan.py",
        "tests/runtime/durable_workloads/test_image_search_contract.py",
        "tests/runtime/durable_workloads/test_job_notices.py",
        "tests/runtime/executors/test_executor_prerequisites.py",
        "tests/runtime/scheduler_v2/test_image_index_refresh.py",
        "tests/runtime/engine/test_engine_core.py",
        "tests/runtime/engine/test_fastpath_feedback_rows.py",
        "tests/runtime/engine/test_orchestration.py",
        "tests/runtime/engine/test_completion_result.py",
        "tests/runtime/sites/test_site_consent_tail.py",
        "tests/runtime/infra/test_long_answers.py",
        "tests/runtime/http/test_consegna_esito_al_proprietario.py",
        "tests/runtime/i18n/test_seed_i18n_gate_keys.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/lre_answers.py",
                        "A request with a long step and other steps: the long step goes to LRE, the rest of "
                        "the plan runs when the job ends, deletions and moves always ask first.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"lre_rest_of_request_20261115_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='lre-rest-of-request-{level}-', suffix='.log')
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
            results, counts = run_cases(cases, registry, "lre_rest_of_request_20261115", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
