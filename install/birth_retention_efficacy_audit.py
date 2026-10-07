"""Completed efficacy/feedback observations written after the native DB commit.

Executor lifecycle remains in executor_stats. This journal has no runtime reader
for restoration or approval; executor names are labels, not recovery references.
Unknown schemas require classification before entering retention.
"""
from datetime import timedelta
import math

from executor_birth_retention import NodeState
from install.birth_retention_jsonl import JournalEntry, _JsonlOwner
from install.birth_retention_maintenance import OwnerObject, _digest
from install.birth_retention_rotated_jsonl import _RotatedJournalOwner
from install.birth_retention_sqlite import _iso, _utc


class _EfficacyAuditJournal(_JsonlOwner):
    name = 'efficacy_audit'
    node_type = 'audit_segment'

    def _record_key(self, record):
        common = {'ts', 'event', 'name'}
        _utc(record.get('ts'))
        if type(record.get('name')) is not str or not record['name']:
            raise ValueError('efficacy executor label')
        if set(record) == common | {'by', 'consecutive_errors'}:
            if (record['event'] != 'deprecated' or record['by'] != 'feedback_ager'
                    or type(record['consecutive_errors']) is not int
                    or record['consecutive_errors'] < 0):
                raise ValueError('feedback observation')
        elif set(record) == common | {'success_rate', 'total_invocations', 'ok_invocations'}:
            total, ok, rate = (record[key] for key in ('total_invocations', 'ok_invocations', 'success_rate'))
            if (record['event'] not in ('deprecated', 'archived')
                    or type(total) is not int or type(ok) is not int
                    or not 0 <= ok <= total or total <= 0
                    or type(rate) not in (int, float) or not math.isfinite(rate)
                    or rate != round(ok / total, 3)):
                raise ValueError('efficacy observation')
        else:
            raise ValueError('unknown efficacy observation schema')
        return _digest(record)

    def _entries(self, custody, info, lines, records):
        return tuple(JournalEntry(OwnerObject(
            self.identity(key), version, NodeState.CLOSED,
            _iso(_utc(items[0]['ts'])), _iso(_utc(items[0]['ts']) + timedelta(days=90)),
        ), items) for key, (items, version) in self._groups(custody, info, lines, records).items())


class _EfficacyAuditOwner(_RotatedJournalOwner):
    journal_type = _EfficacyAuditJournal
