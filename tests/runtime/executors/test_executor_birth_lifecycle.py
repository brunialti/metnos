import base64
import json
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_epoch_store import (
    BirthLifecycle, EpochCacheKey, EpochStoreError, open_epoch,
    decode_legacy_row, preserve_legacy_rows, record_execution, replace_current_epoch,
)
from executor_birth_lifecycle import (
    CERTIFICATION_DOMAIN, LifecycleCoordinator, LifecycleError,
    LifecyclePublication, load_f5_activation, _decode_f5_activation_v1,
)
from executor_birth_certification_authority import CertificationPublicKeyV1
from executor_birth_keystore import birth_key_id
from executor_birth_receipts import AdmissionReceipt, ApprovedLifecycle
from manifest_inventory import ContractId, ManifestOrigin


CID = ContractId(ManifestOrigin.USER, "demo/manifest.toml")
G1 = "sha256:" + "2" * 64
G2 = "sha256:" + "3" * 64
G3 = "sha256:" + "4" * 64
NOW = "2030-01-01T00:00:00Z"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def certificate(private):
    """An isolated codec fixture, never qualifying historical evidence."""
    value = {
        "schema_version": 1,
        "purpose": "f5_activation_v1",
        "installation_id": G1, "qualification_id": G2,
        "head_id": G1, "closed_build_id": G2, "migration_id": G3,
        "policy_id": "rm0008-f5/1", "key_id": birth_key_id(private.public_key()),
    }
    value["signature"] = base64.b64encode(private.sign(
        CERTIFICATION_DOMAIN + canonical(value))).decode()
    return canonical(value)


def activation():
    private = Ed25519PrivateKey.generate()
    return decode_fixture(certificate(private), private)


def decode_fixture(encoded, private):
    public = private.public_key()
    return _decode_f5_activation_v1(
        encoded, authority=CertificationPublicKeyV1(birth_key_id(public), public, "active"),
        installation_id=G1, head_id=G1, closed_build_id=G2,
    )


def fake_receipt(generation, predecessor, lifecycle):
    receipt = object.__new__(AdmissionReceipt)
    object.__setattr__(receipt, "receipt_id", "sha256:" + "9" * 64)
    object.__setattr__(receipt, "contract_id", CID.value)
    object.__setattr__(receipt, "generation_id", generation)
    object.__setattr__(receipt, "predecessor_id", predecessor)
    object.__setattr__(receipt, "approved_lifecycle", ApprovedLifecycle(lifecycle.value))
    return receipt


def test_activation_has_no_caller_selected_authority_or_counts():
    private = Ed25519PrivateKey.generate()
    with pytest.raises(TypeError):
        load_f5_activation(certificate(private), authorities={"invented": private.public_key()})
    damaged = json.loads(certificate(private))
    damaged["routing_cycles"] = 99
    with pytest.raises(LifecycleError, match="f5_activation_invalid"):
        decode_fixture(canonical(damaged), private)
    with pytest.raises(LifecycleError, match="f5_activation_required"):
        LifecycleCoordinator(object(), db_path=None, publish_and_reread=lambda *_: None,
                             verify_admission=lambda _: None)


def test_activation_rejects_changed_binding_and_noncanonical_base64():
    private = Ed25519PrivateKey.generate()
    changed = json.loads(certificate(private))
    changed["qualification_id"] = G3
    with pytest.raises(LifecycleError, match="f5_activation_invalid"):
        decode_fixture(canonical(changed), private)

    noncanonical = json.loads(certificate(private))
    noncanonical["signature"] = noncanonical["signature"].rstrip("=")
    with pytest.raises(LifecycleError, match="f5_activation_invalid"):
        decode_fixture(canonical(noncanonical), private)


def test_replace_current_epoch_is_one_transaction_and_resets_counts(tmp_path):
    db = tmp_path / "epochs.sqlite"
    open_epoch(contract_id=CID, generation_id=G1, name="demo", source="synt",
               lifecycle=BirthLifecycle.PREEXERCISE, observed_at=NOW, db_path=db)
    connection = sqlite3.connect(db)
    connection.execute(
        "UPDATE executor_epochs SET total_calls=7,successful_calls=6,failed_calls=1 "
        "WHERE generation_id=?", (G1,))
    connection.commit(); connection.close()
    result = replace_current_epoch(
        contract_id=CID, expected_generation_id=G1, expected_state_version=1,
        generation_id=G2, name="demo", source="birth", lifecycle=BirthLifecycle.ACTIVE,
        observed_at=NOW, db_path=db, event_kind="lifecycle_active")
    assert result.opened.generation_id == G2 and result.opened.state_version == 1
    connection = sqlite3.connect(db)
    rows = connection.execute(
        "SELECT generation_id,state,lifecycle,total_calls,successful_calls,failed_calls,"
        "historic_epoch_ref FROM executor_epochs ORDER BY generation_id").fetchall()
    assert rows == [(G1, "deprecated", "deprecated", 7, 6, 1, None),
                    (G2, "current", "active", 0, 0, 0, None)]
    connection.close()


def test_replace_stale_generation_or_version_preserves_current(tmp_path):
    db = tmp_path / "epochs.sqlite"
    open_epoch(contract_id=CID, generation_id=G1, name="demo", source="synt",
               lifecycle=BirthLifecycle.PREEXERCISE, observed_at=NOW, db_path=db)
    with pytest.raises(EpochStoreError, match="stale predecessor"):
        replace_current_epoch(
            contract_id=CID, expected_generation_id=G1, expected_state_version=2,
            generation_id=G2, name="demo", source="birth",
            lifecycle=BirthLifecycle.ACTIVE, observed_at=NOW, db_path=db,
            event_kind="lifecycle_active")
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT generation_id,state FROM executor_epochs").fetchall() == [(G1, "current")]
    connection.close()


