#!/usr/bin/env python3
"""Register CPU-only workload attribution and its surrounding transport tests."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.llm.attribution"
GROUPS = {
    "module": (
        "tests/runtime/infra/test_monitor_workload_attribution.py",
        "tests/runtime/infra/test_llm_invocation.py",
    ),
    "cluster": (
        "tests/runtime/infra/test_llm_router_tier_policy.py",
        "tests/runtime/infra/test_llm_provider_selection.py",
        "tests/runtime/infra/test_llm_telemetry_tier.py",
        "tests/runtime/infra/test_llm_six_tier_contracts.py",
        "tests/runtime/infra/test_monitor_capture.py",
        "tests/runtime/infra/test_monitor_boundaries.py",
        "tests/runtime/infra/test_monitor_browser_transport.py",
        "tests/runtime/infra/test_monitor_browser_late.py",
        "tests/runtime/infra/test_monitor_transport_limits.py",
    ),
}


def register(path: Path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "runtime", "runtime/llm_invocation.py",
                        "Isolated workload identity and closed child-origin metadata.")
    for group, files in GROUPS.items():
        code = (
            "import os, re, subprocess, sys, tempfile\n"
            "from pathlib import Path\n"
            f"log = tempfile.NamedTemporaryFile(prefix='monitor-attribution-{group}-', "
            "suffix='.log', delete=False, mode='w+')\n"
            "print('Detailed test log:', log.name, flush=True)\n"
            "result = subprocess.run([os.environ.get('METNOS_TEST_PYTHON', sys.executable), "
            "'-B', '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider', "
            f"*{files!r}], cwd=Path(_RT).parent, "
            "env={**os.environ, 'PYTHONPATH': 'runtime:.', "
            "'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, stdout=log, "
            "stderr=subprocess.STDOUT, text=True, timeout=100)\n"
            "log.seek(0)\noutput = log.read()\nlog.close()\n"
            "print(output[-1800:].strip())\n"
            "assert result.returncode == 0, output[-8000:]\n"
            "assert not re.search(r'\\b[1-9][0-9]* skipped\\b', output), output\n"
        )
        registry.add_case(MODULE, f"workload_origin_20261017_{group}", group,
                          "integration", "python", code)
    return registry


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True,
                        help="Explicit registration DB; use an isolated copy for qualification.")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry = register(args.db)
    try:
        cases = sorted(registry.cases_for_module(MODULE), key=lambda case: case.level != "module")
        print({case.level: case.id for case in cases}, flush=True)
        if args.verify:
            from runner import run_cases
            os.environ.setdefault("METNOS_TEST_PYTHON", str(ROOT / ".venv/bin/python"))
            results, counts = run_cases(cases, registry, "monitor_attribution_20261017", verbose=True)
            for result in results:
                print(result.case.level, result.status, result.output, result.failure)
            raise SystemExit(0 if counts == {"pass": len(cases)} else 1)
    finally:
        registry.conn.close()
