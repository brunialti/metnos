"""Import observations, preserving the native last physical status per skill.

Bindings are read from the live catalog, never reconstructed from this log.
The CLI status reader does use the last row in imports.jsonl: it remains a
root even after its retention window. Older observations can be compacted.
"""
from datetime import datetime, timedelta, timezone
import re

from executor_birth_retention import NodeState, RootKind
from install.birth_retention_jsonl import JournalEntry, _JsonlOwner
from install.birth_retention_maintenance import OwnerObject, _digest
from install.birth_retention_rotated_jsonl import _RotatedJournalOwner
from install.birth_retention_sqlite import _iso


def _timestamp(record):
    value = record.get('ts')
    if type(value) is not str:
        raise ValueError('import timestamp')
    result = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    if result.strftime('%Y-%m-%dT%H:%M:%SZ') != value:
        raise ValueError('import timestamp canonical form')
    try:
        result + timedelta(days=90)
    except OverflowError as exc:
        raise ValueError('import retention timestamp range') from exc
    return result


def _strings(value):
    return type(value) is list and all(type(item) is str for item in value)


class _ImportAuditJournal(_JsonlOwner):
    name = 'import_audit'
    node_type = 'audit_segment'

    def _record_key(self, record):
        common = {'ts', 'skill', 'accepted', 'rejected'}
        _timestamp(record)
        if type(record.get('skill')) is not str or not record['skill'] or not _strings(record.get('accepted')):
            raise ValueError('import skill or accepted plans')
        if set(record) == common | {'version', 'plans', 'translator_rejected'}:
            if (type(record['version']) is not str or type(record['plans']) is not int
                    or record['plans'] < 0 or type(record['translator_rejected']) is not list):
                raise ValueError('import CLI observation')
            for item in record['translator_rejected']:
                if (type(item) is not dict or set(item) != {'domain', 'action', 'reason'}
                        or any(type(value) is not str for value in item.values())):
                    raise ValueError('translator rejection')
            key = 'plan'
        elif set(record) == common | {'skill_source_sha256', 'binding', 'smoke_cases'}:
            if (record['binding'] != record['skill']
                    or type(record['skill_source_sha256']) is not str
                    or re.fullmatch('[0-9a-f]{64}', record['skill_source_sha256']) is None
                    or type(record['smoke_cases']) is not list
                    or len(record['smoke_cases']) != len(record['accepted'])):
                raise ValueError('import admission observation')
            for case in record['smoke_cases']:
                if type(case) is not dict:
                    raise ValueError('smoke observation')
                if set(case) == {'_no_smoke', '_reason'}:
                    if case['_no_smoke'] is not True or type(case['_reason']) is not str:
                        raise ValueError('smoke absence')
                elif set(case) == {'query', 'expected_first_tool', 'expected_arg_keys', 'min_pass_rate', 'note'}:
                    if (any(type(case[k]) is not str for k in ('query', 'expected_first_tool', 'note'))
                            or not _strings(case['expected_arg_keys']) or type(case['min_pass_rate']) is not float
                            or case['min_pass_rate'] != 0.9):
                        raise ValueError('smoke observation schema')
                else:
                    raise ValueError('unknown smoke observation')
            key = 'name'
        else:
            raise ValueError('unknown import observation schema')
        if type(record['rejected']) is not list:
            raise ValueError('rejected plans')
        for item in record['rejected']:
            if (type(item) is not dict or set(item) != {key, 'reasons'}
                    or type(item[key]) is not str or not _strings(item['reasons'])):
                raise ValueError('rejection schema')
        return _digest(record)

    def _entries(self, custody, info, lines, records):
        # Native status uses physical order, not timestamp sorting.
        latest = {record['skill']: self._record_key(record) for record in records}
        protected = set(latest.values())
        entries = []
        for key, (items, version) in self._groups(custody, info, lines, records).items():
            retained = key in protected
            created = _timestamp(items[0])
            entries.append(JournalEntry(OwnerObject(self.identity(key), version,
                NodeState.OPEN if retained else NodeState.CLOSED, _iso(created),
                None if retained else _iso(created + timedelta(days=90)),
                roots=(RootKind.OPEN_AUDIT,) if retained else ()), items))
        return tuple(entries)


class _ImportAuditOwner(_RotatedJournalOwner):
    journal_type = _ImportAuditJournal
