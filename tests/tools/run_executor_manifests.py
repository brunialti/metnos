#!/usr/bin/env python3
"""Esegue test_runner su tutti i manifest in <install_root>/executors/."""
import subprocess
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXECUTORS_DIR = REPO_ROOT / "executors"
TEST_RUNNER = REPO_ROOT / "runtime" / "test_runner.py"


def main():
    manifests = sorted(EXECUTORS_DIR.glob("*/manifest.toml"))
    total_pass = total_fail = 0
    failed_executors: list[tuple[str, int]] = []
    with tempfile.TemporaryDirectory(prefix="metnos-manifest-suite-") as raw:
        root = Path(raw)
        env = os.environ.copy()
        installed_data = Path(
            env.get("METNOS_USER_DATA")
            or (Path.home() / ".local" / "share" / "metnos")
        )
        env.update({
            "METNOS_USER_DATA": str(root / "data"),
            "METNOS_USER_STATE": str(root / "state"),
            "METNOS_INDEX_ROOT": str(root / "data" / "index"),
            "METNOS_HISTORY_DIR": str(root / "data" / "_history"),
        })
        for path in (root / "data", root / "state"):
            path.mkdir(parents=True, exist_ok=True)
        # Error strings and detection concepts are immutable seed inputs for
        # the birth tests. Copy only those two stores; never expose the rest of
        # the user's mutable data to an executor under test.
        for name in ("i18n.sqlite", "detection.sqlite"):
            source = installed_data / name
            if source.is_file():
                shutil.copy2(source, root / "data" / name)
        for m in manifests:
            print(f"\n>>> {m.parent.name}")
            result = subprocess.run(
                ["python3", str(TEST_RUNNER), str(m)],
                capture_output=True, text=True, env=env,
            )
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
                    if p != t:
                        failed_executors.append((m.parent.name, t - p))
                    break
    print(f"\n=================================")
    print(f" SUMMARY: {total_pass}/{total_pass+total_fail} test passati su {len(manifests)} executor")
    if failed_executors:
        rendered = ", ".join(
            f"{name}({count})" for name, count in failed_executors)
        print(f" FAILING EXECUTORS: {rendered}")
    print(f"=================================")
    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
