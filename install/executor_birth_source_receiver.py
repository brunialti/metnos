"""Root-only, content-addressed receiver for RM-0008 source trees.

The productive entry chooses only the source directory and service account.
The destination and deployment lock are fixed here; the reusable core remains
private and accepts only the live nominal lock sessions minted by G6-A.
"""
from __future__ import annotations

import ctypes
import errno
import os
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator


_RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
if str(_RUNTIME) not in sys.path:  # pragma: no cover - installer bootstrap
    sys.path.insert(0, str(_RUNTIME))

from executor_birth_distribution_assembler import (
    DistributionAssemblerError,
    MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1,
    MAX_RECEIVED_SOURCE_DIRECTORIES_V1,
    MAX_RECEIVED_SOURCE_FILES_V1,
    MAX_RECEIVED_SOURCE_PATH_DEPTH_V1,
    MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1,
    ReceivedSourceFileV1,
    ReceivedSourceV1,
    build_received_source_v1,
    decode_received_source_v1,
    encode_received_source_v1,
    received_source_file_hash_v1,
)
from executor_birth_ownership_authorities import DEFAULT_OWNERSHIP_ROOT_V1


INCOMING_DIRECTORY_BASENAME_V1 = "incoming-v1"
SOURCES_DIRECTORY_BASENAME_V1 = "sources-v1"
RECEIVED_SOURCE_BASENAME_V1 = "received-source-v1.json"
MAX_RECEIVED_SOURCE_BYTES_V1 = MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1
MAX_SOURCE_FILES_V1 = MAX_RECEIVED_SOURCE_FILES_V1
MAX_SOURCE_DIRECTORIES_V1 = MAX_RECEIVED_SOURCE_DIRECTORIES_V1
MAX_SOURCE_PATH_DEPTH_V1 = MAX_RECEIVED_SOURCE_PATH_DEPTH_V1
MAX_SOURCE_BYTES_V1 = MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1
MAX_SOURCE_TREE_ENTRIES_V1 = (
    MAX_SOURCE_FILES_V1 + MAX_SOURCE_DIRECTORIES_V1 + 1
)

_ACCOUNT_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_SOURCE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RECEIVE_RE = re.compile(r"\.receive-[0-9a-f]{32}\.tmp\Z")
_STRUCTURED_RE = re.compile(r"\.(sha256:[0-9a-f]{64})\.tmp\Z")
_SHELL_BASENAMES = frozenset({"nologin", "false"})
_SYSTEMD_LINGER_ROOT_V1 = Path("/var/lib/systemd/linger")
_USER_RUNTIME_ROOT_V1 = Path("/run/user")
_USER_UNIT_HOME_SUFFIXES_V1 = (
    (".config", "systemd", "user.control"),
    (".config", "systemd", "user"),
    (".local", "share", "systemd", "user"),
)
_USER_UNIT_RUNTIME_SUFFIXES_V1 = (
    ("systemd", "user.control"),
    ("systemd", "transient"),
    ("systemd", "generator.early"),
    ("systemd", "user"),
    ("systemd", "generator"),
    ("systemd", "generator.late"),
)
_GLOBAL_USER_UNIT_ROOTS_V1 = (
    Path("/etc/xdg/systemd/user"),
    Path("/etc/systemd/user"),
    Path("/run/systemd/user"),
    Path("/usr/local/share/systemd/user"),
    Path("/usr/share/systemd/user"),
    Path("/var/lib/snapd/desktop/systemd/user"),
    Path("/usr/local/lib/systemd/user"),
    Path("/usr/lib/systemd/user"),
)
_MAX_USER_UNIT_OBJECTS_V1 = 100_000

_DIRECTORY_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


@dataclass(frozen=True, slots=True)
class _ServiceAccountV1:
    name: str
    uid: int
    gid: int
    supplementary_gids: tuple[int, ...]
    home: str
    shell: str


@dataclass(frozen=True, slots=True)
class _SourceEntryV1:
    path: str
    directory: bool
    identity: tuple[int, ...]
    mode: int
    size: int


def _fail(code: str, detail: str = "") -> DistributionAssemblerError:
    return DistributionAssemblerError(code, detail)


def _require_linux_v1() -> None:
    if not sys.platform.startswith("linux"):
        raise _fail("birth_ownership_platform_unsupported")


def _require_root_v1() -> None:
    if os.geteuid() != 0:
        raise _fail("birth_ownership_deployment_unsafe", "root required")


def _service_user_grammar_v1(value: object) -> str:
    if type(value) is not str or _ACCOUNT_RE.fullmatch(value) is None:
        raise _fail("birth_ownership_deployment_invalid", "service user")
    return value


def _source_path_grammar_v1(value: object) -> str:
    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise _fail("birth_ownership_deployment_invalid", "source") from exc
    if (
        not isinstance(raw, str) or not raw.startswith("/") or raw.startswith("//")
        or "\0" in raw or "\\" in raw or raw != unicodedata.normalize("NFC", raw)
    ):
        raise _fail("birth_ownership_deployment_invalid", "source")
    parts = raw.split("/")[1:]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise _fail("birth_ownership_deployment_invalid", "source")
    if PurePosixPath(raw).as_posix() != raw:
        raise _fail("birth_ownership_deployment_invalid", "source")
    return raw


def _require_lexically_disjoint_v1(source: str, ownership_root: Path) -> None:
    source_path = PurePosixPath(source)
    root_path = PurePosixPath(
        _source_path_grammar_v1(str(ownership_root)),
    )
    if (
        source_path == root_path
        or source_path in root_path.parents
        or root_path in source_path.parents
    ):
        raise _fail("birth_ownership_deployment_unsafe", "source overlaps ownership")


def _relative_component_v1(name: object) -> str:
    if (
        not isinstance(name, str) or not name or name in {".", ".."}
        or "/" in name or "\\" in name or "\0" in name
        or unicodedata.normalize("NFC", name) != name
    ):
        raise _fail("birth_ownership_deployment_invalid", "source name")
    try:
        encoded = name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _fail("birth_ownership_deployment_invalid", "source name") from exc
    if len(encoded) > 255:
        raise _fail("birth_ownership_deployment_invalid", "source name")
    return name


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
        info.st_uid, info.st_gid, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def _entry_identity(info: os.stat_result) -> tuple[int, ...]:
    return _identity(info)


def _stable_identity(value: tuple[int, ...]) -> tuple[int, ...]:
    return value[:7]


def _require_plain_directory_fd_v1(
    descriptor: int, *, owner: tuple[int, int] | None = None,
    mode: int | None = None,
) -> os.stat_result:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or (owner is not None and (info.st_uid, info.st_gid) != owner)
        or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
    ):
        raise _fail("birth_ownership_deployment_unsafe", "directory metadata")
    return info


def _require_plain_file_info_v1(
    info: os.stat_result, *, owner: tuple[int, int] | None = None,
    mode: int | None = None,
) -> None:
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        or (owner is not None and (info.st_uid, info.st_gid) != owner)
        or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
    ):
        raise _fail("birth_ownership_deployment_unsafe", "file metadata")


def _service_account_snapshot_v1(name: str) -> _ServiceAccountV1:
    import pwd

    try:
        entry = pwd.getpwnam(name)
        supplementary = tuple(sorted(set(os.getgrouplist(name, entry.pw_gid))))
    except (KeyError, OSError) as exc:
        raise _fail("birth_ownership_deployment_invalid", "service account") from exc
    home = _canonical_account_path_v1(entry.pw_dir, "service home")
    shell = _canonical_account_path_v1(entry.pw_shell, "service shell")
    if (
        entry.pw_name != name or entry.pw_uid <= 0 or entry.pw_gid <= 0
        or any(type(item) is not int or item <= 0 for item in supplementary)
        or entry.pw_gid not in supplementary
        or PurePosixPath(shell).name not in _SHELL_BASENAMES
    ):
        raise _fail("birth_ownership_deployment_unsafe", "service account")
    resolved = _resolve_root_owned_shell_v1(shell)
    account = _ServiceAccountV1(
        name, entry.pw_uid, entry.pw_gid, supplementary, home, resolved,
    )
    _require_closed_user_authority_v1(account)
    return account


