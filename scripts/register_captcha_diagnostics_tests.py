#!/usr/bin/env python3
"""Register CPU-only CAPTCHA diagnostics and existing login/challenge contracts."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.sites.captcha_diagnostics"
GROUPS = {
    "module": ("tests/runtime/sites/test_captcha_resolution_audit.py",),
    "cluster": (
        # Browser qualification is separate; these are the six existing CPU contracts.
        "tests/runtime/sites/test_login_captcha.py::test_local_solver_once_without_camoufox_patches_or_api",
        "tests/runtime/sites/test_login_captcha.py::test_unresolved_falls_back_once_without_secret_diagnostics",
        "tests/runtime/sites/test_login_captcha.py::test_cancellation_cleans_up_and_cannot_start_another_attempt",
        "tests/runtime/sites/test_login_captcha.py::test_direct_captcha_call_without_session_authority_is_rejected",
        "tests/runtime/sites/test_login_captcha.py::test_broker_respects_existing_page_and_global_budget",
        "tests/runtime/sites/test_login_captcha.py::test_attempt_consumes_one_action_and_shared_time_across_resume",
        "tests/runtime/sites/test_navigation_challenges.py",
        "tests/runtime/sites/test_navigation_challenge_reobserve.py",
        "tests/runtime/sites/test_navigation_challenge_resume.py",
        "tests/runtime/sites/test_authenticated_landing_anchor.py",
    ),
}


def register(path: Path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "sites", "runtime/playwright_sidecar/session_broker.py",
                        "Closed CAPTCHA resolver diagnostics; decisions and budgets unchanged.")
    for group, selectors in GROUPS.items():
        code = (
            "import os, re, subprocess, sys, tempfile\n"
            "from pathlib import Path\n"
            f"log = tempfile.NamedTemporaryFile(prefix='captcha-diagnostics-{group}-', "
            "suffix='.log', delete=False, mode='w+')\n"
            "print('Detailed test log:', log.name, flush=True)\n"
            "result = subprocess.run([os.environ.get('METNOS_TEST_PYTHON', sys.executable), "
            "'-B', '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider', "
            f"*{selectors!r}], cwd=Path(_RT).parent, "
            "env={**os.environ, 'PYTHONPATH': 'runtime:.', "
            "'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, stdout=log, "
            "stderr=subprocess.STDOUT, text=True, timeout=100)\n"
            "log.seek(0)\noutput = log.read()\nlog.close()\n"
            "print(output[-1800:].strip())\n"
            "assert result.returncode == 0, output[-8000:]\n"
            "assert not re.search(r'\\b[1-9][0-9]* skipped\\b', output), output\n"
        )
        registry.add_case(MODULE, f"captcha_diagnostics_20261017_{group}", group,
                          "integration", "python", code)
    return registry


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True,
                        help="Explicit registration DB; use a fresh isolated copy for qualification.")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry = register(args.db)
    try:
        cases = sorted(registry.cases_for_module(MODULE), key=lambda case: case.level != "module")
        print({case.level: case.id for case in cases}, flush=True)
        if args.verify:
            from runner import run_cases
            os.environ.setdefault("METNOS_TEST_PYTHON", str(ROOT / ".venv/bin/python"))
            results, counts = run_cases(cases, registry, "captcha_diagnostics_20261017", verbose=True)
            for result in results:
                print(result.case.level, result.status, result.output, result.failure)
            raise SystemExit(0 if counts == {"pass": len(cases)} else 1)
    finally:
        registry.conn.close()
