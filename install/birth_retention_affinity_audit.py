"""Completed loader observations, without revision or recovery obligations.

The native writer emits this schema only after applying catalog rejections.
Executor names and shared vocabulary describe that completed observation; this
journal is never read to restore, approve or execute an operation. Extensions
must be classified explicitly before they can enter retention.
"""
from datetime import datetime, timedelta, timezone
import math

from executor_birth_retention import NodeState
from install.birth_retention_jsonl import JournalEntry, _JsonlOwner
from install.birth_retention_maintenance import OwnerObject, _digest
from install.birth_retention_rotated_jsonl import _RotatedJournalOwner
from install.birth_retention_sqlite import _iso


def _timestamp(record):
    value = record['ts']
    if type(value) is not str:
        raise ValueError('affinity timestamp')
    instant = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    if instant.strftime('%Y-%m-%dT%H:%M:%SZ') != value:
        raise ValueError('affinity timestamp canonical form')
    return instant


class _AffinityAuditJournal(_JsonlOwner):
    name = 'affinity_audit'
    node_type = 'audit_segment'

    def _record_key(self, record):
        if set(record) != {'ts', 'name', 'reason', 'overlapping_with', 'jaccard', 'shared_terms'}:
            raise ValueError('unknown affinity observation schema')
        _timestamp(record)
        if record['reason'] != 'affinity_overlap':
            raise ValueError('unknown affinity observation')
        if any(type(record[name]) is not str or not record[name]
               for name in ('name', 'overlapping_with')) or record['name'] == record['overlapping_with']:
            raise ValueError('affinity executor labels')
        score = record['jaccard']
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('affinity score')
        terms = record['shared_terms']
        if (type(terms) is not list or not terms or any(type(term) is not str for term in terms)
                or terms != sorted(set(terms))):
            raise ValueError('affinity vocabulary')
        return _digest(record)

    def _entries(self, custody, info, lines, records):
        return tuple(JournalEntry(OwnerObject(
            self.identity(key), version, NodeState.CLOSED,
            _iso(_timestamp(items[0])), _iso(_timestamp(items[0]) + timedelta(days=90)),
        ), items) for key, (items, version) in self._groups(custody, info, lines, records).items())


class _AffinityAuditOwner(_RotatedJournalOwner):
    journal_type = _AffinityAuditJournal