def _canonical_account_path_v1(value: object, detail: str) -> str:
    if (
        type(value) is not str or not value.startswith("/")
        or value.startswith("//") or "\0" in value or "\\" in value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise _fail("birth_ownership_deployment_unsafe", detail)
    candidate = PurePosixPath(value)
    if candidate.as_posix() != value or any(
        part in {"", ".", ".."} for part in candidate.parts[1:]
    ):
        raise _fail("birth_ownership_deployment_unsafe", detail)
    return value


def _require_root_owned_path_v1(path: str, *, final_regular: bool) -> None:
    current = Path("/")
    parts = PurePosixPath(path).parts[1:]
    for index, component in enumerate(parts):
        current /= component
        try:
            info = current.lstat()
        except OSError as exc:
            raise _fail("birth_ownership_deployment_unsafe", "service shell") from exc
        is_final = index == len(parts) - 1
        if (
            info.st_uid != 0 or info.st_gid != 0
            or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022)
        ):
            raise _fail("birth_ownership_deployment_unsafe", "service shell")
        if is_final:
            if final_regular and not (
                stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            ):
                raise _fail("birth_ownership_deployment_unsafe", "service shell")
        elif not (stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            raise _fail("birth_ownership_deployment_unsafe", "service shell")


def _resolve_root_owned_shell_v1(path: str) -> str:
    _require_root_owned_path_v1(path, final_regular=True)
    try:
        resolved_path = Path(path).resolve(strict=True)
    except OSError as exc:
        raise _fail("birth_ownership_deployment_unsafe", "service shell") from exc
    resolved = _canonical_account_path_v1(str(resolved_path), "service shell")
    if PurePosixPath(resolved).name not in _SHELL_BASENAMES:
        raise _fail("birth_ownership_deployment_unsafe", "service shell")
    _require_root_owned_path_v1(resolved, final_regular=True)
    try:
        info = resolved_path.lstat()
    except OSError as exc:
        raise _fail("birth_ownership_deployment_unsafe", "service shell") from exc
    if not stat.S_ISREG(info.st_mode):
        raise _fail("birth_ownership_deployment_unsafe", "service shell")
    return resolved


def _account_can_create_v1(info: os.stat_result, account: _ServiceAccountV1) -> bool:
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid == account.uid:
        required = stat.S_IWUSR | stat.S_IXUSR
    elif info.st_gid in account.supplementary_gids:
        required = stat.S_IWGRP | stat.S_IXGRP
    else:
        required = stat.S_IWOTH | stat.S_IXOTH
    return mode & required == required


def _account_can_write_v1(info: os.stat_result, account: _ServiceAccountV1) -> bool:
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid == account.uid:
        required = stat.S_IWUSR
    elif info.st_gid in account.supplementary_gids:
        required = stat.S_IWGRP
    else:
        required = stat.S_IWOTH
    return bool(mode & required)


def _require_no_access_acl_v1(path: Path) -> None:
    try:
        os.getxattr(path, "system.posix_acl_access", follow_symlinks=False)
    except OSError as exc:
        if exc.errno not in {
            errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA),
            errno.ENOTSUP, errno.EOPNOTSUPP,
        }:
            raise _fail(
                "birth_ownership_deployment_unsafe", "user unit root",
            ) from exc
    else:
        # A non-trivial ACL needs an ACL-aware evaluator.  B2 remains
        # conservative and refuses it rather than assuming mode bits win.
        raise _fail("birth_ownership_deployment_unsafe", "user unit root")


def _require_user_unit_path_closed_v1(
    path: Path, account: _ServiceAccountV1, *, final_directory: bool | None,
) -> Path | None:
    candidate = Path(path)
    for _redirect in range(9):
        current = Path("/")
        redirected = False
        parts = PurePosixPath(str(candidate)).parts[1:]
        try:
            root_info = current.lstat()
        except OSError as exc:
            raise _fail(
                "birth_ownership_deployment_unsafe", "user unit root",
            ) from exc
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or _account_can_create_v1(root_info, account)
        ):
            raise _fail("birth_ownership_deployment_unsafe", "user unit root")
        _require_no_access_acl_v1(current)
        for index, component in enumerate(parts):
            current /= component
            try:
                info = current.lstat()
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise _fail(
                    "birth_ownership_deployment_unsafe", "user unit root",
                ) from exc
            if stat.S_ISLNK(info.st_mode):
                try:
                    resolved = candidate.resolve(strict=False)
                except (OSError, RuntimeError) as exc:
                    raise _fail(
                        "birth_ownership_deployment_unsafe", "user unit root",
                    ) from exc
                if not resolved.is_absolute() or resolved == candidate:
                    raise _fail(
                        "birth_ownership_deployment_unsafe", "user unit root",
                    )
                candidate = resolved
                redirected = True
                break
            is_final = index == len(parts) - 1
            if stat.S_ISDIR(info.st_mode):
                if _account_can_create_v1(info, account):
                    raise _fail(
                        "birth_ownership_deployment_unsafe", "user unit root",
                    )
            elif (
                is_final and final_directory is not True
                and stat.S_ISREG(info.st_mode)
            ):
                if _account_can_write_v1(info, account):
                    raise _fail(
                        "birth_ownership_deployment_unsafe", "user unit root",
                    )
            else:
                raise _fail(
                    "birth_ownership_deployment_unsafe", "user unit root",
                )
            _require_no_access_acl_v1(current)
        if redirected:
            continue
        if final_directory is True:
            try:
                final_info = current.lstat()
            except OSError as exc:
                raise _fail(
                    "birth_ownership_deployment_unsafe", "user unit root",
                ) from exc
            if not stat.S_ISDIR(final_info.st_mode):
                raise _fail(
                    "birth_ownership_deployment_unsafe", "user unit root",
                )
        return current
    raise _fail("birth_ownership_deployment_unsafe", "user unit root")


def _require_user_unit_root_closed_v1(
    path: Path, account: _ServiceAccountV1,
) -> None:
    root = _require_user_unit_path_closed_v1(
        path, account, final_directory=True,
    )
    if root is None:
        return
    pending = [root]
    seen: set[tuple[int, int]] = set()
    observed = 0
    while pending:
        current = pending.pop()
        closed = _require_user_unit_path_closed_v1(
            current, account, final_directory=None,
        )
        if closed is None:
            continue
        try:
            info = closed.lstat()
        except OSError as exc:
            raise _fail("birth_ownership_deployment_unsafe", "user unit root") from exc
        identity = (info.st_dev, info.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        observed += 1
        if observed > _MAX_USER_UNIT_OBJECTS_V1:
            raise _fail("birth_ownership_deployment_unsafe", "user unit root")
        if stat.S_ISDIR(info.st_mode):
            try:
                with os.scandir(closed) as iterator:
                    children = []
                    for item in iterator:
                        children.append(Path(item.path))
                        if (
                            observed + len(children)
                            > _MAX_USER_UNIT_OBJECTS_V1
                        ):
                            raise _fail(
                                "birth_ownership_deployment_unsafe",
                                "user unit root",
                            )
            except OSError as exc:
                raise _fail(
                    "birth_ownership_deployment_unsafe", "user unit root",
                ) from exc
            pending.extend(children)


def _require_closed_user_authority_v1(
    account: _ServiceAccountV1, *,
    linger_root: Path = _SYSTEMD_LINGER_ROOT_V1,
    runtime_root: Path = _USER_RUNTIME_ROOT_V1,
) -> None:
    user_runtime = runtime_root / str(account.uid)
    markers = (
        linger_root / account.name,
        user_runtime / "systemd",
        user_runtime / "bus",
    )
    for marker in markers:
        try:
            marker.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise _fail("birth_ownership_deployment_unsafe", "service account") from exc
        else:
            raise _fail("birth_ownership_deployment_unsafe", "service account")
    roots = [
        Path(account.home).joinpath(*suffix)
        for suffix in _USER_UNIT_HOME_SUFFIXES_V1
    ]
    roots.extend(
        user_runtime.joinpath(*suffix)
        for suffix in _USER_UNIT_RUNTIME_SUFFIXES_V1
    )
    roots.extend(_GLOBAL_USER_UNIT_ROOTS_V1)
    for root in roots:
        _require_user_unit_root_closed_v1(root, account)


def _open_absolute_directory_v1(path: str) -> tuple[list[int], tuple[str, ...]]:
    descriptors: list[int] = []
    try:
        current = os.open("/", _DIRECTORY_FLAGS)
        descriptors.append(current)
        parts = path.split("/")[1:]
        parent = current
        for component in parts:
            parent = current
            current = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent)
            descriptors.append(current)
        return descriptors, tuple(parts)
    except OSError as exc:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise _fail("birth_ownership_deployment_unsafe", "source directory") from exc


