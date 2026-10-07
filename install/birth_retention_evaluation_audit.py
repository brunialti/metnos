"""Completed evaluator/Stage6 observations, never admission recovery evidence.

Callers consume returned verdicts; these journals contain descriptive labels,
not references used to resume a proposal. Unknown schemas remain uncollectible.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import math
import re

from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_jsonl import JournalEntry, _JsonlOwner
from install.birth_retention_maintenance import OwnerObject, _digest
from install.birth_retention_rotated_jsonl import _RotatedJournalOwner
from install.birth_retention_sqlite import _iso


def _utc_text(value, pattern='%Y-%m-%dT%H:%M:%SZ'):
    if type(value) is not str:
        raise ValueError('audit timestamp')
    instant = datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
    if instant.strftime(pattern) != value:
        raise ValueError('audit timestamp canonical form')
    try:
        instant + timedelta(days=90)
    except OverflowError as exc:
        raise ValueError('audit timestamp range') from exc
    return instant


class _CompletedJournal(_JsonlOwner):
    node_type = 'audit_segment'

    def _entries(self, custody, info, lines, records):
        return tuple(JournalEntry(OwnerObject(
            self.identity(key), version, NodeState.CLOSED,
            _iso(self._instant(items[0])), _iso(self._instant(items[0]) + timedelta(days=90)),
        ), items) for key, (items, version) in self._groups(custody, info, lines, records).items())


class _EvaluationJournal(_CompletedJournal):
    name = 'proposal_evaluation_audit'

    def _instant(self, record):
        value = record['ts']
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('evaluation timestamp')
        try:
            instant = datetime.fromtimestamp(value, timezone.utc)
            instant + timedelta(days=90)
            return instant
        except (ValueError, OverflowError, OSError) as exc:
            raise ValueError('evaluation timestamp range') from exc

    def _record_key(self, record):
        if set(record) != {'ts', 'proposal_id', 'name', 'verdict', 'score', 'killers', 'rationale'}:
            raise ValueError('unknown evaluation schema')
        self._instant(record)
        if (any(type(record[key]) is not str or not record[key] for key in ('proposal_id', 'name'))
                or record['verdict'] not in ('accept', 'gray', 'reject')
                or type(record['score']) not in (int, float) or not math.isfinite(record['score'])
                or type(record['rationale']) is not str or type(record['killers']) is not list
                or any(type(item) is not str for item in record['killers'])):
            raise ValueError('evaluation observation')
        return _digest(record)


class _ProposalEvaluationAuditOwner(_RotatedJournalOwner):
    journal_type = _EvaluationJournal


_VERIFY_NAME = re.compile(r'verify_([0-9]{8}T[0-9]{6}Z)_([0-9a-f]{8})\.jsonl(?:\.(?:[1-9][0-9]*|[0-9]+_[0-9a-f]{32}))?')


def _verify_name(name):
    match = _VERIFY_NAME.fullmatch(name)
    if match is None:
        raise ValueError('unknown semantic audit segment')
    _utc_text(match[1], '%Y%m%dT%H%M%SZ')
    return match[2]


class _SemanticVerificationJournal(_CompletedJournal):
    name = 'semantic_verification_audit'

    def _instant(self, record):
        return _utc_text(record['ts'])

    def _record_key(self, record):
        from synt_stage6_verify import validate_stage6_verdict
        if set(record) != {'ts', 'name_hint', 'models', 'prompt_len', 'response_len', 'verdict'}:
            raise ValueError('unknown semantic audit schema')
        self._instant(record)
        if (type(record['name_hint']) is not str or type(record['models']) is not list
                or not record['models'] or any(type(item) is not str or not item for item in record['models'])
                or any(type(record[key]) is not int or record[key] < 0 for key in ('prompt_len', 'response_len'))
                or type(record['verdict']) is not dict or set(record['verdict']) != {'aligned', 'mismatch'}):
            raise ValueError('semantic audit observation')
        if validate_stage6_verdict(record['verdict']) != record['verdict']:
            raise ValueError('semantic audit verdict')
        expected = hashlib.sha256(record['name_hint'].encode('utf-8', errors='replace')).hexdigest()[:8]
        if _verify_name(self.path.name) != expected:
            raise ValueError('semantic audit filename binding')
        # Filename and record timestamps are separate native clock reads.
        return _digest(record)


class _SemanticVerificationAuditOwner(_RotatedJournalOwner):
    journal_type = _SemanticVerificationJournal

    def _matches_name(self, name):
        return name.startswith('verify_')

    def _journal(self, path):
        try:
            if path.parent != self.current.path.parent:
                raise ValueError('foreign semantic audit directory')
            _verify_name(path.name)
        except ValueError as exc:
            raise RetentionError('retention_inventory_incomplete', 'unknown semantic audit segment') from exc
        return self.journal_type(path=path, require_exclusion=self.require_exclusion, owner=self.current.owner)
