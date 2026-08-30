"""Compact portable oracles for the autonomous RM-0008 preflight."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import contract_boundary_guard as canonical_guard
import executor_birth_admin_preflight as preflight
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
    birth_closed_findings as canonical_birth_closed_findings,
    discover as canonical_boundary_discover,
)


LINUX_ONLY = pytest.mark.skipif(
    sys.platform != "linux", reason="requires POSIX handle-bound filesystem proof",
)


def test_installed_preflight_rejects_candidate_self_attestation() -> None:
    root = Path(__file__).resolve().parents[2]
    sources = canonical_guard.closed_python_sources_from_root(root)
    reviewed_admin = sources["runtime/executor_birth_admin_preflight.py"]
    verified = {
        **sources,
        "deployment/admin/preflight.py": reviewed_admin,
    }

    preflight._require_compiled_source_review_v1(verified)

    altered = dict(verified)
    altered["runtime/admitted_module_v1.py"] = (
        b"def load_admitted_module_v1(executor):\n    exec(executor)\n"
    )
    attacker_root = preflight._closed_python_source_review_sha256_v1(altered)
    forged_admin = reviewed_admin.replace(
        preflight._BIRTH_CLOSED_SOURCE_REVIEW_SHA256.encode("ascii"),
        attacker_root.encode("ascii"),
    )
    altered["runtime/executor_birth_admin_preflight.py"] = forged_admin
    altered["deployment/admin/preflight.py"] = forged_admin

    with pytest.raises(preflight.PreflightError, match="source-review root"):
        preflight._require_compiled_source_review_v1(altered)


def _compiled_boundary_inventory_fixture():
    entries = {}

    def add(scope_key, role, capability, closed_exception=None):
        path, scope = scope_key.split(":", 1)
        entry = {
            "capabilities": [capability], "destination": "fixture",
            "path": path, "phase": "M4", "role": role, "scope": scope,
        }
        if closed_exception is not None:
            entry["closed_exception"] = closed_exception
        entries[scope_key] = entry

    add(BIRTH_CLOSED_OWNER, "birth_owner", "birth")
    for scope_key in sorted(BIRTH_CLOSED_COORDINATOR_STORE_OWNERS):
        add(scope_key, "store_owner", "store_write")
    capability_by_exception = {
        "localization_only": "publish_localization",
        "retirement_only": "retire",
        "offline_nonproductive_authoring": "sign",
    }
    for scope_key, exception in sorted(BIRTH_CLOSED_EXCEPTION_SCOPES.items()):
        add(
            scope_key, "offline_authoring", capability_by_exception[exception],
            exception,
        )
    return {
        "birth_closed": {
            "coordinator_store_owners": sorted(
                BIRTH_CLOSED_COORDINATOR_STORE_OWNERS,
            ),
            "exceptions": [
                {"scope": scope, "exception": exception}
                for scope, exception in sorted(BIRTH_CLOSED_EXCEPTION_SCOPES.items())
            ],
            "guard_version": BIRTH_CLOSED_GUARD_VERSION,
            "owner": BIRTH_CLOSED_OWNER,
            "schema": BIRTH_CLOSED_SCHEMA,
            "sealed_modules": list(BIRTH_CLOSED_SEALED_MODULES),
        },
        "entries": [entries[key] for key in sorted(entries)],
        "scan_roots": list(SCAN_ROOTS),
        "schema": BOUNDARY_INVENTORY_SCHEMA,
        "source_census": BIRTH_CLOSED_SOURCE_REVIEW_SHA256,
    }


def _distribution_fixture(
    tmp_path, *, release_sequence=1, previous_closed_build_id=None,
    private_key=None,
):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    release = tmp_path / "release"
    release.mkdir(mode=0o700)
    inventory = _compiled_boundary_inventory_fixture()
    contents = {
        "deployment/admin/preflight.py": b"#!/usr/bin/python3\n",
        "deployment/executor-birth-deployment-v1.json": b"{}",
        "deployment/executor-birth-service-catalog-v1.json": b"{}",
        "deployment/systemd/fixture.service": b"[Unit]\nDescription=fixture\n",
        "internal/reports/boundary.json": preflight._canonical_json(inventory),
        "requirements.lock": b"fixture==1\n",
        "runtime/__version__.py": b'__version__ = "1.2.3"\n',
        "runtime/contract_boundary_guard.py": b"VALUE = 1\n",
        "runtime/contract_store.py": b"VALUE = 1\n",
        "runtime/executor_birth.py": b"VALUE = 1\n",
        "runtime/executor_birth_distribution_manifest.py": b"VALUE = 1\n",
        "runtime/executor_birth_ownership_preflight.py": b"VALUE = 1\n",
        "runtime/sign.py": b"VALUE = 1\n",
    }
    roles = {
        "deployment/admin/preflight.py": "preflight",
        "deployment/executor-birth-deployment-v1.json": "deployment_descriptor",
        "deployment/executor-birth-service-catalog-v1.json": "service_catalog",
        "deployment/systemd/fixture.service": "service_unit",
        "internal/reports/boundary.json": "boundary_inventory",
        "requirements.lock": "dependency_lock",
        "runtime/__version__.py": "product_version",
        "runtime/contract_boundary_guard.py": "boundary_guard",
        "runtime/executor_birth_distribution_manifest.py": "preflight",
        "runtime/executor_birth_ownership_preflight.py": "preflight",
    }
    files = []
    for relative in sorted(contents, key=lambda value: value.encode("utf-8")):
        path = release / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        current = path.parent
        while current.is_relative_to(release):
            current.chmod(0o700)
            if current == release:
                break
            current = current.parent
        path.write_bytes(contents[relative])
        path.chmod(0o600)
        files.append({
            "content_hash": preflight.distribution_file_hash_v1(relative, contents[relative]),
            "path": relative, "role": roles.get(relative, "runtime_code"),
            "size": len(contents[relative]),
        })
    private = private_key or Ed25519PrivateKey.generate()
    raw_public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    key_id = (
        "distribution-ed25519-v1-sha256-"
        + preflight.hashlib.sha256(raw_public).hexdigest()
    )
    manifest = {
        "architecture": "x86_64",
        "boundary_guard_version": preflight._BIRTH_CLOSED_GUARD_VERSION,
        "boundary_inventory_hash": preflight._digest(
            preflight.BOUNDARY_INVENTORY_DOMAIN,
            contents["internal/reports/boundary.json"],
        ),
        "boundary_inventory_path": "internal/reports/boundary.json",
        "certificate_directory": "/var/lib/metnos/executor-birth",
        "closed_build_id": None,
        "files": files,
        "installation_root": (
            "/var/lib/metnos/executor-birth/releases-v1/"
            f"{release_sequence:020d}"
        ),
        "platform": "linux",
        "preflight_entrypoint": "deployment/admin/preflight.py",
        "previous_closed_build_id": previous_closed_build_id,
        "product_version": "1.2.3",
        "release_sequence": release_sequence,
        "schema_version": 1,
        "signing_key_id": key_id,
    }
    unsigned = dict(manifest)
    unsigned.pop("closed_build_id")
    manifest["closed_build_id"] = preflight._digest(
        preflight.BUILD_ID_DOMAIN, preflight._canonical_json(unsigned),
    )
    encoded = preflight._canonical_json(manifest)
    signature = private.sign(preflight.SIGNATURE_DOMAIN + encoded)
    registry = preflight._canonical_json({
        "authority": "distribution", "first_release_sequence": 1,
        "key_id": key_id, "last_release_sequence": None,
        "public_key": preflight.base64.b64encode(raw_public).decode("ascii"),
        "purposes": ["closed_distribution_v1"], "schema_version": 1,
    })
    temporary = tmp_path / "openssl-temporary"
    temporary.mkdir(mode=0o700)
    return release, encoded, signature, registry, temporary


def _invalid(callable_, *args, **kwargs) -> preflight.PreflightError:
    with pytest.raises(preflight.PreflightError) as failure:
        callable_(*args, **kwargs)
    assert failure.value.code == preflight.CODE_INVALID
    assert failure.value.exit_status == preflight.EXIT_INVALID
    return failure.value


def _recovery(callable_, *args, **kwargs) -> preflight.PreflightError:
    with pytest.raises(preflight.PreflightError) as failure:
        callable_(*args, **kwargs)
    assert failure.value.code == preflight.CODE_RECOVERY
    assert failure.value.exit_status == preflight.EXIT_RECOVERY
    return failure.value


def _write_control_file(path: Path, content: bytes, mode: int = 0o644) -> None:
    path.write_bytes(content)
    path.chmod(mode)


def _fixed_ownership_fixture(
    tmp_path: Path, *, required: bool, include_predecessor: bool | None = None,
) -> Path:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from executor_birth_cutover import CurrentReceiptProof
    from executor_birth_distribution_assembler import (
        PredecessorFileV1,
        PredecessorServiceCommandV1,
        build_predecessor_descriptor_v1,
        encode_predecessor_descriptor_v1,
    )
    from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
    from executor_birth_ownership_authorities import encode_ownership_registry_v1
    from executor_birth_ownership_chain import (
        encode_required_head,
        issue_ownership_head,
        verify_ownership_head,
    )
    from executor_birth_ownership_coordinator import (
        OwnershipCoordinatorRecordV2,
        OwnershipCoordinatorStateV1,
        SuccessorClaimV1,
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

    source_fixture = tmp_path / "distribution-fixture"
    source_fixture.mkdir(mode=0o700)
    _release, manifest, manifest_signature, distribution_registry, _temporary = (
        _distribution_fixture(source_fixture)
    )
    distribution_value = json.loads(manifest)
    digest = lambda character: "sha256:" + character * 64
    private = {
        "cutover": Ed25519PrivateKey.generate(),
        "head": Ed25519PrivateKey.generate(),
    }
    registries = {
        "distribution": distribution_registry,
        "cutover": encode_ownership_registry_v1(
            "cutover", private["cutover"].public_key(),
        ),
        "head": encode_ownership_registry_v1(
            "head", private["head"].public_key(),
        ),
    }

    root = tmp_path / "ownership"
    root.mkdir(mode=0o755)
    authority = root / "authorities-v1"
    authority.mkdir(mode=0o755)
    for kind in ("distribution", "cutover", "head"):
        _write_control_file(
            authority / f"{kind}-registry-v1.json", registries[kind],
        )
        if kind in private:
            raw_private = private[kind].private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
        else:
            raw_private = b"d" * 32
        _write_control_file(
            authority / f"{kind}-private-v1.bin", raw_private, 0o600,
        )
    for index in range(5):
        _write_control_file(
            authority / f"checkpoint-{index:03d}-v1.json",
            preflight._authority_checkpoint_v1(index),
        )

    chain = root / "chain-v1"
    chain.mkdir(mode=0o755)
    for name in ("builds-v1", "cutovers-v1", "heads-v1"):
        (chain / name).mkdir(mode=0o755)
    coordinator = root / "coordinator-v1"
    coordinator.mkdir(mode=0o755)
    claims = coordinator / "successor-claims-v1"
    transactions = coordinator / "transactions-v2"
    claims.mkdir(mode=0o755)
    transactions.mkdir(mode=0o755)

    transaction_id = digest("1")
    if required:
        closed_build_id = distribution_value["closed_build_id"]
        build_stem = closed_build_id.removeprefix("sha256:")
        _write_control_file(chain / "builds-v1" / f"{build_stem}.json", manifest)
        _write_control_file(
            chain / "builds-v1" / f"{build_stem}.sig", manifest_signature,
        )

        proof = CurrentReceiptProof(
            (("fixture.contract", digest("2")),),
            {("fixture.contract", digest("2")): digest("3")},
        )
        cutover_encoded, cutover_signature = issue_ownership_cutover_certificate(
            proof=proof, previous_cutover_id=None, request_id=digest("4"),
            signing_key_id=preflight._decode_ownership_registry_v1(
                registries["cutover"], "cutover",
            ).key_id,
            maintenance_evidence_hash=digest("5"),
            boundary_inventory_hash=distribution_value["boundary_inventory_hash"],
            boundary_guard_version=distribution_value["boundary_guard_version"],
            closed_build_id=closed_build_id, private_key=private["cutover"],
        )
        from executor_birth_ownership_authorities import decode_ownership_registry_v1
        cutover_registry = decode_ownership_registry_v1(
            registries["cutover"], expected_kind="cutover",
        )
        cutover = verify_ownership_cutover_certificate(
            cutover_encoded, cutover_signature, registry=cutover_registry,
        )
        cutover_stem = cutover.cutover_id.removeprefix("sha256:")
        for directory in (root, chain / "cutovers-v1"):
            basename = (
                "ownership-cutover-v1" if directory == root else cutover_stem
            )
            _write_control_file(directory / f"{basename}.json", cutover_encoded)
            _write_control_file(directory / f"{basename}.sig", cutover_signature)

        head_encoded, head_signature = issue_ownership_head(
            release_sequence=1, cutover_id=cutover.cutover_id,
            closed_build_id=closed_build_id, previous_head_id=None,
            signing_key_id=preflight._decode_ownership_registry_v1(
                registries["head"], "head",
            ).key_id,
            private_key=private["head"],
        )
        head_registry = decode_ownership_registry_v1(
            registries["head"], expected_kind="head",
        )
        head = verify_ownership_head(
            head_encoded, head_signature, registry=head_registry,
        )
        head_stem = f"{head.release_sequence:020d}-{cutover_stem}"
        _write_control_file(chain / "heads-v1" / f"{head_stem}.json", head_encoded)
        _write_control_file(chain / "heads-v1" / f"{head_stem}.sig", head_signature)
        _write_control_file(chain / "required-head-v1.bin", encode_required_head(head))

        source_id = digest("6")
        claim_value = {
            "schema_version": 1, "previous_head_id": None,
            "release_sequence": 1, "request_id": digest("4"),
            "source_id": source_id, "closed_build_id": closed_build_id,
        }
        claim_value["claim_id"] = _successor_claim_id_v1(claim_value)
        claim = SuccessorClaimV1(
            claim_value["claim_id"], None, 1, claim_value["request_id"],
            source_id, closed_build_id,
        )
        _write_control_file(claims / "initial.json", claim.encode())

        install_value = {
            "schema_version": 1, "request_id": claim.request_id,
            "source_id": source_id, "closed_build_id": closed_build_id,
            "release_sequence": 1, "previous_head_id": None,
            "successor_claim_id": claim.claim_id,
            "deployment_descriptor_id": digest("7"),
            "service_coverage_hash": digest("8"),
            "administrative_bundle_hash": digest("9"),
        }
        transaction_id = _install_transaction_id_v1(install_value)
        maintenance = canonical_maintenance_proof(
            source="inactive_http_and_inactive_sidecar",
            units=tuple({
                "scope": scope, "unit": unit, "load_state": "loaded",
                "active_state": "inactive", "main_pid": 0,
            } for scope, unit in MAINTENANCE_TARGETS_V1),
        )
        maintenance_hash = maintenance_evidence_hash(maintenance)
        transaction = transactions / claim.request_id
        transaction.mkdir(mode=0o755)
        previous_hash = None
        for sequence in range(6):
            record = OwnershipCoordinatorRecordV2(
                sequence=sequence,
                state=tuple(OwnershipCoordinatorStateV1)[sequence],
                previous_record_sha256=previous_hash,
                request_id=claim.request_id, previous_closed_build_id=None,
                previous_cutover_id=None, closed_build_id=closed_build_id,
                distribution_payload_hash=digest("a"),
                distribution_signature_hash=digest("b"),
                boundary_inventory_hash=distribution_value[
                    "boundary_inventory_hash"
                ],
                boundary_guard_version=distribution_value[
                    "boundary_guard_version"
                ],
                source_id=source_id, successor_claim_id=claim.claim_id,
                deployment_descriptor_id=install_value[
                    "deployment_descriptor_id"
                ],
                install_transaction_id=transaction_id,
                release_sequence=1, previous_head_id=None,
                service_coverage_hash=install_value["service_coverage_hash"],
                administrative_bundle_hash=install_value[
                    "administrative_bundle_hash"
                ],
                current_proof=proof if sequence >= 1 else None,
                maintenance_before_hash=(
                    maintenance_hash if sequence >= 1 else None
                ),
                maintenance_after_hash=(
                    maintenance_hash if sequence >= 1 else None
                ),
                maintenance_proof=maintenance if sequence >= 1 else None,
                startup_prerequisite_id=digest("c") if sequence >= 2 else None,
                startup_prerequisite_digest=(
                    digest("d") if sequence >= 2 else None
                ),
                cutover_id=cutover.cutover_id if sequence >= 2 else None,
                catalog_id=cutover.catalog_id if sequence >= 2 else None,
                certificate_payload_hash=(digest("e") if sequence >= 2 else None),
                certificate_signature_hash=(
                    digest("f") if sequence >= 2 else None
                ),
                installed_tree_hash=digest("1") if sequence >= 4 else None,
                head_id=head.head_id if sequence >= 5 else None,
                head_payload_hash=digest("2") if sequence >= 5 else None,
                head_signature_hash=digest("3") if sequence >= 5 else None,
                required_head_frame_hash=digest("4") if sequence >= 5 else None,
                verified_chain_head_id=head.head_id if sequence >= 5 else None,
                preflight_attestation_hash=None,
            )
            encoded = record.encode()
            _write_control_file(
                transaction / f"record-{sequence:03d}-v2.json", encoded,
            )
            previous_hash = _record_hash_v2(encoded)

    predecessor_descriptor = build_predecessor_descriptor_v1(
        transaction_id=transaction_id, installation_root="/opt/metnos",
        files=(PredecessorFileV1("runtime/worker.py", 1, digest("a")),),
        service_commands=(PredecessorServiceCommandV1(
            "idle", "none", None, None, None, (), None, (),
        ),),
        administrative_bundle_hash=digest("b"),
        service_catalog_id=digest("c"), service_coverage_hash=digest("d"),
    )
    if include_predecessor is None:
        include_predecessor = required
    if include_predecessor:
        _write_control_file(
            root / "predecessor-v1.json",
            encode_predecessor_descriptor_v1(predecessor_descriptor),
        )
    return root


def _authenticated_fixed_ownership_fixture(
    tmp_path: Path, *, release_count: int = 1, final_record_sequence: int = 5,
    chain_mutation: str | None = None, required_after_cas: bool = False,
) -> tuple[Path, Path]:
    """Build signed, mutually bound durable bytes; no live state is asserted."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from executor_birth_cutover import CurrentReceiptProof
    from executor_birth_distribution_assembler import (
        PredecessorFileV1,
        PredecessorServiceCommandV1,
        build_predecessor_descriptor_v1,
        encode_predecessor_descriptor_v1,
    )
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

    if (
        release_count not in (1, 2)
        or final_record_sequence not in (4, 5, 6)
        or (
            release_count == 1 and final_record_sequence == 4
            and not required_after_cas
        )
        or chain_mutation not in {
            None, "previous_build", "previous_cutover", "previous_head",
        }
        or (chain_mutation is not None and release_count != 2)
        or (required_after_cas and final_record_sequence != 4)
    ):
        raise ValueError("authenticated ownership fixture regime")
    root = _fixed_ownership_fixture(
        tmp_path, required=False, include_predecessor=False,
    )
    authority = root / "authorities-v1"
    private = {
        kind: Ed25519PrivateKey.generate()
        for kind in ("distribution", "cutover", "head")
    }
    distributions = []
    previous_build_id = None
    for release_sequence in range(1, release_count + 1):
        source = tmp_path / f"authenticated-distribution-{release_sequence}"
        source.mkdir(mode=0o700)
        manifest_predecessor = previous_build_id
        if release_sequence == 2 and chain_mutation == "previous_build":
            manifest_predecessor = preflight._raw_sha256_v1(
                b"wrong-previous-build",
            )
        _release, encoded, signature, registry, _temporary = _distribution_fixture(
            source, release_sequence=release_sequence,
            previous_closed_build_id=manifest_predecessor,
            private_key=private["distribution"],
        )
        distributions.append((encoded, signature))
        previous_build_id = json.loads(encoded)["closed_build_id"]
    registries = {
        "distribution": registry,
        "cutover": encode_ownership_registry_v1(
            "cutover", private["cutover"].public_key(),
        ),
        "head": encode_ownership_registry_v1(
            "head", private["head"].public_key(),
        ),
    }
    for kind, encoded_registry in registries.items():
        _write_control_file(
            authority / f"{kind}-registry-v1.json", encoded_registry,
        )
        raw_private = private[kind].private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        _write_control_file(
            authority / f"{kind}-private-v1.bin", raw_private, 0o600,
        )

    decoded_registries = {
        kind: decode_ownership_registry_v1(
            encoded, expected_kind=kind,
        )
        for kind, encoded in registries.items()
    }
    chain = root / "chain-v1"
    claims = root / "coordinator-v1/successor-claims-v1"
    transactions = root / "coordinator-v1/transactions-v2"
    proof = CurrentReceiptProof(
        (("fixture.contract", "sha256:" + "2" * 64),),
        {
            ("fixture.contract", "sha256:" + "2" * 64):
            "sha256:" + "3" * 64,
        },
    )
    maintenance = canonical_maintenance_proof(
        source="inactive_http_and_inactive_sidecar",
        units=tuple({
            "scope": scope, "unit": unit, "load_state": "loaded",
            "active_state": "inactive", "main_pid": 0,
        } for scope, unit in MAINTENANCE_TARGETS_V1),
    )
    maintenance_hash = maintenance_evidence_hash(maintenance)
    previous_cutover_id = None
    previous_head_id = None
    latest_head = None
    issued_heads = []
    initial_transaction = None
    initial_catalog_id = None
    initial_coverage_hash = None
    bundle_hash = preflight._raw_sha256_v1(b"administrative-bundle-v1")

    for release_sequence, (manifest, manifest_signature) in enumerate(
        distributions, start=1,
    ):
        distribution = json.loads(manifest)
        closed_build_id = distribution["closed_build_id"]
        build_stem = closed_build_id.removeprefix("sha256:")
        _write_control_file(
            chain / "builds-v1" / f"{build_stem}.json", manifest,
        )
        _write_control_file(
            chain / "builds-v1" / f"{build_stem}.sig", manifest_signature,
        )
        transaction_previous_cutover_id = previous_cutover_id
        if release_sequence == 2 and chain_mutation == "previous_cutover":
            transaction_previous_cutover_id = preflight._raw_sha256_v1(
                b"wrong-previous-cutover",
            )
        request_id = preflight._coordinator_request_id_v1(
            closed_build_id, distribution["previous_closed_build_id"],
            transaction_previous_cutover_id,
        )
        cutover_encoded, cutover_signature = issue_ownership_cutover_certificate(
            proof=proof, previous_cutover_id=transaction_previous_cutover_id,
            request_id=request_id,
            signing_key_id=next(iter(decoded_registries["cutover"].keys)),
            maintenance_evidence_hash=maintenance_hash,
            boundary_inventory_hash=distribution["boundary_inventory_hash"],
            boundary_guard_version=distribution["boundary_guard_version"],
            closed_build_id=closed_build_id, private_key=private["cutover"],
        )
        cutover = verify_ownership_cutover_certificate(
            cutover_encoded, cutover_signature,
            registry=decoded_registries["cutover"],
        )
        cutover_stem = cutover.cutover_id.removeprefix("sha256:")
        _write_control_file(
            chain / "cutovers-v1" / f"{cutover_stem}.json", cutover_encoded,
        )
        _write_control_file(
            chain / "cutovers-v1" / f"{cutover_stem}.sig", cutover_signature,
        )
        if release_sequence == 1:
            _write_control_file(
                root / "ownership-cutover-v1.json", cutover_encoded,
            )
            _write_control_file(
                root / "ownership-cutover-v1.sig", cutover_signature,
            )

        transaction_previous_head_id = previous_head_id
        if release_sequence == 2 and chain_mutation == "previous_head":
            transaction_previous_head_id = preflight._raw_sha256_v1(
                b"wrong-previous-head",
            )
        head_encoded, head_signature = issue_ownership_head(
            release_sequence=release_sequence, cutover_id=cutover.cutover_id,
            closed_build_id=closed_build_id,
            previous_head_id=transaction_previous_head_id,
            signing_key_id=next(iter(decoded_registries["head"].keys)),
            private_key=private["head"],
        )
        head = verify_ownership_head(
            head_encoded, head_signature, registry=decoded_registries["head"],
        )
        head_stem = f"{release_sequence:020d}-{cutover_stem}"
        _write_control_file(
            chain / "heads-v1" / f"{head_stem}.json", head_encoded,
        )
        _write_control_file(
            chain / "heads-v1" / f"{head_stem}.sig", head_signature,
        )
        required_frame = encode_required_head(head)

        source_id = preflight._raw_sha256_v1(
            f"source-{release_sequence}".encode("ascii"),
        )
        claim_value = {
            "schema_version": 1,
            "previous_head_id": transaction_previous_head_id,
            "release_sequence": release_sequence, "request_id": request_id,
            "source_id": source_id, "closed_build_id": closed_build_id,
        }
        claim_value["claim_id"] = _successor_claim_id_v1(claim_value)
        claim = SuccessorClaimV1(
            claim_value["claim_id"], transaction_previous_head_id,
            release_sequence,
            request_id, source_id, closed_build_id,
        )
        claim_name = (
            "initial.json" if transaction_previous_head_id is None
            else transaction_previous_head_id.removeprefix("sha256:") + ".json"
        )
        _write_control_file(claims / claim_name, claim.encode())

        deployment_id = preflight._raw_sha256_v1(
            f"deployment-{release_sequence}".encode("ascii"),
        )
        coverage_hash = preflight._raw_sha256_v1(
            f"coverage-{release_sequence}".encode("ascii"),
        )
        install_value = {
            "schema_version": 1, "request_id": request_id,
            "source_id": source_id, "closed_build_id": closed_build_id,
            "release_sequence": release_sequence,
            "previous_head_id": transaction_previous_head_id,
            "successor_claim_id": claim.claim_id,
            "deployment_descriptor_id": deployment_id,
            "service_coverage_hash": coverage_hash,
            "administrative_bundle_hash": bundle_hash,
        }
        transaction_id = _install_transaction_id_v1(install_value)
        transaction = transactions / request_id
        transaction.mkdir(mode=0o755)
        terminal = 6 if release_sequence < release_count else final_record_sequence
        previous_record_hash = None
        for record_sequence in range(terminal + 1):
            record = OwnershipCoordinatorRecordV2(
                sequence=record_sequence,
                state=tuple(OwnershipCoordinatorStateV1)[record_sequence],
                previous_record_sha256=previous_record_hash,
                request_id=request_id,
                previous_closed_build_id=distribution[
                    "previous_closed_build_id"
                ],
                previous_cutover_id=transaction_previous_cutover_id,
                closed_build_id=closed_build_id,
                distribution_payload_hash=preflight._raw_sha256_v1(manifest),
                distribution_signature_hash=preflight._raw_sha256_v1(
                    manifest_signature,
                ),
                boundary_inventory_hash=distribution[
                    "boundary_inventory_hash"
                ],
                boundary_guard_version=distribution["boundary_guard_version"],
                source_id=source_id, successor_claim_id=claim.claim_id,
                deployment_descriptor_id=deployment_id,
                install_transaction_id=transaction_id,
                release_sequence=release_sequence,
                previous_head_id=transaction_previous_head_id,
                service_coverage_hash=coverage_hash,
                administrative_bundle_hash=bundle_hash,
                current_proof=proof if record_sequence >= 1 else None,
                maintenance_before_hash=(
                    maintenance_hash if record_sequence >= 1 else None
                ),
                maintenance_after_hash=(
                    maintenance_hash if record_sequence >= 1 else None
                ),
                maintenance_proof=(
                    maintenance if record_sequence >= 1 else None
                ),
                startup_prerequisite_id=(
                    preflight._raw_sha256_v1(b"prerequisite")
                    if record_sequence >= 2 else None
                ),
                startup_prerequisite_digest=(
                    preflight._raw_sha256_v1(b"prerequisite-evidence")
                    if record_sequence >= 2 else None
                ),
                cutover_id=(
                    cutover.cutover_id if record_sequence >= 2 else None
                ),
                catalog_id=(cutover.catalog_id if record_sequence >= 2 else None),
                certificate_payload_hash=(
                    preflight._raw_sha256_v1(cutover_encoded)
                    if record_sequence >= 2 else None
                ),
                certificate_signature_hash=(
                    preflight._raw_sha256_v1(cutover_signature)
                    if record_sequence >= 2 else None
                ),
                installed_tree_hash=(
                    preflight._raw_sha256_v1(b"installed-tree")
                    if record_sequence >= 4 else None
                ),
                head_id=head.head_id if record_sequence >= 5 else None,
                head_payload_hash=(
                    preflight._framed_sha256_v1(
                        preflight.HEAD_PAYLOAD_HASH_DOMAIN_V2, head_encoded,
                    ) if record_sequence >= 5 else None
                ),
                head_signature_hash=(
                    preflight._framed_sha256_v1(
                        preflight.HEAD_SIGNATURE_HASH_DOMAIN_V2, head_signature,
                    ) if record_sequence >= 5 else None
                ),
                required_head_frame_hash=(
                    preflight._framed_sha256_v1(
                        preflight.REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2,
                        required_frame,
                    ) if record_sequence >= 5 else None
                ),
                verified_chain_head_id=(
                    head.head_id if record_sequence >= 5 else None
                ),
                preflight_attestation_hash=(
                    preflight._raw_sha256_v1(b"structural-attestation-reference")
                    if record_sequence >= 6 else None
                ),
            )
            encoded_record = record.encode()
            _write_control_file(
                transaction / f"record-{record_sequence:03d}-v2.json",
                encoded_record,
            )
            previous_record_hash = _record_hash_v2(encoded_record)

        previous_cutover_id = cutover.cutover_id
        previous_head_id = head.head_id
        latest_head = head
        issued_heads.append(head)
        if release_sequence == 1:
            initial_transaction = transaction_id
            initial_catalog_id = cutover.catalog_id
            initial_coverage_hash = coverage_hash

    assert latest_head is not None and initial_transaction is not None
    assert initial_catalog_id is not None and initial_coverage_hash is not None
    if final_record_sequence >= 5 or required_after_cas:
        selected_head = issued_heads[-1]
    else:
        selected_head = issued_heads[-2]
    _write_control_file(
        chain / "required-head-v1.bin", encode_required_head(selected_head),
    )
    predecessor = build_predecessor_descriptor_v1(
        transaction_id=initial_transaction, installation_root="/opt/metnos",
        files=(PredecessorFileV1(
            "runtime/worker.py", 1, preflight._raw_sha256_v1(b"worker"),
        ),),
        service_commands=(PredecessorServiceCommandV1(
            "idle", "none", None, None, None, (), None, (),
        ),),
        administrative_bundle_hash=bundle_hash,
        service_catalog_id=initial_catalog_id,
        service_coverage_hash=initial_coverage_hash,
    )
    _write_control_file(
        root / "predecessor-v1.json",
        encode_predecessor_descriptor_v1(predecessor),
    )
    temporary = tmp_path / "authenticated-openssl-temporary"
    temporary.mkdir(mode=0o700)
    return root, temporary


