"""Install the fixed startup gate before any dominant unit can be published."""
from __future__ import annotations

import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_REPOSITORY = Path(__file__).resolve().parents[1]
if str(_REPOSITORY) not in sys.path:  # pragma: no cover - installer bootstrap
    sys.path.insert(0, str(_REPOSITORY))
_RUNTIME = _REPOSITORY / "runtime"
if str(_RUNTIME) not in sys.path:  # pragma: no cover - installer bootstrap
    sys.path.insert(0, str(_RUNTIME))

from executor_birth_admin_preflight import (
    RUNTIME_ROOT, STARTUP_GATE_PATH_V1,
)
from executor_birth_distribution_assembler import DistributionAssemblerError
from executor_birth_posix_metadata import snapshot_stat_v1
from install.executor_birth_source_receiver import (
    _ensure_child_directory_v1, _name_status_v1,
    _open_absolute_directory_v1, _require_absolute_chain_bound_v1,
)


_GATE_SEAL_V1 = object()
_GATE_TEST_SEAL_V1 = object()
_GATE_BASENAME_V1 = "startup-v1.lock"
STARTUP_BOOT_RULE_NAME_V1 = "metnos-executor-birth-v1.conf"
STARTUP_BOOT_RULE_V1 = (
    f"d! {RUNTIME_ROOT} 0700 0 0 -\n"
    f"f! {STARTUP_GATE_PATH_V1} 0600 0 0 -\n"
).encode("ascii")
_FILE_FLAGS_V1 = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _fail(detail: str) -> DistributionAssemblerError:
    return DistributionAssemblerError(
        "birth_ownership_startup_gate_invalid", detail,
    )


@dataclass(frozen=True, slots=True)
class InstalledStartupGateV1:
    runtime_root: str
    gate_path: str
    mode: int
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _GATE_SEAL_V1
            or self.runtime_root != RUNTIME_ROOT.as_posix()
            or self.gate_path != STARTUP_GATE_PATH_V1.as_posix()
            or self.mode != 0o600
        ):
            raise _fail("result")


@dataclass(frozen=True, slots=True)
class _InstalledStartupGateForTestV1:
    runtime_root: Path
    gate_path: Path
    mode: int
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _GATE_TEST_SEAL_V1
            or not self.runtime_root.is_absolute()
            or self.runtime_root.name != "metnos-executor-birth-v1"
            or self.gate_path != self.runtime_root / _GATE_BASENAME_V1
            or self.mode != 0o600
        ):
            raise _fail("test result")


def _require_linux_v1() -> None:
    if not sys.platform.startswith("linux"):
        raise DistributionAssemblerError(
            "birth_ownership_platform_unsupported",
        )


def _require_boot_rule_v1(path: Path, owner: tuple[int, int]) -> bool:
    try:
        descriptor = os.open(path, _FILE_FLAGS_V1)
    except FileNotFoundError:
        if path.is_symlink():
            raise _fail("boot rule symlink")
        return False
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (info.st_uid, info.st_gid) != owner
            or stat.S_IMODE(info.st_mode) != 0o644
            or os.read(descriptor, len(STARTUP_BOOT_RULE_V1) + 1)
            != STARTUP_BOOT_RULE_V1
        ):
            raise _fail("boot rule changed")
    finally:
        os.close(descriptor)
    return True


def _install_boot_rule_v1(directory: Path, owner, require_session) -> None:
    """Publish one persistent boot-only rule; never replace an existing rule."""
    require_session()
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode) or directory.is_symlink()
        or (info.st_uid, info.st_gid) != owner or info.st_mode & 0o022
    ):
        raise _fail("boot rule directory")
    target = directory / STARTUP_BOOT_RULE_NAME_V1
    if _require_boot_rule_v1(target, owner):
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".metnos-boot-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(STARTUP_BOOT_RULE_V1)
            stream.flush()
            os.fsync(stream.fileno())
        require_session()
        os.link(temporary, target, follow_symlinks=False)
    finally:
        os.unlink(temporary)
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    _require_boot_rule_v1(target, owner)


