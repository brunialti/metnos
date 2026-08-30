"""Focused portable proof for the first G6-C systemd installer increment."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from install import executor_birth_systemd as installer
import executor_birth_distribution_manifest as distribution
from contract_boundary_guard import (
    BIRTH_CLOSED_COORDINATOR_STORE_OWNERS,
    BIRTH_CLOSED_EXCEPTION_SCOPES,
    BIRTH_CLOSED_GUARD_VERSION,
    BIRTH_CLOSED_OWNER,
    BIRTH_CLOSED_SCHEMA,
    BIRTH_CLOSED_SEALED_MODULES,
    BIRTH_CLOSED_SOURCE_REVIEW_SHA256,
    SCAN_ROOTS,
    SCHEMA as BOUNDARY_INVENTORY_SCHEMA,
)
from executor_birth_distribution_assembler import (
    DEFAULT_ADMINISTRATIVE_ROOT_TEXT_V1,
    DEFAULT_SYSTEM_UNIT_ROOT_TEXT_V1,
    DeploymentArtifactV1,
    DistributionAssemblerError,
    build_deployment_descriptor_v1,
    encode_deployment_descriptor_v1,
)
from executor_birth_ownership_coordinator import _deployment_lock_for_test_v1
from install.executor_birth_source_receiver import _ServiceAccountV1


pytestmark = pytest.mark.skipif(
    not installer.sys.platform.startswith("linux"),
    reason="the G6-C filesystem transaction is Linux-only",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _inventory_bytes() -> bytes:
    return _canonical({
        "schema": BOUNDARY_INVENTORY_SCHEMA,
        "source_census": BIRTH_CLOSED_SOURCE_REVIEW_SHA256,
        "scan_roots": list(SCAN_ROOTS),
        "entries": [],
        "birth_closed": {
            "schema": BIRTH_CLOSED_SCHEMA,
            "guard_version": BIRTH_CLOSED_GUARD_VERSION,
            "owner": BIRTH_CLOSED_OWNER,
            "coordinator_store_owners": sorted(
                BIRTH_CLOSED_COORDINATOR_STORE_OWNERS
            ),
            "sealed_modules": list(BIRTH_CLOSED_SEALED_MODULES),
            "exceptions": [
                {"scope": scope, "exception": exception}
                for scope, exception
                in sorted(BIRTH_CLOSED_EXCEPTION_SCOPES.items())
            ],
        },
    })


@dataclass(frozen=True)
class _Fixture:
    release_root: Path
    ownership_root: Path
    administrative_root: Path
    preflight: bytes
    unit_source: Path
    descriptor: object
    record: object
    environment: object
    account: _ServiceAccountV1


def _fixture(tmp_path: Path) -> _Fixture:
    release_root = tmp_path / "release"
    ownership_root = tmp_path / "ownership"
    administrative_root = tmp_path / "admin" / "executor-birth-v1"
    preflight = b"#!/usr/bin/python3\nraise SystemExit(0)\n"
    unit = b"[Unit]\nDescription=isolated G6-C probe\n"
    unit_path = "deployment/systemd/metnos-g6c-probe.service"
    account = _ServiceAccountV1(
        "metnos", 12345, 12345, (12345,),
        "/var/lib/metnos", "/usr/sbin/nologin",
    )
    artifacts = (
        DeploymentArtifactV1(
            installer.ADMINISTRATIVE_PROGRAM_SOURCE_V1,
            DEFAULT_ADMINISTRATIVE_ROOT_TEXT_V1 + "/preflight.py",
            "administrative_program", "group6_admin", len(preflight),
            distribution.file_content_hash(
                installer.ADMINISTRATIVE_PROGRAM_SOURCE_V1, preflight,
            ),
            0o755, 0, 0,
        ),
        DeploymentArtifactV1(
            unit_path,
            DEFAULT_SYSTEM_UNIT_ROOT_TEXT_V1 + "/metnos-g6c-probe.service",
            "service_unit", "group7_cutover", len(unit),
            distribution.file_content_hash(unit_path, unit), 0o644, 0, 0,
        ),
    )
    descriptor = build_deployment_descriptor_v1(
        release_sequence=1, service_user=account.name,
        service_uid=account.uid, service_gid=account.gid,
        service_supplementary_gids=account.supplementary_gids,
        service_home=account.home, service_shell=account.shell,
        artifacts=artifacts,
        service_catalog_id="sha256:" + "1" * 64,
        service_coverage_hash="sha256:" + "2" * 64,
        python_executable="/usr/bin/python3",
        openssl_executable="/usr/bin/openssl",
        systemctl_executable="/usr/bin/systemctl",
        systemd_analyze_executable="/usr/bin/systemd-analyze",
    )
    descriptor_bytes = encode_deployment_descriptor_v1(descriptor)
    inventory = _inventory_bytes()
    values = {
        installer.ADMINISTRATIVE_PROGRAM_SOURCE_V1: ("preflight", preflight),
        "deployment/executor-birth-deployment-v1.json": (
            "deployment_descriptor", descriptor_bytes,
        ),
        "deployment/executor-birth-service-catalog-v1.json": (
            "service_catalog", b'{"schema_version":1}\n',
        ),
        unit_path: ("service_unit", unit),
        "requirements.lock": ("dependency_lock", b"cryptography==47.0.0\n"),
        "runtime/__version__.py": (
            "product_version", b'__version__ = "1.2.3"\n',
        ),
        "runtime/contract_boundary_guard.py": ("boundary_guard", b"GUARD = 1\n"),
        "runtime/contract_store.py": ("runtime_code", b"STORE = 1\n"),
        "runtime/executor_birth.py": ("runtime_code", b"BIRTH = 1\n"),
        "runtime/executor_birth_distribution_manifest.py": (
            "preflight", b"VERIFY = 1\n",
        ),
        "runtime/executor_birth_ownership_preflight.py": (
            "preflight", b"PREFLIGHT = 1\n",
        ),
        "runtime/sign.py": ("runtime_code", b"SIGN = 1\n"),
        "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json": (
            "boundary_inventory", inventory,
        ),
    }
    files = []
    for path, (role, content) in values.items():
        destination = release_root.joinpath(*path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        destination.chmod(0o644)
        files.append({
            "path": path, "size": len(content), "role": role,
            "content_hash": distribution.file_content_hash(path, content),
        })
    files.sort(key=lambda item: item["path"].encode("utf-8"))
    private = Ed25519PrivateKey.from_private_bytes(b"g" * 32)
    key_id = distribution.distribution_key_id(private.public_key())
    manifest = {
        "schema_version": 1,
        "closed_build_id": None,
        "previous_closed_build_id": None,
        "release_sequence": 1,
        "product_version": "1.2.3",
        "platform": "linux",
        "architecture": "x86_64",
        "signing_key_id": key_id,
        "installation_root": descriptor.installation_root,
        "certificate_directory": "/var/lib/metnos/executor-birth",
        "boundary_inventory_path": (
            "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json"
        ),
        "boundary_inventory_hash": "sha256:" + hashlib.sha256(
            distribution.BOUNDARY_INVENTORY_DOMAIN + inventory
        ).hexdigest(),
        "boundary_guard_version": BIRTH_CLOSED_GUARD_VERSION,
        "preflight_entrypoint": installer.ADMINISTRATIVE_PROGRAM_SOURCE_V1,
        "files": files,
    }
    manifest["closed_build_id"] = "sha256:" + hashlib.sha256(
        distribution.BUILD_ID_DOMAIN + _canonical({
            key: value for key, value in manifest.items()
            if key != "closed_build_id"
        })
    ).hexdigest()
    encoded = _canonical(manifest)
    signature = private.sign(distribution.SIGNATURE_DOMAIN + encoded)
    registry = distribution.DistributionRegistry({
        key_id: distribution.DistributionKey(
            key_id, private.public_key(), frozenset({distribution.PURPOSE}), 1,
        ),
    })
    record = distribution._authenticate_distribution_record_for_test(
        encoded, signature, registry=registry,
    )
    environment = distribution._environment_for_test(
        "linux", "x86_64", release_root,
        claimed_installation_root=descriptor.installation_root,
    )
    return _Fixture(
        release_root, ownership_root, administrative_root, preflight,
        release_root.joinpath(*unit_path.split("/")), descriptor, record,
        environment, account,
    )


def _install(fixture: _Fixture, session: object, **kwargs):
    return installer._install_group6_administrative_for_test_v1(
        fixture.record, environment=fixture.environment, session=session,
        ownership_root=fixture.ownership_root,
        administrative_root=fixture.administrative_root,
        account=fixture.account, **kwargs,
    )


def test_install_is_byte_identical_idempotent_and_defers_every_unit(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    with _deployment_lock_for_test_v1(fixture.ownership_root) as session:
        first = _install(fixture, session)
        second = _install(fixture, session)

    installed = fixture.administrative_root / "preflight.py"
    assert first == second
    assert type(first) is installer._InstalledGroup6AdministrativeForTestV1
    assert installed.read_bytes() == fixture.preflight
    assert stat.S_IMODE(installed.stat().st_mode) == 0o755
    assert tuple(item.name for item in fixture.administrative_root.iterdir()) == (
        "preflight.py",
    )
    assert not (fixture.administrative_root / fixture.unit_source.name).exists()
    assert not any(
        item.name.startswith(installer._STAGING_PREFIX_V1)
        for item in fixture.administrative_root.parent.iterdir()
    )


@pytest.mark.parametrize("existing", ["partial-stage", "unsafe-final"])
def test_partial_or_unsafe_namespace_requires_explicit_recovery(
    tmp_path: Path, existing: str,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.administrative_root.parent.mkdir(mode=0o755)
    fixture.administrative_root.parent.chmod(0o755)
    if existing == "partial-stage":
        target = fixture.administrative_root.parent / (
            installer._STAGING_PREFIX_V1
            + fixture.descriptor.descriptor_id.removeprefix("sha256:")
            + installer._STAGING_SUFFIX_V1
        )
        target.mkdir(mode=0o755)
    else:
        target = fixture.administrative_root
        target.mkdir(mode=0o755)
        (target / "preflight.py").write_bytes(fixture.preflight)
        (target / "preflight.py").chmod(0o644)

    with _deployment_lock_for_test_v1(fixture.ownership_root) as session:
        with pytest.raises(DistributionAssemblerError) as caught:
            _install(fixture, session)
    assert caught.value.code == "birth_ownership_recovery_required"
    if existing == "partial-stage":
        assert not fixture.administrative_root.exists()


def test_complete_bound_stage_is_promoted_without_rewriting(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.administrative_root.parent.mkdir(mode=0o755)
    fixture.administrative_root.parent.chmod(0o755)
    stage = fixture.administrative_root.parent / (
        installer._STAGING_PREFIX_V1
        + fixture.descriptor.descriptor_id.removeprefix("sha256:")
        + installer._STAGING_SUFFIX_V1
    )
    stage.mkdir(mode=0o755)
    staged = stage / "preflight.py"
    staged.write_bytes(fixture.preflight)
    staged.chmod(0o755)
    before = staged.stat()

    with _deployment_lock_for_test_v1(fixture.ownership_root) as session:
        _install(fixture, session)

    installed = fixture.administrative_root / "preflight.py"
    after = installed.stat()
    assert not stage.exists()
    assert installed.read_bytes() == fixture.preflight
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)


def test_second_full_verification_blocks_changed_signed_source_before_install(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    source = fixture.release_root / installer.ADMINISTRATIVE_PROGRAM_SOURCE_V1

    def mutate() -> None:
        source.write_bytes(b"#!/usr/bin/python3\nraise SystemExit(9)\n")
        source.chmod(0o644)

    with _deployment_lock_for_test_v1(fixture.ownership_root) as session:
        with pytest.raises(distribution.DistributionManifestError) as caught:
            _install(fixture, session, between_verifications=mutate)
    assert caught.value.code == "birth_ownership_distribution_file_mismatch"
    assert not fixture.administrative_root.exists()


def test_product_and_platform_boundaries_refuse_before_administrative_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0)
    with pytest.raises(DistributionAssemblerError) as product:
        installer.install_group6_administrative_v1(fixture.record, object())
    assert product.value.code == "birth_ownership_distribution_invalid"
    assert not fixture.administrative_root.exists()

    with pytest.raises(DistributionAssemblerError) as test_authority:
        installer._install_group6_administrative_for_test_v1(
            fixture.record, environment=object(), session=object(),
            ownership_root=fixture.ownership_root,
            administrative_root=fixture.administrative_root,
            account=fixture.account,
        )
    assert test_authority.value.code == "birth_ownership_deployment_invalid"
    assert not fixture.ownership_root.exists()

    monkeypatch.setattr(installer.sys, "platform", "win32")
    with pytest.raises(DistributionAssemblerError) as platform:
        installer._install_group6_administrative_for_test_v1(
            fixture.record, environment=fixture.environment, session=object(),
            ownership_root=fixture.ownership_root,
            administrative_root=fixture.administrative_root,
            account=fixture.account,
        )
    assert platform.value.code == "birth_ownership_platform_unsupported"
    assert not fixture.administrative_root.exists()
