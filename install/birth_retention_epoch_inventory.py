"""Join physical signed history and native epoch/migration bundles.

Generation presence does not invent an epoch, and historical signatures do not
close its audit. Current pointers and retirement predecessors retain matching
epochs; other epoch lifetimes remain the native owner's decision. This bounded
join is not the installation inventory or a signed-generation collector.
"""
from dataclasses import replace
from types import MappingProxyType

from executor_birth_retention import RetentionError, RootKind
from install.birth_retention_epoch_sqlite import _EpochLegacyOwner
from install.birth_retention_maintenance import OwnerObject
from manifest_inventory import ContractId, ManifestOrigin


class _EpochInventory:
    def __init__(self, *, epochs, signed_store):
        self.epochs, self.signed_store = epochs, signed_store
        self.legacy = tuple(_EpochLegacyOwner(kind, path=epochs.path,
            require_exclusion=epochs.require_exclusion, owner=epochs.owner)
            for kind in ('state', 'migrations'))
        self.owners = MappingProxyType({owner.name: owner for owner in
                                       (epochs, *self.legacy, signed_store)})

    def _rows(self):
        return tuple((owner.name, owner.scan()) for owner in (self.epochs, *self.legacy))

    def inventory(self):
        self.epochs.require_exclusion()
        rows = self._rows()
        signed = self.signed_store.inventory()
        objects = {obj.identity: obj for obj in signed}
        if len(objects) != len(signed):
            raise RetentionError('retention_inventory_incomplete', 'duplicate signed identity')
        epoch_ids, legacy_ids = {}, {}
        for name, records in rows:
            owner = self.owners[name]
            for row in records:
                state = owner.state_of(row)
                if row.identity in objects:
                    raise RetentionError('retention_inventory_incomplete', 'duplicate epoch identity')
                objects[row.identity] = OwnerObject(row.identity, row.version, state.state,
                    state.created_at, state.eligible_after, roots=state.roots)
                if owner is self.epochs:
                    epoch_ids[(row.values['contract_id'], row.values['generation_id'])] = row.identity
                elif owner.table == 'executor_legacy_state':
                    legacy_ids[row.values['legacy_id']] = row
        refs = {identity: set(obj.references) for identity, obj in objects.items()}

        def link(source, target):
            if target not in objects:
                raise RetentionError('retention_inventory_incomplete', 'epoch reference absent')
            refs[source].add(target)

        def generation(contract, identifier):
            try:
                origin, relative = contract.split(':', 1)
                cid = ContractId(ManifestOrigin(origin), relative)
                target = self.signed_store.generation_identity(cid, identifier)
            except (ValueError, TypeError, AttributeError) as exc:
                raise RetentionError('retention_owner_state_invalid', 'epoch contract identity') from exc
            if target not in objects:
                raise RetentionError('retention_inventory_incomplete', 'epoch generation absent')
            return target

        for name, records in rows:
            owner = self.owners[name]
            for row in records:
                related = dict(row.related)
                if owner is self.epochs:
                    link(row.identity, generation(row.values['contract_id'], row.values['generation_id']))
                    for decision in related['executor_legacy_resolutions']:
                        source = legacy_ids.get(decision['legacy_id'])
                        if source is None:
                            raise RetentionError('retention_inventory_incomplete', 'legacy source absent')
                        link(row.identity, source.identity)
                elif owner.table == 'executor_legacy_state':
                    for decision in related['executor_legacy_resolutions']:
                        contract, identifier = decision['contract_id'], decision['generation_id']
                        if (contract is None) != (identifier is None):
                            raise RetentionError('retention_owner_state_invalid', 'legacy identity pair')
                        if contract is not None:
                            link(row.identity, generation(contract, identifier))
                            epoch = epoch_ids.get((contract, identifier))
                            if epoch is not None:
                                link(row.identity, epoch)
                else:
                    for source in related['executor_legacy_state']:
                        observed = legacy_ids.get(source['legacy_id'])
                        if observed is None or source != observed.values:
                            raise RetentionError('retention_owner_changed', 'legacy projections disagree')
                        link(row.identity, observed.identity)
                        link(observed.identity, row.identity)

        # A current pointer can name a retirement, whose exact predecessor is
        # separately rooted. Do not derive currentness from file age or names.
        protected = set()
        for obj in signed:
            if RootKind.CURRENT_POINTER in obj.roots:
                protected.update(obj.references)
            if RootKind.RETIREMENT_PREDECESSOR in obj.roots:
                protected.add(obj.identity)
        for pair, epoch in epoch_ids.items():
            target = generation(*pair)
            if target in protected:
                link(target, epoch)
        if any(target not in objects for targets in refs.values() for target in targets):
            raise RetentionError('retention_inventory_incomplete', 'unresolved epoch reference')
        if rows != self._rows() or signed != self.signed_store.inventory():
            raise RetentionError('retention_owner_changed', 'epoch inventory changed')
        self.epochs.require_exclusion()
        return tuple(replace(objects[identity], references=tuple(sorted(refs[identity],
                     key=lambda item: item.key.node_id)))
                     for identity in sorted(objects, key=lambda item: item.key.node_id))
