"""Parity tests for the publisher's path-aware source-review bindings."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import contract_boundary_guard as guard
import executor_birth_admin_preflight as standalone


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "internal/tools/rm0008_public_source_review.py"
GUARD_PATH = "runtime/contract_boundary_guard.py"
ADMIN_PATH = "runtime/executor_birth_admin_preflight.py"
GUARD_NAME = b"BIRTH_CLOSED_SOURCE_REVIEW_SHA256"
ADMIN_NAME = b"_BIRTH_CLOSED_SOURCE_REVIEW_SHA256"
GOLDEN = "sha256:0fc70f29747469f28083c16648fca5b12a61af3dbcd71c08af130de9a352e1a9"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "rm0008_public_source_review_path_test", TOOL,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pin(name: bytes, digit: bytes) -> bytes:
    return name + b' = "sha256:' + digit * 64 + b'"\n'


def _sources(
    guard_digit: bytes = b"1", admin_digit: bytes = b"2",
) -> dict[str, bytes]:
    return {
        GUARD_PATH: _pin(GUARD_NAME, guard_digit),
        ADMIN_PATH: _pin(ADMIN_NAME, admin_digit),
        "runtime/probe.py": b"VALUE = 1\n",
    }


def _all_hashes(sources: dict[str, bytes]) -> tuple[str, str, str]:
    tool = _load_tool()
    return (
        guard.closed_python_source_review_sha256(sources),
        standalone._closed_python_source_review_sha256_v1(sources),
        tool._source_root(sources),
    )


def test_tool_matches_guard_and_standalone_for_exact_target_bindings() -> None:
    first = _all_hashes(_sources(b"1", b"2"))
    second = _all_hashes(_sources(b"3", b"4"))
    assert len(set(first)) == 1
    assert first == second
    assert first[0] == GOLDEN


def test_tool_keeps_an_impostor_pin_assignment_in_reviewed_bytes() -> None:
    first = _sources()
    second = _sources()
    first["runtime/pin_probe.py"] = _pin(GUARD_NAME, b"5")
    second["runtime/pin_probe.py"] = _pin(GUARD_NAME, b"6")
    first_hashes = _all_hashes(first)
    second_hashes = _all_hashes(second)
    assert len(set(first_hashes)) == 1
    assert len(set(second_hashes)) == 1
    assert first_hashes != second_hashes


def test_tool_accepts_and_hashes_the_admin_public_alias() -> None:
    sources = _sources()
    baseline = _all_hashes(sources)
    sources[ADMIN_PATH] += GUARD_NAME + b" = " + ADMIN_NAME + b"\n"
    with_alias = _all_hashes(sources)
    assert len(set(with_alias)) == 1
    assert with_alias != baseline


@pytest.mark.parametrize("case", ["missing", "duplicate", "wrong_name"])
def test_tool_fails_closed_for_invalid_binding_shape(case: str) -> None:
    sources = _sources()
    if case == "missing":
        del sources[ADMIN_PATH]
    elif case == "duplicate":
        sources[GUARD_PATH] *= 2
    else:
        sources[ADMIN_PATH] = _pin(GUARD_NAME, b"2")
    with pytest.raises(SystemExit, match="pin binding"):
        _load_tool()._source_root(sources)
