#!/usr/bin/env python3
"""Register record-consumer routing and evidence preservation regressions.

Only two named cases are added. Historical registry rows are preserved.
Synthetic browser tests use intercepted pages, without accounts or cloud.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CASE_PREFIX = "sites_record_collection_20261017"
MODULES = {
    "runtime.sites.record_collection_routing": {
        "source": "runtime/playwright_sidecar/session_broker.py",
        "description": "Record consumers save observations without an explicit all quantifier; preserve filters and partial evidence.",
        "groups": {
            "module": (
                "tests/runtime/sites/test_record_collection_routing_dispatch.py",
                "tests/runtime/sites/test_record_collection_routing_browser.py",
            ),
            "cluster": (
                "tests/runtime/sites/test_record_collection_routing_dispatch.py",
                "tests/runtime/sites/test_record_collection_routing_browser.py",
                "tests/runtime/sites/test_record_navigation_contract.py",
                "tests/runtime/sites/test_record_navigation_contract_adversarial.py",
                "tests/runtime/sites/test_sites_record_dispatch_browser.py",
                "tests/runtime/sites/test_collection_navigation.py",
                "tests/runtime/sites/test_collection_navigation_routing.py",
                "tests/runtime/sites/test_collection_observation_preservation.py",
                "tests/runtime/sites/test_collection_source_metadata.py",
                "tests/runtime/sites/test_collection_presentation.py",
            ),
        },
    },
}


def _case_name(module: str, group: str) -> str:
    return f"{CASE_PREFIX}_{module.rsplit('.', 1)[-1]}_{group}"


def _test_code(module: str, group: str, selectors: tuple[str, ...]) -> str:
    prefix = f"{module.rsplit('.', 1)[-1]}-{group}-"
    browser = any(path.endswith("_browser.py") for path in selectors)
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
        f"'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'METNOS_SITES_SIM': {str(int(browser))!r}, "
        "'METNOS_COLLECTION_LOCAL_MODEL_TEST': '0', "
        "'PLAYWRIGHT_BROWSERS_PATH': os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or str(Path.home() / '.cache/ms-playwright')}, stdout=log, "
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

            os.environ.setdefault("METNOS_TEST_PYTHON", sys.executable)
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
