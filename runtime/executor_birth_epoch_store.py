"""Inactive RM-0008 F5 store for generation epochs and preexercise caches.

This module is deliberately not wired into the loader or durable execution.
It provides the transactional substrate that may be activated only after the
real-admission threshold in RM-0008 section 10 has been certified.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from manifest_inventory import ContractId
from executor_birth_feedback import QuarantineCAS


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class EpochStoreError(RuntimeError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class EpochState(str, Enum):
    CURRENT = "current"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class BirthLifecycle(str, Enum):
    PROPOSED = "proposed"
    SYNTHESIZED = "synthesized"
    PREEXERCISE = "preexercise"
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


_LIFECYCLE_TRANSITIONS = {
    BirthLifecycle.PROPOSED: frozenset({BirthLifecycle.SYNTHESIZED}),
    BirthLifecycle.SYNTHESIZED: frozenset({BirthLifecycle.PREEXERCISE, BirthLifecycle.QUARANTINED}),
    BirthLifecycle.PREEXERCISE: frozenset({BirthLifecycle.ACTIVE, BirthLifecycle.QUARANTINED}),
    BirthLifecycle.ACTIVE: frozenset({BirthLifecycle.QUARANTINED, BirthLifecycle.DEPRECATED}),
    BirthLifecycle.QUARANTINED: frozenset(),
    BirthLifecycle.DEPRECATED: frozenset({BirthLifecycle.ARCHIVED}),
    BirthLifecycle.ARCHIVED: frozenset(),
}
_STATE_TRANSITIONS = {
    EpochState.CURRENT: frozenset({EpochState.CURRENT, EpochState.DEPRECATED, EpochState.ARCHIVED}),
    EpochState.DEPRECATED: frozenset({EpochState.DEPRECATED, EpochState.ARCHIVED}),
    EpochState.ARCHIVED: frozenset({EpochState.ARCHIVED}),
}


@dataclass(frozen=True, slots=True)
class EpochCacheKey:
    contract_id: ContractId
    generation_id: str
    lifecycle: BirthLifecycle

    def __post_init__(self) -> None:
        _contract(self.contract_id)
        _generation(self.generation_id)
        if not isinstance(self.lifecycle, BirthLifecycle):
            raise EpochStoreError("cache_key_invalid", "lifecycle")


@dataclass(frozen=True, slots=True)
class EpochRecord:
    contract_id: str
    generation_id: str
    name: str
    source: str
    state: EpochState
    lifecycle: BirthLifecycle
    state_version: int


_SCHEMA = """
CREATE TABLE IF NOT EXISTS executor_epochs (
  contract_id TEXT NOT NULL, generation_id TEXT NOT NULL,
  name TEXT NOT NULL, source TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('current','deprecated','archived')),
  lifecycle TEXT NOT NULL CHECK(lifecycle IN
    ('proposed','synthesized','preexercise','active','quarantined','deprecated','archived')),
  first_seen_at TEXT NOT NULL, last_used_at TEXT,
  total_calls INTEGER NOT NULL DEFAULT 0 CHECK(total_calls>=0),
  successful_calls INTEGER NOT NULL DEFAULT 0 CHECK(successful_calls>=0),
  failed_calls INTEGER NOT NULL DEFAULT 0 CHECK(failed_calls>=0),
  positive_feedback INTEGER NOT NULL DEFAULT 0 CHECK(positive_feedback>=0),
  negative_feedback INTEGER NOT NULL DEFAULT 0 CHECK(negative_feedback>=0),
  last_call_ok INTEGER CHECK(last_call_ok IN (0,1) OR last_call_ok IS NULL),
  inactivity_since TEXT, lifecycle_override TEXT, override_reason TEXT,
  historic_epoch_ref TEXT,
  state_version INTEGER NOT NULL DEFAULT 1 CHECK(state_version>=1),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(contract_id,generation_id), UNIQUE(name,generation_id)
);
CREATE INDEX IF NOT EXISTS idx_epochs_name_state ON executor_epochs(name,state);
CREATE INDEX IF NOT EXISTS idx_epochs_state_used ON executor_epochs(state,last_used_at);
CREATE INDEX IF NOT EXISTS idx_epochs_source_state ON executor_epochs(source,state);
CREATE UNIQUE INDEX IF NOT EXISTS idx_epochs_single_current
  ON executor_epochs(contract_id) WHERE state='current';
