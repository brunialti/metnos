from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_keystore import (
    BirthKeyStoreError,
    birth_key_id,
    load_birth_keystore,
    raw_public_key,
)


def _private_raw(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def _write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.write_bytes(payload)
    if os.name == "posix":
        path.chmod(mode)


def _provision(root: Path, keys: list[Ed25519PrivateKey], *, active: int = -1) -> dict:
    root.mkdir(mode=0o700)
    (root / "private").mkdir(mode=0o700)
    (root / "public").mkdir(mode=0o700)
    if os.name == "posix":
        root.chmod(0o700)
    _write(root / "birth-keystore.lock", b"0")
    records = []
    for index, private in enumerate(keys):
        public = raw_public_key(private.public_key())
        key_id = birth_key_id(public)
        _write(root / "public" / f"{key_id}.pub", public)
        records.append({
            "key_id": key_id,
            "public_file": f"public/{key_id}.pub",
            "status": "active" if index == active % len(keys) else "verifier",
        })
    active_key = keys[active]
    active_id = birth_key_id(raw_public_key(active_key.public_key()))
    _write(root / "private" / f"{active_id}.key", _private_raw(active_key))
    records.sort(key=lambda item: item["key_id"])
    config = {
        "active_key_id": active_id,
        "config_revision": len(keys),
        "keys": records,
        "private_file": f"private/{active_id}.key",
        "schema_version": 1,
    }
    _write(
        root / "keystore.json",
        json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
    )
    return config


def test_loads_dedicated_active_signer_and_historical_verifier_ring(tmp_path: Path) -> None:
    old, active = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    root = tmp_path / "birth-keys"
    config = _provision(root, [old, active])

    loaded = load_birth_keystore(root)

    assert loaded.config_revision == 2
    assert loaded.active_key_id == config["active_key_id"]
    assert set(loaded.verifier_keys) == {record["key_id"] for record in config["keys"]}
    message = b"birth-only"
    loaded.verifier_keys[loaded.active_key_id].verify(
        loaded.active_private_key.sign(message), message,
    )
    with pytest.raises(TypeError):
        loaded.verifier_keys["new"] = active.public_key()  # type: ignore[index]


def test_rotation_requires_new_active_private_but_retains_old_public(tmp_path: Path) -> None:
    old, new = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    root = tmp_path / "birth-keys"
    first = _provision(root, [old])
    old_id = first["active_key_id"]

    # Simulate the external provisioner's atomic replacement, not runtime rotation.
    for child in root.iterdir():
        if child.is_dir():
            for item in child.iterdir():
                item.unlink()
            child.rmdir()
        elif child.name != "birth-keystore.lock":
            child.unlink()
    (root / "private").mkdir(mode=0o700)
    (root / "public").mkdir(mode=0o700)
    second = _provision_existing(root, [old, new], active=1)

    loaded = load_birth_keystore(root)
    assert loaded.active_key_id == second["active_key_id"]
    assert old_id in loaded.verifier_keys
    assert not (root / "private" / f"{old_id}.key").exists()


def _provision_existing(root: Path, keys: list[Ed25519PrivateKey], *, active: int) -> dict:
    records = []
    for index, private in enumerate(keys):
        raw = raw_public_key(private.public_key())
        key_id = birth_key_id(raw)
        _write(root / "public" / f"{key_id}.pub", raw)
        records.append({"key_id": key_id, "public_file": f"public/{key_id}.pub",
                        "status": "active" if index == active else "verifier"})
    active_id = records[active]["key_id"]
    _write(root / "private" / f"{active_id}.key", _private_raw(keys[active]))
    records.sort(key=lambda item: item["key_id"])
    config = {"active_key_id": active_id, "config_revision": 2, "keys": records,
              "private_file": f"private/{active_id}.key", "schema_version": 1}
    _write(root / "keystore.json", json.dumps(
        config, sort_keys=True, separators=(",", ":"),
    ).encode())
    return config


def test_rejects_author_identity_reuse_with_constant_comparison_boundary(tmp_path: Path) -> None:
    author = Ed25519PrivateKey.generate()
    root = tmp_path / "birth-keys"
    _provision(root, [author])
    with pytest.raises(BirthKeyStoreError, match="birth_key_reuses_author_identity"):
        load_birth_keystore(root, forbidden_public_keys=(author.public_key(),))


@pytest.mark.parametrize("target", ["keystore.json", "public", "private"])
def test_rejects_symlinks_in_security_boundary(tmp_path: Path, target: str) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    root = tmp_path / "birth-keys"
    config = _provision(root, [Ed25519PrivateKey.generate()])
    if target == "keystore.json":
        original = root / target
    elif target == "public":
        original = root / config["keys"][0]["public_file"]
    else:
        original = root / config["private_file"]
    saved = tmp_path / f"saved-{target}"
    original.rename(saved)
    original.symlink_to(saved)
    with pytest.raises(BirthKeyStoreError):
        load_birth_keystore(root)


def test_rejects_hardlinked_key_material(tmp_path: Path) -> None:
    root = tmp_path / "birth-keys"
    config = _provision(root, [Ed25519PrivateKey.generate()])
    public = root / config["keys"][0]["public_file"]
    os.link(public, tmp_path / "leaked-public")
    with pytest.raises(BirthKeyStoreError, match="birth_keystore_unsafe"):
        load_birth_keystore(root)


def test_rejects_leftover_private_key_and_undeclared_public(tmp_path: Path) -> None:
    root = tmp_path / "birth-keys"
    _provision(root, [Ed25519PrivateKey.generate()])
    _write(root / "private" / "retired.key", b"x" * 32)
    with pytest.raises(BirthKeyStoreError, match="private inventory mismatch"):
        load_birth_keystore(root)
    (root / "private" / "retired.key").unlink()
    _write(root / "public" / "undeclared.pub", b"x" * 32)
    with pytest.raises(BirthKeyStoreError, match="public inventory mismatch"):
        load_birth_keystore(root)


def test_rejects_symlinked_key_directory(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    root = tmp_path / "birth-keys"
    _provision(root, [Ed25519PrivateKey.generate()])
    public = root / "public"
    saved = tmp_path / "saved-public-directory"
    public.rename(saved)
    public.symlink_to(saved, target_is_directory=True)
    with pytest.raises(BirthKeyStoreError, match="birth_keystore_unsafe"):
        load_birth_keystore(root)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
@pytest.mark.parametrize("target", ["root", "config", "private", "public", "lock"])
def test_rejects_non_exact_permissions(tmp_path: Path, target: str) -> None:
    root = tmp_path / "birth-keys"
    config = _provision(root, [Ed25519PrivateKey.generate()])
    paths = {
        "root": root,
        "config": root / "keystore.json",
        "private": root / config["private_file"],
        "public": root / config["keys"][0]["public_file"],
        "lock": root / "birth-keystore.lock",
    }
    paths[target].chmod(0o755 if target == "root" else 0o640)
    with pytest.raises(BirthKeyStoreError, match="birth_keystore_unsafe"):
        load_birth_keystore(root)


def test_rejects_noncanonical_config_and_duplicate_keys(tmp_path: Path) -> None:
    root = tmp_path / "birth-keys"
    config = _provision(root, [Ed25519PrivateKey.generate()])
    _write(root / "keystore.json", json.dumps(config, indent=2).encode())
    with pytest.raises(BirthKeyStoreError, match="non-canonical"):
        load_birth_keystore(root)
    _write(root / "keystore.json", b'{"schema_version":1,"schema_version":1}')
    with pytest.raises(BirthKeyStoreError, match="duplicate JSON key"):
        load_birth_keystore(root)


def test_rejects_private_length_pair_mismatch_and_public_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "short"
    config = _provision(root, [Ed25519PrivateKey.generate()])
    _write(root / config["private_file"], b"x" * 31)
    with pytest.raises(BirthKeyStoreError, match="exactly 32"):
        load_birth_keystore(root)

    root2 = tmp_path / "mismatch"
    config2 = _provision(root2, [Ed25519PrivateKey.generate()])
    _write(root2 / config2["private_file"], _private_raw(Ed25519PrivateKey.generate()))
    with pytest.raises(BirthKeyStoreError, match="birth_key_pair_mismatch"):
        load_birth_keystore(root2)

    root3 = tmp_path / "fingerprint"
    config3 = _provision(root3, [Ed25519PrivateKey.generate()])
    _write(root3 / config3["keys"][0]["public_file"], raw_public_key(
        Ed25519PrivateKey.generate().public_key(),
    ))
    with pytest.raises(BirthKeyStoreError, match="birth_key_invalid"):
        load_birth_keystore(root3)


def test_missing_store_fails_closed_and_never_generates_files(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    with pytest.raises(BirthKeyStoreError, match="birth_keystore_unavailable"):
        load_birth_keystore(root)
    assert not root.exists()
