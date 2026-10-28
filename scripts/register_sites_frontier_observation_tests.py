#!/usr/bin/env python3
"""Register scoped Sites model selection and its surrounding CPU cluster."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.sites.collection_observe"
GROUPS = {
    "module": (
        "tests/runtime/sites/test_collection_frontier_observation.py",
        "tests/runtime/infra/test_llm_six_tier_contracts.py",
    ),
    "cluster": (
        "tests/runtime/sites/test_collection_navigation.py",
        "tests/runtime/sites/test_collection_frontier_route.py",
        "tests/runtime/sites/test_collection_route_overlap.py",
        "tests/runtime/sites/test_collection_observation_bounds.py",
        "tests/runtime/sites/test_collection_observation_preservation.py",
        "tests/runtime/sites/test_collection_model_timeout.py",
        "tests/runtime/sites/test_collection_detail_leaf.py",
        "tests/runtime/sites/test_collection_record_views_integrity.py",
        "tests/runtime/sites/test_login_navigation.py",
        "tests/runtime/test_published_docs.py",
    ),
}


def register(path: Path):
    sys.path.insert(0, str(ROOT / "runtime/testing"))
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/playwright_sidecar/session_broker.py",
                        "Frontier observes site collections; other model workloads stay unchanged.")
    for group, files in GROUPS.items():
        code = (
            "import os, re, subprocess, sys, tempfile\n"
            "from pathlib import Path\n"
            f"log = tempfile.NamedTemporaryFile(prefix='sites-frontier-{group}-', suffix='.log', delete=False, mode='w+')\n"
            "print('Detailed test log:', log.name, flush=True)\n"
            "result = subprocess.run([os.environ.get('METNOS_TEST_PYTHON', sys.executable), "
            "'-B', '-m', 'pytest', '-v', '--tb=short', '-p', 'no:cacheprovider', "
            "'-o', 'faulthandler_timeout=25', "
            f"*{files!r}, '-m', 'not real_llm', "
            "'--deselect', 'tests/runtime/sites/test_collection_navigation.py::test_installed_local_model_keeps_object_filter_and_continuation_separate'], cwd=Path(_RT).parent, "
            "env={**os.environ, 'PYTHONPATH': 'runtime:.'}, stdout=log, stderr=subprocess.STDOUT, "
            f"text=True, timeout={180 if group == 'cluster' else 90})\n"
            "log.seek(0)\n"
            "output = log.read()\n"
            "log.close()\n"
            "print(output[-1200:].strip())\n"
            "assert result.returncode == 0, output[-8000:]\n"
            "assert not re.search(r'\\b[1-9][0-9]* skipped\\b', output), output\n"
        )
        registry.add_case(MODULE, f"scoped_frontier_20261016_{group}", group,
                          "integration", "python", code)
    return registry


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/testing/tests.db")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry = register(args.db)
    cases = registry.cases_for_module(MODULE)
    print({case.level: case.id for case in cases}, flush=True)
    try:
        if args.verify:
            from runner import run_cases
            os.environ.setdefault("METNOS_TEST_PYTHON", str(ROOT / ".venv/bin/python"))
            cases.sort(key=lambda case: case.level != "module")
            results, counts = run_cases(cases, registry, "sites_frontier_20261016", verbose=True)
            for result in results:
                print(result.case.level, result.status, result.output, result.failure)
            raise SystemExit(0 if counts == {"pass": len(cases)} else 1)
    finally:
        registry.conn.close()