CREATE TABLE IF NOT EXISTS executor_epoch_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract_id TEXT NOT NULL, generation_id TEXT NOT NULL,
  event_seq INTEGER NOT NULL, ts TEXT NOT NULL, event_kind TEXT NOT NULL,
  source TEXT, prior_state_version INTEGER, new_state_version INTEGER NOT NULL,
  detail_json TEXT,
  UNIQUE(contract_id,generation_id,event_seq),
  FOREIGN KEY(contract_id,generation_id)
    REFERENCES executor_epochs(contract_id,generation_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS executor_legacy_state (
  legacy_id INTEGER PRIMARY KEY AUTOINCREMENT,
  legacy_name TEXT NOT NULL, legacy_table TEXT NOT NULL,
  legacy_row_json TEXT NOT NULL,
  resolution TEXT NOT NULL DEFAULT 'unresolved'
    CHECK(resolution IN ('unresolved','attested','discarded')),
  migrated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS executor_preexercise_cache (
  contract_id TEXT NOT NULL, generation_id TEXT NOT NULL,
  lifecycle TEXT NOT NULL CHECK(lifecycle IN
    ('proposed','synthesized','preexercise','active','quarantined','deprecated','archived')),
  payload BLOB NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(contract_id,generation_id,lifecycle),
  FOREIGN KEY(contract_id,generation_id)
    REFERENCES executor_epochs(contract_id,generation_id) ON DELETE RESTRICT
);
"""


def _contract(value: object) -> str:
    if not isinstance(value, ContractId):
        raise EpochStoreError("epoch_invalid", "contract_id")
    return value.value


def _generation(value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise EpochStoreError("epoch_invalid", "generation_id")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise EpochStoreError("epoch_invalid", field)
    return value


def _open(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(_SCHEMA)
    return connection


def open_epoch(*, contract_id: ContractId, generation_id: str, name: str,
               source: str, lifecycle: BirthLifecycle, observed_at: str,
               db_path: Path, historic_epoch_ref: str | None = None) -> EpochRecord:
    """Open a clean epoch.  A second current generation fails atomically."""
    cid, gid = _contract(contract_id), _generation(generation_id)
    if not isinstance(lifecycle, BirthLifecycle):
        raise EpochStoreError("epoch_invalid", "lifecycle")
    values = (_text(name, "name"), _text(source, "source"), _text(observed_at, "observed_at"))
    if historic_epoch_ref is not None:
        _text(historic_epoch_ref, "historic_epoch_ref")
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "INSERT INTO executor_epochs(contract_id,generation_id,name,source,state,lifecycle,"
                "first_seen_at,historic_epoch_ref,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (cid, gid, values[0], values[1], EpochState.CURRENT.value, lifecycle.value,
                 values[2], historic_epoch_ref, values[2], values[2]),
            )
            connection.execute(
                "INSERT INTO executor_epoch_history(contract_id,generation_id,event_seq,ts,event_kind,"
                "source,prior_state_version,new_state_version,detail_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (cid, gid, 1, values[2], "opened", values[1], None, 1,
                 json.dumps({"lifecycle": lifecycle.value}, sort_keys=True, separators=(",", ":"))),
            )
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise EpochStoreError("epoch_conflict", str(exc)) from exc
        connection.commit()
        return EpochRecord(cid, gid, values[0], values[1], EpochState.CURRENT, lifecycle, 1)
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def transition_epoch(key: EpochCacheKey, *, expected_version: int,
                     new_state: EpochState, new_lifecycle: BirthLifecycle,
                     event_kind: str, occurred_at: str, db_path: Path) -> int:
    """CAS an epoch and invalidate every cache entry in the same transaction."""
    if type(expected_version) is not int or expected_version < 1:
        raise EpochStoreError("epoch_invalid", "expected_version")
    if not isinstance(new_state, EpochState) or not isinstance(new_lifecycle, BirthLifecycle):
        raise EpochStoreError("epoch_invalid", "transition")
    event, ts = _text(event_kind, "event_kind"), _text(occurred_at, "occurred_at")
    cid, gid = key.contract_id.value, key.generation_id
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT state,lifecycle,state_version FROM executor_epochs "
            "WHERE contract_id=? AND generation_id=?",
            (cid, gid),
        ).fetchone()
        if current is None or current["state_version"] != expected_version:
            raise EpochStoreError("epoch_conflict")
        old_state = EpochState(current["state"])
        old_lifecycle = BirthLifecycle(current["lifecycle"])
        if old_lifecycle is not key.lifecycle:
            raise EpochStoreError("epoch_conflict", "stale_lifecycle")
        if (new_state not in _STATE_TRANSITIONS[old_state]
                or new_lifecycle not in _LIFECYCLE_TRANSITIONS[old_lifecycle]):
            raise EpochStoreError("epoch_transition_invalid")
        updated = connection.execute(
            "UPDATE executor_epochs SET state=?,lifecycle=?,state_version=state_version+1,updated_at=? "
            "WHERE contract_id=? AND generation_id=? AND state_version=? AND lifecycle=?",
            (new_state.value, new_lifecycle.value, ts, cid, gid, expected_version,
             old_lifecycle.value),
        )
        if updated.rowcount != 1:
            raise EpochStoreError("epoch_conflict")
        connection.execute(
            "DELETE FROM executor_preexercise_cache WHERE contract_id=? AND generation_id=?",
            (cid, gid),
        )
        connection.execute(
            "INSERT INTO executor_epoch_history(contract_id,generation_id,event_seq,ts,event_kind,"
            "prior_state_version,new_state_version,detail_json) "
            "SELECT ?,?,COALESCE(MAX(event_seq),0)+1,?,?,?,?,? FROM executor_epoch_history "
            "WHERE contract_id=? AND generation_id=?",
            (cid, gid, ts, event, expected_version, expected_version + 1,
             json.dumps({"lifecycle": new_lifecycle.value, "state": new_state.value},
                        sort_keys=True, separators=(",", ":")), cid, gid),
        )
        connection.commit()
        return expected_version + 1
    except sqlite3.IntegrityError as exc:
        raise EpochStoreError("epoch_conflict", str(exc)) from exc
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def put_cache(key: EpochCacheKey, payload: bytes, *, created_at: str, db_path: Path) -> None:
    """Store only under the exact typed generation/lifecycle identity."""
    if not isinstance(payload, bytes):
        raise EpochStoreError("cache_value_invalid", "payload")
    ts = _text(created_at, "created_at")
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT lifecycle FROM executor_epochs WHERE contract_id=? AND generation_id=?",
            (key.contract_id.value, key.generation_id),
        ).fetchone()
        if row is None or row["lifecycle"] != key.lifecycle.value:
            raise EpochStoreError("cache_key_stale")
        connection.execute(
            "INSERT INTO executor_preexercise_cache VALUES(?,?,?,?,?) "
            "ON CONFLICT(contract_id,generation_id,lifecycle) DO UPDATE SET payload=excluded.payload,"
            "created_at=excluded.created_at",
            (key.contract_id.value, key.generation_id, key.lifecycle.value,
             sqlite3.Binary(payload), ts),
        )
        connection.commit()
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def get_cache(key: EpochCacheKey, *, db_path: Path) -> bytes | None:
    connection = _open(db_path)
    try:
        row = connection.execute(
            "SELECT payload FROM executor_preexercise_cache WHERE contract_id=? AND generation_id=? "
            "AND lifecycle=?", (key.contract_id.value, key.generation_id, key.lifecycle.value),
        ).fetchone()
        return None if row is None else bytes(row["payload"])
    finally:
        connection.close()


def quarantine_for_feedback(*, contract_id: ContractId, generation_id: str,
                            occurred_at: str, db_path: Path) -> QuarantineCAS:
    """CAS the exact invoked generation to quarantine, never a successor."""
    cid, gid = _contract(contract_id), _generation(generation_id)
    ts = _text(occurred_at, "occurred_at")
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT generation_id,lifecycle,state_version FROM executor_epochs "
            "WHERE contract_id=? AND state='current'", (cid,),
        ).fetchone()
        if row is None or row["generation_id"] != gid:
            connection.commit()
            return QuarantineCAS.STALE
        if row["lifecycle"] == BirthLifecycle.QUARANTINED.value:
            connection.commit()
            return QuarantineCAS.ALREADY_QUARANTINED
        old = BirthLifecycle(row["lifecycle"])
        if BirthLifecycle.QUARANTINED not in _LIFECYCLE_TRANSITIONS[old]:
            raise EpochStoreError("epoch_transition_invalid", "feedback quarantine")
        version = int(row["state_version"])
        updated = connection.execute(
            "UPDATE executor_epochs SET lifecycle='quarantined',state_version=state_version+1,"
            "negative_feedback=negative_feedback+1,updated_at=? "
            "WHERE contract_id=? AND generation_id=? AND state='current' "
            "AND lifecycle=? AND state_version=?",
            (ts, cid, gid, old.value, version),
        )
        if updated.rowcount != 1:
            raise EpochStoreError("epoch_conflict")
        connection.execute(
            "DELETE FROM executor_preexercise_cache WHERE contract_id=? AND generation_id=?",
            (cid, gid),
        )
        connection.execute(
            "INSERT INTO executor_epoch_history(contract_id,generation_id,event_seq,ts,event_kind,"
            "prior_state_version,new_state_version,detail_json) "
            "SELECT ?,?,COALESCE(MAX(event_seq),0)+1,?,'negative_feedback_quarantine',?,?,? "
            "FROM executor_epoch_history WHERE contract_id=? AND generation_id=?",
            (cid, gid, ts, version, version + 1,
             json.dumps({"lifecycle": "quarantined"}, sort_keys=True,
                        separators=(",", ":")), cid, gid),
        )
        connection.commit()
        return QuarantineCAS.APPLIED
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()
