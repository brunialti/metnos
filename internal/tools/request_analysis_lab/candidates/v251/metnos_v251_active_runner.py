#!/usr/bin/env python3
"""Decoder-order control for V25.1: active union branches precede none."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, "/tmp")
import metnos_v251_live_runner as candidate  # noqa: E402


def active_first_requested_result_schema():
    schema = copy.deepcopy(_original_schema())
    variants = schema["oneOf"]
    by_kind = {
        variant["properties"]["kind"]["const"]: variant
        for variant in variants
    }
    schema["oneOf"] = [
        by_kind["question_slot"],
        by_kind["support_argument"],
        by_kind["explicit_entity"],
        by_kind["held_result"],
        by_kind["context_implicit"],
        by_kind["none"],
    ]
    return schema


_original_schema = candidate.requested_result_schema
_original_emit = candidate.emit_and_freeze
_original_verify = candidate.verify_freeze
candidate.requested_result_schema = active_first_requested_result_schema
candidate.RUNNER_PATH = Path(__file__)
candidate.SCHEMA_PATH = Path("/tmp/metnos_v251_active_live.schema.json")
candidate.PROMPT_PATH = Path("/tmp/metnos_v251_active_live.prompt.txt")
candidate.FIXTURE_PATH = Path("/tmp/metnos_v251_active_targeted_fixture.json")
candidate.FREEZE_PATH = Path("/tmp/metnos_v251_active_live.freeze.json")


def emit_active():
    lock = _original_emit()
    lock["freeze_version"] = "metnos.v25.1-requested-result-active-first/1.0"
    lock["candidate_v251_sha256"] = __import__("hashlib").sha256(
        Path(candidate.__file__).read_bytes()).hexdigest()
    candidate.FREEZE_PATH.write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n")
    return lock


candidate.emit_and_freeze = emit_active


def verify_active():
    _original_verify()
    lock = json.loads(candidate.FREEZE_PATH.read_text())
    actual = __import__("hashlib").sha256(
        Path(candidate.__file__).read_bytes()).hexdigest()
    if lock.get("candidate_v251_sha256") != actual:
        raise RuntimeError("frozen active control base changed")


candidate.verify_freeze = verify_active


if __name__ == "__main__":
    raise SystemExit(candidate.main())
