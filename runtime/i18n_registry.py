"""Versioned localization resource registry for RM-0005.

The registry is deliberately independent from any translation provider.  It
records what must be localized, leases bounded work, preserves source drift,
and exposes evidence used by the activation gate.  Canonical identifiers and
source hashes never depend on the target language.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import config as _C


import re

_HASH_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_STATUSES = frozenset({
    "pending", "leased", "translated", "admitted", "failed",
    "manual_review", "stale",
})
_READY_STATUSES = frozenset({"translated", "admitted", "manual_review"})
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 300


class RegistryError(RuntimeError):
    pass


class LeaseConflict(RegistryError):
    pass


@dataclass(frozen=True, slots=True)
class TranslationLease:
    resource_id: str
    layer: str
    source_lang: str
    target_lang: str
    source_hash: str
    attempt: int
    lease_token: str
    expires_at: float
    metadata: Mapping[str, Any]
    basis_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    resource_id: str
    layer: str
    source_lang: str
    target_lang: str
    source_hash: str
    status: str
    attempts: int
    translation_hash: str | None
    quality: str | None
    artifact_path: str | None
    last_error: str | None
    metadata: Mapping[str, Any]
    basis_id: str | None = None


@dataclass(frozen=True, slots=True)
class CoverageReport:
    target_lang: str
    total: int
    ready: int
    admitted: int
    by_status: Mapping[str, int]
    by_layer: Mapping[str, Mapping[str, int]]
    missing: tuple[str, ...]
    manual_review: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.ready == self.total

    @property
    def percent(self) -> float:
        return 100.0 if self.total == 0 else (100.0 * self.ready / self.total)


def normalize_language(value: str) -> str:
    candidate = _C.normalize_language_tag(value)
    if not candidate:
        raise ValueError(f"invalid BCP-47 language tag: {value!r}")
    return candidate


def _valid_hash(value: str, *, field: str) -> str:
    candidate = str(value or "").strip().lower()
    if not _HASH_RE.fullmatch(candidate):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return candidate.removeprefix("sha256:")


def _canonical_json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(value or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class LocalizationRegistry:
    def __init__(
        self,
        path: Path | str | None = None,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.path = Path(path or (_C.PATH_USER_STATE / "i18n_registry.sqlite"))
        self.max_attempts = max(1, int(max_attempts))
        self.lease_seconds = max(1, int(lease_seconds))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        deadline = time.monotonic() + 15.0
        while True:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    conn.close()
                    raise
                time.sleep(0.01)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS localization_resources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource_id TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    basis_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_token TEXT,
                    lease_expires_at REAL,
                    translation_hash TEXT,
                    quality TEXT,
                    artifact_path TEXT,
                    last_error TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(resource_id, target_lang, source_hash),
                    CHECK(status IN ('pending','leased','translated','admitted',
                                     'failed','manual_review','stale'))
                );
                CREATE INDEX IF NOT EXISTS idx_localization_claim
                    ON localization_resources(target_lang, status, attempts, id);
                CREATE INDEX IF NOT EXISTS idx_localization_current
                    ON localization_resources(resource_id, target_lang, updated_at DESC);
                CREATE TABLE IF NOT EXISTS localization_checks (
                    check_id TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence_hash TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(check_id, target_lang)
                );
                """
            )
            # Schema upgrades may be reached concurrently by the service and
            # an administration command.  Serialize the recheck and DDL in
            # SQLite instead of relying on a race-prone check-then-alter.
            conn.execute("BEGIN IMMEDIATE")
            try:
                columns = {
                    str(row[1])
                    for row in conn.execute(
                        "PRAGMA table_info(localization_resources)"
                    )
                }
                if "basis_id" not in columns:
                    conn.execute(
                        "ALTER TABLE localization_resources "
                        "ADD COLUMN basis_id TEXT"
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _record(row: sqlite3.Row) -> ResourceRecord:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        return ResourceRecord(
            resource_id=row["resource_id"], layer=row["layer"],
            source_lang=row["source_lang"], target_lang=row["target_lang"],
            source_hash=row["source_hash"], status=row["status"],
            attempts=int(row["attempts"]), translation_hash=row["translation_hash"],
            quality=row["quality"], artifact_path=row["artifact_path"],
            last_error=row["last_error"], metadata=metadata,
            basis_id=row["basis_id"],
        )

    def register(
        self,
        resource_id: str,
        layer: str,
        source_lang: str,
        target_lang: str,
        source_hash: str,
        *,
        basis_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        manual_review: bool = False,
    ) -> ResourceRecord:
        rid = str(resource_id or "").strip()
        layer_name = str(layer or "").strip()
        if not rid or not layer_name:
            raise ValueError("resource_id and layer are required")
        source = normalize_language(source_lang)
        target = normalize_language(target_lang)
        digest = _valid_hash(source_hash, field="source_hash")
        basis = None if basis_id is None else str(basis_id).strip()
        if basis_id is not None and not basis:
            raise ValueError("basis_id must be a non-empty identifier or None")
        now = time.time()
        status = "manual_review" if manual_review else "pending"
        encoded = _canonical_json(metadata)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """SELECT status,metadata_json,basis_id FROM localization_resources
                   WHERE resource_id=? AND target_lang=? AND source_hash=?""",
                (rid, target, digest),
            ).fetchone()
            basis_changed = (
                existing is not None
                and existing["basis_id"] != basis
            )
            conn.execute(
                """UPDATE localization_resources
                   SET status='stale', lease_token=NULL, lease_expires_at=NULL,
                       updated_at=?
                   WHERE resource_id=? AND target_lang=? AND source_hash<>?
                     AND status<>'stale'""",
                (now, rid, target, digest),
            )
            conn.execute(
                """INSERT INTO localization_resources
                   (resource_id,layer,source_lang,target_lang,source_hash,basis_id,
                    status,metadata_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(resource_id,target_lang,source_hash) DO UPDATE SET
                     layer=excluded.layer, source_lang=excluded.source_lang,
                     basis_id=excluded.basis_id,
                     metadata_json=excluded.metadata_json, updated_at=excluded.updated_at""",
                (rid, layer_name, source, target, digest, basis,
                 status, encoded, now, now),
            )
            if basis_changed:
                conn.execute(
                    """UPDATE localization_resources
                       SET status=?,attempts=0,lease_token=NULL,
                           lease_expires_at=NULL,translation_hash=NULL,
                           quality=NULL,artifact_path=NULL,last_error=NULL,
                           updated_at=?
                       WHERE resource_id=? AND target_lang=? AND source_hash=?""",
                    (status, now, rid, target, digest),
                )
            elif manual_review:
                conn.execute(
                    """UPDATE localization_resources SET status='manual_review',
                       lease_token=NULL,lease_expires_at=NULL,updated_at=?
                       WHERE resource_id=? AND target_lang=? AND source_hash=?""",
                    (now, rid, target, digest),
                )
            elif existing is not None and existing["status"] == "manual_review":
                try:
                    previous_metadata = json.loads(
                        existing["metadata_json"] or "{}"
                    )
                except (TypeError, json.JSONDecodeError):
                    previous_metadata = {}
                # A deliberate policy change reopens work.  A row escalated
                # after bounded provider failures remains manual and cannot be
                # reset merely by another idempotent inventory pass.
                if previous_metadata.get("review_policy") == "manual":
                    conn.execute(
                        """UPDATE localization_resources SET status='pending',
                           attempts=0,last_error=NULL,updated_at=?
                           WHERE resource_id=? AND target_lang=? AND source_hash=?""",
                        (now, rid, target, digest),
                    )
            row = conn.execute(
                """SELECT * FROM localization_resources
                   WHERE resource_id=? AND target_lang=? AND source_hash=?""",
                (rid, target, digest),
            ).fetchone()
            conn.commit()
        assert row is not None
        return self._record(row)

    def claim(self, resource_id: str, target_lang: str) -> TranslationLease | None:
        target = normalize_language(target_lang)
        rid = str(resource_id or "").strip()
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM localization_resources
                   WHERE resource_id=? AND target_lang=? AND status<>'stale'
                   ORDER BY id DESC LIMIT 1""",
                (rid, target),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            reclaimable = row["status"] == "leased" and float(row["lease_expires_at"] or 0) <= now
            if row["status"] not in {"pending", "failed"} and not reclaimable:
                conn.rollback()
                return None
            attempts = int(row["attempts"])
            if attempts >= self.max_attempts:
                conn.execute(
                    """UPDATE localization_resources SET status='manual_review',
                       lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?""",
                    (now, row["id"]),
                )
                conn.commit()
                return None
            token = secrets.token_urlsafe(24)
            expires = now + self.lease_seconds
            attempts += 1
            conn.execute(
                """UPDATE localization_resources SET status='leased', attempts=?,
                   lease_token=?,lease_expires_at=?,last_error=NULL,updated_at=?
                   WHERE id=?""",
                (attempts, token, expires, now, row["id"]),
            )
            conn.commit()
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        return TranslationLease(
            resource_id=rid, layer=row["layer"], source_lang=row["source_lang"],
            target_lang=target, source_hash=row["source_hash"], attempt=attempts,
            lease_token=token, expires_at=expires, metadata=metadata,
            basis_id=row["basis_id"],
        )

    def _leased_row(self, conn: sqlite3.Connection, resource_id: str, target: str) -> sqlite3.Row:
        row = conn.execute(
            """SELECT * FROM localization_resources
               WHERE resource_id=? AND target_lang=? AND status='leased'
               ORDER BY id DESC LIMIT 1""",
            (resource_id, target),
        ).fetchone()
        if row is None:
            raise LeaseConflict(f"no active lease for {resource_id!r}/{target}")
        return row

    @staticmethod
    def _verify_token(row: sqlite3.Row, lease_token: str | None) -> None:
        if lease_token is not None and not secrets.compare_digest(
            str(row["lease_token"] or ""), str(lease_token),
        ):
            raise LeaseConflict("lease token does not match the active claim")

    def complete(
        self,
        resource_id: str,
        target_lang: str,
        translation_hash: str,
        quality: str,
        *,
        lease_token: str | None = None,
        artifact_path: str | None = None,
    ) -> ResourceRecord:
        target = normalize_language(target_lang)
        digest = _valid_hash(translation_hash, field="translation_hash")
        quality_value = str(quality or "").strip()
        if not quality_value:
            raise ValueError("quality is required")
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._leased_row(conn, resource_id, target)
            self._verify_token(row, lease_token)
            conn.execute(
                """UPDATE localization_resources SET status='translated',
                   translation_hash=?,quality=?,artifact_path=?,lease_token=NULL,
                   lease_expires_at=NULL,last_error=NULL,updated_at=? WHERE id=?""",
                (digest, quality_value, artifact_path, now, row["id"]),
            )
            updated = conn.execute(
                "SELECT * FROM localization_resources WHERE id=?", (row["id"],),
            ).fetchone()
            conn.commit()
        assert updated is not None
        return self._record(updated)

    def fail(
        self,
        resource_id: str,
        target_lang: str,
        error_class: str,
        *,
        lease_token: str | None = None,
    ) -> ResourceRecord:
        target = normalize_language(target_lang)
        error = str(error_class or "translation_failed").strip()[:200]
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._leased_row(conn, resource_id, target)
            self._verify_token(row, lease_token)
            status = "manual_review" if int(row["attempts"]) >= self.max_attempts else "failed"
            conn.execute(
                """UPDATE localization_resources SET status=?,last_error=?,
                   lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?""",
                (status, error, now, row["id"]),
            )
            updated = conn.execute(
                "SELECT * FROM localization_resources WHERE id=?", (row["id"],),
            ).fetchone()
            conn.commit()
        assert updated is not None
        return self._record(updated)

    def admit(self, resource_id: str, target_lang: str) -> ResourceRecord:
        target = normalize_language(target_lang)
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM localization_resources
                   WHERE resource_id=? AND target_lang=? AND status='translated'
                   ORDER BY id DESC LIMIT 1""",
                (resource_id, target),
            ).fetchone()
            if row is None:
                raise RegistryError(f"translated resource unavailable: {resource_id}/{target}")
            conn.execute(
                "UPDATE localization_resources SET status='admitted',updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            updated = conn.execute(
                "SELECT * FROM localization_resources WHERE id=?", (row["id"],),
            ).fetchone()
            conn.commit()
        assert updated is not None
        return self._record(updated)

    def review(
        self,
        resource_id: str,
        target_lang: str,
        *,
        quality: str = "reviewed",
    ) -> ResourceRecord:
        """Record a quality decision without admitting the artifact yet."""
        target = normalize_language(target_lang)
        value = str(quality or "").strip()
        if not value:
            raise ValueError("quality is required")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM localization_resources
                   WHERE resource_id=? AND target_lang=? AND status='translated'
                   ORDER BY id DESC LIMIT 1""",
                (resource_id, target),
            ).fetchone()
            if row is None:
                raise RegistryError(f"translated resource unavailable: {resource_id}/{target}")
            conn.execute(
                "UPDATE localization_resources SET quality=?,updated_at=? WHERE id=?",
                (value, time.time(), row["id"]),
            )
            updated = conn.execute(
                "SELECT * FROM localization_resources WHERE id=?", (row["id"],),
            ).fetchone()
            conn.commit()
        assert updated is not None
        return self._record(updated)

    def record_check(
        self,
        check_id: str,
        target_lang: str,
        status: str,
        *,
        evidence_hash: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        target = normalize_language(target_lang)
        status_value = str(status or "").strip()
        if status_value not in {"passed", "failed", "pending"}:
            raise ValueError("check status must be passed, failed, or pending")
        digest = _valid_hash(evidence_hash, field="evidence_hash") if evidence_hash else None
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO localization_checks
                   (check_id,target_lang,status,evidence_hash,details_json,updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(check_id,target_lang) DO UPDATE SET
                     status=excluded.status,evidence_hash=excluded.evidence_hash,
                     details_json=excluded.details_json,updated_at=excluded.updated_at""",
                (str(check_id), target, status_value, digest,
                 _canonical_json(details), time.time()),
            )

    def checks(self, target_lang: str) -> dict[str, dict[str, Any]]:
        target = normalize_language(target_lang)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM localization_checks WHERE target_lang=? ORDER BY check_id",
                (target,),
            ).fetchall()
        return {
            row["check_id"]: {
                "status": row["status"], "evidence_hash": row["evidence_hash"],
                "details": json.loads(row["details_json"] or "{}"),
            }
            for row in rows
        }

    def resources(self, target_lang: str, *, current_only: bool = True) -> tuple[ResourceRecord, ...]:
        target = normalize_language(target_lang)
        query = "SELECT * FROM localization_resources WHERE target_lang=?"
        params: tuple[Any, ...] = (target,)
        if current_only:
            query += " AND status<>'stale'"
        query += " ORDER BY layer,resource_id,id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return tuple(self._record(row) for row in rows)

    def coverage(self, target_lang: str) -> CoverageReport:
        target = normalize_language(target_lang)
        records = self.resources(target)
        by_status_counter = Counter(record.status for record in records)
        layer_counters: dict[str, Counter[str]] = {}
        for record in records:
            layer_counters.setdefault(record.layer, Counter())[record.status] += 1
        ready = sum(1 for record in records if record.status in _READY_STATUSES)
        return CoverageReport(
            target_lang=target, total=len(records), ready=ready,
            admitted=by_status_counter.get("admitted", 0),
            by_status=dict(sorted(by_status_counter.items())),
            by_layer={key: dict(sorted(value.items())) for key, value in sorted(layer_counters.items())},
            missing=tuple(record.resource_id for record in records if record.status not in _READY_STATUSES),
            manual_review=tuple(record.resource_id for record in records if record.status == "manual_review"),
        )


def _default() -> LocalizationRegistry:
    return LocalizationRegistry()


def register(
    resource_id: str,
    layer: str,
    source_lang: str,
    target_lang: str,
    source_hash: str,
    *,
    basis_id: str | None = None,
) -> ResourceRecord:
    return _default().register(
        resource_id,
        layer,
        source_lang,
        target_lang,
        source_hash,
        basis_id=basis_id,
    )


def claim(resource_id: str, target_lang: str) -> TranslationLease | None:
    return _default().claim(resource_id, target_lang)


def complete(resource_id: str, target_lang: str, translation_hash: str, quality: str) -> ResourceRecord:
    return _default().complete(resource_id, target_lang, translation_hash, quality)


def fail(resource_id: str, target_lang: str, error_class: str) -> ResourceRecord:
    return _default().fail(resource_id, target_lang, error_class)


def coverage(target_lang: str) -> CoverageReport:
    return _default().coverage(target_lang)
