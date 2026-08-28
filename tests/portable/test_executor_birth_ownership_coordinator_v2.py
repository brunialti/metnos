from __future__ import annotations

import base64
import hashlib
import json

import pytest

from executor_birth_cutover import CurrentReceiptProof
from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
from executor_birth_ownership_coordinator import (
    LegacyDispositionV2, OwnershipCoordinatorError,
    OwnershipCoordinatorRecordV2, OwnershipCoordinatorStateV1,
    SuccessorClaimV1, _decode_legacy_disposition_v2, _decode_record_v2,
    _decode_successor_claim_v1, _install_transaction_id_v1,
    _legacy_disposition_id_v2, _legacy_journal_hash_v2, _record_basename_v2,
    _record_hash, _record_hash_v2, _successor_claim_basename_v1,
    _successor_claim_id_v1,
)
from executor_birth_ownership_preflight import (
    canonical_maintenance_proof, maintenance_evidence_hash,
)


def D(character: str) -> str:
    return "sha256:" + character * 64


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


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
        current_proof=proof() if sequence >= 1 else None,
        maintenance_before_hash=evidence_hash,
        maintenance_after_hash=evidence_hash,
        maintenance_proof=evidence,
        startup_prerequisite_id=D("1") if sequence >= 2 else None,
        startup_prerequisite_digest=D("2") if sequence >= 2 else None,
        cutover_id=D("3") if sequence >= 2 else None,
        catalog_id=D("4") if sequence >= 2 else None,
        certificate_payload_hash=D("5") if sequence >= 2 else None,
        certificate_signature_hash=D("6") if sequence >= 2 else None,
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

    assert len(value) == 37
    assert _decode_record_v2(encoded) == record
    assert _record_basename_v2(sequence) == f"record-{sequence:03d}-v2.json"
    assert _install_transaction_id_v1(
        record.install_transaction_value(),
    ) == record.install_transaction_id
    assert _record_hash_v2(encoded) != _record_hash(encoded)


@pytest.mark.parametrize(("sequence", "field", "replacement"), (
    (0, "schema_version", 1),
    (0, "sequence", True),
    (0, "current_receipts", [{"unexpected": "receipt"}]),
    (0, "previous_record_sha256", D("1")),
    (0, "install_transaction_id", D("f")),
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

    value = json.loads(record_v2(0).encode())
    value["extra"] = None
    with pytest.raises(OwnershipCoordinatorError):
        _decode_record_v2(canonical(value))
