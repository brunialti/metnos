"""Contracts for the canonical legacy-state preflight projection."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from executor_birth_crypto_framing import framed_sha256_v1
import executor_birth_admin_preflight as standalone
import executor_birth_legacy_state_preflight_projection as projection


ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "runtime" / "executor_birth_admin_preflight.py"
TOOL = ROOT / "internal/tools/render_contract_boundary_policy.py"
GOLDEN_DIGEST_V1 = (
    "sha256:0ecdbe19957f3cb1a94814c9c390e438d66691e7ee7cd7dba38ac8c997c61c59"
)


def _framed_binding_v1(*items: bytes) -> bytes:
    result = bytearray()
    for item in items:
        result.extend(len(item).to_bytes(8, "big"))
        result.extend(item)
    return bytes(result)


def test_projection_is_checked_deterministic_and_binds_embedded_payload() -> None:
    source = PREFLIGHT.read_bytes()
    first = projection.render_generated_region_v1()
    assert first == projection.render_generated_region_v1()
    assert first.isascii() and b"\r" not in first and first.endswith(b"\n")
    assert projection.check_generated_region_v1(source)
    projection.require_generated_region_v1(source)
    assert projection.projection_digest_v1() == GOLDEN_DIGEST_V1
    assert standalone._LEGACY_STATE_PROJECTION_SHA256_V1 == GOLDEN_DIGEST_V1
    payload = projection.canonical_payload_v1()
    assert standalone._LEGACY_STATE_CANONICAL_ASCII_V1 == payload
    owner, _fields, _helpers, body = projection._owner_material_v1()
    binding = _framed_binding_v1(
        projection.OWNER_PATH_V1.encode("ascii"), owner, payload, body,
    )
    assert framed_sha256_v1(
        projection.PROJECTION_DIGEST_DOMAIN_V1, binding,
    ) == standalone._LEGACY_STATE_PROJECTION_SHA256_V1
    assert body in first


def test_projection_rejects_stale_and_invalid_markers() -> None:
    source = PREFLIGHT.read_bytes()
    digest = GOLDEN_DIGEST_V1.encode("ascii")
    tampered = source.replace(digest, b"sha256:" + b"0" * 64, 1)
    assert not projection.check_generated_region_v1(tampered)
    repaired = projection.replace_generated_region_v1(tampered)
    assert projection.check_generated_region_v1(repaired)
    assert projection.replace_generated_region_v1(repaired) == repaired
    missing = source.replace(projection.END_MARKER_V1, b"missing-marker", 1)
    duplicate = source + projection.BEGIN_MARKER_V1 + b"\n"
    reversed_markers = (
        projection.END_MARKER_V1 + b"\nbody\n"
        + projection.BEGIN_MARKER_V1 + b"\n"
    )
    for invalid in (missing, duplicate, reversed_markers):
        with pytest.raises(projection.LegacyStatePreflightProjectionError):
            projection.check_generated_region_v1(invalid)


def test_owner_source_drift_makes_checked_region_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = projection._read_owner_v1()
    monkeypatch.setattr(projection, "_read_owner_v1", lambda: owner + b"# drift\n")
    assert not projection.check_generated_region_v1(PREFLIGHT.read_bytes())
    with pytest.raises(
        projection.LegacyStatePreflightProjectionError, match="stale",
    ):
        projection.require_generated_region_v1(PREFLIGHT.read_bytes())


def test_projection_rejects_owner_profile_and_free_name_mutants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = projection._read_owner_v1()
    with monkeypatch.context() as scoped:
        scoped.setattr(
            projection, "_read_owner_v1",
            lambda: owner.replace(
                b"from __future__ import annotations\n",
                b"from __future__ import annotations\nPROBE = 1\n", 1,
            ),
        )
        with pytest.raises(
            projection.LegacyStatePreflightProjectionError,
            match="owner_top_level|owner_profile",
        ):
            projection.render_generated_region_v1()
    with monkeypatch.context() as scoped:
        scoped.setattr(
            projection, "_read_owner_v1",
            lambda: owner.replace(
                b"return type(value) is str and digest_pattern.fullmatch(value) is not None",
                b"return forbidden_probe(value)", 1,
            ),
        )
        with pytest.raises(
            projection.LegacyStatePreflightProjectionError,
            match="definition_free_names",
        ):
            projection.render_generated_region_v1()


def test_projection_tool_check_and_isolated_standalone_import() -> None:
    checked = subprocess.run(
        [sys.executable, str(TOOL), "--check"], capture_output=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr.decode()
    code = (
        "import runpy;"
        f"v=runpy.run_path({str(PREFLIGHT)!r},run_name='preflight_probe');"
        "print(v['_LEGACY_STATE_PROJECTION_SHA256_V1'])"
    )
    isolated = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code], capture_output=True,
        check=False, env=dict(os.environ, PYTHONHASHSEED="777"),
    )
    assert isolated.returncode == 0, isolated.stderr.decode()
    assert isolated.stdout.decode().strip() == GOLDEN_DIGEST_V1
