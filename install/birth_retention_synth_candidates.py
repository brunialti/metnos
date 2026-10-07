"""Persistent Synth recovery inputs, distinct from ephemeral Birth snapshots.

Rejection and archival move bytes; neither establishes native audit closure.
The explicit roots cover proposals and its approved/rejected sibling stores.
"""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import re
import stat
import time
import tomllib
from types import SimpleNamespace

from executor_birth_canonical import _pairs_v1
from executor_birth_snapshot import CandidateSnapshotError, _declared_code_files, _expected_entries
from executor_birth_retention import NodeState, RetentionError, RootKind
from synth_proposal_store import _identity
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_jsonl import _invalid_constant
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject, _digest
from install.birth_retention_sqlite import _iso

_ADDRESS = re.compile(r'[0-9a-f]{64}')
_LEGACY = re.compile(r'[0-9a-f]{16}')
_FAILED = re.compile(r'_failed_[0-9a-f]{16}')
_PENDING = re.compile(r'\.pending-[a-z0-9_]{8}')
_META = frozenset('proposal_id name description stage birth_error producer contract_id reason'.split())


class _CandidateFiles(_PrivateFiles):
    @contextmanager
    def _directory(self, *parts):
        with super()._directory(*parts) as opened:
            # Snapshot copies are read-only. Legacy writers use the runtime
            # umask; public readability is not permission to mutate the tree.
            if stat.S_IMODE(os.fstat(opened[0]).st_mode) not in {0o500, 0o700, 0o755}:
                raise RetentionError('retention_owner_path_invalid', 'Synth directory mode')
            yield opened


