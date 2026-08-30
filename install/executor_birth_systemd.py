"""Locked, byte-identical G6 administrative installation for RM-0008.

This module deliberately installs only the signed ``group6_admin`` artifact.
Signed systemd units remain distribution artifacts until the G7 cutover.
"""
from __future__ import annotations

import hashlib
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


_REPOSITORY = Path(__file__).resolve().parents[1]
if str(_REPOSITORY) not in sys.path:  # pragma: no cover - installer bootstrap
    sys.path.insert(0, str(_REPOSITORY))
_RUNTIME = _REPOSITORY / "runtime"
if str(_RUNTIME) not in sys.path:  # pragma: no cover - installer bootstrap
    sys.path.insert(0, str(_RUNTIME))

import executor_birth_distribution_manifest as distribution_manifest
from executor_birth_distribution_assembler import (
    DEFAULT_ADMINISTRATIVE_ROOT_TEXT_V1,
    DEFAULT_SYSTEM_UNIT_ROOT_TEXT_V1,
    DEPLOYMENT_DESCRIPTOR_PATH_V1,
    DistributionAssemblerError,
    MAX_DEPLOYMENT_DESCRIPTOR_BYTES_V1,
    decode_deployment_descriptor_v1,
)
from executor_birth_secure_file import (
    SecureFileReadError,
    read_immutable_regular_file,
)
from install.executor_birth_source_receiver import (
    _ServiceAccountV1,
    _create_private_directory_v1,
    _ensure_child_directory_v1,
    _identity,
    _name_status_v1,
    _open_absolute_directory_v1,
    _rename_no_replace_v1,
    _require_absolute_chain_bound_v1,
    _service_account_snapshot_v1,
    _stable_identity,
    _write_all_v1,
)


ADMINISTRATIVE_PROGRAM_SOURCE_V1 = "deployment/admin/preflight.py"
ADMINISTRATIVE_PROGRAM_BASENAME_V1 = "preflight.py"
_STAGING_PREFIX_V1 = ".executor-birth-v1."
_STAGING_SUFFIX_V1 = ".tmp"
_DIRECTORY_FLAGS_V1 = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS_V1 = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _fail(code: str, detail: str = "") -> DistributionAssemblerError:
    return DistributionAssemblerError(code, detail)


def _require_linux_v1() -> None:
    if not sys.platform.startswith("linux"):
        raise _fail("birth_ownership_platform_unsupported")


def _require_root_v1() -> None:
    if os.geteuid() != 0:
        raise _fail("birth_ownership_administrative_required")


def _binding_v1(*values: str) -> bytes:
    digest = hashlib.sha256(
        b"metnos.executor-birth.group6-administrative-install/v1\0"
    )
    for value in values:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


_INSTALLED_ADMINISTRATIVE_SEAL_V1 = object()
_INSTALLED_ADMINISTRATIVE_TEST_SEAL_V1 = object()


@dataclass(frozen=True, slots=True)
class InstalledGroup6AdministrativeV1:
    closed_build_id: str
    descriptor_id: str
    release_sequence: int
    administrative_root: str
    preflight_path: str
    content_hash: str
    _artifact_binding: bytes
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _INSTALLED_ADMINISTRATIVE_SEAL_V1
            or self._artifact_binding != _binding_v1(
                self.closed_build_id, self.descriptor_id,
                str(self.release_sequence), self.administrative_root,
                self.preflight_path, self.content_hash,
            )
        ):
            raise _fail("birth_ownership_deployment_invalid", "installed artifact")


@dataclass(frozen=True, slots=True)
class _InstalledGroup6AdministrativeForTestV1:
    closed_build_id: str
    descriptor_id: str
    release_sequence: int
    administrative_root: str
    preflight_path: str
    content_hash: str
    _artifact_binding: bytes
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _INSTALLED_ADMINISTRATIVE_TEST_SEAL_V1
            or self._artifact_binding != _binding_v1(
                self.closed_build_id, self.descriptor_id,
                str(self.release_sequence), self.administrative_root,
                self.preflight_path, self.content_hash,
            )
        ):
            raise _fail(
                "birth_ownership_deployment_invalid", "test installed artifact",
            )


