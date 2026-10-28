#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register model-readiness explanations and their execution/control boundaries."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.durable_workloads.console"
GROUPS = {
    "module": ("tests/runtime/durable_workloads/test_resource_readiness.py",
               "tests/runtime/http/test_durable_console_behavior.py",
               "tests/runtime/remote/test_device_shim_i18n.py"),
    "cluster": ("tests/runtime/durable_workloads/test_execution_bridge.py",
                "tests/runtime/durable_workloads/test_domain_outcome.py",
                "tests/runtime/durable_workloads/test_control.py",
                "tests/runtime/durable_workloads/test_durable_workload_storage.py",
                "tests/runtime/http/test_durable_workloads_api.py"),
}


def register(path: Path) -> dict[str, int]:
    sys.path.insert(0, str(ROOT / "runtime/testing"))
    from registry import Registry

    registry = Registry.open(path)
    try:
        cases = {}
        for group, files in GROUPS.items():
            name = f"model_resource_message_20261016_{group}"
            code = (
                "import os, re, subprocess, sys\n"
                "from pathlib import Path\n"
                "result = subprocess.run([os.environ.get('METNOS_TEST_PYTHON', sys.executable), "
                "'-B', '-m', 'pytest', '-q', '-x', '--tb=short', '-p', 'no:cacheprovider', "
                f"*{files!r}], cwd=Path(_RT).parent, "
                "env={**os.environ, 'PYTHONPATH': 'runtime:.'}, capture_output=True, text=True)\n"
                "print(result.stdout[-800:].strip())\n"
                "assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-2000:]\n"
                "assert not re.search(r'\\b[1-9][0-9]* skipped\\b', result.stdout), result.stdout\n"
            )
            registry.add_case(MODULE, name, group, "integration", "python", code)
            cases[group] = next(case.id for case in registry.cases_for_module(MODULE)
                                if case.name == name)
        return cases
    finally:
        registry.conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/testing/tests.db")
    args = parser.parse_args()
    print(register(args.db))
