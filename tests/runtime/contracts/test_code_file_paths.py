from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_server
from code_file_paths import (
    PortableCodePathError,
    validate_portable_code_files,
    validate_portable_code_path,
)
from executor_standard import validate_manifest
from invocations import InvocationError


_VECTORS = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "code_file_path_vectors.json"
)


def _error_code(operation) -> str | None:
    try:
        operation()
    except PortableCodePathError as exc:
        return exc.code
    return None


def test_python_and_rust_share_portable_code_path_vectors() -> None:
    vectors = json.loads(_VECTORS.read_text(encoding="utf-8"))
    assert vectors["version"] == 1
    for vector in vectors["single"]:
        assert _error_code(
            lambda value=vector["path"]: validate_portable_code_path(value)
        ) == vector["error"], vector["path"]
    for vector in vectors["sets"]:
        assert _error_code(
            lambda value=vector["files"]: validate_portable_code_files(value)
        ) == vector["error"], vector["files"]


def test_executor_standard_uses_placement_not_executor_identity() -> None:
    device_manifest = {
        "code": {"files": ["A.py", "a.py"]},
        "placement": {"scope": "device"},
    }
    server_manifest = {
        "code": {"files": ["../../shared.py"]},
        "placement": {"scope": "server"},
    }

    device_codes = {finding.code for finding in validate_manifest(device_manifest)}
    server_codes = {finding.code for finding in validate_manifest(server_manifest)}

    assert "code_path_collision" in device_codes
    assert "code_path_segment" not in server_codes


def test_bundle_boundary_fails_with_stable_error_before_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = SimpleNamespace(
        manifest_bytes=b"manifest",
        signature_bytes=b"signature",
        code_files=(("safe.py", b"safe"), ("AUX.py", b"unsafe")),
    )
    monkeypatch.setattr(
        agent_server.invocations,
        "load_executor_artifact",
        lambda _name: artifact,
    )

    with pytest.raises(
        InvocationError,
        match=r"^wire_code_files_invalid:code_path_reserved$",
    ):
        agent_server._executor_bundle_payload("sample")


def test_bundle_boundary_preserves_valid_signed_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = SimpleNamespace(
        manifest_bytes=b"manifest",
        signature_bytes=b"signature",
        code_files=(("pkg/main.py", b"main"), ("pkg/util.py", b"util")),
    )
    monkeypatch.setattr(
        agent_server.invocations,
        "load_executor_artifact",
        lambda _name: artifact,
    )

    payload = agent_server._executor_bundle_payload("sample")

    assert tuple(payload["files"]) == ("pkg/main.py", "pkg/util.py")
