"""Native content-addressed undo backups, under exclusive F6 maintenance.

This owner covers the canonical turn/blob/SHA256.bin layout. Other history
documents are retained with their entire turn; user-root quarantine is never
traversed or removed. Unknown formats stop inventory. It never treats an unknown file as garbage.
References keep backups until the actual undo record is collected; a later
inventory can collect the now-unreferenced files using the same receipt path.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import hashlib
import json
from pathlib import Path
import re
import time

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_protected_undo import _values
from install.birth_retention_sqlite import _iso, _utc


_TURN = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_MAX_DOCUMENT_BYTES = 128 * 1024 * 1024
_BLOB = re.compile(r"[0-9a-f]{64}\.bin")

_PLAN = re.compile(r"[0-9a-f]{64}\.frozen-plan\.json")
_RECEIPT = re.compile(r"[0-9a-f]{64}\.organize-receipt\.json")


def _document(name, payload):
    """Minimal native historical consistency, never a terminal authenticator."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate document key")
            result[key] = value
        return result
    try:
        value = json.loads(payload.decode('ascii'), object_pairs_hook=pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if (type(value) is not dict or type(value.get('schema')) is not int
                or value['schema'] != 1 or type(value.get('token')) is not str
                or re.fullmatch(r'[0-9a-f]{64}', value['token']) is None
                or type(value.get('actions')) is not list
                or any(type(item) is not dict for item in value['actions'])
                or type(value.get('binding')) is not dict
                or type(value.get('policy')) is not dict):
            raise ValueError('document schema')
        token = value['token']
        if _PLAN.fullmatch(name):
            material = {key: item for key, item in value.items()
                        if key not in {'token', 'plan_path', 'plan_sha256'}}
            digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':'),
                                               ensure_ascii=True).encode('ascii')).hexdigest()
            if token != digest or name != token + '.frozen-plan.json':
                raise ValueError('plan identity')
        else:
            digest = hashlib.sha256(b'metnos-organize-receipt-v1\0' + token.encode('ascii')).hexdigest()
            if (name != digest + '.organize-receipt.json'
                    or value.get('kind') != 'organize_files_transaction'
                    or value.get('status') not in {'prepared', 'applying', 'committed', 'rolling_back',
                                                   'rolled_back', 'partial', 'undoing', 'undone'}):
                raise ValueError('receipt identity')
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise RetentionError('retention_inventory_incomplete', 'historical document inconsistent') from exc


