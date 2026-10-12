"""Native historical-set/capability reads authenticate original review evidence."""
from dataclasses import replace
import json
import os
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import executor_birth_prepared_root as prepared
from executor_birth_retention import NodeState, RetentionError
from install.birth_retention_semantic_inventory import _SemanticEvidenceInventory
from install.synth_review import _record_evidence, _digest
from test_birth_retention_reviews import review
from rm0008_2b import support
from rm0008_2b.test_group8_public_history import _chain_boundary_fixture


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='native POSIX administrative review custody')


@pytest.fixture
def linked(review, tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    base = support.make_config(tmp_path / 'native', author=Ed25519PrivateKey.generate(), operator=True)
    registry = json.loads(support.semantic_document())
    registry['verifiers']['review-key'] = registry['verifiers'].pop('review-key-0')
    support.install_operator_input(base, semantic=support.canonical_json(registry), keys={'review.pub': key.public_key().public_bytes_raw()})
    support.provision(monkeypatch, base)
    support.use_config(monkeypatch, base)
    chain = _chain_boundary_fixture(base, monkeypatch)
    context = chain.context_transitions[0].prepared_admission_context_id
    review.record['subject']['admission_context_id'] = context
    review_path = review.write()
    evidence = support.installed_set(base) / 'semantic' / 'evidence' / (_digest(review.record)[7:] + '.json')
    support.write(evidence, _record_evidence(review.record, key), 0o644)
    with prepared.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            inventory = _SemanticEvidenceInventory(reviews=review.owner, birth_root=base / 'birth', contexts=(context, context),
                session=session, owner=None, require_exclusion=lambda: None)
            yield SimpleNamespace(**locals())


def test_native_historical_evidence_links_immutable_and_open(linked):
    objects, evidence = linked.inventory.scan()
    assert len(objects) == 2 and len(evidence) == 1
    assert len(linked.inventory.owners) == 2
    assert all(obj.state is NodeState.OPEN and obj.eligible_after is None and len(obj.references) == 1 for obj in objects)
    identity = next(iter(evidence))
    owner = linked.inventory.owners[identity.owner]
    assert owner.version(identity) == next(obj.version for obj in objects if obj.identity == identity)
    with pytest.raises(TypeError):
        evidence[identity] = None
    with pytest.raises(RetentionError, match='lacks native closure'):
        owner.delete(identity, owner.version(identity))


@pytest.mark.parametrize('fault', ['signature', 'context', 'candidate', 'hash', 'filename', 'unknown', 'missing_review', 'mode', 'hardlink', 'symlink'])
def test_native_evidence_or_review_tampering_fails_closed(linked, tmp_path, fault):
    path = linked.evidence
    if fault == 'signature':
        path.write_bytes(_record_evidence(linked.review.record, Ed25519PrivateKey.generate()))
    elif fault in {'context', 'candidate', 'hash'}:
        # Re-sign correctly: failures must be relation failures, not signature failures.
        from executor_birth_semantic_authority import _canonical, EVIDENCE_DOMAIN
        import base64
        value = json.loads(path.read_bytes())
        field = {'context': 'admission_context_id', 'candidate': 'candidate_id', 'hash': 'evidence_hash'}[fault]
        value['evidence'][field] = 'sha256:' + 'd' * 64
        value['signature'] = base64.b64encode(linked.key.sign(EVIDENCE_DOMAIN + _canonical(value['evidence']))).decode()
        path.write_bytes(_canonical(value))
    elif fault == 'filename':
        path.rename(path.with_name('f' * 64 + '.json'))
    elif fault == 'unknown':
        support.write(path.with_name('unknown.txt'), b'unknown', 0o644)
    elif fault == 'missing_review':
        linked.review_path.unlink()
    elif fault == 'mode':
        path.chmod(0o600)
    elif fault == 'hardlink':
        os.link(path, tmp_path / 'copy')
    else:
        other = tmp_path / 'elsewhere'
        path.rename(other)
        path.symlink_to(other)
    with pytest.raises(RetentionError):
        linked.inventory.scan()


def test_second_physical_read_detects_drift(linked, monkeypatch):
    owner = next(iter(linked.inventory.evidence.values()))
    original = owner.scan
    count = 0
    def scan():
        nonlocal count
        count += 1
        if count == 2:
            stat = linked.evidence.stat()
            os.utime(linked.evidence, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        return original()
    monkeypatch.setattr(owner, 'scan', scan)
    with pytest.raises(RetentionError, match='inventory changed'):
        linked.inventory.scan()


@pytest.mark.parametrize('shared', [False, True])
def test_physical_copy_cannot_replace_session_root(linked, tmp_path, shared):
    with pytest.raises(RetentionError, match='root binding'):
        _SemanticEvidenceInventory(reviews=linked.review.owner, birth_root=tmp_path, contexts=(linked.context,),
            session=linked.session, owner=None, require_exclusion=lambda: None,
            context_bindings=linked.inventory.bindings if shared else None)


def test_crash_after_review_before_proof_keeps_open_audit(linked):
    linked.evidence.unlink()
    objects, evidence = linked.inventory.scan()
    assert len(objects) == 1 and not evidence
    assert objects[0].state is NodeState.OPEN and objects[0].references == ()


def test_other_native_independent_evidence_is_open_without_invented_review(linked):
    from executor_birth_semantic_authority import _canonical, EVIDENCE_DOMAIN
    import base64
    value = json.loads(linked.evidence.read_bytes())
    value['evidence'].update(kind='deterministic_oracle', evidence_id='sha256:' + 'e' * 64,
                             evidence_hash='sha256:' + 'f' * 64)
    value['signature'] = base64.b64encode(linked.key.sign(EVIDENCE_DOMAIN + _canonical(value['evidence']))).decode()
    extra = linked.evidence.with_name('oracle.json')
    support.write(extra, _canonical(value), 0o644)
    objects, evidence = linked.inventory.scan()
    assert len(objects) == 3 and len(evidence) == 2
    obj = next(obj for obj in objects if obj.identity.local_id == 'oracle.json')
    assert obj.state is NodeState.OPEN and not obj.references


def test_native_evidence_size_limit_blocks_oversized_record(linked):
    linked.evidence.write_bytes(b'x' * (64 * 1024 + 1))
    with pytest.raises(RetentionError):
        linked.inventory.scan()


def test_review_context_cannot_be_silently_omitted(linked):
    inventory = _SemanticEvidenceInventory(reviews=linked.review.owner, birth_root=linked.base / 'birth', contexts=(),
        session=linked.session, owner=None, require_exclusion=lambda: None)
    with pytest.raises(RetentionError):
        inventory.scan()


def test_shared_native_census_matches_default_without_reauthentication(linked, monkeypatch):
    expected = linked.inventory.scan()
    bindings = dict(linked.inventory.bindings)
    guards = []
    def no_batch(*args, **kwargs):
        pytest.fail('shared native census must not repeat historical authentication')
    monkeypatch.setattr(prepared, 'load_historical_context_verifiers_for_contexts_in_session_v1', no_batch)
    inventory = _SemanticEvidenceInventory(reviews=linked.review.owner, birth_root=linked.base / 'birth',
        contexts=(linked.context,), session=linked.session, owner=None,
        require_exclusion=lambda: guards.append(True), context_bindings=bindings)
    # The caller's mapping is only borrowed during construction, then frozen.
    bindings.clear()
    for _ in range(2):
        guards.clear()
        assert inventory.scan() == expected
        assert guards


@pytest.mark.parametrize('fault', ['mapping', 'missing', 'extra', 'type', 'context', 'head', 'set', 'mixed_head'])
def test_shared_native_census_rejects_inconsistent_bindings(linked, fault):
    binding = linked.inventory.bindings[linked.context]
    contexts, bindings = (linked.context,), {linked.context: binding}
    other_context = 'sha256:' + ('d' * 64 if linked.context != 'sha256:' + 'd' * 64 else 'e' * 64)
    if fault == 'mapping':
        bindings = tuple(bindings.items())
    elif fault == 'missing':
        bindings = {}
    elif fault == 'extra':
        bindings[other_context] = binding
    elif fault == 'type':
        bindings[linked.context] = SimpleNamespace(required_head_id=binding.required_head_id,
            transition_id=binding.transition_id, public_set=binding.public_set)
    elif fault == 'context':
        pin = replace(binding.public_set.material.pin, admission_context_id=other_context)
        public = replace(binding.public_set, material=replace(binding.public_set.material, pin=pin))
        bindings[linked.context] = replace(binding, public_set=public)
    elif fault == 'head':
        bindings[linked.context] = replace(binding, required_head_id='sha256:invalid')
    elif fault == 'set':
        bindings[linked.context] = replace(binding, public_set=replace(binding.public_set, set_id='../foreign'))
    else:
        contexts = (linked.context, other_context)
        pin = replace(binding.public_set.material.pin, admission_context_id=other_context)
        public = replace(binding.public_set, material=replace(binding.public_set.material, pin=pin))
        other_head = 'sha256:' + ('a' * 64 if binding.required_head_id != 'sha256:' + 'a' * 64 else 'b' * 64)
        bindings[other_context] = replace(binding, public_set=public, required_head_id=other_head)
    with pytest.raises(RetentionError, match='context bindings'):
        _SemanticEvidenceInventory(reviews=linked.review.owner, birth_root=linked.base / 'birth',
            contexts=contexts, session=linked.session, owner=None, require_exclusion=lambda: None,
            context_bindings=bindings)


def test_shared_native_census_still_authenticates_evidence_signature(linked, monkeypatch):
    def no_batch(*args, **kwargs):
        pytest.fail('shared native census must not repeat historical authentication')
    monkeypatch.setattr(prepared, 'load_historical_context_verifiers_for_contexts_in_session_v1', no_batch)
    inventory = _SemanticEvidenceInventory(reviews=linked.review.owner, birth_root=linked.base / 'birth',
        contexts=(linked.context,), session=linked.session, owner=None, require_exclusion=lambda: None,
        context_bindings=linked.inventory.bindings)
    linked.evidence.write_bytes(_record_evidence(linked.review.record, Ed25519PrivateKey.generate()))
    with pytest.raises(RetentionError, match='historical independent evidence'):
        inventory.scan()


def test_shared_native_census_cannot_outlive_held_exclusion(linked, monkeypatch):
    active = True
    def require_exclusion():
        if not active:
            raise RetentionError('retention_owner_changed', 'held exclusion ended')
    inventory = _SemanticEvidenceInventory(reviews=linked.review.owner, birth_root=linked.base / 'birth',
        contexts=(linked.context,), session=linked.session, owner=None, require_exclusion=require_exclusion,
        context_bindings=linked.inventory.bindings)
    def no_read(*args, **kwargs):
        pytest.fail('ended exclusion must prevent further native or physical reads')
    monkeypatch.setattr(prepared, 'load_historical_context_verifiers_for_contexts_in_session_v1', no_read)
    monkeypatch.setattr(linked.review.owner, 'scan', no_read)
    active = False
    with pytest.raises(RetentionError, match='held exclusion ended'):
        inventory.scan()
