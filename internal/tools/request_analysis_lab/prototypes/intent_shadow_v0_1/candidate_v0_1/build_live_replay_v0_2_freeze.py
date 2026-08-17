#!/usr/bin/env python3
"""Build or check the closed, oracle-free replay/evaluator 0.2 freeze."""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from intent_shadow_io import canonical_json_bytes, file_sha256, strict_json_file
from live_replay_gate_v0_2 import (
    CREATED_DATE,
    FREEZE_FORMAT,
    FREEZE_PATH,
    FROZEN_SOURCE_FILES,
    REPLAY_GATE_VERSION,
    ROOT,
    _expected_freeze_policy,
)

HERE = Path(__file__).resolve().parent


def _payload_sha256(document: dict[str, Any]) -> str:
    payload = deepcopy(document)
    payload.pop("lock_payload_sha256", None)
    return sha256(canonical_json_bytes(payload)).hexdigest()


def build() -> dict[str, Any]:
    missing = [relative for relative in FROZEN_SOURCE_FILES if not (ROOT / relative).is_file()]
    if missing:
        raise RuntimeError("missing v0.2 source:" + ",".join(missing))
    value: dict[str, Any] = {
        "freeze_format": FREEZE_FORMAT,
        "replay_gate_version": REPLAY_GATE_VERSION,
        "evaluator_version": "metnos.intent-shadow-live-evaluator/0.2",
        "created_date": CREATED_DATE,
        "algorithm": "sha256",
        "status": "pre_gold_gate_only_evaluation_not_run",
        "oracle_opened": False,
        "evaluation_executed": False,
        "counts": {
            "records": 316,
            "canonical_records": 240,
            "typed_control_records": 8,
            "legacy_records": 68,
        },
        "allowed_difference": _expected_freeze_policy(),
        "source_files": {
            relative: file_sha256(ROOT / relative)
            for relative in sorted(FROZEN_SOURCE_FILES)
        },
        "self_files": {
            "live_replay_gate_v0_2.py": file_sha256(HERE / "live_replay_gate_v0_2.py"),
            "build_live_replay_v0_2_freeze.py": file_sha256(Path(__file__).resolve()),
            "test_live_replay_v0_2.py": file_sha256(HERE / "test_live_replay_v0_2.py"),
        },
    }
    value["lock_payload_sha256"] = _payload_sha256(value)
    return value


def _write(value: dict[str, Any]) -> None:
    FREEZE_PATH.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    expected = build()
    if args.write:
        _write(expected)
    current = strict_json_file(FREEZE_PATH)
    ok = canonical_json_bytes(current) == canonical_json_bytes(expected)
    print(json.dumps({
        "status": "ok" if ok else "error",
        "error_count": 0 if ok else 1,
        "freeze_sha256": file_sha256(FREEZE_PATH) if FREEZE_PATH.is_file() else None,
        "source_count": len(FROZEN_SOURCE_FILES),
        "oracle_opened": False,
        "evaluation_executed": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
