"""Exercise durable recovery across a real file owner and a separate SQLite DB.

These fixtures prove the protocol, not coverage of installed Metnos owners.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import signal
import sqlite3
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_canonical import encode_canonical_ascii_v1 as canonical
from executor_birth_authority_files import OwnershipAuthorityError
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import Maintenance, ObjectIdentity, OwnerObject, plan


NOW = "2026-10-05T12:00:00Z"
OLD = "2020-01-01T00:00:00Z"
RUN = "sha256:" + "a" * 64


def digest(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def write_holds(root, holds=()):
    path = root / "holds.json"
    path.write_bytes(canonical({"schema_version": 1, "holds": sorted(holds)}))
    path.chmod(0o600)


@pytest.mark.skipif(sys.platform != "linux", reason="native administrative exclusion uses flock")
@pytest.mark.parametrize("marker", ["absent", "empty", "valid", "corrupt", "directory", "symlink"])
def test_normal_administrative_lock_blocks_incomplete_retention(tmp_path, marker):
    import fcntl
    from install.birth_ownership_authority_provisioner import (
        _LOCK_BASENAME_V1, _provisioning_lock, _provisioning_exclusion_v1,
    )

    tmp_path.chmod(0o755)
    directory = tmp_path / "retention-maintenance-v1"
    if marker != "absent":
        directory.mkdir(mode=0o755)
    active = directory / "active.json"
    if marker == "valid":
        active.write_bytes(canonical({"schema_version": 1, "run_id": RUN}))
    elif marker == "corrupt":
        active.write_bytes(b"partial")
    elif marker == "directory":
        active.mkdir()
    elif marker == "symlink":
        active.symlink_to(tmp_path / "missing")
    effects = []
    if marker in {"absent", "empty"}:
        with _provisioning_lock(tmp_path, root_owned=False):
            effects.append("normal operation")
    else:
        with pytest.raises(OwnershipAuthorityError, match="birth_retention_recovery_required"):
            with _provisioning_lock(tmp_path, root_owned=False):
                effects.append("must not run")
        assert not effects
    # Recovery still uses the very same lock; there is no unlocked exception.
    with _provisioning_exclusion_v1(tmp_path, root_owned=False):
        competing = os.open(tmp_path / _LOCK_BASENAME_V1, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(competing)


@pytest.mark.skipif(sys.platform != "linux", reason="native deployment exclusion uses flock")
def test_deployment_checks_retention_after_acquiring_existing_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import fcntl
    import executor_birth_authority_files as files
    import executor_birth_ownership_coordinator as coordinator

    tmp_path.chmod(0o755)
    directory = tmp_path / "retention-maintenance-v1"
    directory.mkdir(mode=0o755)
    (directory / "active.json").write_bytes(b"interrupted maintenance")
    native_lock = coordinator._deployment_lock_at_v1
    native_check = files._require_no_retention_maintenance_at_v1
    observed = []

    @contextmanager
    def isolated_lock(root, *, root_owned):
        assert root == tmp_path and root_owned is True
        with native_lock(root, root_owned=False) as lease:
            yield lease

    def check(root, *, root_owned):
        assert root == tmp_path and root_owned is True
        competing = os.open(tmp_path / coordinator.DEPLOYMENT_LOCK_BASENAME_V1, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed.append("checked under deployment lock")
        finally:
            os.close(competing)
        native_check(root, root_owned=False)

    monkeypatch.setattr(coordinator, "DEFAULT_OWNERSHIP_ROOT_V1", tmp_path)
    monkeypatch.setattr(coordinator, "_deployment_lock_at_v1", isolated_lock)
    monkeypatch.setattr(files, "_require_no_retention_maintenance_at_v1", check)
    with pytest.raises(OwnershipAuthorityError, match="birth_retention_recovery_required"):
        with coordinator._deployment_lock_v1():
            pytest.fail("administrative deployment resumed before retention recovery")
    assert observed == ["checked under deployment lock"]


class FileOwner:
    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.identity = ObjectIdentity("files", str(root), "blob", "same-id", "contract-a")
        self.path = root / "data.bin"
        self.path.write_bytes(b"disposable physical file")
        self.effects = root / "effects"

    def version(self, identity):
        assert identity == self.identity
        return digest(self.path.read_bytes()) if self.path.exists() else None

    def delete(self, identity, expected_version):
        if self.version(identity) is None:
            return
        assert self.version(identity) == expected_version
        self.path.unlink()
        with self.effects.open("ab") as output:
            output.write(b"delete\n")
            output.flush()
            os.fsync(output.fileno())
        if sys.platform.startswith("linux"):
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def count(self):
        return len(self.effects.read_bytes().splitlines()) if self.effects.exists() else 0


class SqliteOwner:
    def __init__(self, root):
        self.path = root / "physical-owner.sqlite"
        self.identity = ObjectIdentity("database", str(self.path), "blob", "same-id", "contract-a")
        with sqlite3.connect(self.path) as db:
            db.executescript("CREATE TABLE objects(id TEXT PRIMARY KEY,payload BLOB);"
                             "CREATE TABLE effects(id TEXT);")
            db.execute("INSERT INTO objects VALUES(?,?)", ("same-id", b"independent database row"))

    def version(self, identity):
        assert identity == self.identity
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM objects WHERE id=?", (identity.local_id,)).fetchone()
            return digest(row[0]) if row else None

    def delete(self, identity, expected_version):
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM objects WHERE id=?", (identity.local_id,)).fetchone()
            if row is None:
                return
            assert row and digest(row[0]) == expected_version
            db.execute("DELETE FROM objects WHERE id=?", (identity.local_id,))
            db.execute("INSERT INTO effects VALUES(?)", (identity.local_id,))

    def count(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]


@pytest.fixture
def context(tmp_path):
    root = tmp_path / "maintenance"
    root.mkdir(mode=0o755)
    write_holds(root)
    owners = {"files": FileOwner(tmp_path / "file-owner"), "database": SqliteOwner(tmp_path)}
    objects = tuple(OwnerObject(owner.identity, owner.version(owner.identity),
                               NodeState.CLOSED, OLD, OLD) for owner in owners.values())
    private = Ed25519PrivateKey.generate()
    public = {"receipt-key": private.public_key()}
    excluded = [True]

    def require_exclusion():
        if not excluded[0]:
            raise RetentionError("retention_invalid", "lost exclusion")

    def maintenance():
        return Maintenance(root, root_owned=False, require_exclusion=require_exclusion)

    def begin(crash=lambda _: None, *, run_id=RUN, candidates=objects):
        maintenance().begin(candidates, run_id=run_id, observed_at=NOW, key_id="receipt-key",
                            private_key=private, public_keys=public, owners=owners, crash=crash)

    return root, owners, objects, public, excluded, maintenance, begin


class Interrupted(RuntimeError):
    pass


def interrupt_at(expected):
    def crash(stage):
        if stage == expected:
            raise Interrupted(stage)
    return crash


def journal(root):
    return sqlite3.connect(root / (RUN[7:] + ".sqlite"))


@pytest.mark.parametrize("point", ["after_intents", "after_active.json_temp_prefix",
                                   "after_active.json_temp_full_write", "after_marker"])
def test_prepared_intents_and_atomic_marker_survive_interrupt(context, point):
    root, owners, _, public, _, factory, begin = context
    with pytest.raises(Interrupted):
        begin(interrupt_at(point))
    assert all(owner.count() == 0 for owner in owners.values())
    if not (root / "active.json").exists():
        begin()  # Same immutable intents/key, before any destructive action.
    result = factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    assert result["completed"] == 2 and result["remaining"] == 0
    result = factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert result == {"run_id": RUN, "deleted": 2, "preserved": 0}
    assert not (root / "active.json").exists()
    assert all(owner.count() == 1 for owner in owners.values())


@pytest.mark.parametrize("point", ["after_journal_create", "before_intents_commit"])
def test_incomplete_preparation_has_no_effect_and_does_not_block_startup(context, point):
    root, owners, _, _, _, _, begin = context
    with pytest.raises(Interrupted):
        begin(interrupt_at(point))
    assert not (root / "active.json").exists()
    assert all(owner.count() == 0 for owner in owners.values())
    assert (root / (RUN[7:] + ".sqlite")).exists()  # Preserve failed preparation.
    begin(run_id="sha256:" + "b" * 64)
    assert (root / "active.json").exists()


@pytest.mark.parametrize("point", ["before_effect", "after_effect", "after_outcome"])
def test_separate_owners_resume_original_receipts_without_repeating_effect(context, point):
    root, owners, _, public, _, factory, begin = context
    begin()
    with journal(root) as db:
        before = db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall()
    with pytest.raises(Interrupted):
        factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20,
                         crash=interrupt_at(point))
    assert (root / "active.json").exists()
    factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    with journal(root) as db:
        assert db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall() == before
        assert set(db.execute("SELECT status FROM intents")) == {("deleted",)}
    assert all(owner.count() == 1 for owner in owners.values())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native SIGKILL and fork")
@pytest.mark.parametrize("point", ["before_effect", "after_effect", "after_outcome"])
def test_sigkill_releases_process_but_keeps_startup_barrier_and_original_intent(context, point):
    root, owners, _, public, _, factory, begin = context
    begin()
    child = os.fork()
    if child == 0:
        try:
            factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20,
                             crash=lambda stage: os.kill(os.getpid(), signal.SIGKILL)
                             if stage == point else None)
        finally:
            os._exit(91)
    _, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert (root / "active.json").exists()
    factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert all(owner.count() == 1 for owner in owners.values())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native SIGKILL and fork")
def test_sigkill_inside_owner_finishes_cleanup_even_when_payload_is_absent(context):
    root, owners, objects, public, _, factory, begin = context
    owner = owners["files"]
    # Model a native bundle: unlinking its last payload precedes removing the
    # now-empty container and syncing its parent. Both belong to one effect.
    container = owner.root / "generation"
    container.mkdir()
    owner.path.rename(container / "data.bin")
    owner.path = container / "data.bin"
    original_delete = owner.delete

    def finish_delete(identity, version):
        original_delete(identity, version)
        if container.exists():
            container.rmdir()
        fd = os.open(owner.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    owner.delete = finish_delete
    begin(candidates=tuple(replace(obj, version=owners[obj.identity.owner].version(obj.identity))
                           for obj in objects))
    with journal(root) as db:
        before = db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall()
    child = os.fork()
    if child == 0:
        def interrupted_delete(identity, version):
            original_delete(identity, version)
            os.kill(os.getpid(), signal.SIGKILL)
        owner.delete = interrupted_delete
        try:
            factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
        finally:
            os._exit(91)
    _, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert owner.version(owner.identity) is None and container.exists()
    assert (root / "active.json").exists()
    factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    assert not container.exists()
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    with journal(root) as db:
        assert db.execute("SELECT identity,owner_version,authentication FROM intents").fetchall() == before
    assert all(item.count() == 1 for item in owners.values())


@pytest.mark.parametrize("mutation", ["missing-owner", "changed-owner", "receipt",
                                      "dropped-intent", "holds-missing", "holds-changed"])
def test_uncertain_or_changed_inventory_blocks_before_any_new_effect(context, mutation):
    root, owners, _, public, _, factory, begin = context
    begin()
    selected = dict(owners)
    if mutation == "missing-owner":
        selected.pop("database")
    elif mutation == "changed-owner":
        with sqlite3.connect(owners["database"].path) as db:
            db.execute("UPDATE objects SET payload=?", (b"new owner version",))
    elif mutation == "receipt":
        with journal(root) as db:
            db.execute("UPDATE intents SET authentication='{}' WHERE position=1")
    elif mutation == "dropped-intent":
        with journal(root) as db:
            db.execute("DELETE FROM intents WHERE position=1")
    elif mutation == "holds-missing":
        (root / "holds.json").unlink()
    else:
        write_holds(root, [owners["files"].identity.key.node_id])
    with pytest.raises((RetentionError, OwnershipAuthorityError)):
        factory().resume(selected, public_keys=public, max_objects=10, max_seconds=20)
    assert (root / "active.json").exists()
    assert all(owner.count() == 0 for owner in owners.values())


def test_budget_preserves_untouched_objects_and_allows_verified_restart(context):
    root, owners, _, public, _, factory, begin = context
    begin()
    result = factory().resume(owners, public_keys=public, max_objects=1, max_seconds=20)
    assert result["completed"] == 1 and result["remaining"] == 1
    with pytest.raises(RetentionError, match="pending intents"):
        factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert (root / "active.json").exists()
    verified = []
    result = factory().finish(owners, public_keys=public, preserve_remaining=True,
                              verify_recovery=lambda: verified.append(True))
    assert result == {"run_id": RUN, "deleted": 1, "preserved": 1} and verified == [True]
    assert sum(owner.count() for owner in owners.values()) == 1
    assert not (root / "active.json").exists()


@pytest.mark.parametrize("point", ["after_reconciliation", "after_completion"])
def test_finish_interruption_keeps_barrier_until_readback_succeeds(context, point):
    root, owners, _, public, _, factory, begin = context
    begin()
    factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    with pytest.raises(Interrupted):
        factory().finish(owners, public_keys=public, verify_recovery=lambda: None,
                         crash=interrupt_at(point))
    assert (root / "active.json").exists()
    def failed_readback():
        raise Interrupted("readback failed")

    with pytest.raises(Interrupted):
        factory().finish(owners, public_keys=public, verify_recovery=failed_readback)
    assert (root / "active.json").exists()
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert not (root / "active.json").exists()


def test_no_effect_without_exclusion_and_session_cannot_cross_process(context, monkeypatch):
    root, owners, _, public, excluded, factory, begin = context
    begin()
    session = factory()
    excluded[0] = False
    with pytest.raises(RetentionError, match="lost exclusion"):
        session.resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    excluded[0] = True
    original = os.getpid()
    monkeypatch.setattr(os, "getpid", lambda: original + 1)
    with pytest.raises(RetentionError, match="foreign process"):
        session.resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    assert all(owner.count() == 0 for owner in owners.values())


def test_composite_identity_keeps_equal_local_ids_in_distinct_stores(context, tmp_path):
    _, owners, objects, _, _, _, _ = context
    assert objects[0].identity.key != objects[1].identity.key
    assert replace(objects[0].identity, contract="contract-b").key != objects[0].identity.key
    selected = plan(objects, observed_owners=frozenset(owners), required_owners=frozenset(owners),
                    observed_roots=frozenset(RootKind), holds=frozenset(),
                    graph_path=tmp_path / "graph.sqlite", run_id=RUN, observed_at=NOW)
    assert set(selected) == set(objects)


def test_plan_preserves_open_dependencies_windows_and_legal_holds(context, tmp_path):
    _, owners, objects, _, _, _, _ = context
    source, target = objects
    open_source = replace(source, state=NodeState.OPEN, eligible_after=None,
                          references=(target.identity,))
    selected = plan((open_source, target), observed_owners=frozenset(owners),
                    required_owners=frozenset(owners), observed_roots=frozenset(RootKind),
                    holds=frozenset(), graph_path=tmp_path / "graph.sqlite",
                    run_id=RUN, observed_at=NOW)
    assert selected == ()
    assert plan((source, target), observed_owners=frozenset(owners),
                required_owners=frozenset(owners), observed_roots=frozenset(RootKind),
                holds=frozenset((source.identity.key.node_id, target.identity.key.node_id)),
                graph_path=tmp_path / "held.sqlite", run_id=RUN, observed_at=NOW) == ()
    future = replace(source, eligible_after="2030-01-01T00:00:00Z", references=(target.identity,))
    assert plan((future, target), observed_owners=frozenset(owners),
                required_owners=frozenset(owners), observed_roots=frozenset(RootKind),
                holds=frozenset(), graph_path=tmp_path / "future.sqlite", run_id=RUN,
                observed_at=NOW) == ()


@pytest.mark.parametrize("root_kind", list(RootKind))
def test_each_retention_root_preserves_its_physical_dependencies(context, tmp_path, root_kind):
    _, owners, (source, target), _, _, _, _ = context
    rooted = replace(source, roots=(root_kind,), references=(target.identity,))
    assert plan((rooted, target), observed_owners=frozenset(owners),
                required_owners=frozenset(owners), observed_roots=frozenset(RootKind),
                holds=frozenset(), graph_path=tmp_path / "graph.sqlite", run_id=RUN,
                observed_at=NOW) == ()


@pytest.mark.parametrize("cyclic", [False, True])
def test_reference_group_is_never_split_by_an_object_budget(context, tmp_path, cyclic):
    _, owners, (source, target), public, _, factory, begin = context
    source = replace(source, references=(target.identity,))
    if cyclic:
        target = replace(target, references=(source.identity,))
    selected = plan((source, target), observed_owners=frozenset(owners),
                    required_owners=frozenset(owners), observed_roots=frozenset(RootKind),
                    holds=frozenset(), graph_path=tmp_path / "graph.sqlite",
                    run_id=RUN, observed_at=NOW)
    assert set(selected) == {source, target}  # Closed cycles are collectible.
    begin(candidates=selected)
    result = factory().resume(owners, public_keys=public, max_objects=1, max_seconds=20)
    assert result["completed"] == 0 and result["remaining"] == 2
    result = factory().finish(owners, public_keys=public, verify_recovery=lambda: None,
                              preserve_remaining=True)
    assert result["preserved"] == 2 and all(owner.count() == 0 for owner in owners.values())


@pytest.mark.parametrize("point", ["after_effect", "after_outcome"])
def test_partially_deleted_reference_group_requires_recovery_before_restart(context, point):
    root, owners, (source, target), public, _, factory, begin = context
    begin(candidates=(replace(source, references=(target.identity,)), target))
    with pytest.raises(Interrupted):
        factory().resume(owners, public_keys=public, max_objects=2, max_seconds=20,
                         crash=interrupt_at(point))
    with pytest.raises(RetentionError, match="partially deleted reference group"):
        factory().finish(owners, public_keys=public, verify_recovery=lambda: None,
                          preserve_remaining=True)
    assert (root / "active.json").exists()
    factory().resume(owners, public_keys=public, max_objects=2, max_seconds=20)
    result = factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert result["deleted"] == 2 and all(owner.count() == 1 for owner in owners.values())


def test_time_limit_waits_for_current_reference_group_then_stops(context, monkeypatch):
    import install.birth_retention_maintenance as maintenance

    _, owners, (source, target), public, _, factory, begin = context
    begin(candidates=(replace(source, references=(target.identity,)), target))
    clock = iter((0.0, 0.5, 30.0))
    monkeypatch.setattr(maintenance.time, "monotonic", lambda: next(clock))
    result = factory().resume(owners, public_keys=public, max_objects=2, max_seconds=1)
    assert result["elapsed_seconds"] == 30 and result["remaining"] == 0
    assert factory().finish(owners, public_keys=public, verify_recovery=lambda: None)["deleted"] == 2


def test_reference_groups_cannot_be_changed_in_a_prepared_journal(context):
    root, owners, (source, target), public, _, factory, begin = context
    begin(candidates=(replace(source, references=(target.identity,)), target))
    with journal(root) as db:
        db.execute("UPDATE intents SET recovery_group=1 WHERE position=1")
    with pytest.raises(RetentionError, match="journal content changed"):
        factory().resume(owners, public_keys=public, max_objects=2, max_seconds=20)
    assert all(owner.count() == 0 for owner in owners.values())


@pytest.mark.parametrize("missing", ["owner", "root", "reference", "holds"])
def test_missing_inventory_is_not_interpreted_as_no_references(context, tmp_path, missing):
    _, owners, objects, _, _, _, _ = context
    kwargs = {"observed_owners": frozenset(owners), "required_owners": frozenset(owners),
              "observed_roots": frozenset(RootKind), "holds": frozenset(),
              "graph_path": tmp_path / "graph.sqlite", "run_id": RUN, "observed_at": NOW}
    if missing == "owner":
        kwargs["observed_owners"] = frozenset(("files",))
    elif missing == "root":
        kwargs["observed_roots"] = frozenset((RootKind.CURRENT_POINTER,))
    elif missing == "holds":
        kwargs["holds"] = None
    else:
        objects = (replace(objects[0], references=(replace(objects[1].identity, local_id="absent"),)),)
    with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
        plan(objects, **kwargs)
    assert not kwargs["graph_path"].exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux administrative startup")
@pytest.mark.parametrize("record", ["valid", "malformed", "directory", "dangling"])
def test_startup_refuses_every_incomplete_marker_without_loading_runtime(tmp_path, monkeypatch, record):
    import executor_birth_admin_preflight as preflight

    root = tmp_path / "retention-maintenance-v1"
    root.mkdir()
    marker = root / "active.json"
    if record == "directory":
        marker.mkdir()
    elif record == "dangling":
        marker.symlink_to(root / "absent")
    else:
        marker.write_bytes(b"{}" if record == "malformed" else canonical({"run_id": RUN}))
    checked = []
    monkeypatch.setattr(preflight, "RETENTION_MAINTENANCE_ROOT_V1", root)
    monkeypatch.setattr(preflight, "RETENTION_MAINTENANCE_ACTIVE_V1", marker)
    monkeypatch.setattr(preflight, "_require_safe_directory_chain_v1",
                        lambda path, **_: checked.append(path))
    with pytest.raises(preflight.PreflightError, match="retention maintenance incomplete"):
        preflight._require_no_retention_maintenance_v1()
    assert checked == [preflight.OWNERSHIP_ROOT, root]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux administrative startup")
def test_startup_optional_retention_never_creates_state(tmp_path, monkeypatch):
    import executor_birth_admin_preflight as preflight

    root = tmp_path / "never-provisioned"
    monkeypatch.setattr(preflight, "RETENTION_MAINTENANCE_ROOT_V1", root)
    monkeypatch.setattr(preflight, "RETENTION_MAINTENANCE_ACTIVE_V1", root / "active.json")
    monkeypatch.setattr(preflight, "_require_safe_directory_chain_v1", lambda *_, **__: None)
    preflight._require_no_retention_maintenance_v1()
    assert not root.exists()
    root.mkdir()
    preflight._require_no_retention_maintenance_v1()
    assert list(root.iterdir()) == []
