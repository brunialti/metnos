#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register the LRE console module and its existing command boundaries."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.durable_workloads.console"


def register(path: Path) -> list[int]:
    sys.path.insert(0, str(ROOT / "runtime/testing"))
    from registry import Registry

    registry = Registry.open(path)
    try:
        registry.add_module(MODULE, "runtime", "runtime/http_routes_durable_workloads.py",
                            "LRE console selection, detail and log dismissal")
        for dependency in ("durable_workloads.service", "durable_workloads.temporary_storage"):
            registry.add_dependency(MODULE, dependency)
        for name, test_file in (
            ("log_selection_browser_it_en", "tests/runtime/http/test_durable_console_behavior.py"),
            ("owner_scoped_commands", "tests/runtime/http/test_durable_workloads_api.py"),
        ):
            code = (
                "import os, subprocess, sys\n"
                "from pathlib import Path\n"
                "result = subprocess.run([sys.executable, '-m', 'pytest', "
                f"{test_file!r}, '-q'], cwd=Path(_RT).parent, "
                "env={**os.environ, 'PYTHONPATH': 'runtime:.'}, capture_output=True, text=True)\n"
                "assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-2000:]\n"
                "print(result.stdout.strip())\n"
            )
            registry.add_case(MODULE, name, "module", "integration", "python", code)
        return [case.id for case in registry.cases_for_module(MODULE)]
    finally:
        registry.conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/testing/tests.db")
    args = parser.parse_args()
    print(f"{MODULE}: cases {register(args.db)}")
