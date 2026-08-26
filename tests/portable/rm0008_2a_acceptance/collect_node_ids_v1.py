"""Collect the two A-only trees without importing them into the caller."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "runtime"))
sys.path.insert(0, str(_REPO_ROOT / "tests/windows_identity/rm0008_2a_acceptance"))
import pytest


class _Collector:
    def __init__(self) -> None:
        self.node_ids: list[str] = []
        self.collection_failed = False

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.node_ids = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.collection_failed = True


def main(arguments: list[str]) -> int:
    collector = _Collector()
    exit_code = pytest.main(
        [
            "--collect-only",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:terminal",
            "--import-mode=importlib",
            *arguments,
        ],
        plugins=[collector],
    )
    if exit_code != pytest.ExitCode.OK or collector.collection_failed:
        sys.stderr.write(
            f"pytest collection failed (exit={int(exit_code)}, "
            f"collect_failed={collector.collection_failed})\n"
        )
        return int(exit_code) or 2
    ordered = sorted(set(collector.node_ids), key=lambda item: item.encode("utf-8"))
    if len(ordered) != len(collector.node_ids):
        sys.stderr.write("pytest collection returned duplicate node ids\n")
        return 3
    sys.stdout.write(json.dumps(ordered, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
