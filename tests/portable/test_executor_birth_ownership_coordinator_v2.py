from __future__ import annotations

import base64
import copy
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_ownership_coordinator as coordinator_module
from contract_boundary_guard import BOUNDARY_APIS
from executor_birth_cutover import CurrentInventoryV1, CurrentReceiptProof
from executor_birth_context_selection import (
    _context_selection_for_staged_reattestation_v1,
    _context_selection_from_required_chain_v1,
)
from executor_birth_context_transition import (
    current_inventory_hash_v1, issue_context_transition_v1,
)
from executor_birth_distribution_assembler import (
    DeploymentArtifactV1, build_deployment_descriptor_v1,
    build_startup_prerequisite_v1, encode_startup_prerequisite_v1,
)
from executor_birth_distribution_manifest import (
    BUILD_ID_DOMAIN, DistributionFile, _verified_distribution_for_test,
    installed_tree_hash_v1,
)
from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
from executor_birth_ownership_coordinator import (
    LegacyDispositionV2, OwnershipCoordinatorError,
    OwnershipCoordinatorRecordV1, OwnershipCoordinatorRecordV2,
    OwnershipCoordinatorStateV1, PreparedTransitionPublicationV2,
    _LockedOwnershipCoordinatorGraphSnapshotV2,
    _ObservedOwnershipCoordinatorGraphV2,
    _OwnershipCoordinatorGraphSnapshotForTestV2,
    SuccessorClaimV1, _decode_legacy_disposition_v2, _decode_record_v2,
    _decode_successor_claim_v1, _install_transaction_id_v1,
    _legacy_disposition_id_v2, _legacy_journal_hash_v2, _record_basename_v2,
    _record_hash, _record_hash_v2, _successor_claim_basename_v1,
    _prepared_record_v2, _append_prepared_transition_locked_for_test_v2,
    _build_verified_record_v2, _head_required_material_v2,
    _cross_head_boundary_locked_for_test_v2,
    _preflight_verified_record_v2,
    _cross_preflight_boundary_locked_for_test_v2,
    _certificate_published_record_v2, _certificate_ready_material_v2,
    _cross_certificate_boundary_locked_for_test_v2,
    _receipts_complete_record_v2, _startup_prerequisite_for_test,
    _startup_prerequisite_from_record_v2,
    _successor_claim_id_v1, _deployment_lock_for_test_v1,
    _append_ownership_transaction_locked_for_test_v2,
    _resolve_ownership_coordinator_locked_v2,
    _resolve_ownership_coordinator_locked_for_test_v2,
    _require_locked_coordinator_graph_snapshot_v2,
    _reserve_transition_edge_locked_for_test_v2,
)
from executor_birth_dominant_startup import (
    _complete_dominant_startup_for_test_v1,
)
from executor_birth_ownership_authorities import (
    _root_ownership_authorities_for_test,
)
from executor_birth_ownership_cutover import (
    _binding_values, _bindings_from_proof, _catalog_id,
)
from executor_birth_ownership_chain import (
    _OwnershipChainCrashForTest, _OwnershipChainStoreForTest,
)
from executor_birth_ownership_preflight import (
    _sealed_build_identity_for_test, canonical_maintenance_proof,
    maintenance_evidence_hash,
)
from executor_birth_admin_preflight import (
    DistributionFileV1 as PreflightDistributionFileV1,
    HEAD_PAYLOAD_HASH_DOMAIN_V2, HEAD_SIGNATURE_HASH_DOMAIN_V2,
    REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2, PREFLIGHT_ATTESTATION_DOMAIN_V1,
    _framed_sha256_v1, _preflight_attestation_record_hash_v1,
    _installed_tree_hash_v1 as preflight_installed_tree_hash_v1,
)
from executor_birth_prepared_set import (
    PREPARED_STATE_V1, PreparedAuthoritySetV2, PreparedSetV1,
)
from executor_birth_startup_gate import _exclusive_startup_gate_for_test_v1
import executor_birth_prepared_set as prepared_set_module


def D(character: str) -> str:
    return "sha256:" + character * 64


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def payload_bound_distribution_v2():
    path_roles = {
        "deployment/admin/preflight.py": "preflight",
        "deployment/executor-birth-deployment-v1.json": (
            "deployment_descriptor"
        ),
        "deployment/executor-birth-service-catalog-v1.json": (
            "service_catalog"
        ),
        "requirements.lock": "dependency_lock",
        "runtime/__version__.py": "product_version",
        "runtime/contract_boundary_guard.py": "boundary_guard",
        "runtime/contract_store.py": "runtime_code",
        "runtime/executor_birth.py": "runtime_code",
        "runtime/executor_birth_distribution_manifest.py": "preflight",
        "runtime/executor_birth_ownership_preflight.py": "preflight",
        "runtime/sign.py": "runtime_code",
        "share/metnos/executor-birth/birth-closed-boundary-inventory-v1.json": (
            "boundary_inventory"
        ),
        "systemd/metnos-http-birth-closed.conf": "service_unit",
    }
    files = tuple(
        DistributionFile(path, 1, D("f"), role)
        for path, role in sorted(
            path_roles.items(), key=lambda item: item[0].encode("utf-8"),
        )
    )
    value = {
        "schema_version": 1,
        "closed_build_id": None,
        "previous_closed_build_id": None,
        "release_sequence": 1,
        "product_version": "1.0.0",
        "platform": "linux",
        "architecture": "x86_64",
        "signing_key_id": "distribution-ed25519-v1-sha256-" + "1" * 64,
        "installation_root": "/opt/metnos",
        "certificate_directory": "/var/lib/metnos/executor-birth",
        "boundary_inventory_path": (
            "share/metnos/executor-birth/"
            "birth-closed-boundary-inventory-v1.json"
        ),
        "boundary_inventory_hash": D("e"),
        "boundary_guard_version": "guard-v2",
        "preflight_entrypoint": "deployment/admin/preflight.py",
        "files": [{
            "path": file.path,
            "size": file.size,
            "content_hash": file.content_hash,
            "role": file.role,
        } for file in files],
    }
    value["closed_build_id"] = digest(
        BUILD_ID_DOMAIN + canonical({
            key: item for key, item in value.items()
            if key != "closed_build_id"
        }),
    )
    encoded = canonical(value)
    distribution = _verified_distribution_for_test(
        _sealed_build_identity_for_test(
            value["closed_build_id"], value["boundary_inventory_hash"],
            value["boundary_guard_version"],
        ),
        previous_closed_build_id=None,
        release_sequence=1,
        encoded=encoded,
        signature=b"s" * 64,
    )
    return replace(
        distribution,
        files=files,
        preflight_entrypoint="deployment/admin/preflight.py",
    )


def digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the root-owned coordinator store is Linux-only",
)


def request_id(
    closed_build_id: str, previous_closed_build_id: str | None,
    previous_cutover_id: str | None,
) -> str:
    framed = bytearray(
        b"metnos.executor-birth.ownership-coordinator-request/v1\0"
    )
    for field in (
        closed_build_id, previous_closed_build_id or "none",
        previous_cutover_id or "none",
    ):
        raw = field.encode("ascii")
        framed.extend(len(raw).to_bytes(8, "big"))
        framed.extend(raw)
    return digest(bytes(framed))


def successor_claim_value(
    *, release_sequence: int = 1, previous_head_id: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "previous_head_id": previous_head_id,
        "release_sequence": release_sequence,
        "request_id": D("1"),
        "source_id": D("2"),
        "closed_build_id": D("3"),
    }
    value["claim_id"] = digest(
        b"metnos.executor-birth.successor-claim/v1\0" + canonical(value),
    )
    return value


@pytest.mark.parametrize(
    ("release_sequence", "previous_head_id", "basename"),
    ((1, None, "initial.json"), (2, D("a"), "a" * 64 + ".json")),
)
def test_successor_claim_codec_and_locator(
    release_sequence, previous_head_id, basename,
):
    value = successor_claim_value(
        release_sequence=release_sequence,
        previous_head_id=previous_head_id,
    )
    claim = SuccessorClaimV1(
        claim_id=value["claim_id"],
        previous_head_id=previous_head_id,
        release_sequence=release_sequence,
        request_id=value["request_id"],
        source_id=value["source_id"],
        closed_build_id=value["closed_build_id"],
    )

    assert claim.encode() == canonical(value)
    assert _decode_successor_claim_v1(claim.encode()) == claim
    assert _successor_claim_basename_v1(
        release_sequence, previous_head_id,
    ) == basename
    without_id = {key: item for key, item in value.items() if key != "claim_id"}
    assert _successor_claim_id_v1(without_id) == value["claim_id"]


@pytest.mark.parametrize("mutation", (
    lambda value: value.update(schema_version=True),
    lambda value: value.update(release_sequence=True),
    lambda value: value.update(previous_head_id=D("a")),
    lambda value: value.update(claim_id=D("f")),
    lambda value: value.update(extra=None),
))
def test_successor_claim_codec_rejects_noncanonical_or_inconsistent_values(
    mutation,
):
    value = successor_claim_value()
    mutation(value)
    with pytest.raises(OwnershipCoordinatorError):
        _decode_successor_claim_v1(canonical(value))

    valid = canonical(successor_claim_value())
    with pytest.raises(OwnershipCoordinatorError):
        _decode_successor_claim_v1(valid + b"\n")


def legacy_disposition_value(records: tuple[bytes, ...]) -> dict[str, object]:
    framed = bytearray(b"metnos.executor-birth.legacy-journal/v2\0")
    framed.extend(len(records).to_bytes(8, "big"))
    for record in records:
        framed.extend(len(record).to_bytes(8, "big"))
        framed.extend(record)
    value: dict[str, object] = {
        "schema_version": 2,
        "legacy_journal_hash": digest(bytes(framed)),
        "legacy_request_id": D("4"),
        "legacy_state": "RECEIPTS_COMPLETE",
        "successor_request_id": D("5"),
        "reason": "superseded_before_certificate",
    }
    value["disposition_id"] = digest(
        b"metnos.executor-birth.legacy-disposition/v2\0" + canonical(value),
    )
    return value


def test_legacy_disposition_hashes_original_v1_bytes_and_round_trips():
    records = (b'{"original":1}', b'{"spacing": "preserved"}')
    value = legacy_disposition_value(records)
    disposition = LegacyDispositionV2(
        disposition_id=value["disposition_id"],
        legacy_journal_hash=value["legacy_journal_hash"],
        legacy_request_id=value["legacy_request_id"],
        legacy_state=OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE,
        successor_request_id=value["successor_request_id"],
    )

    assert _legacy_journal_hash_v2(records) == value["legacy_journal_hash"]
    assert _legacy_journal_hash_v2((records[0], records[1] + b" ")) != (
        value["legacy_journal_hash"]
    )
    assert disposition.encode() == canonical(value)
    assert _decode_legacy_disposition_v2(disposition.encode()) == disposition
    without_id = {
        key: item for key, item in value.items() if key != "disposition_id"
    }
    assert _legacy_disposition_id_v2(without_id) == value["disposition_id"]


@pytest.mark.parametrize(("field", "replacement"), (
    ("schema_version", True),
    ("legacy_state", "CERTIFICATE_READY"),
    ("reason", "other"),
    ("disposition_id", D("f")),
))
def test_legacy_disposition_codec_rejects_mutants(field, replacement):
    value = legacy_disposition_value((b"one",))
    value[field] = replacement
    with pytest.raises(OwnershipCoordinatorError):
        _decode_legacy_disposition_v2(canonical(value))


def maintenance() -> bytes:
    return canonical_maintenance_proof(
        source="inactive_http_and_inactive_sidecar",
        units=tuple({
            "scope": scope,
            "unit": unit,
            "load_state": "loaded",
            "active_state": "inactive",
            "main_pid": 0,
        } for scope, unit in MAINTENANCE_TARGETS_V1),
    )


def proof() -> CurrentReceiptProof:
    identities = (("executor:alpha", D("7")),)
    return CurrentReceiptProof(identities, {identities[0]: D("8")})


class _StartupSession:
    def __reduce__(self):
        raise TypeError("startup lock sessions are not transferable")


def dominant_receipt(
    complete: OwnershipCoordinatorRecordV2, *, catalog_id: str,
):
    values = {
        "identity": (
            complete.request_id,
            complete.previous_head_id or D("4"),
            complete.context_transition_id,
        ),
        "topology": D("a"),
        "catalog": catalog_id,
        "retirement": D("c"),
        "enforcement": D("d"),
    }
    return _complete_dominant_startup_for_test_v1(
        sessions=(_StartupSession(), _StartupSession(), _StartupSession()),
        observe_identity=lambda: values["identity"],
        observe_topology=lambda: values["topology"],
        observe_catalog=lambda: values["catalog"],
        plan_retirement=lambda: values["retirement"],
        observe_enforcement=lambda: values["enforcement"],
        cross=lambda _receipt: None,
    )


def portable_authorities():
    return _root_ownership_authorities_for_test(*(
        Ed25519PrivateKey.from_private_bytes(
            hashlib.sha256(name.encode("ascii")).digest()
        )
        for name in ("v2-distribution", "v2-cutover", "v2-head")
    ))


def proof_catalog_id() -> str:
    return _catalog_id(_binding_values(_bindings_from_proof(proof())))


