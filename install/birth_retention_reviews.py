"""Physical administrative review records, not evidence or approval authority.

Native digests and root custody preserve the review inputs. Independent signed
evidence, historical authority sets and approvals still require their own join;
an expired consent does not close this audit material.
"""
import base64
import json
import re
import time

from executor_birth_approval import ApprovalSubject
from executor_birth_canonical import _pairs_v1
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_jsonl import _invalid_constant
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_sqlite import _iso, _utc
from install.synth_review import _digest, _MAX_DOCUMENT, _PROPOSAL_FIELDS
from manifest_inventory import ContractId, ManifestOrigin

_FIELDS = frozenset('schema_version proposal human_cases tests subject token created_at name description capabilities execution'.split())
_NAME = re.compile(r'[0-9a-f]{64}\.json')
_MAX_TOTAL = 128 << 20
_MAX_FILES = 100_000
_SECONDS = 15


class _ReviewOwner(_PrivateFiles):
    name = 'synth_review_records'

    def __init__(self, *, root, require_exclusion, owner=(0, 0)):
        if owner not in ((0, 0), None):
            raise RetentionError('retention_owner_invalid', 'administrative review root custody')
        super().__init__(root=root, require_exclusion=require_exclusion, owner=owner)

    def identity(self, local_id):
        if type(local_id) is not str or not _NAME.fullmatch(local_id):
            raise RetentionError('retention_owner_invalid', 'administrative review identity')
        return ObjectIdentity(self.name, self.root.as_uri(), 'revision', local_id)

    def _check(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError('retention_owner_invalid', 'foreign administrative review')

    def _read(self, directory, custody, identity):
        raw = self._read_file(directory, custody, identity.local_id, identity,
                              include_payload=True, max_bytes=_MAX_DOCUMENT)
        if raw is None:
            return None
        try:
            record = json.loads(raw[4], object_pairs_hook=_pairs_v1,
                                parse_constant=_invalid_constant)
            if (type(record) is not dict or set(record) != _FIELDS
                    or type(record['schema_version']) is not int or record['schema_version'] != 1
                    or _digest(record) != 'sha256:' + identity.local_id[:-5]):
                raise ValueError('review schema or digest')
            proposal = record['proposal']
            if type(proposal) is not dict or set(proposal) != _PROPOSAL_FIELDS:
                raise ValueError('proposal schema')
            for field in ('contract_id', 'producer', 'reason'):
                if type(proposal[field]) is not str or not proposal[field].strip():
                    raise ValueError('proposal text')
            origin, relative = proposal['contract_id'].split(':', 1)
            contract = ContractId(ManifestOrigin(origin), relative)
            if contract.origin is not ManifestOrigin.USER or type(proposal['files']) is not dict:
                raise ValueError('proposal identity')
            files = proposal['files']
            if len(files) != 3 or not {'manifest.toml', 'manifest.lang_state.json'} <= set(files):
                raise ValueError('proposal files')
            for name, payload in files.items():
                if (type(name) is not str or type(payload) is not str
                        or (name not in {'manifest.toml', 'manifest.lang_state.json'}
                            and re.fullmatch(r'[a-z][a-z0-9_-]{0,119}\.py', name) is None)):
                    raise ValueError('proposal file shape')
                content = base64.b64decode(payload, validate=True)
                if not content or len(content) > 1024 * 1024:
                    raise ValueError('proposal file size')
            if type(record['subject']) is not dict:
                raise ValueError('review subject')
            ApprovalSubject(**record['subject'])  # Shape only: never revalidate expiry or consent.
            if (any(type(record[field]) is not list for field in ('human_cases', 'tests'))
                    or any(type(record[field]) is not dict for field in ('description', 'capabilities', 'execution'))
                    or any(type(record[field]) is not str or not record[field]
                           for field in ('name', 'token'))):
                raise ValueError('review fields')
            created = _utc(record['created_at'])
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            raise RetentionError('retention_owner_state_invalid', 'administrative review record') from exc
        obj = OwnerObject(identity, raw[1], NodeState.OPEN, _iso(created), None,
                          roots=(RootKind.OPEN_AUDIT,))
        return obj, record, len(raw[4])

    def scan(self):
        result, total, deadline = {}, 0, time.monotonic() + _SECONDS
        try:
            with self._directory() as (directory, custody):
                names = self._names(directory)
                if len(names) > _MAX_FILES:
                    raise RetentionError('retention_inventory_incomplete', 'review file budget')
                for name in names:
                    identity = self.identity(name)
                    entry = self._read(directory, custody, identity)
                    if entry is None:
                        raise RetentionError('retention_owner_changed', 'review disappeared')
                    result[identity] = entry
                    total += entry[2]
                    if total > _MAX_TOTAL or time.monotonic() > deadline:
                        raise RetentionError('retention_inventory_incomplete', 'review inventory budget')
                if names != self._names(directory):
                    raise RetentionError('retention_owner_changed', 'review namespace changed')
        except FileNotFoundError:
            if self.root.exists() or result:
                raise RetentionError('retention_owner_changed', 'review directory disappeared') from None
        return result

    def inventory(self):
        files = self.scan()
        if files != self.scan():
            raise RetentionError('retention_owner_changed', 'review inventory changed')
        return tuple(entry[0] for _, entry in sorted(files.items(), key=lambda item: item[0].key.node_id))

    def version(self, identity):
        self._check(identity)
        try:
            with self._directory() as (directory, custody):
                entry = self._read(directory, custody, identity)
                return None if entry is None else entry[0].version
        except FileNotFoundError:
            return None

    def delete(self, identity, expected_version):
        self._check(identity)
        raise RetentionError('retention_owner_state_invalid', 'review lacks native audit closure')
