from __future__ import annotations

import hashlib
import importlib
import json
import os
import stat
import subprocess
import base64
from pathlib import Path
from typing import Iterator


def secure_fs():
    return importlib.import_module("executor_birth_secure_fs")


def private_role(module=None):
    module = module or secure_fs()
    return module._BirthObjectRole("birth_confidential")


def public_role(module=None):
    module = module or secure_fs()
    return module._BirthObjectRole("birth_integrity_only")


def make_root(path: Path) -> Path:
    path.mkdir(mode=0o755)
    path.chmod(0o755)
    return path


def open_session(root: Path, *, authenticated_uid: int | None = None):
    """Build the immutable test descriptor specified by section 16.13.1.

    This intentionally uses the post-fix constructor contract.  On the frozen
    prototype construction fails in the test call, rather than at collection.
    """
    module = secure_fs()
    if os.name == "nt":
        windows_support = importlib.import_module("_windows_support")
        service_sid = windows_support.service_sid()
        windows_support.apply_profile(
            root, "integrity_only", directory=True, sid=service_sid
        )
        handles, comparison_path = module._open_win_root(root)
        identity = module._PlatformIdentity(
            posix_uid=None,
            windows_service_sid=service_sid,
        )
    else:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        comparison_path = os.path.abspath(os.fspath(root))
        opened = [os.open(os.sep, flags)]
        for component in Path(comparison_path).parts[1:]:
            opened.append(os.open(component, flags, dir_fd=opened[-1]))
        handles = tuple(opened)
        identity = module._PlatformIdentity(
            posix_uid=os.geteuid() if authenticated_uid is None else authenticated_uid,
            windows_service_sid=None,
        )
    try:
        descriptor = module._AuthenticatedRootDescriptor(
            handles=tuple(handles),
            root_path=comparison_path,
            identity=identity,
        )
    except BaseException:
        closer = module._win_close if os.name == "nt" else os.close
        for handle in reversed(handles):
            closer(handle)
        raise
    return module._adopt_authenticated_root(descriptor)


def write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    if os.name == "nt":
        support = importlib.import_module("_windows_support")
        support.apply_profile(
            path, "confidential", directory=False, sid=support.service_sid()
        )
    else:
        path.chmod(0o600)


