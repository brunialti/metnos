"""N is read as signed history; N+1 retains its own compiled admission policy."""
from __future__ import annotations

import dataclasses
import json
import os
from types import MappingProxyType

import pytest

import executor_birth_distribution_manifest as distribution
import executor_birth_distribution_assembler as assembler
import executor_birth_service_catalog as catalog
from tests.portable.test_executor_birth_distribution_manifest import _authority
from tests.portable.test_executor_birth_service_catalog import _context, _legacy


_INVENTORY = "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json"
_GUARD = "runtime/contract_boundary_guard.py"
_ADMIN = "runtime/executor_birth_admin_preflight.py"
_HELPER = "deployment/admin/preflight.py"
_DESCRIPTOR = "deployment/executor-birth-deployment-v1.json"


def _release(root, sequence, previous_id, key, key_id, registry, *, mutation=None):
    logical = (distribution.DEFAULT_RELEASE_DIRECTORY_V1 / f"{sequence:020d}").as_posix()
    roles = dict(distribution._REQUIRED_PATH_ROLES)
    roles.update({_INVENTORY: "boundary_inventory", "requirements.lock": "dependency_lock",
                  "runtime/__version__.py": "product_version", _ADMIN: "runtime_code"})
    contents = {path: b"VALUE = 1\n" for path in roles}
    contents["runtime/__version__.py"] = b'__version__ = "1.2.3"\n'
    contents[_GUARD] = b'BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = "sha256:' + b"0" * 64 + b'"\n'
    contents[_ADMIN] = (
        b'_BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = "sha256:' + b"0" * 64
        + b'"\nraise RuntimeError("historical code must never execute")\n'
    )
    roles["runtime/history_marker.py"] = "runtime_code"
    contents["runtime/history_marker.py"] = f"VERSION = {sequence}\n".encode()
    review = distribution.closed_python_source_review_sha256(contents)
    for path in (_GUARD, _ADMIN):
        contents[path] = contents[path].replace(b"sha256:" + b"0" * 64, review.encode())
    contents[_HELPER] = contents[_ADMIN]
    inventory = {
        "schema": distribution.BOUNDARY_INVENTORY_SCHEMA,
        "scan_roots": list(distribution.SCAN_ROOTS), "entries": [],
        "source_census": review, "birth_closed": {"guard_version": f"signed-policy-{sequence}/1"},
    }
    if mutation == "source_after_review":
        contents["runtime/history_marker.py"] += b"CHANGED = 1\n"
    elif mutation == "pin":
        contents[_GUARD] = contents[_GUARD].replace(review.encode(), b"sha256:" + b"f" * 64)
    elif mutation == "duplicate_pin":
        contents[_GUARD] *= 2
    elif mutation == "helper_copy":
        contents[_HELPER] += b"# different helper\n"
    elif mutation == "inventory_pin":
        inventory["source_census"] = "sha256:" + "e" * 64
    elif mutation == "inventory_guard":
        inventory["birth_closed"]["guard_version"] = "another-policy/1"
    contents[_INVENTORY] = distribution._canonical(inventory)

    entries = list(catalog._compile_service_source_v1(_context(
        installation_root=logical, supplementary_gids=(1000,),
    )))
    # Both are self-consistent signed recipes; only N+1 matches today's recipe.
    if sequence == 1:
        index = next(i for i, item in enumerate(entries) if item.unit_spec is not None)
        item = entries[index]
        directives = tuple(
            dataclasses.replace(directive, values=("Historical signed description",))
            if directive.section == "Unit" and directive.name == "Description" else directive
            for directive in item.unit_spec.directives
        )
        entries[index] = dataclasses.replace(item, unit_spec=catalog.make_unit_spec_v1(item.unit_name, directives))
    contents[catalog.CATALOG_PATH_V1] = catalog._encode_service_catalog_v1(tuple(entries), _legacy())
    decoded = catalog.decode_service_catalog_v1(contents[catalog.CATALOG_PATH_V1])
    artifacts = [assembler.DeploymentArtifactV1(
        _HELPER, "/usr/libexec/metnos/executor-birth-v1/preflight.py", "administrative_program",
        "group6_admin", len(contents[_HELPER]), distribution.file_content_hash(_HELPER, contents[_HELPER]),
        0o755, 0, 0,
    )]
    kinds = {"gated_service": "service_unit", "gated_timer": "timer_unit",
             "target": "target_unit", "stop_only": "stop_only_unit"}
    for item in entries:
        if item.unit_spec is None:
            continue
        path = f"deployment/systemd/{item.unit_name}"
        contents[path] = catalog.render_unit_spec_v1(item.unit_name, item.unit_spec)
        roles[path] = "service_unit"
        artifacts.append(assembler.DeploymentArtifactV1(
            path, f"/etc/systemd/system/{item.unit_name}", kinds[item.class_name], "group7_cutover",
            len(contents[path]), distribution.file_content_hash(path, contents[path]), 0o644, 0, 0,
        ))
    descriptor = assembler.build_deployment_descriptor_v1(
        release_sequence=sequence, service_user="metnos", service_uid=1000, service_gid=1000,
        service_supplementary_gids=(1000,), service_home="/srv/metnos", service_shell="/usr/sbin/nologin",
        artifacts=tuple(artifacts), service_catalog_id=decoded.catalog_id,
        service_coverage_hash=decoded.service_coverage_hash, python_executable="/usr/bin/python3.12",
        openssl_executable="/usr/bin/openssl", systemctl_executable="/usr/bin/systemctl",
        systemd_analyze_executable="/usr/bin/systemd-analyze",
    )
    contents[_DESCRIPTOR] = assembler.encode_deployment_descriptor_v1(descriptor)
    if mutation == "artifact_after_descriptor":
        path = next(path for path in contents if path.startswith("deployment/systemd/"))
        contents[path] += b"# signed but not declared by descriptor\n"
    if mutation == "catalog_after_descriptor":
        value = json.loads(contents[catalog.CATALOG_PATH_V1])
        value["legacy_bindings"] = []
        value["catalog_id"] = catalog._catalog_id(value)
        contents[catalog.CATALOG_PATH_V1] = catalog._canonical(value)
    for path, content in contents.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o644)
    encoded = distribution.build_distribution_manifest_v1(
        previous_closed_build_id=previous_id, release_sequence=sequence, product_version="1.2.3",
        platform="linux", architecture="x86_64", signing_key_id=key_id, installation_root=logical,
        boundary_inventory_path=_INVENTORY,
        boundary_inventory_hash="sha256:" + distribution.hashlib.sha256(
            distribution.BOUNDARY_INVENTORY_DOMAIN + contents[_INVENTORY]).hexdigest(),
        boundary_guard_version=f"signed-policy-{sequence}/1",
        files=tuple(distribution.DistributionFile(
            path, len(content), distribution.file_content_hash(path, content), roles[path],
        ) for path, content in contents.items()),
    )
    record = distribution._authenticate_distribution_record_for_test(
        encoded, key.sign(distribution.SIGNATURE_DOMAIN + encoded), registry=registry,
    )
    return record, review, contents


