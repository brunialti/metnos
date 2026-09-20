"""Offline POSIX installer for content-addressed Executor Birth Python venvs."""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys

import executor_birth_python_environment as environment_model
from executor_birth_posix_metadata import snapshot_stat_v1
from install import executor_birth_posix_directory as posix_directory


PRODUCT_ENVIRONMENT_STORE_V1 = Path(environment_model.PYTHON_ENVIRONMENT_ROOT_V1)
PRODUCT_BASE_PYTHON_V1 = Path("/usr/bin/python3.12")
_PACKAGE_RE_V1 = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_VERSION_RE_V1 = re.compile(r"[0-9][0-9A-Za-z.!+_-]{0,127}\Z")
_HASH_RE_V1 = re.compile(r"--hash=sha256:[0-9a-f]{64}\Z")
_RENAME_NOREPLACE_V1 = 1
_AT_FDCWD_V1 = -100
_TIMEOUT_SECONDS_V1 = 900
_PROCESS_ENVIRONMENT_V1 = {
    "HOME": "/", "LANG": "C", "LC_ALL": "C",
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "PIP_CONFIG_FILE": "/dev/null",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_CACHE_DIR": "1",
    "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
}


class PythonEnvironmentPosixError(RuntimeError):
    """Stable product error for environment installation failures."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(code)


def _fail(code: str, detail: str = "", cause: BaseException | None = None):
    error = PythonEnvironmentPosixError(code, detail)
    if cause is None:
        raise error
    raise error from cause


@dataclass(frozen=True, slots=True)
class InstalledPythonEnvironmentV1:
    record: environment_model.PythonEnvironmentV1
    environment_root: Path
    python_executable: Path
    repeated: bool

    def __post_init__(self) -> None:
        if (
            type(self.record) is not environment_model.PythonEnvironmentV1
            or not isinstance(self.environment_root, Path)
            or not isinstance(self.python_executable, Path)
            or self.python_executable != self.environment_root / "bin/python"
            or type(self.repeated) is not bool
        ):
            raise ValueError("installed Python environment")


def _require_platform_v1() -> None:
    try:
        posix_directory.require_posix_directory_platform_v1()
    except posix_directory.PosixDirectoryError as exc:
        _fail("birth_python_environment_platform_unsupported", cause=exc)
    if not hasattr(os, "geteuid") or not callable(getattr(fcntl, "flock", None)):
        _fail("birth_python_environment_platform_unsupported")


def _lock_requirements_v1(encoded: object) -> tuple[str, ...]:
    if (
        type(encoded) is not bytes or not encoded or b"\0" in encoded
        or b"\r" in encoded or not encoded.endswith(b"\n")
        or len(encoded) > environment_model.MAX_PYTHON_DEPENDENCY_LOCK_BYTES_V1
    ):
        _fail("birth_python_environment_lock_invalid")
    try:
        lines = encoded.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        _fail("birth_python_environment_lock_invalid", cause=exc)
    if not lines:
        _fail("birth_python_environment_lock_invalid")
    names: list[str] = []
    for line in lines:
        fields = line.split(" ")
        requirement = fields[0].partition("==")
        if (
            len(fields) < 2 or "" in fields or requirement[1] != "=="
            or _PACKAGE_RE_V1.fullmatch(requirement[0]) is None
            or _VERSION_RE_V1.fullmatch(requirement[2]) is None
            or any(_HASH_RE_V1.fullmatch(item) is None for item in fields[1:])
            or fields[1:] != sorted(set(fields[1:]))
        ):
            _fail("birth_python_environment_lock_invalid")
        names.append(requirement[0])
    if names != sorted(set(names)) or any(name in {"pip", "setuptools", "wheel"} for name in names):
        _fail("birth_python_environment_lock_invalid")
    return tuple(names)


def _canonical_path_v1(value: object, detail: str) -> Path:
    try:
        path = Path(os.fspath(value))
    except TypeError as exc:
        _fail("birth_python_environment_request_invalid", detail, exc)
    if not path.is_absolute() or path == Path("/") or Path(os.path.abspath(path)) != path:
        _fail("birth_python_environment_request_invalid", detail)
    return path


def _bound_directory_v1(path: Path, owner: tuple[int, int]):
    try:
        bound = posix_directory.BoundDirectoryChainV1(path)
        before = snapshot_stat_v1(os.fstat(bound.root_fd))
        if (
            not stat.S_ISDIR(before.mode) or (before.uid, before.gid) != owner
            or stat.S_IMODE(before.mode) != 0o755
        ):
            _fail("birth_python_environment_store_invalid")
        posix_directory.require_no_acl_v1(bound.root_fd)
        after = snapshot_stat_v1(os.fstat(bound.root_fd))
        if before != after:
            _fail("birth_python_environment_store_invalid")
        bound.assert_bound()
        return bound
    except PythonEnvironmentPosixError:
        if "bound" in locals():
            bound.close()
        raise
    except (OSError, posix_directory.PosixDirectoryError) as exc:
        if "bound" in locals():
            bound.close()
        _fail("birth_python_environment_store_invalid", cause=exc)


def _run_v1(arguments: list[str], *, cwd: Path) -> None:
    try:
        result = subprocess.run(
            arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False, close_fds=True,
            cwd=cwd, env=_PROCESS_ENVIRONMENT_V1, timeout=_TIMEOUT_SECONDS_V1,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _fail("birth_python_environment_install_failed", cause=exc)
    if result.returncode != 0:
        _fail("birth_python_environment_install_failed")


def _remove_bootstrap_v1(staging: Path) -> None:
    bin_root = staging / "bin"
    for child in tuple(bin_root.iterdir()):
        if child.name != "python":
            child.unlink()
    site = staging / "lib/python3.12/site-packages"
    for child in tuple(site.iterdir()):
        if child.name == "pip" or child.name.startswith("pip-"):
            shutil.rmtree(child)
    for child in (staging / "include", staging / "lib64", staging / "share"):
        if child.is_symlink():
            child.unlink()
        elif child.exists():
            shutil.rmtree(child)
    for child in (staging / ".gitignore", staging / "requirements.lock"):
        if child.exists():
            child.unlink()


def _build_staging_v1(
    staging: Path, base_python: Path, wheelhouse: Path, lock_bytes: bytes,
) -> None:
    _run_v1([
        base_python.as_posix(), "-I", "-B", "-m", "venv", "--copies",
        staging.as_posix(),
    ], cwd=staging.parent)
    lock_path = staging / "requirements.lock"
    lock_path.write_bytes(lock_bytes)
    python = staging / "bin/python"
    _run_v1([
        python.as_posix(), "-I", "-B", "-m", "pip", "install", "--no-index",
        "--find-links", wheelhouse.as_posix(), "--require-hashes", "--no-deps",
        "--only-binary=:all:", "--no-compile", "--disable-pip-version-check",
        "--no-input", "--requirement", lock_path.as_posix(),
    ], cwd=staging)
    _run_v1([
        python.as_posix(), "-I", "-B", "-m", "pip", "uninstall", "--yes", "pip",
    ], cwd=staging)
    _remove_bootstrap_v1(staging)


def _seal_tree_v1(root: Path, owner: tuple[int, int]) -> None:
    for current, directories, files in os.walk(root, topdown=False, followlinks=False):
        for name in (*files, *directories):
            path = Path(current) / name
            if path.is_symlink():
                _fail("birth_python_environment_tree_invalid", "symlink")
            flags = (
                posix_directory.DIRECTORY_FLAGS_V1 if path.is_dir()
                else os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            try:
                descriptor = os.open(path, flags)
                executable = bool(os.fstat(descriptor).st_mode & 0o111)
                os.fchown(descriptor, *owner)
                posix_directory.remove_acl_v1(descriptor)
                mode = 0o755 if path.is_dir() or path == root / "bin/python" or executable else 0o644
                os.fchmod(descriptor, mode)
                os.fsync(descriptor)
            except (OSError, posix_directory.PosixDirectoryError) as exc:
                _fail("birth_python_environment_tree_invalid", cause=exc)
            finally:
                if "descriptor" in locals():
                    os.close(descriptor)
                    del descriptor
    descriptor = -1
    try:
        descriptor = os.open(root, posix_directory.DIRECTORY_FLAGS_V1)
        os.fchown(descriptor, *owner)
        posix_directory.remove_acl_v1(descriptor)
        os.fchmod(descriptor, 0o755)
        os.fsync(descriptor)
    except (OSError, posix_directory.PosixDirectoryError) as exc:
        _fail("birth_python_environment_tree_invalid", cause=exc)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _content_chunks_v1(descriptor: int, maximum: int):
    observed = 0
    while observed <= maximum:
        chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - observed))
        if not chunk:
            return
        observed += len(chunk)
        yield chunk


def _file_record_v1(root: Path, path: Path, owner: tuple[int, int]):
    relative = path.relative_to(root).as_posix()
    descriptor = -1
    try:
        before = snapshot_stat_v1(path.lstat())
        descriptor = os.open(path, posix_directory.FILE_FLAGS_V1)
        opened = snapshot_stat_v1(os.fstat(descriptor))
        if (
            not stat.S_ISREG(opened.mode) or opened.link_count != 1
            or (opened.uid, opened.gid) != owner
            or stat.S_IMODE(opened.mode) not in {0o644, 0o755}
            or before != opened
        ):
            _fail("birth_python_environment_tree_invalid", relative)
        posix_directory.require_no_acl_v1(descriptor)
        digest = environment_model.python_environment_file_hash_v1(
            relative, opened.size, _content_chunks_v1(descriptor, opened.size),
        )
        after = snapshot_stat_v1(os.fstat(descriptor))
        rebound = snapshot_stat_v1(path.lstat())
        posix_directory.require_no_acl_v1(descriptor)
        if opened != after or after != rebound:
            _fail("birth_python_environment_tree_invalid", relative)
        return environment_model.PythonEnvironmentFileV1(
            relative, opened.size, stat.S_IMODE(opened.mode), digest,
        )
    except (OSError, posix_directory.PosixDirectoryError, environment_model.PythonEnvironmentError) as exc:
        if isinstance(exc, PythonEnvironmentPosixError):
            raise
        _fail("birth_python_environment_tree_invalid", relative, exc)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_directory_v1(path: Path, owner: tuple[int, int]) -> None:
    descriptor = -1
    try:
        before = snapshot_stat_v1(path.lstat())
        descriptor = os.open(path, posix_directory.DIRECTORY_FLAGS_V1)
        opened = snapshot_stat_v1(os.fstat(descriptor))
        if (
            not stat.S_ISDIR(opened.mode) or (opened.uid, opened.gid) != owner
            or stat.S_IMODE(opened.mode) != 0o755 or before != opened
        ):
            _fail("birth_python_environment_tree_invalid", path.as_posix())
        posix_directory.require_no_acl_v1(descriptor)
        after = snapshot_stat_v1(os.fstat(descriptor))
        rebound = snapshot_stat_v1(path.lstat())
        if opened != after or after != rebound:
            _fail("birth_python_environment_tree_invalid", path.as_posix())
    except (OSError, posix_directory.PosixDirectoryError) as exc:
        if isinstance(exc, PythonEnvironmentPosixError):
            raise
        _fail("birth_python_environment_tree_invalid", path.as_posix(), exc)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _inventory_v1(
    root: Path, owner: tuple[int, int], profile: str,
    lock_hash: str,
) -> environment_model.PythonEnvironmentV1:
    files, directories = [], {"."}
    bound = _bound_directory_v1(root, owner)
    try:
        before = snapshot_stat_v1(root.lstat())
        for current, names, filenames in os.walk(root, followlinks=False):
            names.sort(key=lambda item: item.encode("utf-8"))
            filenames.sort(key=lambda item: item.encode("utf-8"))
            current_path = Path(current)
            _require_directory_v1(current_path, owner)
            for name in names:
                _require_directory_v1(current_path / name, owner)
            directories.update(
                (current_path / name).relative_to(root).as_posix() for name in names
            )
            files.extend(_file_record_v1(root, current_path / name, owner) for name in filenames)
        record = environment_model.build_python_environment_v1(
            profile=profile, dependency_lock_hash=lock_hash,
            files=tuple(sorted(files, key=lambda item: item.path.encode("utf-8"))),
        )
        expected = {"."}
        for item in record.files:
            parent = Path(item.path).parent
            while parent != Path("."):
                expected.add(parent.as_posix())
                parent = parent.parent
        after = snapshot_stat_v1(root.lstat())
    except (OSError, environment_model.PythonEnvironmentError) as exc:
        _fail("birth_python_environment_tree_invalid", cause=exc)
    finally:
        bound.assert_bound()
        bound.close()
    if directories != expected or before != after:
        _fail("birth_python_environment_tree_invalid", "directory coverage")
    return record


def _remove_staging_v1(path: Path, owner: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or (info.st_uid, info.st_gid) != owner:
        _fail("birth_python_environment_recovery_required")
    try:
        shutil.rmtree(path)
    except OSError as exc:
        _fail("birth_python_environment_recovery_required", cause=exc)


def _verify_runtime_v1(root: Path, profile: str) -> None:
    expected = environment_model.python_environment_profile_v1(profile)
    code = (
        "import sys,sysconfig;"
        f"assert sys.prefix=={root.as_posix()!r};"
        "assert sys.prefix!=sys.base_prefix;"
        "assert sys.flags.isolated==1 and sys.flags.dont_write_bytecode==1;"
        "assert sys.flags.no_site==0 and 'site' in sys.modules;"
        f"assert sys.implementation.cache_tag=={expected.cache_tag!r};"
        f"assert sysconfig.get_config_var('SOABI')=={expected.soabi!r};"
        f"assert all('site-packages' not in p or p.startswith({root.as_posix()!r}) for p in sys.path)"
    )
    _run_v1([
        (root / "bin/python").as_posix(), "-I", "-B", "-c", code,
    ], cwd=root)


def _publish_no_replace_v1(staging: Path, final: Path) -> None:
    renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if renameat2 is None:
        _fail("birth_python_environment_platform_unsupported")
    result = renameat2(
        _AT_FDCWD_V1, os.fsencode(staging), _AT_FDCWD_V1, os.fsencode(final),
        _RENAME_NOREPLACE_V1,
    )
    if result != 0:
        error = ctypes.get_errno()
        code = (
            "birth_python_environment_publication_conflict"
            if error == errno.EEXIST else "birth_python_environment_io_unavailable"
        )
        _fail(code, cause=OSError(error, os.strerror(error)))


def _ensure_core_v1(
    lock_bytes: bytes, wheelhouse: Path, profile: str, store: Path,
    base_python: Path, owner: tuple[int, int],
) -> InstalledPythonEnvironmentV1:
    _require_platform_v1()
    _lock_requirements_v1(lock_bytes)
    lock_hash = environment_model.python_dependency_lock_hash_v1(profile, lock_bytes)
    store_bound = _bound_directory_v1(store, owner)
    wheelhouse_bound = None
    lock_fd = posix_directory.open_lock_file_v1(
        store_bound.root_fd, f"python-environment-{lock_hash[7:]}.lock", owner,
    )
    digest = lock_hash.removeprefix("sha256:")
    staging, final = store / f".{digest}.staging-v1", store / digest
    locked, owner_pid = False, os.getpid()
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        locked = True
        store_bound.assert_bound()
        _remove_staging_v1(staging, owner)
        if final.exists():
            observed = _inventory_v1(final, owner, profile, lock_hash)
            _verify_runtime_v1(final, profile)
            return InstalledPythonEnvironmentV1(
                observed, final, final / "bin/python", True,
            )
        wheelhouse_bound = _bound_directory_v1(wheelhouse, owner)
        wheelhouse_bound.assert_bound()
        _build_staging_v1(staging, base_python, wheelhouse, lock_bytes)
        _seal_tree_v1(staging, owner)
        candidate = _inventory_v1(staging, owner, profile, lock_hash)
        _verify_runtime_v1(staging, profile)
        _publish_no_replace_v1(staging, final)
        os.fsync(store_bound.root_fd)
        observed, repeated = _inventory_v1(final, owner, profile, lock_hash), False
        if observed != candidate:
            _fail("birth_python_environment_recovery_required")
        _verify_runtime_v1(final, profile)
        return InstalledPythonEnvironmentV1(observed, final, final / "bin/python", repeated)
    finally:
        actions = []
        if locked and os.getpid() == owner_pid:
            actions.append(lambda: fcntl.flock(lock_fd, fcntl.LOCK_UN))
        actions.append(lambda: os.close(lock_fd))
        if wheelhouse_bound is not None:
            actions.append(wheelhouse_bound.close)
        actions.append(store_bound.close)
        try:
            posix_directory.run_cleanup_v1(actions, detail="Python environment cleanup")
        except posix_directory.PosixDirectoryError as exc:
            _fail("birth_python_environment_io_unavailable", cause=exc)


def ensure_python_environment_v1(
    lock_bytes: object, wheelhouse: object, profile: object,
) -> InstalledPythonEnvironmentV1:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        _fail("birth_python_environment_administrative_required")
    if type(lock_bytes) is not bytes or type(profile) is not str:
        _fail("birth_python_environment_request_invalid")
    selected_wheelhouse = _canonical_path_v1(wheelhouse, "wheelhouse")
    return _ensure_core_v1(
        lock_bytes, selected_wheelhouse, profile, PRODUCT_ENVIRONMENT_STORE_V1,
        PRODUCT_BASE_PYTHON_V1, (0, 0),
    )


def ensure_python_environment_for_test_v1(
    lock_bytes: object, wheelhouse: object, profile: object, *,
    environment_store: object, base_python: object,
) -> InstalledPythonEnvironmentV1:
    if type(lock_bytes) is not bytes or type(profile) is not str:
        _fail("birth_python_environment_request_invalid")
    return _ensure_core_v1(
        lock_bytes, _canonical_path_v1(wheelhouse, "wheelhouse"), profile,
        _canonical_path_v1(environment_store, "environment store"),
        _canonical_path_v1(base_python, "base Python"),
        (os.geteuid(), os.getegid()),
    )


__all__ = [
    "InstalledPythonEnvironmentV1", "PRODUCT_ENVIRONMENT_STORE_V1",
    "PythonEnvironmentPosixError", "ensure_python_environment_for_test_v1",
    "ensure_python_environment_v1",
]
