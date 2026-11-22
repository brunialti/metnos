#!/usr/bin/env python3
"""Register B2 order, B3 conditional clause arguments and measure diagnostics."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "module": ("tests/runtime/engine/test_s017_dependency_order.py",
               "tests/runtime/test_planning_measure_catalog.py"),
    "cluster": ("tests/runtime/engine/test_s017_action_provenance.py",
                "tests/runtime/engine/test_guard_pipeline_contract.py",
                "tests/runtime/engine/test_guard_idempotence.py",
                "tests/runtime/engine/test_ensure_extract_clause.py",
                "tests/runtime/engine/test_guardia_conserva_gli_args.py",
                "tests/runtime/engine/test_temporal_clause_binding.py",
                "tests/runtime/engine/test_partition_plans.py",
                "tests/runtime/engine/test_partition_destinations.py",
                "tests/runtime/test_planning_measure.py",
                "tests/runtime/test_planning_measure_http.py",
                "tests/runtime/test_planning_measure_ports.py",
                "tests/runtime/test_planning_measure_client.py"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry
    registry = Registry.open(args.db)
    module = "runtime.s017_dependency_order"
    try:
        registry.add_module(module, "runtime", "runtime/engine/dispatch.py", __doc__)
        for level, paths in GROUPS.items():
            code = f'''import os, subprocess, sys, tempfile
from pathlib import Path
with tempfile.TemporaryFile(mode="w+") as log:
    result = subprocess.run([os.environ.get("METNOS_TEST_PYTHON", sys.executable), "-B",
        "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", *{paths!r}],
        cwd=Path(_RT).parent, env={{**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}},
        stdout=log, stderr=subprocess.STDOUT, text=True, timeout=240)
    log.seek(0)
    output = log.read()
print(output[-4000:])
assert result.returncode == 0, output[-8000:]
'''
            registry.add_case(module, f"s017_dependency_order_{level}", level,
                              "integration", "python", code)
    finally:
        registry.conn.close()


if __name__ == "__main__":
    main()
