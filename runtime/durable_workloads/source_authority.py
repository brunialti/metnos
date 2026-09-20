"""Private, revocable authority for reopening sealed LRE sources.

The durable workload database keeps only redacted locators.  This separate
owner-scoped store retains the concrete locator under private filesystem
permissions and releases it only after re-attesting the sealed identity.  A
path or device locator is therefore never reconstructed from redacted data.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .coordinator import instant_text, normalize_instant, parse_instant
from .inventory import (
    InventoryLimits,
    InventorySealError,
    _stable_file_digest,
    seal_local_inventory,
)
from .migrations import BUSY_TIMEOUT_MS
from .models import ExecutionContext, SourceResolution
from .transactions import checked_checkpoint, immediate_transaction


SOURCE_AUTHORITY_SCHEMA_VERSION = 3
_MAX_AUTHORITY_LIFETIME = timedelta(days=366)
_MAX_LOCATOR_BYTES = 32_768
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SOURCE_ID_RE = re.compile(r"source_[a-f0-9]{64}")
_DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
_SESSION_RE = re.compile(r"\.session-[A-Za-z0-9_-]{6,64}")
_SUFFIX_RE = re.compile(r"\.[A-Za-z0-9]{1,16}")
_MAX_STALE_SESSION_SCAN = 256


class SourceAuthorityError(RuntimeError):
    """The requested source has no current, matching execution authority."""


RemoteAttestor = Callable[
    [str, str, Mapping[str, Any], ExecutionContext], SourceResolution
]


def default_authority_path() -> Path:
    from config import PATH_DURABLE_WORKLOADS

    return Path(PATH_DURABLE_WORKLOADS) / "source_authority.sqlite3"


def _identity(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise ValueError(f"{name} is invalid")
    return value


def _source_id(value: object) -> str:
    if not isinstance(value, str) or not _SOURCE_ID_RE.fullmatch(value):
        raise ValueError("source_id is invalid")
    return value


def _source_facts(source: Mapping[str, Any]) -> tuple[str, str, str, int, int]:
    if not isinstance(source, Mapping):
        raise TypeError("source must be an object")
    selected_source_id = _source_id(source.get("source_id"))
    device_id = _identity(source.get("device_id"), name="device_id")
    digest = source.get("content_digest")
    size_bytes = source.get("size_bytes")
    mtime_ns = source.get("mtime_ns")
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        raise ValueError("content_digest is invalid")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
        or isinstance(mtime_ns, bool)
        or not isinstance(mtime_ns, int)
        or mtime_ns < 0
    ):
        raise ValueError("source metadata is invalid")
    return selected_source_id, device_id, digest, size_bytes, mtime_ns


def _open_private(path: str | Path) -> tuple[sqlite3.Connection, Path | None]:
    if str(path) == ":memory:":
        selected: str | Path = ":memory:"
        file_path = None
    else:
        from config import ensure_private_dir, ensure_private_file

        file_path = Path(os.path.abspath(os.path.expanduser(str(path))))
        ensure_private_dir(file_path.parent)
        if file_path.is_symlink():
            raise SourceAuthorityError("source authority database cannot be a symlink")
        try:
            descriptor = os.open(
                file_path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        ensure_private_file(file_path)
        selected = file_path
    connection = sqlite3.connect(
        str(selected),
        timeout=BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys=ON")
        if file_path is not None:
            if str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower() != "wal":
                raise SourceAuthorityError("source authority database refused WAL mode")
            connection.execute("PRAGMA synchronous=FULL")
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{file_path}{suffix}")
                if candidate.exists() and not candidate.is_symlink():
                    candidate.chmod(0o600)
        return connection, file_path
    except BaseException:
        connection.close()
        raise


def _remove_session(root: Path, candidate: Path) -> None:
    if (
        candidate.parent != root
        or not _SESSION_RE.fullmatch(candidate.name)
        or candidate.is_symlink()
        or not candidate.is_dir()
    ):
        raise SourceAuthorityError("source snapshot session is unsafe")
    shutil.rmtree(candidate)


def _prune_stale_sessions(root: Path) -> int:
    """Remove bounded crash residue without touching another live lane."""

    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows uses normal close cleanup
        return 0
    removed = 0
    candidates = sorted(root.iterdir(), key=lambda item: item.name)
    for candidate in candidates[:_MAX_STALE_SESSION_SCAN]:
        if (
            not _SESSION_RE.fullmatch(candidate.name)
            or candidate.is_symlink()
            or not candidate.is_dir()
        ):
            continue
        lock_path = candidate / "lease.lock"
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags)
        except FileNotFoundError:
            try:
                old_enough = time.time() - candidate.stat().st_mtime >= 3600
            except OSError:
                continue
            if old_enough:
                _remove_session(root, candidate)
                removed += 1
            continue
        except OSError:
            continue
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            _remove_session(root, candidate)
            removed += 1
        finally:
            os.close(descriptor)
    return removed


def _write_all(descriptor: int, block: bytes) -> None:
    view = memoryview(block)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("source snapshot write made no progress")
        view = view[written:]


class SourceAuthority:
    """Owner/workload-scoped source mandates with bounded lifetime."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        snapshot_root: Path | None,
        local_device_id: str = "server",
        remote_attestor: RemoteAttestor | None = None,
        clock: Callable[[], datetime] | None = None,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        self._connection = connection
        self.local_device_id = _identity(local_device_id, name="local_device_id")
        self._remote_attestor = remote_attestor
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._checkpoint = checked_checkpoint(checkpoint)
        self._snapshot_root = snapshot_root
        self._snapshot_session: Path | None = None
        self._snapshot_lock: int | None = None
        self._snapshot_files: list[Path] = []
        self._closed = False

    @classmethod
    def open(
        cls,
        path: str | Path | None = None,
        *,
        snapshot_root: str | Path | None = None,
        local_device_id: str = "server",
        remote_attestor: RemoteAttestor | None = None,
        clock: Callable[[], datetime] | None = None,
        checkpoint: Callable[[str], None] | None = None,
    ) -> "SourceAuthority":
        connection, file_path = _open_private(path or default_authority_path())
        selected_snapshot_root = (
            Path(os.path.abspath(os.path.expanduser(str(snapshot_root))))
            if snapshot_root is not None
            else (
                file_path.parent / "source_snapshots"
                if file_path is not None else None
            )
        )
        try:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS source_authority_schema (
                    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                    version INTEGER NOT NULL CHECK (version>=1)
                );
                CREATE TABLE IF NOT EXISTS source_grants (
                    owner_user_id TEXT NOT NULL,
                    workload_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    locator_bytes BLOB NOT NULL,
                    content_digest TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK (size_bytes>=0),
                    mtime_ns INTEGER NOT NULL CHECK (mtime_ns>=0),
                    granted_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    check_generation INTEGER NOT NULL DEFAULT 0
                        CHECK (check_generation>=0),
                    revoked_at TEXT,
                    PRIMARY KEY (owner_user_id, workload_id, source_id),
                    CHECK (length(owner_user_id) BETWEEN 1 AND 128),
                    CHECK (length(workload_id) BETWEEN 1 AND 128),
                    CHECK (length(source_id) BETWEEN 1 AND 128),
                    CHECK (length(device_id) BETWEEN 1 AND 128),
                    CHECK (length(locator_bytes) BETWEEN 1 AND 32768)
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS source_grants_expiry
                    ON source_grants(expires_at, owner_user_id, workload_id);
                INSERT INTO source_authority_schema(singleton, version)
                VALUES (1, 3) ON CONFLICT(singleton) DO NOTHING;
                COMMIT;
                """
            )
            version_row = connection.execute(
                "SELECT version FROM source_authority_schema WHERE singleton=1"
            ).fetchone()
            if version_row is not None and int(version_row[0]) == 1:
                connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    ALTER TABLE source_grants ADD COLUMN checked_at TEXT;
                    ALTER TABLE source_grants ADD COLUMN check_generation
                        INTEGER NOT NULL DEFAULT 0;
                    UPDATE source_grants SET checked_at=granted_at
                    WHERE checked_at IS NULL;
                    UPDATE source_authority_schema SET version=3
                    WHERE singleton=1 AND version=1;
                    COMMIT;
                    """
                )
            elif version_row is not None and int(version_row[0]) == 2:
                connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    ALTER TABLE source_grants ADD COLUMN check_generation
                        INTEGER NOT NULL DEFAULT 0;
                    UPDATE source_authority_schema SET version=3
                    WHERE singleton=1 AND version=2;
                    COMMIT;
                    """
                )
            rows = connection.execute(
                "SELECT singleton, version FROM source_authority_schema"
            ).fetchmany(2)
            if len(rows) != 1 or tuple(rows[0]) != (
                1,
                SOURCE_AUTHORITY_SCHEMA_VERSION,
            ):
                raise SourceAuthorityError("source authority schema is incompatible")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS source_grants_check
                ON source_grants(
                    check_generation, checked_at, owner_user_id, workload_id
                )
                WHERE revoked_at IS NULL
                """
            )
            return cls(
                connection,
                snapshot_root=selected_snapshot_root,
                local_device_id=local_device_id,
                remote_attestor=remote_attestor,
                clock=clock,
                checkpoint=checkpoint,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: BaseException | None = None

        def cleanup(action: Callable[[], None]) -> None:
            nonlocal first_error
            try:
                action()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        cleanup(self._clear_snapshots)
        if self._snapshot_session is not None:
            if self._snapshot_root is None:
                def missing_snapshot_root() -> None:
                    raise SourceAuthorityError(
                        "source snapshot session has no private root"
                    )
                cleanup(missing_snapshot_root)
            else:
                session = self._snapshot_session
                cleanup(lambda: _remove_session(self._snapshot_root, session))
                self._snapshot_session = None
        if self._snapshot_lock is not None:
            descriptor = self._snapshot_lock
            self._snapshot_lock = None
            cleanup(lambda: os.close(descriptor))
        cleanup(self._connection.close)
        if first_error is not None:
            if isinstance(first_error, BaseException) and not isinstance(
                first_error, Exception
            ):
                raise first_error
            raise SourceAuthorityError(
                "source authority cleanup failed"
            ) from first_error

    def __enter__(self) -> "SourceAuthority":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _now(self) -> datetime:
        return normalize_instant(self._clock(), name="source authority clock")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        if self._connection.in_transaction:
            raise SourceAuthorityError("nested source authority transaction")
        with immediate_transaction(
            self._connection,
            self._checkpoint,
            name="source_authority_transaction",
        ) as connection:
            yield connection

    @staticmethod
    def _scope(owner_user_id: object, workload_id: object) -> tuple[str, str]:
        return (
            _identity(owner_user_id, name="owner_user_id"),
            _identity(workload_id, name="workload_id"),
        )

    def _ensure_snapshot_session(self) -> Path:
        if self._snapshot_session is not None:
            return self._snapshot_session
        if self._snapshot_root is None:
            raise SourceAuthorityError(
                "local source snapshots require a file-backed authority"
            )
        from config import ensure_private_dir

        root = ensure_private_dir(self._snapshot_root)
        _prune_stale_sessions(root)
        session = Path(tempfile.mkdtemp(prefix=".session-", dir=root))
        session.chmod(0o700)
        lock_path = session / "lease.lock"
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            try:
                import fcntl
            except ImportError:  # pragma: no cover - no advisory lock on Windows
                pass
            else:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._snapshot_session = session
            self._snapshot_lock = descriptor
            return session
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            shutil.rmtree(session, ignore_errors=True)
            raise

    def _clear_snapshots(self) -> None:
        first_error: OSError | None = None
        while self._snapshot_files:
            path = self._snapshot_files.pop()
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _materialize_local_snapshot(
        self,
        locator: str,
        *,
        source_id: str,
        digest: str,
        size_bytes: int,
        mtime_ns: int,
    ) -> tuple[Path, os.stat_result]:
        """Copy and re-attest one source into a lane-owned immutable path."""

        self._clear_snapshots()
        session = self._ensure_snapshot_session()
        suffix = Path(locator).suffix
        if not _SUFFIX_RE.fullmatch(suffix):
            suffix = ".bin"
        snapshot = session / f"{source_id}{suffix.lower()}"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            self._checkpoint("source_snapshot_before_temp_create")
            descriptor = os.open(snapshot, flags, 0o400)
            self._checkpoint("source_snapshot_after_temp_create")
            self._checkpoint("source_snapshot_before_copy")
            observed_digest, metadata = _stable_file_digest(
                Path(locator),
                chunk_bytes=1_048_576,
                max_bytes=size_bytes,
                before_final_stat=None,
                on_chunk=lambda block: _write_all(descriptor, block),
            )
            self._checkpoint("source_snapshot_after_copy")
            self._checkpoint("source_snapshot_before_fsync")
            os.fsync(descriptor)
            self._checkpoint("source_snapshot_after_fsync")
            os.fchmod(descriptor, 0o400)
            self._checkpoint("source_snapshot_before_verification")
            if (
                observed_digest != digest
                or int(metadata.st_size) != size_bytes
                or int(metadata.st_mtime_ns) != mtime_ns
            ):
                raise SourceAuthorityError("local source changed after sealing")
            self._checkpoint("source_snapshot_after_verification")
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
                descriptor = None
            snapshot.unlink(missing_ok=True)
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
        self._snapshot_files.append(snapshot)
        return snapshot, metadata

    def seal_and_register(
        self,
        roots: Sequence[str | os.PathLike[str]],
        *,
        owner_user_id: str,
        workload_id: str,
        device_id: str,
        limits: InventoryLimits,
        valid_until: datetime,
        chunk_bytes: int = 1_048_576,
    ) -> Mapping[str, Any]:
        """Atomically register concrete locators while sealing the inventory."""

        owner, workload = self._scope(owner_user_id, workload_id)
        device = _identity(device_id, name="device_id")
        current = self._now()
        expiry = normalize_instant(valid_until, name="valid_until")
        if not current < expiry <= current + _MAX_AUTHORITY_LIFETIME:
            raise ValueError("source authority lifetime is outside the allowed range")
        current_text = instant_text(current, name="granted_at")
        expiry_text = instant_text(expiry, name="expires_at")
        inventory: Mapping[str, Any] | None = None

        def register(source: Mapping[str, Any], path: Path) -> None:
            source_id, observed_device, digest, size_bytes, mtime_ns = _source_facts(source)
            if observed_device != device:
                raise SourceAuthorityError("sealed source device identity changed")
            locator = os.fsencode(path)
            if not 1 <= len(locator) <= _MAX_LOCATOR_BYTES:
                raise SourceAuthorityError("source locator exceeds the authority boundary")
            existing = self._connection.execute(
                """
                SELECT device_id, locator_bytes, content_digest, size_bytes, mtime_ns
                FROM source_grants
                WHERE owner_user_id=? AND workload_id=? AND source_id=?
                """,
                (owner, workload, source_id),
            ).fetchone()
            expected = (device, locator, digest, size_bytes, mtime_ns)
            if existing is not None and tuple(existing) != expected:
                raise SourceAuthorityError("source authority identity conflict")
            self._connection.execute(
                """
                INSERT INTO source_grants(
                    owner_user_id, workload_id, source_id, device_id,
                    locator_bytes, content_digest, size_bytes, mtime_ns,
                    granted_at, expires_at, checked_at, check_generation,
                    revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
                ON CONFLICT(owner_user_id, workload_id, source_id) DO UPDATE SET
                    granted_at=excluded.granted_at,
                    expires_at=excluded.expires_at,
                    checked_at=excluded.checked_at,
                    check_generation=0,
                    revoked_at=NULL
                """,
                (
                    owner, workload, source_id, device, locator, digest,
                    size_bytes, mtime_ns, current_text, expiry_text,
                    current_text,
                ),
            )

        try:
            with self._transaction():
                # A retry replaces the workload's complete sealed set.  Mark
                # every previous grant first; register() reactivates only the
                # sources present in the new inventory.  The transaction rolls
                # the marks back if discovery or hashing fails.
                self._connection.execute(
                    """
                    UPDATE source_grants
                    SET checked_at=?, revoked_at=?
                    WHERE owner_user_id=? AND workload_id=?
                      AND revoked_at IS NULL
                    """,
                    (current_text, current_text, owner, workload),
                )
                inventory = seal_local_inventory(
                    roots,
                    device_id=device,
                limits=limits,
                chunk_bytes=chunk_bytes,
                on_source=register,
                checkpoint=self._checkpoint,
            )
            return inventory
        except BaseException:
            if inventory is not None:
                close = getattr(inventory, "close", None)
                if callable(close):
                    close()
            raise

    def resolve(
        self,
        source: Mapping[str, Any],
        context: ExecutionContext,
    ) -> SourceResolution:
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be ExecutionContext")
        owner, workload = self._scope(
            context.owner_user_id,
            context.workload_id,
        )
        source_id, device_id, digest, size_bytes, mtime_ns = _source_facts(source)
        row = self._connection.execute(
            """
            SELECT device_id, locator_bytes, content_digest, size_bytes,
                   mtime_ns, expires_at, revoked_at
            FROM source_grants
            WHERE owner_user_id=? AND workload_id=? AND source_id=?
            """,
            (owner, workload, source_id),
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise SourceAuthorityError("source authority is unavailable")
        if parse_instant(str(row["expires_at"]), name="expires_at") <= self._now():
            raise SourceAuthorityError("source authority has expired")
        registered = (
            str(row["device_id"]),
            str(row["content_digest"]),
            int(row["size_bytes"]),
            int(row["mtime_ns"]),
        )
        if registered != (device_id, digest, size_bytes, mtime_ns):
            raise SourceAuthorityError("source authority does not match the inventory")
        locator = os.fsdecode(bytes(row["locator_bytes"]))
        if device_id != self.local_device_id:
            if self._remote_attestor is None:
                raise SourceAuthorityError("remote source attestation is unavailable")
            try:
                resolution = self._remote_attestor(
                    device_id, locator, source, context,
                )
            except Exception as exc:
                raise SourceAuthorityError(
                    "remote source attestation failed"
                ) from exc
            if (
                not isinstance(resolution, SourceResolution)
                or resolution.source_id != source_id
                or resolution.device_id != device_id
                or resolution.content_digest != digest
                or type(resolution.size_bytes) is not int
                or resolution.size_bytes != size_bytes
                or type(resolution.mtime_ns) is not int
                or resolution.mtime_ns != mtime_ns
                or not isinstance(resolution.authority, str)
                or not _IDENTITY_RE.fullmatch(resolution.authority)
            ):
                raise SourceAuthorityError(
                    "remote source attestation does not match the inventory"
                )
            return resolution

        try:
            snapshot, metadata = self._materialize_local_snapshot(
                locator,
                source_id=source_id,
                digest=digest,
                size_bytes=size_bytes,
                mtime_ns=mtime_ns,
            )
        except (InventorySealError, OSError) as exc:
            raise SourceAuthorityError(
                "local source cannot be re-attested"
            ) from exc
        return SourceResolution(
            value=str(snapshot),
            source_id=source_id,
            device_id=device_id,
            content_digest=digest,
            size_bytes=int(metadata.st_size),
            mtime_ns=int(metadata.st_mtime_ns),
            authority="local-source-registry-v1",
        )

    def revoke_workload(
        self,
        owner_user_id: str,
        workload_id: str,
        *,
        now: datetime | None = None,
    ) -> int:
        owner, workload = self._scope(owner_user_id, workload_id)
        current = normalize_instant(now or self._now(), name="revoked_at")
        changed = self._connection.execute(
            """
            UPDATE source_grants SET revoked_at=?
            WHERE owner_user_id=? AND workload_id=? AND revoked_at IS NULL
            """,
            (instant_text(current, name="revoked_at"), owner, workload),
        )
        return int(changed.rowcount)

    def reconcile_workloads(
        self,
        is_active: Callable[[str, str], bool],
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> int:
        """Revoke terminal/orphan scopes while rotating through active ones."""

        if not callable(is_active):
            raise TypeError("is_active must be callable")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer in 1..1000")
        current = normalize_instant(now or self._now(), name="reconcile time")
        current_text = instant_text(current, name="reconcile time")
        scopes = self._connection.execute(
            """
            SELECT owner_user_id, workload_id,
                   MIN(check_generation) AS oldest_generation,
                   MIN(checked_at) AS oldest_check
            FROM source_grants
            WHERE revoked_at IS NULL AND expires_at>?
            GROUP BY owner_user_id, workload_id
            ORDER BY oldest_generation, oldest_check,
                     owner_user_id, workload_id
            LIMIT ?
            """,
            (current_text, limit),
        ).fetchall()
        if not scopes:
            return 0
        decisions = [
            (
                str(row["owner_user_id"]),
                str(row["workload_id"]),
                bool(is_active(
                    str(row["owner_user_id"]), str(row["workload_id"]),
                )),
            )
            for row in scopes
        ]
        revoked = 0
        with self._transaction():
            for owner, workload, active in decisions:
                if active:
                    self._connection.execute(
                        """
                        UPDATE source_grants
                        SET checked_at=?, check_generation=check_generation+1
                        WHERE owner_user_id=? AND workload_id=?
                          AND revoked_at IS NULL AND expires_at>?
                        """,
                        (current_text, owner, workload, current_text),
                    )
                    continue
                changed = self._connection.execute(
                    """
                    UPDATE source_grants SET checked_at=?, revoked_at=?
                    WHERE owner_user_id=? AND workload_id=?
                      AND revoked_at IS NULL
                    """,
                    (current_text, current_text, owner, workload),
                )
                revoked += int(changed.rowcount)
        return revoked

    def prune(self, *, limit: int = 1000, now: datetime | None = None) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer in 1..1000")
        current = normalize_instant(now or self._now(), name="prune time")
        current_text = instant_text(current, name="prune time")
        deleted = 0
        with self._transaction():
            rows = self._connection.execute(
                """
                SELECT owner_user_id, workload_id, source_id
                FROM source_grants
                WHERE revoked_at IS NOT NULL OR expires_at<=?
                ORDER BY COALESCE(revoked_at, expires_at), owner_user_id,
                         workload_id, source_id
                LIMIT ?
                """,
                (current_text, limit),
            ).fetchall()
            for row in rows:
                changed = self._connection.execute(
                    """
                    DELETE FROM source_grants
                    WHERE owner_user_id=? AND workload_id=? AND source_id=?
                      AND (revoked_at IS NOT NULL OR expires_at<=?)
                    """,
                    (*tuple(row), current_text),
                )
                deleted += int(changed.rowcount)
        return deleted


__all__ = [
    "SOURCE_AUTHORITY_SCHEMA_VERSION",
    "SourceAuthority",
    "SourceAuthorityError",
    "default_authority_path",
]
