import base64
import hashlib
import json
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_epoch_store import (
    BirthLifecycle, EpochCacheKey, EpochStoreError, open_epoch,
    preserve_legacy_rows, record_execution, replace_current_epoch,
)
from executor_birth_lifecycle import (
    CERTIFICATION_DOMAIN, LifecycleCoordinator, LifecycleError,
    LifecyclePublication, load_f5_activation,
)
from executor_birth_receipts import AdmissionReceipt, ApprovedLifecycle
from manifest_inventory import ContractId, ManifestOrigin


CID = ContractId(ManifestOrigin.USER, "demo/manifest.toml")
G1 = "sha256:" + "2" * 64
G2 = "sha256:" + "3" * 64
G3 = "sha256:" + "4" * 64
NOW = "2030-01-01T00:00:00Z"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def certificate(private, *, receipts=5, producers=2, cycles=2, defects=0):
    value = {
        "schema_version": 1,
        "environment_id": "portable-linux-windows",
        "admission_receipt_ids": [f"sha256:{number:064x}" for number in range(10, 10 + receipts)],
        "producer_ids": [f"producer-{number}" for number in range(producers)],
        "routing_cycles": cycles,
        "unresolved_defects": defects,
        "certified_at": NOW,
        "key_id": "operator-2030",
    }
    value["certificate_id"] = "sha256:" + hashlib.sha256(
        CERTIFICATION_DOMAIN + canonical(value)).hexdigest()
    value["signature"] = base64.b64encode(private.sign(
        CERTIFICATION_DOMAIN + canonical(value))).decode()
    return canonical(value)


def activation():
    private = Ed25519PrivateKey.generate()
    return load_f5_activation(certificate(private), authorities={
        "operator-2030": private.public_key(),
    })


def fake_receipt(generation, predecessor, lifecycle):
    receipt = object.__new__(AdmissionReceipt)
    object.__setattr__(receipt, "receipt_id", "sha256:" + "9" * 64)
    object.__setattr__(receipt, "contract_id", CID.value)
    object.__setattr__(receipt, "generation_id", generation)
    object.__setattr__(receipt, "predecessor_id", predecessor)
    object.__setattr__(receipt, "approved_lifecycle", ApprovedLifecycle(lifecycle.value))
    return receipt


def test_activation_is_fail_closed_and_threshold_is_authenticated():
    private = Ed25519PrivateKey.generate()
    with pytest.raises(LifecycleError, match="threshold_not_met"):
        load_f5_activation(certificate(private, receipts=4), authorities={
            "operator-2030": private.public_key(),
        })
    damaged = json.loads(certificate(private))
    damaged["routing_cycles"] = 99
    with pytest.raises(LifecycleError, match="signature"):
        load_f5_activation(canonical(damaged), authorities={
            "operator-2030": private.public_key(),
        })
    with pytest.raises(LifecycleError, match="f5_activation_required"):
        LifecycleCoordinator(object(), db_path=None, publish_and_reread=lambda *_: None,
                             verify_admission=lambda _: None)


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
    assert preserve_legacy_rows(legacy_table="executors", rows=rows,
                                migrated_at=NOW, db_path=db) == 1
    assert preserve_legacy_rows(legacy_table="executors", rows=rows,
                                migrated_at=NOW, db_path=db) == 0
    connection = sqlite3.connect(db)
    row = connection.execute(
        "SELECT legacy_name,legacy_row_json,resolution FROM executor_legacy_state").fetchone()
    assert row == ("legacy-demo", '{"calls":9,"enabled":true,"name":"legacy-demo"}', "unresolved")
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