def preflight_attestation(
    head_required: OwnershipCoordinatorRecordV2, **replacements,
) -> bytes:
    value: dict[str, object] = {
        "schema_version": 1,
        "attestation_id": None,
        "request_id": head_required.request_id,
        "closed_build_id": head_required.closed_build_id,
        "release_sequence": head_required.release_sequence,
        "head_id": head_required.head_id,
        "required_head_frame_hash": head_required.required_head_frame_hash,
        "deployment_descriptor_id": head_required.deployment_descriptor_id,
        "service_catalog_id": D("1"),
        "service_coverage_hash": head_required.service_coverage_hash,
        "candidate_units_hash": D("2"),
        "administrative_bundle_hash": head_required.administrative_bundle_hash,
        "python_binary_hash": D("3"),
        "openssl_binary_hash": D("4"),
        "openssl_tcb_hash": D("5"),
        "systemctl_binary_hash": D("6"),
        "systemd_analyze_binary_hash": D("7"),
        "effective_units_hash": D("8"),
        "checked_entry_ids": ["probe-target"],
    }
    value.update(replacements)
    unsigned = dict(value)
    unsigned.pop("attestation_id")
    value["attestation_id"] = digest(
        PREFLIGHT_ATTESTATION_DOMAIN_V1 + canonical(unsigned),
    )
    return canonical(value)


def current_generation(tmp_path):
    from executor_birth_cutover import CurrentGeneration
    from manifest_inventory import (
        ContractId, ManifestOrigin, ManifestRef, ManifestStatus,
    )

    relative = "alpha/manifest.toml"
    contract_id = ContractId(ManifestOrigin.EXPLICIT, relative)
    manifest = tmp_path / relative
    return CurrentGeneration(
        ManifestRef(
            contract_id, ManifestOrigin.EXPLICIT, ManifestStatus.ADMITTED,
            tmp_path, manifest, relative, (manifest.parent,),
        ),
        D("7"),
    )


def install_maintenance_fixture(monkeypatch, tmp_path, *, drift: bool):
    import contract_cutover_guard as guard_module

    evidence = json.loads(maintenance())

    class Maintenance:
        def __call__(self):
            return True

        def observe(self):
            return evidence

    @contextmanager
    def guard():
        yield Maintenance(), evidence

    item = current_generation(tmp_path)

    class Port:
        calls = 0

        def enumerate_current(self):
            self.calls += 1
            return () if drift and self.calls > 1 else (item,)

    verified = []
    monkeypatch.setattr(guard_module, "contract_cutover_guard", guard)
    monkeypatch.setattr(
        guard_module, "_verify_store_only_catalog_locked",
        lambda: verified.append(True),
    )
    monkeypatch.setattr(
        guard_module, "_maintenance_evidence_under_transition_v1",
        lambda _session: canonical(evidence),
    )
    port = Port()
    monkeypatch.setattr(
        coordinator_module, "_current_reattestation_port_v1", lambda: port,
    )
    return port, verified, item


def test_transition_maintenance_freezes_and_rechecks_the_exact_inventory(
    monkeypatch, tmp_path,
):
    port, verified, item = install_maintenance_fixture(
        monkeypatch, tmp_path, drift=False,
    )

    with coordinator_module._transition_maintenance_inventory_v2() as frozen:
        prove_quiescent, inventory, evidence = frozen
        assert prove_quiescent() is True
        assert inventory.identities == (item.identity,)
        assert evidence == maintenance()

    assert port.calls == 2
    assert verified == [True, True]


def test_transition_maintenance_rejects_inventory_drift_on_exit(
    monkeypatch, tmp_path,
):
    install_maintenance_fixture(monkeypatch, tmp_path, drift=True)

    with pytest.raises(
        OwnershipCoordinatorError,
        match="birth_ownership_recovery_required",
    ):
        with coordinator_module._transition_maintenance_inventory_v2():
            pass


def test_transition_maintenance_preserves_the_body_failure(
    monkeypatch, tmp_path,
):
    install_maintenance_fixture(monkeypatch, tmp_path, drift=True)
    body_failure = OwnershipCoordinatorError(
        "birth_cutover_reattestation_failed", "contract-alpha",
    )

    with pytest.raises(OwnershipCoordinatorError) as failed:
        with coordinator_module._transition_maintenance_inventory_v2():
            raise body_failure

    assert failed.value is body_failure
    assert failed.value.code == "birth_cutover_reattestation_failed"
    assert failed.value.detail == "contract-alpha"


def record_v2(sequence: int) -> OwnershipCoordinatorRecordV2:
    install_value = {
        "schema_version": 1,
        "request_id": D("1"),
        "source_id": D("2"),
        "closed_build_id": D("3"),
        "release_sequence": 2,
        "previous_head_id": D("4"),
        "successor_claim_id": D("5"),
        "deployment_descriptor_id": D("6"),
        "service_coverage_hash": D("7"),
        "administrative_bundle_hash": D("8"),
    }
    transaction_id = digest(
        b"metnos.executor-birth.install-transaction/v1\0"
        + canonical(install_value),
    )
    evidence = maintenance() if sequence >= 1 else None
    evidence_hash = maintenance_evidence_hash(evidence) if evidence else None
    current = proof()
    return OwnershipCoordinatorRecordV2(
        sequence=sequence,
        state=tuple(OwnershipCoordinatorStateV1)[sequence],
        previous_record_sha256=None if sequence == 0 else D("9"),
        request_id=install_value["request_id"],
        previous_closed_build_id=D("a"),
        previous_cutover_id=D("b"),
        closed_build_id=install_value["closed_build_id"],
        distribution_payload_hash=D("c"),
        distribution_signature_hash=D("d"),
        boundary_inventory_hash=D("e"),
        boundary_guard_version="guard-v2",
        source_id=install_value["source_id"],
        successor_claim_id=install_value["successor_claim_id"],
        deployment_descriptor_id=install_value["deployment_descriptor_id"],
        install_transaction_id=transaction_id,
        release_sequence=install_value["release_sequence"],
        previous_head_id=install_value["previous_head_id"],
        service_coverage_hash=install_value["service_coverage_hash"],
        administrative_bundle_hash=install_value["administrative_bundle_hash"],
        provisioning_transaction_id="0" * 32,
        previous_set_id="1" * 64,
        previous_admission_context_id=D("2"),
        previous_context_epoch=D("3"),
        target_set_id="4" * 64,
        target_admission_context_id=D("5"),
        target_context_epoch=D("6"),
        target_context_material_sha256="7" * 64,
        target_set_json_sha256="8" * 64,
        context_transition_id=D("9"),
        current_inventory_hash=current_inventory_hash_v1(current.inventory),
        current_proof=current if sequence >= 1 else None,
        maintenance_before_hash=evidence_hash,
        maintenance_after_hash=evidence_hash,
        maintenance_proof=evidence,
        startup_prerequisite_id=D("1") if sequence >= 2 else None,
        startup_prerequisite_digest=D("2") if sequence >= 2 else None,
        cutover_id=D("3") if sequence >= 2 else None,
        catalog_id=D("4") if sequence >= 2 else None,
        certificate_payload_hash=D("5") if sequence >= 2 else None,
        certificate_signature_hash=D("6") if sequence >= 2 else None,
        dominant_startup_receipt=D("e") if sequence >= 2 else None,
        installed_tree_hash=D("7") if sequence >= 4 else None,
        head_id=D("8") if sequence >= 5 else None,
        head_payload_hash=D("9") if sequence >= 5 else None,
        head_signature_hash=D("a") if sequence >= 5 else None,
        required_head_frame_hash=D("b") if sequence >= 5 else None,
        verified_chain_head_id=D("8") if sequence >= 5 else None,
        preflight_attestation_hash=D("d") if sequence >= 6 else None,
    )


@pytest.mark.parametrize("sequence", range(7))
def test_v2_codec_state_threshold_table(sequence):
    record = record_v2(sequence)
    encoded = record.encode()
    value = json.loads(encoded)

    assert len(value) == 49
    assert _decode_record_v2(encoded) == record
    assert _record_basename_v2(sequence) == f"record-{sequence:03d}-v2.json"
    assert _install_transaction_id_v1(
        record.install_transaction_value(),
    ) == record.install_transaction_id
    assert _record_hash_v2(encoded) != _record_hash(encoded)


def test_receipts_complete_v2_carries_prepared_and_requires_exact_inventory():
    prepared = record_v2(0)
    complete = _receipts_complete_record_v2(
        prepared,
        proof=proof(),
        maintenance_before=maintenance(),
        maintenance_after=maintenance(),
    )

    assert complete.sequence == 1
    assert complete.state is OwnershipCoordinatorStateV1.RECEIPTS_COMPLETE
    assert complete.previous_record_sha256 == _record_hash_v2(
        prepared.encode(),
    )
    assert complete.current_proof == proof()
    assert complete.context_transition_id == prepared.context_transition_id
    assert _decode_record_v2(complete.encode()) == complete
    with pytest.raises(
        OwnershipCoordinatorError,
        match="birth_ownership_receipt_proof_invalid",
    ):
        _receipts_complete_record_v2(
            prepared,
            proof=CurrentReceiptProof((), {}),
            maintenance_before=maintenance(),
            maintenance_after=maintenance(),
        )


def startup_prerequisite(complete: OwnershipCoordinatorRecordV2):
    return build_startup_prerequisite_v1(
        request_id=complete.request_id,
        closed_build_id=complete.closed_build_id,
        release_sequence=complete.release_sequence,
        deployment_descriptor_id=complete.deployment_descriptor_id,
        predecessor_id=D("a"),
        administrative_bundle_hash=complete.administrative_bundle_hash,
        python_binary_hash=D("b"),
        openssl_binary_hash=D("c"),
        openssl_tcb_hash=D("d"),
        systemctl_binary_hash=D("e"),
        systemd_analyze_binary_hash=D("f"),
        service_catalog_id=D("1"),
        service_coverage_hash=complete.service_coverage_hash,
        systemd_manager_version="255.4",
        candidate_units_hash=D("2"),
        effective_units_hash=D("3"),
    )


def test_product_prerequisite_seal_binds_canonical_bytes_to_complete_v2():
    complete = record_v2(1)
    record = startup_prerequisite(complete)
    sealed = _startup_prerequisite_from_record_v2(record, complete)

    assert sealed.prerequisite_id == record.prerequisite_id
    assert sealed.evidence_digest == digest(
        encode_startup_prerequisite_v1(record),
    )


@pytest.mark.parametrize("field", (
    "request_id", "closed_build_id", "release_sequence",
    "deployment_descriptor_id", "administrative_bundle_hash",
    "service_coverage_hash",
))
def test_product_prerequisite_seal_rejects_crossed_v2_bindings(field):
    complete = record_v2(1)
    record = startup_prerequisite(complete)
    replacement = complete.release_sequence + 1 if field == "release_sequence" else D("0")
    with pytest.raises(
        OwnershipCoordinatorError,
        match="startup prerequisite binding",
    ):
        _startup_prerequisite_from_record_v2(
            replace(record, **{field: replacement}), complete,
        )

    with pytest.raises(
        OwnershipCoordinatorError,
        match="birth_ownership_prerequisite_untrusted",
    ):
        _startup_prerequisite_from_record_v2(record, record_v2(0))


def test_certificate_ready_v2_requires_a_sealed_crossing_and_exact_bytes():
    complete = record_v2(1)
    prerequisite = _startup_prerequisite_for_test(D("1"), D("2"))
    receipt = dominant_receipt(complete, catalog_id=proof_catalog_id())
    material = _certificate_ready_material_v2(
        complete,
        authorities=portable_authorities(),
        prerequisite=prerequisite,
        observe_maintenance=maintenance,
        crossing_receipt=receipt,
    )

    ready = material.record
    assert ready.sequence == 2
    assert ready.state is OwnershipCoordinatorStateV1.CERTIFICATE_READY
    assert ready.previous_record_sha256 == _record_hash_v2(complete.encode())
    assert ready.startup_prerequisite_id == prerequisite.prerequisite_id
    assert ready.startup_prerequisite_digest == prerequisite.evidence_digest
    assert ready.dominant_startup_receipt == receipt.dominant_startup_receipt
    assert ready.certificate_payload_hash == digest(material.payload)
    assert ready.certificate_signature_hash == digest(material.signature)
    assert material.certificate.as_proof() == proof()
    assert _decode_record_v2(ready.encode()) == ready

    published = _certificate_published_record_v2(ready)
    assert published.state is OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED
    assert published.previous_record_sha256 == _record_hash_v2(ready.encode())
    assert published.dominant_startup_receipt == receipt.dominant_startup_receipt
    assert _decode_record_v2(published.encode()) == published


def test_certificate_ready_v2_rejects_unsealed_or_drifting_evidence():
    complete = record_v2(1)
    arguments = {
        "authorities": portable_authorities(),
        "prerequisite": _startup_prerequisite_for_test(D("1"), D("2")),
        "observe_maintenance": maintenance,
        "crossing_receipt": dominant_receipt(
            complete, catalog_id=proof_catalog_id(),
        ),
    }
    with pytest.raises(
        OwnershipCoordinatorError,
        match="birth_ownership_prerequisite_untrusted",
    ):
        _certificate_ready_material_v2(
            complete, **{**arguments, "crossing_receipt": D("e")},
        )
    with pytest.raises(
        OwnershipCoordinatorError,
        match="maintenance drift",
    ):
        _certificate_ready_material_v2(
            complete,
            **{
                **arguments,
                "observe_maintenance": lambda: maintenance() + b" ",
            },
        )
    with pytest.raises(
        OwnershipCoordinatorError,
        match="certificate ready binding",
    ):
        _certificate_ready_material_v2(
            complete,
            **{
                **arguments,
                "crossing_receipt": dominant_receipt(
                    complete, catalog_id=D("f"),
                ),
            },
        )