def _require_absolute_chain_bound_v1(
    descriptors: list[int], parts: tuple[str, ...], *, detail: str,
) -> None:
    if len(descriptors) != len(parts) + 1:
        raise _fail("birth_ownership_deployment_unsafe", detail)
    for index, component in enumerate(parts):
        try:
            rebound = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptors[index])
            try:
                expected = os.fstat(descriptors[index + 1])
                observed = os.fstat(rebound)
            finally:
                os.close(rebound)
        except OSError as exc:
            raise _fail("birth_ownership_deployment_unsafe", detail) from exc
        if _identity(expected) != _identity(observed):
            raise _fail("birth_ownership_deployment_unsafe", detail)


def _require_disjoint_directory_chains_v1(
    source_descriptors: list[int], ownership_descriptors: list[int],
) -> None:
    source = os.fstat(source_descriptors[-1])
    ownership = os.fstat(ownership_descriptors[-1])
    source_key = (source.st_dev, source.st_ino)
    ownership_key = (ownership.st_dev, ownership.st_ino)
    source_chain = {
        (info.st_dev, info.st_ino)
        for info in (os.fstat(item) for item in source_descriptors)
    }
    ownership_chain = {
        (info.st_dev, info.st_ino)
        for info in (os.fstat(item) for item in ownership_descriptors)
    }
    if ownership_key in source_chain or source_key in ownership_chain:
        raise _fail("birth_ownership_deployment_unsafe", "source overlaps ownership")


def _scan_source_v1(root_fd: int) -> tuple[_SourceEntryV1, ...]:
    entries: list[_SourceEntryV1] = []
    total_files = 0
    total_directories = 0
    total_bytes = 0
    opened: set[int] = set()
    stack: list[
        tuple[
            str, int, tuple[str, ...], tuple[int, ...] | None, str | None,
        ]
    ] = [
        ("enter", root_fd, (), None, None),
    ]
    try:
        while stack:
            phase, directory_fd, prefix, expected_identity, child_name = stack.pop()
            if phase == "open":
                assert expected_identity is not None and child_name is not None
                child_fd: int | None = None
                try:
                    child_fd = _open_child_directory_v1(directory_fd, child_name)
                    child_info = os.fstat(child_fd)
                    if _identity(child_info) != expected_identity:
                        raise _fail(
                            "birth_ownership_deployment_unsafe", "source changed",
                        )
                except DistributionAssemblerError:
                    if child_fd is not None:
                        os.close(child_fd)
                    raise
                except OSError as exc:
                    if child_fd is not None:
                        os.close(child_fd)
                    raise _fail(
                        "birth_ownership_deployment_unsafe", "source directory",
                    ) from exc
                assert child_fd is not None
                opened.add(child_fd)
                stack.append(("enter", child_fd, prefix, None, None))
                continue
            if phase == "exit":
                assert expected_identity is not None
                if _identity(os.fstat(directory_fd)) != expected_identity:
                    raise _fail("birth_ownership_deployment_unsafe", "source changed")
                if directory_fd in opened:
                    opened.remove(directory_fd)
                    os.close(directory_fd)
                continue
            before = os.fstat(directory_fd)
            if not stat.S_ISDIR(before.st_mode):
                raise _fail("birth_ownership_deployment_unsafe", "source directory")
            try:
                with os.scandir(directory_fd) as observed:
                    names = []
                    for item in observed:
                        names.append(_relative_component_v1(item.name))
                        if len(names) > MAX_SOURCE_TREE_ENTRIES_V1:
                            raise _fail(
                                "birth_ownership_deployment_invalid",
                                "source limits",
                            )
                    names.sort(key=lambda item: item.encode("utf-8"))
            except OSError as exc:
                raise _fail(
                    "birth_ownership_deployment_unsafe", "source inventory",
                ) from exc
            if not names:
                raise _fail(
                    "birth_ownership_deployment_invalid",
                    "empty source" if not prefix else "empty directory",
                )
            if len(names) != len(set(names)):
                raise _fail("birth_ownership_deployment_invalid", "source inventory")
            stack.append(("exit", directory_fd, prefix, _identity(before), None))
            children: list[tuple[str, tuple[str, ...], tuple[int, ...]]] = []
            for name in names:
                relative_parts = prefix + (name,)
                if len(relative_parts) > MAX_SOURCE_PATH_DEPTH_V1:
                    raise _fail(
                        "birth_ownership_deployment_invalid", "source depth",
                    )
                relative = "/".join(relative_parts)
                if not prefix and name == RECEIVED_SOURCE_BASENAME_V1:
                    raise _fail(
                        "birth_ownership_deployment_invalid", "reserved source name",
                    )
                flags = (
                    getattr(os, "O_PATH", os.O_RDONLY)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                member_fd: int | None = None
                try:
                    member_fd = os.open(name, flags, dir_fd=directory_fd)
                    info = os.fstat(member_fd)
                except OSError as exc:
                    raise _fail(
                        "birth_ownership_deployment_unsafe", "source member",
                    ) from exc
                finally:
                    if member_fd is not None:
                        os.close(member_fd)
                if stat.S_ISDIR(info.st_mode):
                    total_directories += 1
                    if total_directories > MAX_SOURCE_DIRECTORIES_V1:
                        raise _fail(
                            "birth_ownership_deployment_invalid", "source limits",
                        )
                    entries.append(_SourceEntryV1(
                        relative, True, _entry_identity(info),
                        stat.S_IMODE(info.st_mode), 0,
                    ))
                    children.append((name, relative_parts, _identity(info)))
                elif stat.S_ISREG(info.st_mode):
                    mode = stat.S_IMODE(info.st_mode)
                    if info.st_nlink != 1 or mode not in {0o644, 0o755}:
                        raise _fail("birth_ownership_deployment_unsafe", "source file")
                    if info.st_size < 0:
                        raise _fail("birth_ownership_deployment_invalid", "source size")
                    total_files += 1
                    total_bytes += info.st_size
                    if (
                        total_files > MAX_SOURCE_FILES_V1
                        or total_bytes > MAX_SOURCE_BYTES_V1
                    ):
                        raise _fail("birth_ownership_deployment_invalid", "source limits")
                    entries.append(_SourceEntryV1(
                        relative, False, _entry_identity(info), mode, info.st_size,
                    ))
                else:
                    raise _fail("birth_ownership_deployment_unsafe", "source member")
            for name, relative_parts, child_identity in reversed(children):
                # The parent stays open while one child at a time is visited.
                # Descriptor use is therefore bounded by tree depth, not width.
                stack.append((
                    "open", directory_fd, relative_parts, child_identity, name,
                ))
    finally:
        for descriptor in tuple(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass
    return tuple(sorted(entries, key=lambda item: item.path.encode("utf-8")))


def _entry_map(entries: Iterable[_SourceEntryV1]) -> dict[str, _SourceEntryV1]:
    return {item.path: item for item in entries}


def _open_source_file_v1(
    root_fd: int, item: _SourceEntryV1,
    expected: dict[str, _SourceEntryV1],
) -> tuple[int, list[int]]:
    current = root_fd
    current_owned: int | None = None
    descriptor: int | None = None
    try:
        parts = item.path.split("/")
        prefix: list[str] = []
        for component in parts[:-1]:
            prefix.append(component)
            next_descriptor: int | None = None
            try:
                next_descriptor = os.open(
                    component, _DIRECTORY_FLAGS, dir_fd=current,
                )
                declared = expected["/".join(prefix)]
                if _identity(os.fstat(next_descriptor)) != declared.identity:
                    raise _fail(
                        "birth_ownership_deployment_unsafe", "source changed",
                    )
            except BaseException:
                if next_descriptor is not None:
                    os.close(next_descriptor)
                raise
            assert next_descriptor is not None
            if current_owned is not None:
                os.close(current_owned)
            current = next_descriptor
            current_owned = next_descriptor
        descriptor = os.open(parts[-1], _READ_FLAGS, dir_fd=current)
        if current_owned is not None:
            os.close(current_owned)
            current_owned = None
        info = os.fstat(descriptor)
        if _identity(info) != item.identity:
            raise _fail("birth_ownership_deployment_unsafe", "source changed")
        _require_plain_file_info_v1(info, mode=item.mode)
        return descriptor, [descriptor]
    except DistributionAssemblerError:
        if descriptor is not None:
            os.close(descriptor)
        if current_owned is not None:
            os.close(current_owned)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        if current_owned is not None:
            os.close(current_owned)
        raise _fail("birth_ownership_deployment_unsafe", "source changed") from exc


def _read_chunks_v1(descriptor: int, size: int) -> Iterator[bytes]:
    consumed = 0
    while consumed <= size:
        try:
            chunk = os.read(descriptor, min(1024 * 1024, size + 1 - consumed))
        except InterruptedError:
            continue
        if not chunk:
            break
        consumed += len(chunk)
        yield chunk
    if consumed != size:
        raise _fail("birth_ownership_deployment_unsafe", "source changed")


def _write_all_v1(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        try:
            count = os.write(descriptor, view[offset:])
        except InterruptedError:
            continue
        if count <= 0:
            raise OSError(errno.EIO, "short write")
        offset += count


def _open_child_directory_v1(parent_fd: int, name: str) -> int:
    descriptor: int | None = None
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        path_info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(path_info.st_mode)
            or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)
        ):
            raise _fail("birth_ownership_deployment_unsafe", "directory binding")
        return descriptor
    except DistributionAssemblerError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise _fail("birth_ownership_deployment_unsafe", "directory open") from exc


def _ensure_child_directory_v1(
    parent_fd: int, name: str, *, owner: tuple[int, int], mode: int,
) -> int:
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "directory create") from exc
    descriptor = _open_child_directory_v1(parent_fd, name)
    try:
        if created:
            os.fchown(descriptor, *owner)
            os.fchmod(descriptor, mode)
        else:
            info = os.fstat(descriptor)
            current_mode = stat.S_IMODE(info.st_mode)
            if current_mode != mode:
                try:
                    with os.scandir(descriptor) as iterator:
                        empty = next(iterator, None) is None
                except OSError as exc:
                    raise _fail(
                        "birth_ownership_recovery_required", "directory recovery",
                    ) from exc
                # A process can die after mkdir(0700) and before fchmod.  Under
                # the deployment lock, only this exact owner-only empty prefix
                # is an unambiguous residue that is safe to finish.
                if (
                    current_mode != 0o700
                    or (info.st_uid, info.st_gid) != owner
                    or not empty
                ):
                    raise _fail(
                        "birth_ownership_deployment_unsafe", "directory metadata",
                    )
                os.fchmod(descriptor, mode)
        _require_plain_directory_fd_v1(descriptor, owner=owner, mode=mode)
        os.fsync(descriptor)
        os.fsync(parent_fd)
        rebound = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) != (rebound.st_dev, rebound.st_ino):
            raise _fail("birth_ownership_deployment_unsafe", "directory binding")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_descendant_directory_v1(root_fd: int, parts: tuple[str, ...]) -> tuple[int, list[int]]:
    current = root_fd
    current_owned: int | None = None
    try:
        for component in parts:
            next_descriptor = _open_child_directory_v1(current, component)
            if current_owned is not None:
                os.close(current_owned)
            current = next_descriptor
            current_owned = next_descriptor
        return current, ([] if current_owned is None else [current_owned])
    except BaseException:
        if current_owned is not None:
            os.close(current_owned)
        raise


