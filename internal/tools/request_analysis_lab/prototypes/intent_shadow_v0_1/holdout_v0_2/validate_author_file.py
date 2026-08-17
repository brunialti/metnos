"""Strict structural validation for one independently authored language file."""
from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONSTITUTION_PATH = ROOT / "authoring_constitution.json"


class AuthorFileError(ValueError):
    """The independent author file violates its closed contract."""


def _load(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise AuthorFileError(f"non-finite number: {value}")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AuthorFileError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, AuthorFileError):
            raise
        raise AuthorFileError(f"invalid UTF-8 JSON: {path}") from exc
    _finite(value, "$")
    return value


def _finite(value: Any, path: str) -> None:
    if type(value) is float and not math.isfinite(value):
        raise AuthorFileError(f"non-finite number at {path}")
    if type(value) is list:
        for index, item in enumerate(value):
            _finite(item, f"{path}[{index}]")
    if type(value) is dict:
        for key, item in value.items():
            _finite(item, f"{path}.{key}")


def _closed(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise AuthorFileError(f"closed object mismatch at {path}")
    return value


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def validate(path: Path) -> dict[str, Any]:
    constitution = _load(CONSTITUTION_PATH)
    tag = path.stem
    assignment_path = ROOT / "assignments" / f"{tag}.json"
    assignment = _load(assignment_path)
    document = _load(path)
    top_keys = set(constitution["output_contract"]["top_level_fields_exact"])
    case_keys = set(constitution["output_contract"]["case_fields_exact"])
    _closed(document, top_keys, "$")
    if document["brief_version"] != constitution["brief_version"]:
        raise AuthorFileError("brief_version mismatch")
    if document["author_id"] != assignment["author_id"]:
        raise AuthorFileError("author_id mismatch")
    if document["language_tag"] != assignment["language_tag"] or tag != assignment["language_tag"]:
        raise AuthorFileError("language_tag mismatch")
    cases = document["cases"]
    if type(cases) is not list or len(cases) != constitution["counts"]["cases_per_language"]:
        raise AuthorFileError("case count mismatch")
    expected_ids = constitution["output_contract"]["local_ids_exact"]
    cells = constitution["cells_in_exact_order"]
    expected_cells = [item["authoring_cell"] for item in cells for _ in range(item["count"])]
    expected_safety = {item["authoring_cell"]: item["safety_tags"] for item in cells}
    seen_queries: set[str] = set()
    for index, case in enumerate(cases):
        path_text = f"$.cases[{index}]"
        _closed(case, case_keys, path_text)
        if case["local_id"] != expected_ids[index]:
            raise AuthorFileError(f"local_id mismatch at {path_text}")
        cell = case["authoring_cell"]
        if cell != expected_cells[index]:
            raise AuthorFileError(f"cell order mismatch at {path_text}")
        if case["safety_tags"] != expected_safety[cell]:
            raise AuthorFileError(f"safety tags mismatch at {path_text}")
        query = case["query"]
        if type(query) is not str or query != query.strip() or not query:
            raise AuthorFileError(f"invalid query at {path_text}")
        if len(query.encode("utf-8")) > 4096:
            raise AuthorFileError(f"query too long at {path_text}")
        if any(unicodedata.category(char) == "Cc" for char in query):
            raise AuthorFileError(f"control character at {path_text}")
        normalized = re.sub(r"\s+", " ", _normalize(query)).strip()
        if normalized in seen_queries:
            raise AuthorFileError(f"duplicate normalized query at {path_text}")
        seen_queries.add(normalized)
    return {"status": "ok", "error_count": 0, "language_tag": tag, "cases": len(cases)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        result = validate(args.path)
    except (AuthorFileError, ValueError) as exc:
        print(json.dumps({"status": "fail", "error_count": 1, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
