"""Certification of the provisioning transaction against a real session.

The journal is exercised through the very filesystem capability of increment
2A: no name is written by a shortcut, so what these tests observe is what a
resumed run would find on disk.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import _rm0008_2b_support as support
from install import birth_authority_provisioner as provisioning
from install.birth_authority_provisioner import (
    BirthProvisioningError, CheckpointV1, ProvisioningStateV1,
    TransactionHeaderV1, empty_digests_v1, new_transaction_id_v1,
)

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="the Windows profile is certified by its own job"
)

BUILD = support.BUILD


def _integrity():
    from executor_birth_secure_fs import _BirthObjectRole

    return _BirthObjectRole.birth_integrity_only


def _zero(transaction: str) -> CheckpointV1:
    return CheckpointV1(
        transaction, 0, None, ProvisioningStateV1.created, (),
        empty_digests_v1(), None,
    )


def _next(previous: CheckpointV1, state: ProvisioningStateV1) -> CheckpointV1:
    return CheckpointV1(
        previous.transaction_id, previous.checkpoint_sequence + 1,
        previous.digest(), state, previous.payload_inventory,
        dict(previous.digests), previous.set_id,
    )


def _open(tmp_path: Path, monkeypatch, **kwargs):
    base = support.make_config(tmp_path, **kwargs)
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    return base, session, journal, transaction


def test_header_and_chain_survive_a_reopened_session(tmp_path: Path, monkeypatch):
    base, session, journal, transaction = _open(tmp_path, monkeypatch)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            zero = _zero(transaction)
            journal.append(zero)
            journal.append(_next(zero, ProvisioningStateV1.author_staged))

    reopened = support.open_layout(monkeypatch, base).birth_session
    with reopened:
        with reopened.global_lock(exclusive=True, create=True):
            state = provisioning._TransactionJournalV1(
                reopened, transaction
            ).read_state()
    assert state.header == TransactionHeaderV1(transaction, BUILD)
    assert [item.checkpoint_sequence for item in state.chain] == [0, 1]
    assert [item.state.value for item in state.chain] == ["created", "author_staged"]
    assert state.pending_checkpoint_sequence is None and not state.header_pending
    assert state.last.state is ProvisioningStateV1.author_staged


def test_no_authoritative_name_is_born_final(tmp_path: Path, monkeypatch):
    base, session, journal, transaction = _open(tmp_path, monkeypatch)
    calls: list[tuple[str, tuple[str, ...]]] = []
    original_create = type(session).create_file_exclusive
    original_rename = type(session).rename_no_replace
    original_read = type(session).read_file

    def create(self, components, payload, *, role):
        calls.append(("create", components))
        return original_create(self, components, payload, role=role)

    def rename(self, source, destination, *, directory):
        calls.append(("rename", source + ("->",) + destination))
        return original_rename(self, source, destination, directory=directory)

    def read(self, components, *, maximum, role=None):
        calls.append(("read", components))
        return original_read(self, components, maximum=maximum, role=role)

    monkeypatch.setattr(type(session), "create_file_exclusive", create)
    monkeypatch.setattr(type(session), "rename_no_replace", rename)
    monkeypatch.setattr(type(session), "read_file", read)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
    root = provisioning.transaction_root_name_v1(transaction)
    pending = provisioning.HEADER_PENDING_PREFIX_V1 + transaction
    assert calls == [
        ("create", (root, pending)),
        ("read", (root, pending)),
        ("rename", (root, pending, "->", root, "transaction-v1.json")),
    ]


def test_a_pending_checkpoint_is_reported_not_read(tmp_path: Path, monkeypatch):
    base, session, journal, transaction = _open(tmp_path, monkeypatch)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            journal.append(_zero(transaction))
            session.create_file_exclusive(
                journal.checkpoints_components + (
                    provisioning._checkpoint_pending_name_v1(1, transaction),
                ),
                b"", role=_integrity(),
            )
            state = journal.read_state()
    assert [item.checkpoint_sequence for item in state.chain] == [0]
    assert state.pending_checkpoint_sequence == 1


@pytest.mark.parametrize("case", [
    "second-pending", "pending-out-of-order", "foreign-name-in-root",
    "foreign-name-in-checkpoints", "gap-in-the-chain",
])
def test_ambiguous_shapes_are_refused(
    tmp_path: Path, monkeypatch, case: str,
):
    base, session, journal, transaction = _open(tmp_path, monkeypatch)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            zero = _zero(transaction)
            journal.append(zero)
            if case == "second-pending":
                for sequence in (1, 2):
                    session.create_file_exclusive(
                        journal.checkpoints_components + (
                            provisioning._checkpoint_pending_name_v1(
                                sequence, transaction,
                            ),
                        ),
                        b"", role=_integrity(),
                    )
            elif case == "pending-out-of-order":
                session.create_file_exclusive(
                    journal.checkpoints_components + (
                        provisioning._checkpoint_pending_name_v1(4, transaction),
                    ),
                    b"", role=_integrity(),
                )
            elif case == "foreign-name-in-root":
                session.create_file_exclusive(
                    journal.root_components + ("prepared-v1.json",),
                    b"{}", role=_integrity(),
                )
            elif case == "foreign-name-in-checkpoints":
                # The catalogue refuses this name, so an intruder is simulated
                # from outside the capability, which is where it would come
                # from in reality.
                stray = (
                    base / "birth"
                    / provisioning.transaction_root_name_v1(transaction)
                    / "checkpoints-v1" / "stray.txt"
                )
                stray.write_bytes(b"")
            else:
                two = _next(_next(zero, ProvisioningStateV1.author_staged),
                            ProvisioningStateV1.inputs_staged)
                journal.append(two)
            with pytest.raises(BirthProvisioningError) as error:
                journal.read_state()
    assert error.value.code == "birth_provisioning_recovery_ambiguous"


def test_a_broken_predecessor_is_a_conflict(tmp_path: Path, monkeypatch):
    base, session, journal, transaction = _open(tmp_path, monkeypatch)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            zero = _zero(transaction)
            journal.append(zero)
            broken = CheckpointV1(
                transaction, 1, "aa" * 32, ProvisioningStateV1.author_staged,
                (), empty_digests_v1(), None,
            )
            journal.append(broken)
            with pytest.raises(BirthProvisioningError) as error:
                journal.read_state()
    assert error.value.code == "birth_provisioning_transaction_conflict"


def test_a_state_never_goes_back(tmp_path: Path, monkeypatch):
    base, session, journal, transaction = _open(tmp_path, monkeypatch)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            zero = _zero(transaction)
            journal.append(zero)
            staged = _next(zero, ProvisioningStateV1.author_staged)
            journal.append(staged)
            journal.append(_next(staged, ProvisioningStateV1.created))
            with pytest.raises(BirthProvisioningError) as error:
                journal.read_state()
    assert error.value.code == "birth_provisioning_transaction_conflict"


def test_a_document_of_another_transaction_is_refused(tmp_path: Path, monkeypatch):
    base, session, journal, transaction = _open(tmp_path, monkeypatch)
    other = new_transaction_id_v1()
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            with pytest.raises(BirthProvisioningError) as header_error:
                journal.write_header(TransactionHeaderV1(other, BUILD))
            with pytest.raises(BirthProvisioningError) as checkpoint_error:
                journal.append(_zero(other))
    assert header_error.value.code == "birth_provisioning_transaction_conflict"
    assert checkpoint_error.value.code == "birth_provisioning_transaction_conflict"


def test_writing_twice_is_a_conflict_not_a_replacement(tmp_path: Path, monkeypatch):
    base, session, journal, transaction = _open(tmp_path, monkeypatch)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            with pytest.raises(BirthProvisioningError):
                journal.write_header(TransactionHeaderV1(transaction, BUILD))
            journal.append(_zero(transaction))
            with pytest.raises(BirthProvisioningError):
                journal.append(_zero(transaction))
            state = journal.read_state()
            names = session.inventory(journal.checkpoints_components)
    assert [item.checkpoint_sequence for item in state.chain] == [0]
    # A promotion that did not happen leaves nothing behind: the invariant of
    # at most one pending under the lock survives the failure.
    assert names == ("0" * 20 + ".json",)


def test_the_journal_needs_the_exclusive_global_lock(tmp_path: Path, monkeypatch):
    base, session, journal, transaction = _open(tmp_path, monkeypatch)
    with session:
        with pytest.raises(BirthProvisioningError) as error:
            journal.create_root()
    assert error.value.code == "birth_provisioning_lock_unsafe"


def test_an_absent_author_source_is_a_state(tmp_path: Path, monkeypatch):
    support.use_config(monkeypatch, support.make_config(tmp_path))
    assert provisioning._resolve_author_source_v1() is None


def test_a_present_author_source_is_opened_read_only(
    tmp_path: Path, monkeypatch,
):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    base = support.make_config(tmp_path, author=Ed25519PrivateKey.generate())
    support.use_config(monkeypatch, base)
    opened = provisioning._resolve_author_source_v1()
    assert opened is not None
    assert not hasattr(opened, "create_file_exclusive")
    assert not hasattr(opened, "global_lock")
    opened.close()


def test_an_unsafe_author_source_is_refused_not_ignored(
    tmp_path: Path, monkeypatch,
):
    base = support.make_config(tmp_path)
    (base / "keys").mkdir(mode=0o777)
    support.use_config(monkeypatch, base)
    with pytest.raises(BirthProvisioningError) as error:
        provisioning._resolve_author_source_v1()
    assert error.value.code == "birth_provisioning_acl_unsafe"