def _create_private_directory_v1(
    parent_fd: int, name: str, *, owner: tuple[int, int],
) -> tuple[int, tuple[int, ...]]:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        raise
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "temporary create") from exc
    descriptor: int | None = None
    try:
        descriptor = _open_child_directory_v1(parent_fd, name)
        os.fchown(descriptor, *owner)
        os.fchmod(descriptor, 0o700)
        os.fsync(descriptor)
        os.fsync(parent_fd)
        info = _require_plain_directory_fd_v1(
            descriptor, owner=owner, mode=0o700,
        )
        return descriptor, _identity(info)
    except BaseException:
        if descriptor is not None:
            try:
                info = os.fstat(descriptor)
                rebound = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                with os.scandir(descriptor) as iterator:
                    empty = next(iterator, None) is None
                if (
                    empty and stat.S_ISDIR(rebound.st_mode)
                    and (info.st_dev, info.st_ino) == (rebound.st_dev, rebound.st_ino)
                ):
                    os.rmdir(name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
            except OSError:
                pass
            os.close(descriptor)
        else:
            try:
                info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    os.rmdir(name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
            except OSError:
                pass
        raise


def _copy_source_file_v1(
    source_root_fd: int, temporary_root_fd: int, item: _SourceEntryV1,
    source_entries: dict[str, _SourceEntryV1], *, owner: tuple[int, int],
) -> ReceivedSourceFileV1:
    source_fd, source_opened = _open_source_file_v1(
        source_root_fd, item, source_entries,
    )
    destination_parent = temporary_root_fd
    destination_opened: list[int] = []
    destination_fd: int | None = None
    try:
        parent_parts = tuple(item.path.split("/")[:-1])
        destination_parent, destination_opened = _open_descendant_directory_v1(
            temporary_root_fd, parent_parts,
        )
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        )
        destination_fd = os.open(
            item.path.split("/")[-1], flags, 0o600,
            dir_fd=destination_parent,
        )
        destination_before = os.fstat(destination_fd)
        _require_plain_file_info_v1(destination_before, owner=owner, mode=0o600)

        def copied_chunks() -> Iterator[bytes]:
            for chunk in _read_chunks_v1(source_fd, item.size):
                _write_all_v1(destination_fd, chunk)
                yield chunk

        content_hash = received_source_file_hash_v1(
            item.path, item.size, copied_chunks(),
        )
        if _identity(os.fstat(source_fd)) != item.identity:
            raise _fail("birth_ownership_deployment_unsafe", "source changed")
        destination_after_write = os.fstat(destination_fd)
        if (
            not stat.S_ISREG(destination_after_write.st_mode)
            or destination_after_write.st_nlink != 1
            or destination_after_write.st_size != item.size
            or (destination_after_write.st_uid, destination_after_write.st_gid) != owner
        ):
            raise _fail("birth_ownership_recovery_required", "temporary file")
        os.fsync(destination_fd)
        os.fchown(destination_fd, *owner)
        os.fchmod(destination_fd, item.mode)
        os.fsync(destination_fd)
        final_info = os.fstat(destination_fd)
        _require_plain_file_info_v1(final_info, owner=owner, mode=item.mode)
        if final_info.st_size != item.size:
            raise _fail("birth_ownership_recovery_required", "temporary file")
        os.fsync(destination_parent)
        return ReceivedSourceFileV1(
            item.path, item.size, content_hash, item.mode,
        )
    except DistributionAssemblerError:
        raise
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "temporary file") from exc
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        for descriptor in reversed(destination_opened):
            os.close(descriptor)
        for descriptor in reversed(source_opened):
            os.close(descriptor)


def _create_source_directories_v1(
    temporary_root_fd: int, entries: tuple[_SourceEntryV1, ...], *, owner: tuple[int, int],
) -> None:
    for item in entries:
        if not item.directory:
            continue
        parts = tuple(item.path.split("/"))
        parent, opened = _open_descendant_directory_v1(
            temporary_root_fd, parts[:-1],
        )
        try:
            descriptor, _identity_value = _create_private_directory_v1(
                parent, parts[-1], owner=owner,
            )
            os.close(descriptor)
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)