class _HistoryBackupOwner(_PrivateFiles):
    name = "history_backup_blobs"

    def __init__(self, *, root: Path, journal):
        super().__init__(root=root, require_exclusion=journal.require_exclusion,
                         owner=journal.owner, private_directory=False,
                         file_modes=frozenset(mode for mode in range(0o1000)
                                              if mode & 0o400 and not mode & 0o022),
                         copied_timestamps=True)
        self.journal = journal

    def identity(self, local_id):
        if type(local_id) is not str:
            raise RetentionError("retention_owner_invalid", "history backup identity")
        parts = local_id.split("/")
        if (len(parts) != 3 or not _TURN.fullmatch(parts[0]) or parts[0] in {".", ".."}
                or parts[1] != "blob" or not any(pattern.fullmatch(parts[2]) for pattern in (_BLOB, _PLAN, _RECEIPT))):
            raise RetentionError("retention_owner_invalid", "history backup identity")
        kind = "blob" if _BLOB.fullmatch(parts[2]) else "audit_segment"
        return ObjectIdentity(self.name, self.root.as_uri(), kind, local_id)

    def _parts(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError("retention_owner_invalid", "foreign history identity")
        return identity.local_id.split("/")

    def _read(self, directory, custody, turn, name):
        return self._read_file(directory, custody, name, self.identity(f"{turn}/blob/{name}"),
                               expected_digest="sha256:" + name[:-4] if _BLOB.fullmatch(name) else None,
                               include_payload=not bool(_BLOB.fullmatch(name)),
                               **({"max_bytes": 8 * 1024 * 1024} if not _BLOB.fullmatch(name) else {}))

    def _files(self):
        files, documents, deadline = {}, {}, time.monotonic() + 15
        document_bytes = 0
        try:
            with self._directory() as (root, _):
                turns = self._names(root)
                for turn in turns:
                    if not _TURN.fullmatch(turn) or turn in {".", ".."}:
                        raise RetentionError("retention_inventory_incomplete", "unknown history namespace")
                    with self._directory(turn) as (container, _):
                        entries = self._names(container)
                        if entries not in {(), ("blob",)}:
                            raise RetentionError("retention_inventory_incomplete", "unknown history container")
                        if not entries:
                            continue
                        with self._directory(turn, "blob") as (directory, custody):
                            names = self._names(directory)
                            for name in names:
                                if not any(pattern.fullmatch(name) for pattern in (_BLOB, _PLAN, _RECEIPT)):
                                    raise RetentionError("retention_inventory_incomplete", "unreconciled history artifact")
                                if len(files) >= 100_000 or time.monotonic() > deadline:
                                    raise RetentionError("retention_inventory_incomplete", "history inventory budget")
                                observed = self._read(directory, custody, turn, name)
                                if observed is None:
                                    raise RetentionError("retention_owner_changed", "history backup disappeared")
                                if not _BLOB.fullmatch(name):
                                    document_bytes += len(observed[4])
                                    if document_bytes > _MAX_DOCUMENT_BYTES:
                                        raise RetentionError("retention_inventory_incomplete", "history document byte budget")
                                    documents[observed[0]] = _document(name, observed[4])
                                files[observed[0]] = OwnerObject(observed[0], observed[1], NodeState.CLOSED,
                                    _iso(observed[3]), _iso(observed[3] + timedelta(days=90)))
                            if names != self._names(directory):
                                raise RetentionError("retention_owner_changed", "history files changed")
                        if entries != self._names(container):
                            raise RetentionError("retention_owner_changed", "history container changed")
                if turns != self._names(root):
                    raise RetentionError("retention_owner_changed", "history namespace changed")
        except FileNotFoundError:
            # Only a missing root is an empty native archive. A missing child
            # of the enumerated root is a changed snapshot, never partial data.
            if self.root.exists() or files:
                raise RetentionError("retention_owner_changed", "history directory disappeared") from None
        if time.monotonic() > deadline:
            raise RetentionError("retention_inventory_incomplete", "history inventory budget")
        # No full recovery validator exists here: retain the entire native turn.
        # This also preserves backups produced before a journaled action settles.
        turns = {identity.local_id.split('/')[0] for identity in documents}
        by_turn, paths, plans = {}, {}, {}
        for identity in files:
            by_turn.setdefault(identity.local_id.split("/")[0], set()).add(identity)
            paths[str(self.root / identity.local_id)] = identity
        for identity, value in documents.items():
            if _PLAN.fullmatch(Path(identity.local_id).name):
                plans[(identity.local_id.split("/")[0], value["token"])] = value
        reference_count = 0
        for identity, obj in tuple(files.items()):
            if identity.local_id.split('/')[0] in turns:
                refs = set()
                if identity in documents:
                    value = documents[identity]
                    refs.update(by_turn[identity.local_id.split('/')[0]] - {identity})
                    reference_count += len(refs)
                    if reference_count > 1_000_000 or time.monotonic() > deadline:
                        raise RetentionError('retention_inventory_incomplete', 'history reference budget')
                    plan = plans.get((identity.local_id.split('/')[0], value['token']))
                    if plan is not None and any(value.get(key) != plan.get(key)
                            for key in ('binding', 'policy', 'root_identities')):
                        raise RetentionError('retention_inventory_incomplete', 'historical documents contradict')
                    # Backup-ready claims cannot point to absent historical bytes.
                    for action in value['actions']:
                        raw_path = action.get('blob_path')
                        if type(raw_path) is str and raw_path in paths:
                            refs.add(paths[raw_path])
                        if action.get('backup_ready'):
                            path = action.get('blob_path')
                            digest = action.get('blob_sha256')
                            target = paths.get(path) if type(path) is str else None
                            if (target is None or target.node_type != 'blob'
                                    or Path(target.local_id).stem != digest):
                                raise RetentionError('retention_inventory_incomplete', 'historical backup absent')
                files[identity] = replace(obj, state=NodeState.OPEN, eligible_after=None,
                    roots=(RootKind.OPEN_AUDIT,), references=tuple(sorted(refs, key=repr)))
        # A newly appeared document must also protect cross-turn backups when
        # delete rechecks the owner after an earlier collection plan.
        linked = {target for identity in documents for target in files[identity].references}
        for identity in linked:
            files[identity] = replace(files[identity], state=NodeState.OPEN,
                eligible_after=None, roots=(RootKind.OPEN_AUDIT,))
        return files

    def reference_index(self, identities):
        identities = tuple(identities)
        paths = {str(self.root / identity.local_id): identity for identity in identities}
        digests = {}
        for identity in identities:
            if identity.node_type == "blob":
                digests.setdefault(Path(identity.local_id).stem, set()).add(identity)
        return paths, digests

    def references(self, records, index):
        paths, digests = index
        refs = set()
        for value in _values(records):
            if type(value) is str:
                if value in paths:
                    refs.add(paths[value])
                # Legacy recovery searches by digest across turn directories.
                refs.update(digests.get(value.removeprefix("sha256:"), ()))
            elif type(value) is dict:
                for field in ("blob_path", "prev_blob_path"):
                    raw = value.get(field)
                    if type(raw) is str and Path(raw).is_relative_to(self.root):
                        fallback = value.get("blob_sha256") or value.get("prev_blob_sha256")
                        fallback_present = (type(fallback) is str
                                            and fallback.removeprefix("sha256:") in digests)
                        if raw not in paths and not fallback_present:
                            raise RetentionError("retention_inventory_incomplete", "referenced history backup absent")
                for field in ("blob_sha256", "prev_blob_sha256"):
                    raw = value.get(field)
                    if raw and (type(raw) is not str or raw.removeprefix("sha256:") not in digests):
                        raise RetentionError("retention_inventory_incomplete", "referenced history digest absent")
        return refs

    def inventory(self):
        operations, files = self.journal.scan(), self._files()
        if files:
            try:
                with self.journal._parent() as (directory, _), self.journal._read(directory) as observed:
                    absent = observed[0] is None
            except FileNotFoundError:
                absent = True
            if absent:
                raise RetentionError("retention_inventory_incomplete", "history undo journal absent")
        index = self.reference_index(files)
        linked, objects = set(), []
        for operation in operations:
            refs = self.references(operation.records, index)
            # No effects yet means a crash may have occurred between backup and
            # receipt. Preserve the operation's entire native turn namespace.
            kinds = [item["type"] for item in operation.records]
            if operation.object.state is NodeState.OPEN and (
                    kinds == ["pending"] or len(set(kinds)) != len(kinds)
                    or any(item.get("reason") == "invalid_execution_receipt" for item in operation.records)):
                turns = {item.get("turn_id") for item in operation.records if item["type"] == "pending"}
                refs.update(identity for identity in files if identity.local_id.split("/")[0] in turns)
                refs.update(identity for identity in files if identity.local_id.startswith("no_turn/"))
            linked.update(refs)
            objects.append(replace(operation.object, references=tuple(sorted(
                set(operation.object.references) | refs, key=lambda item: item.key.node_id))))
        objects.extend(replace(obj, state=NodeState.OPEN, eligible_after=None,
                               roots=(RootKind.OPEN_AUDIT,)) if identity in linked else obj
                       for identity, obj in files.items())
        if operations != self.journal.scan() or files != self._files():
            raise RetentionError("retention_owner_changed", "history references changed")
        self.require_exclusion()
        return tuple(objects)

    def version(self, identity):
        turn, _, name = self._parts(identity)
        try:
            with self._directory(turn, "blob") as (directory, custody):
                observed = self._read(directory, custody, turn, name)
                if observed is None:
                    os.fsync(directory)
                return None if observed is None else observed[1]
        except FileNotFoundError:
            return None

    def delete(self, identity, expected_version):
        turn, _, name = self._parts(identity)
        obj = next((item for item in self.inventory() if item.identity == identity), None)
        if obj is not None and (obj.state is not NodeState.CLOSED or obj.roots
                or obj.eligible_after is None or _utc(obj.eligible_after) >= datetime.now(timezone.utc)):
            raise RetentionError("retention_owner_state_invalid", "history backup still needed")
        try:
            with self._directory(turn, "blob") as (directory, custody):
                observed = self._read(directory, custody, turn, name)
                if observed is not None:
                    if obj is None or obj.version != expected_version or observed[1] != expected_version:
                        raise RetentionError("retention_owner_changed", "history backup version")
                    self.require_exclusion()
                    os.unlink(name, dir_fd=directory)
                os.fsync(directory)
        except FileNotFoundError:
            # Containers are retained: they are native namespaces, not payload
            # garbage, and their removal would invalidate sibling custody.
            with self._directory() as (root, _):
                os.fsync(root)