def test_build_and_head_records_bind_exact_verified_material():
    distribution = payload_bound_distribution_v2()
    claim = bound_claim(
        release_sequence=1,
        previous_head_id=None,
        closed_build_id=distribution.identity.closed_build_id,
        source_id=D("2"),
        previous_closed_build_id=None,
        previous_cutover_id=None,
    )
    published = transaction_records(
        claim,
        end_sequence=3,
        previous_closed_build_id=None,
        previous_cutover_id=None,
        cutover_id=D("3"),
        head_id=D("4"),
    )[-1]
    published = replace(
        published,
        distribution_payload_hash=digest(distribution.encoded),
        distribution_signature_hash=digest(distribution.signature),
        boundary_inventory_hash=(
            distribution.identity.boundary_inventory_hash
        ),
        boundary_guard_version=(
            distribution.identity.boundary_guard_version
        ),
    )

    verified = _build_verified_record_v2(published, distribution)
    assert verified.state is OwnershipCoordinatorStateV1.BUILD_VERIFIED
    assert verified.previous_record_sha256 == _record_hash_v2(
        published.encode(),
    )
    assert verified.installed_tree_hash == installed_tree_hash_v1(
        distribution.files,
    )
    assert verified.installed_tree_hash == preflight_installed_tree_hash_v1(
        tuple(
            PreflightDistributionFileV1(
                item.path, item.size, item.content_hash, item.role,
            )
            for item in distribution.files
        ),
    )
    assert _decode_record_v2(verified.encode()) == verified

    material = _head_required_material_v2(
        verified, authorities=portable_authorities(),
    )
    assert material.record.state is OwnershipCoordinatorStateV1.HEAD_REQUIRED
    assert material.head.release_sequence == 1
    assert material.head.cutover_id == published.cutover_id
    assert material.head.closed_build_id == published.closed_build_id
    assert material.head.previous_head_id is None
    assert material.record.head_payload_hash == _framed_sha256_v1(
        HEAD_PAYLOAD_HASH_DOMAIN_V2, material.encoded,
    )
    assert material.record.head_signature_hash == _framed_sha256_v1(
        HEAD_SIGNATURE_HASH_DOMAIN_V2, material.signature,
    )
    assert material.record.required_head_frame_hash == _framed_sha256_v1(
        REQUIRED_HEAD_FRAME_HASH_DOMAIN_V2, material.frame,
    )
    assert material.record.verified_chain_head_id == material.head.head_id
    assert _decode_record_v2(material.record.encode()) == material.record


@pytest.mark.parametrize("mutation", (
    lambda record: replace(record, previous_closed_build_id=D("0")),
    lambda record: replace(record, distribution_payload_hash=D("0")),
    lambda record: replace(record, boundary_inventory_hash=D("0")),
))
def test_build_verified_record_rejects_distribution_drift(mutation):
    distribution = payload_bound_distribution_v2()
    claim = bound_claim(
        release_sequence=1,
        previous_head_id=None,
        closed_build_id=distribution.identity.closed_build_id,
        source_id=D("2"),
        previous_closed_build_id=None,
        previous_cutover_id=None,
    )
    published = transaction_records(
        claim,
        end_sequence=3,
        previous_closed_build_id=None,
        previous_cutover_id=None,
        cutover_id=D("3"),
        head_id=D("4"),
    )[-1]
    published = replace(
        published,
        distribution_payload_hash=digest(distribution.encoded),
        distribution_signature_hash=digest(distribution.signature),
        boundary_inventory_hash=distribution.identity.boundary_inventory_hash,
        boundary_guard_version=distribution.identity.boundary_guard_version,
    )
    with pytest.raises(
        OwnershipCoordinatorError,
        match="birth_ownership_recovery_required",
    ):
        _build_verified_record_v2(mutation(published), distribution)


def _complete_initial_head_crossing_v2(
    tmp_path: Path, interruption_stage: str | None,
):
    ownership_root = tmp_path / "ownership"
    authorities = portable_authorities()
    distribution = payload_bound_distribution_v2()
    claim = bound_claim(
        release_sequence=1,
        previous_head_id=None,
        closed_build_id=distribution.identity.closed_build_id,
        source_id=D("2"),
        previous_closed_build_id=None,
        previous_cutover_id=None,
    )
    _encoded_transition, transition = issue_context_transition_v1(
        request_id=claim.request_id,
        closed_build_id=claim.closed_build_id,
        previous_cutover_id=None,
        previous_set_id="1" * 64,
        previous_admission_context_id=D("2"),
        previous_context_epoch=D("3"),
        set_id="4" * 64,
        prepared_admission_context_id=D("5"),
        prepared_context_epoch=D("6"),
        context_material_sha256="7" * 64,
        set_json_sha256="8" * 64,
        current_inventory=proof().inventory,
    )
    records = transaction_records(
        claim,
        end_sequence=1,
        previous_closed_build_id=None,
        previous_cutover_id=None,
        cutover_id=D("3"),
        head_id=D("4"),
        distribution=distribution,
        context_transition_id=transition.transition_id,
    )
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(mode=0o700)
    gate_path = runtime_root / "startup-v1.lock"
    gate_path.touch(mode=0o600)

    with _deployment_lock_for_test_v1(ownership_root) as deployment_session:
        directory = make_coordinator_root(ownership_root)
        write_claim(directory, claim)
        write_transaction(directory, claim, records)
        store = _OwnershipChainStoreForTest._initialize_with_authorities(
            ownership_root / "chain-v1", authorities.public,
        )
        store.append_context_transition(
            transition.encoded, expected_proof=proof(),
        )
        complete = records[-1]
        certificate_material = _certificate_ready_material_v2(
            complete,
            authorities=authorities,
            prerequisite=_startup_prerequisite_for_test(D("1"), D("2")),
            observe_maintenance=lambda: complete.maintenance_proof,
            crossing_receipt=dominant_receipt(
                complete, catalog_id=proof_catalog_id(),
            ),
        )
        published = _cross_certificate_boundary_locked_for_test_v2(
            deployment_session,
            ownership_root,
            certificate_material,
            authorities=authorities,
        )
        arguments = dict(
            ownership_root=ownership_root,
            gate_path=gate_path,
            published=published,
            distribution=distribution,
            authorities=authorities,
            chain_store=store,
            builds={distribution.identity.closed_build_id: distribution},
        )
        with _exclusive_startup_gate_for_test_v1(gate_path) as startup_session:
            if interruption_stage is not None:
                def interrupt(stage):
                    if stage == interruption_stage:
                        raise _OwnershipChainCrashForTest(stage)

                with pytest.raises(
                    _OwnershipChainCrashForTest,
                    match=interruption_stage,
                ):
                    _cross_head_boundary_locked_for_test_v2(
                        deployment_session,
                        startup_session,
                        **arguments,
                        _crash_seam=interrupt,
                    )
            result = _cross_head_boundary_locked_for_test_v2(
                deployment_session, startup_session, **arguments,
            )
            repeated = _cross_head_boundary_locked_for_test_v2(
                deployment_session, startup_session, **arguments,
            )
        graph = _resolve_ownership_coordinator_locked_for_test_v2(
            deployment_session, ownership_root,
        ).observation
        required = store.read_required_head()
    return result, repeated, graph, required, distribution, certificate_material


@LINUX_ONLY
def test_head_crossing_publishes_one_verified_required_prefix(tmp_path):
    result, repeated, graph, required, distribution, certificate = (
        _complete_initial_head_crossing_v2(tmp_path, None)
    )
    assert result is repeated or result == repeated
    assert result.state is OwnershipCoordinatorStateV1.HEAD_REQUIRED
    assert result.installed_tree_hash == installed_tree_hash_v1(
        distribution.files,
    )
    assert result.head_id == required.head_id
    assert result.cutover_id == certificate.certificate.cutover_id
    assert graph.transactions[-1].latest == result
    assert len(graph.transactions[-1].records) == 6


@LINUX_ONLY
@pytest.mark.parametrize("interruption_stage", (
    "build_verified",
    "cutover_after_signature",
    "build_after_signature",
    "head_after_signature",
    "required_before_replace",
    "required_after_replace",
    "required_chain_verified",
    "head_required",
))
def test_head_crossing_converges_across_every_durable_boundary(
    tmp_path, interruption_stage,
):
    result, repeated, graph, required, _distribution, _certificate = (
        _complete_initial_head_crossing_v2(tmp_path, interruption_stage)
    )
    assert result == repeated == graph.transactions[-1].latest
    assert result.state is OwnershipCoordinatorStateV1.HEAD_REQUIRED
    assert required.head_id == result.head_id


def _complete_initial_preflight_crossing_v2(tmp_path, interruption_stage):
    head_required, _repeat, _graph, _required, _distribution, _certificate = (
        _complete_initial_head_crossing_v2(tmp_path, None)
    )
    ownership_root = tmp_path / "ownership"
    gate_path = tmp_path / "runtime" / "startup-v1.lock"
    attestation_root = ownership_root / "preflight-attestations-v1"
    attestation_root.mkdir(mode=0o755)
    encoded = preflight_attestation(head_required)
    arguments = dict(
        ownership_root=ownership_root,
        gate_path=gate_path,
        attestation_root=attestation_root,
        head_required=head_required,
        encoded_attestation=encoded,
    )
    with _deployment_lock_for_test_v1(ownership_root) as deployment_session:
        with _exclusive_startup_gate_for_test_v1(gate_path) as startup_session:
            if interruption_stage is not None:
                def interrupt(stage):
                    if stage == interruption_stage:
                        raise _OwnershipChainCrashForTest(stage)

                with pytest.raises(
                    _OwnershipChainCrashForTest,
                    match=interruption_stage,
                ):
                    _cross_preflight_boundary_locked_for_test_v2(
                        deployment_session, startup_session, **arguments,
                        _crash_seam=interrupt,
                    )
            result = _cross_preflight_boundary_locked_for_test_v2(
                deployment_session, startup_session, **arguments,
            )
            before_repeat = tree_snapshot(ownership_root)
            repeated = _cross_preflight_boundary_locked_for_test_v2(
                deployment_session, startup_session, **arguments,
            )
            assert tree_snapshot(ownership_root) == before_repeat
        graph = _resolve_ownership_coordinator_locked_for_test_v2(
            deployment_session, ownership_root,
        ).observation
    return result, repeated, graph, encoded


@LINUX_ONLY
def test_preflight_crossing_publishes_exact_attestation_and_final_record(
    tmp_path,
):
    result, repeated, graph, encoded = _complete_initial_preflight_crossing_v2(
        tmp_path, None,
    )
    assert result == repeated == graph.transactions[-1].latest
    assert result.state is OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED
    assert result.preflight_attestation_hash == (
        _preflight_attestation_record_hash_v1(encoded)
    )
    assert len(graph.transactions[-1].records) == 7
    assert (
        tmp_path / "ownership" / "preflight-attestations-v1"
        / f"{result.request_id}.json"
    ).read_bytes() == encoded


@LINUX_ONLY
@pytest.mark.parametrize("interruption_stage", (
    "preflight_attestation_published", "preflight_verified",
))
def test_preflight_crossing_converges_across_durable_boundaries(
    tmp_path, interruption_stage,
):
    result, repeated, graph, _encoded = _complete_initial_preflight_crossing_v2(
        tmp_path, interruption_stage,
    )
    assert result == repeated == graph.transactions[-1].latest
    assert result.state is OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED


@pytest.mark.parametrize(("field", "replacement"), (
    ("request_id", D("f")),
    ("closed_build_id", D("f")),
    ("release_sequence", 3),
    ("head_id", D("f")),
    ("required_head_frame_hash", D("f")),
    ("deployment_descriptor_id", D("f")),
    ("service_coverage_hash", D("f")),
    ("administrative_bundle_hash", D("f")),
))
def test_preflight_record_rejects_every_foreign_journal_binding(
    field, replacement,
):
    head_required = record_v2(5)
    with pytest.raises(
        OwnershipCoordinatorError, match="preflight binding",
    ):
        _preflight_verified_record_v2(
            head_required,
            preflight_attestation(head_required, **{field: replacement}),
        )


@LINUX_ONLY
def test_preflight_crossing_refuses_conflicting_durable_attestation(tmp_path):
    head_required, _repeat, _graph, _required, _distribution, _certificate = (
        _complete_initial_head_crossing_v2(tmp_path, None)
    )
    ownership_root = tmp_path / "ownership"
    gate_path = tmp_path / "runtime" / "startup-v1.lock"
    attestation_root = ownership_root / "preflight-attestations-v1"
    attestation_root.mkdir(mode=0o755)
    expected = preflight_attestation(head_required)
    foreign = preflight_attestation(
        head_required, effective_units_hash=D("f"),
    )
    (attestation_root / f"{head_required.request_id}.json").write_bytes(
        foreign,
    )

    with _deployment_lock_for_test_v1(ownership_root) as deployment_session:
        with _exclusive_startup_gate_for_test_v1(gate_path) as startup_session:
            with pytest.raises(
                OwnershipCoordinatorError, match="preflight publication",
            ):
                _cross_preflight_boundary_locked_for_test_v2(
                    deployment_session, startup_session,
                    ownership_root=ownership_root, gate_path=gate_path,
                    attestation_root=attestation_root,
                    head_required=head_required,
                    encoded_attestation=expected,
                )
        graph = _resolve_ownership_coordinator_locked_for_test_v2(
            deployment_session, ownership_root,
        ).observation
    assert graph.transactions[-1].latest == head_required
    assert (
        attestation_root / f"{head_required.request_id}.json"
    ).read_bytes() == foreign


