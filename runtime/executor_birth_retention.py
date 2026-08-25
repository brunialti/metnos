"""Inactive RM-0008 F6 reachability collector.

The store is deliberately independent from the productive contract store.  It
models ownership of Birth artefacts, performs a persistent mark pass and a
separate version-checked sweep, and leaves a small authenticated tombstone for
every collected object.  Nothing in this module can issue an admission receipt
or make a historical generation selectable.
"""
from __future__ import annotations

import base64
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,255}$")
_TS = "%Y-%m-%dT%H:%M:%SZ"


class RetentionError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


class NodeType(str, Enum):
    GENERATION = "generation"
    RETIREMENT = "retirement"
    BIRTH_REPORT = "birth_report"
    ADMISSION_RECEIPT = "admission_receipt"
    PRODUCER_RECEIPT = "producer_receipt"
    MINIMAL_RECEIPT = "minimal_receipt"
    CANDIDATE_COPY = "candidate_copy"
    PROPOSAL = "proposal"
    BLOB = "blob"
    EVIDENCE = "evidence"
    APPROVAL = "approval"
    FEEDBACK = "feedback"
    REVISION = "revision"
    EPOCH = "epoch"
    AUDIT_SEGMENT = "audit_segment"
    JOB = "job"


class EdgeType(str, Enum):
    SELECTS = "selects"
    PREDECESSOR = "predecessor"
    ADMITS = "admits"
    PROVENANCE = "provenance"
    EVIDENCE = "evidence"
    APPROVAL = "approval"
    EXECUTION = "execution"
    REVISION = "revision"
    REPAIR = "repair"
    ROLLBACK_DESTINATION = "rollback_destination"
    AUDIT_REFERENCE = "audit_reference"


class RootKind(str, Enum):
    CURRENT_POINTER = "current_pointer"
    RETIREMENT_PREDECESSOR = "retirement_predecessor"
    ADMITTED_ROLLBACKABLE = "admitted_rollbackable"
    OPEN_FEEDBACK = "open_feedback"
    OPEN_REVISION = "open_revision"
    OPEN_APPROVAL = "open_approval"
    OPEN_AUDIT = "open_audit"
    IN_PROGRESS_JOB = "in_progress_job"
    CURRENT_EPOCH = "current_epoch"
    UNEXPIRED_WINDOW = "unexpired_window"
    LEGAL_HOLD = "legal_hold"


class NodeState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    DELETED = "deleted"


class EdgeState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class CandidateStatus(str, Enum):
    MARKED = "marked"
    DELETING = "deleting"
    DELETED = "deleted"
    REFERENCED = "referenced"
    WINDOW_OPEN = "window_open"
    STATE_CHANGED = "state_changed"

    @property
    def error_code(self) -> str | None:
        """Return the normative public error for a preserved candidate."""
        return {
            CandidateStatus.REFERENCED: "retention_referenced",
            CandidateStatus.WINDOW_OPEN: "retention_window_open",
            CandidateStatus.STATE_CHANGED: "retention_state_changed",
        }.get(self)


@dataclass(frozen=True, slots=True)
class NodeKey:
    node_type: NodeType
    node_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.node_type, NodeType) or not _ID.fullmatch(self.node_id):
            raise RetentionError("retention_invalid", "node key")


@dataclass(frozen=True, slots=True)
class SweepResult:
    deleted: tuple[NodeKey, ...]
    preserved: tuple[tuple[NodeKey, CandidateStatus], ...]

    @property
    def error_codes(self) -> tuple[tuple[NodeKey, str], ...]:
        """Expose only the closed normative errors for preserved objects."""
        result: list[tuple[NodeKey, str]] = []
        for key, status in self.preserved:
            code = status.error_code
            if code is None:
                raise RetentionError("retention_partial", "invalid preserved status")
            result.append((key, code))
        return tuple(result)


_GENERATION_GUARD_SEAL = object()