def _rewrite_v2_transactions(root: Path, mutate) -> None:
    """Rewrite a valid V2 prefix after one semantic test mutation."""
    for transaction in sorted(
        (root / "coordinator-v1/transactions-v2").iterdir(),
        key=lambda path: path.name,
    ):
        previous_hash = None
        for path in sorted(transaction.glob("record-*-v2.json")):
            value = json.loads(path.read_bytes())
            mutate(value)
            value["previous_record_sha256"] = previous_hash
            encoded = preflight._canonical_json(value)
            _write_control_file(path, encoded)
            previous_hash = preflight._coordinator_record_hash_v2(encoded)


def _truncate_to_pre_chain_prefix(root: Path, terminal: int) -> None:
    for directory_name in ("builds-v1", "cutovers-v1", "heads-v1"):
        for path in (root / "chain-v1" / directory_name).iterdir():
            path.unlink()
    for path in (
        root / "ownership-cutover-v1.json",
        root / "ownership-cutover-v1.sig",
        root / "chain-v1/required-head-v1.bin",
    ):
        path.unlink()
    transaction = next((root / "coordinator-v1/transactions-v2").iterdir())
    for path in transaction.glob("record-*-v2.json"):
        if int(path.name[7:10]) > terminal:
            path.unlink()
    if terminal < 0:
        transaction.rmdir()


@LINUX_ONLY
@pytest.mark.parametrize(
    ("required", "has_predecessor"),
    [(False, False), (False, True), (True, True)],
)
def test_fixed_ownership_capture_accepts_two_structural_regimes(
    tmp_path: Path, required: bool, has_predecessor: bool,
) -> None:
    root = _fixed_ownership_fixture(
        tmp_path, required=required, include_predecessor=has_predecessor,
    )
    observed = preflight._capture_fixed_ownership_state_for_test_v1(root)
    candidate = observed.candidate
    assert type(observed) is preflight._CapturedFixedOwnershipStateForTestV1
    assert tuple(item.authority for item in candidate.registries) == (
        "distribution", "cutover", "head",
    )
    if required:
        assert candidate.predecessor is not None
        assert candidate.anchor is not None
        assert candidate.required_head is not None
        assert len(candidate.builds) == len(candidate.cutovers) == len(candidate.heads) == 1
        assert len(candidate.claims) == len(candidate.transactions) == 1
        assert candidate.transactions[0].decoded_prefix is not None
        assert candidate.transactions[0].decoded_prefix.records[-1].sequence == 5
    else:
        assert candidate.anchor is candidate.required_head is None
        assert not candidate.builds and not candidate.claims
        assert (candidate.predecessor is not None) is has_predecessor


@LINUX_ONLY
def test_fixed_ownership_authentication_accepts_empty_initial_state(
    tmp_path: Path,
) -> None:
    root = _fixed_ownership_fixture(
        tmp_path, required=False, include_predecessor=False,
    )
    temporary = tmp_path / "initial-authentication-temporary"
    temporary.mkdir(mode=0o700)

    result = preflight._authenticate_fixed_ownership_snapshot_for_test_v1(
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )

    assert type(result) is preflight._AuthenticatedFixedOwnershipSnapshotForTestV1
    assert result.snapshot.required_head is None
    assert not result.snapshot.builds
    assert not result.snapshot.transactions


