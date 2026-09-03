"""Linux effect adapter for the canonical Executor Birth host provisioner."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import os
from pathlib import Path
import stat
import subprocess

import executor_birth_account_identity as account_identity
from executor_birth_posix_metadata import snapshot_stat_v1
from executor_birth_host_layout import (
    HOST_PATH_POLICY_V1, HOST_TRUST_ANCHORS_V1,
    HOST_PROVISIONING_ROOT_V1,
    SERVICE_ACCOUNT_POLICY_V1,
    HostOwnerKindV1,
    HostLayoutObservationV1,
    HostLayoutStepKindV1,
    HostNodeKindV1,
    HostPathObservationV1,
    build_host_layout_spec_v1,
    require_canonical_host_layout_step_v1,
)
from install.executor_birth_host_journal_posix import (
    HostJournalEffectsV1, HostProvisioningPosixError, PosixJournalStoreV1,
    open_journal_lock_v1, raise_posix_v1, require_journal_lock_bound_v1,
)


_ROOT = Path(HOST_PROVISIONING_ROOT_V1.as_posix())
_DIRECTORY_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_ACL_NAMES = ("system.posix_acl_access", "system.posix_acl_default")
_ENVIRONMENT = {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
_TRUST_ANCHORS_V1 = frozenset(Path(path.as_posix()) for path in HOST_TRUST_ANCHORS_V1)
_raise = raise_posix_v1


def _close_all_v1(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        os.close(descriptor)


def _expected_layout_v1(account) -> dict[Path, tuple[int, int, int]]:
    return {
        Path(item.path.as_posix()): (
            item.ownership.uid, item.ownership.gid, item.mode,
        )
        for item in build_host_layout_spec_v1(account).objects
    }


def _bootstrap_expected_v1() -> dict[Path, tuple[int, int, int]]:
    matches = tuple(
        item for item in HOST_PATH_POLICY_V1
        if Path(item.path.as_posix()) == _ROOT
    )
    if len(matches) != 1 or matches[0].owner_kind is not HostOwnerKindV1.root:
        _raise("bootstrap policy")
    return {_ROOT: (0, 0, matches[0].mode)}


def _require_opened_metadata_v1(
    descriptor: int, path: Path,
    expected: dict[Path, tuple[int, int, int]],
) -> None:
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        _raise("directory metadata")
    desired = expected.get(path)
    acl_present = (
        any(_acl_present_v1(descriptor))
        if path in _TRUST_ANCHORS_V1 or desired is not None else False
    )
    if path in _TRUST_ANCHORS_V1 and (
        (info.st_uid, info.st_gid) != (0, 0)
        or stat.S_IMODE(info.st_mode) & 0o022 or acl_present
    ):
        _raise("trust anchor metadata")
    if desired is not None and (
        (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != desired
        or acl_present
    ):
        _raise("canonical parent metadata")


def _open_chain_v1(
    path: Path, expected: dict[Path, tuple[int, int, int]] | None = None,
) -> list[int]:
    if not isinstance(path, Path) or not path.is_absolute():
        _raise("absolute directory")
    expected = {} if expected is None else expected
    descriptors: list[int] = []
    try:
        descriptors.append(os.open("/", _DIRECTORY_FLAGS))
        _require_opened_metadata_v1(descriptors[0], Path("/"), expected)
        if not os.path.samestat(
            os.fstat(descriptors[0]), os.stat("/", follow_symlinks=False),
        ):
            _raise("directory chain replaced")
        current = Path("/")
        for part in path.parts[1:]:
            parent = descriptors[-1]
            descriptors.append(os.open(part, _DIRECTORY_FLAGS, dir_fd=parent))
            current /= part
            _require_bound_v1(parent, part, descriptors[-1])
            _require_opened_metadata_v1(descriptors[-1], current, expected)
            _require_bound_v1(parent, part, descriptors[-1])
        return descriptors
    except BaseException as exc:
        _close_all_v1(descriptors)
        if isinstance(exc, HostProvisioningPosixError):
            raise
        if isinstance(exc, OSError):
            _raise("directory chain", exc)
        raise


def _acl_present_v1(target: int | str) -> tuple[bool, bool]:
    result = []
    for name in _ACL_NAMES:
        try:
            if type(target) is int:
                os.getxattr(target, name)
            else:
                os.getxattr(target, name, follow_symlinks=False)
            result.append(True)
        except OSError as exc:
            if exc.errno not in {errno.ENODATA, getattr(errno, "ENOATTR", -1)}:
                _raise("POSIX ACL observation", exc)
            result.append(False)
    return result[0], result[1]


def _remove_acl_v1(descriptor: int) -> None:
    for name in _ACL_NAMES:
        try:
            os.removexattr(descriptor, name)
        except OSError as exc:
            if exc.errno not in {errno.ENODATA, getattr(errno, "ENOATTR", -1)}:
                _raise("POSIX ACL removal", exc)


def _require_directory_v1(descriptor: int, *, mode: int) -> None:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or (info.st_uid, info.st_gid) != (0, 0)
        or stat.S_IMODE(info.st_mode) != mode
        or any(_acl_present_v1(descriptor))
    ):
        _raise("directory metadata")


def _ensure_bootstrap_root_v1() -> None:
    descriptors = _open_chain_v1(_ROOT.parent)
    child, created = None, False
    mode = _bootstrap_expected_v1()[_ROOT][2]
    try:
        try:
            os.mkdir(_ROOT.name, mode, dir_fd=descriptors[-1])
            created = True
            os.fsync(descriptors[-1])
        except FileExistsError:
            pass
        child = os.open(_ROOT.name, _DIRECTORY_FLAGS, dir_fd=descriptors[-1])
        _require_bound_v1(descriptors[-1], _ROOT.name, child)
        if created:
            os.fchown(child, 0, 0)
            _remove_acl_v1(child)
            os.fchmod(child, mode)
            os.fsync(child)
        _require_directory_v1(child, mode=mode)
        _require_bound_v1(descriptors[-1], _ROOT.name, child)
    except OSError as exc:
        _raise("bootstrap root", exc)
    finally:
        if child is not None:
            os.close(child)
        _close_all_v1(descriptors)


def _require_bound_v1(parent: int, name: str, child: int):
    opened = os.fstat(child)
    rebound = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not os.path.samestat(opened, rebound):
        _raise("layout path replaced")
    return opened


class _PosixHostEffectsV1(HostJournalEffectsV1):
    def observe_account(self):
        try:
            return account_identity.resolve_posix_account_snapshot_v1(
                SERVICE_ACCOUNT_POLICY_V1.name,
            )
        except account_identity.PosixAccountResolutionError as exc:
            if (
                exc.kind is account_identity.PosixAccountFailureKindV1.account_lookup_failed
                and isinstance(exc.internal_cause, KeyError)
            ):
                return None
            _raise("account lookup", exc)

    def observe_primary_group(self) -> int | None:
        try:
            import grp
            entry = grp.getgrnam(SERVICE_ACCOUNT_POLICY_V1.primary_group_name)
        except KeyError:
            return None
        except (ImportError, OSError) as exc:
            _raise("group lookup", exc)
        if entry.gr_name != SERVICE_ACCOUNT_POLICY_V1.primary_group_name:
            _raise("group identity")
        return entry.gr_gid

    def _run(self, arguments: list[str]) -> int:
        try:
            result = subprocess.run(
                arguments, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, close_fds=True, cwd="/", env=_ENVIRONMENT,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _raise("account command", exc)
        return result.returncode

    def create_primary_group(self) -> None:
        returncode = self._run([
            "/usr/sbin/groupadd", "--system", "--",
            SERVICE_ACCOUNT_POLICY_V1.primary_group_name,
        ])
        gid = self.observe_primary_group()
        if type(gid) is not int or gid <= 0:
            detail = "group creation race" if returncode else "group creation"
            _raise(detail)

    def create_account(self) -> None:
        policy = SERVICE_ACCOUNT_POLICY_V1
        gid = self.observe_primary_group()
        if type(gid) is not int or gid <= 0:
            _raise("primary group identity")
        returncode = self._run([
            "/usr/sbin/useradd", "--system", "--gid", policy.primary_group_name,
            "--home-dir", policy.home.as_posix(), "--shell", policy.shell.as_posix(),
            "--no-create-home", "--", policy.name,
        ])
        account = self.observe_account()
        try:
            if account is None or account.record.gid != gid:
                raise ValueError("account or group mismatch")
            build_host_layout_spec_v1(account)
        except (TypeError, ValueError) as exc:
            detail = "account creation race" if returncode else "account creation"
            _raise(detail, exc)

    def _observe_target(self, target) -> HostPathObservationV1:
        child = None
        try:
            descriptors = _open_chain_v1(Path(target.path.as_posix()).parent)
        except HostProvisioningPosixError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                return HostPathObservationV1(target.path, HostNodeKindV1.missing)
            raise
        try:
            try:
                info = os.stat(target.path.name, dir_fd=descriptors[-1], follow_symlinks=False)
            except FileNotFoundError:
                return HostPathObservationV1(target.path, HostNodeKindV1.missing)
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                return HostPathObservationV1(
                    target.path, HostNodeKindV1.other, info.st_uid, info.st_gid,
                    stat.S_IMODE(info.st_mode), False, False,
                )
            child = os.open(
                target.path.name, _DIRECTORY_FLAGS, dir_fd=descriptors[-1],
            )
            opened = _require_bound_v1(descriptors[-1], target.path.name, child)
            acl = _acl_present_v1(child)
            final = _require_bound_v1(descriptors[-1], target.path.name, child)
            if (
                snapshot_stat_v1(info) != snapshot_stat_v1(opened)
                or snapshot_stat_v1(opened) != snapshot_stat_v1(final)
            ):
                _raise("layout path replaced")
            return HostPathObservationV1(
                target.path, HostNodeKindV1.directory, final.st_uid, final.st_gid,
                stat.S_IMODE(final.st_mode), acl[0], acl[1],
            )
        finally:
            if child is not None:
                os.close(child)
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def observe_layout(self, account) -> HostLayoutObservationV1:
        spec = build_host_layout_spec_v1(account)
        return HostLayoutObservationV1(
            account, tuple(self._observe_target(target) for target in spec.objects),
        )

    def apply_layout_step(self, step, account) -> None:
        try:
            step = require_canonical_host_layout_step_v1(step, account)
        except (TypeError, ValueError) as exc:
            _raise("layout step", exc)
        target = step.target
        expected = _expected_layout_v1(account)
        descriptors = _open_chain_v1(
            Path(target.path.as_posix()).parent, expected,
        )
        child = None
        try:
            if step.kind is HostLayoutStepKindV1.create_directory:
                try:
                    os.mkdir(target.path.name, 0o700, dir_fd=descriptors[-1])
                except FileExistsError as exc:
                    _raise("layout create collision", exc)
                os.fsync(descriptors[-1])
            child = os.open(target.path.name, _DIRECTORY_FLAGS, dir_fd=descriptors[-1])
            _require_bound_v1(descriptors[-1], target.path.name, child)
            if step.kind is HostLayoutStepKindV1.set_owner:
                os.fchown(child, target.ownership.uid, target.ownership.gid)
            elif step.kind is HostLayoutStepKindV1.remove_posix_acl:
                _remove_acl_v1(child)
            elif step.kind is HostLayoutStepKindV1.set_mode:
                os.fchmod(child, target.mode)
            os.fsync(child)
            _require_bound_v1(descriptors[-1], target.path.name, child)
        except OSError as exc:
            _raise("layout effect", exc)
        finally:
            if child is not None:
                os.close(child)
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def checkpoint(self, name: str) -> None:
        if type(name) is not str or not name:
            _raise("checkpoint")


def _attest_locked_host_v1(roots, lock_fd, bootstrap) -> None:
    paths = (Path("/"), Path("/var"), Path("/var/lib"), _ROOT)
    if type(roots) is not list or len(roots) != len(paths):
        _raise("host capability root chain")
    for index, (descriptor, path) in enumerate(zip(roots, paths)):
        _require_opened_metadata_v1(descriptor, path, bootstrap)
        if index == 0:
            rebound = os.stat("/", follow_symlinks=False)
            if not os.path.samestat(os.fstat(descriptor), rebound):
                _raise("directory chain replaced")
        else:
            _require_bound_v1(roots[index - 1], path.name, descriptor)
    require_journal_lock_bound_v1(roots[-1], lock_fd, (0, 0))


@contextmanager
def locked_host_effects_v1():
    owner_pid = os.getpid()
    _ensure_bootstrap_root_v1()
    roots, lock_fd, locked = [], None, False
    effects, capability = None, None
    try:
        bootstrap = _bootstrap_expected_v1()
        roots = _open_chain_v1(_ROOT, bootstrap)
        lock_fd = open_journal_lock_v1(roots[-1], (0, 0))
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        locked = True
        effects = _PosixHostEffectsV1(PosixJournalStoreV1(roots[-1], (0, 0)))
        def attest() -> None:
            _attest_locked_host_v1(roots, lock_fd, bootstrap)

        attest()
        from install.executor_birth_host_capability import bind_locked_host_effects_v1
        capability = bind_locked_host_effects_v1(effects, attest)
        yield capability
    finally:
        owner = os.getpid() == owner_pid
        try:
            if capability is not None and owner:
                capability._deactivate_v1()
            elif locked and owner:
                _attest_locked_host_v1(roots, lock_fd, bootstrap)
        finally:
            if lock_fd is not None:
                if locked and owner:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            _close_all_v1(roots)

__all__ = ["HostProvisioningPosixError", "locked_host_effects_v1"]
