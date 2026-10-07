"""Physical history custody plus native authentication, without new admission."""
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import contract_store as store
from executor_birth_receipts import (AdmissionCheck, AdmissionKind, AdmittedCheckStatus,
    ApprovedLifecycle, RevisionClass, issue_admission_receipt)
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_signed_store import _SignedStoreOwner
from manifest_inventory import ContractId, ManifestOrigin

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX signed-store custody')

D = 'sha256:' + 'a' * 64


@pytest.fixture
def history(tmp_path):
    root = tmp_path / 'store'
    root.mkdir(mode=0o700)
    author, admission = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    contract = ContractId(ManifestOrigin.BUILTIN, 'historical/manifest.toml')
    key = store.contract_storage_key(contract)
    directory = root / key

    def write(path, payload):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(payload)
        for parent in (path.parent, *path.parent.parents):
            if parent == root.parent:
                break
            parent.chmod(0o700)
        path.chmod(0o600)
        return path

    write(directory / store.BINDING_FILE, store.encode_binding(contract))
    write(directory / 'writer.lock', b'\0')
    manifest = b'name="historical"\n'
    payloads = {'manifest.toml': manifest, 'manifest.toml.sig': author.sign(manifest),
                'manifest.lang_state.json': b'{"old":"schema"}'}
    generation = store.generation_id(payloads)
    gen = directory / 'generations' / generation[7:]
    for name, payload in payloads.items():
        write(gen / name, payload)
    encoded = issue_admission_receipt(policy_version='historical-policy/v1', contract_id=contract,
        generation_id=generation, candidate_id=D, semantic_core_id=D, admission_context_id=D,
        birth_request_id=D, authoring_journal_hash=D, predecessor_id=None, producer_receipt_hash=D,
        revision_class=RevisionClass.FIRST_BIRTH,
        check_results={'manifest': AdmissionCheck('historical/v1', AdmittedCheckStatus.PASSED, D)},
        semantic_review_hash=D, approval_hash=None, approved_lifecycle=ApprovedLifecycle.ACTIVE,
        kind=AdmissionKind.ADMISSION, issued_at='2020-01-01T00:00:00Z', key_id='admission', private_key=admission)
    receipts = (store._birth_receipt_path(directory, generation),
                store._birth_receipt_path_v2(directory, generation, D))
    for path in receipts:
        write(path, encoded)
    write(directory / 'current', (generation + '\n').encode())
    owner = _SignedStoreOwner(root=root, require_exclusion=lambda: None,
        owner=None, admission_keys={'admission': admission.public_key()},
        author_keys={'author': author.public_key()})
    return SimpleNamespace(**locals())


