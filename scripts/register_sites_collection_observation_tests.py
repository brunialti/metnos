#!/usr/bin/env python3
"""Register shared Sites context/content and bounded partition regressions.

Requires an explicit registry path. Synthetic pages, replies and browser routes
exercise the candidate without a website account or a provider call.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.sites.collection_observation_contract"
CASE_PREFIX = "sites_collection_observation_20261018"
MODULE_TESTS = (
    "tests/runtime/sites/test_collection_native_snapshots.py",
    "tests/runtime/sites/test_collection_observation_contract.py",
    "tests/runtime/sites/test_collection_observation_provider.py",
    "tests/runtime/sites/test_collection_semantic_context.py",
    "tests/runtime/sites/test_collection_semantic_context_browser.py",
    "tests/runtime/sites/test_shared_collection_observer_browser.py",
    "tests/runtime/sites/test_collection_partition_browser.py",
)
GROUPS = {
    "module": MODULE_TESTS,
    "cluster": MODULE_TESTS + (
        "tests/runtime/sites/test_login_navigation.py",
        "tests/runtime/sites/test_collection_record_views_integrity.py",
        "tests/runtime/sites/test_collection_record_binding_browser.py",
        "tests/runtime/sites/test_collection_producer_enrichment.py",
        "tests/runtime/sites/test_collection_year_cutoff_browser.py",
        "tests/runtime/sites/test_collection_context.py",
    ),
}


def _test_code(group: str, selectors: tuple[str, ...]) -> str:
    return (
        "import os, re, subprocess, sys, tempfile\n"
        "from pathlib import Path\n"
        f"log = tempfile.NamedTemporaryFile(prefix='sites-observation-{group}-', suffix='.log', delete=False, mode='w+')\n"
        "print('Detailed test log:', log.name, flush=True)\n"
        "root = Path(_RT).parent\n"
        "result = subprocess.run([os.environ.get('METNOS_TEST_PYTHON', sys.executable), "
        "'-B', '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider', "
        f"*{selectors!r}], cwd=root, "
        "env={**os.environ, 'PYTHONPATH': 'runtime:.', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', "
        "'METNOS_SITES_SIM': '1', 'METNOS_COLLECTION_LOCAL_MODEL_TEST': '0', "
        "'METNOS_INSTALL_ROOT': str(root), "
        "'PLAYWRIGHT_BROWSERS_PATH': os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or str(Path.home() / '.cache/ms-playwright')}, "
        "stdout=log, stderr=subprocess.STDOUT, text=True, timeout=180)\n"
        "log.seek(0)\noutput = log.read()\nlog.close()\n"
        "print(output[-2200:].strip())\n"
        "assert result.returncode == 0, output[-8000:]\n"
        "assert not re.search(r'\\b[1-9][0-9]* skipped\\b', output), output\n"
    )


def register(path: Path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "sites", "runtime/playwright_sidecar/session_broker.py",
        "One grounded collection observer; bounded record partitions preserve evidence and scope.")
    for group, selectors in GROUPS.items():
        registry.add_case(MODULE, f"{CASE_PREFIX}_{group}", group, "integration", "python",
                          _test_code(group, selectors))
    return registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True,
                        help="Explicit registry; qualify on a fresh isolated copy.")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--level", choices=("all", "module", "cluster"), default="all",
                        help="Run only the needed level; registration preserves both cases.")
    args = parser.parse_args()
    registry = register(args.db)
    try:
        names = {f"{CASE_PREFIX}_{group}" for group in GROUPS}
        cases = sorted((c for c in registry.cases_for_module(MODULE) if c.name in names and (args.level == "all" or c.level == args.level)),
                       key=lambda c: c.level != "module")
        print({c.level: c.id for c in cases}, flush=True)
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