def write_public(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    if os.name == "nt":
        support = importlib.import_module("_windows_support")
        support.apply_profile(
            path, "integrity_only", directory=False, sid=support.service_sid()
        )
    else:
        path.chmod(0o644)


def mkdir_private(path: Path) -> None:
    path.mkdir(mode=0o700)
    if os.name == "nt":
        support = importlib.import_module("_windows_support")
        support.apply_profile(
            path, "confidential", directory=True, sid=support.service_sid()
        )
    else:
        path.chmod(0o700)


def mkdir_public(path: Path) -> None:
    path.mkdir(mode=0o755)
    if os.name == "nt":
        support = importlib.import_module("_windows_support")
        support.apply_profile(
            path, "integrity_only", directory=True, sid=support.service_sid()
        )
    else:
        path.chmod(0o755)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def tree_snapshot(root: Path) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        directory_names.sort(key=os.fsencode)
        file_names.sort(key=os.fsencode)
        current_path = Path(current)
        for name in directory_names + file_names:
            path = current_path / name
            value = path.lstat()
            relative = path.relative_to(root).as_posix()
            payload_hash = None
            if stat.S_ISREG(value.st_mode):
                payload_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            rows.append(
                (
                    relative,
                    stat.S_IFMT(value.st_mode),
                    stat.S_IMODE(value.st_mode),
                    value.st_dev,
                    value.st_ino,
                    value.st_nlink,
                    value.st_uid,
                    value.st_size,
                    payload_hash,
                )
            )
    return tuple(rows)


def object_identity(path: Path, module=None):
    module = module or secure_fs()
    value = path.stat(follow_symlinks=False)
    return module._ObjectIdentity(f"{value.st_dev:x}", f"{value.st_ino:x}")


def expected_directory_links() -> int:
    return 2 if os.name == "posix" else 1


def inventory_once_helper_name() -> str:
    return "_posix_inventory" if os.name == "posix" else "_win_inventory"


def invalid_descriptor(module, root: Path):
    if os.name == "nt":
        windows_support = importlib.import_module("_windows_support")
        handle = 0xDEADBEEF
        identity = module._PlatformIdentity(
            posix_uid=None,
            windows_service_sid=windows_support.service_sid(),
        )
    else:
        handle = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.close(handle)
        identity = module._PlatformIdentity(
            posix_uid=os.geteuid(), windows_service_sid=None
        )
    return module._AuthenticatedRootDescriptor(
        handles=(handle,), root_path=os.fspath(root), identity=identity
    )


def close_primitive(module):
    if os.name == "nt":
        return module, "_win_close"
    return os, "close"


def inject_unlock_failure(module, monkeypatch) -> None:
    if os.name == "nt":
        monkeypatch.setattr(module._KERNEL32, "UnlockFileEx", lambda *args: False)
        return
    import fcntl

    real_flock = fcntl.flock

    def failing_unlock(fd: int, operation: int) -> None:
        if operation == fcntl.LOCK_UN:
            raise OSError(5, "private unlock diagnostic")
        return real_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", failing_unlock)


def assert_birth_error(error: BaseException, *, code: str | None = None) -> None:
    module = secure_fs()
    assert isinstance(error, module.BirthSecureFSError)
    assert str(error) == error.code
    if code is not None:
        assert error.code == code


def waitpid_killed(pid: int) -> None:
    waited, status = os.waitpid(pid, 0)
    assert waited == pid
    assert os.WIFSIGNALED(status)
    assert os.WTERMSIG(status) == 9


def chown_other_uid(paths: Iterator[Path]) -> int:
    other_uid = os.geteuid() + 1
    command = ["sudo", "-n", "chown", f"{other_uid}:{os.getegid()}"]
    subprocess.run(command + [os.fspath(path) for path in paths], check=True)
    return other_uid


def restore_owner(paths: Iterator[Path]) -> None:
    command = ["sudo", "-n", "chown", f"{os.geteuid()}:{os.getegid()}"]
    subprocess.run(command + [os.fspath(path) for path in paths], check=True)


def provision_keystore(root: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    module = importlib.import_module("executor_birth_keystore")
    mkdir_private(root)
    mkdir_private(root / "private")
    mkdir_private(root / "public")
    key = Ed25519PrivateKey.generate()
    public = module.raw_public_key(key.public_key())
    key_id = module.birth_key_id(public)
    write_private(root / "birth-keystore.lock", b"0")
    write_private(root / "public" / f"{key_id}.pub", public)
    private = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    write_private(root / "private" / f"{key_id}.key", private)
    config = {
        "active_key_id": key_id,
        "config_revision": 1,
        "keys": [
            {
                "key_id": key_id,
                "public_file": f"public/{key_id}.pub",
                "status": "active",
            }
        ],
        "private_file": f"private/{key_id}.key",
        "schema_version": 1,
    }
    write_private(root / "keystore.json", canonical_json(config))


def provision_approval(path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    value = {
        "actors": {"operator": {"key_ids": ["operator-key"], "scopes": ["birth"]}},
        "keys": {"operator-key": base64.b64encode(key).decode("ascii")},
        "revision": 1,
        "schema_version": 1,
    }
    write_public(path, canonical_json(value))


def provision_semantic(root: Path) -> dict[str, object]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    review = importlib.import_module("executor_birth_semantic_review")
    mkdir_public(root)
    mkdir_public(root / "public")
    mkdir_public(root / "evidence")
    public = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    write_public(root / "public" / "review.pub", public)
    kinds = sorted(item.value for item in review.IndependentEvidenceKind)
    value: dict[str, object] = {
        "evidence_dir": "evidence",
        "owners": {kind: ["independent-owner"] for kind in kinds},
        "verifiers": {
            "review-key": {"path": "public/review.pub", "status": "active"}
        },
        "versions": {kind: ["v1"] for kind in kinds},
    }
    write_public(root / "semantic.json", canonical_json(value))
    return value
