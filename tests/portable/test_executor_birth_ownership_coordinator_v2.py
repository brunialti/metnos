from __future__ import annotations

import base64
import copy
from dataclasses import replace
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

import executor_birth_ownership_coordinator as coordinator_module
from contract_boundary_guard import BOUNDARY_APIS
from executor_birth_cutover import CurrentReceiptProof
from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
from executor_birth_ownership_coordinator import (
    LegacyDispositionV2, OwnershipCoordinatorError,
    OwnershipCoordinatorRecordV1, OwnershipCoordinatorRecordV2,
    OwnershipCoordinatorStateV1,
    _LockedOwnershipCoordinatorGraphSnapshotV2,
    _ObservedOwnershipCoordinatorGraphV2,
    _OwnershipCoordinatorGraphSnapshotForTestV2,
    SuccessorClaimV1, _decode_legacy_disposition_v2, _decode_record_v2,
    _decode_successor_claim_v1, _install_transaction_id_v1,
    _legacy_disposition_id_v2, _legacy_journal_hash_v2, _record_basename_v2,
    _record_hash, _record_hash_v2, _successor_claim_basename_v1,
    _successor_claim_id_v1, _deployment_lock_for_test_v1,
    _resolve_ownership_coordinator_locked_v2,
    _resolve_ownership_coordinator_locked_for_test_v2,
    _require_locked_coordinator_graph_snapshot_v2,
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


def transaction_records(
    claim: SuccessorClaimV1, *, end_sequence: int,
    previous_closed_build_id: str | None,
    previous_cutover_id: str | None,
    cutover_id: str, head_id: str,
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
            distribution_payload_hash=D("c"),
            distribution_signature_hash=D("d"),
            boundary_inventory_hash=D("e"),
            boundary_guard_version="guard-v2",
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
            current_proof=proof() if sequence >= 1 else None,
            maintenance_before_hash=evidence_hash if sequence >= 1 else None,
            maintenance_after_hash=evidence_hash if sequence >= 1 else None,
            maintenance_proof=evidence if sequence >= 1 else None,
            startup_prerequisite_id=D("1") if sequence >= 2 else None,
            startup_prerequisite_digest=D("2") if sequence >= 2 else None,
            cutover_id=cutover_id if sequence >= 2 else None,
            catalog_id=D("4") if sequence >= 2 else None,
            certificate_payload_hash=D("5") if sequence >= 2 else None,
            certificate_signature_hash=D("6") if sequence >= 2 else None,
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


@LINUX_ONLY
def test_read_only_resolver_rejects_rehashed_carry_mutation(tmp_path):
    ownership_root = tmp_path / "ownership"
    with _deployment_lock_for_test_v1(ownership_root) as session:
        directory = make_coordinator_root(ownership_root)
        claim = bound_claim(
            release_sequence=1, previous_head_id=None,
            closed_build_id=D("3"), source_id=D("2"),
            previous_closed_build_id=None, previous_cutover_id=None,
        )
        records = list(transaction_records(
            claim, end_sequence=2, previous_closed_build_id=None,
            previous_cutover_id=None, cutover_id=D("4"), head_id=D("5"),
        ))
        records[1] = replace(records[1], distribution_payload_hash=D("f"))
        records[2] = replace(
            records[2], previous_record_sha256=_record_hash_v2(records[1].encode()),
        )
        write_claim(directory, claim)
        write_transaction(directory, claim, tuple(records))

        with pytest.raises(OwnershipCoordinatorError) as failure:
            _resolve_ownership_coordinator_locked_for_test_v2(
                session, ownership_root,
            )
        assert failure.value.detail == "transaction carry"


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