@LINUX_ONLY
@pytest.mark.parametrize(
    "interruption_stage", ("certificate_ready", "certificate_signature"),
)
def test_certificate_boundary_v2_recovers_only_after_durable_ready(
    tmp_path, interruption_stage,
):
    ownership_root = tmp_path / "ownership"
    authorities = portable_authorities()
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1,
            previous_head_id=None,
            closed_build_id=D("3"),
            source_id=D("2"),
            previous_closed_build_id=None,
            previous_cutover_id=None,
        )
        records = transaction_records(
            claim,
            end_sequence=1,
            previous_closed_build_id=None,
            previous_cutover_id=None,
            cutover_id=D("4"),
            head_id=D("5"),
        )
        write_claim(directory, claim)
        write_transaction(directory, claim, records)
        complete = records[-1]
        material = _certificate_ready_material_v2(
            complete,
            authorities=authorities,
            prerequisite=_startup_prerequisite_for_test(D("1"), D("2")),
            observe_maintenance=lambda: complete.maintenance_proof,
            crossing_receipt=dominant_receipt(
                complete, catalog_id=proof_catalog_id(),
            ),
        )

        class Interrupted(Exception):
            pass

        def interrupt(stage):
            if stage == interruption_stage:
                raise Interrupted

        with pytest.raises(Interrupted):
            _cross_certificate_boundary_locked_for_test_v2(
                session,
                ownership_root,
                material,
                authorities=authorities,
                _crash_seam=interrupt,
            )
        interrupted = _resolve_ownership_coordinator_locked_for_test_v2(
            session, ownership_root,
        ).observation.transactions[-1]
        assert interrupted.latest.state is (
            OwnershipCoordinatorStateV1.CERTIFICATE_READY
        )

        published = _cross_certificate_boundary_locked_for_test_v2(
            session, ownership_root, material, authorities=authorities,
        )
        assert published.state is (
            OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED
        )
        final = _resolve_ownership_coordinator_locked_for_test_v2(
            session, ownership_root,
        ).observation.transactions[-1]
        assert final.records == records + (material.record, published)
        assert (ownership_root / "ownership-cutover-v1.json").read_bytes() == (
            material.payload
        )
        assert (ownership_root / "ownership-cutover-v1.sig").read_bytes() == (
            material.signature
        )


@LINUX_ONLY
def test_certificate_boundary_v2_never_adopts_bytes_before_ready(tmp_path):
    ownership_root = tmp_path / "ownership"
    authorities = portable_authorities()
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1,
            previous_head_id=None,
            closed_build_id=D("3"),
            source_id=D("2"),
            previous_closed_build_id=None,
            previous_cutover_id=None,
        )
        records = transaction_records(
            claim,
            end_sequence=1,
            previous_closed_build_id=None,
            previous_cutover_id=None,
            cutover_id=D("4"),
            head_id=D("5"),
        )
        write_claim(directory, claim)
        write_transaction(directory, claim, records)
        complete = records[-1]
        material = _certificate_ready_material_v2(
            complete,
            authorities=authorities,
            prerequisite=_startup_prerequisite_for_test(D("1"), D("2")),
            observe_maintenance=lambda: complete.maintenance_proof,
            crossing_receipt=dominant_receipt(
                complete, catalog_id=proof_catalog_id(),
            ),
        )
        (ownership_root / "ownership-cutover-v1.sig").write_bytes(b"x" * 64)

        with pytest.raises(
            OwnershipCoordinatorError,
            match="certificate exists before ready",
        ):
            _cross_certificate_boundary_locked_for_test_v2(
                session, ownership_root, material, authorities=authorities,
            )
        graph = _resolve_ownership_coordinator_locked_for_test_v2(
            session, ownership_root,
        ).observation
        assert graph.transactions[-1].records == records


@LINUX_ONLY
def test_later_certificate_v2_is_appended_to_the_chain_not_the_anchor(
    tmp_path,
):
    ownership_root = tmp_path / "ownership"
    authorities = portable_authorities()
    chain_store = _OwnershipChainStoreForTest._initialize_with_authorities(
        tmp_path / "chain-v1", authorities.public,
    )
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        first_claim = bound_claim(
            release_sequence=1,
            previous_head_id=None,
            closed_build_id=D("3"),
            source_id=D("2"),
            previous_closed_build_id=None,
            previous_cutover_id=None,
        )
        first_records = transaction_records(
            first_claim,
            end_sequence=6,
            previous_closed_build_id=None,
            previous_cutover_id=None,
            cutover_id=D("4"),
            head_id=D("5"),
        )
        write_claim(directory, first_claim)
        write_transaction(directory, first_claim, first_records)
        claim = bound_claim(
            release_sequence=2,
            previous_head_id=first_records[-1].head_id,
            closed_build_id=D("a"),
            source_id=D("b"),
            previous_closed_build_id=first_records[-1].closed_build_id,
            previous_cutover_id=first_records[-1].cutover_id,
        )
        records = transaction_records(
            claim,
            end_sequence=1,
            previous_closed_build_id=first_records[-1].closed_build_id,
            previous_cutover_id=first_records[-1].cutover_id,
            cutover_id=D("6"),
            head_id=D("7"),
        )
        write_claim(directory, claim)
        write_transaction(directory, claim, records)
        complete = records[-1]
        material = _certificate_ready_material_v2(
            complete,
            authorities=authorities,
            prerequisite=_startup_prerequisite_for_test(D("1"), D("2")),
            observe_maintenance=lambda: complete.maintenance_proof,
            crossing_receipt=dominant_receipt(
                complete, catalog_id=proof_catalog_id(),
            ),
        )

        published = _cross_certificate_boundary_locked_for_test_v2(
            session,
            ownership_root,
            material,
            authorities=authorities,
            chain_store=chain_store,
        )

        stem = material.certificate.cutover_id.removeprefix("sha256:")
        assert published.state is (
            OwnershipCoordinatorStateV1.CERTIFICATE_PUBLISHED
        )
        assert not (ownership_root / "ownership-cutover-v1.json").exists()
        assert (chain_store.root / "cutovers-v1" / f"{stem}.json").read_bytes() == (
            material.payload
        )
        assert (chain_store.root / "cutovers-v1" / f"{stem}.sig").read_bytes() == (
            material.signature
        )


@pytest.mark.parametrize(("sequence", "field", "replacement"), (
    (0, "schema_version", 1),
    (0, "sequence", True),
    (0, "current_receipts", [{"unexpected": "receipt"}]),
    (0, "previous_record_sha256", D("1")),
    (0, "install_transaction_id", D("f")),
    (0, "provisioning_transaction_id", "f" * 31),
    (0, "target_set_id", D("f")),
    (1, "current_inventory_hash", D("f")),
    (1, "dominant_startup_receipt", D("f")),
    (2, "dominant_startup_receipt", None),
    (3, "installed_tree_hash", D("1")),
    (4, "installed_tree_hash", None),
    (4, "head_id", D("1")),
    (5, "head_signature_hash", None),
    (5, "verified_chain_head_id", D("c")),
    (5, "preflight_attestation_hash", D("1")),
    (6, "preflight_attestation_hash", None),
))
def test_v2_codec_rejects_schema_binding_and_threshold_mutants(
    sequence, field, replacement,
):
    value = json.loads(record_v2(sequence).encode())
    value[field] = replacement
    with pytest.raises(OwnershipCoordinatorError):
        _decode_record_v2(canonical(value))


def test_v2_codec_rejects_noncanonical_base64_and_extra_fields():
    value = json.loads(record_v2(1).encode())
    raw = base64.b64decode(value["maintenance_proof_b64"])
    value["maintenance_proof_b64"] = base64.b64encode(raw).decode("ascii") + "="
    with pytest.raises(OwnershipCoordinatorError):
        _decode_record_v2(canonical(value))


def bound_claim(
    *, release_sequence: int, previous_head_id: str | None,
    closed_build_id: str, source_id: str,
    previous_closed_build_id: str | None,
    previous_cutover_id: str | None,
) -> SuccessorClaimV1:
    value: dict[str, object] = {
        "schema_version": 1,
        "previous_head_id": previous_head_id,
        "release_sequence": release_sequence,
        "request_id": request_id(
            closed_build_id, previous_closed_build_id, previous_cutover_id,
        ),
        "source_id": source_id,
        "closed_build_id": closed_build_id,
    }
    value["claim_id"] = _successor_claim_id_v1(value)
    return SuccessorClaimV1(
        claim_id=value["claim_id"],
        previous_head_id=previous_head_id,
        release_sequence=release_sequence,
        request_id=value["request_id"],
        source_id=source_id,
        closed_build_id=closed_build_id,
    )


def claim_with_request(
    claim: SuccessorClaimV1, replacement_request_id: str,
) -> SuccessorClaimV1:
    value = claim.as_value(include_id=False)
    value["request_id"] = replacement_request_id
    return SuccessorClaimV1(
        claim_id=_successor_claim_id_v1(value),
        previous_head_id=claim.previous_head_id,
        release_sequence=claim.release_sequence,
        request_id=replacement_request_id,
        source_id=claim.source_id,
        closed_build_id=claim.closed_build_id,
    )


def deployment_descriptor(release_sequence: int):
    return build_deployment_descriptor_v1(
        release_sequence=release_sequence,
        service_user="metnos",
        service_uid=991,
        service_gid=991,
        service_supplementary_gids=(44, 991),
        service_home="/var/lib/metnos",
        service_shell="/usr/sbin/nologin",
        artifacts=(
            DeploymentArtifactV1(
                "deployment/admin/preflight.py",
                "/usr/libexec/metnos/executor-birth-v1/preflight.py",
                "administrative_program", "group6_admin", 11, D("a"),
                0o755, 0, 0,
            ),
            DeploymentArtifactV1(
                "deployment/systemd/metnos.target",
                "/etc/systemd/system/metnos.target",
                "target_unit", "group7_cutover", 12, D("b"),
                0o644, 0, 0,
            ),
        ),
        service_catalog_id=D("c"),
        service_coverage_hash=D("d"),
        python_executable="/usr/bin/python3.12",
        openssl_executable="/usr/bin/openssl",
        systemctl_executable="/usr/bin/systemctl",
        systemd_analyze_executable="/usr/bin/systemd-analyze",
    )


def verified_distribution(
    claim: SuccessorClaimV1, descriptor, *, previous_closed_build_id,
):
    identity = _sealed_build_identity_for_test(
        claim.closed_build_id, D("e"), "guard-v2",
    )
    encoded = f"distribution-{claim.release_sequence}".encode("ascii")
    distribution = _verified_distribution_for_test(
        identity,
        previous_closed_build_id=previous_closed_build_id,
        release_sequence=claim.release_sequence,
        encoded=encoded,
        signature=bytes([claim.release_sequence]) * 64,
    )
    return replace(
        distribution, installation_root=descriptor.installation_root,
    )


def prepared_set(
    *, set_id: str, admission_context_id: str, context_epoch: str,
    material_sha256: str = "7" * 64, set_json_sha256: str = "8" * 64,
) -> PreparedSetV1:
    values = dict(
        set_id=set_id,
        state=PREPARED_STATE_V1,
        author_active_key_id="author",
        author_verifier_key_ids=("author",),
        admission_active_key_id="admission",
        producer_keys={},
        prepared_admission_context_id=admission_context_id,
        prepared_context_epoch=context_epoch,
        context_material_sha256=material_sha256,
        set_json_sha256=set_json_sha256,
        provisioning_transaction_id="f" * 32,
        provisioner_build_id="prepared-v1",
    )
    return PreparedSetV1(
        **values,
        _artifact_binding=(
            prepared_set_module._prepared_set_artifact_binding_v1(values)
        ),
        _seal=prepared_set_module._PREPARED_SET_SEAL_V1,
    )


def prepared_target(
    claim: SuccessorClaimV1, distribution, *, previous_set_id: str,
):
    values = dict(
        transaction_id="0" * 32,
        provisioner_build_id="birth-provisioner-v2-test",
        request_id=claim.request_id,
        closed_build_id=claim.closed_build_id,
        distribution_payload_hash=digest(distribution.encoded),
        distribution_signature_hash=digest(distribution.signature),
        previous_set_id=previous_set_id,
        target_set_id="9" * 64,
        target_admission_context_id=D("a"),
        target_context_epoch=D("b"),
        target_context_material_sha256="c" * 64,
        target_set_json_sha256="d" * 64,
        source_inventory_hash=D("e"),
        material_plan_sha256="f" * 64,
        verified_checkpoint_sha256="1" * 64,
    )
    return PreparedAuthoritySetV2(
        **values,
        _artifact_binding=(
            prepared_set_module._prepared_authority_set_binding_v2(values)
        ),
        _seal=prepared_set_module._PREPARED_AUTHORITY_SET_SEAL_V2,
    )


