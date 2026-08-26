"""Run exactly one manifest activity and emit its canonical evidence."""
from __future__ import annotations

import argparse
import os
import platform
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "runtime"))
sys.path.insert(0, str(_REPO_ROOT / "tests/windows_identity/rm0008_2a_acceptance"))
import pytest

from certification_v1 import (
    CertificationError,
    INVENTORY_PATH,
    MANIFEST_PATH,
    SUITE_ID,
    digest_file,
    git_sha,
    select_cells,
    validate_manifest,
    validate_production_inventory,
    write_canonical_json,
)


class _EvidenceRecorder:
    def __init__(self) -> None:
        self.collected: list[str] = []
        self.calls: dict[str, list[str]] = {}
        self.non_call_failures: list[str] = []
        self.expected_results: list[str] = []
        self.collection_failed = False
        self.deselected: list[str] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collected = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.collection_failed = True

    def pytest_deselected(self, items: list[pytest.Item]) -> None:
        self.deselected.extend(item.nodeid for item in items)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if getattr(report, "wasxfail", None) is not None:
            self.expected_results.append(report.nodeid)
        if report.when == "call":
            self.calls.setdefault(report.nodeid, []).append(report.outcome)
        elif report.failed or report.skipped:
            self.non_call_failures.append(f"{report.nodeid}:{report.when}:{report.outcome}")


def _runner_image() -> str:
    value = os.environ.get("ImageOS") or os.environ.get("RUNNER_OS")
    if value:
        return value
    return platform.platform()


def run_activity(
    activity: str, mode: str, evidence_path: Path, import_mode: str = "importlib"
) -> None:
    if import_mode != "importlib":
        raise CertificationError("the two A-only packages require pytest importlib mode")
    manifest = validate_manifest()
    validate_production_inventory(enforce_filesystem=False)
    cells = select_cells(manifest, activity)
    expected_nodes = [cell["node_id"] for cell in cells]
    recorder = _EvidenceRecorder()
    exit_code = pytest.main(
        [
            "-q",
            "-p",
            "no:cacheprovider",
            "--strict-markers",
            f"--import-mode={import_mode}",
            *expected_nodes,
        ],
        plugins=[recorder],
    )
    if recorder.collection_failed:
        raise CertificationError("activity has a pytest collection failure")
    if recorder.deselected:
        raise CertificationError("activity deselected one or more owned cells")
    if recorder.collected != expected_nodes:
        raise CertificationError(
            f"activity collection order mismatch: {recorder.collected!r}"
        )
    if recorder.non_call_failures:
        raise CertificationError(
            "activity has setup/teardown failures or skips: "
            + ", ".join(recorder.non_call_failures)
        )
    if recorder.expected_results:
        raise CertificationError("activity contains xfail/xpass results")
    outcomes: list[str] = []
    for node_id in expected_nodes:
        reports = recorder.calls.get(node_id, [])
        if len(reports) != 1 or reports[0] not in {"passed", "failed"}:
            raise CertificationError(
                f"cell must emit one passed/failed call report: {node_id}: {reports!r}"
            )
        outcomes.append(reports[0])
    if mode == "final":
        if exit_code != pytest.ExitCode.OK or any(outcome != "passed" for outcome in outcomes):
            raise CertificationError("final activity contains a non-pass outcome")
        results: list[dict[str, Any]] = [
            {"node_id": cell["node_id"], "outcome": outcome}
            for cell, outcome in zip(cells, outcomes, strict=True)
        ]
    else:
        if exit_code not in {pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED}:
            raise CertificationError(f"snapshot pytest exit is not admissible: {exit_code}")
        results = []
        for cell, outcome in zip(cells, outcomes, strict=True):
            disposition = cell["pre_fix_disposition"]
            if disposition in {"red", "absent"} and outcome != "failed":
                raise CertificationError(
                    f"pre-fix disposition did not fail: {cell['node_id']}"
                )
            results.append(
                {
                    "node_id": cell["node_id"],
                    "declared_disposition": disposition,
                    "observed_outcome": outcome,
                }
            )
    evidence = {
        "schema_version": 1,
        "suite_id": SUITE_ID,
        "git_sha": git_sha(),
        "manifest_sha256": digest_file(MANIFEST_PATH),
        "production_inventory_sha256": digest_file(INVENTORY_PATH),
        "runner_image": _runner_image(),
        "activity": activity,
        "results": results,
    }
    write_canonical_json(evidence_path, evidence)


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--activity", required=True)
    parser.add_argument("--mode", choices=("final", "snapshot"), required=True)
    parser.add_argument("--import-mode", choices=("importlib",), required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        run_activity(
            options.activity,
            options.mode,
            options.evidence,
            options.import_mode,
        )
    except CertificationError as exc:
        print(f"RM-0008 2A activity failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