def _manifest_file_v1(record: object, path: str, role: str):
    matches = tuple(
        item for item in record.files
        if item.path == path and item.role == role
    )
    if len(matches) != 1:
        raise _fail("birth_ownership_deployment_invalid", "manifest binding")
    return matches[0]


def _capture_signed_file_v1(root: Path, item: object) -> bytes:
    try:
        content = read_immutable_regular_file(
            root.joinpath(*item.path.split("/")), maximum=item.size,
        )
    except SecureFileReadError as exc:
        raise _fail("birth_ownership_deployment_unsafe", "signed source") from exc
    if (
        len(content) != item.size
        or distribution_manifest.file_content_hash(item.path, content)
        != item.content_hash
    ):
        raise _fail("birth_ownership_deployment_unsafe", "signed source")
    return content


def _require_descriptor_binding_v1(
    verified: object, descriptor: object, preflight_bytes: bytes,
    account: _ServiceAccountV1,
):
    if (
        descriptor.release_sequence != verified.release_sequence
        or descriptor.installation_root != verified.installation_root
        or descriptor.administrative_root
        != DEFAULT_ADMINISTRATIVE_ROOT_TEXT_V1
        or descriptor.system_unit_root != DEFAULT_SYSTEM_UNIT_ROOT_TEXT_V1
        or verified.preflight_entrypoint != ADMINISTRATIVE_PROGRAM_SOURCE_V1
        or (
            descriptor.service_user, descriptor.service_uid,
            descriptor.service_gid, descriptor.service_supplementary_gids,
            descriptor.service_home, descriptor.service_shell,
        ) != (
            account.name, account.uid, account.gid,
            account.supplementary_gids, account.home, account.shell,
        )
    ):
        raise _fail("birth_ownership_deployment_invalid", "descriptor binding")

    manifest_by_path = {item.path: item for item in verified.files}
    if len(manifest_by_path) != len(verified.files):
        raise _fail("birth_ownership_deployment_invalid", "manifest duplicates")
    administrative = tuple(
        item for item in descriptor.artifacts
        if item.install_phase == "group6_admin"
    )
    deferred = tuple(
        item for item in descriptor.artifacts
        if item.install_phase == "group7_cutover"
    )
    if len(administrative) != 1 or not deferred:
        raise _fail("birth_ownership_deployment_invalid", "artifact phases")
    artifact = administrative[0]
    expected_destination = (
        DEFAULT_ADMINISTRATIVE_ROOT_TEXT_V1 + "/"
        + ADMINISTRATIVE_PROGRAM_BASENAME_V1
    )
    if (
        artifact.source_path != ADMINISTRATIVE_PROGRAM_SOURCE_V1
        or artifact.destination_path != expected_destination
        or artifact.kind != "administrative_program"
        or (artifact.mode, artifact.uid, artifact.gid) != (0o755, 0, 0)
        or artifact.size != len(preflight_bytes)
        or distribution_manifest.file_content_hash(
            artifact.source_path, preflight_bytes,
        ) != artifact.content_hash
    ):
        raise _fail("birth_ownership_deployment_invalid", "administrative artifact")
    for candidate in descriptor.artifacts:
        manifest_item = manifest_by_path.get(candidate.source_path)
        expected_role = (
            "preflight"
            if candidate.install_phase == "group6_admin" else "service_unit"
        )
        if (
            manifest_item is None or manifest_item.role != expected_role
            or candidate.size != manifest_item.size
            or candidate.content_hash != manifest_item.content_hash
            or candidate.install_phase not in {"group6_admin", "group7_cutover"}
        ):
            raise _fail("birth_ownership_deployment_invalid", "artifact manifest")
    return descriptor, artifact


def _require_plain_directory_fd_v1(
    descriptor: int, *, owner: tuple[int, int], mode: int,
) -> os.stat_result:
    try:
        info = os.fstat(descriptor)
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "directory") from exc
    if (
        not stat.S_ISDIR(info.st_mode) or info.st_nlink < 2
        or stat.S_IMODE(info.st_mode) != mode
        or (info.st_uid, info.st_gid) != owner
    ):
        raise _fail("birth_ownership_recovery_required", "directory metadata")
    return info