def administrative_bundle_oracle(descriptor) -> str:
    material = bytearray(len(descriptor.artifacts).to_bytes(8, "big"))
    for artifact in descriptor.artifacts:
        for value in (
            artifact.destination_path.encode("utf-8"),
            artifact.kind.encode("ascii"),
            artifact.install_phase.encode("ascii"),
        ):
            material.extend(len(value).to_bytes(8, "big"))
            material.extend(value)
        material.extend(artifact.mode.to_bytes(4, "big"))
        material.extend(artifact.size.to_bytes(8, "big"))
        material.extend(bytes.fromhex(artifact.content_hash.removeprefix("sha256:")))
    return digest(
        b"metnos.executor-birth.administrative-bundle/v1\0" + material,
    )


def required_selection_for_predecessor(
    predecessor: OwnershipCoordinatorRecordV2, *, staged: bool = False,
):
    previous_prepared = prepared_set(
        set_id=predecessor.target_set_id,
        admission_context_id=predecessor.target_admission_context_id,
        context_epoch=predecessor.target_context_epoch,
        material_sha256=predecessor.target_context_material_sha256,
        set_json_sha256=predecessor.target_set_json_sha256,
    )
    previous_distribution = _verified_distribution_for_test(
        _sealed_build_identity_for_test(
            predecessor.closed_build_id, D("e"), "guard-v2",
        ),
        previous_closed_build_id=predecessor.previous_closed_build_id,
        release_sequence=predecessor.release_sequence,
        encoded=b"previous-distribution",
        signature=b"p" * 64,
    )
    _, previous_transition = issue_context_transition_v1(
        request_id=predecessor.request_id,
        closed_build_id=predecessor.closed_build_id,
        previous_cutover_id=predecessor.previous_cutover_id,
        previous_set_id=predecessor.previous_set_id,
        previous_admission_context_id=(
            predecessor.previous_admission_context_id
        ),
        previous_context_epoch=predecessor.previous_context_epoch,
        set_id=predecessor.target_set_id,
        prepared_admission_context_id=(
            predecessor.target_admission_context_id
        ),
        prepared_context_epoch=predecessor.target_context_epoch,
        context_material_sha256=(
            predecessor.target_context_material_sha256
        ),
        set_json_sha256=predecessor.target_set_json_sha256,
        current_inventory=proof().inventory,
    )
    producer = (
        _context_selection_for_staged_reattestation_v1
        if staged else _context_selection_from_required_chain_v1
    )
    return producer(
        previous_transition, previous_prepared, previous_distribution,
    )


def test_prepared_v2_record_binds_first_transition_before_publication():
    claim = bound_claim(
        release_sequence=1,
        previous_head_id=None,
        closed_build_id=D("3"),
        source_id=D("2"),
        previous_closed_build_id=None,
        previous_cutover_id=None,
    )
    descriptor = deployment_descriptor(1)
    distribution = verified_distribution(
        claim, descriptor, previous_closed_build_id=None,
    )
    previous = prepared_set(
        set_id="1" * 64,
        admission_context_id=D("2"),
        context_epoch=D("3"),
    )
    target = prepared_target(
        claim, distribution, previous_set_id=previous.set_id,
    )
    inventory = CurrentInventoryV1((("explicit:alpha/manifest.toml", D("4")),))

    record, transition = _prepared_record_v2(
        claim=claim,
        distribution=distribution,
        predecessor=None,
        previous_context=previous,
        prepared_authority_set=target,
        current_inventory=inventory,
        deployment_descriptor=descriptor,
    )

    assert record.state is OwnershipCoordinatorStateV1.PREPARED
    assert record.request_id == claim.request_id == transition.request_id
    assert record.provisioning_transaction_id == target.transaction_id
    assert record.previous_set_id == previous.set_id
    assert record.target_set_id == target.target_set_id == transition.set_id
    assert record.context_transition_id == transition.transition_id
    assert record.current_inventory_hash == current_inventory_hash_v1(inventory)
    assert record.administrative_bundle_hash == administrative_bundle_oracle(
        descriptor,
    )
    assert _decode_record_v2(record.encode()) == record


def test_prepared_publication_artifact_normalizes_unsealed_inputs():
    with pytest.raises(
        OwnershipCoordinatorError,
        match="birth_ownership_request_conflict",
    ):
        PreparedTransitionPublicationV2(
            None, None, None, None, None, None, object(),
        )


def test_prepared_v2_record_binds_a_completed_predecessor_selection():
    predecessor = record_v2(6)
    claim = bound_claim(
        release_sequence=predecessor.release_sequence + 1,
        previous_head_id=predecessor.head_id,
        closed_build_id=D("a"),
        source_id=D("b"),
        previous_closed_build_id=predecessor.closed_build_id,
        previous_cutover_id=predecessor.cutover_id,
    )
    descriptor = deployment_descriptor(claim.release_sequence)
    distribution = verified_distribution(
        claim, descriptor,
        previous_closed_build_id=predecessor.closed_build_id,
    )
    previous_selection = required_selection_for_predecessor(predecessor)
    target = prepared_target(
        claim, distribution, previous_set_id=previous_selection.set_id,
    )

    record, transition = _prepared_record_v2(
        claim=claim,
        distribution=distribution,
        predecessor=predecessor,
        previous_context=previous_selection,
        prepared_authority_set=target,
        current_inventory=proof().inventory,
        deployment_descriptor=descriptor,
    )

    assert record.previous_closed_build_id == predecessor.closed_build_id
    assert record.previous_cutover_id == predecessor.cutover_id
    assert record.previous_head_id == predecessor.head_id
    assert transition.previous_set_id == previous_selection.set_id
    staged = required_selection_for_predecessor(predecessor, staged=True)
    with pytest.raises(
        OwnershipCoordinatorError,
        match="birth_ownership_request_conflict",
    ):
        _prepared_record_v2(
            claim=claim,
            distribution=distribution,
            predecessor=predecessor,
            previous_context=staged,
            prepared_authority_set=target,
            current_inventory=proof().inventory,
            deployment_descriptor=descriptor,
        )


def test_prepared_v2_record_rejects_crossed_target_and_release_facts():
    claim = bound_claim(
        release_sequence=1,
        previous_head_id=None,
        closed_build_id=D("3"),
        source_id=D("2"),
        previous_closed_build_id=None,
        previous_cutover_id=None,
    )
    descriptor = deployment_descriptor(1)
    distribution = verified_distribution(
        claim, descriptor, previous_closed_build_id=None,
    )
    previous = prepared_set(
        set_id="1" * 64,
        admission_context_id=D("2"),
        context_epoch=D("3"),
    )
    base = dict(
        claim=claim,
        distribution=distribution,
        predecessor=None,
        previous_context=previous,
        prepared_authority_set=prepared_target(
            claim, distribution, previous_set_id=previous.set_id,
        ),
        current_inventory=CurrentInventoryV1(()),
        deployment_descriptor=descriptor,
    )
    changes = (
        {"prepared_authority_set": prepared_target(
            claim, distribution, previous_set_id="2" * 64,
        )},
        {"deployment_descriptor": deployment_descriptor(2)},
        {"current_inventory": CurrentReceiptProof((), {})},
        {"predecessor": record_v2(6)},
    )
    for change in changes:
        with pytest.raises(
            OwnershipCoordinatorError,
            match="birth_ownership_request_conflict",
        ):
            _prepared_record_v2(**{**base, **change})


@LINUX_ONLY
def test_prepared_v2_record_persists_once_and_inventory_drift_conflicts(
    tmp_path,
):
    claim = bound_claim(
        release_sequence=1,
        previous_head_id=None,
        closed_build_id=D("3"),
        source_id=D("2"),
        previous_closed_build_id=None,
        previous_cutover_id=None,
    )
    descriptor = deployment_descriptor(1)
    distribution = verified_distribution(
        claim, descriptor, previous_closed_build_id=None,
    )
    previous = prepared_set(
        set_id="1" * 64,
        admission_context_id=D("2"),
        context_epoch=D("3"),
    )
    target = prepared_target(
        claim, distribution, previous_set_id=previous.set_id,
    )

    def build(inventory):
        return _prepared_record_v2(
            claim=claim,
            distribution=distribution,
            predecessor=None,
            previous_context=previous,
            prepared_authority_set=target,
            current_inventory=inventory,
            deployment_descriptor=descriptor,
        )[0]

    first = build(CurrentInventoryV1(()))
    drifted = build(CurrentInventoryV1((
        ("explicit:alpha/manifest.toml", D("4")),
    )))
    assert drifted.context_transition_id != first.context_transition_id
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        write_claim(directory, claim)
        assert _append_ownership_transaction_locked_for_test_v2(
            session, ownership_root, first,
        ) == first
        before = tree_snapshot(directory)
        with pytest.raises(
            OwnershipCoordinatorError,
            match="birth_ownership_journal_conflict",
        ):
            _append_ownership_transaction_locked_for_test_v2(
                session, ownership_root, drifted,
            )
        assert tree_snapshot(directory) == before


def transaction_records(
    claim: SuccessorClaimV1, *, end_sequence: int,
    previous_closed_build_id: str | None,
    previous_cutover_id: str | None,
    cutover_id: str, head_id: str,
    distribution=None, context_transition_id: str = D("9"),
) -> tuple[OwnershipCoordinatorRecordV2, ...]:
    install_value = {
        "schema_version": 1,
        "request_id": claim.request_id,
        "source_id": claim.source_id,
        "closed_build_id": claim.closed_build_id,
        "release_sequence": claim.release_sequence,
        "previous_head_id": claim.previous_head_id,
        "successor_claim_id": claim.claim_id,
        "deployment_descriptor_id": D("6"),
        "service_coverage_hash": D("7"),
        "administrative_bundle_hash": D("8"),
    }
    install_transaction_id = _install_transaction_id_v1(install_value)
    evidence = maintenance()
    evidence_hash = maintenance_evidence_hash(evidence)
    current = proof()
    inventory_hash = current_inventory_hash_v1(current.inventory)
    records: list[OwnershipCoordinatorRecordV2] = []
    previous_hash = None
    for sequence in range(end_sequence + 1):
        record = OwnershipCoordinatorRecordV2(
            sequence=sequence,
            state=tuple(OwnershipCoordinatorStateV1)[sequence],
            previous_record_sha256=previous_hash,
            request_id=claim.request_id,
            previous_closed_build_id=previous_closed_build_id,
            previous_cutover_id=previous_cutover_id,
            closed_build_id=claim.closed_build_id,
            distribution_payload_hash=(
                digest(distribution.encoded)
                if distribution is not None else D("c")
            ),
            distribution_signature_hash=(
                digest(distribution.signature)
                if distribution is not None else D("d")
            ),
            boundary_inventory_hash=(
                distribution.identity.boundary_inventory_hash
                if distribution is not None else D("e")
            ),
            boundary_guard_version=(
                distribution.identity.boundary_guard_version
                if distribution is not None else "guard-v2"
            ),
            source_id=claim.source_id,
            successor_claim_id=claim.claim_id,
            deployment_descriptor_id=install_value["deployment_descriptor_id"],
            install_transaction_id=install_transaction_id,
            release_sequence=claim.release_sequence,
            previous_head_id=claim.previous_head_id,
            service_coverage_hash=install_value["service_coverage_hash"],
            administrative_bundle_hash=(
                install_value["administrative_bundle_hash"]
            ),
            provisioning_transaction_id="0" * 32,
            previous_set_id="1" * 64,
            previous_admission_context_id=D("2"),
            previous_context_epoch=D("3"),
            target_set_id="4" * 64,
            target_admission_context_id=D("5"),
            target_context_epoch=D("6"),
            target_context_material_sha256="7" * 64,
            target_set_json_sha256="8" * 64,
            context_transition_id=context_transition_id,
            current_inventory_hash=inventory_hash,
            current_proof=current if sequence >= 1 else None,
            maintenance_before_hash=evidence_hash if sequence >= 1 else None,
            maintenance_after_hash=evidence_hash if sequence >= 1 else None,
            maintenance_proof=evidence if sequence >= 1 else None,
            startup_prerequisite_id=D("1") if sequence >= 2 else None,
            startup_prerequisite_digest=D("2") if sequence >= 2 else None,
            cutover_id=cutover_id if sequence >= 2 else None,
            catalog_id=D("4") if sequence >= 2 else None,
            certificate_payload_hash=D("5") if sequence >= 2 else None,
            certificate_signature_hash=D("6") if sequence >= 2 else None,
            dominant_startup_receipt=D("e") if sequence >= 2 else None,
            installed_tree_hash=D("7") if sequence >= 4 else None,
            head_id=head_id if sequence >= 5 else None,
            head_payload_hash=D("9") if sequence >= 5 else None,
            head_signature_hash=D("a") if sequence >= 5 else None,
            required_head_frame_hash=D("b") if sequence >= 5 else None,
            verified_chain_head_id=head_id if sequence >= 5 else None,
            preflight_attestation_hash=D("f") if sequence >= 6 else None,
        )
        records.append(record)
        previous_hash = _record_hash_v2(record.encode())
    return tuple(records)


