"""Read-only composition of an explicitly bounded set of native subgraphs.

This internal helper does not attest the installation census, observe all root
sources, resolve operational paths, or authorize a maintenance plan. Its caller
must still implement those installation-specific obligations. A required owner
set only describes this composition's scope, never complete F6 coverage.
"""
from dataclasses import replace
from types import MappingProxyType
import time

from executor_birth_retention import RetentionError
from install.birth_retention_maintenance import OwnerObject

_MAX_OBJECTS = 100_000
_MAX_REFERENCES = 1_000_000
_SECONDS = 15


class _InventoryComposition:
    def __init__(self, *, components, required_owners, require_exclusion):
        self.require_exclusion = require_exclusion
        self.components = tuple(components)
        self.required_owners = frozenset(required_owners)
        if (not self.components or not self.required_owners
                or any(type(name) is not str or not name for name in self.required_owners)
                or len({id(component) for component in self.components}) != len(self.components)):
            raise RetentionError('retention_inventory_incomplete', 'composition scope')
        snapshots, owners = [], {}
        for component in self.components:
            local = dict(component.owners)
            if not local:
                raise RetentionError('retention_inventory_incomplete', 'empty component owners')
            for name, owner in local.items():
                if name != owner.name or (name in owners and owners[name] is not owner):
                    raise RetentionError('retention_owner_invalid', 'ambiguous component owner')
                owners[name] = owner
            snapshots.append(local)
        if set(owners) != self.required_owners:
            raise RetentionError('retention_inventory_incomplete', 'composition owner set')
        self._owner_maps = tuple(snapshots)
        self.owners = MappingProxyType(owners)

    def _check_owners(self):
        for component, expected in zip(self.components, self._owner_maps):
            current = dict(component.owners)
            if (current.keys() != expected.keys()
                    or any(current[name] is not owner or owner.name != name
                           for name, owner in expected.items())):
                raise RetentionError('retention_owner_changed', 'component owner map changed')
        self.require_exclusion()

    def _snapshot(self, deadline):
        self._check_owners()
        objects, membership, projections = {}, {}, []
        count = references = 0
        for component, owners in zip(self.components, self._owner_maps):
            local = {}
            by_owner = {name: set() for name in owners}
            for obj in component.inventory():
                count += 1
                if not isinstance(obj, OwnerObject) or obj.identity.owner not in owners:
                    raise RetentionError('retention_inventory_incomplete', 'foreign component object')
                references += len(obj.references)
                if count > _MAX_OBJECTS or references > _MAX_REFERENCES or time.monotonic() > deadline:
                    raise RetentionError('retention_inventory_incomplete', 'composition budget')
                if obj.identity in local:
                    raise RetentionError('retention_inventory_incomplete', 'duplicate component object')
                # Edge order is not semantic; membership and all physical fields are.
                obj = replace(obj, references=tuple(sorted(set(obj.references), key=lambda ref: ref.key.node_id)))
                local[obj.identity] = obj
                by_owner[obj.identity.owner].add(obj.identity)
                previous = objects.get(obj.identity)
                if previous is not None:
                    if replace(previous, references=()) != replace(obj, references=()):
                        raise RetentionError('retention_owner_changed', 'component projections disagree')
                    obj = replace(obj, references=tuple(sorted(
                        set(previous.references) | set(obj.references), key=lambda ref: ref.key.node_id)))
                objects[obj.identity] = obj
            for name, identities in by_owner.items():
                if name in membership and membership[name] != identities:
                    raise RetentionError('retention_inventory_incomplete', 'missing shared owner projection')
                membership[name] = identities
            projections.append(local)
            self._check_owners()
            if time.monotonic() > deadline:
                raise RetentionError('retention_inventory_incomplete', 'composition budget')
        if any(reference not in objects for obj in objects.values() for reference in obj.references):
            raise RetentionError('retention_inventory_incomplete', 'unresolved component reference')
        return objects, projections

    def inventory(self):
        deadline = time.monotonic() + _SECONDS
        objects, projections = self._snapshot(deadline)
        repeated, repeated_projections = self._snapshot(deadline)
        if objects != repeated or projections != repeated_projections:
            raise RetentionError('retention_owner_changed', 'composition changed during inventory')
        self._check_owners()
        return tuple(sorted(objects.values(), key=lambda obj: obj.identity.key.node_id))
