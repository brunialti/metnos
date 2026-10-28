#!/usr/bin/env python3
"""Register CPU-only collection navigation routing and existing navigation contracts."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.sites.collection_navigation_routing"
GROUPS = {
    "module": ("tests/runtime/sites/test_collection_navigation_routing.py",),
    "cluster": (
        "tests/runtime/sites/test_collection_navigation_routing.py",
        "tests/runtime/sites/test_goal_navigation_decision_audit.py",
        # Eight real-model opt-in cases in test_collection_navigation.py are
        # deliberately excluded: this registry qualifies CPU routing only.
        "tests/runtime/sites/test_collection_navigation.py",
        "tests/runtime/sites/test_goal_esplorazione.py",
    ),
}


def register(path: Path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "sites", "runtime/playwright_sidecar/session_broker.py",
                        "Collection context in goal fallback; sentinels cannot become clicks.")
    for group, selectors in GROUPS.items():
        code = (
            "import os, re, subprocess, sys, tempfile\n"
            "from pathlib import Path\n"
            f"log = tempfile.NamedTemporaryFile(prefix='collection-navigation-{group}-', "
            "suffix='.log', delete=False, mode='w+')\n"
            "print('Detailed test log:', log.name, flush=True)\n"
            "result = subprocess.run([os.environ.get('METNOS_TEST_PYTHON', sys.executable), "
            "'-B', '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider', "
            f"*{selectors!r}], cwd=Path(_RT).parent, "
            "env={**os.environ, 'PYTHONPATH': 'runtime:.', "
            "'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'METNOS_COLLECTION_LOCAL_MODEL_TEST': '0'}, stdout=log, "
            "stderr=subprocess.STDOUT, text=True, timeout=100)\n"
            "log.seek(0)\noutput = log.read()\nlog.close()\n"
            "print(output[-1800:].strip())\n"
            "assert result.returncode == 0, output[-8000:]\n"
        )
        registry.add_case(MODULE, f"collection_navigation_routing_20261017_{group}", group,
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
            results, counts = run_cases(cases, registry, "collection_navigation_routing_20261017", verbose=True)
            for result in results:
                print(result.case.level, result.status, result.output, result.failure)
            raise SystemExit(0 if counts == {"pass": len(cases)} else 1)
    finally:
        registry.conn.close()