class _PersistentSynthOwner:
    name = 'persistent_synth_candidates'

    def __init__(self, *, proposals, require_exclusion, owner):
        self.root, self.require_exclusion = proposals, require_exclusion
        self.stores = {label: _CandidateFiles(root=path, require_exclusion=require_exclusion,
            owner=owner, private_directory=False, file_modes=frozenset({0o400, 0o600, 0o644}),
            copied_timestamps=True) for label, path in (
                ('proposals', proposals), ('approved', proposals.parent / 'approved'),
                ('rejected', proposals.parent / 'rejected'))}
        if len({store.root for store in self.stores.values()}) != 3:
            raise RetentionError('retention_owner_invalid', 'Synth stores overlap')
        self.owners = {self.name: self}

    def identity(self, local_id):
        parts = local_id.split('/') if type(local_id) is str else []
        if (len(parts) != 2 or parts[0] not in self.stores or not (
                _ADDRESS.fullmatch(parts[1]) or _LEGACY.fullmatch(parts[1])
                or parts[0] == 'proposals' and (_FAILED.fullmatch(parts[1]) or _PENDING.fullmatch(parts[1])))):
            raise RetentionError('retention_owner_invalid', 'persistent Synth identity')
        return ObjectIdentity(self.name, self.root.as_uri(), 'revision', local_id)

    def _tree(self, store, name, identity, budget):
        payloads, versions, tree, timestamps = {}, {}, {}, []
        def walk(parts, prefix=''):
            if len(parts) > 32:
                raise RetentionError('retention_inventory_incomplete', 'Synth depth budget')
            with store._directory(*parts) as (directory, custody):
                names = store._names(directory)
                versions[prefix] = (custody, names)
                timestamps.append(os.fstat(directory).st_mtime)
                for name in names:
                    budget[0] += 1
                    if budget[0] > 100_000 or time.monotonic() > budget[2]:
                        raise RetentionError('retention_inventory_incomplete', 'Synth tree budget')
                    relative = prefix + name
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        tree[relative] = 'dir'
                        walk((*parts, name), relative + '/')
                    else:
                        tree[relative] = 'file'
                        raw = store._read_file(directory, custody, name, identity,
                            include_payload=True, max_bytes=8 << 20)
                        if raw is None:
                            raise RetentionError('retention_owner_changed', 'Synth file disappeared')
                        versions[relative] = raw[1]; payloads[relative] = raw[4]
                        timestamps.append(raw[3].timestamp()); budget[1] += raw[2]
                        if budget[1] > 128 << 20:
                            raise RetentionError('retention_inventory_incomplete', 'Synth byte budget')
                if names != store._names(directory):
                    raise RetentionError('retention_owner_changed', 'Synth namespace changed')
        walk((name,))
        return payloads, tree, _digest(versions), max(timestamps)

    @staticmethod
    def _validate(name, payloads, tree):
        def metadata():
            return json.loads(payloads['proposal.json'], object_pairs_hook=_pairs_v1,
                              parse_constant=_invalid_constant)
        if _FAILED.fullmatch(name):
            if not set(tree) <= {'raw_code.py', 'tool_args.json', 'fail_reason.txt'} or any(v != 'file' for v in tree.values()):
                raise ValueError('failed generator namespace')
            return None
        if _PENDING.fullmatch(name):
            # A crashed copy may be incomplete. Its native staging name is not
            # a candidate identity, and cannot establish any downstream edge.
            if any(key != 'proposal.json' and key != 'candidate' and not key.startswith('candidate/') for key in tree):
                raise ValueError('pending candidate namespace')
            return None
        if _ADDRESS.fullmatch(name):
            meta = metadata()
            if type(meta) is not dict or set(meta) != _META or meta['stage'] != 'birth_blocked':
                raise ValueError('retained candidate metadata')
            for key in ('producer', 'contract_id', 'reason', 'birth_error', 'name'):
                if type(meta[key]) is not str or not meta[key]:
                    raise ValueError('retained candidate text')
            manifest = payloads['candidate/manifest.toml']
            paths = _declared_code_files(manifest)
            expected = {'proposal.json': 'file', 'candidate': 'dir',
                        **{'candidate/' + key: value for key, value in _expected_entries(paths).items()}}
            if tree != expected:
                raise ValueError('retained candidate closed tree')
            snapshot = SimpleNamespace(manifest_bytes=manifest,
                language_state_bytes=payloads['candidate/manifest.lang_state.json'],
                code_files={key: payloads['candidate/' + key] for key in paths})
            address = _identity(snapshot, **{key: meta[key] for key in ('reason', 'contract_id', 'producer')})
            if address != name or meta['proposal_id'] != name or meta['name'] != tomllib.loads(manifest.decode())['name']:
                raise ValueError('retained candidate identity')
            return address
        # Legacy generation writes sequentially, including before/after tests.
        # A partial write remains an operational recovery input.
        allowed = {'manifest.toml', 'manifest.lang_state.json', 'args_schema.json', 'sandbox_profile.json', 'proposal.json'}
        if any(kind != 'file' or key not in allowed and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*\.py', key) is None for key, kind in tree.items()):
            raise ValueError('legacy proposal namespace')
        if 'proposal.json' in payloads:
            meta = metadata()
            if type(meta) is not dict or meta.get('proposal_id') != name or meta.get('stage') != 'generated':
                raise ValueError('legacy proposal metadata')
        return name

    def scan(self):
        result, budget = {}, [0, 0, time.monotonic() + 15]
        for label, store in self.stores.items():
            try:
                with store._directory() as (directory, custody):
                    names = store._names(directory)
                    for name in names:
                        budget[0] += 1
                        if budget[0] > 100_000 or time.monotonic() > budget[2]:
                            raise RetentionError('retention_inventory_incomplete', 'Synth store budget')
                        identity = self.identity(label + '/' + name)
                        payloads, tree, version, timestamp = self._tree(store, name, identity, budget)
                        try:
                            address = self._validate(name, payloads, tree)
                        except (ValueError, KeyError, TypeError, UnicodeError, CandidateSnapshotError) as exc:
                            raise RetentionError('retention_owner_state_invalid', 'persistent Synth payload') from exc
                        obj = OwnerObject(identity, version, NodeState.OPEN,
                            _iso(datetime.fromtimestamp(timestamp, timezone.utc)), None,
                            roots=(RootKind.OPEN_REVISION, RootKind.OPEN_AUDIT))
                        result[identity] = (obj, address)
                    if names != store._names(directory):
                        raise RetentionError('retention_owner_changed', 'Synth store changed')
            except FileNotFoundError:
                if store.root.exists() or any(identity.local_id.startswith(label + '/') for identity in result):
                    raise RetentionError('retention_owner_changed', 'Synth store disappeared') from None
        return result

    def inventory(self):
        rows = self.scan()
        objects, groups = [], {}
        for identity, (_, address) in rows.items():
            if address is not None:
                groups.setdefault(address, []).append(identity)
        # At most one copy per each of the three explicit native stores.
        for identity, (obj, address) in rows.items():
            refs = tuple(sorted((other for other in groups.get(address, ())
                if other != identity), key=lambda item: item.key.node_id))
            objects.append(replace(obj, references=refs))
        if rows != self.scan():
            raise RetentionError('retention_owner_changed', 'persistent Synth changed')
        self.require_exclusion()
        return tuple(sorted(objects, key=lambda obj: obj.identity.key.node_id))

    def version(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError('retention_owner_invalid', 'foreign persistent Synth')
        row = self.scan().get(identity)
        return None if row is None else row[0].version

    def delete(self, identity, expected_version):
        if identity != self.identity(identity.local_id):
            raise RetentionError('retention_owner_invalid', 'foreign persistent Synth')
        raise RetentionError('retention_owner_state_invalid', 'persistent Synth lacks native closure')