@LINUX_ONLY
@pytest.mark.parametrize(
    ("release_count", "final_record_sequence", "required_sequence"),
    ((1, 5, 1), (1, 6, 1), (2, 4, 1), (2, 5, 2), (2, 6, 2)),
)
def test_fixed_ownership_authentication_accepts_coherent_durable_graphs(
    tmp_path: Path, release_count: int, final_record_sequence: int,
    required_sequence: int,
) -> None:
    root, temporary = _authenticated_fixed_ownership_fixture(
        tmp_path, release_count=release_count,
        final_record_sequence=final_record_sequence,
    )

    result = preflight._authenticate_fixed_ownership_snapshot_for_test_v1(
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )

    snapshot = result.snapshot
    assert type(result) is preflight._AuthenticatedFixedOwnershipSnapshotForTestV1
    assert snapshot.required_head is not None
    assert snapshot.required_head.release_sequence == required_sequence
    assert len(snapshot.heads) == release_count
    assert len(snapshot.transactions) == release_count
    assert snapshot.transactions[-1].prefix.records[-1].sequence == (
        final_record_sequence
    )


@LINUX_ONLY
@pytest.mark.parametrize("prefix_state", ("pending", "prepared", "receipts"))
def test_fixed_ownership_authentication_accepts_pre_chain_recovery_prefixes(
    tmp_path: Path, prefix_state: str,
) -> None:
    root, temporary = _authenticated_fixed_ownership_fixture(tmp_path)
    terminal = {"pending": -1, "prepared": 0, "receipts": 1}[prefix_state]
    _truncate_to_pre_chain_prefix(root, terminal)
    if prefix_state != "receipts":
        (root / "predecessor-v1.json").unlink()

    result = preflight._authenticate_fixed_ownership_snapshot_for_test_v1(
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )

    assert result.snapshot.required_head is None
    if prefix_state == "pending":
        assert len(result.snapshot.pending_claims) == 1
        assert not result.snapshot.transactions
    else:
        assert result.snapshot.transactions[0].prefix.records[-1].sequence == terminal


@LINUX_ONLY
@pytest.mark.parametrize("release_count", (1, 2))
def test_fixed_ownership_authentication_accepts_required_head_cas_boundary(
    tmp_path: Path, release_count: int,
) -> None:
    root, temporary = _authenticated_fixed_ownership_fixture(
        tmp_path, release_count=release_count, final_record_sequence=4,
        required_after_cas=True,
    )

    result = preflight._authenticate_fixed_ownership_snapshot_for_test_v1(
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )

    assert result.snapshot.required_head is not None
    assert result.snapshot.required_head.release_sequence == release_count
    assert result.snapshot.transactions[-1].prefix.records[-1].sequence == 4


@LINUX_ONLY
@pytest.mark.parametrize("bootstrap_sequence", (2, 4))
def test_fixed_ownership_authentication_accepts_bootstrap_chain_frontiers(
    tmp_path: Path, bootstrap_sequence: int,
) -> None:
    root, temporary = _authenticated_fixed_ownership_fixture(tmp_path)
    transaction = next((root / "coordinator-v1/transactions-v2").iterdir())
    for path in transaction.glob("record-*-v2.json"):
        if int(path.name[7:10]) > bootstrap_sequence:
            path.unlink()
    (root / "chain-v1/required-head-v1.bin").unlink()
    if bootstrap_sequence == 2:
        for directory_name in ("builds-v1", "heads-v1"):
            for path in (root / "chain-v1" / directory_name).iterdir():
                path.unlink()

    result = preflight._authenticate_fixed_ownership_snapshot_for_test_v1(
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )

    assert result.snapshot.anchor is not None
    assert result.snapshot.required_head is None
    assert result.snapshot.transactions[0].prefix.records[-1].sequence == (
        bootstrap_sequence
    )


@LINUX_ONLY
def test_fixed_ownership_authentication_accepts_build_verified_before_archives(
    tmp_path: Path,
) -> None:
    root, temporary = _authenticated_fixed_ownership_fixture(
        tmp_path, release_count=2, final_record_sequence=4,
    )
    for path in tuple((root / "chain-v1/builds-v1").glob("*.json")):
        if json.loads(path.read_bytes())["release_sequence"] == 2:
            path.with_suffix(".sig").unlink()
            path.unlink()
    for path in tuple((root / "chain-v1/heads-v1").glob("*.json")):
        if json.loads(path.read_bytes())["release_sequence"] == 2:
            path.with_suffix(".sig").unlink()
            path.unlink()

    result = preflight._authenticate_fixed_ownership_snapshot_for_test_v1(
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )

    assert result.snapshot.required_head is not None
    assert result.snapshot.required_head.release_sequence == 1
    assert len(result.snapshot.builds) == len(result.snapshot.heads) == 1
    assert result.snapshot.transactions[-1].prefix.records[-1].sequence == 4


@LINUX_ONLY
def test_fixed_ownership_authentication_rejects_missing_predecessor_at_receipts(
    tmp_path: Path,
) -> None:
    root, temporary = _authenticated_fixed_ownership_fixture(tmp_path)
    _truncate_to_pre_chain_prefix(root, 1)
    (root / "predecessor-v1.json").unlink()

    _recovery(
        preflight._authenticate_fixed_ownership_snapshot_for_test_v1,
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )


@LINUX_ONLY
def test_fixed_ownership_authentication_rejects_orphan_predecessor(
    tmp_path: Path,
) -> None:
    root = _fixed_ownership_fixture(
        tmp_path, required=False, include_predecessor=True,
    )
    temporary = tmp_path / "orphan-predecessor-temporary"
    temporary.mkdir(mode=0o700)

    _recovery(
        preflight._authenticate_fixed_ownership_snapshot_for_test_v1,
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )


@LINUX_ONLY
@pytest.mark.parametrize("object_kind", ("builds-v1", "cutovers-v1", "heads-v1"))
def test_fixed_ownership_authentication_rejects_altered_signatures(
    tmp_path: Path, object_kind: str,
) -> None:
    root, temporary = _authenticated_fixed_ownership_fixture(tmp_path)
    signature_path = next((root / "chain-v1" / object_kind).glob("*.sig"))
    altered = bytearray(signature_path.read_bytes())
    altered[-1] ^= 1
    altered_signature = bytes(altered)
    _write_control_file(signature_path, altered_signature)

    if object_kind == "builds-v1":
        def mutate(value):
            value["distribution_signature_hash"] = preflight._raw_sha256_v1(
                altered_signature,
            )
    elif object_kind == "cutovers-v1":
        _write_control_file(
            root / "ownership-cutover-v1.sig", altered_signature,
        )

        def mutate(value):
            if value["sequence"] >= 2:
                value["certificate_signature_hash"] = preflight._raw_sha256_v1(
                    altered_signature,
                )
    else:
        head_path = signature_path.with_suffix(".json")
        head_encoded = head_path.read_bytes()
        required_frame = (
            preflight.REQUIRED_HEAD_MAGIC_V1
            + len(head_encoded).to_bytes(4, "big")
            + head_encoded + altered_signature
        )
        _write_control_file(
            root / "chain-v1/required-head-v1.bin", required_frame,
        )

        def mutate(value):
            if value["sequence"] >= 5:
                value["head_signature_hash"] = preflight._framed_sha256_v1(
                    preflight.HEAD_SIGNATURE_HASH_DOMAIN_V2,
                    altered_signature,
                )
                value["required_head_frame_hash"] = preflight._framed_sha256_v1(
                    preflight.REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2,
                    required_frame,
                )
    _rewrite_v2_transactions(root, mutate)

    _recovery(
        preflight._authenticate_fixed_ownership_snapshot_for_test_v1,
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )


@LINUX_ONLY
def test_fixed_ownership_authentication_rejects_required_head_rollback(
    tmp_path: Path,
) -> None:
    root, temporary = _authenticated_fixed_ownership_fixture(
        tmp_path, release_count=2, final_record_sequence=5,
    )
    first_head_path = sorted((root / "chain-v1/heads-v1").glob("*.json"))[0]
    first_signature_path = first_head_path.with_suffix(".sig")
    first_head = preflight._decode_ownership_head_v1(
        first_head_path.read_bytes(), first_signature_path.read_bytes(),
    )
    first_frame = (
        preflight.REQUIRED_HEAD_MAGIC_V1
        + len(first_head.encoded).to_bytes(4, "big")
        + first_head.encoded + first_head.signature
    )
    _write_control_file(
        root / "chain-v1/required-head-v1.bin",
        first_frame,
    )

    _recovery(
        preflight._authenticate_fixed_ownership_snapshot_for_test_v1,
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )


@LINUX_ONLY
def test_fixed_ownership_authentication_rejects_claim_transaction_mismatch(
    tmp_path: Path,
) -> None:
    from executor_birth_ownership_coordinator import (
        _install_transaction_id_v1,
        _record_hash_v2,
    )

    root, temporary = _authenticated_fixed_ownership_fixture(tmp_path)
    transaction = next((root / "coordinator-v1/transactions-v2").iterdir())
    record_paths = sorted(transaction.glob("record-*-v2.json"))
    values = [json.loads(path.read_bytes()) for path in record_paths]
    changed_source_id = preflight._raw_sha256_v1(b"different-source")
    first = values[0]
    install_value = {
        "schema_version": 1, "request_id": first["request_id"],
        "source_id": changed_source_id,
        "closed_build_id": first["closed_build_id"],
        "release_sequence": first["release_sequence"],
        "previous_head_id": first["previous_head_id"],
        "successor_claim_id": first["successor_claim_id"],
        "deployment_descriptor_id": first["deployment_descriptor_id"],
        "service_coverage_hash": first["service_coverage_hash"],
        "administrative_bundle_hash": first["administrative_bundle_hash"],
    }
    changed_transaction_id = _install_transaction_id_v1(install_value)
    previous_hash = None
    for path, value in zip(record_paths, values, strict=True):
        value["source_id"] = changed_source_id
        value["install_transaction_id"] = changed_transaction_id
        value["previous_record_sha256"] = previous_hash
        encoded = preflight._canonical_json(value)
        _write_control_file(path, encoded)
        previous_hash = _record_hash_v2(encoded)

    _recovery(
        preflight._authenticate_fixed_ownership_snapshot_for_test_v1,
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )


@LINUX_ONLY
def test_fixed_ownership_authentication_rejects_predecessor_mismatch(
    tmp_path: Path,
) -> None:
    from executor_birth_distribution_assembler import (
        build_predecessor_descriptor_v1,
        decode_predecessor_descriptor_v1,
        encode_predecessor_descriptor_v1,
    )

    root, temporary = _authenticated_fixed_ownership_fixture(tmp_path)
    predecessor_path = root / "predecessor-v1.json"
    predecessor = decode_predecessor_descriptor_v1(predecessor_path.read_bytes())
    changed = build_predecessor_descriptor_v1(
        transaction_id=predecessor.transaction_id,
        installation_root=predecessor.installation_root,
        files=predecessor.files, service_commands=predecessor.service_commands,
        administrative_bundle_hash=predecessor.administrative_bundle_hash,
        service_catalog_id=predecessor.service_catalog_id,
        service_coverage_hash=preflight._raw_sha256_v1(b"wrong-coverage"),
    )
    _write_control_file(
        predecessor_path, encode_predecessor_descriptor_v1(changed),
    )

    _recovery(
        preflight._authenticate_fixed_ownership_snapshot_for_test_v1,
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )


@LINUX_ONLY
@pytest.mark.parametrize(
    "chain_mutation",
    ("previous_build", "previous_cutover", "previous_head"),
)
def test_fixed_ownership_authentication_rejects_successor_chain_mutants(
    tmp_path: Path, chain_mutation: str,
) -> None:
    root, temporary = _authenticated_fixed_ownership_fixture(
        tmp_path, release_count=2, final_record_sequence=5,
        chain_mutation=chain_mutation,
    )

    _recovery(
        preflight._authenticate_fixed_ownership_snapshot_for_test_v1,
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )


@LINUX_ONLY
@pytest.mark.parametrize("orphan_kind", ("build", "cutover"))
def test_fixed_ownership_authentication_rejects_isolated_orphan_archives(
    tmp_path: Path, orphan_kind: str,
) -> None:
    root, temporary = _authenticated_fixed_ownership_fixture(
        tmp_path, release_count=2, final_record_sequence=4,
    )
    claims = root / "coordinator-v1/successor-claims-v1"
    next(path for path in claims.iterdir() if path.name != "initial.json").unlink()
    release_two_build = next(
        path for path in (root / "chain-v1/builds-v1").glob("*.json")
        if json.loads(path.read_bytes())["release_sequence"] == 2
    )
    release_two_build_id = json.loads(
        release_two_build.read_bytes(),
    )["closed_build_id"]
    release_two_head = next(
        path for path in (root / "chain-v1/heads-v1").glob("*.json")
        if json.loads(path.read_bytes())["release_sequence"] == 2
    )
    release_two_cutover = next(
        path for path in (root / "chain-v1/cutovers-v1").glob("*.json")
        if json.loads(path.read_bytes())["closed_build_id"] == release_two_build_id
    )
    transaction_root = root / "coordinator-v1/transactions-v2"
    for transaction in transaction_root.iterdir():
        first = json.loads((transaction / "record-000-v2.json").read_bytes())
        if first["release_sequence"] == 2:
            for path in transaction.iterdir():
                path.unlink()
            transaction.rmdir()
            break

    release_two_head.with_suffix(".sig").unlink()
    release_two_head.unlink()
    if orphan_kind == "build":
        release_two_cutover.with_suffix(".sig").unlink()
        release_two_cutover.unlink()
    else:
        release_two_build.with_suffix(".sig").unlink()
        release_two_build.unlink()

    failure = _recovery(
        preflight._authenticate_fixed_ownership_snapshot_for_test_v1,
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )
    assert failure.detail == f"orphan {orphan_kind} archive"


@LINUX_ONLY
@pytest.mark.parametrize(
    "binding",
    ("distribution_payload_hash", "certificate_payload_hash", "head_payload_hash"),
)
def test_fixed_ownership_authentication_rejects_artifact_binding_mutants(
    tmp_path: Path, binding: str,
) -> None:
    root, temporary = _authenticated_fixed_ownership_fixture(tmp_path)

    def mutate(value):
        if (
            binding == "distribution_payload_hash"
            or binding == "certificate_payload_hash" and value["sequence"] >= 2
            or binding == "head_payload_hash" and value["sequence"] >= 5
        ):
            value[binding] = preflight._raw_sha256_v1(
                ("wrong-" + binding).encode("ascii"),
            )

    _rewrite_v2_transactions(root, mutate)
    _recovery(
        preflight._authenticate_fixed_ownership_snapshot_for_test_v1,
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )


@LINUX_ONLY
def test_fixed_ownership_authentication_accepts_bound_legacy_disposition(
    tmp_path: Path,
) -> None:
    from executor_birth_cutover import CurrentReceiptProof
    from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
    from executor_birth_ownership_coordinator import (
        LegacyDispositionV2,
        OwnershipCoordinatorRecordV1,
        OwnershipCoordinatorStateV1,
        SuccessorClaimV1,
        _coordinator_request_id_v1,
        _legacy_disposition_id_v2,
        _legacy_journal_hash_v2,
        _record_hash,
        _successor_claim_id_v1,
    )
    from executor_birth_ownership_preflight import (
        canonical_maintenance_proof,
        maintenance_evidence_hash,
    )

    digest = lambda value: preflight._raw_sha256_v1(value.encode("ascii"))
    root = _fixed_ownership_fixture(
        tmp_path, required=False, include_predecessor=False,
    )
    coordinator = root / "coordinator-v1"
    legacy_closed_build = digest("legacy-build")
    legacy_request = _coordinator_request_id_v1(
        legacy_closed_build, None, None,
    )
    prepared = OwnershipCoordinatorRecordV1(
        0, OwnershipCoordinatorStateV1.PREPARED, None, legacy_request,
        None, None, legacy_closed_build, digest("legacy-payload"),
        digest("legacy-signature"), digest("legacy-boundary"), "guard-v1",
    )
    proof = CurrentReceiptProof(
        (("fixture.contract", digest("legacy-generation")),),
        {
            ("fixture.contract", digest("legacy-generation")):
            digest("legacy-receipt"),
        },
    )
    maintenance = canonical_maintenance_proof(
        source="inactive_http_and_inactive_sidecar",
        units=tuple({
            "scope": scope, "unit": unit, "load_state": "loaded",
            "active_state": "inactive", "main_pid": 0,
        } for scope, unit in MAINTENANCE_TARGETS_V1),
    )
    maintenance_hash = maintenance_evidence_hash(maintenance)
    receipts_complete = OwnershipCoordinatorRecordV1(
        1, OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE,
        _record_hash(prepared.encode()), legacy_request, None, None,
        legacy_closed_build, digest("legacy-payload"),
        digest("legacy-signature"), digest("legacy-boundary"), "guard-v1",
        proof, maintenance_hash, maintenance_hash, maintenance,
    )
    legacy_bytes = (prepared.encode(), receipts_complete.encode())
    for sequence, encoded in enumerate(legacy_bytes):
        _write_control_file(
            coordinator / f"record-{sequence:03d}-v1.json", encoded,
        )

    successor_closed_build = digest("successor-build")
    successor_request = _coordinator_request_id_v1(
        successor_closed_build, None, None,
    )
    claim_value = {
        "schema_version": 1, "previous_head_id": None,
        "release_sequence": 1, "request_id": successor_request,
        "source_id": digest("successor-source"),
        "closed_build_id": successor_closed_build,
    }
    claim_value["claim_id"] = _successor_claim_id_v1(claim_value)
    claim = SuccessorClaimV1(
        claim_value["claim_id"], None, 1, successor_request,
        claim_value["source_id"], successor_closed_build,
    )
    _write_control_file(
        coordinator / "successor-claims-v1/initial.json", claim.encode(),
    )
    disposition_value = {
        "schema_version": 2,
        "legacy_journal_hash": _legacy_journal_hash_v2(legacy_bytes),
        "legacy_request_id": legacy_request,
        "legacy_state": "RECEIPTS_COMPLETE",
        "successor_request_id": successor_request,
        "reason": "superseded_before_certificate",
    }
    disposition_value["disposition_id"] = _legacy_disposition_id_v2(
        disposition_value,
    )
    disposition = LegacyDispositionV2(
        disposition_value["disposition_id"],
        disposition_value["legacy_journal_hash"], legacy_request,
        OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE, successor_request,
    )
    _write_control_file(
        coordinator / "legacy-disposition-v2.json", disposition.encode(),
    )
    temporary = tmp_path / "legacy-authentication-temporary"
    temporary.mkdir(mode=0o700)

    result = preflight._authenticate_fixed_ownership_snapshot_for_test_v1(
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )

    assert result.snapshot.legacy_prefix is not None
    assert result.snapshot.legacy_prefix.encoded_records == legacy_bytes
    assert result.snapshot.legacy_disposition is not None
    assert result.snapshot.legacy_disposition.disposition_id == (
        disposition.disposition_id
    )
    assert result.snapshot.legacy_disposition.successor_request_id == (
        disposition.successor_request_id
    )
    assert result.snapshot.pending_claims == (result.snapshot.claims[0],)

    changed_value = disposition.as_value()
    changed_value["legacy_journal_hash"] = digest("wrong-legacy-journal")
    unsigned = {
        key: value for key, value in changed_value.items()
        if key != "disposition_id"
    }
    changed_value["disposition_id"] = _legacy_disposition_id_v2(unsigned)
    _write_control_file(
        coordinator / "legacy-disposition-v2.json",
        preflight._canonical_json(changed_value),
    )
    _recovery(
        preflight._authenticate_fixed_ownership_snapshot_for_test_v1,
        root, openssl_executable=Path("/usr/bin/openssl"),
        temporary_root=temporary,
    )


@LINUX_ONLY
@pytest.mark.parametrize(
    "mutation", ["required", "claim", "predecessor", "parent"],
)
def test_fixed_ownership_capture_rejects_changes_between_snapshots(
    tmp_path: Path, mutation: str,
) -> None:
    root = _fixed_ownership_fixture(tmp_path, required=True)

    def mutate() -> None:
        if mutation == "required":
            path = root / "chain-v1/required-head-v1.bin"
            content = path.read_bytes()
            path.unlink()
            _write_control_file(path, content)
        elif mutation == "claim":
            _write_control_file(
                root / ("coordinator-v1/successor-claims-v1/" + "f" * 64 + ".json"),
                b"{}",
            )
        elif mutation == "predecessor":
            path = root / "predecessor-v1.json"
            content = bytearray(path.read_bytes())
            content[-1] ^= 1
            _write_control_file(path, bytes(content))
        else:
            root.parent.chmod(0o777)

    try:
        _recovery(
            preflight._capture_fixed_ownership_state_for_test_v1,
            root, between_for_test=mutate,
        )
    finally:
        root.parent.chmod(0o700)


@LINUX_ONLY
@pytest.mark.parametrize(
    "mutation",
    ["hardlink", "mode", "missing-pair", "rebound", "anchor-temp", "lock-only"],
)
def test_fixed_ownership_capture_rejects_unsafe_metadata_and_inventory(
    tmp_path: Path, mutation: str,
) -> None:
    root = _fixed_ownership_fixture(
        tmp_path, required=mutation != "lock-only",
    )
    between = None
    if mutation == "hardlink":
        os.link(
            root / "authorities-v1/distribution-private-v1.bin",
            root / "private-key-hardlink",
        )
    elif mutation == "mode":
        (root / "authorities-v1/checkpoint-004-v1.json").chmod(0o600)
    elif mutation == "missing-pair":
        next((root / "chain-v1/heads-v1").glob("*.sig")).unlink()
    elif mutation == "rebound":
        old = root.with_name("ownership-old")

        def rebound() -> None:
            root.rename(old)
            root.mkdir(mode=0o755)

        between = rebound
    elif mutation == "anchor-temp":
        _write_control_file(
            root / ".ownership-cutover-v1.json.dead.tmp", b"temporary",
        )
    else:
        _write_control_file(
            root / "chain-v1/.required-head-v1.lock", b"\0", 0o600,
        )
    _recovery(
        preflight._capture_fixed_ownership_state_for_test_v1,
        root, between_for_test=between,
    )


def test_autonomous_ownership_registry_cutover_and_head_codecs_match_runtime() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from executor_birth_cutover import CurrentReceiptProof
    from executor_birth_ownership_authorities import (
        decode_ownership_registry_v1,
        encode_ownership_registry_v1,
    )
    from executor_birth_ownership_chain import (
        OwnershipChainError,
        decode_required_head,
        encode_required_head,
        issue_ownership_head,
        verify_ownership_head,
    )
    from executor_birth_ownership_cutover import (
        issue_ownership_cutover_certificate,
        verify_ownership_cutover_certificate,
    )

    private_keys = {
        kind: Ed25519PrivateKey.generate()
        for kind in ("distribution", "cutover", "head")
    }
    registries = {
        kind: encode_ownership_registry_v1(kind, private.public_key())
        for kind, private in private_keys.items()
    }
    decoded_registries = preflight._decode_ownership_registry_set_v1(
        registries["distribution"], registries["cutover"], registries["head"],
    )
    for facts, kind in zip(
        decoded_registries, ("distribution", "cutover", "head"), strict=True,
    ):
        raw = private_keys[kind].public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        assert facts.authority == kind
        assert facts.raw_public_key == raw
        assert facts.key_id.endswith(preflight.hashlib.sha256(raw).hexdigest())
    runtime_cutover_registry = decode_ownership_registry_v1(
        registries["cutover"], expected_kind="cutover",
    )
    runtime_head_registry = decode_ownership_registry_v1(
        registries["head"], expected_kind="head",
    )

    generation_id = "sha256:" + "1" * 64
    receipt_hash = "sha256:" + "2" * 64
    proof = CurrentReceiptProof(
        (("fixture.contract", generation_id),),
        {("fixture.contract", generation_id): receipt_hash},
    )
    cutover_key_id = decoded_registries[1].key_id
    cutover_encoded, cutover_signature = issue_ownership_cutover_certificate(
        proof=proof,
        previous_cutover_id=None,
        request_id="sha256:" + "3" * 64,
        signing_key_id=cutover_key_id,
        maintenance_evidence_hash="sha256:" + "4" * 64,
        boundary_inventory_hash="sha256:" + "5" * 64,
        boundary_guard_version="fixture/1",
        closed_build_id="sha256:" + "6" * 64,
        private_key=private_keys["cutover"],
    )
    runtime_cutover = verify_ownership_cutover_certificate(
        cutover_encoded, cutover_signature, registry=runtime_cutover_registry,
        expected_proof=proof, expected_previous_cutover_id=None,
    )
    autonomous_cutover = preflight._decode_ownership_cutover_v1(
        cutover_encoded, cutover_signature,
    )
    assert autonomous_cutover[:10] == (
        runtime_cutover.cutover_id,
        runtime_cutover.previous_cutover_id,
        runtime_cutover.request_id,
        runtime_cutover.signing_key_id,
        runtime_cutover.catalog_id,
        tuple(
            preflight.OwnershipReceiptFactsV1(
                item.contract_id, item.generation_id, item.receipt_hash,
            )
            for item in runtime_cutover.current_receipts
        ),
        runtime_cutover.maintenance_evidence_hash,
        runtime_cutover.boundary_inventory_hash,
        runtime_cutover.boundary_guard_version,
        runtime_cutover.closed_build_id,
    )

    head_encoded, head_signature = issue_ownership_head(
        release_sequence=1,
        cutover_id=runtime_cutover.cutover_id,
        closed_build_id=runtime_cutover.closed_build_id,
        previous_head_id=None,
        signing_key_id=decoded_registries[2].key_id,
        private_key=private_keys["head"],
    )
    runtime_head = verify_ownership_head(
        head_encoded, head_signature, registry=runtime_head_registry,
    )
    frame = encode_required_head(runtime_head)
    assert decode_required_head(frame, registry=runtime_head_registry) == runtime_head
    autonomous_head = preflight._decode_required_head_frame_v1(frame)
    assert autonomous_head == preflight._DecodedOwnershipHeadV1(
        runtime_head.release_sequence, runtime_head.cutover_id,
        runtime_head.closed_build_id, runtime_head.previous_head_id,
        runtime_head.head_id, runtime_head.signing_key_id,
        runtime_head.encoded, runtime_head.signature,
    )

    shared_head = encode_ownership_registry_v1(
        "head", private_keys["cutover"].public_key(),
    )
    _invalid(
        preflight._decode_ownership_registry_set_v1,
        registries["distribution"], registries["cutover"], shared_head,
    )
    _invalid(preflight._decode_required_head_frame_v1, frame + b"trailing")
    _recovery(
        preflight._decode_fixed_required_head_frame_v1,
        frame + b"trailing",
    )

    mutated_signature = bytes([head_signature[0] ^ 1]) + head_signature[1:]
    decoded_candidate = preflight._decode_ownership_head_v1(
        head_encoded, mutated_signature,
    )
    assert decoded_candidate.signature == mutated_signature
    with pytest.raises(OwnershipChainError, match="signature"):
        verify_ownership_head(
            head_encoded, mutated_signature, registry=runtime_head_registry,
        )

    invalid_sequence = json.loads(head_encoded)
    invalid_sequence["release_sequence"] = 2
    unsigned = {
        key: value for key, value in invalid_sequence.items()
        if key != "head_id"
    }
    invalid_sequence["head_id"] = preflight._digest(
        preflight.HEAD_ID_DOMAIN_V1, preflight._canonical_json(unsigned),
    )
    invalid_head = preflight._canonical_json(invalid_sequence)
    _invalid(
        preflight._decode_ownership_head_v1,
        invalid_head, head_signature,
    )

    for field, value in (
        ("purposes", ["ownership_head_v1"]),
        ("first_release_sequence", None),
    ):
        invalid_registry = json.loads(registries["distribution"])
        invalid_registry[field] = value
        _invalid(
            preflight._decode_ownership_registry_v1,
            preflight._canonical_json(invalid_registry), "distribution",
        )

    invalid_cutover = json.loads(cutover_encoded)
    invalid_cutover["catalog_id"] = "sha256:" + "f" * 64
    unsigned_cutover = {
        key: value for key, value in invalid_cutover.items()
        if key != "cutover_id"
    }
    invalid_cutover["cutover_id"] = preflight._digest(
        preflight.CUTOVER_ID_DOMAIN_V1,
        preflight._canonical_json(unsigned_cutover),
    )
    _invalid(
        preflight._decode_ownership_cutover_v1,
        preflight._canonical_json(invalid_cutover), cutover_signature,
    )


def test_autonomous_successor_claim_codec_matches_runtime_and_rejects_mutants() -> None:
    from executor_birth_ownership_coordinator import (
        SuccessorClaimV1,
        _successor_claim_id_v1,
    )

    digest = lambda character: "sha256:" + character * 64
    for sequence, predecessor in ((1, None), (2, digest("a"))):
        value = {
            "schema_version": 1,
            "previous_head_id": predecessor,
            "release_sequence": sequence,
            "request_id": digest("1"),
            "source_id": digest("2"),
            "closed_build_id": digest("3"),
        }
        value["claim_id"] = _successor_claim_id_v1(value)
        runtime_claim = SuccessorClaimV1(
            value["claim_id"], predecessor, sequence, value["request_id"],
            value["source_id"], value["closed_build_id"],
        )
        assert preflight._decode_successor_claim_v1(
            runtime_claim.encode(),
        ) == preflight._DecodedSuccessorClaimV1(
            runtime_claim.claim_id, runtime_claim.previous_head_id,
            runtime_claim.release_sequence, runtime_claim.request_id,
            runtime_claim.source_id, runtime_claim.closed_build_id,
        )

    valid = json.loads(runtime_claim.encode())
    for field, replacement in (
        ("previous_head_id", None),
        ("claim_id", digest("f")),
    ):
        mutated = dict(valid)
        mutated[field] = replacement
        _invalid(
            preflight._decode_successor_claim_v1,
            preflight._canonical_json(mutated),
        )


def test_autonomous_coordinator_prefix_000_through_005_matches_runtime() -> None:
    from executor_birth_cutover import CurrentReceiptProof
    from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
    from executor_birth_ownership_coordinator import (
        OwnershipCoordinatorRecordV2,
        OwnershipCoordinatorStateV1,
        _decode_record_v2,
        _install_transaction_id_v1,
        _record_hash_v2,
    )
    from executor_birth_ownership_preflight import (
        canonical_maintenance_proof,
        maintenance_evidence_hash,
    )

    digest = lambda character: "sha256:" + character * 64
    install_value = {
        "schema_version": 1, "request_id": digest("1"),
        "source_id": digest("2"), "closed_build_id": digest("3"),
        "release_sequence": 2, "previous_head_id": digest("4"),
        "successor_claim_id": digest("5"),
        "deployment_descriptor_id": digest("6"),
        "service_coverage_hash": digest("7"),
        "administrative_bundle_hash": digest("8"),
    }
    transaction_id = _install_transaction_id_v1(install_value)
    proof = CurrentReceiptProof(
        (("executor:alpha", digest("7")),),
        {("executor:alpha", digest("7")): digest("8")},
    )
    maintenance = canonical_maintenance_proof(
        source="inactive_http_and_inactive_sidecar",
        units=tuple({
            "scope": scope, "unit": unit, "load_state": "loaded",
            "active_state": "inactive", "main_pid": 0,
        } for scope, unit in MAINTENANCE_TARGETS_V1),
    )
    maintenance_hash = maintenance_evidence_hash(maintenance)
    maintenance_value = json.loads(maintenance)
    for field, replacement in (("source", []),):
        mutated_proof = dict(maintenance_value)
        mutated_proof[field] = replacement
        _invalid(
            preflight._maintenance_evidence_hash_v1,
            preflight._canonical_json(mutated_proof),
        )
    for field in ("scope", "active_state"):
        mutated_proof = json.loads(maintenance)
        mutated_proof["units"][0][field] = []
        _invalid(
            preflight._maintenance_evidence_hash_v1,
            preflight._canonical_json(mutated_proof),
        )
    records = []
    previous_hash = None
    for sequence in range(6):
        record = OwnershipCoordinatorRecordV2(
            sequence=sequence,
            state=tuple(OwnershipCoordinatorStateV1)[sequence],
            previous_record_sha256=previous_hash,
            request_id=install_value["request_id"],
            previous_closed_build_id=digest("a"),
            previous_cutover_id=digest("b"),
            closed_build_id=install_value["closed_build_id"],
            distribution_payload_hash=digest("c"),
            distribution_signature_hash=digest("d"),
            boundary_inventory_hash=digest("e"),
            boundary_guard_version="guard-v2",
            source_id=install_value["source_id"],
            successor_claim_id=install_value["successor_claim_id"],
            deployment_descriptor_id=install_value["deployment_descriptor_id"],
            install_transaction_id=transaction_id,
            release_sequence=install_value["release_sequence"],
            previous_head_id=install_value["previous_head_id"],
            service_coverage_hash=install_value["service_coverage_hash"],
            administrative_bundle_hash=install_value[
                "administrative_bundle_hash"
            ],
            current_proof=proof if sequence >= 1 else None,
            maintenance_before_hash=(maintenance_hash if sequence >= 1 else None),
            maintenance_after_hash=(maintenance_hash if sequence >= 1 else None),
            maintenance_proof=maintenance if sequence >= 1 else None,
            startup_prerequisite_id=digest("1") if sequence >= 2 else None,
            startup_prerequisite_digest=digest("2") if sequence >= 2 else None,
            cutover_id=digest("3") if sequence >= 2 else None,
            catalog_id=digest("4") if sequence >= 2 else None,
            certificate_payload_hash=digest("5") if sequence >= 2 else None,
            certificate_signature_hash=digest("6") if sequence >= 2 else None,
            installed_tree_hash=digest("7") if sequence >= 4 else None,
            head_id=digest("8") if sequence >= 5 else None,
            head_payload_hash=digest("9") if sequence >= 5 else None,
            head_signature_hash=digest("a") if sequence >= 5 else None,
            required_head_frame_hash=digest("b") if sequence >= 5 else None,
            verified_chain_head_id=digest("8") if sequence >= 5 else None,
            preflight_attestation_hash=None,
        )
        records.append(record)
        previous_hash = _record_hash_v2(record.encode())
    encoded_records = tuple(record.encode() for record in records)
    decoded = preflight._decode_coordinator_prefix_v2(encoded_records)
    assert tuple(item.as_value() for item in decoded.records) == tuple(
        item.as_value() for item in records
    )
    assert decoded.encoded_records == encoded_records

    large_value = json.loads(encoded_records[1])
    large_value["current_receipts"] = [{
        "contract_id": f"executor:{index:05d}",
        "generation_id": digest("7"), "receipt_hash": digest("8"),
    } for index in range(30_000)]
    large_record = preflight._canonical_json(large_value)
    assert len(large_record) < preflight.MAX_COORDINATOR_RECORD_BYTES_V2
    assert _decode_record_v2(large_record).as_value() == large_value
    assert preflight._decode_coordinator_record_v2(
        large_record,
    ).as_value() == large_value

    mutants = [encoded_records[:2] + encoded_records[3:]]
    for index, field, replacement in (
        (1, "previous_record_sha256", digest("f")),
        (3, "boundary_guard_version", "changed-guard"),
        (3, "catalog_id", digest("f")),
    ):
        changed = list(encoded_records)
        value = json.loads(changed[index])
        value[field] = replacement
        changed[index] = preflight._canonical_json(value)
        mutants.append(tuple(changed))
    for mutant in mutants:
        _invalid(preflight._decode_coordinator_prefix_v2, mutant)

    direct_mutants = (
        (0, "startup_prerequisite_id", digest("f")),
        (1, "maintenance_proof_b64", "not-base64"),
        (1, "maintenance_after_hash", digest("f")),
        (2, "catalog_id", None),
        (3, "installed_tree_hash", digest("f")),
        (5, "verified_chain_head_id", digest("f")),
        (5, "preflight_attestation_hash", digest("f")),
        (5, "install_transaction_id", digest("f")),
    )
    for index, field, replacement in direct_mutants:
        value = json.loads(encoded_records[index])
        value[field] = replacement
        _invalid(
            preflight._decode_coordinator_record_v2,
            preflight._canonical_json(value),
        )


