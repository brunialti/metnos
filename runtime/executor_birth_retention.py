"""Inactive RM-0008 F6 reachability collector.

The store is deliberately independent from the productive contract store.  It
models ownership of Birth artefacts, performs a persistent mark pass and a
separate version-checked sweep, and leaves a small authenticated tombstone for
every collected object.  Nothing in this module can issue an admission receipt
or make a historical generation selectable.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,255}$")
_TS = "%Y-%m-%dT%H:%M:%SZ"


class RetentionError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


class NodeType(str, Enum):
    GENERATION = "generation"
    BIRTH_REPORT = "birth_report"
    ADMISSION_RECEIPT = "admission_receipt"
    PRODUCER_RECEIPT = "producer_receipt"
    REVIEW = "review"
    EVIDENCE = "evidence"
    APPROVAL = "approval"
    EPOCH = "epoch"
    AUDIT = "audit"


class NodeState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    DELETED = "deleted"


class EdgeState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class CandidateStatus(str, Enum):
    MARKED = "marked"
    DELETED = "deleted"
    REFERENCED = "referenced"
    WINDOW_OPEN = "window_open"
    STATE_CHANGED = "state_changed"


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


_SCHEMA = """
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
"""


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


def _open(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(_SCHEMA)
    return connection


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


def add_edge(source: NodeKey, target: NodeKey, *, edge_type: str,
             state: EdgeState, created_at: str, db_path: Path) -> None:
    edge, created = _text(edge_type, "edge_type"), _timestamp(created_at)
    if not isinstance(state, EdgeState):
        raise RetentionError("retention_invalid", "edge state")
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
        raise RetentionError("retention_state_changed", "edge conflict") from exc
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def add_root(key: NodeKey, *, root_kind: str, db_path: Path) -> None:
    kind = _text(root_kind, "root_kind")
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
        connection.execute("INSERT OR REPLACE INTO retention_roots VALUES(?,?,?,?)",
                           (kind, key.node_type.value, key.node_id, version))
        connection.execute("UPDATE retention_meta SET root_version=? WHERE singleton=1", (version,))
        connection.commit()
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def remove_root(key: NodeKey, *, root_kind: str, db_path: Path) -> None:
    kind = _text(root_kind, "root_kind")
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


def close_edge(source: NodeKey, target: NodeKey, *, edge_type: str,
               closed_at: str, db_path: Path) -> None:
    edge, closed = _text(edge_type, "edge_type"), _timestamp(closed_at)
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


def _receipt(key: NodeKey, run_id: str, version: int, deleted_at: str, secret: bytes) -> str:
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise RetentionError("retention_invalid", "receipt key")
    payload = json.dumps({"deleted_at": deleted_at, "node_id": key.node_id,
                          "node_type": key.node_type.value, "object_version": version,
                          "run_id": run_id}, sort_keys=True, separators=(",", ":")).encode()
    return "hmac-sha256:" + hmac.new(secret, b"metnos.retention-receipt/v1\0" + payload,
                                     hashlib.sha256).hexdigest()


def sweep(*, run_id: str, observed_at: str, receipt_key: bytes, db_path: Path,
          before_each: Callable[[NodeKey], None] | None = None) -> SweepResult:
    """Second pass: re-mark under a write lock and CAS every candidate."""
    run, now = _text(run_id, "run_id"), _timestamp(observed_at)
    deleted: list[NodeKey] = []
    preserved: list[tuple[NodeKey, CandidateStatus]] = []
    # A callback is a test/embedding seam and runs before acquiring the lock so
    # a concurrent root or edge can win the race normally.
    probe = _open(db_path)
    try:
        rows = probe.execute(
            "SELECT node_type,node_id FROM retention_candidates WHERE run_id=? AND status='marked' "
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
            if marked is None or node is None or marked["status"] != CandidateStatus.MARKED.value:
                status = CandidateStatus.STATE_CHANGED
            elif int(node["object_version"]) != int(marked["observed_version"]) or node["state"] != NodeState.CLOSED.value:
                status = CandidateStatus.STATE_CHANGED
            elif node["eligible_after"] > now:
                status = CandidateStatus.WINDOW_OPEN
            elif (key.node_type.value, key.node_id) in _reachable(connection):
                status = CandidateStatus.REFERENCED
            if status is CandidateStatus.DELETED:
                auth = _receipt(key, run, int(node["object_version"]), now, receipt_key)
                connection.execute("INSERT OR IGNORE INTO retention_receipts VALUES(?,?,?,?,?,?)",
                                   (run, key.node_type.value, key.node_id,
                                    node["object_version"], now, auth))
                connection.execute(
                    "DELETE FROM retention_edges WHERE (source_type=? AND source_id=?) "
                    "OR (target_type=? AND target_id=?)",
                    (key.node_type.value, key.node_id, key.node_type.value, key.node_id),
                )
                changed = connection.execute(
                    "UPDATE retention_nodes SET state='deleted',object_version=object_version+1 "
                    "WHERE node_type=? AND node_id=? AND object_version=? AND state='closed'",
                    (key.node_type.value, key.node_id, marked["observed_version"]),
                )
                if changed.rowcount != 1:
                    raise RetentionError("retention_state_changed")
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
        final_state = "complete" if not preserved else "partial"
        connection.execute("UPDATE retention_runs SET state=? WHERE run_id=?", (final_state, run))
    finally:
        connection.close()
    return SweepResult(tuple(deleted), tuple(preserved))


def historical_generation_selectable(key: NodeKey, *, db_path: Path) -> bool:
    """History is inert unless linked to a closed admission receipt.

    The collector only observes this proof.  It never creates the link or the
    receipt, so scanning old history cannot accidentally re-admit it.
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
