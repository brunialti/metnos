"""Root-held review records remain historical evidence, never fresh consent."""
from dataclasses import replace
import base64
import json
import os
from types import SimpleNamespace

import pytest

from executor_birth_retention import NodeState, RetentionError, RootKind
from executor_birth_semantic_authority import _canonical
from install import birth_retention_reviews as module
from install.synth_review import _digest

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX administrative custody')


@pytest.fixture
def review(tmp_path):
    root = tmp_path / 'reviews'
    root.mkdir(mode=0o700)
    owner = module._ReviewOwner(root=root, require_exclusion=lambda: None, owner=None)
    files = {name: base64.b64encode(data).decode() for name, data in {
        'manifest.toml': b'name="compute_sample"\n', 'manifest.lang_state.json': b'{}',
        'compute_sample.py': b'def run(args): return args\n'}.items()}
    record = dict(schema_version=1, proposal=dict(files=files, contract_id='user:compute_sample/manifest.toml',
        producer='promoter', reason='native retained review'), human_cases=[], tests=[],
        subject=dict(candidate_id='sha256:' + 'a' * 64, semantic_core_id='sha256:' + 'b' * 64,
            admission_context_id='sha256:' + 'c' * 64, lifecycle='promotion',
            expires_at='2020-01-01T00:00:00Z'), token='native-approval-token',
        created_at='2019-12-31T00:00:00Z', name='compute_sample', description={},
        capabilities={}, execution={})
    def write(data=None):
        data = record if data is None else data
        path = root / (_digest(data)[7:] + '.json')
        path.write_bytes(_canonical(data))
        path.chmod(0o600)
        return path
    return SimpleNamespace(**locals())


def test_expired_review_preserves_original_bytes_and_remains_open(review):
    path = review.write()
    before = path.read_bytes()
    identity = review.owner.identity(path.name)
    obj, record, size = review.owner.scan()[identity]
    assert record == review.record and size == len(before)
    assert obj.state is NodeState.OPEN and obj.eligible_after is None
    assert obj.roots == (RootKind.OPEN_AUDIT,)
    assert review.owner.inventory() == (obj,)
    assert review.owner.version(identity) == obj.version
    with pytest.raises(RetentionError, match='native audit closure'):
        review.owner.delete(identity, obj.version)
    assert path.read_bytes() == before


@pytest.mark.parametrize('fault', ['digest', 'partial', 'duplicate', 'nonfinite', 'schema', 'subject', 'type', 'origin', 'files', 'base64'])
def test_invalid_native_record_blocks_inventory(review, fault):
    data = json.loads(json.dumps(review.record))
    if fault == 'schema':
        data['invented'] = True
    elif fault == 'subject':
        data['subject']['candidate_id'] = 'not-a-digest'
    elif fault == 'type':
        data['schema_version'] = True
    elif fault == 'origin':
        data['proposal']['contract_id'] = 'builtin:compute_sample/manifest.toml'
    elif fault == 'files':
        data['proposal']['files'] = {}
    elif fault == 'base64':
        data['proposal']['files']['manifest.toml'] = 'not/base64!'
    path = review.write(data)
    if fault == 'digest':
        data['token'] = 'different'
        path.write_bytes(_canonical(data))
    elif fault == 'partial':
        path.write_bytes(b'{')
    elif fault == 'duplicate':
        path.write_bytes(b'{"schema_version":1,' + path.read_bytes()[1:])
    elif fault == 'nonfinite':
        path.write_bytes(b'{"schema_version":NaN}')
    with pytest.raises(RetentionError):
        review.owner.inventory()


@pytest.mark.parametrize('fault', ['mode', 'hardlink', 'symlink', 'directory_mode', 'unknown_entry'])
def test_unsafe_physical_records_are_not_ignored(review, tmp_path, fault):
    path = review.write()
    if fault == 'mode':
        path.chmod(0o644)
    elif fault == 'hardlink':
        os.link(path, tmp_path / 'outside-copy')
    elif fault == 'symlink':
        target = tmp_path / 'outside'
        path.rename(target)
        path.symlink_to(target)
    elif fault == 'directory_mode':
        review.root.chmod(0o755)
    else:
        (review.root / 'unfinished.tmp').write_text('partial')
    with pytest.raises(RetentionError):
        review.owner.scan()


@pytest.mark.parametrize('budget', ['file_bytes', 'aggregate_bytes', 'files', 'time'])
def test_scan_refuses_budget_exhaustion(review, monkeypatch, budget):
    path = review.write()
    if budget == 'file_bytes':
        path.write_bytes(b' ' * ((4 << 20) + 1))
    elif budget == 'aggregate_bytes':
        monkeypatch.setattr(module, '_MAX_TOTAL', path.stat().st_size - 1)
    elif budget == 'files':
        monkeypatch.setattr(module, '_MAX_FILES', 0)
    else:
        monkeypatch.setattr(module, '_SECONDS', -1)
    with pytest.raises(RetentionError):
        review.owner.scan()


def test_changed_second_read_invalidates_inventory(review, monkeypatch):
    path = review.write()
    original, reads = review.owner.scan, []
    def scan():
        reads.append(1)
        if len(reads) == 2:
            os.utime(path, ns=(1_000_000_000, 1_000_000_000))
        return original()
    monkeypatch.setattr(review.owner, 'scan', scan)
    with pytest.raises(RetentionError, match='inventory changed'):
        review.owner.inventory()


def test_missing_directory_is_empty_without_creating_it(tmp_path):
    root = tmp_path / 'absent'
    owner = module._ReviewOwner(root=root, require_exclusion=lambda: None, owner=None)
    assert owner.inventory() == () and not root.exists()
    assert owner.version(owner.identity('a' * 64 + '.json')) is None


def test_identity_and_root_owner_cannot_be_substituted(review):
    path = review.write()
    identity = review.owner.identity(path.name)
    with pytest.raises(RetentionError, match='foreign'):
        review.owner.version(replace(identity, owner='another-owner'))
    with pytest.raises(RetentionError, match='root custody'):
        module._ReviewOwner(root=review.root, require_exclusion=lambda: None, owner=(1, 1))
    with pytest.raises(RetentionError):
        review.owner.identity('../' + path.name)