def test_counts_are_bound_to_exact_current_generation_and_version(tmp_path):
    db = tmp_path / "epochs.sqlite"
    open_epoch(contract_id=CID, generation_id=G1, name="demo", source="synt",
               lifecycle=BirthLifecycle.ACTIVE, observed_at=NOW, db_path=db)
    record_execution(EpochCacheKey(CID, G1, BirthLifecycle.ACTIVE),
                     expected_version=1, successful=True, occurred_at=NOW, db_path=db)
    with pytest.raises(EpochStoreError, match="stale execution identity"):
        record_execution(EpochCacheKey(CID, G2, BirthLifecycle.ACTIVE),
                         expected_version=1, successful=False, occurred_at=NOW, db_path=db)
    connection = sqlite3.connect(db)
    assert connection.execute(
        "SELECT total_calls,successful_calls,failed_calls FROM executor_epochs").fetchone() == (1, 1, 0)
    connection.close()


def test_legacy_migration_is_lossless_unresolved_and_idempotent(tmp_path):
    db = tmp_path / "epochs.sqlite"
    rows = ({"name": "legacy-demo", "calls": 9, "enabled": True},)
    assert preserve_legacy_rows(source_id=G1, source_schema_id=G2, legacy_table="executors", rows=rows,
                                migrated_at=NOW, db_path=db) == 1
    assert preserve_legacy_rows(source_id=G1, source_schema_id=G2, legacy_table="executors", rows=rows,
                                migrated_at=NOW, db_path=db) == 0
    connection = sqlite3.connect(db)
    row = connection.execute(
        "SELECT legacy_name,legacy_row_json,resolution FROM executor_legacy_state").fetchone()
    assert (row[0], decode_legacy_row(row[1]), row[2]) == ("legacy-demo", rows[0], "unresolved")
    connection.close()


def test_coordinator_opens_only_after_exact_authenticated_reread(tmp_path):
    db = tmp_path / "epochs.sqlite"
    open_epoch(contract_id=CID, generation_id=G1, name="demo", source="synt",
               lifecycle=BirthLifecycle.PREEXERCISE, observed_at=NOW, db_path=db)
    events = []
    verified = {}
    def publish(cid, predecessor, target, historic):
        events.append(("published_and_reread", predecessor, target))
        receipt = fake_receipt(G2, predecessor, target)
        verified[b"signed-g2"] = receipt
        return LifecyclePublication(receipt, b"signed-g2", G2,
                                    predecessor, ApprovedLifecycle(target.value))
    coordinator = LifecycleCoordinator(
        activation(), db_path=db, publish_and_reread=publish,
        verify_admission=lambda encoded: verified[encoded])
    result = coordinator.revise(
        EpochCacheKey(CID, G1, BirthLifecycle.PREEXERCISE), expected_version=1,
        target=BirthLifecycle.ACTIVE, name="demo", source="birth", occurred_at=NOW)
    assert events == [("published_and_reread", G1, BirthLifecycle.ACTIVE)]
    assert result.epochs.opened.generation_id == G2


def test_bad_reread_never_changes_epoch(tmp_path):
    db = tmp_path / "epochs.sqlite"
    open_epoch(contract_id=CID, generation_id=G1, name="demo", source="synt",
               lifecycle=BirthLifecycle.ACTIVE, observed_at=NOW, db_path=db)
    publication = LifecyclePublication(
        fake_receipt(G2, G1, BirthLifecycle.QUARANTINED), b"signed-g2", G2, G3,
        ApprovedLifecycle.QUARANTINED)
    coordinator = LifecycleCoordinator(
        activation(), db_path=db, publish_and_reread=lambda *_: publication,
        verify_admission=lambda _: publication.receipt)
    with pytest.raises(LifecycleError, match="reread_invalid"):
        coordinator.quarantine_before_nonselection(
            EpochCacheKey(CID, G1, BirthLifecycle.ACTIVE), expected_version=1,
            name="demo", source="feedback", occurred_at=NOW)
    connection = sqlite3.connect(db)
    assert connection.execute(
        "SELECT generation_id,lifecycle,state FROM executor_epochs").fetchall() == [
            (G1, "active", "current")]
    connection.close()


def test_rollback_opens_clean_epoch_with_historic_reference(tmp_path):
    db = tmp_path / "epochs.sqlite"
    open_epoch(contract_id=CID, generation_id=G1, name="demo", source="birth",
               lifecycle=BirthLifecycle.ACTIVE, observed_at=NOW, db_path=db)
    verified = {}
    def publish(_cid, predecessor, target, historic):
        assert historic == "epoch:" + G1
        receipt = fake_receipt(G2, predecessor, target)
        verified[b"signed-g2"] = receipt
        return LifecyclePublication(receipt, b"signed-g2", G2,
                                    predecessor, ApprovedLifecycle.ACTIVE)
    coordinator = LifecycleCoordinator(
        activation(), db_path=db, publish_and_reread=publish,
        verify_admission=lambda encoded: verified[encoded])
    coordinator.revise(
        EpochCacheKey(CID, G1, BirthLifecycle.ACTIVE), expected_version=1,
        target=BirthLifecycle.ACTIVE, name="demo", source="rollback", occurred_at=NOW,
        historic_epoch_ref="epoch:" + G1)
    connection = sqlite3.connect(db)
    assert connection.execute(
        "SELECT generation_id,total_calls,historic_epoch_ref FROM executor_epochs "
        "WHERE state='current'").fetchone() == (G2, 0, "epoch:" + G1)
    assert connection.execute(
        "SELECT COUNT(*) FROM executor_epochs WHERE state='current'").fetchone()[0] == 1
    connection.close()
