"""Install the fixed startup gate before any dominant unit can be published."""
from __future__ import annotations

import os
import stat
import sys
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
from install.executor_birth_source_receiver import (
    _ensure_child_directory_v1, _identity, _name_status_v1,
    _open_absolute_directory_v1, _require_absolute_chain_bound_v1,
)


_GATE_SEAL_V1 = object()
_GATE_TEST_SEAL_V1 = object()
_GATE_BASENAME_V1 = "startup-v1.lock"
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
            or _identity(opened) != _identity(rebound)
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
