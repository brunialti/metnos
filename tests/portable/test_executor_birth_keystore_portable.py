"""Public Linux/Windows certification for the closed Executor Birth keystore."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_keystore import (
    BirthKeyStoreError,
    _harden_windows_private_acl,
    birth_key_id,
    load_birth_keystore,
    raw_public_key,
)
from install.birth_authority_provisioning import (
    BirthAuthorityProvisioningError,
    inspect_author_keystore,
    provision_author_keystore,
)


def _write(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    if os.name == "posix":
        path.chmod(0o600)


def _provision(root: Path) -> None:
    private = Ed25519PrivateKey.generate()
    root.mkdir(mode=0o700)
    if os.name == "nt":
        _harden_windows_private_acl(root)
    (root / "private").mkdir(mode=0o700)
    (root / "public").mkdir(mode=0o700)
    if os.name == "nt":
        _harden_windows_private_acl(root / "private")
        _harden_windows_private_acl(root / "public")
    if os.name == "posix":
        root.chmod(0o700)
    public = raw_public_key(private.public_key())
    key_id = birth_key_id(public)
    _write(root / "birth-keystore.lock", b"0")
    _write(root / "public" / f"{key_id}.pub", public)
    _write(root / "private" / f"{key_id}.key", private.private_bytes_raw())
    _write(root / "keystore.json", json.dumps({
        "active_key_id": key_id,
        "config_revision": 1,
        "keys": [{
            "key_id": key_id,
            "public_file": f"public/{key_id}.pub",
            "status": "active",
        }],
        "private_file": f"private/{key_id}.key",
        "schema_version": 1,
    }, sort_keys=True, separators=(",", ":")).encode())


def _legacy_author(root: Path) -> None:
    root.mkdir(mode=0o700)
    if os.name == "nt":
        _harden_windows_private_acl(root)
    private = Ed25519PrivateKey.generate()
    _write(root / "author_priv.bin", private.private_bytes_raw())
    _write(root / "author_pub.bin", raw_public_key(private.public_key()))


def test_portable_keystore_load_and_hardlink_rejection(tmp_path: Path) -> None:
    root = tmp_path / "valid"
    _provision(root)
    loaded = load_birth_keystore(root)
    message = b"portable-keystore"
    loaded.verifier_keys[loaded.active_key_id].verify(
        loaded.active_private_key.sign(message), message,
    )

    public = next((root / "public").iterdir())
    os.link(public, tmp_path / "leaked-public")
    with pytest.raises(BirthKeyStoreError, match="birth_keystore_unsafe"):
        load_birth_keystore(root)


def test_portable_author_migration_survives_legacy_retirement(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private-root"
    private_root.mkdir(mode=0o700)
    if os.name == "nt":
        _harden_windows_private_acl(private_root)
    legacy = private_root / "legacy"
    _legacy_author(legacy)
    birth = private_root / "birth"

    created = provision_author_keystore(
        legacy_keys_dir=legacy,
        birth_dir=birth,
    )
    for item in legacy.iterdir():
        item.unlink()
    legacy.rmdir()

    assert inspect_author_keystore(birth_dir=birth) == {
        **created,
        "created": False,
    }


def test_portable_author_migration_is_serialized_between_processes(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private-root"
    private_root.mkdir(mode=0o700)
    if os.name == "nt":
        _harden_windows_private_acl(private_root)
    legacy = private_root / "legacy"
    _legacy_author(legacy)
    birth = private_root / "birth"
    repository = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((
        str(repository / "runtime"),
        str(repository),
        environment.get("PYTHONPATH", ""),
    ))
    source = (
        "import json,sys; from pathlib import Path; "
        "from install.birth_authority_provisioning import provision_author_keystore; "
        "print(json.dumps(provision_author_keystore("
        "legacy_keys_dir=Path(sys.argv[1]),birth_dir=Path(sys.argv[2]))))"
    )
    children = tuple(
        subprocess.Popen(
            [sys.executable, "-c", source, str(legacy), str(birth)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        for _index in range(2)
    )
    outputs = []
    for child in children:
        stdout, stderr = child.communicate(timeout=20)
        assert child.returncode == 0, stderr
        outputs.append(json.loads(stdout))

    assert sorted(result["created"] for result in outputs) == [False, True]
    assert len({result["active_key_id"] for result in outputs}) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows junction and DACL contract")
def test_windows_rejects_junction_root_and_shared_write_acl(tmp_path: Path) -> None:
    target = tmp_path / "junction-target"
    _provision(target)
    junction = tmp_path / "junction-store"
    linked = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert linked.returncode == 0, linked.stderr
    with pytest.raises(BirthKeyStoreError, match="birth_keystore_unsafe"):
        load_birth_keystore(junction)

    shared = tmp_path / "shared-acl"
    _provision(shared)
    changed = subprocess.run(
        ["icacls", str(shared), "/grant", "*S-1-5-32-545:(OI)(CI)M"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert changed.returncode == 0, changed.stderr
    with pytest.raises(BirthKeyStoreError, match="birth_keystore_unsafe"):
        load_birth_keystore(shared)

    shared_read = tmp_path / "shared-read-acl"
    _provision(shared_read)
    private_file = next((shared_read / "private").iterdir())
    changed = subprocess.run(
        ["icacls", str(private_file), "/grant", "*S-1-5-32-545:R"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert changed.returncode == 0, changed.stderr
    with pytest.raises(BirthKeyStoreError, match="birth_keystore_unsafe"):
        load_birth_keystore(shared_read)


@pytest.mark.skipif(os.name != "nt", reason="Windows private legacy ACL contract")
def test_windows_author_migration_rejects_shared_read_acl(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private-root"
    private_root.mkdir(mode=0o700)
    _harden_windows_private_acl(private_root)
    legacy = private_root / "legacy"
    _legacy_author(legacy)
    changed = subprocess.run(
        [
            "icacls",
            str(legacy / "author_priv.bin"),
            "/grant",
            "*S-1-5-32-545:R",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert changed.returncode == 0, changed.stderr

    with pytest.raises(
        BirthAuthorityProvisioningError,
        match="birth_author_provisioning_unsafe",
    ):
        provision_author_keystore(
            legacy_keys_dir=legacy,
            birth_dir=private_root / "birth",
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-range lock contract")
def test_windows_lock_contention_has_bounded_stable_error(tmp_path: Path) -> None:
    root = tmp_path / "contended"
    _provision(root)
    lock_path = root / "birth-keystore.lock"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import msvcrt,os,sys; "
                "f=open(sys.argv[1],'r+b',buffering=0); "
                "os.lseek(f.fileno(),0,os.SEEK_SET); "
                "msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1); "
                "print('locked',flush=True); sys.stdin.readline(); "
                "os.lseek(f.fileno(),0,os.SEEK_SET); "
                "msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)"
            ),
            str(lock_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(BirthKeyStoreError) as rejected:
            load_birth_keystore(root)
        assert rejected.value.code == "birth_keystore_unavailable"
    finally:
        if child.stdin is not None:
            child.stdin.write("\n")
            child.stdin.flush()
        child.wait(timeout=10)
    assert child.returncode == 0, child.stderr.read() if child.stderr else ""
