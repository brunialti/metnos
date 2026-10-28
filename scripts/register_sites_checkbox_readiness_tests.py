#!/usr/bin/env python3
"""Register readiness plus the unchanged CAPTCHA cluster on an explicit test DB."""
from __future__ import annotations

import argparse
from pathlib import Path

import register_sites_record_dispatch_tests as registration

MODULE = "runtime.sites.captcha_checkbox"
TESTS = (
    "tests/runtime/sites/test_captcha_checkbox.py",
    "tests/runtime/sites/test_captcha_crop_guard.py",
    "tests/runtime/sites/test_captcha_checkbox_readiness.py",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.db.resolve() == (registration.ROOT / "runtime/testing/tests.db").resolve():
        parser.error("readiness qualification requires an isolated test DB")
    registration.CASE_PREFIX = "sites_checkbox_readiness_20261018"
    registration.MODULES = {MODULE: {
        "source": "runtime/playwright_sidecar/captcha_checkbox.py",
        "description": "Bounded zero-only readiness; exact checkbox, frame, origin and command authority.",
        "groups": {"module": TESTS, "cluster": TESTS + (
            "tests/runtime/sites/test_login_captcha.py",
            "tests/runtime/sites/test_login_captcha_dispatch.py",
            "tests/runtime/sites/test_captcha_resolution_audit.py",
            "tests/runtime/infra/test_agentic_executor.py")},
    }}
    registry = registration.register(args.db)
    try:
        cases = sorted(registry.cases_for_module(MODULE), key=lambda case: case.level != "module")
        assert len(cases) == 2
        print({case.level: case.id for case in cases}, flush=True)
        if args.verify:
            from runner import run_cases
            results, counts = run_cases(cases, registry, registration.CASE_PREFIX, verbose=True)
            for result in results:
                print(result.case.level, result.status, result.output, result.failure)
            return 0 if counts == {"pass": 2} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