def test_autonomous_predecessor_codec_matches_runtime_and_rejects_mutants() -> None:
    from executor_birth_distribution_assembler import (
        DistributionAssemblerError,
        PredecessorFileV1,
        PredecessorServiceCommandV1,
        ServiceCommandEnvironmentV1,
        build_predecessor_descriptor_v1,
        decode_predecessor_descriptor_v1,
        encode_predecessor_descriptor_v1,
    )

    digest = lambda character: "sha256:" + character * 64
    long_path = "tree/" + "x" * 4_100
    long_root = "/opt/" + "r" * 4_100
    long_unit = "u" * 220 + ".service"
    record = build_predecessor_descriptor_v1(
        transaction_id=digest("1"), installation_root=long_root,
        files=(
            PredecessorFileV1(long_path, 7, digest("2")),
            PredecessorFileV1("runtime/worker.py", 9, digest("3")),
        ),
        service_commands=(
            PredecessorServiceCommandV1(
                "worker", "python_module", "/usr/bin/python3.12", digest("4"),
                "runtime.worker", ("--once", ""), "/opt/metnos",
                (
                    ServiceCommandEnvironmentV1("LC_ALL", "C"),
                    ServiceCommandEnvironmentV1("TZ", "UTC"),
                ),
            ),
            PredecessorServiceCommandV1(
                "quarantine", "systemctl_stop", "/usr/bin/systemctl",
                digest("5"), None, ("stop", long_unit), "/", (),
            ),
            PredecessorServiceCommandV1(
                "native", "native_executable", "/usr/bin/helper", digest("6"),
                None, ("--once",), "/opt/metnos", (),
            ),
            PredecessorServiceCommandV1(
                "idle", "none", None, None, None, (), None, (),
            ),
        ),
        administrative_bundle_hash=digest("7"),
        service_catalog_id=digest("8"), service_coverage_hash=digest("9"),
    )
    encoded = encode_predecessor_descriptor_v1(record)
    assert decode_predecessor_descriptor_v1(encoded) == record
    decoded = preflight._decode_predecessor_descriptor_v1(encoded)
    assert decoded.as_value() == json.loads(encoded)
    assert decoded.predecessor_id == record.predecessor_id
    assert decoded.installation_root == long_root
    assert decoded.files[-1].path == long_path
    assert next(
        item for item in decoded.service_commands
        if item.execution_kind == "systemctl_stop"
    ).target_args == ("stop", long_unit)
    assert preflight._UNIT_RE.fullmatch(long_unit) is None

    def rebound(value):
        unsigned = {
            key: item for key, item in value.items()
            if key != "predecessor_id"
        }
        value["predecessor_id"] = preflight._digest(
            preflight.PREDECESSOR_DESCRIPTOR_ID_DOMAIN_V1,
            preflight._canonical_json(unsigned),
        )
        return preflight._canonical_json(value)

    original = json.loads(encoded)
    mutants = []

    value = json.loads(encoded)
    value["installation_root"] = "/"
    mutants.append(rebound(value))

    value = json.loads(encoded)
    value["files"][0]["path"] = "received-source-v1.json"
    mutants.append(rebound(value))

    value = json.loads(encoded)
    value["files"].reverse()
    mutants.append(rebound(value))

    value = json.loads(encoded)
    value["service_commands"].reverse()
    mutants.append(rebound(value))

    value = json.loads(encoded)
    value["files"][0]["size"] = True
    mutants.append(rebound(value))

    value = json.loads(encoded)
    worker = next(
        item for item in value["service_commands"]
        if item["execution_kind"] == "python_module"
    )
    worker["target_environment"][0]["name"] = "PYTHONPATH"
    mutants.append(rebound(value))

    value = json.loads(encoded)
    worker = next(
        item for item in value["service_commands"]
        if item["execution_kind"] == "python_module"
    )
    worker["target_environment"].reverse()
    mutants.append(rebound(value))

    value = json.loads(encoded)
    worker = next(
        item for item in value["service_commands"]
        if item["execution_kind"] == "python_module"
    )
    worker["python_module"] = None
    mutants.append(rebound(value))

    value = json.loads(encoded)
    stop = next(
        item for item in value["service_commands"]
        if item["execution_kind"] == "systemctl_stop"
    )
    stop["target_args"][1] = "invalid%.service"
    mutants.append(rebound(value))

    value = dict(original)
    value["predecessor_id"] = digest("f")
    mutants.append(preflight._canonical_json(value))

    for mutant in mutants:
        with pytest.raises(DistributionAssemblerError):
            decode_predecessor_descriptor_v1(mutant)
        _invalid(preflight._decode_predecessor_descriptor_v1, mutant)


