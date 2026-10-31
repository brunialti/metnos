#!/usr/bin/env python3
"""Register and run the offline logical/encrypted backup recovery regressions."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.testing.registry import DEFAULT_DB, Registry
from runtime.testing.runner import run_cluster, run_module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    registry = Registry.open(args.db)
    try:
        registry.add_module("backup_logical", "runtime", "deploy/backup_logical.py",
                            "Logical SQLite recovery, NAS rotation and public-key encryption")
        registry.add_case(
            "backup_logical", "logical_and_encrypted_recovery", "module", "integration", "python",
            "import subprocess, sys\nfrom pathlib import Path\n"
            "result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', "
            "'-s', 'tests/internal', '-p', 'test_backup_logical.py', '-q'], "
            "cwd=Path(_RT).parent, capture_output=True, text=True, timeout=100)\n"
            "print(result.stdout)\nprint(result.stderr)\nassert result.returncode == 0",
        )
        # This administrative producer uses the standard library and system
        # SQLite/GnuPG commands; it does not import application runtime modules.
        for scope, runner in (("module", run_module), ("cluster", run_cluster)):
            results, counts = runner("backup_logical", registry)
            print(scope, counts)
            for result in results:
                if result.status != "pass":
                    print(result.output, result.failure)
            assert results and all(result.status == "pass" for result in results)
    finally:
        registry.conn.close()


if __name__ == "__main__":
    main()