def _open_parent_v1(
    path: Path, *, owner: tuple[int, int], require_session: Callable[[], None],
) -> int:
    if not path.is_absolute() or path.name != "executor-birth-v1":
        raise _fail("birth_ownership_deployment_unsafe", "administrative root")
    descriptors, parts = _open_absolute_directory_v1(str(path.parent.parent))
    parent_fd: int | None = None
    try:
        _require_absolute_chain_bound_v1(
            descriptors, parts, detail="administrative parent",
        )
        grandparent_fd = descriptors[-1]
        grandparent = os.fstat(grandparent_fd)
        if (
            (grandparent.st_uid, grandparent.st_gid) != owner
            or stat.S_IMODE(grandparent.st_mode) & 0o022
        ):
            raise _fail("birth_ownership_deployment_unsafe", "administrative parent")
        require_session()
        parent_fd = _ensure_child_directory_v1(
            grandparent_fd, path.parent.name, owner=owner, mode=0o755,
        )
        info = os.fstat(parent_fd)
        rebound = path.parent.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(rebound.st_mode)
            or (info.st_dev, info.st_ino) != (rebound.st_dev, rebound.st_ino)
            or (info.st_uid, info.st_gid) != owner
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise _fail("birth_ownership_deployment_unsafe", "administrative parent")
        result = parent_fd
        parent_fd = None
        return result
    except DistributionAssemblerError:
        raise
    except OSError as exc:
        raise _fail("birth_ownership_deployment_unsafe", "administrative parent") from exc
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _verify_installed_tree_v1(
    parent_fd: int, name: str, *, content: bytes, content_hash: str,
    owner: tuple[int, int], error_code: str,
) -> int:
    directory_fd: int | None = None
    file_fd: int | None = None
    try:
        directory_fd = os.open(name, _DIRECTORY_FLAGS_V1, dir_fd=parent_fd)
        directory_info = _require_plain_directory_fd_v1(
            directory_fd, owner=owner, mode=0o755,
        )
        rebound = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (directory_info.st_dev, directory_info.st_ino) != (
            rebound.st_dev, rebound.st_ino,
        ):
            raise _fail(error_code, "directory binding")
        with os.scandir(directory_fd) as entries:
            names = tuple(sorted(entry.name for entry in entries))
        if names != (ADMINISTRATIVE_PROGRAM_BASENAME_V1,):
            raise _fail(error_code, "administrative tree")
        file_fd = os.open(
            ADMINISTRATIVE_PROGRAM_BASENAME_V1, _READ_FLAGS_V1,
            dir_fd=directory_fd,
        )
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o755
            or (before.st_uid, before.st_gid) != owner
            or before.st_size != len(content)
        ):
            raise _fail(error_code, "administrative file metadata")
        observed = bytearray()
        while len(observed) <= len(content):
            chunk = os.read(file_fd, min(65536, len(content) + 1 - len(observed)))
            if not chunk:
                break
            observed.extend(chunk)
        after = os.fstat(file_fd)
        if (
            bytes(observed) != content
            or _stable_identity(_identity(before))
            != _stable_identity(_identity(after))
            or distribution_manifest.file_content_hash(
                ADMINISTRATIVE_PROGRAM_SOURCE_V1, bytes(observed),
            ) != content_hash
        ):
            raise _fail(error_code, "administrative file content")
        os.fsync(directory_fd)
        return directory_fd
    except DistributionAssemblerError:
        if directory_fd is not None:
            os.close(directory_fd)
        raise
    except OSError as exc:
        if directory_fd is not None:
            os.close(directory_fd)
        raise _fail(error_code, "administrative tree") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)


