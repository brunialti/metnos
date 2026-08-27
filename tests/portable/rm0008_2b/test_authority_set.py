"""Generation of the authority set inside the transaction.

Admission and every Producer are born here, once and under the confidential
profile of the layout.  The two public registries are copied byte for byte; no
private key of the approver or of the reviewer is created, imported or kept.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from install import birth_authority_provisioner as provisioning
from install.birth_authority_provisioner import (
    BirthProvisioningError, TransactionHeaderV1, acquire_author_source_v1,
    acquire_operator_inputs_v1, new_transaction_id_v1, producer_catalog_v1,
    verify_authority_set_v1,
)

from . import support

pytestmark = pytest.mark.skipif(
    not support.can_set_owner(),
    reason="the owner probe refused: " + support.owner_privilege_reason(),
)

BUILD = support.build_id()


def _stage(tmp_path: Path, monkeypatch, *, author=None, keys=None):
    """Stage the author root and the authority set on one isolated root."""
    author = author or Ed25519PrivateKey.generate()
    base = support.make_config(tmp_path, author=author)
    if keys is None:
        support.complete_operator_input(base)
    else:
        support.install_operator_input(
            base,
            approval=support.approval_document(),
            semantic=support.semantic_document(tuple(keys)),
            keys=keys,
        )
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    inputs = acquire_operator_inputs_v1(layout.operator_input)
    opened = provisioning._resolve_author_source_v1()
    try:
        source = acquire_author_source_v1(opened)
    finally:
        opened.close()
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            journal.ensure_checkpoints()
            staged = provisioning._stage_author_store_v1(session, journal, source)
            authority = provisioning._stage_authority_set_v1(
                session, journal, inputs, producer_catalog_v1(),
                author_publics=source.publics,
                first_object_sequence=staged.next_object_sequence,
            )
            verify_authority_set_v1(
                session, journal.root_components + ("authority-set",), authority,
            )
    root = base / "birth" / provisioning.transaction_root_name_v1(transaction)
    return base, root / "authority-set", authority, inputs, source


def test_every_authority_is_generated_once(tmp_path: Path, monkeypatch):
    base, location, authority, inputs, source = _stage(tmp_path, monkeypatch)

    assert sorted(item.name for item in location.iterdir()) == [
        "admission", "approval", "producers", "semantic",
    ]
    catalog = producer_catalog_v1()
    expected = {
        provisioning.producer_store_name_v1(*item) for item in catalog
    }
    assert {item.name for item in (location / "producers").iterdir()} == expected
    assert set(authority.producer_key_ids) == expected

    identifiers = {authority.admission_key_id, *authority.producer_key_ids.values()}
    assert len(identifiers) == len(catalog) + 1
    assert not identifiers & set(source.publics)

    for store in [location / "admission", *(location / "producers").iterdir()]:
        assert sorted(item.name for item in store.iterdir()) == [
            "birth-keystore.lock", "keystore.json", "private", "public",
        ]
        assert len(list((store / "private").iterdir())) == 1
        assert len(list((store / "public").iterdir())) == 1


def test_the_registries_are_copied_byte_for_byte(tmp_path: Path, monkeypatch):
    base, location, authority, inputs, _ = _stage(tmp_path, monkeypatch)

    assert (
        location / "approval" / "authority.json"
    ).read_bytes() == inputs.approval_document
    assert (
        location / "semantic" / "authority.json"
    ).read_bytes() == inputs.semantic_document
    installed = {
        item.name: item.read_bytes()
        for item in (location / "semantic" / "public").iterdir()
    }
    assert installed == dict(inputs.semantic_publics)
    assert list((location / "semantic" / "evidence").iterdir()) == []


def test_no_operator_private_key_reaches_the_set(tmp_path: Path, monkeypatch):
    base, location, _, _, _ = _stage(tmp_path, monkeypatch)
    private = [
        item for item in location.rglob("*")
        if item.is_file() and item.suffix == ".key"
    ]
    # One private key per generated store and not one more: the approver and
    # the reviewer keep theirs outside Birth.
    assert len(private) == len(producer_catalog_v1()) + 1
    assert all("producers" in item.parts or "admission" in item.parts
               for item in private)


def test_the_confidential_profile_covers_every_private_key(
    tmp_path: Path, monkeypatch,
):
    base, location, _, _, _ = _stage(tmp_path, monkeypatch)
    for item in location.rglob("*"):
        if item.is_file() and item.suffix == ".key":
            assert oct(item.stat().st_mode & 0o777) == "0o600"
            assert oct(item.parent.stat().st_mode & 0o777) == "0o700"
        if item.is_file() and item.suffix == ".pub":
            assert oct(item.stat().st_mode & 0o777) == "0o644"


def test_a_key_shared_between_two_roles_is_refused(tmp_path: Path, monkeypatch):
    author = Ed25519PrivateKey.generate()
    with pytest.raises(BirthProvisioningError) as error:
        _stage(
            tmp_path, monkeypatch, author=author,
            keys={"review.pub": support.public_bytes(author)},
        )
    assert error.value.code == "birth_authority_key_reused"


def test_a_repeated_public_inside_the_operator_input_is_refused(
    tmp_path: Path, monkeypatch,
):
    shared = support.public_bytes(Ed25519PrivateKey.generate())
    with pytest.raises(BirthProvisioningError) as error:
        _stage(
            tmp_path, monkeypatch,
            keys={"one.pub": shared, "two.pub": shared},
        )
    assert error.value.code == "birth_authority_key_reused"


def test_the_staged_inventory_names_every_object(tmp_path: Path, monkeypatch):
    base, location, authority, _, _ = _stage(tmp_path, monkeypatch)
    recorded = {item.relative_path for item in authority.payload_inventory}
    root = location.parent
    observed = {
        item.relative_to(root).as_posix()
        for item in location.rglob("*")
    } | {"authority-set"}
    assert recorded == observed
    for record in authority.payload_inventory:
        target = root / record.relative_path
        assert record.platform_identity.inode == target.stat().st_ino
        if record.object_type.value == "file":
            assert record.size == target.stat().st_size


def test_a_restart_reuses_the_generated_identities(tmp_path: Path, monkeypatch):
    """Section 6.1: a valid transaction is resumed, never generated again."""
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    support.provision_until_verified(monkeypatch, base)
    staged = support.transaction_root(base) / "authority-set"
    before = {
        item.relative_to(staged).as_posix(): item.read_bytes()
        for item in staged.rglob("*") if item.is_file()
    }
    support.provision(monkeypatch, base)
    location = support.installed_set(base)
    after = {
        item.relative_to(location).as_posix(): item.read_bytes()
        for item in location.rglob("*") if item.is_file()
    }
    # The set is moved to its final name, not generated a second time.
    assert after == before


def test_a_stop_after_the_inputs_completes_the_set(tmp_path: Path, monkeypatch):
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    opened = provisioning._resolve_author_source_v1()
    try:
        source = acquire_author_source_v1(opened)
    finally:
        opened.close()
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            journal.ensure_checkpoints()
            zero = provisioning.CheckpointV1(
                transaction, 0, None, provisioning.ProvisioningStateV1.created,
                (), provisioning.empty_digests_v1(), None,
            )
            journal.append(zero)
            acquired = provisioning._record_author_source_v1(
                journal, zero, source,
            )
            staged = provisioning._stage_and_record_v1(
                session, journal, acquired, source,
            )
            provisioning._record_operator_inputs_v1(journal, staged, layout)

    result = support.provision(monkeypatch, base)
    assert result.transaction_id == transaction
    location = support.installed_set(base)
    assert len(list((location / "producers").iterdir())) == len(
        producer_catalog_v1()
    )


def test_a_half_generated_set_is_never_adopted(tmp_path: Path, monkeypatch):
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    opened = provisioning._resolve_author_source_v1()
    try:
        source = acquire_author_source_v1(opened)
    finally:
        opened.close()
    from executor_birth_secure_fs import _BirthObjectRole

    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            journal.ensure_checkpoints()
            zero = provisioning.CheckpointV1(
                transaction, 0, None, provisioning.ProvisioningStateV1.created,
                (), provisioning.empty_digests_v1(), None,
            )
            journal.append(zero)
            acquired = provisioning._record_author_source_v1(
                journal, zero, source,
            )
            staged = provisioning._stage_and_record_v1(
                session, journal, acquired, source,
            )
            provisioning._record_operator_inputs_v1(journal, staged, layout)
            session.create_directory_exclusive(
                journal.root_components + ("authority-set",),
                role=_BirthObjectRole.birth_integrity_only,
            )
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_provisioning_recovery_ambiguous"


def test_an_operator_input_that_changed_is_a_conflict(
    tmp_path: Path, monkeypatch,
):
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    opened = provisioning._resolve_author_source_v1()
    try:
        source = acquire_author_source_v1(opened)
    finally:
        opened.close()
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            journal.ensure_checkpoints()
            zero = provisioning.CheckpointV1(
                transaction, 0, None, provisioning.ProvisioningStateV1.created,
                (), provisioning.empty_digests_v1(), None,
            )
            journal.append(zero)
            acquired = provisioning._record_author_source_v1(
                journal, zero, source,
            )
            staged = provisioning._stage_and_record_v1(
                session, journal, acquired, source,
            )
            provisioning._record_operator_inputs_v1(journal, staged, layout)
    support.install_operator_input(base, approval=support.approval_document())
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_provisioning_transaction_conflict"
