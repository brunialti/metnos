"""Certification of the author root: acquisition, staging and installation.

The previous identity is read from the fixed names alone, the store is built
inside the transaction and it becomes final by a rename without replacement.
Every check observes the real store through the productive loader, on an
installer configuration that lives entirely in a temporary directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import support

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason=support.WINDOWS_BLOCKER_V1
)
from install import birth_authority_provisioner as provisioning
from install.birth_authority_provisioner import (
    AuthorProvisioningOutcomeV1, BirthProvisioningError, ProvisioningStateV1,
    acquire_author_source_v1,
)

posix_only = pytest.mark.skipif(
    os.name == "nt", reason="the fact under test is a POSIX one"
)

BUILD = support.build_id()


def _config(tmp_path: Path, *, author=None, extra=()) -> Path:
    return support.make_config(
        tmp_path, author=author, extra=extra, operator=True,
    )


def _source(base: Path, monkeypatch):
    support.use_config(monkeypatch, base)
    opened = provisioning._resolve_author_source_v1()
    if opened is None:
        raise BirthProvisioningError("birth_author_identity_incomplete")
    try:
        return acquire_author_source_v1(opened)
    finally:
        opened.close()


def _store(base: Path) -> Path:
    """The author store, which stays inside its transaction until 2E."""
    return support.installed_author_store(base)


def _transaction_root(base: Path) -> Path:
    return support.transaction_root(base)


def test_a_first_migration_installs_the_previous_identity(
    tmp_path: Path, monkeypatch,
):
    author = Ed25519PrivateKey.generate()
    peer = Ed25519PrivateKey.generate()
    base = _config(tmp_path, author=author, extra=[
        ("peer_pub.bin", support.public_bytes(peer), 0o644),
        ("peer_priv.bin", support.private_bytes(peer), 0o600),
    ])
    source = _source(base, monkeypatch)
    result = support.provision(monkeypatch, base)

    assert result.outcome is AuthorProvisioningOutcomeV1.installed
    assert result.active_key_id == source.active_key_id
    store = _store(base)
    assert sorted(item.name for item in store.iterdir()) == [
        "birth-keystore.lock", "keystore.json", "private", "public",
    ]
    # Only the default private key travels; the other one is never read.
    assert len(list((store / "private").iterdir())) == 1
    assert len(list((store / "public").iterdir())) == 2
    assert (
        store / "private" / f"{source.active_key_id}.key"
    ).read_bytes() == support.private_bytes(author)

    config = json.loads((store / "keystore.json").read_text())
    assert config["private_file"] == f"private/{source.active_key_id}.key"
    assert [item["key_id"] for item in config["keys"]] == sorted(source.publics)
    assert [item["status"] for item in config["keys"]].count("active") == 1


def test_publication_reopens_once_after_the_verified_checkpoint(
    tmp_path: Path, monkeypatch,
):
    """Staging handles are closed before a non-empty tree is published."""
    base = _config(tmp_path, author=Ed25519PrivateKey.generate())
    support.use_config(monkeypatch, base)
    original = provisioning._open_installer_layout_v1
    sessions = []

    def tracked_layout():
        if sessions:
            assert sessions[-1]._closed is True
        layout = original()
        sessions.append(layout.birth_session)
        return layout

    monkeypatch.setattr(
        provisioning, "_open_installer_layout_v1", tracked_layout,
    )
    result = provisioning.ensure_executor_birth_authorities_prepared()

    assert result.outcome is AuthorProvisioningOutcomeV1.installed
    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert all(session._closed for session in sessions)


def test_the_journal_records_every_step_in_order(tmp_path: Path, monkeypatch):
    base = _config(tmp_path, author=Ed25519PrivateKey.generate())
    source = _source(base, monkeypatch)
    support.provision_until_verified(monkeypatch, base)

    checkpoints = sorted(
        (support.transaction_root(base) / "checkpoints-v1").iterdir(),
        key=lambda item: item.name,
    )
    chain = [
        provisioning.decode_checkpoint_v1(item.read_bytes())
        for item in checkpoints
    ]
    assert [item.state for item in chain] == [
        ProvisioningStateV1.created, ProvisioningStateV1.created,
        ProvisioningStateV1.author_staged, ProvisioningStateV1.inputs_staged,
        ProvisioningStateV1.authorities_staged,
        ProvisioningStateV1.context_staged, ProvisioningStateV1.verified,
    ]
    assert chain[0].digests["author_source_public_inventory_sha256"] is None
    assert chain[1].digests[
        "author_source_public_inventory_sha256"
    ] == source.inventory_sha256
    assert chain[0].payload_inventory == () and len(chain[2].payload_inventory) == 7
    assert len(chain[4].payload_inventory) > len(chain[2].payload_inventory)
    for previous, current in zip(chain, chain[1:]):
        assert current.previous_checkpoint_sha256 == previous.digest()


@posix_only
def test_the_recorded_identity_survives_the_installation(
    tmp_path: Path, monkeypatch,
):
    """A rename does not change an object: what was recorded still describes it."""
    base = _config(tmp_path, author=Ed25519PrivateKey.generate())
    support.provision_until_verified(monkeypatch, base)
    checkpoints = sorted(
        (support.transaction_root(base) / "checkpoints-v1").iterdir(),
        key=lambda item: item.name,
    )
    staged = provisioning.decode_checkpoint_v1(checkpoints[2].read_bytes())
    recorded = {
        item.relative_path: item.platform_identity.inode
        for item in staged.payload_inventory
    }
    support.provision(monkeypatch, base)
    for relative, inode in recorded.items():
        assert (base / "birth" / relative).stat().st_ino == inode


def test_a_second_run_only_inspects(tmp_path: Path, monkeypatch):
    base = _config(tmp_path, author=Ed25519PrivateKey.generate())
    first = support.provision(monkeypatch, base)
    assert first.outcome is AuthorProvisioningOutcomeV1.installed
    before = sorted(
        (item.relative_to(base).as_posix(), item.stat().st_ino)
        for item in _store(base).rglob("*")
    )
    second = support.provision(monkeypatch, base)
    assert second.outcome is AuthorProvisioningOutcomeV1.already_installed
    assert second.active_key_id == first.active_key_id
    assert second.public_inventory_sha256 == first.public_inventory_sha256
    assert second.transaction_id is None
    after = sorted(
        (item.relative_to(base).as_posix(), item.stat().st_ino)
        for item in _store(base).rglob("*")
    )
    assert after == before


def test_a_second_run_needs_no_previous_source(tmp_path: Path, monkeypatch):
    """Section 10.2: the restart converges with the old key directory gone."""
    base = _config(tmp_path, author=Ed25519PrivateKey.generate())
    first = support.provision(monkeypatch, base)
    for item in (base / "keys").iterdir():
        item.unlink()
    (base / "keys").rmdir()
    second = support.provision(monkeypatch, base)
    assert second.outcome is AuthorProvisioningOutcomeV1.already_installed
    assert second.public_inventory_sha256 == first.public_inventory_sha256


def test_an_installation_without_any_author_creates_nothing(
    tmp_path: Path, monkeypatch,
):
    base = _config(tmp_path)
    result = support.prepare_or_defer(monkeypatch, base)
    assert result.outcome is AuthorProvisioningOutcomeV1.author_not_yet_created
    assert sorted(item.name for item in (base / "birth").iterdir()) == [
        "operator-input-v1", "provisioning-v1.lock",
    ]


def test_a_verified_transaction_completes_from_its_own_bytes(
    tmp_path: Path, monkeypatch,
):
    author = Ed25519PrivateKey.generate()
    base = _config(tmp_path, author=author)
    source = _source(base, monkeypatch)
    transaction = support.provision_until_verified(monkeypatch, base)
    # Every external input disappears between the two runs.
    import shutil

    for item in (base / "keys").iterdir():
        item.unlink()
    (base / "keys").rmdir()
    for item in sorted(
        (base / "birth" / "operator-input-v1").rglob("*"), reverse=True,
    ):
        item.unlink() if item.is_file() else item.rmdir()

    result = support.provision(monkeypatch, base)
    assert result.outcome is AuthorProvisioningOutcomeV1.installed
    assert result.transaction_id == transaction
    assert (
        _store(base) / "private" / f"{source.active_key_id}.key"
    ).read_bytes() == support.private_bytes(author)
    assert support.installed_marker(base).is_file()
    assert not any(
        item.name.startswith(provisioning.TRANSACTION_PREFIX_V1)
        for item in (base / "birth").iterdir()
    )


def test_a_transaction_of_another_build_is_a_conflict(
    tmp_path: Path, monkeypatch,
):
    base = _config(tmp_path, author=Ed25519PrivateKey.generate())
    source = _source(base, monkeypatch)
    layout = support.open_layout(monkeypatch, base)
    session = layout.birth_session
    transaction = provisioning.new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(
                provisioning.TransactionHeaderV1(transaction, "another-build")
            )
            journal.ensure_checkpoints()
            zero = provisioning.CheckpointV1(
                transaction, 0, None, ProvisioningStateV1.created, (),
                provisioning.empty_digests_v1(), None,
            )
            journal.append(zero)
            provisioning._record_author_source_v1(journal, zero, source)
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_provisioning_transaction_conflict"


def test_two_transactions_stop_the_provisioner(tmp_path: Path, monkeypatch):
    base = _config(tmp_path, author=Ed25519PrivateKey.generate())
    support.use_config(monkeypatch, base)
    for _ in range(2):
        (base / "birth" / provisioning.transaction_root_name_v1(
            provisioning.new_transaction_id_v1()
        )).mkdir(mode=0o755)
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_provisioning_recovery_ambiguous"


@pytest.mark.parametrize("missing", ["author_priv.bin", "author_pub.bin"])
def test_an_incomplete_identity_is_named_as_such(
    tmp_path: Path, monkeypatch, missing: str,
):
    base = _config(tmp_path, author=Ed25519PrivateKey.generate())
    (base / "keys" / missing).unlink()
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_author_identity_incomplete"


def test_a_public_that_is_not_the_pair_is_refused(tmp_path: Path, monkeypatch):
    author = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    base = _config(tmp_path, author=author)
    support.write(
        base / "keys" / "author_pub.bin", support.public_bytes(other), 0o644,
    )
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_author_identity_mismatch"


@pytest.mark.parametrize("case", ["short", "long", "empty"])
def test_a_malformed_public_is_a_refusal_not_a_key_fewer(
    tmp_path: Path, monkeypatch, case: str,
):
    payload = {"short": b"x" * 31, "long": b"x" * 33, "empty": b""}[case]
    base = _config(tmp_path, author=Ed25519PrivateKey.generate(), extra=[
        ("broken_pub.bin", payload, 0o644),
    ])
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_author_source_invalid"


def test_a_hard_linked_public_stops_the_enumeration(
    tmp_path: Path, monkeypatch,
):
    """The refusal comes one layer lower, before a single byte is read."""
    base = _config(tmp_path, author=Ed25519PrivateKey.generate())
    victim = base / "keys" / "author_pub.bin"
    before = victim.read_bytes()
    os.link(victim, base / "keys" / "copy_pub.bin")
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_provisioning_recovery_ambiguous"
    assert victim.read_bytes() == before


@posix_only
def test_a_symlinked_public_is_never_followed(tmp_path: Path, monkeypatch):
    peer = Ed25519PrivateKey.generate()
    base = _config(tmp_path, author=Ed25519PrivateKey.generate(), extra=[
        ("real_pub.bin", support.public_bytes(peer), 0o644),
    ])
    victim = base / "keys" / "real_pub.bin"
    before = victim.read_bytes()
    (base / "keys" / "link_pub.bin").symlink_to(victim)
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_provisioning_recovery_ambiguous"
    assert victim.read_bytes() == before


def test_an_invalid_final_store_is_not_repaired(tmp_path: Path, monkeypatch):
    base = _config(tmp_path, author=Ed25519PrivateKey.generate())
    support.provision(monkeypatch, base)
    broken = _store(base) / "keystore.json"
    broken.write_bytes(b'{"schema_version":1}')
    os.chmod(broken, 0o600)
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_author_keystore_existing_invalid"
    assert broken.read_bytes() == b'{"schema_version":1}'


def test_a_store_without_a_marker_is_ambiguous(tmp_path: Path, monkeypatch):
    """A final author root alone is a state no conforming stop can produce."""
    base = _config(tmp_path, author=Ed25519PrivateKey.generate())
    support.provision(monkeypatch, base)
    support.installed_marker(base).unlink()
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_provisioning_recovery_ambiguous"
    assert (_store(base) / "keystore.json").exists()


def test_the_two_entries_take_no_cryptographic_parameter():
    """Section 10.6: fixed layout, no path, no key, no mode."""
    import inspect

    for name in (
        "prepare_or_defer_until_legacy_author_exists",
        "ensure_executor_birth_authorities_prepared",
    ):
        entry = getattr(provisioning, name)
        assert list(inspect.signature(entry).parameters) == []


def test_deferring_is_not_an_outcome_of_the_second_entry(
    tmp_path: Path, monkeypatch,
):
    base = _config(tmp_path)
    deferred = support.prepare_or_defer(monkeypatch, base)
    assert deferred.outcome is AuthorProvisioningOutcomeV1.author_not_yet_created
    with pytest.raises(BirthProvisioningError) as error:
        support.provision(monkeypatch, base)
    assert error.value.code == "birth_author_identity_incomplete"


def test_the_build_identifier_follows_the_loaded_code(monkeypatch):
    first = provisioning._provisioner_build_id_v1()
    assert first == provisioning._provisioner_build_id_v1()
    assert first.startswith("birth-provisioner-v1-") and len(first) == 85
