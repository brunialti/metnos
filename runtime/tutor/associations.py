"""Per-user query→source associations promoted only by explicit feedback.

The store never receives clear-text queries.  Matching happens only inside
Tutor retrieval, after the EXPLAIN gate, and an association is usable only
while its embedding space and signed source content are unchanged.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time

import numpy as np

import config
from logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_STORE_PATH = config.PATH_USER_DATA / "tutor_learning.sqlite"
LEGACY_STORE_PATH = config.PATH_USER_DATA / "tutor_associations.sqlite"
STORE_PATH = DEFAULT_STORE_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS associations (
    owner_hash TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    vector BLOB NOT NULL,
    dim INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    unit_hash TEXT NOT NULL,
    audience TEXT NOT NULL,
    confirmations INTEGER NOT NULL DEFAULT 1,
    created REAL NOT NULL,
    last_confirmed REAL NOT NULL,
    PRIMARY KEY(owner_hash, query_hash)
);
CREATE INDEX IF NOT EXISTS idx_tutor_assoc_owner_recent
ON associations(owner_hash, last_confirmed DESC);
CREATE TABLE IF NOT EXISTS association_confirmations (
    owner_hash TEXT NOT NULL,
    confirmation_id TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    created REAL NOT NULL,
    PRIMARY KEY(owner_hash, confirmation_id)
);
CREATE INDEX IF NOT EXISTS idx_tutor_assoc_confirm_query
ON association_confirmations(owner_hash, query_hash);
CREATE TABLE IF NOT EXISTS tutor_association_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_MAX_ROWS_PER_OWNER = 500
_TTL_DAYS = 90.0
_MAX_CONFIRMATIONS_PER_OWNER = 2_000
_CONFIRMATION_TTL_DAYS = 30.0


def _floor() -> float:
    try:
        return min(0.999, max(0.5, float(
            os.environ.get("METNOS_TUTOR_ASSOC_MIN", "0.90"))))
    except (TypeError, ValueError):
        return 0.90


def _strong() -> float:
    try:
        return min(0.999, max(_floor(), float(
            os.environ.get("METNOS_TUTOR_ASSOC_STRONG", "0.95"))))
    except (TypeError, ValueError):
        return 0.95


def _primary_confirmations() -> int:
    try:
        return min(10, max(2, int(os.environ.get(
            "METNOS_TUTOR_ASSOC_PRIMARY_CONFIRMATIONS", "2"))))
    except (TypeError, ValueError):
        return 2


def owner_hash(user_id: str) -> str:
    return hashlib.sha256(
        ("metnos-tutor-owner-v1\0" + str(user_id or "")).encode("utf-8")
    ).hexdigest()


def query_hash(text: str) -> str:
    return hashlib.sha256(
        " ".join(str(text).casefold().split()).encode("utf-8")).hexdigest()


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Install the association tables on an existing learning connection."""

    columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(associations)").fetchall()
    }
    if columns and "owner_hash" not in columns:
        # The pre-F4 scaffold had no call sites and no owner dimension.  Its
        # rows cannot be assigned safely, so fail closed instead of importing
        # them into an arbitrary user's scope.
        connection.execute("DROP TABLE associations")
    connection.executescript(_SCHEMA)


