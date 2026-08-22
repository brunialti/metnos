#!/usr/bin/env python3
"""Deterministic, resumable coordinator for RM-0006 certification batches.

This module is deliberately independent from the Metnos runtime.  C0 feeds it
synthetic observations; later phases may add an HTTP adapter without changing
the oracle or the artifact contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schemas.json"


class CertificationError(ValueError):
    """The frozen matrix, observation, or artifact registry is invalid."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def matrix_digest(cases: Iterable[dict[str, Any]]) -> str:
    payload = "".join(canonical_json(case) + "\n" for case in cases)
    return sha256_bytes(payload.encode("utf-8"))


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_record(kind: str, record: dict[str, Any]) -> None:
    schema = _schema()
    if kind not in schema["$defs"]:
        raise CertificationError(f"unknown schema kind: {kind}")
    validator = Draft202012Validator(
        {"$ref": f"#/$defs/{kind}", "$defs": schema["$defs"]},
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(record), key=lambda item: list(item.path))
    if errors:
        error = errors[0]
        where = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise CertificationError(f"{kind} {where}: {error.message}")
    if kind == "CaseSpec":
        _validate_case_semantics(record)
    elif kind == "CaseResult":
        _validate_result_semantics(record)


def _validate_case_semantics(case: dict[str, Any]) -> None:
    if set(case["required_effects"]) & set(case["forbidden_effects"]):
        raise CertificationError("CaseSpec: an effect cannot be required and forbidden")
    if case["required_approval"] and case["budgets"]["max_approvals"] < 1:
        raise CertificationError("CaseSpec: required approval has a zero approval budget")


def _validate_result_semantics(result: dict[str, Any]) -> None:
    probes = result["probe_results"]
    if result["verdict"] == "pass":
        if not probes:
            raise CertificationError("CaseResult: pass requires postcondition evidence")
        if not all(probe["passed"] for probe in probes):
            raise CertificationError("CaseResult: pass cannot contain a failed probe")
        if result["failure_reasons"]:
            raise CertificationError("CaseResult: pass cannot contain failure reasons")
    elif not result["failure_reasons"]:
        raise CertificationError("CaseResult: fail/error requires a failure reason")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            raise CertificationError(f"blank JSONL record: {path}:{line_no}")
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CertificationError(f"invalid JSONL: {path}:{line_no}: {exc}") from exc
        if not isinstance(record, dict):
            raise CertificationError(f"JSONL record is not an object: {path}:{line_no}")
        records.append(record)
    return records


