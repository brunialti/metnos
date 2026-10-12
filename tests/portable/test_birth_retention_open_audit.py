"""Actual review/filter writers retain their open native administrative history."""
import json
import os
from types import SimpleNamespace

import pytest

from admin import promotions_review
from jobs import promoter_state, promoter_rollback
import proposal_actions
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_open_audit import _PromoterReviewAuditOwner, _TelosFilteredOwner
from test_birth_retention_llm_cost import selected


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX audit custody')
OLD = '2020-01-01T00:00:00Z'


@pytest.fixture
def native(tmp_path, monkeypatch):
    audit = tmp_path / 'synth_audit'; audit.mkdir(mode=0o700)
    data = tmp_path / 'data'; data.mkdir(mode=0o700)
    filtered = data / 'telos_filtered.jsonl'
    allowed = [True]

    def guard():
        if not allowed[0]:
            raise RetentionError('retention_invalid', 'lost exclusion')

    monkeypatch.setattr(promotions_review, '_audit_dir', lambda: audit)
    monkeypatch.setattr(promotions_review, '_now_iso', lambda: OLD)
    monkeypatch.setattr(promoter_state, 'mark_finalized', lambda pid: True)
    monkeypatch.setattr(promoter_state, 'archive_review_needed', lambda pid: False)
    monkeypatch.setattr(promoter_state, 'resurrect_from_archive', lambda pid: True)
    monkeypatch.setattr(promoter_rollback, 'rollback_promotion', lambda pid: {'ok': False, 'error': None})
    monkeypatch.setattr(promotions_review, '_mark_review_to_pending', lambda pid: True)
    monkeypatch.setattr(proposal_actions, 'TELOS_FILTERED_LOG', filtered)
    review_owner = _PromoterReviewAuditOwner(root=audit, require_exclusion=guard, owner=None)
    filtered_owner = _TelosFilteredOwner(path=filtered, require_exclusion=guard, owner=None)

    def review(values=None):
        result = promotions_review.apply_review_decisions(values or {})
        return audit / os.path.basename(result['audit_path'])

    def filter_proposal(**changes):
        proposal = dict(prop_id='native-proposal', executor_target='sample',
                        expected_alignment=.2, name_status='existing', lens='extend', convergence_count=2)
        proposal.update(changes)
        proposal_actions._audit_filtered(proposal, {'ts': 1577836800.0, 'by': 'operator'}, .3, .5)
        return filtered

    previous = os.umask(0o022)
    try:
        yield SimpleNamespace(**locals())
    finally:
        os.umask(previous)


def test_actual_review_decisions_remain_open_including_errors_and_skips(native, tmp_path):
    path = native.review({
        'promoted_grace__confirmed': 'confirm',
        'promoted_grace__reverted': 'rollback',
        'review_needed__pending': 'promote_now',
        'review_needed__archived': 'archive',
        'archived__confirmed': 'confirm_archive',
        'archived__resurrected': 'resurrect',
        'archived__skipped': 'skip',
        'archived__unknown': 'unknown value',
        'ignored': 'confirm',
    })
    objects = native.review_owner.inventory()
    assert len(objects) == 8
    assert all(obj.state is NodeState.OPEN and obj.eligible_after is None
               and obj.roots == (RootKind.OPEN_AUDIT,) for obj in objects)
    assert selected(native.review_owner, tmp_path) == ()
    original = path.read_bytes()
    for obj in objects:
        assert native.review_owner.version(obj.identity) == obj.version
        with pytest.raises(RetentionError, match='lacks native closure'):
            native.review_owner.delete(obj.identity, obj.version)
    assert path.read_bytes() == original


def test_empty_review_and_native_exception_are_retained(native, monkeypatch):
    def fail(pid):
        raise RuntimeError('native action failed')
    monkeypatch.setattr(promoter_state, 'mark_finalized', fail)
    native.review()
    native.review({'promoted_grace__sample': 'confirm'})
    assert len(native.review_owner.inventory()) == 3


def test_actual_filtered_record_retained_as_open_revision(native, tmp_path):
    path = native.filter_proposal()
    obj, = native.filtered_owner.inventory()
    assert obj.state is NodeState.OPEN and obj.eligible_after is None
    assert obj.roots == (RootKind.OPEN_REVISION,)
    assert selected(native.filtered_owner, tmp_path) == ()
    before = path.read_bytes()
    assert native.filtered_owner.version(obj.identity) == obj.version
    with pytest.raises(RetentionError, match='lacks native closure'):
        native.filtered_owner.delete(obj.identity, obj.version)
    assert path.read_bytes() == before


@pytest.mark.parametrize('kind', ['review', 'filtered'])
def test_rotated_copies_keep_exact_native_records_linked(native, kind):
    path = native.review({'archived__sample': 'confirm_archive'}) if kind == 'review' else native.filter_proposal()
    owner = native.review_owner if kind == 'review' else native.filtered_owner
    copy = path.with_name(path.name + '.1')
    copy.write_bytes(path.read_bytes())
    objects = owner.inventory()
    assert len(objects) == (4 if kind == 'review' else 2)
    assert all(len(obj.references) == 1 and obj.state is NodeState.OPEN for obj in objects)


@pytest.mark.parametrize('fault', ['session', 'action', 'extra', 'timestamp', 'summary_bool', 'namespace', 'partial'])
def test_review_format_and_session_must_match_native_writer(native, fault):
    path = native.review({'archived__sample': 'confirm_archive'})
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if fault == 'session': rows[0]['session_id'] = 'f' * 12
    elif fault == 'action': rows[1]['action'] = 'invented_closure'
    elif fault == 'extra': rows[0]['closed'] = True
    elif fault == 'timestamp': rows[0]['ts'] = 'today'
    elif fault == 'summary_bool': rows[0]['failed'] = False
    elif fault == 'namespace': (native.audit / 'promoter_review_unknown.jsonl').write_text('')
    path.write_text('{' if fault == 'partial' else ''.join(json.dumps(row) + '\n' for row in rows))
    with pytest.raises(RetentionError):
        native.review_owner.inventory()


@pytest.mark.parametrize('fault', ['extra', 'timestamp', 'score_bool', 'convergence_bool', 'nan', 'partial'])
def test_filtered_format_must_match_native_writer(native, fault):
    path = native.filter_proposal()
    row = json.loads(path.read_text())
    if fault == 'extra': row['closed'] = True
    elif fault == 'timestamp': row['ts'] = 'today'
    elif fault == 'score_bool': row['gate_score'] = True
    elif fault == 'convergence_bool': row['convergence_count'] = False
    elif fault == 'nan': row['hard_gate'] = float('nan')
    path.write_text('{' if fault == 'partial' else json.dumps(row) + '\n')
    with pytest.raises(RetentionError):
        native.filtered_owner.inventory()


@pytest.mark.parametrize('kind', ['review', 'filtered'])
@pytest.mark.parametrize('fault', ['symlink', 'hardlink', 'mode', 'exclusion'])
def test_native_journal_custody_and_held_phase_remain_required(native, kind, fault):
    path = native.review() if kind == 'review' else native.filter_proposal()
    owner = native.review_owner if kind == 'review' else native.filtered_owner
    if fault == 'symlink':
        outside = path.parent.parent / 'outside.jsonl'
        path.rename(outside)
        path.symlink_to(outside)
    elif fault == 'hardlink': os.link(path, path.parent.parent / 'outside.jsonl')
    elif fault == 'mode': path.chmod(0o664)
    elif fault == 'exclusion': native.allowed[0] = False
    with pytest.raises(RetentionError):
        owner.inventory()
