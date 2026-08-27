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


def lock_role_binding(module=None):
    module = module or secure_fs()
    return role_binding(
        module,
        ("provisioning-v1.lock",),
        directory=False,
        role=public_role(module),
    )


def role_binding(module, components, *, directory: bool, role):
    return module._BirthRoleBindingV1(
        components=tuple(components),
        kind=module._ObjectKind("directory" if directory else "regular_file"),
        role=role,
    )


def exact_role_catalog(module, bindings=(), *, root: Path | None = None):
    values = [
        role_binding(
            module,
            (),
            directory=True,
            role=public_role(module),
        )
    ]
    candidates = tuple(set((lock_role_binding(module), *bindings)))
    if root is not None:
        for binding in candidates:
            path = root.joinpath(*binding.components)
            try:
                observed = path.lstat()
            except FileNotFoundError:
                continue
            assert (
                stat.S_IFMT(observed.st_mode),
                binding.kind.value,
            ) in {
                (stat.S_IFDIR, "directory"),
                (stat.S_IFREG, "regular_file"),
            }, "preexisting exact binding kind mismatch"
            values.append(binding)
    ordered = sorted(
        values,
        key=lambda item: (
            tuple(os.fsencode(part) for part in item.components),
            item.kind.value,
            item.role.value,
        ),
    )
    keys = [(item.components, item.kind) for item in ordered]
    assert len(keys) == len(set(keys))
    return module._BirthRoleCatalogV1(
        schema_version=1,
        patterns=(),
        exact_bindings=tuple(ordered),
        generation=0,
    )


def birth_keystore_role_bindings(module, components, key_id: str):
    base = tuple(components)
    return (
        role_binding(module, base, directory=True, role=private_role(module)),
        role_binding(
            module,
            base + ("keystore.json",),
            directory=False,
            role=private_role(module),
        ),
        role_binding(
            module,
            base + ("birth-keystore.lock",),
            directory=False,
            role=private_role(module),
        ),
        role_binding(
            module,
            base + ("private",),
            directory=True,
            role=private_role(module),
        ),
        role_binding(
            module,
            base + ("private", f"{key_id}.key"),
            directory=False,
            role=private_role(module),
        ),
        role_binding(
            module,
            base + ("public",),
            directory=True,
            role=public_role(module),
        ),
        role_binding(
            module,
            base + ("public", f"{key_id}.pub"),
            directory=False,
            role=public_role(module),
        ),
    )


def make_root(path: Path) -> Path:
    path.mkdir(mode=0o755)
    path.chmod(0o755)
    return path


def open_session(
    root: Path,
    *,
    authenticated_uid: int | None = None,
    role_bindings=(),
    role_catalog=None,
):
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
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
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
            role_catalog=(
                exact_role_catalog(module, role_bindings, root=root)
                if role_catalog is None
                else role_catalog
            ),
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

    def append_path(path: Path, relative: str) -> None:
        value = path.lstat()
        payload_hash = None
        if stat.S_ISREG(value.st_mode):
            # A byte-range lock is mandatory on Windows: an object the product
            # holds cannot be read while it holds it, and the refusal is
            # recorded as such instead of ending the snapshot.
            try:
                payload_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except PermissionError:
                payload_hash = "locked"
        rows.append(
            (
                relative,
                stat.S_IFMT(value.st_mode),
                stat.S_IMODE(value.st_mode),
                value.st_dev,
                value.st_ino,
                value.st_nlink,
                value.st_uid,
                value.st_gid,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
                payload_hash,
            )
        )

    append_path(root, ".")
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        directory_names.sort(key=os.fsencode)
        file_names.sort(key=os.fsencode)
        current_path = Path(current)
        for name in directory_names + file_names:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            append_path(path, relative)
    return tuple(rows)


def assert_posix_security(
    path: Path, *, directory: bool, mode: int, uid: int | None = None
) -> os.stat_result:
    value = path.stat(follow_symlinks=False)
    assert (
        stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)
    )
    assert not stat.S_ISLNK(value.st_mode)
    assert stat.S_IMODE(value.st_mode) == mode
    assert value.st_uid == (os.geteuid() if uid is None else uid)
    if not directory:
        assert value.st_nlink == 1
    return value


def object_identity(path: Path, module=None):
    module = module or secure_fs()
    if os.name == "nt":
        windows_support = importlib.import_module("_windows_support")
        facts = windows_support.identity(path, directory=path.is_dir())
        return module._ObjectIdentity(facts["volume"], facts["file_id"])
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
        handle = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        os.close(handle)
        identity = module._PlatformIdentity(
            posix_uid=os.geteuid(), windows_service_sid=None
        )
    return module._AuthenticatedRootDescriptor(
        handles=(handle,),
        root_path=os.fspath(root),
        identity=identity,
        role_catalog=exact_role_catalog(module, root=root),
    )


def close_primitive(module):
    if os.name == "nt":
        return module, "_win_close"
    return os, "close"


def inject_unlock_failure(module, monkeypatch) -> dict[str, list[int]]:
    state: dict[str, list[int]] = {
        "unlock_handles": [],
        "closed_handles": [],
    }
    owner, closer_name = close_primitive(module)
    real_close = getattr(owner, closer_name)

    def tracked_close(handle: int) -> None:
        value = int(getattr(handle, "value", handle) or 0)
        if value in state["unlock_handles"]:
            state["closed_handles"].append(value)
        return real_close(handle)

    monkeypatch.setattr(owner, closer_name, tracked_close)
    if os.name == "nt":
        def failing_unlock(*args):
            state["unlock_handles"].append(
                int(getattr(args[0], "value", args[0]) or 0)
            )
            return False

        monkeypatch.setattr(module._KERNEL32, "UnlockFileEx", failing_unlock)
        return state
    import fcntl

    real_flock = fcntl.flock

    def failing_unlock(fd: int, operation: int) -> None:
        if operation == fcntl.LOCK_UN:
            state["unlock_handles"].append(int(fd))
            raise OSError(5, "private unlock diagnostic")
        return real_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", failing_unlock)
    return state


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


def provision_keystore(root: Path) -> str:
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
    return key_id


def provision_birth_keystore(root: Path) -> str:
    """Provision the known Birth profile variant of the keystore fixture."""
    key_id = provision_keystore(root)
    (root / "public").chmod(0o755)
    (root / "public" / f"{key_id}.pub").chmod(0o644)
    return key_id


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
