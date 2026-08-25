"""Inactive RM-0008 F5 store for generation epochs and preexercise caches.

This module is deliberately not wired into the loader or durable execution.
It provides the transactional substrate that may be activated only after the
real-admission threshold in RM-0008 section 10 has been certified.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from manifest_inventory import ContractId
from executor_birth_feedback import QuarantineCAS


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
# RM-0008 section 11 fixes the original epoch schema as version 1.  Version 2
# is the explicit local extension that adds lifecycle/cache state and the
# evidence tables needed to certify the one-time name-only migration.  There
# is intentionally no implicit adoption of an unversioned/unknown epoch store.
EPOCH_STORE_NORMATIVE_SCHEMA_VERSION = 1
EPOCH_STORE_SCHEMA_VERSION = 2
_LEGACY_DIGEST_DOMAIN = b"metnos.executor-birth.legacy-epoch-migration/v1\0"


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


@dataclass(frozen=True, slots=True)
class ExecutionEpochAttestation:
    """Exact lifecycle facts re-read for one execution attempt."""

    contract_id: ContractId
    generation_id: str
    name: str
    state: EpochState
    lifecycle: BirthLifecycle
    state_version: int


@dataclass(frozen=True, slots=True)
class EpochReplacement:
    closed_generation_id: str
    closed_state_version: int
    opened: EpochRecord


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
CREATE TABLE IF NOT EXISTS executor_legacy_migrations (
  migration_id TEXT PRIMARY KEY,
  legacy_table TEXT NOT NULL UNIQUE,
  source_count INTEGER NOT NULL CHECK(source_count>=0),
  source_digest TEXT NOT NULL,
  migrated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS executor_legacy_migration_rows (
  migration_id TEXT NOT NULL,
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal>=0),
  legacy_id INTEGER NOT NULL UNIQUE,
  PRIMARY KEY(migration_id,source_ordinal),
  FOREIGN KEY(migration_id) REFERENCES executor_legacy_migrations(migration_id)
    ON DELETE RESTRICT,
  FOREIGN KEY(legacy_id) REFERENCES executor_legacy_state(legacy_id)
    ON DELETE RESTRICT
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

_REQUIRED_COLUMNS = {
    "executor_epochs": frozenset({
        "contract_id", "generation_id", "name", "source", "state", "lifecycle",
        "first_seen_at", "last_used_at", "total_calls", "successful_calls",
        "failed_calls", "positive_feedback", "negative_feedback", "last_call_ok",
        "inactivity_since", "lifecycle_override", "override_reason",
        "historic_epoch_ref", "state_version", "created_at", "updated_at",
    }),
    "executor_epoch_history": frozenset({
        "id", "contract_id", "generation_id", "event_seq", "ts", "event_kind",
        "source", "prior_state_version", "new_state_version", "detail_json",
    }),
    "executor_legacy_state": frozenset({
        "legacy_id", "legacy_name", "legacy_table", "legacy_row_json", "resolution",
        "migrated_at",
    }),
    "executor_legacy_migrations": frozenset({
        "migration_id", "legacy_table", "source_count", "source_digest", "migrated_at",
    }),
    "executor_legacy_migration_rows": frozenset({
        "migration_id", "source_ordinal", "legacy_id",
    }),
    "executor_preexercise_cache": frozenset({
        "contract_id", "generation_id", "lifecycle", "payload", "created_at",
    }),
}
_REQUIRED_INDEXES = frozenset({
    "idx_epochs_name_state", "idx_epochs_state_used", "idx_epochs_source_state",
    "idx_epochs_single_current",
})
_EXPECTED_SCHEMA_FINGERPRINT: tuple[tuple[str, str, str], ...] | None = None


def _execute_schema(connection: sqlite3.Connection) -> None:
    # sqlite3.executescript commits implicitly; executing statements separately
    # keeps schema creation and PRAGMA user_version inside BEGIN IMMEDIATE.
    for statement in _SCHEMA.split(";"):
        if statement.strip():
            connection.execute(statement)


def _schema_fingerprint(connection: sqlite3.Connection) -> tuple[tuple[str, str, str], ...]:
    names = tuple(_REQUIRED_COLUMNS) + tuple(sorted(_REQUIRED_INDEXES))
    placeholders = ",".join("?" for _ in names)
    rows = connection.execute(
        f"SELECT type,name,sql FROM sqlite_master WHERE name IN ({placeholders}) "
        "ORDER BY type,name", names,
    ).fetchall()
    return tuple(
        (row[0], row[1], " ".join((row[2] or "").split()))
        for row in rows
    )


def _expected_schema_fingerprint() -> tuple[tuple[str, str, str], ...]:
    global _EXPECTED_SCHEMA_FINGERPRINT
    if _EXPECTED_SCHEMA_FINGERPRINT is None:
        reference = sqlite3.connect(":memory:")
        try:
            _execute_schema(reference)
            _EXPECTED_SCHEMA_FINGERPRINT = _schema_fingerprint(reference)
        finally:
            reference.close()
    return _EXPECTED_SCHEMA_FINGERPRINT


def _validate_schema(connection: sqlite3.Connection) -> None:
    for table, expected in _REQUIRED_COLUMNS.items():
        actual = frozenset(row[1] for row in connection.execute(
            f'PRAGMA table_info("{table}")'
        ).fetchall())
        if actual != expected:
            raise EpochStoreError("epoch_schema_mismatch", table)
    indexes = frozenset(row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_autoindex_%'"
    ).fetchall())
    if not _REQUIRED_INDEXES.issubset(indexes):
        raise EpochStoreError("epoch_schema_mismatch", "indexes")
    single_current = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_epochs_single_current'"
    ).fetchone()
    if single_current is None or "WHERE state='current'" not in (single_current[0] or ""):
        raise EpochStoreError("epoch_schema_mismatch", "idx_epochs_single_current")
    if _schema_fingerprint(connection) != _expected_schema_fingerprint():
        raise EpochStoreError("epoch_schema_mismatch", "definition")


def _ensure_schema(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version == EPOCH_STORE_SCHEMA_VERSION:
        _validate_schema(connection)
        return
    if version != 0:
        raise EpochStoreError("epoch_schema_version", str(version))
    owned_names = tuple(_REQUIRED_COLUMNS)
    placeholders = ",".join("?" for _ in owned_names)
    owned = connection.execute(
        f"SELECT 1 FROM sqlite_master WHERE name IN ({placeholders}) LIMIT 1",
        owned_names,
    ).fetchone()
    if owned is not None:
        raise EpochStoreError("epoch_schema_version", "unversioned")
    connection.execute("BEGIN IMMEDIATE")
    try:
        _execute_schema(connection)
        connection.execute(f"PRAGMA user_version={EPOCH_STORE_SCHEMA_VERSION}")
        _validate_schema(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


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


def _canonical_legacy_value(value: object) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if isinstance(value, (list, tuple)):
        return [_canonical_legacy_value(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise EpochStoreError("legacy_migration_invalid", "non_string_key")
        return {
            key: _canonical_legacy_value(value[key])
            for key in sorted(value)
        }
    # Floats (including NaN and signed zero) and implicit repr conversions are
    # deliberately excluded from the v1 canonical form.
    raise EpochStoreError("legacy_migration_invalid", "non_canonical_value")


def _encode_legacy_rows(
    rows: tuple[Mapping[str, object], ...],
) -> tuple[tuple[str, str], ...]:
    if not isinstance(rows, tuple):
        raise EpochStoreError("legacy_migration_invalid", "rows")
    encoded: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise EpochStoreError("legacy_migration_invalid", "row")
        name = _text(row.get("name"), "legacy_name")
        canonical = _canonical_legacy_value(row)
        body = json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
        encoded.append((name, body))
    # A table scan has no portable implicit order.  Sorting the full canonical
    # rows makes the digest and source ordinals repeatable while preserving
    # duplicate multiplicity.
    return tuple(sorted(encoded, key=lambda item: (item[1].encode("utf-8"), item[0])))


def _legacy_digest_from_bodies(bodies: tuple[str, ...]) -> str:
    payload = json.dumps(sorted(bodies), ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(_LEGACY_DIGEST_DOMAIN + payload).hexdigest()


def legacy_rows_digest(rows: tuple[Mapping[str, object], ...]) -> str:
    """Return the domain-separated canonical digest required by migration."""
    encoded = _encode_legacy_rows(rows)
    return _legacy_digest_from_bodies(tuple(body for _, body in encoded))


def _open(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        _ensure_schema(connection)
        return connection
    except BaseException:
        connection.close()
        raise


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


def attest_execution_epoch(
    *, contract_id: ContractId, generation_id: str, name: str, db_path: Path,
) -> ExecutionEpochAttestation:
    """Re-read an exact epoch without resolving by executor name.

    The caller supplies all three immutable identities obtained from the
    authenticated generation.  Legacy name-only rows and successor generations
    therefore cannot satisfy this lookup.
    """
    cid, gid = _contract(contract_id), _generation(generation_id)
    expected_name = _text(name, "name")
    connection = _open(db_path)
    try:
        row = connection.execute(
            "SELECT name,state,lifecycle,state_version FROM executor_epochs "
            "WHERE contract_id=? AND generation_id=?",
            (cid, gid),
        ).fetchone()
        if row is None:
            raise EpochStoreError("execution.runner_absent")
        if row["name"] != expected_name:
            raise EpochStoreError("execution.runner_absent", "name_mismatch")
        lifecycle = BirthLifecycle(row["lifecycle"])
        state = EpochState(row["state"])
        if lifecycle is BirthLifecycle.QUARANTINED:
            raise EpochStoreError("execution.quarantined")
        if lifecycle in {BirthLifecycle.DEPRECATED, BirthLifecycle.ARCHIVED} or state is not EpochState.CURRENT:
            raise EpochStoreError("execution.retired")
        if lifecycle is not BirthLifecycle.ACTIVE:
            raise EpochStoreError("execution.dormant")
        return ExecutionEpochAttestation(
            contract_id, gid, expected_name, state, lifecycle,
            int(row["state_version"]),
        )
    finally:
        connection.close()


def replace_current_epoch(
    *, contract_id: ContractId, expected_generation_id: str,
    expected_state_version: int, generation_id: str, name: str, source: str,
    lifecycle: BirthLifecycle, observed_at: str, db_path: Path,
    event_kind: str, historic_epoch_ref: str | None = None,
) -> EpochReplacement:
    """Atomically close an exact current epoch and open a clean successor.

    The publication and authenticated reread must already have completed.  The
    exact predecessor generation and version are nevertheless compared again,
    preventing a late publisher or feedback event from replacing a newer
    selection.  Counters are intentionally not copied to the new epoch.
    """
    cid = _contract(contract_id)
    old_gid, new_gid = (_generation(expected_generation_id),
                        _generation(generation_id))
    if old_gid == new_gid:
        raise EpochStoreError("epoch_invalid", "successor generation")
    if type(expected_state_version) is not int or expected_state_version < 1:
        raise EpochStoreError("epoch_invalid", "expected_state_version")
    if not isinstance(lifecycle, BirthLifecycle):
        raise EpochStoreError("epoch_invalid", "lifecycle")
    clean_name = _text(name, "name")
    clean_source = _text(source, "source")
    ts = _text(observed_at, "observed_at")
    event = _text(event_kind, "event_kind")
    if historic_epoch_ref is not None:
        _text(historic_epoch_ref, "historic_epoch_ref")
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT generation_id,state_version FROM executor_epochs "
            "WHERE contract_id=? AND state='current'", (cid,),
        ).fetchone()
        if (current is None or current["generation_id"] != old_gid
                or int(current["state_version"]) != expected_state_version):
            raise EpochStoreError("epoch_conflict", "stale predecessor")
        closed = connection.execute(
            "UPDATE executor_epochs SET state='deprecated',lifecycle='deprecated',"
            "state_version=state_version+1,updated_at=? WHERE contract_id=? "
            "AND generation_id=? AND state='current' AND state_version=?",
            (ts, cid, old_gid, expected_state_version),
        )
        if closed.rowcount != 1:
            raise EpochStoreError("epoch_conflict", "stale predecessor")
        connection.execute(
            "DELETE FROM executor_preexercise_cache WHERE contract_id=? AND generation_id=?",
            (cid, old_gid),
        )
        connection.execute(
            "INSERT INTO executor_epoch_history(contract_id,generation_id,event_seq,ts,event_kind,"
            "prior_state_version,new_state_version,detail_json) "
            "SELECT ?,?,COALESCE(MAX(event_seq),0)+1,?,?,?,?,? FROM executor_epoch_history "
            "WHERE contract_id=? AND generation_id=?",
            (cid, old_gid, ts, event + "_predecessor_closed", expected_state_version,
             expected_state_version + 1, json.dumps(
                 {"state": "deprecated", "successor_generation_id": new_gid},
                 sort_keys=True, separators=(",", ":")), cid, old_gid),
        )
        connection.execute(
            "INSERT INTO executor_epochs(contract_id,generation_id,name,source,state,lifecycle,"
            "first_seen_at,historic_epoch_ref,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (cid, new_gid, clean_name, clean_source, EpochState.CURRENT.value,
             lifecycle.value, ts, historic_epoch_ref, ts, ts),
        )
        connection.execute(
            "INSERT INTO executor_epoch_history(contract_id,generation_id,event_seq,ts,event_kind,"
            "source,prior_state_version,new_state_version,detail_json) VALUES(?,?,?,?,?,?,?,?,?)",
            (cid, new_gid, 1, ts, event, clean_source, None, 1, json.dumps({
                "historic_epoch_ref": historic_epoch_ref,
                "lifecycle": lifecycle.value,
                "predecessor_generation_id": old_gid,
            }, sort_keys=True, separators=(",", ":"))),
        )
        connection.commit()
        return EpochReplacement(
            old_gid, expected_state_version + 1,
            EpochRecord(cid, new_gid, clean_name, clean_source,
                        EpochState.CURRENT, lifecycle, 1),
        )
    except sqlite3.IntegrityError as exc:
        raise EpochStoreError("epoch_conflict", str(exc)) from exc
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


def record_execution(
    key: EpochCacheKey, *, expected_version: int, successful: bool,
    occurred_at: str, db_path: Path,
) -> None:
    """Count one invocation only against the exact selectable generation."""
    if type(expected_version) is not int or expected_version < 1:
        raise EpochStoreError("epoch_invalid", "expected_version")
    if type(successful) is not bool:
        raise EpochStoreError("epoch_invalid", "successful")
    ts = _text(occurred_at, "occurred_at")
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        updated = connection.execute(
            "UPDATE executor_epochs SET total_calls=total_calls+1,"
            "successful_calls=successful_calls+?,failed_calls=failed_calls+?,"
            "last_call_ok=?,last_used_at=?,updated_at=? WHERE contract_id=? "
            "AND generation_id=? AND lifecycle=? AND state='current' AND state_version=?",
            (1 if successful else 0, 0 if successful else 1, 1 if successful else 0,
             ts, ts, key.contract_id.value, key.generation_id, key.lifecycle.value,
             expected_version),
        )
        if updated.rowcount != 1:
            raise EpochStoreError("epoch_conflict", "stale execution identity")
        connection.commit()
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def preserve_legacy_rows(
    *, legacy_table: str, rows: tuple[Mapping[str, object], ...],
    migrated_at: str, db_path: Path,
) -> int:
    """Preserve legacy state without guessing an unauthenticated generation.

    Rows remain ``unresolved`` until a separate attestation binds them to a
    Birth generation. Exact retries are idempotent.
    """
    digest = legacy_rows_digest(rows)
    return migrate_legacy_rows(
        legacy_table=legacy_table, rows=rows, expected_count=len(rows),
        expected_digest=digest, migrated_at=migrated_at, db_path=db_path,
    )


def _insert_legacy_row(
    connection: sqlite3.Connection, *, migration_id: str, ordinal: int,
    name: str, table: str, body: str, migrated_at: str,
) -> None:
    inserted = connection.execute(
        "INSERT INTO executor_legacy_state(legacy_name,legacy_table,legacy_row_json,"
        "resolution,migrated_at) VALUES(?,?,?,'unresolved',?)",
        (name, table, body, migrated_at),
    )
    connection.execute(
        "INSERT INTO executor_legacy_migration_rows(migration_id,source_ordinal,legacy_id) "
        "VALUES(?,?,?)", (migration_id, ordinal, int(inserted.lastrowid)),
    )


def _verify_legacy_migration(
    connection: sqlite3.Connection, *, migration_id: str,
    expected_count: int, expected_digest: str,
) -> None:
    rows = connection.execute(
        "SELECT s.legacy_row_json,s.resolution FROM executor_legacy_migration_rows r "
        "JOIN executor_legacy_state s ON s.legacy_id=r.legacy_id "
        "WHERE r.migration_id=? ORDER BY r.source_ordinal", (migration_id,),
    ).fetchall()
    if len(rows) != expected_count:
        raise EpochStoreError("legacy_migration_count_mismatch", "target")
    if any(row[1] != "unresolved" for row in rows):
        raise EpochStoreError("legacy_migration_schema_mismatch", "resolution")
    actual = _legacy_digest_from_bodies(tuple(row[0] for row in rows))
    if actual != expected_digest:
        raise EpochStoreError("legacy_migration_digest_mismatch", "target")


def migrate_legacy_rows(
    *, legacy_table: str, rows: tuple[Mapping[str, object], ...],
    expected_count: int, expected_digest: str, migrated_at: str, db_path: Path,
) -> int:
    """Copy one complete name-only source into unresolved storage atomically.

    The caller must supply the independently observed count and canonical
    digest.  A completed exact retry verifies persisted evidence and performs
    no writes; any changed source, partial target, unsupported schema/version,
    or digest/count discrepancy fails closed.
    """
    table = _text(legacy_table, "legacy_table")
    ts = _text(migrated_at, "migrated_at")
    if type(expected_count) is not int or expected_count < 0:
        raise EpochStoreError("legacy_migration_invalid", "expected_count")
    if not isinstance(expected_digest, str) or _DIGEST.fullmatch(expected_digest) is None:
        raise EpochStoreError("legacy_migration_invalid", "expected_digest")
    encoded = _encode_legacy_rows(rows)
    if len(encoded) != expected_count:
        raise EpochStoreError("legacy_migration_count_mismatch", "source")
    actual_digest = _legacy_digest_from_bodies(tuple(body for _, body in encoded))
    if actual_digest != expected_digest:
        raise EpochStoreError("legacy_migration_digest_mismatch", "source")
    migration_id = "sha256:" + hashlib.sha256(
        b"metnos.executor-birth.legacy-migration-id/v1\0"
        + table.encode("utf-8") + b"\0" + expected_digest.encode("ascii")
    ).hexdigest()
    connection = _open(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            "SELECT migration_id,source_count,source_digest FROM executor_legacy_migrations "
            "WHERE legacy_table=?", (table,),
        ).fetchone()
        if prior is not None:
            if (prior[0] != migration_id or int(prior[1]) != expected_count
                    or prior[2] != expected_digest):
                raise EpochStoreError("legacy_migration_source_mismatch", table)
            _verify_legacy_migration(
                connection, migration_id=migration_id,
                expected_count=expected_count, expected_digest=expected_digest,
            )
            connection.commit()
            return 0
        connection.execute(
            "INSERT INTO executor_legacy_migrations(migration_id,legacy_table,source_count,"
            "source_digest,migrated_at) VALUES(?,?,?,?,?)",
            (migration_id, table, expected_count, expected_digest, ts),
        )
        for ordinal, (name, body) in enumerate(encoded):
            _insert_legacy_row(
                connection, migration_id=migration_id, ordinal=ordinal,
                name=name, table=table, body=body, migrated_at=ts,
            )
        _verify_legacy_migration(
            connection, migration_id=migration_id,
            expected_count=expected_count, expected_digest=expected_digest,
        )
        connection.commit()
        return expected_count
    finally:
        if connection.in_transaction:
            connection.rollback()
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