def _pair(tmp_path, *, mutation=None):
    key, key_id, registry = _authority(distribution.PURPOSE)
    previous, old_review, old_contents = _release(
        tmp_path / "previous", 1, None, key, key_id, registry, mutation=mutation,
    )
    current, new_review, _ = _release(
        tmp_path / "current", 2, previous.closed_build_id, key, key_id, registry,
    )
    environment = distribution._environment_for_test(
        "linux", "x86_64", tmp_path / "previous", claimed_installation_root=previous.installation_root,
        verify_static_boundary=True,
    )
    return current, previous, registry, environment, old_review, new_review, old_contents


def test_historical_review_and_recipe_differ_without_running_old_code(tmp_path, monkeypatch):
    current, previous, registry, environment, old_pin, new_pin, contents = _pair(tmp_path)
    assert old_pin != new_pin
    monkeypatch.setattr(distribution, "BIRTH_CLOSED_SOURCE_REVIEW_SHA256", new_pin)
    assert not distribution._source_review_is_exact_v1(contents)
    verified = distribution._verify_previous_distribution_record_for_test_v1(
        current, previous, registry=registry, environment=environment,
    )
    assert distribution.is_verified_distribution(verified)
    assert verified.identity.closed_build_id == previous.closed_build_id
    artifacts = distribution._capture_previous_release_artifacts_for_test_v1(
        current, previous, registry=registry, environment=environment,
    )
    loaded = catalog.load_previous_service_catalog_v1(artifacts)
    assert loaded.catalog.encoded == contents[catalog.CATALOG_PATH_V1]
    assert dict(loaded.unit_fragments)
    assert artifacts.contents[_HELPER] == contents[_ADMIN]
    with pytest.raises(catalog.ServiceCatalogError, match="source recipe"):
        catalog._source_identity(loaded.catalog, previous.installation_root)
    with pytest.raises(distribution.DistributionManifestError):
        distribution._verify_authenticated_distribution_record_for_test(previous, environment=environment)
    with pytest.raises(distribution.DistributionManifestError):
        distribution.verify_installed_distribution_record_v1(artifacts)
    with pytest.raises(catalog.ServiceCatalogError):
        catalog.load_service_catalog_v1(artifacts)
    with pytest.raises(TypeError):
        artifacts.contents[_HELPER] = b"modified"


