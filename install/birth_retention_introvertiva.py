"""Native introvertiva snapshots, including the historical archive layout.

The reader's lexical last-two window is functional state, not an age rule.
Records remain available to the installation's external-reference join.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import time
from types import MappingProxyType

from executor_birth_canonical import _pairs_v1
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_jsonl import _invalid_constant
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_sqlite import _iso, _utc

_NAME = re.compile(r'candidates_(dedupe|generalize|specialize)_([0-9]{1,20})(?:_([0-9a-f]{32}))?\.jsonl')


class _IntrovertivaOwner(_PrivateFiles):
    name = 'introvertiva_snapshots'

    def __init__(self, *, root, require_exclusion, owner):
        super().__init__(root=root, require_exclusion=require_exclusion, owner=owner,
            private_directory=False, file_modes=frozenset({0o600, 0o640, 0o644}),
            copied_timestamps=True)
        self.owners = MappingProxyType({self.name: self})

    def identity(self, local_id):
        parts = local_id.split('/') if type(local_id) is str else []
        if (len(parts) not in (1, 4) or not _NAME.fullmatch(parts[-1])
                or (len(parts) == 4 and (parts[0] != '_archived'
                    or not re.fullmatch(r'[0-9]{4}', parts[1])
                    or parts[2] not in {f'{n:02d}' for n in range(1, 13)}))):
            raise RetentionError('retention_owner_invalid', 'introvertiva snapshot identity')
        return ObjectIdentity(self.name, self.root.as_uri(), 'audit_segment', local_id)

    def _parts(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError('retention_owner_invalid', 'foreign introvertiva snapshot')
        return identity.local_id.split('/')

    def _read(self, directory, custody, identity):
        parts = self._parts(identity)
        raw = self._read_file(directory, custody, parts[-1], identity,
                              include_payload=True, max_bytes=8 << 20)
        if raw is None:
            return None
        try:
            match = _NAME.fullmatch(parts[-1])
            timestamp = int(match[2]) / (1_000_000_000 if match[3] else 1)
            created = datetime.fromtimestamp(timestamp, timezone.utc)
            if raw[4] and not raw[4].endswith(b'\n'):
                raise ValueError('partial snapshot')
            records = []
            for line in raw[4].splitlines():
                if not line.strip():
                    continue
                row = json.loads(line, object_pairs_hook=_pairs_v1, parse_constant=_invalid_constant)
                if type(row) is not dict:
                    raise ValueError('snapshot record')
                records.append(row)
                if len(records) > 100_000:
                    raise ValueError('snapshot record budget')
            obj = OwnerObject(identity, raw[1], NodeState.CLOSED, _iso(created),
                _iso(max(created, raw[3]) + timedelta(days=90)))
            return obj, tuple(records), len(raw[4])
        except (ValueError, TypeError, OverflowError, OSError, RecursionError) as exc:
            raise RetentionError('retention_owner_state_invalid', 'introvertiva snapshot payload') from exc

    def scan(self):
        result, total, count, deadline = {}, 0, 0, time.monotonic() + 15
        def walk(parts):
            nonlocal total, count
            with self._directory(*parts) as (directory, custody):
                names = self._names(directory)
                for name in names:
                    count += 1
                    if count > 100_000 or time.monotonic() > deadline:
                        raise RetentionError('retention_inventory_incomplete', 'introvertiva inventory budget')
                    if ((not parts and name == '_archived')
                            or (parts == ('_archived',) and re.fullmatch(r'[0-9]{4}', name))
                            or (len(parts) == 2 and parts[0] == '_archived'
                                and name in {f'{n:02d}' for n in range(1, 13)})):
                        walk((*parts, name))
                    else:
                        identity = self.identity('/'.join((*parts, name)))
                        entry = self._read(directory, custody, identity)
                        if entry is None:
                            raise RetentionError('retention_owner_changed', 'introvertiva snapshot disappeared')
                        result[identity] = entry
                        total += entry[2]
                        if total > 128 << 20:
                            raise RetentionError('retention_inventory_incomplete', 'introvertiva inventory bytes')
                if self._names(directory) != names:
                    raise RetentionError('retention_owner_changed', 'introvertiva namespace changed')
        try:
            walk(())
        except FileNotFoundError:
            if self.root.exists() or result:
                raise RetentionError('retention_owner_changed', 'introvertiva directory disappeared') from None
        if time.monotonic() > deadline:
            raise RetentionError('retention_inventory_incomplete', 'introvertiva inventory deadline')
        return result

    def _project(self, files):
        current, copies = {}, {}
        for identity in files:
            parts = self._parts(identity)
            copies.setdefault(parts[-1], set()).add(identity)
            if len(parts) == 1:
                current.setdefault(_NAME.fullmatch(parts[-1])[1], []).append(identity)
        roots = {identity for group in current.values()
                 for identity in sorted(group, key=lambda x: x.local_id)[-2:]}
        refs = {identity: set() for identity in files}
        for group in copies.values():
            hub = min(group, key=lambda x: x.key.node_id)
            for identity in group - {hub}:
                refs[hub].add(identity)
                refs[identity].add(hub)
        return tuple(replace(entry[0],
            state=NodeState.OPEN if identity in roots else NodeState.CLOSED,
            eligible_after=None if identity in roots else entry[0].eligible_after,
            roots=(RootKind.OPEN_AUDIT,) if identity in roots else (),
            references=tuple(sorted(refs[identity], key=lambda x: x.key.node_id)))
            for identity, entry in sorted(files.items(), key=lambda x: x[0].key.node_id))

    def inventory(self):
        files = self.scan()
        objects = self._project(files)
        if files != self.scan():
            raise RetentionError('retention_owner_changed', 'introvertiva inventory changed')
        return objects

    def version(self, identity):
        parts = self._parts(identity)
        try:
            with self._directory(*parts[:-1]) as (directory, custody):
                entry = self._read(directory, custody, identity)
                if entry is None:
                    os.fsync(directory)
                return None if entry is None else entry[0].version
        except FileNotFoundError:
            return None

    def delete(self, identity, expected_version):
        parts = self._parts(identity)
        objects = {obj.identity: obj for obj in self.inventory()}
        obj = objects.get(identity)
        if obj is not None:
            if obj.version != expected_version:
                raise RetentionError('retention_owner_changed', 'introvertiva snapshot version')
            if obj.state is not NodeState.CLOSED or _utc(obj.eligible_after) >= datetime.now(timezone.utc):
                raise RetentionError('retention_owner_state_invalid', 'introvertiva retained snapshot')
        try:
            with self._directory(*parts[:-1]) as (directory, custody):
                observed = self._read(directory, custody, identity)
                if observed is not None:
                    if obj is None or observed[0].version != expected_version:
                        raise RetentionError('retention_owner_changed', 'introvertiva changed before unlink')
                    self.require_exclusion()
                    os.unlink(parts[-1], dir_fd=directory)
                os.fsync(directory)
        except FileNotFoundError:
            with self._directory() as (directory, _):
                os.fsync(directory)