def _publish_administrative_tree_v1(
    administrative_root: Path, *, descriptor_id: str, content: bytes,
    content_hash: str, owner: tuple[int, int], require_session: Callable[[], None],
) -> None:
    parent_fd = _open_parent_v1(
        administrative_root, owner=owner, require_session=require_session,
    )
    final_name = administrative_root.name
    stage_name = (
        _STAGING_PREFIX_V1 + descriptor_id.removeprefix("sha256:")
        + _STAGING_SUFFIX_V1
    )
    stage_fd: int | None = None
    try:
        final_info = _name_status_v1(parent_fd, final_name)
        stage_info = _name_status_v1(parent_fd, stage_name)
        if final_info is not None:
            if stage_info is not None:
                raise _fail("birth_ownership_recovery_required", "duplicate transaction")
            installed_fd = _verify_installed_tree_v1(
                parent_fd, final_name, content=content, content_hash=content_hash,
                owner=owner, error_code="birth_ownership_recovery_required",
            )
            os.close(installed_fd)
            require_session()
            return
        if stage_info is not None:
            stage_fd = _verify_installed_tree_v1(
                parent_fd, stage_name, content=content, content_hash=content_hash,
                owner=owner, error_code="birth_ownership_recovery_required",
            )
        else:
            require_session()
            stage_fd, _identity = _create_private_directory_v1(
                parent_fd, stage_name, owner=owner,
            )
            file_fd: int | None = None
            try:
                file_fd = os.open(
                    ADMINISTRATIVE_PROGRAM_BASENAME_V1,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600, dir_fd=stage_fd,
                )
                os.fchown(file_fd, *owner)
                _write_all_v1(file_fd, content)
                os.fchmod(file_fd, 0o755)
                os.fsync(file_fd)
            except DistributionAssemblerError:
                raise
            except OSError as exc:
                raise _fail(
                    "birth_ownership_recovery_required", "administrative write",
                ) from exc
            finally:
                if file_fd is not None:
                    os.close(file_fd)
            os.fchmod(stage_fd, 0o755)
            os.fsync(stage_fd)
            os.fsync(parent_fd)
            os.close(stage_fd)
            stage_fd = _verify_installed_tree_v1(
                parent_fd, stage_name, content=content, content_hash=content_hash,
                owner=owner, error_code="birth_ownership_recovery_required",
            )
        require_session()
        try:
            _rename_no_replace_v1(
                parent_fd, stage_name, parent_fd, final_name,
                expected_fd=stage_fd, sync_source_parent=False,
            )
        except FileExistsError as exc:
            raise _fail(
                "birth_ownership_recovery_required", "publication collision",
            ) from exc
        os.close(stage_fd)
        stage_fd = None
        installed_fd = _verify_installed_tree_v1(
            parent_fd, final_name, content=content, content_hash=content_hash,
            owner=owner, error_code="birth_ownership_recovery_required",
        )
        os.close(installed_fd)
        require_session()
    finally:
        if stage_fd is not None:
            os.close(stage_fd)
        os.close(parent_fd)
def _install_locked_core_v1(
    record: object, *, verify: Callable[[], object], source_root: Path,
    administrative_root: Path,
    account_for_descriptor: Callable[[str], _ServiceAccountV1],
    account_again: Callable[[_ServiceAccountV1], _ServiceAccountV1],
    owner: tuple[int, int],
    require_session: Callable[[], None], between_verifications: Callable[[], None] | None,
    for_test: bool,
):
    require_session()
    verified_before = verify()
    descriptor_item = _manifest_file_v1(
        record, DEPLOYMENT_DESCRIPTOR_PATH_V1, "deployment_descriptor",
    )
    preflight_item = _manifest_file_v1(
        record, ADMINISTRATIVE_PROGRAM_SOURCE_V1, "preflight",
    )
    descriptor_bytes = _capture_signed_file_v1(source_root, descriptor_item)
    if len(descriptor_bytes) > MAX_DEPLOYMENT_DESCRIPTOR_BYTES_V1:
        raise _fail("birth_ownership_deployment_invalid", "descriptor size")
    preflight_bytes = _capture_signed_file_v1(source_root, preflight_item)
    try:
        decoded_descriptor = decode_deployment_descriptor_v1(descriptor_bytes)
    except DistributionAssemblerError as exc:
        raise _fail("birth_ownership_deployment_invalid", "descriptor") from exc
    account = account_for_descriptor(decoded_descriptor.service_user)
    descriptor, artifact = _require_descriptor_binding_v1(
        verified_before, decoded_descriptor, preflight_bytes, account,
    )
    if between_verifications is not None:
        between_verifications()
    verified_after = verify()
    if verified_before != verified_after or account_again(account) != account:
        raise _fail("birth_ownership_deployment_unsafe", "verification changed")
    require_session()
    _publish_administrative_tree_v1(
        administrative_root, descriptor_id=descriptor.descriptor_id,
        content=preflight_bytes, content_hash=artifact.content_hash,
        owner=owner, require_session=require_session,
    )
    values = (
        verified_after.identity.closed_build_id, descriptor.descriptor_id,
        str(descriptor.release_sequence), str(administrative_root),
        str(administrative_root / ADMINISTRATIVE_PROGRAM_BASENAME_V1),
        artifact.content_hash,
    )
    result_type = (
        _InstalledGroup6AdministrativeForTestV1
        if for_test else InstalledGroup6AdministrativeV1
    )
    seal = (
        _INSTALLED_ADMINISTRATIVE_TEST_SEAL_V1
        if for_test else _INSTALLED_ADMINISTRATIVE_SEAL_V1
    )
    return result_type(
        values[0], values[1], descriptor.release_sequence, values[3], values[4],
        values[5], _binding_v1(*values), seal,
    )


