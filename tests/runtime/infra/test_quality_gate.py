"""Deterministic tests for the E2E quality objective."""
from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests" / "e2e"))

from quality_gate import evaluate  # noqa: E402


TARGETS = {
    "required_consecutive_clean_runs": 2,
    "min_collected_tests": 2,
    "min_execution_coverage": 0.95,
    "min_success_rate": 1.0,
    "max_failures": 0,
    "max_errors": 0,
    "require_stable_suite": True,
}


def _junit(path: Path, outcomes: list[str]) -> Path:
    cases = []
    for index, outcome in enumerate(outcomes):
        child = ""
        if outcome == "failure":
            child = '<failure message="failed" />'
        elif outcome == "error":
            child = '<error message="error" />'
        elif outcome == "skipped":
            child = '<skipped message="not applicable" />'
        cases.append(
            f'<testcase classname="suite" name="case_{index}" time="0.1">'
            f"{child}</testcase>"
        )
    path.write_text(
        '<testsuite name="suite">' + "".join(cases) + "</testsuite>",
        encoding="utf-8",
    )
    return path


def test_two_identical_clean_runs_reach_objective(tmp_path):
    first = _junit(tmp_path / "first.xml", ["passed", "passed"])
    second = _junit(tmp_path / "second.xml", ["passed", "passed"])
    report = evaluate([first, second], TARGETS)
    assert report["objective_achieved"] is True
    assert report["consecutive_clean_runs"] == 2


def test_failure_resets_clean_streak(tmp_path):
    first = _junit(tmp_path / "first.xml", ["passed", "passed"])
    second = _junit(tmp_path / "second.xml", ["passed", "failure"])
    report = evaluate([first, second], TARGETS)
    assert report["objective_achieved"] is False
    assert report["consecutive_clean_runs"] == 0


def test_execution_coverage_is_enforced(tmp_path):
    outcomes = ["passed"] + ["skipped"] * 19
    first = _junit(tmp_path / "first.xml", outcomes)
    second = _junit(tmp_path / "second.xml", outcomes)
    report = evaluate([first, second], TARGETS)
    assert report["runs"][0]["execution_coverage"] == 0.05
    assert report["objective_achieved"] is False


def test_different_executed_matrix_is_not_stable(tmp_path):
    first = _junit(tmp_path / "first.xml", ["passed", "skipped"])
    second = _junit(tmp_path / "second.xml", ["skipped", "passed"])
    targets = dict(TARGETS, min_execution_coverage=0.5)
    report = evaluate([first, second], targets)
    assert report["suite_stable"] is False
    assert report["objective_achieved"] is False