def _write_descriptor_v1(
    temporary_root_fd: int, encoded: bytes, *, owner: tuple[int, int],
) -> None:
    if not encoded or len(encoded) > MAX_RECEIVED_SOURCE_BYTES_V1:
        raise _fail("birth_ownership_deployment_invalid", "descriptor size")
    flags = (
        os.O_RDWR | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(
            RECEIVED_SOURCE_BASENAME_V1, flags, 0o600,
            dir_fd=temporary_root_fd,
        )
        try:
            _write_all_v1(descriptor, encoded)
            os.fsync(descriptor)
            os.fchown(descriptor, *owner)
            os.fchmod(descriptor, 0o644)
            os.fsync(descriptor)
            info = os.fstat(descriptor)
            _require_plain_file_info_v1(info, owner=owner, mode=0o644)
            if info.st_size != len(encoded):
                raise _fail("birth_ownership_recovery_required", "descriptor write")
            os.lseek(descriptor, 0, os.SEEK_SET)
            observed = bytearray()
            while len(observed) <= len(encoded):
                chunk = os.read(descriptor, len(encoded) + 1 - len(observed))
                if not chunk:
                    break
                observed.extend(chunk)
            if bytes(observed) != encoded:
                raise _fail("birth_ownership_recovery_required", "descriptor reread")
        finally:
            os.close(descriptor)
        os.fsync(temporary_root_fd)
    except DistributionAssemblerError:
        raise
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "descriptor write") from exc


def _seal_temporary_directories_v1(
    temporary_root_fd: int, entries: tuple[_SourceEntryV1, ...], *, owner: tuple[int, int],
) -> None:
    directories = [item.path for item in entries if item.directory]
    for path in sorted(
        directories, key=lambda item: (item.count("/"), item.encode("utf-8")),
        reverse=True,
    ):
        descriptor, opened = _open_descendant_directory_v1(
            temporary_root_fd, tuple(path.split("/")),
        )
        try:
            os.fchown(descriptor, *owner)
            os.fchmod(descriptor, 0o755)
            os.fsync(descriptor)
            _require_plain_directory_fd_v1(descriptor, owner=owner, mode=0o755)
        finally:
            for current in reversed(opened):
                os.close(current)
    os.fchown(temporary_root_fd, *owner)
    os.fchmod(temporary_root_fd, 0o755)
    os.fsync(temporary_root_fd)
    _require_plain_directory_fd_v1(temporary_root_fd, owner=owner, mode=0o755)


def _read_bounded_file_at_v1(
    parent_fd: int, name: str, *, maximum: int, owner: tuple[int, int], mode: int,
) -> bytes:
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
        try:
            before = os.fstat(descriptor)
            _require_plain_file_info_v1(before, owner=owner, mode=mode)
            if before.st_size > maximum:
                raise _fail("birth_ownership_recovery_required", "file size")
            payload = bytearray()
            while len(payload) <= maximum:
                chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
            if (
                len(payload) > maximum or len(payload) != before.st_size
                or _identity(before) != _identity(after)
            ):
                raise _fail("birth_ownership_recovery_required", "file changed")
            return bytes(payload)
        finally:
            os.close(descriptor)
    except DistributionAssemblerError:
        raise
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "file read") from exc


def _expected_tree_v1(record: ReceivedSourceV1) -> tuple[dict[str, str], dict[str, ReceivedSourceFileV1]]:
    expected: dict[str, str] = {RECEIVED_SOURCE_BASENAME_V1: "file"}
    files = {item.path: item for item in record.files}
    for item in record.files:
        if item.path in expected:
            raise _fail("birth_ownership_recovery_required", "tree collision")
        expected[item.path] = "file"
        parent = PurePosixPath(item.path).parent
        while parent != PurePosixPath("."):
            name = parent.as_posix()
            previous = expected.setdefault(name, "directory")
            if previous != "directory":
                raise _fail("birth_ownership_recovery_required", "tree collision")
            parent = parent.parent
    return expected, files


def _verify_received_tree_fd_v1(
    root_fd: int, *, expected_record: ReceivedSourceV1 | None,
    owner: tuple[int, int],
) -> ReceivedSourceV1:
    _require_plain_directory_fd_v1(root_fd, owner=owner, mode=0o755)
    encoded = _read_bounded_file_at_v1(
        root_fd, RECEIVED_SOURCE_BASENAME_V1,
        maximum=MAX_RECEIVED_SOURCE_BYTES_V1, owner=owner, mode=0o644,
    )
    try:
        record = decode_received_source_v1(encoded)
    except DistributionAssemblerError as exc:
        raise _fail("birth_ownership_recovery_required", "descriptor invalid") from exc
    if expected_record is not None and record != expected_record:
        raise _fail("birth_ownership_deployment_conflict", "source identity")
    if encode_received_source_v1(record) != encoded:
        raise _fail("birth_ownership_recovery_required", "descriptor encoding")
    expected, files = _expected_tree_v1(record)
    observed: dict[str, str] = {}

    opened: set[int] = set()
    stack: list[
        tuple[
            str, int, tuple[str, ...], tuple[int, ...] | None, str | None,
        ]
    ] = [
        ("enter", root_fd, (), None, None),
    ]
    try:
        while stack:
            phase, directory_fd, prefix, expected_identity, child_name = stack.pop()
            if phase == "open":
                assert expected_identity is not None and child_name is not None
                child = _open_child_directory_v1(directory_fd, child_name)
                opened.add(child)
                child_info = _require_plain_directory_fd_v1(
                    child, owner=owner, mode=0o755,
                )
                if _identity(child_info) != expected_identity:
                    raise _fail("birth_ownership_recovery_required", "tree changed")
                stack.append(("enter", child, prefix, None, None))
                continue
            if phase == "exit":
                assert expected_identity is not None
                if _identity(os.fstat(directory_fd)) != expected_identity:
                    raise _fail("birth_ownership_recovery_required", "tree changed")
                if directory_fd in opened:
                    opened.remove(directory_fd)
                    os.close(directory_fd)
                continue
            before = os.fstat(directory_fd)
            try:
                with os.scandir(directory_fd) as iterator:
                    names = []
                    for item in iterator:
                        names.append(_relative_component_v1(item.name))
                        if len(names) > len(expected):
                            raise _fail(
                                "birth_ownership_recovery_required",
                                "tree inventory",
                            )
                    names.sort(key=lambda item: item.encode("utf-8"))
            except OSError as exc:
                raise _fail(
                    "birth_ownership_recovery_required", "tree inventory",
                ) from exc
            stack.append(("exit", directory_fd, prefix, _identity(before), None))
            children: list[tuple[str, tuple[str, ...], tuple[int, ...]]] = []
            for name in names:
                relative_parts = prefix + (name,)
                if len(relative_parts) > MAX_SOURCE_PATH_DEPTH_V1:
                    raise _fail(
                        "birth_ownership_recovery_required", "tree depth",
                    )
                relative = "/".join(relative_parts)
                flags = (
                    getattr(os, "O_PATH", os.O_RDONLY)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                member: int | None = None
                try:
                    member = os.open(name, flags, dir_fd=directory_fd)
                    info = os.fstat(member)
                except OSError as exc:
                    raise _fail(
                        "birth_ownership_recovery_required", "tree member",
                    ) from exc
                finally:
                    if member is not None:
                        os.close(member)
                if stat.S_ISDIR(info.st_mode):
                    observed[relative] = "directory"
                    if len(observed) > len(expected):
                        raise _fail(
                            "birth_ownership_recovery_required", "tree inventory",
                        )
                    if (
                        (info.st_uid, info.st_gid) != owner
                        or stat.S_IMODE(info.st_mode) != 0o755
                    ):
                        raise _fail(
                            "birth_ownership_recovery_required", "directory metadata",
                        )
                    children.append((name, relative_parts, _identity(info)))
                elif stat.S_ISREG(info.st_mode):
                    observed[relative] = "file"
                    if len(observed) > len(expected):
                        raise _fail(
                            "birth_ownership_recovery_required", "tree inventory",
                        )
                    declared = files.get(relative)
                    mode = (
                        0o644 if relative == RECEIVED_SOURCE_BASENAME_V1
                        else declared.mode if declared is not None else None
                    )
                    if mode is None:
                        raise _fail("birth_ownership_recovery_required", "extra file")
                    _require_plain_file_info_v1(info, owner=owner, mode=mode)
                else:
                    raise _fail("birth_ownership_recovery_required", "tree member")
            for name, child_prefix, child_identity in reversed(children):
                stack.append((
                    "open", directory_fd, child_prefix, child_identity, name,
                ))
    finally:
        for descriptor in tuple(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass
    if observed != expected:
        raise _fail("birth_ownership_recovery_required", "tree inventory")
    for item in record.files:
        parent, opened = _open_descendant_directory_v1(
            root_fd, tuple(item.path.split("/")[:-1]),
        )
        try:
            descriptor = os.open(item.path.split("/")[-1], _READ_FLAGS, dir_fd=parent)
            try:
                before = os.fstat(descriptor)
                _require_plain_file_info_v1(before, owner=owner, mode=item.mode)
                if before.st_size != item.size:
                    raise _fail("birth_ownership_recovery_required", "file size")
                observed_hash = received_source_file_hash_v1(
                    item.path, item.size, _read_chunks_v1(descriptor, item.size),
                )
                if _identity(before) != _identity(os.fstat(descriptor)):
                    raise _fail("birth_ownership_recovery_required", "file changed")
                if observed_hash != item.content_hash:
                    raise _fail("birth_ownership_recovery_required", "file hash")
            finally:
                os.close(descriptor)
        except DistributionAssemblerError:
            raise
        except OSError as exc:
            raise _fail("birth_ownership_recovery_required", "file read") from exc
        finally:
            for current in reversed(opened):
                os.close(current)
    os.fsync(root_fd)
    return record


def _name_status_v1(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "namespace") from exc


def _open_received_tree_at_v1(
    parent_fd: int, name: str, *, owner: tuple[int, int],
    expected_record: ReceivedSourceV1 | None,
) -> tuple[int, ReceivedSourceV1, tuple[int, ...]]:
    descriptor = _open_child_directory_v1(parent_fd, name)
    try:
        record = _verify_received_tree_fd_v1(
            descriptor, expected_record=expected_record, owner=owner,
        )
        info = os.fstat(descriptor)
        rebound = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (rebound.st_dev, rebound.st_ino):
            raise _fail("birth_ownership_recovery_required", "tree binding")
        return descriptor, record, _identity(info)
    except BaseException:
        os.close(descriptor)
        raise


def _require_initial_namespaces_v1(
    incoming_fd: int, sources_fd: int, *, owner: tuple[int, int],
) -> None:
    try:
        with os.scandir(incoming_fd) as iterator:
            incoming_names = tuple(sorted(item.name for item in iterator))
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "incoming inventory") from exc
    if incoming_names != (SOURCES_DIRECTORY_BASENAME_V1,):
        # The deployment lock serializes all legitimate receivers.  Therefore
        # any foreign receive temporary observed here is a crash residue, not
        # an in-flight peer that may be ignored.
        raise _fail("birth_ownership_recovery_required", "incoming inventory")
    try:
        with os.scandir(sources_fd) as iterator:
            names = tuple(sorted(item.name for item in iterator))
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "sources inventory") from exc
    for name in names:
        match = _STRUCTURED_RE.fullmatch(name)
        source_id = match.group(1) if match is not None else name
        if _SOURCE_ID_RE.fullmatch(source_id) is None:
            raise _fail("birth_ownership_recovery_required", "sources inventory")
        info = _name_status_v1(sources_fd, name)
        if (
            info is None or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != owner[0] or info.st_gid != owner[1]
            or stat.S_IMODE(info.st_mode) != 0o755
        ):
            raise _fail("birth_ownership_recovery_required", "sources metadata")


