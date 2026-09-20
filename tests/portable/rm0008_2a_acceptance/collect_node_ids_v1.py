"""Collect the two A-only trees without importing them into the caller."""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_A_ROOT = Path(__file__).resolve().parent
_PYTEST_CONFIG = _A_ROOT / "pytest-certification.ini"


def _inside_repository(entry: str) -> bool:
    try:
        Path(entry or os.curdir).resolve().relative_to(_REPO_ROOT)
    except (OSError, ValueError):
        return False
    return True


os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ.pop("PYTEST_ADDOPTS", None)
os.environ.pop("PYTEST_PLUGINS", None)
_original_sys_path = sys.path[:]
try:
    sys.path[:] = [entry for entry in sys.path if not _inside_repository(entry)]
    pytest = importlib.import_module("pytest")
finally:
    sys.path[:] = _original_sys_path
try:
    Path(pytest.__file__).resolve().relative_to(_REPO_ROOT)
except ValueError:
    pass
else:
    raise RuntimeError("pytest resolved inside the repository")
_distribution_pytest = Path(
    importlib.metadata.distribution("pytest").locate_file("pytest/__init__.py")
).resolve()
if Path(pytest.__file__).resolve() != _distribution_pytest:
    raise RuntimeError("pytest did not resolve to the installed distribution")

sys.path.insert(0, str(_A_ROOT))
import certification_v1 as _certification

if Path(_certification.__file__).resolve() != _A_ROOT / "certification_v1.py":
    raise RuntimeError("certification_v1 did not resolve to the A-only package")
_certification.validate_pytest_boundary_configuration(_PYTEST_CONFIG)

sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "runtime"))
sys.path.insert(0, str(_REPO_ROOT / "tests/windows_identity/rm0008_2a_acceptance"))


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
            "-c",
            str(_PYTEST_CONFIG),
            "--rootdir",
            str(_REPO_ROOT),
            "--confcutdir",
            str(_REPO_ROOT),
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
