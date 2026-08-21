"""Private, owner-scoped artifact storage with crash reconciliation.

The module is dormant: it opens no database, starts no worker and registers no
route at import time.  Callers provide already-authorized bytes or binary
streams; filesystem paths are never accepted as artifact input.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import BinaryIO

from .migrations import (
    CURRENT_SCHEMA_VERSION,
    migrate,
    open_db,
    schema_version as database_schema_version,
    utc_now,
)
from .models import ArtifactState, PublicationState


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_LOGICAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_MIME_RE = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")
_SCHEMA_RE = re.compile(r"^metnos\.[a-z0-9_.-]+/[1-9][0-9]*$")
_TARGET_RE = re.compile(r"^[a-z][a-z0-9_.:/-]{0,255}$")
_DIGEST_RE = re.compile(r"^sha256:([a-f0-9]{64})$")
_HEX_RE = re.compile(r"^[a-f0-9]{64}$")
_BLOB_TEMP_RE = re.compile(r"^\.blob-[a-f0-9]{32}\.tmp$")
_PUBLICATION_TEMP_RE = re.compile(r"^\.prepared-[a-f0-9]{64}$")
_CHUNK_BYTES = 1024 * 1024
_DEFAULT_MAX_BLOB_BYTES = 1024 * 1024 * 1024
_BLOB_REF_PREFIX = "metnos-owner-blob/"


class ArtifactError(RuntimeError):
    """Base error with a stable internal code, never user-facing prose."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ArtifactContractError(ArtifactError, ValueError):
    """Input does not satisfy the bounded internal contract."""


class ArtifactNotFoundError(ArtifactError, LookupError):
    """No owner-scoped artifact or publication matches the request."""


class ArtifactConflictError(ArtifactError):
    """An identifier or logical target was reused with different content."""


class ArtifactIntegrityError(ArtifactError):
    """Stored bytes do not match their registered digest and size."""


class ArtifactSecurityError(ArtifactError):
    """A path component is a symlink, the wrong type or otherwise unsafe."""


class ArtifactBudgetError(ArtifactContractError):
    """A new artifact would exceed or bypass frozen revision accounting."""


class RecoveryStatus(str, Enum):
    COMMITTED = "committed"
    RETRYABLE = "retryable"
    NEEDS_ATTENTION = "needs_attention"


@dataclass(frozen=True, slots=True)
class Blob:
    digest: str
    size_bytes: int
    blob_ref: str


@dataclass(frozen=True, slots=True)
class Artifact:
    owner_user_id: str
    artifact_id: str
    workload_id: str
    revision_id: str
    logical_name: str
    digest: str
    mime_type: str
    size_bytes: int
    schema_version: str
    state: ArtifactState
    blob_ref: str
    retention_until: str | None
    published_target_redacted: str | None
    digest_verified: bool
    schema_valid: bool
    postconditions_valid: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Publication:
    owner_user_id: str
    publication_id: str
    artifact_id: str
    target_key: str
    target_redacted: str
    state: PublicationState
    expected_digest: str
    observed_digest: str | None
    attempt_count: int
    prepared_at: str
    published_at: str | None


@dataclass(frozen=True, slots=True)
class PublicationRecovery:
    status: RecoveryStatus
    publication: Publication
    reason_code: str


@dataclass(frozen=True, slots=True)
class GarbageCollectionReport:
    scanned: int
    deleted: int
    referenced: int
    recent: int
    unsafe: int
    more: bool
    events: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OwnerDeletionReport:
    database_rows: int
    files: int
    directories: int


@dataclass(frozen=True, slots=True)
class ArtifactDownloadCapability:
    """Short-lived, owner-bound authorization for one registered artifact."""

    token: str
    owner_user_id: str
    artifact_id: str
    expires_at: datetime


class ArtifactDownloadRegistry:
    """In-memory capability registry with explicit expiration and revocation.

    Capabilities are deliberately not derivable from a filesystem path and are
    invalid after a process restart.  The artifact repository remains the
    second, persistent authorization check when bytes are opened.
    """

    def __init__(self, *, capacity: int = 8192) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 65536:
            raise ValueError("capacity must be an integer in 1..65536")
        self._entries: dict[str, ArtifactDownloadCapability] = {}
        self._by_artifact: dict[tuple[str, str], str] = {}
        self._expiry_heap: list[tuple[float, str]] = []
        self._capacity = capacity
        self._lock = threading.Lock()

    @staticmethod
    def _now(value: datetime | None) -> datetime:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("capability time must be timezone-aware")
        return current.astimezone(timezone.utc)

    def issue(
        self,
        owner_user_id: str,
        artifact_id: str,
        *,
        lifetime: timedelta = timedelta(minutes=5),
        now: datetime | None = None,
    ) -> ArtifactDownloadCapability:
        owner = _require_owner(owner_user_id)
        artifact = _require_id(artifact_id)
        if not isinstance(lifetime, timedelta) or not timedelta(seconds=1) <= lifetime <= timedelta(minutes=15):
            raise ArtifactContractError("artifact_download_lifetime_invalid")
        current = self._now(now)
        with self._lock:
            self._purge_expired(current)
            artifact_key = (owner, artifact)
            existing_token = self._by_artifact.get(artifact_key)
            existing = (
                self._entries.get(existing_token)
                if existing_token is not None else None
            )
            expires_at = current + lifetime
            if existing is not None:
                if existing.expires_at >= expires_at:
                    return existing
                capability = ArtifactDownloadCapability(
                    token=existing.token,
                    owner_user_id=owner,
                    artifact_id=artifact,
                    expires_at=expires_at,
                )
                self._entries[capability.token] = capability
                heapq.heappush(
                    self._expiry_heap,
                    (capability.expires_at.timestamp(), capability.token),
                )
                self._compact_expiry_heap()
                return capability
            if len(self._entries) >= self._capacity:
                raise ArtifactContractError("artifact_download_registry_full")
            token = ""
            for _attempt in range(8):
                token = secrets.token_urlsafe(32)
                if token not in self._entries:
                    break
            else:
                raise ArtifactContractError("artifact_download_token_unavailable")
            capability = ArtifactDownloadCapability(
                token=token,
                owner_user_id=owner,
                artifact_id=artifact,
                expires_at=expires_at,
            )
            self._entries[capability.token] = capability
            self._by_artifact[artifact_key] = capability.token
            heapq.heappush(
                self._expiry_heap,
                (capability.expires_at.timestamp(), capability.token),
            )
            self._compact_expiry_heap()
            return capability

    def resolve(
        self,
        token: str,
        *,
        owner_user_id: str,
        now: datetime | None = None,
    ) -> ArtifactDownloadCapability | None:
        owner = _require_owner(owner_user_id)
        if not isinstance(token, str) or not token:
            return None
        current = self._now(now)
        with self._lock:
            self._purge_expired(current)
            capability = self._entries.get(token)
            if capability is None or capability.owner_user_id != owner:
                return None
            return capability

    def revoke(self, token: str) -> bool:
        if not isinstance(token, str) or not token:
            return False
        with self._lock:
            capability = self._entries.pop(token, None)
            if capability is None:
                return False
            self._by_artifact.pop(
                (capability.owner_user_id, capability.artifact_id), None,
            )
            self._compact_expiry_heap()
            return True

    def _purge_expired(self, current: datetime) -> None:
        """Remove expired entries while the registry lock is held."""
        current_epoch = current.timestamp()
        while self._expiry_heap and self._expiry_heap[0][0] <= current_epoch:
            expiry_epoch, token = heapq.heappop(self._expiry_heap)
            capability = self._entries.get(token)
            if (
                capability is None
                or capability.expires_at.timestamp() != expiry_epoch
            ):
                continue
            self._entries.pop(token, None)
            self._by_artifact.pop(
                (capability.owner_user_id, capability.artifact_id), None,
            )

    def _compact_expiry_heap(self) -> None:
        """Bound obsolete heap records while the registry lock is held."""
        threshold = min(
            self._capacity * 2,
            max(64, len(self._entries) * 2),
        )
        if len(self._expiry_heap) <= threshold:
            return
        self._expiry_heap = [
            (capability.expires_at.timestamp(), token)
            for token, capability in self._entries.items()
        ]
        heapq.heapify(self._expiry_heap)