def _retire_legacy_files() -> None:
    """Remove the superseded association database and SQLite sidecars."""

    for suffix in ("", "-wal", "-shm"):
        path = LEGACY_STORE_PATH.with_name(LEGACY_STORE_PATH.name + suffix)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _migrate_legacy(connection: sqlite3.Connection) -> None:
    """Import the admitted legacy store once, then retire it physically."""

    if STORE_PATH != DEFAULT_STORE_PATH or not LEGACY_STORE_PATH.is_file():
        return
    done = connection.execute(
        "SELECT value FROM tutor_association_meta WHERE key=?",
        ("legacy_store_migrated_v1",),
    ).fetchone()
    if done is not None:
        _retire_legacy_files()
        return
    attached = False
    migrated = False
    try:
        connection.execute(
            "ATTACH DATABASE ? AS legacy_tutor_assoc",
            (str(LEGACY_STORE_PATH),),
        )
        attached = True
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA legacy_tutor_assoc.table_info(associations)"
            ).fetchall()
        }
        required = {
            "owner_hash", "query_hash", "vector", "dim", "fingerprint",
            "catalog_version", "unit_id", "unit_hash", "audience",
            "confirmations", "created", "last_confirmed",
        }
        if required.issubset(columns):
            connection.execute(
                "INSERT OR IGNORE INTO associations "
                "(owner_hash,query_hash,vector,dim,fingerprint,"
                "catalog_version,unit_id,unit_hash,audience,confirmations,"
                "created,last_confirmed) SELECT owner_hash,query_hash,vector,"
                "dim,fingerprint,catalog_version,unit_id,unit_hash,audience,"
                "confirmations,created,last_confirmed "
                "FROM legacy_tutor_assoc.associations"
            )
        connection.execute(
            "INSERT OR REPLACE INTO tutor_association_meta(key,value) "
            "VALUES (?,?)", ("legacy_store_migrated_v1", "done"),
        )
        # The receipt and every imported row must be durable before the old
        # physical copy is removed.  _connect() performs no caller mutation
        # before this migration, so this commit cannot split another action.
        connection.commit()
        migrated = True
    except sqlite3.Error:
        connection.rollback()
        log.warning("Tutor legacy association migration unavailable",
                    exc_info=True)
    finally:
        if attached:
            try:
                connection.execute("DETACH DATABASE legacy_tutor_assoc")
            except sqlite3.Error:
                pass
    if migrated:
        _retire_legacy_files()


def _purge_legacy_owner(scoped_owner_hash: str) -> int:
    """Delete one owner from a legacy copy left by an interrupted migration."""

    if not LEGACY_STORE_PATH.is_file():
        return 0
    connection = sqlite3.connect(LEGACY_STORE_PATH)
    try:
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(associations)").fetchall()
        }
        if "owner_hash" not in columns:
            # A pre-owner scaffold cannot be attributed to any principal and
            # is unusable by the current runtime; retire the whole old copy.
            connection.close()
            _retire_legacy_files()
            return 0
        cursor = connection.execute(
            "DELETE FROM associations WHERE owner_hash=?",
            (scoped_owner_hash,),
        )
        if "association_confirmations" in {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
        }:
            connection.execute(
                "DELETE FROM association_confirmations WHERE owner_hash=?",
                (scoped_owner_hash,),
            )
        connection.commit()
        return max(0, int(cursor.rowcount))
    finally:
        try:
            connection.close()
        except sqlite3.Error:
            pass


def _connect() -> sqlite3.Connection:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    created = not STORE_PATH.exists()
    connection = sqlite3.connect(STORE_PATH)
    ensure_schema(connection)
    _migrate_legacy(connection)
    if created or STORE_PATH.exists():
        try:
            os.chmod(STORE_PATH, 0o600)
        except OSError:
            pass
    return connection


def _prune(connection: sqlite3.Connection, now: float,
           scoped_owner_hash: str) -> None:
    connection.execute(
        "DELETE FROM associations WHERE last_confirmed < ?",
        (now - _TTL_DAYS * 86400.0,))
    connection.execute(
        "DELETE FROM associations WHERE rowid IN ("
        " SELECT rowid FROM associations WHERE owner_hash=?"
        " ORDER BY last_confirmed DESC LIMIT -1 OFFSET ?)",
        (scoped_owner_hash, _MAX_ROWS_PER_OWNER))
    connection.execute(
        "DELETE FROM association_confirmations WHERE owner_hash=? "
        "AND NOT EXISTS (SELECT 1 FROM associations a "
        "WHERE a.owner_hash=association_confirmations.owner_hash "
        "AND a.query_hash=association_confirmations.query_hash)",
        (scoped_owner_hash,),
    )
    connection.execute(
        "DELETE FROM association_confirmations WHERE owner_hash=? "
        "AND created < ?",
        (scoped_owner_hash,
         now - _CONFIRMATION_TTL_DAYS * 86400.0),
    )
    connection.execute(
        "DELETE FROM association_confirmations WHERE rowid IN ("
        " SELECT rowid FROM association_confirmations WHERE owner_hash=?"
        " ORDER BY created DESC, rowid DESC LIMIT -1 OFFSET ?)",
        (scoped_owner_hash, _MAX_CONFIRMATIONS_PER_OWNER),
    )


