"""Native persistent writers, recovery copies and hostile/unknown namespaces."""
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_synth_candidates import _PersistentSynthOwner
from synth_proposal_store import preserve_candidate, review_document
from synt import Synt, GeneratedProposal


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX synthesis file custody')


@pytest.fixture(autouse=True)
def private_writer_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.fixture
def store(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    (source / 'manifest.toml').write_text('name="example"\n[code]\nfiles=["example.py"]\n')
    (source / 'manifest.lang_state.json').write_text('{}')
    (source / 'example.py').write_text('print("example")\n')
    root = tmp_path / '.synt' / 'proposals'
    owner = _PersistentSynthOwner(proposals=root, require_exclusion=lambda: None, owner=None)
    def retain():
        return preserve_candidate(source, proposals_dir=root, reason='operator request',
            contract_id='user:example/manifest.toml', producer='synt_multistage', error_code='approval_required')
    return SimpleNamespace(**locals())


def test_native_retained_candidate_and_export_keep_exact_storage_identity(store):
    address = store.retain()
    before = {str(p): (p.stat().st_ino, p.stat().st_mtime_ns) for p in store.root.rglob('*')}
    obj, = store.owner.inventory()
    assert obj.identity.local_id == 'proposals/' + address
    assert obj.state is NodeState.OPEN and obj.roots
    assert store.owner.scan()[obj.identity][1] == address
    assert review_document(store.root, address, [])['proposal']['reason'] == 'operator request'
    assert before == {str(p): (p.stat().st_ino, p.stat().st_mtime_ns) for p in store.root.rglob('*')}
    with pytest.raises(RetentionError, match='retention_owner_state_invalid'):
        store.owner.delete(obj.identity, obj.version)


def test_native_reject_move_and_interrupted_copy_keep_recoverable_bytes(store):
    address = store.retain()
    events = []
    service = object.__new__(Synt); service.proposals_dir = store.root
    service.locks = SimpleNamespace(lock=lambda *args: None)
    service.audit = SimpleNamespace(log=events.append)
    assert service.reject_proposal(address)['ok']
    obj, = store.owner.inventory()
    assert obj.identity.local_id == 'rejected/' + address and obj.state is NodeState.OPEN
    shutil.copytree(store.root.parent / 'rejected' / address, store.root / address)
    objects = store.owner.inventory()
    assert len(objects) == 2 and all(len(obj.references) == 1 for obj in objects)
    assert events[0]['proposal_id'] == address


def test_native_legacy_writer_and_approved_copy_remain_audit_inputs(store):
    name = 'a' * 16
    proposal = GeneratedProposal(proposal_id=name, request_id='request-1', name='example',
        description='SCOPO: example. PATTERN: example(). NON: none. OUT: ok.', purpose='example',
        affinity=['example'], python_code='def invoke(args): return {"ok": True}',
        args_schema={'type': 'object', 'properties': {}, 'required': []}, output_summary='ok',
        proposal_dir=store.root / name, llm_provider='test', llm_model='test', llm_in_tokens=0,
        llm_out_tokens=0, llm_latency_ms=0, code_imports=[], non_stdlib_imports=[],
        convention_ok=True, convention_reason='ok')
    Synt._write_proposal_to_disk(object.__new__(Synt), SimpleNamespace(target_intent='example',
        proto_mnest=None, capability_hint=[]), proposal)
    obj, = store.owner.inventory()
    assert obj.state is NodeState.OPEN
    archived = store.root.parent / 'approved' / name
    archived.parent.mkdir(); shutil.move(str(proposal.proposal_dir), str(archived))
    obj, = store.owner.inventory()
    assert obj.identity.local_id == 'approved/' + name and obj.roots


@pytest.mark.parametrize('name,relative', [('.pending-abcdefgh', 'candidate/example.py'),
    ('_failed_' + 'b' * 16, 'raw_code.py'), ('c' * 16, 'example.py')])
def test_crashed_native_sequential_writes_remain_open(store, name, relative):
    root = store.root / name
    path = root / relative; path.parent.mkdir(parents=True); path.write_text('partial')
    obj, = store.owner.inventory()
    assert obj.state is NodeState.OPEN and obj.roots


@pytest.mark.parametrize('fault', ['bytes', 'metadata', 'extra', 'symlink', 'hardlink', 'writable', 'unknown', 'pending_unknown'])
def test_substitution_and_unknown_namespace_fail_closed(store, fault):
    address = store.retain(); root = store.root / address
    code = root / 'candidate' / 'example.py'
    if fault == 'bytes':
        code.chmod(0o600); code.write_text('changed')
    elif fault == 'metadata':
        path = root / 'proposal.json'; value = json.loads(path.read_text()); value['reason'] = 'changed'; path.write_text(json.dumps(value))
    elif fault == 'extra':
        (root / 'extra').write_text('x')
    elif fault == 'symlink':
        code.parent.chmod(0o700)
        code.unlink(); code.symlink_to(store.source / 'example.py')
    elif fault == 'hardlink':
        os.link(code, store.source / 'alias')
    elif fault == 'writable':
        code.chmod(0o666)
    elif fault == 'unknown':
        (store.root / 'unknown').mkdir()
    else:
        pending = store.root / '.pending-abcdefgh'; pending.mkdir(); (pending / 'unknown').write_text('x')
    with pytest.raises(RetentionError):
        store.owner.inventory()


def test_replaced_store_during_inventory_fails_closed(store, monkeypatch):
    store.retain(); native = store.owner.scan; count = 0
    def changed():
        nonlocal count
        rows = native(); count += 1
        if count == 1:
            (store.root / ('d' * 16)).mkdir()
        return rows
    monkeypatch.setattr(store.owner, 'scan', changed)
    with pytest.raises(RetentionError, match='retention_owner_changed'):
        store.owner.inventory()


def test_interrupted_native_candidate_copy_keeps_pending_recovery(store, monkeypatch):
    import synth_proposal_store as native
    copy = native.shutil.copytree
    def interrupted(*args, **kwargs):
        copy(*args, **kwargs)
        raise OSError('interrupted candidate copy')
    monkeypatch.setattr(native.shutil, 'copytree', interrupted)
    # Model process death before the native finally-cleanup removes staging.
    remove = native.shutil.rmtree
    def preserve_staging(path, *args, **kwargs):
        if Path(path).name.startswith('.pending-'):
            return None
        return remove(path, *args, **kwargs)
    monkeypatch.setattr(native.shutil, 'rmtree', preserve_staging)
    with pytest.raises(OSError, match='interrupted candidate copy'):
        store.retain()
    obj, = store.owner.inventory()
    assert obj.identity.local_id.startswith('proposals/.pending-')
    assert obj.state is NodeState.OPEN and obj.roots


def test_group_writable_native_metadata_is_not_certified(store):
    address = store.retain()
    (store.root / address / 'proposal.json').chmod(0o664)
    with pytest.raises(RetentionError, match='retention_owner_path_invalid'):
        store.owner.inventory()
