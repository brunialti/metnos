"""One deterministic schema and oracle for the harness and administrative F5 owner."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable
from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_PATH = Path(__file__).with_name("schemas.json")

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

