#!/usr/bin/env python3
"""Register CPU-only CAPTCHA dispatch and site record-navigation contracts.

Registration upserts only the two modules and four named cases below. Existing
modules, cases, run history and unrelated DB contents are preserved. Browser and
real-model opt-in cases remain excluded from this CPU qualification.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CASE_PREFIX = "sites_record_dispatch_20261017"
LOGIN_TESTS = (
    "tests/runtime/sites/test_login_captcha_dispatch.py",
    "tests/runtime/sites/test_captcha_resolution_audit.py",
)
RECORD_TESTS = (
    "tests/runtime/sites/test_record_navigation_contract.py",
    "tests/runtime/sites/test_record_navigation_contract_adversarial.py",
)
MODULES = {
    "runtime.sites.login_captcha_dispatch": {
        "source": "runtime/playwright_sidecar/credential_injection.py",
        "description": (
            "Dispatch a uniquely observed supported CAPTCHA despite form text; "
            "preserve login authority, budgets and independent acceptance checks."
        ),
        "groups": {
            "module": LOGIN_TESTS,
            "cluster": LOGIN_TESTS + (
                "tests/runtime/sites/test_login_captcha.py",
                "tests/runtime/sites/test_manual_login_observation_budget.py",
                "tests/runtime/sites/test_login_document_wait_budget.py",
                "tests/runtime/sites/test_login_post_submit_stability.py",
                "tests/runtime/sites/test_login_post_submit_audit.py",
                "tests/runtime/sites/test_login_choice_audit.py",
                "tests/runtime/sites/test_navigation_challenges.py",
                "tests/runtime/sites/test_navigation_challenge_reobserve.py",
                "tests/runtime/sites/test_navigation_challenge_resume.py",
            ),
        },
    },
    "runtime.sites.record_navigation_contract": {
        "source": "runtime/engine/dispatch.py",
        "description": (
            "Bind observed record consumers to their site action without changing "
            "the natural goal or granting authority across unrelated branches."
        ),
        "groups": {
            "module": RECORD_TESTS,
            "cluster": RECORD_TESTS + (
                "tests/runtime/sites/test_collection_navigation_routing.py",
                "tests/runtime/sites/test_goal_navigation_decision_audit.py",
                "tests/runtime/sites/test_collection_navigation.py",
                "tests/runtime/sites/test_goal_esplorazione.py",
                # Only the declared pipeline order and guard metadata belong to
                # this change. Unrelated full-chain corpus behavior is not part
                # of this contract's qualification.
                "tests/runtime/engine/test_guard_pipeline_contract.py::test_guard_pipeline_order_contract",
                "tests/runtime/engine/test_guard_pipeline_contract.py::test_all_guards_have_metadata",
            ),
        },
    },
}


def _case_name(module: str, group: str) -> str:
    return f"{CASE_PREFIX}_{module.rsplit('.', 1)[-1]}_{group}"


def _test_code(module: str, group: str, selectors: tuple[str, ...]) -> str:
    prefix = f"{module.rsplit('.', 1)[-1]}-{group}-"
    return (
        "import os, subprocess, sys, tempfile\n"
        "from pathlib import Path\n"
        f"log = tempfile.NamedTemporaryFile(prefix={prefix!r}, "
        "suffix='.log', delete=False, mode='w+')\n"
        "print('Detailed test log:', log.name, flush=True)\n"
        "result = subprocess.run([os.environ.get('METNOS_TEST_PYTHON', sys.executable), "
        "'-B', '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider', "
        f"*{selectors!r}], cwd=Path(_RT).parent, "
        "env={**os.environ, 'PYTHONPATH': 'runtime:.', "
        "'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'METNOS_SITES_SIM': '0', "
        "'METNOS_COLLECTION_LOCAL_MODEL_TEST': '0'}, stdout=log, "
        "stderr=subprocess.STDOUT, text=True, timeout=100)\n"
        "log.seek(0)\noutput = log.read()\nlog.close()\n"
        "print(output[-1800:].strip())\n"
        "assert result.returncode == 0, output[-8000:]\n"
    )


def register(path: Path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    for module, spec in MODULES.items():
        registry.add_module(module, "sites", spec["source"], spec["description"])
        for group, selectors in spec["groups"].items():
            registry.add_case(
                module, _case_name(module, group), group, "integration", "python",
                _test_code(module, group, selectors),
            )
    return registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=Path, required=True,
        help="Explicit registration DB; use a fresh isolated copy for qualification.",
    )
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry = register(args.db)
    try:
        cases = []
        for module, spec in MODULES.items():
            names = {_case_name(module, group) for group in spec["groups"]}
            selected = sorted(
                (case for case in registry.cases_for_module(module) if case.name in names),
                key=lambda case: case.level != "module",
            )
            cases.extend(selected)
            print(module, {case.level: case.id for case in selected}, flush=True)
        if args.verify:
            from runner import run_cases

            os.environ.setdefault("METNOS_TEST_PYTHON", str(ROOT / ".venv/bin/python"))
            results, counts = run_cases(cases, registry, CASE_PREFIX, verbose=True)
            for result in results:
                print(result.case.module_name, result.case.level, result.status,
                      result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
