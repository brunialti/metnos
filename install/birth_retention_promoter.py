"""Native reopenable promotions and their exact physical recovery references.

Neither finalization nor archival revokes native rollback/review rights. This
join therefore supplies roots, not a newly invented promoter expiration.
"""
from dataclasses import replace
import io
import json
import gzip
from pathlib import Path
import re
import tarfile
import time
import tomllib
from types import MappingProxyType

import contract_store
from executor_birth_canonical import _pairs_v1
from install.birth_retention_jsonl import _invalid_constant
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_sqlite import _SQLiteOwner, _retained, _iso
from manifest_inventory import ContractId, ManifestOrigin

_ID = re.compile(r'[A-Za-z0-9_.-]{1,240}')
_STATES = frozenset({'pending', 'promoted_grace', 'promoted_finalized',
                     'rolled_back', 'archived', 'review_needed'})


def _state(row):
    if row['state'] not in _STATES:
        raise RetentionError('retention_owner_state_invalid', 'promoter state')
    return _retained(row['created_at'], RootKind.OPEN_REVISION)


class _PromoterOwner(_SQLiteOwner):
    def __init__(self, *, path, require_exclusion, owner):
        from jobs.promoter_state import ensure_schema
        super().__init__(name='promoter', path=path, schema=ensure_schema,
            table='proposal_promote', primary_key='proposal_id', node_type='revision',
            state=_state, require_exclusion=require_exclusion, owner=owner)


class _PromoterBlobs(_PrivateFiles):
    name = 'promoter_rollback_blobs'

    def __init__(self, *, root, promoter):
        super().__init__(root=root, require_exclusion=promoter.require_exclusion,
            owner=promoter.owner, private_directory=False,
            file_modes=frozenset({0o600, 0o640, 0o644}), copied_timestamps=True)

    def identity(self, local_id):
        parts = local_id.split('/') if type(local_id) is str else []
        if (len(parts) not in (1, 2) or (len(parts) == 2 and parts[0] != '_rolled_back')
                or not parts[-1].endswith('.tar.gz')
                or not _ID.fullmatch(parts[-1][:-7]) or parts[-1][:-7] in {'.', '..'}):
            raise RetentionError('retention_owner_invalid', 'promoter blob identity')
        return ObjectIdentity(self.name, self.root.as_uri(), 'blob', local_id)

    def _parts(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError('retention_owner_invalid', 'foreign promoter blob')
        return identity.local_id.split('/')

    def _read(self, directory, custody, identity):
        parts = self._parts(identity)
        raw = self._read_file(directory, custody, parts[-1], identity,
                              include_payload=True, max_bytes=32 << 20)
        if raw is None:
            return None
        payloads, names, total, deadline = {}, set(), 0, time.monotonic() + 15
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw[4])) as compressed:
                expanded = compressed.read((64 << 20) + 1)
            if len(expanded) > 64 << 20:
                raise ValueError('expanded rollback archive budget')
            with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as archive:
                for member in archive:
                    name = member.name
                    if (Path(name).name != name or name in {'.', '..'} or not member.isfile()
                            or name in names or len(names) >= 1000 or member.size < 0):
                        raise ValueError('unsafe or duplicate rollback member')
                    names.add(name)
                    total += member.size
                    if total > 64 << 20 or time.monotonic() > deadline:
                        raise ValueError('rollback archive budget')
                    if name in contract_store.GENERATION_FILES:
                        stream = archive.extractfile(member)
                        payloads[name] = stream.read(member.size + 1)
                        if len(payloads[name]) != member.size:
                            raise ValueError('partial rollback member')
            if 'manifest.toml' not in names:
                raise ValueError('rollback manifest absent')
            generation = (contract_store.generation_id(payloads)
                if set(payloads) == set(contract_store.GENERATION_FILES) else None)
            contract = None
            if generation is not None:
                name = tomllib.loads(payloads['manifest.toml'].decode('utf-8'))['name']
                if type(name) is not str or not _ID.fullmatch(name) or name in {'.', '..'}:
                    raise ValueError('rollback contract name')
                contract = ContractId(ManifestOrigin.USER, f'{name}/manifest.toml')
        except (tarfile.TarError, OSError, ValueError, EOFError, KeyError, TypeError) as exc:
            raise RetentionError('retention_owner_state_invalid', 'promoter rollback archive') from exc
        obj = OwnerObject(identity, raw[1], NodeState.OPEN, _iso(raw[3]), None,
                          roots=(RootKind.OPEN_AUDIT,))
        return obj, generation, len(raw[4]), contract

    def scan(self):
        files, total, deadline = {}, 0, time.monotonic() + 15
        def walk(parts):
            nonlocal total
            with self._directory(*parts) as (directory, custody):
                names = self._names(directory)
                for name in names:
                    if not parts and name == '_rolled_back':
                        walk((name,))
                    else:
                        identity = self.identity('/'.join((*parts, name)))
                        entry = self._read(directory, custody, identity)
                        if entry is None:
                            raise RetentionError('retention_owner_changed', 'promoter blob disappeared')
                        files[identity] = entry
                        total += entry[2]
                    if len(files) > 100_000 or total > 128 << 20 or time.monotonic() > deadline:
                        raise RetentionError('retention_inventory_incomplete', 'promoter blob budget')
                if names != self._names(directory):
                    raise RetentionError('retention_owner_changed', 'promoter blob namespace changed')
        try:
            walk(())
        except FileNotFoundError:
            if self.root.exists() or files:
                raise RetentionError('retention_owner_changed', 'promoter blob directory disappeared') from None
        return files

    def version(self, identity):
        parts = self._parts(identity)
        try:
            with self._directory(*parts[:-1]) as (directory, custody):
                entry = self._read(directory, custody, identity)
                return None if entry is None else entry[0].version
        except FileNotFoundError:
            return None

    def delete(self, identity, expected_version):
        self._parts(identity)
        raise RetentionError('retention_owner_state_invalid', 'promoter rollback lacks native closure')


