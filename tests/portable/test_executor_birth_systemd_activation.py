"""Disposable-VM proof for signed G6-C denial and real admission."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from install import executor_birth_systemd as installer
from install.executor_birth_source_receiver import _ServiceAccountV1
import executor_birth_admin_preflight as preflight
import executor_birth_distribution_assembler as assembler
import executor_birth_distribution_manifest as distribution
import executor_birth_service_catalog as catalog
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
    closed_python_source_review_sha256,
    closed_python_sources_from_root,
    discover as discover_boundary_scopes,
)
from executor_birth_cutover import CurrentReceiptProof
from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
from executor_birth_ownership_authorities import (
    decode_ownership_registry_v1,
    encode_ownership_registry_v1,
)
from executor_birth_ownership_chain import (
    encode_required_head,
    issue_ownership_head,
    verify_ownership_head,
)
from executor_birth_ownership_coordinator import (
    OwnershipCoordinatorRecordV2,
    OwnershipCoordinatorStateV1,
    SuccessorClaimV1,
    _coordinator_request_id_v1,
    _deployment_lock_for_test_v1,
    _install_transaction_id_v1,
    _record_hash_v2,
    _successor_claim_id_v1,
)
from executor_birth_ownership_cutover import (
    issue_ownership_cutover_certificate,
    verify_ownership_cutover_certificate,
)
from executor_birth_ownership_preflight import (
    canonical_maintenance_proof,
    maintenance_evidence_hash,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("METNOS_REQUIRE_REAL_G6C_SYSTEMD") != "1",
    reason="the destructive G6-C activation cell is CI opt-in only",
)


OWNERSHIP_ROOT = Path("/var/lib/metnos/executor-birth")
RELEASE_ROOT = OWNERSHIP_ROOT / "releases-v1/00000000000000000001"
ADMINISTRATIVE_ROOT = Path("/usr/libexec/metnos/executor-birth-v1")
UNIT_ROOT = Path("/etc/systemd/system")
RUNTIME_ROOT = Path("/run/metnos-executor-birth-v1")
STARTUP_GATE = Path("/run/lock/metnos/executor-birth-startup-v1.lock")
BOUNDARY_INVENTORY_PATH = (
    "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json"
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _raw_digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _framed_digest(domain: bytes, content: bytes) -> str:
    """Frame exactly as the product does, length included.

    The product frames `domain || u64be(len) || payload` precisely so one
    field cannot slide into its neighbour. This helper omitted the length, so
    every head hash the fixture built disagreed with the three the preflight
    recomputes — `head_payload_hash`, `head_signature_hash` and
    `required_head_frame_hash` — and the transaction head binding refused a
    fixture that was otherwise correct.
    """
    return "sha256:" + hashlib.sha256(
        domain + len(content).to_bytes(8, "big") + content,
    ).hexdigest()


def _write_control(path: Path, content: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)


def _service_account() -> _ServiceAccountV1:
    import pwd

    candidates = sorted(
        (
            item for item in pwd.getpwall()
            if 0 < item.pw_uid < 65534
            and Path(item.pw_shell).name in {"nologin", "false"}
        ),
        key=lambda item: item.pw_uid,
    )
    assert candidates, "no non-root nologin service account"
    selected = candidates[0]
    supplementary = tuple(sorted(set(os.getgrouplist(
        selected.pw_name, selected.pw_gid,
    ))))
    assert selected.pw_gid in supplementary
    return _ServiceAccountV1(
        selected.pw_name, selected.pw_uid, selected.pw_gid, supplementary,
        selected.pw_dir, selected.pw_shell,
    )


def _compiled_boundary_inventory(repository: Path) -> bytes:
    """Build a closed test inventory solely from compiled public policy."""
    entries = []
    for fact in discover_boundary_scopes(repository):
        if not (fact.capabilities or fact.direct_manifest_dir_access):
            continue
        capabilities = set(fact.capabilities)
        if fact.key == BIRTH_CLOSED_OWNER:
            role = "birth_owner"
        elif capabilities & {"store_write", "publish_bootstrap"}:
            role = "store_owner"
        elif "legacy_bootstrap" in capabilities:
            role = "migration_boundary"
        elif fact.direct_manifest_dir_access:
            role = "offline_authoring"
        else:
            role = "administrative_tool"
        entry = {
            "capabilities": list(fact.capabilities),
            "destination": "compiled disposable-VM G6-C proof",
            "path": fact.path,
            "phase": "M4",
            "role": role,
            "scope": fact.scope,
        }
        exception = BIRTH_CLOSED_EXCEPTION_SCOPES.get(fact.key)
        if exception is not None:
            entry["closed_exception"] = exception
        entries.append(entry)
    return _canonical({
        "birth_closed": {
            "coordinator_store_owners": sorted(
                BIRTH_CLOSED_COORDINATOR_STORE_OWNERS,
            ),
            "exceptions": [
                {"scope": scope, "exception": exception}
                for scope, exception in sorted(
                    BIRTH_CLOSED_EXCEPTION_SCOPES.items(),
                )
            ],
            "guard_version": BIRTH_CLOSED_GUARD_VERSION,
            "owner": BIRTH_CLOSED_OWNER,
            "schema": BIRTH_CLOSED_SCHEMA,
            "sealed_modules": list(BIRTH_CLOSED_SEALED_MODULES),
        },
        "entries": entries,
        "scan_roots": list(SCAN_ROOTS),
        "schema": BOUNDARY_INVENTORY_SCHEMA,
        "source_census": BIRTH_CLOSED_SOURCE_REVIEW_SHA256,
    })


def _canonical_executable(path: str) -> tuple[str, bytes]:
    resolved = Path(path).resolve(strict=True)
    assert resolved.is_file() and not resolved.is_symlink()
    return resolved.as_posix(), resolved.read_bytes()


@dataclass(frozen=True)
class _ActivationFixture:
    namespace: str
    account: _ServiceAccountV1
    service_entry_id: str
    service_name: str
    timer_name: str
    marker_root: Path
    marker_path: Path
    catalog_bytes: bytes
    descriptor: assembler.DeploymentDescriptorV1
    descriptor_bytes: bytes
    unit_fragments: tuple[tuple[str, bytes], ...]
    contents: dict[str, bytes]
    manifest: bytes
    signature: bytes
    distribution_registry: bytes
    private_keys: dict[str, Ed25519PrivateKey]
    record: object
    environment: object


def _activation_fixture(repository: Path, namespace: str) -> _ActivationFixture:
    account = _service_account()
    python, python_bytes = _canonical_executable("/usr/bin/python3")
    openssl, _openssl_bytes = _canonical_executable("/usr/bin/openssl")
    systemctl, _systemctl_bytes = _canonical_executable("/usr/bin/systemctl")
    systemd_analyze, _analyze_bytes = _canonical_executable(
        "/usr/bin/systemd-analyze",
    )
    marker_root = Path(f"/run/metnos-g6c-{namespace}")
    marker_path = marker_root / "marker.json"
    service_entry_id = f"g6c-{namespace}-probe"
    service_name = f"metnos-g6c-{namespace}-probe.service"
    timer_entry_id = service_entry_id + "-timer"
    timer_name = f"metnos-g6c-{namespace}-probe.timer"
    administrative = "!" + python
    supplementary = " ".join(str(item) for item in account.supplementary_gids)
    service_spec = catalog.make_unit_spec_v1(service_name, (
        catalog.ServiceDirectiveV1(
            "Unit", "Description", "scalar", ("isolated signed G6-C probe",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "CapabilityBoundingSet", "scalar",
            ("CAP_SETGID CAP_SETPCAP CAP_SETUID",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "ExecStart", "argv",
            (
                administrative, "-I", "-S",
                catalog.ADMINISTRATIVE_ADAPTER_PATH_V1,
                "launch", "--entry-id", service_entry_id,
            ),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "ExecStartPre", "argv",
            (
                administrative, "-I", "-S",
                catalog.ADMINISTRATIVE_ADAPTER_PATH_V1,
                "check", "--entry-id", service_entry_id,
            ),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "Group", "scalar", (str(account.gid),),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "KillMode", "scalar", ("control-group",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "NoNewPrivileges", "boolean", ("yes",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "PrivateTmp", "boolean", ("yes",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "ProtectSystem", "scalar", ("strict",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "ReadWritePaths", "path_list",
            (marker_root.as_posix(),),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "SupplementaryGroups", "scalar", (supplementary,),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "Type", "scalar", ("oneshot",),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "User", "scalar", (account.name,),
        ),
        catalog.ServiceDirectiveV1(
            "Service", "WorkingDirectory", "path_list", ("/",),
        ),
    ))
    timer_spec = catalog.make_unit_spec_v1(timer_name, (
        catalog.ServiceDirectiveV1(
            "Unit", "Description", "scalar",
            ("isolated signed G6-C timer",),
        ),
        catalog.ServiceDirectiveV1(
            "Timer", "AccuracySec", "duration", ("1ms",),
        ),
        catalog.ServiceDirectiveV1(
            "Timer", "OnActiveSec", "duration", ("100ms",),
        ),
        catalog.ServiceDirectiveV1(
            "Timer", "Unit", "unit_list", (service_name,),
        ),
    ))
    entries = (
        catalog.ServiceCatalogEntryV1(
            service_entry_id, service_name, None, None, "gated_service",
            "system", "python_module", python,
            catalog.target_executable_hash_v1(python, python_bytes),
            "runtime.executor_birth_activation_probe",
            (marker_path.as_posix(),),
            RELEASE_ROOT.as_posix(), (), None, service_spec, True, True,
        ),
        catalog.ServiceCatalogEntryV1(
            timer_entry_id, timer_name, None, None, "gated_timer", "system",
            "none", None, None, None, (), None, (), service_entry_id,
            timer_spec, False, False,
        ),
    )
    catalog_bytes = catalog._encode_service_catalog_v1(entries, ())
    decoded_catalog = catalog.decode_service_catalog_v1(catalog_bytes)
    unit_fragments = tuple(sorted((
        (service_name, catalog.render_unit_spec_v1(service_name, service_spec)),
        (timer_name, catalog.render_unit_spec_v1(timer_name, timer_spec)),
    )))
    preflight_bytes = (
        repository / "runtime/executor_birth_admin_preflight.py"
    ).read_bytes()
    artifacts = [assembler.DeploymentArtifactV1(
        "deployment/admin/preflight.py",
        "/usr/libexec/metnos/executor-birth-v1/preflight.py",
        "administrative_program", "group6_admin", len(preflight_bytes),
        distribution.file_content_hash(
            "deployment/admin/preflight.py", preflight_bytes,
        ),
        0o755, 0, 0,
    )]
    for unit_name, fragment in unit_fragments:
        source = "deployment/systemd/" + unit_name
        artifacts.append(assembler.DeploymentArtifactV1(
            source, "/etc/systemd/system/" + unit_name,
            "timer_unit" if unit_name.endswith(".timer") else "service_unit",
            "group7_cutover", len(fragment),
            distribution.file_content_hash(source, fragment),
            0o644, 0, 0,
        ))
    descriptor = assembler.build_deployment_descriptor_v1(
        release_sequence=1, service_user=account.name,
        service_uid=account.uid, service_gid=account.gid,
        service_supplementary_gids=account.supplementary_gids,
        service_home=account.home, service_shell=account.shell,
        artifacts=tuple(artifacts),
        service_catalog_id=decoded_catalog.catalog_id,
        service_coverage_hash=decoded_catalog.service_coverage_hash,
        python_executable=python, openssl_executable=openssl,
        systemctl_executable=systemctl,
        systemd_analyze_executable=systemd_analyze,
    )
    descriptor_bytes = assembler.encode_deployment_descriptor_v1(descriptor)

    sources = closed_python_sources_from_root(repository)
    assert closed_python_source_review_sha256(
        sources,
    ) == BIRTH_CLOSED_SOURCE_REVIEW_SHA256
    contents = dict(sources)
    contents.update({
        "deployment/admin/preflight.py": preflight_bytes,
        "deployment/executor-birth-deployment-v1.json": descriptor_bytes,
        "deployment/executor-birth-service-catalog-v1.json": catalog_bytes,
        BOUNDARY_INVENTORY_PATH: _compiled_boundary_inventory(repository),
        "requirements.lock": b"cryptography==47.0.0\n",
        **{
            "deployment/systemd/" + name: fragment
            for name, fragment in unit_fragments
        },
    })
    roles = {
        "deployment/admin/preflight.py": "preflight",
        "deployment/executor-birth-deployment-v1.json": (
            "deployment_descriptor"
        ),
        "deployment/executor-birth-service-catalog-v1.json": "service_catalog",
        BOUNDARY_INVENTORY_PATH: "boundary_inventory",
        "requirements.lock": "dependency_lock",
        "runtime/__version__.py": "product_version",
        "runtime/contract_boundary_guard.py": "boundary_guard",
        "runtime/executor_birth_distribution_manifest.py": "preflight",
        "runtime/executor_birth_ownership_preflight.py": "preflight",
        **{
            "deployment/systemd/" + name: "service_unit"
            for name, _fragment in unit_fragments
        },
    }
    inventory = contents[BOUNDARY_INVENTORY_PATH]
    version_namespace: dict[str, object] = {}
    exec(contents["runtime/__version__.py"], version_namespace)
    product_version = version_namespace["__version__"]
    assert isinstance(product_version, str)
    private_keys = {
        name: Ed25519PrivateKey.generate()
        for name in ("distribution", "cutover", "head")
    }
    distribution_registry = encode_ownership_registry_v1(
        "distribution", private_keys["distribution"].public_key(),
    )
    distribution_registry_decoded = decode_ownership_registry_v1(
        distribution_registry, expected_kind="distribution",
    )
    signing_key_id = next(iter(distribution_registry_decoded.keys))
    manifest_files = [{
        "path": path,
        "size": len(content),
        "content_hash": distribution.file_content_hash(path, content),
        "role": roles.get(path, "runtime_code"),
    } for path, content in sorted(
        contents.items(), key=lambda item: item[0].encode("utf-8"),
    )]
    architecture = {
        "amd64": "x86_64", "x86_64": "x86_64",
        "arm64": "aarch64", "aarch64": "aarch64",
    }[platform.machine().lower()]
    manifest_value = {
        "schema_version": 1,
        "closed_build_id": None,
        "previous_closed_build_id": None,
        "release_sequence": 1,
        "product_version": product_version,
        "platform": "linux",
        "architecture": architecture,
        "signing_key_id": signing_key_id,
        "installation_root": RELEASE_ROOT.as_posix(),
        "certificate_directory": OWNERSHIP_ROOT.as_posix(),
        "boundary_inventory_path": BOUNDARY_INVENTORY_PATH,
        "boundary_inventory_hash": _framed_digest(
            distribution.BOUNDARY_INVENTORY_DOMAIN, inventory,
        ),
        "boundary_guard_version": BIRTH_CLOSED_GUARD_VERSION,
        "preflight_entrypoint": "deployment/admin/preflight.py",
        "files": manifest_files,
    }
    manifest_value["closed_build_id"] = _framed_digest(
        distribution.BUILD_ID_DOMAIN,
        _canonical({
            key: value for key, value in manifest_value.items()
            if key != "closed_build_id"
        }),
    )
    manifest = _canonical(manifest_value)
    signature = private_keys["distribution"].sign(
        distribution.SIGNATURE_DOMAIN + manifest,
    )
    record = distribution._authenticate_distribution_record_for_test(
        manifest, signature, registry=distribution_registry_decoded,
    )
    environment = distribution._environment_for_test(
        "linux", architecture, RELEASE_ROOT,
        claimed_installation_root=RELEASE_ROOT.as_posix(),
    )
    return _ActivationFixture(
        namespace, account, service_entry_id, service_name, timer_name,
        marker_root, marker_path, catalog_bytes, descriptor, descriptor_bytes,
        unit_fragments, contents, manifest, signature, distribution_registry,
        private_keys, record, environment,
    )


def _materialize_release(fixture: _ActivationFixture) -> None:
    RELEASE_ROOT.mkdir(mode=0o755, parents=True)
    for relative, content in fixture.contents.items():
        path = RELEASE_ROOT.joinpath(*relative.split("/"))
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o644)


def _install_administrative_and_units(fixture: _ActivationFixture) -> None:
    with _deployment_lock_for_test_v1(OWNERSHIP_ROOT) as session:
        installer._install_group6_administrative_for_test_v1(
            fixture.record, environment=fixture.environment, session=session,
            ownership_root=OWNERSHIP_ROOT,
            administrative_root=ADMINISTRATIVE_ROOT,
            account=fixture.account,
        )
        capability = installer._signed_isolated_systemd_for_test_v1(
            fixture.record, environment=fixture.environment, session=session,
            ownership_root=OWNERSHIP_ROOT, unit_root=UNIT_ROOT,
            account=fixture.account, namespace=fixture.namespace,
        )
        installed = installer._install_signed_isolated_systemd_for_test_v1(
            capability, session=session, ownership_root=OWNERSHIP_ROOT,
        )
    assert installed.unit_names == tuple(
        name for name, _fragment in fixture.unit_fragments
    )


def _capture_live_bindings(
    fixture: _ActivationFixture,
) -> tuple[object, object, str]:
    captured_tcb = preflight._capture_administrative_tcb_v1().capture
    autonomous_catalog = preflight._decode_service_catalog_v1(
        fixture.catalog_bytes,
    )
    autonomous_descriptor = preflight._decode_deployment_descriptor_v1(
        fixture.descriptor_bytes,
    )
    candidate = preflight._compile_candidate_units_v1(autonomous_catalog)
    preflight._service_source_identity_v1(
        autonomous_catalog, autonomous_descriptor,
    )
    provisional = preflight._BoundPreflightMaterialsV1(
        None, None, autonomous_catalog, autonomous_descriptor, None,
        candidate, fixture.unit_fragments, "", "",
    )
    effective = preflight._capture_effective_systemd_units_core_v1(
        provisional,
        systemctl_executable=fixture.descriptor.systemctl_executable,
        live_root=Path("/"), uid=0, gid=0,
    )
    return captured_tcb, effective, candidate.candidate_units_hash


def _build_prerequisite_and_graph(
    fixture: _ActivationFixture, captured_tcb: object,
    effective: object, candidate_hash: str,
) -> tuple[bytes, str]:
    authority = OWNERSHIP_ROOT / "authorities-v1"
    authority.mkdir(mode=0o755)
    registries = {
        "distribution": fixture.distribution_registry,
        "cutover": encode_ownership_registry_v1(
            "cutover", fixture.private_keys["cutover"].public_key(),
        ),
        "head": encode_ownership_registry_v1(
            "head", fixture.private_keys["head"].public_key(),
        ),
    }
    for kind, encoded in registries.items():
        _write_control(authority / f"{kind}-registry-v1.json", encoded)
        raw_private = fixture.private_keys[kind].private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        _write_control(
            authority / f"{kind}-private-v1.bin", raw_private, 0o600,
        )
    for index in range(5):
        _write_control(
            authority / f"checkpoint-{index:03d}-v1.json",
            preflight._authority_checkpoint_v1(index),
        )

    manifest_value = json.loads(fixture.manifest)
    closed_build_id = manifest_value["closed_build_id"]
    request_id = _coordinator_request_id_v1(closed_build_id, None, None)
    source_id = _raw_digest(
        ("isolated-g6c-source-" + fixture.namespace).encode("ascii"),
    )
    proof = CurrentReceiptProof(
        (("isolated.g6c.contract", _raw_digest(b"g6c-contract")),),
        {(
            "isolated.g6c.contract", _raw_digest(b"g6c-contract"),
        ): _raw_digest(b"g6c-receipt")},
    )
    maintenance = canonical_maintenance_proof(
        source="inactive_http_and_inactive_sidecar",
        units=tuple({
            "scope": scope, "unit": unit, "load_state": "loaded",
            "active_state": "inactive", "main_pid": 0,
        } for scope, unit in MAINTENANCE_TARGETS_V1),
    )
    maintenance_hash = maintenance_evidence_hash(maintenance)
    cutover_registry = decode_ownership_registry_v1(
        registries["cutover"], expected_kind="cutover",
    )
    cutover_encoded, cutover_signature = issue_ownership_cutover_certificate(
        proof=proof, previous_cutover_id=None, request_id=request_id,
        signing_key_id=next(iter(cutover_registry.keys)),
        maintenance_evidence_hash=maintenance_hash,
        boundary_inventory_hash=manifest_value["boundary_inventory_hash"],
        boundary_guard_version=manifest_value["boundary_guard_version"],
        closed_build_id=closed_build_id,
        private_key=fixture.private_keys["cutover"],
    )
    cutover = verify_ownership_cutover_certificate(
        cutover_encoded, cutover_signature, registry=cutover_registry,
    )
    head_registry = decode_ownership_registry_v1(
        registries["head"], expected_kind="head",
    )
    head_encoded, head_signature = issue_ownership_head(
        release_sequence=1, cutover_id=cutover.cutover_id,
        closed_build_id=closed_build_id, previous_head_id=None,
        signing_key_id=next(iter(head_registry.keys)),
        private_key=fixture.private_keys["head"],
    )
    head = verify_ownership_head(
        head_encoded, head_signature, registry=head_registry,
    )
    required_frame = encode_required_head(head)

    decoded_descriptor = preflight._decode_deployment_descriptor_v1(
        fixture.descriptor_bytes,
    )
    bundle_hash = preflight._administrative_bundle_hash_v1(
        decoded_descriptor,
    )
    decoded_catalog = preflight._decode_service_catalog_v1(
        fixture.catalog_bytes,
    )
    claim_value = {
        "schema_version": 1, "previous_head_id": None,
        "release_sequence": 1, "request_id": request_id,
        "source_id": source_id, "closed_build_id": closed_build_id,
    }
    claim_value["claim_id"] = _successor_claim_id_v1(claim_value)
    claim = SuccessorClaimV1(
        claim_value["claim_id"], None, 1, request_id, source_id,
        closed_build_id,
    )
    install_value = {
        "schema_version": 1, "request_id": request_id,
        "source_id": source_id, "closed_build_id": closed_build_id,
        "release_sequence": 1, "previous_head_id": None,
        "successor_claim_id": claim.claim_id,
        "deployment_descriptor_id": fixture.descriptor.descriptor_id,
        "service_coverage_hash": decoded_catalog.service_coverage_hash,
        "administrative_bundle_hash": bundle_hash,
    }
    transaction_id = _install_transaction_id_v1(install_value)
    predecessor_record = assembler.build_predecessor_descriptor_v1(
        transaction_id=transaction_id, installation_root="/opt/metnos",
        files=(assembler.PredecessorFileV1(
            "runtime/legacy.py", 1, _raw_digest(b"legacy"),
        ),),
        service_commands=(assembler.PredecessorServiceCommandV1(
            "legacy", "none", None, None, None, (), None, (),
        ),),
        administrative_bundle_hash=bundle_hash,
        service_catalog_id=decoded_catalog.catalog_id,
        service_coverage_hash=decoded_catalog.service_coverage_hash,
    )
    predecessor_bytes = assembler.encode_predecessor_descriptor_v1(
        predecessor_record,
    )
    prerequisite = assembler.build_startup_prerequisite_v1(
        request_id=request_id, closed_build_id=closed_build_id,
        release_sequence=1,
        deployment_descriptor_id=fixture.descriptor.descriptor_id,
        predecessor_id=predecessor_record.predecessor_id,
        administrative_bundle_hash=bundle_hash,
        python_binary_hash=captured_tcb.executables.python_binary_hash,
        openssl_binary_hash=captured_tcb.executables.openssl_binary_hash,
        openssl_tcb_hash=captured_tcb.openssl_tcb.openssl_tcb_hash,
        systemctl_binary_hash=captured_tcb.executables.systemctl_binary_hash,
        systemd_analyze_binary_hash=(
            captured_tcb.executables.systemd_analyze_binary_hash
        ),
        service_catalog_id=decoded_catalog.catalog_id,
        service_coverage_hash=decoded_catalog.service_coverage_hash,
        systemd_manager_version=effective.manager_version,
        candidate_units_hash=candidate_hash,
        effective_units_hash=effective.snapshot.effective_units_hash,
    )
    prerequisite_bytes = assembler.encode_startup_prerequisite_v1(prerequisite)
    _manifest_value, manifest_files = preflight._parse_distribution_manifest_v1(
        fixture.manifest,
    )
    installed_tree_hash = preflight._installed_tree_hash_v1(manifest_files)

    chain = OWNERSHIP_ROOT / "chain-v1"
    for name in ("builds-v1", "cutovers-v1", "heads-v1"):
        (chain / name).mkdir(mode=0o755, parents=True, exist_ok=True)
    build_stem = closed_build_id.removeprefix("sha256:")
    _write_control(chain / "builds-v1" / f"{build_stem}.json", fixture.manifest)
    _write_control(chain / "builds-v1" / f"{build_stem}.sig", fixture.signature)
    cutover_stem = cutover.cutover_id.removeprefix("sha256:")
    _write_control(
        chain / "cutovers-v1" / f"{cutover_stem}.json", cutover_encoded,
    )
    _write_control(
        chain / "cutovers-v1" / f"{cutover_stem}.sig", cutover_signature,
    )
    head_stem = f"{1:020d}-{cutover_stem}"
    _write_control(chain / "heads-v1" / f"{head_stem}.json", head_encoded)
    _write_control(chain / "heads-v1" / f"{head_stem}.sig", head_signature)
    _write_control(chain / "required-head-v1.bin", required_frame)
    _write_control(OWNERSHIP_ROOT / "ownership-cutover-v1.json", cutover_encoded)
    _write_control(OWNERSHIP_ROOT / "ownership-cutover-v1.sig", cutover_signature)
    _write_control(OWNERSHIP_ROOT / "predecessor-v1.json", predecessor_bytes)

    coordinator = OWNERSHIP_ROOT / "coordinator-v1"
    claims = coordinator / "successor-claims-v1"
    transaction = coordinator / "transactions-v2" / request_id
    claims.mkdir(mode=0o755, parents=True)
    transaction.mkdir(mode=0o755, parents=True)
    _write_control(claims / "initial.json", claim.encode())
    previous_hash = None
    for sequence, state in enumerate(OwnershipCoordinatorStateV1):
        if sequence > 5:
            break
        record = OwnershipCoordinatorRecordV2(
            sequence=sequence, state=state,
            previous_record_sha256=previous_hash,
            request_id=request_id, previous_closed_build_id=None,
            previous_cutover_id=None, closed_build_id=closed_build_id,
            distribution_payload_hash=_raw_digest(fixture.manifest),
            distribution_signature_hash=_raw_digest(fixture.signature),
            boundary_inventory_hash=manifest_value["boundary_inventory_hash"],
            boundary_guard_version=manifest_value["boundary_guard_version"],
            source_id=source_id, successor_claim_id=claim.claim_id,
            deployment_descriptor_id=fixture.descriptor.descriptor_id,
            install_transaction_id=transaction_id, release_sequence=1,
            previous_head_id=None,
            service_coverage_hash=decoded_catalog.service_coverage_hash,
            administrative_bundle_hash=bundle_hash,
            current_proof=proof if sequence >= 1 else None,
            maintenance_before_hash=(maintenance_hash if sequence >= 1 else None),
            maintenance_after_hash=(maintenance_hash if sequence >= 1 else None),
            maintenance_proof=(maintenance if sequence >= 1 else None),
            startup_prerequisite_id=(
                prerequisite.prerequisite_id if sequence >= 2 else None
            ),
            startup_prerequisite_digest=(
                _raw_digest(prerequisite_bytes) if sequence >= 2 else None
            ),
            cutover_id=cutover.cutover_id if sequence >= 2 else None,
            catalog_id=cutover.catalog_id if sequence >= 2 else None,
            certificate_payload_hash=(
                _raw_digest(cutover_encoded) if sequence >= 2 else None
            ),
            certificate_signature_hash=(
                _raw_digest(cutover_signature) if sequence >= 2 else None
            ),
            installed_tree_hash=installed_tree_hash if sequence >= 4 else None,
            head_id=head.head_id if sequence >= 5 else None,
            head_payload_hash=(
                _framed_digest(preflight.HEAD_PAYLOAD_HASH_DOMAIN_V2, head_encoded)
                if sequence >= 5 else None
            ),
            head_signature_hash=(
                _framed_digest(
                    preflight.HEAD_SIGNATURE_HASH_DOMAIN_V2, head_signature,
                ) if sequence >= 5 else None
            ),
            required_head_frame_hash=(
                _framed_digest(
                    preflight.REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2, required_frame,
                ) if sequence >= 5 else None
            ),
            verified_chain_head_id=head.head_id if sequence >= 5 else None,
        )
        encoded_record = record.encode()
        _write_control(
            transaction / f"record-{sequence:03d}-v2.json", encoded_record,
        )
        previous_hash = _record_hash_v2(encoded_record)
    prerequisite_root = OWNERSHIP_ROOT / "startup-prerequisites-v1"
    prerequisite_root.mkdir(mode=0o755)
    return prerequisite_bytes, request_id


def _systemctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/systemctl", *arguments], check=check,
        capture_output=True, text=True, timeout=30,
    )


def _unit_diagnosis(unit_name: str) -> str:
    """What the manager says about a unit, for a timeout that must explain.

    A timeout that reports only "timed out" costs one CI round per hypothesis,
    and this cell runs only where root and a real manager exist. The unit
    state and its last log lines are what any operator would look at first.
    """
    lines = []
    shown = _systemctl(
        "show", unit_name, "--property=ActiveState,SubState,Result,"
        "ExecMainStatus,ExecMainCode,NRestarts,LoadState", check=False,
    )
    lines.append(shown.stdout.strip().replace("\n", " "))
    logged = subprocess.run(
        ["journalctl", "--no-pager", "-n", "25", "-u", unit_name],
        capture_output=True, text=True, timeout=30,
    )
    lines.append(logged.stdout.strip()[-1500:])
    # The launched process may only print the public denial CLASS: the detail
    # is kept off an operator stream by design. Repeating the same attestation
    # in-process, against the same root-owned state, names the check that
    # actually refused — without weakening the boundary the product keeps.
    try:
        preflight._attest_operational_preflight_v1()
        lines.append("in-process attestation: accepted")
    except preflight.PreflightError as denial:
        lines.append(f"in-process attestation: {denial.code} :: {denial.detail}")
    except Exception as failure:
        lines.append(f"in-process attestation raised {type(failure).__name__}")
    return "\n".join(item for item in lines if item)


def _wait_for(predicate, timeout: float = 15.0, *, diagnose=None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    detail = ""
    if diagnose is not None:
        try:
            detail = "\n" + diagnose()
        except Exception as failure:  # a diagnosis must never mask the timeout
            detail = f"\ndiagnosis unavailable: {type(failure).__name__}"
    raise AssertionError("G6-C live condition timed out" + detail)


def _demote(account: _ServiceAccountV1):
    def demote() -> None:
        os.setgroups(list(account.supplementary_gids))
        os.setgid(account.gid)
        os.setuid(account.uid)
    return demote


def test_signed_systemd_cell_denies_then_admits_real_timer(
    tmp_path: Path,
) -> None:
    import fcntl

    del tmp_path  # fixed roots are intentional and the VM is disposable.
    assert os.geteuid() == 0
    assert Path("/run/systemd/system").is_dir()
    assert shutil.which("systemctl") == "/usr/bin/systemctl"
    assert not OWNERSHIP_ROOT.exists()
    assert not ADMINISTRATIVE_ROOT.parent.exists()
    assert not RUNTIME_ROOT.exists()
    namespace = os.urandom(8).hex()
    repository = Path(__file__).resolve().parents[2]
    fixture = _activation_fixture(repository, namespace)
    unit_paths = tuple(UNIT_ROOT / name for name, _ in fixture.unit_fragments)
    assert all(not path.exists() for path in unit_paths)
    assert not fixture.marker_root.exists()
    assert not STARTUP_GATE.exists()

    installed = False
    try:
        _materialize_release(fixture)
        _install_administrative_and_units(fixture)
        installed = True
        RUNTIME_ROOT.mkdir(mode=0o700)
        STARTUP_GATE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        gate = os.open(STARTUP_GATE, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(gate)
        fixture.marker_root.mkdir(mode=0o700)
        os.chown(fixture.marker_root, fixture.account.uid, fixture.account.gid)
        _systemctl("daemon-reload")

        captured_tcb, effective, candidate_hash = _capture_live_bindings(fixture)
        prerequisite, request_id = _build_prerequisite_and_graph(
            fixture, captured_tcb, effective, candidate_hash,
        )
        prerequisite_path = (
            OWNERSHIP_ROOT / "startup-prerequisites-v1" / f"{request_id}.json"
        )
        assert not prerequisite_path.exists()

        direct_denial = _systemctl("start", fixture.service_name, check=False)
        assert direct_denial.returncode != 0
        assert not fixture.marker_path.exists()
        _systemctl("reset-failed", fixture.service_name, check=False)
        _systemctl("start", fixture.timer_name)
        _wait_for(lambda: _systemctl(
            "show", fixture.service_name, "--property=Result", "--value",
            check=False,
        ).stdout.strip() == "exit-code")
        assert not fixture.marker_path.exists()
        _systemctl("stop", fixture.timer_name, check=False)
        _systemctl("reset-failed", fixture.service_name, check=False)

        _write_control(prerequisite_path, prerequisite)
        preflight_path = ADMINISTRATIVE_ROOT / "preflight.py"
        python = fixture.descriptor.python_executable
        for command in ("check", "launch"):
            denied = subprocess.run(
                [
                    python, "-I", "-S", preflight_path.as_posix(), command,
                    "--entry-id", fixture.service_entry_id,
                ],
                capture_output=True, text=True, timeout=30,
                preexec_fn=_demote(fixture.account),
            )
            assert denied.returncode == preflight.EXIT_INVALID
            assert denied.stderr == preflight.CODE_INVALID + "\n"
            assert not fixture.marker_path.exists()

        _systemctl("start", fixture.timer_name)
        _wait_for(
            fixture.marker_path.exists,
            diagnose=lambda: _unit_diagnosis(fixture.service_name),
        )
        gate_descriptor = os.open(STARTUP_GATE, os.O_RDWR)
        try:
            fcntl.flock(gate_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            payload = json.loads(fixture.marker_path.read_bytes())
            assert os.readlink(
                f"/proc/{payload['pid']}/ns/mnt",
            ) == payload["mount_namespace"]
        finally:
            fcntl.flock(gate_descriptor, fcntl.LOCK_UN)
            os.close(gate_descriptor)

        expected_groups = list(fixture.account.supplementary_gids)
        expected_environment = {
            "HOME": fixture.account.home,
            "LOGNAME": fixture.account.name,
            "SHELL": fixture.account.shell,
            "USER": fixture.account.name,
        }
        assert payload["uid"] == fixture.account.uid
        assert payload["gid"] == fixture.account.gid
        assert payload["groups"] == expected_groups
        assert payload["fds"] == [0, 1, 2]
        assert payload["cwd"] == RELEASE_ROOT.as_posix()
        assert payload["environment"] == expected_environment
        assert payload["argv"] == [
            "runtime.executor_birth_activation_probe",
            fixture.marker_path.as_posix(),
        ]
        status = payload["status"]
        assert status["NoNewPrivs"] == "1"
        assert status["Groups"].split() == [str(item) for item in expected_groups]
        assert all(status[name] == "0000000000000000" for name in (
            "CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb",
        ))
        marker_info = fixture.marker_path.stat()
        assert (marker_info.st_uid, marker_info.st_gid) == (
            fixture.account.uid, fixture.account.gid,
        )
        assert stat.S_IMODE(marker_info.st_mode) == 0o640

        # ── G6-C4: la prova relazionale, sulla stessa cella ────────────
        # Misurato su systemd 255.4 prima di scrivere questa parte:
        # `TriggeredBy` sul servizio NON compare dopo `daemon-reload`, solo
        # dopo l'avvio del timer — che qui e' gia' avvenuto. `ConflictedBy`
        # non compare ne' dopo `daemon-reload` ne' avviando un'ausiliaria
        # `oneshot` ordinaria: systemd carica pigramente e scarica subito una
        # oneshot inattiva senza riferimenti, e con essa spariscono i suoi
        # archi. L'unita' ausiliaria deve quindi restare residente.
        attestation_root = OWNERSHIP_ROOT / "preflight-attestations-v1"

        def _attestations() -> set[str]:
            if not attestation_root.is_dir():
                return set()
            return {item.name for item in attestation_root.iterdir()}

        def _check_all() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [python, "-I", "-S", preflight_path.as_posix(), "check-all"],
                capture_output=True, text=True, timeout=60,
            )

        def _edges(unit_name: str) -> set[tuple[str, str]]:
            _tcb, observed, _candidate = _capture_live_bindings(fixture)
            entry = next(
                item for item in observed.snapshot.entries
                if item.unit_name == unit_name
            )
            return {
                (edge.relation, edge.unit_name)
                for edge in entry.manager_added_edges
            }

        published_before = _attestations()
        accepted = _check_all()
        assert accepted.returncode == 0, accepted.stderr
        assert _attestations() > published_before

        # I due archi causali sono nella fotografia canonica, in entrambe le
        # direzioni, e non solo nella lettura diretta di systemd.
        assert ("Triggers", fixture.service_name) in _edges(fixture.timer_name)
        assert ("TriggeredBy", fixture.timer_name) in _edges(fixture.service_name)

        # Entrambi partecipano all'impronta effettiva: toglierne uno la
        # cambia, quindi nessuno dei due e' decorativo.
        _tcb, baseline_observation, _candidate = _capture_live_bindings(fixture)
        baseline_hash = baseline_observation.snapshot.effective_units_hash

        auxiliary_name = f"metnos-birth-c4-{namespace}.service"
        auxiliary_path = UNIT_ROOT / auxiliary_name
        auxiliary_body = (
            "[Unit]\n"
            f"Description=isolated G6-C4 conflicting unit {namespace}\n"
            f"Conflicts={fixture.service_name}\n"
            "[Service]\n"
            "Type=oneshot\n"
            "ExecStart=/bin/true\n"
            "RemainAfterExit=yes\n"
        ).encode("utf-8")
        assert not auxiliary_path.exists()
        auxiliary_installed = False
        try:
            _write_control(auxiliary_path, auxiliary_body)
            auxiliary_installed = True
            assert auxiliary_path.read_bytes() == auxiliary_body
            auxiliary_info = auxiliary_path.stat()
            assert (auxiliary_info.st_uid, auxiliary_info.st_gid) == (0, 0)
            assert stat.S_IMODE(auxiliary_info.st_mode) == 0o644
            _systemctl("daemon-reload")
            _systemctl("start", auxiliary_name)

            assert ("ConflictedBy", auxiliary_name) in _edges(
                fixture.service_name,
            )
            _tcb, drifted, _candidate = _capture_live_bindings(fixture)
            assert drifted.snapshot.effective_units_hash != baseline_hash

            # La deriva relazionale nega, e nega PRIMA di pubblicare.
            published_before_denial = _attestations()
            denied = _check_all()
            assert denied.returncode == preflight.EXIT_INVALID
            assert denied.stderr == preflight.CODE_INVALID + "\n"
            assert _attestations() == published_before_denial
        finally:
            if auxiliary_installed:
                _systemctl("stop", auxiliary_name, check=False)
                _systemctl("reset-failed", auxiliary_name, check=False)
                assert auxiliary_path.is_file() and not auxiliary_path.is_symlink()
                assert auxiliary_path.read_bytes() == auxiliary_body
                auxiliary_path.unlink()
                _systemctl("daemon-reload")
    finally:
        if installed:
            _systemctl("stop", fixture.timer_name, check=False)
            _systemctl("stop", fixture.service_name, check=False)
            _systemctl("reset-failed", fixture.service_name, check=False)
            expected = dict(fixture.unit_fragments)
            for name, content in expected.items():
                path = UNIT_ROOT / name
                if path.exists():
                    assert path.is_file() and not path.is_symlink()
                    assert path.read_bytes() == content
                    path.unlink()
            installed_preflight = ADMINISTRATIVE_ROOT / "preflight.py"
            if installed_preflight.exists():
                assert installed_preflight.read_bytes() == fixture.contents[
                    "deployment/admin/preflight.py"
                ]
                installed_preflight.unlink()
                ADMINISTRATIVE_ROOT.rmdir()
                ADMINISTRATIVE_ROOT.parent.rmdir()
            _systemctl("daemon-reload")
        if fixture.marker_path.exists():
            fixture.marker_path.unlink()
        temporary_marker = fixture.marker_path.with_suffix(".json.tmp")
        if temporary_marker.exists():
            temporary_marker.unlink()
        if fixture.marker_root.exists():
            assert tuple(fixture.marker_root.iterdir()) == ()
            fixture.marker_root.rmdir()