def test_all_physical_copies_authenticate_without_current_source_or_policy(history, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('historical inventory attempted current policy, source or publication')
    for name in ('_authenticate_payloads', '_load_generation', 'current_contract', 'catalog_admission_lock'):
        monkeypatch.setattr(store, name, forbidden)
    objects = history.owner.inventory()
    assert len(objects) == 8
    assert all(obj.state is NodeState.OPEN for obj in objects)
    ids = {obj.identity for obj in objects}
    assert all(set(obj.references) <= ids for obj in objects)
    receipts = [obj for obj in objects if obj.identity.node_type == 'admission_receipt']
    assert len(receipts) == 2 and receipts[0].identity != receipts[1].identity
    assert all(obj.identity.contract == history.contract.value for obj in objects)
    assert all(history.owner.version(obj.identity) == obj.version for obj in objects)
    assert any(RootKind.CURRENT_POINTER in obj.roots for obj in objects)
    for obj in objects:
        with pytest.raises(RetentionError, match='lacks native closure'):
            history.owner.delete(obj.identity, obj.version)


def test_authenticated_retirement_preserves_exact_predecessor(history):
    payload = store.encode_retirement(history.contract, previous_generation_id=history.generation,
                                      actor='administrator', reason='native test retirement')
    payloads = {'retirement.json': payload,
                'retirement.json.sig': history.author.sign(store.RETIREMENT_SIGNATURE_DOMAIN + payload)}
    retirement = store.retirement_id(payloads)
    for name, content in payloads.items():
        history.write(history.directory / 'generations' / retirement[7:] / name, content)
    history.write(history.directory / 'current', (retirement + '\n').encode())
    objects = history.owner.inventory()
    assert len(objects) == 10
    prior = next(obj for obj in objects if RootKind.RETIREMENT_PREDECESSOR in obj.roots)
    retired = next(obj for obj in objects if obj.identity.local_id.endswith('retirement.json'))
    assert prior.identity in retired.references


@pytest.mark.parametrize('fault', ['manifest', 'signature', 'receipt', 'context', 'current', 'predecessor'])
def test_changed_or_unbound_native_evidence_blocks_everything(history, fault):
    if fault == 'manifest':
        history.write(history.gen / 'manifest.toml', b'changed')
    elif fault == 'signature':
        history.owner.author_keys = {'wrong': Ed25519PrivateKey.generate().public_key()}
    elif fault == 'receipt':
        history.owner.admission_keys = {'admission': Ed25519PrivateKey.generate().public_key()}
    elif fault == 'context':
        history.receipts[1].rename(history.receipts[1].with_name('b' * 64 + '.json'))
    elif fault == 'current':
        history.write(history.directory / 'current', ('sha256:' + 'f' * 64 + '\n').encode())
    else:
        payload = store.encode_retirement(history.contract, previous_generation_id=D,
                                          actor='administrator', reason='missing predecessor')
        payloads = {'retirement.json': payload,
                    'retirement.json.sig': history.author.sign(store.RETIREMENT_SIGNATURE_DOMAIN + payload)}
        retirement = store.retirement_id(payloads)
        for name, content in payloads.items():
            history.write(history.directory / 'generations' / retirement[7:] / name, content)
    with pytest.raises(RetentionError):
        history.owner.inventory()


@pytest.mark.parametrize('fault', ['unknown', 'staging', 'hardlink', 'symlink', 'writable', 'missing_file'])
def test_unsafe_or_unreconciled_physical_history_is_not_garbage(history, fault):
    path = history.gen / 'manifest.toml'
    if fault == 'unknown':
        history.write(history.directory / 'unknown', b'opaque')
    elif fault == 'staging':
        (history.directory / '.generation-pending').mkdir(mode=0o700)
    elif fault == 'hardlink':
        os.link(path, history.root.parent / 'alias')
    elif fault == 'symlink':
        path.unlink()
        path.symlink_to(history.receipts[0])
    elif fault == 'writable':
        path.chmod(0o666)
    else:
        path.unlink()
    with pytest.raises(RetentionError):
        history.owner.inventory()


def test_empty_unbound_namespace_is_preserved_without_inventing_contract(history):
    key = 'f' * 64
    (history.root / key / 'generations').mkdir(mode=0o700, parents=True)
    history.write(history.root / key / 'writer.lock', b'\0')
    objects = history.owner.inventory()
    opaque, = (obj for obj in objects if obj.identity.contract is None)
    assert opaque.roots == (RootKind.OPEN_AUDIT,)
    assert history.owner.version(opaque.identity) == opaque.version


def test_empty_v2_directory_cannot_hide_missing_generation(history):
    (history.directory / 'admission-receipts-v2' / ('f' * 64)).mkdir(mode=0o700)
    with pytest.raises(RetentionError, match='namespace binding'):
        history.owner.inventory()


def test_foreign_contract_identity_and_changed_reread_are_rejected(history, monkeypatch):
    obj = history.owner.inventory()[0]
    with pytest.raises(RetentionError):
        history.owner.version(replace(obj.identity, contract='builtin:foreign/manifest.toml'))
    original = history.owner._files
    calls = []
    def read():
        calls.append(1)
        if len(calls) == 2:
            history.write(history.directory / 'current', (history.generation + '\n').encode())
        return original()
    monkeypatch.setattr(history.owner, '_files', read)
    with pytest.raises(RetentionError, match='signed history changed'):
        history.owner.inventory()


def test_empty_existing_store_is_explicit_but_missing_store_blocks(history):
    root = history.root.parent / 'empty'
    root.mkdir(mode=0o700)
    history.owner.root = root
    assert history.owner.inventory() == ()
    root.rmdir()
    with pytest.raises(RetentionError, match='signed store missing'):
        history.owner.inventory()


def test_graph_retains_history_without_inventing_age_based_closure(history, tmp_path):
    from install.birth_retention_maintenance import plan
    objects = history.owner.inventory()
    assert history.owner.generation_identity(history.contract, history.generation) in {obj.identity for obj in objects}
    assert plan(objects, required_owners=frozenset({history.owner.name}),
        observed_owners=frozenset({history.owner.name}), observed_roots=frozenset(RootKind),
        holds=frozenset(), graph_path=tmp_path / 'graph.sqlite', run_id=D,
        observed_at='2035-01-01T00:00:00Z') == ()


def test_native_owner_still_blocks_group_writable_ancestor(history):
    history.root.parent.chmod(0o770)
    history.owner.owner = (os.getuid(), os.getgid())
    # A writable ancestor cannot substitute for installed custody.
    with pytest.raises(RetentionError, match='directory custody'):
        history.owner.inventory()


def test_signed_admission_with_missing_predecessor_does_not_hide_history_gap(history):
    from executor_birth_receipts import verify_admission_receipt
    receipt = verify_admission_receipt(history.encoded, verifier_keys=history.owner.admission_keys)
    encoded = issue_admission_receipt(policy_version=receipt.policy_version, contract_id=history.contract,
        generation_id=receipt.generation_id, candidate_id=receipt.candidate_id,
        semantic_core_id=receipt.semantic_core_id, admission_context_id=receipt.admission_context_id,
        birth_request_id=receipt.birth_request_id, authoring_journal_hash=receipt.authoring_journal_hash,
        predecessor_id='sha256:' + 'f' * 64, producer_receipt_hash=receipt.producer_receipt_hash,
        revision_class=receipt.revision_class, check_results=receipt.check_results,
        semantic_review_hash=receipt.semantic_review_hash, approval_hash=receipt.approval_hash,
        approved_lifecycle=receipt.approved_lifecycle, kind=receipt.kind, issued_at=receipt.issued_at,
        key_id='admission', private_key=history.admission)
    history.write(history.receipts[0], encoded)
    with pytest.raises(RetentionError, match='admission predecessor absent'):
        history.owner.inventory()


@pytest.mark.parametrize('kind', ['generation', 'retirement', 'empty'])
def test_native_recoverable_staging_is_open_and_inventory_never_recovers(history, monkeypatch, kind):
    stage = history.directory / 'generations' / '.generation-native-crash'
    stage.mkdir(mode=0o700)
    if kind != 'empty':
        name = 'manifest.toml' if kind == 'generation' else 'retirement.json'
        path = history.write(stage / name, b'partial native write')
    def forbidden(*args, **kwargs):
        pytest.fail('inventory invoked destructive native recovery')
    monkeypatch.setattr(store, '_remove_staging_recovery_plan', forbidden)
    objects = history.owner.inventory()
    staged = [obj for obj in objects if '.generation-' in obj.identity.local_id]
    assert len(staged) == (kind != 'empty')
    for obj in staged:
        assert obj.state is NodeState.OPEN and obj.roots == (RootKind.OPEN_AUDIT,)
        assert history.owner.version(obj.identity) == obj.version
        with pytest.raises(RetentionError, match='lacks native closure'):
            history.owner.delete(obj.identity, obj.version)
    assert stage.is_dir()
    if kind != 'empty':
        assert path.read_bytes() == b'partial native write'


@pytest.mark.parametrize('fault', ['mixed', 'nested', 'unknown', 'symlink', 'hardlink', 'empty-suffix'])
def test_invalid_native_staging_never_disappears_from_inventory(history, fault):
    stage = history.directory / 'generations' / ('.generation-' if fault == 'empty-suffix' else '.generation-crash')
    stage.mkdir(mode=0o700)
    path = history.write(stage / 'manifest.toml', b'partial')
    if fault == 'mixed':
        history.write(stage / 'retirement.json', b'mixed')
    elif fault == 'nested':
        (stage / 'manifest.toml.sig').mkdir(mode=0o700)
    elif fault == 'unknown':
        history.write(stage / 'unknown', b'unknown')
    elif fault == 'symlink':
        path.unlink(); path.symlink_to(history.gen / 'manifest.toml')
    elif fault == 'hardlink':
        os.link(path, history.root.parent / 'alias')
    with pytest.raises(RetentionError):
        history.owner.inventory()
    assert stage.exists()


def test_first_binding_staging_identifies_namespace_without_publication(history):
    contract = ContractId(ManifestOrigin.BUILTIN, 'first/manifest.toml')
    directory = history.root / store.contract_storage_key(contract)
    (directory / 'generations').mkdir(mode=0o700, parents=True)
    history.write(directory / 'writer.lock', b'\0')
    stage = history.write(directory / '.binding.json.1.2.3.tmp', store.encode_binding(contract))
    objects = history.owner.inventory()
    obj = next(obj for obj in objects if obj.identity.local_id.endswith(stage.name))
    assert obj.identity.contract == contract.value and obj.state is NodeState.OPEN
    assert not (directory / store.BINDING_FILE).exists() and stage.exists()
    stage.write_bytes(store.encode_binding(history.contract))
    with pytest.raises(RetentionError):
        history.owner.inventory()


@pytest.mark.parametrize('retired', [False, True])
def test_staged_pointer_preserves_native_authenticated_target(history, tmp_path, monkeypatch, retired):
    from test_contract_store_certification import _make_source
    ref, _ = _make_source(tmp_path / 'native-source')
    generation_payloads = {name: (ref.manifest_path.parent / name).read_bytes()
                           for name in store.GENERATION_FILES}
    generation_payloads['manifest.toml.sig'] = history.author.sign(generation_payloads['manifest.toml'])
    generation = store.generation_id(generation_payloads)
    for name, content in generation_payloads.items():
        history.write(history.directory / 'generations' / generation[7:] / name, content)
    def forbidden(*args, **kwargs):
        pytest.fail('historical staging attempted current qualification or source binding')
    monkeypatch.setattr(store, '_validate_current_standard', forbidden)
    monkeypatch.setattr(store, '_code_digest', forbidden)
    payload = store.encode_retirement(history.contract, previous_generation_id=generation,
                                     actor='administrator', reason='native staged current')
    payloads = {'retirement.json': payload,
                'retirement.json.sig': history.author.sign(store.RETIREMENT_SIGNATURE_DOMAIN + payload)}
    retirement = store.retirement_id(payloads)
    for name, content in payloads.items():
        history.write(history.directory / 'generations' / retirement[7:] / name, content)
    selected = retirement if retired else generation
    stage = history.write(history.directory / '.current.1.2.3.tmp', (selected + '\n').encode())
    objects = history.owner.inventory()
    obj = next(obj for obj in objects if obj.identity.local_id.endswith(stage.name))
    target_name = 'retirement.json' if retired else 'manifest.toml'
    target = next(obj for obj in objects if obj.identity.local_id.endswith(selected[7:] + '/' + target_name))
    assert target.identity in obj.references and obj.state is NodeState.OPEN
    assert stage.exists() and (history.directory / 'current').read_text() == history.generation + '\n'
    stage.write_bytes(('sha256:' + 'f' * 64 + '\n').encode())
    with pytest.raises(RetentionError):
        history.owner.inventory()


def crash_receipt_write(history, destination, payload_kind):
    """Hard-stop the native atomic writer, including its normally active finally."""
    import subprocess
    import sys
    # Select the same runtime explicitly: pytest's sys.path is not inherited.
    result = subprocess.run([sys.executable, '-I', '-B', '-c', '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
import contract_store as native
path = Path(sys.argv[1])
payload = path.read_bytes()
if sys.argv[2] != 'complete':
    original = native._write_new_file
    def incomplete(path, data, **kwargs):
        original(path, b'' if sys.argv[2] == 'empty' else data[:13], **kwargs)
        os._exit(71)
    native._write_new_file = incomplete
else:
    native._replace_retry = lambda *args, **kwargs: os._exit(71)
native._atomic_replace_file(path, payload, replace_timeout=1, mode=0o600)
''', str(destination), payload_kind, str(Path(store.__file__).resolve().parent)],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 71, result.stderr
    stage, = destination.parent.glob('.' + destination.name + '.*.tmp')
    return stage


@pytest.mark.parametrize('version', [0, 1])
@pytest.mark.parametrize('payload_kind', ['empty', 'partial', 'complete'])
def test_native_receipt_crash_files_retained_without_admission(history, version, payload_kind):
    before_objects, before_admissions, before_evidence = history.owner.scan_with_evidence()
    destination = history.receipts[version]
    committed = destination.read_bytes()
    stage = crash_receipt_write(history, destination, payload_kind)
    objects, admissions, evidence = history.owner.scan_with_evidence()
    obj, = [obj for obj in objects if obj.identity.local_id.endswith(stage.name)]
    assert len(objects) == len(before_objects) + 1
    assert obj.identity.node_type == 'evidence' and obj.state is NodeState.OPEN
    assert obj.roots == (RootKind.OPEN_AUDIT,)
    assert obj.references == (history.owner.generation_identity(history.contract, history.generation),)
    assert history.owner.version(obj.identity) == obj.version
    assert admissions == before_admissions and evidence == before_evidence
    with pytest.raises(RetentionError, match='lacks native closure'):
        history.owner.delete(obj.identity, obj.version)
    assert stage.exists() and destination.read_bytes() == committed
    # A crash before the first receipt rename also has no committed receipt.
    destination.unlink()
    objects, admissions, evidence = history.owner.scan_with_evidence()
    assert obj.identity in {item.identity for item in objects}
    assert obj.identity not in admissions and obj.identity not in evidence
    assert len(admissions) == len(before_admissions) - 1


@pytest.mark.parametrize('suffix', ['01.2.3.tmp', '-1.2.3.tmp', '1.2.tmp', '1.2.3.tmp.extra', '1.2.3.tmp\n'])
def test_receipt_staging_reuses_exact_native_atomic_suffix(history, suffix):
    path = history.receipts[0]
    stage = history.write(path.with_name('.' + path.name + '.' + suffix), b'partial')
    with pytest.raises(RetentionError):
        history.owner.inventory()
    assert stage.exists()


@pytest.mark.parametrize('fault', ['symlink', 'hardlink', 'directory', 'writable', 'oversized'])
def test_receipt_staging_requires_same_custody_and_limits(history, fault):
    path = history.receipts[1]
    stage = history.write(path.with_name('.' + path.name + '.1.2.3.tmp'), b'partial')
    if fault == 'symlink':
        stage.unlink(); stage.symlink_to(path)
    elif fault == 'hardlink':
        os.link(stage, history.root.parent / 'receipt-alias')
    elif fault == 'directory':
        stage.unlink(); stage.mkdir(mode=0o700)
    elif fault == 'writable':
        stage.chmod(0o666)
    else:
        stage.write_bytes(b'x' * ((1 << 20) + 1))
    with pytest.raises(RetentionError):
        history.owner.inventory()
    assert stage.exists()


@pytest.mark.parametrize('version', [0, 1])
def test_receipt_staging_without_target_generation_fails_closed(history, version):
    digest = 'f' * 64
    path = (history.directory / 'admission-receipts' / (digest + '.json') if version == 0
            else history.directory / store._ADMISSION_RECEIPTS_V2 / digest / (D[7:] + '.json'))
    stage = history.write(path.with_name('.' + path.name + '.1.2.3.tmp'), b'partial')
    with pytest.raises(RetentionError, match='staged receipt generation absent'):
        history.owner.inventory()
    assert stage.exists()


def test_receipt_staging_drift_is_not_a_complete_inventory(history, monkeypatch):
    path = history.receipts[0]
    stage = history.write(path.with_name('.' + path.name + '.1.2.3.tmp'), b'partial')
    original = history.owner._project
    def changed(*args):
        result = original(*args)
        stage.write_bytes(b'changed')
        return result
    monkeypatch.setattr(history.owner, '_project', changed)
    with pytest.raises(RetentionError, match='signed history changed'):
        history.owner.inventory()
