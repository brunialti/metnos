"""Locked, byte-identical G6 administrative installation for RM-0008.

The product API installs only the signed ``group6_admin`` artifact.  A private,
nominally separate seam installs namespace-isolated signed units solely in the
disposable G6-C certification VM; it grants no productive G7 authority.
"""
from __future__ import annotations

import hashlib
import os
import re
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
import executor_birth_service_catalog as service_catalog
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
_SYSTEMD_SOURCE_PREFIX_V1 = "deployment/systemd/"
_STAGING_PREFIX_V1 = ".executor-birth-v1."
_STAGING_SUFFIX_V1 = ".tmp"
_ISOLATED_NAMESPACE_RE_V1 = re.compile(r"[0-9a-f]{16}\Z")
_SYSTEMD_UNIT_SUFFIXES_V1 = (".service", ".timer", ".target")
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
_SIGNED_ISOLATED_SYSTEMD_TEST_SEAL_V1 = object()
_INSTALLED_ISOLATED_SYSTEMD_TEST_SEAL_V1 = object()


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


def _isolated_binding_v1(
    closed_build_id: str, descriptor_id: str, namespace: str,
    unit_root: str, ownership_root: str,
    unit_fragments: tuple[tuple[str, bytes], ...],
) -> bytes:
    digest = hashlib.sha256(
        b"metnos.executor-birth.group6-isolated-systemd-test/v1\0"
    )
    for value in (
        closed_build_id, descriptor_id, namespace, unit_root, ownership_root,
    ):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    for unit_name, fragment in unit_fragments:
        encoded_name = unit_name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(fragment).to_bytes(8, "big"))
        digest.update(fragment)
    return digest.digest()


@dataclass(frozen=True, slots=True)
class _SignedIsolatedSystemdTestV1:
    closed_build_id: str
    descriptor_id: str
    namespace: str
    unit_root: str
    ownership_root: str
    unit_fragments: tuple[tuple[str, bytes], ...]
    record: object
    environment: object
    account: _ServiceAccountV1
    _capability_binding: bytes
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _SIGNED_ISOLATED_SYSTEMD_TEST_SEAL_V1
            or self._capability_binding != _isolated_binding_v1(
                self.closed_build_id, self.descriptor_id, self.namespace,
                self.unit_root, self.ownership_root, self.unit_fragments,
            )
        ):
            raise _fail(
                "birth_ownership_deployment_invalid", "isolated capability",
            )


