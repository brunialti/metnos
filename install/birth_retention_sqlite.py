"""Native SQLite owners for exclusive F6 maintenance.

These readers never create or migrate an application database. A row's version
binds its complete stored values, schema and physical database identity. The
complete inventory coordinator still has to resolve references across owners;
raw records are deliberately not graph nodes. There is no executable entry or
runtime deletion API here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
from pathlib import Path
import sqlite3
import stat
import time
from typing import Callable

from executor_birth_canonical import encode_canonical_ascii_v1 as canonical
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import ObjectIdentity


@dataclass(frozen=True)
class NativeRow:
    identity: ObjectIdentity
    version: str
    values: dict
    related: tuple[tuple[str, tuple[dict, ...]], ...] = ()


@dataclass(frozen=True)
class RowState:
    state: NodeState
    created_at: str
    eligible_after: str | None
    roots: tuple[RootKind, ...]


def _utc(value: object) -> datetime:
    if type(value) is not str:
        raise RetentionError("retention_owner_state_invalid", "timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("naive timestamp")
        return result.astimezone(timezone.utc)
    except ValueError as exc:
        raise RetentionError("retention_owner_state_invalid", "timestamp") from exc


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _retained(created: object, *roots: RootKind) -> RowState:
    return RowState(NodeState.OPEN, _iso(_utc(created)), None, tuple(roots))


def _closed(created: object, closed: object, window_end: object) -> RowState:
    start, end, expiry = (_utc(value) for value in (created, closed, window_end))
    if end < start:
        raise RetentionError("retention_owner_state_invalid", "closure before creation")
    # The approved operational horizon starts at the last relevant activity,
    # and cannot shorten an owner's explicit validity or recovery window.
    eligible = max(end + timedelta(days=90), expiry)
    return RowState(NodeState.CLOSED, _iso(start), _iso(eligible), ())


def _proposal_state(row: dict) -> RowState:
    if row["state"] == "expired":
        if row.get("last_action"):
            raise RetentionError("retention_owner_state_invalid", "expired human decision")
        closed = _iso(max(_utc(row["expired_at"]), _utc(row["last_seen"])))
        return _closed(row["first_seen"], closed, closed)
    if row["state"] not in {"pending", "dormant", "applied", "rejected", "blocked"}:
        raise RetentionError("retention_owner_state_invalid", "proposal state")
    # A dormant proposal can reappear. Human decisions are persistent memory,
    # including a rejection represented by dormant, not an expired cache.
    return _retained(row["first_seen"], RootKind.OPEN_REVISION)


def _intent_state(row: dict) -> RowState:
    from change_intents import ALL_STATES, STATE_ROLLED_BACK

    if row["state"] not in ALL_STATES:
        raise RetentionError("retention_owner_state_invalid", "intent state")
    # Even finalized can transition to rolled_back, rejected to proposed and
    # failed to accepted. Age and these labels do not close their undo window.
    if row["state"] != STATE_ROLLED_BACK:
        return _retained(row["discovered_at"], RootKind.OPEN_REVISION)
    last_activity = _iso(max(_utc(row["rolled_back_at"]), _utc(row["updated_at"])))
    return _closed(row["discovered_at"], last_activity, last_activity)


def _approval_state(row: dict) -> RowState:
    from approval_registry import VALID_STATUS

    if row["status"] not in VALID_STATUS:
        raise RetentionError("retention_owner_state_invalid", "approval state")
    if row["status"] == "pending":
        # Expiry does not silently perform the owner's transition here.
        return _retained(row["created_at"], RootKind.OPEN_APPROVAL)
    return _closed(row["created_at"], row["decision_at"], row["expires_at"])


def _encoded_row(row: dict) -> bytes:
    values = {}
    for name, value in row.items():
        if isinstance(value, bytes):
            values[name] = ["blob", value.hex()]
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise RetentionError("retention_owner_state_invalid", "nonfinite SQL value")
            values[name] = ["real", value.hex()]
        elif value is None or type(value) in (str, int):
            values[name] = [type(value).__name__, value]
        else:
            raise RetentionError("retention_owner_state_invalid", "SQL value type")
    return canonical(values)


def _bounded_rows(connection, query, parameters):
    """Bound a native relation before it becomes part of an object version."""
    result, used = [], 0
    for row in connection.execute(query, parameters):
        row = dict(row)
        used += len(_encoded_row(row))
        if len(result) >= 100_000 or used > 128 * 1024 * 1024:
            raise RetentionError("retention_inventory_incomplete", "related row limit")
        result.append(row)
    return tuple(result)


def _schema(connection) -> tuple[tuple, ...]:
    return tuple(tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))


class _SQLiteOwner:
    """One code-selected table. A caller cannot select SQL or change the schema.

    The installed assembly supplies its native exclusion checker and service
    account. Private constructor arguments also allow real disposable stores in
    portable tests; they are not exposed by an administrative command.
    """
    def __init__(self, *, name: str, path: Path, schema: str | Callable, table: str,
                 primary_key: str | tuple[str, ...], node_type: str, state: Callable[[dict], RowState],
                 require_exclusion: Callable[[], None], owner: tuple[int, int] | None):
        keys = (primary_key,) if isinstance(primary_key, str) else primary_key
        if (not path.is_absolute() or not keys or len(set(keys)) != len(keys)
                or any(not item.isidentifier() for item in (table, *keys))):
            raise RetentionError("retention_owner_invalid")
        self.name, self.path, self.table, self.primary_key = name, path, table, primary_key
        self.primary_keys = keys
        self._order = ",".join(f'"{key}"' for key in keys)
        self._where = " AND ".join(f'"{key}"=?' for key in keys)
        self.node_type, self.state = node_type, state
        self.require_exclusion, self.owner = require_exclusion, owner
        with sqlite3.connect(":memory:") as template:
            # The native migration may initialise ONLY this disposable template.
            # Opening the real store never creates, migrates or repairs it.
            schema(template) if callable(schema) else template.executescript(schema)
            self.expected_schema = _schema(template)
            columns = template.execute(f'PRAGMA table_info("{table}")').fetchall()
            if tuple(column[1] for column in sorted(columns, key=lambda c: c[5]) if column[5]) != keys:
                raise RetentionError("retention_owner_invalid", "primary key")

    def _files(self, *, allow_absent=False):
        # Resolve does not open SQLite or create missing parents. Inspect links
        # separately so a dangling database/WAL link is never treated as absent.
        if self.path.resolve(strict=False) != self.path:
            raise RetentionError("retention_owner_path_invalid")
        for parent in self.path.parents:
            try:
                info = parent.lstat()
            except FileNotFoundError:
                continue  # a lazily created store, only allowed by scan()
            if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    or (self.owner is not None and (
                        info.st_uid not in {0, self.owner[0]}
                        or info.st_gid not in {0, self.owner[1]} or info.st_mode & 0o022))):
                raise RetentionError("retention_owner_path_invalid", "parent custody")
        try:
            main = self.path.lstat()
        except FileNotFoundError:
            # A sidecar without its database is not an empty optional store.
            if any(os.path.lexists(str(self.path) + suffix) for suffix in ("-wal", "-shm", "-journal")):
                raise RetentionError("retention_owner_path_invalid", "orphan sidecar")
            if allow_absent:
                return None
            raise RetentionError("retention_owner_missing")
        for path in (self.path, *(Path(str(self.path) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    or (self.owner is not None and (
                        (info.st_uid, info.st_gid) != self.owner or info.st_mode & 0o022))):
                raise RetentionError("retention_owner_path_invalid", "database or sidecar")
        return (main.st_dev, main.st_ino, stat.S_IMODE(main.st_mode),
                main.st_uid, main.st_gid, main.st_nlink)

    @contextmanager
    def _open(self, *, write=False, allow_absent=False):
        self.require_exclusion()
        files = self._files(allow_absent=allow_absent)
        if files is None:
            yield None, None
            self.require_exclusion()
            if self._files(allow_absent=True) is not None:
                raise RetentionError("retention_owner_changed", "absent store appeared")
            return
        connection = sqlite3.connect(self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
                                     uri=True, isolation_level=None, timeout=5)
        deadline = time.monotonic() + 15
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024 * 1024)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if self._files() != files or _schema(connection) != self.expected_schema:
                raise RetentionError("retention_owner_changed", "file identity or schema")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RetentionError("retention_owner_invalid", "database integrity")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RetentionError("retention_owner_invalid", "orphan reference")
            self._validate_store(connection)
            yield connection, files
            self.require_exclusion()
            if self._files() != files:
                raise RetentionError("retention_owner_changed", "database replaced")
            if write:
                connection.commit()
        except sqlite3.Error as exc:
            raise RetentionError("retention_owner_invalid", "SQLite read or effect") from exc
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def _validate_store(self, connection) -> None:
        """Owner metadata is checked even when the inventory has no rows."""

    def _related(self, connection, values: dict) -> tuple:
        return ()

    def state_of(self, row: NativeRow) -> RowState:
        return self.state(row.values)

    def _native(self, values: dict, files: tuple, related: tuple = ()) -> NativeRow:
        keys = [values[key] for key in self.primary_keys]
        if any(type(key) not in (str, int) for key in keys):
            raise RetentionError("retention_owner_state_invalid", "row identity")
        # Keep SQL integer and text keys distinct, as well as store and owner.
        typed_keys = [[type(key).__name__, key] for key in keys]
        identity = ObjectIdentity(self.name, self.path.as_uri(), self.node_type,
                                  canonical(typed_keys[0] if len(keys) == 1 else typed_keys).decode("ascii"))
        version = "sha256:" + hashlib.sha256(canonical({
            "database": files, "schema": self.expected_schema,
            "row": _encoded_row(values).decode("ascii"),
            "related": [(name, [_encoded_row(row).decode("ascii") for row in rows])
                        for name, rows in related],
        })).hexdigest()
        return NativeRow(identity, version, values, related)

    def scan(self) -> tuple[NativeRow, ...]:
        records, used = [], 0
        with self._open(allow_absent=True) as (connection, files):
            if connection is None:
                return ()
            for row in connection.execute(f'SELECT * FROM "{self.table}" ORDER BY {self._order}'):
                values = dict(row)
                used += len(_encoded_row(values))
                related = self._related(connection, values)
                used += sum(len(_encoded_row(item)) for _name, rows in related for item in rows)
                if len(records) >= 100_000 or used > 128 * 1024 * 1024:
                    raise RetentionError("retention_inventory_incomplete", "SQLite inventory limit")
                record = self._native(values, files, related)
                self.state_of(record)  # unknown states are not silently retained or collected
                records.append(record)
        return tuple(records)

    def _row(self, connection, identity, files):
        import json
        if (identity.owner != self.name or identity.store != self.path.as_uri()
                or identity.node_type != self.node_type or identity.contract is not None):
            raise RetentionError("retention_owner_invalid", "foreign identity")
        try:
            decoded = json.loads(identity.local_id)
            items = [decoded] if len(self.primary_keys) == 1 else decoded
            if (type(items) is not list or len(items) != len(self.primary_keys)
                    or canonical(decoded).decode("ascii") != identity.local_id):
                raise ValueError("key type")
            keys = []
            for item in items:
                if type(item) is not list or len(item) != 2:
                    raise ValueError("key structure")
                kind, key = item
                if (kind != type(key).__name__ or type(key) not in (str, int)
                        or (type(key) is int and not -(2**63) <= key < 2**63)):
                    raise ValueError("key type")
                keys.append(key)
        except (ValueError, TypeError) as exc:
            raise RetentionError("retention_owner_invalid", "row key") from exc
        row = connection.execute(f'SELECT * FROM "{self.table}" WHERE {self._where}', keys).fetchone()
        result = self._native(dict(row), files, self._related(connection, dict(row))) if row is not None else None
        if result is not None and result.identity != identity:
            raise RetentionError("retention_owner_invalid", "row key representation")
        return result, keys

    def version(self, identity: ObjectIdentity) -> str | None:
        with self._open() as (connection, files):
            row, _key = self._row(connection, identity, files)
            return None if row is None else row.version

    def _delete_related(self, connection, row: NativeRow) -> None:
        if row.related:
            raise RetentionError("retention_owner_invalid", "unknown related records")

    def delete(self, identity: ObjectIdentity, expected_version: str) -> None:
        with self._open(write=True) as (connection, files):
            row, keys = self._row(connection, identity, files)
            if row is None:
                return
            if row.version != expected_version:
                raise RetentionError("retention_owner_changed", "row version")
            state = self.state_of(row)
            if (state.state is not NodeState.CLOSED or state.roots
                    or _utc(state.eligible_after) >= datetime.now(timezone.utc)):
                raise RetentionError("retention_owner_state_invalid", "row still retained")
            self.require_exclusion()
            if self._files() != files:
                raise RetentionError("retention_owner_changed", "database replaced")
            self._delete_related(connection, row)
            removed = connection.execute(f'DELETE FROM "{self.table}" WHERE {self._where}', keys)
            if removed.rowcount != 1:
                raise RetentionError("retention_owner_changed", "delete count")


def _operational_sqlite_owners(require_exclusion, account_owner, *,
                               proposals_state_path, change_intents_path,
                               approval_registry_path):
    """Compose three stores explicitly selected by the installed caller.

    Never fall back to ambient configuration: module defaults may have been
    imported for a different account or installation. Path selection and its
    authentication belong to the installed coordinator, before composition.
    This is a subset of the full owner inventory, never a complete F6 plan.
    Birth, LRE, scheduler and file owners are assembled separately.
    """
    import approval_registry
    import change_intents
    import proposals_state

    definitions = (
        ("proposals_state", proposals_state_path, proposals_state.init_schema,
         "proposals_state", "sig_key", "proposal", _proposal_state),
        ("change_intents", change_intents_path, change_intents._SCHEMA,
         "change_intents", "id", "revision", _intent_state),
        ("approval_registry", approval_registry_path,
         approval_registry.SCHEMA, "pending", "token", "approval", _approval_state),
    )
    return tuple(_SQLiteOwner(name=name, path=path, schema=schema, table=table,
                             primary_key=key, node_type=node_type, state=state,
                             require_exclusion=require_exclusion, owner=account_owner)
                 for name, path, schema, table, key, node_type, state in definitions)
