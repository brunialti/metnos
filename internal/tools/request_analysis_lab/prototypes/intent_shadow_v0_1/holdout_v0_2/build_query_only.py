"""Assemble and freeze the ten independently authored RUN4 language panels."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .validate_author_file import ROOT, validate


LAB_ROOT = ROOT.parent
WORKSPACE = LAB_ROOT.parents[4]
CONSTITUTION_PATH = ROOT / "authoring_constitution.json"
CHALLENGER_FREEZE_PATH = LAB_ROOT / "prompt_challenger_v0_1" / "prompt_challenger_v0_1.freeze.json"
BLINDED_PATH = ROOT / "blinded_query_panel.json"
FREEZE_PATH = ROOT / "query_only.freeze.json"
EXPECTED_CHALLENGER_SHA256 = "91f7acb5b582f1ad064ed19e788207c8a0452e1a712d934a60f8e7e59463d966"
LANGUAGES = (
    "en-GB", "it-IT", "es-MX", "de-DE", "tr-TR",
    "sr-Cyrl-RS", "ar-EG", "hi-IN", "ja-JP", "zh-Hant-TW",
)


class BuildError(ValueError):
    """The independent query-only build cannot be trusted."""


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise BuildError(f"cannot read {path}") from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(_read(path)).hexdigest()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"invalid JSON: {path}") from exc


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _relative(path: Path) -> str:
    return path.resolve().relative_to(WORKSPACE.resolve()).as_posix()


def build_panel() -> dict[str, Any]:
    constitution = _load(CONSTITUTION_PATH)
    cases: list[dict[str, Any]] = []
    ordinal = 0
    for language in LANGUAGES:
        author_path = ROOT / "authors" / f"{language}.json"
        validate(author_path)
        author = _load(author_path)
        for item in author["cases"]:
            ordinal += 1
            query = item["query"]
            cases.append(
                {
                    "case_id": f"h4-{ordinal:04d}",
                    "language_tag": language,
                    "authoring_cell": item["authoring_cell"],
                    "safety_tags": item["safety_tags"],
                    "query": query,
                    "query_sha256": _sha_bytes(query.encode("utf-8")),
                }
            )
    if ordinal != 320:
        raise BuildError("assembled case count differs from 320")
    return {
        "format_version": "metnos.intent-holdout-query-panel/2.0",
        "holdout_id": "metnos.intent-holdout/0.2",
        "case_count": ordinal,
        "language_tags": list(LANGUAGES),
        "cases": cases,
        "authoring_constitution_sha256": _sha256(CONSTITUTION_PATH),
        "challenger_freeze_sha256": constitution["challenger_binding"]["freeze_sha256"],
    }


def build_freeze(panel: dict[str, Any]) -> dict[str, Any]:
    challenger_hash = _sha256(CHALLENGER_FREEZE_PATH)
    if challenger_hash != EXPECTED_CHALLENGER_SHA256:
        raise BuildError("challenger freeze mismatch")
    challenger_mtime = CHALLENGER_FREEZE_PATH.stat().st_mtime_ns
    constitution_mtime = CONSTITUTION_PATH.stat().st_mtime_ns
    author_paths = [ROOT / "authors" / f"{language}.json" for language in LANGUAGES]
    if not challenger_mtime < constitution_mtime:
        raise BuildError("challenger did not predate authoring constitution")
    if any(path.stat().st_mtime_ns < constitution_mtime for path in author_paths):
        raise BuildError("an author file predates the authoring constitution")
    panel_bytes = _canonical(panel)
    files = {
        _relative(CONSTITUTION_PATH): _sha256(CONSTITUTION_PATH),
        _relative(ROOT / "validate_author_file.py"): _sha256(ROOT / "validate_author_file.py"),
        _relative(ROOT / "build_query_only.py"): _sha256(ROOT / "build_query_only.py"),
        _relative(BLINDED_PATH): _sha_bytes(panel_bytes),
    }
    for language in LANGUAGES:
        assignment = ROOT / "assignments" / f"{language}.json"
        author = ROOT / "authors" / f"{language}.json"
        files[_relative(assignment)] = _sha256(assignment)
        files[_relative(author)] = _sha256(author)
    body: dict[str, Any] = {
        "freeze_version": "metnos.intent-holdout-query-freeze/2.0",
        "holdout_id": panel["holdout_id"],
        "status": "independent_query_only_frozen_gold_absent",
        "counts": {"cases": 320, "languages": 10, "cases_per_language": 32, "safety_cases": 80},
        "challenger_binding": {
            "path": _relative(CHALLENGER_FREEZE_PATH),
            "sha256": challenger_hash,
            "preexists_authoring": True,
        },
        "chronology": {
            "challenger_freeze_mtime_ns": challenger_mtime,
            "constitution_mtime_ns": constitution_mtime,
            "strict_challenger_before_constitution": challenger_mtime < constitution_mtime,
            "all_authors_after_constitution": True,
        },
        "independent_authorship": {
            "authors": 10,
            "one_language_per_assignment": True,
            "cross_language_visibility_forbidden": True,
            "custodian_review_required_before_adjudication": True,
        },
        "files": dict(sorted(files.items())),
        "payload_sha256": _sha_bytes(_canonical(panel["cases"])),
        "gold": {"present": False, "runner_access": False, "adjudication_started": False},
    }
    body["lock_sha256"] = _sha_bytes(_canonical(body))
    return body


def write() -> dict[str, Any]:
    panel = build_panel()
    BLINDED_PATH.write_bytes(_canonical(panel))
    freeze = build_freeze(panel)
    FREEZE_PATH.write_bytes(_canonical(freeze))
    return freeze


def check() -> dict[str, Any]:
    panel = build_panel()
    if _read(BLINDED_PATH) != _canonical(panel):
        raise BuildError("blinded query panel drift")
    freeze = build_freeze(panel)
    if _load(FREEZE_PATH) != freeze:
        raise BuildError("query-only freeze drift")
    return {
        "status": "ok",
        "error_count": 0,
        "cases": panel["case_count"],
        "payload_sha256": freeze["payload_sha256"],
        "lock_sha256": freeze["lock_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        result = write() if args.write else check()
    except (BuildError, ValueError) as exc:
        print(json.dumps({"status": "fail", "error_count": 1, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
