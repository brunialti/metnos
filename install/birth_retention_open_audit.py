"""Native review/filter journals whose producers provide no closure transition.

Reuse physical JSONL custody, versions and copy references. Neither a successful
review nor a filtered proposal closes its administrative history.
"""
import math
import re

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_evaluation_audit import _utc_text
from install.birth_retention_jsonl import JournalEntry, _JsonlOwner, _time
from install.birth_retention_maintenance import OwnerObject, _digest
from install.birth_retention_rotated_jsonl import _RotatedJournalOwner
from install.birth_retention_sqlite import _iso


class _OpenJournal(_JsonlOwner):
    node_type = 'audit_segment'

    def _entries(self, custody, info, lines, records):
        return tuple(JournalEntry(OwnerObject(
            self.identity(key), version, NodeState.OPEN,
            _iso(self._instant(items[0])), None, roots=(self.root_kind,),
        ), items) for key, (items, version) in self._groups(custody, info, lines, records).items())

    def delete(self, identity, expected_version):
        self._identity(identity)
        raise RetentionError('retention_owner_state_invalid', 'audit lacks native closure')


_REVIEW_NAME = re.compile(r'promoter_review_([0-9]{4}-[0-9]{2}-[0-9]{2})_([0-9a-f]{12})'
                          r'\.jsonl(?:\.(?:[1-9][0-9]*|[0-9]+_[0-9a-f]{32}))?')
_ACTIONS = {
    ('promoted_grace', 'confirm'): 'finalized',
    ('promoted_grace', 'rollback'): 'rolled_back',
    ('review_needed', 'promote_now'): 'marked_pending_for_repromote',
    ('review_needed', 'archive'): 'archived_from_review',
    ('archived', 'confirm_archive'): 'archive_confirmed',
    ('archived', 'resurrect'): 'resurrected_to_review',
}


def _review_session(name):
    match = _REVIEW_NAME.fullmatch(name)
    if match is None:
        raise ValueError('unknown promoter review segment')
    _utc_text(match[1], '%Y-%m-%d')
    return match[2]


class _PromoterReviewJournal(_OpenJournal):
    name = 'promoter_review_audit'
    root_kind = RootKind.OPEN_AUDIT

    def _instant(self, record):
        return _utc_text(record['ts'])

    def _record_key(self, record):
        common = {'ts', 'session_id', 'action'}
        summary = common | {'applied', 'skipped', 'failed', 'by_action', 'total_decisions'}
        decision = common | {'proposal_id', 'state', 'choice', 'ok'}
        if set(record) not in (summary, decision, decision | {'error'}):
            raise ValueError('unknown promoter review schema')
        self._instant(record)
        if record['session_id'] != _review_session(self.path.name):
            raise ValueError('promoter review session binding')
        if set(record) == summary:
            if (record['action'] != 'review_session_summary'
                    or any(type(record[key]) is not int or record[key] < 0
                           for key in ('applied', 'skipped', 'failed', 'total_decisions'))
                    or type(record['by_action']) is not dict
                    or any(key not in _ACTIONS.values() or type(value) is not int or value < 1
                           for key, value in record['by_action'].items())
                    or record['applied'] + record['skipped'] + record['failed'] > record['total_decisions']):
                raise ValueError('promoter review summary')
        else:
            if (any(type(record[key]) is not str for key in ('proposal_id', 'state', 'choice', 'action'))
                    or not record['proposal_id'] or record['state'] not in
                       {'promoted_grace', 'review_needed', 'archived'}
                    or type(record['ok']) is not bool
                    or ('error' in record and record['error'] is not None and type(record['error']) is not str)):
                raise ValueError('promoter review decision')
            action = record['action']
            if action in {'unknown_choice', 'exception', 'no_action_for_state_value'}:
                if record['ok']:
                    raise ValueError('successful unhandled promoter review')
                if action != 'unknown_choice' and (record['state'], record['choice']) not in _ACTIONS:
                    raise ValueError('promoter review choice')
            elif _ACTIONS.get((record['state'], record['choice'])) != action:
                raise ValueError('promoter review action')
        # The producer reads the filename date and event time separately.
        return _digest(record)


class _PromoterReviewAuditOwner(_RotatedJournalOwner):
    journal_type = _PromoterReviewJournal

    def __init__(self, *, root, require_exclusion, owner):
        super().__init__(path=root / 'promoter_review_', require_exclusion=require_exclusion, owner=owner)

    def _matches_name(self, name):
        return name.startswith('promoter_review_')

    def _journal(self, path):
        try:
            if path.parent != self.current.path.parent:
                raise ValueError('foreign promoter review directory')
            _review_session(path.name)
        except ValueError as exc:
            raise RetentionError('retention_inventory_incomplete', 'unknown promoter review segment') from exc
        return self.journal_type(path=path, require_exclusion=self.require_exclusion, owner=self.current.owner)


class _TelosFilteredJournal(_OpenJournal):
    name = 'telos_filtered'
    root_kind = RootKind.OPEN_REVISION

    def _instant(self, record):
        return _time(record['ts'])

    def _record_key(self, record):
        if set(record) != {'ts', 'prop_id', 'by', 'executor_target', 'expected_alignment',
                           'gate_score', 'hard_gate', 'name_status', 'lens', 'convergence_count'}:
            raise ValueError('unknown Telos filtered schema')
        self._instant(record)
        if (any(type(record[key]) is not str for key in
                ('prop_id', 'by', 'executor_target', 'name_status', 'lens'))
                or any(type(record[key]) not in (int, float) or not math.isfinite(record[key])
                       for key in ('expected_alignment', 'gate_score', 'hard_gate'))
                or type(record['convergence_count']) is not int):
            raise ValueError('Telos filtered observation')
        return _digest(record)


class _TelosFilteredOwner(_RotatedJournalOwner):
    journal_type = _TelosFilteredJournal

    def __init__(self, *, path, require_exclusion, owner):
        if path.name != 'telos_filtered.jsonl':
            raise RetentionError('retention_owner_invalid', 'Telos filtered filename')
        super().__init__(path=path, require_exclusion=require_exclusion, owner=owner)
