"""``set.json``: the immutable identity of one prepared authority set.

The document is written only after the context material is durable, because
the identity of the set depends on those bytes.  Nothing in it is chosen by a
caller and nothing private appears.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from install import birth_authority_provisioner as provisioning
from install.birth_authority_provisioner import (
    BirthProvisioningError, PreparedContextMaterialV1, build_set_document_v1,
    producer_catalog_v1,
)

from . import support

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason=support.WINDOWS_BLOCKER_V1
)

EXPECTED_FIELDS = {
    "schema_version", "state", "provisioning_transaction_id",
    "provisioner_build_id", "author_active_key_id", "author_verifier_key_ids",
    "admission_active_key_id", "admission_verifier_key_ids", "producer_keys",
    "approval_authority_sha256", "semantic_authority_sha256",
    "sandbox_registry_sha256",
    "semantic_public_key_ids", "approval_input_sha256", "semantic_input_sha256",
    "producer_catalog_sha256", "context_source_inventory_sha256",
    "prepared_admission_context_id", "prepared_context_epoch",
    "context_material_sha256", "set_id",
}


def _provision(tmp_path: Path, monkeypatch):
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    result = support.provision(monkeypatch, base)
    location = support.installed_set(base)
    return base, location, result


def test_the_set_document_has_the_closed_schema(tmp_path: Path, monkeypatch):
    base, location, result = _provision(tmp_path, monkeypatch)
    payload = (location / "set.json").read_bytes()
    document = json.loads(payload)

    assert set(document) == EXPECTED_FIELDS
    assert document["schema_version"] == 1 and document["state"] == "complete"
    assert document["provisioning_transaction_id"] == result.transaction_id
    assert set(document["producer_keys"]) == {
        f"{producer}:{operation}" for producer, operation in producer_catalog_v1()
    }
    for entry in document["producer_keys"].values():
        assert set(entry) == {"store_name", "active_key_id", "verifier_key_ids"}
        assert entry["store_name"].startswith("p-")
    assert payload == support.canonical_json(document)


def test_the_identity_is_the_digest_of_the_document_without_it(
    tmp_path: Path, monkeypatch,
):
    base, location, _ = _provision(tmp_path, monkeypatch)
    document = json.loads((location / "set.json").read_bytes())
    declared = document.pop("set_id")
    recomputed = hashlib.sha256(
        provisioning.SET_ID_DIGEST_DOMAIN_V1
        + support.canonical_json(document)
    ).hexdigest()
    assert declared == recomputed
    assert len(declared) == 64


def test_the_document_carries_nothing_private(tmp_path: Path, monkeypatch):
    base, location, _ = _provision(tmp_path, monkeypatch)
    text = (location / "set.json").read_text()
    assert "origin" not in text and str(tmp_path) not in text
    for private in location.rglob("*.key"):
        assert private.read_bytes().hex() not in text


def test_the_material_is_durable_before_the_set(tmp_path: Path, monkeypatch):
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    support.provision_until_verified(monkeypatch, base)
    location = support.transaction_root(base) / "authority-set"
    checkpoints = sorted(
        (support.transaction_root(base) / "checkpoints-v1").iterdir(),
        key=lambda item: item.name,
    )
    chain = [
        provisioning.decode_checkpoint_v1(item.read_bytes())
        for item in checkpoints
    ]
    context = next(
        item for item in chain
        if item.state is provisioning.ProvisioningStateV1.context_staged
    )
    verified = next(
        item for item in chain
        if item.state is provisioning.ProvisioningStateV1.verified
    )
    assert context.checkpoint_sequence < verified.checkpoint_sequence
    assert context.digests["context_material_sha256"] == hashlib.sha256(
        (location / "context" / "material-v1.json").read_bytes()
    ).hexdigest()
    assert context.set_id is None
    assert verified.set_id == json.loads(
        (location / "set.json").read_bytes()
    )["set_id"]
    assert verified.digests["set_json_sha256"] == hashlib.sha256(
        (location / "set.json").read_bytes()
    ).hexdigest()
    recorded = {item.relative_path for item in verified.payload_inventory}
    assert "authority-set/set.json" in recorded
    assert "authority-set/context/material-v1.json" in recorded


def test_a_second_run_rewrites_nothing(tmp_path: Path, monkeypatch):
    base, location, first = _provision(tmp_path, monkeypatch)
    before = {
        item.relative_to(location).as_posix(): item.read_bytes()
        for item in location.rglob("*") if item.is_file()
    }
    second = support.provision(monkeypatch, base)
    after = {
        item.relative_to(location).as_posix(): item.read_bytes()
        for item in location.rglob("*") if item.is_file()
    }
    assert second.outcome.value == "already_installed"
    assert second.transaction_id is None
    assert after == before


def test_two_installations_have_different_identities(
    tmp_path: Path, monkeypatch,
):
    _, first_location, _ = _provision(tmp_path / "a", monkeypatch)
    _, second_location, _ = _provision(tmp_path / "b", monkeypatch)
    first = json.loads((first_location / "set.json").read_bytes())
    second = json.loads((second_location / "set.json").read_bytes())
    assert first["set_id"] != second["set_id"]
    # The context material is the same distribution, so only the identities
    # move: the prepared epoch follows the material, not the installation.
    assert first["prepared_context_epoch"] != second["prepared_context_epoch"]


def test_the_derivation_is_a_fixed_function_of_its_inputs():
    prepared = PreparedContextMaterialV1(
        document=b"{}",
        prepared_admission_context_id="sha256:" + "1" * 64,
        prepared_context_epoch="sha256:" + "2" * 64,
        source_inventory_sha256="3" * 64,
        material_sha256="4" * 64,
    )
    registry = {
        "admission": {
            "active_key_id": "admission-key",
            "verifier_key_ids": ["admission-key"],
        },
        "producers": {
            provisioning.producer_store_name_v1(producer, operation): {
                "active_key_id": f"{producer}-{operation}",
                "verifier_key_ids": [f"{producer}-{operation}"],
            }
            for producer, operation in producer_catalog_v1()
        },
        "semantic": {"review-key-0": "active"},
    }
    digests = {
        "approval_input_sha256": "5" * 64,
        "semantic_input_sha256": "6" * 64,
        "producer_catalog_sha256": "7" * 64,
    }
    payload, set_id = build_set_document_v1(
        transaction_id="0" * 32,
        provisioner_build_id="fixed-build",
        author={"active_key_id": "author-key", "verifier_key_ids": ["author-key"]},
        registry=registry,
        catalog=producer_catalog_v1(),
        digests=digests,
        prepared=prepared,
        approval_document=b"approval",
        semantic_document=b"semantic",
        sandbox_document=b"sandbox",
    )
    assert set_id == (
        "d18a1fe5d7d4c0486a92ce087a9e1f0324fc2e1b2b46332ad185b46c3a67c288"
    )
    assert json.loads(payload)["set_id"] == set_id


def test_the_provisioner_never_uses_the_previous_decoder():
    """Section 10.4: the old free-form builder stays, unused by this path."""
    import inspect

    source = inspect.getsource(provisioning)
    assert "executor_birth_bootstrap" not in source
    assert "_context_builder" not in source
    assert "build_admission_context" not in source
    assert "MaterialFile" not in source