def _require_no_foreign_structured_v1(sources_fd: int, source_id: str) -> None:
    try:
        with os.scandir(sources_fd) as iterator:
            structured = tuple(
                item.name for item in iterator
                if _STRUCTURED_RE.fullmatch(item.name) is not None
            )
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "sources inventory") from exc
    if any(name != f".{source_id}.tmp" for name in structured):
        raise _fail("birth_ownership_recovery_required", "foreign temporary")


def _remove_owned_tree_at_v1(
    parent_fd: int, name: str, *, expected_identity: tuple[int, ...],
    owner: tuple[int, int],
) -> None:
    root_fd = _open_child_directory_v1(parent_fd, name)
    opened = {root_fd}
    try:
        root_info = os.fstat(root_fd)
        if (
            (root_info.st_dev, root_info.st_ino) != expected_identity[:2]
            or (root_info.st_uid, root_info.st_gid) != owner
        ):
            raise _fail("birth_ownership_recovery_required", "temporary cleanup")

        stack: list[
            tuple[
                str, int, int | None, str | None, tuple[int, int] | None, int,
            ]
        ] = [("enter", root_fd, parent_fd, name, expected_identity[:2], 0)]
        while stack:
            (
                phase, directory_fd, containing_fd, child_name, identity, depth,
            ) = stack.pop()
            if phase == "exit":
                assert (
                    containing_fd is not None and child_name is not None
                    and identity is not None
                )
                os.fsync(directory_fd)
                current = os.fstat(directory_fd)
                rebound = os.stat(
                    child_name, dir_fd=containing_fd, follow_symlinks=False,
                )
                if (
                    (current.st_dev, current.st_ino) != identity
                    or (current.st_uid, current.st_gid) != owner
                    or (rebound.st_dev, rebound.st_ino) != identity
                ):
                    raise _fail(
                        "birth_ownership_recovery_required", "temporary cleanup",
                    )
                os.rmdir(child_name, dir_fd=containing_fd)
                opened.remove(directory_fd)
                os.close(directory_fd)
                continue
            if phase == "child":
                assert child_name is not None
                if depth + 1 > MAX_SOURCE_PATH_DEPTH_V1:
                    raise _fail(
                        "birth_ownership_recovery_required", "temporary cleanup",
                    )
                flags = (
                    getattr(os, "O_PATH", os.O_RDONLY)
                    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
                )
                child_handle: int | None = None
                try:
                    child_handle = os.open(child_name, flags, dir_fd=directory_fd)
                    child_info = os.fstat(child_handle)
                except OSError as exc:
                    raise _fail("birth_ownership_recovery_required", "temporary cleanup") from exc
                finally:
                    if child_handle is not None:
                        os.close(child_handle)
                if (child_info.st_uid, child_info.st_gid) != owner:
                    raise _fail("birth_ownership_recovery_required", "temporary cleanup")
                if stat.S_ISDIR(child_info.st_mode):
                    child_fd = _open_child_directory_v1(directory_fd, child_name)
                    opened.add(child_fd)
                    opened_info = os.fstat(child_fd)
                    child_identity = (child_info.st_dev, child_info.st_ino)
                    if (
                        (opened_info.st_dev, opened_info.st_ino) != child_identity
                        or (opened_info.st_uid, opened_info.st_gid) != owner
                    ):
                        raise _fail("birth_ownership_recovery_required", "temporary cleanup")
                    stack.append((
                        "enter", child_fd, directory_fd, child_name, child_identity,
                        depth + 1,
                    ))
                elif stat.S_ISREG(child_info.st_mode) and child_info.st_nlink == 1:
                    rebound = os.stat(
                        child_name, dir_fd=directory_fd, follow_symlinks=False,
                    )
                    if (rebound.st_dev, rebound.st_ino) != (
                        child_info.st_dev, child_info.st_ino,
                    ):
                        raise _fail("birth_ownership_recovery_required", "temporary cleanup")
                    os.unlink(child_name, dir_fd=directory_fd)
                else:
                    raise _fail("birth_ownership_recovery_required", "temporary cleanup")
                continue

            try:
                with os.scandir(directory_fd) as iterator:
                    observed_names = []
                    for item in iterator:
                        observed_names.append(item.name)
                        if len(observed_names) > MAX_SOURCE_TREE_ENTRIES_V1:
                            raise _fail(
                                "birth_ownership_recovery_required",
                                "temporary cleanup",
                            )
                    names = tuple(sorted(observed_names))
            except OSError as exc:
                raise _fail(
                    "birth_ownership_recovery_required", "temporary cleanup",
                ) from exc
            stack.append((
                "exit", directory_fd, containing_fd, child_name, identity, depth,
            ))
            for member_name in reversed(names):
                stack.append((
                    "child", directory_fd, None, member_name, None, depth,
                ))

        os.fsync(parent_fd)
    except DistributionAssemblerError:
        raise
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "temporary cleanup") from exc
    finally:
        for descriptor in tuple(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _rename_no_replace_v1(
    source_parent_fd: int, source_name: str,
    target_parent_fd: int, target_name: str,
    *, expected_fd: int, sync_source_parent: bool,
) -> None:
    try:
        before = os.fstat(expected_fd)
        source_info = os.stat(
            source_name, dir_fd=source_parent_fd, follow_symlinks=False,
        )
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "temporary binding") from exc
    if (before.st_dev, before.st_ino) != (source_info.st_dev, source_info.st_ino):
        raise _fail("birth_ownership_recovery_required", "temporary binding")
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise _fail("birth_ownership_recovery_required", "renameat2 unavailable")
    renameat2.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_parent_fd, os.fsencode(source_name),
        target_parent_fd, os.fsencode(target_name), 1,
    )
    if result != 0:
        number = ctypes.get_errno()
        if number == errno.EEXIST:
            raise FileExistsError(number, os.strerror(number), target_name)
        raise _fail("birth_ownership_recovery_required", "rename no-replace") from OSError(
            number, os.strerror(number), target_name,
        )
    try:
        target_info = os.stat(
            target_name, dir_fd=target_parent_fd, follow_symlinks=False,
        )
        after = os.fstat(expected_fd)
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "published binding") from exc
    if (
        _stable_identity(_identity(before)) != _stable_identity(_identity(after))
        or (after.st_dev, after.st_ino) != (target_info.st_dev, target_info.st_ino)
    ):
        raise _fail("birth_ownership_recovery_required", "published binding")
    try:
        os.fsync(target_parent_fd)
        if sync_source_parent and source_parent_fd != target_parent_fd:
            os.fsync(source_parent_fd)
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "rename sync") from exc


