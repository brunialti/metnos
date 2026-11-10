#!/usr/bin/env python3
"""Register and run the tests of the RM-0008/0009/0010 review: sandbox secrets, affinity rule on the whole catalog, proposals and lifecycle (10/11/2026)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.rm_review_20261110"
GROUPS = {
    "module": (
        "tests/runtime/infra/test_sandbox_secret_masks.py",
        "tests/runtime/http/test_change_row_escaping.py",
        "tests/runtime/learning/test_change_intents.py",
        "tests/runtime/learning/test_change_applier.py",
        "tests/runtime/learning/test_affinity_integrity.py",
        "tests/runtime/executors/test_executor_lifecycle_state.py",
    ),
    "cluster": (
        "tests/runtime/learning/test_learning_loop.py",
        "tests/runtime/learning/test_change_intents_ui.py",
        "tests/runtime/learning/test_synth_admission_affinity.py",
        "tests/runtime/learning/test_synth_candidate_admission.py",
        "tests/runtime/learning/test_vocabulary_dossier.py",
        "tests/runtime/skills/test_skill_admission_smoke_case.py",
        "tests/runtime/infra/test_turn_feedback.py",
        "tests/runtime/infra/test_runtime_settings.py",
        "tests/runtime/i18n/test_detection_lexicon_codegen_consumers.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "learning", "runtime/sandbox.py",
                        "Review of 10/11/2026: private configuration masked in the sandbox, one affinity rule "
                        "for every executor, one proposal per capability, restrictions tied to the revision.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"rm_review_20261110_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='rm-review-{level}-', suffix='.log')
            os.close(fd)
            log = Path(name)
            with log.open('w') as output:
                result = subprocess.run([
                    os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                    '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
                    *{selectors!r}], cwd=root,
                    env={{**os.environ, 'PYTHONPATH': 'runtime:.'}},
                    stdout=output, stderr=subprocess.STDOUT, text=True, timeout=180)
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
            results, counts = run_cases(cases, registry, "rm_review_20261110", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
