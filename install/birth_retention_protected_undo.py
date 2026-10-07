"""Native encrypted undo files joined to the actual undo journal under F6.

The opaque native handles are resolved against authenticated envelopes. Every
matching value in the journal conservatively retains its file; the native
``before_blob`` receipt also detects missing recovery data. A completed undo
may already have discarded that file through the native producer. Present
files remain referenced until the operation itself has been collected.

This is the protected-undo part of the inventory, not a complete installation
inventory or an enabled cleanup command. No decrypted data enters receipts.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import time

import protected_undo as native
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject, _digest
from install.birth_retention_sqlite import _iso, _utc


def _recovered(records):
    by_type = {record["type"]: record for record in records}
    reverse = by_type.get("undone", {}).get("reverse_results", {})
    return (len(by_type) == len(records) and set(by_type) == {"pending", "done", "undone"}
            and type(reverse.get("ok_count")) is int and reverse["ok_count"] > 0
            and type(reverse.get("fail_count", 0)) is int and reverse.get("fail_count", 0) == 0
            and not reverse.get("failed"))


def _values(records):
    """Bounded JSON traversal; native handles can occur in any receipt value."""
    stack, count = list(records), 0
    while stack:
        value = stack.pop()
        count += 1
        if count > 100_000:
            raise RetentionError("retention_inventory_incomplete", "undo reference budget")
        yield value
        if type(value) is dict:
            stack.extend(value.values())
        elif type(value) is list:
            stack.extend(value)


class _ProtectedUndoOwner(_PrivateFiles):
    name = "protected_undo_blobs"

    def __init__(self, *, root: Path, journal, key_path: Path):
        super().__init__(root=root, require_exclusion=journal.require_exclusion, owner=journal.owner)
        if not key_path.is_absolute() or ".." in key_path.parts or key_path.name != "admin.key":
            raise RetentionError("retention_owner_invalid", "protected undo key path")
        self.journal, self.key_path = journal, key_path
        # The shared configuration directory need not be 0700. Its ancestors
        # and owner still have to be trusted; the key itself is always 0600.
        self.key_files = _PrivateFiles(root=key_path.parent, require_exclusion=self.require_exclusion,
                                       owner=self.owner, private_directory=False)

    def identity(self, handle):
        if type(handle) is not str or not native._HANDLE.fullmatch(handle):
            raise RetentionError("retention_owner_invalid", "protected undo handle")
        return ObjectIdentity(self.name, self.root.as_uri(), "blob", handle)

    def _handle(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError("retention_owner_invalid", "foreign protected undo identity")
        return identity.local_id

    def _key(self):
        try:
            with self.key_files._directory() as (directory, custody):
                observed = self.key_files._read_file(directory, custody, self.key_path.name, None,
                                                     include_payload=True, max_bytes=4096)
            if observed is None:
                raise ValueError("missing key")
            raw = observed[4].decode("utf-8").strip()
            try:
                key = bytes.fromhex(raw)
            except ValueError:
                key = raw.encode("utf-8")
            if not key:
                raise ValueError("empty key")
            return observed[1], key
        except (OSError, ValueError):
            raise RetentionError("retention_owner_invalid", "protected undo key unavailable") from None

    def _read(self, directory, custody, handle, key=None):
        observed = self._read_file(directory, custody, handle + ".age", self.identity(handle),
                                   include_payload=True, max_bytes=64 * 1024 * 1024)
        if observed is None:
            return None
        key = self._key() if key is None else key
        try:
            envelope, data = native._decode_payload(observed[4], master_key=key[1])
            if (type(envelope) is not dict or set(envelope) != {
                    "format", "owner", "namespace", "created_at", "expires_at", "sha256", "data"}
                    or type(envelope["format"]) is not int or envelope["format"] != native._FORMAT):
                raise ValueError("format")
            native._validate_binding(envelope["owner"], envelope["namespace"])
            created, expires = envelope["created_at"], envelope["expires_at"]
            if (type(created) is not int or type(expires) is not int or created < 0
                    or not 86400 <= expires - created <= 3650 * 86400
                    or (expires - created) % 86400
                    or envelope["sha256"] != hashlib.sha256(data).hexdigest()):
                raise ValueError("envelope")
            born = datetime.fromtimestamp(created, timezone.utc)
            eligible = max(datetime.fromtimestamp(expires, timezone.utc),
                           max(observed[3], born) + timedelta(days=90))
        except (ValueError, TypeError, KeyError, OverflowError, OSError, RecursionError):
            raise RetentionError("retention_owner_invalid", "protected undo authentication") from None
        obj = OwnerObject(observed[0], _digest({"file": observed[1], "key": key[0]}),
                          NodeState.CLOSED, _iso(born), _iso(eligible))
        return obj, envelope["owner"], envelope["namespace"], envelope["sha256"]

    def inventory(self):
        operations, files = self.journal.scan(), {}
        deadline = time.monotonic() + 15
        try:
            with self._directory() as (directory, custody):
                names = self._names(directory)
                key = self._key() if names else None
                for name in names:
                    if not name.endswith(".age"):
                        raise RetentionError("retention_inventory_incomplete", "unknown protected undo entry")
                    handle = self.identity(name[:-4]).local_id
                    if time.monotonic() > deadline:
                        raise RetentionError("retention_inventory_incomplete", "protected undo inventory budget")
                    observed = self._read(directory, custody, handle, key)
                    if observed is None:
                        raise RetentionError("retention_owner_changed", "protected undo disappeared")
                    files[handle] = observed
                if names != self._names(directory) or (names and self._key()[0] != key[0]):
                    raise RetentionError("retention_owner_changed", "protected undo inventory changed")
        except FileNotFoundError:
            pass
        if files:
            # An absent source journal is not proof that existing secret data
            # was never referenced. The native empty journal is distinguishable.
            try:
                with self.journal._parent() as (directory, _), self.journal._read(directory) as observed:
                    journal_absent = observed[0] is None
            except FileNotFoundError:
                journal_absent = True
            if journal_absent:
                raise RetentionError("retention_inventory_incomplete", "protected undo journal absent")
        linked, actors_in_flight, objects = set(), set(), []
        for operation in operations:
            pending = [record for record in operation.records if record["type"] == "pending"]
            actor = (pending[0].get("actor") or "host") if len(pending) == 1 else None
            if actor is None and files:
                raise RetentionError("retention_inventory_incomplete", "unbound protected undo operation")
            kinds = [record["type"] for record in operation.records]
            if (kinds == ["pending"] or len(set(kinds)) != len(kinds)
                    or ("undone" in kinds and not _recovered(operation.records))
                    or any(record.get("reason") == "invalid_execution_receipt" for record in operation.records)):
                actors_in_flight.add(actor)
            refs = set()
            for value in _values(operation.records):
                if type(value) is str and value in files:
                    refs.add(value)
                if type(value) is dict and "before_blob" in value:
                    handle = self.identity(value["before_blob"]).local_id
                    if handle not in files:
                        if not _recovered(operation.records):
                            raise RetentionError("retention_inventory_incomplete", "referenced protected undo absent")
                    elif value.get("before_sha256") != files[handle][3]:
                        raise RetentionError("retention_inventory_incomplete", "protected undo receipt digest")
            if any(files[handle][1] != actor for handle in refs):
                raise RetentionError("retention_inventory_incomplete", "protected undo actor binding")
            linked.update(refs)
            objects.append(replace(operation.object, references=tuple(self.identity(h) for h in sorted(refs))))
        for handle, (obj, actor, _namespace, _digest_value) in files.items():
            roots = ((RootKind.IN_PROGRESS_JOB,) if actor in actors_in_flight else
                     (RootKind.OPEN_AUDIT,) if handle in linked else ())
            objects.append(replace(obj, state=NodeState.OPEN, eligible_after=None, roots=roots) if roots else obj)
        if (operations != self.journal.scan() or time.monotonic() > deadline):
            raise RetentionError("retention_owner_changed", "protected undo references changed")
        self.require_exclusion()
        return tuple(objects)

    def version(self, identity):
        handle = self._handle(identity)
        with self._directory() as (directory, custody):
            # A key remains necessary for a present file; absence is made
            # durable before the original receipt can record successful removal.
            observed = self._read(directory, custody, handle)
            if observed is None:
                os.fsync(directory)
                return None
            return observed[0].version

    def delete(self, identity, expected_version):
        handle = self._handle(identity)
        objects = self.inventory()
        obj = next((item for item in objects if item.identity == identity), None)
        if obj is not None and (obj.state is not NodeState.CLOSED or obj.roots
                or obj.eligible_after is None or _utc(obj.eligible_after) >= datetime.now(timezone.utc)):
            raise RetentionError("retention_owner_state_invalid", "protected undo still needed")
        with self._directory() as (directory, custody):
            observed = self._read(directory, custody, handle, self._key())
            if observed is None:
                os.fsync(directory)
                return
            if observed[0].version != expected_version or obj is None or obj.version != expected_version:
                raise RetentionError("retention_owner_changed", "protected undo version")
            self.require_exclusion()
            os.unlink(handle + ".age", dir_fd=directory)
            os.fsync(directory)
