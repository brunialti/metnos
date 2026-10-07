"""Real native epoch transitions and preserved migrations exercise F6 ownership."""
import os
import signal
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_epoch_store as native
from executor_birth_canonical import encode_canonical_ascii_v1 as canonical
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_epoch_sqlite import _EpochLegacyOwner, _EpochOwner
from install.birth_retention_maintenance import Maintenance, OwnerObject
from manifest_inventory import ContractId, ManifestOrigin


OLD = "2020-01-01T00:00:00Z"
CID = ContractId(ManifestOrigin.USER, "epoch_sample/manifest.toml")
G1, G2 = ("sha256:" + digit * 64 for digit in "12")


@pytest.fixture
def epoch(tmp_path):
    owner = _EpochOwner(path=tmp_path / "epochs.sqlite", require_exclusion=lambda: None, owner=None)

    def create(*, archived=True, generation=G1):
        native.open_epoch(contract_id=CID, generation_id=generation, name="epoch_sample", source="synt",
                          lifecycle=native.BirthLifecycle.ACTIVE, observed_at=OLD, db_path=owner.path)
        if archived:
            for version, before, after in ((1, "active", "deprecated"), (2, "deprecated", "archived")):
                native.transition_epoch(
                    native.EpochCacheKey(CID, generation, native.BirthLifecycle(before)),
                    expected_version=version, new_state=native.EpochState(after),
                    new_lifecycle=native.BirthLifecycle(after), event_kind=after,
                    occurred_at=OLD, db_path=owner.path)
        key = native.EpochCacheKey(CID, generation, native.BirthLifecycle.ARCHIVED if archived
                                   else native.BirthLifecycle.ACTIVE)
        native.put_cache(key, b"native cached bytes", created_at=OLD, db_path=owner.path)
        return key
    return owner, create


def _migration(path):
    rows = ({"name": "old_name", "calls": 7},)
    native.migrate_legacy_rows(
        source_id=G1, source_schema_id=G2, legacy_table="executor_usage", rows=rows,
        expected_count=1, expected_digest=native.legacy_rows_digest(rows), migrated_at=OLD, db_path=path)
    return native.verify_preserved_migrations(db_path=path)[0]["migration_id"]


