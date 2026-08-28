"""Adversarial portable certification of the RM-0008 distribution manifest."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_distribution_manifest as distribution
import executor_birth_ownership_preflight as preflight
from executor_birth_cutover import CurrentReceiptProof
from contract_boundary_guard import (
    BIRTH_CLOSED_COORDINATOR_STORE_OWNERS, BIRTH_CLOSED_EXCEPTION_SCOPES,
    BIRTH_CLOSED_GUARD_VERSION,
    BIRTH_CLOSED_OWNER, BIRTH_CLOSED_SCHEMA, BIRTH_CLOSED_SEALED_MODULES,
    SCAN_ROOTS, SCHEMA as BOUNDARY_INVENTORY_SCHEMA,
)


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _authority(*purposes: str, first: int = 1, last: int | None = None):
    private = Ed25519PrivateKey.from_private_bytes(b"d" * 32)
    key_id = distribution.distribution_key_id(private.public_key())
    registry = distribution.DistributionRegistry({
        key_id: distribution.DistributionKey(
            key_id, private.public_key(), frozenset(purposes), first, last,
        ),
    })
    return private, key_id, registry


def _fixed_public_bundle(distribution_private):
    import executor_birth_ownership_authorities as authority_module

    return authority_module._root_ownership_authorities_for_test(
        distribution_private, Ed25519PrivateKey.generate(),
        Ed25519PrivateKey.generate(),
    ).public


def _test_environment(root: Path):
    return distribution._environment_for_test(
        "windows" if os.name == "nt" else "linux", "x86_64", root,
    )


def _inventory_bytes():
    return _canonical({
        "schema": BOUNDARY_INVENTORY_SCHEMA,
        "source_census": "signed-release",
        "scan_roots": list(SCAN_ROOTS),
        "entries": [],
        "birth_closed": {
            "schema": BIRTH_CLOSED_SCHEMA,
            "guard_version": BIRTH_CLOSED_GUARD_VERSION,
            "owner": BIRTH_CLOSED_OWNER,
            "coordinator_store_owners": sorted(BIRTH_CLOSED_COORDINATOR_STORE_OWNERS),
            "sealed_modules": list(BIRTH_CLOSED_SEALED_MODULES),
            "exceptions": [
                {"scope": scope, "exception": exception}
                for scope, exception in sorted(BIRTH_CLOSED_EXCEPTION_SCOPES.items())
            ],
        },
    })


def _files(root: Path):
    inventory = _inventory_bytes()
    values = {
        "requirements.lock": ("dependency_lock", b"cryptography==47.0.0\n"),
        "runtime/__version__.py": ("product_version", b'__version__ = "1.2.3"\n'),
        "runtime/contract_boundary_guard.py": ("boundary_guard", b"GUARD = 1\n"),
        "runtime/contract_store.py": ("runtime_code", b"STORE = 1\n"),
        "runtime/executor_birth.py": ("runtime_code", b"BIRTH = 1\n"),
        "runtime/executor_birth_distribution_manifest.py": ("preflight", b"VERIFY = 1\n"),
        "runtime/executor_birth_ownership_preflight.py": ("preflight", b"PREFLIGHT = 1\n"),
        "runtime/sign.py": ("runtime_code", b"SIGN = 1\n"),
        "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json": (
            "boundary_inventory", inventory,
        ),
        "systemd/metnos-http-birth-closed.conf": ("service_unit", b"[Service]\n"),
    }
    result = []
    for path, (role, content) in values.items():
        destination = root.joinpath(*path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        if os.name != "nt":
            destination.chmod(0o644)
        result.append({
            "path": path, "size": len(content), "role": role,
            "content_hash": distribution.file_content_hash(path, content),
        })
    return sorted(result, key=lambda item: item["path"].encode("utf-8"))


def _replace_declared_file(files, root: Path, path: str, content: bytes):
    root.joinpath(*path.split("/")).write_bytes(content)
    item = next(value for value in files if value["path"] == path)
    item["size"] = len(content)
    item["content_hash"] = distribution.file_content_hash(path, content)


def _manifest(root: Path, private, key_id, *, target=None, architecture="x86_64",
              mutate=None, files_mutate=None):
    target = target or ("windows" if os.name == "nt" else "linux")
    files = _files(root)
    if files_mutate:
        files_mutate(files, root)
    value = {
        "schema_version": 1,
        "closed_build_id": None,
        "previous_closed_build_id": None,
        "release_sequence": 1,
        "product_version": "1.2.3",
        "platform": target,
        "architecture": architecture,
        "signing_key_id": key_id,
        "installation_root": str(root),
        "certificate_directory": (
            r"C:\ProgramData\Metnos\ExecutorBirth"
            if target == "windows" else "/var/lib/metnos/executor-birth"
        ),
        "boundary_inventory_path": (
            "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json"
        ),
        "boundary_inventory_hash": "sha256:" + hashlib.sha256(
            distribution.BOUNDARY_INVENTORY_DOMAIN + _inventory_bytes()
        ).hexdigest(),
        "boundary_guard_version": "metnos.contract-boundary-inventory/2+birth-closed/1",
        "preflight_entrypoint": "runtime/executor_birth_distribution_manifest.py",
        "files": files,
    }
    if mutate:
        mutate(value)
    value["closed_build_id"] = "sha256:" + hashlib.sha256(
        distribution.BUILD_ID_DOMAIN + _canonical({
            key: item for key, item in value.items() if key != "closed_build_id"
        })
    ).hexdigest()
    encoded = _canonical(value)
    return value, encoded, private.sign(distribution.SIGNATURE_DOMAIN + encoded)


def _verify(tmp_path, *, purposes=(distribution.PURPOSE,), mutate=None,
            environment=None, first=1, last=None):
    private, key_id, registry = _authority(*purposes, first=first, last=last)
    value, encoded, signature = _manifest(tmp_path, private, key_id, mutate=mutate)
    environment = environment or distribution._environment_for_test(
        "windows" if os.name == "nt" else "linux", "x86_64", tmp_path,
    )
    result = distribution._verify_distribution_manifest_for_test(
        encoded, signature, registry=registry, _environment=environment,
    )
    return value, encoded, signature, registry, result


def test_signed_manifest_produces_only_sealed_preflight_identity(tmp_path):
    value, encoded, _signature, _registry, result = _verify(tmp_path)
    assert result.identity.closed_build_id == value["closed_build_id"]
    assert result.identity.boundary_inventory_hash == value["boundary_inventory_hash"]
    assert result.release_sequence == 1
    assert encoded == _canonical(json.loads(encoded))
    assert result.encoded == encoded
    assert result.signature == _signature
    with pytest.raises(FrozenInstanceError):
        result.encoded = b"replacement"


@pytest.mark.skipif(os.name == "nt", reason="productive release root is Linux-only")
def test_historical_record_is_nominally_separate_and_live_root_is_derived(
    tmp_path, monkeypatch,
):
    private, key_id, registry = _authority(distribution.PURPOSE)
    releases = tmp_path / "releases-v1"
    exact_root = releases / "00000000000000000001"
    _value, encoded, signature = _manifest(
        exact_root, private, key_id,
    )
    test_record = distribution._authenticate_distribution_record_for_test(
        encoded, signature, registry=registry,
    )
    with pytest.raises(distribution.DistributionManifestError):
        distribution.verify_installed_distribution_record_v1(test_record)

    import executor_birth_ownership_authorities as authority_module

    authority_bundle = _fixed_public_bundle(private)
    monkeypatch.setattr(
        authority_module, "load_ownership_public_registries_v1",
        lambda: authority_bundle,
    )
    productive_record = distribution.authenticate_distribution_record_v1(
        encoded, signature,
    )
    observed = []
    sentinel = object()

    def capture(record, environment, *, for_test):
        observed.append((record, environment.installation_root, for_test))
        return sentinel

    monkeypatch.setattr(distribution, "DEFAULT_RELEASE_DIRECTORY_V1", releases)
    monkeypatch.setattr(
        distribution, "_require_product_release_metadata_v1", lambda _root: None,
    )
    monkeypatch.setattr(
        distribution, "_verify_authenticated_distribution_record", capture,
    )
    assert distribution.verify_installed_distribution_record_v1(
        productive_record
    ) is sentinel
    assert observed == [(productive_record, exact_root, False)]

    wrong_root = releases / "00000000000000000001-extra"
    _value, wrong_encoded, wrong_signature = _manifest(
        wrong_root, private, key_id,
    )
    wrong_record = distribution.authenticate_distribution_record_v1(
        wrong_encoded, wrong_signature,
    )
    with pytest.raises(distribution.DistributionManifestError, match="root"):
        distribution.verify_installed_distribution_record_v1(wrong_record)


def test_current_installation_product_verifier_uses_fixed_trust_and_rereads_files(
    tmp_path, monkeypatch,
):
    private, key_id, _registry = _authority(distribution.PURPOSE)
    _value, encoded, signature = _manifest(tmp_path, private, key_id)
    import executor_birth_ownership_authorities as authority_module

    monkeypatch.setattr(
        authority_module, "load_ownership_public_registries_v1",
        lambda: _fixed_public_bundle(private),
    )
    monkeypatch.setattr(
        distribution, "_runtime_environment",
        lambda: _test_environment(tmp_path),
    )
    original_lstat = Path.lstat
    trusted_directories = {tmp_path, *tmp_path.parents}

    def root_owned_ancestor_lstat(path):
        if Path(path) in trusted_directories:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0,
                st_file_attributes=0,
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", root_owned_ancestor_lstat)
    verified = distribution.verify_current_installation_distribution_v1(
        encoded, signature,
    )
    assert verified.encoded == encoded
    (tmp_path / "runtime" / "sign.py").write_bytes(b"tampered")
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        distribution.verify_current_installation_distribution_v1(
            encoded, signature,
        )


@pytest.mark.skipif(os.name == "nt", reason="productive ancestor metadata is POSIX")
def test_current_installation_rejects_writable_parent_before_file_verification(
    tmp_path, monkeypatch,
):
    private, key_id, _registry = _authority(distribution.PURPOSE)
    installation_root = tmp_path / "relocated" / "metnos"
    _value, encoded, signature = _manifest(
        installation_root, private, key_id,
    )
    import executor_birth_ownership_authorities as authority_module

    monkeypatch.setattr(
        authority_module, "load_ownership_public_registries_v1",
        lambda: _fixed_public_bundle(private),
    )
    monkeypatch.setattr(
        distribution, "_runtime_environment",
        lambda: distribution._environment_for_test(
            "linux", "x86_64", installation_root,
        ),
    )
    unsafe_parent = installation_root.parent

    def synthetic_lstat(path):
        return SimpleNamespace(
            st_mode=stat.S_IFDIR | (
                0o775 if Path(path) == unsafe_parent else 0o755
            ),
            st_uid=0, st_gid=0, st_file_attributes=0,
        )

    monkeypatch.setattr(Path, "lstat", synthetic_lstat)
    monkeypatch.setattr(
        distribution, "_verify_authenticated_distribution_record",
        lambda *_args, **_kwargs: pytest.fail(
            "file verification reached after unsafe current-install parent"
        ),
    )
    with pytest.raises(
        distribution.DistributionManifestError, match="release metadata",
    ):
        distribution.verify_current_installation_distribution_v1(
            encoded, signature,
        )


@pytest.mark.skipif(os.name == "nt", reason="productive release metadata is POSIX")
@pytest.mark.parametrize("mutation", ("ancestor_link", "ancestor_owner", "ancestor_mode"))
def test_product_release_rejects_unsafe_ancestor_before_file_verification(
    tmp_path, monkeypatch, mutation,
):
    private, key_id, _registry = _authority(distribution.PURPOSE)
    releases = tmp_path / "releases-v1"
    exact_root = releases / "00000000000000000001"
    _value, encoded, signature = _manifest(exact_root, private, key_id)
    import executor_birth_ownership_authorities as authority_module

    monkeypatch.setattr(
        authority_module, "load_ownership_public_registries_v1",
        lambda: _fixed_public_bundle(private),
    )
    record = distribution.authenticate_distribution_record_v1(encoded, signature)
    monkeypatch.setattr(distribution, "DEFAULT_RELEASE_DIRECTORY_V1", releases)
    unsafe_ancestor = releases

    def synthetic_lstat(path):
        mode = stat.S_IFDIR | 0o755
        uid = gid = 0
        if Path(path) == unsafe_ancestor:
            if mutation == "ancestor_link":
                mode = stat.S_IFLNK | 0o777
            elif mutation == "ancestor_owner":
                uid = 1000
            else:
                mode = stat.S_IFDIR | 0o775
        return SimpleNamespace(
            st_mode=mode, st_uid=uid, st_gid=gid, st_file_attributes=0,
        )

    monkeypatch.setattr(Path, "lstat", synthetic_lstat)
    monkeypatch.setattr(
        distribution, "_verify_authenticated_distribution_record",
        lambda *_args, **_kwargs: pytest.fail(
            "file verification reached after unsafe release metadata"
        ),
    )
    with pytest.raises(
        distribution.DistributionManifestError, match="release metadata",
    ):
        distribution.verify_installed_distribution_record_v1(record)


@pytest.mark.parametrize("source", [
    b'__version__ = "9.9.9"\n',
    b'def version(): return "1.2.3"\n__version__ = version()\n',
    b'__version__ = "1.2.3"\n__version__ = "1.2.3"\n',
])
def test_product_version_must_equal_single_literal_in_signed_source(tmp_path, source):
    private, key_id, registry = _authority(distribution.PURPOSE)
    _value, encoded, signature = _manifest(
        tmp_path, private, key_id,
        files_mutate=lambda files, root: _replace_declared_file(
            files, root, "runtime/__version__.py", source,
        ),
    )
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry, _environment=_test_environment(tmp_path),
        )


def test_guard_version_inventory_policy_and_full_static_gate_are_fail_closed(tmp_path):
    private, key_id, registry = _authority(distribution.PURPOSE)
    _value, encoded, signature = _manifest(
        tmp_path, private, key_id,
        mutate=lambda value: value.update(boundary_guard_version="caller-guard/1"),
    )
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry, _environment=_test_environment(tmp_path),
        )

    malformed = _canonical({"schema": BOUNDARY_INVENTORY_SCHEMA, "entries": []})
    _value, encoded, signature = _manifest(
        tmp_path, private, key_id,
        files_mutate=lambda files, root: _replace_declared_file(
            files, root,
            "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json",
            malformed,
        ),
        mutate=lambda value: value.update(boundary_inventory_hash=
            "sha256:" + hashlib.sha256(
                distribution.BOUNDARY_INVENTORY_DOMAIN + malformed
            ).hexdigest()),
    )
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry, _environment=_test_environment(tmp_path),
        )

    # Structural fixture is valid, but cannot counterfeit the compiled census.
    _value, encoded, signature = _manifest(tmp_path, private, key_id)
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry,
            _environment=distribution._environment_for_test(
                "windows" if os.name == "nt" else "linux", "x86_64", tmp_path,
                verify_static_boundary=True,
            ),
        )


def test_uncovered_local_and_dynamic_imports_fail_closed(tmp_path):
    private, key_id, registry = _authority(distribution.PURPOSE)
    for source, local in ((b"import local_secret\n", True),
                          (b'import importlib\nimportlib.import_module("json")\n', False)):
        def mutate_files(files, root, source=source, local=local):
            _replace_declared_file(files, root, "runtime/executor_birth.py", source)
            if local:
                (root / "runtime" / "local_secret.py").write_bytes(b"SECRET = 1\n")
        _value, encoded, signature = _manifest(
            tmp_path, private, key_id, files_mutate=mutate_files,
        )
        with pytest.raises(distribution.DistributionManifestError, match="extra_file"):
            distribution._verify_distribution_manifest_for_test(
                encoded, signature, registry=registry,
                _environment=_test_environment(tmp_path),
            )
        try:
            (tmp_path / "runtime" / "local_secret.py").unlink()
        except FileNotFoundError:
            pass


def test_verified_identity_is_consumed_by_existing_startup_preflight(tmp_path, monkeypatch):
    value, _encoded, _signature, _registry, result = _verify(tmp_path)
    certificate = SimpleNamespace(
        cutover_id="sha256:" + "1" * 64,
        closed_build_id=result.identity.closed_build_id,
        boundary_inventory_hash=result.identity.boundary_inventory_hash,
        boundary_guard_version=result.identity.boundary_guard_version,
        catalog_id="sha256:" + "2" * 64,
        current_count=0,
    )
    monkeypatch.setattr(preflight, "DEFAULT_CERTIFICATE_DIRECTORY", tmp_path)
    monkeypatch.setattr(preflight, "verify_root_owned_certificate_directory", lambda _path: None)
    monkeypatch.setattr(preflight, "read_ownership_cutover_certificate", lambda *args, **kwargs: certificate)
    proof = CurrentReceiptProof((), {})
    attestation = preflight.preflight_closed_build(
        tmp_path, registry=object(), authenticated_build=result.identity,
        expected_current=proof,
    )
    assert attestation.closed_build_id == value["closed_build_id"]


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(extra=True),
    lambda value: value.update(release_sequence=True),
    lambda value: value.update(product_version="01.2.3"),
    lambda value: value["files"][0].update(extra=True),
    lambda value: value["files"].append(dict(value["files"][0])),
    lambda value: value["files"].reverse(),
    lambda value: value["files"][0].update(path="../requirements.lock"),
    lambda value: value["files"][0].update(path="runtime\\escape.py"),
    lambda value: value["files"][0].update(path="de\u0301pendency.lock"),
])
def test_closed_schema_numbers_order_duplicates_and_paths_fail(tmp_path, mutation):
    private, key_id, registry = _authority(distribution.PURPOSE)
    _value, encoded, signature = _manifest(tmp_path, private, key_id, mutate=mutation)
    with pytest.raises(distribution.DistributionManifestError):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry,
            _environment=_test_environment(tmp_path),
        )


def test_duplicate_json_noncanonical_and_signature_tamper_fail(tmp_path):
    _value, encoded, signature, registry, _result = _verify(tmp_path)
    duplicate = encoded[:-1] + b',"schema_version":1}'
    for payload, proof in ((encoded + b"\n", signature), (duplicate, signature),
                           (encoded, b"x" * 64)):
        with pytest.raises(distribution.DistributionManifestError):
            distribution._verify_distribution_manifest_for_test(
                payload, proof, registry=registry,
                _environment=_test_environment(tmp_path),
            )


def test_wrong_purpose_and_key_epoch_are_unauthorized(tmp_path):
    for purposes, first, last in (({"ownership_cutover_v1"}, 1, None),
                                  ({distribution.PURPOSE, "ownership_cutover_v1"}, 1, None),
                                  ({distribution.PURPOSE}, 2, None),
                                  ({distribution.PURPOSE}, 1, 0)):
        if last == 0:
            with pytest.raises(distribution.DistributionManifestError):
                _authority(*purposes, first=first, last=last)
            continue
        private, key_id, registry = _authority(*purposes, first=first, last=last)
        _value, encoded, signature = _manifest(tmp_path, private, key_id)
        with pytest.raises(distribution.DistributionManifestError, match="key_unauthorized"):
            distribution._verify_distribution_manifest_for_test(
                encoded, signature, registry=registry,
                _environment=_test_environment(tmp_path),
            )


@pytest.mark.skipif(os.name == "nt", reason="POSIX link adversarial case")
def test_file_tamper_missing_symlink_hardlink_and_extra_birth_module_fail(tmp_path):
    value, encoded, signature, registry, _result = _verify(tmp_path)
    environment = _test_environment(tmp_path)
    target = tmp_path / "runtime" / "sign.py"
    target.write_bytes(b"changed")
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry, _environment=environment,
        )
    target.unlink()
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry, _environment=environment,
        )

    # Regenerate an intact release for link and extra-file checks.
    _value, encoded, signature = _manifest(tmp_path, _authority(distribution.PURPOSE)[0],
                                           value["signing_key_id"])
    # The preceding private key is deterministic, so it matches the registry.
    target = tmp_path / "runtime" / "sign.py"
    backup = tmp_path / "sign-copy.py"
    backup.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(backup)
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry, _environment=environment,
        )
    target.unlink()
    target.write_bytes(backup.read_bytes())
    os.link(target, tmp_path / "sign-hardlink.py")
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry, _environment=environment,
        )
    (tmp_path / "sign-hardlink.py").unlink()
    (tmp_path / "runtime" / "executor_birth_shadow_authority.py").write_bytes(b"x")
    with pytest.raises(distribution.DistributionManifestError, match="extra_file"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry, _environment=environment,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX parent-link adversarial case")
def test_inventory_domain_binding_and_parent_symlink_fail(tmp_path):
    value, encoded, signature, registry, _result = _verify(tmp_path)
    environment = _test_environment(tmp_path)
    inventory = tmp_path.joinpath(*value["boundary_inventory_path"].split("/"))
    inventory.write_bytes(b'{"changed":true}')
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry, _environment=environment,
        )

    # A symlink in an ancestor is rejected even when the leaf itself is regular.
    _value, encoded, signature = _manifest(tmp_path, _authority(distribution.PURPOSE)[0],
                                           value["signing_key_id"])
    real_share = tmp_path / "real-share"
    (tmp_path / "share").rename(real_share)
    (tmp_path / "share").symlink_to(real_share, target_is_directory=True)
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry, _environment=environment,
        )


@pytest.mark.parametrize("axis", ["platform", "architecture"])
def test_platform_and_architecture_mismatch_fail_before_authority_is_returned(
    tmp_path, axis,
):
    private, key_id, registry = _authority(distribution.PURPOSE)
    _value, encoded, signature = _manifest(tmp_path, private, key_id)
    native = "windows" if os.name == "nt" else "linux"
    platform = ("linux" if native == "windows" else "windows") if axis == "platform" else native
    architecture = "aarch64" if axis == "architecture" else "x86_64"
    with pytest.raises(distribution.DistributionManifestError, match="platform_mismatch"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry,
            _environment=distribution._environment_for_test(
                platform, architecture, tmp_path,
            ),
        )


@pytest.mark.parametrize("path", [
    r"\\server\share\metnos", r"\\?\C:\Metnos", r"C:\Metnos:stream",
    r"C:\Metnos\..\Other", r"C:/Metnos",
])
def test_windows_parser_rejects_unc_devices_ads_traversal_and_slashes(tmp_path, path):
    private, key_id, _registry = _authority(distribution.PURPOSE)
    _value, encoded, _signature = _manifest(
        tmp_path, private, key_id, target="windows", mutate=lambda value: value.update(
            installation_root=path, certificate_directory=r"C:\ProgramData\Metnos\Birth",
        ),
    )
    with pytest.raises(distribution.DistributionManifestError):
        distribution._parse(encoded)


def test_windows_parser_accepts_normal_drive_absolute_paths(tmp_path):
    private, key_id, _registry = _authority(distribution.PURPOSE)
    value, encoded, _signature = _manifest(
        tmp_path, private, key_id, target="windows",
        mutate=lambda item: item.update(
            installation_root=r"C:\Metnos",
            certificate_directory=r"C:\ProgramData\Metnos\ExecutorBirth",
        ),
    )
    parsed, files = distribution._parse(encoded)
    assert parsed["installation_root"] == r"C:\Metnos"
    assert files


def test_windows_verification_dispatches_to_certified_handle_reader(tmp_path, monkeypatch):
    private, key_id, registry = _authority(distribution.PURPOSE)
    claimed = r"C:\Metnos"
    _value, encoded, signature = _manifest(
        tmp_path, private, key_id, target="windows",
        mutate=lambda value: value.update(installation_root=claimed),
    )
    observed = []

    def certified_read(root, item):
        observed.append(item.path)
        return root.joinpath(*item.path.split("/")).read_bytes()

    monkeypatch.setattr(distribution, "_secure_read_windows", certified_read)
    result = distribution._verify_distribution_manifest_for_test(
        encoded, signature, registry=registry,
        _environment=distribution._environment_for_test(
            "windows", "x86_64", tmp_path,
            claimed_installation_root=claimed,
        ),
    )
    assert observed == [item.path for item in result.files]


@pytest.mark.skipif(os.name != "nt", reason="real Win32 handle enforcement")
def test_windows_real_handle_reader_rejects_hardlinked_release_file(tmp_path):
    value, encoded, signature, registry, _result = _verify(tmp_path)
    target = tmp_path / "runtime" / "sign.py"
    os.link(target, tmp_path / "sign-second-name.py")
    with pytest.raises(distribution.DistributionManifestError, match="file_mismatch"):
        distribution._verify_distribution_manifest_for_test(
            encoded, signature, registry=registry, _environment=_test_environment(tmp_path),
        )


def test_previous_build_is_null_only_for_first_sequence(tmp_path):
    private, key_id, registry = _authority(distribution.PURPOSE)
    for mutation in (
        lambda value: value.update(release_sequence=2),
        lambda value: value.update(previous_closed_build_id="sha256:" + "9" * 64),
    ):
        _value, encoded, signature = _manifest(tmp_path, private, key_id, mutate=mutation)
        with pytest.raises(distribution.DistributionManifestError, match="chain_invalid"):
            distribution._verify_distribution_manifest_for_test(
                encoded, signature, registry=registry,
                _environment=_test_environment(tmp_path),
            )
