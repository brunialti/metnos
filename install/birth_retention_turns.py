"""Native TurnLog/StepLog records using the shared F6 JSONL mechanism.

One physical object contains every original line for one turn in one daily
journal. Open input, missing execution evidence and contradictory observations
remain roots. Native receipt parsing supplies exact contract/generation
references to the enclosing inventory; this module never infers absence of
external feedback, undo, review, approval or job references.
"""
from __future__ import annotations

from datetime import date, timedelta
import time

from executor_birth_feedback import (
    FeedbackError, dispatch_identifier_reference, execution_receipt_from_record,
)
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_jsonl import JournalEntry, _JsonlOwner, _time
from install.birth_retention_maintenance import OwnerObject
from install.birth_retention_sqlite import _iso


def _receipts(record):
    """Native self-verifying dispatch bindings, never a guessed tool name."""
    found, gap = [], False
    for step in record["steps"]:
        tool = step.get("chosen_tool")
        encoded = step.get("execution_receipt")
        if encoded is None:
            if tool and tool != "final_answer":
                gap = True
            continue
        try:
            receipt = execution_receipt_from_record(encoded)
            if (receipt.executor_name != tool or receipt.turn_id !=
                    dispatch_identifier_reference("turn", record["turn_id"])):
                raise FeedbackError("feedback_binding_invalid", "turn or tool")
            found.append(receipt)
        except (FeedbackError, TypeError, ValueError):
            gap = True
    return tuple(found), gap


class _TurnJournal(_JsonlOwner):
    name = "turn_logs"
    node_type = "audit_segment"

    def _record_key(self, record):
        if (type(record.get("turn_id")) is not str or not record["turn_id"]
                or type(record.get("steps")) is not list
                or any(type(step) is not dict for step in record["steps"])):
            raise ValueError("turn record")
        _time(record.get("ts_start"))
        if "ts_end" in record:
            _time(record["ts_end"])
        for key in ("actor", "owner_user_id", "parent_turn_id", "outcome", "final_kind"):
            if key in record and type(record[key]) is not str:
                raise ValueError("turn identity or outcome")
        return record["turn_id"]

    def _entries(self, custody, info, lines, records):
        result = []
        for turn_id, (items, version) in self._groups(custody, info, lines, records).items():
            roots, times, owners = set(), [], set()
            for record in items:
                started = _time(record["ts_start"])
                ended = _time(record["ts_end"]) if record.get("ts_end") else None
                times.append(started)
                owners.add((record.get("actor", "host"), record.get("owner_user_id", "")))
                if ended is None:
                    roots.add(RootKind.IN_PROGRESS_JOB)
                else:
                    times.append(ended)
                    if ended < started:
                        roots.add(RootKind.OPEN_AUDIT)
                if (record.get("outcome") == "awaiting_input"
                        or record.get("final_kind") in {"ask", "needs_inputs", "input_required"}
                        or record.get("pending_location")):
                    roots.add(RootKind.OPEN_APPROVAL)
                elif record.get("outcome") not in {"completed", "partial", "failed"}:
                    roots.add(RootKind.OPEN_AUDIT)
                _, gap = _receipts(record)
                if gap:
                    roots.add(RootKind.OPEN_AUDIT)
            if len(owners) != 1:
                roots.add(RootKind.OPEN_AUDIT)
            ordered = tuple(sorted(roots, key=lambda root: root.value))
            result.append(JournalEntry(OwnerObject(self.identity(turn_id), version,
                NodeState.OPEN if roots else NodeState.CLOSED, _iso(min(times)),
                None if roots else _iso(max(times) + timedelta(days=90)), roots=ordered), items))
        return tuple(result)


class _TurnLogOwner(_PrivateFiles):
    name = _TurnJournal.name

    @staticmethod
    def _filename(name):
        try:
            if type(name) is not str:
                raise ValueError("daily journal")
            suffix = ".jsonl.bak" if name.endswith(".jsonl.bak") else ".jsonl"
            if (not name.endswith(suffix)
                    or date.fromisoformat(name[:-len(suffix)]).isoformat() + suffix != name):
                raise ValueError("daily journal")
        except ValueError as exc:
            raise RetentionError("retention_inventory_incomplete", "unknown turn journal entry") from exc
        return name

    def _journal(self, filename):
        return _TurnJournal(path=self.root / self._filename(filename),
                            require_exclusion=self.require_exclusion, owner=self.owner)

    def identity(self, filename, turn_id):
        return self._journal(filename).identity(turn_id)

    def _owner_for(self, identity):
        # Comparing the complete URI also rejects encoded traversal, foreign
        # authorities, parameters and alternate spellings of the same path.
        name = identity.store.rsplit("/", 1)[-1]
        journal = self._journal(name)
        if identity != journal.identity(identity.local_id):
            raise RetentionError("retention_owner_invalid", "foreign turn journal identity")
        return journal

    def scan(self):
        deadline, result = time.monotonic() + 15, []
        try:
            with self._directory() as (directory, _):
                names = self._names(directory)
                for name in names:
                    result.extend(self._journal(name).scan())
                    if len(result) > 100_000 or time.monotonic() > deadline:
                        raise RetentionError("retention_inventory_incomplete", "turn inventory budget")
                if names != self._names(directory):
                    raise RetentionError("retention_owner_changed", "turn journal set changed")
            return tuple(result)
        except FileNotFoundError:
            return ()

    def version(self, identity):
        journal = self._owner_for(identity)
        with self._directory():
            return journal.version(identity)

    def delete(self, identity, expected_version):
        journal = self._owner_for(identity)
        with self._directory():
            journal.delete(identity, expected_version)
