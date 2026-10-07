"""Read-only approval/admission links; consumed requests remain open for recovery.

This projection does not prove producer origin or predecessor approval scope.
Native terminal reconciliation must supply that proof before any closure.
"""
from dataclasses import replace
from types import MappingProxyType

from executor_birth_approval import ApprovalDecision, approval_evidence_hash
from executor_birth_receipts import ApprovedLifecycle, RevisionClass
from executor_birth_retention import NodeState, RetentionError, RootKind


class _ApprovalAdmissionInventory:
    def __init__(self, *, review_links, signed_store, require_exclusion):
        self.review_links, self.signed_store = review_links, signed_store
        self.require_exclusion = require_exclusion
        owners = dict(review_links.owners)
        if signed_store.name in owners:
            raise RetentionError('retention_owner_invalid', 'duplicate admission owner')
        owners[signed_store.name] = signed_store
        self.owners = MappingProxyType(owners)

    def _snapshot(self):
        self.require_exclusion()
        if (dict(self.review_links.owners) != {k: v for k, v in self.owners.items()
                                             if k != self.signed_store.name}
                or self.owners.get(self.signed_store.name) is not self.signed_store
                or any(owner.name != name for name, owner in self.owners.items())):
            raise RetentionError('retention_owner_changed', 'admission owner mapping')
        return (self.review_links.scan(), self.signed_store.scan(),
                self.review_links.approvals.scan())

    def inventory(self):
        before = self._snapshot()
        (reviews, decisions), (signed, admissions), rows = before
        objects = {}
        for obj in (*reviews, *signed):
            if obj.identity in objects or obj.identity.owner not in self.owners:
                raise RetentionError('retention_inventory_incomplete', 'duplicate or foreign admission object')
            objects[obj.identity] = obj
        consumed = {}
        for row in rows:
            if row.identity not in objects or row.version != objects[row.identity].version:
                raise RetentionError('retention_owner_changed', 'approval projection changed')
            for _table, children in row.related:
                for child in children:
                    request = child['request_id']
                    if request in consumed or child['token'] != row.values['token']:
                        raise RetentionError('retention_inventory_incomplete', 'consumption identity')
                    consumed[request] = row
                    obj = objects[row.identity]
                    objects[row.identity] = replace(obj, state=NodeState.OPEN, eligible_after=None,
                        roots=tuple(sorted(set(obj.roots) | {RootKind.OPEN_AUDIT}, key=lambda root: root.value)))
        scopes = {RevisionClass.AUTHORITY_REVISION: 'authority', RevisionClass.PROMOTION_REVISION: 'promotion',
                  RevisionClass.REACTIVATION_REVISION: 'reactivation'}
        for identity, admission in admissions.items():
            if identity not in objects:
                raise RetentionError('retention_inventory_incomplete', 'admission physical identity')
            if admission.approval_hash is None:
                continue
            row = consumed.get(admission.birth_request_id)
            evidence = None if row is None else decisions.get(row.identity)
            if row is None or evidence is None or evidence.decision is not ApprovalDecision.APPROVED:
                raise RetentionError('retention_inventory_incomplete', 'admission approval consumption absent')
            data = row.values
            scope = scopes.get(admission.revision_class)
            if scope is None:
                if admission.approved_lifecycle not in (ApprovedLifecycle.ACTIVE, ApprovedLifecycle.PREEXERCISE):
                    raise RetentionError('retention_inventory_incomplete', 'admission approval lifecycle')
                scope = admission.approved_lifecycle.value
            if (data['approval_scope'] != scope
                    or any(data[name] != getattr(admission, name) for name in (
                        'candidate_id', 'semantic_core_id', 'admission_context_id'))
                    or evidence.registry_token != data['token']
                    or approval_evidence_hash(evidence) != admission.approval_hash):
                raise RetentionError('retention_inventory_incomplete', 'admission approval binding')
            for source, target in ((identity, row.identity), (row.identity, identity)):
                obj = objects[source]
                objects[source] = replace(obj, references=tuple(sorted(set(obj.references) | {target}, key=repr)))
        if any(target not in objects for obj in objects.values() for target in obj.references):
            raise RetentionError('retention_inventory_incomplete', 'unresolved admission reference')
        if before != self._snapshot():
            raise RetentionError('retention_owner_changed', 'admission links changed')
        self.require_exclusion()
        return tuple(sorted(objects.values(), key=lambda obj: repr(obj.identity)))
