"""Privacy-bounded F4 evidence, gap ledger, and semantic debt map."""

from __future__ import annotations

from collections import defaultdict
import json
import os
import sqlite3
import time

import numpy as np

import config
from logging_setup import get_logger

from . import associations
from .models import TutorAnswer, TutorRequest

log = get_logger(__name__)

STORE_PATH = config.PATH_USER_DATA / "tutor_learning.sqlite"

GAP_REASONS = frozenset({
    "no_source",
    "restricted_source",
    "stale_source",
    "source_conflict",
    "composer_insufficient",
    "composer_incomplete",
    "composer_unavailable",
    "mode_ambiguity",
    "feedback_negative",
    "weak_language",
    "live_observation_incomplete",
    "source_unavailable",
    "mode_unavailable",
    "source_incomplete",
})

_EVIDENCE_TTL_DAYS = 7.0
_GAP_TTL_DAYS = 30.0
_COUNTER_TTL_DAYS = 90.0
_MAX_EVIDENCE_PER_OWNER = 1000
_MAX_GAPS_PER_OWNER = 1000
_MAX_COUNTERS_PER_OWNER = 2000
_RECEIPT_TTL_DAYS = 30.0
_MAX_RECEIPTS_PER_OWNER = 2000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turn_evidence (
    turn_id TEXT PRIMARY KEY,
    owner_hash TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    vector BLOB NOT NULL,
    dim INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    primary_source_id TEXT NOT NULL,
    primary_content_hash TEXT NOT NULL,
    audience TEXT NOT NULL,
    lang TEXT NOT NULL,
    eligible INTEGER NOT NULL,
    association_contributors_json TEXT NOT NULL DEFAULT '[]',
    created REAL NOT NULL,
    expires REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tutor_evidence_owner_expiry
ON turn_evidence(owner_hash, expires);

CREATE TABLE IF NOT EXISTS gap_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    owner_hash TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    vector BLOB,
    dim INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    lang TEXT NOT NULL,
    audience TEXT NOT NULL,
    reason TEXT NOT NULL,
    source_ids_json TEXT NOT NULL,
    created REAL NOT NULL,
    expires REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tutor_gaps_owner_reason
ON gap_events(owner_hash, reason, created DESC);

CREATE TABLE IF NOT EXISTS query_counters (
    owner_hash TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    outcome TEXT NOT NULL,
    count INTEGER NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    PRIMARY KEY(owner_hash, query_hash, outcome)
);
CREATE TABLE IF NOT EXISTS tutor_feedback_receipts (
    owner_hash TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('ok','error')),
    revision INTEGER NOT NULL DEFAULT 1,
    first_applied REAL NOT NULL,
    last_applied REAL NOT NULL,
    expires REAL NOT NULL,
    PRIMARY KEY(owner_hash, turn_id)
);
"""


def _connect() -> sqlite3.Connection:
    if STORE_PATH != associations.STORE_PATH:
        raise RuntimeError("Tutor learning and association stores diverged")
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(STORE_PATH)
    connection.executescript(_SCHEMA)
    receipt_columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(tutor_feedback_receipts)").fetchall()
    }
    if "expires" not in receipt_columns:
        connection.execute(
            "ALTER TABLE tutor_feedback_receipts "
            "ADD COLUMN expires REAL NOT NULL DEFAULT 0")
        connection.execute(
            "UPDATE tutor_feedback_receipts SET expires=last_applied+?",
            (_RECEIPT_TTL_DAYS * 86400.0,))
    evidence_columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(turn_evidence)").fetchall()
    }
    if "association_contributors_json" not in evidence_columns:
        connection.execute(
            "ALTER TABLE turn_evidence ADD COLUMN "
            "association_contributors_json TEXT NOT NULL DEFAULT '[]'")
    associations.ensure_schema(connection)
    try:
        os.chmod(STORE_PATH, 0o600)
    except OSError:
        pass
    return connection


def _prune(connection: sqlite3.Connection, now: float,
           scoped_owner: str) -> None:
    connection.execute("DELETE FROM turn_evidence WHERE expires < ?", (now,))
    connection.execute("DELETE FROM gap_events WHERE expires < ?", (now,))
    connection.execute(
        "DELETE FROM tutor_feedback_receipts WHERE expires < ?", (now,))
    connection.execute(
        "DELETE FROM query_counters WHERE last_seen < ?",
        (now - _COUNTER_TTL_DAYS * 86400.0,))
    for table, order_col, cap in (
            ("turn_evidence", "created", _MAX_EVIDENCE_PER_OWNER),
            ("gap_events", "created", _MAX_GAPS_PER_OWNER),
            ("query_counters", "last_seen", _MAX_COUNTERS_PER_OWNER)):
        connection.execute(
            f"DELETE FROM {table} WHERE rowid IN ("
            f" SELECT rowid FROM {table} WHERE owner_hash=?"
            f" ORDER BY {order_col} DESC LIMIT -1 OFFSET ?)",
            (scoped_owner, cap))
    connection.execute(
        "DELETE FROM tutor_feedback_receipts WHERE rowid IN ("
        " SELECT rowid FROM tutor_feedback_receipts WHERE owner_hash=?"
        " ORDER BY last_applied DESC LIMIT -1 OFFSET ?)",
        (scoped_owner, _MAX_RECEIPTS_PER_OWNER),
    )


def _vector_blob(values) -> tuple[bytes, int]:
    vector = np.asarray(values, dtype=np.float32).reshape(-1)
    if not vector.size or not np.isfinite(vector).all():
        return b"", 0
    return vector.tobytes(), int(vector.shape[0])


def _insert_gap(connection: sqlite3.Connection, *, turn_id: str,
                scoped_owner: str, normalized_query_hash: str,
                vector_blob: bytes, dim: int, fingerprint: str,
                catalog_version: str, lang: str, audience: str,
                reason: str, source_ids: tuple[str, ...], now: float) -> None:
    if reason not in GAP_REASONS:
        raise ValueError(f"unknown Tutor gap reason: {reason}")
    connection.execute(
        "INSERT INTO gap_events (turn_id,owner_hash,query_hash,vector,dim,"
        " fingerprint,catalog_version,lang,audience,reason,source_ids_json,"
        " created,expires) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (turn_id, scoped_owner, normalized_query_hash,
         vector_blob if dim else None, dim, fingerprint, catalog_version,
         lang, audience, reason,
         json.dumps(list(source_ids), ensure_ascii=True, separators=(",", ":")),
         now, now + _GAP_TTL_DAYS * 86400.0))


def record_turn(request: TutorRequest, answer: TutorAnswer) -> dict:
    """Persist bounded evidence after telemetry assigned a real turn ID."""

    return record_turn_hashed(
        owner_user_id=request.principal.user_id,
        normalized_query_hash=associations.query_hash(
            request.query_redacted),
        lang=request.lang,
        audience=request.principal.audience,
        answer=answer,
    )


def record_turn_hashed(*, owner_user_id: str,
                       normalized_query_hash: str, lang: str,
                       audience: str, answer: TutorAnswer) -> dict:
    """Persist evidence from a query-minimized post-commit job."""

    if not answer.turn_id:
        return {"recorded": False, "reason": "missing_turn_id"}
    now = time.time()
    scoped = associations.owner_hash(owner_user_id)
    normalized_hash = str(normalized_query_hash or "")
    if not normalized_hash:
        return {"recorded": False, "reason": "missing_query_hash"}
    evidence = answer.evidence
    blob, dim = _vector_blob(
        evidence.query_vector if evidence is not None else ())
    fingerprint = evidence.embedding_fingerprint if evidence else ""
    catalog_version = evidence.catalog_version if evidence else ""
    with _connect() as connection:
        connection.execute(
            "INSERT INTO query_counters (owner_hash,query_hash,outcome,count,"
            " first_seen,last_seen) VALUES (?,?,?,1,?,?)"
            " ON CONFLICT(owner_hash,query_hash,outcome) DO UPDATE SET"
            " count=count+1,last_seen=excluded.last_seen",
            (scoped, normalized_hash, answer.esito, now, now))
        if evidence is not None and dim:
            connection.execute(
                "INSERT OR REPLACE INTO turn_evidence (turn_id,owner_hash,"
                " query_hash,vector,dim,fingerprint,catalog_version,"
                " primary_source_id,primary_content_hash,audience,lang,"
                " eligible,association_contributors_json,created,expires) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (answer.turn_id, scoped, normalized_hash, blob, dim,
                 fingerprint, catalog_version, evidence.primary_source_id,
                 evidence.primary_content_hash, audience,
                 lang, int(evidence.eligible_for_association),
                 json.dumps(
                     list(evidence.association_contributor_hashes),
                     ensure_ascii=True, separators=(",", ":")),
                 now,
                 now + _EVIDENCE_TTL_DAYS * 86400.0))
        if answer.gap_reason:
            _insert_gap(
                connection,
                turn_id=answer.turn_id,
                scoped_owner=scoped,
                normalized_query_hash=normalized_hash,
                vector_blob=blob,
                dim=dim,
                fingerprint=fingerprint,
                catalog_version=catalog_version,
                lang=lang,
                audience=audience,
                reason=answer.gap_reason,
                source_ids=answer.source_ids,
                now=now,
            )
        _prune(connection, now, scoped)
    return {"recorded": True, "gap": answer.gap_reason or ""}


def _evidence_for_turn(connection: sqlite3.Connection, turn_id: str,
                       scoped_owner: str) -> dict | None:
    row = connection.execute(
        "SELECT query_hash,vector,dim,fingerprint,catalog_version,"
        " primary_source_id,primary_content_hash,audience,lang,eligible,"
        " association_contributors_json,"
        " created,expires FROM turn_evidence"
        " WHERE turn_id=? AND owner_hash=?",
        (turn_id, scoped_owner)).fetchone()
    if row is None:
        return None
    keys = ("query_hash", "vector", "dim", "fingerprint",
            "catalog_version", "primary_source_id", "primary_content_hash",
            "audience", "lang", "eligible", "association_contributors_json",
            "created", "expires")
    return dict(zip(keys, row))


def apply_feedback(turn: dict, action: str) -> tuple[dict, ...]:
    """Atomically apply one owner-scoped, last-write-wins Tutor verdict."""

    if turn.get("mode") != "tutor" or action not in {"ok", "error"}:
        return ()
    owner_user_id = str(turn.get("owner_user_id") or turn.get("actor") or "")
    if not owner_user_id:
        return ()
    scoped = associations.owner_hash(owner_user_id)
    turn_id = str(turn.get("turn_id") or "")
    if not turn_id:
        return ()
    now = time.time()
    effects: list[dict] = []
    with _connect() as connection:
        # Expiry is an authorization boundary for learning, not merely GC.
        # Prune before lookup so delayed feedback cannot revive old evidence.
        _prune(connection, now, scoped)
        evidence = _evidence_for_turn(connection, turn_id, scoped)
        fallback_hash = str(turn.get("tutor_query_hash") or "")
        if fallback_hash.startswith("sha256:"):
            fallback_hash = fallback_hash[7:]
        normalized_hash = (
            evidence.get("query_hash") if evidence else fallback_hash)
        if not normalized_hash:
            return ()
        prior = connection.execute(
            "SELECT action,revision FROM tutor_feedback_receipts "
            "WHERE owner_hash=? AND turn_id=?",
            (scoped, turn_id),
        ).fetchone()
        if prior is not None and str(prior[0]) == action:
            return ({
                "type": "tutor_feedback",
                "status": "already_applied",
                "action": action,
                "revision": int(prior[1]),
            },)
        if action == "ok":
            if evidence is None:
                return ({
                    "type": "tutor_feedback",
                    "status": "not_applicable",
                    "action": action,
                    "reason": "evidence_missing_or_expired",
                },)
            if not evidence.get("eligible"):
                return ({
                    "type": "tutor_feedback",
                    "status": "not_applicable",
                    "action": action,
                    "reason": "response_not_eligible",
                },)
            connection.execute(
                "DELETE FROM gap_events WHERE turn_id=? AND owner_hash=?"
                " AND reason='feedback_negative'",
                (turn_id, scoped),
            )
            source_id = str(evidence.get("primary_source_id") or "")
            if not source_id.startswith("knowledge:"):
                return ({
                    "type": "tutor_feedback",
                    "status": "not_applicable",
                    "action": action,
                    "reason": "source_not_learnable",
                },)
            unit_id = source_id.split(":", 1)[1]
            changed = associations._record_confirmation_hash(
                connection,
                owner_user_id=owner_user_id,
                normalized_query_hash=normalized_hash,
                vector=np.frombuffer(
                    evidence["vector"], dtype=np.float32),
                fingerprint=str(evidence["fingerprint"]),
                catalog_version=str(evidence["catalog_version"]),
                unit_id=unit_id,
                unit_hash=str(evidence["primary_content_hash"]),
                audience=str(evidence["audience"]),
                confirmation_id=turn_id,
            )
            if changed:
                effects.append({
                    "type": "tutor_association_promoted",
                    "status": "applied",
                    "unit_id": unit_id,
                })
        elif action == "error":
            contributors = ()
            if evidence:
                try:
                    contributors = tuple(json.loads(str(
                        evidence.get("association_contributors_json") or "[]")))
                except (TypeError, ValueError, json.JSONDecodeError):
                    contributors = ()
            removed = associations._record_negative_hashes(
                connection,
                owner_user_id=owner_user_id,
                normalized_query_hashes=tuple((
                    normalized_hash,
                    *(str(value) for value in contributors if str(value)),
                )),
            )
            blob = evidence.get("vector", b"") if evidence else b""
            dim = int(evidence.get("dim", 0)) if evidence else 0
            # Feedback is last-write-wins.  Repeated clicks do not inflate
            # debt, and a later positive verdict can remove this event while
            # the short-lived evidence remains available for promotion.
            connection.execute(
                "DELETE FROM gap_events WHERE turn_id=? AND owner_hash=?"
                " AND reason='feedback_negative'",
                (turn_id, scoped),
            )
            _insert_gap(
                connection,
                turn_id=turn_id,
                scoped_owner=scoped,
                normalized_query_hash=normalized_hash,
                vector_blob=blob,
                dim=dim,
                fingerprint=str(evidence.get("fingerprint", "")
                                if evidence else ""),
                catalog_version=str(evidence.get("catalog_version", "")
                                    if evidence else ""),
                lang=str(evidence.get("lang", "") if evidence else ""),
                audience=str(evidence.get("audience", "")
                             if evidence else ""),
                reason="feedback_negative",
                source_ids=tuple(turn.get("tutor_source_ids") or ()),
                now=now,
            )
            effects.append({
                "type": "tutor_negative_recorded",
                "status": "applied",
                "association_rows_removed": removed,
            })
        revision = int(prior[1]) + 1 if prior is not None else 1
        connection.execute(
            "INSERT INTO tutor_feedback_receipts "
            "(owner_hash,turn_id,query_hash,action,revision,first_applied,"
            "last_applied,expires) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(owner_hash,turn_id) DO UPDATE SET "
            "query_hash=excluded.query_hash,action=excluded.action,"
            "revision=excluded.revision,last_applied=excluded.last_applied,"
            "expires=excluded.expires",
            (scoped, turn_id, normalized_hash, action, revision, now, now,
             now + _RECEIPT_TTL_DAYS * 86400.0),
        )
        if not effects:
            effects.append({
                "type": "tutor_feedback",
                "status": "applied",
                "action": action,
                "revision": revision,
            })
        _prune(connection, now, scoped)
    return tuple(effects)


def debt_map(*, owner_user_id: str, similarity_floor: float = 0.88
             ) -> tuple[dict, ...]:
    """Group active gaps by closed cause and semantic neighborhood."""

    scoped = associations.owner_hash(owner_user_id)
    now = time.time()
    with _connect() as connection:
        _prune(connection, now, scoped)
        rows = connection.execute(
            "SELECT query_hash,vector,dim,fingerprint,lang,audience,reason,"
            " source_ids_json,created FROM gap_events"
            " WHERE owner_hash=? AND expires>=? ORDER BY created",
            (scoped, now)).fetchall()
    groups: list[dict] = []
    by_shape: dict[tuple[str, str, int], list[dict]] = defaultdict(list)
    for row in rows:
        event = {
            "query_hash": row[0], "vector": row[1], "dim": int(row[2]),
            "fingerprint": row[3], "lang": row[4], "audience": row[5],
            "reason": row[6], "source_ids": tuple(json.loads(row[7] or "[]")),
            "created": float(row[8]),
        }
        by_shape[(event["reason"], event["fingerprint"], event["dim"])].append(
            event)
    floor = max(0.5, min(0.999, float(similarity_floor)))
    for (reason, fingerprint, dim), events in sorted(by_shape.items()):
        clusters: list[list[dict]] = []
        centroids: list[np.ndarray | None] = []
        for event in events:
            vector = (np.frombuffer(event["vector"], dtype=np.float32)
                      if event["vector"] is not None and dim else None)
            target = None
            if vector is not None and vector.shape[0] == dim:
                similarities = [
                    float(centroid @ vector) if centroid is not None else -1.0
                    for centroid in centroids
                ]
                if similarities and max(similarities) >= floor:
                    target = int(np.argmax(similarities))
            elif vector is None:
                # Without a semantic vector we can still aggregate exact
                # recurrence by its normalized, non-reversible query hash.
                target = next(
                    (index for index, cluster in enumerate(clusters)
                     if centroids[index] is None
                     and cluster[0]["query_hash"] == event["query_hash"]),
                    None,
                )
            if target is None:
                clusters.append([event])
                centroids.append(vector.copy() if vector is not None else None)
            else:
                clusters[target].append(event)
                if vector is not None:
                    stack = np.vstack([
                        np.frombuffer(item["vector"], dtype=np.float32)
                        for item in clusters[target]
                    ])
                    centroid = stack.mean(axis=0)
                    norm = float(np.linalg.norm(centroid))
                    centroids[target] = (
                        centroid / norm if norm > 1e-8 else None)
        for cluster in clusters:
            groups.append({
                "reason": reason,
                "count": len(cluster),
                "languages": sorted({item["lang"] for item in cluster}),
                "audiences": sorted({item["audience"] for item in cluster}),
                "source_ids": sorted({source for item in cluster
                                      for source in item["source_ids"]}),
                "first_seen": min(item["created"] for item in cluster),
                "last_seen": max(item["created"] for item in cluster),
                "query_hashes": tuple(item["query_hash"] for item in cluster[:5]),
                "embedding_fingerprint": fingerprint,
            })
    return tuple(sorted(groups, key=lambda group: (
        -group["count"], group["reason"], group["first_seen"])))


def purge_owner(*, owner_user_id: str) -> dict:
    """Verifiably remove one user's F4 ledger and learned associations."""

    scoped = associations.owner_hash(owner_user_id)
    deleted = {}
    with _connect() as connection:
        for table in (
                "turn_evidence", "gap_events", "query_counters",
                "tutor_feedback_receipts"):
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE owner_hash=?", (scoped,))
            deleted[table] = cursor.rowcount
    deleted["associations"] = associations.purge_owner(
        owner_user_id=owner_user_id)
    return deleted