@pytest.mark.parametrize(
    ("release_sequence", "previous_head_id", "end_sequence"),
    ((1, None, 1), (2, D("4"), 6)),
)
def test_dominant_identity_rereads_the_exact_transition_and_initial_anchor(
    release_sequence, previous_head_id, end_sequence,
):
    previous_closed_build_id = None if release_sequence == 1 else D("a")
    previous_cutover_id = None if release_sequence == 1 else D("b")
    claim = bound_claim(
        release_sequence=release_sequence,
        previous_head_id=previous_head_id,
        closed_build_id=D("3"),
        source_id=D("2"),
        previous_closed_build_id=previous_closed_build_id,
        previous_cutover_id=previous_cutover_id,
    )
    _, transition = issue_context_transition_v1(
        request_id=claim.request_id,
        closed_build_id=claim.closed_build_id,
        previous_cutover_id=previous_cutover_id,
        previous_set_id="1" * 64,
        previous_admission_context_id=D("2"),
        previous_context_epoch=D("3"),
        set_id="4" * 64,
        prepared_admission_context_id=D("5"),
        prepared_context_epoch=D("6"),
        context_material_sha256="7" * 64,
        set_json_sha256="8" * 64,
        current_inventory=proof().inventory,
    )
    records = transaction_records(
        claim,
        end_sequence=end_sequence,
        previous_closed_build_id=previous_closed_build_id,
        previous_cutover_id=previous_cutover_id,
        cutover_id=D("3"),
        head_id=D("4"),
        context_transition_id=transition.transition_id,
    )
    transaction = coordinator_module._ResolvedOwnershipTransactionV2(
        claim, records, tuple(record.encode() for record in records),
    )
    graph = _ObservedOwnershipCoordinatorGraphV2(
        (claim,), (), (transaction,), (), (), None,
    )
    reads = []

    def read_transition(transition_id, expected_proof):
        reads.append((transition_id, expected_proof))
        return transition

    observed = coordinator_module._observe_dominant_identity_core_v2(
        graph, records[1], read_transition,
    )

    expected_anchor = previous_head_id or "sha256:" + records[1].previous_set_id
    assert observed == (
        claim.request_id, expected_anchor, transition.transition_id,
    )
    assert reads == [(transition.transition_id, records[1].current_proof)]


def test_dominant_identity_rejects_a_different_context_transition():
    claim = bound_claim(
        release_sequence=1,
        previous_head_id=None,
        closed_build_id=D("3"),
        source_id=D("2"),
        previous_closed_build_id=None,
        previous_cutover_id=None,
    )
    _, expected = issue_context_transition_v1(
        request_id=claim.request_id,
        closed_build_id=claim.closed_build_id,
        previous_cutover_id=None,
        previous_set_id="1" * 64,
        previous_admission_context_id=D("2"),
        previous_context_epoch=D("3"),
        set_id="4" * 64,
        prepared_admission_context_id=D("5"),
        prepared_context_epoch=D("6"),
        context_material_sha256="7" * 64,
        set_json_sha256="8" * 64,
        current_inventory=proof().inventory,
    )
    records = transaction_records(
        claim,
        end_sequence=1,
        previous_closed_build_id=None,
        previous_cutover_id=None,
        cutover_id=D("3"),
        head_id=D("4"),
        context_transition_id=expected.transition_id,
    )
    transaction = coordinator_module._ResolvedOwnershipTransactionV2(
        claim, records, tuple(record.encode() for record in records),
    )
    graph = _ObservedOwnershipCoordinatorGraphV2(
        (claim,), (), (transaction,), (), (), None,
    )
    _, other = issue_context_transition_v1(
        request_id=D("f"),
        closed_build_id=claim.closed_build_id,
        previous_cutover_id=None,
        previous_set_id="1" * 64,
        previous_admission_context_id=D("2"),
        previous_context_epoch=D("3"),
        set_id="4" * 64,
        prepared_admission_context_id=D("5"),
        prepared_context_epoch=D("6"),
        context_material_sha256="7" * 64,
        set_json_sha256="8" * 64,
        current_inventory=proof().inventory,
    )

    with pytest.raises(
        OwnershipCoordinatorError,
        match="birth_context_transition_recovery_required",
    ):
        coordinator_module._observe_dominant_identity_core_v2(
            graph, records[1], lambda _transition_id, _proof: other,
        )


@pytest.mark.parametrize("reason", ("record missing", "record binding"))
def test_dominant_identity_preserves_the_transition_read_reason(reason):
    from executor_birth_ownership_chain import OwnershipChainError

    complete = record_v2(1)
    claim_value = successor_claim_value(
        release_sequence=complete.release_sequence,
        previous_head_id=complete.previous_head_id,
    )
    claim = claim_with_request(SuccessorClaimV1(
        claim_id=claim_value["claim_id"],
        previous_head_id=complete.previous_head_id,
        release_sequence=complete.release_sequence,
        request_id=claim_value["request_id"],
        source_id=claim_value["source_id"],
        closed_build_id=claim_value["closed_build_id"],
    ), complete.request_id)
    transaction = coordinator_module._ResolvedOwnershipTransactionV2(
        claim, (record_v2(0), complete), (b"prepared", complete.encode()),
    )
    graph = _ObservedOwnershipCoordinatorGraphV2(
        (claim,), (), (transaction,), (), (), None,
    )

    def read_transition(_transition_id, _proof):
        raise OwnershipChainError(
            "birth_context_transition_recovery_required", reason,
        )

    with pytest.raises(OwnershipCoordinatorError) as failed:
        coordinator_module._observe_dominant_identity_core_v2(
            graph, complete, read_transition,
        )
    assert failed.value.detail == (
        "birth_context_transition_recovery_required:" + reason.replace(" ", "_")
    )


def test_translated_reason_keeps_only_allowlisted_typed_components():
    from executor_birth_ownership_chain import OwnershipChainError

    stable = OwnershipChainError(
        "birth_context_transition_recovery_required", "record missing",
    )
    free_text = OwnershipChainError(
        "birth_context_transition_recovery_required", "customer name",
    )

    assert coordinator_module._wrapped_cause_detail_v1(stable) == (
        "birth_context_transition_recovery_required:record_missing"
    )
    assert coordinator_module._wrapped_cause_detail_v1(free_text) == (
        "birth_context_transition_recovery_required"
    )
    assert coordinator_module._wrapped_cause_detail_v1(RuntimeError("opaque")) == ""


@pytest.mark.parametrize("code", sorted(
    coordinator_module._WRAPPED_CONTRACT_DETAIL_CODES_V1,
))
def test_translated_reason_preserves_a_canonical_contract_identity(code):
    from executor_birth_cutover import BirthCutoverError

    assert coordinator_module._wrapped_cause_detail_v1(
        BirthCutoverError(code, "explicit:alpha/manifest.toml"),
    ) == f"{code}: explicit:alpha/manifest.toml"


@pytest.mark.parametrize("detail", [
    "free text",
    "explicit:/alpha/manifest.toml",
    "explicit:../alpha/manifest.toml",
    "explicit:alpha/manifest.toml\nsecond line",
    "explicit:" + "a" * 4096 + "/manifest.toml",
])
def test_translated_reason_rejects_noncanonical_contract_context(detail):
    from executor_birth_cutover import BirthCutoverError

    code = "birth_cutover_reattestation_failed"
    assert coordinator_module._wrapped_cause_detail_v1(
        BirthCutoverError(code, detail),
    ) == code


def test_translated_reason_does_not_attach_contract_context_to_another_code():
    from executor_birth_cutover import BirthCutoverError

    code = "birth_cutover_inventory_changed"
    assert coordinator_module._wrapped_cause_detail_v1(
        BirthCutoverError(code, "explicit:alpha/manifest.toml"),
    ) == code


def test_completed_transition_selection_returns_only_the_exact_final_release():
    distribution = payload_bound_distribution_v2()
    claim = bound_claim(
        release_sequence=1,
        previous_head_id=None,
        closed_build_id=distribution.identity.closed_build_id,
        source_id=D("2"),
        previous_closed_build_id=None,
        previous_cutover_id=None,
    )
    records = transaction_records(
        claim,
        end_sequence=6,
        previous_closed_build_id=None,
        previous_cutover_id=None,
        cutover_id=D("3"),
        head_id=D("4"),
        distribution=distribution,
    )
    transaction = coordinator_module._ResolvedOwnershipTransactionV2(
        claim, records, tuple(record.encode() for record in records),
    )
    graph = _ObservedOwnershipCoordinatorGraphV2(
        (claim,), (), (transaction,), (), (), None,
    )

    assert coordinator_module._completed_transition_from_graph_v2(
        graph, distribution,
    ) == records[-1]
    incomplete = coordinator_module._ResolvedOwnershipTransactionV2(
        claim, records[:-1], tuple(record.encode() for record in records[:-1]),
    )
    assert coordinator_module._completed_transition_from_graph_v2(
        _ObservedOwnershipCoordinatorGraphV2(
            (claim,), (), (incomplete,), (), (), None,
        ),
        distribution,
    ) is None


def legacy_records(
    *, end_sequence: int, closed_build_id: str,
) -> tuple[OwnershipCoordinatorRecordV1, ...]:
    evidence = maintenance()
    evidence_hash = maintenance_evidence_hash(evidence)
    records: list[OwnershipCoordinatorRecordV1] = []
    previous_hash = None
    for sequence in range(end_sequence + 1):
        record = OwnershipCoordinatorRecordV1(
            sequence=sequence,
            state=tuple(OwnershipCoordinatorStateV1)[sequence],
            previous_record_sha256=previous_hash,
            request_id=request_id(closed_build_id, None, None),
            previous_closed_build_id=None,
            previous_cutover_id=None,
            closed_build_id=closed_build_id,
            distribution_payload_hash=D("c"),
            distribution_signature_hash=D("d"),
            boundary_inventory_hash=D("e"),
            boundary_guard_version="guard-v1",
            current_proof=proof() if sequence >= 1 else None,
            maintenance_before_hash=evidence_hash if sequence >= 1 else None,
            maintenance_after_hash=evidence_hash if sequence >= 1 else None,
            maintenance_proof=evidence if sequence >= 1 else None,
        )
        records.append(record)
        previous_hash = _record_hash(record.encode())
    return tuple(records)


def write_control(path: Path, encoded: bytes) -> None:
    path.write_bytes(encoded)
    path.chmod(0o644)


def make_coordinator_root(ownership_root: Path) -> Path:
    directory = ownership_root / "coordinator-v1"
    directory.mkdir(mode=0o755)
    return directory


def write_claim(directory: Path, claim: SuccessorClaimV1) -> Path:
    claims = directory / "successor-claims-v1"
    claims.mkdir(mode=0o755, exist_ok=True)
    path = claims / _successor_claim_basename_v1(
        claim.release_sequence, claim.previous_head_id,
    )
    write_control(path, claim.encode())
    return path


def write_transaction(
    directory: Path, claim: SuccessorClaimV1,
    records: tuple[OwnershipCoordinatorRecordV2, ...],
) -> Path:
    transactions = directory / "transactions-v2"
    transactions.mkdir(mode=0o755, exist_ok=True)
    transaction = transactions / claim.request_id
    transaction.mkdir(mode=0o755)
    for record in records:
        write_control(
            transaction / _record_basename_v2(record.sequence),
            record.encode(),
        )
    return transaction


def write_legacy(
    directory: Path, records: tuple[OwnershipCoordinatorRecordV1, ...],
) -> tuple[bytes, ...]:
    encoded_records = tuple(record.encode() for record in records)
    for record, encoded in zip(records, encoded_records, strict=True):
        write_control(
            directory / f"record-{record.sequence:03d}-v1.json", encoded,
        )
    return encoded_records


def write_disposition(
    directory: Path, records: tuple[OwnershipCoordinatorRecordV1, ...],
    encoded_records: tuple[bytes, ...], claim: SuccessorClaimV1,
) -> LegacyDispositionV2:
    value: dict[str, object] = {
        "schema_version": 2,
        "legacy_journal_hash": _legacy_journal_hash_v2(encoded_records),
        "legacy_request_id": records[-1].request_id,
        "legacy_state": records[-1].state.value,
        "successor_request_id": claim.request_id,
        "reason": "superseded_before_certificate",
    }
    value["disposition_id"] = _legacy_disposition_id_v2(value)
    disposition = LegacyDispositionV2(
        disposition_id=value["disposition_id"],
        legacy_journal_hash=value["legacy_journal_hash"],
        legacy_request_id=value["legacy_request_id"],
        legacy_state=records[-1].state,
        successor_request_id=claim.request_id,
    )
    write_control(directory / "legacy-disposition-v2.json", disposition.encode())
    return disposition


def tree_snapshot(directory: Path) -> tuple[tuple[str, int, bytes], ...]:
    return tuple(
        (
            str(path.relative_to(directory)),
            stat.S_IMODE(path.lstat().st_mode),
            b"" if path.is_dir() else path.read_bytes(),
        )
        for path in sorted((directory, *directory.rglob("*")))
    )