def install_group6_administrative_v1(
    record: distribution_manifest.AuthenticatedDistributionRecordV1,
    session: object,
) -> InstalledGroup6AdministrativeV1:
    """Install the sole G6 administrative artifact under the live outer lock."""
    _require_linux_v1()
    _require_root_v1()
    if type(record) is not distribution_manifest.AuthenticatedDistributionRecordV1:
        raise _fail("birth_ownership_distribution_invalid", "authenticated artifact")
    from executor_birth_ownership_coordinator import (
        _require_deployment_lock_session_v1,
    )

    _require_deployment_lock_session_v1(session)
    return _install_locked_core_v1(
        record,
        verify=lambda: distribution_manifest.verify_installed_distribution_record_v1(
            record,
        ),
        source_root=Path(record.installation_root),
        administrative_root=Path(DEFAULT_ADMINISTRATIVE_ROOT_TEXT_V1),
        account_for_descriptor=_service_account_snapshot_v1,
        account_again=lambda account: _service_account_snapshot_v1(account.name),
        owner=(0, 0),
        require_session=lambda: _require_deployment_lock_session_v1(session),
        between_verifications=None,
        for_test=False,
    )


def _install_group6_administrative_for_test_v1(
    record: object, *, environment: object, session: object,
    ownership_root: Path, administrative_root: Path,
    account: _ServiceAccountV1,
    between_verifications: Callable[[], None] | None = None,
) -> _InstalledGroup6AdministrativeForTestV1:
    """Nominally isolated portable seam; never accepts productive authority."""
    _require_linux_v1()
    if (
        type(record)
        is not distribution_manifest._AuthenticatedDistributionRecordForTestV1
        or type(environment) is not distribution_manifest._VerificationEnvironment
        or environment._seal is not distribution_manifest._ENVIRONMENT_SEAL
        or type(account) is not _ServiceAccountV1
        or between_verifications is not None
        and not callable(between_verifications)
    ):
        raise _fail("birth_ownership_deployment_invalid", "test authority")
    from executor_birth_ownership_coordinator import (
        _require_test_deployment_lock_session_v1,
    )

    _require_test_deployment_lock_session_v1(session, Path(ownership_root))
    return _install_locked_core_v1(
        record,
        verify=lambda: distribution_manifest._verify_authenticated_distribution_record_for_test(
            record, environment=environment,
        ),
        source_root=Path(environment.installation_root),
        administrative_root=Path(administrative_root),
        account_for_descriptor=lambda _name: account,
        account_again=lambda _account: account,
        owner=(os.geteuid(), os.getegid()),
        require_session=lambda: _require_test_deployment_lock_session_v1(
            session, Path(ownership_root),
        ),
        between_verifications=between_verifications,
        for_test=True,
    )


__all__ = (
    "ADMINISTRATIVE_PROGRAM_BASENAME_V1",
    "ADMINISTRATIVE_PROGRAM_SOURCE_V1",
    "InstalledGroup6AdministrativeV1",
    "install_group6_administrative_v1",
)