@dataclass(frozen=True, slots=True)
class _InstalledIsolatedSystemdTestV1:
    closed_build_id: str
    descriptor_id: str
    namespace: str
    unit_root: str
    unit_names: tuple[str, ...]
    _capability_binding: bytes
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _INSTALLED_ISOLATED_SYSTEMD_TEST_SEAL_V1
            or self._capability_binding != _binding_v1(
                self.closed_build_id, self.descriptor_id, self.namespace,
                self.unit_root, *self.unit_names,
            )
        ):
            raise _fail(
                "birth_ownership_deployment_invalid", "isolated installation",
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


def _capture_descriptor_and_preflight_v1(
    record: object, source_root: Path,
) -> tuple[object, bytes]:
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
        descriptor = decode_deployment_descriptor_v1(descriptor_bytes)
    except DistributionAssemblerError as exc:
        raise _fail("birth_ownership_deployment_invalid", "descriptor") from exc
    return descriptor, preflight_bytes


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
            "preflight" if candidate.install_phase == "group6_admin"
            else "service_unit"
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
    decoded_descriptor, preflight_bytes = _capture_descriptor_and_preflight_v1(
        record, source_root,
    )
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


def _isolated_stage_name_v1(descriptor_id: str, unit_name: str) -> str:
    return (
        _STAGING_PREFIX_V1
        + descriptor_id.removeprefix("sha256:")[:16] + "."
        + hashlib.sha256(unit_name.encode("utf-8")).hexdigest()[:16]
        + _STAGING_SUFFIX_V1
    )


def _require_isolated_namespace_v1(
    catalog: service_catalog.DecodedServiceCatalogV1, namespace: str,
) -> None:
    entry_prefix = f"g6c-{namespace}-"
    unit_prefix = f"metnos-g6c-{namespace}-"
    services = 0
    timers = 0
    for entry in catalog.entries:
        if (
            not entry.entry_id.startswith(entry_prefix)
            or entry.class_name not in {"gated_service", "gated_timer"}
            or entry.unit_name is None or entry.unit_spec is None
            or not entry.unit_name.startswith(unit_prefix)
        ):
            raise _fail(
                "birth_ownership_deployment_invalid", "isolated namespace",
            )
        services += entry.class_name == "gated_service"
        timers += entry.class_name == "gated_timer"
        if (
            entry.timer_target is not None
            and not entry.timer_target.startswith(entry_prefix)
        ):
            raise _fail(
                "birth_ownership_deployment_invalid", "isolated timer target",
            )
        referenced_values = tuple(
            value
            for directive in entry.unit_spec.directives
            for value in directive.values
        ) + entry.target_args
        for value in referenced_values:
            for token in value.split():
                if (
                    token.endswith(_SYSTEMD_UNIT_SUFFIXES_V1)
                    and not token.startswith(unit_prefix)
                ):
                    raise _fail(
                        "birth_ownership_deployment_invalid",
                        "isolated unit reference",
                    )
    if services < 1 or timers < 1:
        raise _fail("birth_ownership_deployment_invalid", "isolated topology")


def _capture_isolated_cell_v1(
    record: object, *, environment: object, account: _ServiceAccountV1,
    namespace: str, verify: Callable[[], object],
) -> tuple[object, object, tuple[tuple[str, bytes], ...]]:
    verified = verify()
    source_root = Path(environment.installation_root)
    catalog_item = _manifest_file_v1(
        record, service_catalog.CATALOG_PATH_V1, "service_catalog",
    )
    catalog_bytes = _capture_signed_file_v1(source_root, catalog_item)
    descriptor, preflight_bytes = _capture_descriptor_and_preflight_v1(
        record, source_root,
    )
    try:
        catalog = service_catalog.decode_service_catalog_v1(catalog_bytes)
    except service_catalog.ServiceCatalogError as exc:
        raise _fail(
            "birth_ownership_deployment_invalid", "isolated signed metadata",
        ) from exc
    _require_descriptor_binding_v1(
        verified, descriptor, preflight_bytes, account,
    )
    if (
        descriptor.service_catalog_id != catalog.catalog_id
        or descriptor.service_coverage_hash != catalog.service_coverage_hash
    ):
        raise _fail("birth_ownership_deployment_invalid", "catalog binding")
    _require_isolated_namespace_v1(catalog, namespace)

    entries = tuple(
        entry for entry in catalog.entries if entry.unit_spec is not None
    )
    deferred = tuple(
        item for item in descriptor.artifacts
        if item.install_phase == "group7_cutover"
    )
    expected_names = {str(entry.unit_name) for entry in entries}
    expected_kinds = {
        str(entry.unit_name): (
            "timer_unit" if entry.class_name == "gated_timer"
            else "service_unit"
        )
        for entry in entries
    }
    artifacts_by_name: dict[str, object] = {}
    for artifact in deferred:
        destination_prefix = DEFAULT_SYSTEM_UNIT_ROOT_TEXT_V1 + "/"
        if not artifact.destination_path.startswith(destination_prefix):
            raise _fail(
                "birth_ownership_deployment_invalid", "unit destination",
            )
        unit_name = artifact.destination_path.removeprefix(destination_prefix)
        if (
            "/" in unit_name
            or artifact.source_path != _SYSTEMD_SOURCE_PREFIX_V1 + unit_name
            or artifact.kind != expected_kinds.get(unit_name)
            or (artifact.mode, artifact.uid, artifact.gid) != (0o644, 0, 0)
            or unit_name in artifacts_by_name
        ):
            raise _fail(
                "birth_ownership_deployment_invalid", "unit artifact",
            )
        artifacts_by_name[unit_name] = artifact
    if set(artifacts_by_name) != expected_names:
        raise _fail("birth_ownership_deployment_invalid", "unit coverage")

    fragments: list[tuple[str, bytes]] = []
    for entry in entries:
        unit_name = str(entry.unit_name)
        artifact = artifacts_by_name[unit_name]
        manifest_item = _manifest_file_v1(
            record, artifact.source_path, "service_unit",
        )
        fragment = _capture_signed_file_v1(source_root, manifest_item)
        try:
            rendered = service_catalog.render_unit_spec_v1(
                unit_name, entry.unit_spec,
            )
        except service_catalog.ServiceCatalogError as exc:
            raise _fail(
                "birth_ownership_deployment_invalid", "unit rendering",
            ) from exc
        if (
            fragment != rendered
            or artifact.size != len(fragment)
            or artifact.content_hash != manifest_item.content_hash
            or distribution_manifest.file_content_hash(
                artifact.source_path, fragment,
            ) != artifact.content_hash
        ):
            raise _fail("birth_ownership_deployment_invalid", "unit binding")
        fragments.append((unit_name, fragment))
    fragments.sort(key=lambda item: item[0].encode("utf-8"))
    return verified, descriptor, tuple(fragments)


def _signed_isolated_systemd_for_test_v1(
    record: object, *, environment: object, session: object,
    ownership_root: Path, unit_root: Path, account: _ServiceAccountV1,
    namespace: str,
    between_verifications: Callable[[], None] | None = None,
) -> _SignedIsolatedSystemdTestV1:
    """Mint a non-productive capability from one fully signed private cell."""
    _require_linux_v1()
    if (
        type(record)
        is not distribution_manifest._AuthenticatedDistributionRecordForTestV1
        or type(environment) is not distribution_manifest._VerificationEnvironment
        or environment._seal is not distribution_manifest._ENVIRONMENT_SEAL
        or type(account) is not _ServiceAccountV1
        or not isinstance(namespace, str)
        or _ISOLATED_NAMESPACE_RE_V1.fullmatch(namespace) is None
        or not Path(ownership_root).is_absolute()
        or not Path(unit_root).is_absolute()
        or between_verifications is not None
        and not callable(between_verifications)
    ):
        raise _fail("birth_ownership_deployment_invalid", "isolated authority")
    from executor_birth_ownership_coordinator import (
        _require_test_deployment_lock_session_v1,
    )

    ownership_root = Path(ownership_root)
    unit_root = Path(unit_root)
    require_session = lambda: _require_test_deployment_lock_session_v1(
        session, ownership_root,
    )
    verify = lambda: distribution_manifest._verify_authenticated_distribution_record_for_test(
        record, environment=environment,
    )
    require_session()
    verified_before, descriptor, fragments = _capture_isolated_cell_v1(
        record, environment=environment, account=account,
        namespace=namespace, verify=verify,
    )
    if between_verifications is not None:
        between_verifications()
    verified_after = verify()
    if verified_before != verified_after:
        raise _fail("birth_ownership_deployment_unsafe", "verification changed")
    require_session()
    values = (
        verified_after.identity.closed_build_id, descriptor.descriptor_id,
        namespace, str(unit_root), str(ownership_root), fragments,
    )
    return _SignedIsolatedSystemdTestV1(
        values[0], values[1], values[2], values[3], values[4], values[5],
        record, environment, account,
        _isolated_binding_v1(*values), _SIGNED_ISOLATED_SYSTEMD_TEST_SEAL_V1,
    )


def _verify_unit_file_v1(
    root_fd: int, name: str, *, content: bytes, owner: tuple[int, int],
) -> int:
    descriptor: int | None = None
    try:
        descriptor = os.open(name, _READ_FLAGS_V1, dir_fd=root_fd)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o644
            or (before.st_uid, before.st_gid) != owner
            or before.st_size != len(content)
        ):
            raise _fail("birth_ownership_recovery_required", "unit metadata")
        observed = bytearray()
        while len(observed) <= len(content):
            chunk = os.read(
                descriptor, min(65536, len(content) + 1 - len(observed)),
            )
            if not chunk:
                break
            observed.extend(chunk)
        after = os.fstat(descriptor)
        if (
            bytes(observed) != content
            or _stable_identity(_identity(before))
            != _stable_identity(_identity(after))
        ):
            raise _fail("birth_ownership_recovery_required", "unit content")
        result = descriptor
        descriptor = None
        return result
    except DistributionAssemblerError:
        raise
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "unit file") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _publish_isolated_units_for_test_v1(
    capability: _SignedIsolatedSystemdTestV1, *, owner: tuple[int, int],
    require_session: Callable[[], None],
) -> None:
    descriptors: tuple[int, ...] = ()
    staged: list[int] = []
    try:
        descriptors, parts = _open_absolute_directory_v1(capability.unit_root)
        _require_absolute_chain_bound_v1(
            descriptors, parts, detail="isolated unit root",
        )
        root_fd = descriptors[-1]
        root_info = _require_plain_directory_fd_v1(
            root_fd, owner=owner, mode=0o755,
        )
        rebound = Path(capability.unit_root).stat(follow_symlinks=False)
        if (root_info.st_dev, root_info.st_ino) != (
            rebound.st_dev, rebound.st_ino,
        ):
            raise _fail(
                "birth_ownership_recovery_required", "unit root binding",
            )
        names = tuple(name for name, _content in capability.unit_fragments)
        stage_names = tuple(
            _isolated_stage_name_v1(capability.descriptor_id, name)
            for name in names
        )
        if len(stage_names) != len(set(stage_names)):
            raise _fail("birth_ownership_deployment_invalid", "stage collision")
        require_session()
        if any(
            _name_status_v1(root_fd, name) is not None
            for name in names + stage_names
        ):
            raise _fail(
                "birth_ownership_recovery_required", "unit namespace occupied",
            )

        for (unit_name, content), stage_name in zip(
            capability.unit_fragments, stage_names, strict=True,
        ):
            require_session()
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    stage_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600, dir_fd=root_fd,
                )
                os.fchown(descriptor, *owner)
                _write_all_v1(descriptor, content)
                os.fchmod(descriptor, 0o644)
                os.fsync(descriptor)
            except OSError as exc:
                raise _fail(
                    "birth_ownership_recovery_required", "unit stage write",
                ) from exc
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            staged.append(_verify_unit_file_v1(
                root_fd, stage_name, content=content, owner=owner,
            ))
        os.fsync(root_fd)

        for ((unit_name, _content), stage_name, stage_fd) in zip(
            capability.unit_fragments, stage_names, staged, strict=True,
        ):
            require_session()
            try:
                _rename_no_replace_v1(
                    root_fd, stage_name, root_fd, unit_name,
                    expected_fd=stage_fd, sync_source_parent=False,
                )
            except FileExistsError as exc:
                raise _fail(
                    "birth_ownership_recovery_required", "unit publication",
                ) from exc
        os.fsync(root_fd)
        for unit_name, content in capability.unit_fragments:
            installed_fd = _verify_unit_file_v1(
                root_fd, unit_name, content=content, owner=owner,
            )
            os.close(installed_fd)
        require_session()
    except DistributionAssemblerError:
        raise
    except OSError as exc:
        raise _fail("birth_ownership_recovery_required", "unit root") from exc
    finally:
        for descriptor in reversed(staged):
            os.close(descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _install_signed_isolated_systemd_for_test_v1(
    capability: _SignedIsolatedSystemdTestV1, *, session: object,
    ownership_root: Path,
) -> _InstalledIsolatedSystemdTestV1:
    """Install exact signed names; this seam never starts or enables a unit."""
    _require_linux_v1()
    if (
        type(capability) is not _SignedIsolatedSystemdTestV1
        or capability._seal is not _SIGNED_ISOLATED_SYSTEMD_TEST_SEAL_V1
        or Path(ownership_root).is_absolute() is False
        or str(Path(ownership_root)) != capability.ownership_root
        or capability._capability_binding != _isolated_binding_v1(
            capability.closed_build_id, capability.descriptor_id,
            capability.namespace, capability.unit_root,
            capability.ownership_root, capability.unit_fragments,
        )
    ):
        raise _fail("birth_ownership_deployment_invalid", "isolated capability")
    from executor_birth_ownership_coordinator import (
        _require_test_deployment_lock_session_v1,
    )

    ownership_root = Path(ownership_root)
    require_session = lambda: _require_test_deployment_lock_session_v1(
        session, ownership_root,
    )
    verify = lambda: distribution_manifest._verify_authenticated_distribution_record_for_test(
        capability.record, environment=capability.environment,
    )
    require_session()
    verified, descriptor, fragments = _capture_isolated_cell_v1(
        capability.record, environment=capability.environment,
        account=capability.account, namespace=capability.namespace,
        verify=verify,
    )
    verified_again = verify()
    if (
        verified != verified_again
        or verified.identity.closed_build_id != capability.closed_build_id
        or descriptor.descriptor_id != capability.descriptor_id
        or fragments != capability.unit_fragments
    ):
        raise _fail("birth_ownership_deployment_unsafe", "capability changed")
    require_session()
    _publish_isolated_units_for_test_v1(
        capability, owner=(os.geteuid(), os.getegid()),
        require_session=require_session,
    )
    unit_names = tuple(name for name, _content in fragments)
    return _InstalledIsolatedSystemdTestV1(
        capability.closed_build_id, capability.descriptor_id,
        capability.namespace, capability.unit_root, unit_names,
        _binding_v1(
            capability.closed_build_id, capability.descriptor_id,
            capability.namespace, capability.unit_root, *unit_names,
        ),
        _INSTALLED_ISOLATED_SYSTEMD_TEST_SEAL_V1,
    )


__all__ = (
    "ADMINISTRATIVE_PROGRAM_BASENAME_V1",
    "ADMINISTRATIVE_PROGRAM_SOURCE_V1",
    "InstalledGroup6AdministrativeV1",
    "install_group6_administrative_v1",
)
