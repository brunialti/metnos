"""Completed native LLM metering; shared F6 compaction, no cleanup entrypoint."""
from datetime import datetime, timedelta
import math

from executor_birth_retention import NodeState
from install.birth_retention_rotated_jsonl import _RotatedJournalOwner
from install.birth_retention_jsonl import JournalEntry, _JsonlOwner, _time
from install.birth_retention_maintenance import OwnerObject, _digest
from install.birth_retention_sqlite import _iso


class _LlmCostJournal(_JsonlOwner):
    name = "llm_cost"
    node_type = "audit_segment"

    def _record_key(self, record):
        # These records are emitted after a call. Unlike workflow audits, they
        # contain no live operation references. An extension needs review here.
        if set(record) != {"ts", "ts_iso", "provider", "model", "kind", "tier",
                           "in_tokens", "out_tokens", "cost_usd", "latency_ms"}:
            raise ValueError("unknown metering schema")
        timestamp = _time(record["ts"])
        iso = datetime.fromisoformat(record["ts_iso"])
        if iso.tzinfo is None or abs((timestamp - iso).total_seconds()) > .001:
            raise ValueError("inconsistent metering time")
        for field in ("provider", "model", "kind", "tier"):
            if record[field] is not None and type(record[field]) is not str:
                raise ValueError("metering label")
        for field in ("in_tokens", "out_tokens"):
            if type(record[field]) is not int or record[field] < 0:
                raise ValueError("metering tokens")
        for field in ("cost_usd", "latency_ms"):
            value = record[field]
            if value is not None and (type(value) not in (int, float)
                                      or not math.isfinite(value) or value < 0):
                raise ValueError("metering quantity")
        return _digest(record)

    def _entries(self, custody, info, lines, records):
        return tuple(JournalEntry(OwnerObject(
            self.identity(key), version, NodeState.CLOSED,
            _iso(_time(items[0]["ts"])),
            _iso(_time(items[0]["ts"]) + timedelta(days=90)),
        ), items) for key, (items, version) in self._groups(custody, info, lines, records).items())


class _LlmCostOwner(_RotatedJournalOwner):
    journal_type = _LlmCostJournal
