"""Recoverable native JSONL compaction shared by F6 owners.

Subclasses supply only record identity and native closure rules. The common
mechanism keeps every surviving byte and object version, checks file custody
at each replacement, and resumes the original partial copy after interruption.
External references are joined by the enclosing installation inventory.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time

from executor_birth_canonical import _pairs_v1
from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject, _digest
from install.birth_retention_sqlite import _utc


_MAX_BYTES = 128 * 1024 * 1024
_TEMP_PREFIX = ".metnos-f6-jsonl-"


@dataclass(frozen=True)
class JournalEntry:
    object: OwnerObject
    records: tuple[dict, ...]


def _custody(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)


def _file_version(info):
    return (*_custody(info), info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _time(value):
    try:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("timestamp")
        return datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise RetentionError("retention_owner_state_invalid", "journal timestamp") from exc


def _invalid_constant(value):
    raise ValueError(value)


class _JsonlOwner:
    temporary_prefix = _TEMP_PREFIX
    node_type = "evidence"

    def __init__(self, *, path: Path, require_exclusion, owner):
        if (os.name != "posix" or not path.is_absolute() or ".." in path.parts
                or path == Path(path.anchor)):
            raise RetentionError("retention_owner_invalid", "journal path")
        self.path, self.require_exclusion, self.owner = path, require_exclusion, owner

    def identity(self, op_id):
        return ObjectIdentity(self.name, self.path.as_uri(), self.node_type, op_id)

    def _identity(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError("retention_owner_invalid", "foreign journal operation")

    @contextmanager
    def _parent(self):
        self.require_exclusion()
        fd = os.open(self.path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        custody = []
        entered = False
        try:
            for part in self.path.parent.parts[1:]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
                info = os.fstat(fd)
                if self.owner is not None and (
                    info.st_uid not in {0, self.owner[0]} or info.st_gid not in {0, self.owner[1]}
                    or info.st_mode & 0o022
                ):
                    raise RetentionError("retention_owner_path_invalid", "journal parent custody")
                custody.append(_custody(info))
            entered = True
            yield fd, tuple(custody)
            self.require_exclusion()
            if _custody(os.stat(self.path.parent, follow_symlinks=False)) != _custody(os.fstat(fd)):
                raise RetentionError("retention_owner_changed", "journal parent replaced")
        except FileNotFoundError as exc:
            if not entered:
                raise
            raise RetentionError("retention_owner_changed", "journal parent disappeared") from exc
        except OSError as exc:
            raise RetentionError("retention_owner_path_invalid", "journal parent") from exc
        finally:
            os.close(fd)

    def _regular(self, fd):
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) not in {0o600, 0o640, 0o644}
                or (self.owner is not None and (info.st_uid, info.st_gid) != self.owner)):
            raise RetentionError("retention_owner_path_invalid", "journal file custody")
        return info

    @staticmethod
    def _bytes(fd):
        deadline, parts, size = time.monotonic() + 15, [], 0
        while chunk := os.read(fd, 1024 * 1024):
            parts.append(chunk)
            size += len(chunk)
            if size > _MAX_BYTES or time.monotonic() > deadline:
                raise RetentionError("retention_inventory_incomplete", "journal budget")
        return b"".join(parts)

    @contextmanager
    def _read(self, parent):
        try:
            fd = os.open(self.path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            yield None, (), ()
            return
        try:
            before = self._regular(fd)
            payload = self._bytes(fd)
            lines, records = self._decode(payload)
            self._same(parent, fd, before)
            yield before, lines, records
        except OSError as exc:
            raise RetentionError("retention_owner_path_invalid", "journal read") from exc
        finally:
            os.close(fd)

    def _decode(self, payload):
        """The same strict native records before or after archival compression."""
        lines, records = [], []
        if payload and not payload.endswith(b"\n"):
            raise RetentionError("retention_inventory_incomplete", "unfinished journal record")
        for raw in payload.splitlines(keepends=True):
            if not raw.strip():
                lines.append((None, raw))
                continue
            if len(raw) > 8 * 1024 * 1024 or len(records) >= 100_000:
                raise RetentionError("retention_inventory_incomplete", "journal record budget")
            try:
                record = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_v1,
                                    parse_constant=_invalid_constant)
                if type(record) is not dict:
                    raise ValueError("journal record")
                key = self._record_key(record)
                self.identity(key)
            except (ValueError, TypeError, RecursionError) as exc:
                raise RetentionError("retention_inventory_incomplete", "invalid journal record") from exc
            records.append(record)
            lines.append((key, raw))
        return tuple(lines), tuple(records)

    def _same(self, parent, fd, before):
        if (_file_version(self._regular(fd)) != _file_version(before) or _file_version(os.stat(
                self.path.name, dir_fd=parent, follow_symlinks=False)) != _file_version(before)):
            raise RetentionError("retention_owner_changed", "journal replaced or appended")

    def _record_key(self, record):
        raise NotImplementedError

    def _entries(self, custody, info, lines, records):
        raise NotImplementedError

    def _groups(self, custody, info, lines, records):
        grouped, original = {}, {}
        for record in records:
            grouped.setdefault(self._record_key(record), []).append(record)
        for key, raw in lines:
            if key is not None:
                original.setdefault(key, []).append(hashlib.sha256(raw).hexdigest())
        return {key: (tuple(items), _digest({"parents": custody,
            "custody": (info.st_mode, info.st_uid, info.st_gid), "records": original[key]}))
            for key, items in grouped.items()}

    def scan(self):
        try:
            with self._parent() as (parent, custody):
                # A scan cannot turn leftover recovery files into absent work.
                deadline = time.monotonic() + 15
                with os.scandir(parent) as entries:
                    for index, entry in enumerate(entries):
                        if (entry.name.startswith(".metnos-f6-") or index >= 100_000
                                or time.monotonic() > deadline):
                            raise RetentionError("retention_inventory_incomplete", "journal directory or recovery pending")
                with self._read(parent) as (info, lines, records):
                    return () if info is None else self._entries(custody, info, lines, records)
        except FileNotFoundError:
            return ()

    def version(self, identity):
        self._identity(identity)
        with self._parent() as (parent, custody), self._read(parent) as (info, lines, records):
            operations = () if info is None else self._entries(custody, info, lines, records)
            found = next((item.object for item in operations if item.object.identity == identity), None)
            if found is None:
                os.fsync(parent)
            return None if found is None else found.version

    def delete(self, identity, expected_version):
        self._identity(identity)
        with self._parent() as (parent, custody), self._read(parent) as (info, lines, records):
            operations = () if info is None else self._entries(custody, info, lines, records)
            found = next((item.object for item in operations if item.object.identity == identity), None)
            if found is None:
                os.fsync(parent)
                return
            if found.version != expected_version:
                raise RetentionError("retention_owner_changed", "journal operation version")
            if (found.state is not NodeState.CLOSED or found.roots
                    or _utc(found.eligible_after) >= datetime.now(timezone.utc)):
                raise RetentionError("retention_owner_state_invalid", "journal operation still retained")
            payload = b"".join(raw for op_id, raw in lines if op_id != identity.local_id)
            temporary = self.temporary_prefix + identity.key.node_id[7:] + ".tmp"
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                fd = os.open(temporary, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
            except FileExistsError:
                fd = os.open(temporary, flags, dir_fd=parent)
            try:
                temporary_info = os.fstat(fd)
                # Recovery also covers a crash between creation, chown and
                # chmod. Only the administrator/native custody and private
                # creation mode are accepted until installation is complete.
                if (not stat.S_ISREG(temporary_info.st_mode) or temporary_info.st_nlink != 1
                        or temporary_info.st_uid not in {os.geteuid(), info.st_uid}
                        or temporary_info.st_gid not in {os.getegid(), os.fstat(parent).st_gid, info.st_gid}
                        or stat.S_IMODE(temporary_info.st_mode) not in {0o600, stat.S_IMODE(info.st_mode)}):
                    raise RetentionError("retention_owner_path_invalid", "journal recovery custody")
                partial = self._bytes(fd)
                if not payload.startswith(partial):
                    raise RetentionError("retention_owner_changed", "journal recovery copy")
                remaining = memoryview(payload)[len(partial):]
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise RetentionError("retention_owner_invalid", "journal compaction write")
                    remaining = remaining[written:]
                if (temporary_info.st_uid, temporary_info.st_gid) != (info.st_uid, info.st_gid):
                    os.fchown(fd, info.st_uid, info.st_gid)
                os.fchmod(fd, stat.S_IMODE(info.st_mode))
                os.fsync(fd)
                self.require_exclusion()
                if (_file_version(os.stat(self.path.name, dir_fd=parent, follow_symlinks=False)) != _file_version(info)
                        or _file_version(os.stat(temporary, dir_fd=parent, follow_symlinks=False)) != _file_version(os.fstat(fd))):
                    raise RetentionError("retention_owner_changed", "journal compaction source replaced")
                os.replace(temporary, self.path.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                os.close(fd)
