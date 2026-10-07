"""Native Telos journal retention, with physical copies and durable decisions.

Rejection closes proposal payloads, not the decision suppressing regeneration.
Acceptance can have started work even when a later decision rejects it: that
history stays open until the enclosing inventory resolves operative markers.
This owner subset does not attest the full installation census.
"""
from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_jsonl import JournalEntry, _JsonlOwner, _time
from install.birth_retention_maintenance import OwnerObject
from install.birth_retention_sqlite import _iso
from telos_proposals_store import _format_prop_id, _VALID_ACTIONS


class _TelosDecisions(_JsonlOwner):
    name = "telos_decisions"
    node_type = "proposal"

    def _record_key(self, record):
        if (type(record.get("prop_id")) is not str or not record["prop_id"]
                or record.get("action") not in _VALID_ACTIONS
                or type(record.get("by")) is not str):
            raise ValueError("Telos decision")
        _time(record.get("ts"))
        for key in ("executor_target", "signature_relaxed", "lens"):
            if key in record and type(record[key]) is not str:
                raise ValueError("Telos rejection binding")
        return record["prop_id"]

    def _entries(self, custody, info, lines, records):
        return tuple(JournalEntry(OwnerObject(
            self.identity(key), version, NodeState.OPEN,
            _iso(min(_time(record["ts"]) for record in items)), None,
            roots=(RootKind.OPEN_REVISION,),
        ), items) for key, (items, version) in self._groups(custody, info, lines, records).items())

    def current(self):
        # Read through the same checked descriptors during compaction recovery.
        # scan() rejects an outstanding proposal temporary in the shared parent;
        # recovery must still read the unchanged native decision history.
        try:
            with self._parent() as (parent, custody), self._read(parent) as (info, lines, records):
                return () if info is None else self._entries(custody, info, lines, records)
        except FileNotFoundError:
            return ()


class _TelosProposalJournal(_JsonlOwner):
    name = "telos_proposals"
    node_type = "proposal"

    def __init__(self, *, decisions, **kwargs):
        super().__init__(**kwargs)
        self.decisions = decisions

    def _record_key(self, record):
        _time(record.get("ts"))
        # Native decision IDs round to microseconds. Preserve collisions as a
        # group; neither timestamps nor executor names identify physical copies.
        return _format_prop_id(float(record["ts"]))

    def _entries(self, custody, info, lines, records):
        decisions = {entry.object.identity.local_id: entry for entry in self.decisions.current()}
        result = []
        for key, (items, version) in self._groups(custody, info, lines, records).items():
            decision = decisions.get(key)
            history = decision.records if decision else ()
            closed = (bool(history) and history[-1]["action"] == "reject"
                      and all(record["action"] != "accept" for record in history))
            times = tuple(_time(record["ts"]) for record in (*items, *history))
            result.append(JournalEntry(OwnerObject(
                self.identity(key), version, NodeState.CLOSED if closed else NodeState.OPEN,
                _iso(min(times)), _iso(max(times) + timedelta(days=90)) if closed else None,
                references=(decision.object.identity,) if decision else (),
                roots=() if closed else (RootKind.OPEN_APPROVAL,),
            ), items))
        return tuple(result)


class _TelosProposalOwner:
    name = _TelosProposalJournal.name

    def __init__(self, *, paths, decisions, markers=None):
        paths = tuple(paths)
        if len(paths) != 3 or len(set(paths)) != 3 or decisions.name != "telos_decisions":
            raise RetentionError("retention_owner_invalid", "Telos native stores")
        self.decisions = decisions
        if markers is not None and markers.name != "telos_acceptance_markers":
            raise RetentionError("retention_owner_invalid", "Telos marker owner")
        self.markers = markers
        self.require_exclusion = decisions.require_exclusion
        self.journals = tuple(_TelosProposalJournal(path=path, decisions=decisions,
            require_exclusion=self.require_exclusion, owner=decisions.owner) for path in paths)

    def scan(self):
        return tuple(entry for journal in self.journals for entry in journal.scan())

    def _journal(self, identity):
        for journal in self.journals:
            if identity == journal.identity(identity.local_id):
                return journal
        raise RetentionError("retention_owner_invalid", "foreign Telos proposal")

    def version(self, identity):
        return self._journal(identity).version(identity)

    def delete(self, identity, expected_version):
        self._journal(identity).delete(identity, expected_version)

    @property
    def owners(self):
        return MappingProxyType({self.name: self, self.decisions.name: self.decisions,
                                 **({self.markers.name: self.markers} if self.markers else {})})

    def inventory(self):
        self.require_exclusion()
        snapshot = self.scan(), self.decisions.scan(), self.markers.scan() if self.markers else {}
        proposals, decisions, markers = snapshot
        objects = {entry.object.identity: entry.object for entry in (*proposals, *decisions)}
        marker_objects = self.markers.inventory() if self.markers else ()
        if {obj.identity: replace(obj, references=()) for obj in marker_objects} != {
                identity: entry[0] for identity, entry in markers.items()}:
            raise RetentionError("retention_owner_changed", "Telos marker projection changed")
        objects.update((obj.identity, obj) for obj in marker_objects)
        copies = {}
        for entry in proposals:
            copies.setdefault(entry.object.identity.local_id, set()).add(entry.object.identity)
        references = {identity: set(obj.references) for identity, obj in objects.items()}
        decision_ids = {entry.object.identity.local_id: entry.object.identity for entry in decisions}
        for identity, (_, _, payload) in markers.items():
            # Sidecars have no prop_id; their physical marker supplies the edge.
            # Non-Telos IDs remain under the marker's native obligations.
            # Introvertiva snapshots have no prop_id; matching executor labels
            # or snapshot filenames would invent a cross-store reference.
            prop_id = payload.get("prop_id")
            if type(prop_id) is not str:
                continue
            if prop_id in copies:
                for proposal in copies[prop_id]:
                    references[identity].add(proposal)
                    references[proposal].add(identity)
            if prop_id in decision_ids:
                references[identity].add(decision_ids[prop_id])
        for identities in copies.values():
            hub = min(identities, key=lambda item: item.key.node_id)
            for identity in identities - {hub}:
                references[hub].add(identity)
                references[identity].add(hub)
        if any(ref not in objects for refs in references.values() for ref in refs):
            raise RetentionError("retention_inventory_incomplete", "Telos decision absent")
        if (self.scan(), self.decisions.scan(), self.markers.scan() if self.markers else {}) != snapshot:
            raise RetentionError("retention_owner_changed", "Telos inventory changed")
        self.require_exclusion()
        return tuple(replace(objects[identity], references=tuple(sorted(references[identity],
            key=lambda item: item.key.node_id))) for identity in sorted(objects,
            key=lambda item: item.key.node_id))
