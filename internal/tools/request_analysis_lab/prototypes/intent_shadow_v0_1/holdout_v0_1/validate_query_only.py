"""Fail-closed structural checks for the query-only RUN4 holdout proposal."""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
BRIEF_PATH = ROOT / "authoring_brief.json"
DEFAULT_PROPOSAL_PATH = ROOT / "author" / "query_only_proposal.json"


class QueryOnlyValidationError(ValueError):
    """The query-only proposal violates its frozen authoring contract."""


def _load_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise QueryOnlyValidationError(f"non-finite JSON number: {value}")

    def pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise QueryOnlyValidationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise QueryOnlyValidationError(f"cannot read UTF-8 JSON: {path}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=pairs_no_duplicates,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, QueryOnlyValidationError):
            raise
        raise QueryOnlyValidationError(f"invalid JSON: {path}") from exc


def _exact_dict(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise QueryOnlyValidationError(f"{label} must be an object")
    actual = set(value)
    if actual != keys:
        raise QueryOnlyValidationError(
            f"{label} keys differ: missing={sorted(keys - actual)}, extra={sorted(actual - keys)}"
        )
    return value


def _normal_form(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def validate(proposal_path: Path = DEFAULT_PROPOSAL_PATH) -> dict[str, Any]:
    brief = _load_json(BRIEF_PATH)
    proposal = _load_json(proposal_path)
    _exact_dict(proposal, {"brief_version", "cases"}, "proposal")
    if proposal["brief_version"] != brief["brief_version"]:
        raise QueryOnlyValidationError("brief_version mismatch")
    cases = proposal["cases"]
    if type(cases) is not list:
        raise QueryOnlyValidationError("cases must be an array")
    expected_total = brief["size"]["total_cases"]
    if len(cases) != expected_total:
        raise QueryOnlyValidationError(f"expected {expected_total} cases, got {len(cases)}")

    languages = brief["size"]["language_tags"]
    cells = brief["cells_per_language"]
    expected_cell_counts = {item["authoring_cell"]: item["count"] for item in cells}
    expected_safety = {item["authoring_cell"]: item["safety_tags"] for item in cells}
    forbidden = set(brief["output"]["format"]["forbidden_fields"])
    exact_case_fields = set(brief["output"]["format"]["case_fields_exact"])
    case_id_pattern = re.compile(brief["output"]["format"]["case_id_pattern"] + r"\Z")

    language_counts: Counter[str] = Counter()
    cell_counts: Counter[tuple[str, str]] = Counter()
    safety_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()
    seen_queries: dict[str, str] = {}
    ordered_pairs: list[tuple[str, str]] = []

    for index, raw_case in enumerate(cases, start=1):
        case = _exact_dict(raw_case, exact_case_fields, f"case[{index - 1}]")
        if forbidden.intersection(case):
            raise QueryOnlyValidationError(f"forbidden field in case {index}")
        expected_id = f"h4-{index:04d}"
        case_id = case["case_id"]
        if type(case_id) is not str or not case_id_pattern.fullmatch(case_id):
            raise QueryOnlyValidationError(f"invalid case_id at position {index}")
        if case_id != expected_id or case_id in seen_ids:
            raise QueryOnlyValidationError(f"case_id order/uniqueness failure: {case_id}")
        seen_ids.add(case_id)

        language = case["language_tag"]
        cell = case["authoring_cell"]
        safety_tags = case["safety_tags"]
        query = case["query"]
        if type(language) is not str or language not in languages:
            raise QueryOnlyValidationError(f"unknown language tag in {case_id}")
        if type(cell) is not str or cell not in expected_cell_counts:
            raise QueryOnlyValidationError(f"unknown authoring cell in {case_id}")
        if type(safety_tags) is not list or any(type(tag) is not str for tag in safety_tags):
            raise QueryOnlyValidationError(f"invalid safety_tags in {case_id}")
        if safety_tags != expected_safety[cell]:
            raise QueryOnlyValidationError(f"safety_tags mismatch in {case_id}")
        if type(query) is not str or not query.strip() or query != query.strip():
            raise QueryOnlyValidationError(f"invalid query text in {case_id}")
        if len(query.encode("utf-8")) > 4096:
            raise QueryOnlyValidationError(f"query too large in {case_id}")
        if any(unicodedata.category(char) == "Cc" for char in query):
            raise QueryOnlyValidationError(f"control character in {case_id}")
        normalized = _normal_form(query)
        if normalized in seen_queries:
            raise QueryOnlyValidationError(
                f"duplicate normalized query: {seen_queries[normalized]} and {case_id}"
            )
        seen_queries[normalized] = case_id
        language_counts[language] += 1
        cell_counts[(language, cell)] += 1
        safety_counts.update(safety_tags)
        ordered_pairs.append((language, cell))

    cases_per_language = brief["size"]["cases_per_language"]
    for language in languages:
        if language_counts[language] != cases_per_language:
            raise QueryOnlyValidationError(f"language count mismatch: {language}")
        for cell, expected_count in expected_cell_counts.items():
            if cell_counts[(language, cell)] != expected_count:
                raise QueryOnlyValidationError(f"cell count mismatch: {language}/{cell}")

    expected_order = [
        (language, item["authoring_cell"])
        for language in languages
        for item in cells
        for _ in range(item["count"])
    ]
    if ordered_pairs != expected_order:
        raise QueryOnlyValidationError("cases are not in language/cell contract order")

    return {
        "status": "ok",
        "error_count": 0,
        "cases": len(cases),
        "languages": dict(language_counts),
        "safety_tags": dict(safety_counts),
        "normalized_queries_unique": len(seen_queries),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("proposal", nargs="?", type=Path, default=DEFAULT_PROPOSAL_PATH)
    args = parser.parse_args()
    try:
        result = validate(args.proposal)
    except QueryOnlyValidationError as exc:
        print(json.dumps({"status": "fail", "error_count": 1, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
