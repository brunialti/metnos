"""Physical evidence in explicitly selected historical sets, never fresh review.

File timestamps describe custody observations, not an evidence issuance date.
The caller supplies the census of contexts; this component does not attest that
all historical contexts of the installation have been included.
"""
from dataclasses import replace
from pathlib import Path
import re
import time
from types import MappingProxyType

from executor_birth_retention import NodeState, RetentionError, RootKind
from executor_birth_semantic_authority import (
    _load_semantic_authority_in_session, _MAX_EVIDENCE_BYTES, _MAX_EVIDENCE_FILES,
)
from executor_birth_semantic_review import IndependentEvidenceKind, EvidenceStatus, SemanticReviewError
from executor_birth_secure_fs import _BirthObjectRole
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_sqlite import _iso


class _SemanticEvidenceOwner(_PrivateFiles):
    def __init__(self, *, root, set_id, owner, require_exclusion):
        self.name = 'birth_semantic_evidence.' + set_id
        super().__init__(root=root, owner=owner, require_exclusion=require_exclusion,
                         private_directory=False, file_modes=frozenset({0o644}))

    def identity(self, name):
        if type(name) is not str or re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,234}\.json', name) is None:
            raise RetentionError('retention_inventory_incomplete', 'unknown semantic evidence entry')
        return ObjectIdentity(self.name, self.root.as_uri(), 'evidence', name)

    def scan(self):
        result, total, deadline = {}, 0, time.monotonic() + 15
        with self._directory() as (directory, custody):
            names = self._names(directory)
            if len(names) > _MAX_EVIDENCE_FILES:
                raise RetentionError('retention_inventory_incomplete', 'semantic evidence count')
            for name in names:
                identity = self.identity(name)
                raw = self._read_file(directory, custody, name, identity,
                    include_payload=True, max_bytes=_MAX_EVIDENCE_BYTES)
                if raw is None:
                    raise RetentionError('retention_owner_changed', 'semantic evidence disappeared')
                total += len(raw[4])
                if total > 128 << 20 or time.monotonic() > deadline:
                    raise RetentionError('retention_inventory_incomplete', 'semantic evidence budget')
                result[identity] = (OwnerObject(identity, raw[1], NodeState.OPEN, _iso(raw[3]), None,
                    roots=(RootKind.OPEN_AUDIT,)), raw[4])
            if names != self._names(directory):
                raise RetentionError('retention_owner_changed', 'semantic evidence namespace')
        return result

    def version(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError('retention_owner_invalid', 'foreign semantic evidence')
        entry = self.scan().get(identity)
        return None if entry is None else entry[0].version

    def inventory(self):
        first = self.scan()
        if first != self.scan():
            raise RetentionError('retention_owner_changed', 'semantic evidence inventory')
        return tuple(entry[0] for entry in first.values())

    def delete(self, identity, expected_version):
        raise RetentionError('retention_owner_invalid', 'semantic evidence lacks native closure')


class _SemanticEvidenceInventory:
    def __init__(self, *, reviews, birth_root, contexts, session, owner, require_exclusion):
        from executor_birth_prepared_root import load_historical_context_verifiers_in_session_v1
        self.reviews, self.session = reviews, session
        self.require_exclusion = require_exclusion
        require_exclusion()
        session._require_exclusive_global_lock()
        # Bind physical metadata to the very root whose capability authenticates
        # the bytes; an identical copy at another location is not this object.
        if Path(birth_root) != Path(session._root_path):
            raise RetentionError('retention_owner_invalid', 'semantic evidence root binding')
        self.contexts = tuple(sorted(set(contexts)))
        self.bindings = MappingProxyType({context: load_historical_context_verifiers_in_session_v1(context, session)
                                         for context in self.contexts})
        evidence = {}
        for binding in self.bindings.values():
            set_id = binding.public_set.set_id
            if set_id not in evidence:
                evidence[set_id] = _SemanticEvidenceOwner(root=Path(birth_root) / 'authority-sets' / set_id / 'semantic' / 'evidence',
                    set_id=set_id, owner=owner, require_exclusion=require_exclusion)
        self.evidence = MappingProxyType(evidence)
        self.owners = MappingProxyType({item.name: item for item in (reviews, *evidence.values())})

    def scan(self):
        from executor_birth_prepared_root import load_historical_context_verifiers_in_session_v1, PreparedRootError
        from executor_birth_prepared_set import PreparedSetError
        from executor_birth_ownership_chain import OwnershipChainError
        from executor_birth_secure_fs import BirthSecureFSError
        self.require_exclusion()
        self.session._require_exclusive_global_lock()
        reviews = self.reviews.scan()
        objects = {identity: item[0] for identity, item in reviews.items()}
        verified, physical = {}, {}
        deadline, total = time.monotonic() + 15, 0
        try:
            if any(entry[1]['subject']['admission_context_id'] not in self.bindings for entry in reviews.values()):
                raise ValueError('review context absent from supplied census')
            for context, binding in self.bindings.items():
                current = load_historical_context_verifiers_in_session_v1(context, self.session)
                if (current.required_head_id != binding.required_head_id
                        or current.transition_id != binding.transition_id
                        or current.public_set.set_id != binding.public_set.set_id):
                    raise ValueError('historical context changed')
            for set_id, owner in self.evidence.items():
                base = ('authority-sets', set_id, 'semantic')
                authority = _load_semantic_authority_in_session(base + ('authority.json',),
                    base + ('public',), base + ('evidence',), self.session)
                entries = physical[set_id] = owner.scan()
                names = authority.evidence_dir.inventory()
                if set(names) != {identity.local_id for identity in entries}:
                    raise ValueError('physical evidence namespace mismatch')
                for identity, (obj, raw) in entries.items():
                    total += len(raw)
                    if total > 128 << 20 or len(verified) >= 100_000 or time.monotonic() > deadline:
                        raise ValueError('aggregate historical evidence budget')
                    native = authority.evidence_dir.read_file(identity.local_id,
                        maximum=_MAX_EVIDENCE_BYTES, role=_BirthObjectRole.birth_integrity_only)
                    if native != raw:
                        raise ValueError('physical evidence bytes mismatch')
                    item = authority._decode_record(raw, identity.local_id)
                    binding = self.bindings.get(item.admission_context_id)
                    if binding is None or binding.public_set.set_id != set_id:
                        raise ValueError('evidence context does not select physical set')
                    objects[identity] = obj
                    # The administrative producer emits this exact source shape;
                    # other native evidence remains authenticated and open.
                    matching_review = reviews.get(self.reviews.identity(item.evidence_id[7:] + '.json'))
                    if matching_review is not None and item.evidence_hash != item.evidence_id:
                        raise ValueError('administrative evidence hash mismatch')
                    administrative = (item.kind is IndependentEvidenceKind.HUMAN_CASE
                        and item.owner_id == 'independent-owner' and item.status is EvidenceStatus.PASSED
                        and item.evidence_hash == item.evidence_id)
                    if administrative:
                        if item.evidence_id != 'sha256:' + identity.local_id[:-5]:
                            raise ValueError('administrative evidence filename mismatch')
                        review_id = self.reviews.identity(identity.local_id)
                        record = reviews.get(review_id)
                        if record is None or any(record[1]['subject'][field] != getattr(item, field)
                                                 for field in ('candidate_id', 'admission_context_id')):
                            raise ValueError('independent evidence review absent or mismatched')
                        objects[identity] = replace(obj, references=(review_id,))
                        review_obj = objects[review_id]
                        objects[review_id] = replace(review_obj, references=tuple(sorted(
                            set(review_obj.references) | {identity}, key=repr)))
                    verified[identity] = item
                if set(names) != set(authority.evidence_dir.inventory()):
                    raise ValueError('native evidence namespace changed')
            # review() persists its administrative record before publishing
            # evidence: interruption in between is legitimate open audit work.
            if reviews != self.reviews.scan() or any(physical[key] != owner.scan() for key, owner in self.evidence.items()):
                raise RetentionError('retention_owner_changed', 'semantic evidence inventory changed')
        except (ValueError, TypeError, KeyError, PreparedRootError, PreparedSetError,
                OwnershipChainError, BirthSecureFSError, SemanticReviewError) as exc:
            raise RetentionError('retention_inventory_incomplete', 'historical independent evidence') from exc
        self.require_exclusion()
        self.session._require_exclusive_global_lock()
        return tuple(sorted(objects.values(), key=lambda obj: repr(obj.identity))), MappingProxyType(verified)

    def inventory(self):
        return self.scan()[0]
