#!/usr/bin/env python3
"""Deterministic quality gate for consecutive Metnos E2E runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _suite_nodes(root: ET.Element) -> list[ET.Element]:
    if root.tag == "testsuite":
        return [root]
    direct = list(root.findall("./testsuite"))
    return direct or list(root.findall(".//testsuite"))


def _fingerprint(items: list[str]) -> str:
    payload = "\n".join(sorted(items)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def read_junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = _suite_nodes(root)
    cases: list[ET.Element] = []
    for suite in suites:
        cases.extend(suite.findall("./testcase"))
    tests = len(cases)
    failures = sum(case.find("failure") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    passed = max(0, tests - failures - errors - skipped)
    executed = max(0, tests - skipped)
    ids = [
        f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
        for case in cases
    ]
    executed_ids = [
        item for item, case in zip(ids, cases)
        if case.find("skipped") is None
    ]
    duration_s = sum(float(case.attrib.get("time", "0") or 0) for case in cases)
    return {
        "junit": str(path),
        "tests": tests,
        "passed": passed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "executed": executed,
        "success_rate": passed / executed if executed else 0.0,
        "execution_coverage": executed / tests if tests else 0.0,
        "duration_s": round(duration_s, 3),
        "suite_fingerprint": _fingerprint(ids),
        "executed_fingerprint": _fingerprint(executed_ids),
    }


def evaluate(paths: list[Path], targets: dict) -> dict:
    runs = [read_junit(path) for path in paths]
    for run in runs:
        run["clean"] = bool(
            run["tests"] >= int(targets["min_collected_tests"])
            and run["failures"] <= int(targets["max_failures"])
            and run["errors"] <= int(targets["max_errors"])
            and run["success_rate"] >= float(targets["min_success_rate"])
            and run["execution_coverage"] >= float(
                targets["min_execution_coverage"]
            )
        )
    suite_stable = bool(runs) and len({
        (run["suite_fingerprint"], run["executed_fingerprint"])
        for run in runs
    }) == 1
    streak = 0
    for run in reversed(runs):
        if not run["clean"]:
            break
        streak += 1
    required = int(targets["required_consecutive_clean_runs"])
    stable_required = bool(targets.get("require_stable_suite", True))
    achieved = bool(
        streak >= required and (suite_stable or not stable_required)
    )
    return {
        "schema_version": 1,
        "targets": targets,
        "runs": runs,
        "suite_stable": suite_stable,
        "consecutive_clean_runs": streak,
        "objective_achieved": achieved,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", action="append", required=True)
    parser.add_argument(
        "--targets", default=str(ROOT / "quality_targets.json"),
    )
    parser.add_argument("--output")
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()

    targets = json.loads(Path(args.targets).read_text(encoding="utf-8"))
    report = evaluate([Path(path) for path in args.junit], targets)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return int(args.enforce and not report["objective_achieved"])


if __name__ == "__main__":
    raise SystemExit(main())
