"""Build and verify the blinded, query-only RUN4 holdout artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .validate_query_only import BRIEF_PATH, DEFAULT_PROPOSAL_PATH, validate


ROOT = Path(__file__).resolve().parent
LAB_ROOT = ROOT.parent
CHALLENGER_FREEZE_PATH = LAB_ROOT / "prompt_challenger_v0_1" / "prompt_challenger_v0_1.freeze.json"
BLINDED_PATH = ROOT / "blinded_query_panel.json"
FREEZE_PATH = ROOT / "query_only.freeze.json"
HOLDOUT_ID = "metnos.intent-holdout/0.1"
EXPECTED_CHALLENGER_FREEZE_SHA256 = "91f7acb5b582f1ad064ed19e788207c8a0452e1a712d934a60f8e7e59463d966"


class BuildError(ValueError):
    """A query-only build or freeze invariant failed."""


def _bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise BuildError(f"cannot read {path}") from exc


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(_bytes(path))


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"invalid JSON: {path}") from exc


def _relative(path: Path) -> str:
    return path.resolve().relative_to(LAB_ROOT.parents[4].resolve()).as_posix()


def build_blinded() -> dict[str, Any]:
    validate(DEFAULT_PROPOSAL_PATH)
    proposal = _load(DEFAULT_PROPOSAL_PATH)
    cases = []
    for item in proposal["cases"]:
        query = item["query"]
        cases.append(
            {
                "case_id": item["case_id"],
                "language_tag": item["language_tag"],
                "query": query,
                "query_sha256": _sha256_bytes(query.encode("utf-8")),
            }
        )
    return {
        "format_version": "metnos.intent-holdout-query-panel/1.0",
        "holdout_id": HOLDOUT_ID,
        "case_count": len(cases),
        "cases": cases,
    }


def build_freeze(blinded: dict[str, Any]) -> dict[str, Any]:
    challenger_hash = _sha256_file(CHALLENGER_FREEZE_PATH)
    if challenger_hash != EXPECTED_CHALLENGER_FREEZE_SHA256:
        raise BuildError("challenger freeze does not match pre-holdout authority")
    challenger_mtime = CHALLENGER_FREEZE_PATH.stat().st_mtime_ns
    brief_mtime = BRIEF_PATH.stat().st_mtime_ns
    proposal_mtime = DEFAULT_PROPOSAL_PATH.stat().st_mtime_ns
    if not challenger_mtime < brief_mtime <= proposal_mtime:
        raise BuildError("filesystem chronology does not show challenger-before-holdout")
    blinded_bytes = _canonical(blinded)
    body: dict[str, Any] = {
        "freeze_version": "metnos.intent-holdout-query-freeze/1.0",
        "holdout_id": HOLDOUT_ID,
        "status": "query_only_frozen_gold_absent",
        "counts": {
            "cases": blinded["case_count"],
            "languages": 10,
            "cases_per_language": 32,
            "safety_cases": 80,
        },
        "challenger_binding": {
            "path": _relative(CHALLENGER_FREEZE_PATH),
            "sha256": challenger_hash,
            "preexists_holdout": True,
        },
        "chronology": {
            "challenger_freeze_mtime_ns": challenger_mtime,
            "authoring_brief_mtime_ns": brief_mtime,
            "proposal_mtime_ns": proposal_mtime,
            "strict_challenger_before_brief": challenger_mtime < brief_mtime,
            "brief_not_after_proposal": brief_mtime <= proposal_mtime,
        },
        "files": {
            _relative(BRIEF_PATH): _sha256_file(BRIEF_PATH),
            _relative(DEFAULT_PROPOSAL_PATH): _sha256_file(DEFAULT_PROPOSAL_PATH),
            _relative(BLINDED_PATH): _sha256_bytes(blinded_bytes),
            _relative(ROOT / "adjudication_contract.json"): _sha256_file(ROOT / "adjudication_contract.json"),
            _relative(ROOT / "validate_query_only.py"): _sha256_file(ROOT / "validate_query_only.py"),
            _relative(ROOT / "build_query_only.py"): _sha256_file(ROOT / "build_query_only.py"),
        },
        "payload_sha256": _sha256_bytes(_canonical(blinded["cases"])),
        "gold": {
            "present": False,
            "runner_access": False,
            "adjudication_not_started_at_freeze": True,
        },
    }
    body["lock_sha256"] = _sha256_bytes(_canonical(body))
    return body


def write() -> dict[str, Any]:
    blinded = build_blinded()
    BLINDED_PATH.write_bytes(_canonical(blinded))
    freeze = build_freeze(blinded)
    FREEZE_PATH.write_bytes(_canonical(freeze))
    return freeze


def check() -> dict[str, Any]:
    validate(DEFAULT_PROPOSAL_PATH)
    expected_blinded = build_blinded()
    if _bytes(BLINDED_PATH) != _canonical(expected_blinded):
        raise BuildError("blinded panel drift")
    expected_freeze = build_freeze(expected_blinded)
    actual_freeze = _load(FREEZE_PATH)
    if actual_freeze != expected_freeze:
        raise BuildError("query-only freeze drift")
    return {
        "status": "ok",
        "error_count": 0,
        "cases": expected_blinded["case_count"],
        "payload_sha256": expected_freeze["payload_sha256"],
        "lock_sha256": expected_freeze["lock_sha256"],
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
