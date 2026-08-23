from __future__ import annotations

import os

import pytest

from runtime import protected_undo


@pytest.fixture
def protected_store(tmp_path, monkeypatch):
    key = tmp_path / "admin.key"
    key.write_text("b" * 64, encoding="utf-8")
    monkeypatch.setattr(protected_undo, "ADMIN_KEY_PATH", key)
    monkeypatch.setattr(protected_undo, "BLOB_DIR", tmp_path / "undo_blobs")
    monkeypatch.setenv("METNOS_UNDO_RETENTION_DAYS", "30")
    return tmp_path


def test_secret_roundtrip_is_encrypted_and_actor_bound(protected_store):
    secret = b"SESSION_ID=never-write-this-to-undo-jsonl"
    handle = protected_undo.store(
        secret, owner="host", namespace="tests.cookie")

    blob_path = protected_undo.BLOB_DIR / f"{handle}.age"
    assert secret not in blob_path.read_bytes()
    assert os.stat(blob_path).st_mode & 0o077 == 0
    assert protected_undo.load(
        handle, owner="host", namespace="tests.cookie") == secret
    with pytest.raises(PermissionError):
        protected_undo.load(
            handle, owner="guest:other", namespace="tests.cookie")


def test_discard_validates_binding_before_deleting(protected_store):
    handle = protected_undo.store(
        b"secret", owner="host", namespace="tests.cookie")

    with pytest.raises(PermissionError):
        protected_undo.discard(
            handle, owner="host", namespace="tests.other")
    assert protected_undo.discard(
        handle, owner="host", namespace="tests.cookie") is True
    assert not (protected_undo.BLOB_DIR / f"{handle}.age").exists()


def test_expired_authenticated_blob_is_purged(protected_store, monkeypatch):
    monkeypatch.setattr(protected_undo.time, "time", lambda: 1_000_000)
    handle = protected_undo.store(
        b"secret", owner="host", namespace="tests.cookie")

    assert protected_undo.purge_expired(now=1_000_000 + 31 * 86400) == 1
    assert not (protected_undo.BLOB_DIR / f"{handle}.age").exists()
