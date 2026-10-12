"""Historical review/approval links; no candidate requalification or fresh consent."""
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
import re
from types import MappingProxyType

from executor_birth_approval import ApprovalDecision, ApprovalEvidence, ApprovalSubject, approval_subject_hash
from executor_birth_approval_authority import _load_approval_authority_in_session, verify_decision
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import OwnerObject


class _ReviewApprovalInventory:
    def __init__(self, *, reviews, approvals, session, require_exclusion,
                 context_bindings=None):
        self.reviews, self.approvals = reviews, approvals
        self.session, self.require_exclusion = session, require_exclusion
        if reviews.name == approvals.name:
            raise RetentionError('retention_owner_invalid', 'duplicate review approval owner')
        self.owners = MappingProxyType({owner.name: owner for owner in (reviews, approvals)})
        self._context_bindings = None
        if context_bindings is not None:
            from executor_birth_prepared_root import HistoricalContextVerifiersV1
            require_exclusion()
            session._require_exclusive_global_lock()
            try:
                if not isinstance(context_bindings, Mapping):
                    raise ValueError('context bindings mapping')
                bindings, heads = dict(context_bindings), set()
                for context, binding in bindings.items():
                    if (type(context) is not str or re.fullmatch(r'sha256:[0-9a-f]{64}', context) is None
                            or type(binding) is not HistoricalContextVerifiersV1
                            or binding.public_set.material.pin.admission_context_id != context
                            or type(binding.public_set.set_id) is not str
                            or re.fullmatch(r'[0-9a-f]{64}', binding.public_set.set_id) is None
                            or type(binding.required_head_id) is not str
                            or re.fullmatch(r'sha256:[0-9a-f]{64}', binding.required_head_id) is None):
                        raise ValueError('context bindings identity')
                    heads.add(binding.required_head_id)
                if len(heads) > 1:
                    raise ValueError('context bindings frontier')
                self._context_binding_root = Path(session._root_path)
            except (AttributeError, TypeError, ValueError) as exc:
                raise RetentionError('retention_owner_invalid', 'historical approval context bindings') from exc
            # This private input belongs to the caller's held native census.
            # Copy it so later caller mutations cannot change a scan's binding.
            self._context_bindings = MappingProxyType(bindings)

    def scan(self):
        from executor_birth_prepared_root import PreparedRootError, load_historical_context_verifiers_for_contexts_in_session_v1
        from executor_birth_prepared_set import PreparedSetError
        from executor_birth_ownership_chain import OwnershipChainError
        from executor_birth_secure_fs import BirthSecureFSError
        self.require_exclusion()
        self.session._require_exclusive_global_lock()
        if (self._context_bindings is not None
                and Path(self.session._root_path) != self._context_binding_root):
            raise RetentionError('retention_owner_invalid', 'historical approval root binding')
        reviews, rows = self.reviews.scan(), self.approvals.scan()
        objects = {identity: entry[0] for identity, entry in reviews.items()}
        decisions, subjects, tokens, authorities = {}, {}, {}, {}
        try:
            contexts = tuple(sorted({row.values['admission_context_id'] for row in rows
                if row.values['status'] in {'approved', 'rejected'} and all(row.values[name] for name in (
                    'decision_actor', 'decision_key_id', 'decision_signature'))}))
            if self._context_bindings is None:
                authenticated = (load_historical_context_verifiers_for_contexts_in_session_v1(
                    contexts, self.session) if contexts else ())
                if len(authenticated) != len(contexts):
                    raise ValueError('historical approval context batch')
                bindings = dict(zip(contexts, authenticated))
            else:
                bindings = self._context_bindings
                if not set(contexts).issubset(bindings):
                    raise ValueError('historical approval context absent from supplied census')
        except (ValueError, TypeError, KeyError, PreparedRootError, PreparedSetError,
                OwnershipChainError, BirthSecureFSError) as exc:
            raise RetentionError('retention_inventory_incomplete', 'historical approval authentication') from exc
        for row in rows:
            data = row.values
            state = self.approvals.state_of(row)
            obj = OwnerObject(row.identity, row.version, state.state, state.created_at,
                              state.eligible_after, roots=state.roots)
            tokens[data['token']] = row.identity
            try:
                subject = ApprovalSubject(*(data[name] for name in (
                    'candidate_id', 'semantic_core_id', 'admission_context_id', 'approval_scope', 'expires_at')))
                if approval_subject_hash(subject) != data['subject_hash']:
                    raise ValueError('approval subject mismatch')
                subjects[row.identity] = subject
                signed = data['status'] in {'approved', 'rejected'} and all(data[name] for name in (
                    'decision_actor', 'decision_key_id', 'decision_signature'))
                if signed:
                    context = subject.admission_context_id
                    if context not in authorities:
                        historical = bindings[context]
                        authorities[context] = _load_approval_authority_in_session(
                            ('authority-sets', historical.public_set.set_id, 'approval', 'authority.json'), self.session)
                    evidence = ApprovalEvidence('birth_approval.' + data['token'], data['subject_hash'],
                        data['decision_actor'], ApprovalDecision(data['status']), data['decision_at'],
                        data['token'], data['decision_key_id'], data['decision_signature'])
                    verify_decision(authorities[context], token=data['token'], subject_hash=evidence.subject_hash,
                        scope=subject.lifecycle, actor=evidence.actor, decision=evidence.decision,
                        decided_at=evidence.decided_at, key_id=evidence.key_id, signature=evidence.signature)
                    decisions[row.identity] = evidence
                elif obj.state is NodeState.CLOSED:
                    obj = replace(obj, state=NodeState.OPEN, eligible_after=None, roots=(RootKind.OPEN_AUDIT,))
            except (ValueError, TypeError, KeyError, PreparedRootError, PreparedSetError,
                    OwnershipChainError, BirthSecureFSError) as exc:
                raise RetentionError('retention_inventory_incomplete', 'historical approval authentication') from exc
            objects[row.identity] = obj
        for identity, (_obj, record, _size) in reviews.items():
            target = tokens.get(record['token'])
            if target is None or subjects.get(target) != ApprovalSubject(**record['subject']):
                raise RetentionError('retention_inventory_incomplete', 'review approval absent or subject mismatched')
            for source, destination in ((identity, target), (target, identity)):
                obj = objects[source]
                objects[source] = replace(obj, references=tuple(sorted(set(obj.references) | {destination}, key=repr)))
        self.require_exclusion()
        if reviews != self.reviews.scan() or rows != self.approvals.scan():
            raise RetentionError('retention_owner_changed', 'review approval inventory changed')
        self.session._require_exclusive_global_lock()
        if (self._context_bindings is not None
                and Path(self.session._root_path) != self._context_binding_root):
            raise RetentionError('retention_owner_invalid', 'historical approval root binding')
        return tuple(sorted(objects.values(), key=lambda obj: repr(obj.identity))), MappingProxyType(decisions)

    def inventory(self):
        return self.scan()[0]
