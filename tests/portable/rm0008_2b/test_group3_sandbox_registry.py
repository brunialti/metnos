"""The sandbox backend is measured once, frozen in the set, and handed over.

Group 2 declared ``sandbox_registry`` prepared only: on Linux the two programs
that run a phase were whatever the environment named at that instant.  These
cells follow the measurement from the installer's door to the runner.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_runner import LinuxSandboxRegistry
from executor_birth_sandbox_registry_v1 import (
    MEASURED_STATE_V1, SANDBOX_CONTAINER_BASENAME_V1,
    SANDBOX_REGISTRY_BASENAME_V1, SandboxRegistryError,
    decode_sandbox_registry_v1, measure_sandbox_backend_v1,
)

from . import support

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason=support.WINDOWS_BLOCKER_V1
)


def _prepared(tmp_path: Path, monkeypatch) -> Path:
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    support.provision(monkeypatch, base)
    support.use_config(monkeypatch, base)
    return base


def _document(base: Path) -> Path:
    return (support.installed_set(base) / SANDBOX_CONTAINER_BASENAME_V1
            / SANDBOX_REGISTRY_BASENAME_V1)


def test_the_installer_freezes_the_measurement_in_the_set(
    tmp_path: Path, monkeypatch,
):
    """The document exists, is canonical, and says what it measured."""
    base = _prepared(tmp_path, monkeypatch)
    raw = _document(base).read_bytes()
    assert raw == support.canonical_json(json.loads(raw))
    value = json.loads(raw)
    assert set(value) == {
        "schema_version", "platform", "state", "reason", "programs"
    }
    if value["state"] == MEASURED_STATE_V1:
        assert set(value["programs"]) == {"bwrap", "interpreter"}
        for entry in value["programs"].values():
            assert Path(entry["path"]).is_absolute()
            assert len(entry["sha256"]) == 64
    else:
        assert value["programs"] == {} and value["reason"]


def test_the_runtime_hands_the_runner_the_measured_backend(
    tmp_path: Path, monkeypatch,
):
    """What the installer measured is what the core will launch."""
    import executor_birth_prepared_root as door

    base = _prepared(tmp_path, monkeypatch)
    sealed = door.load_sealed_authorities_v1()
    value = json.loads(_document(base).read_bytes())

    if value["state"] != MEASURED_STATE_V1:
        assert sealed.sandbox is None
        pytest.skip(f"no backend on this machine: {value['reason']}")
    assert isinstance(sealed.sandbox, LinuxSandboxRegistry)
    assert str(sealed.sandbox.bwrap_path) == value["programs"]["bwrap"]["path"]
    assert sealed.sandbox.bwrap_binary_hash == value["programs"]["bwrap"]["sha256"]
    assert str(sealed.sandbox.interpreter_path) == (
        value["programs"]["interpreter"]["path"]
    )


def test_editing_the_frozen_measurement_stops_the_gate(
    tmp_path: Path, monkeypatch,
):
    """The set records its digest, so a later edit is refused, not adopted."""
    import executor_birth_prepared_root as door
    from executor_birth_prepared_set import PreparedSetError

    base = _prepared(tmp_path, monkeypatch)
    value = json.loads(_document(base).read_bytes())
    value["reason"] = "tampered"
    support.write(_document(base), support.canonical_json(value), 0o644)

    with pytest.raises(PreparedSetError, match="birth_prepared_set_mismatch"):
        door.load_sealed_authorities_v1()


def test_a_machine_without_a_backend_says_so_instead_of_pretending():
    """An unavailable document names its reason and carries no program."""
    unavailable = support.canonical_json({
        "schema_version": 1, "platform": "linux", "state": "unavailable",
        "reason": "bwrap_absent", "programs": {},
    })
    assert decode_sandbox_registry_v1(unavailable) is None

    lying = support.canonical_json({
        "schema_version": 1, "platform": "linux", "state": "unavailable",
        "reason": "bwrap_absent",
        "programs": {"bwrap": {"path": "/x", "sha256": "0" * 64}},
    })
    with pytest.raises(SandboxRegistryError, match="sandbox_registry_invalid"):
        decode_sandbox_registry_v1(lying)


def test_a_document_that_is_not_canonical_is_refused():
    """Byte-for-byte canonical, like every other document of the set."""
    raw = measure_sandbox_backend_v1()
    with pytest.raises(SandboxRegistryError, match="sandbox_registry_noncanonical"):
        decode_sandbox_registry_v1(b" " + raw)