def test_script_loads_with_isolated_standard_library_only() -> None:
    script = str(Path(preflight.__file__).resolve())
    inventory = json.dumps(_compiled_boundary_inventory_fixture(), sort_keys=True)
    probe = (
        f"import json,runpy; n=runpy.run_path({script!r},run_name='preflight_probe'); "
        f"i=json.loads({inventory!r}); "
        "s=b'import importlib as il\\nname=\"runtime.sign\"\\n"
        "def probe():\\n return il.import_module(name)\\n'; "
        "f=n['_birth_closed_finding_tuples_v1']({'runtime/probe.py':s},i); "
        "assert any(x[0]=='birth_closed_dynamic_boundary' for x in f)"
    )
    completed = subprocess.run(
        [
            sys.executable, "-I", "-S", "-c",
            probe,
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=10, check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", errors="replace",
    )
    assert completed.stdout == b""
    assert completed.stderr == b""


def test_boundary_ast_budgets_and_failures_are_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    simple = b"value = 1\n"
    with monkeypatch.context() as patcher:
        patcher.setattr(preflight, "MAX_BOUNDARY_SOURCE_BYTES_V1", len(simple))
        assert preflight._scan_boundary_source_v1("runtime/probe.py", simple)[1] > 0

    parse_calls = []
    real_parse = preflight.ast.parse
    with monkeypatch.context() as patcher:
        patcher.setattr(preflight, "MAX_BOUNDARY_SOURCE_BYTES_V1", len(simple) - 1)
        patcher.setattr(
            preflight.ast, "parse",
            lambda *args, **kwargs: parse_calls.append(args) or real_parse(*args, **kwargs),
        )
        _invalid(preflight._scan_boundary_source_v1, "runtime/probe.py", simple)
    assert parse_calls == []

    cases = (
        ("MAX_BOUNDARY_AST_NODES_V1", 1, b"value = 1\n"),
        ("MAX_BOUNDARY_AST_DEPTH_V1", 2, b"value = 1 + 2 + 3\n"),
        ("MAX_BOUNDARY_SCOPES_V1", 1, b"def probe():\n return 1\n"),
        ("MAX_BOUNDARY_CALLS_V1", 0, b"probe()\n"),
    )
    for constant, limit, source in cases:
        with monkeypatch.context() as patcher:
            patcher.setattr(preflight, constant, limit)
            _invalid(preflight._scan_boundary_source_v1, "runtime/probe.py", source)

    with monkeypatch.context() as patcher:
        patcher.setattr(
            preflight, "MAX_BOUNDARY_TOTAL_SOURCE_BYTES_V1", len(simple) * 2 - 1,
        )
        _invalid(
            preflight._discover_boundary_from_verified_v1,
            {"runtime/a.py": simple, "runtime/b.py": simple},
        )

    nodes_per_source = preflight._scan_boundary_source_v1(
        "runtime/a.py", simple,
    )[1]
    with monkeypatch.context() as patcher:
        patcher.setattr(
            preflight,
            "MAX_BOUNDARY_TOTAL_AST_NODES_V1",
            nodes_per_source * 2 - 1,
        )
        _invalid(
            preflight._discover_boundary_from_verified_v1,
            {"runtime/a.py": simple, "runtime/b.py": simple},
        )

    canonical_source = tmp_path / "runtime" / "probe.py"
    canonical_source.parent.mkdir()
    canonical_source.write_bytes(simple)
    with monkeypatch.context() as patcher:
        patcher.setattr(canonical_guard, "MAX_BOUNDARY_AST_NODES", 1)
        with pytest.raises(ValueError):
            canonical_guard.scan_file(canonical_source, repository_root=tmp_path)

    for failure_type in (RecursionError, ValueError, OverflowError, MemoryError):
        with monkeypatch.context() as patcher:
            patcher.setattr(
                preflight.ast, "parse",
                lambda *_args, _failure=failure_type, **_kwargs: (_ for _ in ()).throw(
                    _failure("synthetic AST failure")
                ),
            )
            _invalid(preflight._scan_boundary_source_v1, "runtime/probe.py", simple)


def test_late_ast_walk_memory_failures_are_normalized(monkeypatch) -> None:
    source = b"__version__ = '1.2.3'\n"
    with monkeypatch.context() as patcher:
        patcher.setattr(
            preflight.ast,
            "walk",
            lambda _tree: (_ for _ in ()).throw(MemoryError("synthetic")),
        )
        _invalid(preflight._product_version_from_source_v1, source)

    path = "runtime/probe.py"
    payload = b"value = 1\n"
    files = (preflight.DistributionFileV1(
        path,
        len(payload),
        preflight.distribution_file_hash_v1(path, payload),
        "runtime_code",
    ),)
    with monkeypatch.context() as patcher:
        patcher.setattr(
            preflight.ast,
            "walk",
            lambda _tree: (_ for _ in ()).throw(MemoryError("synthetic")),
        )
        _invalid(
            preflight._verify_local_import_closure_v1,
            Path("."),
            files,
            {path: payload},
        )


def test_local_import_closure_enforces_combined_ast_budget(monkeypatch) -> None:
    payload = b"value = 1\n"
    paths = ("runtime/a.py", "runtime/b.py")
    files = tuple(
        preflight.DistributionFileV1(
            path,
            len(payload),
            preflight.distribution_file_hash_v1(path, payload),
            "runtime_code",
        )
        for path in paths
    )
    per_file = preflight._bounded_ast_metrics_v1(
        preflight.ast.parse(payload.decode("utf-8")),
    )
    monkeypatch.setattr(
        preflight,
        "MAX_BOUNDARY_TOTAL_AST_NODES_V1",
        per_file * 2 - 1,
    )

    _invalid(
        preflight._verify_local_import_closure_v1,
        Path("."),
        files,
        {path: payload for path in paths},
    )


@pytest.mark.parametrize("source, rejected", [
    (b"class Runner:\n def run_module(self, name): return name\n"
     b"VALUE = Runner().run_module('safe')\n", False),
    (b"import runpy\nVALUE = runpy.run_module('unsafe')\n", True),
])
def test_autonomous_import_closure_distinguishes_local_methods_from_loaders(
        source: bytes, rejected: bool) -> None:
    path = "runtime/sample.py"
    item = preflight.DistributionFileV1(
        path, len(source), preflight.distribution_file_hash_v1(path, source),
        "runtime_code",
    )
    if rejected:
        _invalid(
            preflight._verify_local_import_closure_v1,
            Path("."), (item,), {path: source},
        )
    else:
        preflight._verify_local_import_closure_v1(
            Path("."), (item,), {path: source},
        )


def test_autonomous_import_closure_limits_door_exception_to_exact_scope() -> None:
    path = "runtime/admitted_module_v1.py"
    source = (
        b"def load_admitted_module_v1(payload):\n"
        b" compiled = compile(payload, '<signed>', 'exec')\n"
        b" exec(compiled, {})\n"
        b"def rogue(payload):\n return eval(payload)\n"
    )
    item = preflight.DistributionFileV1(
        path, len(source), preflight.distribution_file_hash_v1(path, source),
        "runtime_code",
    )
    _invalid(
        preflight._verify_local_import_closure_v1,
        Path("."), (item,), {path: source},
    )


@pytest.mark.parametrize(
    ("source", "expected_dynamic"),
    (
        (
            b"import importlib as il\nname = 'runtime.sign'\n"
            b"def probe():\n return il.import_module(name)\n",
            True,
        ),
        (
            b"from importlib import import_module\n"
            b"def probe(name):\n return import_module(name)\n",
            True,
        ),
        (
            b"from importlib import import_module\n"
            b"def probe(name):\n name = 'json'\n return import_module(name)\n",
            True,
        ),
        (
            b"from importlib import import_module\n"
            b"mods = ['json']\nmods.append('runtime.sign')\n"
            b"for name in mods:\n import_module(name)\n",
            True,
        ),
        (
            b"from importlib import import_module\nmods = ('json',)\n"
            b"def mutate():\n global mods\n mods = ('runtime.sign',)\n"
            b"def probe():\n"
            b" for name in mods:\n  import_module(name)\n",
            True,
        ),
        (
            b"from importlib import import_module\n"
            b"import_module('runtime.sign.child')\n",
            True,
        ),
        (
            b"from importlib import import_module\n"
            b"def probe(name):\n return import_module(f'runtime.sign.{name}')\n",
            True,
        ),
        (
            b"import importlib\nloader = importlib.import_module\n"
            b"loader('runtime.sign')\n",
            True,
        ),
        (
            b"from importlib import import_module\n"
            b"import_module('.sign', package='runtime')\n",
            True,
        ),
        (
            b"import importlib\n"
            b"def probe(name):\n return getattr(importlib, name)('runtime.sign')\n",
            True,
        ),
        (
            b"from importlib import import_module\n"
            b"probe = lambda name: import_module(name)\n",
            True,
        ),
        (
            b"def probe(name):\n"
            b" return __builtins__['__import__'](name)\n",
            True,
        ),
        (
            b"from importlib import import_module\nname = 'json'\n"
            b"with manager as name:\n import_module(name)\n",
            True,
        ),
        (
            b"from importlib import import_module\n"
            b"values = [import_module(name) for name in ('runtime.sign',)]\n",
            True,
        ),
        (
            b"from importlib import import_module\nname = 'json'\n"
            b"match payload:\n case {'module': name}: import_module(name)\n",
            True,
        ),
        (
            b"from importlib import import_module\n"
            b"def outer():\n name = 'json'\n def inner():\n"
            b"  nonlocal name\n  return import_module(name)\n return inner\n",
            True,
        ),
        (
            b"from dataclasses import dataclass\n"
            b"from importlib import import_module\n@dataclass(frozen=True)\n"
            b"class Row:\n module: str\nrows = (Row('json'),)\n"
            b"for row in rows:\n import_module(row.module)\n",
            True,
        ),
        (
            b"from importlib import import_module\nmods = ('json',)\n"
            b"globals()['mods'] = ('runtime.sign',)\n"
            b"for name in mods:\n import_module(name)\n",
            True,
        ),
        (
            b"def probe(name):\n"
            b" return globals().get('__builtins__').get('__import__')(name)\n",
            True,
        ),
        (
            b"import sys\ndef probe(name):\n"
            b" return getattr(sys.modules.get('builtins'), '__import__')(name)\n",
            True,
        ),
        (
            b"import importlib\ngetter = getattr\n"
            b"getter(importlib, 'import_module')('runtime.sign')\n",
            True,
        ),
        (
            b"import builtins\n"
            b"builtins.__getattribute__('__import__')('runtime.sign')\n",
            True,
        ),
        (b"import sys\nmods = sys.modules\nmods[name]\n", True),
        (b"import sys\nlookup = sys.modules.get\nlookup(name)\n", True),
        (
            b"import importlib.util\n"
            b"spec = importlib.util.spec_from_file_location('x', 'x.py')\n"
            b"module = importlib.util.module_from_spec(spec)\n"
            b"spec.loader.exec_module(module)\n",
            True,
        ),
        (
            b"import importlib\n"
            b"spec = getattr(importlib.util, 'spec_from_file_location')"
            b"('x', 'x.py')\n"
            b"spec.loader.load_module()\n",
            True,
        ),
        (
            b"import sys\n"
            b"sys.modules.__getitem__('builtins').__dict__."
            b"__getitem__('__import__')('runtime.sign')\n",
            True,
        ),
        (
            b"import sys\n"
            b"sys.modules['builtins'].__dict__['eval']("
            b"\"__import__('runtime.sign')\")\n",
            True,
        ),
        (
            b"import sys\n"
            b"sys.modules['builtins'].__dict__.__getitem__('exec')"
            b"(\"__import__('runtime.sign')\")\n",
            True,
        ),
        (
            b"import sys\n"
            b"[m.sign_executor(None) for m in sys.modules.values() "
            b"if getattr(m, '__name__', '') == 'sign']\n",
            True,
        ),
        (
            b"import sys\n"
            b"[(name, module) for name, module in sys.modules.items()]\n",
            True,
        ),
        (b"import sys\nsys.modules.pop('sign').sign_executor(None)\n", True),
        (
            b"import sys\nsys.modules.setdefault('loader', fake).load_catalog()\n",
            True,
        ),
        (b"import sys\nsys.modules['loader'] = fake\n", True),
        (
            b"import sys\nmods = sys.modules.__or__({})\n"
            b"[m for m in mods.values()]\n",
            True,
        ),
        (
            b"import sys\nmods = vars(sys)['modules']\nmods.values()\n",
            True,
        ),
        (
            b"import sys\nmods = sys.__dict__['modules']\nmods.values()\n",
            True,
        ),
        (
            b"import sys\nmods = getattr(sys, 'modules')\nmods.values()\n",
            True,
        ),
        (
            b"import sys\nmods = object.__getattribute__(sys, 'modules')\n"
            b"mods.values()\n",
            True,
        ),
        (
            b"module = __loader__.load_module('runtime.sign')\n"
            b"module.sign_executor(None)\n",
            True,
        ),
        (
            b"module = globals()['__loader__'].load_module('runtime.sign')\n"
            b"module.sign_executor(None)\n",
            True,
        ),
        (
            b"import sys\n"
            b"sys.modules.copy()['builtins'].__dict__['__import__']"
            b"('runtime.sign')\n",
            True,
        ),
        (
            b"from types import FunctionType\n"
            b"FunctionType(compile(\"__import__('runtime.sign')\", "
            b"'<probe>', 'exec'), {})()\n",
            True,
        ),
        (
            b"import sys\n"
            b"sys.modules['types'].FunctionType(code, {})()\n",
            True,
        ),
        (
            b"import sys\n"
            b"sys.modules['runpy'].run_path(name)\n",
            True,
        ),
        (
            b"import sys\n"
            b"sys.modules['importlib.util'].spec_from_file_location"
            b"('x', name)\n",
            True,
        ),
        (
            b"import importlib.util\n"
            b"factory = importlib.util.spec_from_file_location\n"
            b"factory('x', name)\n",
            True,
        ),
        (
            b"import runtime.sign as s\nm = (s,)[0]\n"
            b"m.sign_executor(None)\n",
            True,
        ),
        (
            b"import runtime.sign as s\nm = [s][0]\n"
            b"m.sign_executor(None)\n",
            True,
        ),
        (
            b"import runtime.sign as s\nm = s if flag else safe\n"
            b"m.sign_executor(None)\n",
            True,
        ),
        (
            b"import runtime.sign as s\ndef identity(value): return value\n"
            b"m = identity(s)\nm.sign_executor(None)\n",
            True,
        ),
        (b"from importlib import import_module\nimport_module('json')\n", True),
        (
            b"from importlib import import_module\n"
            b"for name in ('phase1_bootstrap', 'phase2_infra'):\n"
            b" import_module(f'install.phases.{name}')\n",
            True,
        ),
        (
            b"def run_module(name): return name\n"
            b"def main(): return run_module('safe')\n",
            False,
        ),
        (
            b"class Registry:\n def values(self): return ()\n"
            b"VALUE = Registry().values()\n",
            False,
        ),
        (
            b"def render(value):\n sign = '-'\n return sign + value\n",
            False,
        ),
        (b"import re\nVALUE = re.compile('safe')\n", False),
    ),
)
def test_boundary_dynamic_imports_are_always_closed(
    tmp_path: Path, source: bytes, expected_dynamic: bool,
) -> None:
    target = tmp_path / "runtime" / "probe.py"
    target.parent.mkdir()
    target.write_bytes(source)
    canonical = canonical_guard.discover(tmp_path)
    autonomous = preflight._discover_boundary_from_verified_v1({
        "runtime/probe.py": source,
    })
    for facts in (canonical, autonomous):
        assert any(
            "dynamic_boundary_access" in fact.capabilities
            for fact in facts
        ) is expected_dynamic
        assert any(fact.closed_dynamic_boundary for fact in facts) is expected_dynamic
    assert [
        (fact.path, fact.scope, fact.capabilities, fact.closed_dynamic_boundary)
        for fact in autonomous
    ] == [
        (fact.path, fact.scope, fact.capabilities, fact.closed_dynamic_boundary)
        for fact in canonical
    ]


@pytest.mark.parametrize(
    "source",
    (
        b"import runtime.sign\nruntime.sign.sign_executor(None)\n",
        b"from runtime.sign import sign_executor as f\nf(None)\n"
        b"f = lambda value: value\n",
    ),
)
def test_boundary_static_import_authority_survives_aliasing_and_rebinding(
    tmp_path: Path, source: bytes,
) -> None:
    target = tmp_path / "runtime" / "probe.py"
    target.parent.mkdir()
    target.write_bytes(source)
    canonical = canonical_guard.discover(tmp_path)
    autonomous = preflight._discover_boundary_from_verified_v1({
        "runtime/probe.py": source,
    })
    for facts in (canonical, autonomous):
        assert any("sign" in fact.capabilities for fact in facts)


def test_direct_reviewed_boundary_api_is_not_misclassified_as_dynamic(
    tmp_path: Path,
) -> None:
    source = b"import runtime.sign as signing\nsigning.sign_executor(None)\n"
    target = tmp_path / "runtime" / "probe.py"
    target.parent.mkdir()
    target.write_bytes(source)

    for facts in (
        canonical_guard.discover(tmp_path),
        preflight._discover_boundary_from_verified_v1({
            "runtime/probe.py": source,
        }),
    ):
        fact = next(item for item in facts if item.scope == "<module>")
        assert fact.capabilities == ("sign",)
        assert fact.closed_dynamic_boundary is False


def test_autonomous_manifest_source_grammar_and_budget_are_closed(
    tmp_path: Path,
) -> None:
    _release, encoded, _signature, _registry, _temporary = _distribution_fixture(
        tmp_path,
    )

    def mutant(path: str, size: int = 1, *, replace: bool = False) -> bytes:
        value = json.loads(encoded)
        if replace:
            item = next(entry for entry in value["files"] if entry["path"] == path)
            item["size"] = size
        else:
            value["files"].append({
                "content_hash": preflight.distribution_file_hash_v1(path, b"x"),
                "path": path, "role": "runtime_code", "size": size,
            })
        value["files"].sort(key=lambda item: item["path"].encode("utf-8"))
        value["closed_build_id"] = None
        unsigned = dict(value)
        unsigned.pop("closed_build_id")
        value["closed_build_id"] = preflight._digest(
            preflight.BUILD_ID_DOMAIN, preflight._canonical_json(unsigned),
        )
        return preflight._canonical_json(value)

    for path in (
        "other/evil.py", "Runtime/evil.py", "runtime/evil.PY",
        "runtime/package.py/payload.dat",
    ):
        _invalid(preflight._parse_distribution_manifest_v1, mutant(path))
    _invalid(
        preflight._parse_distribution_manifest_v1,
        mutant(
            "runtime/oversize.py",
            preflight.MAX_BOUNDARY_SOURCE_BYTES_V1 + 1,
        ),
    )
    _invalid(
        preflight._parse_distribution_manifest_v1,
        mutant(
            "deployment/admin/preflight.py",
            preflight.MAX_BOUNDARY_SOURCE_BYTES_V1 + 1,
            replace=True,
        ),
    )


def test_cli_accepts_only_three_exact_forms() -> None:
    assert preflight.parse_cli_v1(["check-all"]) == ("check-all", None)
    assert preflight.parse_cli_v1(
        ["check", "--entry-id", "service-http"],
    ) == ("check", "service-http")
    assert preflight.parse_cli_v1(
        ["launch", "--entry-id", "entry-installer"],
    ) == ("launch", "entry-installer")
    for argv in (
        [], ["--help"], ["check-all", "extra"],
        ["check", "--entry", "service-http"],
        ["check", "--entry-id", "service-http", "--entry-id", "x"],
        ["launch", "service-http"], ["CHECK-ALL"],
    ):
        _invalid(preflight.parse_cli_v1, argv)


@LINUX_ONLY
def test_operational_entrypoint_isolated_nonroot_denies_before_io(
    tmp_path,
) -> None:
    script = Path(preflight.__file__).resolve()
    run_options = {}
    if os.geteuid() == 0:
        import pwd

        account = pwd.getpwnam("nobody")
        # The demoted child must be able to enter pytest's root-owned 0700
        # temporary directory before Python can exercise the entry point.
        tmp_path.chmod(0o755)
        run_options = {
            "user": account.pw_uid,
            "group": account.pw_gid,
            "extra_groups": (),
        }
    before = tuple(tmp_path.iterdir())
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(script), "check-all"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
        **run_options,
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        preflight.EXIT_INVALID, "", preflight.CODE_INVALID + "\n",
    )
    assert tuple(tmp_path.iterdir()) == before


@pytest.mark.parametrize(
    "argv",
    (
        ["check", "--entry-id", "service-http"],
        ["launch", "--entry-id", "entry-installer"],
        ["check-all"],
    ),
)
def test_operational_entrypoint_root_denies_missing_without_mutation(
    argv, tmp_path, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        preflight, "_run_operational_command_v1",
        lambda _command: (_ for _ in ()).throw(
            preflight.PreflightError(
                preflight.CODE_MISSING, preflight.EXIT_MISSING,
                "fixed graph absent",
            )
        ),
    )
    before = tuple(tmp_path.iterdir())

    assert preflight.main(argv) == preflight.EXIT_MISSING
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == (
        "", preflight.CODE_MISSING + "\n",
    )
    assert tuple(tmp_path.iterdir()) == before


def test_operational_dispatch_keeps_check_all_outside_shared_gate(
    monkeypatch,
) -> None:
    events = []
    operational = object()
    entry = SimpleNamespace(class_name="gated_service")
    plan = object()

    monkeypatch.setattr(
        preflight, "_acquire_startup_gate_shared_v1",
        lambda: events.append("gate") or 41,
    )
    monkeypatch.setattr(
        preflight, "_release_startup_gate_v1",
        lambda descriptor: events.append(("release", descriptor)),
    )
    monkeypatch.setattr(
        preflight, "_attest_operational_preflight_v1",
        lambda: events.append("attest") or operational,
    )
    monkeypatch.setattr(
        preflight, "_require_preflight_entry_v1",
        lambda authority, entry_id: events.append(
            ("entry", authority, entry_id),
        ) or entry,
    )
    monkeypatch.setattr(
        preflight, "_publish_preflight_attestation_v1",
        lambda authority: events.append(("publish", authority)),
    )
    monkeypatch.setattr(
        preflight, "_make_launch_plan_v1",
        lambda authority, selected: events.append(
            ("plan", authority, selected),
        ) or plan,
    )
    monkeypatch.setattr(
        preflight, "_launch_gated_service_v1",
        lambda selected, lease: events.append(
            ("launch", selected, lease.descriptor),
        ),
    )

    preflight._run_operational_command_v1(
        preflight.CliCommandV1("check-all", None),
    )
    assert events == ["attest", ("publish", operational)]

    events.clear()
    preflight._run_operational_command_v1(
        preflight.CliCommandV1("check", "service-http"),
    )
    assert events == [
        "gate", "attest", ("entry", operational, "service-http"),
        ("release", 41),
    ]

    events.clear()
    preflight._run_operational_command_v1(
        preflight.CliCommandV1("launch", "service-http"),
    )
    assert events == [
        "gate", "attest", ("entry", operational, "service-http"),
        ("plan", operational, entry), ("launch", plan, 41),
        ("release", 41),
    ]


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_exit"),
    (
        (preflight.PreflightError(
            preflight.CODE_MISSING, preflight.EXIT_MISSING, "secret detail",
        ), preflight.CODE_MISSING, preflight.EXIT_MISSING),
        (preflight.PreflightError(
            preflight.CODE_INVALID, preflight.EXIT_INVALID, "secret detail",
        ), preflight.CODE_INVALID, preflight.EXIT_INVALID),
        (preflight.PreflightError(
            preflight.CODE_HEAD_MISMATCH, preflight.EXIT_HEAD_MISMATCH,
            "secret detail",
        ), preflight.CODE_HEAD_MISMATCH, preflight.EXIT_HEAD_MISMATCH),
        (preflight.PreflightError(
            preflight.CODE_PLATFORM, preflight.EXIT_PLATFORM, "secret detail",
        ), preflight.CODE_PLATFORM, preflight.EXIT_PLATFORM),
        (preflight.PreflightError(
            preflight.CODE_RECOVERY, preflight.EXIT_RECOVERY, "secret detail",
        ), preflight.CODE_RECOVERY, preflight.EXIT_RECOVERY),
        (RuntimeError("unexpected secret detail"),
         preflight.CODE_RECOVERY, preflight.EXIT_RECOVERY),
    ),
)
def test_operational_entrypoint_maps_failures_without_traceback(
    failure, expected_code, expected_exit, tmp_path, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0, raising=False)

    def deny(_command):
        raise failure

    monkeypatch.setattr(preflight, "_run_operational_command_v1", deny)
    before = tuple(tmp_path.iterdir())
    assert preflight.main(["check-all"]) == expected_exit
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", expected_code + "\n")
    assert "secret detail" not in captured.err
    assert "Traceback" not in captured.err
    assert tuple(tmp_path.iterdir()) == before


def test_operational_entrypoint_preserves_target_system_exit(
    monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0, raising=False)

    def target_exit(_command):
        raise SystemExit(7)

    monkeypatch.setattr(
        preflight, "_run_operational_command_v1", target_exit,
    )
    with pytest.raises(SystemExit) as failure:
        preflight.main(["launch", "--entry-id", "entry-installer"])
    assert failure.value.code == 7
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_platform_guard_is_a_stable_early_denial(monkeypatch) -> None:
    monkeypatch.setattr(preflight.sys, "platform", "win32")
    with pytest.raises(preflight.PreflightError) as failure:
        preflight.require_linux_before_io_v1()
    assert (failure.value.code, failure.value.exit_status) == (
        preflight.CODE_PLATFORM, preflight.EXIT_PLATFORM,
    )


@LINUX_ONLY
def test_bounded_reader_rejects_link_mode_hardlink_and_replacement(
    tmp_path, monkeypatch,
) -> None:
    tmp_path.chmod(0o700)
    trusted = tmp_path / "trusted"
    trusted.mkdir(mode=0o700)
    target = trusted / "record.json"
    target.write_bytes(b"evidence")
    target.chmod(0o600)
    uid, gid = os.getuid(), os.getgid()
    assert preflight._read_bounded_regular_v1(
        target, 8, uid=uid, gid=gid, mode=0o600, chain_stop=tmp_path,
    ) == b"evidence"

    target.chmod(0o622)
    _invalid(
        preflight._read_bounded_regular_v1,
        target, 8, uid=uid, gid=gid, mode=0o600, chain_stop=tmp_path,
    )
    target.chmod(0o600)
    hardlink = trusted / "second-name"
    os.link(target, hardlink)
    _invalid(
        preflight._read_bounded_regular_v1,
        target, 8, uid=uid, gid=gid, mode=0o600, chain_stop=tmp_path,
    )
    hardlink.unlink()
    symlink = trusted / "link.json"
    symlink.symlink_to(target.name)
    _invalid(
        preflight._read_bounded_regular_v1,
        symlink, 8, uid=uid, gid=gid, mode=0o600, chain_stop=tmp_path,
    )
    _invalid(
        preflight._read_bounded_regular_v1,
        target, 7, uid=uid, gid=gid, mode=0o600, chain_stop=tmp_path,
    )
    _invalid(
        preflight._read_bounded_regular_v1,
        target, 8, uid=uid, gid=gid, mode=0o600,
        chain_stop=Path("relative"),
    )

    original_read = preflight.os.read
    replaced = False

    def replace_live_name(descriptor, size):
        nonlocal replaced
        chunk = original_read(descriptor, size)
        if chunk and not replaced:
            replaced = True
            target.rename(trusted / "old-record.json")
            target.write_bytes(b"evidence")
            target.chmod(0o600)
        return chunk

    monkeypatch.setattr(preflight.os, "read", replace_live_name)
    _invalid(
        preflight._read_bounded_regular_v1,
        target, 8, uid=uid, gid=gid, mode=0o600, chain_stop=tmp_path,
    )


