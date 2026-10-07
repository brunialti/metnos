"""Real historical signed sources and public sets under an existing EX barrier."""
from dataclasses import replace
import os

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
def test_exact_declarations_use_existing_barrier_and_historical_source(tmp_path, monkeypatch, reattestation, archive):
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
            deny_new_authority(monkeypatch)
            observed = root.load_historical_producer_declarations_for_contexts_in_session_v1(selectors, session, **options)
            assert observed == expected
            assert observed[0].authors
            assert session._holds_global_exclusive()
            assert snapshot(tmp_path) == before


@pytest.mark.parametrize('change', ['frontier', 'source', 'archive', 'selector'])
def test_held_session_rejects_changed_or_unbound_history(tmp_path, monkeypatch, change):
    chain, release, source = _producer_policy_fixture(tmp_path, monkeypatch)
    selectors = (chain.context_transitions[0].prepared_admission_context_id,)
    options = {}
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
    with root.open_prepared_root_session_v1() as session:
        with session.global_lock(exclusive=True, create=False):
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
