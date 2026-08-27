"""Certification of the author root: acquisition, staging and installation.

The previous identity is read from the fixed names alone, the store is built
inside the transaction and it becomes final by a rename without replacement.
Every check below observes the real store, through the productive loader.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_provisioning as provisioning
from executor_birth_provisioning import (
    BirthProvisioningError, ProvisioningStateV1, TransactionHeaderV1,
    acquire_author_source_v1, install_author_store_v1, new_transaction_id_v1,
    stage_author_store_v1, verify_author_store_v1,
)

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="the Windows profile is certified by its own job"
)

BUILD = "rm0008-group2-2b"


def _private_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _keys_directory(tmp_path: Path, *, author=None, extra=()) -> Path:
    base = tmp_path / "config"
    (base / "birth").mkdir(mode=0o755, parents=True)
    keys = base / "keys"
    keys.mkdir(mode=0o700)
    if author is not None:
        _write(keys / "author_priv.bin", _private_bytes(author), 0o600)
        _write(keys / "author_pub.bin", _public_bytes(author), 0o644)
    for name, payload, mode in extra:
        _write(keys / name, payload, mode)
    return base


def _write(path: Path, payload: bytes, mode: int) -> None:
    path.write_bytes(payload)
    os.chmod(path, mode)


def _source(base: Path):
    layout = provisioning.open_provisioning_layout_v1(
        base, provisioner_build_id=BUILD,
    )
    try:
        return acquire_author_source_v1(layout.author_source), layout
    except BaseException:
        layout.close_author_source()
        raise


def _provision(base: Path, source):
    layout = provisioning.open_provisioning_layout_v1(
        base, provisioner_build_id=BUILD,
    )
    session = layout.open_root_session()
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    with session:
        with session.global_lock(exclusive=True, create=True):
            journal.create_root()
            journal.write_header(TransactionHeaderV1(transaction, BUILD))
            staged = stage_author_store_v1(session, journal, source)
            verify_author_store_v1(
                session,
                journal.root_components + ("author-root-v1",),
                source,
            )
            install_author_store_v1(session, journal)
            loaded = verify_author_store_v1(session, ("author-root-v1",), source)
    layout.close_author_source()
    return staged, loaded


def test_a_first_migration_installs_the_previous_identity(tmp_path: Path):
    author = Ed25519PrivateKey.generate()
    peer = Ed25519PrivateKey.generate()
    base = _keys_directory(tmp_path, author=author, extra=[
        ("peer_pub.bin", _public_bytes(peer), 0o644),
        ("peer_priv.bin", _private_bytes(peer), 0o600),
    ])
    source, layout = _source(base)
    layout.close_author_source()
    staged, loaded = _provision(base, source)

    store = base / "birth" / "author-root-v1"
    assert sorted(item.name for item in store.iterdir()) == [
        "birth-keystore.lock", "keystore.json", "private", "public",
    ]
    # Only the default private key travels; the other one is never read.
    assert len(list((store / "private").iterdir())) == 1
    assert len(list((store / "public").iterdir())) == 2
    assert (
        store / "private" / f"{source.active_key_id}.key"
    ).read_bytes() == _private_bytes(author)
    assert loaded.active_key_id == source.active_key_id
    assert set(loaded.verifier_keys) == set(source.publics)

    config = json.loads((store / "keystore.json").read_text())
    assert config["private_file"] == f"private/{source.active_key_id}.key"
    assert [item["key_id"] for item in config["keys"]] == sorted(source.publics)
    assert [item["status"] for item in config["keys"]].count("active") == 1
    assert staged.next_object_sequence == 5
    assert {item.relative_path for item in staged.payload_inventory} == {
        "author-root-v1", "author-root-v1/private", "author-root-v1/public",
        f"author-root-v1/private/{source.active_key_id}.key",
        "author-root-v1/keystore.json", "author-root-v1/birth-keystore.lock",
    } | {
        f"author-root-v1/public/{key_id}.pub" for key_id in source.publics
    }


def test_the_recorded_identity_survives_the_installation(tmp_path: Path):
    author = Ed25519PrivateKey.generate()
    base = _keys_directory(tmp_path, author=author)
    source, layout = _source(base)
    layout.close_author_source()
    staged, _ = _provision(base, source)
    recorded = {
        item.relative_path: item.platform_identity
        for item in staged.payload_inventory
    }
    for relative, identity in recorded.items():
        observed = (base / "birth" / relative).stat()
        assert identity.device == observed.st_dev
        assert identity.inode == observed.st_ino


def test_the_public_ring_digest_follows_the_ring(tmp_path: Path):
    author = Ed25519PrivateKey.generate()
    peer = Ed25519PrivateKey.generate()
    first = _keys_directory(tmp_path / "a", author=author)
    second = _keys_directory(tmp_path / "b", author=author, extra=[
        ("peer_pub.bin", _public_bytes(peer), 0o644),
    ])
    one, layout_one = _source(first)
    layout_one.close_author_source()
    two, layout_two = _source(second)
    layout_two.close_author_source()
    assert one.inventory_sha256 != two.inventory_sha256
    third, layout_three = _source(first)
    layout_three.close_author_source()
    assert third.inventory_sha256 == one.inventory_sha256
    assert provisioning.author_store_public_inventory_sha256_v1(
        one.publics
    ) != provisioning.author_store_public_inventory_sha256_v1(two.publics)


@pytest.mark.parametrize("missing", ["author_priv.bin", "author_pub.bin"])
def test_an_incomplete_identity_is_named_as_such(tmp_path: Path, missing: str):
    author = Ed25519PrivateKey.generate()
    base = _keys_directory(tmp_path, author=author)
    (base / "keys" / missing).unlink()
    with pytest.raises(BirthProvisioningError) as error:
        _source(base)
    assert error.value.code == "birth_author_identity_incomplete"


def test_a_public_that_is_not_the_pair_is_refused(tmp_path: Path):
    author = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    base = _keys_directory(tmp_path, author=author)
    _write(base / "keys" / "author_pub.bin", _public_bytes(other), 0o644)
    with pytest.raises(BirthProvisioningError) as error:
        _source(base)
    assert error.value.code == "birth_author_identity_mismatch"


@pytest.mark.parametrize("case", ["short", "long", "empty"])
def test_a_malformed_public_is_a_refusal_not_a_key_fewer(
    tmp_path: Path, case: str
):
    author = Ed25519PrivateKey.generate()
    payload = {"short": b"x" * 31, "long": b"x" * 33, "empty": b""}[case]
    base = _keys_directory(tmp_path, author=author, extra=[
        ("broken_pub.bin", payload, 0o644),
    ])
    with pytest.raises(BirthProvisioningError) as error:
        _source(base)
    assert error.value.code == "birth_author_source_invalid"


def test_a_hard_linked_public_stops_the_enumeration(tmp_path: Path):
    """The refusal comes one layer lower, before a single byte is read."""
    author = Ed25519PrivateKey.generate()
    base = _keys_directory(tmp_path, author=author)
    victim = base / "keys" / "author_pub.bin"
    before = victim.read_bytes()
    os.link(victim, base / "keys" / "copy_pub.bin")
    with pytest.raises(BirthProvisioningError) as error:
        _source(base)
    assert error.value.code == "birth_provisioning_recovery_ambiguous"
    assert victim.read_bytes() == before


def test_a_symlinked_public_is_never_followed(tmp_path: Path):
    author = Ed25519PrivateKey.generate()
    peer = Ed25519PrivateKey.generate()
    base = _keys_directory(tmp_path, author=author, extra=[
        ("real_pub.bin", _public_bytes(peer), 0o644),
    ])
    victim = base / "keys" / "real_pub.bin"
    before = victim.read_bytes()
    (base / "keys" / "link_pub.bin").symlink_to(victim)
    with pytest.raises(BirthProvisioningError) as error:
        _source(base)
    assert error.value.code == "birth_provisioning_recovery_ambiguous"
    assert victim.read_bytes() == before


def test_a_final_store_is_never_replaced(tmp_path: Path):
    author = Ed25519PrivateKey.generate()
    base = _keys_directory(tmp_path, author=author)
    source, layout = _source(base)
    layout.close_author_source()
    _provision(base, source)
    before = (base / "birth" / "author-root-v1" / "keystore.json").read_bytes()
    with pytest.raises(BirthProvisioningError) as error:
        _provision(base, source)
    assert error.value.code in {
        "birth_provisioning_transaction_conflict",
        "birth_authority_set_conflict",
        "birth_provisioning_io_unavailable",
    }
    assert (
        base / "birth" / "author-root-v1" / "keystore.json"
    ).read_bytes() == before


def test_an_invalid_final_store_is_not_repaired(tmp_path: Path):
    author = Ed25519PrivateKey.generate()
    base = _keys_directory(tmp_path, author=author)
    source, layout = _source(base)
    layout.close_author_source()
    _provision(base, source)
    store = base / "birth" / "author-root-v1"
    broken = store / "keystore.json"
    broken.write_bytes(b'{"schema_version":1}')
    os.chmod(broken, 0o600)

    second = provisioning.open_provisioning_layout_v1(
        base, provisioner_build_id=BUILD,
    )
    session = second.open_root_session()
    with session:
        with session.global_lock(exclusive=True, create=True):
            with pytest.raises(BirthProvisioningError) as error:
                verify_author_store_v1(session, ("author-root-v1",), source)
    second.close_author_source()
    assert error.value.code == "birth_author_keystore_existing_invalid"
    assert broken.read_bytes() == b'{"schema_version":1}'


def test_a_store_of_another_identity_is_refused(tmp_path: Path):
    author = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    base = _keys_directory(tmp_path, author=author)
    source, layout = _source(base)
    layout.close_author_source()
    _provision(base, source)
    foreign = _keys_directory(tmp_path / "other", author=other)
    foreign_source, foreign_layout = _source(foreign)
    foreign_layout.close_author_source()

    second = provisioning.open_provisioning_layout_v1(
        base, provisioner_build_id=BUILD,
    )
    session = second.open_root_session()
    with session:
        with session.global_lock(exclusive=True, create=True):
            with pytest.raises(BirthProvisioningError) as error:
                verify_author_store_v1(
                    session, ("author-root-v1",), foreign_source,
                )
    second.close_author_source()
    assert error.value.code == "birth_author_keystore_existing_invalid"


def test_staging_needs_the_exclusive_global_lock(tmp_path: Path):
    author = Ed25519PrivateKey.generate()
    base = _keys_directory(tmp_path, author=author)
    source, layout = _source(base)
    layout.close_author_source()
    second = provisioning.open_provisioning_layout_v1(
        base, provisioner_build_id=BUILD,
    )
    session = second.open_root_session()
    transaction = new_transaction_id_v1()
    journal = provisioning._TransactionJournalV1(session, transaction)
    with session:
        with pytest.raises(BirthProvisioningError) as error:
            stage_author_store_v1(session, journal, source)
    second.close_author_source()
    assert error.value.code == "birth_provisioning_lock_unsafe"
