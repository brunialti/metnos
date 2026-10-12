"""Real historical signed sources and public sets under an existing EX barrier."""
from dataclasses import replace
import os
from types import SimpleNamespace

import pytest
import executor_birth_prepared_root as root
import executor_birth_ownership_chain as chain_module
from executor_birth_distribution_manifest import DistributionManifestError
from .test_group8_public_history import _producer_policy_fixture, _POLICY_PATH
from .test_historical_context_session import deny_new_authority, snapshot
from . import support

pytestmark = pytest.mark.skipif(os.name == 'nt', reason=support.POSIX_SCENARIO_ONLY_V1)


@pytest.mark.parametrize('reattestation', [False, True])
@pytest.mark.parametrize('archive', [False, True])
@pytest.mark.parametrize('shared', [False, True])
def test_exact_declarations_use_existing_barrier_and_historical_source(tmp_path, monkeypatch, reattestation, archive, shared):
    chain, release, source = _producer_policy_fixture(tmp_path, monkeypatch)
    selectors = (chain.context_transitions[0].prepared_admission_context_id,)
    options = dict(include_reattestation=reattestation,
                   public_sources=((_POLICY_PATH, source),) if archive else ())
    expected = root.load_historical_producer_declarations_for_contexts_v1(selectors, **options)
    import executor_birth_producer_table_v1 as table
    monkeypatch.setattr(table, 'PRODUCER_AUTHOR_V1', {})
    monkeypatch.setattr(table, '_MANIFEST_ORIGIN_TO_EXECUTOR_V1', {})
    if archive:
        (release / _POLICY_PATH).write_bytes(b'current source cannot reinterpret history')
    with root.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            before = snapshot(tmp_path)
            if shared:
                bindings = root.load_historical_context_verifiers_for_contexts_in_session_v1(selectors, session)
                options['_context_bindings'] = dict(zip(selectors, bindings))
                monkeypatch.setattr(root, '_read_historical_context_set_in_session_v1',
                    lambda *args, **kwargs: pytest.fail('authenticated public set was reread'))
            chain_reads, source_reads = [], []
            original_source = root._historical_public_source_v1
            def inspect():
                chain_reads.append(None)
                return chain
            def source_read(*args, **kwargs):
                source_reads.append(None)
                return original_source(*args, **kwargs)
            monkeypatch.setattr(chain_module, 'inspect_ownership_chain_state_v1', inspect)
            monkeypatch.setattr(root, '_historical_public_source_v1', source_read)
            deny_new_authority(monkeypatch)
            observed = root.load_historical_producer_declarations_for_contexts_in_session_v1(selectors, session, **options)
            assert observed == expected
            assert observed[0].authors
            assert len(chain_reads) == 2 and source_reads
            if shared:
                reads = len(source_reads)
                assert root.load_historical_producer_declarations_for_contexts_in_session_v1(
                    selectors, session, **options) == expected
                assert len(chain_reads) == 4 and len(source_reads) == 2 * reads
            assert session._holds_global_exclusive()
            assert snapshot(tmp_path) == before


@pytest.mark.parametrize('change', ['frontier', 'source', 'archive', 'selector'])
@pytest.mark.parametrize('shared', [False, True])
def test_held_session_rejects_changed_or_unbound_history(tmp_path, monkeypatch, change, shared):
    chain, release, source = _producer_policy_fixture(tmp_path, monkeypatch)
    selectors = (chain.context_transitions[0].prepared_admission_context_id,)
    options = {}
    with root.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            if shared:
                bindings = root.load_historical_context_verifiers_for_contexts_in_session_v1(selectors, session)
                options['_context_bindings'] = dict(zip(selectors, bindings))
            if change == 'frontier':
                sequence = iter((chain, replace(chain, context_transitions=())))
                monkeypatch.setattr(chain_module, 'inspect_ownership_chain_state_v1', lambda: next(sequence))
            elif change == 'source':
                (release / _POLICY_PATH).write_bytes(source + b'\n# altered\n')
            elif change == 'archive':
                (release / _POLICY_PATH).write_bytes(b'missing historical bytes')
                options['public_sources'] = ((_POLICY_PATH, source + b'\n'),)
            else:
                selectors = (chain.context_transitions[0].previous_admission_context_id,)
            deny_new_authority(monkeypatch)
            with pytest.raises((root.PreparedRootError, DistributionManifestError)):
                root.load_historical_producer_declarations_for_contexts_in_session_v1(selectors, session, **options)


