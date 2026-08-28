"""Windows publishes the prepared tree and carries its sandbox fact through."""
from __future__ import annotations

import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from install.birth_authority_provisioner import AuthorProvisioningOutcomeV1

from . import support

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="this certification cell is Windows-specific"
)


def test_windows_publishes_and_authenticates_its_sandbox_measurement(
    tmp_path, monkeypatch,
):
    """One real installation proves publication and the group-3 handoff."""
    import executor_birth_prepared_root as prepared_root
    from executor_birth_sandbox_registry_v1 import (
        SANDBOX_CONTAINER_BASENAME_V1,
        SANDBOX_REGISTRY_BASENAME_V1,
        UNAVAILABLE_STATE_V1,
    )

    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    result = support.provision(monkeypatch, base)

    assert result.outcome is AuthorProvisioningOutcomeV1.installed
    registry = (
        support.installed_set(base)
        / SANDBOX_CONTAINER_BASENAME_V1
        / SANDBOX_REGISTRY_BASENAME_V1
    )
    document = json.loads(registry.read_bytes())
    assert document == {
        "platform": "windows",
        "programs": {},
        "reason": "windows_backend_not_measured",
        "schema_version": 1,
        "state": UNAVAILABLE_STATE_V1,
    }
    sealed = prepared_root.load_sealed_authorities_v1()
    assert sealed.sandbox is None