@LINUX_ONLY
def test_transaction_writer_persists_rereads_and_resolves_all_states(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        records = transaction_records(
            claim, end_sequence=6, previous_closed_build_id=None,
            previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
        )
        write_claim(directory, claim)

        for record in records:
            assert _append_ownership_transaction_locked_for_test_v2(
                session, ownership_root, record,
            ) == record
        before_retry = tree_snapshot(directory)
        assert _append_ownership_transaction_locked_for_test_v2(
            session, ownership_root, records[-1],
        ) == records[-1]
        assert tree_snapshot(directory) == before_retry

        graph = _resolve_ownership_coordinator_locked_for_test_v2(
            session, ownership_root,
        ).observation
        assert graph.transactions[0].records == records
        assert graph.transactions[0].latest.state is (
            OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED
        )


@LINUX_ONLY
def test_locked_prepared_checkpoint_selects_the_durable_pending_claim(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        write_claim(directory, claim)
        descriptor = deployment_descriptor(1)
        distribution = verified_distribution(
            claim, descriptor, previous_closed_build_id=None,
        )
        previous = prepared_set(
            set_id="1" * 64,
            admission_context_id=D("2"),
            context_epoch=D("3"),
        )
        target = prepared_target(
            claim, distribution, previous_set_id=previous.set_id,
        )
        values = dict(
            distribution=distribution,
            previous_context=previous,
            prepared_authority_set=target,
            current_inventory=CurrentInventoryV1(()),
            deployment_descriptor=descriptor,
        )

        first, transition = _append_prepared_transition_locked_for_test_v2(
            session, ownership_root, **values,
        )
        assert first.successor_claim_id == claim.claim_id
        assert first.context_transition_id == transition.transition_id
        before_retry = tree_snapshot(directory)
        assert _append_prepared_transition_locked_for_test_v2(
            session, ownership_root, **values,
        ) == (first, transition)
        assert tree_snapshot(directory) == before_retry

        crossed_claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("4"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        crossed_distribution = verified_distribution(
            crossed_claim, descriptor, previous_closed_build_id=None,
        )
        with pytest.raises(
            OwnershipCoordinatorError,
            match="birth_ownership_request_conflict",
        ):
            _append_prepared_transition_locked_for_test_v2(
                session, ownership_root,
                **{**values, "distribution": crossed_distribution},
            )
        assert tree_snapshot(directory) == before_retry


@LINUX_ONLY
def test_locked_prepared_checkpoint_uses_only_the_completed_predecessor(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        first_claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        first_records = transaction_records(
            first_claim, end_sequence=6, previous_closed_build_id=None,
            previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
        )
        write_claim(directory, first_claim)
        write_transaction(directory, first_claim, first_records)
        predecessor = first_records[-1]
        claim = bound_claim(
            release_sequence=2, previous_head_id=predecessor.head_id,
            closed_build_id=D("a"), source_id=D("b"),
            previous_closed_build_id=predecessor.closed_build_id,
            previous_cutover_id=predecessor.cutover_id,
        )
        write_claim(directory, claim)
        descriptor = deployment_descriptor(2)
        distribution = verified_distribution(
            claim, descriptor,
            previous_closed_build_id=predecessor.closed_build_id,
        )
        previous = required_selection_for_predecessor(predecessor)
        target = prepared_target(
            claim, distribution, previous_set_id=previous.set_id,
        )

        record, transition = _append_prepared_transition_locked_for_test_v2(
            session, ownership_root,
            distribution=distribution,
            previous_context=previous,
            prepared_authority_set=target,
            current_inventory=proof().inventory,
            deployment_descriptor=descriptor,
        )

        assert record.release_sequence == 2
        assert record.previous_head_id == predecessor.head_id
        assert record.previous_cutover_id == predecessor.cutover_id
        assert transition.previous_set_id == previous.set_id
        graph = _resolve_ownership_coordinator_locked_for_test_v2(
            session, ownership_root,
        ).observation
        assert graph.pending_claims == ()
        assert graph.transactions[-1].records == (record,)


@LINUX_ONLY
@pytest.mark.parametrize(
    "interruption_stage",
    (
        "transaction_directory_staged",
        "transaction_record_staged",
        "transaction_record_published",
    ),
)
def test_transaction_writer_recovers_staged_record_and_rejects_conflict(
    tmp_path, interruption_stage,
):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        record = transaction_records(
            claim, end_sequence=0, previous_closed_build_id=None,
            previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
        )[0]
        write_claim(directory, claim)

        def interrupt(stage):
            if stage == interruption_stage:
                raise InterruptedError(stage)

        with pytest.raises(InterruptedError):
            _append_ownership_transaction_locked_for_test_v2(
                session, ownership_root, record, _crash_seam=interrupt,
            )
        transactions = directory / "transactions-v2"
        transaction = transactions / claim.request_id
        staged = transactions / f".{claim.request_id[7:]}.v2.tmp"
        if interruption_stage == "transaction_record_published":
            assert not staged.exists()
            assert tuple(path.name for path in transaction.iterdir()) == (
                "record-000-v2.json",
            )
        else:
            assert not transaction.exists()
            expected = (
                () if interruption_stage == "transaction_directory_staged"
                else ("record-000-v2.json",)
            )
            assert tuple(path.name for path in staged.iterdir()) == expected
            graph = _resolve_ownership_coordinator_locked_for_test_v2(
                session, ownership_root,
            ).observation
            assert graph.transactions == ()
            assert graph.pending_claims == (claim,)

        assert _append_ownership_transaction_locked_for_test_v2(
            session, ownership_root, record,
        ) == record
        before_conflict = tree_snapshot(directory)
        with pytest.raises(OwnershipCoordinatorError) as failure:
            _append_ownership_transaction_locked_for_test_v2(
                session, ownership_root,
                replace(record, target_set_id="f" * 64),
            )
        assert failure.value.code == "birth_ownership_journal_conflict"
        assert tree_snapshot(directory) == before_conflict


@LINUX_ONLY
def test_resolver_keeps_rejecting_an_empty_committed_transaction(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        record = transaction_records(
            claim, end_sequence=0, previous_closed_build_id=None,
            previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
        )[0]
        write_claim(directory, claim)
        transactions = directory / "transactions-v2"
        transactions.mkdir(mode=0o755)
        transactions.chmod(0o755)
        transaction = transactions / claim.request_id
        transaction.mkdir(mode=0o755)
        transaction.chmod(0o755)
        before = tree_snapshot(directory)

        with pytest.raises(OwnershipCoordinatorError) as failure:
            _resolve_ownership_coordinator_locked_for_test_v2(
                session, ownership_root,
            )
        assert failure.value.detail == "transaction cardinality"
        assert tree_snapshot(directory) == before
        with pytest.raises(OwnershipCoordinatorError) as writer_failure:
            _append_ownership_transaction_locked_for_test_v2(
                session, ownership_root, record,
            )
        assert writer_failure.value.detail == "transaction cardinality"
        assert tree_snapshot(directory) == before


@LINUX_ONLY
def test_initial_transaction_recovers_a_partial_unpublished_record(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        record = transaction_records(
            claim, end_sequence=0, previous_closed_build_id=None,
            previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
        )[0]
        write_claim(directory, claim)
        transactions = directory / "transactions-v2"
        transactions.mkdir(mode=0o755)
        transactions.chmod(0o755)
        staged = transactions / f".{claim.request_id[7:]}.v2.tmp"
        staged.mkdir(mode=0o755)
        staged.chmod(0o700)
        partial = staged / "record-000-v2.json"
        encoded = record.encode()
        partial.write_bytes(encoded[: len(encoded) // 2])
        partial.chmod(0o600)

        graph = _resolve_ownership_coordinator_locked_for_test_v2(
            session, ownership_root,
        ).observation
        assert graph.transactions == ()
        assert graph.pending_claims == (claim,)
        assert _append_ownership_transaction_locked_for_test_v2(
            session, ownership_root, record,
        ) == record
        assert not staged.exists()
        assert (
            transactions / claim.request_id / "record-000-v2.json"
        ).read_bytes() == encoded


@LINUX_ONLY
def test_transaction_writer_rejects_gap_before_creating_transaction(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        record = transaction_records(
            claim, end_sequence=1, previous_closed_build_id=None,
            previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
        )[1]
        write_claim(directory, claim)
        before = tree_snapshot(directory)

        with pytest.raises(OwnershipCoordinatorError) as failure:
            _append_ownership_transaction_locked_for_test_v2(
                session, ownership_root, record,
            )
        assert failure.value.detail == "transaction gap"
        assert tree_snapshot(directory) == before


@LINUX_ONLY
def test_transaction_writer_requires_exact_durable_claim_before_writing(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        record = transaction_records(
            claim, end_sequence=0, previous_closed_build_id=None,
            previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
        )[0]
        claims = directory / "successor-claims-v1"
        claims.mkdir(mode=0o755)
        before = tree_snapshot(directory)

        with pytest.raises(OwnershipCoordinatorError) as failure:
            _append_ownership_transaction_locked_for_test_v2(
                session, ownership_root, record,
            )
        assert failure.value.detail == "claim transaction binding"
        assert tree_snapshot(directory) == before


@LINUX_ONLY
def test_transition_reservation_publishes_and_rereads_one_idempotent_claim(
    tmp_path,
):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        expected = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        distribution = verified_distribution(
            expected, deployment_descriptor(1),
            previous_closed_build_id=None,
        )
        first = _reserve_transition_edge_locked_for_test_v2(
            session, ownership_root, distribution=distribution,
            source_id=expected.source_id,
        )
        snapshot = tree_snapshot(directory)
        second = _reserve_transition_edge_locked_for_test_v2(
            session, ownership_root, distribution=distribution,
            source_id=expected.source_id,
        )

        assert first == second == expected
        assert tree_snapshot(directory) == snapshot
        assert (
            directory / "successor-claims-v1/initial.json"
        ).read_bytes() == expected.encode()


@LINUX_ONLY
def test_initial_transition_reservation_creates_its_coordinator_namespace(
    tmp_path,
):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        expected = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        distribution = verified_distribution(
            expected, deployment_descriptor(1),
            previous_closed_build_id=None,
        )

        assert _reserve_transition_edge_locked_for_test_v2(
            session, ownership_root, distribution=distribution,
            source_id=expected.source_id,
        ) == expected
        coordinator = ownership_root / "coordinator-v1"
        assert stat.S_IMODE(coordinator.stat().st_mode) == 0o755
        assert (
            coordinator / "successor-claims-v1/initial.json"
        ).read_bytes() == expected.encode()


@LINUX_ONLY
def test_transition_reservation_refuses_a_competing_source_without_writes(
    tmp_path,
):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        expected = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        distribution = verified_distribution(
            expected, deployment_descriptor(1),
            previous_closed_build_id=None,
        )
        _reserve_transition_edge_locked_for_test_v2(
            session, ownership_root, distribution=distribution,
            source_id=expected.source_id,
        )
        before = tree_snapshot(directory)

        with pytest.raises(OwnershipCoordinatorError) as failure:
            _reserve_transition_edge_locked_for_test_v2(
                session, ownership_root, distribution=distribution,
                source_id=D("9"),
            )
        assert failure.value.code == "birth_ownership_successor_conflict"
        assert tree_snapshot(directory) == before


@LINUX_ONLY
def test_transition_reservation_disposes_the_exact_legacy_prefix(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        legacy = legacy_records(end_sequence=1, closed_build_id=D("8"))
        encoded_legacy = write_legacy(directory, legacy)
        expected = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        distribution = verified_distribution(
            expected, deployment_descriptor(1),
            previous_closed_build_id=None,
        )

        assert _reserve_transition_edge_locked_for_test_v2(
            session, ownership_root, distribution=distribution,
            source_id=expected.source_id,
        ) == expected
        graph = _resolve_ownership_coordinator_locked_for_test_v2(
            session, ownership_root,
        ).observation
        assert graph.legacy_disposition is not None
        assert graph.legacy_disposition.legacy_journal_hash == (
            _legacy_journal_hash_v2(encoded_legacy)
        )
        assert graph.legacy_disposition.successor_request_id == (
            expected.request_id
        )


@LINUX_ONLY
def test_transition_reservation_extends_only_a_completed_head(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        first = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        write_claim(directory, first)
        write_transaction(
            directory, first,
            transaction_records(
                first, end_sequence=6, previous_closed_build_id=None,
                previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
            ),
        )
        expected = bound_claim(
            release_sequence=2, previous_head_id=D("5"),
            closed_build_id=D("6"), source_id=D("7"),
            previous_closed_build_id=first.closed_build_id,
            previous_cutover_id=D("4"),
        )
        distribution = verified_distribution(
            expected, deployment_descriptor(2),
            previous_closed_build_id=first.closed_build_id,
        )

        assert _reserve_transition_edge_locked_for_test_v2(
            session, ownership_root, distribution=distribution,
            source_id=expected.source_id,
        ) == expected
        graph = _resolve_ownership_coordinator_locked_for_test_v2(
            session, ownership_root,
        ).observation
        assert graph.pending_claims == (expected,)


@LINUX_ONLY
def test_read_only_resolver_accepts_empty_and_single_pending_claim(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        empty_before = tree_snapshot(directory)
        empty = _resolve_ownership_coordinator_locked_for_test_v2(
            session, ownership_root,
        )
        assert type(empty) is _OwnershipCoordinatorGraphSnapshotForTestV2
        assert type(empty) is not _LockedOwnershipCoordinatorGraphSnapshotV2
        empty_graph = empty.observation
        assert (
            empty_graph.claims
            == empty_graph.transactions
            == empty_graph.pending_claims
            == ()
        )
        assert tree_snapshot(directory) == empty_before

        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        write_claim(directory, claim)
        pending_before = tree_snapshot(directory)
        pending = _resolve_ownership_coordinator_locked_for_test_v2(
            session, ownership_root,
        )
        pending_graph = pending.observation
        assert pending_graph.claims == pending_graph.pending_claims == (claim,)
        assert pending_graph.transactions == ()
        assert tree_snapshot(directory) == pending_before


@LINUX_ONLY
def test_read_only_resolver_accepts_two_release_closed_graph_without_writes(
    tmp_path, monkeypatch,
):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        first = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("1"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        first_records = transaction_records(
            first, end_sequence=6, previous_closed_build_id=None,
            previous_cutover_id=None, cutover_id=D("3"), head_id=D("4"),
        )
        second = bound_claim(
            release_sequence=2, previous_head_id=D("4"),
            closed_build_id=D("5"), source_id=D("6"),
            previous_closed_build_id=D("1"), previous_cutover_id=D("3"),
        )
        second_records = transaction_records(
            second, end_sequence=6, previous_closed_build_id=D("1"),
            previous_cutover_id=D("3"), cutover_id=D("7"), head_id=D("8"),
        )
        write_claim(directory, first)
        write_claim(directory, second)
        write_transaction(directory, first, first_records)
        write_transaction(directory, second, second_records)
        before = tree_snapshot(directory)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("resolver attempted a filesystem mutation")

        for name in ("mkdir", "unlink", "rename", "replace"):
            monkeypatch.setattr(coordinator_module.os, name, forbidden)
        result = _resolve_ownership_coordinator_locked_for_test_v2(
            session, ownership_root,
        )

        graph = result.observation
        assert tuple(item.claim for item in graph.transactions) == (first, second)
        assert graph.pending_claims == ()
        assert graph.transactions[-1].latest.sequence == 6
        assert tree_snapshot(directory) == before


@pytest.mark.parametrize("prefix", ("v1", "claim", "disposition", "v2"))
@LINUX_ONLY
def test_read_only_resolver_accepts_only_valid_legacy_migration_prefixes(
    tmp_path, monkeypatch, prefix,
):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        records = legacy_records(end_sequence=1, closed_build_id=D("3"))
        encoded = write_legacy(directory, records)
        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("4"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        disposition = None
        if prefix != "v1":
            write_claim(directory, claim)
        if prefix in {"disposition", "v2"}:
            disposition = write_disposition(directory, records, encoded, claim)
        if prefix == "v2":
            write_transaction(
                directory, claim,
                transaction_records(
                    claim, end_sequence=0, previous_closed_build_id=None,
                    previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
                ),
            )

        monkeypatch.setattr(
            OwnershipCoordinatorRecordV1, "encode",
            lambda _self: pytest.fail("legacy bytes were re-encoded"),
        )
        result = _resolve_ownership_coordinator_locked_for_test_v2(
            session, ownership_root,
        )

        graph = result.observation
        assert graph.legacy_record_bytes == encoded
        assert graph.legacy_disposition == disposition
        assert len(graph.transactions) == (1 if prefix == "v2" else 0)


@pytest.mark.parametrize("release_sequence", (1, 2))
@LINUX_ONLY
def test_read_only_resolver_rejects_pending_claim_with_unbound_request(
    tmp_path, release_sequence,
):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        previous_head_id = None
        previous_closed_build_id = None
        previous_cutover_id = None
        if release_sequence == 2:
            first = bound_claim(
                release_sequence=1, previous_head_id=None,
                closed_build_id=D("1"), source_id=D("2"),
                previous_closed_build_id=None, previous_cutover_id=None,
            )
            first_records = transaction_records(
                first, end_sequence=6, previous_closed_build_id=None,
                previous_cutover_id=None, cutover_id=D("3"), head_id=D("4"),
            )
            write_claim(directory, first)
            write_transaction(directory, first, first_records)
            previous_head_id = D("4")
            previous_closed_build_id = D("1")
            previous_cutover_id = D("3")
        pending = bound_claim(
            release_sequence=release_sequence,
            previous_head_id=previous_head_id,
            closed_build_id=D("5"), source_id=D("6"),
            previous_closed_build_id=previous_closed_build_id,
            previous_cutover_id=previous_cutover_id,
        )
        write_claim(directory, claim_with_request(pending, D("9")))

        with pytest.raises(OwnershipCoordinatorError) as failure:
            _resolve_ownership_coordinator_locked_for_test_v2(
                session, ownership_root,
            )
        assert failure.value.detail == "claim request"


@LINUX_ONLY
def test_read_only_resolver_requires_completed_preflight_before_successor(
    tmp_path,
):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        first = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("1"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        write_claim(directory, first)
        write_transaction(
            directory, first,
            transaction_records(
                first, end_sequence=5, previous_closed_build_id=None,
                previous_cutover_id=None, cutover_id=D("3"), head_id=D("4"),
            ),
        )
        successor = bound_claim(
            release_sequence=2, previous_head_id=D("4"),
            closed_build_id=D("5"), source_id=D("6"),
            previous_closed_build_id=D("1"), previous_cutover_id=D("3"),
        )
        write_claim(directory, successor)

        with pytest.raises(OwnershipCoordinatorError) as failure:
            _resolve_ownership_coordinator_locked_for_test_v2(
                session, ownership_root,
            )
        assert failure.value.detail == "claim predecessor"


@pytest.mark.parametrize(
    "invalid_layout",
    ("root-extra", "transaction-gap", "transaction-eighth", "orphan-transaction",
     "legacy-transaction-without-disposition", "orphan-disposition"),
)
@LINUX_ONLY
def test_read_only_resolver_rejects_invalid_inventory_and_cardinality(
    tmp_path, invalid_layout,
):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        records = transaction_records(
            claim, end_sequence=1, previous_closed_build_id=None,
            previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
        )
        if invalid_layout == "root-extra":
            write_control(directory / ".record-000-v1.json.tmp", b"x")
        elif invalid_layout == "transaction-gap":
            write_claim(directory, claim)
            transaction = write_transaction(directory, claim, records)
            (transaction / "record-000-v2.json").unlink()
        elif invalid_layout == "transaction-eighth":
            write_claim(directory, claim)
            complete = transaction_records(
                claim, end_sequence=6, previous_closed_build_id=None,
                previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
            )
            transaction = write_transaction(directory, claim, complete)
            write_control(
                transaction / "record-007-v2.json", complete[-1].encode(),
            )
        elif invalid_layout == "orphan-transaction":
            write_transaction(directory, claim, records)
        elif invalid_layout == "legacy-transaction-without-disposition":
            write_legacy(
                directory, legacy_records(end_sequence=0, closed_build_id=D("3")),
            )
            write_claim(directory, claim)
            write_transaction(directory, claim, records)
        else:
            legacy = legacy_records(end_sequence=0, closed_build_id=D("3"))
            encoded = tuple(record.encode() for record in legacy)
            write_disposition(directory, legacy, encoded, claim)

        with pytest.raises(OwnershipCoordinatorError) as failure:
            _resolve_ownership_coordinator_locked_for_test_v2(
                session, ownership_root,
            )
        assert failure.value.code == "birth_ownership_recovery_required"


@pytest.mark.parametrize(
    ("sequence", "field", "replacement", "detail"),
    (
        (1, "distribution_payload_hash", D("f"), "transaction carry"),
        (1, "target_set_id", "f" * 64, "transaction carry"),
        (
            3, "dominant_startup_receipt", D("f"),
            "transaction threshold carry",
        ),
    ),
)
@LINUX_ONLY
def test_read_only_resolver_rejects_rehashed_carry_mutation(
    tmp_path, sequence, field, replacement, detail,
):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        records = list(transaction_records(
            claim, end_sequence=max(2, sequence), previous_closed_build_id=None,
            previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
        ))
        records[sequence] = replace(records[sequence], **{field: replacement})
        for successor in range(sequence + 1, len(records)):
            records[successor] = replace(
                records[successor],
                previous_record_sha256=_record_hash_v2(
                    records[successor - 1].encode(),
                ),
            )
        write_claim(directory, claim)
        write_transaction(directory, claim, tuple(records))

        with pytest.raises(OwnershipCoordinatorError) as failure:
            _resolve_ownership_coordinator_locked_for_test_v2(
                session, ownership_root,
            )
        assert failure.value.detail == detail


@LINUX_ONLY
def test_read_only_resolver_detects_inventory_race(tmp_path, monkeypatch):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        make_coordinator_root(ownership_root)
        real_snapshot = coordinator_module._coordinator_inventory_snapshot_v2
        calls = 0

        def changing_snapshot(directory):
            nonlocal calls
            calls += 1
            value = real_snapshot(directory)
            return value if calls == 1 else value + (("changed",),)

        monkeypatch.setattr(
            coordinator_module, "_coordinator_inventory_snapshot_v2",
            changing_snapshot,
        )
        with pytest.raises(OwnershipCoordinatorError) as failure:
            _resolve_ownership_coordinator_locked_for_test_v2(
                session, ownership_root,
            )
        assert failure.value.detail == "coordinator changed"


@LINUX_ONLY
def test_read_only_resolver_validates_session_before_state_io(
    tmp_path, monkeypatch,
):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        pass

    monkeypatch.setattr(
        coordinator_module, "_resolve_ownership_coordinator_at_v2",
        lambda *_args, **_kwargs: pytest.fail("state I/O preceded session check"),
    )
    with pytest.raises(OwnershipCoordinatorError) as failure:
        _resolve_ownership_coordinator_locked_for_test_v2(session, ownership_root)
    assert failure.value.code == "birth_ownership_deployment_lock_invalid"


def test_product_resolver_platform_gate_precedes_session_and_state_io(
    monkeypatch,
):
    monkeypatch.setattr(coordinator_module.sys, "platform", "win32")
    monkeypatch.setattr(
        coordinator_module, "_require_deployment_lock_session_v1",
        lambda *_args: pytest.fail("session touched before platform gate"),
    )
    monkeypatch.setattr(
        coordinator_module, "_resolve_ownership_coordinator_at_v2",
        lambda *_args, **_kwargs: pytest.fail("state I/O before platform gate"),
    )
    with pytest.raises(OwnershipCoordinatorError) as failure:
        _resolve_ownership_coordinator_locked_v2(object())
    assert failure.value.code == "birth_ownership_platform_unsupported"


@LINUX_ONLY
def test_product_graph_snapshot_requires_registered_identity_and_exact_session(
    monkeypatch,
):
    observation = _ObservedOwnershipCoordinatorGraphV2(
        (), (), (), (), (), None,
    )
    session = object()
    monkeypatch.setattr(
        coordinator_module, "_require_deployment_lock_session_v1",
        lambda _candidate: None,
    )
    monkeypatch.setattr(
        coordinator_module, "_resolve_ownership_coordinator_at_v2",
        lambda *_args, **_kwargs: observation,
    )

    snapshot = _resolve_ownership_coordinator_locked_v2(session)
    assert type(snapshot) is _LockedOwnershipCoordinatorGraphSnapshotV2
    assert _require_locked_coordinator_graph_snapshot_v2(
        snapshot, session,
    ) is observation
    assert not hasattr(snapshot, "_seal")
    assert not hasattr(snapshot, "observation")
    assert not hasattr(
        coordinator_module, "_mint_locked_coordinator_graph_snapshot_v2",
    )

    with pytest.raises(TypeError):
        copy.copy(snapshot)
    with pytest.raises(TypeError):
        copy.deepcopy(snapshot)
    with pytest.raises(TypeError):
        replace(snapshot)

    direct = _LockedOwnershipCoordinatorGraphSnapshotV2(object())
    forged = object.__new__(_LockedOwnershipCoordinatorGraphSnapshotV2)
    object.__setattr__(forged, "_token", snapshot._token)

    class HostileHash:
        def __hash__(self):
            raise RuntimeError("hash must not run before the nominal check")

        def __eq__(self, _other):
            raise RuntimeError("equality must not run before the nominal check")

    for candidate, candidate_session in (
        (direct, session), (forged, session), (snapshot, object()),
        (HostileHash(), session),
    ):
        with pytest.raises(OwnershipCoordinatorError) as failure:
            _require_locked_coordinator_graph_snapshot_v2(
                candidate, candidate_session,
            )
        assert failure.value.detail == "graph authority"

    coordinator_apis = BOUNDARY_APIS["executor_birth_ownership_coordinator"]
    assert coordinator_apis["_resolve_locked_coordinator_graph_issued_v2"] == (
        "store_write",
    )
    assert coordinator_apis["_require_locked_coordinator_graph_issued_v2"] == (
        "store_write",
    )


@LINUX_ONLY
def test_read_only_resolver_rejects_hardlinked_control_file(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        claim_path = write_claim(directory, claim)
        os.link(claim_path, tmp_path / "outside-alias.json")

        with pytest.raises(OwnershipCoordinatorError) as failure:
            _resolve_ownership_coordinator_locked_for_test_v2(
                session, ownership_root,
            )
        assert failure.value.code == "birth_ownership_recovery_required"


def test_legacy_v1_codec_rejects_boolean_schema_version():
    encoded = legacy_records(end_sequence=0, closed_build_id=D("3"))[0].encode()
    value = json.loads(encoded)
    value["schema_version"] = True
    with pytest.raises(OwnershipCoordinatorError):
        coordinator_module._decode_record(canonical(value))

    value = json.loads(record_v2(0).encode())
    value["extra"] = None
    with pytest.raises(OwnershipCoordinatorError):
        _decode_record_v2(canonical(value))
