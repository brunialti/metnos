"""Certification of the provisioning transaction against a real session.

The journal is exercised through the very filesystem capability of increment
2A: no name is written by a shortcut, so what these tests observe is what a
resumed run would find on disk.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import executor_birth_provisioning as provisioning
from executor_birth_provisioning import (
    BirthProvisioningError, CheckpointV1, ProvisioningStateV1,
    TransactionHeaderV1, empty_digests_v1, new_transaction_id_v1,
)

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="the Windows profile is certified by its own job"
)

BUILD = "rm0008-group2-2b"


def _layout(tmp_path: Path, *, with_source: bool = False):
    base = tmp_path / "config"
    (base / "birth").mkdir(mode=0o755, parents=True)
    if with_source:
        (base / "keys").mkdir(mode=0o700)
    return provisioning.open_provisioning_layout_v1(
        base, provisioner_build_id=BUILD,
    )


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


def _open(tmp_path: Path, **kwargs):
    layout = _layout(tmp_path, **kwargs)
    session = layout.open_root_session()
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    return layout, session, journal, transaction


def test_header_and_chain_survive_a_reopened_session(tmp_path: Path):
    layout, session, journal, transaction = _open(tmp_path)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            zero = _zero(transaction)
            journal.append(zero)
            journal.append(_next(zero, ProvisioningStateV1.author_staged))

    second = provisioning.open_provisioning_layout_v1(
        tmp_path / "config", provisioner_build_id=BUILD,
    )
    reopened = second.open_root_session()
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
    layout, session, journal, transaction = _open(tmp_path)
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


def test_a_pending_checkpoint_is_reported_not_read(tmp_path: Path):
    layout, session, journal, transaction = _open(tmp_path)
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
def test_ambiguous_shapes_are_refused(tmp_path: Path, case: str):
    layout, session, journal, transaction = _open(tmp_path)
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
                    tmp_path / "config" / "birth"
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


def test_a_broken_predecessor_is_a_conflict(tmp_path: Path):
    layout, session, journal, transaction = _open(tmp_path)
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


def test_a_state_never_goes_back(tmp_path: Path):
    layout, session, journal, transaction = _open(tmp_path)
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


def test_a_document_of_another_transaction_is_refused(tmp_path: Path):
    layout, session, journal, transaction = _open(tmp_path)
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


def test_writing_twice_is_a_conflict_not_a_replacement(tmp_path: Path):
    layout, session, journal, transaction = _open(tmp_path)
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


def test_the_journal_needs_the_exclusive_global_lock(tmp_path: Path):
    layout, session, journal, transaction = _open(tmp_path)
    with session:
        with pytest.raises(BirthProvisioningError) as error:
            journal.create_root()
    assert error.value.code == "birth_provisioning_lock_unsafe"


def test_an_absent_author_source_is_a_state(tmp_path: Path):
    layout = _layout(tmp_path)
    assert layout.author_source is None
    layout.close_author_source()


def test_a_present_author_source_is_opened_read_only(tmp_path: Path):
    layout = _layout(tmp_path, with_source=True)
    assert layout.author_source is not None
    assert not hasattr(layout.author_source, "create_file_exclusive")
    assert not hasattr(layout.author_source, "global_lock")
    layout.close_author_source()


def test_an_unsafe_author_source_is_refused_not_ignored(tmp_path: Path):
    base = tmp_path / "config"
    (base / "birth").mkdir(mode=0o755, parents=True)
    (base / "keys").mkdir(mode=0o777)
    with pytest.raises(BirthProvisioningError) as error:
        provisioning.open_provisioning_layout_v1(
            base, provisioner_build_id=BUILD,
        )
    assert error.value.code == "birth_provisioning_acl_unsafe"


def test_the_root_descriptor_is_consumed_once(tmp_path: Path):
    layout = _layout(tmp_path)
    session = layout.open_root_session()
    with session:
        with pytest.raises(BirthProvisioningError) as error:
            layout.open_root_session()
    assert error.value.code == "birth_provisioning_io_unavailable"


def test_the_layout_refuses_a_descriptor_it_did_not_receive(tmp_path: Path):
    with pytest.raises(BirthProvisioningError) as error:
        provisioning.ProvisioningLayoutV1(
            root=object(), author_source=None, provisioner_build_id=BUILD,
        )
    assert error.value.code == "birth_provisioning_io_unavailable"
    layout = _layout(tmp_path)
    with pytest.raises(BirthProvisioningError):
        provisioning.ProvisioningLayoutV1(
            root=layout.root, author_source=None, provisioner_build_id="",
        )


def test_the_productive_catalogue_is_the_whole_closed_grammar():
    from executor_birth_secure_fs import _BirthRolePatternV1

    catalog = provisioning._productive_role_catalog_v1()
    assert catalog.patterns == tuple(_BirthRolePatternV1)
    assert catalog.exact_bindings == () and catalog.generation == 0
