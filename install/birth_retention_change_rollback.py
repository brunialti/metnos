"""Native extend_executor manifest backups and their exact change-intent links.

A backup is written before Birth and before mark_applied. Unreferenced backups
therefore remain recovery material; neither age nor a terminal intent closes
the producer's backup namespace.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import time
import tomllib
from types import MappingProxyType

from executor_birth_canonical import _pairs_v1
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_jsonl import _invalid_constant
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_sqlite import _iso


_BLOB_NAME = re.compile(r'([0-9a-f]{8})-([A-Za-z0-9_.-]{1,240})\.toml')


def _blob_name(name):
    match = _BLOB_NAME.fullmatch(name) if type(name) is str else None
    if match is None or match[2] in {'.', '..'}:
        raise RetentionError('retention_owner_invalid', 'change rollback filename')
    return match


class _ChangeRollbackBlobs(_PrivateFiles):
    name = 'change_rollback_blobs'

    def __init__(self, *, root, intents):
        super().__init__(root=root, require_exclusion=intents.require_exclusion,
            owner=intents.owner, private_directory=False,
            file_modes=frozenset({0o600, 0o640, 0o644}))
        self.intents = intents

    def identity(self, local_id):
        _blob_name(local_id)
        return ObjectIdentity(self.name, self.root.as_uri(), 'blob', local_id)

    def _identity(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError('retention_owner_invalid', 'foreign change rollback blob')

    def _read(self, directory, custody, identity):
        self._identity(identity)
        match = _blob_name(identity.local_id)
        raw = self._read_file(directory, custody, identity.local_id, identity,
                              include_payload=True, max_bytes=8 << 20)
        if raw is None:
            return None
        try:
            if hashlib.sha256(raw[4]).hexdigest()[:8] != match[1]:
                raise ValueError('change rollback content prefix')
            parsed = tomllib.loads(raw[4].decode('utf-8'))
            if type(parsed.get('args')) is not dict or type(parsed['args'].get('properties')) is not dict:
                raise ValueError('change rollback argument schema')
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise RetentionError('retention_owner_state_invalid', 'change rollback manifest') from exc
        obj = OwnerObject(identity, raw[1], NodeState.OPEN, _iso(raw[3]), None,
                          roots=(RootKind.OPEN_REVISION,))
        return obj, match[2], raw[2]

    def scan(self):
        files, total, deadline = {}, 0, time.monotonic() + 15
        try:
            with self._directory() as (directory, custody):
                names = self._names(directory)
                for name in names:
                    identity = self.identity(name)
                    entry = self._read(directory, custody, identity)
                    if entry is None:
                        raise RetentionError('retention_owner_changed', 'change rollback blob disappeared')
                    files[identity] = entry
                    total += entry[2]
                    if len(files) > 100_000 or total > 128 << 20 or time.monotonic() > deadline:
                        raise RetentionError('retention_inventory_incomplete', 'change rollback inventory budget')
                if names != self._names(directory):
                    raise RetentionError('retention_owner_changed', 'change rollback namespace changed')
        except FileNotFoundError:
            if self.root.exists() or files:
                raise RetentionError('retention_owner_changed', 'change rollback directory disappeared') from None
        return files

    def version(self, identity):
        self._identity(identity)
        try:
            with self._directory() as (directory, custody):
                entry = self._read(directory, custody, identity)
                return None if entry is None else entry[0].version
        except FileNotFoundError:
            return None

    def delete(self, identity, expected_version):
        self._identity(identity)
        raise RetentionError('retention_owner_state_invalid', 'change rollback lacks native closure')


class _ChangeRollbackInventory:
    def __init__(self, *, intents, blobs):
        if (intents.name != 'change_intents' or intents.table != 'change_intents'
                or intents.primary_keys != ('id',) or blobs.intents is not intents):
            raise RetentionError('retention_owner_invalid', 'change rollback native intent owner')
        self.intents, self.blobs = intents, blobs
        self.owners = MappingProxyType({intents.name: intents, blobs.name: blobs})

    def inventory(self):
        from change_intents import KIND_EXTEND_EXECUTOR

        rows, files = self.intents.scan(), self.blobs.scan()
        objects = {identity: entry[0] for identity, entry in files.items()}
        refs = {identity: set() for identity in files}
        for row in rows:
            state = self.intents.state_of(row)
            objects[row.identity] = OwnerObject(row.identity, row.version, state.state,
                state.created_at, state.eligible_after, roots=state.roots)
            refs[row.identity] = set()
            data = row.values
            try:
                effect = ({} if data['applied_effect'] is None else json.loads(data['applied_effect'],
                    object_pairs_hook=_pairs_v1, parse_constant=_invalid_constant))
                if type(effect) is not dict:
                    raise ValueError('change applied effect')
            except (ValueError, TypeError, RecursionError) as exc:
                raise RetentionError('retention_owner_state_invalid', 'change applied effect') from exc
            if 'rollback_blob_path' not in effect:
                continue
            raw_path = effect['rollback_blob_path']
            if (data['intent_kind'] != KIND_EXTEND_EXECUTOR or type(raw_path) is not str
                    or effect.get('executor_name') != data['intent_target']):
                raise RetentionError('retention_owner_state_invalid', 'change rollback effect binding')
            identity = self.blobs.identity(Path(raw_path).name)
            entry = files.get(identity)
            if (raw_path != str(self.blobs.root / identity.local_id) or entry is None
                    or entry[1] != data['intent_target']):
                raise RetentionError('retention_inventory_incomplete', 'change rollback path unresolved')
            refs[row.identity].add(identity)
            refs[identity].add(row.identity)
        if self.intents.scan() != rows or self.blobs.scan() != files:
            raise RetentionError('retention_owner_changed', 'change rollback inventory changed')
        self.intents.require_exclusion()
        return tuple(replace(obj, references=tuple(sorted(refs[identity],
            key=lambda target: target.key.node_id))) for identity, obj in objects.items())
