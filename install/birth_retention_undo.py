"""Native undo closure and boundaries, using shared recoverable JSONL custody."""
from datetime import timedelta

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_jsonl import JournalEntry, _JsonlOwner, _time
from install.birth_retention_maintenance import OwnerObject
from install.birth_retention_sqlite import _iso


_TEMP_PREFIX = ".metnos-f6-undo-"


class _UndoOwner(_JsonlOwner):
    name = "undo_operations"
    temporary_prefix = _TEMP_PREFIX

    def _record_key(self, record):
        if (record.get("type") not in {"pending", "done", "closed", "undone"}
                or type(record.get("op_id")) is not str or not record["op_id"]):
            raise ValueError("undo record")
        _time(record.get("ts"))
        field = {"pending": "plan", "done": "results", "undone": "reverse_results"}.get(record["type"])
        if field is not None and type(record.get(field)) is not dict:
            raise ValueError("undo payload")
        if record["type"] == "closed" and record.get("outcome") not in {"no_effect", "irreversible"}:
            raise ValueError("undo closure")
        return record["op_id"]

    def _entries(self, custody, info, lines, records):
        grouped = self._groups(custody, info, lines, records)
        boundaries = {}
        # The last completed turn per actor is an undo boundary even when it
        # did nothing. Removing it could make undo reach a different old turn.
        # Stable insertion order reproduces the native reader's tied timestamps.
        for op_id, (items, version) in grouped.items():
            op = {item["type"]: item for item in items}
            pending = op.get("pending")
            completion = op.get("undone") or op.get("closed") or op.get("done")
            if pending and completion:
                actor = pending.get("actor") or "host"
                if type(actor) is not str or len(actor) > 4096:
                    raise RetentionError("retention_owner_state_invalid", "undo actor")
                previous = boundaries.get(actor)
                if previous is None or completion["ts"] >= previous[0]:
                    boundaries[actor] = (completion["ts"], op_id)
        retained_boundaries = {item[1] for item in boundaries.values()}
        result = []
        for op_id, (items, version) in grouped.items():
            op = {item["type"]: item for item in items}
            times = [_time(item["ts"]) for item in items]
            roots = ()
            if len(op) != len(items) or "pending" not in op or ("done" in op and "closed" in op):
                roots = (RootKind.OPEN_AUDIT,)
            elif "undone" in op:
                reverse = op["undone"]["reverse_results"]
                count, failed = reverse.get("ok_count"), reverse.get("fail_count", 0)
                if ("done" not in op or type(count) is not int or count <= 0 or type(failed) is not int
                        or failed != 0 or reverse.get("failed")):
                    roots = (RootKind.OPEN_AUDIT,)
            elif op.get("closed", {}).get("reason") == "invalid_execution_receipt":
                roots = (RootKind.OPEN_AUDIT,)
            elif "closed" not in op:
                roots = ((RootKind.ADMITTED_ROLLBACKABLE,) if "done" in op else (RootKind.IN_PROGRESS_JOB,))
            if not roots and op_id in retained_boundaries:
                roots = (RootKind.OPEN_AUDIT,)
            result.append(JournalEntry(OwnerObject(self.identity(op_id), version,
                NodeState.OPEN if roots else NodeState.CLOSED, _iso(min(times)),
                None if roots else _iso(max(times) + timedelta(days=90)), roots=roots), tuple(items)))
        return tuple(result)
