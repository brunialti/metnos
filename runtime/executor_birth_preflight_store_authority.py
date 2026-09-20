"""Process-local authority for durable preflight-attestation effects."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows import-purity lane
    fcntl = None  # type: ignore[assignment]

from executor_birth_admin_preflight import (
    MAX_PREFLIGHT_ATTESTATIONS_V1, _invalid, _recovery,
    require_linux_before_io_v1,
)
from executor_birth_host_path_policy import PREFLIGHT_ATTESTATION_ROOT_V1
from executor_birth_posix_metadata import snapshot_stat_v1


_PRODUCT_SEAL_V1 = object()
_TEST_SEAL_V1 = object()
MAX_STORE_ENTRIES_V1 = MAX_PREFLIGHT_ATTESTATIONS_V1
ACL_NAMES_V1 = ("system.posix_acl_access", "system.posix_acl_default")


def same_store_directory_binding_v1(
    left: os.stat_result, right: os.stat_result,
) -> bool:
    first = snapshot_stat_v1(left)
    second = snapshot_stat_v1(right)
    return first.same_object_as(second) and (
        first.mode, first.link_count, first.uid, first.gid,
    ) == (
        second.mode, second.link_count, second.uid, second.gid,
    )


class _NonTransferableV1:
    __slots__ = ()

    def __copy__(self):
        raise TypeError("preflight store authority cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("preflight store authority cannot be copied")

    def __reduce__(self):
        raise TypeError("preflight store authority cannot be serialized")

    def __reduce_ex__(self, _protocol):
        raise TypeError("preflight store authority cannot be serialized")


class _ProductStoreAuthorityV1(_NonTransferableV1):
    __slots__ = ("sessions", "pid", "_seal")

    def __init__(self, sessions: tuple[object, ...], seal: object) -> None:
        if seal is not _PRODUCT_SEAL_V1:
            raise _invalid("preflight store authority")
        self.sessions = sessions
        self.pid = os.getpid()
        self._seal = seal

class _TestStoreAuthorityV1(_NonTransferableV1):
    __slots__ = ("root", "chain_stop", "pid", "_seal")

    def __init__(self, root: Path, chain_stop: Path, seal: object) -> None:
        if seal is not _TEST_SEAL_V1:
            raise _invalid("test preflight store authority")
        self.root = root
        self.chain_stop = chain_stop
        self.pid = os.getpid()
        self._seal = seal

def product_store_root_v1() -> Path:
    return Path(PREFLIGHT_ATTESTATION_ROOT_V1.as_posix())


def product_store_authority_v1(
    sessions: tuple[object, ...],
) -> _ProductStoreAuthorityV1:
    return _ProductStoreAuthorityV1(sessions, _PRODUCT_SEAL_V1)


def _canonical_test_root_v1(root: Path) -> Path:
    if not isinstance(root, Path):
        raise _invalid("test preflight store root")
    raw = os.fspath(root)
    absolute = Path(os.path.abspath(raw))
    if (
        not root.is_absolute() or root.parent == root or absolute != root
        or ".." in root.parts or "\x00" in raw
        or root == product_store_root_v1()
    ):
        raise _invalid("test preflight store root")
    return root


def test_store_authority_v1(root: Path) -> _TestStoreAuthorityV1:
    canonical = _canonical_test_root_v1(root)
    return _TestStoreAuthorityV1(canonical, canonical.parent, _TEST_SEAL_V1)


def authorize_store_effect_v1(
    authority: object, root: Path, chain_stop: Path | None,
) -> None:
    if type(authority) is _ProductStoreAuthorityV1:
        _authorize_product_v1(authority, root, chain_stop)
        return
    if type(authority) is _TestStoreAuthorityV1 and (
        authority._seal is _TEST_SEAL_V1
        and authority.pid == os.getpid()
        and root == authority.root and chain_stop == authority.chain_stop
        and root != product_store_root_v1()
    ):
        return
    raise _recovery("preflight store authority")


def require_store_platform_v1() -> None:
    require_linux_before_io_v1()
    required = (
        os.open, os.stat, os.unlink, os.link, os.fstat, os.fchmod,
        os.fchown, os.fsync, os.getxattr,
    )
    constants = (
        getattr(os, "O_CLOEXEC", None), getattr(os, "O_DIRECTORY", None),
        getattr(os, "O_NOFOLLOW", None), getattr(fcntl, "LOCK_EX", None),
    )
    if (
        fcntl is None
        or any(not callable(item) for item in required)
        or any(type(item) is not int for item in constants)
        or not {os.open, os.stat, os.unlink, os.link} <= os.supports_dir_fd
        or not {os.stat, os.link} <= os.supports_follow_symlinks
    ):
        raise _invalid("preflight store POSIX capabilities")


def require_no_acl_v1(descriptor: int, detail: str) -> None:
    for name in ACL_NAMES_V1:
        try:
            os.getxattr(descriptor, name)
        except OSError as exc:
            if exc.errno == errno.ENODATA:
                continue
            raise _recovery(detail) from exc
        raise _recovery(detail)


def require_acl_free_directory_chain_v1(
    root: Path, stop: Path | None, *, uid: int, gid: int,
) -> None:
    current = root
    while True:
        descriptor = -1
        try:
            before = current.lstat()
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
            descriptor = os.open(current, flags)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(opened.st_mode) or opened.st_uid != uid
                or opened.st_gid != gid or opened.st_mode & 0o022
                or not same_store_directory_binding_v1(before, opened)
            ):
                raise _recovery("preflight attestation directory chain")
            require_no_acl_v1(descriptor, "preflight attestation directory ACL")
        except OSError as exc:
            raise _recovery("preflight attestation directory chain") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if current == stop or stop is None and current.parent == current:
            return
        if current.parent == current:
            raise _recovery("preflight attestation directory chain")
        current = current.parent


def bounded_store_names_v1(directory: int) -> frozenset[str]:
    names: list[str] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if len(names) >= MAX_STORE_ENTRIES_V1:
                    raise _recovery("preflight attestation inventory bound")
                names.append(entry.name)
    except OSError as exc:
        raise _recovery("preflight attestation inventory") from exc
    if len(names) != len(set(names)):
        raise _recovery("preflight attestation inventory")
    return frozenset(names)


def require_store_capacity_v1(
    names: frozenset[str], temporary: str, basename: str,
) -> None:
    if (
        type(names) is not frozenset
        or any(type(name) is not str for name in names)
        or (
            temporary not in names and basename not in names
            and len(names) >= MAX_STORE_ENTRIES_V1
        )
    ):
        raise _recovery("preflight attestation inventory bound")


def _require_bound_directory_v1(root: Path, directory: int) -> None:
    try:
        opened = os.fstat(directory)
        named = root.lstat()
    except OSError as exc:
        raise _recovery("preflight attestation directory rebound") from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not same_store_directory_binding_v1(opened, named)
    ):
        raise _recovery("preflight attestation directory rebound")
    require_no_acl_v1(directory, "preflight attestation directory ACL")


def _require_temporary_name_v1(name: str) -> str:
    digest = name[1:-4] if type(name) is str else ""
    if (
        type(name) is not str
        or not name.startswith(".") or not name.endswith(".tmp")
        or len(digest) != 64
        or any(item not in "0123456789abcdef" for item in digest)
    ):
        raise _recovery("preflight attestation staging name")
    return digest


def _require_name_pair_v1(temporary: str, basename: str) -> None:
    digest = _require_temporary_name_v1(temporary)
    if basename != "sha256:" + digest + ".json":
        raise _recovery("preflight attestation publication name")


def _require_staging_fd_v1(directory: int, name: str, descriptor: int) -> None:
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except OSError as exc:
        raise _recovery("preflight attestation staging rebound") from exc
    if (
        not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
        or snapshot_stat_v1(opened) != snapshot_stat_v1(named)
    ):
        raise _recovery("preflight attestation staging rebound")


_PORT_SEAL_V1 = object()


class _StoreMutationPortV1(_NonTransferableV1):
    __slots__ = ("_authority", "_root", "_stop", "_directory", "_pid", "_seal")

    def __init__(
        self, authority: object, root: Path, stop: Path | None,
        directory: int, seal: object,
    ) -> None:
        if seal is not _PORT_SEAL_V1 or type(directory) is not int:
            raise _recovery("preflight store mutation port")
        self._authority, self._root, self._stop = authority, root, stop
        self._directory, self._pid, self._seal = directory, os.getpid(), seal
        self._check()

    def _check(self) -> None:
        if self._seal is not _PORT_SEAL_V1 or self._pid != os.getpid():
            raise _recovery("preflight store mutation port")
        authorize_store_effect_v1(self._authority, self._root, self._stop)
        _require_bound_directory_v1(self._root, self._directory)

    @contextmanager
    def _effect(self, detail: str):
        self._check()
        try:
            yield
        except OSError as exc:
            raise _recovery(detail) from exc
        self._check()

    def lock(self) -> None:
        with self._effect("preflight attestation lock"):
            fcntl.flock(self._directory, fcntl.LOCK_EX)

    def create_staging(self, temporary: str) -> int:
        _require_temporary_name_v1(temporary)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = -1
        try:
            with self._effect("preflight attestation staging"):
                descriptor = os.open(
                    temporary, flags, 0o600, dir_fd=self._directory,
                )
            _require_staging_fd_v1(self._directory, temporary, descriptor)
            return descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def chown_staging(
        self, temporary: str, descriptor: int, uid: int, gid: int,
    ) -> None:
        self._staging_effect(temporary, descriptor, "chown", uid, gid)

    def chmod_staging(
        self, temporary: str, descriptor: int, mode: int,
    ) -> None:
        if type(mode) is not int or mode not in {0o600, 0o644}:
            raise _recovery("preflight attestation staging mode")
        self._staging_effect(temporary, descriptor, "chmod", mode, None)

    def _staging_effect(
        self, temporary: str, descriptor: int, kind: str,
        first: int, second: int | None,
    ) -> None:
        _require_temporary_name_v1(temporary)
        _require_staging_fd_v1(self._directory, temporary, descriptor)
        with self._effect("preflight attestation staging"):
            if kind == "chown" and second is not None:
                os.fchown(descriptor, first, second)
            elif kind == "chmod" and second is None:
                os.fchmod(descriptor, first)
            else:
                raise _recovery("preflight attestation staging operation")
        _require_staging_fd_v1(self._directory, temporary, descriptor)

    def write_staging(
        self, temporary: str, descriptor: int, content: bytes,
    ) -> int:
        if type(content) is not bytes or not content:
            raise _recovery("preflight attestation write")
        _require_temporary_name_v1(temporary)
        _require_staging_fd_v1(self._directory, temporary, descriptor)
        with self._effect("preflight attestation write"):
            written = os.write(descriptor, content)
        _require_staging_fd_v1(self._directory, temporary, descriptor)
        return written

    def sync_staging(self, temporary: str, descriptor: int) -> None:
        _require_temporary_name_v1(temporary)
        _require_staging_fd_v1(self._directory, temporary, descriptor)
        with self._effect("preflight attestation staging"):
            os.fsync(descriptor)
        _require_staging_fd_v1(self._directory, temporary, descriptor)

    def sync_directory(self) -> None:
        with self._effect("preflight attestation directory sync"):
            os.fsync(self._directory)

    def link_staging(self, temporary: str, basename: str) -> None:
        _require_name_pair_v1(temporary, basename)
        with self._effect("preflight attestation publication"):
            os.link(
                temporary, basename, src_dir_fd=self._directory,
                dst_dir_fd=self._directory, follow_symlinks=False,
            )

    def unlink_temporary(self, temporary: str) -> None:
        _require_temporary_name_v1(temporary)
        with self._effect("preflight attestation recovery"):
            os.unlink(temporary, dir_fd=self._directory)


def bind_store_mutation_port_v1(
    authority: object, root: Path, chain_stop: Path | None, directory: int,
) -> _StoreMutationPortV1:
    return _StoreMutationPortV1(
        authority, root, chain_stop, directory, _PORT_SEAL_V1,
    )


def _authorize_product_v1(
    authority: _ProductStoreAuthorityV1,
    root: Path, chain_stop: Path | None,
) -> None:
    if (
        authority._seal is not _PRODUCT_SEAL_V1
        or authority.pid != os.getpid()
        or root != product_store_root_v1() or chain_stop is not None
    ):
        raise _recovery("preflight store product authority")
    try:
        from executor_birth_dominant_startup import _require_product_sessions_v1
        _require_product_sessions_v1(authority.sessions)
    except Exception as exc:
        raise _recovery("preflight store product sessions") from exc


__all__ = []