def _require_owner(value: str) -> str:
    if (
        not isinstance(value, str)
        or not (1 <= len(value) <= 160)
        or value != value.strip()
        or "\x00" in value
    ):
        raise ArtifactContractError("artifact_owner_invalid")
    return value


def _require_id(value: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ArtifactContractError("artifact_identifier_invalid")
    return value


def _chosen_id(value: str | None, *, prefix: str) -> str:
    return _require_id(f"{prefix}_{uuid.uuid4().hex}" if value is None else value)


def _require_digest(value: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ArtifactContractError("artifact_digest_invalid")
    return value


def _require_text(
    value: str,
    pattern: re.Pattern[str],
    code: str,
    *,
    maximum: int,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or not pattern.fullmatch(value)
    ):
        raise ArtifactContractError(code)
    return value


def _require_instant(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ArtifactContractError("artifact_retention_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ArtifactContractError("artifact_retention_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ArtifactContractError("artifact_retention_invalid")
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _owner_key(owner_user_id: str) -> str:
    payload = b"metnos:durable-artifact-owner:1\x00" + owner_user_id.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _target_key(owner_user_id: str, target_key: str) -> str:
    payload = (
        b"metnos:durable-artifact-target:1\x00"
        + owner_user_id.encode("utf-8")
        + b"\x00"
        + target_key.encode("utf-8")
    )
    return hashlib.sha256(payload).hexdigest()


def _publication_temp_name(publication_id: str) -> str:
    payload = b"metnos:durable-publication:1\x00" + publication_id.encode("ascii")
    return ".prepared-" + hashlib.sha256(payload).hexdigest()


def _blob_ref(digest: str) -> str:
    return _BLOB_REF_PREFIX + _require_digest(digest)


def _artifact_from_row(row: sqlite3.Row) -> Artifact:
    return Artifact(
        owner_user_id=str(row["owner_user_id"]),
        artifact_id=str(row["id"]),
        workload_id=str(row["workload_id"]),
        revision_id=str(row["revision_id"]),
        logical_name=str(row["logical_name"]),
        digest=str(row["digest"]),
        mime_type=str(row["mime_type"]),
        size_bytes=int(row["size_bytes"]),
        schema_version=str(row["schema_version"]),
        state=ArtifactState(row["state"]),
        blob_ref=str(row["blob_ref"]),
        retention_until=row["retention_until"],
        published_target_redacted=row["published_target_redacted"],
        digest_verified=bool(row["digest_verified"]),
        schema_valid=bool(row["schema_valid"]),
        postconditions_valid=bool(row["postconditions_valid"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _publication_from_row(row: sqlite3.Row) -> Publication:
    return Publication(
        owner_user_id=str(row["owner_user_id"]),
        publication_id=str(row["id"]),
        artifact_id=str(row["artifact_id"]),
        target_key=str(row["target_key"]),
        target_redacted=str(row["target_redacted"]),
        state=PublicationState(row["state"]),
        expected_digest=str(row["expected_digest"]),
        observed_digest=row["observed_digest"],
        attempt_count=int(row["attempt_count"]),
        prepared_at=str(row["prepared_at"]),
        published_at=row["published_at"],
    )


class ArtifactRepository:
    """Small owner-scoped repository over the schema frozen in F2."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        owns_connection: bool = False,
    ) -> None:
        if database_schema_version(connection) != CURRENT_SCHEMA_VERSION:
            raise ArtifactError("artifact_repository_schema_not_ready")
        connection.row_factory = sqlite3.Row
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise ArtifactError("artifact_repository_foreign_keys_disabled")
        self._connection = connection
        self._owns_connection = owns_connection

    @classmethod
    def open(cls, path: str | Path | None = None) -> "ArtifactRepository":
        connection = open_db(path)
        try:
            migrate(connection)
            return cls(connection, owns_connection=True)
        except BaseException:
            connection.close()
            raise

    def close(self) -> None:
        if self._owns_connection:
            self._connection.close()
            self._owns_connection = False

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()

    def register(
        self,
        owner_user_id: str,
        workload_id: str,
        revision_id: str,
        logical_name: str,
        mime_type: str,
        schema_version: str,
        blob: Blob,
        *,
        artifact_id: str | None = None,
        retention_until: str | None = None,
        schema_valid: bool = True,
        postconditions_valid: bool = True,
        verify_blob: Callable[[], None],
    ) -> Artifact:
        owner = _require_owner(owner_user_id)
        workload = _require_id(workload_id)
        revision = _require_id(revision_id)
        selected_id = _chosen_id(artifact_id, prefix="artifact")
        logical_name = _require_text(
            logical_name,
            _LOGICAL_NAME_RE,
            "artifact_logical_name_invalid",
            maximum=64,
        )
        mime_type = _require_text(
            mime_type,
            _MIME_RE,
            "artifact_mime_type_invalid",
            maximum=127,
        )
        schema_version = _require_text(
            schema_version,
            _SCHEMA_RE,
            "artifact_schema_version_invalid",
            maximum=160,
        )
        if not isinstance(schema_valid, bool) or not isinstance(postconditions_valid, bool):
            raise ArtifactContractError("artifact_validation_flags_invalid")
        retention = _require_instant(retention_until)
        _require_digest(blob.digest)
        if (
            not isinstance(blob.size_bytes, int)
            or isinstance(blob.size_bytes, bool)
            or blob.size_bytes < 0
        ):
            raise ArtifactContractError("artifact_size_invalid")
        if blob.blob_ref != _blob_ref(blob.digest):
            raise ArtifactContractError("artifact_blob_ref_invalid")

        with self._transaction() as connection:
            revision_row = connection.execute(
                """
                SELECT revision.plan_json,
                       usage.output_bytes, usage.artifact_count,
                       usage.usage_unknown
                FROM revisions revision
                JOIN revision_usage usage
                  ON usage.owner_user_id=revision.owner_user_id
                 AND usage.revision_id=revision.id
                WHERE revision.owner_user_id=? AND revision.id=?
                  AND revision.workload_id=?
                """,
                (owner, revision, workload),
            ).fetchone()
            if revision_row is None:
                raise ArtifactNotFoundError("artifact_revision_not_found")

            existing = connection.execute(
                """
                SELECT * FROM artifacts
                WHERE owner_user_id=? AND revision_id=? AND logical_name=?
                """,
                (owner, revision, logical_name),
            ).fetchone()
            if existing is not None:
                expected = (
                    workload,
                    blob.digest,
                    mime_type,
                    blob.size_bytes,
                    schema_version,
                    blob.blob_ref,
                    retention,
                    int(schema_valid),
                    int(postconditions_valid),
                )
                observed = (
                    existing["workload_id"],
                    existing["digest"],
                    existing["mime_type"],
                    existing["size_bytes"],
                    existing["schema_version"],
                    existing["blob_ref"],
                    existing["retention_until"],
                    existing["schema_valid"],
                    existing["postconditions_valid"],
                )
                if observed != expected:
                    raise ArtifactConflictError("artifact_logical_name_conflict")
                verify_blob()
                return _artifact_from_row(existing)

            id_collision = connection.execute(
                "SELECT 1 FROM artifacts WHERE owner_user_id=? AND id=?",
                (owner, selected_id),
            ).fetchone()
            if id_collision is not None:
                raise ArtifactConflictError("artifact_identifier_conflict")

            budgets = json.loads(str(revision_row["plan_json"]))["budgets"]
            if bool(revision_row["usage_unknown"]):
                raise ArtifactBudgetError("artifact_budget_accounting_incomplete")
            next_artifact_count = int(revision_row["artifact_count"]) + 1
            if next_artifact_count > int(budgets["max_artifacts"]):
                raise ArtifactBudgetError("artifact_count_budget_exhausted")
            next_output_bytes = int(revision_row["output_bytes"]) + blob.size_bytes
            if next_output_bytes > int(budgets["max_bytes_written"]):
                raise ArtifactBudgetError("artifact_byte_budget_exhausted")

            # The expensive digest pass has already completed without a
            # database lock.  This callback only checks that the verified open
            # file is still the blob stored at its canonical name.  GC uses the
            # same writer transaction, so it cannot unlink the name between
            # this check and the artifact insert.
            verify_blob()
            now = utc_now()
            connection.execute(
                """
                INSERT INTO artifacts(
                    owner_user_id, id, workload_id, revision_id, logical_name,
                    digest, mime_type, size_bytes, schema_version, state,
                    blob_ref, retention_until, digest_verified, schema_valid,
                    postconditions_valid, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    owner,
                    selected_id,
                    workload,
                    revision,
                    logical_name,
                    blob.digest,
                    mime_type,
                    blob.size_bytes,
                    schema_version,
                    blob.blob_ref,
                    retention,
                    int(schema_valid),
                    int(postconditions_valid),
                    now,
                    now,
                ),
            )
            usage_updated = connection.execute(
                """
                UPDATE revision_usage
                SET output_bytes=?, artifact_count=?, updated_at=?
                WHERE owner_user_id=? AND revision_id=?
                  AND output_bytes=? AND artifact_count=?
                """,
                (
                    next_output_bytes, next_artifact_count, now,
                    owner, revision,
                    revision_row["output_bytes"],
                    revision_row["artifact_count"],
                ),
            )
            if usage_updated.rowcount != 1:
                raise ArtifactError("artifact_budget_accounting_conflict")
            row = connection.execute(
                "SELECT * FROM artifacts WHERE owner_user_id=? AND id=?",
                (owner, selected_id),
            ).fetchone()
            assert row is not None
            return _artifact_from_row(row)

    def get_artifact(self, owner_user_id: str, artifact_id: str) -> Artifact:
        owner = _require_owner(owner_user_id)
        selected_id = _require_id(artifact_id)
        row = self._connection.execute(
            "SELECT * FROM artifacts WHERE owner_user_id=? AND id=?",
            (owner, selected_id),
        ).fetchone()
        if row is None:
            raise ArtifactNotFoundError("artifact_not_found")
        return _artifact_from_row(row)

    def list_workload_artifacts(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        limit: int = 100,
    ) -> tuple[Artifact, ...]:
        """List bounded registered metadata for one owner/workload pair."""

        owner = _require_owner(owner_user_id)
        workload = _require_id(workload_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ArtifactContractError("artifact_list_limit_invalid")
        rows = self._connection.execute(
            """
            SELECT * FROM artifacts
            WHERE owner_user_id=? AND workload_id=?
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (owner, workload, limit),
        ).fetchall()
        return tuple(_artifact_from_row(row) for row in rows)

    def prepare_publication(
        self,
        owner_user_id: str,
        artifact_id: str,
        target_key: str,
        target_redacted: str,
        *,
        publication_id: str | None = None,
    ) -> Publication:
        owner = _require_owner(owner_user_id)
        selected_artifact = _require_id(artifact_id)
        selected_publication = _chosen_id(publication_id, prefix="publication")
        target_key = _require_text(
            target_key,
            _TARGET_RE,
            "artifact_target_key_invalid",
            maximum=256,
        )
        if not isinstance(target_redacted, str) or not (1 <= len(target_redacted) <= 1024):
            raise ArtifactContractError("artifact_target_redaction_invalid")

        with self._transaction() as connection:
            artifact = connection.execute(
                "SELECT * FROM artifacts WHERE owner_user_id=? AND id=?",
                (owner, selected_artifact),
            ).fetchone()
            if artifact is None:
                raise ArtifactNotFoundError("artifact_not_found")
            if artifact["state"] not in {
                ArtifactState.COMMITTED.value,
                ArtifactState.PUBLISHED.value,
            }:
                raise ArtifactConflictError("artifact_not_publishable")

            existing = connection.execute(
                """
                SELECT * FROM publications
                WHERE owner_user_id=? AND artifact_id=? AND target_key=?
                """,
                (owner, selected_artifact, target_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["expected_digest"] != artifact["digest"]
                    or existing["target_redacted"] != target_redacted
                ):
                    raise ArtifactConflictError("artifact_publication_conflict")
                return _publication_from_row(existing)

            collision = connection.execute(
                "SELECT 1 FROM publications WHERE owner_user_id=? AND id=?",
                (owner, selected_publication),
            ).fetchone()
            if collision is not None:
                raise ArtifactConflictError("artifact_publication_identifier_conflict")

            now = utc_now()
            connection.execute(
                """
                INSERT INTO publications(
                    owner_user_id, id, artifact_id, target_key,
                    target_redacted, state, expected_digest, prepared_at
                ) VALUES (?, ?, ?, ?, ?, 'prepared', ?, ?)
                """,
                (
                    owner,
                    selected_publication,
                    selected_artifact,
                    target_key,
                    target_redacted,
                    artifact["digest"],
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM publications WHERE owner_user_id=? AND id=?",
                (owner, selected_publication),
            ).fetchone()
            assert row is not None
            return _publication_from_row(row)

    def get_publication(self, owner_user_id: str, publication_id: str) -> Publication:
        owner = _require_owner(owner_user_id)
        selected_id = _require_id(publication_id)
        row = self._connection.execute(
            "SELECT * FROM publications WHERE owner_user_id=? AND id=?",
            (owner, selected_id),
        ).fetchone()
        if row is None:
            raise ArtifactNotFoundError("artifact_publication_not_found")
        return _publication_from_row(row)

    def begin_publication_attempt(
        self,
        owner_user_id: str,
        publication_id: str,
    ) -> Publication:
        owner = _require_owner(owner_user_id)
        selected_id = _require_id(publication_id)
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE publications SET attempt_count=attempt_count+1
                WHERE owner_user_id=? AND id=? AND state='prepared'
                """,
                (owner, selected_id),
            )
            row = connection.execute(
                "SELECT * FROM publications WHERE owner_user_id=? AND id=?",
                (owner, selected_id),
            ).fetchone()
            if row is None:
                raise ArtifactNotFoundError("artifact_publication_not_found")
            return _publication_from_row(row)

    def mark_published(
        self,
        owner_user_id: str,
        publication_id: str,
        observed_digest: str,
    ) -> Publication:
        owner = _require_owner(owner_user_id)
        selected_id = _require_id(publication_id)
        observed = _require_digest(observed_digest)
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT * FROM publications WHERE owner_user_id=? AND id=?",
                (owner, selected_id),
            ).fetchone()
            if current is None:
                raise ArtifactNotFoundError("artifact_publication_not_found")
            if current["expected_digest"] != observed:
                raise ArtifactConflictError("artifact_publication_digest_conflict")
            if current["state"] == PublicationState.PUBLISHED.value:
                return _publication_from_row(current)
            if current["state"] != PublicationState.PREPARED.value:
                raise ArtifactConflictError("artifact_publication_state_conflict")

            now = utc_now()
            changed = connection.execute(
                """
                UPDATE publications
                SET state='published', observed_digest=?, published_at=?
                WHERE owner_user_id=? AND id=? AND state='prepared'
                  AND expected_digest=?
                """,
                (observed, now, owner, selected_id, observed),
            ).rowcount
            if changed != 1:
                raise ArtifactConflictError("artifact_publication_cas_conflict")
            connection.execute(
                """
                UPDATE artifacts
                SET state='published', published_target_redacted=?, updated_at=?
                WHERE owner_user_id=? AND id=?
                  AND state IN ('committed', 'published')
                """,
                (
                    current["target_redacted"],
                    now,
                    owner,
                    current["artifact_id"],
                ),
            )
            row = connection.execute(
                "SELECT * FROM publications WHERE owner_user_id=? AND id=?",
                (owner, selected_id),
            ).fetchone()
            assert row is not None
            return _publication_from_row(row)

    def mark_needs_attention(
        self,
        owner_user_id: str,
        publication_id: str,
        observed_digest: str | None,
    ) -> Publication:
        owner = _require_owner(owner_user_id)
        selected_id = _require_id(publication_id)
        observed = None if observed_digest is None else _require_digest(observed_digest)
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT * FROM publications WHERE owner_user_id=? AND id=?",
                (owner, selected_id),
            ).fetchone()
            if current is None:
                raise ArtifactNotFoundError("artifact_publication_not_found")
            if current["state"] == PublicationState.PUBLISHED.value:
                return _publication_from_row(current)
            if current["state"] == PublicationState.PREPARED.value:
                connection.execute(
                    """
                    UPDATE publications
                    SET state='needs_attention', observed_digest=?
                    WHERE owner_user_id=? AND id=? AND state='prepared'
                    """,
                    (observed, owner, selected_id),
                )
                connection.execute(
                    """
                    UPDATE artifacts SET state='needs_attention', updated_at=?
                    WHERE owner_user_id=? AND id=? AND state='committed'
                    """,
                    (utc_now(), owner, current["artifact_id"]),
                )
            row = connection.execute(
                "SELECT * FROM publications WHERE owner_user_id=? AND id=?",
                (owner, selected_id),
            ).fetchone()
            assert row is not None
            return _publication_from_row(row)

    def list_prepared(self, owner_user_id: str, *, limit: int = 100) -> tuple[Publication, ...]:
        owner = _require_owner(owner_user_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= 1000):
            raise ArtifactContractError("artifact_reconcile_limit_invalid")
        rows = self._connection.execute(
            """
            SELECT * FROM publications
            WHERE owner_user_id=? AND state='prepared'
            ORDER BY prepared_at, id LIMIT ?
            """,
            (owner, limit),
        ).fetchall()
        return tuple(_publication_from_row(row) for row in rows)

    def unlink_if_unreferenced(
        self,
        owner_user_id: str,
        digest: str,
        unlink: Callable[[], None],
    ) -> bool:
        owner = _require_owner(owner_user_id)
        selected_digest = _require_digest(digest)
        with self._transaction() as connection:
            count = int(connection.execute(
                """
                SELECT COUNT(*) FROM artifacts
                WHERE owner_user_id=? AND digest=?
                """,
                (owner, selected_digest),
            ).fetchone()[0])
            if count:
                return False
            unlink()
            return True

    def garbage_collection_cursor(self, owner_user_id: str) -> str:
        """Return the durable lexicographic cursor for one owner's blob scan."""

        owner = _require_owner(owner_user_id)
        row = self._connection.execute(
            """
            SELECT cursor_name FROM artifact_gc_cursors
            WHERE owner_user_id=?
            """,
            (owner,),
        ).fetchone()
        return "" if row is None else str(row["cursor_name"])

    def advance_garbage_collection_cursor(
        self,
        owner_user_id: str,
        *,
        expected: str,
        next_cursor: str,
    ) -> bool:
        """Persist scan progress with CAS; an empty cursor starts a new pass."""

        owner = _require_owner(owner_user_id)
        for value in (expected, next_cursor):
            if (
                not isinstance(value, str)
                or len(value) > 255
                or "\x00" in value
            ):
                raise ArtifactContractError("artifact_gc_cursor_invalid")
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT cursor_name FROM artifact_gc_cursors
                WHERE owner_user_id=?
                """,
                (owner,),
            ).fetchone()
            current = "" if row is None else str(row["cursor_name"])
            if current != expected:
                return False
            if not next_cursor:
                if row is not None:
                    connection.execute(
                        "DELETE FROM artifact_gc_cursors WHERE owner_user_id=?",
                        (owner,),
                    )
                return True
            now = utc_now()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO artifact_gc_cursors(
                        owner_user_id, cursor_name, updated_at
                    ) VALUES (?, ?, ?)
                    """,
                    (owner, next_cursor, now),
                )
            else:
                updated = connection.execute(
                    """
                    UPDATE artifact_gc_cursors
                    SET cursor_name=?, updated_at=?
                    WHERE owner_user_id=? AND cursor_name=?
                    """,
                    (next_cursor, now, owner, expected),
                )
                if updated.rowcount != 1:
                    return False
            return True

    def delete_owner_rows(self, owner_user_id: str) -> int:
        owner = _require_owner(owner_user_id)
        with self._transaction() as connection:
            count = int(connection.execute(
                "SELECT COUNT(*) FROM artifacts WHERE owner_user_id=?",
                (owner,),
            ).fetchone()[0])
            connection.execute(
                "DELETE FROM artifacts WHERE owner_user_id=?",
                (owner,),
            )
            connection.execute(
                "DELETE FROM artifact_gc_cursors WHERE owner_user_id=?",
                (owner,),
            )
            residue = int(connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM artifacts WHERE owner_user_id=?)
                  + (SELECT COUNT(*) FROM publications WHERE owner_user_id=?)
                """,
                (owner, owner),
            ).fetchone()[0])
            if residue:
                raise ArtifactError("artifact_owner_delete_incomplete")
            return count


class ArtifactStore:
    """Crash-safe private blob store backed by :class:`ArtifactRepository`."""

    def __init__(
        self,
        root: str | Path,
        repository: ArtifactRepository,
        *,
        max_blob_bytes: int = _DEFAULT_MAX_BLOB_BYTES,
        fsync: Callable[[int], None] = os.fsync,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        if (
            isinstance(max_blob_bytes, bool)
            or not isinstance(max_blob_bytes, int)
            or max_blob_bytes < 1
        ):
            raise ArtifactContractError("artifact_max_blob_bytes_invalid")
        self._root = Path(os.path.abspath(os.path.expanduser(str(root))))
        self._repository = repository
        self._max_blob_bytes = max_blob_bytes
        self._fsync = fsync
        self._checkpoint = checkpoint or (lambda _name: None)
        self._ensure_root()
        with self._directory("owners", create=True):
            pass

    def close(self) -> None:
        """Close the explicitly-owned metadata repository, if any."""

        self._repository.close()

    def _ensure_root(self) -> None:
        try:
            current = os.lstat(self._root)
        except FileNotFoundError:
            self._root.mkdir(parents=True, mode=0o700)
            current = os.lstat(self._root)
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
            raise ArtifactSecurityError("artifact_root_unsafe")
        descriptor = self._open_root()
        try:
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)

    def _open_root(self) -> int:
        before = os.lstat(self._root)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._root, flags)
        except OSError as exc:
            raise ArtifactSecurityError("artifact_root_unsafe") from exc
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            os.close(descriptor)
            raise ArtifactSecurityError("artifact_root_replaced")
        return descriptor

    @contextmanager
    def _directory(self, *parts: str, create: bool) -> Iterator[int]:
        descriptor = self._open_root()
        try:
            for part in parts:
                if (
                    not isinstance(part, str)
                    or part in {"", ".", ".."}
                    or "/" in part
                    or "\x00" in part
                ):
                    raise ArtifactSecurityError("artifact_path_component_invalid")
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                try:
                    child = os.open(part, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    raise
                except OSError as exc:
                    raise ArtifactSecurityError("artifact_directory_unsafe") from exc
                opened = os.fstat(child)
                if not stat.S_ISDIR(opened.st_mode):
                    os.close(child)
                    raise ArtifactSecurityError("artifact_directory_unsafe")
                os.fchmod(child, 0o700)
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _payload_chunks(payload: bytes | bytearray | memoryview | BinaryIO) -> Iterator[bytes]:
        if isinstance(payload, (bytes, bytearray, memoryview)):
            view = memoryview(payload)
            for offset in range(0, len(view), _CHUNK_BYTES):
                yield bytes(view[offset:offset + _CHUNK_BYTES])
            return
        if isinstance(payload, (str, os.PathLike)) or not callable(getattr(payload, "read", None)):
            raise ArtifactContractError("artifact_payload_must_be_bytes_or_stream")
        while True:
            chunk = payload.read(_CHUNK_BYTES)
            if chunk == b"":
                return
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise ArtifactContractError("artifact_stream_must_be_binary")
            if len(chunk) > _CHUNK_BYTES:
                raise ArtifactContractError("artifact_stream_chunk_too_large")
            yield bytes(chunk)

    @staticmethod
    def _fd_chunks(descriptor: int) -> Iterator[bytes]:
        while True:
            chunk = os.read(descriptor, _CHUNK_BYTES)
            if not chunk:
                return
            yield chunk

    @staticmethod
    def _write_all(descriptor: int, chunk: bytes) -> None:
        offset = 0
        while offset < len(chunk):
            written = os.write(descriptor, chunk[offset:])
            if written <= 0:
                raise OSError("artifact_write_failed")
            offset += written

    def _write_temp(
        self,
        directory_fd: int,
        name: str,
        chunks: Iterator[bytes],
        *,
        checkpoint: str,
    ) -> tuple[str, int]:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
        succeeded = False
        try:
            os.fchmod(descriptor, 0o600)
            digest = hashlib.sha256()
            size = 0
            for chunk in chunks:
                size += len(chunk)
                if size > self._max_blob_bytes:
                    raise ArtifactContractError("artifact_payload_too_large")
                self._write_all(descriptor, chunk)
                digest.update(chunk)
            self._fsync(descriptor)
            self._checkpoint(checkpoint)
            succeeded = True
            return "sha256:" + digest.hexdigest(), size
        finally:
            os.close(descriptor)
            if not succeeded:
                try:
                    os.unlink(name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _open_regular(directory_fd: int, name: str) -> int:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(name, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ArtifactSecurityError("artifact_file_unsafe") from exc
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise ArtifactSecurityError("artifact_file_unsafe")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            os.close(descriptor)
            raise ArtifactSecurityError("artifact_file_permissions_unsafe")
        return descriptor

    @staticmethod
    def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    @classmethod
    def _verify_descriptor_state(
        cls,
        descriptor: int,
        *,
        max_bytes: int,
    ) -> tuple[str, int, tuple[int, int, int, int, int, int]]:
        before = os.fstat(descriptor)
        if before.st_size > max_bytes:
            raise ArtifactIntegrityError("artifact_file_too_large")
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        size = 0
        for chunk in cls._fd_chunks(descriptor):
            size += len(chunk)
            if size > max_bytes:
                raise ArtifactIntegrityError("artifact_file_too_large")
            digest.update(chunk)
        after = os.fstat(descriptor)
        identity = cls._file_identity(after)
        if cls._file_identity(before) != identity or size != int(after.st_size):
            raise ArtifactIntegrityError("artifact_file_changed_during_verification")
        return "sha256:" + digest.hexdigest(), size, identity

    @classmethod
    def _verify_descriptor(
        cls,
        descriptor: int,
        *,
        max_bytes: int,
    ) -> tuple[str, int]:
        digest, size, _identity = cls._verify_descriptor_state(
            descriptor,
            max_bytes=max_bytes,
        )
        return digest, size

    def _verify_file(self, directory_fd: int, name: str) -> tuple[str, int]:
        descriptor = self._open_regular(directory_fd, name)
        try:
            return self._verify_descriptor(
                descriptor,
                max_bytes=self._max_blob_bytes,
            )
        finally:
            os.close(descriptor)

    def _install_without_overwrite(self, directory_fd: int, temporary: str, final: str) -> None:
        changed = False
        try:
            # link+unlink is the portable no-overwrite installation primitive.
            # Both names are in the same directory and therefore filesystem.
            os.link(
                temporary,
                final,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            changed = True
        except FileExistsError:
            pass
        try:
            os.unlink(temporary, dir_fd=directory_fd)
            changed = True
        except FileNotFoundError:
            pass
        if changed:
            self._fsync(directory_fd)

    def _blob_directory(self, owner_user_id: str, *, create: bool):
        return self._directory(
            "owners",
            _owner_key(owner_user_id),
            "blobs",
            "sha256",
            create=create,
        )

    def _publication_directory(
        self,
        owner_user_id: str,
        target_key: str,
        *,
        create: bool,
    ):
        return self._directory(
            "owners",
            _owner_key(owner_user_id),
            "publications",
            _target_key(owner_user_id, target_key),
            create=create,
        )

    def stage(
        self,
        owner_user_id: str,
        payload: bytes | bytearray | memoryview | BinaryIO,
    ) -> Blob:
        owner = _require_owner(owner_user_id)
        temporary = f".blob-{uuid.uuid4().hex}.tmp"
        with self._blob_directory(owner, create=True) as directory:
            digest, size = self._write_temp(
                directory,
                temporary,
                self._payload_chunks(payload),
                checkpoint="blob_after_fsync",
            )
            final = digest[7:]
            self._install_without_overwrite(directory, temporary, final)
            observed_digest, observed_size = self._verify_file(directory, final)
            if (observed_digest, observed_size) != (digest, size):
                raise ArtifactIntegrityError("artifact_blob_collision")
            self._checkpoint("blob_after_install")
            return Blob(digest=digest, size_bytes=size, blob_ref=_blob_ref(digest))

    def verify_blob(self, owner_user_id: str, blob: Blob) -> None:
        owner = _require_owner(owner_user_id)
        expected_digest = _require_digest(blob.digest)
        if blob.blob_ref != _blob_ref(expected_digest):
            raise ArtifactContractError("artifact_blob_ref_invalid")
        try:
            with self._blob_directory(owner, create=False) as directory:
                observed = self._verify_file(directory, expected_digest[7:])
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError("artifact_blob_missing") from exc
        if observed != (expected_digest, blob.size_bytes):
            raise ArtifactIntegrityError("artifact_blob_integrity_failed")

    @contextmanager
    def _verified_registration(
        self,
        owner_user_id: str,
        blob: Blob,
    ) -> Iterator[Callable[[], None]]:
        """Hash a blob without a DB lock and retain a cheap atomic guard."""

        owner = _require_owner(owner_user_id)
        expected_digest = _require_digest(blob.digest)
        if blob.blob_ref != _blob_ref(expected_digest):
            raise ArtifactContractError("artifact_blob_ref_invalid")
        name = expected_digest[7:]
        try:
            with self._blob_directory(owner, create=False) as directory:
                descriptor = self._open_regular(directory, name)
                try:
                    digest, size, identity = self._verify_descriptor_state(
                        descriptor,
                        max_bytes=self._max_blob_bytes,
                    )
                    if (digest, size) != (expected_digest, blob.size_bytes):
                        raise ArtifactIntegrityError("artifact_blob_integrity_failed")

                    def verify_current_identity() -> None:
                        try:
                            descriptor_identity = self._file_identity(
                                os.fstat(descriptor)
                            )
                            path_identity = self._file_identity(os.stat(
                                name,
                                dir_fd=directory,
                                follow_symlinks=False,
                            ))
                        except FileNotFoundError as exc:
                            raise ArtifactIntegrityError(
                                "artifact_blob_missing"
                            ) from exc
                        if descriptor_identity != identity or path_identity != identity:
                            raise ArtifactIntegrityError(
                                "artifact_blob_replaced_after_verification"
                            )

                    yield verify_current_identity
                finally:
                    os.close(descriptor)
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError("artifact_blob_missing") from exc

    def open_registered_download(
        self,
        owner_user_id: str,
        artifact_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[Artifact, BinaryIO]:
        """Open bytes only after registry, retention and integrity checks.

        The caller receives an already-open descriptor rather than a path, so
        a browser-controlled value can never reach filesystem resolution.
        """

        owner = _require_owner(owner_user_id)
        artifact = self.get_downloadable_artifact(owner, artifact_id, now=now)
        try:
            with self._blob_directory(owner, create=False) as directory:
                descriptor = self._open_regular(directory, artifact.digest[7:])
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError("artifact_blob_missing") from exc
        try:
            observed = self._verify_descriptor(
                descriptor,
                max_bytes=self._max_blob_bytes,
            )
            if observed != (artifact.digest, artifact.size_bytes):
                raise ArtifactIntegrityError("artifact_blob_integrity_failed")
            os.lseek(descriptor, 0, os.SEEK_SET)
            return artifact, os.fdopen(descriptor, "rb", closefd=True)
        except BaseException:
            os.close(descriptor)
            raise

    def get_downloadable_artifact(
        self,
        owner_user_id: str,
        artifact_id: str,
        *,
        now: datetime | None = None,
    ) -> Artifact:
        """Resolve one registered artifact without exposing its blob path."""

        owner = _require_owner(owner_user_id)
        artifact = self._repository.get_artifact(owner, artifact_id)
        moment = now or datetime.now(timezone.utc)
        if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
            raise ArtifactContractError("artifact_download_time_invalid")
        current = _require_instant(
            moment.astimezone(timezone.utc).isoformat(
                timespec="microseconds"
            ).replace("+00:00", "Z")
        )
        assert current is not None
        if artifact.state not in {ArtifactState.COMMITTED, ArtifactState.PUBLISHED}:
            raise ArtifactNotFoundError("artifact_download_unavailable")
        if artifact.retention_until is not None:
            expires = _require_instant(artifact.retention_until)
            assert expires is not None
            if expires <= current:
                raise ArtifactNotFoundError("artifact_download_expired")
        return artifact

    def list_workload_artifacts(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        limit: int = 100,
    ) -> tuple[Artifact, ...]:
        return self._repository.list_workload_artifacts(
            owner_user_id, workload_id, limit=limit,
        )

    def commit(
        self,
        owner_user_id: str,
        workload_id: str,
        revision_id: str,
        logical_name: str,
        mime_type: str,
        schema_version: str,
        payload: bytes | bytearray | memoryview | BinaryIO,
        *,
        artifact_id: str | None = None,
        retention_until: str | None = None,
        schema_valid: bool = True,
        postconditions_valid: bool = True,
    ) -> Artifact:
        owner = _require_owner(owner_user_id)
        _require_id(workload_id)
        _require_id(revision_id)
        _require_text(
            logical_name,
            _LOGICAL_NAME_RE,
            "artifact_logical_name_invalid",
            maximum=64,
        )
        _require_text(
            mime_type,
            _MIME_RE,
            "artifact_mime_type_invalid",
            maximum=127,
        )
        _require_text(
            schema_version,
            _SCHEMA_RE,
            "artifact_schema_version_invalid",
            maximum=160,
        )
        if artifact_id is not None:
            _require_id(artifact_id)
        _require_instant(retention_until)
        if not isinstance(schema_valid, bool) or not isinstance(postconditions_valid, bool):
            raise ArtifactContractError("artifact_validation_flags_invalid")
        blob = self.stage(owner, payload)
        with self._verified_registration(owner, blob) as verify_current_identity:
            self._checkpoint("blob_after_registration_verification")
            return self._repository.register(
                owner,
                workload_id,
                revision_id,
                logical_name,
                mime_type,
                schema_version,
                blob,
                artifact_id=artifact_id,
                retention_until=retention_until,
                schema_valid=schema_valid,
                postconditions_valid=postconditions_valid,
                verify_blob=verify_current_identity,
            )

    @staticmethod
    def _redacted_target(owner_user_id: str, target_key: str) -> str:
        return "metnos-internal://artifact/" + _target_key(owner_user_id, target_key)

    def _copy_blob_to_publication_temp(
        self,
        owner_user_id: str,
        artifact: Artifact,
        publication: Publication,
        directory_fd: int,
        temporary: str,
    ) -> None:
        try:
            with self._blob_directory(owner_user_id, create=False) as blobs:
                source = self._open_regular(blobs, artifact.digest[7:])
                try:
                    digest, size = self._write_temp(
                        directory_fd,
                        temporary,
                        self._fd_chunks(source),
                        checkpoint="publication_after_fsync",
                    )
                finally:
                    os.close(source)
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError("artifact_blob_missing") from exc
        if (digest, size) != (publication.expected_digest, artifact.size_bytes):
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            raise ArtifactIntegrityError("artifact_registered_blob_changed")

    def _publication_files(
        self,
        owner_user_id: str,
        artifact: Artifact,
        publication: Publication,
        *,
        create: bool,
        write_if_missing: bool,
    ) -> tuple[RecoveryStatus, str | None]:
        temporary = _publication_temp_name(publication.publication_id)
        try:
            context = self._publication_directory(
                owner_user_id,
                publication.target_key,
                create=create,
            )
            with context as directory:
                try:
                    observed = self._verify_file(directory, "artifact")
                except FileNotFoundError:
                    observed = None
                if observed is not None:
                    try:
                        os.unlink(temporary, dir_fd=directory)
                        self._fsync(directory)
                    except FileNotFoundError:
                        pass
                    if observed != (publication.expected_digest, artifact.size_bytes):
                        return RecoveryStatus.NEEDS_ATTENTION, observed[0]
                    return RecoveryStatus.COMMITTED, observed[0]

                try:
                    prepared = self._verify_file(directory, temporary)
                except FileNotFoundError:
                    prepared = None
                if prepared is None and write_if_missing:
                    self._copy_blob_to_publication_temp(
                        owner_user_id,
                        artifact,
                        publication,
                        directory,
                        temporary,
                    )
                    prepared = self._verify_file(directory, temporary)
                if prepared is None:
                    return RecoveryStatus.RETRYABLE, None
                if prepared != (publication.expected_digest, artifact.size_bytes):
                    os.unlink(temporary, dir_fd=directory)
                    self._fsync(directory)
                    return RecoveryStatus.RETRYABLE, prepared[0]

                self._install_without_overwrite(directory, temporary, "artifact")
                self._checkpoint("publication_after_install")
                final = self._verify_file(directory, "artifact")
                if final != (publication.expected_digest, artifact.size_bytes):
                    return RecoveryStatus.NEEDS_ATTENTION, final[0]
                return RecoveryStatus.COMMITTED, final[0]
        except FileNotFoundError:
            return RecoveryStatus.RETRYABLE, None

    def publish(
        self,
        owner_user_id: str,
        artifact_id: str,
        target_key: str,
        *,
        publication_id: str | None = None,
    ) -> PublicationRecovery:
        owner = _require_owner(owner_user_id)
        artifact = self._repository.get_artifact(owner, artifact_id)
        publication = self._repository.prepare_publication(
            owner,
            artifact.artifact_id,
            target_key,
            self._redacted_target(owner, target_key),
            publication_id=publication_id,
        )
        if publication.state is PublicationState.PUBLISHED:
            return PublicationRecovery(RecoveryStatus.COMMITTED, publication, "already_published")
        if publication.state is PublicationState.NEEDS_ATTENTION:
            return PublicationRecovery(
                RecoveryStatus.NEEDS_ATTENTION,
                publication,
                "already_needs_attention",
            )
        publication = self._repository.begin_publication_attempt(
            owner,
            publication.publication_id,
        )
        return self._finish_publication(owner, artifact, publication, write_if_missing=True)

    def reconcile(
        self,
        owner_user_id: str,
        publication_id: str,
    ) -> PublicationRecovery:
        owner = _require_owner(owner_user_id)
        publication = self._repository.get_publication(owner, publication_id)
        if publication.state is PublicationState.PUBLISHED:
            return PublicationRecovery(RecoveryStatus.COMMITTED, publication, "already_published")
        if publication.state is PublicationState.NEEDS_ATTENTION:
            return PublicationRecovery(
                RecoveryStatus.NEEDS_ATTENTION,
                publication,
                "already_needs_attention",
            )
        artifact = self._repository.get_artifact(owner, publication.artifact_id)
        return self._finish_publication(owner, artifact, publication, write_if_missing=False)

    def _finish_publication(
        self,
        owner_user_id: str,
        artifact: Artifact,
        publication: Publication,
        *,
        write_if_missing: bool,
    ) -> PublicationRecovery:
        try:
            status, observed = self._publication_files(
                owner_user_id,
                artifact,
                publication,
                create=write_if_missing,
                write_if_missing=write_if_missing,
            )
        except ArtifactSecurityError:
            current = self._repository.mark_needs_attention(
                owner_user_id,
                publication.publication_id,
                None,
            )
            return PublicationRecovery(
                RecoveryStatus.NEEDS_ATTENTION,
                current,
                "unsafe_publication_path",
            )
        except ArtifactIntegrityError:
            current = self._repository.mark_needs_attention(
                owner_user_id,
                publication.publication_id,
                None,
            )
            return PublicationRecovery(
                RecoveryStatus.NEEDS_ATTENTION,
                current,
                "artifact_integrity_failed",
            )
        except OSError:
            current = self._repository.get_publication(
                owner_user_id,
                publication.publication_id,
            )
            return PublicationRecovery(RecoveryStatus.RETRYABLE, current, "filesystem_retryable")

        if status is RecoveryStatus.COMMITTED:
            assert observed is not None
            current = self._repository.mark_published(
                owner_user_id,
                publication.publication_id,
                observed,
            )
            return PublicationRecovery(status, current, "digest_verified")
        if status is RecoveryStatus.NEEDS_ATTENTION:
            current = self._repository.mark_needs_attention(
                owner_user_id,
                publication.publication_id,
                observed,
            )
            return PublicationRecovery(status, current, "final_digest_mismatch")
        current = self._repository.get_publication(
            owner_user_id,
            publication.publication_id,
        )
        return PublicationRecovery(status, current, "publication_not_materialized")

    def reconcile_prepared(
        self,
        owner_user_id: str,
        *,
        limit: int = 100,
    ) -> tuple[PublicationRecovery, ...]:
        owner = _require_owner(owner_user_id)
        return tuple(
            self.reconcile(owner, publication.publication_id)
            for publication in self._repository.list_prepared(owner, limit=limit)
        )

    def collect_garbage(
        self,
        owner_user_id: str,
        *,
        grace_period: timedelta,
        batch_limit: int = 100,
        log_limit: int = 32,
        now: datetime | None = None,
    ) -> GarbageCollectionReport:
        owner = _require_owner(owner_user_id)
        if grace_period <= timedelta(0):
            raise ArtifactContractError("artifact_gc_grace_invalid")
        if (
            isinstance(batch_limit, bool)
            or not isinstance(batch_limit, int)
            or not (1 <= batch_limit <= 1000)
        ):
            raise ArtifactContractError("artifact_gc_batch_invalid")
        if (
            isinstance(log_limit, bool)
            or not isinstance(log_limit, int)
            or not (0 <= log_limit <= 100)
        ):
            raise ArtifactContractError("artifact_gc_log_limit_invalid")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ArtifactContractError("artifact_gc_now_invalid")
        threshold = current.timestamp() - grace_period.total_seconds()
        scanned = deleted = referenced = recent = unsafe = 0
        events: list[str] = []

        try:
            context = self._blob_directory(owner, create=False)
            with context as directory:
                cursor = self._repository.garbage_collection_cursor(owner)

                def names_after(value: str) -> list[str]:
                    # nsmallest keeps memory proportional to the requested
                    # batch, unlike sorting the whole (potentially very large)
                    # blob directory.
                    with os.scandir(directory) as entries:
                        return heapq.nsmallest(
                            batch_limit + 1,
                            (
                                entry.name for entry in entries
                                if entry.name > value
                            ),
                        )

                names = names_after(cursor)
                if not names and cursor:
                    # The cursor can outlive the last file of a pass.  Wrap in
                    # the same call so an externally removed tail cannot cost
                    # an otherwise empty maintenance cycle.
                    names = names_after("")
                selected = names[:batch_limit]
                for name in selected:
                    scanned += 1
                    try:
                        metadata = os.stat(
                            name, dir_fd=directory, follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        # Another collector may already have removed the same
                        # orphan.  Advancing past its stable name is safe.
                        continue
                    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                        unsafe += 1
                        if len(events) < log_limit:
                            events.append("unsafe_entry_skipped")
                        continue
                    if metadata.st_mtime > threshold:
                        recent += 1
                        continue
                    identity = (metadata.st_dev, metadata.st_ino)

                    def unlink_checked() -> None:
                        latest = os.stat(name, dir_fd=directory, follow_symlinks=False)
                        if (
                            (latest.st_dev, latest.st_ino) != identity
                            or not stat.S_ISREG(latest.st_mode)
                        ):
                            raise ArtifactSecurityError("artifact_gc_entry_replaced")
                        os.unlink(name, dir_fd=directory)

                    if _BLOB_TEMP_RE.fullmatch(name):
                        unlink_checked()
                        deleted += 1
                        if len(events) < log_limit:
                            events.append("stale_temporary_deleted")
                    elif _HEX_RE.fullmatch(name):
                        removed = self._repository.unlink_if_unreferenced(
                            owner,
                            "sha256:" + name,
                            unlink_checked,
                        )
                        if removed:
                            deleted += 1
                            if len(events) < log_limit:
                                events.append("orphan_blob_deleted")
                        else:
                            referenced += 1
                    else:
                        unsafe += 1
                        if len(events) < log_limit:
                            events.append("unknown_entry_skipped")
                if deleted:
                    self._fsync(directory)
                self._repository.advance_garbage_collection_cursor(
                    owner,
                    expected=cursor,
                    next_cursor=(
                        selected[-1]
                        if len(names) > batch_limit and selected else ""
                    ),
                )
                return GarbageCollectionReport(
                    scanned=scanned,
                    deleted=deleted,
                    referenced=referenced,
                    recent=recent,
                    unsafe=unsafe,
                    more=len(names) > batch_limit,
                    events=tuple(events),
                )
        except FileNotFoundError:
            return GarbageCollectionReport(0, 0, 0, 0, 0, False, ())

    @staticmethod
    def _open_child_directory(parent: int, name: str) -> int:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise ArtifactSecurityError("artifact_delete_directory_unsafe")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        child = os.open(name, flags, dir_fd=parent)
        opened = os.fstat(child)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            os.close(child)
            raise ArtifactSecurityError("artifact_delete_directory_replaced")
        return child

    @staticmethod
    def _remove_open_directory(parent: int, name: str, child: int) -> None:
        opened = os.fstat(child)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ArtifactSecurityError("artifact_delete_directory_replaced")
        os.rmdir(name, dir_fd=parent)

    @staticmethod
    def _delete_files(directory: int, allowed: Callable[[str], bool]) -> int:
        with os.scandir(directory) as entries:
            for entry in entries:
                metadata = os.stat(
                    entry.name, dir_fd=directory, follow_symlinks=False,
                )
                if (
                    not allowed(entry.name)
                    or not stat.S_ISREG(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                ):
                    raise ArtifactSecurityError("artifact_delete_entry_unsafe")
        deleted = 0
        with os.scandir(directory) as entries:
            for entry in entries:
                metadata = os.stat(
                    entry.name, dir_fd=directory, follow_symlinks=False,
                )
                if (
                    not allowed(entry.name)
                    or not stat.S_ISREG(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                ):
                    raise ArtifactSecurityError("artifact_delete_entry_unsafe")
                os.unlink(entry.name, dir_fd=directory)
                deleted += 1
        return deleted

    def _delete_owner_files(self, owner_user_id: str) -> tuple[int, int]:
        files = directories = 0
        with self._directory("owners", create=False) as owners:
            owner_name = _owner_key(owner_user_id)
            try:
                owner = self._open_child_directory(owners, owner_name)
            except FileNotFoundError:
                return 0, 0
            try:
                top_names: set[str] = set()
                with os.scandir(owner) as entries:
                    for entry in entries:
                        if entry.name not in {"blobs", "publications"}:
                            raise ArtifactSecurityError(
                                "artifact_delete_owner_tree_unsafe"
                            )
                        top_names.add(entry.name)

                if "publications" in top_names:
                    publications = self._open_child_directory(owner, "publications")
                    try:
                        with os.scandir(publications) as entries:
                            for entry in entries:
                                if not _HEX_RE.fullmatch(entry.name):
                                    raise ArtifactSecurityError(
                                        "artifact_delete_target_unsafe"
                                    )
                        with os.scandir(publications) as targets:
                            for target_entry in targets:
                                target_name = target_entry.name
                                if not _HEX_RE.fullmatch(target_name):
                                    raise ArtifactSecurityError(
                                        "artifact_delete_target_unsafe"
                                    )
                                target = self._open_child_directory(
                                    publications, target_name,
                                )
                                try:
                                    files += self._delete_files(
                                        target,
                                        lambda name: name == "artifact"
                                        or bool(
                                            _PUBLICATION_TEMP_RE.fullmatch(name)
                                        ),
                                    )
                                    self._fsync(target)
                                    self._remove_open_directory(
                                        publications, target_name, target,
                                    )
                                    directories += 1
                                finally:
                                    os.close(target)
                        self._fsync(publications)
                        self._remove_open_directory(owner, "publications", publications)
                        directories += 1
                    finally:
                        os.close(publications)

                if "blobs" in top_names:
                    blobs = self._open_child_directory(owner, "blobs")
                    try:
                        blob_children: list[str] = []
                        with os.scandir(blobs) as entries:
                            for entry in entries:
                                blob_children.append(entry.name)
                                if len(blob_children) > 1:
                                    break
                        if blob_children != ["sha256"]:
                            raise ArtifactSecurityError("artifact_delete_blob_tree_unsafe")
                        sha256 = self._open_child_directory(blobs, "sha256")
                        try:
                            files += self._delete_files(
                                sha256,
                                lambda name: bool(_HEX_RE.fullmatch(name))
                                or bool(_BLOB_TEMP_RE.fullmatch(name)),
                            )
                            self._fsync(sha256)
                            self._remove_open_directory(blobs, "sha256", sha256)
                            directories += 1
                        finally:
                            os.close(sha256)
                        self._fsync(blobs)
                        self._remove_open_directory(owner, "blobs", blobs)
                        directories += 1
                    finally:
                        os.close(blobs)

                self._fsync(owner)
                self._remove_open_directory(owners, owner_name, owner)
                directories += 1
            finally:
                os.close(owner)
            self._fsync(owners)
        return files, directories

    def delete_owner(self, owner_user_id: str) -> OwnerDeletionReport:
        owner = _require_owner(owner_user_id)
        rows = self._repository.delete_owner_rows(owner)
        files, directories = self._delete_owner_files(owner)
        return OwnerDeletionReport(rows, files, directories)


__all__ = [
    "Artifact",
    "ArtifactBudgetError",
    "ArtifactConflictError",
    "ArtifactContractError",
    "ArtifactDownloadCapability",
    "ArtifactDownloadRegistry",
    "ArtifactError",
    "ArtifactIntegrityError",
    "ArtifactNotFoundError",
    "ArtifactRepository",
    "ArtifactSecurityError",
    "ArtifactStore",
    "Blob",
    "GarbageCollectionReport",
    "OwnerDeletionReport",
    "Publication",
    "PublicationRecovery",
    "RecoveryStatus",
]