@pytest.mark.parametrize("mutation", (
    "source_after_review", "pin", "duplicate_pin", "helper_copy", "inventory_pin", "inventory_guard",
    "artifact_after_descriptor", "catalog_after_descriptor",
))
def test_signed_but_incoherent_historical_evidence_is_denied(tmp_path, mutation):
    current, previous, registry, environment, *_ = _pair(tmp_path, mutation=mutation)
    with pytest.raises((distribution.DistributionManifestError, catalog.ServiceCatalogError)):
        artifacts = distribution._capture_previous_release_artifacts_for_test_v1(
            current, previous, registry=registry, environment=environment,
        )
        catalog.load_previous_service_catalog_v1(artifacts)


@pytest.mark.parametrize("mutation", ("current_id", "previous_id", "sequence", "root", "signature"))
def test_previous_edge_cannot_select_other_signed_or_mutated_material(tmp_path, mutation):
    current, previous, registry, environment, *_ = _pair(tmp_path)
    if mutation == "current_id":
        current = dataclasses.replace(current, previous_closed_build_id="sha256:" + "f" * 64)
    elif mutation == "previous_id":
        previous = dataclasses.replace(previous, closed_build_id="sha256:" + "f" * 64)
    elif mutation == "sequence":
        current = dataclasses.replace(current, release_sequence=3)
    elif mutation == "root":
        previous = dataclasses.replace(previous, installation_root=str(tmp_path))
    else:
        previous = dataclasses.replace(
            previous, signature=current.signature,
            _artifact_binding=distribution._authenticated_artifact_binding(previous.encoded, current.signature),
        )
    with pytest.raises(distribution.DistributionManifestError):
        distribution._verify_previous_distribution_record_for_test_v1(
            current, previous, registry=registry, environment=environment,
        )


