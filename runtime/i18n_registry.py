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
import stat
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


class CandidateConflict(RegistryError):
    """The reviewed candidate is no longer the current registry observation."""


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
    contract_id: str | None = None


@dataclass(frozen=True, slots=True)
class PublishedTranslation:
    """One translation proven live by an authenticated contract snapshot.

    This is reconciliation input, not a translation candidate.  Its hashes
    are checked again before the registry changes state.
    """

    resource_id: str
    source_lang: str
    target_lang: str
    source_hash: str
    translation_hash: str
    basis_id: str
    metadata: Mapping[str, Any]


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


def _contract_identity(value: object) -> str:
    """Preserve the exact structural identity authenticated by the store.

    The registry intentionally treats ContractId as opaque: it must never
    reconstruct one from a display name or a localization resource ID.
    """
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or ":" not in value
    ):
        raise ValueError("contract_id must be an exact structural contract identity")
    return value


def _published_contract_owners(
    assignments: tuple[tuple[str, tuple[str, ...]], ...],
) -> dict[str, str]:
    if not isinstance(assignments, tuple) or not assignments:
        raise ValueError("assignments must contain published contracts")
    owners: dict[str, str] = {}
    contracts: set[str] = set()
    for raw_contract_id, resource_ids in assignments:
        contract_id = _contract_identity(raw_contract_id)
        if contract_id in contracts:
            raise ValueError("published ContractId is duplicated")
        contracts.add(contract_id)
        if (
            not isinstance(resource_ids, tuple)
            or not resource_ids
            or len(set(resource_ids)) != len(resource_ids)
        ):
            raise ValueError("resource_ids must be unique contract resource IDs")
        for resource_id in resource_ids:
            if (
                not isinstance(resource_id, str)
                or not resource_id.startswith("contract:")
            ):
                raise ValueError(
                    "resource_ids must be unique contract resource IDs"
                )
            previous = owners.setdefault(resource_id, contract_id)
            if previous != contract_id:
                raise RegistryError(
                    "contract resource identity collides with another "
                    "ContractId"
                )
    return owners


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
                    contract_id TEXT,
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
                if "contract_id" not in columns:
                    conn.execute(
                        "ALTER TABLE localization_resources "
                        "ADD COLUMN contract_id TEXT"
                    )
                # Existing canonical contract rows already carry the exact
                # structural identity in metadata.  Backfill that authority
                # once; never infer it from the executor name embedded in the
                # resource ID.
                for row in conn.execute(
                    "SELECT id,metadata_json FROM localization_resources "
                    "WHERE layer='contract' AND contract_id IS NULL"
                ):
                    try:
                        metadata = json.loads(row["metadata_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        metadata = {}
                    contract_id = (
                        metadata.get("contract_id")
                        if isinstance(metadata, Mapping)
                        else None
                    )
                    try:
                        canonical_contract_id = _contract_identity(contract_id)
                    except ValueError:
                        continue
                    else:
                        conn.execute(
                            "UPDATE localization_resources SET contract_id=? "
                            "WHERE id=?",
                            (canonical_contract_id, int(row["id"])),
                        )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_localization_contract "
                    "ON localization_resources(contract_id,status,id)"
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
            contract_id=row["contract_id"],
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
        contract_id: str | None = None
        if layer_name == "contract":
            raw_contract_id = dict(metadata or {}).get("contract_id")
            if raw_contract_id is not None:
                contract_id = _contract_identity(raw_contract_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """SELECT status,metadata_json,basis_id,contract_id,layer,source_lang
                   FROM localization_resources
                   WHERE resource_id=? AND target_lang=? AND source_hash=?""",
                (rid, target, digest),
            ).fetchone()
            if (
                existing is not None
                and existing["contract_id"] is not None
                and existing["contract_id"] != contract_id
            ):
                conn.rollback()
                raise RegistryError(
                    "registered resource belongs to another ContractId"
                )
            work_must_reopen = (
                existing is not None
                and (
                    existing["basis_id"] != basis
                    or existing["contract_id"] != contract_id
                    or existing["layer"] != layer_name
                    or existing["source_lang"] != source
                    or existing["status"] == "stale"
                )
            )
            # Metadata describes the current route/context, not the semantic
            # translation identity.  Refresh it without discarding admitted
            # work when source, layer, language and basis are unchanged.  The
            # exact review/admission CAS still includes metadata, so an
            # in-flight judgment made against old context is rejected.
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
                    contract_id,status,metadata_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(resource_id,target_lang,source_hash) DO UPDATE SET
                     layer=excluded.layer, source_lang=excluded.source_lang,
                     basis_id=excluded.basis_id,
                     contract_id=excluded.contract_id,
                     metadata_json=excluded.metadata_json, updated_at=excluded.updated_at""",
                (rid, layer_name, source, target, digest, basis, contract_id,
                 status, encoded, now, now),
            )
            if work_must_reopen:
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

    def reconcile_published_contract(
        self,
        contract_id: str,
        resource_ids: tuple[str, ...],
        publications: tuple[PublishedTranslation, ...],
    ) -> tuple[ResourceRecord, ...]:
        """Make registry state match one authenticated live contract.

        A generation change invalidates every in-flight candidate for the
        contract.  Translations whose source and target hashes are present in
        the signed snapshot are then restored as ``admitted`` against the new
        generation.  The whole contract is reconciled in one SQLite
        transaction, so a partial registry view is never exposed.
        """

        exact_contract_id = _contract_identity(contract_id)
        if (
            not isinstance(resource_ids, tuple)
            or not resource_ids
            or any(
                not isinstance(resource_id, str)
                or not resource_id.startswith("contract:")
                for resource_id in resource_ids
            )
            or len(set(resource_ids)) != len(resource_ids)
        ):
            raise ValueError("resource_ids must be unique contract resource IDs")
        allowed = set(resource_ids)
        normalized: list[tuple[PublishedTranslation, str, str, str, str, str]] = []
        identities: set[tuple[str, str]] = set()
        bases: set[str] = set()
        for publication in publications:
            if not isinstance(publication, PublishedTranslation):
                raise TypeError("publications must contain PublishedTranslation values")
            if publication.resource_id not in allowed:
                raise ValueError("published resource is outside the reconciled contract")
            if _contract_identity(
                dict(publication.metadata).get("contract_id"),
            ) != exact_contract_id:
                raise ValueError("published resource belongs to another contract")
            source = normalize_language(publication.source_lang)
            target = normalize_language(publication.target_lang)
            if source == target:
                raise ValueError("published translation source and target must differ")
            source_hash = _valid_hash(publication.source_hash, field="source_hash")
            translation_hash = _valid_hash(
                publication.translation_hash, field="translation_hash",
            )
            basis = str(publication.basis_id or "").strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", basis):
                raise ValueError("basis_id must be a canonical SHA-256 identifier")
            identity = (publication.resource_id, target)
            if identity in identities:
                raise ValueError("published translation identity is duplicated")
            identities.add(identity)
            bases.add(basis)
            normalized.append((
                publication, source, target, source_hash, translation_hash,
                _canonical_json(publication.metadata),
            ))
        if len(bases) > 1:
            raise ValueError("published translations must share one generation")

        now = time.time()
        updated_ids: list[int] = []
        placeholders = ",".join("?" for _ in resource_ids)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            collisions = conn.execute(
                f"""SELECT DISTINCT contract_id FROM localization_resources
                    WHERE resource_id IN ({placeholders})
                      AND contract_id IS NOT NULL AND contract_id<>?""",
                (*resource_ids, exact_contract_id),
            ).fetchall()
            if collisions:
                conn.rollback()
                raise RegistryError(
                    "contract resource identity collides with another ContractId"
                )
            # A candidate is tied to its observed generation.  Never carry a
            # lease or unadmitted artifact across the publication boundary.
            # ContractId also catches selectors removed by this generation;
            # the resource list adopts pre-index legacy rows only after the
            # collision check above.
            conn.execute(
                f"""UPDATE localization_resources
                    SET status='stale',lease_token=NULL,lease_expires_at=NULL,
                        updated_at=?
                    WHERE (contract_id=? OR
                           (contract_id IS NULL AND resource_id IN ({placeholders})))
                      AND status<>'stale'""",
                (now, exact_contract_id, *resource_ids),
            )
            for publication, source, target, source_hash, translation_hash, metadata in normalized:
                conn.execute(
                    """INSERT INTO localization_resources
                       (resource_id,layer,source_lang,target_lang,source_hash,
                        basis_id,contract_id,status,attempts,translation_hash,quality,
                        artifact_path,last_error,metadata_json,created_at,updated_at)
                       VALUES (?,'contract',?,?,?,?,?,'admitted',0,?,'published',
                               NULL,NULL,?,?,?)
                       ON CONFLICT(resource_id,target_lang,source_hash) DO UPDATE SET
                         layer='contract',source_lang=excluded.source_lang,
                         basis_id=excluded.basis_id,
                         contract_id=excluded.contract_id,status='admitted',
                         lease_token=NULL,lease_expires_at=NULL,
                         translation_hash=excluded.translation_hash,
                         quality=CASE
                           WHEN localization_resources.translation_hash=
                                excluded.translation_hash
                           THEN COALESCE(localization_resources.quality,'published')
                           ELSE 'published'
                         END,
                         artifact_path=NULL,last_error=NULL,
                         metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                    (
                        publication.resource_id, source, target, source_hash,
                        publication.basis_id, exact_contract_id,
                        translation_hash, metadata, now, now,
                    ),
                )
                row = conn.execute(
                    """SELECT id FROM localization_resources
                       WHERE resource_id=? AND target_lang=? AND source_hash=?""",
                    (publication.resource_id, target, source_hash),
                ).fetchone()
                assert row is not None
                updated_ids.append(int(row["id"]))
            rows = (
                conn.execute(
                    f"SELECT * FROM localization_resources WHERE id IN ({','.join('?' for _ in updated_ids)}) ORDER BY resource_id,target_lang",
                    tuple(updated_ids),
                ).fetchall()
                if updated_ids else []
            )
            conn.commit()
        return tuple(self._record(row) for row in rows)

    def preflight_published_contracts(
        self,
        assignments: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> None:
        """Check contract resource ownership without changing registry rows.

        Resource IDs are stable, name-derived localization identities.  Once
        any historical row associates one with a structural ContractId, a
        different ContractId must not acquire it during cutover.  The query
        deliberately includes ``stale`` rows: retirement changes liveness,
        not ownership of a historical localization identity.
        """

        owners = _published_contract_owners(assignments)

        # One unfiltered ownership scan avoids SQLite's bounded IN-list size
        # while still checking every historical status, including stale.
        # It is intentionally SELECT-only: activation performs no speculative
        # registry mutation before the filesystem boundary succeeds.
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT DISTINCT resource_id,contract_id
                   FROM localization_resources
                   WHERE contract_id IS NOT NULL"""
            ).fetchall()
        for row in rows:
            resource_id = str(row["resource_id"])
            expected = owners.get(resource_id)
            if expected is not None and row["contract_id"] != expected:
                raise RegistryError(
                    "contract resource identity collides with another "
                    "ContractId"
                )

    @classmethod
    def preflight_published_contract_path(
        cls,
        assignments: tuple[tuple[str, tuple[str, ...]], ...],
        *,
        registry_location: Path | str | None = None,
    ) -> None:
        """Inspect an existing registry through a SQLite read-only handle.

        A missing database has no historical owner and is compatible.  An
        older schema is inspected without migrating it; structural identities
        still present only in metadata are honored exactly as schema upgrade
        would honor them.
        """

        owners = _published_contract_owners(assignments)
        registry_path = Path(
            registry_location
            or (_C.PATH_USER_STATE / "i18n_registry.sqlite")
        )
        try:
            status = registry_path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RegistryError(
                f"localization registry cannot be inspected: {exc}"
            ) from exc
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
            raise RegistryError(
                "localization registry is not a regular file"
            )
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                registry_path.absolute().as_uri() + "?mode=ro",
                uri=True,
                timeout=15,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            table = connection.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type='table' AND name='localization_resources'"""
            ).fetchone()
            if table is None:
                return
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(localization_resources)"
                )
            }
            required = {"resource_id", "metadata_json"}
            if not required.issubset(columns):
                raise RegistryError(
                    "localization registry schema cannot prove ownership"
                )
            contract_column = (
                "contract_id" if "contract_id" in columns
                else "NULL AS contract_id"
            )
            rows = connection.execute(
                f"""SELECT resource_id,{contract_column},metadata_json
                    FROM localization_resources"""
            ).fetchall()
        except sqlite3.Error as exc:
            raise RegistryError(
                f"localization registry cannot be inspected: {exc}"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

        for row in rows:
            expected = owners.get(str(row["resource_id"]))
            if expected is None:
                continue
            registered = row["contract_id"]
            if registered is None:
                try:
                    metadata = json.loads(row["metadata_json"] or "{}")
                except (TypeError, json.JSONDecodeError) as exc:
                    raise RegistryError(
                        "localization registry metadata is invalid"
                    ) from exc
                registered = (
                    metadata.get("contract_id")
                    if isinstance(metadata, Mapping)
                    else None
                )
            if registered is None:
                continue
            try:
                registered = _contract_identity(registered)
            except ValueError as exc:
                raise RegistryError(
                    "localization registry ContractId is invalid"
                ) from exc
            if registered != expected:
                raise RegistryError(
                    "contract resource identity collides with another "
                    "ContractId"
                )

    def retire_published_contract(self, contract_id: str) -> int:
        """Atomically make every indexed resource of one ContractId non-live.

        A retirement contains no executor name or selector list.  The exact
        identity indexed while its manifest was live is therefore the sole
        authority.  Repeating the operation is a successful no-op.
        """
        exact_contract_id = _contract_identity(contract_id)
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """UPDATE localization_resources
                   SET status='stale',lease_token=NULL,lease_expires_at=NULL,
                       updated_at=?
                   WHERE contract_id=? AND status<>'stale'""",
                (now, exact_contract_id),
            )
            changed = int(cursor.rowcount)
            conn.commit()
        return changed

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

    def _exact_candidate_row(
        self,
        conn: sqlite3.Connection,
        expected: ResourceRecord,
        *,
        status: str,
    ) -> sqlite3.Row:
        """Resolve one candidate only if every observed identity still matches."""
        if not isinstance(expected, ResourceRecord):
            raise TypeError("expected must be a ResourceRecord")
        target = normalize_language(expected.target_lang)
        row = conn.execute(
            """SELECT * FROM localization_resources
               WHERE resource_id=? AND layer=? AND source_lang=?
                 AND target_lang=? AND source_hash=? AND status=?
                 AND basis_id IS ? AND translation_hash IS ?
                 AND quality IS ? AND artifact_path IS ? AND metadata_json=?
               ORDER BY id DESC LIMIT 1""",
            (
                expected.resource_id,
                expected.layer,
                expected.source_lang,
                target,
                expected.source_hash,
                status,
                expected.basis_id,
                expected.translation_hash,
                expected.quality,
                expected.artifact_path,
                _canonical_json(expected.metadata),
            ),
        ).fetchone()
        if row is None:
            raise CandidateConflict(
                "candidate observation is stale: "
                f"{expected.resource_id}/{target}"
            )
        return row

    def admit_candidate(self, expected: ResourceRecord) -> ResourceRecord:
        """Admit exactly the translated candidate previously inspected."""
        if not isinstance(expected, ResourceRecord):
            raise TypeError("expected must be a ResourceRecord")
        if expected.quality != "reviewed":
            raise CandidateConflict(
                "candidate has not been reviewed: "
                f"{expected.resource_id}/{expected.target_lang}"
            )
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._exact_candidate_row(conn, expected, status="translated")
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

    def review_candidate(
        self,
        expected: ResourceRecord,
        *,
        quality: str = "reviewed",
    ) -> ResourceRecord:
        """Review exactly the translated candidate whose artifact was judged."""
        value = str(quality or "").strip()
        if not value:
            raise ValueError("quality is required")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._exact_candidate_row(conn, expected, status="translated")
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