@dataclass(frozen=True, slots=True, init=False)
class GenerationDeletionGuard:
    """Collector-issued observation, never an owner deletion authority.

    The future productive owner must revalidate all four facts against its own
    authoritative store.  Requiring the module-private seal prevents an
    embedding caller from manufacturing this observation through the public
    constructor and accidentally treating booleans as authority.
    """
    noncurrent: bool
    no_retirement_requirement: bool
    not_rollbackable: bool
    unreferenced: bool
    observed_version: int

    def __init__(self, *, noncurrent: bool, no_retirement_requirement: bool,
                 not_rollbackable: bool, unreferenced: bool,
                 observed_version: int, _seal: object) -> None:
        if _seal is not _GENERATION_GUARD_SEAL:
            raise RetentionError("retention_invalid", "generation guard seal")
        if (noncurrent, no_retirement_requirement, not_rollbackable,
                unreferenced) != (True, True, True, True):
            raise RetentionError("retention_invalid", "generation guard facts")
        if isinstance(observed_version, bool) or not isinstance(observed_version, int) \
                or observed_version < 1:
            raise RetentionError("retention_invalid", "generation guard version")
        object.__setattr__(self, "noncurrent", True)
        object.__setattr__(self, "no_retirement_requirement", True)
        object.__setattr__(self, "not_rollbackable", True)
        object.__setattr__(self, "unreferenced", True)
        object.__setattr__(self, "observed_version", observed_version)


def _generation_deletion_guard(observed_version: int) -> GenerationDeletionGuard:
    return GenerationDeletionGuard(
        noncurrent=True, no_retirement_requirement=True, not_rollbackable=True,
        unreferenced=True, observed_version=observed_version,
        _seal=_GENERATION_GUARD_SEAL,
    )


