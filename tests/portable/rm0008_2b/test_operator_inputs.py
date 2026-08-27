"""Acquisition of the two public registries the administrator installs.

Nothing is generated here to make a run succeed: a registry that is missing
keeps the provisioning incomplete, and a registry that is malformed is a
refusal.  The keys of the approver and of the reviewer never enter Birth.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from install import birth_authority_provisioner as provisioning
from install.birth_authority_provisioner import (
    BirthProvisioningError, acquire_operator_inputs_v1,
)

from . import support

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="the Windows profile is certified by its own job"
)


def _acquire(base: Path, monkeypatch):
    layout = support.open_layout(monkeypatch, base)
    try:
        return acquire_operator_inputs_v1(layout.operator_input)
    finally:
        layout.birth_session.close()


def test_a_complete_operator_input_is_acquired(tmp_path: Path, monkeypatch):
    base = support.make_config(tmp_path)
    keys = support.complete_operator_input(base)
    inputs = _acquire(base, monkeypatch)

    location = base / "birth" / "operator-input-v1"
    assert inputs.approval_document == (
        location / "approval-authority.json"
    ).read_bytes()
    assert inputs.semantic_document == (
        location / "semantic-authority.json"
    ).read_bytes()
    assert json.loads(inputs.approval_document)["schema_version"] == 1
    assert dict(inputs.semantic_publics) == keys
    assert len(inputs.approval_sha256) == 64 and len(inputs.semantic_sha256) == 64


def test_the_digests_follow_the_bytes(tmp_path: Path, monkeypatch):
    first = support.make_config(tmp_path / "a")
    keys = support.complete_operator_input(first)
    one = _acquire(first, monkeypatch)

    second = support.make_config(tmp_path / "b")
    support.install_operator_input(
        second,
        approval=one.approval_document,
        semantic=one.semantic_document,
        keys=keys,
    )
    two = _acquire(second, monkeypatch)
    assert two.approval_sha256 == one.approval_sha256
    assert two.semantic_sha256 == one.semantic_sha256

    third = support.make_config(tmp_path / "c")
    other = {"review.pub": support.public_bytes(Ed25519PrivateKey.generate())}
    support.install_operator_input(
        third,
        approval=one.approval_document,
        semantic=one.semantic_document,
        keys=other,
    )
    three = _acquire(third, monkeypatch)
    assert three.approval_sha256 == one.approval_sha256
    assert three.semantic_sha256 != one.semantic_sha256


def test_a_missing_approval_registry_keeps_it_incomplete(
    tmp_path: Path, monkeypatch,
):
    base = support.make_config(tmp_path)
    keys = {"review.pub": support.public_bytes(Ed25519PrivateKey.generate())}
    support.install_operator_input(
        base, semantic=support.semantic_document(tuple(keys)), keys=keys,
    )
    with pytest.raises(BirthProvisioningError) as error:
        _acquire(base, monkeypatch)
    assert error.value.code == "birth_approval_authority_input_missing"


def test_a_missing_semantic_authority_keeps_it_incomplete(
    tmp_path: Path, monkeypatch,
):
    base = support.make_config(tmp_path)
    support.install_operator_input(base, approval=support.approval_document())
    with pytest.raises(BirthProvisioningError) as error:
        _acquire(base, monkeypatch)
    assert error.value.code == "birth_semantic_authority_input_missing"


def test_an_unexpected_file_beside_the_registries_is_ambiguous(
    tmp_path: Path, monkeypatch,
):
    base = support.make_config(tmp_path)
    support.complete_operator_input(base)
    support.write(
        base / "birth" / "operator-input-v1" / "notes.txt", b"x", 0o644,
    )
    with pytest.raises(BirthProvisioningError) as error:
        _acquire(base, monkeypatch)
    assert error.value.code == "birth_provisioning_recovery_ambiguous"


@pytest.mark.parametrize("case", [
    "not-canonical", "unknown-field", "no-keys", "empty-scope",
    "unknown-key-id", "not-a-document",
])
def test_a_malformed_approval_registry_is_refused(
    tmp_path: Path, monkeypatch, case: str,
):
    base = support.make_config(tmp_path)
    keys = {"review.pub": support.public_bytes(Ed25519PrivateKey.generate())}
    document = json.loads(support.approval_document())
    if case == "not-canonical":
        payload = json.dumps(document, indent=2).encode("utf-8")
    elif case == "not-a-document":
        payload = b"not json"
    else:
        if case == "unknown-field":
            document["extra"] = 1
        elif case == "no-keys":
            document["keys"] = {}
        elif case == "empty-scope":
            document["actors"]["operator"]["scopes"] = []
        else:
            document["actors"]["operator"]["key_ids"] = ["absent"]
        payload = support.canonical_json(document)
    support.install_operator_input(
        base, approval=payload,
        semantic=support.semantic_document(tuple(keys)), keys=keys,
    )
    with pytest.raises(BirthProvisioningError) as error:
        _acquire(base, monkeypatch)
    assert error.value.code == "birth_approval_authority_invalid"


@pytest.mark.parametrize("case", [
    "unknown-field", "wrong-evidence-dir", "missing-kind", "empty-owner",
    "no-verifier", "absent-key", "unreferenced-key", "escaping-path",
    "short-key", "not-canonical",
])
def test_a_malformed_semantic_authority_is_refused(
    tmp_path: Path, monkeypatch, case: str,
):
    base = support.make_config(tmp_path)
    keys = {"review.pub": support.public_bytes(Ed25519PrivateKey.generate())}
    document = json.loads(support.semantic_document(tuple(keys)))
    payload = None
    if case == "unknown-field":
        document["extra"] = 1
    elif case == "wrong-evidence-dir":
        document["evidence_dir"] = "elsewhere"
    elif case == "missing-kind":
        document["versions"].popitem()
    elif case == "empty-owner":
        document["owners"]["human_case"] = []
    elif case == "no-verifier":
        document["verifiers"] = {}
    elif case == "absent-key":
        document["verifiers"]["review-key-0"]["path"] = "public/absent.pub"
    elif case == "unreferenced-key":
        keys["spare.pub"] = support.public_bytes(Ed25519PrivateKey.generate())
    elif case == "escaping-path":
        document["verifiers"]["review-key-0"]["path"] = "../review.pub"
    elif case == "short-key":
        keys["review.pub"] = b"x" * 31
    else:
        payload = json.dumps(document, indent=2).encode("utf-8")
    support.install_operator_input(
        base, approval=support.approval_document(),
        semantic=payload if payload is not None else support.canonical_json(document),
        keys=keys,
    )
    with pytest.raises(BirthProvisioningError) as error:
        _acquire(base, monkeypatch)
    assert error.value.code == "birth_semantic_authority_invalid"


def test_a_revoked_verifier_needs_no_key(tmp_path: Path, monkeypatch):
    base = support.make_config(tmp_path)
    keys = {"review.pub": support.public_bytes(Ed25519PrivateKey.generate())}
    document = json.loads(support.semantic_document(("review.pub", "old.pub")))
    document["verifiers"]["review-key-1"]["status"] = "revoked"
    support.install_operator_input(
        base, approval=support.approval_document(),
        semantic=support.canonical_json(document), keys=keys,
    )
    inputs = _acquire(base, monkeypatch)
    assert set(inputs.semantic_publics) == {"review.pub"}


def test_the_producer_catalogue_is_closed_and_distinct():
    catalog = provisioning.producer_catalog_v1()
    assert len(catalog) == 11
    names = {provisioning.producer_store_name_v1(*item) for item in catalog}
    assert len(names) == len(catalog)
    assert all(
        name.startswith("p-") and len(name) == 66 and
        set(name[2:]) <= set("0123456789abcdef")
        for name in names
    )
    assert provisioning.producer_catalog_sha256_v1(
        catalog
    ) == provisioning.producer_catalog_sha256_v1(catalog)
    assert provisioning.producer_catalog_sha256_v1(
        catalog[:-1]
    ) != provisioning.producer_catalog_sha256_v1(catalog)


def test_the_producer_name_is_derived_not_substituted():
    first = provisioning.producer_store_name_v1("alpha", "beta")
    second = provisioning.producer_store_name_v1("alphabeta", "")
    third = provisioning.producer_store_name_v1("alpha_beta", "")
    assert len({first, second, third}) == 3
