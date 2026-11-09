"""Join native journal owners in a stopped, consistent physical inventory.

Copies are distinct objects. Bidirectional copy links preserve all evidence
when any copy is retained, including a whole gzip segment containing other
turns. Closed connected groups remain collectible by the existing planner.
This subset neither attests the full F6 census nor grants deletion authority.
"""
from dataclasses import replace
from types import MappingProxyType

from executor_birth_retention import RetentionError
from install.birth_retention_feedback import _compensating_retry


class _JournalInventory:
    def __init__(self, *, turns, archives, feedback, protected_undo, history=None):
        self.turns, self.archives = turns, archives
        self.feedback, self.protected_undo = feedback, protected_undo
        self.undo = protected_undo.journal
        owners = (turns, archives, feedback, self.undo, protected_undo)
        if tuple(owner.name for owner in owners) != (
            "turn_logs", "turn_archives", "turn_feedback", "undo_operations",
            "protected_undo_blobs",
        ):
            raise RetentionError("retention_owner_invalid", "journal owners")
        self.history = history
        if history is not None:
            if history.name != "history_backup_blobs" or history.journal.path != self.undo.path:
                raise RetentionError("retention_owner_invalid", "history journal owner")
            owners += (history,)
        self.owners = MappingProxyType({owner.name: owner for owner in owners})

    def _snapshot(self):
        return (self.turns.scan(), self.archives.scan(), self.feedback.scan(),
                self.undo.scan(), self.protected_undo.inventory(),
                None if self.history is None else self.history.inventory())

    def inventory(self):
        for owner in self.owners.values():
            owner.require_exclusion()
        snapshot = self._snapshot()
        turns, archives, feedback, undo, protected, history = snapshot
        objects = {}

        def add(obj):
            if obj.identity in objects:
                raise RetentionError("retention_inventory_incomplete", "duplicate journal object")
            objects[obj.identity] = obj

        for entry in (*turns, *archives, *feedback, *undo):
            add(entry.object)
        projections = [(self.protected_undo.name, protected)]
        if history is not None:
            projections.append((self.history.name, history))
        for owner_name, projection in projections:
            observed, seen = set(), set()
            for obj in projection:
                if obj.identity in seen:
                    raise RetentionError("retention_inventory_incomplete", "duplicate protected object")
                seen.add(obj.identity)
                if obj.identity.owner == self.undo.name:
                    previous = objects.get(obj.identity)
                    if previous is None or replace(previous, references=()) != replace(obj, references=()):
                        raise RetentionError("retention_owner_changed", "undo projections disagree")
                    observed.add(obj.identity)
                    objects[obj.identity] = replace(previous, references=tuple(
                        set(previous.references) | set(obj.references)))
                elif obj.identity.owner == owner_name:
                    add(obj)
                else:
                    raise RetentionError("retention_inventory_incomplete", "foreign protected object")
            if observed != {entry.object.identity for entry in undo}:
                raise RetentionError("retention_inventory_incomplete", "missing undo projection")

        references = {identity: set(obj.references) for identity, obj in objects.items()}
        if history is not None:
            index = self.history.reference_index(
                obj.identity for obj in history if obj.identity.owner == self.history.name)
            for entry in (*turns, *archives, *feedback):
                references[entry.object.identity].update(self.history.references(entry.records, index))
        copies = {}
        for entry in (*turns, *archives):
            for record in entry.records:
                copies.setdefault(record["turn_id"], set()).add(entry.object.identity)
        # A star is enough for transitive retention, without quadratic edges
        # when an interrupted compression left many copies of the same turn.
        for identities in copies.values():
            hub = min(identities, key=lambda identity: identity.key.node_id)
            for identity in identities - {hub}:
                references[hub].add(identity)
                references[identity].add(hub)

        def link_turn(identity, turn_id, *, absent_allowed=False):
            if type(turn_id) is not str or not turn_id:
                raise RetentionError("retention_inventory_incomplete", "missing turn binding")
            targets = copies.get(turn_id)
            if not targets:
                if absent_allowed:
                    return
                raise RetentionError("retention_inventory_incomplete", "referenced turn absent")
            references[identity].add(min(targets, key=lambda target: target.key.node_id))

        for entry in (*turns, *archives):
            for record in entry.records:
                if record.get("parent_turn_id"):
                    link_turn(entry.object.identity, record["parent_turn_id"])
        for entry in feedback:
            for record in entry.records:
                link_turn(entry.object.identity, record["turn_id"], absent_allowed=(
                    record.get("warning") == "turn_not_found" or _compensating_retry(record)))
        for entry in undo:
            for record in entry.records:
                if record["type"] == "pending":
                    link_turn(entry.object.identity, record.get("turn_id"))
        if any(ref not in objects for refs in references.values() for ref in refs):
            raise RetentionError("retention_inventory_incomplete", "unresolved journal reference")
        if self._snapshot() != snapshot:
            raise RetentionError("retention_owner_changed", "journals changed during inventory")
        for owner in self.owners.values():
            owner.require_exclusion()
        return tuple(replace(objects[identity], references=tuple(sorted(
            references[identity] - {identity}, key=lambda target: target.key.node_id)))
            for identity in sorted(objects, key=lambda identity: identity.key.node_id))