def _record_confirmation_hash(
        connection: sqlite3.Connection, *, owner_user_id: str,
        normalized_query_hash: str, vector: np.ndarray, fingerprint: str,
        catalog_version: str, unit_id: str, unit_hash: str, audience: str,
        confirmation_id: str = "") -> bool:
    """Apply one confirmation inside the caller's learning transaction."""

    if (not owner_user_id or not normalized_query_hash or not fingerprint
            or not unit_id or not unit_hash):
        raise ValueError("incomplete Tutor association evidence")
    now = time.time()
    flat = np.asarray(vector, dtype=np.float32).reshape(-1)
    if not flat.size or not np.isfinite(flat).all():
        raise ValueError("invalid Tutor association vector")
    scoped = owner_hash(owner_user_id)
    if confirmation_id:
        receipt = connection.execute(
            "INSERT OR IGNORE INTO association_confirmations "
            "(owner_hash,confirmation_id,query_hash,created) VALUES (?,?,?,?)",
            (scoped, confirmation_id, normalized_query_hash, now),
        )
        if receipt.rowcount == 0:
            return False
    connection.execute(
        "INSERT INTO associations (owner_hash, query_hash, vector, dim,"
        " fingerprint, catalog_version, unit_id, unit_hash, audience,"
        " confirmations, created, last_confirmed)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)"
        " ON CONFLICT(owner_hash,query_hash) DO UPDATE SET"
        " vector=excluded.vector, dim=excluded.dim,"
        " fingerprint=excluded.fingerprint,"
        " catalog_version=excluded.catalog_version,"
        " unit_id=excluded.unit_id, unit_hash=excluded.unit_hash,"
        " audience=excluded.audience,"
        " confirmations=CASE WHEN associations.unit_id=excluded.unit_id"
        " AND associations.unit_hash=excluded.unit_hash"
        " AND associations.fingerprint=excluded.fingerprint"
        " THEN associations.confirmations+1 ELSE 1 END,"
        " last_confirmed=excluded.last_confirmed",
        (scoped, normalized_query_hash, flat.tobytes(), int(flat.shape[0]),
         fingerprint, catalog_version, unit_id, unit_hash, audience,
         now, now))
    _prune(connection, now, scoped)
    return True


def record_confirmation_hash(
        *, owner_user_id: str, normalized_query_hash: str,
        vector: np.ndarray, fingerprint: str, catalog_version: str,
        unit_id: str, unit_hash: str, audience: str,
        confirmation_id: str = "") -> bool:
    """Promote one source actually served and approved by this user."""

    with _connect() as connection:
        return _record_confirmation_hash(
            connection,
            owner_user_id=owner_user_id,
            normalized_query_hash=normalized_query_hash,
            vector=vector,
            fingerprint=fingerprint,
            catalog_version=catalog_version,
            unit_id=unit_id,
            unit_hash=unit_hash,
            audience=audience,
            confirmation_id=confirmation_id,
        )


def _record_negative_hash(connection: sqlite3.Connection, *,
                          owner_user_id: str,
                          normalized_query_hash: str) -> int:
    return _record_negative_hashes(
        connection,
        owner_user_id=owner_user_id,
        normalized_query_hashes=(normalized_query_hash,),
    )


def _record_negative_hashes(connection: sqlite3.Connection, *,
                            owner_user_id: str,
                            normalized_query_hashes: tuple[str, ...]) -> int:
    scoped = owner_hash(owner_user_id)
    hashes = tuple(sorted({
        str(value) for value in normalized_query_hashes if str(value)
    }))
    removed = 0
    for normalized_query_hash in hashes:
        cursor = connection.execute(
            "DELETE FROM associations WHERE owner_hash=? AND query_hash=?",
            (scoped, normalized_query_hash))
        removed += max(0, int(cursor.rowcount))
        connection.execute(
            "DELETE FROM association_confirmations "
            "WHERE owner_hash=? AND query_hash=?",
            (scoped, normalized_query_hash),
        )
    return removed


def record_negative_hash(*, owner_user_id: str,
                         normalized_query_hash: str) -> int:
    with _connect() as connection:
        return _record_negative_hash(
            connection,
            owner_user_id=owner_user_id,
            normalized_query_hash=normalized_query_hash,
        )


