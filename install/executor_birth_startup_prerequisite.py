"""Durably publish the startup prerequisite inside the complete F4 crossing."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
from pathlib import Path
from typing import Callable


_REPOSITORY = Path(__file__).resolve().parents[1]
if str(_REPOSITORY) not in sys.path:  # pragma: no cover - installer bootstrap
    sys.path.insert(0, str(_REPOSITORY))
_RUNTIME = _REPOSITORY / "runtime"
if str(_RUNTIME) not in sys.path:  # pragma: no cover - installer bootstrap
    sys.path.insert(0, str(_RUNTIME))

from executor_birth_distribution_assembler import (
    DistributionAssemblerError,
    MAX_STARTUP_PREREQUISITE_BYTES_V1,
    StartupPrerequisiteV1,
    decode_startup_prerequisite_v1,
    encode_startup_prerequisite_v1,
)
from executor_birth_ownership_authorities import DEFAULT_OWNERSHIP_ROOT_V1
from install.executor_birth_source_receiver import (
    _ensure_child_directory_v1,
    _identity,
    _open_absolute_directory_v1,
    _rename_no_replace_v1,
    _require_absolute_chain_bound_v1,
    _stable_identity,
    _write_all_v1,
)


STARTUP_PREREQUISITES_DIRECTORY_V1 = "startup-prerequisites-v1"
MAX_STARTUP_PREREQUISITES_V1 = 4096
_FINAL_RE_V1 = re.compile(r"sha256:([0-9a-f]{64})\.json\Z")
_TEMPORARY_RE_V1 = re.compile(r"\.([0-9a-f]{64})\.json\.tmp\Z")
_READ_FLAGS_V1 = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _fail(detail: str) -> DistributionAssemblerError:
    return DistributionAssemblerError(
        "birth_ownership_startup_prerequisite_invalid", detail,
    )


def _require_linux_v1() -> None:
    if not sys.platform.startswith("linux"):
        raise DistributionAssemblerError(
            "birth_ownership_platform_unsupported",
        )


def _read_bound_file_v1(
    directory_fd: int, name: str, *, owner: tuple[int, int],
) -> tuple[StartupPrerequisiteV1, bytes, int]:
    descriptor = None
    try:
        descriptor = os.open(name, _READ_FLAGS_V1, dir_fd=directory_fd)
        before = os.fstat(descriptor)
        rebound = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_uid, before.st_gid) != owner
            or stat.S_IMODE(before.st_mode) != 0o644
            or not 0 < before.st_size <= MAX_STARTUP_PREREQUISITE_BYTES_V1
            or _identity(before) != _identity(rebound)
        ):
            raise _fail("file metadata")
        content = bytearray()
        while len(content) <= MAX_STARTUP_PREREQUISITE_BYTES_V1:
            chunk = os.read(
                descriptor,
                min(65536, MAX_STARTUP_PREREQUISITE_BYTES_V1 + 1 - len(content)),
            )
            if not chunk:
                break
            content.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(content) != before.st_size
            or _stable_identity(_identity(before))
            != _stable_identity(_identity(after))
        ):
            raise _fail("file changed")
        encoded = bytes(content)
        try:
            record = decode_startup_prerequisite_v1(encoded)
        except DistributionAssemblerError as exc:
            raise _fail("file content") from exc
        return record, encoded, descriptor
    except DistributionAssemblerError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise _fail("file read") from exc


def _read_temporary_file_v1(
    directory_fd: int, name: str, *, owner: tuple[int, int],
) -> tuple[bytes, int, int]:
    descriptor = None
    try:
        descriptor = os.open(name, _READ_FLAGS_V1, dir_fd=directory_fd)
        before = os.fstat(descriptor)
        rebound = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_uid, before.st_gid) != owner
            or mode not in {0o600, 0o644}
            or before.st_size > MAX_STARTUP_PREREQUISITE_BYTES_V1
            or _identity(before) != _identity(rebound)
        ):
            raise _fail("temporary metadata")
        content = bytearray()
        while len(content) <= MAX_STARTUP_PREREQUISITE_BYTES_V1:
            chunk = os.read(
                descriptor,
                min(65536, MAX_STARTUP_PREREQUISITE_BYTES_V1 + 1 - len(content)),
            )
            if not chunk:
                break
            content.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(content) != before.st_size
            or _stable_identity(_identity(before))
            != _stable_identity(_identity(after))
        ):
            raise _fail("temporary changed")
        return bytes(content), mode, descriptor
    except DistributionAssemblerError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise _fail("temporary read") from exc


def _require_inventory_v1(
    directory_fd: int, *, owner: tuple[int, int], current_hex: str,
) -> tuple[str, ...]:
    try:
        with os.scandir(directory_fd) as iterator:
            names = tuple(sorted(item.name for item in iterator))
    except OSError as exc:
        raise _fail("inventory") from exc
    if len(names) > MAX_STARTUP_PREREQUISITES_V1 + 1:
        raise _fail("inventory size")
    for name in names:
        final = _FINAL_RE_V1.fullmatch(name)
        temporary = _TEMPORARY_RE_V1.fullmatch(name)
        if final is None and temporary is None:
            raise _fail("inventory name")
        if temporary is not None and temporary.group(1) != current_hex:
            raise _fail("foreign temporary")
        if final is not None:
            record, _encoded, descriptor = _read_bound_file_v1(
                directory_fd, name, owner=owner,
            )
            try:
                if final.group(1) != record.request_id.removeprefix("sha256:"):
                    raise _fail("file binding")
            finally:
                os.close(descriptor)
        else:
            _content, _mode, descriptor = _read_temporary_file_v1(
                directory_fd, name, owner=owner,
            )
            os.close(descriptor)
    return names


def _finish_temporary_v1(
    directory_fd: int, name: str, *, encoded: bytes,
    owner: tuple[int, int],
) -> int:
    content, mode, descriptor = _read_temporary_file_v1(
        directory_fd, name, owner=owner,
    )
    os.close(descriptor)
    if content != encoded and (
        mode != 0o600 or not encoded.startswith(content)
    ):
        raise _fail("temporary conflict")
    writable = None
    try:
        if content != encoded or mode != 0o644:
            writable = os.open(
                name,
                os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            opened = os.fstat(writable)
            rebound = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_uid, opened.st_gid) != owner
                or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_size != len(content)
                or _identity(opened) != _identity(rebound)
            ):
                raise _fail("temporary recovery metadata")
            if os.lseek(writable, 0, os.SEEK_END) != len(content):
                raise _fail("temporary recovery offset")
            _write_all_v1(writable, encoded[len(content):])
            os.fsync(writable)
            os.fchmod(writable, 0o644)
            os.fsync(writable)
    except DistributionAssemblerError:
        raise
    except OSError as exc:
        raise _fail("temporary recovery") from exc
    finally:
        if writable is not None:
            os.close(writable)
    record, observed, descriptor = _read_bound_file_v1(
        directory_fd, name, owner=owner,
    )
    if record != decode_startup_prerequisite_v1(encoded) or observed != encoded:
        os.close(descriptor)
        raise _fail("temporary reread")
    return descriptor


def _publish_core_v1(
    prerequisite: StartupPrerequisiteV1, *, ownership_root: Path,
    owner: tuple[int, int], require_sessions: Callable[[], None],
    _crash_seam: Callable[[str], None] | None = None,
) -> object:
    _require_linux_v1()
    if (
        type(prerequisite) is not StartupPrerequisiteV1
        or not isinstance(ownership_root, Path)
        or not ownership_root.is_absolute()
        or not callable(require_sessions)
        or _crash_seam is not None and not callable(_crash_seam)
    ):
        raise _fail("arguments")
    encoded = encode_startup_prerequisite_v1(prerequisite)
    request_hex = prerequisite.request_id.removeprefix("sha256:")
    final_name = f"{prerequisite.request_id}.json"
    temporary_name = f".{request_hex}.json.tmp"
    descriptors: list[int] = []
    directory_fd = None
    temporary_fd = None
    try:
        require_sessions()
        descriptors, parts = _open_absolute_directory_v1(
            ownership_root.as_posix(),
        )
        _require_absolute_chain_bound_v1(
            descriptors, parts, detail="startup prerequisite root",
        )
        root_fd = descriptors[-1]
        root = os.fstat(root_fd)
        rebound = ownership_root.lstat()
        if (
            not stat.S_ISDIR(root.st_mode)
            or (root.st_uid, root.st_gid) != owner
            or stat.S_IMODE(root.st_mode) != 0o755
            or _identity(root) != _identity(rebound)
        ):
            raise _fail("ownership root")
        require_sessions()
        directory_fd = _ensure_child_directory_v1(
            root_fd, STARTUP_PREREQUISITES_DIRECTORY_V1,
            owner=owner, mode=0o755,
        )
        directory = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory.st_mode)
            or (directory.st_uid, directory.st_gid) != owner
            or stat.S_IMODE(directory.st_mode) != 0o755
        ):
            raise _fail("directory metadata")
        names = _require_inventory_v1(
            directory_fd, owner=owner, current_hex=request_hex,
        )
        if final_name in names:
            if temporary_name in names:
                raise _fail("duplicate material")
            observed, content, final_fd = _read_bound_file_v1(
                directory_fd, final_name, owner=owner,
            )
            try:
                if observed != prerequisite or content != encoded:
                    raise _fail("published conflict")
            finally:
                os.close(final_fd)
            require_sessions()
        else:
            if temporary_name in names:
                temporary_fd = _finish_temporary_v1(
                    directory_fd, temporary_name,
                    encoded=encoded, owner=owner,
                )
            else:
                require_sessions()
                try:
                    temporary_fd = os.open(
                        temporary_name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                        0o600, dir_fd=directory_fd,
                    )
                    os.fchown(temporary_fd, *owner)
                    os.fsync(temporary_fd)
                    os.fsync(directory_fd)
                    if _crash_seam is not None:
                        _crash_seam("startup_prerequisite_created")
                    _write_all_v1(temporary_fd, encoded)
                    os.fsync(temporary_fd)
                    if _crash_seam is not None:
                        _crash_seam("startup_prerequisite_written")
                    os.fchmod(temporary_fd, 0o644)
                    os.fsync(temporary_fd)
                except OSError as exc:
                    raise _fail("temporary write") from exc
                finally:
                    if temporary_fd is not None:
                        os.close(temporary_fd)
                        temporary_fd = None
                os.fsync(directory_fd)
                temporary_fd = _finish_temporary_v1(
                    directory_fd, temporary_name,
                    encoded=encoded, owner=owner,
                )
            if _crash_seam is not None:
                _crash_seam("startup_prerequisite_temporary")
            require_sessions()
            try:
                _rename_no_replace_v1(
                    directory_fd, temporary_name, directory_fd, final_name,
                    expected_fd=temporary_fd, sync_source_parent=False,
                )
            except FileExistsError as exc:
                raise _fail("publication collision") from exc
            os.fsync(directory_fd)
            os.close(temporary_fd)
            temporary_fd = None
            observed, content, final_fd = _read_bound_file_v1(
                directory_fd, final_name, owner=owner,
            )
            try:
                if observed != prerequisite or content != encoded:
                    raise _fail("published reread")
            finally:
                os.close(final_fd)
            require_sessions()
    except DistributionAssemblerError:
        raise
    except OSError as exc:
        raise _fail("publication") from exc
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if directory_fd is not None:
            os.close(directory_fd)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _publish_startup_prerequisite_locked_v2(
    prerequisite: object, complete: object, sessions: tuple[object, ...],
    *, _crash_seam: Callable[[str], None] | None = None,
) -> object:
    """Product seam used only by the complete crossing wrapper."""
    if type(sessions) is not tuple or len(sessions) != 3:
        raise _fail("sessions")
    from contract_cutover_guard import _require_maintenance_session_v1
    from executor_birth_dominant_startup import _require_product_sessions_v1
    from executor_birth_ownership_coordinator import (
        _startup_prerequisite_from_record_v2,
    )

    sealed = _startup_prerequisite_from_record_v2(prerequisite, complete)
    require = lambda: _require_product_sessions_v1(sessions)
    require()
    _publish_core_v1(
        prerequisite,
        ownership_root=DEFAULT_OWNERSHIP_ROOT_V1,
        owner=(0, 0), require_sessions=require,
        _crash_seam=_crash_seam,
    )
    _require_maintenance_session_v1(sessions[2])
    return sealed


def _publish_startup_prerequisite_for_test_v2(
    prerequisite: StartupPrerequisiteV1, *, deployment_session: object,
    startup_session: object, ownership_root: Path, gate_path: Path,
    _crash_seam: Callable[[str], None] | None = None,
) -> object:
    """Portable nominal seam with exact test deployment and startup sessions."""
    from executor_birth_ownership_coordinator import (
        _require_test_deployment_lock_session_v1,
        _startup_prerequisite_for_test,
    )
    from executor_birth_startup_gate import (
        _require_exclusive_startup_gate_session_for_test_v1,
    )

    ownership_root = Path(ownership_root)
    gate_path = Path(gate_path)

    def require() -> None:
        _require_test_deployment_lock_session_v1(
            deployment_session, ownership_root,
        )
        _require_exclusive_startup_gate_session_for_test_v1(
            startup_session, gate_path,
        )

    require()
    _publish_core_v1(
        prerequisite, ownership_root=ownership_root,
        owner=(os.geteuid(), os.getegid()), require_sessions=require,
        _crash_seam=_crash_seam,
    )
    encoded = encode_startup_prerequisite_v1(prerequisite)
    return _startup_prerequisite_for_test(
        prerequisite.prerequisite_id,
        "sha256:" + hashlib.sha256(encoded).hexdigest(),
    )


__all__: tuple[str, ...] = ()