class _PromoterInventory:
    def __init__(self, *, promoter, blobs, synth, signed_store, reviews):
        self.promoter, self.blobs, self.synth, self.signed_store = promoter, blobs, synth, signed_store
        self.reviews = reviews
        owners = dict(synth.owners)
        for owner in (promoter, blobs, signed_store, reviews):
            if owner.name in owners:
                raise RetentionError('retention_owner_invalid', 'duplicate promoter owner')
            owners[owner.name] = owner
        self.owners = MappingProxyType(owners)

    def inventory(self):
        rows, files = self.promoter.scan(), self.blobs.scan()
        sources, signed = self.synth.inventory(), self.signed_store.inventory()
        reviews = self.reviews.scan()
        objects = {obj.identity: obj for obj in (*sources, *signed)}
        for entry in (*files.values(), *reviews.values()):
            objects[entry[0].identity] = entry[0]
        source_ids, blob_ids = {}, {}
        for obj in sources:
            if obj.identity.owner in getattr(self.synth, 'proposal_owner_names', {self.synth.name}):
                source_ids.setdefault(Path(obj.identity.local_id).stem, set()).add(obj.identity)
        for identity in files:
            blob_ids.setdefault(Path(identity.local_id).name[:-7], set()).add(identity)
        refs = {identity: set(obj.references) for identity, obj in objects.items()}
        for identity, entry in files.items():
            proposal_id = Path(identity.local_id).name[:-7]
            refs[identity].update(source_ids.get(proposal_id, ()))
            if re.fullmatch(r'[0-9a-f]{64}', proposal_id):
                review = self.reviews.identity(proposal_id + '.json')
                if review in reviews:
                    refs[identity].add(review)
            if entry[1] is not None:
                target = self.signed_store.generation_identity(entry[3], entry[1])
                if target not in objects:
                    raise RetentionError('retention_inventory_incomplete', 'promoter blob exact generation absent')
                refs[identity].add(target)
        for row in rows:
            data = row.values
            state = self.promoter.state_of(row)
            objects[row.identity] = OwnerObject(row.identity, row.version, state.state,
                state.created_at, state.eligible_after, roots=state.roots)
            targets = source_ids.get(data['proposal_id'], set())
            try:
                verdict = json.loads(data['evaluator_verdict'] or '{}',
                    object_pairs_hook=_pairs_v1, parse_constant=_invalid_constant)
                if type(verdict) is not dict:
                    raise ValueError('promoter verdict')
            except (ValueError, TypeError, RecursionError) as exc:
                raise RetentionError('retention_owner_state_invalid', 'promoter verdict') from exc
            if verdict.get('source') == 'operator_review':
                review_id = verdict.get('review_id')
                if (type(review_id) is not str or re.fullmatch(r'sha256:[0-9a-f]{64}', review_id) is None
                        or review_id[7:] != data['proposal_id']):
                    raise RetentionError('retention_owner_state_invalid', 'promoter review identity')
                identity = self.reviews.identity(review_id[7:] + '.json')
                entry = reviews.get(identity)
                contract_value = ContractId(ManifestOrigin.USER, f"{data['name']}/manifest.toml").value
                if (entry is None or entry[1]['proposal']['contract_id'] != contract_value
                        or entry[1]['proposal']['producer'] != 'promoter'):
                    raise RetentionError('retention_inventory_incomplete', 'promoter operator review absent or mismatched')
                targets = targets | {identity}
            elif not targets:
                raise RetentionError('retention_inventory_incomplete', 'promoter source proposal absent')
            refs[row.identity] = set(targets)
            copies = blob_ids.get(data['proposal_id'], set())
            refs[row.identity].update(copies)
            for target in copies:
                refs[target].add(row.identity)
            raw_path = data['rollback_blob_path']
            if raw_path:
                expected = self.blobs.root / (data['proposal_id'] + '.tar.gz')
                if raw_path != str(expected) or not copies:
                    raise RetentionError('retention_inventory_incomplete', 'promoter rollback path unresolved')
            for field in ('prepromotion_generation_id', 'active_generation_id'):
                generation = data[field]
                if not generation:
                    continue  # Historical authoring promotions predate store IDs.
                try:
                    name = data['name']
                    if not _ID.fullmatch(name) or name in {'.', '..'}:
                        raise ValueError('promoter contract name')
                    contract = ContractId(ManifestOrigin.USER, f'{name}/manifest.toml')
                    target = self.signed_store.generation_identity(contract, generation)
                except (ValueError, TypeError) as exc:
                    raise RetentionError('retention_owner_state_invalid', 'promoter contract identity') from exc
                if target not in objects:
                    raise RetentionError('retention_inventory_incomplete', 'promoter exact generation absent')
                refs[row.identity].add(target)
                if field == 'prepromotion_generation_id':
                    # A resurrected promotion can leave a different old copy.
                    # Only the current recovery blob (or its moved successor)
                    # binds the current row's prepromotion generation.
                    current = self.blobs.identity(data['proposal_id'] + '.tar.gz')
                    moved = self.blobs.identity('_rolled_back/' + data['proposal_id'] + '.tar.gz')
                    recovery = current if current in files else moved
                    if (recovery not in files or files[recovery][1] != generation
                            or files[recovery][3] != contract):
                        raise RetentionError('retention_owner_state_invalid', 'promoter rollback generation mismatch')
        # A blob without a row can precede a failed state commit; preserve it as
        # recovery evidence instead of inventing closure from absence or age.
        if (rows != self.promoter.scan() or files != self.blobs.scan()
                or sources != self.synth.inventory() or signed != self.signed_store.inventory()
                or reviews != self.reviews.scan()):
            raise RetentionError('retention_owner_changed', 'promoter inventory changed')
        return tuple(replace(obj, references=tuple(sorted(refs[identity], key=lambda x: x.key.node_id)))
            for identity, obj in sorted(objects.items(), key=lambda x: x[0].key.node_id))
