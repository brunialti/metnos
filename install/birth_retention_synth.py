"""Native synthesis records: failed runs close, synthesis is not promotion.

Change-intent witnesses outlive their source files, preventing discovery from
recreating a collected row between bounded maintenance windows. This subset
still requires the installation's external marker/Birth reference join.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import time
from types import MappingProxyType

from executor_birth_canonical import _pairs_v1
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_jsonl import _invalid_constant, _time
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_sqlite import _iso, _utc

_FIELDS = frozenset('id expected_name intent user_query ts_start elapsed_s final_state name abandon_reason path_hash path_steps path_n_steps path_eta_p50_ms path_eta_p95_ms path_call_count_60d stages'.split())
_FAILED = frozenset({'abandoned', 'rejected', 'rejected_lint_structural',
    'rejected_lint_unavailable', 'rejected_semantic_input_invalid',
    'rejected_semantic_drift', 'rejected_semantic_invalid', 'rejected_semantic_unavailable'})


class _SynthProposalOwner(_PrivateFiles):
    name = 'synth_proposal_files'

    def __init__(self, *, root, intents):
        if intents.name != 'change_intents':
            raise RetentionError('retention_owner_invalid', 'synthesis intent owner')
        super().__init__(root=root, require_exclusion=intents.require_exclusion,
            owner=intents.owner, private_directory=False,
            file_modes=frozenset({0o600, 0o640, 0o644}), copied_timestamps=True)
        self.intents = intents

    def identity(self, local_id):
        parts = local_id.split('/') if type(local_id) is str else []
        if (len(parts) not in (1, 2) or (len(parts) == 2 and parts[0] != '_archived')
                or not parts[-1].endswith('.json') or parts[-1] in {'.json', '..json'}
                or any(Path(p).name != p or p in {'.', '..'} for p in parts)):
            raise RetentionError('retention_owner_invalid', 'synthesis file identity')
        return ObjectIdentity(self.name, self.root.as_uri(), 'proposal', local_id)

    def _parts(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError('retention_owner_invalid', 'foreign synthesis file')
        return identity.local_id.split('/')

    def _read(self, directory, custody, identity):
        parts = self._parts(identity)
        raw = self._read_file(directory, custody, parts[-1], identity,
                              include_payload=True, max_bytes=8 << 20)
        if raw is None:
            return None
        try:
            data = json.loads(raw[4], object_pairs_hook=_pairs_v1, parse_constant=_invalid_constant)
            if (type(data) is not dict or not set(data) <= _FIELDS
                    or data.get('id') != parts[-1][:-5]):
                raise ValueError('synthesis schema or identity')
            start = _time(data.get('ts_start'))
            elapsed = data.get('elapsed_s')
            if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
                raise ValueError('synthesis duration')
            state = data.get('final_state')
            if state not in _FAILED | {'installed', 'in_progress', 'synthesized', ''}:
                raise ValueError('synthesis state')
            closed = state in _FAILED
            end = max(start + timedelta(seconds=elapsed), raw[3])
            obj = OwnerObject(identity, raw[1], NodeState.CLOSED if closed else NodeState.OPEN,
                _iso(start), _iso(end + timedelta(days=90)) if closed else None,
                roots=() if closed else (RootKind.OPEN_REVISION,))
            return obj, data, len(raw[4])
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise RetentionError('retention_owner_state_invalid', 'synthesis payload') from exc

    def scan(self):
        result, total, deadline = {}, 0, time.monotonic() + 15
        def walk(parts):
            nonlocal total
            with self._directory(*parts) as (directory, custody):
                names = self._names(directory)
                for name in names:
                    if not parts and name == '_archived':
                        walk((name,))
                    else:
                        identity = self.identity('/'.join((*parts, name)))
                        entry = self._read(directory, custody, identity)
                        if entry is None:
                            raise RetentionError('retention_owner_changed', 'synthesis file disappeared')
                        result[identity] = entry
                        total += entry[2]
                    if total > 128 << 20 or len(result) > 100_000 or time.monotonic() > deadline:
                        raise RetentionError('retention_inventory_incomplete', 'synthesis inventory budget')
                if self._names(directory) != names:
                    raise RetentionError('retention_owner_changed', 'synthesis directory changed')
        try:
            walk(())
        except FileNotFoundError:
            if self.root.exists() or result:
                raise RetentionError('retention_owner_changed', 'synthesis directory disappeared') from None
        return result

    @property
    def owners(self):
        return MappingProxyType({self.name: self, self.intents.name: self.intents})

    def inventory(self):
        files, rows = self.scan(), self.intents.scan()
        objects = self._project(files, rows)
        if files != self.scan() or rows != self.intents.scan():
            raise RetentionError('retention_owner_changed', 'synthesis references changed')
        return objects

    def _project(self, files, rows):
        objects = {identity: entry[0] for identity, entry in files.items()}
        copies = {}
        for identity, (_, data, _) in files.items():
            copies.setdefault(data['id'], set()).add(identity)
        refs = {identity: set() for identity in files}
        for ids in copies.values():
            hub = min(ids, key=lambda item: item.key.node_id)
            for identity in ids - {hub}:
                refs[hub].add(identity)
                refs[identity].add(hub)
        for row in rows:
            state = self.intents.state_of(row)
            obj = OwnerObject(row.identity, row.version, state.state, state.created_at,
                              state.eligible_after, roots=state.roots)
            refs[row.identity] = set()
            if row.values['origin_family'] == 'synt':
                targets = copies.get(row.values['origin_source_id'], set())
                if targets:
                    if state.state is NodeState.OPEN:
                        refs[row.identity].update(targets)
                    else:
                        # The file may regenerate this closed native row. Keep
                        # the witness until a later inventory sees no copies.
                        obj = replace(obj, state=NodeState.OPEN, eligible_after=None,
                                      roots=(RootKind.OPEN_AUDIT,))
                    for target in targets:
                        refs[target].add(row.identity)
            objects[row.identity] = obj
        return tuple(replace(obj, references=tuple(sorted(refs[identity], key=lambda x: x.key.node_id)))
                     for identity, obj in sorted(objects.items(), key=lambda x: x[0].key.node_id))

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
        try:
            with self._directory(*parts[:-1]) as (directory, custody):
                entry = self._read(directory, custody, identity)
                if entry is not None:
                    obj = entry[0]
                    if obj.version != expected_version:
                        raise RetentionError('retention_owner_changed', 'synthesis version')
                    if obj.state is not NodeState.CLOSED or _utc(obj.eligible_after) >= datetime.now(timezone.utc):
                        raise RetentionError('retention_owner_state_invalid', 'synthesis not closed or expired')
                    self.require_exclusion()
                    os.unlink(parts[-1], dir_fd=directory)
                os.fsync(directory)
        except FileNotFoundError:
            with self._directory() as (directory, _):
                os.fsync(directory)
