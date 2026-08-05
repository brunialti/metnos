from __future__ import annotations

import sys
from pathlib import Path


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from agent_runtime import validate_args  # noqa: E402
from engine.validator import Validator  # noqa: E402


SCHEMA = {
    "type": "object",
    "properties": {
        "account": {"type": ["string", "array"], "items": {"type": "string"}},
    },
}


def test_agent_runtime_accepts_each_declared_union_branch() -> None:
    assert validate_args({"account": "all"}, SCHEMA) == []
    assert validate_args({"account": ["work", "personal"]}, SCHEMA) == []
    assert validate_args({"account": {"unexpected": True}}, SCHEMA)


def test_engine_validator_accepts_each_declared_union_branch() -> None:
    validator = Validator([])
    assert validator._check_args({"account": "all"}, SCHEMA) is None
    assert validator._check_args(
        {"account": ["work", "personal"]}, SCHEMA,
    ) is None
    assert validator._check_args({"account": {"unexpected": True}}, SCHEMA)