def test_missing_barrier_rejected_before_acquisition(tmp_path, monkeypatch):
    chain, _, _ = _producer_policy_fixture(tmp_path, monkeypatch)
    selectors = (chain.context_transitions[0].prepared_admission_context_id,)
    with root.open_prepared_root_session_v1() as session:
        monkeypatch.setattr(chain_module, 'inspect_ownership_chain_state_v1', lambda: pytest.fail('acquisition without barrier'))
        with pytest.raises(root.PreparedRootError):
            root.load_historical_producer_declarations_for_contexts_in_session_v1(selectors, session)


def test_shared_context_census_rejects_incomplete_or_mismatched_bindings(tmp_path, monkeypatch):
    chain, _, _ = _producer_policy_fixture(tmp_path, monkeypatch)
    identifier = chain.context_transitions[0].prepared_admission_context_id
    with root.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            binding, = root.load_historical_context_verifiers_for_contexts_in_session_v1((identifier,), session)
            public = binding.public_set
            def changed_public(**kwargs):
                return replace(binding, public_set=replace(public, **kwargs))
            def changed_pin(**kwargs):
                return changed_public(material=replace(public.material, pin=replace(public.material.pin, **kwargs)))
            wrong = 'sha256:' + '0' * 64
            invalid = [(), {}, {wrong: binding}, {identifier: object()},
                {identifier: replace(binding, required_head_id=wrong)},
                {identifier: replace(binding, transition_id=wrong)},
                {identifier: replace(binding, binding_kind='initial_predecessor')},
                {identifier: replace(binding, previous_admission_context_id=wrong)},
                {identifier: replace(binding, public_set=object())},
                {identifier: changed_public(set_id='0' * 64)},
                {identifier: changed_public(set_json_sha256=wrong)},
                {identifier: changed_pin(admission_context_id=wrong)},
                {identifier: changed_pin(context_epoch=public.material.pin.context_epoch + '-changed')},
                {identifier: changed_public(material=SimpleNamespace(pin=SimpleNamespace(admission_context_id=identifier)))}]
            monkeypatch.setattr(root, '_read_historical_context_set_in_session_v1',
                lambda *args, **kwargs: pytest.fail('invalid supplied census caused public-set acquisition'))
            deny_new_authority(monkeypatch)
            for census in invalid:
                with pytest.raises(root.PreparedRootError, match='birth_context_selection_invalid'):
                    root.load_historical_producer_declarations_for_contexts_in_session_v1(
                        (identifier,), session, _context_bindings=census)


def test_shared_census_keeps_signed_policy_ast_validation(tmp_path, monkeypatch):
    chain, _, _ = _producer_policy_fixture(tmp_path, monkeypatch,
        source_transform=lambda source: source + b'\nPRODUCER_AUTHOR_V1 = MappingProxyType({})\n')
    selectors = (chain.context_transitions[0].prepared_admission_context_id,)
    with root.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
            bindings = root.load_historical_context_verifiers_for_contexts_in_session_v1(selectors, session)
            with pytest.raises(root.PreparedRootError, match='birth_context_producer_policy_invalid'):
                root.load_historical_producer_declarations_for_contexts_in_session_v1(
                    selectors, session, _context_bindings=dict(zip(selectors, bindings)))


def test_shared_census_requires_exclusive_session_and_stable_root(tmp_path, monkeypatch):
    chain, _, _ = _producer_policy_fixture(tmp_path, monkeypatch)
    selectors = (chain.context_transitions[0].prepared_admission_context_id,)
    from executor_birth_secure_fs import BirthSecureFSError
    with root.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=False, create=False):
            bindings = root.load_historical_context_verifiers_for_contexts_in_session_v1(selectors, session)
            with pytest.raises(BirthSecureFSError):
                root.load_historical_producer_declarations_for_contexts_in_session_v1(
                    selectors, session, _context_bindings=dict(zip(selectors, bindings)))
        with session.global_lock(exclusive=True, create=False):
            bindings = root.load_historical_context_verifiers_for_contexts_in_session_v1(selectors, session)
            original = root._historical_public_source_v1
            def read(*args, **kwargs):
                result = original(*args, **kwargs)
                session._root_path = str(session._root_path) + '-changed'
                return result
            monkeypatch.setattr(root, '_historical_public_source_v1', read)
            with pytest.raises(root.PreparedRootError, match='birth_context_selection_changed'):
                root.load_historical_producer_declarations_for_contexts_in_session_v1(
                    selectors, session, _context_bindings=dict(zip(selectors, bindings)))
