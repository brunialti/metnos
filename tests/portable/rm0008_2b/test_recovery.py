"""Recovery of a transaction interrupted before its first durable steps.

Each shape below is built with the very primitive the provisioner uses, so it
is a state a real stop could leave.  What is certified is convergence: the same
author root, the same public ring, one transaction and nothing left over.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from install import birth_authority_provisioner as provisioning
from install.birth_authority_provisioner import (
    AuthorProvisioningOutcomeV1, BirthProvisioningError, CheckpointV1,
    ProvisioningStateV1, TransactionHeaderV1, empty_digests_v1,
    new_transaction_id_v1,
)

from . import support

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="the Windows profile is certified by its own job"
)

BUILD = support.BUILD

SHAPES = [
    "empty-root",
    "complete-header-pending",
    "partial-header-pending",
    "header-without-container",
    "header-with-empty-container",
    "complete-checkpoint-pending",
    "partial-checkpoint-pending",
]


def _integrity():
    from executor_birth_secure_fs import _BirthObjectRole

    return _BirthObjectRole.birth_integrity_only


def _reference(tmp_path: Path, monkeypatch, author):
    """One complete provisioning, used as the value every shape must reach."""
    base = support.make_config(tmp_path / "reference", author=author, operator=True)
    return base, support.provision(monkeypatch, base)


def _build_shape(session, journal, transaction: str, shape: str) -> None:
    header = TransactionHeaderV1(transaction, BUILD)
    journal.create_root()
    if shape == "empty-root":
        return
    if shape in {"complete-header-pending", "partial-header-pending"}:
        payload = (
            header.encode() if shape == "complete-header-pending"
            else b'{"schema_version":1,"transa'
        )
        session.create_file_exclusive(
            journal.root_components + (
                provisioning.HEADER_PENDING_PREFIX_V1 + transaction,
            ),
            payload, role=_integrity(),
        )
        return
    journal.write_header(header)
    if shape == "header-without-container":
        return
    journal.ensure_checkpoints()
    if shape == "header-with-empty-container":
        return
    zero = CheckpointV1(
        transaction, 0, None, ProvisioningStateV1.created, (),
        empty_digests_v1(), None,
    )
    journal.append(zero)
    one = CheckpointV1(
        transaction, 1, zero.digest(), ProvisioningStateV1.created, (),
        empty_digests_v1(), None,
    )
    payload = (
        one.encode() if shape == "complete-checkpoint-pending"
        else one.encode()[:40]
    )
    session.create_file_exclusive(
        journal.checkpoints_components + (
            provisioning._checkpoint_pending_name_v1(1, transaction),
        ),
        payload, role=_integrity(),
    )


@pytest.mark.parametrize("shape", SHAPES, ids=SHAPES)
def test_an_interrupted_transaction_converges(
    tmp_path: Path, monkeypatch, shape: str,
):
    author = Ed25519PrivateKey.generate()
    _, reference = _reference(tmp_path, monkeypatch, author)

    base = support.make_config(tmp_path / "case", author=author, operator=True)
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            _build_shape(session, journal, transaction, shape)

    result = support.provision(monkeypatch, base)
    assert result.outcome is AuthorProvisioningOutcomeV1.installed
    assert result.transaction_id == transaction
    assert result.public_inventory_sha256 == reference.public_inventory_sha256

    store = support.installed_author_store(base)
    assert sorted(item.name for item in store.iterdir()) == [
        "birth-keystore.lock", "keystore.json", "private", "public",
    ]
    assert support.installed_marker(base).is_file()
    assert (support.installed_set(base) / "set.json").is_file()
    # The transaction that agreed with what was installed is gone, and it left
    # nothing behind.
    assert sorted(item.name for item in (base / "birth").iterdir()) == [
        "author-root-v1", "authority-sets", "operator-input-v1",
        "prepared-v1.json", "provisioning-v1.lock",
    ]


@pytest.mark.parametrize("shape", ["complete-header-pending", "partial-header-pending"])
def test_a_second_child_beside_the_header_pending_is_ambiguous(
    tmp_path: Path, monkeypatch, shape: str,
):
    base = support.make_config(tmp_path, author=Ed25519PrivateKey.generate(), operator=True)
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            _build_shape(session, journal, transaction, shape)
            journal.ensure_checkpoints()
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_provisioning_recovery_ambiguous"


def test_a_complete_header_pending_is_promoted_not_rewritten(
    tmp_path: Path, monkeypatch,
):
    """The promoted object keeps its identity: it is moved, not written again."""
    base = support.make_config(tmp_path, author=Ed25519PrivateKey.generate(), operator=True)
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            _build_shape(session, journal, transaction, "complete-header-pending")
    root = base / "birth" / provisioning.transaction_root_name_v1(transaction)
    pending = root / (provisioning.HEADER_PENDING_PREFIX_V1 + transaction)
    before = pending.stat().st_ino
    module = support.provisioner()
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    journal = module._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.recover_header(
                module.TransactionHeaderV1(transaction, BUILD),
                journal.read_state(),
            )
    assert (root / "transaction-v1.json").stat().st_ino == before


def test_a_partial_header_pending_is_removed_and_rewritten(
    tmp_path: Path, monkeypatch,
):
    base = support.make_config(tmp_path, author=Ed25519PrivateKey.generate(), operator=True)
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            _build_shape(session, journal, transaction, "partial-header-pending")
    root = base / "birth" / provisioning.transaction_root_name_v1(transaction)
    pending = root / (provisioning.HEADER_PENDING_PREFIX_V1 + transaction)
    assert pending.read_bytes() != TransactionHeaderV1(transaction, BUILD).encode()

    module = support.provisioner()
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    journal = module._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.recover_header(
                module.TransactionHeaderV1(transaction, BUILD),
                journal.read_state(),
            )
    # The truncated bytes are gone, not promoted: the name that survives holds
    # a whole header, and the pending no longer exists.  The inode says
    # nothing here, because a filesystem may hand the same one back.
    assert not pending.exists()
    assert provisioning.decode_transaction_header_v1(
        (root / "transaction-v1.json").read_bytes()
    ) == TransactionHeaderV1(transaction, BUILD)


def test_a_pending_of_another_transaction_is_never_adopted(
    tmp_path: Path, monkeypatch,
):
    base = support.make_config(tmp_path, author=Ed25519PrivateKey.generate(), operator=True)
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = new_transaction_id_v1()
    other = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            session.create_file_exclusive(
                journal.root_components + (
                    provisioning.HEADER_PENDING_PREFIX_V1 + transaction,
                ),
                TransactionHeaderV1(other, BUILD).encode(), role=_integrity(),
            )
    # The foreign document is discarded and the header is written again for
    # this transaction: a nonce is never taken from a document.
    module = support.provisioner()
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    journal = module._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.recover_header(
                module.TransactionHeaderV1(transaction, BUILD),
                journal.read_state(),
            )
    root = base / "birth" / provisioning.transaction_root_name_v1(transaction)
    assert provisioning.decode_transaction_header_v1(
        (root / "transaction-v1.json").read_bytes()
    ) == TransactionHeaderV1(transaction, BUILD)
    result = support.provision(monkeypatch, base)
    assert result.transaction_id == transaction


def test_a_checkpoint_pending_that_lies_is_discarded(
    tmp_path: Path, monkeypatch,
):
    base = support.make_config(tmp_path, author=Ed25519PrivateKey.generate(), operator=True)
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            journal.ensure_checkpoints()
            zero = CheckpointV1(
                transaction, 0, None, ProvisioningStateV1.created, (),
                empty_digests_v1(), None,
            )
            journal.append(zero)
            # A whole document whose predecessor digest is wrong: complete, so
            # not partial, and incoherent, so never promoted.
            liar = CheckpointV1(
                transaction, 1, "aa" * 32, ProvisioningStateV1.author_staged,
                (), empty_digests_v1(), None,
            )
            session.create_file_exclusive(
                journal.checkpoints_components + (
                    provisioning._checkpoint_pending_name_v1(1, transaction),
                ),
                liar.encode(), role=_integrity(),
            )
    module = support.provisioner()
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    journal = module._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.recover_checkpoint_pending(journal.read_state())
    checkpoints = base / "birth" / provisioning.transaction_root_name_v1(
        transaction
    ) / "checkpoints-v1"
    assert sorted(item.name for item in checkpoints.iterdir()) == [
        provisioning.checkpoint_name_v1(0),
    ]
    support.provision(monkeypatch, base)


def test_the_marker_is_staged_under_a_pending_like_any_payload(
    tmp_path: Path, monkeypatch,
):
    """The marker lives directly in the transaction root, and still gets one.

    Its parent is the transaction root itself, which the closed grammar had to
    admit: no authoritative name is born final, the marker included.
    """
    from executor_birth_secure_fs import _BirthObjectRole, _pending_payload_parent

    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = new_transaction_id_v1()
    root = provisioning.transaction_root_name_v1(transaction)
    pending = (
        provisioning.PAYLOAD_PENDING_PREFIX_V1 + "0" * 16 + "8191-" + transaction
    )
    assert _pending_payload_parent((root, pending)) == (root,)
    with session:
        resolved = session._resolve_effective_role_binding_v1((root, pending))
    assert resolved.binding.kind.value == "regular_file"
    assert resolved.binding.role is _BirthObjectRole.birth_integrity_only


def test_no_pending_survives_a_complete_provisioning(
    tmp_path: Path, monkeypatch,
):
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    support.provision(monkeypatch, base)
    leftovers = [
        item.name for item in (base / "birth").rglob("*")
        if item.name.startswith((
            provisioning.HEADER_PENDING_PREFIX_V1,
            provisioning.CHECKPOINT_PENDING_PREFIX_V1,
            provisioning.PAYLOAD_PENDING_PREFIX_V1,
            provisioning.TRANSACTION_PREFIX_V1,
        ))
    ]
    assert leftovers == []
