"""F6 maintenance journal and owner recovery, with no autonomous collection.

The installed coordinator must hold deployment, startup and owner exclusion
throughout this operation. A graph is a disposable projection: physical owners
provide identities, versions and effects. An intent is committed and its signed
receipt read back before an effect in another database or the filesystem.

This module contains no executable entry point and grants no deletion authority
to a runtime caller. Administrative integration supplies the closed owner set.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from itertools import groupby
import os
from pathlib import Path
import sqlite3
import time
from typing import Callable, Mapping, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_authority_files import _directory_metadata, _read_regular
from executor_birth_canonical import decode_canonical_ascii_v1, encode_canonical_ascii_v1
from executor_birth_retention import (
    EdgeState, EdgeType, NodeKey, NodeState, NodeType, RetentionError, RootKind,
    _receipt, _timestamp, add_edge, add_root, mark, put_node, verify_minimal_receipt,
)
from install.birth_ownership_authority_provisioner import (
    _path_present, _sync_directory, _temporary_path, _write_exclusive,
)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(encode_canonical_ascii_v1(value)).hexdigest()


def _version(value: object) -> str:
    if (type(value) is not str or len(value) != 71 or not value.startswith("sha256:")
            or any(c not in "0123456789abcdef" for c in value[7:])):
        raise RetentionError("retention_invalid", "owner version")
    return value


@dataclass(frozen=True)
class ObjectIdentity:
    owner: str
    store: str
    node_type: str
    local_id: str
    contract: str | None = None

    def __post_init__(self):
        for value in (self.owner, self.store, self.local_id):
            if type(value) is not str or not value or len(value) > 4096 or "\0" in value:
                raise RetentionError("retention_invalid", "physical identity")
        if self.contract is not None and (
            type(self.contract) is not str or not self.contract
            or len(self.contract) > 4096 or "\0" in self.contract
        ):
            raise RetentionError("retention_invalid", "contract identity")
        NodeType(self.node_type)

    @property
    def key(self) -> NodeKey:
        return NodeKey(NodeType(self.node_type), _digest(asdict(self)))


@dataclass(frozen=True)
class OwnerObject:
    identity: ObjectIdentity
    version: str
    state: NodeState
    created_at: str
    eligible_after: str | None
    references: tuple[ObjectIdentity, ...] = ()
    roots: tuple[RootKind, ...] = ()

    def __post_init__(self):
        _version(self.version)
        _timestamp(self.created_at)
        if self.eligible_after is not None:
            _timestamp(self.eligible_after)
        if type(self.state) is not NodeState or self.state is NodeState.DELETED:
            raise RetentionError("retention_invalid", "owner state")
        if self.state is NodeState.CLOSED and self.eligible_after is None:
            raise RetentionError("retention_invalid", "closed owner window")
        if any(type(root) is not RootKind for root in self.roots):
            raise RetentionError("retention_invalid", "root")


class PhysicalOwner(Protocol):
    def version(self, identity: ObjectIdentity) -> str | None:
        """Read the exact physical version, or prove absence after deletion."""

    def delete(self, identity: ObjectIdentity, expected_version: str) -> None:
        """Recheck ownership/version, delete durably; absence is idempotent."""


def plan(
    objects: tuple[OwnerObject, ...], *, observed_owners: frozenset[str],
    required_owners: frozenset[str], observed_roots: frozenset[RootKind],
    holds: frozenset[str], graph_path: Path, run_id: str, observed_at: str,
) -> tuple[OwnerObject, ...]:
    """Project a complete stopped inventory through the existing reachability pass."""
    if not required_owners or observed_owners != required_owners:
        raise RetentionError("retention_inventory_incomplete", "owners")
    if observed_roots != frozenset(RootKind):
        raise RetentionError("retention_inventory_incomplete", "roots")
    if type(holds) is not frozenset:
        raise RetentionError("retention_inventory_incomplete", "legal holds")
    for hold in holds:
        _version(hold)
    if graph_path.exists() or graph_path.is_symlink():
        raise RetentionError("retention_invalid", "projection already exists")
    by_key = {obj.identity.key: obj for obj in objects}
    if (len(by_key) != len(objects)
            or any(obj.identity.owner not in observed_owners for obj in objects)):
        raise RetentionError("retention_inventory_incomplete", "duplicate or unknown owner")
    for obj in objects:
        if any(reference.key not in by_key for reference in obj.references):
            raise RetentionError("retention_inventory_incomplete", "unresolved reference")
    for obj in objects:
        key = obj.identity.key
        put_node(key, state=obj.state, created_at=obj.created_at,
                 eligible_after=obj.eligible_after, db_path=graph_path)
        for root in obj.roots:
            add_root(key, root_kind=root, db_path=graph_path)
        if key.node_id in holds:
            add_root(key, root_kind=RootKind.LEGAL_HOLD, db_path=graph_path)
    for obj in objects:
        for reference in obj.references:
            add_edge(obj.identity.key, reference.key, edge_type=EdgeType.PROVENANCE,
                     state=EdgeState.CLOSED, created_at=observed_at, db_path=graph_path)
    return tuple(by_key[key] for key in mark(
        run_id=run_id, observed_at=observed_at, db_path=graph_path,
    ))


def _receipt_key(identity: ObjectIdentity, version: str) -> NodeKey:
    # Bind the physical owner's full version, not the projection's counter.
    return NodeKey(NodeType(identity.node_type), _digest({
        "identity": asdict(identity), "owner_version": _version(version),
    }))


def _independent_groups(candidates: tuple[OwnerObject, ...]):
    """Pause only between connected sets, including cycles across stores.

    A partially deleted set must finish recovery before startup. Keeping the
    remaining half would leave physical references to objects just removed.
    Group size is bounded before its first effect; time is checked between
    groups because an in-flight physical deletion cannot be undone by a timer.
    """
    objects = {obj.identity: obj for obj in candidates}
    neighbours = {identity: set() for identity in objects}
    for obj in candidates:
        for reference in obj.references:
            if reference in objects:
                neighbours[obj.identity].add(reference)
                neighbours[reference].add(obj.identity)
    seen = set()
    for obj in candidates:
        if obj.identity in seen:
            continue
        pending, group = [obj.identity], []
        seen.add(obj.identity)
        while pending:
            identity = pending.pop()
            group.append(objects[identity])
            for reference in sorted(neighbours[identity], key=lambda item: item.key.node_id):
                if reference not in seen:
                    seen.add(reference)
                    pending.append(reference)
        yield tuple(group)


def _read_holds_file(root: Path, *, root_owned: bool) -> frozenset[str]:
    _directory_metadata(root, root_owned=root_owned)
    value = decode_canonical_ascii_v1(_read_regular(
        root / "holds.json", maximum=1024 * 1024, mode=0o600, root_owned=root_owned,
    ), maximum=1024 * 1024)
    if (type(value) is not dict or set(value) != {"schema_version", "holds"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["holds"]) is not list):
        raise RetentionError("retention_invalid", "hold register")
    values = tuple(_version(value) for value in value["holds"])
    if tuple(sorted(set(values))) != values:
        raise RetentionError("retention_invalid", "hold order")
    return frozenset(values)


def read_holds(root: Path, *, root_owned: bool) -> frozenset[str]:
    """Missing or partly changed administrative holds are never an empty set."""
    _directory_metadata(root, root_owned=root_owned)
    pending = root / "holds.next.json"
    if _path_present(pending) or _path_present(_temporary_path(pending)):
        raise RetentionError("retention_holds_recovery_required")
    return _read_holds_file(root, root_owned=root_owned)


def _update_holds(root: Path, *, root_owned: bool, holds: frozenset[str],
                  expected_version: str | None, require_exclusion, crash=None) -> str:
    """Explicit initialization or CAS replacement under the native admin lock.

    The installed caller owns the fixed root and exclusion. Pending bytes block
    collection, while the old register remains intact until atomic replacement.
    A retry after replacement synchronizes the directory before acknowledging it.
    This is not a runtime API or a source of object/deletion authority.
    """
    require_exclusion()
    _directory_metadata(root, root_owned=root_owned)
    if expected_version is not None:
        _version(expected_version)
    if type(holds) is not frozenset:
        raise RetentionError("retention_invalid", "holds must be an explicit set")
    for hold in holds:
        _version(hold)
    payload = encode_canonical_ascii_v1({"schema_version": 1, "holds": sorted(holds)})
    if len(payload) > 1024 * 1024:
        raise RetentionError("retention_invalid", "hold register size")
    active = root / "active.json"
    if _path_present(active) or _path_present(_temporary_path(active)):
        raise RetentionError("retention_holds_active_maintenance")
    path, pending = root / "holds.json", root / "holds.next.json"
    current = _read_holds_file(root, root_owned=root_owned) if _path_present(path) else None
    pending_exists = _path_present(pending) or _path_present(_temporary_path(pending))
    desired = _digest(sorted(holds))
    if current == holds and not pending_exists:
        _sync_directory(root)
        require_exclusion()
        return desired
    version = _digest(sorted(current)) if current is not None else None
    if version != expected_version:
        raise RetentionError("retention_holds_changed")
    # The already verified private writer recovers interrupted prefix/full writes.
    if not _path_present(pending):
        _write_exclusive(pending, payload, 0o600, root_owned=root_owned, crash=crash)
    if _read_regular(pending, maximum=1024 * 1024, mode=0o600,
                     root_owned=root_owned) != payload:
        raise RetentionError("retention_holds_recovery_required", "pending change differs")
    if _path_present(_temporary_path(pending)):
        raise RetentionError("retention_holds_recovery_required", "duplicate pending change")
    require_exclusion()
    if crash:
        crash("before_holds_replace")
    os.replace(pending, path)
    if crash:
        crash("after_holds_replace")
    _sync_directory(root)
    if read_holds(root, root_owned=root_owned) != holds:
        raise RetentionError("retention_holds_changed")
    require_exclusion()
    return desired


_SCHEMA = """
CREATE TABLE intents (
  position INTEGER PRIMARY KEY, identity BLOB NOT NULL, owner_version TEXT NOT NULL,
  authentication TEXT NOT NULL, recovery_group INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('intended','deleted','preserved'))
);
CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Maintenance:
    """One private administrative session; all effects re-observe exclusion.

    The caller retains the same locks even on completion. Only after catalog
    and recovery readback succeeds can ``finish`` remove the startup barrier.
    Exceptions leave the record and original receipts available for recovery.
    """

    def __init__(self, root: Path, *, root_owned: bool,
                 require_exclusion: Callable[[], None]):
        self.root, self.root_owned = root, root_owned
        self.require_exclusion = require_exclusion
        self.process = os.getpid()

    def _require(self):
        if self.process != os.getpid():
            raise RetentionError("retention_invalid", "foreign process")
        self.require_exclusion()
        _directory_metadata(self.root, root_owned=self.root_owned)

    def _active(self) -> dict:
        self._require()
        value = decode_canonical_ascii_v1(_read_regular(
            self.root / "active.json", maximum=4096, mode=0o600,
            root_owned=self.root_owned,
        ), maximum=4096)
        if (type(value) is not dict
                or set(value) != {"schema_version", "run_id", "started_at", "plan_digest"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise RetentionError("retention_invalid", "maintenance record")
        _version(value["run_id"])
        _version(value["plan_digest"])
        _timestamp(value["started_at"])
        return value

    def _database(self, run_id: str) -> sqlite3.Connection:
        path = self.root / (_version(run_id)[7:] + ".sqlite")
        # Reuse the no-link, ownership and exact-mode check before SQLite opens.
        _read_regular(path, maximum=64 * 1024 * 1024, mode=0o600,
                      root_owned=self.root_owned)
        db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            db.close()
            raise RetentionError("retention_invalid", "journal version")
        return db

    def _verified_rows(self, db: sqlite3.Connection, active: dict,
                       public_keys: Mapping) -> list[sqlite3.Row]:
        metadata = dict(db.execute("SELECT name,value FROM metadata"))
        if (set(metadata) != {"run_id", "started_at", "state", "holds"}
                or metadata["run_id"] != active["run_id"]
                or metadata["started_at"] != active["started_at"]
                or metadata["state"] not in ("intended", "complete")):
            raise RetentionError("retention_invalid", "journal binding")
        holds = read_holds(self.root, root_owned=self.root_owned)
        if metadata["holds"] != _digest(sorted(holds)):
            raise RetentionError("retention_state_changed", "hold register changed")
        rows = db.execute("SELECT * FROM intents ORDER BY position").fetchall()
        manifest = []
        previous_group = 0
        for position, row in enumerate(rows):
            if (row["position"] != position
                    or row["recovery_group"] not in (previous_group, previous_group + 1)
                    or (position == 0 and row["recovery_group"] != 0)
                    or row["status"] not in ("intended", "deleted", "preserved")
                    or (metadata["state"] == "complete" and row["status"] == "intended")):
                raise RetentionError("retention_invalid", "journal outcome")
            identity = ObjectIdentity(**decode_canonical_ascii_v1(
                bytes(row["identity"]), maximum=128 * 1024,
            ))
            verify_minimal_receipt(
                key=_receipt_key(identity, row["owner_version"]), run_id=active["run_id"],
                object_version=1, deleted_at=active["started_at"],
                authentication=row["authentication"], public_keys=public_keys,
            )
            manifest.append([position, asdict(identity), row["owner_version"],
                             row["authentication"], row["recovery_group"]])
            previous_group = row["recovery_group"]
        if active["plan_digest"] != _digest({"holds": metadata["holds"], "intents": manifest}):
            raise RetentionError("retention_invalid", "journal content changed")
        return rows

    @staticmethod
    def _owner(row: sqlite3.Row, owners: Mapping[str, PhysicalOwner]):
        identity = ObjectIdentity(**decode_canonical_ascii_v1(
            bytes(row["identity"]), maximum=128 * 1024,
        ))
        if identity.owner not in owners:
            raise RetentionError("retention_inventory_incomplete", "physical owner")
        owner = owners[identity.owner]
        current = owner.version(identity)
        if current is not None and current != row["owner_version"]:
            raise RetentionError("retention_state_changed", "physical owner version")
        if row["status"] == "deleted" and current is not None:
            raise RetentionError("retention_state_changed", "deleted object reappeared")
        if row["status"] == "preserved" and current is None:
            raise RetentionError("retention_state_changed", "preserved object missing")
        return identity, owner, current

    def begin(self, candidates: tuple[OwnerObject, ...], *, run_id: str,
              observed_at: str, key_id: str, private_key: Ed25519PrivateKey,
              public_keys: Mapping, owners: Mapping[str, PhysicalOwner],
              crash: Callable[[str], None] = lambda _: None) -> None:
        self._require()
        _version(run_id)
        _timestamp(observed_at)
        if (self.root / "active.json").exists() or (self.root / "active.json").is_symlink():
            raise RetentionError("retention_recovery_required", "active maintenance")
        # A fully verified register must exist even for an empty collection.
        holds = read_holds(self.root, root_owned=self.root_owned)
        if any(obj.identity.key.node_id in holds for obj in candidates):
            raise RetentionError("retention_state_changed", "legal hold")
        identities = [obj.identity.key for obj in candidates]
        if len(set(identities)) != len(identities):
            raise RetentionError("retention_invalid", "duplicate candidate")
        if any(obj.state is not NodeState.CLOSED or obj.eligible_after > observed_at
               or obj.roots for obj in candidates):
            raise RetentionError("retention_invalid", "candidate not eligible")
        manifest = []
        grouped = ((group_id, obj) for group_id, group in enumerate(_independent_groups(candidates))
                   for obj in group)
        for position, (group_id, obj) in enumerate(grouped):
            if (obj.identity.owner not in owners
                    or owners[obj.identity.owner].version(obj.identity) != obj.version):
                raise RetentionError("retention_state_changed", "initial physical owner")
            authentication = _receipt(_receipt_key(obj.identity, obj.version), run_id, 1,
                                      observed_at, key_id, private_key)
            verify_minimal_receipt(key=_receipt_key(obj.identity, obj.version), run_id=run_id,
                                   object_version=1, deleted_at=observed_at,
                                   authentication=authentication, public_keys=public_keys)
            manifest.append([position, asdict(obj.identity), obj.version, authentication, group_id])
        holds_digest = _digest(sorted(holds))
        active = {"schema_version": 1, "run_id": run_id, "started_at": observed_at,
                  "plan_digest": _digest({"holds": holds_digest, "intents": manifest})}
        # Complete the immutable intents before publishing the barrier. A crash
        # during preparation has no physical effects and cannot strand startup
        # behind an active record whose journal was never committed.
        path = self.root / (run_id[7:] + ".sqlite")
        if not path.exists() and not path.is_symlink():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            _sync_directory(self.root)
            crash("after_journal_create")
            db = sqlite3.connect(path)
            try:
                db.execute("PRAGMA synchronous=FULL")
                db.executescript(_SCHEMA)
                db.execute("PRAGMA user_version=1")
                with db:
                    db.executemany("INSERT INTO metadata VALUES(?,?)", (
                        ("run_id", run_id), ("started_at", observed_at),
                        ("state", "intended"), ("holds", holds_digest),
                    ))
                    for position, identity, version, authentication, group_id in manifest:
                        db.execute("INSERT INTO intents VALUES(?,?,?,?,?,'intended')",
                                   (position, encode_canonical_ascii_v1(identity),
                                    version, authentication, group_id))
                    crash("before_intents_commit")
            finally:
                db.close()
        _sync_directory(self.root)
        crash("after_intents")
        db = self._database(run_id)
        try:
            self._verified_rows(db, active, public_keys)
            if dict(db.execute("SELECT name,value FROM metadata"))["state"] != "intended":
                raise RetentionError("retention_invalid", "completed run")
            if any(row[0] != "intended" for row in db.execute("SELECT status FROM intents")):
                raise RetentionError("retention_invalid", "preparation already used")
        finally:
            db.close()
        self._require()
        _write_exclusive(self.root / "active.json", encode_canonical_ascii_v1(active), 0o600,
                         root_owned=self.root_owned, crash=crash)
        crash("after_marker")

    def resume(self, owners: Mapping[str, PhysicalOwner], *, public_keys: Mapping,
               max_objects: int, max_seconds: float,
               crash: Callable[[str], None] = lambda _: None) -> dict:
        if (type(max_objects) is not int or max_objects <= 0
                or type(max_seconds) not in (int, float) or not 0 < max_seconds <= 3600):
            raise RetentionError("retention_invalid", "maintenance budget")
        active = self._active()
        db = self._database(active["run_id"])
        started = time.monotonic()
        completed = 0
        try:
            rows = self._verified_rows(db, active, public_keys)
            # Discover a missing/changed owner before the first new effect.
            for row in rows:
                self._owner(row, owners)
            for _, group in groupby(rows, key=lambda row: row["recovery_group"]):
                pending = [row for row in group if row["status"] == "intended"]
                if not pending:
                    continue
                if completed + len(pending) > max_objects or time.monotonic() - started >= max_seconds:
                    break
                for row in pending:
                    self._require()
                    identity, owner, current = self._owner(row, owners)
                    # Absence can be observed after an interrupted unlink,
                    # before the owner's durability or container cleanup.
                    # Resume its idempotent operation under the original
                    # authenticated intent before acknowledging completion.
                    crash("before_effect")
                    self._require()
                    owner.delete(identity, row["owner_version"])
                    crash("after_effect")
                    if owner.version(identity) is not None:
                        raise RetentionError("retention_state_changed", "effect not observed")
                    with db:
                        db.execute("UPDATE intents SET status='deleted' WHERE position=?",
                                   (row["position"],))
                    completed += 1
                    crash("after_outcome")
            remaining = db.execute("SELECT COUNT(*) FROM intents WHERE status='intended'").fetchone()[0]
            return {"run_id": active["run_id"], "completed": completed,
                    "remaining": remaining, "elapsed_seconds": time.monotonic() - started}
        finally:
            db.close()

    def finish(self, owners: Mapping[str, PhysicalOwner], *, public_keys: Mapping,
               verify_recovery: Callable[[], None], preserve_remaining: bool = False,
               crash: Callable[[str], None] = lambda _: None) -> dict:
        active = self._active()
        db = self._database(active["run_id"])
        try:
            rows = self._verified_rows(db, active, public_keys)
            outcomes = []
            presence = {}
            for row in rows:
                self._require()
                _, _, current = self._owner(row, owners)
                presence.setdefault(row["recovery_group"], set()).add(current is not None)
                if row["status"] == "intended":
                    if current is not None and not preserve_remaining:
                        raise RetentionError("retention_recovery_required", "pending intents")
                    outcomes.append(("deleted" if current is None else "preserved", row["position"]))
            if any(len(states) != 1 for states in presence.values()):
                raise RetentionError("retention_recovery_required", "partially deleted reference group")
            # A bounded window may preserve untouched objects. They are reported
            # explicitly, never as deleted, and a later window inventories anew.
            with db:
                db.executemany("UPDATE intents SET status=? WHERE position=?", outcomes)
            crash("after_reconciliation")
            self._require()
            verify_recovery()
            self._require()
            self._verified_rows(db, active, public_keys)
            with db:
                db.execute("UPDATE metadata SET value='complete' WHERE name='state'")
            crash("after_completion")
            # Keep journal and public verification key. Only the active startup
            # barrier goes away, after owner/catalog recovery readback succeeds.
            (self.root / "active.json").unlink()
            _sync_directory(self.root)
            counts = dict(db.execute("SELECT status,COUNT(*) FROM intents GROUP BY status"))
            return {"run_id": active["run_id"], "deleted": counts.get("deleted", 0),
                    "preserved": counts.get("preserved", 0)}
        finally:
            db.close()
