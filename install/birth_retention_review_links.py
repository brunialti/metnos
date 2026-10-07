"""Historical review/approval links; no candidate requalification or fresh consent."""
from dataclasses import replace
from types import MappingProxyType

from executor_birth_approval import ApprovalDecision, ApprovalEvidence, ApprovalSubject, approval_subject_hash
from executor_birth_approval_authority import _load_approval_authority_in_session, verify_decision
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import OwnerObject


class _ReviewApprovalInventory:
    def __init__(self, *, reviews, approvals, session, require_exclusion):
        self.reviews, self.approvals = reviews, approvals
        self.session, self.require_exclusion = session, require_exclusion
        if reviews.name == approvals.name:
            raise RetentionError('retention_owner_invalid', 'duplicate review approval owner')
        self.owners = MappingProxyType({owner.name: owner for owner in (reviews, approvals)})

    def scan(self):
        from executor_birth_prepared_root import PreparedRootError, load_historical_context_verifiers_in_session_v1
        from executor_birth_prepared_set import PreparedSetError
        from executor_birth_ownership_chain import OwnershipChainError
        from executor_birth_secure_fs import BirthSecureFSError
        self.require_exclusion()
        self.session._require_exclusive_global_lock()
        reviews, rows = self.reviews.scan(), self.approvals.scan()
        objects = {identity: entry[0] for identity, entry in reviews.items()}
        decisions, subjects, tokens, authorities = {}, {}, {}, {}
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
                        historical = load_historical_context_verifiers_in_session_v1(context, self.session)
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
        return tuple(sorted(objects.values(), key=lambda obj: repr(obj.identity))), MappingProxyType(decisions)

    def inventory(self):
        return self.scan()[0]