def _receive_source_locked_core_v1(
    source_path: str, service_user: str, session: object, *,
    test_ownership_root: Path | None = None,
) -> str:
    _require_linux_v1()
    from executor_birth_ownership_coordinator import (
        _DeploymentLockSessionForTestV1, _DeploymentLockSessionV1,
        _require_deployment_lock_session_v1,
        _require_test_deployment_lock_session_v1,
    )

    if type(session) is _DeploymentLockSessionV1:
        if test_ownership_root is not None:
            raise _fail("birth_ownership_deployment_unsafe", "productive root")
        ownership_root = DEFAULT_OWNERSHIP_ROOT_V1
        owner = (0, 0)

        def require_session() -> None:
            _require_deployment_lock_session_v1(session)
    elif type(session) is _DeploymentLockSessionForTestV1:
        if not isinstance(test_ownership_root, Path):
            raise _fail("birth_ownership_deployment_unsafe", "test root")
        ownership_root = test_ownership_root
        owner = (os.geteuid(), os.getegid())

        def require_session() -> None:
            _require_test_deployment_lock_session_v1(session, ownership_root)
    else:
        # The productive validator performs the exact-type refusal before any
        # filesystem observation and keeps one stable lock error contract.
        _require_deployment_lock_session_v1(session)
        raise AssertionError("unreachable")

    require_session()
    account_before = _service_account_snapshot_v1(service_user)
    source_descriptors, source_parts = _open_absolute_directory_v1(source_path)
    source_fd = source_descriptors[-1]
    source_root_identity = _identity(os.fstat(source_fd))
    ownership_descriptors: list[int] = []
    ownership_fd: int | None = None
    incoming_fd: int | None = None
    sources_fd: int | None = None
    temporary_fd: int | None = None
    receive_name: str | None = None
    receive_identity: tuple[int, ...] | None = None
    receive_owned = False
    try:
        try:
            ownership_descriptors, ownership_parts = _open_absolute_directory_v1(
                _source_path_grammar_v1(str(ownership_root)),
            )
            ownership_fd = ownership_descriptors[-1]
        except DistributionAssemblerError as exc:
            raise _fail("birth_ownership_deployment_unsafe", "ownership root") from exc
        _require_plain_directory_fd_v1(ownership_fd, owner=owner, mode=0o755)
        _require_absolute_chain_bound_v1(
            source_descriptors, source_parts, detail="source changed",
        )
        _require_absolute_chain_bound_v1(
            ownership_descriptors, ownership_parts, detail="ownership root",
        )
        _require_disjoint_directory_chains_v1(
            source_descriptors, ownership_descriptors,
        )
        first_inventory = _scan_source_v1(source_fd)
        require_session()
        incoming_fd = _ensure_child_directory_v1(
            ownership_fd, INCOMING_DIRECTORY_BASENAME_V1,
            owner=owner, mode=0o755,
        )
        sources_fd = _ensure_child_directory_v1(
            incoming_fd, SOURCES_DIRECTORY_BASENAME_V1,
            owner=owner, mode=0o755,
        )
        _require_initial_namespaces_v1(incoming_fd, sources_fd, owner=owner)

        for _attempt in range(16):
            receive_name = ".receive-" + os.urandom(16).hex() + ".tmp"
            try:
                temporary_fd, receive_identity = _create_private_directory_v1(
                    incoming_fd, receive_name, owner=owner,
                )
                receive_owned = True
                break
            except FileExistsError:
                continue
        else:
            raise _fail("birth_ownership_recovery_required", "temporary collision")
        assert temporary_fd is not None and receive_identity is not None

        source_map = _entry_map(first_inventory)
        _create_source_directories_v1(temporary_fd, first_inventory, owner=owner)
        received_files = tuple(
            _copy_source_file_v1(
                source_fd, temporary_fd, item, source_map, owner=owner,
            )
            for item in first_inventory if not item.directory
        )
        record = build_received_source_v1(service_user, received_files)
        _require_no_foreign_structured_v1(sources_fd, record.source_id)
        encoded = encode_received_source_v1(record)
        _write_descriptor_v1(temporary_fd, encoded, owner=owner)
        _seal_temporary_directories_v1(
            temporary_fd, first_inventory, owner=owner,
        )
        receive_identity = _identity(os.fstat(temporary_fd))
        if _verify_received_tree_fd_v1(
            temporary_fd, expected_record=record, owner=owner,
        ) != record:
            raise _fail("birth_ownership_recovery_required", "temporary reread")

        second_inventory = _scan_source_v1(source_fd)
        _require_absolute_chain_bound_v1(
            source_descriptors, source_parts, detail="source changed",
        )
        if (
            _identity(os.fstat(source_fd)) != source_root_identity
            or first_inventory != second_inventory
        ):
            raise _fail("birth_ownership_deployment_unsafe", "source changed")
        account_after = _service_account_snapshot_v1(service_user)
        if account_before != account_after:
            raise _fail("birth_ownership_deployment_unsafe", "service account changed")
        require_session()

        structured_name = f".{record.source_id}.tmp"
        final_name = record.source_id
        final_status = _name_status_v1(sources_fd, final_name)
        structured_status = _name_status_v1(sources_fd, structured_name)
        if final_status is not None:
            if structured_status is not None:
                raise _fail("birth_ownership_recovery_required", "two source trees")
            final_fd, final_record, _final_identity = _open_received_tree_at_v1(
                sources_fd, final_name, owner=owner, expected_record=record,
            )
            try:
                os.fsync(final_fd)
                os.fsync(sources_fd)
            finally:
                os.close(final_fd)
            _remove_owned_tree_at_v1(
                incoming_fd, receive_name,
                expected_identity=receive_identity, owner=owner,
            )
            receive_owned = False
            require_session()
            return final_record.source_id

        if structured_status is not None:
            structured_fd, structured_record, structured_identity = (
                _open_received_tree_at_v1(
                    sources_fd, structured_name, owner=owner,
                    expected_record=record,
                )
            )
            _remove_owned_tree_at_v1(
                incoming_fd, receive_name,
                expected_identity=receive_identity, owner=owner,
            )
            receive_owned = False
            os.close(temporary_fd)
            temporary_fd = structured_fd
            receive_identity = structured_identity
            record = structured_record
        else:
            try:
                _rename_no_replace_v1(
                    incoming_fd, receive_name, sources_fd, structured_name,
                    expected_fd=temporary_fd, sync_source_parent=True,
                )
            except FileExistsError as exc:
                raise _fail("birth_ownership_recovery_required", "structured conflict") from exc
            receive_owned = False
            receive_identity = _identity(os.fstat(temporary_fd))

        try:
            _rename_no_replace_v1(
                sources_fd, structured_name, sources_fd, final_name,
                expected_fd=temporary_fd, sync_source_parent=False,
            )
        except FileExistsError as exc:
            raise _fail("birth_ownership_deployment_conflict", "source exists") from exc
        require_session()
        final_fd, final_record, _final_identity = _open_received_tree_at_v1(
            sources_fd, final_name, owner=owner, expected_record=record,
        )
        try:
            os.fsync(final_fd)
            os.fsync(sources_fd)
        finally:
            os.close(final_fd)
        require_session()
        return final_record.source_id
    except BaseException as failure:
        if (
            receive_owned and receive_name is not None
            and receive_identity is not None and incoming_fd is not None
        ):
            # A failure after rename but before either parent fsync leaves the
            # object under its structured name.  Do not mistake that durable
            # recovery object for a still-private receive tree.
            if _name_status_v1(incoming_fd, receive_name) is not None:
                _remove_owned_tree_at_v1(
                    incoming_fd, receive_name,
                    expected_identity=receive_identity, owner=owner,
                )
            receive_owned = False
        if isinstance(failure, OSError):
            raise _fail(
                "birth_ownership_recovery_required", "receiver io",
            ) from failure
        raise
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if sources_fd is not None:
            os.close(sources_fd)
        if incoming_fd is not None:
            os.close(incoming_fd)
        for descriptor in reversed(ownership_descriptors):
            os.close(descriptor)
        for descriptor in reversed(source_descriptors):
            os.close(descriptor)


