"""Strict, query-blind validation for one independent RUN4 adjudication."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
LAB_ROOT = ROOT.parent
PANEL_PATH = ROOT / "blinded_query_panel.json"
CONTRACT_PATH = ROOT / "adjudication_contract.json"
REGISTRY_PATH = LAB_ROOT / "intent_shadow_registry_v0_1.json"
CANDIDATE_V0_1 = LAB_ROOT / "candidate_v0_1"
if str(CANDIDATE_V0_1) not in sys.path:
    sys.path.insert(0, str(CANDIDATE_V0_1))

from intent_shadow_validate import validate_model_document  # noqa: E402


class AdjudicationValidationError(ValueError):
    """One adjudication violates the closed review contract."""


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise AdjudicationValidationError(f"cannot read {path}") from exc


def _load_strict(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise AdjudicationValidationError(f"non-finite JSON number: {value}")

    def pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AdjudicationValidationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(
            text,
            object_pairs_hook=pairs_no_duplicates,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, AdjudicationValidationError):
            raise
        raise AdjudicationValidationError(f"invalid UTF-8 JSON: {path}") from exc
    _reject_nonfinite(value, "$")
    return value


def _reject_nonfinite(value: Any, path: str) -> None:
    if type(value) is float and not math.isfinite(value):
        raise AdjudicationValidationError(f"non-finite number at {path}")
    if type(value) is list:
        for index, item in enumerate(value):
            _reject_nonfinite(item, f"{path}[{index}]")
    if type(value) is dict:
        for key, item in value.items():
            _reject_nonfinite(item, f"{path}.{key}")


def _exact_dict(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise AdjudicationValidationError(f"exact object required at {path}")
    actual = set(value)
    if actual != keys:
        raise AdjudicationValidationError(
            f"closed object mismatch at {path}: missing={sorted(keys - actual)}, extra={sorted(actual - keys)}"
        )
    return value


def _nonempty_string(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise AdjudicationValidationError(f"nonempty trimmed string required at {path}")
    return value


def _canonical_expected_shape(value: Any, path: str) -> None:
    if type(value) is not dict:
        raise AdjudicationValidationError(f"expected object required at {path}")
    kind = value.get("kind")
    if kind == "operation_graph":
        _exact_dict(value, {"kind", "body"}, path)
    elif kind == "system_control":
        _exact_dict(value, {"kind", "control"}, path)
    elif kind == "unrepresentable":
        _exact_dict(value, {"kind", "reason"}, path)
    else:
        raise AdjudicationValidationError(f"unknown root kind at {path}")


def validate(path: Path) -> dict[str, Any]:
    contract = _load_strict(CONTRACT_PATH)
    panel = _load_strict(PANEL_PATH)
    registry = _load_strict(REGISTRY_PATH)
    document = _load_strict(path)
    top_keys = set(contract["output"]["top_level_fields_exact"])
    _exact_dict(document, top_keys, "$")
    if document["contract_version"] != contract["contract_version"]:
        raise AdjudicationValidationError("contract_version mismatch")
    reviewer_id = _nonempty_string(document["reviewer_id"], "$.reviewer_id")
    if document["query_panel_sha256"] != _sha256(PANEL_PATH):
        raise AdjudicationValidationError("query_panel_sha256 mismatch")
    cases = document["cases"]
    if type(cases) is not list or len(cases) != panel["case_count"]:
        raise AdjudicationValidationError("case array length mismatch")

    case_keys = set(contract["output"]["case_fields_exact"])
    citation_keys = set(contract["output"]["authority_citation_fields_exact"])
    safety_keys = set(contract["output"]["safety_applicability_fields_exact"])
    confidence_values = set(contract["output"]["confidence_values"])
    root_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    safety_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()

    for index, (case, query_case) in enumerate(zip(cases, panel["cases"], strict=True)):
        case_path = f"$.cases[{index}]"
        _exact_dict(case, case_keys, case_path)
        case_id = case["case_id"]
        if case_id != query_case["case_id"] or case_id in seen_ids:
            raise AdjudicationValidationError(f"case identity/order mismatch at {case_path}")
        seen_ids.add(case_id)
        if case["query_sha256"] != query_case["query_sha256"]:
            raise AdjudicationValidationError(f"query hash mismatch at {case_path}")

        expected = case["expected"]
        _canonical_expected_shape(expected, f"{case_path}.expected")
        validation = validate_model_document(expected, registry)
        if not validation.valid:
            issue_codes = [issue.code for issue in validation.issues]
            raise AdjudicationValidationError(
                f"invalid canonical expected at {case_path}: {issue_codes}"
            )
        root_counts[expected["kind"]] += 1

        citations = case["authority_citations"]
        if type(citations) is not list or not citations:
            raise AdjudicationValidationError(f"nonempty citations required at {case_path}")
        for citation_index, citation in enumerate(citations):
            citation_path = f"{case_path}.authority_citations[{citation_index}]"
            _exact_dict(citation, citation_keys, citation_path)
            for key in citation_keys:
                _nonempty_string(citation[key], f"{citation_path}.{key}")

        safety = case["safety_applicability"]
        _exact_dict(safety, safety_keys, f"{case_path}.safety_applicability")
        for key, value in safety.items():
            if type(value) is not bool:
                raise AdjudicationValidationError(
                    f"exact boolean required at {case_path}.safety_applicability.{key}"
                )
            if value:
                safety_counts[key] += 1

        confidence = case["confidence"]
        if type(confidence) is not str or confidence not in confidence_values:
            raise AdjudicationValidationError(f"invalid confidence at {case_path}")
        confidence_counts[confidence] += 1

    return {
        "status": "ok",
        "error_count": 0,
        "reviewer_id": reviewer_id,
        "cases": len(cases),
        "root_counts": dict(root_counts),
        "confidence_counts": dict(confidence_counts),
        "safety_counts": dict(safety_counts),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        result = validate(args.path)
    except (AdjudicationValidationError, ValueError) as exc:
        print(json.dumps({"status": "fail", "error_count": 1, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