@LINUX_ONLY
def test_distribution_registry_manifest_openssl_and_tree_are_one_binding(
    tmp_path, monkeypatch,
) -> None:
    release, encoded, signature, registry, temporary = _distribution_fixture(tmp_path)
    record = preflight._authenticate_distribution_for_test_v1(
        encoded, signature, registry,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
    )
    assert list(temporary.iterdir()) == []
    boundary_calls = []
    monkeypatch.setattr(
        preflight, "_require_birth_closed_sources_v1",
        lambda verified, inventory: boundary_calls.append((verified, inventory)),
    )
    preflight._verify_installed_distribution_for_test_v1(record, release)
    assert len(boundary_calls) == 1
    manifest, files = preflight._parse_distribution_manifest_v1(encoded)
    expected_capture = {
        item.path for item in files if item.path.endswith(".py")
    } | {manifest["boundary_inventory_path"]}
    assert set(boundary_calls[0][0]) == expected_capture
    assert boundary_calls[0][1]["schema"] == BOUNDARY_INVENTORY_SCHEMA

    sentinel = preflight.PreflightError(
        preflight.CODE_INVALID, preflight.EXIT_INVALID, "boundary sentinel",
    )
    with monkeypatch.context() as patcher:
        def deny(_verified, _inventory):
            raise sentinel

        patcher.setattr(preflight, "_require_birth_closed_sources_v1", deny)
        patcher.setattr(
            preflight, "_product_version_from_source_v1",
            lambda _source: (_ for _ in ()).throw(AssertionError("late version")),
        )
        patcher.setattr(
            preflight, "_verify_local_import_closure_v1",
            lambda *_args: (_ for _ in ()).throw(AssertionError("late imports")),
        )
        with pytest.raises(preflight.PreflightError) as failure:
            preflight._verify_installed_distribution_for_test_v1(record, release)
        assert failure.value is sentinel
    assert isinstance(record.facts, tuple) and isinstance(record.files, tuple)
    with pytest.raises(AttributeError):
        record.facts.release_sequence = 2
    with pytest.raises(preflight.PreflightError):
        preflight._AuthenticatedDistributionForTestV1(
            record.facts, record.files, record.encoded, record.signature,
            b"x" * 32,
        )

    profiles = (
        subprocess.CompletedProcess((), 0, b"Signature Verified Successfully\n", b""),
        subprocess.CompletedProcess(
            (), 0, b"Signature Verified Successfully\n",
            b"Using configuration from /dev/null\nextra\n",
        ),
        subprocess.CompletedProcess(
            (), 0, b"Signature Verified Successfully\nextra\n",
            b"Using configuration from /dev/null\n",
        ),
        subprocess.CompletedProcess(
            (), 1, b"", b"Using configuration from /dev/null\n",
        ),
    )
    for completed in profiles:
        with monkeypatch.context() as patcher:
            patcher.setattr(
                preflight, "_run_openssl_bounded_v1",
                lambda _argv: (
                    completed.returncode, completed.stdout, completed.stderr,
                ),
            )
            _invalid(
                preflight._authenticate_distribution_for_test_v1,
                encoded, signature, registry,
                openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
            )
        assert list(temporary.iterdir()) == []

    _invalid(
        preflight._authenticate_distribution_for_test_v1,
        encoded, bytes([signature[0] ^ 1]) + signature[1:], registry,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
    )
    assert list(temporary.iterdir()) == []
    target = release / "runtime" / "executor_birth.py"
    target.write_bytes(b"ALTERED\n")
    target.chmod(0o600)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)


@LINUX_ONLY
def test_exact_tree_rejects_extra_empty_link_hardlink_special_and_bytecode(tmp_path) -> None:
    release, encoded, signature, registry, temporary = _distribution_fixture(tmp_path)
    record = preflight._authenticate_distribution_for_test_v1(
        encoded, signature, registry,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
    )
    target = release / "runtime" / "executor_birth.py"

    extra = release / "hidden.py"
    extra.write_bytes(b"hidden\n")
    extra.chmod(0o600)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    extra.unlink()

    empty = release / "empty"
    empty.mkdir(mode=0o700)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    empty.rmdir()

    outside_link = tmp_path / "outside-hardlink"
    os.link(target, outside_link)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    outside_link.unlink()

    saved = tmp_path / "saved-source"
    target.rename(saved)
    target.symlink_to(saved)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    target.unlink()
    saved.rename(target)

    target.rename(saved)
    os.mkfifo(target, mode=0o600)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    target.unlink()
    saved.rename(target)

    for bytecode_path in (
        "runtime/__pycache__/x.pyc", "runtime/x.pyo", "runtime/X.PYC",
    ):
        bytecode = preflight.DistributionFileV1(
            bytecode_path, 0,
            preflight.distribution_file_hash_v1(bytecode_path, b""),
            "runtime_code",
        )
        _invalid(preflight._distribution_trie_v1, (bytecode,))


@LINUX_ONLY
def test_exact_tree_detects_live_name_substitution_during_bytes(tmp_path, monkeypatch) -> None:
    fixture_root = tmp_path / "substitution"
    fixture_root.mkdir(mode=0o700)
    release, encoded, signature, registry, temporary = _distribution_fixture(fixture_root)
    record = preflight._authenticate_distribution_for_test_v1(
        encoded, signature, registry,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
    )
    target = release / "runtime" / "executor_birth.py"
    original_content = target.read_bytes()
    original_read = preflight.os.read
    replaced = False

    def replace_open_name(descriptor, size):
        nonlocal replaced
        chunk = original_read(descriptor, size)
        try:
            live_name = os.readlink(f"/proc/self/fd/{descriptor}")
        except OSError:
            live_name = ""
        if chunk and not replaced and live_name == str(target):
            replaced = True
            target.rename(target.with_suffix(".old"))
            target.write_bytes(original_content)
            target.chmod(0o600)
        return chunk

    monkeypatch.setattr(preflight.os, "read", replace_open_name)
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    assert replaced

    monkeypatch.setattr(preflight.os, "read", original_read)
    semantic_root = tmp_path / "semantic-substitution"
    semantic_root.mkdir(mode=0o700)
    release, encoded, signature, registry, temporary = _distribution_fixture(semantic_root)
    record = preflight._authenticate_distribution_for_test_v1(
        encoded, signature, registry,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
    )
    target = release / "runtime" / "executor_birth.py"
    original_content = target.read_bytes()
    validate_boundary = preflight._validate_boundary_inventory_v1
    replaced = False

    def replace_during_semantics(content):
        nonlocal replaced
        value = validate_boundary(content)
        if not replaced:
            replaced = True
            target.rename(target.with_suffix(".old"))
            target.write_bytes(original_content)
            target.chmod(0o600)
        return value

    monkeypatch.setattr(
        preflight, "_validate_boundary_inventory_v1", replace_during_semantics,
    )
    _invalid(preflight._verify_installed_distribution_for_test_v1, record, release)
    assert replaced


def test_distribution_codecs_reject_registry_role_boundary_and_import_mutants(tmp_path) -> None:
    release, encoded, signature, registry, _temporary = _distribution_fixture(tmp_path)
    registry_value = json.loads(registry)
    registry_value["purposes"] = ["ownership_head_v1"]
    _invalid(
        preflight._decode_distribution_registry_v1,
        preflight._canonical_json(registry_value),
    )

    manifest = json.loads(encoded)
    manifest["files"] = [
        item for item in manifest["files"] if item["role"] != "service_unit"
    ]
    unsigned = dict(manifest)
    unsigned.pop("closed_build_id")
    manifest["closed_build_id"] = preflight._digest(
        preflight.BUILD_ID_DOMAIN, preflight._canonical_json(unsigned),
    )
    _invalid(
        preflight._parse_distribution_manifest_v1,
        preflight._canonical_json(manifest),
    )

    inventory = json.loads((release / "internal/reports/boundary.json").read_bytes())
    inventory["birth_closed"]["sealed_modules"] = []
    _invalid(
        preflight._validate_boundary_inventory_v1,
        preflight._canonical_json(inventory),
    )

    hidden = release / "runtime" / "hidden.py"
    hidden.write_bytes(b"VALUE = 1\n")
    hidden.chmod(0o600)
    files = (preflight.DistributionFileV1(
        "runtime/executor_birth.py", len(b"import hidden as alias\n"),
        preflight.distribution_file_hash_v1(
            "runtime/executor_birth.py", b"import hidden as alias\n",
        ), "runtime_code",
    ),)
    _invalid(
        preflight._verify_local_import_closure_v1,
        release, files, {"runtime/executor_birth.py": b"import hidden as alias\n"},
    )

    windows = json.loads(encoded)
    windows.update({
        "platform": "windows", "installation_root": "C:\\Metnos\\release",
        "certificate_directory": "C:\\Metnos\\ownership",
    })
    unsigned = dict(windows)
    unsigned.pop("closed_build_id")
    windows["closed_build_id"] = preflight._digest(
        preflight.BUILD_ID_DOMAIN, preflight._canonical_json(unsigned),
    )
    parsed, _files = preflight._parse_distribution_manifest_v1(
        preflight._canonical_json(windows),
    )
    assert parsed["installation_root"] == "C:\\Metnos\\release"


def test_manifest_final_bounds_and_fixed_admin_preflight(tmp_path, monkeypatch) -> None:
    _release, encoded, _signature, _registry, _temporary = _distribution_fixture(tmp_path)
    manifest = json.loads(encoded)
    monkeypatch.setattr(preflight, "MAX_MANIFEST_FILES", len(manifest["files"]) - 1)
    _invalid(preflight._parse_distribution_manifest_v1, encoded)
    monkeypatch.setattr(preflight, "MAX_MANIFEST_FILES", 20_000)
    total = sum(item["size"] for item in manifest["files"])
    monkeypatch.setattr(preflight, "MAX_MANIFEST_TOTAL_BYTES", total - 1)
    _invalid(preflight._parse_distribution_manifest_v1, encoded)
    monkeypatch.setattr(
        preflight, "MAX_MANIFEST_TOTAL_BYTES", 2 * 1024 * 1024 * 1024,
    )

    manifest["preflight_entrypoint"] = "runtime/executor_birth_ownership_preflight.py"
    unsigned = dict(manifest)
    unsigned.pop("closed_build_id")
    manifest["closed_build_id"] = preflight._digest(
        preflight.BUILD_ID_DOMAIN, preflight._canonical_json(unsigned),
    )
    _invalid(
        preflight._parse_distribution_manifest_v1,
        preflight._canonical_json(manifest),
    )


def test_real_boundary_policy_snapshot_is_exact_and_entry_schema_is_closed() -> None:
    raw = _compiled_boundary_inventory_fixture()
    encoded = preflight._canonical_json(raw)
    parsed = preflight._validate_boundary_inventory_v1(encoded)
    assert len(parsed["birth_closed"]["coordinator_store_owners"]) == 76
    assert len(parsed["birth_closed"]["exceptions"]) == 16
    for mutate in ("owners", "exceptions", "entry"):
        mutant = json.loads(encoded)
        if mutate == "owners":
            mutant["birth_closed"]["coordinator_store_owners"] = (
                mutant["birth_closed"]["coordinator_store_owners"][:-1]
            )
        elif mutate == "exceptions":
            mutant["birth_closed"]["exceptions"][0]["exception"] = "retirement_only"
        else:
            mutant["entries"][0]["unexpected"] = True
        _invalid(
            preflight._validate_boundary_inventory_v1,
            preflight._canonical_json(mutant),
        )


@pytest.mark.parametrize(
    ("relative", "source", "expected_code"),
    (
        (
            "runtime/dynamic_probe.py",
            b"import importlib as il\ndef probe():\n return il.import_module('runtime.sign')\n",
            "birth_closed_dynamic_boundary",
        ),
        (
            "runtime/extra_probe.py",
            b"from runtime.executor_birth_operational import birth_executor as be\n"
            b"def extra():\n return be()\n",
            "unclassified_boundary_scope",
        ),
        (
            "runtime/admin/manifest_refactor.py",
            b"from runtime.sign import sign_executor\n"
            b"def refactor_manifest():\n return sign_executor(None)\n",
            "birth_closed_exception_invalid",
        ),
    ),
)
def test_autonomous_boundary_clone_matches_certified_oracle(
    tmp_path, relative, source, expected_code,
) -> None:
    release = tmp_path / "release"
    target = release / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(source)
    inventory = _compiled_boundary_inventory_fixture()
    canonical = tuple(
        (finding.code, finding.scope, finding.message)
        for finding in canonical_birth_closed_findings(
            canonical_boundary_discover(release), inventory,
        )
    )
    verified = {relative: source}
    autonomous = preflight._birth_closed_finding_tuples_v1(
        verified, inventory,
    )
    assert autonomous == canonical
    assert expected_code in {finding[0] for finding in autonomous}


@LINUX_ONLY
def test_productive_consumption_reauthenticates_before_tree(monkeypatch, tmp_path) -> None:
    _release, encoded, signature, registry, temporary = _distribution_fixture(tmp_path)
    test_record = preflight._authenticate_distribution_for_test_v1(
        encoded, signature, registry,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
    )
    product_record = preflight.AuthenticatedDistributionV1(
        test_record.facts, test_record.files, test_record.encoded,
        test_record.signature, test_record.artifact_binding,
    )
    calls = []

    def stop_after_fixed_reauthentication(authenticated, authenticated_signature):
        calls.append((authenticated, authenticated_signature))
        raise preflight.PreflightError(
            preflight.CODE_INVALID, preflight.EXIT_INVALID, "fixed trust sentinel",
        )

    monkeypatch.setattr(
        preflight, "authenticate_distribution_v1", stop_after_fixed_reauthentication,
    )
    monkeypatch.setattr(
        preflight, "_verify_installed_distribution_core_v1",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("tree reached before fixed reauthentication")
        ),
    )
    _invalid(preflight.verify_installed_distribution_v1, product_record)
    assert calls == [(encoded, signature)]


@LINUX_ONLY
@pytest.mark.parametrize(("descriptor", "size"), ((1, 4096), (2, 4096)))
def test_openssl_stream_exact_bound_is_accepted(descriptor, size) -> None:
    returncode, stdout, stderr = preflight._run_openssl_bounded_v1((
        sys.executable, "-I", "-S", "-c",
        f"import os; os.write({descriptor}, b'x' * {size})",
    ))
    assert returncode == 0
    assert len(stdout if descriptor == 1 else stderr) == size


@LINUX_ONLY
@pytest.mark.parametrize("descriptor", (1, 2))
def test_openssl_overflow_kills_and_reaps_process(
    monkeypatch, descriptor,
) -> None:
    original = preflight.subprocess.Popen
    pids = []

    def capture(*args, **kwargs):
        process = original(*args, **kwargs)
        pids.append(process.pid)
        return process

    monkeypatch.setattr(preflight.subprocess, "Popen", capture)
    _invalid(
        preflight._run_openssl_bounded_v1,
        (
            sys.executable, "-I", "-S", "-c",
            f"import os,time; os.write({descriptor}, b'x' * 4097); time.sleep(30)",
        ),
    )
    assert len(pids) == 1
    with pytest.raises(ChildProcessError):
        os.waitpid(pids[0], os.WNOHANG)


