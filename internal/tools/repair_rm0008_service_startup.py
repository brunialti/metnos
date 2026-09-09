#!/usr/bin/python3.12
"""Install the reviewed service-local verifier, preserving signed history.

Root and no arguments. Refuse an unknown predecessor or active Metnos work.
Only the administrative helper is replaced; its exact predecessor is retained.
Start HTTP only, without starting the stack-wide readiness/quarantine chain.
"""
from __future__ import annotations

from contextlib import ExitStack
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types

SOURCE = Path("/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/executor_birth_admin_preflight.py")
TARGET = Path("/usr/libexec/metnos/executor-birth-v1/preflight.py")
BACKUP = Path("/var/lib/metnos-admin/rm0008-service-startup-20260909")
ROOT = Path("/var/lib/metnos/executor-birth")
DESCRIPTOR = ROOT / "releases-v1/00000000000000000001/deployment/executor-birth-deployment-v1.json"
OLD_SHA = "0fd620bf0d519024368dfec61e5a39f39f22fa7c7e8a8642457be978c0cd15ca"
NEW_SHA = "cbe320dcc21d066ca565a6972fd1af611320a913798d6ce8cd7bce1cf432f099"
DESCRIPTOR_SHA = "241c8d93912f64cc6356be056fa72260737195af74f8e6392a86436d1abdd503"
LOCKS = (ROOT / "ownership-deployment-v1.lock",
         Path("/run/metnos-executor-birth-v1/startup-v1.lock"))


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise RuntimeError(detail)


def protected_directory(path: Path) -> None:
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) == (0, 0)
                and not info.st_mode & 0o022, f"unprotected directory: {ancestor}")


def read_exact(path: Path, digest: str, *, root: bool = True) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW), "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_size < 2_000_000, f"unsafe file: {path}")
        require(not root or ((info.st_uid, info.st_gid) == (0, 0)
                             and not info.st_mode & 0o022), f"unprotected file: {path}")
        content = stream.read()
        require(hashlib.sha256(content).hexdigest() == digest, f"changed file: {path}")
        return content


def command(*args: str, timeout: int = 30) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True,
                            timeout=timeout, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                                                  "LANG": "C", "LC_ALL": "C"})
    return result.stdout.strip()


def require_stopped() -> None:
    active = command("/usr/bin/systemctl", "list-units", "--all", "--plain",
                     "--no-legend", "--no-pager",
                     "--state=active,activating,reloading,deactivating", "metnos-*")
    require(not active, "Metnos work is active; no services were stopped")


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_reviewed_verifier(content: bytes) -> dict:
    # Dataclass/typing definitions resolve their module through sys.modules.
    module = types.ModuleType("_reviewed_startup_verifier")
    module.__file__ = str(SOURCE)
    sys.modules[module.__name__] = module
    namespace = vars(module)
    exec(compile(content, str(SOURCE), "exec"), namespace)
    return namespace


def replace_helper(content: bytes, predecessor: bytes) -> None:
    protected_directory(BACKUP.parent)
    if not os.path.lexists(BACKUP):
        BACKUP.mkdir(mode=0o700)
        sync_directory(BACKUP.parent)
    protected_directory(BACKUP)
    saved = BACKUP / "preflight.before.py"
    if not os.path.lexists(saved):
        with os.fdopen(os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                               0o400), "wb") as stream:
            stream.write(predecessor)
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(BACKUP)
    read_exact(saved, OLD_SHA)
    descriptor, name = tempfile.mkstemp(prefix=".service-startup-", dir=TARGET.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), 0o755)
            stream.flush()
            os.fsync(stream.fileno())
        read_exact(Path(name), NEW_SHA)
        read_exact(TARGET, OLD_SHA)
        os.replace(name, TARGET)
        sync_directory(TARGET.parent)
    finally:
        if os.path.lexists(name):
            os.unlink(name)
    read_exact(TARGET, NEW_SHA)


def repair() -> None:
    require(os.geteuid() == 0 and len(sys.argv) == 1, "root and zero arguments required")
    content = read_exact(SOURCE, NEW_SHA, root=False)
    protected_directory(TARGET.parent)
    protected_directory(DESCRIPTOR.parent)
    read_exact(DESCRIPTOR, DESCRIPTOR_SHA)
    predecessor = read_exact(TARGET, OLD_SHA)
    require_stopped()
    with ExitStack() as stack:
        for path in LOCKS:
            protected_directory(path.parent)
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            stack.callback(os.close, descriptor)
            info = os.fstat(descriptor)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and (info.st_uid, info.st_gid) == (0, 0)
                    and stat.S_IMODE(info.st_mode) == 0o600, "unsafe lock")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_stopped()
        namespace = load_reviewed_verifier(content)
        namespace["require_linux_before_io_v1"]()
        authenticated = namespace["_authenticate_fixed_ownership_snapshot_v1"]()
        _, materials = namespace["_load_installed_preflight_materials_v1"](
            authenticated, review_sources=False)
        for entry in materials.catalog.entries:
            if entry.class_name == "gated_service":
                namespace["_check_installed_service_v1"](materials, entry)
                print("SERVICE_STARTUP_CHECK_OK", entry.entry_id, flush=True)
        read_exact(DESCRIPTOR, DESCRIPTOR_SHA)
        replace_helper(content, predecessor)
        print("SERVICE_STARTUP_HELPER_INSTALLED signed_history_unchanged", flush=True)
    command("/usr/bin/python3.12", "-I", "-S", str(TARGET),
            "check", "--entry-id", "service-http", timeout=60)
    command("/usr/bin/systemctl", "start", "metnos-http.service", timeout=60)
    print("HTTP_START_REQUESTED usability_not_yet_verified", flush=True)


if __name__ == "__main__":
    try:
        repair()
    except Exception as error:
        print(f"SERVICE_STARTUP_REPAIR_REFUSED {type(error).__name__}: {error}", flush=True)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stdout or "", error.stderr or "", flush=True)
        raise SystemExit(78)
