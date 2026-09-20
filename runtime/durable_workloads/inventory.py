"""Fail-closed local inventory sealing for durable-plan v1."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .schema import (
    INVENTORY_SCHEMA_VERSION,
    MAX_INVENTORY_JSON_BYTES,
    canonical_json,
    inventory_digest,
    validate_inventory,
)
from .transactions import checked_checkpoint


class InventorySealError(ValueError):
    """A source set cannot be sealed without weakening its guarantees."""


_MAX_INVENTORY_ROOTS = 1024
_MAX_DISCOVERY_ENTRIES = 1_100_000
_MIN_DISCOVERY_ENTRIES = 4096
# Small inventories remain plain mappings for the common interactive path.
# This is only a representation threshold, never a source-count limit: larger
# inventories keep their metadata in the disposable spool and stay repeatable.
_IN_MEMORY_SOURCE_LIMIT = 512
_SPOOL_BATCH_SIZE = 1024
_MAX_CHUNK_BYTES = 16 * 1024 * 1024
_DEVICE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


@dataclass(frozen=True, slots=True)
class InventoryLimits:
    max_sources: int
    max_total_bytes: int
    max_depth: int

    def __post_init__(self) -> None:
        for name, value in (
            ("max_sources", self.max_sources),
            ("max_total_bytes", self.max_total_bytes),
            ("max_depth", self.max_depth),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InventorySealError(f"{name} must be a non-negative integer")
        if self.max_sources > 1_000_000:
            raise InventorySealError("max_sources exceeds the v1 limit")
        if self.max_depth > 128:
            raise InventorySealError("max_depth exceeds the v1 limit")


@dataclass(frozen=True, slots=True)
class _Candidate:
    path: Path
    locator: str


class _InventorySpool:
    """Process-local temporary database; SQLite removes it on close."""

    def __init__(self) -> None:
        self.connection = sqlite3.connect("")
        # This database is a disposable derivation.  Avoid a rollback journal
        # that could merely move the same large accumulation into RAM.
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA secure_delete=ON")
        self.connection.executescript(
            """
            CREATE TABLE candidate_files (
                locator_key BLOB PRIMARY KEY,
                locator TEXT NOT NULL,
                path_bytes BLOB NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE pending_directories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path_bytes BLOB NOT NULL,
                depth INTEGER NOT NULL,
                locator_prefix TEXT NOT NULL
            );
            CREATE TABLE observed_directories (
                path_bytes BLOB PRIMARY KEY,
                device TEXT NOT NULL,
                inode TEXT NOT NULL,
                mode TEXT NOT NULL,
                size TEXT NOT NULL,
                mtime_ns TEXT NOT NULL,
                ctime_ns TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE sealed_sources (
                ordinal INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL
            );
            """
        )
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.connection.close()


class _SpoolSources(Sequence[Mapping[str, Any]]):
    """Repeatable source view with constant Python memory."""

    def __init__(
        self,
        spool: _InventorySpool,
        *,
        start: int = 0,
        stop: int | None = None,
        step: int = 1,
    ) -> None:
        self._spool = spool
        self._start = start
        self._stop = self._source_count() if stop is None else stop
        self._step = step

    def _source_count(self) -> int:
        if self._spool.closed:
            raise InventorySealError("sealed inventory spool is closed")
        return int(self._spool.connection.execute(
            "SELECT COUNT(*) FROM sealed_sources"
        ).fetchone()[0])

    def __len__(self) -> int:
        return len(range(self._start, self._stop, self._step))

    def _absolute_ordinal(self, index: int) -> int:
        size = len(self)
        selected = index + size if index < 0 else index
        if selected < 0 or selected >= size:
            raise IndexError("sealed inventory source index out of range")
        return self._start + selected * self._step

    def __getitem__(
        self,
        index: int | slice,
    ) -> Mapping[str, Any] | Sequence[Mapping[str, Any]]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            return _SpoolSources(
                self._spool,
                start=self._start + start * self._step,
                stop=self._start + stop * self._step,
                step=self._step * step,
            )
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("sealed inventory source index must be an integer")
        ordinal = self._absolute_ordinal(index)
        row = self._spool.connection.execute(
            "SELECT payload_json FROM sealed_sources WHERE ordinal=?",
            (ordinal,),
        ).fetchone()
        if row is None:
            raise InventorySealError("sealed inventory spool is incomplete")
        return json.loads(str(row[0]))

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        if self._spool.closed:
            raise InventorySealError("sealed inventory spool is closed")
        if self._step != 1:
            for index in range(len(self)):
                yield self[index]
            return
        rows = self._spool.connection.execute(
            """
            SELECT payload_json FROM sealed_sources
            WHERE ordinal>=? AND ordinal<? ORDER BY ordinal
            """,
            (self._start, self._stop),
        )
        for row in rows:
            yield json.loads(str(row[0]))


class SealedInventory(Mapping[str, Any]):
    """Large immutable inventory backed by a disposable SQLite spool."""

    _KEYS = ("schema_version", "sealed", "digest", "sources")

    def __init__(self, spool: _InventorySpool, digest: str) -> None:
        self._spool = spool
        self._digest = digest
        self._sources = _SpoolSources(spool)

    def __getitem__(self, key: str) -> Any:
        if self._spool.closed:
            raise InventorySealError("sealed inventory spool is closed")
        if key == "schema_version":
            return INVENTORY_SCHEMA_VERSION
        if key == "sealed":
            return True
        if key == "digest":
            return self._digest
        if key == "sources":
            return self._sources
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._KEYS)

    def __len__(self) -> int:
        return len(self._KEYS)

    def close(self) -> None:
        self._spool.close()

    def __enter__(self) -> "SealedInventory":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _stat_identity(item: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(item.st_dev),
        int(item.st_ino),
        int(item.st_mode),
        int(item.st_size),
        int(item.st_mtime_ns),
        int(item.st_ctime_ns),
    )


def _validate_root(
    raw: str | os.PathLike[str],
    checkpoint: Callable[[str], None],
) -> Path:
    text = os.fspath(raw)
    if not isinstance(text, str) or not text or "\x00" in text:
        raise InventorySealError("inventory roots must be non-empty local paths")
    parsed = urlsplit(text)
    if parsed.scheme:
        raise InventorySealError("URI and device-style inventory roots are not allowed")
    if text.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\")):
        raise InventorySealError("network and device-style paths are not allowed")
    # Freeze the root identity now.  A later process-wide ``chdir`` must not
    # redirect discovery or the final revalidation to another tree.
    path = Path(os.path.abspath(text))
    try:
        checkpoint("inventory_root_before_lstat")
        metadata = path.lstat()
        checkpoint("inventory_root_after_lstat")
    except OSError as exc:
        raise InventorySealError("inventory root is missing or unreadable") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise InventorySealError("an inventory root cannot be a symbolic link")
    if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
        raise InventorySealError("inventory roots must be regular files or directories")
    return path


def _discover(
    roots: Sequence[str | os.PathLike[str]],
    limits: InventoryLimits,
    checkpoint: Callable[[str], None],
) -> tuple[_InventorySpool, int]:
    if (
        isinstance(roots, (str, bytes, os.PathLike))
        or not isinstance(roots, Sequence)
    ):
        raise InventorySealError("inventory roots must be an array of paths")
    if len(roots) > _MAX_INVENTORY_ROOTS:
        raise InventorySealError("inventory root count exceeds the v1 limit")
    normalized = tuple(_validate_root(raw, checkpoint) for raw in roots)
    if not normalized:
        return _InventorySpool(), 0
    absolute_keys = [os.path.abspath(os.fspath(path)) for path in normalized]
    if len(absolute_keys) != len(set(absolute_keys)):
        raise InventorySealError("inventory roots must be unique")

    spool = _InventorySpool()
    source_count = 0
    discovered_entries = 0
    discovery_limit = min(
        _MAX_DISCOVERY_ENTRIES,
        max(_MIN_DISCOVERY_ENTRIES, limits.max_sources * 2),
    )

    def claim_entry() -> None:
        nonlocal discovered_entries
        discovered_entries += 1
        if discovered_entries > discovery_limit:
            raise InventorySealError(
                "inventory discovery exceeds the bounded entry limit"
            )

    def add_candidate(path: Path, locator: str) -> None:
        nonlocal source_count
        try:
            locator_key = locator.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise InventorySealError(
                "inventory locator is not valid UTF-8"
            ) from exc
        if len(locator_key) > 1024:
            raise InventorySealError("inventory locator exceeds the v1 limit")
        source_count += 1
        if source_count > limits.max_sources:
            raise InventorySealError(
                "inventory source count exceeds the admitted maximum"
            )
        try:
            spool.connection.execute(
                """
                INSERT INTO candidate_files(locator_key, locator, path_bytes)
                VALUES (?, ?, ?)
                """,
                (locator_key, locator, os.fsencode(path)),
            )
        except (sqlite3.Error, UnicodeError) as exc:
            raise InventorySealError(
                "inventory candidate cannot be staged safely"
            ) from exc

    try:
        for root_index, root in enumerate(normalized):
            checkpoint("inventory_root_before_revalidation")
            metadata = root.lstat()
            checkpoint("inventory_root_after_revalidation")
            prefix = f"root-{root_index:04d}/"
            if stat.S_ISREG(metadata.st_mode):
                add_candidate(root, prefix + root.name)
            else:
                spool.connection.execute(
                    """
                    INSERT INTO pending_directories(
                        path_bytes, depth, locator_prefix
                    ) VALUES (?, 0, ?)
                    """,
                    (os.fsencode(root), prefix),
                )

        while True:
            pending = spool.connection.execute(
                """
                SELECT id, path_bytes, depth, locator_prefix
                FROM pending_directories ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            if pending is None:
                break
            spool.connection.execute(
                "DELETE FROM pending_directories WHERE id=?", (pending[0],)
            )
            directory = Path(os.fsdecode(bytes(pending[1])))
            depth = int(pending[2])
            prefix = str(pending[3])
            if depth > limits.max_depth:
                raise InventorySealError(
                    "inventory directory depth exceeds the admitted maximum"
                )
            try:
                checkpoint("inventory_directory_before_lstat")
                opened_directory = directory.lstat()
                checkpoint("inventory_directory_after_lstat")
                if not stat.S_ISDIR(opened_directory.st_mode):
                    raise InventorySealError(
                        "inventory directory changed during discovery"
                    )
                checkpoint("inventory_directory_before_scan")
                with os.scandir(directory) as entries:
                    for entry in entries:
                        claim_entry()
                        locator = prefix + entry.name
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if depth >= limits.max_depth:
                                    raise InventorySealError(
                                        "inventory directory depth exceeds "
                                        "the admitted maximum"
                                    )
                                locator.encode("utf-8")
                                spool.connection.execute(
                                    """
                                    INSERT INTO pending_directories(
                                        path_bytes, depth, locator_prefix
                                    ) VALUES (?, ?, ?)
                                    """,
                                    (
                                        os.fsencode(entry.path), depth + 1,
                                        locator + "/",
                                    ),
                                )
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                raise InventorySealError(
                                    "inventory contains a non-regular "
                                    "filesystem entry"
                                )
                        except OSError as exc:
                            raise InventorySealError(
                                "inventory entry changed during discovery"
                            ) from exc
                        add_candidate(Path(entry.path), locator)
                checkpoint("inventory_directory_after_scan")
                checkpoint("inventory_directory_before_final_lstat")
                final_directory = directory.lstat()
                checkpoint("inventory_directory_after_final_lstat")
                identity = _stat_identity(opened_directory)
                if identity != _stat_identity(final_directory):
                    raise InventorySealError(
                        "inventory directory changed during discovery"
                    )
                spool.connection.execute(
                    """
                    INSERT INTO observed_directories(
                        path_bytes, device, inode, mode, size,
                        mtime_ns, ctime_ns
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (os.fsencode(directory), *(str(value) for value in identity)),
                )
            except InventorySealError:
                raise
            except (OSError, sqlite3.Error, UnicodeError) as exc:
                raise InventorySealError(
                    "inventory directory changed or became unreadable"
                ) from exc
        checkpoint("inventory_spool_before_discovery_commit")
        spool.connection.commit()
        checkpoint("inventory_spool_after_discovery_commit")
        return spool, source_count
    except BaseException:
        spool.close()
        raise


def _verify_directories_unchanged(
    spool: _InventorySpool,
    checkpoint: Callable[[str], None],
) -> None:
    """Reject additions, removals or renames that occurred while hashing."""

    rows = spool.connection.execute(
        """
        SELECT path_bytes, device, inode, mode, size, mtime_ns, ctime_ns
        FROM observed_directories ORDER BY path_bytes
        """
    )
    for row in rows:
        path = Path(os.fsdecode(bytes(row[0])))
        try:
            checkpoint("inventory_directory_before_seal_revalidation")
            current = path.lstat()
            checkpoint("inventory_directory_after_seal_revalidation")
        except OSError as exc:
            raise InventorySealError(
                "inventory directory changed while sources were sealed"
            ) from exc
        expected = tuple(int(value) for value in row[1:])
        if not stat.S_ISDIR(current.st_mode) or _stat_identity(current) != expected:
            raise InventorySealError(
                "inventory directory changed while sources were sealed"
            )


def _stable_file_digest(
    path: Path,
    *,
    chunk_bytes: int,
    max_bytes: int,
    before_final_stat: Callable[[Path], None] | None,
    on_chunk: Callable[[bytes], None] | None = None,
    checkpoint: Callable[[str], None] | None = None,
) -> tuple[str, os.stat_result]:
    fault = checked_checkpoint(checkpoint)
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fault("inventory_source_before_open")
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise InventorySealError("inventory source cannot be opened safely") from exc
    try:
        fault("inventory_source_after_open")
        fault("inventory_source_before_initial_stat")
        opened = os.fstat(descriptor)
        fault("inventory_source_after_initial_stat")
        if not stat.S_ISREG(opened.st_mode):
            raise InventorySealError("inventory source is not a regular file")
        if opened.st_size > max_bytes:
            raise InventorySealError(
                "inventory byte size exceeds the admitted maximum"
            )
        digest = hashlib.sha256()
        size = 0
        fault("inventory_source_before_read")
        while True:
            block = os.read(descriptor, chunk_bytes)
            if not block:
                break
            size += len(block)
            if size > max_bytes:
                raise InventorySealError(
                    "inventory byte size exceeds the admitted maximum"
                )
            digest.update(block)
            if on_chunk is not None:
                on_chunk(block)
        fault("inventory_source_after_read")
        fault("inventory_source_before_final_stat")
        if before_final_stat is not None:
            before_final_stat(path)
        final_descriptor = os.fstat(descriptor)
        try:
            final_path = path.lstat()
        except OSError as exc:
            raise InventorySealError("inventory source disappeared while sealing") from exc
        fault("inventory_source_after_final_stat")
    finally:
        os.close(descriptor)

    if (
        _stat_identity(opened) != _stat_identity(final_descriptor)
        or _stat_identity(opened) != _stat_identity(final_path)
    ):
        raise InventorySealError("inventory source changed while its digest was calculated")
    return f"sha256:{digest.hexdigest()}", final_descriptor


def seal_local_inventory(
    roots: Sequence[str | os.PathLike[str]],
    *,
    device_id: str,
    limits: InventoryLimits,
    chunk_bytes: int = 1_048_576,
    before_final_stat: Callable[[Path], None] | None = None,
    on_source: Callable[[Mapping[str, Any], Path], None] | None = None,
    checkpoint: Callable[[str], None] | None = None,
) -> Mapping[str, Any]:
    """Discover and seal regular local files without persisting absolute paths."""

    if not isinstance(device_id, str) or not _DEVICE_ID_RE.fullmatch(device_id):
        raise InventorySealError("device_id is not a valid technical identity")
    if (
        isinstance(chunk_bytes, bool)
        or not isinstance(chunk_bytes, int)
        or not 4096 <= chunk_bytes <= _MAX_CHUNK_BYTES
    ):
        raise InventorySealError(
            "chunk_bytes must be an integer between 4096 and 16777216"
        )
    if on_source is not None and not callable(on_source):
        raise InventorySealError("on_source must be callable")
    fault = checked_checkpoint(checkpoint)

    fault("inventory_before_discovery")
    spool, source_count = _discover(roots, limits, fault)
    total_bytes = 0
    ordinal = 0
    try:
        fault("inventory_after_discovery")

        def seal_candidate(path_bytes: bytes, locator_value: str) -> None:
            nonlocal total_bytes, ordinal
            candidate = _Candidate(
                Path(os.fsdecode(bytes(path_bytes))), str(locator_value),
            )
            digest, metadata = _stable_file_digest(
                candidate.path,
                chunk_bytes=chunk_bytes,
                max_bytes=limits.max_total_bytes - total_bytes,
                before_final_stat=before_final_stat,
                checkpoint=fault,
            )
            total_bytes += int(metadata.st_size)
            if total_bytes > limits.max_total_bytes:
                raise InventorySealError(
                    "inventory byte size exceeds the admitted maximum"
                )
            source_id = hashlib.sha256(
                (
                    "metnos:durable-source:1\x00"
                    f"{device_id}\x00{candidate.locator}\x00{digest}"
                ).encode("utf-8")
            ).hexdigest()
            source = {
                "source_id": f"source_{source_id}",
                "ordinal": ordinal,
                "device_id": device_id,
                "locator_redacted": candidate.locator,
                "kind": "file",
                "size_bytes": int(metadata.st_size),
                "mtime_ns": int(metadata.st_mtime_ns),
                "content_digest": digest,
                "state": "ready",
                "accounted": True,
            }
            if on_source is not None:
                on_source(source, candidate.path)
            source_json = canonical_json(
                source, max_bytes=MAX_INVENTORY_JSON_BYTES,
            )
            spool.connection.execute(
                """
                INSERT INTO sealed_sources(ordinal, source_id, payload_json)
                VALUES (?, ?, ?)
                """,
                (ordinal, source["source_id"], source_json),
            )
            ordinal += 1

        last_locator_key: bytes | None = None
        while True:
            if last_locator_key is None:
                batch = spool.connection.execute(
                    """
                    SELECT locator_key, path_bytes, locator
                    FROM candidate_files ORDER BY locator_key LIMIT ?
                    """,
                    (_SPOOL_BATCH_SIZE,),
                ).fetchall()
            else:
                batch = spool.connection.execute(
                    """
                    SELECT locator_key, path_bytes, locator
                    FROM candidate_files
                    WHERE locator_key>? ORDER BY locator_key LIMIT ?
                    """,
                    (last_locator_key, _SPOOL_BATCH_SIZE),
                ).fetchall()
            if not batch:
                break
            for locator_key, path_bytes, locator_value in batch:
                seal_candidate(bytes(path_bytes), str(locator_value))
                last_locator_key = bytes(locator_key)
            spool.connection.executemany(
                "DELETE FROM candidate_files WHERE locator_key=?",
                ((row[0],) for row in batch),
            )
            fault("inventory_spool_before_batch_commit")
            spool.connection.commit()
            fault("inventory_spool_after_batch_commit")
        if ordinal != source_count:
            raise InventorySealError("inventory candidate spool is incomplete")
        fault("inventory_before_directory_revalidation")
        _verify_directories_unchanged(spool, fault)
        fault("inventory_after_directory_revalidation")
        fault("inventory_spool_before_finalize_commit")
        spool.connection.executescript(
            """
            BEGIN IMMEDIATE;
            DROP TABLE candidate_files;
            DROP TABLE pending_directories;
            DROP TABLE observed_directories;
            COMMIT;
            """
        )
        fault("inventory_spool_after_finalize_commit")
        sources = _SpoolSources(spool)
        digest = inventory_digest(sources)
        if source_count <= _IN_MEMORY_SOURCE_LIMIT:
            inline_sources = list(sources)
            payload: Mapping[str, Any] = {
                "schema_version": INVENTORY_SCHEMA_VERSION,
                "sealed": True,
                "digest": digest,
                "sources": inline_sources,
            }
            validate_inventory(payload)
            spool.close()
            return payload
        payload = SealedInventory(spool, digest)
        validate_inventory(payload)
        return payload
    except BaseException:
        spool.close()
        raise