_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS retention_meta (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  graph_version INTEGER NOT NULL, root_version INTEGER NOT NULL
);
INSERT OR IGNORE INTO retention_meta VALUES(1,0,0);
CREATE TABLE IF NOT EXISTS retention_nodes (
  node_type TEXT NOT NULL, node_id TEXT NOT NULL,
  object_version INTEGER NOT NULL CHECK(object_version>=1),
  state TEXT NOT NULL CHECK(state IN ('open','closed','deleted')),
  created_at TEXT NOT NULL, closed_at TEXT, eligible_after TEXT,
  PRIMARY KEY(node_type,node_id)
);
CREATE TABLE IF NOT EXISTS retention_edges (
  source_type TEXT NOT NULL, source_id TEXT NOT NULL, edge_type TEXT NOT NULL,
  target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('open','closed')),
  edge_version INTEGER NOT NULL CHECK(edge_version>=1),
  created_at TEXT NOT NULL, closed_at TEXT,
  PRIMARY KEY(source_type,source_id,edge_type,target_type,target_id),
  FOREIGN KEY(source_type,source_id) REFERENCES retention_nodes(node_type,node_id),
  FOREIGN KEY(target_type,target_id) REFERENCES retention_nodes(node_type,node_id)
);
CREATE TABLE IF NOT EXISTS retention_roots (
  root_kind TEXT NOT NULL, node_type TEXT NOT NULL, node_id TEXT NOT NULL,
  root_version INTEGER NOT NULL CHECK(root_version>=1),
  PRIMARY KEY(root_kind,node_type,node_id),
  FOREIGN KEY(node_type,node_id) REFERENCES retention_nodes(node_type,node_id)
);
CREATE TABLE IF NOT EXISTS retention_runs (
  run_id TEXT PRIMARY KEY, graph_version INTEGER NOT NULL,
  root_version INTEGER NOT NULL, started_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('marked','complete','partial'))
);
CREATE TABLE IF NOT EXISTS retention_candidates (
  run_id TEXT NOT NULL, node_type TEXT NOT NULL, node_id TEXT NOT NULL,
  observed_version INTEGER NOT NULL, eligible_after TEXT NOT NULL,
  status TEXT NOT NULL,
  PRIMARY KEY(run_id,node_type,node_id),
  FOREIGN KEY(run_id) REFERENCES retention_runs(run_id)
);
CREATE TABLE IF NOT EXISTS retention_receipts (
  run_id TEXT NOT NULL, node_type TEXT NOT NULL, node_id TEXT NOT NULL,
  object_version INTEGER NOT NULL, deleted_at TEXT NOT NULL,
  authentication TEXT NOT NULL,
  PRIMARY KEY(node_type,node_id)
);
CREATE TRIGGER IF NOT EXISTS retention_no_root_while_deleting
BEFORE INSERT ON retention_roots WHEN EXISTS (
  SELECT 1 FROM retention_candidates c
  WHERE c.node_type=NEW.node_type AND c.node_id=NEW.node_id AND c.status='deleting'
) BEGIN SELECT RAISE(ABORT,'retention_deleting'); END;
CREATE TRIGGER IF NOT EXISTS retention_no_edge_while_deleting
BEFORE INSERT ON retention_edges WHEN EXISTS (
  SELECT 1 FROM retention_candidates c
  WHERE c.status='deleting' AND (
    (c.node_type=NEW.source_type AND c.node_id=NEW.source_id) OR
    (c.node_type=NEW.target_type AND c.node_id=NEW.target_id)
  )
) BEGIN SELECT RAISE(ABORT,'retention_deleting'); END;
"""

_SCHEMA_V2_ADDITION = """
CREATE TABLE IF NOT EXISTS retention_outbox (
  event_id TEXT PRIMARY KEY,
  event_version INTEGER NOT NULL CHECK(event_version=1),
  node_type TEXT NOT NULL,
  node_id TEXT NOT NULL,
  event_kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','applying','applied')),
  created_at TEXT NOT NULL,
  applied_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
  last_error TEXT
);
"""
_SCHEMA = _SCHEMA_V1 + _SCHEMA_V2_ADDITION

_SCHEMA_VERSION = 2
_SCHEMA_TABLE_COLUMNS = {
    "retention_meta": ("singleton", "graph_version", "root_version"),
    "retention_nodes": ("node_type", "node_id", "object_version", "state",
                        "created_at", "closed_at", "eligible_after"),
    "retention_edges": ("source_type", "source_id", "edge_type", "target_type",
                        "target_id", "state", "edge_version", "created_at", "closed_at"),
    "retention_roots": ("root_kind", "node_type", "node_id", "root_version"),
    "retention_runs": ("run_id", "graph_version", "root_version", "started_at", "state"),
    "retention_candidates": ("run_id", "node_type", "node_id", "observed_version",
                             "eligible_after", "status"),
    "retention_receipts": ("run_id", "node_type", "node_id", "object_version",
                           "deleted_at", "authentication"),
    "retention_outbox": ("event_id", "event_version", "node_type", "node_id",
                         "event_kind", "payload_json", "state", "created_at",
                         "applied_at", "attempts", "last_error"),
}
_SCHEMA_TRIGGERS = {
    "retention_no_root_while_deleting",
    "retention_no_edge_while_deleting",
}


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.strptime(value, _TS).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise RetentionError("retention_invalid", "timestamp") from exc
    if parsed.strftime(_TS) != value:
        raise RetentionError("retention_invalid", "timestamp")
    return value


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise RetentionError("retention_invalid", field)
    return value


def _retention_objects(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    return {(str(row[0]), str(row[1])) for row in connection.execute(
        "SELECT type,name FROM sqlite_master WHERE substr(name,1,10)='retention_' "
        "AND type IN ('table','index','trigger','view')"
    )}


def _normalized_schema_sql(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RetentionError("retention_schema_mismatch", "empty definition")
    return " ".join(value.split()).lower()


def _schema_fingerprint(connection: sqlite3.Connection) -> tuple[tuple[str, str, str], ...]:
    return tuple(sorted(
        (str(row[0]), str(row[1]), _normalized_schema_sql(row[2]))
        for row in connection.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE substr(name,1,10)='retention_' "
            "AND type IN ('table','index','trigger','view')"
        )
    ))


@lru_cache(maxsize=2)
def _expected_schema_fingerprint(
        version: int = _SCHEMA_VERSION) -> tuple[tuple[str, str, str], ...]:
    reference = sqlite3.connect(":memory:", isolation_level=None)
    try:
        reference.execute("PRAGMA foreign_keys=ON")
        if version == 1:
            reference.executescript(_SCHEMA_V1)
        elif version == _SCHEMA_VERSION:
            reference.executescript(_SCHEMA)
        else:
            raise RetentionError("retention_schema_version", str(version))
        return _schema_fingerprint(reference)
    finally:
        reference.close()


def _validate_schema(connection: sqlite3.Connection) -> None:
    expected = {("table", name) for name in _SCHEMA_TABLE_COLUMNS}
    expected |= {("trigger", name) for name in _SCHEMA_TRIGGERS}
    objects = _retention_objects(connection)
    if objects != expected:
        raise RetentionError("retention_schema_mismatch", "objects")
    if _schema_fingerprint(connection) != _expected_schema_fingerprint(
            _SCHEMA_VERSION):
        raise RetentionError("retention_schema_mismatch", "definition")
    for table, columns in _SCHEMA_TABLE_COLUMNS.items():
        actual = tuple(str(row[1]) for row in connection.execute(
            f'PRAGMA table_info("{table}")'))
        if actual != columns:
            raise RetentionError("retention_schema_mismatch", table)
    foreign_keys = tuple(connection.execute("PRAGMA foreign_key_check"))
    if foreign_keys:
        raise RetentionError("retention_schema_mismatch", "foreign keys")


def _initialize_or_validate_schema(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    objects = _retention_objects(connection)
    if version == 0:
        # F6 has never been productive.  Silently adopting an unversioned
        # lookalike database would turn arbitrary local rows into retention
        # authority, so V0 is migratable only when it contains no F6 objects.
        if objects:
            raise RetentionError("retention_schema_version", "unversioned")
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + _SCHEMA
            + f"\nPRAGMA user_version={_SCHEMA_VERSION};\nCOMMIT;"
        )
    elif version == 1:
        if _schema_fingerprint(connection) != _expected_schema_fingerprint(1):
            raise RetentionError(
                "retention_schema_mismatch", "v1 migration source")
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + _SCHEMA_V2_ADDITION
            + f"\nPRAGMA user_version={_SCHEMA_VERSION};\nCOMMIT;"
        )
    elif version != _SCHEMA_VERSION:
        raise RetentionError("retention_schema_version", str(version))
    _validate_schema(connection)


def _open(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=FULL")
    try:
        _initialize_or_validate_schema(connection)
        return connection
    except Exception:
        connection.close()
        raise


def put_node(key: NodeKey, *, state: NodeState, created_at: str,
             eligible_after: str | None, db_path: Path) -> int:
    if not isinstance(state, NodeState) or state is NodeState.DELETED:
        raise RetentionError("retention_invalid", "node state")
    created = _timestamp(created_at)
    eligible = None if eligible_after is None else _timestamp(eligible_after)
    if state is NodeState.CLOSED and eligible is None:
        raise RetentionError("retention_invalid", "eligible_after")
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT object_version,state FROM retention_nodes WHERE node_type=? AND node_id=?",
            (key.node_type.value, key.node_id),
        ).fetchone()
        version = 1 if row is None else int(row["object_version"]) + 1
        if row is not None and row["state"] == NodeState.DELETED.value:
            raise RetentionError("retention_state_changed", "deleted tombstone")
        deleting = connection.execute(
            "SELECT 1 FROM retention_candidates WHERE node_type=? AND node_id=? "
            "AND status='deleting' LIMIT 1",
            (key.node_type.value, key.node_id),
        ).fetchone()
        if deleting is not None:
            raise RetentionError("retention_state_changed", "object deleting")
        connection.execute(
            "INSERT INTO retention_nodes VALUES(?,?,?,?,?,?,?) ON CONFLICT(node_type,node_id) "
            "DO UPDATE SET object_version=excluded.object_version,state=excluded.state,"
            "closed_at=excluded.closed_at,eligible_after=excluded.eligible_after",
            (key.node_type.value, key.node_id, version, state.value, created,
             created if state is NodeState.CLOSED else None, eligible),
        )
        connection.execute("UPDATE retention_meta SET graph_version=graph_version+1 WHERE singleton=1")
        connection.commit()
        return version
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def add_edge(source: NodeKey, target: NodeKey, *, edge_type: EdgeType,
             state: EdgeState, created_at: str, db_path: Path) -> None:
    created = _timestamp(created_at)
    if not isinstance(edge_type, EdgeType) or not isinstance(state, EdgeState):
        raise RetentionError("retention_invalid", "edge state")
    edge = edge_type.value
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for key in (source, target):
            row = connection.execute(
                "SELECT state FROM retention_nodes WHERE node_type=? AND node_id=?",
                (key.node_type.value, key.node_id),
            ).fetchone()
            if row is None or row["state"] == NodeState.DELETED.value:
                raise RetentionError("retention_referenced", "missing endpoint")
        connection.execute(
            "INSERT INTO retention_edges VALUES(?,?,?,?,?,?,?,?,?)",
            (source.node_type.value, source.node_id, edge, target.node_type.value,
             target.node_id, state.value, 1, created,
             created if state is EdgeState.CLOSED else None),
        )
        connection.execute("UPDATE retention_meta SET graph_version=graph_version+1 WHERE singleton=1")
        connection.commit()
    except sqlite3.IntegrityError as exc:
        if "retention_deleting" in str(exc):
            raise RetentionError("retention_state_changed", "object deleting") from exc
        raise RetentionError("retention_state_changed", "edge conflict") from exc
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def add_root(key: NodeKey, *, root_kind: RootKind, db_path: Path) -> None:
    if not isinstance(root_kind, RootKind):
        raise RetentionError("retention_invalid", "root_kind")
    kind = root_kind.value
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT state FROM retention_nodes WHERE node_type=? AND node_id=?",
            (key.node_type.value, key.node_id),
        ).fetchone()
        if row is None or row["state"] == NodeState.DELETED.value:
            raise RetentionError("retention_referenced", "missing root")
        meta = connection.execute("SELECT root_version FROM retention_meta WHERE singleton=1").fetchone()
        version = int(meta[0]) + 1
        try:
            connection.execute("INSERT OR REPLACE INTO retention_roots VALUES(?,?,?,?)",
                               (kind, key.node_type.value, key.node_id, version))
        except sqlite3.IntegrityError as exc:
            if "retention_deleting" in str(exc):
                raise RetentionError("retention_state_changed", "object deleting") from exc
            raise
        connection.execute("UPDATE retention_meta SET root_version=? WHERE singleton=1", (version,))
        connection.commit()
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def remove_root(key: NodeKey, *, root_kind: RootKind, db_path: Path) -> None:
    if not isinstance(root_kind, RootKind):
        raise RetentionError("retention_invalid", "root_kind")
    kind = root_kind.value
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        deleted = connection.execute(
            "DELETE FROM retention_roots WHERE root_kind=? AND node_type=? AND node_id=?",
            (kind, key.node_type.value, key.node_id),
        )
        if deleted.rowcount != 1:
            raise RetentionError("retention_state_changed", "root absent")
        connection.execute(
            "UPDATE retention_meta SET root_version=root_version+1 WHERE singleton=1"
        )
        connection.commit()
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def close_edge(source: NodeKey, target: NodeKey, *, edge_type: EdgeType,
               closed_at: str, db_path: Path) -> None:
    if not isinstance(edge_type, EdgeType):
        raise RetentionError("retention_invalid", "edge_type")
    edge, closed = edge_type.value, _timestamp(closed_at)
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute(
            "UPDATE retention_edges SET state='closed',edge_version=edge_version+1,closed_at=? "
            "WHERE source_type=? AND source_id=? AND edge_type=? AND target_type=? "
            "AND target_id=? AND state='open'",
            (closed, source.node_type.value, source.node_id, edge,
             target.node_type.value, target.node_id),
        )
        if changed.rowcount != 1:
            raise RetentionError("retention_state_changed", "edge not open")
        connection.execute("UPDATE retention_meta SET graph_version=graph_version+1 WHERE singleton=1")
        connection.commit()
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def _reachable(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    # Until every durable owner event has been reconciled, absence of a graph
    # edge or root is not evidence of unreachability.  Treat the whole live
    # graph as rooted; this deliberately trades collection progress for safety.
    pending = connection.execute(
        "SELECT 1 FROM retention_outbox WHERE state IN ('pending','applying') LIMIT 1"
    ).fetchone()
    if pending is not None:
        return {(row[0], row[1]) for row in connection.execute(
            "SELECT node_type,node_id FROM retention_nodes WHERE state!='deleted'"
        )}
    # Open nodes and both endpoints of open references are conservative roots.
    roots = {(row[0], row[1]) for row in connection.execute(
        "SELECT node_type,node_id FROM retention_roots UNION "
        "SELECT node_type,node_id FROM retention_nodes WHERE state='open' UNION "
        "SELECT source_type,source_id FROM retention_edges WHERE state='open' UNION "
        "SELECT target_type,target_id FROM retention_edges WHERE state='open'"
    )}
    reached, frontier = set(roots), list(roots)
    while frontier:
        source = frontier.pop()
        for row in connection.execute(
            "SELECT target_type,target_id FROM retention_edges WHERE source_type=? AND source_id=?",
            source,
        ):
            target = (row[0], row[1])
            if target not in reached:
                reached.add(target)
                frontier.append(target)
    return reached


def mark(*, run_id: str, observed_at: str, db_path: Path) -> tuple[NodeKey, ...]:
    run, now = _text(run_id, "run_id"), _timestamp(observed_at)
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        meta = connection.execute(
            "SELECT graph_version,root_version FROM retention_meta WHERE singleton=1"
        ).fetchone()
        connection.execute("INSERT INTO retention_runs VALUES(?,?,?,?,?)",
                           (run, meta[0], meta[1], now, "marked"))
        reachable = _reachable(connection)
        candidates: list[NodeKey] = []
        for row in connection.execute(
            "SELECT node_type,node_id,object_version,eligible_after FROM retention_nodes "
            "WHERE state='closed' AND eligible_after<=? ORDER BY node_type,node_id", (now,)
        ):
            identity = (row[0], row[1])
            if identity in reachable:
                continue
            key = NodeKey(NodeType(row[0]), row[1])
            candidates.append(key)
            connection.execute("INSERT INTO retention_candidates VALUES(?,?,?,?,?,?)",
                               (run, row[0], row[1], row[2], row[3], CandidateStatus.MARKED.value))
        connection.commit()
        return tuple(candidates)
    except sqlite3.IntegrityError as exc:
        raise RetentionError("retention_state_changed", "run conflict") from exc
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


_RECEIPT_DOMAIN = b"metnos.executor-birth.retention-receipt/v1\0"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def _receipt_payload(key: NodeKey, run_id: str, version: int, deleted_at: str) -> bytes:
    return _canonical_json({"deleted_at": deleted_at, "node_id": key.node_id,
                            "node_type": key.node_type.value,
                            "object_version": version, "run_id": run_id,
                            "schema_version": 1}).encode("ascii")


def _receipt(key: NodeKey, run_id: str, version: int, deleted_at: str,
             key_id: str, private_key: Ed25519PrivateKey) -> str:
    if not isinstance(private_key, Ed25519PrivateKey):
        raise RetentionError("retention_invalid", "receipt key")
    identity = _text(key_id, "receipt key id")
    signature = private_key.sign(_RECEIPT_DOMAIN + _receipt_payload(
        key, run_id, version, deleted_at))
    return _canonical_json({
        "algorithm": "ed25519", "key_id": identity, "schema_version": 1,
        "signature": base64.b64encode(signature).decode("ascii"),
    })


def _strict_json_object(raw: str) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in items:
            if name in result:
                raise ValueError("duplicate key")
            result[name] = value
        return result
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1024:
        raise ValueError("authentication envelope")
    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict) or _canonical_json(value) != raw:
        raise ValueError("noncanonical authentication envelope")
    return value


def verify_minimal_receipt(*, key: NodeKey, run_id: str, object_version: int,
                           deleted_at: str, authentication: str,
                           public_keys: Mapping[str, Ed25519PublicKey]) -> str:
    """Verify an authenticated, deliberately non-personal deletion receipt."""
    try:
        envelope = _strict_json_object(authentication)
        if set(envelope) != {"algorithm", "key_id", "schema_version", "signature"}:
            raise ValueError
        algorithm, key_id = envelope["algorithm"], envelope["key_id"]
        encoded = envelope["signature"]
        if (algorithm != "ed25519" or envelope["schema_version"] != 1
                or isinstance(envelope["schema_version"], bool)
                or not isinstance(key_id, str) or not _ID.fullmatch(key_id)
                or not isinstance(encoded, str)):
            raise ValueError
        signature = base64.b64decode(encoded, validate=True)
        if len(signature) != 64 or base64.b64encode(signature).decode("ascii") != encoded:
            raise ValueError
        public_key = public_keys[key_id]
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError
        public_key.verify(signature, _RECEIPT_DOMAIN + _receipt_payload(
            key, _text(run_id, "run_id"), object_version, _timestamp(deleted_at)))
    except (InvalidSignature, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RetentionError("retention_invalid", "minimal receipt authentication") from exc
    return key_id


def sweep(*, run_id: str, observed_at: str, receipt_key_id: str,
          receipt_private_key: Ed25519PrivateKey, db_path: Path,
          receipt_public_keys: Mapping[str, Ed25519PublicKey],
          delete_object: Callable[[NodeKey, GenerationDeletionGuard | None], None] | None = None,
          before_each: Callable[[NodeKey], None] | None = None) -> SweepResult:
    """Second pass: re-mark under a write lock and CAS every candidate."""
    run, now = _text(run_id, "run_id"), _timestamp(observed_at)
    active_key_id = _text(receipt_key_id, "receipt key id")
    if delete_object is None:
        raise RetentionError("retention_partial", "delete callback absent")
    try:
        registered_active = receipt_public_keys[active_key_id]
        active_public = receipt_private_key.public_key()
        registered_raw = registered_active.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        active_raw = active_public.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise RetentionError("retention_invalid", "receipt key registry") from exc
    if registered_raw != active_raw:
        raise RetentionError("retention_invalid", "active receipt key mismatch")
    deleted: list[NodeKey] = []
    preserved: list[tuple[NodeKey, CandidateStatus]] = []
    # A callback is a test/embedding seam and runs before acquiring the lock so
    # a concurrent root or edge can win the race normally.
    probe = _open(db_path)
    try:
        rows = probe.execute(
            "SELECT node_type,node_id FROM retention_candidates WHERE run_id=? "
            "AND status IN ('marked','deleting') "
            "ORDER BY node_type,node_id", (run,),
        ).fetchall()
        pending = {(row[0], row[1]) for row in rows}
        ordered: list[tuple[str, str]] = []
        # A source owns/refers to its targets, therefore targets are the leaves
        # to tombstone first.  Strongly connected garbage has no leaf; its
        # deterministic first member breaks the cycle without affecting a live
        # reference (reachability is checked again under the write lock).
        while pending:
            sources_with_targets = {(row[0], row[1]) for row in probe.execute(
                "SELECT source_type,source_id,target_type,target_id FROM retention_edges"
            ) if (row[0], row[1]) in pending and (row[2], row[3]) in pending}
            leaves = sorted(pending - sources_with_targets)
            chosen = leaves if leaves else [min(pending)]
            ordered.extend(chosen)
            pending.difference_update(chosen)
    finally:
        probe.close()
    for candidate in ordered:
        key = NodeKey(NodeType(candidate[0]), candidate[1])
        if before_each is not None:
            before_each(key)
        connection = _open(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            marked = connection.execute(
                "SELECT observed_version,eligible_after,status FROM retention_candidates "
                "WHERE run_id=? AND node_type=? AND node_id=?",
                (run, key.node_type.value, key.node_id),
            ).fetchone()
            node = connection.execute(
                "SELECT object_version,state,eligible_after FROM retention_nodes "
                "WHERE node_type=? AND node_id=?", (key.node_type.value, key.node_id),
            ).fetchone()
            status = CandidateStatus.DELETED
            if marked is None or node is None or marked["status"] not in {
                    CandidateStatus.MARKED.value, CandidateStatus.DELETING.value}:
                status = CandidateStatus.STATE_CHANGED
            elif int(node["object_version"]) != int(marked["observed_version"]) or node["state"] != NodeState.CLOSED.value:
                status = CandidateStatus.STATE_CHANGED
            elif node["eligible_after"] > now:
                status = CandidateStatus.WINDOW_OPEN
            elif (key.node_type.value, key.node_id) in _reachable(connection):
                status = CandidateStatus.REFERENCED
            elif key.node_type is NodeType.GENERATION and connection.execute(
                    "SELECT 1 FROM retention_edges WHERE (source_type=? AND source_id=?) "
                    "OR (target_type=? AND target_id=?) LIMIT 1",
                    (key.node_type.value, key.node_id,
                     key.node_type.value, key.node_id)).fetchone() is not None:
                # Generations have a stricter rule than generic graph leaves:
                # no closed reference may be removed merely as part of sweep.
                status = CandidateStatus.REFERENCED
            if status is CandidateStatus.DELETED:
                # Persist the signed receipt and per-object intent first.  The
                # triggers above freeze references to this object, so a crash
                # can safely resume the idempotent callback.
                existing_receipt = connection.execute(
                    "SELECT run_id,object_version,deleted_at,authentication "
                    "FROM retention_receipts WHERE node_type=? AND node_id=?",
                    (key.node_type.value, key.node_id),
                ).fetchone()
                if existing_receipt is None:
                    auth = _receipt(key, run, int(node["object_version"]), now,
                                    receipt_key_id, receipt_private_key)
                    connection.execute("INSERT INTO retention_receipts VALUES(?,?,?,?,?,?)",
                                       (run, key.node_type.value, key.node_id,
                                        node["object_version"], now, auth))
                elif (existing_receipt["run_id"] != run
                      or int(existing_receipt["object_version"]) != int(node["object_version"])):
                    raise RetentionError("retention_state_changed", "receipt conflict")
                else:
                    verify_minimal_receipt(
                        key=key, run_id=run,
                        object_version=int(existing_receipt["object_version"]),
                        deleted_at=existing_receipt["deleted_at"],
                        authentication=existing_receipt["authentication"],
                        public_keys=receipt_public_keys,
                    )
                connection.execute(
                    "UPDATE retention_candidates SET status='deleting' WHERE run_id=? "
                    "AND node_type=? AND node_id=?",
                    (run, key.node_type.value, key.node_id),
                )
                connection.commit()

                guard: GenerationDeletionGuard | None = None
                if key.node_type is NodeType.GENERATION:
                    roots = {row[0] for row in connection.execute(
                        "SELECT root_kind FROM retention_roots WHERE node_type=? AND node_id=?",
                        (key.node_type.value, key.node_id))}
                    references = connection.execute(
                        "SELECT 1 FROM retention_edges WHERE (source_type=? AND source_id=?) "
                        "OR (target_type=? AND target_id=?) LIMIT 1",
                        (key.node_type.value, key.node_id,
                         key.node_type.value, key.node_id),
                    ).fetchone()
                    forbidden = {
                        RootKind.CURRENT_POINTER.value,
                        RootKind.RETIREMENT_PREDECESSOR.value,
                        RootKind.ADMITTED_ROLLBACKABLE.value,
                    }
                    if roots & forbidden or references is not None:
                        raise RetentionError("retention_referenced", "generation safeguard")
                    guard = _generation_deletion_guard(
                        int(marked["observed_version"]))
                try:
                    delete_object(key, guard)
                except Exception as exc:
                    raise RetentionError("retention_partial", "delete callback failed") from exc

                connection.execute("BEGIN IMMEDIATE")
                changed = connection.execute(
                    "UPDATE retention_nodes SET state='deleted',object_version=object_version+1 "
                    "WHERE node_type=? AND node_id=? AND object_version=? AND state='closed'",
                    (key.node_type.value, key.node_id, marked["observed_version"]),
                )
                if changed.rowcount != 1:
                    raise RetentionError("retention_state_changed")
                connection.execute(
                    "DELETE FROM retention_edges WHERE (source_type=? AND source_id=?) "
                    "OR (target_type=? AND target_id=?)",
                    (key.node_type.value, key.node_id, key.node_type.value, key.node_id),
                )
                connection.execute(
                    "UPDATE retention_candidates SET status='deleted' WHERE run_id=? "
                    "AND node_type=? AND node_id=?",
                    (run, key.node_type.value, key.node_id),
                )
                connection.execute(
                    "UPDATE retention_meta SET graph_version=graph_version+1 WHERE singleton=1"
                )
                connection.commit()
                deleted.append(key)
            else:
                preserved.append((key, status))
                connection.execute(
                    "UPDATE retention_candidates SET status=? WHERE run_id=? AND node_type=? AND node_id=?",
                    (status.value, run, key.node_type.value, key.node_id),
                )
                connection.commit()
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
    connection = _open(db_path)
    try:
        unfinished = connection.execute(
            "SELECT 1 FROM retention_candidates WHERE run_id=? AND status!='deleted' LIMIT 1",
            (run,),
        ).fetchone()
        final_state = "partial" if unfinished is not None else "complete"
        connection.execute("UPDATE retention_runs SET state=? WHERE run_id=?", (final_state, run))
    finally:
        connection.close()
    return SweepResult(tuple(deleted), tuple(preserved))


def diagnostic_has_admission_edge(key: NodeKey, *, db_path: Path) -> bool:
    """Report a graph edge without making any selection or authority claim.

    Retention edges are bookkeeping, not authenticated RM-0007 or Birth
    records.  Productive rollbackability must be established by the owning
    stores and must never use this diagnostic as a gate.
    """
    if key.node_type is not NodeType.GENERATION:
        raise RetentionError("retention_invalid", "generation key")
    connection = _open(db_path)
    try:
        row = connection.execute(
            "SELECT 1 FROM retention_edges e JOIN retention_nodes r "
            "ON r.node_type=e.source_type AND r.node_id=e.source_id "
            "WHERE e.target_type=? AND e.target_id=? AND e.edge_type='admits' "
            "AND e.source_type='admission_receipt' AND r.state!='deleted' LIMIT 1",
            (key.node_type.value, key.node_id),
        ).fetchone()
        return row is not None
    finally:
        connection.close()