def test_archived_bundle_deleted_without_touching_current_generation(epoch):
    owner, create = epoch
    old_key = create()
    current_key = create(archived=False, generation=G2)
    rows = {row.values["generation_id"]: row for row in owner.scan()}
    row = rows[G1]
    assert owner.state_of(row).state is NodeState.CLOSED
    assert [len(items) for _, items in row.related] == [3, 1, 0]
    owner.delete(row.identity, row.version)
    assert owner.version(row.identity) is None
    owner.delete(row.identity, row.version)
    assert native.get_cache(old_key, db_path=owner.path) is None
    assert native.get_cache(current_key, db_path=owner.path) == b"native cached bytes"
    assert owner.scan() == (rows[G2],)
    with sqlite3.connect(owner.path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_current_and_deprecated_epochs_are_not_closed_by_age(epoch):
    owner, create = epoch
    key = create(archived=False)
    row, = owner.scan()
    assert owner.state_of(row).roots == (RootKind.CURRENT_EPOCH,)
    with pytest.raises(RetentionError, match="row still retained"):
        owner.delete(row.identity, row.version)
    native.transition_epoch(key, expected_version=1, new_state=native.EpochState.DEPRECATED,
                            new_lifecycle=native.BirthLifecycle.DEPRECATED, event_kind="deprecated",
                            occurred_at=OLD, db_path=owner.path)
    row, = owner.scan()
    assert owner.state_of(row).roots == (RootKind.OPEN_AUDIT,)
    with pytest.raises(RetentionError, match="row still retained"):
        owner.delete(row.identity, row.version)


def test_native_replacement_keeps_its_original_opening_event(epoch):
    owner, create = epoch
    create(archived=False)
    replacement = native.replace_current_epoch(
        contract_id=CID, expected_generation_id=G1, expected_state_version=1,
        generation_id=G2, name="epoch_sample", source="synt",
        lifecycle=native.BirthLifecycle.ACTIVE, observed_at=OLD,
        db_path=owner.path, event_kind="birth_committed")
    assert replacement.closed_generation_id == G1
    rows = {row.values["generation_id"]: row for row in owner.scan()}
    assert owner.state_of(rows[G2]).roots == (RootKind.CURRENT_EPOCH,)
    assert owner.state_of(rows[G1]).roots == (RootKind.OPEN_AUDIT,)
    assert dict(rows[G2].related)["executor_epoch_history"][0]["event_kind"] == "birth_committed"


def test_changed_cache_invalidates_version_and_extends_retention(epoch):
    owner, create = epoch
    key = create()
    before, = owner.scan()
    native.put_cache(key, b"new native bytes", created_at="2030-01-01T00:00:00Z", db_path=owner.path)
    with pytest.raises(RetentionError, match="retention_owner_changed"):
        owner.delete(before.identity, before.version)
    after, = owner.scan()
    assert owner.state_of(after).eligible_after == "2030-04-01T00:00:00Z"
    with pytest.raises(RetentionError, match="row still retained"):
        owner.delete(after.identity, after.version)


def test_native_legacy_resolution_preserves_epoch_and_migration_proof(epoch):
    owner, create = epoch
    create()
    before, = owner.scan()
    migration_id = _migration(owner.path)
    native.record_legacy_resolutions(migration_id=migration_id,
        resolutions=((0, "attested", CID.value, G1, G2),), recorded_at=OLD, db_path=owner.path)
    expected = native.verify_preserved_migrations(db_path=owner.path)
    with pytest.raises(RetentionError, match="retention_owner_changed"):
        owner.delete(before.identity, before.version)
    after, = owner.scan()
    assert owner.state_of(after).roots == (RootKind.OPEN_AUDIT,)
    with pytest.raises(RetentionError, match="row still retained"):
        owner.delete(after.identity, after.version)
    for kind in ("state", "migrations"):
        legacy = _EpochLegacyOwner(kind, path=owner.path, require_exclusion=lambda: None, owner=None)
        evidence, = legacy.scan()
        assert legacy.state_of(evidence).roots == (RootKind.OPEN_AUDIT,)
        with pytest.raises(RetentionError, match="row still retained"):
            legacy.delete(evidence.identity, evidence.version)
        with pytest.raises(RetentionError, match="foreign identity"):
            legacy.delete(after.identity, after.version)
    assert native.verify_preserved_migrations(db_path=owner.path) == expected


def test_legacy_copy_damage_cannot_pass_inventory(epoch):
    owner, create = epoch
    create()
    _migration(owner.path)
    legacy = _EpochLegacyOwner("migrations", path=owner.path, require_exclusion=lambda: None, owner=None)
    with sqlite3.connect(owner.path) as db:
        db.execute("UPDATE executor_legacy_state SET legacy_name='different'")
    with pytest.raises(RetentionError, match="legacy migration binding"):
        legacy.scan()


def test_absent_and_empty_store_never_initialised_or_migrated(epoch):
    owner, create = epoch
    assert owner.scan() == ()
    assert not owner.path.exists()
    create()
    row, = owner.scan()
    owner.delete(row.identity, row.version)
    with sqlite3.connect(owner.path) as db:
        db.execute("PRAGMA user_version=2")
    with pytest.raises(RetentionError, match="epoch schema version"):
        owner.scan()


@pytest.mark.parametrize("damage", ["orphan", "missing_history"])
def test_damaged_native_relations_refuse_collection(epoch, damage):
    owner, create = epoch
    create()
    row, = owner.scan()
    with sqlite3.connect(owner.path) as db:
        db.execute("DELETE FROM " + ("executor_epochs" if damage == "orphan" else "executor_epoch_history"))
    with pytest.raises(RetentionError):
        owner.scan()
    with pytest.raises(RetentionError):
        owner.delete(row.identity, row.version)


def test_parent_effect_failure_rolls_back_history_and_cache(epoch, monkeypatch):
    owner, create = epoch
    create()
    row, = owner.scan()
    original = owner._delete_related

    def interrupted(connection, record):
        original(connection, record)
        raise RuntimeError("interrupted before parent deletion")
    monkeypatch.setattr(owner, "_delete_related", interrupted)
    with pytest.raises(RuntimeError):
        owner.delete(row.identity, row.version)
    assert owner.scan() == (row,)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="native process termination requires fork")
@pytest.mark.parametrize("point", ["before_effect", "after_effect"])
def test_epoch_collection_recovers_original_receipt_after_process_death(epoch, tmp_path, point):
    owner, create = epoch
    create()
    row, = owner.scan()
    state = owner.state_of(row)
    obj = OwnerObject(row.identity, row.version, state.state, state.created_at, state.eligible_after)
    root = tmp_path / "maintenance"
    root.mkdir(mode=0o755)
    (root / "holds.json").write_bytes(canonical({"schema_version": 1, "holds": []}))
    (root / "holds.json").chmod(0o600)
    key = Ed25519PrivateKey.generate()
    public, owners = {"receipt": key.public_key()}, {owner.name: owner}
    maintenance = lambda: Maintenance(root, root_owned=False, require_exclusion=lambda: None)
    maintenance().begin((obj,), run_id=G1, observed_at="2026-10-05T12:00:00Z",
                        key_id="receipt", private_key=key, public_keys=public, owners=owners)
    pid = os.fork()
    if pid == 0:
        def crash(stage):
            if stage == point:
                os.kill(os.getpid(), signal.SIGKILL)
        try:
            maintenance().resume(owners, public_keys=public, max_objects=10, max_seconds=20, crash=crash)
        finally:
            os._exit(91)
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert maintenance().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["remaining"] == 0
    assert maintenance().finish(owners, public_keys=public, verify_recovery=lambda: None)["deleted"] == 1
    assert owner.scan() == ()