def _install_startup_gate_core_v1(
    *, runtime_root: Path, owner: tuple[int, int], require_session,
) -> None:
    _require_linux_v1()
    if (
        not isinstance(runtime_root, Path)
        or not runtime_root.is_absolute()
        or runtime_root.name != "metnos-executor-birth-v1"
        or not callable(require_session)
    ):
        raise _fail("arguments")
    require_session()
    descriptors: list[int] = []
    runtime_fd = None
    gate_fd = None
    try:
        descriptors, parts = _open_absolute_directory_v1(
            runtime_root.parent.as_posix(),
        )
        _require_absolute_chain_bound_v1(
            descriptors, parts, detail="startup runtime parent",
        )
        parent = os.fstat(descriptors[-1])
        if (
            not stat.S_ISDIR(parent.st_mode)
            or (parent.st_uid, parent.st_gid) != owner
            or stat.S_IMODE(parent.st_mode) & 0o022
        ):
            raise _fail("runtime parent")
        require_session()
        runtime_fd = _ensure_child_directory_v1(
            descriptors[-1], runtime_root.name, owner=owner, mode=0o700,
        )
        try:
            with os.scandir(runtime_fd) as iterator:
                names = tuple(sorted(item.name for item in iterator))
        except OSError as exc:
            raise _fail("runtime inventory") from exc
        if any(name != _GATE_BASENAME_V1 for name in names):
            raise _fail("runtime inventory")
        created = False
        status = _name_status_v1(runtime_fd, _GATE_BASENAME_V1)
        if status is None:
            require_session()
            try:
                gate_fd = os.open(
                    _GATE_BASENAME_V1,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=runtime_fd,
                )
                os.fchown(gate_fd, *owner)
                os.fchmod(gate_fd, 0o600)
                os.fsync(gate_fd)
                os.fsync(runtime_fd)
                created = True
            except OSError as exc:
                raise _fail("gate create") from exc
        else:
            try:
                gate_fd = os.open(
                    _GATE_BASENAME_V1, _FILE_FLAGS_V1, dir_fd=runtime_fd,
                )
            except OSError as exc:
                raise _fail("gate open") from exc
        opened = os.fstat(gate_fd)
        rebound = os.stat(
            _GATE_BASENAME_V1, dir_fd=runtime_fd, follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size != 0
            or (opened.st_uid, opened.st_gid) != owner
            or stat.S_IMODE(opened.st_mode) != 0o600
            or snapshot_stat_v1(opened) != snapshot_stat_v1(rebound)
        ):
            raise _fail("gate metadata")
        require_session()
    except DistributionAssemblerError:
        raise
    except OSError as exc:
        raise _fail("installation") from exc
    finally:
        if gate_fd is not None:
            os.close(gate_fd)
        if runtime_fd is not None:
            os.close(runtime_fd)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def install_startup_gate_v1(session: object) -> InstalledStartupGateV1:
    """Create or revalidate the fixed root-owned gate under deployment lock."""
    _require_linux_v1()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise DistributionAssemblerError(
            "birth_ownership_administrative_required",
        )
    from executor_birth_ownership_coordinator import (
        _require_deployment_lock_session_v1,
    )

    require_session = lambda: _require_deployment_lock_session_v1(session)
    _install_boot_rule_v1(Path("/etc/tmpfiles.d"), (0, 0), require_session)
    _install_startup_gate_core_v1(
        runtime_root=RUNTIME_ROOT, owner=(0, 0),
        require_session=require_session,
    )
    return InstalledStartupGateV1(
        RUNTIME_ROOT.as_posix(), STARTUP_GATE_PATH_V1.as_posix(),
        0o600, _GATE_SEAL_V1,
    )


def _install_startup_gate_for_test_v1(
    session: object, ownership_root: Path, runtime_root: Path,
) -> _InstalledStartupGateForTestV1:
    from executor_birth_ownership_coordinator import (
        _require_test_deployment_lock_session_v1,
    )

    ownership_root = Path(ownership_root)
    runtime_root = Path(runtime_root)
    require_session = lambda: _require_test_deployment_lock_session_v1(
        session, ownership_root,
    )
    _install_startup_gate_core_v1(
        runtime_root=runtime_root,
        owner=(os.geteuid(), os.getegid()),
        require_session=require_session,
    )
    return _InstalledStartupGateForTestV1(
        runtime_root, runtime_root / _GATE_BASENAME_V1,
        0o600, _GATE_TEST_SEAL_V1,
    )


__all__ = ["InstalledStartupGateV1", "install_startup_gate_v1"]
