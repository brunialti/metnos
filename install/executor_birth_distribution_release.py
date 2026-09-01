#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Build and publish one signed release from a fixed received source.

The productive entry point accepts only a content-addressed source identity.
Every path, service recipe, release edge and signing authority is derived while
the fixed deployment lock is held.  Publication is no-replace and an exact
repetition returns the already verified release.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


_RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
if str(_RUNTIME) not in sys.path:  # pragma: no cover - installer bootstrap
    sys.path.insert(0, str(_RUNTIME))

from contract_boundary_guard import BIRTH_CLOSED_GUARD_VERSION
from executor_birth_distribution_assembler import (
    DEPLOYMENT_DESCRIPTOR_PATH_V1,
    DeploymentArtifactV1,
    DistributionAssemblerError,
    ReceivedSourceFileV1,
    ReceivedSourceV1,
    build_deployment_descriptor_v1,
    encode_deployment_descriptor_v1,
    received_source_file_hash_v1,
)
from executor_birth_distribution_installer import (
    _publish_core_v1, bundle_hash_v1, observe_staging_v1,
)
from executor_birth_distribution_manifest import (
    BOUNDARY_INVENTORY_DOMAIN,
    DEFAULT_RELEASE_DIRECTORY_V1,
    MAX_FILE_BYTES,
    DistributionFile,
    VerifiedDistribution,
    _ENVIRONMENT_SEAL,
    _VerificationEnvironment,
    _authenticate_distribution_record_from_fixed_snapshot_v1,
    _product_version_from_source,
    _runtime_environment,
    _verify_authenticated_distribution_record,
    authenticate_distribution_record_v1,
    build_distribution_manifest_v1,
    file_content_hash,
    verify_installed_distribution_record_v1,
)
from executor_birth_ownership_authorities import (
    DEFAULT_OWNERSHIP_ROOT_V1,
    _distribution_signing_key_id_v1,
    _load_distribution_signing_authority_v1,
    _load_fixed_ownership_public_snapshot_v1,
    _sign_distribution_payload_v1,
)
from executor_birth_ownership_coordinator import (
    COORDINATOR_DIRECTORY_BASENAME_V1,
    OwnershipCoordinatorStateV1,
    _deployment_lock_v1,
    _ensure_coordinator_child_directory_v2,
    _require_deployment_lock_session_v1,
    _resolve_ownership_coordinator_at_v2,
)
from executor_birth_service_catalog import (
    _build_service_catalog_v1, decode_service_catalog_v1,
)

from install.executor_birth_source_receiver import (
    INCOMING_DIRECTORY_BASENAME_V1,
    SOURCES_DIRECTORY_BASENAME_V1,
    _ServiceAccountV1,
    _load_received_source_with_product_session_v1,
    _service_account_snapshot_v1,
)


BOUNDARY_INVENTORY_SOURCE_PATH_V1 = (
    "internal/reports/rm0007-m4-boundary-inventory.json"
)
BOUNDARY_INVENTORY_RELEASE_PATH_V1 = (
    "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json"
)
DEPENDENCY_SOURCE_PATH_V1 = "requirements.txt"
DEPENDENCY_RELEASE_PATH_V1 = "requirements.lock"
ADMIN_PREFLIGHT_SOURCE_PATH_V1 = "runtime/executor_birth_admin_preflight.py"
ADMIN_PREFLIGHT_RELEASE_PATH_V1 = "deployment/admin/preflight.py"
LLAMA_SOURCE_PATH_V1 = "runtime/bin/llama-server"
PUBLICATION_INDEX_SOURCE_PATH_V1 = "docs/en/index.html"
TUTOR_SOURCES_SOURCE_PATH_V1 = "tutor/sources.toml"
_SOURCE_ROOTS_V1 = frozenset({
    "docs", "runtime", "install", "scripts", "executors", "tutor",
})
_EXCLUDED_SUFFIXES_V1 = (".pyc", ".pyo")
_OPENSSL_V1 = "/usr/bin/openssl"
_SYSTEMCTL_V1 = "/usr/bin/systemctl"
_SYSTEMD_ANALYZE_V1 = "/usr/bin/systemd-analyze"
_JAVA_V1 = "/usr/bin/java"
_XVFB_V1 = "/usr/bin/Xvfb"


@dataclass(frozen=True, slots=True)
class _ReleaseEdgeV1:
    sequence: int
    previous_closed_build_id: str | None


@dataclass(frozen=True, slots=True)
class _StagedReleaseV1:
    staging_root: Path
    final_root: Path
    encoded: bytes
    files: tuple[DistributionFile, ...]


