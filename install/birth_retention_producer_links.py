"""Native historical Producer/admission joins; incomplete requests remain open."""
from dataclasses import replace
from types import MappingProxyType

from cryptography.exceptions import InvalidSignature

from executor_birth_receipts import AdmissionKind
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import OwnerObject


class _ProducerAdmissionInventory:
    def __init__(self, *, producers, signed_store, session, require_exclusion, public_sources=()):
        self.producers, self.signed_store = producers, signed_store
        self.session, self.require_exclusion = session, require_exclusion
        if type(public_sources) is not tuple:
            raise RetentionError('retention_owner_invalid', 'producer public sources')
        self.public_sources = public_sources
        if producers.name == signed_store.name:
            raise RetentionError('retention_owner_invalid', 'duplicate producer admission owner')
        self.owners = MappingProxyType({owner.name: owner for owner in (producers, signed_store)})

    def _snapshot(self):
        self.require_exclusion()
        self.session._require_exclusive_global_lock()
        if any(self.owners.get(owner.name) is not owner for owner in (self.producers, self.signed_store)):
            raise RetentionError('retention_owner_changed', 'producer owner mapping')
        return self.producers.scan(), self.signed_store.scan_with_evidence()

    def inventory(self):
        from executor_birth_operational import verify_historical_publication_v1
        from executor_birth_prepared_root import load_historical_producer_declarations_for_contexts_in_session_v1
        from executor_birth_producer_store import (producer_history_rows_for_binding_v1,
            verify_historical_producer_binding_v1, verify_historical_reattestation_producer_v2)
        from executor_birth_reattestation import verify_historical_reattestation_v2
        before = self._snapshot()
        rows, (signed, admissions, evidence) = before
        objects, by_hash = {}, {}
        for obj in signed:
            if obj.identity in objects or obj.identity.owner != self.signed_store.name:
                raise RetentionError('retention_inventory_incomplete', 'duplicate or foreign signed object')
            objects[obj.identity] = obj
        for row in rows:
            if row.identity in objects or row.identity.owner != self.producers.name:
                raise RetentionError('retention_inventory_incomplete', 'duplicate or foreign producer object')
            state = self.producers.state_of(row)
            roots = tuple(sorted(set(state.roots) | {RootKind.OPEN_AUDIT}, key=lambda root: root.value))
            objects[row.identity] = OwnerObject(row.identity, row.version, NodeState.OPEN,
                state.created_at, None, roots=roots)
            digest = row.values['receipt_hash']
            if digest in by_hash:
                raise RetentionError('retention_inventory_incomplete', 'ambiguous producer receipt')
            by_hash[digest] = row
        if set(admissions) != set(evidence) or any(identity not in objects for identity in admissions):
            raise RetentionError('retention_inventory_incomplete', 'admission physical evidence')
        contexts = tuple(sorted({admission.admission_context_id for admission in admissions.values()}))
        try:
            selected = load_historical_producer_declarations_for_contexts_in_session_v1(
                contexts, self.session, include_reattestation=True,
                public_sources=self.public_sources) if contexts else ()
            if type(selected) is not tuple or len(selected) != len(contexts):
                raise ValueError('historical producer declaration coverage')
            declarations = dict(zip(contexts, selected))
            for identity, admission in admissions.items():
                row = by_hash.get(admission.producer_receipt_hash)
                if row is None:
                    raise ValueError('admission producer absent')
                children = [child for table, values in row.related
                            if table == 'birth_producer_issuance' for child in values]
                if len(children) != 1 or row.values['state'] not in {'committed', 'in_progress'}:
                    raise ValueError('admission producer issuance or state')
                receipt, issuance = producer_history_rows_for_binding_v1(row.values, children[0])
                inputs = dict(evidence=evidence[identity], receipt_row=receipt, issuance_row=issuance,
                              declarations=declarations[admission.admission_context_id])
                if receipt.state == 'committed':
                    verifier = (verify_historical_publication_v1 if admission.kind is AdmissionKind.ADMISSION
                                else verify_historical_reattestation_v2)
                    verifier(**inputs)
                else:
                    verifier = (verify_historical_producer_binding_v1 if admission.kind is AdmissionKind.ADMISSION
                                else verify_historical_reattestation_producer_v2)
                    verifier(inputs['evidence'].receipt_bytes, receipt_row=receipt, issuance_row=issuance,
                             declarations=inputs['declarations'])
                for source, target in ((identity, row.identity), (row.identity, identity)):
                    obj = objects[source]
                    objects[source] = replace(obj, references=tuple(sorted(set(obj.references) | {target}, key=repr)))
        except (InvalidSignature, ValueError, RuntimeError, TypeError, KeyError) as exc:
            raise RetentionError('retention_inventory_incomplete', 'historical producer authentication') from exc
        if any(target not in objects for obj in objects.values() for target in obj.references):
            raise RetentionError('retention_inventory_incomplete', 'unresolved producer reference')
        if before != self._snapshot():
            raise RetentionError('retention_owner_changed', 'producer admission links changed')
        self.require_exclusion()
        self.session._require_exclusive_global_lock()
        return tuple(sorted(objects.values(), key=lambda obj: repr(obj.identity)))