@LINUX_ONLY
def test_openssl_timeout_is_bounded_and_reaps_process(monkeypatch) -> None:
    original = preflight.subprocess.Popen
    pids = []

    def capture(*args, **kwargs):
        process = original(*args, **kwargs)
        pids.append(process.pid)
        return process

    monkeypatch.setattr(preflight.subprocess, "Popen", capture)
    monkeypatch.setattr(preflight, "OPENSSL_TIMEOUT_SECONDS", 0.05)
    started = time.monotonic()
    _invalid(
        preflight._run_openssl_bounded_v1,
        (sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"),
    )
    assert time.monotonic() - started < 2
    with pytest.raises(ChildProcessError):
        os.waitpid(pids[0], os.WNOHANG)


@LINUX_ONLY
def test_openssl_teardown_timeout_closes_streams_and_requires_recovery() -> None:
    events = []

    class FakeStream:
        def __init__(self, label):
            self.label = label

        def close(self):
            events.append(("close", self.label))

    class FakeProcess:
        stdout = FakeStream("stdout")
        stderr = FakeStream("stderr")

        def poll(self):
            events.append(("poll", None))
            return None

        def kill(self):
            events.append(("kill", None))

        def wait(self, *, timeout):
            events.append(("wait", timeout))
            raise subprocess.TimeoutExpired(("openssl",), timeout)

    _recovery(preflight._teardown_openssl_process_v1, FakeProcess())
    assert events == [
        ("poll", None),
        ("kill", None),
        ("wait", preflight.OPENSSL_TEARDOWN_TIMEOUT_SECONDS),
        ("close", "stdout"),
        ("close", "stderr"),
        ("poll", None),
    ]


@LINUX_ONLY
@pytest.mark.parametrize("valid_openssl_result", (False, True))
def test_openssl_cleanup_attempts_all_resources_and_residue_blocks_retry(
    monkeypatch, tmp_path, valid_openssl_result,
) -> None:
    temporary = tmp_path / "openssl-temporary"
    temporary.mkdir(mode=0o700)
    original_unlink = Path.unlink
    original_rmdir = Path.rmdir
    attempts = []
    failed_once = False

    def fail_first_unlink(path, *args, **kwargs):
        nonlocal failed_once
        attempts.append(("unlink", path.name))
        if path.name == "public-key.pem" and not failed_once:
            failed_once = True
            raise OSError("injected unlink failure")
        return original_unlink(path, *args, **kwargs)

    def record_rmdir(path, *args, **kwargs):
        attempts.append(("rmdir", path.name))
        return original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_unlink)
    monkeypatch.setattr(Path, "rmdir", record_rmdir)
    monkeypatch.setattr(
        preflight, "_run_openssl_bounded_v1",
        lambda _argv: (
            (
                0,
                b"Signature Verified Successfully\n",
                b"Using configuration from /dev/null\n",
            )
            if valid_openssl_result else
            (1, b"", b"Using configuration from /dev/null\n")
        ),
    )
    failure = _recovery(
        preflight._verify_ed25519_openssl_core_v1,
        b"k" * 32, b"payload", b"s" * 64,
        openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
        temporary_uid=os.getuid(), temporary_gid=os.getgid(),
        chain_stop=temporary,
    )
    if valid_openssl_result:
        assert failure.__cause__ is None
    else:
        assert isinstance(failure.__cause__, preflight.PreflightError)
        assert failure.__cause__.code == preflight.CODE_INVALID
    assert {item for item in attempts if item[0] == "unlink"} == {
        ("unlink", "public-key.pem"),
        ("unlink", "payload.bin"),
        ("unlink", "signature.bin"),
    }
    assert any(operation == "rmdir" for operation, _name in attempts)

    with monkeypatch.context() as patcher:
        patcher.setattr(
            preflight, "_run_openssl_bounded_v1",
            lambda _argv: (_ for _ in ()).throw(
                AssertionError("OpenSSL reached before residue denial")
            ),
        )
        _recovery(
            preflight._verify_ed25519_openssl_core_v1,
            b"k" * 32, b"payload", b"s" * 64,
            openssl_executable=Path("/usr/bin/openssl"), temporary_root=temporary,
            temporary_uid=os.getuid(), temporary_gid=os.getgid(),
            chain_stop=temporary,
        )


def test_product_distribution_entries_deny_non_linux_before_io(monkeypatch) -> None:
    monkeypatch.setattr(preflight.sys, "platform", "win32")
    monkeypatch.setattr(
        preflight, "_parse_distribution_manifest_v1",
        lambda *_args: (_ for _ in ()).throw(AssertionError("I/O path reached")),
    )
    for callable_, args in (
        (preflight._load_product_distribution_registry_v1, ()),
        (preflight.authenticate_distribution_v1, (b"", b"")),
        (preflight.verify_installed_distribution_v1, (object(),)),
    ):
        with pytest.raises(preflight.PreflightError) as failure:
            callable_(*args)
        assert failure.value.exit_status == preflight.EXIT_PLATFORM


def test_canonical_json_rejects_duplicate_noncanonical_and_noninteger() -> None:
    encoded = b'{"a":1,"text":"caf\\u00e9"}'
    assert preflight.decode_canonical_json_v1(encoded, len(encoded)) == {
        "a": 1, "text": "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
    }
    for mutant in (
        b'{"a":1,"a":1}', b'{"a": 1}', b'{"a":1.0}',
        b'{"a":NaN}', b'{"text":"caf\xc3\xa9"}',
    ):
        _invalid(preflight.decode_canonical_json_v1, mutant, 1024)
    _invalid(preflight.decode_canonical_json_v1, encoded, len(encoded) - 1)
    _invalid(preflight.decode_canonical_json_v1, encoded, True)
    huge_integer = b'{"a":' + b"9" * 5000 + b"}"
    _invalid(
        preflight.decode_canonical_json_v1, huge_integer, len(huge_integer),
    )
    too_deep = b"[" * 1100 + b"0" + b"]" * 1100
    _invalid(preflight.decode_canonical_json_v1, too_deep, len(too_deep))


def test_canonical_json_accepts_manifest_maximum_node_shape() -> None:
    digest = "sha256:" + "a" * 64
    value = {
        "files": [
            {"content_hash": digest, "path": f"f/{index}", "role": "runtime_code", "size": 0}
            for index in range(20_000)
        ],
        "schema_version": 1,
    }
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    assert preflight.decode_canonical_json_v1(encoded, 16 * 1024 * 1024) == value


def test_closed_identifier_and_path_grammars() -> None:
    digest = "sha256:" + "a" * 64
    assert preflight.validate_digest_v1(digest) == digest
    assert preflight.validate_entry_id_v1("service-http") == "service-http"
    exact_unit = "a" * 184 + ".service"
    assert len(exact_unit.encode()) == 192
    assert preflight.validate_unit_name_v1(exact_unit) == exact_unit
    assert preflight.validate_absolute_path_v1("/usr/bin/systemctl") == (
        "/usr/bin/systemctl"
    )
    assert preflight.validate_absolute_path_v1("/") == "/"
    assert preflight.validate_relative_path_v1("pkg/main.py") == "pkg/main.py"
    for value in ("SHA256:" + "a" * 64, "sha256:" + "g" * 64, None):
        _invalid(preflight.validate_digest_v1, value)
    for value in ("-bad", "Bad", "a" * 65):
        _invalid(preflight.validate_entry_id_v1, value)
    for value in ("a" * 185 + ".service", "name.socket", "../a.service"):
        _invalid(preflight.validate_unit_name_v1, value)
    for value in (
        "//", "//usr/bin/x", "/usr/../bin/x", "/usr//bin/x",
        "/tmp/a\\b", "/tmp/cafe\N{COMBINING ACUTE ACCENT}", "usr/bin/x",
    ):
        _invalid(preflight.validate_absolute_path_v1, value)
    for value in (
        ".", "/pkg/main.py", "../main.py", "pkg//main.py",
        "pkg\\main.py", "cafe\N{COMBINING ACUTE ACCENT}.py",
    ):
        _invalid(preflight.validate_relative_path_v1, value)


def test_systemctl_argv_is_closed_and_dash_safe() -> None:
    assert preflight.systemctl_show_argv_v1(
        "/usr/bin/systemctl", None, ("Version",),
    ) == (
        "/usr/bin/systemctl", "--no-pager", "--plain", "--all", "show",
        "--property=Version",
    )
    assert preflight.systemctl_show_argv_v1(
        "/usr/bin/systemctl", "-.slice", ("Id", "LoadState"),
    )[-2:] == ("--", "-.slice")
    for unit in (
        "home.mount", "systemd-journald.socket", "system.slice",
        "dev-sda.device", "init.scope",
    ):
        assert preflight.systemctl_show_argv_v1(
            "/usr/bin/systemctl", unit, ("Id", "LoadState"),
        )[-1] == unit
    _invalid(
        preflight.systemctl_show_argv_v1,
        "usr/bin/systemctl", None, ("Version",),
    )
    _invalid(
        preflight.systemctl_show_argv_v1,
        "/", None, ("Version",),
    )
    _invalid(
        preflight.systemctl_show_argv_v1,
        "/usr/bin/systemctl", "a.service", ("LoadState", "Id"),
    )
    _invalid(
        preflight.systemctl_show_argv_v1,
        "/usr/bin/systemctl", r"bad\q.socket", ("Id", "LoadState"),
    )
    _invalid(
        preflight.systemctl_show_argv_v1,
        "/usr/bin/systemctl", "a.service", ("Id", 1),
    )


def test_systemctl_show_parser_preserves_only_allowed_repetition() -> None:
    properties = ("LoadState", "TimersMonotonic")
    parsed = preflight.parse_systemctl_show_v1(
        b"LoadState=loaded\n"
        b"TimersMonotonic={ OnUnitActiveUSec=1d ; next_elapse=2w }\n"
        b"TimersMonotonic={ OnBootUSec=15min ; next_elapse=15min }\n",
        properties,
    )
    assert parsed["LoadState"] == ("loaded",)
    assert len(parsed["TimersMonotonic"]) == 2
    _invalid(
        preflight.parse_systemctl_show_v1,
        b"LoadState=loaded\nLoadState=loaded\n", properties,
    )
    for output in (
        b"Unknown=x\n", b"LoadState=loaded", b"LoadState=loaded\r\n",
        b"LoadState=loaded\n\n", b"LoadState=\xff\n",
    ):
        _invalid(preflight.parse_systemctl_show_v1, output, properties)


def test_effective_edge_profile_matches_supported_systemd_interface() -> None:
    relations = preflight._SYSTEMD_ADDED_EDGE_RELATIONS_V1
    assert {"Triggers", "TriggeredBy", "Conflicts", "ConflictedBy"} <= relations
    assert relations.isdisjoint({"References", "ReferencedBy"})


def test_manager_version_is_exact() -> None:
    assert preflight.parse_systemd_manager_version_v1(
        b"Version=255.4-1ubuntu8.17\n",
    ) == "255.4-1ubuntu8.17"
    _invalid(
        preflight.parse_systemd_manager_version_v1,
        b"Version=255.4-1ubuntu8.18\n",
    )


def test_systemd_word_tokenizer_is_canonical_and_bounded() -> None:
    assert preflight.tokenize_systemd_words_v1(
        'one "two\\swords" three\\x2dfour',
    ) == ("one", "two words", "three-four")
    for value in (
        " one", "one ", "one  two", '"unterminated', "bad\\q",
        "bad\\x00", "bad\\xc3",
    ):
        _invalid(preflight.tokenize_systemd_words_v1, value)


@pytest.mark.parametrize(("raw", "expected"), [
    ("100ms", "100000"), ("90s", "90000000"),
    ("1min 30s", "90000000"), ("1.5s", "1500000"),
])
def test_duration_normalization_uses_integer_microseconds(
    raw: str, expected: str,
) -> None:
    assert preflight.normalize_systemd_duration_usec_v1(raw) == expected


@pytest.mark.parametrize("raw", [
    "1.0000001s", "0.1us", "1s  2s", "1 s", "-1s", "infinity",
])
def test_duration_normalization_rejects_ambiguous_values(raw: str) -> None:
    _invalid(preflight.normalize_systemd_duration_usec_v1, raw)


def _exec_value(*, extended: bool, flags: str, argv: str = "/bin/x arg") -> str:
    field = "flags" if extended else "ignore_errors"
    return (
        f"{{ path=/bin/x ; argv[]={argv} ; {field}={flags} ; "
        "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; "
        "status=0/0 }"
    )


def test_exec_pair_preserves_privileged_prefix_and_argv() -> None:
    historical = (_exec_value(extended=False, flags="no"),)
    privileged = (_exec_value(extended=True, flags="no-setuid"),)
    result = preflight.validate_exec_property_pair_v1(
        historical, privileged, ("no-setuid",),
    )
    assert result[0] == {
        "path": "/bin/x", "argv": ("/bin/x", "arg"),
        "flags": ("no-setuid",),
    }
    assert preflight.validate_exec_property_pair_v1(
        historical, (_exec_value(extended=True, flags=""),), (),
    )[0]["flags"] == ()
    for extended in (
        (_exec_value(extended=True, flags="privileged"),),
        (_exec_value(extended=True, flags="ambient"),),
        (_exec_value(extended=True, flags="no-setuid,privileged"),),
    ):
        _invalid(
            preflight.validate_exec_property_pair_v1,
            historical, extended, ("no-setuid",),
        )
    _invalid(
        preflight.validate_exec_property_pair_v1,
        historical,
        (_exec_value(extended=True, flags="no-setuid", argv="/bin/x other"),),
        ("no-setuid",),
    )


def test_exec_pair_accepts_completed_process_and_rejects_mixed_state() -> None:
    historical = _exec_value(extended=False, flags="no").replace(
        "stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0",
        "stop_time=[Fri 2026-08-28 03:38:07 CEST] ; pid=2766513 ; "
        "code=exited ; status=0",
    )
    extended = _exec_value(extended=True, flags="").replace(
        "stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0",
        "stop_time=[Fri 2026-08-28 03:38:07 CEST] ; pid=2766513 ; "
        "code=exited ; status=0",
    )
    assert preflight.validate_exec_property_pair_v1(
        (historical,), (extended,), (),
    )[0]["path"] == "/bin/x"
    _invalid(
        preflight.validate_exec_property_pair_v1,
        (historical,), (extended.replace("status=0", "status=0/0"),), (),
    )
    _invalid(
        preflight.validate_exec_property_pair_v1,
        (historical,), (extended.replace("pid=2766513", "pid=2766514"),), (),
    )
    _invalid(
        preflight.parse_systemd_exec_v1,
        historical.replace(
            "start_time=[n/a]", "start_time=[n/a] ; injected=[x]",
        ),
        extended=False,
    )


def test_timer_parser_matches_real_repeated_systemd_255_shape() -> None:
    observed = preflight.parse_systemd_timer_properties_v1(
        (
            "{ OnUnitActiveUSec=1d ; next_elapse=2w 16min 42.105129s }",
            "{ OnBootUSec=15min ; next_elapse=15min }",
        ),
        ("{ OnCalendar=*-*-* 06,18:00:00 ; next_elapse=[n/a] }",),
    )
    assert observed == {
        "OnUnitActiveUSec": "86400000000",
        "OnBootUSec": "900000000",
        "OnCalendar": "*-*-* 06,18:00:00",
    }
    _invalid(
        preflight.parse_systemd_timer_properties_v1,
        (
            "{ OnBootUSec=15min ; next_elapse=15min }",
            "{ OnBootUSec=15min ; next_elapse=15min }",
        ), (),
    )
    _invalid(
        preflight.parse_systemd_timer_properties_v1,
        ("{ OnBootUSec=15min ; next_elapse=[n/a] ; injected=yes }",), (),
    )


def test_unset_timer_collection_is_absent_not_empty() -> None:
    """systemd omits a timer collection with no entries; it is not empty.

    Measured on systemd 255.4 with the exact argv this module builds: a timer
    carrying only monotonic entries renders `TimersMonotonic` once per entry
    and does not render `TimersCalendar` at all. Only scalar and list
    properties render empty. Claiming one value for an unset collection put a
    name in the expected set that can never be observed.
    """
    observed = {
        "TimersMonotonic": ("{ OnActiveUSec=100ms ; next_elapse=[n/a] }",),
        "AccuracyUSec": ("1ms",),
    }
    accepted = preflight._SystemdPropertyPlanV1(
        "gated_timer", ("TimersMonotonic", "TimersCalendar", "AccuracyUSec"),
        (("AccuracyUSec", 1), ("TimersCalendar", 0), ("TimersMonotonic", 1)),
    )
    preflight._validate_systemd_property_cardinality_v1(accepted, observed)

    claiming_empty_collection = preflight._SystemdPropertyPlanV1(
        "gated_timer", ("TimersMonotonic", "TimersCalendar", "AccuracyUSec"),
        (("AccuracyUSec", 1), ("TimersCalendar", 1), ("TimersMonotonic", 1)),
    )
    with pytest.raises(preflight.PreflightError) as caught:
        preflight._validate_systemd_property_cardinality_v1(
            claiming_empty_collection, observed,
        )
    # The denial names the difference: learning which property it was must not
    # cost a CI round trip.
    assert "TimersCalendar" in caught.value.detail
    assert "missing=" in caught.value.detail


def test_property_denials_name_the_difference() -> None:
    plan = preflight._SystemdPropertyPlanV1(
        "gated_service", ("ExecStart", "FragmentPath"),
        (("ExecStart", 1), ("FragmentPath", 1)),
    )
    with pytest.raises(preflight.PreflightError) as unexpected:
        preflight._validate_systemd_property_cardinality_v1(
            plan, {"ExecStart": ("x",), "FragmentPath": ("y",),
                   "Surprise": ("z",)},
        )
    assert "unexpected=Surprise" in unexpected.value.detail

    with pytest.raises(preflight.PreflightError) as cardinality:
        preflight._validate_systemd_property_cardinality_v1(
            plan, {"ExecStart": ("x", "x2"), "FragmentPath": ("y",)},
        )
    assert "ExecStart expected=1 observed=2" in cardinality.value.detail