def _receive_source_with_product_session_v1(
    source_path: str, service_user: str, session: object,
) -> str:
    return _receive_source_locked_core_v1(source_path, service_user, session)


def _receive_source_with_test_session_v1(
    source_path: str, service_user: str, ownership_root: Path, session: object,
) -> str:
    root = Path(ownership_root)
    return _receive_source_locked_core_v1(
        source_path, service_user, session, test_ownership_root=root,
    )


def _load_received_source_locked_core_v1(
    source_id: object, session: object, *,
    test_ownership_root: Path | None = None,
) -> ReceivedSourceV1:
    """Reread one exact received source while the deployment lock is held."""
    _require_linux_v1()
    from executor_birth_ownership_coordinator import (
        _DeploymentLockSessionForTestV1, _DeploymentLockSessionV1,
        _require_deployment_lock_session_v1,
        _require_test_deployment_lock_session_v1,
    )

    if type(source_id) is not str or _SOURCE_ID_RE.fullmatch(source_id) is None:
        raise _fail("birth_ownership_deployment_invalid", "source id")
    if type(session) is _DeploymentLockSessionV1:
        if test_ownership_root is not None:
            raise _fail("birth_ownership_deployment_unsafe", "productive root")
        ownership_root = DEFAULT_OWNERSHIP_ROOT_V1
        owner = (0, 0)

        def require_session() -> None:
            _require_deployment_lock_session_v1(session)
    elif type(session) is _DeploymentLockSessionForTestV1:
        if not isinstance(test_ownership_root, Path):
            raise _fail("birth_ownership_deployment_unsafe", "test root")
        ownership_root = test_ownership_root
        owner = (os.geteuid(), os.getegid())

        def require_session() -> None:
            _require_test_deployment_lock_session_v1(session, ownership_root)
    else:
        _require_deployment_lock_session_v1(session)
        raise AssertionError("unreachable")

    require_session()
    ownership_descriptors: list[int] = []
    incoming_fd: int | None = None
    sources_fd: int | None = None
    source_fd: int | None = None
    try:
        try:
            ownership_descriptors, ownership_parts = _open_absolute_directory_v1(
                _source_path_grammar_v1(str(ownership_root)),
            )
        except DistributionAssemblerError as exc:
            raise _fail(
                "birth_ownership_recovery_required", "ownership root",
            ) from exc
        ownership_fd = ownership_descriptors[-1]
        _require_plain_directory_fd_v1(ownership_fd, owner=owner, mode=0o755)
        _require_absolute_chain_bound_v1(
            ownership_descriptors, ownership_parts, detail="ownership root",
        )
        incoming_fd = _open_child_directory_v1(
            ownership_fd, INCOMING_DIRECTORY_BASENAME_V1,
        )
        _require_plain_directory_fd_v1(incoming_fd, owner=owner, mode=0o755)
        sources_fd = _open_child_directory_v1(
            incoming_fd, SOURCES_DIRECTORY_BASENAME_V1,
        )
        _require_plain_directory_fd_v1(sources_fd, owner=owner, mode=0o755)
        _require_initial_namespaces_v1(incoming_fd, sources_fd, owner=owner)
        source_fd, record, _identity_value = _open_received_tree_at_v1(
            sources_fd, source_id, owner=owner, expected_record=None,
        )
        if record.source_id != source_id:
            raise _fail("birth_ownership_recovery_required", "source binding")
        require_session()
        _require_absolute_chain_bound_v1(
            ownership_descriptors, ownership_parts, detail="ownership root",
        )
        return record
    except DistributionAssemblerError:
        raise
    except OSError as exc:
        raise _fail(
            "birth_ownership_recovery_required", "received source",
        ) from exc
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if sources_fd is not None:
            os.close(sources_fd)
        if incoming_fd is not None:
            os.close(incoming_fd)
        for descriptor in reversed(ownership_descriptors):
            os.close(descriptor)


def _load_received_source_with_product_session_v1(
    source_id: object, session: object,
) -> ReceivedSourceV1:
    return _load_received_source_locked_core_v1(source_id, session)


def _load_received_source_with_test_session_v1(
    source_id: object, ownership_root: Path, session: object,
) -> ReceivedSourceV1:
    root = Path(ownership_root)
    return _load_received_source_locked_core_v1(
        source_id, session, test_ownership_root=root,
    )


def _receive_source_v1(source: object, service_user: object) -> str:
    """Receive one source into the fixed productive content-addressed root."""
    _require_linux_v1()
    _require_root_v1()
    source_path = _source_path_grammar_v1(source)
    user = _service_user_grammar_v1(service_user)
    _require_lexically_disjoint_v1(source_path, DEFAULT_OWNERSHIP_ROOT_V1)
    from executor_birth_ownership_coordinator import (
        OwnershipCoordinatorError, _deployment_lock_v1,
    )

    try:
        with _deployment_lock_v1() as session:
            return _receive_source_with_product_session_v1(
                source_path, user, session,
            )
    except OwnershipCoordinatorError as exc:
        code = (
            "birth_ownership_platform_unsupported"
            if exc.code == "birth_ownership_platform_unsupported"
            else "birth_ownership_recovery_required"
        )
        raise _fail(code, "deployment lock") from exc


def _receive_source_for_test_v1(
    source: object, service_user: object, ownership_root: Path,
) -> str:
    """Linux seam with a nominally separate G6-A lock and isolated root."""
    _require_linux_v1()
    source_path = _source_path_grammar_v1(source)
    user = _service_user_grammar_v1(service_user)
    root = Path(ownership_root)
    _require_lexically_disjoint_v1(source_path, root)
    from executor_birth_ownership_coordinator import _deployment_lock_for_test_v1

    with _deployment_lock_for_test_v1(root) as session:
        return _receive_source_with_test_session_v1(
            source_path, user, root, session,
        )


def _parse_cli_v1(argv: object) -> tuple[str, str]:
    if type(argv) is not list or len(argv) != 5:
        raise _fail("birth_ownership_deployment_invalid", "arguments")
    if argv[0] != "receive" or argv[1] != "--source" or argv[3] != "--service-user":
        raise _fail("birth_ownership_deployment_invalid", "arguments")
    return _source_path_grammar_v1(argv[2]), _service_user_grammar_v1(argv[4])


def main(argv: list[str] | None = None) -> int:
    try:
        _require_linux_v1()
        _require_root_v1()
        source, user = _parse_cli_v1(list(sys.argv[1:] if argv is None else argv))
        source_id = _receive_source_v1(source, user)
    except DistributionAssemblerError as exc:
        print(exc.code, file=sys.stderr)
        return 78
    print(source_id)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by installed entry tests
    raise SystemExit(main())


__all__ = ["main"]
