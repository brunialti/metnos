#!/usr/bin/env python3
"""Esegue test_runner su tutti i manifest in <install_root>/executors/."""
import subprocess
import sys
from pathlib import Path

EXECUTORS_DIR = Path(__file__).resolve().parents[1] / "executors"
TEST_RUNNER = Path(__file__).parent / "test_runner.py"


def main():
    manifests = sorted(EXECUTORS_DIR.glob("*/manifest.toml"))
    total_pass = total_fail = 0
    for m in manifests:
        print(f"\n>>> {m.parent.name}")
        result = subprocess.run(["python3", str(TEST_RUNNER), str(m)], capture_output=True, text=True)
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        # Parse "X/Y passati" dalla coda dello stdout
        import re
        for line in result.stdout.splitlines():
            mt = re.search(r"(\d+)/(\d+)\s+passati", line)
            if mt:
                p, t = int(mt.group(1)), int(mt.group(2))
                total_pass += p; total_fail += t - p
                break
    print(f"\n=================================")
    print(f" SUMMARY: {total_pass}/{total_pass+total_fail} test passati su {len(manifests)} executor")
    print(f"=================================")
    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