def _fail(detail: str, *, recovery: bool = False) -> DistributionAssemblerError:
    return DistributionAssemblerError(
        (
            "birth_ownership_recovery_required"
            if recovery else "birth_ownership_deployment_invalid"
        ),
        detail,
    )


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _fail("boundary inventory", recovery=True) from exc


def _source_root_v1(ownership_root: Path, source_id: str) -> Path:
    return (
        ownership_root / INCOMING_DIRECTORY_BASENAME_V1
        / SOURCES_DIRECTORY_BASENAME_V1 / source_id
    )


def _stable_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
        info.st_uid, info.st_gid, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def _read_received_file_v1(
    root: Path, item: ReceivedSourceFileV1, *, root_owned: bool,
) -> bytes:
    """Read one descriptor-bound source file without following a leaf link."""
    owner = (0, 0) if root_owned else (os.geteuid(), os.getegid())
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise _fail("received source", recovery=True) from exc
    if (
        not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode)
        or (root_info.st_uid, root_info.st_gid) != owner
        or stat.S_IMODE(root_info.st_mode) != 0o755
    ):
        raise _fail("received source", recovery=True)
    current = root
    for component in item.path.split("/")[:-1]:
        current /= component
        try:
            info = current.lstat()
        except OSError as exc:
            raise _fail("received source", recovery=True) from exc
        if (
            not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or (info.st_uid, info.st_gid) != owner
            or stat.S_IMODE(info.st_mode) != 0o755
        ):
            raise _fail("received source", recovery=True)
    path = root.joinpath(*item.path.split("/"))
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _fail("received source", recovery=True) from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or (before.st_uid, before.st_gid) != owner
            or stat.S_IMODE(before.st_mode) != item.mode
            or before.st_size != item.size
        ):
            raise _fail("received source", recovery=True)
        chunks: list[bytes] = []
        total = 0
        while total <= item.size:
            chunk = os.read(descriptor, min(1 << 20, item.size + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    content = b"".join(chunks)
    if (
        total != item.size or _stable_identity(before) != _stable_identity(after)
        or received_source_file_hash_v1(
            item.path, item.size, (content,) if content else (),
        ) != item.content_hash
    ):
        raise _fail("received source", recovery=True)
    return content


def _projected_source_path_v1(path: str) -> str | None:
    parts = path.split("/")
    if (
        "__pycache__" in parts
        or path.casefold().endswith(_EXCLUDED_SUFFIXES_V1)
    ):
        return None
    if path == DEPENDENCY_SOURCE_PATH_V1:
        return DEPENDENCY_RELEASE_PATH_V1
    if parts[0] == "docs" and path.casefold().endswith(".py"):
        return None
    if parts[0] in _SOURCE_ROOTS_V1:
        return path
    return None


def _next_release_edge_v1(graph: object, source_id: str) -> _ReleaseEdgeV1:
    pending = graph.pending_claims
    if len(pending) > 1:
        raise _fail("successor edge", recovery=True)
    if pending:
        claim = pending[0]
        if claim.source_id != source_id:
            raise _fail("successor edge")
        previous = None
        if claim.release_sequence > 1:
            if not graph.transactions:
                raise _fail("successor edge", recovery=True)
            previous = graph.transactions[-1].latest.closed_build_id
        return _ReleaseEdgeV1(claim.release_sequence, previous)
    if graph.transactions:
        current = graph.transactions[-1]
        latest = current.latest
        if current.claim.source_id == source_id:
            return _ReleaseEdgeV1(
                current.claim.release_sequence,
                latest.previous_closed_build_id,
            )
        if (
            latest.state is not OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED
            or latest.sequence != 6
        ):
            raise _fail("successor edge")
        return _ReleaseEdgeV1(
            latest.release_sequence + 1, latest.closed_build_id,
        )
    if graph.claims:
        raise _fail("successor edge", recovery=True)
    return _ReleaseEdgeV1(1, None)


def _ensure_directory_v1(path: Path, *, root_owned: bool) -> None:
    owner = (0, 0) if root_owned else (os.geteuid(), os.getegid())
    try:
        path.mkdir(mode=0o755)
    except FileExistsError:
        pass
    except OSError as exc:
        raise _fail("release directory", recovery=True) from exc
    try:
        info = path.lstat()
    except OSError as exc:
        raise _fail("release directory", recovery=True) from exc
    if (
        not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or (info.st_uid, info.st_gid) != owner
        or stat.S_IMODE(info.st_mode) != 0o755
    ):
        raise _fail("release directory", recovery=True)


def _ensure_descendant_directory_v1(
    root: Path, relative: str, *, root_owned: bool,
) -> Path:
    current = root
    for component in PurePosixPath(relative).parts:
        current /= component
        _ensure_directory_v1(current, root_owned=root_owned)
    return current


def _write_exact_file_v1(
    root: Path, relative: str, content: bytes, mode: int, *, root_owned: bool,
) -> None:
    parent_text = PurePosixPath(relative).parent.as_posix()
    parent = root if parent_text == "." else _ensure_descendant_directory_v1(
        root, parent_text, root_owned=root_owned,
    )
    path = parent / PurePosixPath(relative).name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        descriptor = -1
    except OSError as exc:
        raise _fail("staged release", recovery=True) from exc
    if descriptor >= 0:
        try:
            view = memoryview(content)
            written = 0
            while written < len(view):
                written += os.write(descriptor, view[written:])
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        except OSError as exc:
            raise _fail("staged release", recovery=True) from exc
        finally:
            os.close(descriptor)
    owner = (0, 0) if root_owned else (os.geteuid(), os.getegid())
    try:
        before = path.lstat()
        observed = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise _fail("staged release", recovery=True) from exc
    if (
        not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)
        or before.st_nlink != 1 or (before.st_uid, before.st_gid) != owner
        or stat.S_IMODE(before.st_mode) != mode or observed != content
        or _stable_identity(before) != _stable_identity(after)
    ):
        raise _fail("staged release", recovery=True)


def _read_executable_v1(path: str) -> bytes:
    candidate = Path(path)
    try:
        before = candidate.stat()
        content = candidate.read_bytes()
        after = candidate.stat()
    except OSError as exc:
        raise _fail("target executable") from exc
    if (
        not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES
        or before.st_mode & 0o111 == 0
        or _stable_identity(before) != _stable_identity(after)
        or len(content) != before.st_size
    ):
        raise _fail("target executable")
    return content


def _release_file_role_v1(path: str) -> str:
    fixed = {
        ADMIN_PREFLIGHT_RELEASE_PATH_V1: "preflight",
        DEPLOYMENT_DESCRIPTOR_PATH_V1: "deployment_descriptor",
        "deployment/executor-birth-service-catalog-v1.json": "service_catalog",
        BOUNDARY_INVENTORY_RELEASE_PATH_V1: "boundary_inventory",
        DEPENDENCY_RELEASE_PATH_V1: "dependency_lock",
        "runtime/__version__.py": "product_version",
        "runtime/contract_boundary_guard.py": "boundary_guard",
        "runtime/executor_birth_distribution_manifest.py": "preflight",
        "runtime/executor_birth_ownership_preflight.py": "preflight",
    }
    if path.startswith("deployment/systemd/"):
        return "service_unit"
    if path.startswith("docs/"):
        return "public_document"
    if path.startswith("tutor/"):
        return "tutor_material"
    return fixed.get(path, "runtime_code")


def _deployment_artifacts_v1(
    catalog_bytes: bytes, content: dict[str, tuple[bytes, int]],
) -> tuple[DeploymentArtifactV1, ...]:
    catalog = decode_service_catalog_v1(catalog_bytes)
    kinds = {
        "gated_service": "service_unit",
        "gated_timer": "timer_unit",
        "stop_only": "stop_only_unit",
        "target": "target_unit",
    }
    artifacts = [DeploymentArtifactV1(
        ADMIN_PREFLIGHT_RELEASE_PATH_V1,
        "/usr/libexec/metnos/executor-birth-v1/preflight.py",
        "administrative_program", "group6_admin",
        len(content[ADMIN_PREFLIGHT_RELEASE_PATH_V1][0]),
        file_content_hash(
            ADMIN_PREFLIGHT_RELEASE_PATH_V1,
            content[ADMIN_PREFLIGHT_RELEASE_PATH_V1][0],
        ),
        0o755, 0, 0,
    )]
    for entry in catalog.entries:
        if entry.unit_spec is None:
            continue
        source = f"deployment/systemd/{entry.unit_name}"
        payload = content[source][0]
        try:
            kind = kinds[entry.class_name]
        except KeyError as exc:
            raise _fail("service unit kind") from exc
        artifacts.append(DeploymentArtifactV1(
            source, f"/etc/systemd/system/{entry.unit_name}", kind,
            "group7_cutover", len(payload), file_content_hash(source, payload),
            0o644, 0, 0,
        ))
    return tuple(artifacts)


def _assemble_staging_v1(
    *, source: ReceivedSourceV1, source_root: Path,
    account: _ServiceAccountV1, edge: _ReleaseEdgeV1, signing_key_id: str,
    release_directory: Path, root_owned: bool,
) -> _StagedReleaseV1:
    if type(source) is not ReceivedSourceV1 or type(account) is not _ServiceAccountV1:
        raise _fail("release inputs")
    if source.service_user != account.name:
        raise _fail("service account")
    final_root = release_directory / f"{edge.sequence:020d}"
    staging_root = release_directory / (
        f".release-{edge.sequence:020d}-"
        f"{source.source_id.removeprefix('sha256:')}.staging-v1"
    )
    _ensure_directory_v1(release_directory, root_owned=root_owned)
    _ensure_directory_v1(staging_root, root_owned=root_owned)

    by_source = {item.path: item for item in source.files}
    required = {
        BOUNDARY_INVENTORY_SOURCE_PATH_V1,
        DEPENDENCY_SOURCE_PATH_V1,
        ADMIN_PREFLIGHT_SOURCE_PATH_V1,
        LLAMA_SOURCE_PATH_V1,
        PUBLICATION_INDEX_SOURCE_PATH_V1,
        TUTOR_SOURCES_SOURCE_PATH_V1,
        "runtime/__version__.py",
    }
    if not required.issubset(by_source):
        raise _fail("received source incomplete")
    content: dict[str, tuple[bytes, int]] = {}
    for item in source.files:
        destination = _projected_source_path_v1(item.path)
        if destination is None:
            continue
        payload = _read_received_file_v1(
            source_root, item, root_owned=root_owned,
        )
        if destination in content:
            raise _fail("release path collision")
        content[destination] = (payload, item.mode)

    inventory_source = _read_received_file_v1(
        source_root, by_source[BOUNDARY_INVENTORY_SOURCE_PATH_V1],
        root_owned=root_owned,
    )
    try:
        inventory = json.loads(inventory_source.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("boundary inventory") from exc
    inventory_bytes = _canonical(inventory)
    preflight = content[ADMIN_PREFLIGHT_SOURCE_PATH_V1][0]
    generated = {
        ADMIN_PREFLIGHT_RELEASE_PATH_V1: (preflight, 0o644),
        BOUNDARY_INVENTORY_RELEASE_PATH_V1: (inventory_bytes, 0o644),
    }
    if set(generated) & set(content):
        raise _fail("release path collision")
    content.update(generated)

    python_executable = os.path.abspath(sys.executable)
    llama_target = f"{final_root.as_posix()}/{LLAMA_SOURCE_PATH_V1}"
    target_executables = tuple((path, payload) for path, payload in (
        (python_executable, _read_executable_v1(python_executable)),
        (_SYSTEMCTL_V1, _read_executable_v1(_SYSTEMCTL_V1)),
        (_JAVA_V1, _read_executable_v1(_JAVA_V1)),
        (_XVFB_V1, _read_executable_v1(_XVFB_V1)),
        (llama_target, content[LLAMA_SOURCE_PATH_V1][0]),
    ))
    built_catalog = _build_service_catalog_v1(
        installation_root=final_root.as_posix(),
        python_executable=python_executable, service_user=account.name,
        service_gid=account.gid,
        service_supplementary_gids=account.supplementary_gids,
        service_home=account.home, systemctl_executable=_SYSTEMCTL_V1,
        target_executables=target_executables,
    )
    catalog_path = "deployment/executor-birth-service-catalog-v1.json"
    content[catalog_path] = (built_catalog.encoded, 0o644)
    for unit_name, fragment in built_catalog.unit_fragments:
        content[f"deployment/systemd/{unit_name}"] = (fragment, 0o644)

    descriptor = build_deployment_descriptor_v1(
        release_sequence=edge.sequence, service_user=account.name,
        service_uid=account.uid, service_gid=account.gid,
        service_supplementary_gids=account.supplementary_gids,
        service_home=account.home, service_shell=account.shell,
        artifacts=_deployment_artifacts_v1(built_catalog.encoded, content),
        service_catalog_id=built_catalog.catalog_id,
        service_coverage_hash=built_catalog.service_coverage_hash,
        python_executable=python_executable, openssl_executable=_OPENSSL_V1,
        systemctl_executable=_SYSTEMCTL_V1,
        systemd_analyze_executable=_SYSTEMD_ANALYZE_V1,
    )
    content[DEPLOYMENT_DESCRIPTOR_PATH_V1] = (
        encode_deployment_descriptor_v1(descriptor), 0o644,
    )
    if len(content) > 4096:
        raise _fail("release file count")
    for relative, (payload, mode) in sorted(
        content.items(), key=lambda item: item[0].encode("utf-8"),
    ):
        _write_exact_file_v1(
            staging_root, relative, payload, mode, root_owned=root_owned,
        )
    files = tuple(DistributionFile(
        path, len(payload), file_content_hash(path, payload),
        _release_file_role_v1(path),
    ) for path, (payload, _mode) in sorted(
        content.items(), key=lambda item: item[0].encode("utf-8"),
    ))
    runtime_environment = _runtime_environment()
    product_version = _product_version_from_source(
        content["runtime/__version__.py"][0],
    )
    boundary_hash = "sha256:" + hashlib.sha256(
        BOUNDARY_INVENTORY_DOMAIN + inventory_bytes,
    ).hexdigest()
    encoded = build_distribution_manifest_v1(
        previous_closed_build_id=edge.previous_closed_build_id,
        release_sequence=edge.sequence, product_version=product_version,
        platform=runtime_environment.platform,
        architecture=runtime_environment.architecture,
        signing_key_id=signing_key_id,
        installation_root=final_root.as_posix(),
        boundary_inventory_path=BOUNDARY_INVENTORY_RELEASE_PATH_V1,
        boundary_inventory_hash=boundary_hash,
        boundary_guard_version=BIRTH_CLOSED_GUARD_VERSION, files=files,
    )
    return _StagedReleaseV1(
        staging_root, final_root, encoded, files,
    )


def _remove_repeated_staging_v1(staged: _StagedReleaseV1) -> None:
    """Remove only the exact temporary tree left by an idempotent repeat."""
    expected = {item.path for item in staged.files}
    observed = {item.relative_path for item in observe_staging_v1(staged.staging_root)}
    if observed != expected:
        raise _fail("staged release", recovery=True)
    paths = sorted(staged.staging_root.rglob("*"), key=lambda item: len(item.parts), reverse=True)
    for path in paths:
        if path.is_symlink():
            raise _fail("staged release", recovery=True)
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
    staged.staging_root.rmdir()


def build_and_install_received_source_v1(source_id: object) -> VerifiedDistribution:
    """Build, sign, preverify and publish one fixed received source."""
    if not sys.platform.startswith("linux"):
        raise _fail("platform")
    if os.geteuid() != 0:
        raise _fail("root required")
    if type(source_id) is not str:
        raise _fail("source id")
    with _deployment_lock_v1() as session:
        _require_deployment_lock_session_v1(session)
        source = _load_received_source_with_product_session_v1(
            source_id, session,
        )
        account = _service_account_snapshot_v1(source.service_user)
        coordinator, _created = _ensure_coordinator_child_directory_v2(
            DEFAULT_OWNERSHIP_ROOT_V1,
            COORDINATOR_DIRECTORY_BASENAME_V1, root_owned=True,
        )
        graph = _resolve_ownership_coordinator_at_v2(
            coordinator, root_owned=True,
        )
        edge = _next_release_edge_v1(graph, source.source_id)
        authority = _load_distribution_signing_authority_v1()
        key_id = _distribution_signing_key_id_v1(authority)
        staged = _assemble_staging_v1(
            source=source,
            source_root=_source_root_v1(
                DEFAULT_OWNERSHIP_ROOT_V1, source.source_id,
            ),
            account=account, edge=edge, signing_key_id=key_id,
            release_directory=DEFAULT_RELEASE_DIRECTORY_V1,
            root_owned=True,
        )
        signature = _sign_distribution_payload_v1(authority, staged.encoded)
        snapshot = _load_fixed_ownership_public_snapshot_v1()
        authenticated = _authenticate_distribution_record_from_fixed_snapshot_v1(
            staged.encoded, signature, snapshot,
        )
        environment = _VerificationEnvironment(
            authenticated.platform, authenticated.architecture,
            staged.staging_root, staged.final_root.as_posix(), True, True,
            _ENVIRONMENT_SEAL,
        )
        _verify_authenticated_distribution_record(
            authenticated, environment, for_test=False,
        )
        observed = observe_staging_v1(staged.staging_root)
        publication = _publish_core_v1(
            staged.staging_root, staged.final_root, bundle_hash_v1(observed),
        )
        if publication.repeated:
            _remove_repeated_staging_v1(staged)
        record = authenticate_distribution_record_v1(
            staged.encoded, signature,
        )
        verified = verify_installed_distribution_record_v1(record)
        if verified.encoded != staged.encoded or verified.signature != signature:
            raise _fail("published release", recovery=True)
        if _load_received_source_with_product_session_v1(
            source_id, session,
        ) != source or _service_account_snapshot_v1(account.name) != account:
            raise _fail("received source changed", recovery=True)
        _require_deployment_lock_session_v1(session)
        return verified


__all__ = ["build_and_install_received_source_v1"]
