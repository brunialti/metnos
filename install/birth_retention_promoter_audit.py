"""Completed daily promoter observations; mutable promotion state is elsewhere.

Recovery consumes the native database and blobs, never these daily journals.
Closing an observation does not close its named proposal, catalog or backup.
"""
import math
import re

from executor_birth_retention import RetentionError
from install.birth_retention_evaluation_audit import _CompletedJournal, _utc_text
from install.birth_retention_maintenance import _digest
from install.birth_retention_rotated_jsonl import _RotatedJournalOwner
from install.birth_retention_sqlite import _utc

_NAME = re.compile(r'promoter_(\d{4}-\d{2}-\d{2})\.jsonl(?:\.(?:[1-9][0-9]*|[0-9]+_[0-9a-f]{32}))?')
_BASE = {'ts', 'proposal_id', 'name', 'dry_run', 'action'}
_EVALUATED = _BASE | {'verdict', 'evaluator_score', 'killers', 'example'}
_SCHEMAS = {
    'promoted_finalized_via_grace_expiry': ({'ts', 'proposal_id', 'action'},),
    'skipped': (_BASE | {'reason'}, {'ts', 'proposal_id', 'action', 'reason'}),
    'evaluator_crash': (_BASE | {'error'},),
    'would_promote': (_EVALUATED,), 'would_review_needed': (_EVALUATED,),
    'would_archive': (_EVALUATED,), 'review_needed': (_EVALUATED,),
    'archived': (_EVALUATED | {'archived_path'},),
    'promote_failed_admission': (_EVALUATED | {'error', 'error_detail'},),
    'promote_failed_transient': (_EVALUATED | {'error', 'error_detail'},),
    'promoted_grace': (_EVALUATED | {'grace_until', 'blob_path', 'catalog_path'},),
    'promoted_finalized': (_EVALUATED | {'grace_until', 'blob_path', 'catalog_path'},),
    'rolled_back': ({'ts', 'proposal_id', 'name', 'action', 'restored_path', 'rolled_back_blob', 'prev_state'},),
    'notify_skipped': ({'ts', 'proposal_id', 'action', 'reason'},),
    'notify_skipped_aggregated': ({'ts', 'proposal_id', 'action', 'reason'},),
    'notify_failed': ({'ts', 'proposal_id', 'action', 'error'},),
    'notify_failed_aggregated': ({'ts', 'action', 'error'},),
    'notified': ({'ts', 'proposal_id', 'action', 'recipient', 'chunks'},),
    'notified_aggregated': ({'ts', 'action', 'recipient', 'n_total', 'n_grace', 'n_review', 'n_archived'},),
}


def _check_name(name):
    match = _NAME.fullmatch(name)
    if match is None:
        raise ValueError('promoter daily namespace')
    _utc_text(match[1], '%Y-%m-%d')


class _PromoterAuditJournal(_CompletedJournal):
    name = 'promoter_daily_audit'

    def _instant(self, record):
        return _utc_text(record['ts'])

    def _record_key(self, record):
        action = record.get('action')
        if type(action) is not str or set(record) not in _SCHEMAS.get(action, ()):
            raise ValueError('promoter observation schema')
        self._instant(record)
        for key in ('proposal_id', 'name', 'recipient', 'blob_path', 'catalog_path', 'restored_path', 'rolled_back_blob'):
            if key in record and (type(record[key]) is not str or not record[key]):
                raise ValueError('promoter observation text')
        for key in ('reason', 'error', 'error_detail', 'example'):
            if key in record and type(record[key]) is not str:
                raise ValueError('promoter observation detail')
        if 'dry_run' in record and type(record['dry_run']) is not bool:
            raise ValueError('promoter dry run')
        if 'verdict' in record:
            if (record['verdict'] not in {'accept', 'gray', 'reject'}
                    or type(record['evaluator_score']) not in (int, float)
                    or not math.isfinite(record['evaluator_score'])
                    or type(record['killers']) is not list
                    or any(type(value) is not str for value in record['killers'])):
                raise ValueError('promoter evaluation')
        if 'archived_path' in record and record['archived_path'] is not None and type(record['archived_path']) is not str:
            raise ValueError('promoter archive path')
        if 'grace_until' in record:
            if action == 'promoted_finalized' and record['grace_until'] == '':
                pass
            else:
                _utc(record['grace_until'])
        if 'prev_state' in record and record['prev_state'] not in {'promoted_grace', 'promoted_finalized'}:
            raise ValueError('promoter previous state')
        for key in ('chunks', 'n_total', 'n_grace', 'n_review', 'n_archived'):
            if key in record and (type(record[key]) is not int or record[key] < (1 if key == 'chunks' else 0)):
                raise ValueError('promoter notification count')
        return _digest(record)


class _PromoterDailyAuditOwner(_RotatedJournalOwner):
    journal_type = _PromoterAuditJournal

    def _matches_name(self, name):
        return name.startswith('promoter_') and not name.startswith('promoter_review_')

    def _journal(self, path):
        try:
            if path.parent != self.current.path.parent:
                raise ValueError('foreign promoter daily directory')
            _check_name(path.name)
        except ValueError as exc:
            raise RetentionError('retention_inventory_incomplete', 'unknown promoter daily segment') from exc
        return self.journal_type(path=path, require_exclusion=self.require_exclusion, owner=self.current.owner)