@pytest.mark.parametrize("mutation", ("bytes", "symlink", "hardlink", "extra", "missing"))
def test_historical_tree_tampering_is_denied_without_writes(tmp_path, mutation):
    if os.name == "nt" and mutation in {"symlink", "hardlink"}:
        pytest.skip("POSIX link boundary; Windows metadata has its dedicated suite")
    current, previous, registry, environment, *_ = _pair(tmp_path)
    victim = environment.installation_root / "runtime/history_marker.py"
    if mutation == "bytes":
        victim.write_bytes(b"tampered\n")
    elif mutation in {"symlink", "hardlink"}:
        other = tmp_path / "external"
        victim.rename(other)
        if mutation == "symlink":
            victim.symlink_to(other)
        else:
            os.link(other, victim)
    elif mutation == "extra":
        (environment.installation_root / "foreign").write_bytes(b"foreign")
    else:
        victim.unlink()
    before = sorted(str(path) for path in environment.installation_root.rglob("*"))
    with pytest.raises(distribution.DistributionManifestError):
        distribution._capture_previous_release_artifacts_for_test_v1(
            current, previous, registry=registry, environment=environment,
        )
    assert sorted(str(path) for path in environment.installation_root.rglob("*")) == before


@pytest.mark.parametrize("mutation", ("sequence", "previous_id", "previous_release"))
def test_valid_signatures_do_not_replace_the_immediate_edge(tmp_path, mutation):
    current, previous, registry, environment, *_ = _pair(tmp_path)
    key, key_id, _ = _authority(distribution.PURPOSE)
    if mutation == "previous_release":
        previous, *_ = _release(tmp_path / "other", 1, None, key, key_id, registry,
                               mutation="helper_copy")
    else:
        current, *_ = _release(
            tmp_path / "other", 3 if mutation == "sequence" else 2,
            previous.closed_build_id if mutation == "sequence" else "sha256:" + "f" * 64,
            key, key_id, registry,
        )
    with pytest.raises(distribution.DistributionManifestError, match="previous edge"):
        distribution._verify_previous_distribution_record_for_test_v1(
            current, previous, registry=registry, environment=environment,
        )


@pytest.mark.parametrize("phase", ("before", "after"))
def test_capture_reverifies_non_artifact_bytes_around_the_read(tmp_path, monkeypatch, phase):
    current, previous, registry, environment, *_ = _pair(tmp_path)
    capture = distribution._capture_previous_release_artifacts_core_v1
    victim = environment.installation_root / "runtime/history_marker.py"

    def changing_capture(*args, **kwargs):
        if phase == "before":
            victim.write_bytes(b"MUTATED = 1\n")
        result = capture(*args, **kwargs)
        if phase == "after":
            victim.write_bytes(b"MUTATED = 1\n")
        return result

    monkeypatch.setattr(distribution, "_capture_previous_release_artifacts_core_v1", changing_capture)
    with pytest.raises(distribution.DistributionManifestError):
        distribution._capture_previous_release_artifacts_for_test_v1(
            current, previous, registry=registry, environment=environment,
        )


@pytest.mark.parametrize("mutation", ("descriptor", "contents", "record", "plain_object"))
def test_historical_loader_rejects_exchanged_capture_fields(tmp_path, mutation):
    current, previous, registry, environment, *_ = _pair(tmp_path)
    artifacts = distribution._capture_previous_release_artifacts_for_test_v1(
        current, previous, registry=registry, environment=environment,
    )
    if mutation == "descriptor":
        artifacts = dataclasses.replace(artifacts, descriptor=dataclasses.replace(
            artifacts.descriptor, service_uid=1001,
        ))
    elif mutation == "contents":
        contents = dict(artifacts.contents)
        contents[catalog.CATALOG_PATH_V1] += b" "
        artifacts = dataclasses.replace(artifacts, contents=MappingProxyType(contents))
    elif mutation == "record":
        artifacts = dataclasses.replace(artifacts, record=current)
    else:
        artifacts = object()
    with pytest.raises(catalog.ServiceCatalogError):
        catalog.load_previous_service_catalog_v1(artifacts)


def test_previous_product_api_rejects_test_authorities(tmp_path):
    current, previous, *_ = _pair(tmp_path)
    with pytest.raises(distribution.DistributionManifestError):
        distribution.verify_previous_distribution_record_v1(current, previous)
    with pytest.raises(distribution.DistributionManifestError):
        distribution.capture_previous_release_artifacts_v1(current, previous)