def match_with_evidence(
        query_vector: np.ndarray, fingerprint: str,
        known_units: dict[str, str] | frozenset[str], *,
        owner_user_id: str = "host",
        audience: str = "user",
        ) -> tuple[tuple[str, float, bool, str], ...]:
    """Return the exact owner-scoped row that contributes per source."""

    if not STORE_PATH.exists() or not owner_user_id:
        return ()
    floor, strong = _floor(), _strong()
    primary_confirmations = _primary_confirmations()
    now = time.time()
    query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
    if isinstance(known_units, dict):
        hashes = known_units
    else:
        hashes = {unit_id: "" for unit_id in known_units}
    results: dict[str, tuple[float, bool, str]] = {}
    stale_rows: list[tuple[str, str]] = []
    try:
        with _connect() as connection:
            _prune(connection, now, owner_hash(owner_user_id))
            rows = connection.execute(
                "SELECT owner_hash, query_hash, vector, dim, fingerprint,"
                " unit_id, unit_hash, audience, confirmations"
                " FROM associations WHERE owner_hash=? AND last_confirmed>=?",
                (owner_hash(owner_user_id),
                 now - _TTL_DAYS * 86400.0)).fetchall()
    except sqlite3.Error:
        log.warning("Tutor associations store unreadable", exc_info=True)
        return ()
    audience_rank = {"user": 0, "instance_admin": 1}
    current_rank = audience_rank.get(audience, -1)
    for (scoped, row_hash, blob, dim, row_fingerprint, unit_id, unit_hash,
         row_audience, confirmations) in rows:
        expected_hash = hashes.get(unit_id)
        if expected_hash is None or (expected_hash and expected_hash != unit_hash):
            stale_rows.append((scoped, row_hash))
            continue
        if row_fingerprint != fingerprint:
            continue
        if audience_rank.get(str(row_audience), 99) > current_rank:
            continue
        stored = np.frombuffer(blob, dtype=np.float32)
        if stored.shape[0] != dim or dim != query.shape[0]:
            continue
        similarity = float(stored @ query)
        if similarity < floor:
            continue
        best = results.get(unit_id)
        if best is None or similarity > best[0]:
            results[unit_id] = (
                similarity,
                similarity >= strong
                and int(confirmations or 0) >= primary_confirmations,
                str(row_hash),
            )
    if stale_rows:
        try:
            with _connect() as connection:
                connection.executemany(
                    "DELETE FROM associations WHERE owner_hash=? AND query_hash=?",
                    stale_rows)
                connection.executemany(
                    "DELETE FROM association_confirmations "
                    "WHERE owner_hash=? AND query_hash=?",
                    stale_rows,
                )
        except sqlite3.Error:
            pass
    return tuple(sorted(
        ((unit_id, similarity, is_strong, row_hash)
         for unit_id, (similarity, is_strong, row_hash) in results.items()),
        key=lambda row: (-row[1], row[0]),
    ))


def match(
        query_vector: np.ndarray, fingerprint: str,
        known_units: dict[str, str] | frozenset[str], *,
        owner_user_id: str = "host", audience: str = "user",
        ) -> tuple[tuple[str, float, bool], ...]:
    """Compatibility projection without private contributor identifiers."""

    return tuple(
        (unit_id, similarity, strong)
        for unit_id, similarity, strong, _row_hash in match_with_evidence(
            query_vector, fingerprint, known_units,
            owner_user_id=owner_user_id, audience=audience,
        )
    )


def list_rows(*, owner_user_id: str) -> tuple[dict, ...]:
    """Private inspection API used by the counterfactual gate."""

    if not STORE_PATH.exists():
        return ()
    with _connect() as connection:
        now = time.time()
        scoped = owner_hash(owner_user_id)
        _prune(connection, now, scoped)
        rows = connection.execute(
            "SELECT query_hash, vector, dim, fingerprint, catalog_version,"
            " unit_id, unit_hash, audience, confirmations, created,"
            " last_confirmed FROM associations WHERE owner_hash=?"
            " AND last_confirmed>=?"
            " ORDER BY last_confirmed DESC",
            (scoped, now - _TTL_DAYS * 86400.0)).fetchall()
    return tuple({
        "query_hash": row[0], "vector": row[1], "dim": row[2],
        "fingerprint": row[3], "catalog_version": row[4],
        "unit_id": row[5], "unit_hash": row[6], "audience": row[7],
        "confirmations": row[8], "created": row[9],
        "last_confirmed": row[10],
    } for row in rows)


def purge_owner(*, owner_user_id: str) -> int:
    """Remove only one principal's learned associations."""

    scoped = owner_hash(owner_user_id)
    removed = _purge_legacy_owner(scoped)
    if not STORE_PATH.exists():
        return removed
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM associations WHERE owner_hash=?",
            (scoped,),
        )
        connection.execute(
            "DELETE FROM association_confirmations WHERE owner_hash=?",
            (scoped,),
        )
        return removed + max(0, int(cursor.rowcount))