def _write_immutable(path: Path, value: dict[str, Any] | list[dict[str, Any]]) -> None:
    if isinstance(value, list):
        rendered = "".join(canonical_json(item) + "\n" for item in value)
    else:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != rendered:
            raise CertificationError(f"immutable artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _observation_map(
    observations: list[dict[str, Any]],
) -> dict[tuple[str, int | None], dict[str, Any]]:
    mapped: dict[tuple[str, int | None], dict[str, Any]] = {}
    required = {
        "case_id", "started_at", "finished_at", "duration_ms", "route", "plan",
        "placement", "approval_count", "model_calls", "terminal", "effects",
        "probes", "response_checks",
    }
    for observation in observations:
        missing = required - set(observation)
        extra = set(observation) - required - {"cycle"}
        if missing or extra:
            raise CertificationError(
                f"observation fields for {observation.get('case_id', '?')}: "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        case_id = observation["case_id"]
        cycle = observation.get("cycle")
        if cycle is not None and (not isinstance(cycle, int) or cycle < 1):
            raise CertificationError(f"invalid observation cycle: {case_id}/{cycle}")
        key = (case_id, cycle)
        if key in mapped:
            raise CertificationError(f"duplicate observation: {case_id}/{cycle}")
        mapped[key] = observation
    return mapped


def evaluate_case(
    manifest: dict[str, Any], case: dict[str, Any], cycle: int,
    observation: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    plan = observation["plan"]
    if observation["route"] != case["expected_route"]:
        reasons.append("route_mismatch")
    if plan not in case["allowed_plans"]:
        reasons.append("plan_not_allowed")
    if set(plan) & set(case["forbidden_tools"]):
        reasons.append("forbidden_tool_used")
    if observation["placement"] != case["expected_placement"]:
        reasons.append("placement_mismatch")
    approvals = observation["approval_count"]
    if case["required_approval"] and approvals < 1:
        reasons.append("required_approval_missing")
    if not case["required_approval"] and approvals:
        reasons.append("unexpected_approval")
    if approvals > case["budgets"]["max_approvals"]:
        reasons.append("approval_budget_exceeded")
    if observation["model_calls"] > case["budgets"]["max_model_calls"]:
        reasons.append("model_call_budget_exceeded")
    if observation["duration_ms"] > case["budgets"]["deadline_s"] * 1000:
        reasons.append("deadline_exceeded")
    if observation["terminal"] != case["expected_terminal"]:
        reasons.append("terminal_mismatch")

    effects = set(observation["effects"])
    if not set(case["required_effects"]).issubset(effects):
        reasons.append("required_effect_missing")
    if set(case["forbidden_effects"]) & effects:
        reasons.append("forbidden_effect_observed")

    probes = observation["probes"]
    probe_names = [probe.get("name") for probe in probes]
    if len(probe_names) != len(set(probe_names)):
        reasons.append("duplicate_probe")
    if set(probe_names) != set(case["postcondition_probes"]):
        reasons.append("postcondition_probe_mismatch")
    if not probes or not all(probe.get("passed") is True for probe in probes):
        reasons.append("postcondition_not_proven")
    if not set(case["response_requirements"]).issubset(observation["response_checks"]):
        reasons.append("response_requirement_missing")

    result = {
        "schema_version": "metnos.certification-result/1",
        "certification_id": manifest["certification_id"],
        "case_id": case["case_id"],
        "cycle": cycle,
        "verdict": "pass" if not reasons else "fail",
        "started_at": observation["started_at"],
        "finished_at": observation["finished_at"],
        "duration_ms": observation["duration_ms"],
        "observed_route": observation["route"],
        "observed_plan": plan,
        "observed_placement": observation["placement"],
        "approval_count": approvals,
        "model_calls": observation["model_calls"],
        "observed_terminal": observation["terminal"],
        "observed_effects": observation["effects"],
        "probe_results": probes,
        "response_checks": observation["response_checks"],
        "failure_reasons": sorted(set(reasons)),
    }
    validate_record("CaseResult", result)
    return result


def _summary(
    manifest: dict[str, Any], cases: list[dict[str, Any]], results: list[dict[str, Any]],
    results_path: Path,
) -> dict[str, Any]:
    expected = len(cases) * len(manifest["cycles"])
    counts = {verdict: 0 for verdict in ("pass", "fail", "error")}
    for result in results:
        counts[result["verdict"]] += 1
    failures = [
        {"case_id": result["case_id"], "cycle": result["cycle"],
         "reasons": result["failure_reasons"]}
        for result in results if result["verdict"] != "pass"
    ]
    registry_bytes = results_path.read_bytes() if results_path.exists() else b""
    complete = len(results) == expected
    return {
        "schema_version": "metnos.certification-summary/1",
        "certification_id": manifest["certification_id"],
        "expected_cases": expected,
        "evaluated_cases": len(results),
        "passed": counts["pass"],
        "failed": counts["fail"],
        "errors": counts["error"],
        "pending": expected - len(results),
        "complete": complete,
        "objective_achieved": complete and counts["fail"] == 0 and counts["error"] == 0,
        "matrix_sha256": manifest["case_matrix_sha256"],
        "results_registry_sha256": sha256_bytes(registry_bytes),
        "failures": failures,
    }


def _write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_junit(path: Path, results: list[dict[str, Any]]) -> None:
    failures = sum(result["verdict"] == "fail" for result in results)
    errors = sum(result["verdict"] == "error" for result in results)
    suite = ET.Element("testsuite", {
        "name": "rm0006-certification",
        "tests": str(len(results)),
        "failures": str(failures),
        "errors": str(errors),
        "time": f"{sum(result['duration_ms'] for result in results) / 1000:.3f}",
    })
    for result in results:
        case = ET.SubElement(suite, "testcase", {
            "classname": f"cycle-{result['cycle']}",
            "name": result["case_id"],
            "time": f"{result['duration_ms'] / 1000:.3f}",
        })
        if result["verdict"] == "fail":
            ET.SubElement(case, "failure", {"message": ",".join(result["failure_reasons"])})
        elif result["verdict"] == "error":
            ET.SubElement(case, "error", {"message": ",".join(result["failure_reasons"])})
    ET.indent(suite, space="  ")
    path.write_text(ET.tostring(suite, encoding="unicode") + "\n", encoding="utf-8")


def run_batch(
    *, output_dir: Path, manifest: dict[str, Any], cases: list[dict[str, Any]],
    observations: list[dict[str, Any]], max_new_cases: int | None = None,
) -> dict[str, Any]:
    """Evaluate a frozen batch, append new results, and resume completed pairs."""
    validate_record("CertificationManifest", manifest)
    if not cases:
        raise CertificationError("the case matrix is empty")
    seen_case_ids: set[str] = set()
    for case in cases:
        validate_record("CaseSpec", case)
        if case["case_id"] in seen_case_ids:
            raise CertificationError(f"duplicate case_id: {case['case_id']}")
        seen_case_ids.add(case["case_id"])
    if matrix_digest(cases) != manifest["case_matrix_sha256"]:
        raise CertificationError("manifest case_matrix_sha256 does not match cases")

    obs_by_case = _observation_map(observations)
    missing_observations = [
        (case_id, cycle)
        for cycle in manifest["cycles"] for case_id in seen_case_ids
        if (case_id, cycle) not in obs_by_case and (case_id, None) not in obs_by_case
    ]
    if missing_observations:
        raise CertificationError(f"missing observations: {missing_observations}")

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_immutable(output_dir / "manifest.json", manifest)
    _write_immutable(output_dir / "cases.jsonl", cases)
    results_path = output_dir / "results.jsonl"
    existing = read_jsonl(results_path)
    completed: set[tuple[str, int]] = set()
    expected_keys = {
        (case["case_id"], cycle)
        for cycle in manifest["cycles"] for case in cases
    }
    for result in existing:
        validate_record("CaseResult", result)
        if result["certification_id"] != manifest["certification_id"]:
            raise CertificationError("result belongs to another certification")
        key = (result["case_id"], result["cycle"])
        if key not in expected_keys:
            raise CertificationError(f"result is outside the frozen matrix: {key}")
        if key in completed:
            raise CertificationError(f"duplicate result registry key: {key}")
        completed.add(key)

    events_path = output_dir / "events.redacted.jsonl"
    event_keys: set[tuple[str, int]] = set()
    for event in read_jsonl(events_path):
        if event.get("certification_id") != manifest["certification_id"]:
            raise CertificationError("event belongs to another certification")
        key = (event.get("case_id"), event.get("cycle"))
        if key not in completed:
            raise CertificationError(f"event has no registered result: {key}")
        if key in event_keys:
            raise CertificationError(f"duplicate event registry key: {key}")
        event_keys.add(key)
    for result in existing:
        key = (result["case_id"], result["cycle"])
        if key not in event_keys:
            _append_jsonl(events_path, {
                "schema_version": "metnos.certification-event/1",
                "certification_id": manifest["certification_id"],
                "case_id": result["case_id"],
                "cycle": result["cycle"],
                "event": "case_evaluated",
                "verdict": result["verdict"],
            })

    written = 0
    for cycle in manifest["cycles"]:
        for case in cases:
            key = (case["case_id"], cycle)
            if key in completed:
                continue
            if max_new_cases is not None and written >= max_new_cases:
                break
            observation = obs_by_case.get(
                (case["case_id"], cycle), obs_by_case.get((case["case_id"], None)),
            )
            assert observation is not None
            result = evaluate_case(manifest, case, cycle, observation)
            _append_jsonl(results_path, result)
            _append_jsonl(events_path, {
                "schema_version": "metnos.certification-event/1",
                "certification_id": manifest["certification_id"],
                "case_id": case["case_id"],
                "cycle": cycle,
                "event": "case_evaluated",
                "verdict": result["verdict"],
            })
            if result["verdict"] != "pass":
                _write_immutable(
                    output_dir / "failures" / case["case_id"] / f"cycle-{cycle}.json",
                    {"case": case, "observation": observation, "result": result},
                )
            completed.add(key)
            written += 1
        if max_new_cases is not None and written >= max_new_cases:
            break

    results = read_jsonl(results_path)
    summary = _summary(manifest, cases, results, results_path)
    _write_summary(output_dir / "summary.json", summary)
    _write_junit(output_dir / "junit.xml", results)
    return summary
